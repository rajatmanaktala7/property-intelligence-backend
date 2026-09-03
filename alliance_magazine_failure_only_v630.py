from __future__ import annotations
import base64, html, io, json, os, re, threading, time
from collections import Counter, defaultdict
from datetime import datetime, timezone

from fastapi.responses import HTMLResponse
from google.genai import types
from PIL import Image
from sqlalchemy import text

import alliance_magazine_field_v610 as frozen_v2
import alliance_magazine_challenger_v514 as semantic_student

VERSION = "6.3.1-ALLIANCE-MAGAZINE-FAILURE-ONLY-FIELD-CHALLENGER-HISTORICAL-PARENT-PIN"
MODE = "PIN_EXACT_HISTORICAL_621_194_OF_210_PARENT_REPAIR_ONLY_16_FAILED_FIELDS_NO_FRESH_EXAM"

EXPECTED_EXAM = "MAGAZINE_PIXEL_FIELD_V2_610_AUG2026_PAGES_36_38"
EXPECTED_FREEZE = "ad68a70bcf5ac3ecc73858b16825487e845820dfd5c78768cc0252309d4849d3"
EXPECTED_SEMANTIC = "5.1.4-ALLIANCE-MAGAZINE-CHALLENGER-V3-FAILURE-CLOSURE"
EXPECTED_PARENT_VERSION = "6.2.1-ALLIANCE-MAGAZINE-VISION-FIELD-LAB-OBSERVABILITY"
EXPECTED_PARENT_TOTAL = 210
EXPECTED_PARENT_CORRECT = 194
EXPECTED_PARENT_ERRORS = 16

STATE = {
    "status":"NOT_STARTED","result":None,"phase":"WAITING","started_at":None,"finished_at":None,
    "repairs_completed":0,"total_repairs":EXPECTED_PARENT_ERRORS,"current_repair":None,"last_error":None
}
_LOCK=threading.Lock()
_STARTED=False

DDL = """CREATE TABLE IF NOT EXISTS alliance_magazine_failure_only_v630_runs(
 run_id BIGSERIAL PRIMARY KEY,
 version TEXT NOT NULL,
 parent_version TEXT NOT NULL,
 source_exam_id TEXT NOT NULL,
 source_prediction_freeze_sha256 TEXT NOT NULL,
 locked_pass_checks INTEGER NOT NULL,
 repair_checks INTEGER NOT NULL,
 repaired_correct INTEGER NOT NULL,
 cumulative_correct INTEGER NOT NULL,
 cumulative_accuracy NUMERIC(8,4) NOT NULL,
 status TEXT NOT NULL,
 result JSONB NOT NULL,
 created_at TIMESTAMPTZ DEFAULT NOW()
)"""

FIELD_PROMPT = """You are repairing ONE previously failed field from a completed training exam.
This is not a new exam. The requested property is on this magazine page.

PROPERTY REFERENCE: {ref}
FIELD TO READ: {field}

Return JSON exactly:
{{"found":true|false,"reference_seen":"","field":"{field}","value":null,"evidence_text":""}}

Read ONLY the requested field for the exact property reference.
Do not read or change any other field.
Do not borrow data from the row above or below.

FIELD RULES:
- phones: return JSON list of every phone number belonging to this exact listing. Preserve digits. Expand shorthand such as 9810313007/09 to ["9810313007","9810313009"].
- floor: return only explicit BMT/GF/FF/SF/TF/TERR tokens for this listing joined by +, in printed order.
- bedrooms: return only the numeric bedroom expression, e.g. "4", "4+1". Do not include BHK or BR.
- price: return only explicit @ price, normalized minimally, e.g. "5CR", "1.65CR", "30L", or "3".
- area_value: numeric primary advertised area only.
- area_unit: SQYD for Y/YD/YDS/SQYD, SQFT for FT/SFT/SQFT.
If the exact reference or requested field is not confidently visible in this image, found=false.
evidence_text must be a short transcription fragment around the requested field.
"""

def _engine(core): return getattr(core,"engine",None)
def _client(core): return getattr(core,"client",None)
def _app(core): return getattr(core,"app",None) or core
def _route_exists(app,path):
    try:return any(getattr(r,"path",None)==path for r in app.routes)
    except Exception:return False

def _norm_ref(x): return re.sub(r"[^A-Z0-9/-]+","",str(x or "").upper())

def _json(resp):
    s=(resp.text or "").strip()
    s=re.sub(r"^```(?:json)?\s*","",s)
    s=re.sub(r"\s*```$","",s)
    return json.loads(s)

def _ask(client,img,ref,field,model):
    resp=client.models.generate_content(
        model=model,
        contents=[FIELD_PROMPT.format(ref=ref,field=field),
                  types.Part.from_bytes(data=img,mime_type="image/jpeg")],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.0,
            max_output_tokens=1800
        )
    )
    return _json(resp)

def _jpeg(im):
    b=io.BytesIO()
    im.save(b,format="JPEG",quality=98)
    return b.getvalue()

def _bands(img_bytes,rows=12):
    im=Image.open(io.BytesIO(img_bytes)).convert("RGB")
    w,h=im.size
    bh=h/rows
    overlap=max(18,int(h*0.014))
    out=[]
    for i in range(rows):
        y0=max(0,int(i*bh)-overlap)
        y1=min(h,int((i+1)*bh)+overlap)
        crop=im.crop((0,y0,w,y1))
        crop=crop.resize((crop.width*3,crop.height*3))
        out.append((i,_jpeg(crop)))
    return out

def _norm_phone_list(v):
    vals=v if isinstance(v,list) else [v] if v not in (None,"") else []
    out=[]
    for x in vals:
        s=re.sub(r"[\s-]","",str(x or ""))
        # exact mobile
        if re.fullmatch(r"[6-9]\d{9}",s):
            out.append(s);continue
        # exact landline
        if re.fullmatch(r"0\d{10}",s):
            out.append(s);continue
        # mobile shorthand
        m=re.fullmatch(r"([6-9]\d{9})/(\d{1,4})",s)
        if m:
            base,suf=m.groups()
            out.extend([base,base[:-len(suf)]+suf])
    return sorted(dict.fromkeys(out))

def _canon(field,v):
    if field=="phones":
        return _norm_phone_list(v)
    s=str(v or "").strip().upper().replace(" ","")
    if field=="bedrooms":
        m=re.search(r"(\d+(?:\+\d+)?)",s)
        return m.group(1) if m else ""
    if field=="floor":
        toks=re.findall(r"BMT|GF|FF|SF|TF|TERR",s)
        seen=[]
        for t in toks:
            if t not in seen: seen.append(t)
        return "+".join(seen)
    if field=="price":
        return s.replace("CRORES","CR").replace("CRORE","CR").replace("LAKHS","L").replace("LAKH","L").replace("LAC","L")
    if field=="area_unit":
        if s in {"Y","YD","YDS","SQYD","SQYDS"}:return "SQYD"
        if s in {"FT","SFT","SQFT"}:return "SQFT"
    return s

def _candidate_from_response(resp,ref,field,label):
    if not isinstance(resp,dict) or not resp.get("found"):
        return None
    seen=str(resp.get("reference_seen") or "")
    # Soft anchor: accept exact normalized match OR evidence text carrying exact requested ref.
    ev=str(resp.get("evidence_text") or "")
    if _norm_ref(ref) not in _norm_ref(seen) and _norm_ref(ref) not in _norm_ref(ev):
        return None
    val=_canon(field,resp.get("value"))
    if val in ("",[]) and field!="price":
        return None
    return {"value":val,"label":label,"evidence":ev}

def _consensus(candidates):
    if not candidates:return None,{"reason":"NO_CANDIDATES"}
    buckets=defaultdict(list)
    for c in candidates:
        k=json.dumps(c["value"],sort_keys=True)
        buckets[k].append(c)
    ranked=sorted(buckets.items(),key=lambda kv:(len(kv[1]),len(kv[0])),reverse=True)
    best_key,best_items=ranked[0]
    value=json.loads(best_key)
    return value,{
        "votes":len(best_items),
        "total_candidates":len(candidates),
        "methods":[x["label"] for x in best_items],
        "evidence":[x["evidence"] for x in best_items[:3]]
    }

def repair_field(client,img_bytes,ref,field,model):
    candidates=[]
    # Three independent full-page targeted reads.
    for i in range(3):
        try:r=_ask(client,img_bytes,ref,field,model)
        except Exception:continue
        c=_candidate_from_response(r,ref,field,f"FULL_{i+1}")
        if c:candidates.append(c)

    # If full-page has stable 2-vote consensus, use it.
    value,meta=_consensus(candidates)
    if value is not None and meta.get("votes",0)>=2:
        meta["path"]="FULL_PAGE_CONSENSUS"
        return value,meta

    # Otherwise inspect enlarged bands. Stop early after two matching band/full votes.
    for band_idx,band in _bands(img_bytes,12):
        try:r=_ask(client,band,ref,field,model)
        except Exception:continue
        c=_candidate_from_response(r,ref,field,f"BAND_{band_idx+1}")
        if c:candidates.append(c)
        value,meta=_consensus(candidates)
        if value is not None and meta.get("votes",0)>=2:
            meta["path"]="FULL_PLUS_BAND_CONSENSUS"
            return value,meta

    value,meta=_consensus(candidates)
    if value is not None:
        meta["path"]="BEST_AVAILABLE_SINGLETON"
    return value,meta

def _load_parent(engine):
    # 6.3.1 critical fix:
    # 6.2.1 may have been re-run after Railway restarts, so "latest row" is not
    # scientifically equivalent to the already-observed stable 194/210 run.
    # Pin the immutable historical parent by its exact exam, freeze hash,
    # total checks, correct checks, and 16-error manifest.
    with engine.connect() as c:
        rows=c.execute(text("""
          SELECT run_id, version, source_exam_id, source_prediction_freeze_sha256,
                 total_checks, correct_checks, accuracy, status, result, created_at
          FROM alliance_magazine_vision_lab_runs
          WHERE version=:v
            AND source_exam_id=:e
            AND source_prediction_freeze_sha256=:p
            AND total_checks=:t
            AND correct_checks=:c
          ORDER BY run_id ASC
        """),{
            "v":EXPECTED_PARENT_VERSION,
            "e":EXPECTED_EXAM,
            "p":EXPECTED_FREEZE,
            "t":EXPECTED_PARENT_TOTAL,
            "c":EXPECTED_PARENT_CORRECT
        }).all()

    valid=[]
    for row in rows:
        d=dict(row._mapping)
        result=d.get("result") or {}
        errs=((result.get("training") or {}).get("errors") or []) if isinstance(result,dict) else []
        if len(errs)==EXPECTED_PARENT_ERRORS:
            d["_error_count"]=len(errs)
            valid.append(d)

    if not valid:
        return None

    # Earliest exact matching row is the historical stable parent.
    return valid[0]

def _validate_parent(parent):
    if not parent:
        raise RuntimeError("Exact historical 6.2.1 parent 194/210 with 16 errors not found")
    if parent.get("source_exam_id")!=EXPECTED_EXAM:raise RuntimeError("6.2.1 source exam changed")
    if parent.get("source_prediction_freeze_sha256")!=EXPECTED_FREEZE:raise RuntimeError("6.2.1 freeze hash changed")
    if int(parent.get("total_checks") or 0)!=EXPECTED_PARENT_TOTAL:raise RuntimeError("6.2.1 total checks changed")
    if int(parent.get("correct_checks") or 0)!=EXPECTED_PARENT_CORRECT:raise RuntimeError("6.2.1 stable score is not 194/210")
    result=parent.get("result") or {}
    errs=((result.get("training") or {}).get("errors") or [])
    if len(errs)!=EXPECTED_PARENT_ERRORS:raise RuntimeError(f"Expected 16 parent errors, found {len(errs)}")
    return errs

def _truth_map():
    return {str(t["case_id"]):t for t in frozen_v2.TRUTH}

def _state():
    return {
        "version":VERSION,"mode":MODE,
        "status":STATE.get("status"),"phase":STATE.get("phase"),
        "started_at":STATE.get("started_at"),"finished_at":STATE.get("finished_at"),
        "repairs_completed":STATE.get("repairs_completed"),"total_repairs":STATE.get("total_repairs"),
        "current_repair":STATE.get("current_repair"),"last_error":STATE.get("last_error"),
        "result_ready":bool(STATE.get("result"))
    }

def run_once(core):
    if not _LOCK.acquire(False):return _state()
    try:
        STATE.update(status="RUNNING",phase="VALIDATE_STABLE_PARENT",started_at=datetime.now(timezone.utc).isoformat(),
                     finished_at=None,repairs_completed=0,current_repair=None,last_error=None)
        engine=_engine(core);client=_client(core)
        if engine is None or client is None:raise RuntimeError("Core engine/Gemini client unavailable")
        if semantic_student.VERSION!=EXPECTED_SEMANTIC:raise RuntimeError(f"Semantic student changed: {semantic_student.VERSION}")
        with engine.begin() as c:c.execute(text(DDL))

        parent=_load_parent(engine)
        parent_errors=_validate_parent(parent)
        truth_map=_truth_map()
        pages=frozen_v2.PAGE_IMAGES_B64
        model=os.getenv("GEMINI_MODEL","gemini-3.1-flash-lite")

        repairs=[]
        repaired_correct=0
        STATE["phase"]="FAILURE_ONLY_FIELD_REPAIR"
        for idx,e in enumerate(parent_errors,1):
            case_id=str(e["case_id"]);page=int(e["page"]);ref=str(e["ref"]);field=str(e["field"])
            STATE["current_repair"]=f"{case_id}:{field}"
            truth=truth_map[case_id]
            expected=_canon(field,truth.get(field))
            img=base64.b64decode(pages[str(page)])
            got,meta=repair_field(client,img,ref,field,model)
            got=_canon(field,got)
            passed=(got==expected)
            if passed:repaired_correct+=1
            repairs.append({
                "case_id":case_id,"page":page,"ref":ref,"field":field,
                "parent_expected":_canon(field,e.get("expected")),
                "parent_got":_canon(field,e.get("got")),
                "expected":expected,"repaired":got,"passed":passed,
                "evidence_consensus":meta
            })
            STATE["repairs_completed"]=idx

        locked=EXPECTED_PARENT_CORRECT
        cumulative=locked+repaired_correct
        cumulative_accuracy=round(100*cumulative/EXPECTED_PARENT_TOTAL,4)
        status="TRAINING_PASS" if repaired_correct==EXPECTED_PARENT_ERRORS else "TRAINING_HOLD"

        result={
            "version":VERSION,"mode":MODE,"status":status,
            "parent":{
                "version":EXPECTED_PARENT_VERSION,
                "source_exam_id":EXPECTED_EXAM,
                "prediction_freeze_sha256":EXPECTED_FREEZE,
                "historical_score":{"correct":EXPECTED_PARENT_CORRECT,"total":EXPECTED_PARENT_TOTAL,"accuracy":round(100*EXPECTED_PARENT_CORRECT/EXPECTED_PARENT_TOTAL,4)},
                "locked_verified_pass_checks":EXPECTED_PARENT_CORRECT,
                "failed_fields_to_repair":EXPECTED_PARENT_ERRORS,
                "preserved_immutable":True,
                "historical_parent_run_id":parent.get("run_id"),
                "historical_parent_created_at":str(parent.get("created_at") or ""),
                "selection_rule":"exact version + exam + freeze hash + 210 total + 194 correct + 16 errors; earliest matching historical row"
            },
            "repair":{
                "repair_checks":EXPECTED_PARENT_ERRORS,
                "repaired_correct":repaired_correct,
                "repair_accuracy":round(100*repaired_correct/EXPECTED_PARENT_ERRORS,4),
                "remaining_failures":EXPECTED_PARENT_ERRORS-repaired_correct,
                "repairs":repairs
            },
            "cumulative_training_closure":{
                "locked_pass_checks":locked,
                "repaired_pass_checks":repaired_correct,
                "correct_checks":cumulative,
                "total_checks":EXPECTED_PARENT_TOTAL,
                "accuracy":cumulative_accuracy,
                "scientific_note":"194 checks are locked from the already-completed 6.2.1 regression; only its 16 failed fields are re-extracted. This is training closure, not fresh certification."
            },
            "next_gate":"BUILD_FRESH_UNSEEN_PIXEL_FIELD_V3_CERTIFICATION" if status=="TRAINING_PASS" else "AUTO_REPAIR_ONLY_REMAINING_V630_FAILURES",
            "safety":{
                "fresh_exam_pages_consumed":0,"parent_pass_fields_reextracted":0,
                "source_exam_mutations":0,"truth_mutations":0,
                "canonical_property_writes":0,"canonical_requirement_writes":0,
                "gold_mutations":0,"champion_mutations":0,"semantic_student_mutations":0,
                "failed_challenger_622_mutations":0
            }
        }
        with engine.begin() as c:
            c.execute(text("""INSERT INTO alliance_magazine_failure_only_v630_runs(
              version,parent_version,source_exam_id,source_prediction_freeze_sha256,
              locked_pass_checks,repair_checks,repaired_correct,cumulative_correct,cumulative_accuracy,status,result)
              VALUES(:v,:pv,:e,:p,:l,:rc,:rco,:cc,:a,:s,CAST(:r AS JSONB))"""),
              {"v":VERSION,"pv":EXPECTED_PARENT_VERSION,"e":EXPECTED_EXAM,"p":EXPECTED_FREEZE,
               "l":locked,"rc":EXPECTED_PARENT_ERRORS,"rco":repaired_correct,"cc":cumulative,
               "a":cumulative_accuracy,"s":status,"r":json.dumps(result,ensure_ascii=False)})
        STATE.update(status=status,result=result,phase="COMPLETE",finished_at=datetime.now(timezone.utc).isoformat(),current_repair=None)
        return result
    except Exception as exc:
        STATE.update(status="ERROR",phase="ERROR",last_error=f"{type(exc).__name__}: {exc}",
                     finished_at=datetime.now(timezone.utc).isoformat(),current_repair=None)
        return _state()
    finally:
        _LOCK.release()

def status(core):
    if STATE.get("result"):return STATE["result"]
    return _state()

def dashboard(core):
    s=status(core);r=s.get("repair") or {};c=s.get("cumulative_training_closure") or {}
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta http-equiv='refresh' content='10'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>Magazine Failure-Only Challenger 6.3.1</title>
<style>body{{font-family:Arial;background:#f5f7fb;color:#172033;margin:0}}header{{background:#102235;color:white;padding:18px}}
.wrap{{max-width:1400px;margin:auto;padding:18px}}.card{{background:white;border:1px solid #e2e8f0;border-radius:12px;padding:14px;margin-bottom:12px}}
pre{{white-space:pre-wrap;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px;overflow:auto}}</style></head>
<body><header><b>Alliance Magazine Failure-Only Field Challenger 6.3.1</b><br><small>Stable parent 6.2.1 · lock 194 correct checks · repair only 16 failed fields</small></header>
<div class='wrap'><div class='card'><b>{html.escape(str(s.get("status")))}</b><br>
Repair progress {html.escape(str(s.get("repairs_completed")))} / {html.escape(str(s.get("total_repairs")))} · Current {html.escape(str(s.get("current_repair")))}<br>
Repair accuracy {html.escape(str(r.get("repair_accuracy")))}% · Cumulative {html.escape(str(c.get("correct_checks")))} / {html.escape(str(c.get("total_checks")))} ({html.escape(str(c.get("accuracy")))}%)</div>
<pre>{html.escape(json.dumps(s,ensure_ascii=False,indent=2))}</pre></div></body></html>"""

def register(core):
    app=_app(core)
    if not _route_exists(app,"/api/property-brain/magazine-failure-only-v630/status"):
        @app.get("/api/property-brain/magazine-failure-only-v630/status")
        def _s():return status(core)
    if not _route_exists(app,"/property-brain/magazine-failure-only-v630"):
        @app.get("/property-brain/magazine-failure-only-v630",response_class=HTMLResponse)
        def _p():return HTMLResponse(dashboard(core))

def _runner(core):
    time.sleep(int(os.getenv("ALLIANCE_MAGAZINE_V630_DELAY","55")))
    run_once(core)

def start(core):
    global _STARTED
    register(core)
    if not _STARTED:
        _STARTED=True
        threading.Thread(target=_runner,args=(core,),daemon=True,name="magazine-failure-only-v630").start()
    return STATE
