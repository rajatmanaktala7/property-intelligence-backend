from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections import defaultdict

from fastapi import Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_cre_academy_v400 as v400

VERSION = "4.0.1-ALLIANCE-CRE-ACADEMY-MASTERY-REPAIR"
MODE = "CUMULATIVE_CLASS_ATOMICITY_PRELEASED_OWNERSHIP_REPAIR_SHADOW_ONLY"
RULESET_VERSION = "CRE_ACADEMY_2026_09_03_V2"
ACADEMY_TARGET = 99.0
ADVERSARIAL_TARGET = 97.0
FIELD_TARGET = 98.0
HALLUCINATION_MAX = 1.0
DEFAULT_INTERVAL_SECONDS = 900
MAX_BATCH = 5000

DDL = [
"""CREATE TABLE IF NOT EXISTS alliance_academy_v401_runs(
run_id UUID PRIMARY KEY,
ruleset_version TEXT NOT NULL,
academy_accuracy NUMERIC(8,4),
adversarial_accuracy NUMERIC(8,4),
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

_thread_started = False
_thread_lock = threading.Lock()

def _engine(core): return foundation._engine_from_core(core)
def _app(core): return getattr(core,"app",None) or core
def _j(v): return json.dumps(foundation._json_safe(v),ensure_ascii=False)

def _install(engine):
    with engine.begin() as conn:
        for stmt in DDL: conn.execute(text(stmt))

def _norm(raw): return v400._norm(raw)
def _lines(raw): return v400._lines(raw)

def _inventory_group_strong(raw):
    n=_norm(raw)
    lines=_lines(raw)

    # 1) Explicit mixed transaction parent always owns a group, not one property.
    sale_headers=len(re.findall(r"\b(?:for sale|available for sale|deal available on sale|sale inventory)\b",n))
    rent_headers=len(re.findall(r"\b(?:for rent|available for rent|available for lease|deal on rent)\b",n))
    if sale_headers and rent_headers:
        return True,"V401_MIXED_PARENT_IS_INVENTORY_GROUP"

    # 2) Multiple alternative sale/rent options for same project/floor family.
    option_markers=len(re.findall(r"\b(?:ug|upper ground|ground|first|second|third|top|terrace|basement)\b",n))
    price_mentions=len(re.findall(r"(?:₹|rs\.?\s*)?\d+(?:\.\d+)?\s*(?:cr|crore|lac|lakh|l)\b",n))
    bhk_mentions=len(re.findall(r"\b\d(?:\.\d)?\s*bhk\b",n))
    if (sale_headers or rent_headers) and price_mentions>=2 and (option_markers>=2 or bhk_mentions>=2):
        return True,"V401_MULTI_OPTION_GROUP"

    # 3) Repeated asset/project blocks without explicit 'inventory' wording.
    project_hits=len(re.findall(r"\b(?:dlf|m3m|emaar|aipl|ireo|bestech|tulip|elan|ats|unitech|mahindra|vipul|suncity|sobha|godrej|hero homes|krisumi|raheja)\b",n))
    if project_hits>=3 and price_mentions>=2:
        return True,"V401_MULTI_PROJECT_GROUP"

    # 4) Multiple bullet rows + multiple monetary rows.
    bullets=sum(1 for x in lines if re.match(r"^\s*(?:[-•▫️]|\d+[.)])",x))
    if bullets>=3 and price_mentions>=2:
        return True,"V401_BULLET_MULTI_ASSET_GROUP"

    return False,""

def _preleased_property_identity(raw):
    n=_norm(raw)
    # Investment assets often omit BHK/plot/etc but still clearly identify a real estate asset.
    asset_terms=bool(re.search(r"\b(?:shop|office|unit|space|commercial|floor|building|mall|project|ground floor|first floor|leased area|area on sale|area offered)\b",n))
    tenancy=bool(re.search(r"\b(?:pre[- ]?leased|pre[- ]?lease|pre[- ]?rented|freshly leased|leased to|tenant|roi|rental income)\b",n))
    capital=bool(re.search(r"(?:₹|rs\.?\s*)?\d+(?:\.\d+)?\s*(?:cr|crore)\b|\b(?:demand|asking|price)\b.{0,15}\d",n))
    return asset_terms and tenancy and capital

def predict_message(raw):
    raw=raw or ""
    base=v400.predict_message(raw)
    n=_norm(raw)
    rules=[base.get("rule","V400_BASE")]
    cls=base["class"]; tx=base["transaction"]; own=base["ownership"]; conf=float(base["confidence"])
    evidence=dict(base.get("evidence") or {})

    is_group,group_rule=_inventory_group_strong(raw)
    prelease_identity=_preleased_property_identity(raw)

    # Fix 1: transaction conflict at parent level implies inventory group ownership.
    if is_group:
        cls="INVENTORY_GROUP"
        own="OWNED"
        rules.append(group_rule)
        conf=max(conf,98.7 if tx in {"SALE","RENT"} else 96.5)

    # Fix 2: pre-leased investment with capital value is a property availability even
    # when BHK/plot words are absent. This also repairs ownership.
    if prelease_identity and tx=="SALE" and not is_group:
        cls="PROPERTY_AVAILABILITY"
        own="OWNED"
        rules.append("V401_PRELEASED_INVESTMENT_IS_PROPERTY")
        conf=max(conf,99.0)

    # Fix 3: two explicit sale options in one source are an inventory group.
    if cls=="PROPERTY_AVAILABILITY":
        sale_headers=len(re.findall(r"\b(?:for sale|available for sale|sale)\b",n))
        price_mentions=len(re.findall(r"(?:₹|rs\.?\s*)?\d+(?:\.\d+)?\s*(?:cr|crore)\b",n))
        option_terms=len(re.findall(r"\b(?:ug|upper ground|top|terrace|basement|first|second|third)\b",n))
        if sale_headers and price_mentions>=2 and option_terms>=2:
            cls="INVENTORY_GROUP"; own="OWNED"; rules.append("V401_TWO_SALE_OPTIONS_GROUP"); conf=max(conf,98.8)

    # Evidence quality repair: no hallucination penalty when a SALE is directly
    # supported by tenancy+capital evidence.
    if prelease_identity and tx=="SALE":
        evidence["evidence_sufficiency"]="SUPPORTED"
    if is_group:
        evidence["inventory_group_repair"]=group_rule

    return {"class":cls,"transaction":tx,"ownership":own,"confidence":round(conf,2),"rule":"|".join(r for r in rules if r),
            "evidence":evidence}

# Reuse 4.0 curriculum but score through repaired predictor.
CURRICULUM=v400.CURRICULUM
ADVERSARIAL=v400.ADVERSARIAL

def _score_suite(suite):
    field=defaultdict(lambda:[0,0]); cases=[]; halluc=0
    for name,raw,hc,ht,ho in suite:
        p=predict_message(raw); exp={"class":hc,"transaction":ht,"ownership":ho}; ok=True
        for f in ("class","transaction","ownership"):
            field[f][1]+=1
            if p[f]==exp[f]: field[f][0]+=1
            else: ok=False
        if p["transaction"]!="UNKNOWN" and (p.get("evidence") or {}).get("evidence_sufficiency")=="INSUFFICIENT": halluc+=1
        cases.append({"name":name,"pass":ok,"expected":exp,"predicted":{k:p[k] for k in ("class","transaction","ownership","confidence","rule")}})
    total=sum(v[1] for v in field.values()); correct=sum(v[0] for v in field.values())
    return {"cases":len(suite),"case_pass":sum(1 for c in cases if c["pass"]),"accuracy":round(100*correct/max(total,1),4),
            "field_accuracy":{k:round(100*v[0]/max(v[1],1),4) for k,v in field.items()},
            "hallucination_rate":round(100*halluc/max(len(suite),1),4),
            "errors":[c for c in cases if not c["pass"]]}

# Additional locked regression cases for the exact failure families, expressed as variants
# so we test concepts rather than memorize the original strings.
REPAIR_REGRESSION = [
("mixed_portfolio_variant","AVAILABLE FOR SALE: 3 BHK 3.2 Cr, 4 BHK 4.1 Cr. AVAILABLE FOR RENT: 3 BHK 75k, 4 BHK 90k.","INVENTORY_GROUP","AMBIGUOUS","OWNED"),
("preleased_variant","Freshly leased retail unit to a fashion brand. Ground floor, rent 95,000. Asking 2.95 Cr.","PROPERTY_AVAILABILITY","SALE","OWNED"),
("two_sale_options_variant","Floors for sale: upper ground with basement 4.6 Cr; top floor with terrace 4.6 Cr.","INVENTORY_GROUP","SALE","OWNED"),
]

def academy_status():
    c=_score_suite(CURRICULUM)
    a=_score_suite(ADVERSARIAL)
    r=_score_suite(REPAIR_REGRESSION)
    floor={k:min(c["field_accuracy"][k],a["field_accuracy"][k],r["field_accuracy"][k]) for k in ("class","transaction","ownership")}
    halluc=max(c["hallucination_rate"],a["hallucination_rate"],r["hallucination_rate"])
    passed=(c["accuracy"]>=ACADEMY_TARGET and a["accuracy"]>=ADVERSARIAL_TARGET and r["accuracy"]>=100.0
            and all(v>=FIELD_TARGET for v in floor.values()) and halluc<=HALLUCINATION_MAX)
    return {"curriculum":c,"adversarial":a,"repair_regression":r,"minimum_field_accuracy":floor,
            "hallucination_rate":halluc,
            "precert_gate":"PRECERT_PASS_READY_TO_FREEZE_NEW_UNSEEN_V4" if passed else "PRECERT_HOLD_KEEP_TRAINING",
            "v3_policy":"RETIRED_UNLABELED_PRE_V400_DO_NOT_CERTIFY_V401"}

def run(engine):
    _install(engine)
    s=academy_status()
    result={"version":VERSION,"mode":MODE,"status":"PASS","academy":s,
            "safety":{"production_writes":0,"whatsapp_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0,"auto_labels_called_gold":False},
            "next_step":"If pre-certification PASS, freeze a completely new unseen V4. Do not label retired V3."}
    c=s["curriculum"]; a=s["adversarial"]; f=s["minimum_field_accuracy"]
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO alliance_academy_v401_runs
        (run_id,ruleset_version,academy_accuracy,adversarial_accuracy,class_accuracy,transaction_accuracy,ownership_accuracy,hallucination_rate,precert_gate,result,
         production_writes,whatsapp_writes,gold_v1_mutations,gold_v2_mutations)
        VALUES(:id,:rv,:ca,:aa,:cl,:tx,:ow,:hr,:gate,CAST(:res AS JSONB),0,0,0,0)"""),
        {"id":str(uuid.uuid4()),"rv":RULESET_VERSION,"ca":c["accuracy"],"aa":a["accuracy"],"cl":f["class"],"tx":f["transaction"],"ow":f["ownership"],
         "hr":s["hallucination_rate"],"gate":s["precert_gate"],"res":_j(result)})
    return result

def _latest(engine):
    with engine.connect() as conn:
        return conn.execute(text("SELECT result FROM alliance_academy_v401_runs ORDER BY created_at DESC LIMIT 1")).scalar() or {}

def _dashboard(engine):
    s=academy_status(); l=_latest(engine)
    c=s["curriculum"]; a=s["adversarial"]; r=s["repair_regression"]; f=s["minimum_field_accuracy"]
    errors=c["errors"]+a["errors"]+r["errors"]
    err="".join(f"<details><summary>{e['name']}</summary><pre>{json.dumps(e,ensure_ascii=False,indent=2)}</pre></details>" for e in errors) or "<p>✅ No academy/adversarial/regression failures.</p>"
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Alliance CRE Academy 4.0.1</title>
    <style>body{{font-family:Arial;margin:30px;background:#f6f7fb;color:#172033}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}}
    .card{{background:white;padding:18px;border-radius:14px;box-shadow:0 2px 10px #0001}}strong{{display:block;font-size:28px;margin-top:7px}}
    .gate{{margin:18px 0;padding:16px;background:#fff4cf;border-radius:12px;font-weight:700}}button{{padding:12px 18px;background:#172033;color:white;border:0;border-radius:9px}}
    pre{{white-space:pre-wrap;background:#101624;color:#eaf0ff;padding:14px;border-radius:10px}}details{{background:white;padding:10px;margin:8px 0;border-radius:9px}}</style></head><body>
    <h1>Alliance CRE Academy 4.0.1 — Mastery Repair</h1>
    <p>Repairs mixed-parent grouping, pre-leased investment identity/ownership, and multi-option inventory recognition. All earlier foundations remain intact.</p>
    <div class='grid'><div class='card'>Academy<strong>{c['accuracy']}%</strong></div><div class='card'>Adversarial<strong>{a['accuracy']}%</strong></div>
    <div class='card'>Repair Regression<strong>{r['accuracy']}%</strong></div><div class='card'>Class Floor<strong>{f['class']}%</strong></div>
    <div class='card'>Transaction Floor<strong>{f['transaction']}%</strong></div><div class='card'>Ownership Floor<strong>{f['ownership']}%</strong></div>
    <div class='card'>Hallucination<strong>{s['hallucination_rate']}%</strong></div></div>
    <div class='gate'>{s['precert_gate']}</div>
    <form method='post' action='/api/property-brain/academy-v401/run'><button>Run 4.0.1 Mastery Repair</button></form>
    <h2>Remaining failures</h2>{err}<h2>Latest Run</h2><pre>{json.dumps(foundation._json_safe(l),ensure_ascii=False,indent=2)}</pre></body></html>"""

def register(core):
    engine=_engine(core); app=_app(core); _install(engine)
    if not foundation._route_exists(app,"/api/property-brain/academy-v401/status"):
        @app.get("/api/property-brain/academy-v401/status")
        def status_v401(): return {"version":VERSION,"mode":MODE,"academy":academy_status(),"latest":_latest(engine),
                                   "safety":{"production_writes":0,"whatsapp_writes":0,"gold_mutations":0}}
    if not foundation._route_exists(app,"/api/property-brain/academy-v401/run"):
        @app.post("/api/property-brain/academy-v401/run")
        def run_v401(): return run(engine)
    if not foundation._route_exists(app,"/property-brain/academy-v401"):
        @app.get("/property-brain/academy-v401",response_class=HTMLResponse)
        def page_v401(): return HTMLResponse(_dashboard(engine))
    try: run(engine)
    except Exception: pass
    return {"status":"REGISTERED","version":VERSION,"route":"/property-brain/academy-v401","production_writes":0,"whatsapp_writes":0,"gold_mutations":0}
