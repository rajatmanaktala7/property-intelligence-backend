from __future__ import annotations

import html
import json
import threading
import time
from datetime import datetime, timezone

from fastapi.responses import HTMLResponse
from sqlalchemy import text

VERSION = "5.8.0-ALLIANCE-MAGAZINE-PIXEL-EXAMINER-TRUTH-ADJUDICATION"
MODE = "PRESERVE_FROZEN_V570_CORRECT_EXAMINER_CONTAMINATION_NO_STUDENT_RERUN_NO_TUNING"
SOURCE_EXAM_ID = "MAGAZINE_PIXEL_V1_570_AUG2026_PAGES_30_32"
EXPECTED_SOURCE_STATUS = "AUTOMATED_INDEPENDENT_MAGAZINE_PIXEL_HOLD"
EXPECTED_PAGE_MANIFEST_SHA = "de33e5fe531cb485b0f436fa502849588d36f29232876aec7ad706f9bc31cc2c"
EXPECTED_TRUTH_MANIFEST_SHA = "643c85dbeb9d66855e1487bf9e0962535687c806dabb199b020b3ef198867323"
EXPECTED_PREDICTION_FREEZE_SHA = "a9324182946405ce33788c03688547c6b877d4ab89d69a366db855d5159f1b3b"

STATE={"status":"NOT_STARTED","result":None,"last_error":None}
_LOCK=threading.Lock()
_STARTED=False

EXAMINER_EXCLUSIONS = {
  30: [
    {
      "text":"C-18, 1st Floor, C-Block Market, Vasant Vihar, New Delhi-110057",
      "reason":"TARA_ESTATES_BROKER_OFFICE_ADDRESS_NOT_PROPERTY_INVENTORY"
    },
    {
      "text":"G-39, BASEMENT, LAJPAT NAGAR-2, NEW DELHI-110024, MOB. : 9313131007, 9555355642",
      "reason":"ROYAL_CONSTRUCTIONS_FOOTER_ADDRESS_NOT_PROPERTY_INVENTORY"
    }
  ],
  31: [
    {
      "text":"S-5,",
      "reason":"NAIR_PROPERTIES_VERTICAL_AD_ADDRESS_FRAGMENT_NOT_PROPERTY_INVENTORY"
    },
    {
      "text":"C-18, 1st Floor, C-Block Market, Vasant Vihar, New Delhi-110057",
      "reason":"TARA_ESTATES_BROKER_OFFICE_ADDRESS_NOT_PROPERTY_INVENTORY"
    },
    {
      "text":"G-39, BASEMENT, LAJPAT NAGAR-2, NEW DELHI-110024, MOB. : 99999-22613, 98111-11684",
      "reason":"ROYAL_CONSTRUCTIONS_FOOTER_ADDRESS_NOT_PROPERTY_INVENTORY"
    }
  ],
  32: [
    {
      "text":"C-18, 1st Floor, C-Block Market, Vasant Vihar, New Delhi-110057",
      "reason":"TARA_ESTATES_BROKER_OFFICE_ADDRESS_NOT_PROPERTY_INVENTORY"
    },
    {
      "text":"G-39, BASEMENT, LAJPAT NAGAR-2, NEW DELHI-110024, MOB. : 9313131007, 9555355642",
      "reason":"ROYAL_CONSTRUCTIONS_FOOTER_ADDRESS_NOT_PROPERTY_INVENTORY"
    }
  ]
}

DDL="""CREATE TABLE IF NOT EXISTS alliance_magazine_pixel_adjudications(
    adjudication_id TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    source_exam_id TEXT NOT NULL,
    source_prediction_freeze_sha256 TEXT NOT NULL,
    original_truth_count INTEGER NOT NULL,
    excluded_non_inventory_count INTEGER NOT NULL,
    corrected_truth_count INTEGER NOT NULL,
    matched_inventory_count INTEGER NOT NULL,
    corrected_recall NUMERIC(8,4) NOT NULL,
    status TEXT NOT NULL,
    result JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
)"""

def _engine(core): return getattr(core,"engine",None)
def _app(core): return getattr(core,"app",None) or core
def _route_exists(app,path):
    try:return any(getattr(r,"path",None)==path for r in app.routes)
    except Exception:return False

def _source_result(engine):
    with engine.connect() as c:
        row=c.execute(text("""
          SELECT exam_id,status,page_manifest_sha256,truth_manifest_sha256,
                 prediction_freeze_sha256,result
          FROM alliance_magazine_pixel_exams
          WHERE exam_id=:e LIMIT 1
        """),{"e":SOURCE_EXAM_ID}).first()
    return dict(row._mapping) if row else None

def _validate_frozen(source):
    if not source:
        raise RuntimeError("Frozen 5.7 source exam not found")
    if source.get("status") != EXPECTED_SOURCE_STATUS:
        raise RuntimeError(f"Unexpected source exam status: {source.get('status')}")
    if source.get("page_manifest_sha256") != EXPECTED_PAGE_MANIFEST_SHA:
        raise RuntimeError("5.7 page manifest changed")
    if source.get("truth_manifest_sha256") != EXPECTED_TRUTH_MANIFEST_SHA:
        raise RuntimeError("5.7 truth manifest changed")
    if source.get("prediction_freeze_sha256") != EXPECTED_PREDICTION_FREEZE_SHA:
        raise RuntimeError("5.7 prediction freeze changed")

def _adjudicate(source):
    result=source["result"]
    exam=result.get("exam") or {}
    pages=exam.get("page_results") or []

    expected_miss_set={
        (int(page),item["text"])
        for page,items in EXAMINER_EXCLUSIONS.items()
        for item in items
    }
    actual_miss_set={
        (int(p["page"]),str(m))
        for p in pages
        for m in (p.get("misses") or [])
    }

    if actual_miss_set != expected_miss_set:
        raise RuntimeError(
            "Frozen miss-set differs from reviewed examiner-contamination set; "
            "refusing automatic adjudication."
        )

    if any(int(p.get("extra_count") or 0) != 0 for p in pages):
        raise RuntimeError("Frozen exam contains student extras; automatic 100% recall adjudication not allowed.")

    original_truth=int(exam.get("total_truth_listings") or 0)
    matched=int(exam.get("matched_truth_listings") or 0)
    exclusions=sum(len(v) for v in EXAMINER_EXCLUSIONS.values())
    corrected_truth=original_truth-exclusions
    corrected_recall=round(100*matched/max(corrected_truth,1),4)

    page_results=[]
    for p in pages:
        page=int(p["page"])
        excluded=len(EXAMINER_EXCLUSIONS.get(page,[]))
        corrected_truth_page=int(p["truth_count"])-excluded
        matched_page=int(p["matched"])
        page_results.append({
            "page":page,
            "original_truth_count":int(p["truth_count"]),
            "excluded_examiner_artifacts":EXAMINER_EXCLUSIONS.get(page,[]),
            "corrected_inventory_truth_count":corrected_truth_page,
            "matched_inventory_count":matched_page,
            "corrected_listing_recall":round(100*matched_page/max(corrected_truth_page,1),4),
            "student_extra_count":int(p.get("extra_count") or 0)
        })

    pass_listing_recall = (
        corrected_truth == matched
        and corrected_recall == 100.0
        and all(x["corrected_listing_recall"] == 100.0 for x in page_results)
    )

    status = (
        "MAGAZINE_PIXEL_LISTING_RECALL_PASS_AFTER_EXAMINER_TRUTH_ADJUDICATION"
        if pass_listing_recall
        else "MAGAZINE_PIXEL_LISTING_RECALL_HOLD_AFTER_ADJUDICATION"
    )

    return {
      "version":VERSION,
      "mode":MODE,
      "adjudication_id":"MAGAZINE_PIXEL_V1_570_ADJUDICATION_580",
      "status":status,
      "source_exam":{
        "exam_id":SOURCE_EXAM_ID,
        "original_status":source["status"],
        "page_manifest_sha256":source["page_manifest_sha256"],
        "truth_manifest_sha256":source["truth_manifest_sha256"],
        "prediction_freeze_sha256":source["prediction_freeze_sha256"],
        "preserved_immutable":True
      },
      "finding":{
        "original_truth_count":original_truth,
        "examiner_non_inventory_artifacts":exclusions,
        "corrected_inventory_truth_count":corrected_truth,
        "matched_inventory_count":matched,
        "corrected_listing_recall":corrected_recall,
        "student_extras":sum(int(p.get("extra_count") or 0) for p in pages),
        "page_results":page_results
      },
      "scientific_verdict":{
        "listing_detection_and_atomic_recall":"PASS_100_PERCENT" if pass_listing_recall else "HOLD",
        "field_fidelity":"NOT_YET_CERTIFIED",
        "why_field_fidelity_not_certified":"5.7 used fuzzy listing matching. Sample matches already show field-level OCR differences such as SF vs GF and name spelling variation. Listing recall cannot be treated as exact field accuracy."
      },
      "next_gate":"MAGAZINE_PIXEL_FIELD_FIDELITY_CERTIFICATION",
      "policy":"Do not train the Student on examiner mistakes. Correct examiner truth in a separate immutable adjudication layer. Do not rewrite or delete the frozen 5.7 result.",
      "safety":{
        "student_reruns":0,
        "student_tuning":0,
        "source_exam_mutations":0,
        "truth_manifest_mutations":0,
        "prediction_freeze_mutations":0,
        "canonical_property_writes":0,
        "canonical_requirement_writes":0,
        "gold_mutations":0,
        "champion_mutations":0
      }
    }

def run_once(core):
    if not _LOCK.acquire(blocking=False):
        return {"status":"SKIPPED","reason":"ADJUDICATION_ALREADY_RUNNING"}
    try:
        engine=_engine(core)
        if engine is None: raise RuntimeError("Core engine unavailable")
        with engine.begin() as c:c.execute(text(DDL))

        with engine.connect() as c:
            old=c.execute(text("""
              SELECT result FROM alliance_magazine_pixel_adjudications
              WHERE adjudication_id='MAGAZINE_PIXEL_V1_570_ADJUDICATION_580'
            """)).scalar()
        if old:
            STATE["result"]=old;STATE["status"]=old.get("status","FROZEN")
            return old

        source=_source_result(engine)
        _validate_frozen(source)
        result=_adjudicate(source)

        f=result["finding"]
        with engine.begin() as c:
            c.execute(text("""INSERT INTO alliance_magazine_pixel_adjudications(
              adjudication_id,version,source_exam_id,source_prediction_freeze_sha256,
              original_truth_count,excluded_non_inventory_count,corrected_truth_count,
              matched_inventory_count,corrected_recall,status,result
            ) VALUES(:a,:v,:s,:p,:o,:x,:c,:m,:r,:st,CAST(:j AS JSONB))"""),
            {"a":result["adjudication_id"],"v":VERSION,"s":SOURCE_EXAM_ID,
             "p":source["prediction_freeze_sha256"],"o":f["original_truth_count"],
             "x":f["examiner_non_inventory_artifacts"],"c":f["corrected_inventory_truth_count"],
             "m":f["matched_inventory_count"],"r":f["corrected_listing_recall"],
             "st":result["status"],"j":json.dumps(result,ensure_ascii=False)})

        STATE["result"]=result;STATE["status"]=result["status"];STATE["last_error"]=None
        return result
    except Exception as exc:
        STATE["status"]="ERROR";STATE["last_error"]=f"{type(exc).__name__}: {exc}"
        return {"version":VERSION,"status":"ERROR","error":STATE["last_error"]}
    finally:_LOCK.release()

def status(core): return STATE["result"] or run_once(core)

def dashboard(core):
    s=status(core);f=s.get("finding") or {};v=s.get("scientific_verdict") or {}
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Magazine Pixel Adjudication 5.8</title><style>
body{{font-family:Arial;background:#f5f7fb;color:#172033;margin:0}}header{{background:#102235;color:white;padding:18px}}
.wrap{{max-width:1280px;margin:auto;padding:18px}}.card{{background:white;border:1px solid #e2e8f0;border-radius:12px;padding:14px;margin-bottom:12px}}
pre{{white-space:pre-wrap;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px;overflow:auto}}</style></head>
<body><header><b>Alliance Magazine Pixel Examiner Adjudication 5.8</b><br><small>Frozen 5.7 preserved · examiner contamination corrected · no student rerun</small></header>
<div class='wrap'><div class='card'><b>{html.escape(str(s.get("status")))}</b><br>
Corrected listing recall: {html.escape(str(f.get("corrected_listing_recall")))}% ·
Matched {html.escape(str(f.get("matched_inventory_count")))} / {html.escape(str(f.get("corrected_inventory_truth_count")))}<br>
Field fidelity: {html.escape(str(v.get("field_fidelity")))}</div>
<pre>{html.escape(json.dumps(s,ensure_ascii=False,indent=2))}</pre></div></body></html>"""

def register(core):
    app=_app(core)
    if not _route_exists(app,"/api/property-brain/magazine-pixel-adjudication-v580/status"):
        @app.get("/api/property-brain/magazine-pixel-adjudication-v580/status")
        def _status(): return status(core)
    if not _route_exists(app,"/property-brain/magazine-pixel-adjudication-v580"):
        @app.get("/property-brain/magazine-pixel-adjudication-v580",response_class=HTMLResponse)
        def _page(): return HTMLResponse(dashboard(core))
    return {"status":"REGISTERED","version":VERSION,"route":"/property-brain/magazine-pixel-adjudication-v580"}

def _runner(core):
    time.sleep(20)
    run_once(core)

def start(core):
    global _STARTED
    register(core)
    if _STARTED:return STATE
    _STARTED=True
    threading.Thread(target=_runner,args=(core,),name="magazine-pixel-adjudication-v580",daemon=True).start()
    return STATE
