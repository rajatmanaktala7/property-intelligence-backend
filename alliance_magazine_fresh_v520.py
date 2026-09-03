from __future__ import annotations

import hashlib
import html
import inspect
import json
import os
import threading
import time
from datetime import datetime, timezone

from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_magazine_academy_v510 as student
import alliance_autonomous_student_v438 as champion

VERSION = "5.2.0-ALLIANCE-FRESH-MAGAZINE-BLIND-CERTIFICATION"
MODE = "FROZEN_STUDENT_PREDICTIONS_BEFORE_INDEPENDENT_TRUTH_NO_TUNING_NO_PRODUCTION_WRITES"
EXAM_ID = "MAGAZINE_FRESH_BLIND_V1_520_2026_09_03"
EXPECTED_STUDENT_VERSION = "5.1.1-ALLIANCE-MAGAZINE-AUTONOMOUS-ACADEMY-SEMANTIC-CLOSURE"
EXPECTED_CHAMPION_VERSION = "4.3.8-ALLIANCE-STUDENT-DEMAND-OBJECT-CLOSURE"
EXPECTED_CHAMPION_SHA = "8b5014fa5ec5e38b75f4da15945b357230573c88a096be9c4b2e0609ecd52694"

STATE = {"status":"NOT_STARTED","result":None,"last_error":None}
_STARTED = False
_LOCK = threading.Lock()

DDL = [
    """CREATE TABLE IF NOT EXISTS alliance_magazine_fresh_exams(
        exam_id TEXT PRIMARY KEY,
        version TEXT NOT NULL,
        student_version TEXT NOT NULL,
        student_source_sha256 TEXT NOT NULL,
        champion_sha256 TEXT NOT NULL,
        case_manifest_sha256 TEXT NOT NULL,
        prediction_freeze_sha256 TEXT NOT NULL,
        frozen_at TIMESTAMPTZ NOT NULL,
        status TEXT NOT NULL,
        total_cases INTEGER NOT NULL,
        total_checks INTEGER NOT NULL,
        correct_checks INTEGER NOT NULL,
        accuracy NUMERIC(8,4) NOT NULL,
        critical_errors INTEGER NOT NULL,
        result JSONB NOT NULL,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )""",
]

# These are fresh page-like classified blocks. None are part of the 5.1.1
# training curriculum. Inputs and examiner truth are deliberately separated.
CASE_INPUTS = [
    {"id":"F01","row":{"source_id":"F01","listing_type":"Available","category":"Commercial","locality":"Nehru Place","area":"1850","area_unit":"SQFT","price":"₹3.75 Cr","valid_mobiles":"9811122233","original_raw_text":"Nehru Place office 1850 sq ft, fully leased to MNC, monthly rent 2.10 lac. Available for sale at ₹3.75 Cr."}},
    {"id":"F02","row":{"source_id":"F02","listing_type":"Wanted","category":"Retail","locality":"Saket","area":"3500","area_unit":"SQFT","price":"","valid_mobiles":"9876543210","original_raw_text":"Urgently required 3000-3500 sqft ground floor retail space on lease in Saket for premium restaurant brand."}},
    {"id":"F03","row":{"source_id":"F03","listing_type":"Sale / Rent","category":"Industrial","locality":"Manesar","area":"2200","area_unit":"SQM","price":"On request","valid_mobiles":"9899991111","original_raw_text":"IMT Manesar industrial building 2200 sq m, clear height 24 ft, available sale or lease."}},
    {"id":"F04","row":{"source_id":"F04","listing_type":"Sale","category":"Residential","locality":"Vasant Vihar","area":"600","area_unit":"SQYD","price":"₹18 Cr","valid_mobiles":"9810012345","original_raw_text":"Vasant Vihar bungalow plot 600 sq yards, clear title, for sale ₹18 Cr."}},
    {"id":"F05","row":{"source_id":"F05","listing_type":"Rent","category":"Office","locality":"Golf Course Road","area":"12500","area_unit":"SQFT","price":"₹160/sqft/month","valid_mobiles":"9910012233","original_raw_text":"Grade A office 12,500 sqft on Golf Course Road available for lease at ₹160 per sqft per month."}},
    {"id":"F06","row":{"source_id":"F06","listing_type":"Sale","category":"Commercial","locality":"Sector 18 Noida","area":"900","area_unit":"SQFT","price":"18","valid_mobiles":"9811002233","original_raw_text":"Sector 18 Noida shop 900 sq ft available for sale. Price on request."}},
    {"id":"F07","row":{"source_id":"F07","listing_type":"Rent","category":"Hospitality","locality":"Aerocity","area":"18000","area_unit":"SQFT","price":"₹22 Lac/month","valid_mobiles":"9811919191","original_raw_text":"Operational restaurant space 18,000 sq ft in Aerocity, available on long lease, rent ₹22 lakh/month."}},
    {"id":"F08","row":{"source_id":"F08","listing_type":"Sale","category":"Land","locality":"Sohna","area":"4.5","area_unit":"ACRE","price":"₹21 Cr","valid_mobiles":"9811777888","original_raw_text":"Sohna road frontage land 4.5 acres, institutional use, outright sale asking ₹21 crore."}},
    {"id":"F09","row":{"source_id":"F09","listing_type":"Available","category":"Commercial","locality":"Cyber City","area":"5000","area_unit":"SQFT","price":"₹6.8 Cr","valid_mobiles":"9822001100","original_raw_text":"Pre-leased office in Cyber City, 5000 sqft, tenant multinational company, rent 3.2 lac pm, asking 6.8 Cr."}},
    {"id":"F10","row":{"source_id":"F10","listing_type":"Requirement - Buy","category":"Industrial","locality":"Faridabad","area":"2","area_unit":"ACRE","price":"Budget ₹15 Cr","valid_mobiles":"9999912345","original_raw_text":"Client wants to purchase 1.5 to 2 acre industrial plot in Faridabad. Budget up to ₹15 Cr."}},
    {"id":"F11","row":{"source_id":"F11","listing_type":"Sale","category":"Residential","locality":"Greater Kailash 1","area":"300","area_unit":"SQYD","price":"₹9.25 Cr","valid_mobiles":"9810099999","original_raw_text":"GK-1 builder floor, plot 300 sq yds, 4 BHK, second floor with lift and parking. For sale ₹9.25 Cr."}},
    {"id":"F12","row":{"source_id":"F12","listing_type":"Rent","category":"Retail","locality":"Ground Floor 2168 sqft","area":"2168","area_unit":"SQFT","price":"₹5.5 Lac pm","valid_mobiles":"9810555666","original_raw_text":"Connaught Place ground floor showroom 2168 sqft, rent ₹5.5 lakh pm."}},
    {"id":"F13","row":{"source_id":"F13","listing_type":"Sale","category":"Commercial","locality":"Dwarka","area":"1200","area_unit":"SQFT","price":"₹2.4 Cr","valid_mobiles":"9911223344","original_raw_text":"Dwarka sector 12 pre-rented retail shop 1200 sqft, leased to pharmacy, ₹1.35 lac monthly rent, sale ₹2.4 Cr."}},
    {"id":"F14","row":{"source_id":"F14","listing_type":"Sale","category":"Residential","locality":"South Delhi","area":"","area_unit":"","price":"Varies","valid_mobiles":"9810011111","original_raw_text":"Available options: Defence Colony 325 yds ₹14 Cr; Panchsheel Park 500 yds ₹22 Cr; Vasant Vihar 600 yds ₹28 Cr."}},
    {"id":"F15","row":{"source_id":"F15","listing_type":"Rent","category":"Office","locality":"Okhla Phase 3","area":"1000","area_unit":"SQM","price":"₹12 Lac pm","valid_mobiles":"9811888999","original_raw_text":"Okhla Phase III office building, 1000 sq m built-up, available for rent ₹12 lakh per month."}},
    {"id":"F16","row":{"source_id":"F16","listing_type":"Sale","category":"Hotel","locality":"Paharganj","area":"12000","area_unit":"SQFT","price":"₹28 Cr","valid_mobiles":"99107880","original_raw_text":"3-star hotel Paharganj, 42 rooms, built-up 12,000 sqft, for sale ₹28 Cr. Contact 99107880."}},
    {"id":"F17","row":{"source_id":"F17","listing_type":"Wanted","category":"Warehouse","locality":"NH-8","area":"50000","area_unit":"SQFT","price":"","valid_mobiles":"9810707070","original_raw_text":"Required warehouse 40,000-50,000 sqft on rent near NH-8 / Bilaspur for logistics company."}},
    {"id":"F18","row":{"source_id":"F18","listing_type":"Sale","category":"Land","locality":"Chattarpur","area":"1000","area_unit":"SQYD","price":"₹8 Cr","valid_mobiles":"9810888777","original_raw_text":"Chattarpur farmhouse land options 1000 sq yd, 2000 sq yd and 2 acre, sale. Starting ₹8 Cr."}},
    {"id":"F19","row":{"source_id":"F19","listing_type":"Lease","category":"Commercial","locality":"Rajouri Garden","area":"4200","area_unit":"SQFT","price":"₹6 Lac pm","valid_mobiles":"9898989898","original_raw_text":"Rajouri Garden main market showroom 4200 sqft available on lease, asking rent ₹6 lakh pm."}},
    {"id":"F20","row":{"source_id":"F20","listing_type":"Sale","category":"Industrial","locality":"Bawal","area":"150000","area_unit":"SQFT","price":"₹48 Cr","valid_mobiles":"9811333444","original_raw_text":"Bawal industrial facility, land 5 acres, covered area 150,000 sq ft, operational factory, outright sale ₹48 Cr."}},
]

# Independent examiner truth. The prediction phase below does not read this.
TRUTH = {
    "F01":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"COMMERCIAL","occupancy":"TENANTED","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
    "F02":{"class":"REQUIREMENT","transaction":"RENT","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"UNKNOWN","locality":"VALID_OR_UNPROVEN"},
    "F03":{"class":"PROPERTY_AVAILABILITY","transaction":"AMBIGUOUS","asset":"INDUSTRIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"TEXT_PRICE","locality":"VALID_OR_UNPROVEN"},
    "F04":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"LAND_OR_PLOT","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
    "F05":{"class":"PROPERTY_AVAILABILITY","transaction":"RENT","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"RATE_OR_RENT_RATE","locality":"VALID_OR_UNPROVEN"},
    "F06":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"BARE_NUMBER","locality":"VALID_OR_UNPROVEN","must_reason":"NUMERIC_PRICE_WITHOUT_MONEY_EVIDENCE"},
    "F07":{"class":"PROPERTY_AVAILABILITY","transaction":"RENT","asset":"HOSPITALITY","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
    "F08":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"LAND_OR_PLOT","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
    "F09":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"COMMERCIAL","occupancy":"TENANTED","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
    "F10":{"class":"REQUIREMENT","transaction":"SALE","asset":"INDUSTRIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"TEXT_PRICE","locality":"VALID_OR_UNPROVEN"},
    "F11":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"RESIDENTIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
    "F12":{"class":"PROPERTY_AVAILABILITY","transaction":"RENT","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"POLLUTED","must_reason":"LOCALITY_FRAGMENT_OR_MISSING"},
    "F13":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"COMMERCIAL","occupancy":"TENANTED","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
    "F14":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"RESIDENTIAL","occupancy":"UNKNOWN","atomicity":"GROUP_PARENT","price_kind":"TEXT_PRICE","locality":"VALID_OR_UNPROVEN","must_reason":"MULTI_PROPERTY_REQUIRES_ATOMIC_SPLIT"},
    "F15":{"class":"PROPERTY_AVAILABILITY","transaction":"RENT","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
    "F16":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"HOSPITALITY","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN","contact_quality":"OCR_CONFLICT_OR_INVALID","must_reason":"PHONE_INVALID_OR_OCR_CONFLICT"},
    "F17":{"class":"REQUIREMENT","transaction":"RENT","asset":"INDUSTRIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"UNKNOWN","locality":"VALID_OR_UNPROVEN"},
    "F18":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"LAND_OR_PLOT","occupancy":"UNKNOWN","atomicity":"GROUP_PARENT","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN","must_reason":"MULTI_PROPERTY_REQUIRES_ATOMIC_SPLIT"},
    "F19":{"class":"PROPERTY_AVAILABILITY","transaction":"RENT","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
    "F20":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"INDUSTRIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
}

CRITICAL_FIELDS = ("class","transaction","asset","occupancy","atomicity","price_kind","locality")

def _utc():
    return datetime.now(timezone.utc)

def _app(core):
    return getattr(core,"app",None) or core

def _engine(core):
    return getattr(core,"engine",None)

def _route_exists(app,path):
    try:
        return any(getattr(r,"path",None)==path for r in app.routes)
    except Exception:
        return False

def _install(engine):
    with engine.begin() as c:
        for stmt in DDL:
            c.execute(text(stmt))

def _sha_text(s):
    return hashlib.sha256(str(s).encode("utf-8")).hexdigest()

def _student_sha():
    src = inspect.getsource(student.analyze) + inspect.getsource(student._transaction) + inspect.getsource(student._classify) + inspect.getsource(student._multi_property)
    return _sha_text(src)

def _champion_sha():
    src = inspect.getsource(champion.predict_message) + inspect.getsource(champion.leading_demand_object)
    return _sha_text(src)

def _manifest_sha():
    payload = json.dumps(CASE_INPUTS,sort_keys=True,ensure_ascii=False,separators=(",",":"))
    return _sha_text(payload)

def _existing(engine):
    with engine.connect() as c:
        r=c.execute(text("SELECT result FROM alliance_magazine_fresh_exams WHERE exam_id=:e"),{"e":EXAM_ID}).scalar()
        return r

def _student_projection(a):
    evidence=a.get("evidence") or {}
    return {
        "class":a.get("predicted_class"),
        "transaction":a.get("predicted_transaction"),
        "asset":a.get("predicted_asset_class"),
        "occupancy":a.get("occupancy_status"),
        "atomicity":a.get("atomicity_status"),
        "price_kind":a.get("price_kind"),
        "locality":evidence.get("locality_status"),
        "contact_quality":a.get("contact_quality"),
        "reasons":sorted(a.get("risk_reasons") or []),
    }

def freeze_predictions():
    # IMPORTANT: this function has no access to TRUTH.
    predictions=[]
    for case in CASE_INPUTS:
        a=student.analyze(dict(case["row"]))
        predictions.append({"id":case["id"],"prediction":_student_projection(a)})
    payload=json.dumps(predictions,sort_keys=True,ensure_ascii=False,separators=(",",":"))
    return predictions,_sha_text(payload)

def examiner_grade(predictions):
    # Independent examiner runs only after prediction payload has been frozen.
    errors=[]
    total=0
    correct=0
    critical_errors=0
    per_case=[]
    by_id={x["id"]:x["prediction"] for x in predictions}
    for cid, truth in TRUTH.items():
        pred=by_id[cid]
        case_errors=[]
        for field in CRITICAL_FIELDS:
            total+=1
            exp=truth.get(field)
            got=pred.get(field)
            if got==exp:
                correct+=1
            else:
                critical_errors+=1
                err={"id":cid,"field":field,"expected":exp,"got":got}
                errors.append(err); case_errors.append(err)
        if "contact_quality" in truth:
            total+=1
            if pred.get("contact_quality")==truth["contact_quality"]:
                correct+=1
            else:
                critical_errors+=1
                err={"id":cid,"field":"contact_quality","expected":truth["contact_quality"],"got":pred.get("contact_quality")}
                errors.append(err); case_errors.append(err)
        if "must_reason" in truth:
            total+=1
            if truth["must_reason"] in pred.get("reasons",[]):
                correct+=1
            else:
                critical_errors+=1
                err={"id":cid,"field":"must_reason","expected":truth["must_reason"],"got":pred.get("reasons",[])}
                errors.append(err); case_errors.append(err)
        per_case.append({"id":cid,"passed":not case_errors,"errors":case_errors})
    return {
        "total_cases":len(TRUTH),
        "total_checks":total,
        "correct_checks":correct,
        "accuracy":round(100*correct/max(total,1),4),
        "critical_errors":critical_errors,
        "case_passes":sum(1 for x in per_case if x["passed"]),
        "case_accuracy":round(100*sum(1 for x in per_case if x["passed"])/max(len(per_case),1),4),
        "errors":errors,
        "cases":per_case,
    }

def run_once(core):
    if not _LOCK.acquire(blocking=False):
        return {"status":"SKIPPED","reason":"EXAM_ALREADY_RUNNING"}
    try:
        engine=_engine(core)
        if engine is None:
            raise RuntimeError("Core engine unavailable")
        _install(engine)

        old=_existing(engine)
        if old:
            STATE["status"]=old.get("status","FROZEN") if isinstance(old,dict) else "FROZEN"
            STATE["result"]=old
            return old

        if student.VERSION != EXPECTED_STUDENT_VERSION:
            raise RuntimeError(f"Student version mismatch: {student.VERSION}")
        if champion.VERSION != EXPECTED_CHAMPION_VERSION:
            raise RuntimeError(f"Champion version mismatch: {champion.VERSION}")

        ch=_champion_sha()
        if ch != EXPECTED_CHAMPION_SHA:
            raise RuntimeError(f"Champion hash mismatch: {ch}")

        student_sha=_student_sha()
        manifest_sha=_manifest_sha()

        # Phase 1: predictions are frozen before the examiner sees truth.
        predictions,prediction_sha=freeze_predictions()
        frozen_at=_utc()

        # Phase 2: independent grading.
        grade=examiner_grade(predictions)
        status="AUTOMATED_INDEPENDENT_MAGAZINE_PASS" if grade["critical_errors"]==0 and grade["accuracy"]==100.0 else "AUTOMATED_INDEPENDENT_MAGAZINE_HOLD"

        result={
            "version":VERSION,
            "mode":MODE,
            "exam_id":EXAM_ID,
            "status":status,
            "student":{"version":student.VERSION,"source_sha256":student_sha,"frozen":True},
            "champion":{"version":champion.VERSION,"sha256":ch,"immutable":True},
            "freeze":{
                "case_manifest_sha256":manifest_sha,
                "prediction_freeze_sha256":prediction_sha,
                "frozen_at":frozen_at.isoformat(),
                "tuning_during_exam":False,
            },
            "exam":{
                **grade,
                "critical_pass_rule":"100% on all certified fields; any critical error = HOLD",
            },
            "next_gate":"MAGAZINE_IMAGE_EVIDENCE_CERTIFICATION" if status.endswith("_PASS") else "TRAIN_SEPARATE_MAGAZINE_CHALLENGER_ON_EXACT_FAILURES",
            "scientific_policy":"The student never grades itself. Predictions are frozen before independent truth grading. This V1 exam certifies magazine semantic reasoning on fresh unseen page-like cases; actual source-image vision remains a separate image-evidence gate.",
            "safety":{"production_writes":0,"source_mutations":0,"gold_mutations":0,"whatsapp_writes":0,"champion_mutations":0,"student_tuning_during_exam":0},
        }

        with engine.begin() as c:
            c.execute(text("""
                INSERT INTO alliance_magazine_fresh_exams(
                    exam_id,version,student_version,student_source_sha256,champion_sha256,
                    case_manifest_sha256,prediction_freeze_sha256,frozen_at,status,total_cases,
                    total_checks,correct_checks,accuracy,critical_errors,result
                ) VALUES(
                    :eid,:v,:sv,:ss,:cs,:ms,:ps,:fa,:st,:tc,:checks,:correct,:acc,:ce,CAST(:r AS JSONB)
                )
            """),{
                "eid":EXAM_ID,"v":VERSION,"sv":student.VERSION,"ss":student_sha,"cs":ch,
                "ms":manifest_sha,"ps":prediction_sha,"fa":frozen_at,"st":status,
                "tc":grade["total_cases"],"checks":grade["total_checks"],"correct":grade["correct_checks"],
                "acc":grade["accuracy"],"ce":grade["critical_errors"],
                "r":json.dumps(result,ensure_ascii=False),
            })

        STATE["status"]=status
        STATE["result"]=result
        STATE["last_error"]=None
        return result
    except Exception as exc:
        STATE["status"]="ERROR"
        STATE["last_error"]=f"{type(exc).__name__}: {exc}"
        return {"status":"ERROR","version":VERSION,"exam_id":EXAM_ID,"error":STATE["last_error"]}
    finally:
        _LOCK.release()

def status(core):
    if STATE.get("result"):
        return STATE["result"]
    return run_once(core)

def dashboard(core):
    s=status(core)
    exam=s.get("exam") or {}
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Magazine Fresh Certification 5.2</title><style>
body{{font-family:Arial;background:#f5f7fb;color:#172033;margin:0}}header{{background:#102235;color:white;padding:18px}}
.wrap{{max-width:1280px;margin:auto;padding:18px}}.card{{background:white;border:1px solid #e2e8f0;border-radius:12px;padding:14px;margin-bottom:12px}}
pre{{white-space:pre-wrap;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px;overflow:auto}}
</style></head><body><header><b>Alliance Magazine Fresh Blind Certification 5.2</b><br><small>Frozen predictions · independent truth · no tuning during exam</small></header>
<div class='wrap'><div class='card'><b>{html.escape(str(s.get("status")))}</b> · Accuracy {html.escape(str(exam.get("accuracy")))}% ·
Cases passed {html.escape(str(exam.get("case_passes")))} / {html.escape(str(exam.get("total_cases")))}</div>
<div class='card'>This semantic certification does not yet certify image/OCR vision. Passing advances to the separate magazine image-evidence gate.</div>
<pre>{html.escape(json.dumps(s,ensure_ascii=False,indent=2))}</pre></div></body></html>"""

def register(core):
    app=_app(core)
    if not _route_exists(app,"/api/property-brain/magazine-fresh-v520/status"):
        @app.get("/api/property-brain/magazine-fresh-v520/status")
        def fresh_status():
            return status(core)
    if not _route_exists(app,"/property-brain/magazine-fresh-v520"):
        @app.get("/property-brain/magazine-fresh-v520",response_class=HTMLResponse)
        def fresh_page():
            return HTMLResponse(dashboard(core))
    return {"status":"REGISTERED","version":VERSION,"route":"/property-brain/magazine-fresh-v520"}

def _runner(core):
    time.sleep(int(os.getenv("ALLIANCE_MAGAZINE_FRESH_EXAM_DELAY","35")))
    run_once(core)

def start(core):
    global _STARTED
    register(core)
    if _STARTED:
        return STATE
    _STARTED=True
    threading.Thread(target=_runner,args=(core,),name="alliance-magazine-fresh-v520",daemon=True).start()
    return STATE
