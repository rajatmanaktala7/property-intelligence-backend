from __future__ import annotations

import json
import re
import uuid
from collections import Counter, defaultdict

from fastapi import Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_gold_v2_structural_lab_v293 as goldlab

VERSION = "2.9.4-AUTONOMOUS-GOLD-TEACHER"
MODE = "SEED_GOLD_DETERMINISTIC_AUTOTEACHER_EXCEPTION_ONLY_NO_PRODUCTION_WRITES"
ENGINE_VERSION = "ALLIANCE_AUTONOMOUS_GOLD_TEACHER_V1"
RULESET_VERSION = "AUTOTEACH_RULESET_2026_09_03_V1"

AUTO_ACCEPT = 98.0
SHADOW_ACCEPT = 90.0

DDL = [
"""CREATE TABLE IF NOT EXISTS alliance_auto_teacher_predictions_v294(
prediction_id UUID PRIMARY KEY,
case_id UUID NOT NULL,
entity_id TEXT NOT NULL,
task_type TEXT NOT NULL,
predicted_decision TEXT NOT NULL,
confidence NUMERIC(6,2) NOT NULL,
disposition TEXT NOT NULL,
canonical_value TEXT,
rule_id TEXT NOT NULL,
reason TEXT NOT NULL,
evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
ruleset_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(case_id,ruleset_version))""",
"""CREATE TABLE IF NOT EXISTS alliance_auto_teacher_exceptions_v294(
exception_id UUID PRIMARY KEY,
case_id UUID NOT NULL,
entity_id TEXT NOT NULL,
task_type TEXT NOT NULL,
reason_code TEXT NOT NULL,
teacher_confidence NUMERIC(6,2) NOT NULL,
teacher_prediction TEXT,
review_status TEXT NOT NULL DEFAULT 'OPEN',
payload JSONB NOT NULL DEFAULT '{}'::jsonb,
ruleset_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(case_id,ruleset_version))""",
"""CREATE TABLE IF NOT EXISTS alliance_auto_teacher_rule_metrics_v294(
rule_id TEXT NOT NULL,
ruleset_version TEXT NOT NULL,
human_examples INTEGER NOT NULL DEFAULT 0,
human_agreements INTEGER NOT NULL DEFAULT 0,
agreement_pct NUMERIC(7,3),
notes TEXT,
updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
PRIMARY KEY(rule_id,ruleset_version))""",
"""CREATE TABLE IF NOT EXISTS alliance_auto_teacher_runs_v294(
run_id UUID PRIMARY KEY,
ruleset_version TEXT NOT NULL,
cases_seen INTEGER NOT NULL DEFAULT 0,
auto_accept INTEGER NOT NULL DEFAULT 0,
shadow_accept INTEGER NOT NULL DEFAULT 0,
exceptions INTEGER NOT NULL DEFAULT 0,
human_seed_labels INTEGER NOT NULL DEFAULT 0,
gold_v1_mutations INTEGER NOT NULL DEFAULT 0,
production_writes INTEGER NOT NULL DEFAULT 0,
whatsapp_writes INTEGER NOT NULL DEFAULT 0,
result JSONB NOT NULL DEFAULT '{}'::jsonb,
created_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
]

def _engine(core):
    return foundation._engine_from_core(core)

def _app(core):
    return getattr(core, "app", None) or core

def _loads(v, default):
    if v is None:
        return default
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return default

def _json(v):
    return json.dumps(foundation._json_safe(v), ensure_ascii=False)

def _install(engine):
    with engine.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))

def _norm(s):
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def _lines(s):
    return [x.strip() for x in (s or "").splitlines() if x.strip()]

def _has_phone(s):
    return bool(re.search(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)", s or ""))

def _transaction(s):
    n = _norm(s)
    if re.search(r"\b(?:rent|rental|lease|leasing|to let)\b", n):
        return "RENT"
    if re.search(r"\b(?:sale|sell|selling|resale|for sale|buyer|buyers)\b", n):
        return "SALE"
    if re.search(r"\b(?:owner|owners)\s+(?:want|wants|asking)\b", n) and re.search(r"\b(?:cr|crore|lac|lakh)\b", n):
        return "SALE"
    return None

def _explicit_locations(raw):
    out = []
    for line in _lines(raw):
        n = _norm(line)
        if re.fullmatch(r"(?:dlf\s*)?phase\s*[1-9][0-9]*", n):
            out.append(line)
        elif re.fullmatch(r"(?:shushant|sushant)\s*lok\s*[1-9][0-9]*", n):
            out.append(line)
        elif re.fullmatch(r"g\s*k\s*[12]", n) or re.fullmatch(r"greater kailash\s*[12i]+", n):
            out.append(line)
        elif re.search(r"\b(?:location|sector|village)\s*[:\-]?\s*[a-z]", n):
            out.append(line)
    return out

def _starts_truncated(raw):
    lines = _lines(raw)
    if not lines:
        return True
    first = lines[0].upper()
    patterns = (
        r"^SYDS?\b",
        r"^/\s*\d",
        r"^\d{1,3}(?:\.\d+)?\s*LAC\b",
        r"^\d{1,3}(?:\.\d+)?\s*K\b",
    )
    return any(re.search(p, first) for p in patterns)

def _looks_forward_header(parent, raw):
    rlines = _lines(raw)
    plines = _lines(parent)
    if len(rlines) < 2 or not plines:
        return False, None
    last = rlines[-1]
    ln = _norm(last)
    if not (
        re.fullmatch(r"(?:dlf\s*)?phase\s*[1-9][0-9]*", ln)
        or re.fullmatch(r"(?:shushant|sushant)\s*lok\s*[1-9][0-9]*", ln)
        or re.fullmatch(r"g\s*k\s*[12]", ln)
    ):
        return False, None
    for i, p in enumerate(plines[:-1]):
        if _norm(p) == ln:
            nxt = _norm(plines[i + 1])
            if re.search(r"\b(?:syds?|sq\s*yds?|bhk|plot|floor|kothi|apartment|shop|office)\b", nxt):
                return True, last
    return False, None

def _inventory_summary_fragment(parent, raw):
    return (
        "new floors in resale" in _norm(parent)
        and (raw or "").count("/") >= 2
        and bool(re.search(r"\b(?:syds?|sq\s*yds?)\b", _norm(raw)))
    )

def _source_traceability(case):
    raw = case.get("raw_text") or ""
    candidate = case.get("candidate_value") or ""
    nr, nc = _norm(raw), _norm(candidate)
    if nc and nc in nr:
        return "SOURCE_SUPPORTED", 99.5, candidate, "TRACE_EXACT_NORMALIZED", "Candidate is directly present in normalized atomic source text."
    aliases = {
        "greater kailash 1": [r"\bg\s*k\s*1\b", r"\bgk\s*1\b", r"\bgreater kailash\s*(?:1|i)\b"],
        "greater kailash 2": [r"\bg\s*k\s*2\b", r"\bgk\s*2\b", r"\bgreater kailash\s*(?:2|ii)\b"],
    }
    if nc in aliases and any(re.search(p, nr) for p in aliases[nc]):
        return "SOURCE_SUPPORTED", 99.5, candidate, "TRACE_ALIAS_GK", "Canonical locality is supported by an explicit punctuation/styling variant in atomic evidence."
    return "AMBIGUOUS", 88.0, None, "TRACE_UNRESOLVED", "Source traceability is not deterministically provable."

def _structural_conflict(case):
    parent = case.get("parent_message_text") or ""
    mp = case.get("machine_payload") or {}
    audit = (mp.get("structural_audit") or {}) if isinstance(mp, dict) else {}
    vals = audit.get("header_values") or []
    if len(set(map(str, vals))) >= 2:
        return "TRUE_CONFLICT", 99.0, None, "CONFLICT_MULTI_HEADER", "Multiple competing structural header values exist in the same parent scope."
    n = _norm(parent)
    if "for sale" in n and "for rent" in n:
        return "TRUE_CONFLICT", 98.5, None, "CONFLICT_SALE_RENT_SECTIONS", "Parent contains distinct sale and rent sections; transaction must bind to the nearest section after splitting."
    city_hits = set(re.findall(r"\b(?:gurgaon|gurugram|noida|new delhi|delhi|mohali|goa)\b", n))
    if len(city_hits) >= 2:
        return "TRUE_CONFLICT", 98.0, None, "CONFLICT_MULTI_CITY", "Parent contains multiple city contexts; geography cannot be globally inherited."
    return "AMBIGUOUS", 88.0, None, "CONFLICT_NOT_PROVEN", "Deterministic structural conflict is not proven."

def _content_class(case):
    raw = case.get("raw_text") or ""
    parent = case.get("parent_message_text") or ""
    n, np = _norm(raw), _norm(parent)
    if len(n) < 8 and not _has_phone(raw):
        return "FRAGMENT", 99.0, None, "CONTENT_FRAGMENT_SHORT", "Atomic text is an incomplete fragment."
    if re.search(r"\b(?:required|requirement|looking for|need|wanted|seeking)\b", n):
        return "REQUIREMENT", 99.0, None, "CONTENT_REQUIREMENT", "Atomic evidence explicitly expresses demand/requirement intent."
    if _inventory_summary_fragment(parent, raw):
        return "INVENTORY_GROUP", 99.0, None, "CONTENT_INVENTORY_SUMMARY", "Slash-separated size summary belongs to an inventory group."
    if _transaction(raw) and re.search(r"\b(?:bhk|plot|floor|shop|office|land|apartment|villa|syds?|sqft|sq ft)\b", n):
        return "PROPERTY_AVAILABILITY", 98.5, None, "CONTENT_PROPERTY", "Atomic evidence contains property attributes plus explicit transaction intent."
    if not re.search(r"\b(?:property|rent|sale|plot|floor|bhk|shop|office|land|villa|syds?|sqft|sq ft)\b", np):
        return "NOISE", 98.0, None, "CONTENT_NOISE", "Parent context contains no meaningful property signal."
    return "AMBIGUOUS", 85.0, None, "CONTENT_UNRESOLVED", "Content class needs exception review."

def _ownership(case):
    raw = case.get("raw_text") or ""
    parent = case.get("parent_message_text") or ""
    mp = case.get("machine_payload") or {}
    n = _norm(raw)
    lines = _lines(raw)

    if _inventory_summary_fragment(parent, raw):
        return "NOT_OWNED", 99.5, None, "OWN_SUMMARY_NOT_ATOMIC", "Atomic span is an inventory-size summary, not one uniquely identifiable property."

    if _has_phone(raw) and not re.search(r"\b(?:bhk|plot|floor|shop|office|land|villa|syds?|sqft|sq ft|rent|sale)\b", n):
        return "NOT_OWNED", 99.0, None, "OWN_SHARED_FOOTER", "Span is a contact/footer fragment; contact may be shared but atomic property ownership is not established."

    forward, header = _looks_forward_header(parent, raw)
    if forward:
        return "NOT_OWNED", 99.5, None, "OWN_FORWARD_HEADER", f"Trailing '{header}' is the next record's forward-binding header."

    if _starts_truncated(raw):
        locs = _explicit_locations(raw)
        if len(lines) == 1 or (len(lines) <= 2 and not locs):
            return "NOT_OWNED", 99.0, None, "OWN_TRUNCATED_FRAGMENT", "Atomic child is truncated and lacks enough identity fields to reconstruct one unique property safely."
        if locs:
            anchors = bool(re.search(r"\b\d+\s*(?:syds?|sq\s*yds?|sqft|sq ft|sq m|sqm)\b", n)) or bool(re.search(r"\b\d+\s*bhk\b", n))
            if not anchors:
                return "AMBIGUOUS", 89.0, None, "OWN_TRUNCATED_WITH_LOCATION", "Location text is literal but exact property boundary remains uncertain."

    tx = _transaction(raw)
    identity = (
        bool(re.search(r"\b\d+\s*bhk\b", n))
        or bool(re.search(r"\b\d+(?:\.\d+)?\s*(?:syds?|sq\s*yds?|sqft|sq ft|sqm|sq m|acre|acres)\b", n))
        or bool(re.search(r"\b(?:plot|apartment|builder floor|independent house|shop|office|land)\b", n))
    )
    if tx and identity:
        return "OWNED", 99.0, tx, "OWN_ATOMIC_TX_IDENTITY", "Atomic evidence contains explicit transaction intent and sufficient property identity."

    if tx == "SALE" and re.search(r"\b(?:cr|crore|lac|lakh)\b", n):
        return "OWNED", 98.5, "SALE", "OWN_SEMANTIC_SALE", "Atomic evidence explicitly conveys an asking-price sale intent."

    locs = _explicit_locations(raw)
    if locs and identity:
        return "OWNED", 98.0, _norm(locs[-1]).title(), "OWN_LITERAL_LOCATION_IDENTITY", "Location is explicitly present in atomic evidence with property identity."

    if re.fullmatch(r"(?:rental|rent|sale|resale)\s+inventory", n):
        return "OWNED", 99.0, _transaction(raw), "OWN_TRANSACTION_HEADER", "Atomic span is an explicit transaction-scoped inventory header."

    sibling = (mp.get("sibling_context") or {}) if isinstance(mp, dict) else {}
    if sibling.get("multi_property_message") and len(n) < 80:
        return "NOT_OWNED", 98.5, None, "OWN_MULTI_PARENT_WEAK_CHILD", "Weak atomic fragment sits inside a multi-property parent; sibling-specific context must not be inherited."

    return "AMBIGUOUS", 87.0, None, "OWN_UNRESOLVED", "Deterministic ownership is not strong enough; route to exception review."

def adjudicate(case):
    task = case.get("task_type")
    if task == "OWNERSHIP":
        d, c, cv, rule, reason = _ownership(case)
    elif task == "SOURCE_TRACEABILITY":
        d, c, cv, rule, reason = _source_traceability(case)
    elif task == "STRUCTURAL_CONFLICT":
        d, c, cv, rule, reason = _structural_conflict(case)
    elif task == "CONTENT_CLASS":
        d, c, cv, rule, reason = _content_class(case)
    else:
        d, c, cv, rule, reason = "AMBIGUOUS", 70.0, None, "UNKNOWN_TASK", "Unknown task type."
    disposition = "AUTO_ACCEPT" if c >= AUTO_ACCEPT else ("SHADOW_ACCEPT" if c >= SHADOW_ACCEPT else "EXCEPTION")
    return {"decision": d, "confidence": round(float(c), 2), "canonical_value": cv, "rule_id": rule, "reason": reason, "disposition": disposition}

def _all_cases(engine):
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(text("""
            SELECT c.*,
                   l.human_decision,l.human_confidence,l.canonical_value AS human_canonical,
                   l.reason AS human_reason
            FROM alliance_gold_v2_structural_cases c
            LEFT JOIN alliance_gold_v2_structural_labels l ON l.case_id=c.case_id
            WHERE c.source_version=:v
            ORDER BY c.priority_score DESC,c.created_at ASC
        """), {"v": goldlab.CURRICULUM_VERSION}).mappings().all()]
    for r in rows:
        r["machine_payload"] = _loads(r.get("machine_payload"), {})
    return rows

def _calibrate(engine, cases):
    stats = defaultdict(lambda: [0, 0])
    for c in cases:
        if not c.get("human_decision"):
            continue
        p = adjudicate(c)
        stats[p["rule_id"]][0] += 1
        if p["decision"] == c["human_decision"]:
            stats[p["rule_id"]][1] += 1
    with engine.begin() as conn:
        for rule, (n, ok) in stats.items():
            pct = round(100.0 * ok / max(n, 1), 3)
            conn.execute(text("""
                INSERT INTO alliance_auto_teacher_rule_metrics_v294
                (rule_id,ruleset_version,human_examples,human_agreements,agreement_pct,notes)
                VALUES(:r,:v,:n,:ok,:pct,'Measured only against immutable Human Gold V2 seed labels')
                ON CONFLICT(rule_id,ruleset_version) DO UPDATE SET
                  human_examples=EXCLUDED.human_examples,
                  human_agreements=EXCLUDED.human_agreements,
                  agreement_pct=EXCLUDED.agreement_pct,
                  notes=EXCLUDED.notes,
                  updated_at=now()
            """), {"r": rule, "v": RULESET_VERSION, "n": n, "ok": ok, "pct": pct})
    return {k: {"examples": v[0], "agreements": v[1], "agreement_pct": round(100*v[1]/max(v[0],1),2)} for k,v in stats.items()}

def run_teacher(engine, only_open=True, limit=500):
    _install(engine)
    goldlab.seed(engine, goldlab.TARGET)
    cases = _all_cases(engine)
    calibration = _calibrate(engine, cases)
    candidates = [c for c in cases if not c.get("human_decision")]
    if only_open:
        candidates = [c for c in candidates if c.get("status") == "OPEN"]
    candidates = candidates[:max(1, min(int(limit), 5000))]

    counts, rule_counts = Counter(), Counter()
    with engine.begin() as conn:
        for c in candidates:
            p = adjudicate(c)
            counts[p["disposition"]] += 1
            rule_counts[p["rule_id"]] += 1
            ev = {"raw_text": c.get("raw_text"), "parent_message_text": c.get("parent_message_text"), "machine_payload": c.get("machine_payload"), "source_status": c.get("status")}
            conn.execute(text("""
                INSERT INTO alliance_auto_teacher_predictions_v294
                (prediction_id,case_id,entity_id,task_type,predicted_decision,confidence,
                 disposition,canonical_value,rule_id,reason,evidence,ruleset_version)
                VALUES(:pid,:cid,:eid,:task,:decision,:conf,:disp,:cv,:rule,:reason,CAST(:ev AS jsonb),:v)
                ON CONFLICT(case_id,ruleset_version) DO UPDATE SET
                  predicted_decision=EXCLUDED.predicted_decision,
                  confidence=EXCLUDED.confidence,
                  disposition=EXCLUDED.disposition,
                  canonical_value=EXCLUDED.canonical_value,
                  rule_id=EXCLUDED.rule_id,
                  reason=EXCLUDED.reason,
                  evidence=EXCLUDED.evidence,
                  updated_at=now()
            """), {"pid": str(uuid.uuid4()), "cid": str(c["case_id"]), "eid": c["entity_id"], "task": c["task_type"], "decision": p["decision"], "conf": p["confidence"], "disp": p["disposition"], "cv": p["canonical_value"], "rule": p["rule_id"], "reason": p["reason"], "ev": _json(ev), "v": RULESET_VERSION})

            if p["disposition"] == "EXCEPTION":
                conn.execute(text("""
                    INSERT INTO alliance_auto_teacher_exceptions_v294
                    (exception_id,case_id,entity_id,task_type,reason_code,teacher_confidence,
                     teacher_prediction,payload,ruleset_version,review_status)
                    VALUES(:id,:cid,:eid,:task,:reason,:conf,:pred,CAST(:payload AS jsonb),:v,'OPEN')
                    ON CONFLICT(case_id,ruleset_version) DO UPDATE SET
                      reason_code=EXCLUDED.reason_code,
                      teacher_confidence=EXCLUDED.teacher_confidence,
                      teacher_prediction=EXCLUDED.teacher_prediction,
                      payload=EXCLUDED.payload,
                      updated_at=now()
                """), {"id": str(uuid.uuid4()), "cid": str(c["case_id"]), "eid": c["entity_id"], "task": c["task_type"], "reason": p["rule_id"], "conf": p["confidence"], "pred": p["decision"], "payload": _json({"prediction": p, "case": foundation._json_safe(c)}), "v": RULESET_VERSION})

        human_seed = sum(1 for c in cases if c.get("human_decision"))
        result = {"status":"PASS","version":VERSION,"ruleset_version":RULESET_VERSION,"human_seed_labels":human_seed,"unlabeled_cases_seen":len(candidates),"auto_accept":counts["AUTO_ACCEPT"],"shadow_accept":counts["SHADOW_ACCEPT"],"exceptions":counts["EXCEPTION"],"rules_used":dict(rule_counts),"calibration":calibration,"policy":"Human Gold remains immutable. Auto predictions are stored separately. Only exceptions require human review.","gold_v1_mutations":0,"production_writes":0,"whatsapp_writes":0}
        conn.execute(text("""
            INSERT INTO alliance_auto_teacher_runs_v294
            (run_id,ruleset_version,cases_seen,auto_accept,shadow_accept,exceptions,
             human_seed_labels,gold_v1_mutations,production_writes,whatsapp_writes,result)
            VALUES(:id,:v,:seen,:aa,:sa,:ex,:human,0,0,0,CAST(:result AS jsonb))
        """), {"id":str(uuid.uuid4()),"v":RULESET_VERSION,"seen":len(candidates),"aa":counts["AUTO_ACCEPT"],"sa":counts["SHADOW_ACCEPT"],"ex":counts["EXCEPTION"],"human":human_seed,"result":_json(result)})
    return result

def status(engine):
    _install(engine)
    with engine.connect() as conn:
        human = conn.execute(text("SELECT count(*) FROM alliance_gold_v2_structural_labels")).scalar() or 0
        pred = conn.execute(text("""
            SELECT disposition,count(*) n FROM alliance_auto_teacher_predictions_v294
            WHERE ruleset_version=:v GROUP BY disposition
        """), {"v":RULESET_VERSION}).mappings().all()
        exc = conn.execute(text("""
            SELECT count(*) FROM alliance_auto_teacher_exceptions_v294
            WHERE ruleset_version=:v AND review_status='OPEN'
        """), {"v":RULESET_VERSION}).scalar() or 0
        rules = [dict(r) for r in conn.execute(text("""
            SELECT rule_id,human_examples,human_agreements,agreement_pct,notes
            FROM alliance_auto_teacher_rule_metrics_v294 WHERE ruleset_version=:v
            ORDER BY human_examples DESC,rule_id
        """), {"v":RULESET_VERSION}).mappings().all()]
        latest = conn.execute(text("""
            SELECT result,created_at FROM alliance_auto_teacher_runs_v294
            WHERE ruleset_version=:v ORDER BY created_at DESC LIMIT 1
        """), {"v":RULESET_VERSION}).mappings().first()
    return foundation._json_safe({"status":"PASS","version":VERSION,"mode":MODE,"engine_version":ENGINE_VERSION,"ruleset_version":RULESET_VERSION,"thresholds":{"auto_accept":AUTO_ACCEPT,"shadow_accept":SHADOW_ACCEPT},"human_seed_labels":int(human),"prediction_distribution":{r["disposition"]:int(r["n"]) for r in pred},"open_exceptions":int(exc),"rule_calibration":rules,"latest_run":_loads(latest["result"],{}) if latest else None,"human_gold_policy":"IMMUTABLE","production_writes":0,"whatsapp_live_writes":0,"gold_v1_mutations":0})

def exceptions(engine, limit=100):
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(text("""
            SELECT exception_id,case_id,entity_id,task_type,reason_code,teacher_confidence,
                   teacher_prediction,review_status,payload,updated_at
            FROM alliance_auto_teacher_exceptions_v294
            WHERE ruleset_version=:v AND review_status='OPEN'
            ORDER BY teacher_confidence ASC,updated_at DESC LIMIT :limit
        """), {"v":RULESET_VERSION,"limit":max(1,min(int(limit),500))}).mappings().all()]
    for r in rows:
        r["payload"] = _loads(r.get("payload"), {})
    return foundation._json_safe({"status":"PASS","count":len(rows),"exceptions":rows})

DASHBOARD = """<!doctype html><html><head><meta charset='utf-8'><title>Alliance Auto Teacher</title>
<style>body{font-family:Arial;background:#07111d;color:#eef6ff;max-width:1400px;margin:24px auto}.card{background:#101d2b;padding:16px;border-radius:12px;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.big{font-size:28px;font-weight:700}button{padding:12px 18px;border:0;border-radius:8px;background:#f5d76e;font-weight:700}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style></head><body>
<h1>🤖 Alliance Property Brain — Autonomous Gold Teacher 2.9.4</h1>
<p>Human Gold is immutable. The teacher auto-adjudicates deterministic cases and sends only true exceptions for review.</p>
<button onclick='run()'>Run Auto Teacher Now</button><div id='cards' class='grid'></div>
<div class='card'><h3>Latest Run</h3><pre id='latest'></pre></div><div class='card'><h3>Human Exceptions Only</h3><pre id='exceptions'></pre></div>
<script>
async function call(p,m='GET'){let r=await fetch(p,{method:m});let t=await r.text();let d;try{d=JSON.parse(t)}catch(e){d={raw:t}};if(!r.ok)throw Error(d.detail||d.raw||('HTTP '+r.status));return d}
function c(label,v){return `<div class="card"><div>${label}</div><div class="big">${v??0}</div></div>`}
async function load(){let s=await call('/api/property-brain/auto-teacher-v294/status');let p=s.prediction_distribution||{};document.getElementById('cards').innerHTML=c('Human Seed Gold',s.human_seed_labels)+c('Auto Accept',p.AUTO_ACCEPT)+c('Shadow Accept',p.SHADOW_ACCEPT)+c('Open Exceptions',s.open_exceptions);document.getElementById('latest').textContent=JSON.stringify(s.latest_run,null,2);let e=await call('/api/property-brain/auto-teacher-v294/exceptions?limit=50');document.getElementById('exceptions').textContent=JSON.stringify(e.exceptions,null,2)}
async function run(){document.getElementById('latest').textContent='Running...';await call('/api/property-brain/auto-teacher-v294/run?limit=500','POST');await load()}load()
</script></body></html>"""

def register(core):
    engine = _engine(core)
    app = _app(core)
    _install(engine)
    try:
        run_teacher(engine, only_open=True, limit=500)
    except Exception:
        pass

    if not foundation._route_exists(app, "/api/property-brain/auto-teacher-v294/status"):
        @app.get("/api/property-brain/auto-teacher-v294/status")
        def _status():
            return status(engine)

    if not foundation._route_exists(app, "/api/property-brain/auto-teacher-v294/run"):
        @app.post("/api/property-brain/auto-teacher-v294/run")
        def _run(limit:int=Query(default=500,ge=1,le=5000)):
            return run_teacher(engine, only_open=True, limit=limit)

    if not foundation._route_exists(app, "/api/property-brain/auto-teacher-v294/exceptions"):
        @app.get("/api/property-brain/auto-teacher-v294/exceptions")
        def _exceptions(limit:int=Query(default=100,ge=1,le=500)):
            return exceptions(engine, limit)

    if not foundation._route_exists(app, "/property-brain/auto-teacher-v294"):
        @app.get("/property-brain/auto-teacher-v294", response_class=HTMLResponse)
        def _dashboard():
            return HTMLResponse(DASHBOARD)

    return {"status":"REGISTERED","version":VERSION,"dashboard":"/property-brain/auto-teacher-v294","auto_run_on_start":True,"human_gold_mutations":0,"production_writes":0,"whatsapp_live_writes":0}
