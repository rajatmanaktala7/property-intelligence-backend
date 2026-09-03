from __future__ import annotations

import base64
import hashlib
import html
import io
import json
import os
import re
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone

from fastapi.responses import HTMLResponse
from google.genai import types
from PIL import Image
from sqlalchemy import text

import alliance_magazine_field_v610 as frozen_v2
import alliance_magazine_challenger_v514 as semantic_student

VERSION = "6.2.0-ALLIANCE-MAGAZINE-VISION-FIELD-LAB"
MODE = "POST_EXAM_FAILURE_TRAINING_MULTI_STRATEGY_LOCATOR_PHONE_REPAIR_NO_FRESH_EXAM_NO_SOURCE_MUTATION"

EXPECTED_V2_EXAM_ID = "MAGAZINE_PIXEL_FIELD_V2_610_AUG2026_PAGES_36_38"
EXPECTED_V2_STATUS = "AUTOMATED_INDEPENDENT_MAGAZINE_FIELD_V2_HOLD"
EXPECTED_V2_PREDICTION_FREEZE = "ad68a70bcf5ac3ecc73858b16825487e845820dfd5c78768cc0252309d4849d3"
EXPECTED_SEMANTIC = "5.1.4-ALLIANCE-MAGAZINE-CHALLENGER-V3-FAILURE-CLOSURE"

FIELDS = ("ref","area_value","area_unit","floor","bedrooms","price","phones")
STATE={"status":"NOT_STARTED","result":None,"last_error":None}
_LOCK=threading.Lock()
_STARTED=False

DDL="""CREATE TABLE IF NOT EXISTS alliance_magazine_vision_lab_runs(
    run_id BIGSERIAL PRIMARY KEY,
    version TEXT NOT NULL,
    source_exam_id TEXT NOT NULL,
    source_prediction_freeze_sha256 TEXT NOT NULL,
    total_cases INTEGER NOT NULL,
    total_checks INTEGER NOT NULL,
    correct_checks INTEGER NOT NULL,
    accuracy NUMERIC(8,4) NOT NULL,
    status TEXT NOT NULL,
    result JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
)"""

BATCH_PROMPT="""You are repairing a vision extractor after a completed blind exam.
This is TRAINING, not a new exam. Read ONLY the supplied real-estate magazine page image.

Requested property references:
{refs}

Return JSON exactly:
{{"records":[{{"ref":"","raw_line":""}}]}}

Rules:
- Return one raw printed inventory line for each requested reference you can truly see.
- The raw_line must belong to that exact reference, never the line above/below.
- Preserve all digits, area token, floor tokens, BHK/BR, explicit @price, and phone numbers.
- Do not use broker headers/footers/office addresses.
- If a requested ref is not visible, omit it rather than inventing.
"""

SINGLE_PROMPT="""Locate ONE exact property listing in this real-estate magazine image.
Requested reference: {ref}
Return JSON exactly:
{{"found":true|false,"ref":"{ref}","raw_line":""}}

Copy the complete single printed inventory line belonging to this exact reference.
Never substitute a nearby property. Preserve every digit exactly.
Do not use broker/header/footer/address text.
If not visible, found=false.
"""

def _engine(core): return getattr(core,"engine",None)
def _app(core): return getattr(core,"app",None) or core
def _client(core): return getattr(core,"client",None)
def _route_exists(app,path):
    try:return any(getattr(r,"path",None)==path for r in app.routes)
    except Exception:return False

def _json(resp):
    s=(resp.text or "").strip()
    if s.startswith("```"):
        s=re.sub(r"^```(?:json)?\s*","",s)
        s=re.sub(r"\s*```$","",s)
    return json.loads(s)

def _ask(client,image_bytes,prompt,model):
    return _json(client.models.generate_content(
        model=model,
        contents=[prompt,types.Part.from_bytes(data=image_bytes,mime_type="image/jpeg")],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.0,
            max_output_tokens=16000
        )
    ))

def _norm_ref(x):
    return re.sub(r"[^A-Z0-9/-]+","",str(x or "").upper())

def _jpeg(im):
    b=io.BytesIO(); im.save(b,format="JPEG",quality=98); return b.getvalue()

def _make_bands(image_bytes,rows=12):
    im=Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w,h=im.size
    bh=h/rows
    overlap=max(16,int(h*0.012))
    out=[]
    for i in range(rows):
        y0=max(0,int(i*bh)-overlap)
        y1=min(h,int((i+1)*bh)+overlap)
        crop=im.crop((0,y0,w,y1))
        crop=crop.resize((crop.width*3,crop.height*3))
        out.append((i,_jpeg(crop)))
    return out

def _line_score(ref,raw):
    rr=_norm_ref(ref); nr=_norm_ref(raw)
    if rr not in nr:return -999
    score=0
    if nr.startswith(rr):score+=100
    # line completeness signals
    score += min(20,len(re.findall(r"\d",raw)))
    score += 8 if re.search(r"\b(?:YD|YDS|Y|SQYD|FT|SQFT|SFT)\b",str(raw).upper()) else 0
    score += 8 if re.search(r"\b(?:BMT|GF|FF|SF|TF|TERR|BHK|BR)\b",str(raw).upper()) else 0
    score += 8 if re.search(r"\d{10,11}",re.sub(r"[\s-]","",str(raw))) else 0
    return score

def _batch_full_page(client,image_bytes,refs,model):
    try:
        data=_ask(client,image_bytes,BATCH_PROMPT.format(refs=json.dumps(refs,ensure_ascii=False)),model)
    except Exception:
        return {}
    out={}
    wanted={_norm_ref(r):r for r in refs}
    for rec in data.get("records") or []:
        key=_norm_ref(rec.get("ref"))
        raw=str(rec.get("raw_line") or "").strip()
        if key in wanted and _norm_ref(wanted[key]) in _norm_ref(raw):
            out[wanted[key]]=raw
    return out

def _single_full_page(client,image_bytes,ref,model):
    try:
        data=_ask(client,image_bytes,SINGLE_PROMPT.format(ref=ref),model)
        raw=str(data.get("raw_line") or "").strip()
        if data.get("found") and _norm_ref(ref) in _norm_ref(raw):
            return raw
    except Exception:
        pass
    return ""

def _single_band_fallback(client,image_bytes,ref,model):
    best=""
    best_score=-999
    for _,band in _make_bands(image_bytes,12):
        try:
            data=_ask(client,band,SINGLE_PROMPT.format(ref=ref),model)
        except Exception:
            continue
        raw=str(data.get("raw_line") or "").strip()
        if not data.get("found"):continue
        sc=_line_score(ref,raw)
        if sc>best_score:
            best_score=sc;best=raw
    return best if best_score>=0 else ""

def locate_training_lines(client,image_bytes,refs,model):
    # Strategy A: batch full-page.
    batch=_batch_full_page(client,image_bytes,refs,model)
    chosen={}
    method={}
    for ref in refs:
        candidates=[]
        if batch.get(ref):
            candidates.append(("BATCH_FULL_PAGE",batch[ref]))
        # Strategy B: single-ref full page repairs omissions and ambiguous batch binding.
        raw=_single_full_page(client,image_bytes,ref,model)
        if raw:candidates.append(("SINGLE_FULL_PAGE",raw))
        # Strategy C only if earlier methods are absent or weak.
        if not candidates or max(_line_score(ref,x[1]) for x in candidates)<118:
            raw2=_single_band_fallback(client,image_bytes,ref,model)
            if raw2:candidates.append(("SINGLE_12_BAND",raw2))
        if candidates:
            candidates.sort(key=lambda x:_line_score(ref,x[1]),reverse=True)
            method[ref],chosen[ref]=candidates[0]
        else:
            method[ref]="NOT_FOUND";chosen[ref]=""
    return chosen,method

def _phones(raw):
    s=str(raw or "")
    candidates=[]
    # Indian mobiles: exact 10 digits starting 6-9.
    for d in re.findall(r"(?<!\d)([6-9]\d{9})(?!\d)",re.sub(r"[\s-]","",s)):
        candidates.append(d)
    # Delhi/Indian landlines: exact 11 digits starting 0.
    for d in re.findall(r"(?<!\d)(0\d{10})(?!\d)",re.sub(r"[\s-]","",s)):
        candidates.append(d)

    # Slash shorthand expansion e.g. 9810313007/09, 9654228805/06.
    compact=re.sub(r"[\s-]","",s)
    for m in re.finditer(r"(?<!\d)([6-9]\d{9})/(\d{1,4})(?!\d)",compact):
        base,suf=m.group(1),m.group(2)
        candidates.append(base)
        candidates.append(base[:-len(suf)]+suf)

    # Landline shorthand e.g. 011-26477771/2
    for m in re.finditer(r"(?<!\d)(0\d{10})/(\d{1,4})(?!\d)",compact):
        base,suf=m.group(1),m.group(2)
        candidates.append(base)
        candidates.append(base[:-len(suf)]+suf)

    out=[]
    for d in candidates:
        if d not in out:out.append(d)
    return sorted(out)

def parse_line(ref,raw):
    u=str(raw or "").upper()

    # Remove leading reference from parsing body where feasible.
    body=u
    # Primary area.
    am=re.search(r"\b(\d+(?:\.\d+)?)\s*(SQYDS?|SQYD|YDS|YD|Y|SQFT|SFT|FT)\b",body)
    area_value=am.group(1) if am else ""
    area_unit=""
    if am:
        area_unit="SQYD" if am.group(2) in {"SQYDS","SQYD","YDS","YD","Y"} else "SQFT"

    # Floors: preserve printed order and only explicit tokens.
    floors=[]
    for tok in re.findall(r"\b(BMT|GF|FF|SF|TF|TERR)\b",body):
        if tok not in floors:floors.append(tok)
    # Keep TRIPLEX when immediately associated with BMT/GF semantics.
    floor="+".join(floors)
    if "TRIPLEX" in body and floor:
        # Match legacy certified convention such as BMT+GFTRIPLEX.
        if "GF" in floors:
            floor=floor.replace("GF","GFTRIPLEX",1)

    bm=re.search(r"\b(\d+(?:\+\d+)?)\s*(?:BHK|BR)\b",body)
    bedrooms=bm.group(1) if bm else ""

    pm=re.search(r"@\s*([0-9]+(?:\.[0-9]+)?\s*(?:CR|CRORE|CRORES|L|LAC|LAKH|LAKHS)?)",body)
    price=""
    if pm:
        price=re.sub(r"\s+","",pm.group(1))
        price=price.replace("CRORES","CR").replace("CRORE","CR").replace("LAKHS","L").replace("LAKH","L").replace("LAC","L")

    return {
        "ref":ref,
        "area_value":area_value,
        "area_unit":area_unit,
        "floor":floor,
        "bedrooms":bedrooms,
        "price":price,
        "phones":_phones(raw),
        "_raw_line":raw
    }

def _canon(r):
    return {
      "ref":_norm_ref(r.get("ref")),
      "area_value":str(r.get("area_value") or ""),
      "area_unit":str(r.get("area_unit") or "").upper(),
      "floor":str(r.get("floor") or "").upper().replace(" ","").strip("+"),
      "bedrooms":str(r.get("bedrooms") or ""),
      "price":str(r.get("price") or "").upper().replace(" ",""),
      "phones":sorted([re.sub(r"\D","",str(x)) for x in (r.get("phones") or []) if re.sub(r"\D","",str(x))])
    }

def _load_source_exam(engine):
    with engine.connect() as c:
        row=c.execute(text("""
          SELECT exam_id,status,prediction_freeze_sha256,result
          FROM alliance_magazine_pixel_field_v2_exams
          WHERE exam_id=:e LIMIT 1
        """),{"e":EXPECTED_V2_EXAM_ID}).first()
    return dict(row._mapping) if row else None

def _validate_source(source):
    if not source:raise RuntimeError("Frozen 6.1 exam not found")
    if source.get("status")!=EXPECTED_V2_STATUS:
        raise RuntimeError(f"Unexpected 6.1 status: {source.get('status')}")
    if source.get("prediction_freeze_sha256")!=EXPECTED_V2_PREDICTION_FREEZE:
        raise RuntimeError("Frozen 6.1 prediction hash changed")

def run_once(core):
    if not _LOCK.acquire(blocking=False):
        return {"status":"SKIPPED","reason":"VISION_LAB_ALREADY_RUNNING"}
    try:
        engine=_engine(core);client=_client(core)
        if engine is None:raise RuntimeError("Core engine unavailable")
        if client is None:raise RuntimeError("GEMINI client unavailable")
        if semantic_student.VERSION!=EXPECTED_SEMANTIC:
            raise RuntimeError(f"Semantic student changed: {semantic_student.VERSION}")

        with engine.begin() as c:c.execute(text(DDL))
        source=_load_source_exam(engine);_validate_source(source)

        model=os.getenv("GEMINI_MODEL","gemini-3.1-flash-lite")
        truth=frozen_v2.TRUTH
        pages=frozen_v2.PAGE_IMAGES_B64

        refs_by_page=defaultdict(list)
        for t in truth:refs_by_page[int(t["page"])].append(t["ref"])

        preds=[]
        methods={}
        for page,refs in sorted(refs_by_page.items()):
            image=base64.b64decode(pages[str(page)])
            lines,used=locate_training_lines(client,image,refs,model)
            methods[str(page)]=used
            for ref in refs:
                preds.append({"page":page,**parse_line(ref,lines.get(ref,""))})

        by={(int(r["page"]),_norm_ref(r["ref"])):_canon(r) for r in preds}
        errors=[];total=0;correct=0;cases=[]
        field_stats={f:{"correct":0,"total":0} for f in FIELDS}
        for t in truth:
            exp=_canon(t);got=by.get((int(t["page"]),exp["ref"]),{})
            ce=[]
            for f in FIELDS:
                total+=1;field_stats[f]["total"]+=1
                gv=got.get(f,[] if f=="phones" else "")
                if gv==exp[f]:
                    correct+=1;field_stats[f]["correct"]+=1
                else:
                    e={"case_id":t["case_id"],"page":t["page"],"ref":t["ref"],"field":f,"expected":exp[f],"got":gv,
                       "method":methods.get(str(t["page"]),{}).get(t["ref"])}
                    errors.append(e);ce.append(e)
            cases.append({"case_id":t["case_id"],"passed":not ce,"errors":ce})

        for f,s in field_stats.items():
            s["accuracy"]=round(100*s["correct"]/max(s["total"],1),4)
        accuracy=round(100*correct/max(total,1),4)
        status="TRAINING_PASS" if correct==total else "TRAINING_HOLD"

        result={
          "version":VERSION,"mode":MODE,"status":status,
          "source_exam":{"exam_id":EXPECTED_V2_EXAM_ID,"original_status":source["status"],
                         "prediction_freeze_sha256":source["prediction_freeze_sha256"],"preserved_immutable":True},
          "training":{
            "total_cases":len(truth),"total_checks":total,"correct_checks":correct,"accuracy":accuracy,
            "critical_errors":len(errors),"case_passes":sum(1 for x in cases if x["passed"]),
            "case_accuracy":round(100*sum(1 for x in cases if x["passed"])/len(cases),4),
            "field_accuracy":field_stats,"errors":errors,"cases":cases,
            "locator_methods":methods
          },
          "lessons":{
            "blank_rows":"Use full-page batch + single-ref full-page + 12-band fallback; do not trust one crop strategy.",
            "phone_parser":"Accept exact 10-digit mobiles and 11-digit 0-prefixed landlines only; expand slash shorthand deterministically; reject artificial 8/12-digit overlaps.",
            "ownership":"Fields are parsed only from the exact reference-owned raw line."
          },
          "next_gate":"BUILD_FRESH_UNSEEN_PIXEL_FIELD_V3" if status=="TRAINING_PASS" else "AUTO_REPAIR_VISION_LAB_ON_REMAINING_FAILURES",
          "safety":{"fresh_exam_pages_consumed":0,"source_exam_mutations":0,"student_tuning_during_exam":0,
                    "canonical_property_writes":0,"canonical_requirement_writes":0,"gold_mutations":0,
                    "champion_mutations":0,"semantic_student_mutations":0}
        }

        with engine.begin() as c:
            c.execute(text("""INSERT INTO alliance_magazine_vision_lab_runs(
              version,source_exam_id,source_prediction_freeze_sha256,total_cases,total_checks,
              correct_checks,accuracy,status,result
            ) VALUES(:v,:e,:p,:tc,:tch,:cc,:a,:s,CAST(:r AS JSONB))"""),
            {"v":VERSION,"e":EXPECTED_V2_EXAM_ID,"p":source["prediction_freeze_sha256"],
             "tc":len(truth),"tch":total,"cc":correct,"a":accuracy,"s":status,
             "r":json.dumps(result,ensure_ascii=False)})
        STATE["result"]=result;STATE["status"]=status;STATE["last_error"]=None
        return result
    except Exception as exc:
        STATE["status"]="ERROR";STATE["last_error"]=f"{type(exc).__name__}: {exc}"
        return {"version":VERSION,"status":"ERROR","error":STATE["last_error"]}
    finally:
        _LOCK.release()

def status(core):return STATE["result"] or run_once(core)

def dashboard(core):
    s=status(core);t=s.get("training") or {}
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Magazine Vision Lab 6.2</title><style>
body{{font-family:Arial;background:#f5f7fb;color:#172033;margin:0}}header{{background:#102235;color:white;padding:18px}}
.wrap{{max-width:1350px;margin:auto;padding:18px}}.card{{background:white;border:1px solid #e2e8f0;border-radius:12px;padding:14px;margin-bottom:12px}}
pre{{white-space:pre-wrap;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px;overflow:auto}}</style></head>
<body><header><b>Alliance Magazine Vision Field Laboratory 6.2</b><br><small>6.1 frozen · failure training only · no fresh exam pages consumed</small></header>
<div class='wrap'><div class='card'><b>{html.escape(str(s.get("status")))}</b><br>
Training accuracy {html.escape(str(t.get("accuracy")))}% · Checks {html.escape(str(t.get("correct_checks")))} / {html.escape(str(t.get("total_checks")))}</div>
<pre>{html.escape(json.dumps(s,ensure_ascii=False,indent=2))}</pre></div></body></html>"""

def register(core):
    app=_app(core)
    if not _route_exists(app,"/api/property-brain/magazine-vision-lab-v620/status"):
        @app.get("/api/property-brain/magazine-vision-lab-v620/status")
        def _status():return status(core)
    if not _route_exists(app,"/property-brain/magazine-vision-lab-v620"):
        @app.get("/property-brain/magazine-vision-lab-v620",response_class=HTMLResponse)
        def _page():return HTMLResponse(dashboard(core))
    return {"status":"REGISTERED","version":VERSION,"route":"/property-brain/magazine-vision-lab-v620"}

def _runner(core):
    time.sleep(int(os.getenv("ALLIANCE_MAGAZINE_VISION_LAB_DELAY","50")))
    run_once(core)

def start(core):
    global _STARTED
    register(core)
    if _STARTED:return STATE
    _STARTED=True
    threading.Thread(target=_runner,args=(core,),name="magazine-vision-lab-v620",daemon=True).start()
    return STATE
