from __future__ import annotations

import html
import json
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
import alliance_truth_integrity_v426 as v426

VERSION="4.3.5-ALLIANCE-STUDENT-TRUTH-INTEGRITY-REPAIR"
MODE="CORRECT_PSEUDO_TRUTH_THEN_GENERIC_RENTAL_REQUIREMENT_REPAIR_V5_STILL_PROTECTED"

def _engine(core): return foundation._engine_from_core(core)
def _app(core): return getattr(core,"app",None) or core

def predict_message(raw):
    p=v434.predict_message(raw)
    out=dict(p); rules=[p.get("rule","V434_BASE")]
    # Narrow generic repair: explicit "rental requirement" is direct transaction evidence.
    if out["class"]=="REQUIREMENT" and v426.rental_contract(raw):
        out["transaction"]="RENT"
        out["ownership"]="OWNED"
        out["confidence"]=max(float(out.get("confidence") or 0),99.2)
        rules.append("V435_EXPLICIT_RENTAL_REQUIREMENT_DIRECT_EVIDENCE")
    out["rule"]="|".join(x for x in rules if x)
    return out

def _score(cases):
    fs={f:[0,0] for f in ("class","transaction","ownership")}; errors=[]; caseok=0
    for name,raw,hc,ht,ho in cases:
        p=predict_message(raw); exp={"class":hc,"transaction":ht,"ownership":ho}; ok=True
        for f in fs:
            fs[f][1]+=1
            if p[f]==exp[f]: fs[f][0]+=1
            else: ok=False; errors.append({"name":name,"field":f,"truth":exp[f],"student":p[f]})
        caseok+=int(ok)
    total=sum(v[1] for v in fs.values()); cor=sum(v[0] for v in fs.values())
    return {"cases":len(cases),"accuracy":round(100*cor/max(total,1),4),
            "field_accuracy":{k:round(100*v[0]/max(v[1],1),4) for k,v in fs.items()},
            "case_accuracy":round(100*caseok/max(len(cases),1),4),"errors":errors}

def training_status(engine):
    revised=v426.revised_truth(engine)
    v4=[(f"V4_{x['ordinal']}",x["raw_text"],x["revised_truth"]["class"],x["revised_truth"]["transaction"],x["revised_truth"]["ownership"]) for x in revised]
    v4s=_score(v4)

    legacy=[]; seen=set()
    for suite in (v400.CURRICULUM,v400.ADVERSARIAL,v401.REPAIR_REGRESSION,v402.REPAIR_REGRESSION):
        for row in suite:
            key=tuple(row)
            if key not in seen: seen.add(key); legacy.append(row)
    leg=_score(legacy)

    lessons=(list(v430.LESSON_REGRESSION)+list(v431.MASTER_REPAIR_REGRESSION)+
             list(v432.FINAL_REPAIR_REGRESSION)+list(v433.CLOSURE_REGRESSION)+list(v434.CLOSURE_REGRESSION)+[
        ("explicit_rental_requirement_heading","Rental Requirement\nVasant Kunj\nFarm House 4/5 BHK\nBudget 5-6 lakhs","REQUIREMENT","RENT","OWNED"),
        ("solicitation_not_offer","Required staff accommodation for 70 people. If you have any suitable building available, please send details.","REQUIREMENT","UNKNOWN","OWNED"),
        ("direct_need_rent","Need urgently 2 BHK unfurnished flat for rent in Panjim. Budget 20k.","REQUIREMENT","RENT","OWNED"),
    ])
    less=_score(lessons)

    v4_ok=(len(v4)==20 and v4s["accuracy"]>=98 and all(x>=95 for x in v4s["field_accuracy"].values()))
    legacy_ok=(leg["accuracy"]>=99 and all(x>=98 for x in leg["field_accuracy"].values()))
    lesson_ok=(less["accuracy"]==100 and all(x==100 for x in less["field_accuracy"].values()))
    passed=v4_ok and legacy_ok and lesson_ok

    return {"version":VERSION,"status":"TRAINING_PASS" if passed else "TRAINING_HOLD",
            "truth_integrity":v426.report(engine),"v4_training_revised_truth":v4s,
            "legacy_regression":leg,"lesson_regression":less,
            "training_gate":"V435_TRAINING_PASS_READY_FOR_SEPARATE_FRESH_V5_FREEZER" if passed else "V435_TRAINING_HOLD_V5_NOT_FROZEN",
            "v5":{"status":"NOT_FROZEN"},
            "scientific_policy":"V4 original truth remains immutable; contradictory pseudo-truth is version-corrected. V5 is not touched by this module.",
            "safety":{"v5_freeze":0,"production_writes":0,"whatsapp_writes":0,"gold_mutations":0}}

def _dashboard(engine):
    s=training_status(engine)
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Alliance Student 4.3.5</title>
<style>body{{font-family:Arial;margin:30px;background:#f6f7fb;color:#172033;max-width:1200px}}pre{{white-space:pre-wrap;background:#101624;color:#eef3ff;padding:18px;border-radius:10px}}.gate{{padding:15px;background:#fff4cf;border-radius:10px;font-weight:700}}</style></head><body>
<h1>Alliance Student 4.3.5 — Truth Integrity Repair</h1>
<div class='gate'>{html.escape(s["training_gate"])}</div>
<p>Correct pseudo-truth first, then score the cumulative student. Fresh V5 remains untouched in this stage.</p>
<pre>{html.escape(json.dumps(foundation._json_safe(s),ensure_ascii=False,indent=2))}</pre></body></html>"""

def register(core):
    engine=_engine(core); app=_app(core)
    if not foundation._route_exists(app,"/api/property-brain/autonomous-v435/status"):
        @app.get("/api/property-brain/autonomous-v435/status")
        def status_v435(): return training_status(engine)
    if not foundation._route_exists(app,"/property-brain/autonomous-v435"):
        @app.get("/property-brain/autonomous-v435",response_class=HTMLResponse)
        def page_v435(): return HTMLResponse(_dashboard(engine))
    return {"status":"REGISTERED","version":VERSION,"route":"/property-brain/autonomous-v435"}
