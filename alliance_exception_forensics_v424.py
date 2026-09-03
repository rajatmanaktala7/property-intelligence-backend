from __future__ import annotations
import html, json
from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_cre_championship_v410 as v410
import alliance_automation_truth_escalator_v421 as v421
import alliance_automation_closure_v422 as v422
import alliance_automation_grammar_rescue_v423 as v423

VERSION="4.2.4.2-ALLIANCE-EXCEPTION-FORENSICS-HOTFIX"
MODE="READ_ONLY_FORENSICS_API_HOTFIX_V421_INTERNAL_JUDGE_REGISTRY"
EXAM_VERSION=v410.EXAM_VERSION

def _engine(core): return foundation._engine_from_core(core)
def _app(core): return getattr(core,"app",None) or core

def inspect(engine):
    v423.run(engine)
    with engine.connect() as c:
        rows=[dict(r) for r in c.execute(text("""
        SELECT c.audit_id,c.ordinal,c.raw_text,
               c.predicted_class,c.predicted_transaction,c.predicted_ownership,
               c.prediction_confidence,c.prediction_rule,
               a.status a_status,a.truth_class a_class,a.truth_transaction a_tx,a.truth_ownership a_own,a.consensus a_evidence,
               z.status z_status,z.truth_class z_class,z.truth_transaction z_tx,z.truth_ownership z_own,z.evidence z_evidence,
               r.status r_status,r.truth_class r_class,r.truth_transaction r_tx,r.truth_ownership r_own,r.evidence r_evidence
        FROM alliance_championship_v410_cases c
        LEFT JOIN alliance_automation_v421_truth a ON a.audit_id=c.audit_id
        LEFT JOIN alliance_automation_v422_truth z ON z.audit_id=c.audit_id
        LEFT JOIN alliance_automation_v423_truth r ON r.audit_id=c.audit_id
        WHERE c.exam_version=:e
        ORDER BY c.ordinal
        """),{"e":EXAM_VERSION}).mappings()]
    unresolved=[]
    for x in rows:
        if x["a_status"]=="AUTO_RESOLVED" or x["z_status"]=="AUTO_RESOLVED" or x["r_status"]=="AUTO_RESOLVED":
            continue
        raw=x["raw_text"] or ""
        unresolved.append({
            "audit_id":str(x["audit_id"]),
            "ordinal":x["ordinal"],
            "raw_text":raw,
            "student_frozen_prediction":{
                "class":x["predicted_class"],
                "transaction":x["predicted_transaction"],
                "ownership":x["predicted_ownership"],
                "confidence":float(x["prediction_confidence"] or 0),
                "rule":x["prediction_rule"],
            },
            "v421":{
                "status":x["a_status"],
                "truth":[x["a_class"],x["a_tx"],x["a_own"],
                ],
                "evidence":x["a_evidence"],
            },
            "v422":{
                "status":x["z_status"],
                "truth":[x["z_class"],x["z_tx"],x["z_own"]],
                "evidence":x["z_evidence"],
            },
            "v423":{
                "status":x["r_status"],
                "truth":[x["r_class"],x["r_tx"],x["r_own"]],
                "evidence":x["r_evidence"],
            },
            "fresh_diagnostics":{
                "v421_all_judges":v421._judges(engine,raw),
                "v422_semantic_truth":v422.semantic_truth(raw),
                "v423_grammar_judge_one":v423.grammar_judge_one(raw),
                "v423_grammar_judge_two":v423.grammar_judge_two(raw),
                "v423_rescue_truth":v423.rescue_truth(raw),
            }
        })
    return {
        "version":VERSION,
        "exam_version":EXAM_VERSION,
        "unresolved_count":len(unresolved),
        "cases":foundation._json_safe(unresolved),
        "next_step":"Use this read-only forensic packet to build a generic automated resolver. Do not manually label the case.",
        "safety":{"student_tuning":0,"production_writes":0,"whatsapp_writes":0,"gold_mutations":0}
    }

def _dashboard(engine):
    data=inspect(engine)
    return f"""<!doctype html><html><head><meta charset='utf-8'>
    <title>Alliance Exception Forensics 4.2.4</title>
    <style>body{{font-family:Arial;margin:30px;background:#f6f7fb;color:#172033;max-width:1200px}}pre{{white-space:pre-wrap;background:#101624;color:#eef3ff;padding:18px;border-radius:12px}}.ok{{background:#e8f8ee;padding:14px;border-radius:10px}}</style></head><body>
    <h1>Alliance Exception Forensics 4.2.4</h1>
    <p>Read-only forensic packet for only the final unresolved V4 case(s). Frozen student remains untouched.</p>
    <div class='ok'>Unresolved cases: <b>{data["unresolved_count"]}</b>. No manual classification requested.</div>
    <h2>Machine Forensic Report</h2>
    <pre>{html.escape(json.dumps(data,ensure_ascii=False,indent=2))}</pre>
    </body></html>"""

def register(core):
    engine=_engine(core); app=_app(core)
    if not foundation._route_exists(app,"/api/property-brain/automation-v424/status"):
        @app.get("/api/property-brain/automation-v424/status")
        def status_v424(): return inspect(engine)
    if not foundation._route_exists(app,"/property-brain/automation-v424"):
        @app.get("/property-brain/automation-v424",response_class=HTMLResponse)
        def page_v424(): return HTMLResponse(_dashboard(engine))
    return {"status":"REGISTERED","version":VERSION,"route":"/property-brain/automation-v424",
            "policy":"READ_ONLY_EXCEPTION_FORENSICS","student_tuning":0,"production_writes":0,"whatsapp_writes":0,"gold_mutations":0}
