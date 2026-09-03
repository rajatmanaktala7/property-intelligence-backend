from __future__ import annotations

import html
import json
import re
from fastapi.responses import HTMLResponse

import alliance_property_brain_foundation_v1 as foundation
import alliance_cre_academy_v400 as v400
import alliance_cre_academy_v401 as v401
import alliance_cre_academy_v402 as v402
import alliance_autonomous_student_v430 as v430
import alliance_autonomous_student_v431 as v431
import alliance_autonomous_student_v432 as v432
import alliance_autonomous_student_v433 as v433
import alliance_autonomous_student_v434 as v434
import alliance_autonomous_student_v435 as v435
import alliance_truth_integrity_v426 as v426

VERSION = "4.3.7-ALLIANCE-STUDENT-LEADING-DEMAND-OWNERSHIP-CLOSURE"
MODE = "RESTORE_V435_STABLE_BASE_THEN_APPLY_LEADING_DEMAND_OWNERSHIP_V5_STILL_PROTECTED"

def _engine(core): return foundation._engine_from_core(core)
def _app(core): return getattr(core, "app", None) or core

def _norm(raw):
    return re.sub(r"\s+", " ", (raw or "").lower()).strip()

def _clauses(raw):
    return [re.sub(r"\s+"," ",c).strip().lower()
            for c in re.split(r"[\n\r.;!?|]+", raw or "") if c.strip()]

def _strip_symbols(s):
    return re.sub(r"^[^a-z0-9]+","",s or "").strip()

def _asset_in(s):
    return bool(re.search(
        r"\b(?:bhk|flat|apartment|farm\s*house|house|hotel|guest\s*house|hostel|dormitory|"
        r"property|building|plot|shop|office|villa|floor|showroom|warehouse|land|space)s?\b", s or ""))

def leading_demand_contract(raw):
    cs=_clauses(raw)
    if not cs:
        return False
    first=_strip_symbols(cs[0])
    second=_strip_symbols(cs[1]) if len(cs)>1 else ""

    direct=bool(re.match(
        r"^(?:urgent(?:ly)?\s+)?(?:required?|requirement|require|requires|need(?:ed)?|"
        r"wanted|looking\s+for|seeking|tenant\s+requirement|buyer\s+required)\b", first))

    client=bool(re.match(r"^(?:client|tenant|buyer)\s+(?:requires?|needs?|seeks?|wants?)\b", first))
    client_transaction=bool(re.search(r"\b(?:purchase|buy|acquire|rent|lease)\b", first))

    # A genuine leading demand must own an asset specification in its own clause
    # or the immediately following specification clause.
    asset_owned=_asset_in(first) or _asset_in(second)

    # "Client wants quick closure. 4 BHK available for sale..." is NOT demand.
    if client and not client_transaction and not _asset_in(first):
        return False

    return asset_owned and (direct or client)

def _direct_rental(raw):
    if v426.rental_contract(raw):
        return True
    n=_norm(raw)
    # Heading-style rental demand.
    return bool(re.search(r"\brental\s+requirement\b", n))

def _direct_sale(raw):
    return v426.sale_contract(raw)

def predict_message(raw):
    raw=raw or ""

    # Important: use 4.3.5 as the stable base. 4.3.6 was intentionally not used
    # because its message-wide demand override regressed mature availability logic.
    p=v435.predict_message(raw)
    out=dict(p)
    rules=[p.get("rule","V435_BASE")]

    lead=leading_demand_contract(raw)
    rent=_direct_rental(raw)
    sale=_direct_sale(raw)
    rf=v431._repair_features(raw)

    if lead and out["class"]!="NOISE":
        out["class"]="REQUIREMENT"
        out["ownership"]="OWNED"
        out["confidence"]=max(float(out.get("confidence") or 0),99.2)
        rules.append("V437_LEADING_DEMAND_OWNS_CLASS")

        if sale:
            out["transaction"]="SALE"
            rules.append("V437_LEADING_DEMAND_EXPLICIT_SALE")
        elif rent:
            out["transaction"]="RENT"
            rules.append("V437_LEADING_DEMAND_EXPLICIT_RENT")
        elif rf["low_budget"] and rf["residential_spec"] and not rf["plural_capital"]:
            out["transaction"]="RENT"
            rules.append("V437_LEADING_DEMAND_MONTHLY_BUDGET_RENT")
        elif rf["plural_capital"]:
            out["transaction"]="SALE"
            rules.append("V437_LEADING_DEMAND_CAPITAL_BUDGET_SALE")
        else:
            out["transaction"]="UNKNOWN"
            rules.append("V437_LEADING_DEMAND_TRANSACTION_ABSTAIN")

    out["rule"]="|".join(x for x in rules if x)
    evidence=dict(out.get("evidence") or {})
    evidence["v437"]={
        "leading_demand_contract":lead,
        "direct_rental":rent,
        "direct_sale":sale,
        "stable_base":"4.3.5",
        "policy":"Only leading demand that owns an asset specification can override class. Message-wide client/availability words cannot."
    }
    out["evidence"]=evidence
    return out

def _score(cases):
    fs={f:[0,0] for f in ("class","transaction","ownership")}
    errors=[]; caseok=0
    for name,raw,hc,ht,ho in cases:
        p=predict_message(raw); exp={"class":hc,"transaction":ht,"ownership":ho}; ok=True
        for f in fs:
            fs[f][1]+=1
            if p[f]==exp[f]:
                fs[f][0]+=1
            else:
                ok=False
                errors.append({"name":name,"field":f,"truth":exp[f],"student":p[f]})
        caseok+=int(ok)
    total=sum(v[1] for v in fs.values()); cor=sum(v[0] for v in fs.values())
    return {"cases":len(cases),"accuracy":round(100*cor/max(total,1),4),
            "field_accuracy":{k:round(100*v[0]/max(v[1],1),4) for k,v in fs.items()},
            "case_accuracy":round(100*caseok/max(len(cases),1),4),"errors":errors}

CLOSURE_REGRESSION=[
    ("leading_required_solicitation",
     "Required staff accommodation for 70 people. If you have any suitable building available, please send details.",
     "REQUIREMENT","UNKNOWN","OWNED"),
    ("leading_requirement_unrelated_portfolio",
     "Requirement house 300 sq yd in GK2. Separate office lease portfolio also available.",
     "REQUIREMENT","UNKNOWN","OWNED"),
    ("leading_requirement_unrelated_rent_business",
     "Requirement house 215 sq yd Sushant Lok 1 for self use. Broker handles rent deals too.",
     "REQUIREMENT","UNKNOWN","OWNED"),
    ("rental_requirement_heading",
     "Rental Requirement\nVasant Kunj\nFarm House\n4/5 BHK\nBudget 5 - 6 lakhs",
     "REQUIREMENT","RENT","OWNED"),
    ("low_budget_requirement",
     "Require 1 BHK furnished in Mapusa for airport staff. Budget 20k.",
     "REQUIREMENT","RENT","OWNED"),
    ("client_quick_closure_offer_control",
     "Client wants quick closure. 4 BHK apartment available for sale, asking 8.5 Cr.",
     "PROPERTY_AVAILABILITY","SALE","OWNED"),
    ("owner_offer_control",
     "Owner has 4 BHK available for sale, client wants quick closure, asking 8.5 Cr.",
     "PROPERTY_AVAILABILITY","SALE","OWNED"),
    ("available_rent_control",
     "3 BHK floor available for rent in Defence Colony, tenant profile required, asking rent 1.5 lakh.",
     "PROPERTY_AVAILABILITY","RENT","OWNED"),
]

def training_status(engine):
    revised=v426.revised_truth(engine)
    v4=[(f"V4_{x['ordinal']}",x["raw_text"],
         x["revised_truth"]["class"],x["revised_truth"]["transaction"],x["revised_truth"]["ownership"])
        for x in revised]
    v4s=_score(v4)

    legacy=[]; seen=set()
    for suite in (v400.CURRICULUM,v400.ADVERSARIAL,v401.REPAIR_REGRESSION,v402.REPAIR_REGRESSION):
        for row in suite:
            key=tuple(row)
            if key not in seen:
                seen.add(key); legacy.append(row)
    leg=_score(legacy)

    lessons=(list(v430.LESSON_REGRESSION)+list(v431.MASTER_REPAIR_REGRESSION)+
             list(v432.FINAL_REPAIR_REGRESSION)+list(v433.CLOSURE_REGRESSION)+
             list(v434.CLOSURE_REGRESSION)+[
                ("explicit_rental_requirement_heading",
                 "Rental Requirement\nVasant Kunj\nFarm House 4/5 BHK\nBudget 5-6 lakhs",
                 "REQUIREMENT","RENT","OWNED"),
                ("solicitation_not_offer",
                 "Required staff accommodation for 70 people. If you have any suitable building available, please send details.",
                 "REQUIREMENT","UNKNOWN","OWNED"),
                ("direct_need_rent",
                 "Need urgently 2 BHK unfurnished flat for rent in Panjim. Budget 20k.",
                 "REQUIREMENT","RENT","OWNED"),
             ]+CLOSURE_REGRESSION)
    less=_score(lessons)

    v4_ok=(len(v4)==20 and v4s["accuracy"]>=98 and all(x>=95 for x in v4s["field_accuracy"].values()))
    legacy_ok=(leg["accuracy"]>=99 and all(x>=98 for x in leg["field_accuracy"].values()))
    lesson_ok=(less["accuracy"]==100 and all(x==100 for x in less["field_accuracy"].values()))
    passed=v4_ok and legacy_ok and lesson_ok

    return {
        "version":VERSION,
        "status":"TRAINING_PASS" if passed else "TRAINING_HOLD",
        "truth_integrity":v426.report(engine),
        "v4_training_revised_truth":v4s,
        "legacy_regression":leg,
        "lesson_regression":less,
        "training_gate":"V437_TRAINING_PASS_READY_FOR_FRESH_V5_FREEZER" if passed else "V437_TRAINING_HOLD_V5_NOT_FROZEN",
        "v5":{"status":"NOT_FROZEN"},
        "scientific_policy":"4.3.5 restored as stable base; only leading asset-owning demand can override class. V5 untouched.",
        "safety":{"v5_freeze":0,"production_writes":0,"whatsapp_writes":0,"gold_mutations":0},
    }

def _dashboard(engine):
    s=training_status(engine)
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Alliance Student 4.3.7</title>
<style>body{{font-family:Arial;margin:30px;background:#f6f7fb;color:#172033;max-width:1200px}}pre{{white-space:pre-wrap;background:#101624;color:#eef3ff;padding:18px;border-radius:10px}}.gate{{padding:15px;background:#fff4cf;border-radius:10px;font-weight:700}}</style></head><body>
<h1>Alliance Student 4.3.7 — Leading Demand Ownership Closure</h1>
<div class='gate'>{html.escape(s["training_gate"])}</div>
<p>4.3.5 is restored as the stable base. Only leading demand that owns an asset specification can override class. Fresh V5 remains untouched.</p>
<pre>{html.escape(json.dumps(foundation._json_safe(s),ensure_ascii=False,indent=2))}</pre></body></html>"""

def register(core):
    engine=_engine(core); app=_app(core)
    if not foundation._route_exists(app,"/api/property-brain/autonomous-v437/status"):
        @app.get("/api/property-brain/autonomous-v437/status")
        def status_v437(): return training_status(engine)
    if not foundation._route_exists(app,"/property-brain/autonomous-v437"):
        @app.get("/property-brain/autonomous-v437",response_class=HTMLResponse)
        def page_v437(): return HTMLResponse(_dashboard(engine))
    return {"status":"REGISTERED","version":VERSION,"route":"/property-brain/autonomous-v437",
            "v5_freeze":0,"production_writes":0,"whatsapp_writes":0,"gold_mutations":0}
