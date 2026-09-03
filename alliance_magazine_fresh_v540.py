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

import alliance_magazine_challenger_v513 as student
import alliance_autonomous_student_v438 as champion

VERSION="5.4.0-ALLIANCE-MAGAZINE-FRESH-BLIND-V3-CERTIFICATION"
MODE="NEW_UNSEEN_V3_CASES_FROZEN_PREDICTIONS_INDEPENDENT_TRUTH_NO_TUNING"
EXAM_ID="MAGAZINE_FRESH_BLIND_V3_540_2026_09_03"
EXPECTED_STUDENT="5.1.3-ALLIANCE-MAGAZINE-CHALLENGER-V2-FAILURE-CLOSURE"
EXPECTED_CHAMPION="4.3.8-ALLIANCE-STUDENT-DEMAND-OBJECT-CLOSURE"
EXPECTED_CHAMPION_SHA="8b5014fa5ec5e38b75f4da15945b357230573c88a096be9c4b2e0609ecd52694"

STATE={"status":"NOT_STARTED","result":None,"last_error":None}
_STARTED=False
_LOCK=threading.Lock()

DDL="""CREATE TABLE IF NOT EXISTS alliance_magazine_fresh_v3_exams(
 exam_id TEXT PRIMARY KEY, version TEXT NOT NULL, student_version TEXT NOT NULL,
 student_source_sha256 TEXT NOT NULL, champion_sha256 TEXT NOT NULL,
 case_manifest_sha256 TEXT NOT NULL, prediction_freeze_sha256 TEXT NOT NULL,
 frozen_at TIMESTAMPTZ NOT NULL, status TEXT NOT NULL,
 total_cases INTEGER NOT NULL,total_checks INTEGER NOT NULL,correct_checks INTEGER NOT NULL,
 accuracy NUMERIC(8,4) NOT NULL,critical_errors INTEGER NOT NULL,result JSONB NOT NULL,
 created_at TIMESTAMPTZ DEFAULT NOW()
)"""

CASE_INPUTS=[
 {"id":"V301","row":{"source_id":"V301","listing_type":"Wanted","category":"Office","locality":"Gurugram","area":"18000","area_unit":"SQFT","price":"Budget ₹15 Lac pm","valid_mobiles":"9811110001","original_raw_text":"Technology company seeking 15,000-18,000 sqft Grade A office premises on lease in Gurugram. Budget ₹15 lakh pm."}},
 {"id":"V302","row":{"source_id":"V302","listing_type":"Sale","category":"Residential","locality":"Jor Bagh","area":"575","area_unit":"SQYD","price":"₹42 Cr","valid_mobiles":"9811110002","original_raw_text":"Jor Bagh bungalow plot 575 sq yards with old structure, redevelopment potential, outright sale ₹42 Cr."}},
 {"id":"V303","row":{"source_id":"V303","listing_type":"Rent","category":"Commercial","locality":"Khan Market","area":"1200","area_unit":"SQFT","price":"₹5.8 Lac/month","valid_mobiles":"9811110003","original_raw_text":"Khan Market ground-floor shop 1200 sqft available on rent, ₹5.8 lakh per month."}},
 {"id":"V304","row":{"source_id":"V304","listing_type":"Sale","category":"Residential","locality":"Panchsheel Park","area":"500","area_unit":"SQYD","price":"₹16 Cr","valid_mobiles":"9811110004","original_raw_text":"Panchsheel Park builder floor on 500 sq yd plot, 4 BHK, upper ground floor, sale ₹16 Cr."}},
 {"id":"V305","row":{"source_id":"V305","listing_type":"Requirement - Buy","category":"Land","locality":"Dwarka Expressway","area":"8","area_unit":"ACRE","price":"Budget ₹120 Cr","valid_mobiles":"9811110005","original_raw_text":"Developer looking to acquire 5-8 acre land parcel near Dwarka Expressway for group housing. Budget up to ₹120 Cr."}},
 {"id":"V306","row":{"source_id":"V306","listing_type":"Sale","category":"Commercial","locality":"Golf Course Extension","area":"4800","area_unit":"SQFT","price":"₹7.4 Cr","valid_mobiles":"9811110006","original_raw_text":"Pre-leased office 4800 sqft on Golf Course Extension, leased to consulting firm at ₹3.1 lakh monthly, sale ₹7.4 Cr."}},
 {"id":"V307","row":{"source_id":"V307","listing_type":"Rent","category":"Warehouse","locality":"Tauru","area":"85000","area_unit":"SQFT","price":"₹19/sqft/month","valid_mobiles":"9811110007","original_raw_text":"Warehouse 85,000 sqft near Tauru available for rent at ₹19 per sqft per month."}},
 {"id":"V308","row":{"source_id":"V308","listing_type":"Sale / Rent","category":"Hotel","locality":"Goa","area":"45000","area_unit":"SQFT","price":"Terms on request","valid_mobiles":"9811110008","original_raw_text":"Boutique hotel 38 keys, 45,000 sqft built-up, Goa. Owner open to sale or long lease."}},
 {"id":"V309","row":{"source_id":"V309","listing_type":"Wanted","category":"Industrial","locality":"Greater Noida","area":"4","area_unit":"ACRE","price":"","valid_mobiles":"9811110009","original_raw_text":"EV manufacturer requires 3-4 acre industrial facility on lease in Greater Noida."}},
 {"id":"V310","row":{"source_id":"V310","listing_type":"Sale","category":"Land","locality":"Chhatarpur","area":"3","area_unit":"ACRE","price":"₹27 Cr","valid_mobiles":"9811110010","original_raw_text":"Vacant agricultural land parcel 3 acres in Chhatarpur, sale ₹27 Cr."}},
 {"id":"V311","row":{"source_id":"V311","listing_type":"Sale","category":"Commercial","locality":"Lajpat Nagar","area":"1800","area_unit":"SQFT","price":"₹52,000/sqft","valid_mobiles":"9811110011","original_raw_text":"Lajpat Nagar showroom 1800 sqft, sale quote ₹52,000/sqft."}},
 {"id":"V312","row":{"source_id":"V312","listing_type":"Requirement - Buy","category":"Hotel","locality":"Delhi","area":"","area_unit":"","price":"Maximum budget ₹60 Cr","valid_mobiles":"9811110012","original_raw_text":"Investor wants to purchase a 40-60 room operating hotel in Delhi, maximum budget ₹60 Cr."}},
 {"id":"V313","row":{"source_id":"V313","listing_type":"Sale","category":"Residential","locality":"New Friends Colony","area":"400","area_unit":"SQYD","price":"₹12.5 Cr","valid_mobiles":"9811110013","original_raw_text":"New Friends Colony independent floor, 4 BHK, plot size 400 sq yds, sale ₹12.5 Cr."}},
 {"id":"V314","row":{"source_id":"V314","listing_type":"Rent","category":"Retail","locality":"First Floor 2200 sqft","area":"2200","area_unit":"SQFT","price":"₹4 Lac pm","valid_mobiles":"9811110014","original_raw_text":"South Extension first floor retail space 2200 sqft available on rent ₹4 lakh pm."}},
 {"id":"V315","row":{"source_id":"V315","listing_type":"Sale","category":"Industrial","locality":"Dharuhera","area":"7","area_unit":"ACRE","price":"₹68 Cr","valid_mobiles":"9811110015","original_raw_text":"Running industrial factory at Dharuhera, land 7 acres, covered shed 210,000 sqft, sale ₹68 Cr."}},
 {"id":"V316","row":{"source_id":"V316","listing_type":"Available","category":"Residential","locality":"South Delhi","area":"","area_unit":"","price":"Various","valid_mobiles":"9811110016","original_raw_text":"Multiple South Delhi homes: Friends Colony 500 yds ₹18 Cr; Maharani Bagh 800 yds ₹34 Cr; Jor Bagh 600 yds ₹45 Cr."}},
 {"id":"V317","row":{"source_id":"V317","listing_type":"Rent","category":"Office","locality":"Noida Sector 62","area":"25000","area_unit":"SQFT","price":"25","valid_mobiles":"9811110017","original_raw_text":"Noida Sector 62 office 25,000 sqft available for lease. Rent on request."}},
 {"id":"V318","row":{"source_id":"V318","listing_type":"Sale","category":"Commercial","locality":"Vasant Kunj","area":"1600","area_unit":"SQFT","price":"₹3.9 Cr","valid_mobiles":"9811110018","original_raw_text":"Vasant Kunj pre-rented retail unit 1600 sqft, tenant premium salon, rent ₹1.9 lakh monthly, for sale ₹3.9 Cr."}},
 {"id":"V319","row":{"source_id":"V319","listing_type":"Wanted","category":"Retail","locality":"Delhi NCR","area":"6000","area_unit":"SQFT","price":"","valid_mobiles":"9811110019","original_raw_text":"Restaurant chain looking for 4500-6000 sqft high-street premises on rent across Delhi NCR."}},
 {"id":"V320","row":{"source_id":"V320","listing_type":"Sale","category":"Land","locality":"Westend","area":"800","area_unit":"SQYD","price":"₹44 Cr","valid_mobiles":"9811110020","original_raw_text":"Westend bungalow plot 800 sq yds with old house to demolish, redevelopment site, sale ₹44 Cr."}},
]

TRUTH={
"V301":{"class":"REQUIREMENT","transaction":"RENT","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"TEXT_PRICE","locality":"VALID_OR_UNPROVEN"},
"V302":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"LAND_OR_PLOT","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
"V303":{"class":"PROPERTY_AVAILABILITY","transaction":"RENT","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
"V304":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"RESIDENTIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
"V305":{"class":"REQUIREMENT","transaction":"SALE","asset":"LAND_OR_PLOT","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"TEXT_PRICE","locality":"VALID_OR_UNPROVEN"},
"V306":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"COMMERCIAL","occupancy":"TENANTED","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
"V307":{"class":"PROPERTY_AVAILABILITY","transaction":"RENT","asset":"INDUSTRIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"RATE_OR_RENT_RATE","locality":"VALID_OR_UNPROVEN"},
"V308":{"class":"PROPERTY_AVAILABILITY","transaction":"AMBIGUOUS","asset":"HOSPITALITY","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"TEXT_PRICE","locality":"VALID_OR_UNPROVEN"},
"V309":{"class":"REQUIREMENT","transaction":"RENT","asset":"INDUSTRIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"UNKNOWN","locality":"VALID_OR_UNPROVEN"},
"V310":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"LAND_OR_PLOT","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
"V311":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"RATE_OR_RENT_RATE","locality":"VALID_OR_UNPROVEN"},
"V312":{"class":"REQUIREMENT","transaction":"SALE","asset":"HOSPITALITY","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"TEXT_PRICE","locality":"VALID_OR_UNPROVEN"},
"V313":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"RESIDENTIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
"V314":{"class":"PROPERTY_AVAILABILITY","transaction":"RENT","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"POLLUTED","must_reason":"LOCALITY_FRAGMENT_OR_MISSING"},
"V315":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"INDUSTRIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
"V316":{"class":"PROPERTY_AVAILABILITY","transaction":"UNKNOWN","asset":"RESIDENTIAL","occupancy":"UNKNOWN","atomicity":"GROUP_PARENT","price_kind":"TEXT_PRICE","locality":"VALID_OR_UNPROVEN","must_reason":"MULTI_PROPERTY_REQUIRES_ATOMIC_SPLIT"},
"V317":{"class":"PROPERTY_AVAILABILITY","transaction":"RENT","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"BARE_NUMBER","locality":"VALID_OR_UNPROVEN","must_reason":"NUMERIC_PRICE_WITHOUT_MONEY_EVIDENCE"},
"V318":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"COMMERCIAL","occupancy":"TENANTED","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
"V319":{"class":"REQUIREMENT","transaction":"RENT","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"UNKNOWN","locality":"VALID_OR_UNPROVEN"},
"V320":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"LAND_OR_PLOT","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
}
FIELDS=("class","transaction","asset","occupancy","atomicity","price_kind","locality")

def _engine(core): return getattr(core,"engine",None)
def _app(core): return getattr(core,"app",None) or core
def _route_exists(app,path):
    try:return any(getattr(r,"path",None)==path for r in app.routes)
    except Exception:return False
def _sha(s): return hashlib.sha256(str(s).encode("utf-8")).hexdigest()
def _manifest_sha(): return _sha(json.dumps(CASE_INPUTS,sort_keys=True,ensure_ascii=False,separators=(",",":")))
def _student_sha():
    return _sha(inspect.getsource(student.analyze)+inspect.getsource(student._transaction)+inspect.getsource(student._classify)+inspect.getsource(student._asset_class)+inspect.getsource(student._occupancy))
def _champion_sha():
    return _sha(inspect.getsource(champion.predict_message)+inspect.getsource(champion.leading_demand_object))

def _projection(a):
    return {"class":a.get("predicted_class"),"transaction":a.get("predicted_transaction"),
            "asset":a.get("predicted_asset_class"),"occupancy":a.get("occupancy_status"),
            "atomicity":a.get("atomicity_status"),"price_kind":a.get("price_kind"),
            "locality":(a.get("evidence") or {}).get("locality_status"),
            "reasons":sorted(a.get("risk_reasons") or [])}

def freeze_predictions():
    preds=[]
    for c in CASE_INPUTS:
        preds.append({"id":c["id"],"prediction":_projection(student.analyze(dict(c["row"])))})
    payload=json.dumps(preds,sort_keys=True,ensure_ascii=False,separators=(",",":"))
    return preds,_sha(payload)

def grade(preds):
    by={x["id"]:x["prediction"] for x in preds}
    errors=[];total=0;correct=0;critical=0;cases=[]
    for cid,t in TRUTH.items():
        p=by[cid];ce=[]
        for f in FIELDS:
            total+=1
            if p.get(f)==t.get(f):correct+=1
            else:
                critical+=1;e={"id":cid,"field":f,"expected":t.get(f),"got":p.get(f)};errors.append(e);ce.append(e)
        if "must_reason" in t:
            total+=1
            if t["must_reason"] in p.get("reasons",[]):correct+=1
            else:
                critical+=1;e={"id":cid,"field":"must_reason","expected":t["must_reason"],"got":p.get("reasons",[])};errors.append(e);ce.append(e)
        cases.append({"id":cid,"passed":not ce,"errors":ce})
    return {"total_cases":len(TRUTH),"total_checks":total,"correct_checks":correct,
            "accuracy":round(100*correct/max(total,1),4),"critical_errors":critical,
            "case_passes":sum(1 for x in cases if x["passed"]),
            "case_accuracy":round(100*sum(1 for x in cases if x["passed"])/len(cases),4),
            "errors":errors,"cases":cases}

def run_once(core):
    if not _LOCK.acquire(blocking=False):return {"status":"SKIPPED","reason":"V3_EXAM_RUNNING"}
    try:
        eng=_engine(core)
        if eng is None:raise RuntimeError("Core engine unavailable")
        with eng.begin() as c:c.execute(text(DDL))
        with eng.connect() as c:old=c.execute(text("SELECT result FROM alliance_magazine_fresh_v3_exams WHERE exam_id=:e"),{"e":EXAM_ID}).scalar()
        if old:
            STATE["result"]=old;STATE["status"]=old.get("status","FROZEN") if isinstance(old,dict) else "FROZEN";return old

        tr=student.self_check()
        if tr.get("status")!="TRAINING_PASS":
            return {"status":"BLOCKED","reason":"CHALLENGER_V513_TRAINING_NOT_PASS","training":tr}
        if student.VERSION!=EXPECTED_STUDENT:raise RuntimeError(f"Student version mismatch: {student.VERSION}")
        if champion.VERSION!=EXPECTED_CHAMPION:raise RuntimeError(f"Champion version mismatch: {champion.VERSION}")
        ch=_champion_sha()
        if ch!=EXPECTED_CHAMPION_SHA:raise RuntimeError(f"Champion hash mismatch: {ch}")

        ss=_student_sha();ms=_manifest_sha()
        preds,ps=freeze_predictions()
        frozen=datetime.now(timezone.utc)
        g=grade(preds)
        status="AUTOMATED_INDEPENDENT_MAGAZINE_V3_PASS" if g["critical_errors"]==0 and g["accuracy"]==100.0 else "AUTOMATED_INDEPENDENT_MAGAZINE_V3_HOLD"
        result={"version":VERSION,"mode":MODE,"exam_id":EXAM_ID,"status":status,
                "challenger_training":tr,
                "student":{"version":student.VERSION,"source_sha256":ss,"frozen":True},
                "champion":{"version":champion.VERSION,"sha256":ch,"immutable":True},
                "freeze":{"case_manifest_sha256":ms,"prediction_freeze_sha256":ps,"frozen_at":frozen.isoformat(),"tuning_during_exam":False},
                "exam":{**g,"critical_pass_rule":"100% on every certified field; any critical error = HOLD"},
                "next_gate":"MAGAZINE_IMAGE_EVIDENCE_CERTIFICATION" if status.endswith("_PASS") else "TRAIN_NEXT_SEPARATE_CHALLENGER_ON_V3_FAILURES",
                "safety":{"production_writes":0,"source_mutations":0,"gold_mutations":0,"whatsapp_writes":0,
                          "champion_mutations":0,"parent_challenger_mutations":0,"tuning_during_exam":0}}
        with eng.begin() as c:
            c.execute(text("""INSERT INTO alliance_magazine_fresh_v3_exams(
            exam_id,version,student_version,student_source_sha256,champion_sha256,case_manifest_sha256,
            prediction_freeze_sha256,frozen_at,status,total_cases,total_checks,correct_checks,accuracy,critical_errors,result)
            VALUES(:eid,:v,:sv,:ss,:cs,:ms,:ps,:fa,:st,:tc,:tch,:cc,:ac,:ce,CAST(:r AS JSONB))"""),
            {"eid":EXAM_ID,"v":VERSION,"sv":student.VERSION,"ss":ss,"cs":ch,"ms":ms,"ps":ps,"fa":frozen,
             "st":status,"tc":g["total_cases"],"tch":g["total_checks"],"cc":g["correct_checks"],"ac":g["accuracy"],
             "ce":g["critical_errors"],"r":json.dumps(result,ensure_ascii=False)})
        STATE["result"]=result;STATE["status"]=status;return result
    except Exception as e:
        STATE["status"]="ERROR";STATE["last_error"]=f"{type(e).__name__}: {e}"
        return {"status":"ERROR","version":VERSION,"exam_id":EXAM_ID,"error":STATE["last_error"]}
    finally:_LOCK.release()

def status(core):return STATE["result"] or run_once(core)

def dashboard(core):
    s=status(core);e=s.get("exam") or {}
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Magazine V3 Certification</title><style>body{{font-family:Arial;background:#f5f7fb;margin:0;color:#172033}}
header{{background:#102235;color:#fff;padding:18px}}.wrap{{max-width:1280px;margin:auto;padding:18px}}
.card{{background:white;border:1px solid #e2e8f0;border-radius:12px;padding:14px;margin-bottom:12px}}
pre{{white-space:pre-wrap;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px}}</style></head>
<body><header><b>Alliance Magazine Fresh Blind V3 Certification 5.4</b><br><small>Separate Challenger 5.1.3 · fresh cases · frozen predictions · no tuning</small></header>
<div class='wrap'><div class='card'><b>{html.escape(str(s.get("status")))}</b> · Accuracy {html.escape(str(e.get("accuracy")))}% · Cases {html.escape(str(e.get("case_passes")))} / {html.escape(str(e.get("total_cases")))}</div>
<pre>{html.escape(json.dumps(s,ensure_ascii=False,indent=2))}</pre></div></body></html>"""

def register(core):
    app=_app(core)
    if not _route_exists(app,"/api/property-brain/magazine-fresh-v540/status"):
        @app.get("/api/property-brain/magazine-fresh-v540/status")
        def _status():return status(core)
    if not _route_exists(app,"/property-brain/magazine-fresh-v540"):
        @app.get("/property-brain/magazine-fresh-v540",response_class=HTMLResponse)
        def _page():return HTMLResponse(dashboard(core))
    return {"status":"REGISTERED","version":VERSION,"route":"/property-brain/magazine-fresh-v540"}

def _runner(core):
    time.sleep(int(os.getenv("ALLIANCE_MAGAZINE_V3_EXAM_DELAY","45")))
    run_once(core)

def start(core):
    global _STARTED
    register(core)
    if _STARTED:return STATE
    _STARTED=True
    threading.Thread(target=_runner,args=(core,),name="magazine-fresh-v540",daemon=True).start()
    return STATE
