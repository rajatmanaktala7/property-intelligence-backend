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

import alliance_magazine_challenger_v512 as student
import alliance_autonomous_student_v438 as champion

VERSION="5.3.0-ALLIANCE-MAGAZINE-FRESH-BLIND-V2-CERTIFICATION"
MODE="NEW_UNSEEN_V2_CASES_FROZEN_PREDICTIONS_INDEPENDENT_TRUTH_NO_TUNING"
EXAM_ID="MAGAZINE_FRESH_BLIND_V2_530_2026_09_03"
EXPECTED_STUDENT="5.1.2-ALLIANCE-MAGAZINE-CHALLENGER-V1-FAILURE-CLOSURE"
EXPECTED_CHAMPION="4.3.8-ALLIANCE-STUDENT-DEMAND-OBJECT-CLOSURE"
EXPECTED_CHAMPION_SHA="8b5014fa5ec5e38b75f4da15945b357230573c88a096be9c4b2e0609ecd52694"

STATE={"status":"NOT_STARTED","result":None,"last_error":None}
_LOCK=threading.Lock()
_STARTED=False

DDL="""CREATE TABLE IF NOT EXISTS alliance_magazine_fresh_v2_exams(
 exam_id TEXT PRIMARY KEY, version TEXT NOT NULL, student_version TEXT NOT NULL,
 student_source_sha256 TEXT NOT NULL, champion_sha256 TEXT NOT NULL,
 case_manifest_sha256 TEXT NOT NULL, prediction_freeze_sha256 TEXT NOT NULL,
 frozen_at TIMESTAMPTZ NOT NULL, status TEXT NOT NULL,
 total_cases INTEGER NOT NULL,total_checks INTEGER NOT NULL,correct_checks INTEGER NOT NULL,
 accuracy NUMERIC(8,4) NOT NULL,critical_errors INTEGER NOT NULL,result JSONB NOT NULL,
 created_at TIMESTAMPTZ DEFAULT NOW()
)"""

# V2 is deliberately different from V1 and from all training examples.
CASE_INPUTS=[
 {"id":"V201","row":{"source_id":"V201","listing_type":"Lease","category":"Office","locality":"Connaught Place","area":"7600","area_unit":"SQFT","price":"₹9.5 Lac/month","valid_mobiles":"9810101010","original_raw_text":"Connaught Place office 7,600 sq ft available on lease. Monthly rent ₹9.5 lakh."}},
 {"id":"V202","row":{"source_id":"V202","listing_type":"Requirement - Rent","category":"Retail","locality":"Greater Kailash","area":"2500","area_unit":"SQFT","price":"Budget ₹4 Lac pm","valid_mobiles":"9876012345","original_raw_text":"Fashion brand requires 2000-2500 sqft ground floor store on rent in GK-1/GK-2. Budget ₹4 lakh pm."}},
 {"id":"V203","row":{"source_id":"V203","listing_type":"Sale","category":"Residential","locality":"Defence Colony","area":"325","area_unit":"SQYD","price":"₹13.75 Cr","valid_mobiles":"9899111000","original_raw_text":"Defence Colony independent builder floor on 325 sq yds plot, 4 BHK, first floor, sale ₹13.75 Cr."}},
 {"id":"V204","row":{"source_id":"V204","listing_type":"Sale","category":"Land","locality":"Rajokri","area":"2","area_unit":"ACRE","price":"₹36 Cr","valid_mobiles":"9810202020","original_raw_text":"Rajokri vacant farmhouse land parcel 2 acres for outright sale at ₹36 Cr."}},
 {"id":"V205","row":{"source_id":"V205","listing_type":"Available","category":"Commercial","locality":"Gurugram","area":"3200","area_unit":"SQFT","price":"₹5.2 Cr","valid_mobiles":"9810303030","original_raw_text":"Pre-leased retail unit 3200 sqft in Gurugram, leased to national brand at ₹2.8 lakh monthly, offered for sale ₹5.2 Cr."}},
 {"id":"V206","row":{"source_id":"V206","listing_type":"Sale / Lease","category":"Industrial","locality":"Noida Phase 2","area":"4500","area_unit":"SQM","price":"On application","valid_mobiles":"9810404040","original_raw_text":"Industrial factory building 4500 sq m in Noida Phase II available for sale or lease."}},
 {"id":"V207","row":{"source_id":"V207","listing_type":"Sale","category":"Commercial","locality":"Karol Bagh","area":"1400","area_unit":"SQFT","price":"₹48,000/sqft","valid_mobiles":"9810505050","original_raw_text":"Karol Bagh showroom 1400 sqft for sale. Quoted rate ₹48,000 per sqft."}},
 {"id":"V208","row":{"source_id":"V208","listing_type":"Wanted","category":"Hotel","locality":"Delhi NCR","area":"","area_unit":"","price":"Budget ₹80 Cr","valid_mobiles":"9810606060","original_raw_text":"Hospitality operator looking to buy operational 50-80 room hotel in Delhi NCR, budget up to ₹80 Cr."}},
 {"id":"V209","row":{"source_id":"V209","listing_type":"Rent","category":"Warehouse","locality":"Bilaspur","area":"100000","area_unit":"SQFT","price":"₹24/sqft/month","valid_mobiles":"9810708080","original_raw_text":"Grade A warehouse 100,000 sqft near Bilaspur available on rent at ₹24/sqft/month."}},
 {"id":"V210","row":{"source_id":"V210","listing_type":"Sale","category":"Residential","locality":"South Delhi","area":"","area_unit":"","price":"Multiple","valid_mobiles":"9810808080","original_raw_text":"South Delhi options: Anand Niketan 400 yds ₹18 Cr; Shanti Niketan 600 yds ₹29 Cr; Westend 800 yds ₹41 Cr."}},
 {"id":"V211","row":{"source_id":"V211","listing_type":"Rent","category":"Retail","locality":"2nd Floor 3500 sqft","area":"3500","area_unit":"SQFT","price":"₹7 Lac pm","valid_mobiles":"9810909090","original_raw_text":"Khan Market second floor retail 3500 sqft available on rent ₹7 lakh pm."}},
 {"id":"V212","row":{"source_id":"V212","listing_type":"Sale","category":"Hotel","locality":"Mahipalpur","area":"22000","area_unit":"SQFT","price":"₹34 Cr","valid_mobiles":"9820101010","original_raw_text":"Mahipalpur hotel 65 rooms, built-up 22,000 sqft, operational, sale ₹34 Cr."}},
 {"id":"V213","row":{"source_id":"V213","listing_type":"Requirement - Buy","category":"Office","locality":"Noida","area":"30000","area_unit":"SQFT","price":"Maximum budget ₹40 Cr","valid_mobiles":"9820202020","original_raw_text":"Corporate investor wants to purchase 25,000-30,000 sqft office asset in Noida. Maximum budget ₹40 Cr."}},
 {"id":"V214","row":{"source_id":"V214","listing_type":"Lease","category":"Commercial","locality":"South Extension","area":"2800","area_unit":"SQFT","price":"₹8.25 Lac/month","valid_mobiles":"9820303030","original_raw_text":"South Extension main market showroom 2800 sqft available on lease at ₹8.25 lakh per month."}},
 {"id":"V215","row":{"source_id":"V215","listing_type":"Sale","category":"Industrial","locality":"Neemrana","area":"6","area_unit":"ACRE","price":"₹52 Cr","valid_mobiles":"9820404040","original_raw_text":"Neemrana running factory, 6 acre land with 180,000 sqft covered shed, outright sale ₹52 Cr."}},
 {"id":"V216","row":{"source_id":"V216","listing_type":"Rent","category":"Office","locality":"Aerocity","area":"15000","area_unit":"SQFT","price":"15","valid_mobiles":"9820505050","original_raw_text":"Aerocity office 15,000 sqft available for rent. Commercial terms on request."}},
 {"id":"V217","row":{"source_id":"V217","listing_type":"Wanted","category":"Industrial","locality":"Manesar","area":"3","area_unit":"ACRE","price":"","valid_mobiles":"9820606060","original_raw_text":"Auto ancillary company requires 2-3 acre industrial facility on lease in Manesar."}},
 {"id":"V218","row":{"source_id":"V218","listing_type":"Sale","category":"Residential","locality":"Vasant Vihar","area":"800","area_unit":"SQYD","price":"₹31 Cr","valid_mobiles":"9820707070","original_raw_text":"Vasant Vihar bungalow plot 800 sq yds, old house, redevelopment opportunity, sale ₹31 Cr."}},
 {"id":"V219","row":{"source_id":"V219","listing_type":"Sale","category":"Commercial","locality":"Saket","area":"2100","area_unit":"SQFT","price":"₹4.6 Cr","valid_mobiles":"9820808080","original_raw_text":"Saket pre-rented shop 2100 sqft, tenant café chain, monthly rent ₹2.4 lakh, sale ₹4.6 Cr."}},
 {"id":"V220","row":{"source_id":"V220","listing_type":"Rent","category":"Hospitality","locality":"Hauz Khas","area":"5000","area_unit":"SQFT","price":"₹12 Lac pm","valid_mobiles":"9820909090","original_raw_text":"Hauz Khas restaurant and lounge space 5000 sqft, fully fitted, available on rent ₹12 lakh pm."}},
]

TRUTH={
 "V201":{"class":"PROPERTY_AVAILABILITY","transaction":"RENT","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
 "V202":{"class":"REQUIREMENT","transaction":"RENT","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"TEXT_PRICE","locality":"VALID_OR_UNPROVEN"},
 "V203":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"RESIDENTIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
 "V204":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"LAND_OR_PLOT","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
 "V205":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"COMMERCIAL","occupancy":"TENANTED","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
 "V206":{"class":"PROPERTY_AVAILABILITY","transaction":"AMBIGUOUS","asset":"INDUSTRIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"TEXT_PRICE","locality":"VALID_OR_UNPROVEN"},
 "V207":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"RATE_OR_RENT_RATE","locality":"VALID_OR_UNPROVEN"},
 "V208":{"class":"REQUIREMENT","transaction":"SALE","asset":"HOSPITALITY","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"TEXT_PRICE","locality":"VALID_OR_UNPROVEN"},
 "V209":{"class":"PROPERTY_AVAILABILITY","transaction":"RENT","asset":"INDUSTRIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"RATE_OR_RENT_RATE","locality":"VALID_OR_UNPROVEN"},
 "V210":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"RESIDENTIAL","occupancy":"UNKNOWN","atomicity":"GROUP_PARENT","price_kind":"TEXT_PRICE","locality":"VALID_OR_UNPROVEN","must_reason":"MULTI_PROPERTY_REQUIRES_ATOMIC_SPLIT"},
 "V211":{"class":"PROPERTY_AVAILABILITY","transaction":"RENT","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"POLLUTED","must_reason":"LOCALITY_FRAGMENT_OR_MISSING"},
 "V212":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"HOSPITALITY","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
 "V213":{"class":"REQUIREMENT","transaction":"SALE","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"TEXT_PRICE","locality":"VALID_OR_UNPROVEN"},
 "V214":{"class":"PROPERTY_AVAILABILITY","transaction":"RENT","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
 "V215":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"INDUSTRIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
 "V216":{"class":"PROPERTY_AVAILABILITY","transaction":"RENT","asset":"COMMERCIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"BARE_NUMBER","locality":"VALID_OR_UNPROVEN","must_reason":"NUMERIC_PRICE_WITHOUT_MONEY_EVIDENCE"},
 "V217":{"class":"REQUIREMENT","transaction":"RENT","asset":"INDUSTRIAL","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"UNKNOWN","locality":"VALID_OR_UNPROVEN"},
 "V218":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"LAND_OR_PLOT","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
 "V219":{"class":"PROPERTY_AVAILABILITY","transaction":"SALE","asset":"COMMERCIAL","occupancy":"TENANTED","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
 "V220":{"class":"PROPERTY_AVAILABILITY","transaction":"RENT","asset":"HOSPITALITY","occupancy":"UNKNOWN","atomicity":"ATOMIC_OR_UNPROVEN","price_kind":"MONEY_AMOUNT","locality":"VALID_OR_UNPROVEN"},
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
    return _sha(inspect.getsource(student.analyze)+inspect.getsource(student._transaction)+inspect.getsource(student._asset_class)+inspect.getsource(student._price_kind))
def _champion_sha():
    return _sha(inspect.getsource(champion.predict_message)+inspect.getsource(champion.leading_demand_object))

def _projection(a):
    return {
      "class":a.get("predicted_class"),"transaction":a.get("predicted_transaction"),
      "asset":a.get("predicted_asset_class"),"occupancy":a.get("occupancy_status"),
      "atomicity":a.get("atomicity_status"),"price_kind":a.get("price_kind"),
      "locality":(a.get("evidence") or {}).get("locality_status"),
      "contact_quality":a.get("contact_quality"),
      "reasons":sorted(a.get("risk_reasons") or []),
    }

def freeze_predictions():
    # No TRUTH access here.
    preds=[]
    for c in CASE_INPUTS:
        preds.append({"id":c["id"],"prediction":_projection(student.analyze(dict(c["row"])))})
    payload=json.dumps(preds,sort_keys=True,ensure_ascii=False,separators=(",",":"))
    return preds,_sha(payload)

def grade(preds):
    by={x["id"]:x["prediction"] for x in preds}
    errors=[]; total=0; correct=0; critical=0; cases=[]
    for cid,t in TRUTH.items():
        p=by[cid]; ce=[]
        for f in FIELDS:
            total+=1
            if p.get(f)==t.get(f): correct+=1
            else:
                critical+=1; e={"id":cid,"field":f,"expected":t.get(f),"got":p.get(f)}; errors.append(e); ce.append(e)
        if "must_reason" in t:
            total+=1
            if t["must_reason"] in p.get("reasons",[]): correct+=1
            else:
                critical+=1; e={"id":cid,"field":"must_reason","expected":t["must_reason"],"got":p.get("reasons",[])}; errors.append(e); ce.append(e)
        cases.append({"id":cid,"passed":not ce,"errors":ce})
    return {"total_cases":len(TRUTH),"total_checks":total,"correct_checks":correct,
            "accuracy":round(100*correct/max(total,1),4),"critical_errors":critical,
            "case_passes":sum(1 for x in cases if x["passed"]),
            "case_accuracy":round(100*sum(1 for x in cases if x["passed"])/len(cases),4),
            "errors":errors,"cases":cases}

def run_once(core):
    if not _LOCK.acquire(blocking=False):
        return {"status":"SKIPPED","reason":"V2_EXAM_ALREADY_RUNNING"}
    try:
        eng=_engine(core)
        if eng is None: raise RuntimeError("Core engine unavailable")
        with eng.begin() as c:c.execute(text(DDL))
        old=None
        with eng.connect() as c:old=c.execute(text("SELECT result FROM alliance_magazine_fresh_v2_exams WHERE exam_id=:e"),{"e":EXAM_ID}).scalar()
        if old:
            STATE["result"]=old; STATE["status"]=old.get("status","FROZEN") if isinstance(old,dict) else "FROZEN"; return old

        tr=student.self_check()
        if tr.get("status")!="TRAINING_PASS":
            return {"status":"BLOCKED","reason":"CHALLENGER_TRAINING_NOT_PASS","training":tr}
        if student.VERSION!=EXPECTED_STUDENT: raise RuntimeError(f"Student version mismatch: {student.VERSION}")
        if champion.VERSION!=EXPECTED_CHAMPION: raise RuntimeError(f"Champion version mismatch: {champion.VERSION}")
        ch=_champion_sha()
        if ch!=EXPECTED_CHAMPION_SHA: raise RuntimeError(f"Champion hash mismatch: {ch}")

        ss=_student_sha(); ms=_manifest_sha()
        preds,ps=freeze_predictions()
        frozen=datetime.now(timezone.utc)
        g=grade(preds)
        status="AUTOMATED_INDEPENDENT_MAGAZINE_V2_PASS" if g["critical_errors"]==0 and g["accuracy"]==100.0 else "AUTOMATED_INDEPENDENT_MAGAZINE_V2_HOLD"
        result={
          "version":VERSION,"mode":MODE,"exam_id":EXAM_ID,"status":status,
          "challenger_training":tr,
          "student":{"version":student.VERSION,"source_sha256":ss,"frozen":True},
          "champion":{"version":champion.VERSION,"sha256":ch,"immutable":True},
          "freeze":{"case_manifest_sha256":ms,"prediction_freeze_sha256":ps,"frozen_at":frozen.isoformat(),"tuning_during_exam":False},
          "exam":{**g,"critical_pass_rule":"100% on every certified field; any critical error = HOLD"},
          "next_gate":"MAGAZINE_IMAGE_EVIDENCE_CERTIFICATION" if status.endswith("_PASS") else "TRAIN_NEXT_SEPARATE_CHALLENGER_ON_V2_FAILURES",
          "safety":{"production_writes":0,"source_mutations":0,"gold_mutations":0,"whatsapp_writes":0,"champion_mutations":0,"base_student_mutations":0,"tuning_during_exam":0},
        }
        with eng.begin() as c:
            c.execute(text("""INSERT INTO alliance_magazine_fresh_v2_exams(
             exam_id,version,student_version,student_source_sha256,champion_sha256,case_manifest_sha256,
             prediction_freeze_sha256,frozen_at,status,total_cases,total_checks,correct_checks,accuracy,critical_errors,result
             ) VALUES(:eid,:v,:sv,:ss,:cs,:ms,:ps,:fa,:st,:tc,:tch,:cc,:ac,:ce,CAST(:r AS JSONB))"""),
             {"eid":EXAM_ID,"v":VERSION,"sv":student.VERSION,"ss":ss,"cs":ch,"ms":ms,"ps":ps,"fa":frozen,
              "st":status,"tc":g["total_cases"],"tch":g["total_checks"],"cc":g["correct_checks"],"ac":g["accuracy"],
              "ce":g["critical_errors"],"r":json.dumps(result,ensure_ascii=False)})
        STATE["result"]=result; STATE["status"]=status; return result
    except Exception as e:
        STATE["status"]="ERROR"; STATE["last_error"]=f"{type(e).__name__}: {e}"
        return {"status":"ERROR","version":VERSION,"exam_id":EXAM_ID,"error":STATE["last_error"]}
    finally:_LOCK.release()

def status(core):
    return STATE["result"] or run_once(core)

def dashboard(core):
    s=status(core); e=s.get("exam") or {}
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Magazine V2 Certification</title><style>body{{font-family:Arial;background:#f5f7fb;margin:0;color:#172033}}header{{background:#102235;color:#fff;padding:18px}}
.wrap{{max-width:1280px;margin:auto;padding:18px}}.card{{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:14px;margin-bottom:12px}}
pre{{white-space:pre-wrap;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px}}</style></head>
<body><header><b>Alliance Magazine Fresh Blind V2 Certification 5.3</b><br><small>Separate Challenger · fresh cases · frozen predictions · no tuning</small></header>
<div class='wrap'><div class='card'><b>{html.escape(str(s.get("status")))}</b> · Accuracy {html.escape(str(e.get("accuracy")))}% · Cases {html.escape(str(e.get("case_passes")))} / {html.escape(str(e.get("total_cases")))}</div>
<pre>{html.escape(json.dumps(s,ensure_ascii=False,indent=2))}</pre></div></body></html>"""

def register(core):
    app=_app(core)
    if not _route_exists(app,"/api/property-brain/magazine-fresh-v530/status"):
        @app.get("/api/property-brain/magazine-fresh-v530/status")
        def _status(): return status(core)
    if not _route_exists(app,"/property-brain/magazine-fresh-v530"):
        @app.get("/property-brain/magazine-fresh-v530",response_class=HTMLResponse)
        def _page(): return HTMLResponse(dashboard(core))
    return {"status":"REGISTERED","route":"/property-brain/magazine-fresh-v530","version":VERSION}

def _runner(core):
    time.sleep(int(os.getenv("ALLIANCE_MAGAZINE_V2_EXAM_DELAY","40")))
    run_once(core)

def start(core):
    global _STARTED
    register(core)
    if _STARTED:return STATE
    _STARTED=True
    threading.Thread(target=_runner,args=(core,),name="magazine-fresh-v530",daemon=True).start()
    return STATE
