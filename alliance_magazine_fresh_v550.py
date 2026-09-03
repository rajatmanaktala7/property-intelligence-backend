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

import alliance_magazine_challenger_v514 as student
import alliance_autonomous_student_v438 as champion

VERSION="5.5.0-ALLIANCE-MAGAZINE-FRESH-BLIND-V4-CERTIFICATION"
MODE="NEW_UNSEEN_V4_OPERATOR_VS_ASSET_AND_CRE_SEMANTICS_FROZEN_NO_TUNING"
EXAM_ID="MAGAZINE_FRESH_BLIND_V4_550_2026_09_03"
EXPECTED_STUDENT="5.1.4-ALLIANCE-MAGAZINE-CHALLENGER-V3-FAILURE-CLOSURE"
EXPECTED_CHAMPION="4.3.8-ALLIANCE-STUDENT-DEMAND-OBJECT-CLOSURE"
EXPECTED_CHAMPION_SHA="8b5014fa5ec5e38b75f4da15945b357230573c88a096be9c4b2e0609ecd52694"

STATE={"status":"NOT_STARTED","result":None,"last_error":None}
_STARTED=False
_LOCK=threading.Lock()

DDL="""CREATE TABLE IF NOT EXISTS alliance_magazine_fresh_v4_exams(
 exam_id TEXT PRIMARY KEY, version TEXT NOT NULL, student_version TEXT NOT NULL,
 student_source_sha256 TEXT NOT NULL, champion_sha256 TEXT NOT NULL,
 case_manifest_sha256 TEXT NOT NULL, prediction_freeze_sha256 TEXT NOT NULL,
 frozen_at TIMESTAMPTZ NOT NULL, status TEXT NOT NULL,
 total_cases INTEGER NOT NULL,total_checks INTEGER NOT NULL,correct_checks INTEGER NOT NULL,
 accuracy NUMERIC(8,4) NOT NULL,critical_errors INTEGER NOT NULL,result JSONB NOT NULL,
 created_at TIMESTAMPTZ DEFAULT NOW()
)"""

CASE_INPUTS=[
 {"id":"V401","row":{"source_id":"V401","listing_type":"Wanted","category":"Retail","locality":"Gurugram","area":"4500","area_unit":"SQFT","price":"Budget ₹8 Lac pm","valid_mobiles":"9899000001","original_raw_text":"Premium café chain seeking 3500-4500 sqft high-street retail premises on rent in Gurugram. Budget ₹8 lakh pm."}},
 {"id":"V402","row":{"source_id":"V402","listing_type":"Wanted","category":"Office","locality":"Noida","area":"12000","area_unit":"SQFT","price":"","valid_mobiles":"9899000002","original_raw_text":"Healthcare company requires 10,000-12,000 sqft office premises on lease in Noida."}},
 {"id":"V403","row":{"source_id":"V403","listing_type":"Requirement - Buy","category":"Hotel","locality":"Jaipur","area":"","area_unit":"","price":"Budget ₹75 Cr","valid_mobiles":"9899000003","original_raw_text":"Hospitality investor looking to acquire a 70-100 room operational hotel in Jaipur, budget ₹75 Cr."}},
 {"id":"V404","row":{"source_id":"V404","listing_type":"Wanted","category":"Retail","locality":"Delhi","area":"3000","area_unit":"SQFT","price":"","valid_mobiles":"9899000004","original_raw_text":"Salon brand looking for 2500-3000 sqft ground floor showroom space on rent in South Delhi."}},
 {"id":"V405","row":{"source_id":"V405","listing_type":"Sale","category":"Residential","locality":"Shanti Niketan","area":"600","area_unit":"SQYD","price":"₹29 Cr","valid_mobiles":"9899000005","original_raw_text":"Shanti Niketan bungalow plot 600 sq yds with ageing house for redevelopment, outright sale ₹29 Cr."}},
 {"id":"V406","row":{"source_id":"V406","listing_type":"Sale","category":"Residential","locality":"Greater Kailash 2","area":"300","area_unit":"SQYD","price":"₹10.5 Cr","valid_mobiles":"9899000006","original_raw_text":"GK-2 independent builder floor on 300 sq yd plot, 4 BHK, lift and parking, sale ₹10.5 Cr."}},
 {"id":"V407","row":{"source_id":"V407","listing_type":"Rent","category":"Warehouse","locality":"Farukhnagar","area":"120000","area_unit":"SQFT","price":"₹22/sqft/month","valid_mobiles":"9899000007","original_raw_text":"Grade A warehouse 120,000 sqft in Farukhnagar available on lease at ₹22 per sqft per month."}},
 {"id":"V408","row":{"source_id":"V408","listing_type":"Sale","category":"Commercial","locality":"Sector 44 Gurugram","area":"6500","area_unit":"SQFT","price":"₹9.8 Cr","valid_mobiles":"9899000008","original_raw_text":"Pre-leased office 6500 sqft Sector 44 Gurugram, tenant IT company, rent ₹4.6 lakh monthly, for sale ₹9.8 Cr."}},
 {"id":"V409","row":{"source_id":"V409","listing_type":"Wanted","category":"Industrial","locality":"Bhiwadi","area":"5","area_unit":"ACRE","price":"","valid_mobiles":"9899000009","original_raw_text":"FMCG manufacturer requires 4-5 acre industrial facility on lease in Bhiwadi."}},
 {"id":"V410","row":{"source_id":"V410","listing_type":"Requirement - Buy","category":"Land","locality":"Sohna","area":"12","area_unit":"ACRE","price":"Maximum budget ₹150 Cr","valid_mobiles":"9899000010","original_raw_text":"Developer seeking to purchase 8-12 acre land parcel in Sohna for plotted development. Maximum budget ₹150 Cr."}},
 {"id":"V411","row":{"source_id":"V411","listing_type":"Sale / Lease","category":"Commercial","locality":"Connaught Place","area":"10000","area_unit":"SQFT","price":"Terms on request","valid_mobiles":"9899000011","original_raw_text":"Connaught Place commercial building 10,000 sqft available for sale or lease."}},
 {"id":"V412","row":{"source_id":"V412","listing_type":"Rent","category":"Hospitality","locality":"Mehrauli","area":"7000","area_unit":"SQFT","price":"₹14 Lac pm","valid_mobiles":"9899000012","original_raw_text":"Fitted restaurant and bar premises 7000 sqft in Mehrauli available on rent ₹14 lakh pm."}},
 {"id":"V413","row":{"source_id":"V413","listing_type":"Wanted","category":"Retail","locality":"Delhi NCR","area":"8000","area_unit":"SQFT","price":"","valid_mobiles":"9899000013","original_raw_text":"Gym operator seeking 6000-8000 sqft commercial space on lease across Delhi NCR."}},
 {"id":"V414","row":{"source_id":"V414","listing_type":"Wanted","category":"Retail","locality":"Delhi NCR","area":"2500","area_unit":"SQFT","price":"","valid_mobiles":"9899000014","original_raw_text":"Bank requires 2000-2500 sqft ground floor branch premises on lease in Delhi NCR."}},
 {"id":"V415","row":{"source_id":"V415","listing_type":"Sale","category":"Land","locality":"Rajokri","area":"1.5","area_unit":"ACRE","price":"₹30 Cr","valid_mobiles":"9899000015","original_raw_text":"Vacant land parcel 1.5 acres Rajokri, clear title, sale ₹30 Cr."}},
 {"id":"V416","row":{"source_id":"V416","listing_type":"Rent","category":"Office","locality":"Cyber City","area":"18000","area_unit":"SQFT","price":"18","valid_mobiles":"9899000016","original_raw_text":"Cyber City office 18,000 sqft available on lease. Commercials on request."}},
 {"id":"V417","row":{"source_id":"V417","listing_type":"Sale","category":"Commercial","locality":"Defence Colony","area":"2200","area_unit":"SQFT","price":"₹6.2 Cr","valid_mobiles":"9899000017","original_raw_text":"Defence Colony retail showroom 2200 sqft, vacant possession, sale ₹6.2 Cr."}},
 {"id":"V418","row":{"source_id":"V418","listing_type":"Available","category":"Residential","locality":"South Delhi","area":"","area_unit":"","price":"Various","valid_mobiles":"9899000018","original_raw_text":"South Delhi residential options: Vasant Vihar 600 yds ₹27 Cr; Westend 800 yds ₹40 Cr; Anand Niketan 400 yds ₹18 Cr."}},
 {"id":"V419","row":{"source_id":"V419","listing_type":"Requirement - Buy","category":"Office","locality":"Gurugram","area":"50000","area_unit":"SQFT","price":"Budget ₹55 Cr","valid_mobiles":"9899000019","original_raw_text":"Family office wants to buy 40,000-50,000 sqft Grade A office asset in Gurugram. Budget ₹55 Cr."}},
 {"id":"V420","row":{"source_id":"V420","listing_type":"Rent","category":"Retail","locality":"Ground Floor 1800 sqft","area":"1800","area_unit":"SQFT","price":"₹3.5 Lac pm","valid_mobiles":"9899000020","original_raw_text":"Greater Kailash main market ground floor showroom 1800 sqft available on rent ₹3.5 lakh pm."}},
]

TRUTH={
"V401":{"class":"REQUIREMENT","transaction":"RENT","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"TEXT_PRICE","locality":"VALID_OR_UNPROVEN"},
"V402":{"class":"REQUIREMENT","transaction":"RENT","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"UNKNOWN","locality":"VALID_OR_UNPROVEN"},
"V403":{"class":"REQUIREMENT","transaction":"SALE","asset":"HOSPITALITY","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"TEXT_PRICE","locality":"VALID_OR_UNPROVEN"},
"V404":{"class":"REQUIREMENT","transaction":"RENT","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"UNKNOWN","locality":"VALID_OR_UNPROVEN"},
"V405":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"LAND_OR_PLOT","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
"V406":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"RESIDENTIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
"V407":{"class":"PROPERTY_AVAILABILITY","transaction":"RENT","asset":"INDUSTRIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"RATE_OR_RENT_RATE","locality":"VALID_OR_UNPROVEN"},
"V408":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"COMMERCIAL","occupancy":"TENANTED","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
"V409":{"class":"REQUIREMENT","transaction":"RENT","asset":"INDUSTRIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"UNKNOWN","locality":"VALID_OR_UNPROVEN"},
"V410":{"class":"REQUIREMENT","transaction":"SALE","asset":"LAND_OR_PLOT","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"TEXT_PRICE","locality":"VALID_OR_UNPROVEN"},
"V411":{"class":"PROPERTY_AVAILABILITY","transaction":"AMBIGUOUS","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"TEXT_PRICE","locality":"VALID_OR_UNPROVEN"},
"V412":{"class":"PROPERTY_AVAILABILITY","transaction":"RENT","asset":"HOSPITALITY","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
"V413":{"class":"REQUIREMENT","transaction":"RENT","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"UNKNOWN","locality":"VALID_OR_UNPROVEN"},
"V414":{"class":"REQUIREMENT","transaction":"RENT","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"UNKNOWN","locality":"VALID_OR_UNPROVEN"},
"V415":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"LAND_OR_PLOT","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
"V416":{"class":"PROPERTY_AVAILABILITY","transaction":"RENT","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"BARE_NUMBER","locality":"VALID_OR_UNPROVEN","must_reason":"NUMERIC_PRICE_WITHOUT_MONEY_EVIDENCE"},
"V417":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"COMMERCIAL","occupancy":"VACANT_OR_READY","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
"V418":{"class":"PROPERTY_AVAILABILITY","transaction":"UNKNOWN","asset":"RESIDENTIAL","occupancy":"UNKNOWN","atomicity":"GROUP_PARENT","price_kind":"TEXT_PRICE","locality":"VALID_OR_UNPROVEN","must_reason":"MULTI_PROPERTY_REQUIRES_ATOMIC_SPLIT"},
"V419":{"class":"REQUIREMENT","transaction":"SALE","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"TEXT_PRICE","locality":"VALID_OR_UNPROVEN"},
"V420":{"class":"PROPERTY_AVAILABILITY","transaction":"RENT","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"POLLUTED","must_reason":"LOCALITY_FRAGMENT_OR_MISSING"},
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
    return _sha(inspect.getsource(student.analyze)+inspect.getsource(student._asset_class))
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
    if not _LOCK.acquire(blocking=False):return {"status":"SKIPPED","reason":"V4_EXAM_RUNNING"}
    try:
        eng=_engine(core)
        if eng is None:raise RuntimeError("Core engine unavailable")
        with eng.begin() as c:c.execute(text(DDL))
        with eng.connect() as c:old=c.execute(text("SELECT result FROM alliance_magazine_fresh_v4_exams WHERE exam_id=:e"),{"e":EXAM_ID}).scalar()
        if old:
            STATE["result"]=old;STATE["status"]=old.get("status","FROZEN") if isinstance(old,dict) else "FROZEN";return old

        tr=student.self_check()
        if tr.get("status")!="TRAINING_PASS":
            return {"status":"BLOCKED","reason":"CHALLENGER_V514_TRAINING_NOT_PASS","training":tr}
        if student.VERSION!=EXPECTED_STUDENT:raise RuntimeError(f"Student version mismatch: {student.VERSION}")
        if champion.VERSION!=EXPECTED_CHAMPION:raise RuntimeError(f"Champion version mismatch: {champion.VERSION}")
        ch=_champion_sha()
        if ch!=EXPECTED_CHAMPION_SHA:raise RuntimeError(f"Champion hash mismatch: {ch}")

        ss=_student_sha();ms=_manifest_sha()
        preds,ps=freeze_predictions()
        frozen=datetime.now(timezone.utc)
        g=grade(preds)
        status="AUTOMATED_INDEPENDENT_MAGAZINE_V4_PASS" if g["critical_errors"]==0 and g["accuracy"]==100.0 else "AUTOMATED_INDEPENDENT_MAGAZINE_V4_HOLD"
        result={"version":VERSION,"mode":MODE,"exam_id":EXAM_ID,"status":status,
                "challenger_training":tr,
                "student":{"version":student.VERSION,"source_sha256":ss,"frozen":True},
                "champion":{"version":champion.VERSION,"sha256":ch,"immutable":True},
                "freeze":{"case_manifest_sha256":ms,"prediction_freeze_sha256":ps,"frozen_at":frozen.isoformat(),"tuning_during_exam":False},
                "exam":{**g,"critical_pass_rule":"100% on every certified field; any critical error = HOLD"},
                "next_gate":"MAGAZINE_IMAGE_EVIDENCE_CERTIFICATION" if status.endswith("_PASS") else "TRAIN_NEXT_SEPARATE_CHALLENGER_ON_V4_FAILURES",
                "safety":{"production_writes":0,"source_mutations":0,"gold_mutations":0,"whatsapp_writes":0,
                          "champion_mutations":0,"parent_challenger_mutations":0,"tuning_during_exam":0}}
        with eng.begin() as c:
            c.execute(text("""INSERT INTO alliance_magazine_fresh_v4_exams(
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
<title>Magazine V4 Certification</title><style>body{{font-family:Arial;background:#f5f7fb;margin:0;color:#172033}}
header{{background:#102235;color:#fff;padding:18px}}.wrap{{max-width:1280px;margin:auto;padding:18px}}
.card{{background:white;border:1px solid #e2e8f0;border-radius:12px;padding:14px;margin-bottom:12px}}
pre{{white-space:pre-wrap;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px}}</style></head>
<body><header><b>Alliance Magazine Fresh Blind V4 Certification 5.5</b><br><small>Operator profile ≠ property asset · frozen predictions · independent truth</small></header>
<div class='wrap'><div class='card'><b>{html.escape(str(s.get("status")))}</b> · Accuracy {html.escape(str(e.get("accuracy")))}% · Cases {html.escape(str(e.get("case_passes")))} / {html.escape(str(e.get("total_cases")))}</div>
<pre>{html.escape(json.dumps(s,ensure_ascii=False,indent=2))}</pre></div></body></html>"""

def register(core):
    app=_app(core)
    if not _route_exists(app,"/api/property-brain/magazine-fresh-v550/status"):
        @app.get("/api/property-brain/magazine-fresh-v550/status")
        def _status():return status(core)
    if not _route_exists(app,"/property-brain/magazine-fresh-v550"):
        @app.get("/property-brain/magazine-fresh-v550",response_class=HTMLResponse)
        def _page():return HTMLResponse(dashboard(core))
    return {"status":"REGISTERED","version":VERSION,"route":"/property-brain/magazine-fresh-v550"}

def _runner(core):
    time.sleep(int(os.getenv("ALLIANCE_MAGAZINE_V4_EXAM_DELAY","45")))
    run_once(core)

def start(core):
    global _STARTED
    register(core)
    if _STARTED:return STATE
    _STARTED=True
    threading.Thread(target=_runner,args=(core,),name="magazine-fresh-v550",daemon=True).start()
    return STATE
