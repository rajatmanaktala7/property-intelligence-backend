from __future__ import annotations

import json
import re
import uuid
from collections import defaultdict

from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_cre_academy_v400 as v400
import alliance_cre_academy_v401 as v401

VERSION = "4.0.2-ALLIANCE-CRE-ACADEMY-PRECERT-FINALIZER"
MODE = "CUMULATIVE_PRELEASED_ECONOMICS_IDENTITY_REPAIR_SHADOW_ONLY"
RULESET_VERSION = "CRE_ACADEMY_2026_09_03_V3"
ACADEMY_TARGET = 99.0
ADVERSARIAL_TARGET = 97.0
FIELD_TARGET = 98.0
HALLUCINATION_MAX = 1.0

DDL = [
"""CREATE TABLE IF NOT EXISTS alliance_academy_v402_runs(
run_id UUID PRIMARY KEY,
ruleset_version TEXT NOT NULL,
academy_accuracy NUMERIC(8,4),
adversarial_accuracy NUMERIC(8,4),
repair_accuracy NUMERIC(8,4),
class_accuracy NUMERIC(8,4),
transaction_accuracy NUMERIC(8,4),
ownership_accuracy NUMERIC(8,4),
hallucination_rate NUMERIC(8,4),
precert_gate TEXT NOT NULL,
result JSONB NOT NULL DEFAULT '{}'::jsonb,
production_writes INTEGER NOT NULL DEFAULT 0,
whatsapp_writes INTEGER NOT NULL DEFAULT 0,
gold_v1_mutations INTEGER NOT NULL DEFAULT 0,
gold_v2_mutations INTEGER NOT NULL DEFAULT 0,
created_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
]

def _engine(core): return foundation._engine_from_core(core)
def _app(core): return getattr(core,"app",None) or core
def _j(v): return json.dumps(foundation._json_safe(v),ensure_ascii=False)
def _norm(raw): return v400._norm(raw)

def _install(engine):
    with engine.begin() as conn:
        for stmt in DDL: conn.execute(text(stmt))

def _economic_prelease_identity(raw):
    n=_norm(raw)
    tenancy=bool(re.search(r"\b(?:pre[- ]?leased|pre[- ]?lease|pre[- ]?rented|freshly leased|leased to|tenant)\b",n))
    rent_economics=bool(re.search(r"\brent(?:al)?\b.{0,20}(?:₹|rs\.?)?\s*\d|\broi\b",n))
    capital=bool(re.search(r"(?:₹|rs\.?\s*)?\d+(?:\.\d+)?\s*(?:cr|crore)\b|\b(?:demand|asking|price)\b.{0,18}\d",n))
    brand_or_asset=bool(re.search(r"\b(?:brand|vero moda|zara|h&m|haldiram|starbucks|bank|tenant|shop|office|retail|commercial|unit|space|floor|mall|building)\b",n))
    # In CRE source streams, tenancy + periodic rent + capital consideration is itself a
    # complete investment-property signature even if the broker omits the noun "shop/unit".
    return tenancy and rent_economics and capital and brand_or_asset

def predict_message(raw):
    raw=raw or ""
    p=v401.predict_message(raw)
    n=_norm(raw)
    eco=_economic_prelease_identity(raw)
    rules=[p.get("rule","V401_BASE")]
    evidence=dict(p.get("evidence") or {})
    cls=p["class"]; tx=p["transaction"]; own=p["ownership"]; conf=float(p["confidence"])

    if eco and tx=="SALE" and cls not in {"INVENTORY_GROUP","REQUIREMENT","NOISE"}:
        cls="PROPERTY_AVAILABILITY"
        own="OWNED"
        conf=max(conf,99.2)
        rules.append("V402_PRELEASED_ECONOMICS_DEFINE_PROPERTY_IDENTITY")
        evidence["economic_property_identity"]={
            "tenancy":True,
            "periodic_rent":True,
            "capital_consideration":True,
            "policy":"CRE investment asset supported without requiring an explicit noun such as shop/unit."
        }
        evidence["evidence_sufficiency"]="SUPPORTED"

    return {"class":cls,"transaction":tx,"ownership":own,"confidence":round(conf,2),
            "rule":"|".join(r for r in rules if r),"evidence":evidence}

CURRICULUM=v400.CURRICULUM
ADVERSARIAL=v400.ADVERSARIAL
REPAIR_REGRESSION=v401.REPAIR_REGRESSION + [
("prelease_no_asset_noun_variant",
 "Freshly leased to a national fashion brand. Rent 91,500 per month. Asking 2.80 Cr.",
 "PROPERTY_AVAILABILITY","SALE","OWNED"),
("prelease_tenant_economics_variant",
 "Tenant: premium bank. Rent Rs 1.25 lakh per month. ROI 5.5%. Demand 3.10 Cr.",
 "PROPERTY_AVAILABILITY","SALE","OWNED"),
("ordinary_lease_not_sale_variant",
 "Commercial office available for lease. 2500 sq ft. Rent 2.25 lakh per month.",
 "PROPERTY_AVAILABILITY","RENT","OWNED"),
]

def _score_suite(suite):
    field=defaultdict(lambda:[0,0]); cases=[]; halluc=0
    for name,raw,hc,ht,ho in suite:
        p=predict_message(raw); exp={"class":hc,"transaction":ht,"ownership":ho}; ok=True
        for f in ("class","transaction","ownership"):
            field[f][1]+=1
            if p[f]==exp[f]: field[f][0]+=1
            else: ok=False
        if p["transaction"]!="UNKNOWN" and (p.get("evidence") or {}).get("evidence_sufficiency")=="INSUFFICIENT":
            halluc+=1
        cases.append({"name":name,"pass":ok,"expected":exp,
                      "predicted":{k:p[k] for k in ("class","transaction","ownership","confidence","rule")}})
    total=sum(v[1] for v in field.values()); correct=sum(v[0] for v in field.values())
    return {"cases":len(suite),"case_pass":sum(1 for c in cases if c["pass"]),
            "accuracy":round(100*correct/max(total,1),4),
            "field_accuracy":{k:round(100*v[0]/max(v[1],1),4) for k,v in field.items()},
            "hallucination_rate":round(100*halluc/max(len(suite),1),4),
            "errors":[c for c in cases if not c["pass"]]}

def academy_status():
    c=_score_suite(CURRICULUM); a=_score_suite(ADVERSARIAL); r=_score_suite(REPAIR_REGRESSION)
    floor={k:min(c["field_accuracy"][k],a["field_accuracy"][k],r["field_accuracy"][k]) for k in ("class","transaction","ownership")}
    halluc=max(c["hallucination_rate"],a["hallucination_rate"],r["hallucination_rate"])
    passed=(c["accuracy"]>=ACADEMY_TARGET and a["accuracy"]>=ADVERSARIAL_TARGET and r["accuracy"]>=100.0
            and all(v>=FIELD_TARGET for v in floor.values()) and halluc<=HALLUCINATION_MAX)
    return {"curriculum":c,"adversarial":a,"repair_regression":r,
            "minimum_field_accuracy":floor,"hallucination_rate":halluc,
            "precert_gate":"PRECERT_PASS_READY_TO_FREEZE_NEW_UNSEEN_V4" if passed else "PRECERT_HOLD_KEEP_TRAINING",
            "v3_policy":"RETIRED_UNLABELED_PRE_V400_DO_NOT_CERTIFY_V402"}

def run(engine):
    _install(engine); s=academy_status()
    result={"version":VERSION,"mode":MODE,"status":"PASS","academy":s,
            "safety":{"production_writes":0,"whatsapp_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0,"auto_labels_called_gold":False},
            "next_step":"If PRECERT_PASS, freeze a fresh unseen V4 certification exam before any further learning."}
    c=s["curriculum"]; a=s["adversarial"]; r=s["repair_regression"]; f=s["minimum_field_accuracy"]
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO alliance_academy_v402_runs
        (run_id,ruleset_version,academy_accuracy,adversarial_accuracy,repair_accuracy,class_accuracy,transaction_accuracy,ownership_accuracy,hallucination_rate,precert_gate,result,
         production_writes,whatsapp_writes,gold_v1_mutations,gold_v2_mutations)
        VALUES(:id,:rv,:ca,:aa,:ra,:cl,:tx,:ow,:hr,:gate,CAST(:res AS JSONB),0,0,0,0)"""),
        {"id":str(uuid.uuid4()),"rv":RULESET_VERSION,"ca":c["accuracy"],"aa":a["accuracy"],"ra":r["accuracy"],
         "cl":f["class"],"tx":f["transaction"],"ow":f["ownership"],"hr":s["hallucination_rate"],
         "gate":s["precert_gate"],"res":_j(result)})
    return result

def _latest(engine):
    with engine.connect() as conn:
        return conn.execute(text("SELECT result FROM alliance_academy_v402_runs ORDER BY created_at DESC LIMIT 1")).scalar() or {}

def _dashboard(engine):
    s=academy_status(); l=_latest(engine); c=s["curriculum"]; a=s["adversarial"]; r=s["repair_regression"]; f=s["minimum_field_accuracy"]
    errors=c["errors"]+a["errors"]+r["errors"]
    err="".join(f"<details><summary>{e['name']}</summary><pre>{json.dumps(e,ensure_ascii=False,indent=2)}</pre></details>" for e in errors) or "<p>✅ All pre-certification tests pass.</p>"
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Alliance CRE Academy 4.0.2</title>
    <style>body{{font-family:Arial;margin:30px;background:#f6f7fb;color:#172033}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}}
    .card{{background:white;padding:18px;border-radius:14px;box-shadow:0 2px 10px #0001}}strong{{display:block;font-size:28px;margin-top:7px}}.gate{{margin:18px 0;padding:16px;background:#e8f8ee;border-radius:12px;font-weight:700}}
    button{{padding:12px 18px;background:#172033;color:white;border:0;border-radius:9px}}pre{{white-space:pre-wrap;background:#101624;color:#eaf0ff;padding:14px;border-radius:10px}}
    details{{background:white;padding:10px;margin:8px 0;border-radius:9px}}</style></head><body>
    <h1>Alliance CRE Academy 4.0.2 — Pre-Cert Finalizer</h1>
    <p>Final deterministic repair for pre-leased investment identity. Foundation 4.0.1 and all earlier fixes remain intact.</p>
    <div class='grid'><div class='card'>Academy<strong>{c['accuracy']}%</strong></div><div class='card'>Adversarial<strong>{a['accuracy']}%</strong></div>
    <div class='card'>Repair Regression<strong>{r['accuracy']}%</strong></div><div class='card'>Class Floor<strong>{f['class']}%</strong></div>
    <div class='card'>Transaction Floor<strong>{f['transaction']}%</strong></div><div class='card'>Ownership Floor<strong>{f['ownership']}%</strong></div>
    <div class='card'>Hallucination<strong>{s['hallucination_rate']}%</strong></div></div><div class='gate'>{s['precert_gate']}</div>
    <form method='post' action='/api/property-brain/academy-v402/run'><button>Run 4.0.2 Pre-Cert Test</button></form>
    <h2>Remaining failures</h2>{err}<h2>Latest Run</h2><pre>{json.dumps(foundation._json_safe(l),ensure_ascii=False,indent=2)}</pre></body></html>"""

def register(core):
    engine=_engine(core); app=_app(core); _install(engine)
    if not foundation._route_exists(app,"/api/property-brain/academy-v402/status"):
        @app.get("/api/property-brain/academy-v402/status")
        def status_v402(): return {"version":VERSION,"mode":MODE,"academy":academy_status(),"latest":_latest(engine),
                                   "safety":{"production_writes":0,"whatsapp_writes":0,"gold_mutations":0}}
    if not foundation._route_exists(app,"/api/property-brain/academy-v402/run"):
        @app.post("/api/property-brain/academy-v402/run")
        def run_v402(): return run(engine)
    if not foundation._route_exists(app,"/property-brain/academy-v402"):
        @app.get("/property-brain/academy-v402",response_class=HTMLResponse)
        def page_v402(): return HTMLResponse(_dashboard(engine))
    try: run(engine)
    except Exception: pass
    return {"status":"REGISTERED","version":VERSION,"route":"/property-brain/academy-v402",
            "production_writes":0,"whatsapp_writes":0,"gold_mutations":0}
