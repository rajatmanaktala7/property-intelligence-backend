from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections import Counter

from fastapi import Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_blind_failure_learning_v360 as v360

VERSION = "3.7.0-AUTONOMOUS-PROPERTY-BRAIN-TEACHER"
MODE = "EXCEPTION_ONLY_AUTOTEACHER_SILVER_SHADOW_NO_PRODUCTION_WRITES"
ENGINE_VERSION = "ALLIANCE_AUTONOMOUS_PROPERTY_BRAIN_V370"
RULESET_VERSION = "AUTONOMOUS_PROPERTY_BRAIN_2026_09_03_V1"

AUTO_ACCEPT = 98.0
SHADOW_ACCEPT = 90.0
DEFAULT_INTERVAL_SECONDS = 900
MAX_BATCH = 5000

DDL = [
"""CREATE TABLE IF NOT EXISTS alliance_autonomous_teacher_v370_predictions(
prediction_id UUID PRIMARY KEY,
source_table TEXT NOT NULL,
source_id TEXT NOT NULL,
raw_hash TEXT NOT NULL,
raw_text TEXT NOT NULL,
predicted_class TEXT NOT NULL,
predicted_transaction TEXT NOT NULL,
predicted_ownership TEXT NOT NULL,
confidence NUMERIC(6,2) NOT NULL,
disposition TEXT NOT NULL,
rule_id TEXT NOT NULL,
evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
certification_gate TEXT NOT NULL,
ruleset_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(source_table,source_id,ruleset_version))""",

"""CREATE TABLE IF NOT EXISTS alliance_autonomous_teacher_v370_exceptions(
exception_id UUID PRIMARY KEY,
prediction_id UUID NOT NULL,
source_table TEXT NOT NULL,
source_id TEXT NOT NULL,
raw_hash TEXT NOT NULL,
reason_code TEXT NOT NULL,
payload JSONB NOT NULL DEFAULT '{}'::jsonb,
review_status TEXT NOT NULL DEFAULT 'OPEN',
ruleset_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(prediction_id))""",

"""CREATE TABLE IF NOT EXISTS alliance_autonomous_teacher_v370_runs(
run_id UUID PRIMARY KEY,
ruleset_version TEXT NOT NULL,
exam_v2_total INTEGER NOT NULL DEFAULT 0,
exam_v2_labeled INTEGER NOT NULL DEFAULT 0,
exam_v2_accuracy NUMERIC(8,4),
expertise_gate TEXT NOT NULL,
source_rows_seen INTEGER NOT NULL DEFAULT 0,
new_predictions INTEGER NOT NULL DEFAULT 0,
auto_accept INTEGER NOT NULL DEFAULT 0,
shadow_accept INTEGER NOT NULL DEFAULT 0,
exceptions INTEGER NOT NULL DEFAULT 0,
production_writes INTEGER NOT NULL DEFAULT 0,
whatsapp_writes INTEGER NOT NULL DEFAULT 0,
gold_v1_mutations INTEGER NOT NULL DEFAULT 0,
gold_v2_mutations INTEGER NOT NULL DEFAULT 0,
result JSONB NOT NULL DEFAULT '{}'::jsonb,
created_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
]

SOURCE_PRIORITY = ["wai_raw_messages", "ai_whatsapp_purity", "alliance_live_feed_entities"]
TEXT_COLUMNS = ["raw_text", "message_text", "raw_message", "source_text", "content", "text", "body", "message"]
ID_COLUMNS = ["id", "message_id", "entity_id", "listing_id", "source_message_id"]

_thread_started = False
_thread_lock = threading.Lock()


def _engine(core): return foundation._engine_from_core(core)
def _app(core): return getattr(core, "app", None) or core
def _j(v): return json.dumps(foundation._json_safe(v), ensure_ascii=False)


def _install(engine):
    with engine.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))


def _norm(s):
    s = (s or "").lower()
    s = re.sub(r"https?://\S+", " URL ", s, flags=re.I)
    s = re.sub(r"[^a-z0-9₹+./@\- ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _lines(raw): return [re.sub(r"\s+", " ", x).strip() for x in (raw or "").splitlines() if x.strip()]


def _phone(raw):
    compact = re.sub(r"[\s()-]", "", raw or "")
    return bool(re.search(r"(?<!\d)(?:\+?91)?[6-9]\d{9}(?!\d)", compact))


def _noise(raw):
    n = _norm(raw)
    lines = _lines(raw)
    urls = re.findall(r"https?://\S+", raw or "", re.I)
    without_urls = re.sub(r"https?://\S+", " ", raw or "", flags=re.I)
    without_urls = re.sub(r"[^A-Za-z0-9]+", " ", without_urls).strip()
    if urls and not without_urls:
        return True, "V370_NOISE_STANDALONE_URL"
    greeting = bool(re.search(r"\b(?:good morning|good evening|good night|happy raksha|raksha bandhan|rakshabandhan|hardik shubhkamnaye)\b|हार्दिक शुभकामनाएं|शुभकामनाएं", raw or "", re.I))
    cre = bool(re.search(r"\b(?:for rent|for sale|available|requirement|required|wanted|bhk|sq\.?\s*ft|sq\.?\s*yd|shop|office|villa|floor|plot|rent|demand|asking|lease)\b", n, re.I))
    if greeting and not cre:
        return True, "V370_NOISE_GREETING"
    admin = bool(re.search(r"\b(?:keep this group|group for rented properties|group for rental properties|request everyone to keep this group)\b", n, re.I))
    if admin and not re.search(r"\b(?:available|required|wanted|for sale|for rent)\b", n):
        return True, "V370_NOISE_GROUP_ADMIN"
    if len(lines) <= 2 and re.fullmatch(r"(?:url\s*){1,3}", n):
        return True, "V370_NOISE_LINK_ONLY"
    return False, ""


def _requirement(raw):
    return bool(re.search(r"\b(?:wanted|immediate required|required|requirement|looking for|need(?:ed)?|seeking|urgent rental requirement|client budget|tenant meeting)\b", _norm(raw), re.I))


def _rent(raw):
    return bool(re.search(r"\b(?:available for rent|avail for rent|for rent|to let|to-let|wanted for rent|required on rent|rental requirement|asking rent|rent\s*[:@]|lease(?:d|ing)?\b)\b", _norm(raw), re.I))


def _sale(raw):
    return bool(re.search(r"\b(?:for sale|sale inventory|inventories? .* sale|outright|out-right|resale|asking price|demand\s*[:@-]?|price\s*[:@-]?)\b", _norm(raw), re.I))


def _pre_rented_sale(raw):
    n = _norm(raw)
    tenancy = bool(re.search(r"\b(?:pre[- ]?rented|pre[- ]?lease(?:d)?|rental income|tenant)\b", n))
    capital = bool(re.search(r"\b(?:demand|asking|price)\b[^0-9₹]{0,15}(?:₹|rs\.?)?\s*\d+(?:\.\d+)?\s*(?:cr|crore)\b", n))
    header = bool(re.search(r"\b(?:asset deals? for sale|for sale)\b", n))
    return tenancy and (capital or header)


def _mixed_sale_rent(raw):
    n = _norm(raw)
    explicit_sale = bool(re.search(r"\bshops? for sale\b|\bfor sale\b", n))
    explicit_rent = bool(re.search(r"\bshops? .* rent\b|\bfor rent\b|\boptions .* rent\b", n))
    return explicit_sale and explicit_rent and not _pre_rented_sale(raw)


def _inventory_group(raw):
    n = _norm(raw)
    lines = _lines(raw)
    explicit = bool(re.search(r"\b(?:inventor(?:y|ies)|many options|multiple units|multiple inventories|asset deals|shops for sale and rent|pre[- ]?rent(?:ed)? asset deals)\b", n))
    separators = sum(1 for x in lines if re.fullmatch(r"[_\-]{5,}", x))
    projects = len(re.findall(r"\b(?:sector[- ]?\d+[a-z]?|dlf phase\s*\d+|aipl joy|m3m |emaar |ireo |bestech |tulip |elan |hero homes|krisumi |ss linden|ats )", n))
    price_mentions = len(re.findall(r"\b(?:demand|asking|price)\b", n))
    detailed_single = price_mentions <= 1 and projects <= 1 and len(re.findall(r"\b\d+(?:\.\d+)?\s*(?:sq ?ft|sqft|sq ?yds?|syds?)\b", n)) <= 1
    if explicit and not detailed_single: return True
    if separators >= 2 and price_mentions >= 2: return True
    if projects >= 3 and price_mentions >= 2: return True
    return False


def _property_identity(raw):
    return bool(re.search(r"\b(?:\d+(?:\.\d+)?\s*(?:sq ?ft|sqft|sq ?yds?|syds?|sqm|sq ?m)|\d+(?:\.\d+)?\s*bhk|builder floor|apartment|flat|villa|farm house|farmhouse|basement|shop|office|plot|building|penthouse)\b", _norm(raw)))


def predict_message(raw):
    base = v360.predict_message(raw or "")
    n = _norm(raw)
    is_noise, noise_rule = _noise(raw)
    req = _requirement(raw)
    rent = _rent(raw)
    sale = _sale(raw)
    pre_sale = _pre_rented_sale(raw)
    mixed = _mixed_sale_rent(raw)
    group = _inventory_group(raw)
    prop = _property_identity(raw)
    phone = _phone(raw)
    rules = []

    if is_noise:
        cls, tx, own, conf = "NOISE", "UNKNOWN", "NOT_OWNED", 99.5
        rules.append(noise_rule)
    else:
        if req:
            cls = "REQUIREMENT"; rules.append("V370_DEMAND_SIDE_REQUIREMENT")
        elif group:
            cls = "INVENTORY_GROUP"; rules.append("V370_MULTI_PROPERTY_INVENTORY")
        elif prop and (rent or sale or pre_sale):
            cls = "PROPERTY_AVAILABILITY"; rules.append("V370_SPECIFIC_PROPERTY_AVAILABILITY")
        else:
            cls = base.get("class") or "UNRESOLVED"; rules.append("V370_BASE_CLASS")

        if req:
            if rent:
                tx = "RENT"; rules.append("V370_REQUIREMENT_RENT")
            elif sale:
                tx = "SALE"; rules.append("V370_REQUIREMENT_SALE")
            else:
                buyer_signal = bool(re.search(r"\b(?:clear title|immediate payment|client budget)\b", n))
                tx = "SALE" if buyer_signal else "UNKNOWN"
                rules.append("V370_REQUIREMENT_BUYER_SEMANTICS" if buyer_signal else "V370_REQUIREMENT_TX_ABSTAIN")
        elif pre_sale:
            tx = "SALE"; rules.append("V370_PRE_RENTED_IS_SALE")
        elif mixed:
            tx = "AMBIGUOUS"; rules.append("V370_EXPLICIT_MIXED_SALE_RENT")
        elif sale and not rent:
            tx = "SALE"; rules.append("V370_EXPLICIT_SALE")
        elif rent and not sale:
            tx = "RENT"; rules.append("V370_EXPLICIT_RENT")
        elif sale and rent:
            if re.search(r"\b(?:tenant|pre[- ]?rent|rental income)\b", n) and re.search(r"\b(?:demand|asking|price)\b", n):
                tx = "SALE"; rules.append("V370_TENANCY_RENT_NOT_TRANSACTION")
            else:
                tx = "AMBIGUOUS"; rules.append("V370_DUAL_TX_ABSTAIN")
        else:
            tx = base.get("transaction") or "UNKNOWN"; rules.append("V370_BASE_TX")

        if cls in ("PROPERTY_AVAILABILITY", "INVENTORY_GROUP", "REQUIREMENT"):
            own = "OWNED"; rules.append("V370_MESSAGE_OWNS_CRE_INTENT")
        elif cls == "NOISE":
            own = "NOT_OWNED"; rules.append("V370_NOISE_NOT_OWNED")
        else:
            own = base.get("ownership") or "AMBIGUOUS"; rules.append("V370_BASE_OWNERSHIP")

        if cls == "REQUIREMENT" and tx in ("SALE", "RENT") and own == "OWNED": conf = 99.0
        elif cls == "INVENTORY_GROUP" and tx in ("SALE", "RENT") and own == "OWNED": conf = 98.7
        elif cls == "PROPERTY_AVAILABILITY" and tx in ("SALE", "RENT") and own == "OWNED" and prop: conf = 99.0
        elif cls == "INVENTORY_GROUP" and tx == "AMBIGUOUS" and own == "OWNED": conf = 96.0
        else: conf = min(float(base.get("confidence") or 75.0), 94.0)

    evidence = {"requirement_signal": req, "rent_signal": rent, "sale_signal": sale, "pre_rented_sale": pre_sale, "mixed_sale_rent": mixed, "inventory_group": group, "property_identity": prop, "contact_present": phone, "base_prediction": base}
    return {"class": cls, "transaction": tx, "ownership": own, "confidence": round(conf, 2), "rule": "|".join(rules), "evidence": evidence}


def _certification(engine):
    v360_result = v360.run(engine, 1000)
    return v360_result.get("expertise_gate") or "UNKNOWN", v360.exam_v2_status(engine)


def _columns(conn, table_name):
    return set(conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=:t"), {"t": table_name}).scalars().all())


def _source_specs(engine):
    specs = []
    with engine.connect() as conn:
        for table_name in SOURCE_PRIORITY:
            exists = conn.execute(text("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name=:t)"), {"t": table_name}).scalar()
            if not exists: continue
            cols = _columns(conn, table_name)
            txt = next((c for c in TEXT_COLUMNS if c in cols), None)
            ident = next((c for c in ID_COLUMNS if c in cols), None)
            if txt and ident: specs.append((table_name, ident, txt))
    return specs


def _fetch_candidates(engine, limit):
    out = []
    specs = _source_specs(engine)
    per_table = max(1, int(limit / max(len(specs), 1)))
    with engine.connect() as conn:
        for table_name, id_col, text_col in specs:
            sql = f"SELECT CAST({id_col} AS TEXT) AS source_id, CAST({text_col} AS TEXT) AS raw_text FROM {table_name} WHERE {text_col} IS NOT NULL AND length(trim(CAST({text_col} AS TEXT)))>0 ORDER BY {id_col} DESC LIMIT :lim"
            for r in conn.execute(text(sql), {"lim": per_table}).mappings().all():
                out.append({"source_table": table_name, "source_id": r["source_id"], "raw_text": r["raw_text"]})
    return out[:limit]


def _already(engine, source_table, source_id):
    with engine.connect() as conn:
        return bool(conn.execute(text("SELECT 1 FROM alliance_autonomous_teacher_v370_predictions WHERE source_table=:t AND source_id=:i AND ruleset_version=:r LIMIT 1"), {"t": source_table, "i": source_id, "r": RULESET_VERSION}).scalar())


def _disposition(pred, gate):
    ambiguous = pred["class"] in ("UNRESOLVED", "AMBIGUOUS", "FRAGMENT") or pred["ownership"] == "AMBIGUOUS" or (pred["transaction"] in ("UNKNOWN", "AMBIGUOUS") and pred["class"] != "NOISE")
    if ambiguous or pred["confidence"] < SHADOW_ACCEPT: return "EXCEPTION"
    if pred["confidence"] >= AUTO_ACCEPT:
        return "AUTO_ACCEPT_SILVER" if gate == "EXPERTISE_V1_GATE_PASS" else "SHADOW_ACCEPT"
    return "SHADOW_ACCEPT"


def _save_prediction(engine, row, pred, disposition, gate):
    raw_hash = hashlib.sha256((row["raw_text"] or "").encode("utf-8")).hexdigest()
    prediction_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO alliance_autonomous_teacher_v370_predictions
          (prediction_id,source_table,source_id,raw_hash,raw_text,predicted_class,predicted_transaction,predicted_ownership,confidence,disposition,rule_id,evidence,certification_gate,ruleset_version)
          VALUES(:pid,:st,:sid,:rh,:raw,:pc,:pt,:po,:cf,:d,:rule,CAST(:ev AS jsonb),:gate,:rv)
          ON CONFLICT(source_table,source_id,ruleset_version) DO NOTHING"""),
          {"pid":prediction_id,"st":row["source_table"],"sid":row["source_id"],"rh":raw_hash,"raw":row["raw_text"],"pc":pred["class"],"pt":pred["transaction"],"po":pred["ownership"],"cf":pred["confidence"],"d":disposition,"rule":pred["rule"],"ev":_j(pred["evidence"]),"gate":gate,"rv":RULESET_VERSION})
        actual = conn.execute(text("SELECT prediction_id FROM alliance_autonomous_teacher_v370_predictions WHERE source_table=:st AND source_id=:sid AND ruleset_version=:rv"), {"st":row["source_table"],"sid":row["source_id"],"rv":RULESET_VERSION}).scalar()
        if disposition == "EXCEPTION" and actual:
            conn.execute(text("""INSERT INTO alliance_autonomous_teacher_v370_exceptions
              (exception_id,prediction_id,source_table,source_id,raw_hash,reason_code,payload,ruleset_version)
              VALUES(:eid,:pid,:st,:sid,:rh,:rc,CAST(:p AS jsonb),:rv) ON CONFLICT(prediction_id) DO NOTHING"""),
              {"eid":str(uuid.uuid4()),"pid":str(actual),"st":row["source_table"],"sid":row["source_id"],"rh":raw_hash,"rc":"LOW_CONFIDENCE_OR_AMBIGUOUS","p":_j({"prediction":pred,"raw_text":row["raw_text"]}),"rv":RULESET_VERSION})


def run(engine, limit=500):
    _install(engine)
    gate, exam = _certification(engine)
    candidates = _fetch_candidates(engine, min(max(int(limit), 1), MAX_BATCH))
    counts = Counter(); new_predictions = 0
    for row in candidates:
        if _already(engine, row["source_table"], row["source_id"]): continue
        pred = predict_message(row["raw_text"])
        disposition = _disposition(pred, gate)
        _save_prediction(engine, row, pred, disposition, gate)
        counts[disposition] += 1; new_predictions += 1
    result = {
        "status":"PASS","version":VERSION,"mode":MODE,"ruleset_version":RULESET_VERSION,
        "exam_v2":exam,"expertise_gate":gate,"source_rows_seen":len(candidates),"new_predictions":new_predictions,
        "auto_accept_silver":counts["AUTO_ACCEPT_SILVER"],"shadow_accept":counts["SHADOW_ACCEPT"],"exceptions":counts["EXCEPTION"],
        "automation":{"manual_routine_labeling":"STOPPED","normal_flow":"AUTOMATED","human_review":"EXCEPTIONS_ONLY_PLUS_PERIODIC_QA_SAMPLE","certification":"INDEPENDENT_BLIND_AUDIT_ONLY"},
        "safety":{"production_writes":0,"whatsapp_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0,"auto_labels_called_gold":False},
        "next_step":"EXPERTISE PASS: high-confidence deterministic predictions may enter Silver shadow/auto-accept tables." if gate=="EXPERTISE_V1_GATE_PASS" else "EXPERTISE HOLD: all high-confidence predictions remain shadow; exceptions are isolated automatically."
    }
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO alliance_autonomous_teacher_v370_runs
          (run_id,ruleset_version,exam_v2_total,exam_v2_labeled,exam_v2_accuracy,expertise_gate,source_rows_seen,new_predictions,auto_accept,shadow_accept,exceptions,result,production_writes,whatsapp_writes,gold_v1_mutations,gold_v2_mutations)
          VALUES(:id,:rv,:et,:el,:ea,:eg,:seen,:new,:auto,:shadow,:exc,CAST(:r AS jsonb),0,0,0,0)"""),
          {"id":str(uuid.uuid4()),"rv":RULESET_VERSION,"et":exam.get("total") or 0,"el":exam.get("labeled") or 0,"ea":exam.get("accuracy"),"eg":gate,"seen":len(candidates),"new":new_predictions,"auto":counts["AUTO_ACCEPT_SILVER"],"shadow":counts["SHADOW_ACCEPT"],"exc":counts["EXCEPTION"],"r":_j(result)})
    return foundation._json_safe(result)


def status(engine):
    _install(engine)
    with engine.connect() as conn:
        latest = conn.execute(text("SELECT result FROM alliance_autonomous_teacher_v370_runs WHERE ruleset_version=:r ORDER BY created_at DESC LIMIT 1"), {"r":RULESET_VERSION}).scalar()
        counts = dict(conn.execute(text("SELECT disposition, count(*) AS n FROM alliance_autonomous_teacher_v370_predictions WHERE ruleset_version=:r GROUP BY disposition"), {"r":RULESET_VERSION}).all())
        open_exceptions = conn.execute(text("SELECT count(*) FROM alliance_autonomous_teacher_v370_exceptions WHERE ruleset_version=:r AND review_status='OPEN'"), {"r":RULESET_VERSION}).scalar() or 0
    if isinstance(latest, str):
        try: latest = json.loads(latest)
        except Exception: latest = {}
    return foundation._json_safe({"status":"PASS","version":VERSION,"latest_run":latest or {},"prediction_counts":counts,"open_exceptions":int(open_exceptions),"production_writes":0,"whatsapp_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0})


def exceptions(engine, limit=100):
    _install(engine)
    with engine.connect() as conn:
        rows = conn.execute(text("""SELECT e.exception_id,e.source_table,e.source_id,e.reason_code,e.review_status,e.created_at,p.predicted_class,p.predicted_transaction,p.predicted_ownership,p.confidence,p.raw_text
          FROM alliance_autonomous_teacher_v370_exceptions e JOIN alliance_autonomous_teacher_v370_predictions p ON p.prediction_id=e.prediction_id
          WHERE e.ruleset_version=:r AND e.review_status='OPEN' ORDER BY e.created_at DESC LIMIT :lim"""), {"r":RULESET_VERSION,"lim":int(limit)}).mappings().all()
    return foundation._json_safe([dict(r) for r in rows])


DASHBOARD = r'''<!doctype html><html><head><meta charset="utf-8"><title>Alliance Property Brain 3.7</title>
<style>body{font-family:Arial;background:#07111d;color:#eef6ff;max-width:1400px;margin:24px auto;padding:0 12px}.card{background:#101d2b;padding:16px;border-radius:12px;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.big{font-size:26px;font-weight:bold}button{padding:11px 16px;border:0;border-radius:8px;background:#f5d76e;font-weight:bold;cursor:pointer}.good{background:#123d2a}.warn{background:#3c3011}.muted{opacity:.76}pre{white-space:pre-wrap;overflow-wrap:anywhere}@media(max-width:800px){.grid{grid-template-columns:1fr 1fr}}</style></head><body>
<h1>🧠 Alliance Property Brain — Autonomous Teacher 3.7</h1><p>Routine labeling is automated. Human work is reduced to exceptions and periodic independent certification.</p><button onclick="runNow()">Run Autonomous Teacher Now</button><div id="cards" class="grid"></div><div class="card"><h3>Operating Policy</h3><p><b>Normal messages:</b> automatic classification → evidence/confidence gate → Silver shadow/auto-accept.</p><p><b>Uncertain/conflicting messages:</b> exception queue only.</p><p><b>Gold:</b> never auto-mutated. <b>Production/WhatsApp writes:</b> blocked.</p></div><div class="card"><h3>Latest Run</h3><pre id="latest"></pre></div><div class="card"><h3>Open Exceptions</h3><div id="exceptions"></div></div>
<script>async function call(p,m='GET'){let r=await fetch(p,{method:m});let t=await r.text(),d;try{d=JSON.parse(t)}catch(e){d={raw:t}}if(!r.ok)throw Error(d.detail||d.raw||('HTTP '+r.status));return d}function card(k,v){return `<div class='card'><div>${k}</div><div class='big'>${v??0}</div></div>`}function esc(s){return String(s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}async function load(){let s=await call('/api/property-brain/autonomous-v370/status'),l=s.latest_run||{},c=s.prediction_counts||{};document.getElementById('cards').innerHTML=card('Exam V2',((l.exam_v2||{}).labeled||0)+'/'+((l.exam_v2||{}).total||0))+card('Expertise Gate',l.expertise_gate||'NOT RUN')+card('Auto Silver',c.AUTO_ACCEPT_SILVER||0)+card('Open Exceptions',s.open_exceptions||0);document.getElementById('latest').textContent=JSON.stringify(l,null,2);let ex=await call('/api/property-brain/autonomous-v370/exceptions?limit=50');document.getElementById('exceptions').innerHTML=ex.length?ex.map(x=>`<div class='card warn'><b>${esc(x.source_table)} / ${esc(x.source_id)}</b><br>${esc(x.predicted_class)} | ${esc(x.predicted_transaction)} | ${esc(x.predicted_ownership)} | ${esc(x.confidence)}<pre>${esc(x.raw_text)}</pre></div>`).join(''):'<div class="card good">No open exceptions.</div>'}async function runNow(){await call('/api/property-brain/autonomous-v370/run?limit=500','POST');await load()}load();</script></body></html>'''


def _background_loop(engine):
    interval = max(300, int(os.getenv("ALLIANCE_AUTOTEACH_INTERVAL_SECONDS", str(DEFAULT_INTERVAL_SECONDS)) or DEFAULT_INTERVAL_SECONDS))
    while True:
        try:
            with engine.begin() as conn:
                locked = conn.execute(text("SELECT pg_try_advisory_xact_lock(370370)")).scalar()
                if locked: run(engine, 500)
        except Exception:
            pass
        time.sleep(interval)


def _start_background(engine):
    global _thread_started
    with _thread_lock:
        if _thread_started: return
        threading.Thread(target=_background_loop, args=(engine,), daemon=True, name="alliance-autoteacher-v370").start()
        _thread_started = True


def register(core):
    engine = _engine(core); app = _app(core); _install(engine)
    try: run(engine, 500)
    except Exception: pass
    if not foundation._route_exists(app, "/api/property-brain/autonomous-v370/status"):
        @app.get("/api/property-brain/autonomous-v370/status")
        def _status(): return status(engine)
    if not foundation._route_exists(app, "/api/property-brain/autonomous-v370/run"):
        @app.post("/api/property-brain/autonomous-v370/run")
        def _run(limit:int=Query(default=500,ge=1,le=MAX_BATCH)): return run(engine,limit)
    if not foundation._route_exists(app, "/api/property-brain/autonomous-v370/exceptions"):
        @app.get("/api/property-brain/autonomous-v370/exceptions")
        def _exceptions(limit:int=Query(default=100,ge=1,le=500)): return exceptions(engine,limit)
    if not foundation._route_exists(app, "/property-brain/autonomous-v370"):
        @app.get("/property-brain/autonomous-v370",response_class=HTMLResponse)
        def _dash(): return HTMLResponse(DASHBOARD)
    _start_background(engine)
    return {"status":"REGISTERED","version":VERSION,"dashboard":"/property-brain/autonomous-v370","automation_interval_seconds":max(300,int(os.getenv("ALLIANCE_AUTOTEACH_INTERVAL_SECONDS",str(DEFAULT_INTERVAL_SECONDS)) or DEFAULT_INTERVAL_SECONDS)),"production_writes":0,"whatsapp_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0}
