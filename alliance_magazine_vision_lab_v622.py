from __future__ import annotations
import base64, html, io, json, os, re, threading, time
from collections import defaultdict
from datetime import datetime, timezone
from fastapi.responses import HTMLResponse
from google.genai import types
from PIL import Image
from sqlalchemy import text

import alliance_magazine_field_v610 as frozen_v2
import alliance_magazine_challenger_v514 as semantic_student

VERSION="6.2.2-ALLIANCE-MAGAZINE-VISION-FIELD-LAB-TARGETED-REPAIR"
MODE="POST_621_FAILURE_TARGETED_FIELD_EXTRACTION_EXACT_REF_OWNERSHIP_NO_FRESH_EXAM_NO_SOURCE_MUTATION"
EXPECTED_EXAM="MAGAZINE_PIXEL_FIELD_V2_610_AUG2026_PAGES_36_38"
EXPECTED_FREEZE="ad68a70bcf5ac3ecc73858b16825487e845820dfd5c78768cc0252309d4849d3"
EXPECTED_SEMANTIC="5.1.4-ALLIANCE-MAGAZINE-CHALLENGER-V3-FAILURE-CLOSURE"
FIELDS=("ref","area_value","area_unit","floor","bedrooms","price","phones")
STATE={"status":"NOT_STARTED","result":None,"phase":"WAITING","started_at":None,"finished_at":None,"current_case":None,"cases_completed":0,"total_cases":30,"last_error":None}
_LOCK=threading.Lock(); _STARTED=False

DDL="""CREATE TABLE IF NOT EXISTS alliance_magazine_vision_lab_v622_runs(
 run_id BIGSERIAL PRIMARY KEY, version TEXT NOT NULL, source_exam_id TEXT NOT NULL,
 source_prediction_freeze_sha256 TEXT NOT NULL, total_cases INTEGER NOT NULL,
 total_checks INTEGER NOT NULL, correct_checks INTEGER NOT NULL, accuracy NUMERIC(8,4) NOT NULL,
 status TEXT NOT NULL, result JSONB NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW())"""

PROMPT="""You are a forensic transcription engine reading ONE requested property record from a dense real-estate magazine page.
Requested property reference: {ref}

Return JSON exactly:
{{"found":true,"ref":"{ref}","raw_line":"","area_value":"","area_unit":"","floor":"","bedrooms":"","price":"","phones":[]}}

STRICT RULES:
1. Find the exact printed reference first. The record starts at that reference.
2. Read ONLY that record. Never borrow a floor, price, bedroom or phone from the row above or below.
3. If contact numbers are printed at the end of the same record, copy all of them. If a compact slash suffix is printed, preserve it in raw_line.
4. Preserve exact digits. Do not autocorrect names/numbers.
5. floor must use only explicit BMT/GF/FF/SF/TF/TERR tokens belonging to this record, joined with +.
6. area_unit must be SQYD for Y/YD/YDS/SQYD and SQFT for FT/SFT/SQFT.
7. price only when explicitly printed with @.
8. If a field is absent in this exact record, return empty. If the reference cannot be confidently isolated, found=false.
"""

def _engine(c): return getattr(c,"engine",None)
def _client(c): return getattr(c,"client",None)
def _app(c): return getattr(c,"app",None) or c
def _route_exists(a,p):
    try:return any(getattr(r,"path",None)==p for r in a.routes)
    except:return False
def _norm_ref(x):return re.sub(r"[^A-Z0-9/-]+","",str(x or "").upper())
def _json(resp):
    s=(resp.text or "").strip()
    s=re.sub(r"^```(?:json)?\s*","",s);s=re.sub(r"\s*```$","",s)
    return json.loads(s)
def _ask(client,img,ref,model):
    return _json(client.models.generate_content(
      model=model,
      contents=[PROMPT.format(ref=ref),types.Part.from_bytes(data=img,mime_type="image/jpeg")],
      config=types.GenerateContentConfig(response_mime_type="application/json",temperature=0.0,max_output_tokens=2500)))
def _jpeg(im):
    b=io.BytesIO();im.save(b,format="JPEG",quality=98);return b.getvalue()
def _bands(img,rows=10):
    im=Image.open(io.BytesIO(img)).convert("RGB");w,h=im.size;bh=h/rows;ov=max(20,int(h*.018))
    out=[]
    for i in range(rows):
        y0=max(0,int(i*bh)-ov);y1=min(h,int((i+1)*bh)+ov)
        c=im.crop((0,y0,w,y1));c=c.resize((c.width*3,c.height*3));out.append(_jpeg(c))
    return out

def _phones_from_text(raw):
    compact=re.sub(r"[\s-]","",str(raw or ""))
    out=[]
    # slash forms first
    for m in re.finditer(r"(?<!\d)([6-9]\d{9})/(\d{1,4})(?!\d)",compact):
        b,s=m.groups();out += [b,b[:-len(s)]+s]
    for d in re.findall(r"(?<!\d)([6-9]\d{9})(?!\d)",compact):out.append(d)
    for d in re.findall(r"(?<!\d)(0\d{10})(?!\d)",compact):out.append(d)
    return sorted(dict.fromkeys(out))

def _canon_value(field,v):
    if field=="ref":return _norm_ref(v)
    if field=="phones":
        vals=v if isinstance(v,list) else []
        return sorted(dict.fromkeys(re.sub(r"\D","",str(x)) for x in vals if re.sub(r"\D","",str(x))))
    s=str(v or "").upper().replace(" ","")
    if field=="area_unit":
        if s in {"Y","YD","YDS","SQYD","SQYDS"}:return "SQYD"
        if s in {"FT","SFT","SQFT"}:return "SQFT"
    if field=="price":
        return s.replace("CRORES","CR").replace("CRORE","CR").replace("LAKHS","L").replace("LAKH","L").replace("LAC","L")
    return s.strip("+")

def _canon(r):
    return {f:_canon_value(f,r.get(f)) for f in FIELDS}

def _candidate(client,img,ref,model,label):
    try:d=_ask(client,img,ref,model)
    except Exception:return None
    if not d.get("found"):return None
    if _norm_ref(d.get("ref"))!=_norm_ref(ref):return None
    raw=str(d.get("raw_line") or "")
    # exact reference ownership guard: raw line itself must contain requested anchor.
    if _norm_ref(ref) not in _norm_ref(raw):return None
    phones=_phones_from_text(raw)
    if not phones:
        phones=_canon_value("phones",d.get("phones"))
    rec={"ref":ref,"raw_line":raw,"area_value":d.get("area_value",""),"area_unit":d.get("area_unit",""),
         "floor":d.get("floor",""),"bedrooms":d.get("bedrooms",""),"price":d.get("price",""),"phones":phones,"method":label}
    return rec

def extract_exact(client,img,ref,model):
    candidates=[]
    # Independent full-page reads reduce batch-row contamination.
    for i in range(2):
        c=_candidate(client,img,ref,model,f"FULL_PAGE_TARGETED_{i+1}")
        if c:candidates.append(c)
    # Band reads are only accepted when exact anchor is transcribed.
    for i,b in enumerate(_bands(img,10)):
        c=_candidate(client,b,ref,model,f"BAND_{i+1}")
        if c:candidates.append(c)

    if not candidates:
        return {"ref":ref,"area_value":"","area_unit":"","floor":"","bedrooms":"","price":"","phones":[],"method":"NOT_FOUND"}

    # Field-level consensus. This avoids choosing one visually plausible but wrong neighbouring line.
    out={"ref":ref}; methods=[]
    for f in ("area_value","area_unit","floor","bedrooms","price","phones"):
        votes=defaultdict(list)
        for c in candidates:
            v=_canon_value(f,c.get(f))
            key=json.dumps(v,sort_keys=True)
            votes[key].append(c["method"])
        # Empty values may win only if every accepted exact-anchor read is empty.
        nonempty={k:v for k,v in votes.items() if json.loads(k) not in ("",[])}
        pool=nonempty or votes
        best=max(pool.items(),key=lambda kv:(len(kv[1]), any(x.startswith("BAND_") for x in kv[1]),len(kv[0])))
        out[f]=json.loads(best[0]);methods.extend(best[1])
    out["method"]="FIELD_CONSENSUS:"+",".join(sorted(set(methods)))
    return out

def _source_ok(engine):
    with engine.connect() as c:
        r=c.execute(text("""SELECT exam_id,status,prediction_freeze_sha256 FROM alliance_magazine_pixel_field_v2_exams
                            WHERE exam_id=:e LIMIT 1"""),{"e":EXPECTED_EXAM}).first()
    if not r:raise RuntimeError("Frozen 6.1 exam missing")
    d=dict(r._mapping)
    if d["status"]!="AUTOMATED_INDEPENDENT_MAGAZINE_FIELD_V2_HOLD":raise RuntimeError("Frozen 6.1 status changed")
    if d["prediction_freeze_sha256"]!=EXPECTED_FREEZE:raise RuntimeError("Frozen 6.1 prediction hash changed")

def _state():
    return {"version":VERSION,"mode":MODE,**{k:v for k,v in STATE.items() if k!="result"},"result_ready":bool(STATE.get("result"))}

def run_once(core):
    if not _LOCK.acquire(False):return _state()
    try:
        STATE.update(status="RUNNING",phase="INITIALIZING",started_at=datetime.now(timezone.utc).isoformat(),
                     finished_at=None,current_case=None,cases_completed=0,last_error=None)
        engine=_engine(core);client=_client(core)
        if engine is None or client is None:raise RuntimeError("Core engine/Gemini client unavailable")
        if semantic_student.VERSION!=EXPECTED_SEMANTIC:raise RuntimeError("Semantic student changed")
        with engine.begin() as c:c.execute(text(DDL))
        _source_ok(engine)
        model=os.getenv("GEMINI_MODEL","gemini-3.1-flash-lite")
        truth=frozen_v2.TRUTH;pages=frozen_v2.PAGE_IMAGES_B64
        cache={p:base64.b64decode(pages[str(p)]) for p in sorted({int(x["page"]) for x in truth})}
        preds=[]
        for i,t in enumerate(truth,1):
            STATE["phase"]="TARGETED_EXACT_REF_EXTRACTION";STATE["current_case"]=t["case_id"]
            rec=extract_exact(client,cache[int(t["page"])],t["ref"],model)
            preds.append({"page":t["page"],"case_id":t["case_id"],**rec})
            STATE["cases_completed"]=i

        STATE["phase"]="EXACT_REGRESSION_GRADING";STATE["current_case"]=None
        by={x["case_id"]:_canon(x) for x in preds}
        total=correct=0;errors=[];fs={f:{"correct":0,"total":0} for f in FIELDS}
        for t in truth:
            exp=_canon(t);got=by[t["case_id"]]
            for f in FIELDS:
                total+=1;fs[f]["total"]+=1
                if got[f]==exp[f]:correct+=1;fs[f]["correct"]+=1
                else:errors.append({"case_id":t["case_id"],"page":t["page"],"ref":t["ref"],"field":f,"expected":exp[f],"got":got[f]})
        for f,s in fs.items():s["accuracy"]=round(100*s["correct"]/s["total"],4)
        acc=round(100*correct/total,4);status="TRAINING_PASS" if correct==total else "TRAINING_HOLD"
        result={"version":VERSION,"mode":MODE,"status":status,
                "source_exam":{"exam_id":EXPECTED_EXAM,"prediction_freeze_sha256":EXPECTED_FREEZE,"preserved_immutable":True},
                "training":{"total_cases":len(truth),"total_checks":total,"correct_checks":correct,"accuracy":acc,
                            "critical_errors":len(errors),"field_accuracy":fs,"errors":errors},
                "repair":{"batch_locator_removed_from_final_selection":True,"exact_ref_guard":True,
                          "independent_targeted_reads":True,"field_level_consensus":True,
                          "deterministic_phone_shorthand_expansion":True},
                "next_gate":"BUILD_FRESH_UNSEEN_PIXEL_FIELD_V3" if status=="TRAINING_PASS" else "AUTO_REPAIR_ONLY_REMAINING_V622_FAILURES",
                "safety":{"fresh_exam_pages_consumed":0,"source_exam_mutations":0,"truth_mutations":0,
                          "canonical_property_writes":0,"canonical_requirement_writes":0,"gold_mutations":0,
                          "champion_mutations":0,"semantic_student_mutations":0}}
        with engine.begin() as c:
            c.execute(text("""INSERT INTO alliance_magazine_vision_lab_v622_runs
            (version,source_exam_id,source_prediction_freeze_sha256,total_cases,total_checks,correct_checks,accuracy,status,result)
            VALUES(:v,:e,:p,:tc,:t,:cc,:a,:s,CAST(:r AS JSONB))"""),
            {"v":VERSION,"e":EXPECTED_EXAM,"p":EXPECTED_FREEZE,"tc":len(truth),"t":total,"cc":correct,"a":acc,"s":status,
             "r":json.dumps(result)})
        STATE.update(status=status,result=result,phase="COMPLETE",finished_at=datetime.now(timezone.utc).isoformat())
        return result
    except Exception as e:
        STATE.update(status="ERROR",phase="ERROR",last_error=f"{type(e).__name__}: {e}",finished_at=datetime.now(timezone.utc).isoformat())
        return _state()
    finally:_LOCK.release()

def status(core):
    if STATE.get("result"):return STATE["result"]
    return _state()

def dashboard(core):
    s=status(core);t=s.get("training") or {}
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta http-equiv='refresh' content='10'>
    <meta name='viewport' content='width=device-width,initial-scale=1'><title>Magazine Vision Lab 6.2.2</title>
    <style>body{{font-family:Arial;background:#f5f7fb;color:#172033;margin:0}}header{{background:#102235;color:white;padding:18px}}
    .wrap{{max-width:1350px;margin:auto;padding:18px}}.card{{background:white;border:1px solid #e2e8f0;border-radius:12px;padding:14px;margin-bottom:12px}}
    pre{{white-space:pre-wrap;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px;overflow:auto}}</style></head>
    <body><header><b>Alliance Magazine Vision Field Laboratory 6.2.2</b><br><small>Targeted exact-reference repair · no fresh exam pages</small></header>
    <div class='wrap'><div class='card'><b>{html.escape(str(s.get("status")))}</b><br>
    Accuracy {html.escape(str(t.get("accuracy")))}% · Checks {html.escape(str(t.get("correct_checks")))} / {html.escape(str(t.get("total_checks")))}<br>
    Phase {html.escape(str(s.get("phase")))} · Cases {html.escape(str(s.get("cases_completed")))} / {html.escape(str(s.get("total_cases")))}</div>
    <pre>{html.escape(json.dumps(s,indent=2))}</pre></div></body></html>"""

def register(core):
    a=_app(core)
    if not _route_exists(a,"/api/property-brain/magazine-vision-lab-v622/status"):
        @a.get("/api/property-brain/magazine-vision-lab-v622/status")
        def _s():return status(core)
    if not _route_exists(a,"/property-brain/magazine-vision-lab-v622"):
        @a.get("/property-brain/magazine-vision-lab-v622",response_class=HTMLResponse)
        def _p():return HTMLResponse(dashboard(core))
def _runner(core):
    time.sleep(int(os.getenv("ALLIANCE_MAGAZINE_V622_DELAY","55")));run_once(core)
def start(core):
    global _STARTED
    register(core)
    if not _STARTED:
        _STARTED=True;threading.Thread(target=_runner,args=(core,),daemon=True,name="magazine-v622").start()
    return STATE
