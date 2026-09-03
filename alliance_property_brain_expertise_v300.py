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
import alliance_autonomous_gold_teacher_v294 as teacher

VERSION = "3.0.0-EXPERTISE-LOOP"
MODE = "AUTONOMOUS_EXCEPTION_RESOLUTION_RULE_TRUST_REGRESSION_PROMOTION_GATE"
ENGINE_VERSION = "ALLIANCE_PROPERTY_BRAIN_EXPERTISE_LOOP_V1"
RULESET_VERSION = "EXPERTISE_RULESET_2026_09_03_V1"

# Expertise is a measured state, never a self-declared label.
MIN_HUMAN_FOR_RULE_TRUST = 2
RULE_TRUST_ACCURACY = 90.0
EXPERTISE_OVERALL_ACCURACY = 95.0
EXPERTISE_CRITICAL_TASK_ACCURACY = 90.0

DDL = [
"""CREATE TABLE IF NOT EXISTS alliance_expert_predictions_v300(
prediction_id UUID PRIMARY KEY,
case_id UUID NOT NULL,
entity_id TEXT NOT NULL,
task_type TEXT NOT NULL,
decision TEXT NOT NULL,
confidence NUMERIC(6,2) NOT NULL,
canonical_value TEXT,
rule_id TEXT NOT NULL,
rule_trusted BOOLEAN NOT NULL DEFAULT FALSE,
disposition TEXT NOT NULL,
reason TEXT NOT NULL,
evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
ruleset_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(case_id,ruleset_version))""",

"""CREATE TABLE IF NOT EXISTS alliance_expert_lessons_v300(
lesson_id UUID PRIMARY KEY,
lesson_code TEXT NOT NULL,
lesson_text TEXT NOT NULL,
lesson_scope TEXT NOT NULL,
source TEXT NOT NULL,
ruleset_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(lesson_code,ruleset_version))""",

"""CREATE TABLE IF NOT EXISTS alliance_expert_rule_calibration_v300(
rule_id TEXT NOT NULL,
ruleset_version TEXT NOT NULL,
human_examples INTEGER NOT NULL DEFAULT 0,
human_agreements INTEGER NOT NULL DEFAULT 0,
agreement_pct NUMERIC(7,3),
trusted BOOLEAN NOT NULL DEFAULT FALSE,
hard_invariant BOOLEAN NOT NULL DEFAULT FALSE,
notes TEXT,
updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
PRIMARY KEY(rule_id,ruleset_version))""",

"""CREATE TABLE IF NOT EXISTS alliance_expert_benchmark_v300(
metric_key TEXT PRIMARY KEY,
metric_value NUMERIC(10,4),
numerator INTEGER NOT NULL DEFAULT 0,
denominator INTEGER NOT NULL DEFAULT 0,
passed BOOLEAN NOT NULL DEFAULT FALSE,
notes TEXT,
ruleset_version TEXT NOT NULL,
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_expert_runs_v300(
run_id UUID PRIMARY KEY,
ruleset_version TEXT NOT NULL,
human_gold INTEGER NOT NULL DEFAULT 0,
cases_seen INTEGER NOT NULL DEFAULT 0,
resolved INTEGER NOT NULL DEFAULT 0,
shadow INTEGER NOT NULL DEFAULT 0,
exceptions INTEGER NOT NULL DEFAULT 0,
expertise_gate TEXT NOT NULL,
result JSONB NOT NULL DEFAULT '{}'::jsonb,
production_writes INTEGER NOT NULL DEFAULT 0,
whatsapp_writes INTEGER NOT NULL DEFAULT 0,
gold_v1_mutations INTEGER NOT NULL DEFAULT 0,
gold_v2_mutations INTEGER NOT NULL DEFAULT 0,
created_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
]

LESSONS = [
("LOCATION_REFERENCE_NOT_SUBJECT", "A city mentioned in connectivity, distance, airport, railway, highway, beach or route text is a reference location unless the property itself is stated to be there.", "GEOGRAPHY"),
("HIGHWAY_ENDPOINT_NOT_PROPERTY_CITY", "A highway name such as Goa-Mumbai Highway does not make Mumbai the property's city.", "GEOGRAPHY"),
("ATOMIC_EXPLICIT_LOCATION_WINS", "Explicit atomic locality/city attached to the property wins over remote reference locations.", "GEOGRAPHY"),
("NUMBERED_INVENTORY_NEEDS_SPLIT", "A numbered multi-property parent must be split before city, area, price or property attributes are inherited.", "BOUNDARY"),
("FORWARD_HEADER_BINDS_FORWARD", "A locality/project header at the start of the next property binds forward, not backward.", "BOUNDARY"),
("WEAK_FRAGMENT_NO_SIBLING_THEFT", "A weak or truncated atomic fragment must not inherit sibling-specific property identity.", "OWNERSHIP"),
("SHARED_FOOTER_IS_SHARED", "Broker/contact footer may be shared across children but does not make the footer a property.", "OWNERSHIP"),
("RATE_NOT_TOTAL", "Per sq yd/per acre/per sq m values are rates, not total consideration.", "MONEY"),
("LAC_NOT_AC", "The token LAC must never create the amenity AC.", "TOKEN_BOUNDARY"),
("SPACIOUS_NOT_SPA", "Spacious must never create SPA.", "TOKEN_BOUNDARY"),
("LANDSCAPED_NOT_LAND", "Landscaped must never create LAND property type.", "TOKEN_BOUNDARY"),
("PROPERTY_TYPE_NOT_SUITABLE_USE", "Proposed villa/farmhouse use must not become the current property type.", "ONTOLOGY"),
("SOURCE_TRUTH_BEFORE_ENRICHMENT", "Canonical normalization may map explicit aliases, but external knowledge must not be inserted as source truth.", "PROVENANCE"),
]

HARD_INVARIANT_RULES = {
    "EXPERT_SINGLE_PROPERTY_REFERENCE_CITY",
    "EXPERT_NUMBERED_MULTI_PROPERTY_PARENT",
    "EXPERT_ATOMIC_LOCATION_COHERENT",
    "EXPERT_SHORT_NONPROPERTY_FRAGMENT",
    "TRACE_ALIAS_GK",
    "OWN_SUMMARY_NOT_ATOMIC",
    "OWN_ATOMIC_TX_IDENTITY",
    "OWN_TRANSACTION_HEADER",
}

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

def _j(v):
    return json.dumps(foundation._json_safe(v), ensure_ascii=False)

def _norm(s):
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def _lines(s):
    return [x.strip() for x in (s or "").splitlines() if x.strip()]

def _install(engine):
    with engine.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))
        for code, lesson, scope in LESSONS:
            conn.execute(text("""
                INSERT INTO alliance_expert_lessons_v300
                (lesson_id,lesson_code,lesson_text,lesson_scope,source,ruleset_version)
                VALUES(:id,:code,:lesson,:scope,'HUMAN_GOLD_PLUS_EXCEPTION_DIAGNOSIS',:v)
                ON CONFLICT(lesson_code,ruleset_version) DO NOTHING
            """), {"id":str(uuid.uuid4()),"code":code,"lesson":lesson,"scope":scope,"v":RULESET_VERSION})

def _property_signal(s):
    n = _norm(s)
    return bool(re.search(r"\b(?:bhk|plot|apartment|builder floor|floor|shop|office|land|villa|kothi|syds?|gaj|sqft|sq ft|sq m|sqm|acre|bigha|price|rent|sale)\b", n))

def _subject_location_lines(raw):
    """Return location statements that describe the property, excluding connectivity/reference text."""
    out = []
    for line in _lines(raw):
        n = _norm(line)
        reference = bool(re.search(
            r"\b(?:km|kms|away from|distance|airport|railway|station|highway|nh ?\d+|beach|market|connectivity|road to|route to)\b",
            n
        ))
        if reference:
            continue
        if re.search(r"\b(?:located in|location|village|sector|phase|mapusa|goa|gurgaon|gurugram|noida|delhi|jaipur|ajmer|mohali|parra|dona paula|aldeia de goa|karsawada)\b", n):
            out.append(line)
    return out

def _reference_location_lines(raw):
    out = []
    for line in _lines(raw):
        n = _norm(line)
        if re.search(r"\b(?:km|kms|away from|airport|railway|station|highway|nh ?\d+|beach|market|connectivity)\b", n):
            out.append(line)
    return out

def _numbered_property_count(parent):
    count = 0
    for line in _lines(parent):
        n = _norm(line)
        if re.match(r"^\d+\s+", n) and _property_signal(line):
            count += 1
    # Also count clear repeated sale/rent property anchors.
    count += min(5, len(re.findall(r"(?im)^\s*(?:plot|land|apartment|villa|shop|office).{0,30}\b(?:for sale|for rent|available)\b", parent or "")))
    return count

def _single_property_coherence(raw, parent):
    """Strong single-property document, despite reference-city names inside connectivity prose."""
    n = _norm(raw)
    subject = _subject_location_lines(raw)
    refs = _reference_location_lines(raw)
    has_price = bool(re.search(r"\b(?:price|demand|asking)\b", n) or re.search(r"(?:₹|rs\.?)\s*\d", raw or "", re.I))
    has_type = bool(re.search(r"\b(?:apartment|plot|villa|floor|shop|office|land|bhk)\b", n))
    has_area = bool(re.search(r"\b\d+(?:\.\d+)?\s*(?:sq\s*m|sqm|sqft|sq ft|gaj|syds?|acre|bigha)\b", n))
    # Parent approximately same property when raw is large and substantially overlaps.
    np = _norm(parent)
    overlap = len(n) >= 180 and (n[:120] in np or np[:120] in n)
    return bool(subject and has_type and (has_price or has_area) and overlap), subject, refs

def _expert_structural(case):
    raw = case.get("raw_text") or ""
    parent = case.get("parent_message_text") or ""
    n = _norm(raw)

    coherent, subjects, refs = _single_property_coherence(raw, parent)
    if coherent:
        return {
            "decision":"FALSE_CONFLICT","confidence":99.4,"canonical_value":None,
            "rule_id":"EXPERT_SINGLE_PROPERTY_REFERENCE_CITY",
            "reason":"Atomic evidence is one coherent property listing. Connectivity/highway/airport place names are reference locations, not competing property cities.",
            "evidence":{"subject_location_lines":subjects,"reference_location_lines":refs},
        }

    numbered = _numbered_property_count(parent)
    if numbered >= 2:
        return {
            "decision":"TRUE_CONFLICT","confidence":99.2,"canonical_value":None,
            "rule_id":"EXPERT_NUMBERED_MULTI_PROPERTY_PARENT",
            "reason":"Parent contains multiple numbered property items. Structural splitting is required before geography or property attributes can be inherited.",
            "evidence":{"numbered_property_anchors":numbered},
        }

    # A short decorative/marketing fragment inside one coherent parent is not evidence of a city conflict.
    if len(n) <= 30 and not _property_signal(raw):
        coherent_parent, subjects_p, refs_p = _single_property_coherence(parent, parent)
        if coherent_parent:
            return {
                "decision":"FALSE_CONFLICT","confidence":98.8,"canonical_value":None,
                "rule_id":"EXPERT_SHORT_NONPROPERTY_FRAGMENT",
                "reason":"Short decorative fragment belongs to a single coherent property message and does not create a structural city conflict.",
                "evidence":{"subject_location_lines":subjects_p,"reference_location_lines":refs_p},
            }

    # Explicit atomic property location + references in same listing.
    subjects = _subject_location_lines(raw)
    refs = _reference_location_lines(raw)
    if subjects and _property_signal(raw) and refs:
        return {
            "decision":"FALSE_CONFLICT","confidence":98.8,"canonical_value":None,
            "rule_id":"EXPERT_ATOMIC_LOCATION_COHERENT",
            "reason":"Property location is explicitly stated in atomic evidence; other places occur only as connectivity/reference locations.",
            "evidence":{"subject_location_lines":subjects,"reference_location_lines":refs},
        }

    return None

def _expert_source_trace(case):
    raw = case.get("raw_text") or ""
    cand = case.get("candidate_value") or ""
    nr, nc = _norm(raw), _norm(cand)
    if nc and nc in nr:
        return {"decision":"SOURCE_SUPPORTED","confidence":99.7,"canonical_value":cand,
                "rule_id":"EXPERT_LITERAL_TRACE","reason":"Candidate is literally supported by normalized atomic evidence.",
                "evidence":{"candidate":cand}}
    # Trust the v2.9.4 alias-aware adjudicator when it proves source support.
    base = teacher.adjudicate(case)
    if base.get("decision") == "SOURCE_SUPPORTED":
        return {"decision":"SOURCE_SUPPORTED","confidence":99.5,"canonical_value":base.get("canonical_value"),
                "rule_id":base.get("rule_id"),"reason":base.get("reason"),"evidence":{"teacher_v294":base}}
    return None

def _expert_ownership(case):
    # Start from prior teacher but remove unsafe confidence from poorly calibrated generic rules.
    base = teacher.adjudicate(case)
    rule = base.get("rule_id")
    if rule in ("OWN_SUMMARY_NOT_ATOMIC","OWN_ATOMIC_TX_IDENTITY","OWN_TRANSACTION_HEADER"):
        return {**base, "evidence":{"teacher_v294":base}}
    raw = case.get("raw_text") or ""
    n = _norm(raw)
    if len(n) < 10 and not _property_signal(raw):
        return {"decision":"NOT_OWNED","confidence":99.0,"canonical_value":None,
                "rule_id":"EXPERT_SHORT_NONPROPERTY_FRAGMENT",
                "reason":"Atomic child is a non-property fragment and cannot own a unique property identity.",
                "evidence":{"raw_text":raw}}
    return None

def expert_adjudicate(case):
    task = case.get("task_type")
    result = None
    if task == "STRUCTURAL_CONFLICT":
        result = _expert_structural(case)
    elif task == "SOURCE_TRACEABILITY":
        result = _expert_source_trace(case)
    elif task == "OWNERSHIP":
        result = _expert_ownership(case)

    if result is None:
        base = teacher.adjudicate(case)
        result = {
            "decision":base["decision"],
            "confidence":min(float(base["confidence"]), 89.0),
            "canonical_value":base.get("canonical_value"),
            "rule_id":base.get("rule_id","V294_FALLBACK"),
            "reason":"Fallback to 2.9.4 judgment, deliberately capped below promotion threshold until calibrated.",
            "evidence":{"teacher_v294":base},
        }
    return result

def _cases(engine):
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(text("""
            SELECT c.*,l.human_decision,l.human_confidence,l.canonical_value AS human_canonical
            FROM alliance_gold_v2_structural_cases c
            LEFT JOIN alliance_gold_v2_structural_labels l ON l.case_id=c.case_id
            WHERE c.source_version=:v
            ORDER BY c.priority_score DESC,c.created_at ASC
        """), {"v":goldlab.CURRICULUM_VERSION}).mappings().all()]
    for r in rows:
        r["machine_payload"] = _loads(r.get("machine_payload"), {})
    return rows

def _calibrate(engine, cases):
    stats = defaultdict(lambda:[0,0])
    for c in cases:
        if not c.get("human_decision"):
            continue
        p = expert_adjudicate(c)
        stats[p["rule_id"]][0] += 1
        if p["decision"] == c["human_decision"]:
            stats[p["rule_id"]][1] += 1

    calibration = {}
    with engine.begin() as conn:
        for rule,(n,ok) in stats.items():
            pct = round(100.0*ok/max(n,1),3)
            hard = rule in HARD_INVARIANT_RULES
            trusted = bool((n >= MIN_HUMAN_FOR_RULE_TRUST and pct >= RULE_TRUST_ACCURACY) or (hard and n >= 1 and pct >= RULE_TRUST_ACCURACY))
            calibration[rule] = {"examples":n,"agreements":ok,"agreement_pct":pct,"trusted":trusted,"hard_invariant":hard}
            conn.execute(text("""
                INSERT INTO alliance_expert_rule_calibration_v300
                (rule_id,ruleset_version,human_examples,human_agreements,agreement_pct,trusted,hard_invariant,notes)
                VALUES(:r,:v,:n,:ok,:pct,:trusted,:hard,'Human-Gold calibrated. Unproven rules cannot auto-promote.')
                ON CONFLICT(rule_id,ruleset_version) DO UPDATE SET
                  human_examples=EXCLUDED.human_examples,human_agreements=EXCLUDED.human_agreements,
                  agreement_pct=EXCLUDED.agreement_pct,trusted=EXCLUDED.trusted,
                  hard_invariant=EXCLUDED.hard_invariant,notes=EXCLUDED.notes,updated_at=now()
            """), {"r":rule,"v":RULESET_VERSION,"n":n,"ok":ok,"pct":pct,"trusted":trusted,"hard":hard})
    return calibration

def _benchmark(engine, cases):
    totals, correct = Counter(), Counter()
    overall_n = overall_ok = 0
    for c in cases:
        if not c.get("human_decision"):
            continue
        p = expert_adjudicate(c)
        task = c["task_type"]
        totals[task] += 1
        overall_n += 1
        if p["decision"] == c["human_decision"]:
            correct[task] += 1
            overall_ok += 1

    metrics = {}
    for task,n in totals.items():
        pct = round(100.0*correct[task]/max(n,1),4)
        metrics[task.lower()+"_accuracy"] = (pct, correct[task], n, pct >= EXPERTISE_CRITICAL_TASK_ACCURACY)
    overall_pct = round(100.0*overall_ok/max(overall_n,1),4)
    metrics["overall_human_gold_accuracy"] = (overall_pct,overall_ok,overall_n,overall_pct >= EXPERTISE_OVERALL_ACCURACY)

    with engine.begin() as conn:
        for key,(pct,num,den,passed) in metrics.items():
            conn.execute(text("""
                INSERT INTO alliance_expert_benchmark_v300
                (metric_key,metric_value,numerator,denominator,passed,notes,ruleset_version)
                VALUES(:k,:pct,:num,:den,:passed,'Measured against Human Gold V2 only',:v)
                ON CONFLICT(metric_key) DO UPDATE SET metric_value=EXCLUDED.metric_value,
                  numerator=EXCLUDED.numerator,denominator=EXCLUDED.denominator,passed=EXCLUDED.passed,
                  notes=EXCLUDED.notes,ruleset_version=EXCLUDED.ruleset_version,updated_at=now()
            """), {"k":key,"pct":pct,"num":num,"den":den,"passed":passed,"v":RULESET_VERSION})

    critical = [k for k in metrics if k.endswith("_accuracy") and k != "overall_human_gold_accuracy"]
    gate = overall_n >= 24 and metrics["overall_human_gold_accuracy"][3] and all(metrics[k][3] for k in critical)
    return {
        "human_gold_examples":overall_n,
        "overall_accuracy":overall_pct,
        "task_accuracy":{t:round(100.0*correct[t]/max(totals[t],1),2) for t in totals},
        "gate_passed":gate,
        "gate_rule":"At least 24 Human Gold structural cases, >=95% overall accuracy and >=90% on every populated critical task.",
    }

def run_expertise(engine, limit=1000):
    _install(engine)
    # Run the previous teacher first so its shadow tables remain current.
    teacher_run = teacher.run_teacher(engine, only_open=True, limit=min(int(limit),5000))
    cases = _cases(engine)
    calibration = _calibrate(engine, cases)
    benchmark = _benchmark(engine, cases)

    unlabeled = [c for c in cases if not c.get("human_decision") and c.get("status") == "OPEN"]
    unlabeled = unlabeled[:max(1,min(int(limit),5000))]
    counts = Counter()
    with engine.begin() as conn:
        for c in unlabeled:
            p = expert_adjudicate(c)
            cal = calibration.get(p["rule_id"], {})
            human_examples = int(cal.get("examples",0))
            agreement = float(cal.get("agreement_pct",0))
            hard = p["rule_id"] in HARD_INVARIANT_RULES

            # Promotion is based on rule evidence, not model self-confidence alone.
            trusted = bool(cal.get("trusted",False))
            if trusted and p["confidence"] >= 98:
                disposition = "EXPERT_RESOLVED"
            elif hard and p["confidence"] >= 98:
                disposition = "SHADOW_RESOLVED"
            else:
                disposition = "EXCEPTION"
            counts[disposition] += 1

            conn.execute(text("""
                INSERT INTO alliance_expert_predictions_v300
                (prediction_id,case_id,entity_id,task_type,decision,confidence,canonical_value,
                 rule_id,rule_trusted,disposition,reason,evidence,ruleset_version)
                VALUES(:id,:cid,:eid,:task,:d,:conf,:cv,:rule,:trusted,:disp,:reason,CAST(:ev AS jsonb),:v)
                ON CONFLICT(case_id,ruleset_version) DO UPDATE SET
                  decision=EXCLUDED.decision,confidence=EXCLUDED.confidence,canonical_value=EXCLUDED.canonical_value,
                  rule_id=EXCLUDED.rule_id,rule_trusted=EXCLUDED.rule_trusted,disposition=EXCLUDED.disposition,
                  reason=EXCLUDED.reason,evidence=EXCLUDED.evidence,updated_at=now()
            """), {"id":str(uuid.uuid4()),"cid":str(c["case_id"]),"eid":c["entity_id"],"task":c["task_type"],
                    "d":p["decision"],"conf":p["confidence"],"cv":p.get("canonical_value"),"rule":p["rule_id"],
                    "trusted":trusted,"disp":disposition,"reason":p["reason"],
                    "ev":_j({"expert_evidence":p.get("evidence",{}),"human_examples":human_examples,
                            "human_agreement_pct":agreement,"hard_invariant":hard}),"v":RULESET_VERSION})

        gate_state = "EXPERTISE_GATE_PASS" if benchmark["gate_passed"] else "EXPERTISE_GATE_HOLD"
        result = {
            "status":"PASS",
            "version":VERSION,
            "mode":MODE,
            "ruleset_version":RULESET_VERSION,
            "teacher_v294":teacher_run,
            "human_gold":sum(1 for c in cases if c.get("human_decision")),
            "unlabeled_cases_seen":len(unlabeled),
            "expert_resolved":counts["EXPERT_RESOLVED"],
            "shadow_resolved":counts["SHADOW_RESOLVED"],
            "exceptions":counts["EXCEPTION"],
            "benchmark":benchmark,
            "expertise_gate":gate_state,
            "policy":"No auto-labeled case mutates Human Gold. No prediction writes to production or WhatsApp.",
            "production_writes":0,"whatsapp_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0,
        }
        conn.execute(text("""
            INSERT INTO alliance_expert_runs_v300
            (run_id,ruleset_version,human_gold,cases_seen,resolved,shadow,exceptions,expertise_gate,
             result,production_writes,whatsapp_writes,gold_v1_mutations,gold_v2_mutations)
            VALUES(:id,:v,:human,:seen,:resolved,:shadow,:exceptions,:gate,CAST(:result AS jsonb),0,0,0,0)
        """), {"id":str(uuid.uuid4()),"v":RULESET_VERSION,"human":result["human_gold"],"seen":len(unlabeled),
                "resolved":counts["EXPERT_RESOLVED"],"shadow":counts["SHADOW_RESOLVED"],
                "exceptions":counts["EXCEPTION"],"gate":gate_state,"result":_j(result)})
    return result

def status(engine):
    _install(engine)
    with engine.connect() as conn:
        latest = conn.execute(text("""
            SELECT result FROM alliance_expert_runs_v300
            WHERE ruleset_version=:v ORDER BY created_at DESC LIMIT 1
        """), {"v":RULESET_VERSION}).scalar()
        dist = [dict(r) for r in conn.execute(text("""
            SELECT disposition,count(*) n FROM alliance_expert_predictions_v300
            WHERE ruleset_version=:v GROUP BY disposition ORDER BY disposition
        """), {"v":RULESET_VERSION}).mappings().all()]
        lessons = conn.execute(text("""
            SELECT count(*) FROM alliance_expert_lessons_v300 WHERE ruleset_version=:v
        """), {"v":RULESET_VERSION}).scalar() or 0
        calib = [dict(r) for r in conn.execute(text("""
            SELECT rule_id,human_examples,human_agreements,agreement_pct,trusted,hard_invariant
            FROM alliance_expert_rule_calibration_v300 WHERE ruleset_version=:v
            ORDER BY trusted DESC,human_examples DESC,rule_id
        """), {"v":RULESET_VERSION}).mappings().all()]
    return foundation._json_safe({
        "status":"PASS","version":VERSION,"mode":MODE,"ruleset_version":RULESET_VERSION,
        "latest_run":_loads(latest,{}) if latest else None,
        "prediction_distribution":{r["disposition"]:int(r["n"]) for r in dist},
        "lessons_installed":int(lessons),"rule_calibration":calib,
        "production_writes":0,"whatsapp_live_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0,
    })

DASHBOARD = """<!doctype html><html><head><meta charset='utf-8'><title>Alliance Expertise Loop</title>
<style>body{font-family:Arial;background:#07111d;color:#eef6ff;max-width:1450px;margin:24px auto}.card{background:#101d2b;padding:16px;border-radius:12px;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.big{font-size:28px;font-weight:bold}button{padding:12px 18px;border:0;border-radius:8px;background:#f5d76e;font-weight:bold}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style></head><body>
<h1>🧠 Alliance Property Brain — Expertise Loop 3.0</h1>
<p>Expertise is benchmark-gated. Human Gold is immutable. Self-confidence alone can never promote a rule.</p>
<button onclick='run()'>Run Full Expertise Cycle</button>
<div id='cards' class='grid'></div><div class='card'><h3>Latest Expertise Run</h3><pre id='latest'></pre></div>
<div class='card'><h3>Rule Calibration</h3><pre id='cal'></pre></div>
<script>
async function call(p,m='GET'){let r=await fetch(p,{method:m});let t=await r.text();let d;try{d=JSON.parse(t)}catch(e){d={raw:t}}if(!r.ok)throw Error(d.detail||d.raw||('HTTP '+r.status));return d}
function c(k,v){return `<div class="card"><div>${k}</div><div class="big">${v??0}</div></div>`}
async function load(){let s=await call('/api/property-brain/expertise-v300/status');let d=s.prediction_distribution||{};let l=s.latest_run||{};
document.getElementById('cards').innerHTML=c('Expert Resolved',d.EXPERT_RESOLVED)+c('Shadow Resolved',d.SHADOW_RESOLVED)+c('Exceptions',d.EXCEPTION)+c('Expertise Gate',l.expertise_gate||'NOT RUN');
document.getElementById('latest').textContent=JSON.stringify(l,null,2);document.getElementById('cal').textContent=JSON.stringify(s.rule_calibration,null,2)}
async function run(){document.getElementById('latest').textContent='Running full expertise cycle...';await call('/api/property-brain/expertise-v300/run?limit=1000','POST');await load()}load()
</script></body></html>"""

def register(core):
    engine = _engine(core)
    app = _app(core)
    _install(engine)
    try:
        run_expertise(engine, 1000)
    except Exception:
        pass

    if not foundation._route_exists(app, "/api/property-brain/expertise-v300/status"):
        @app.get("/api/property-brain/expertise-v300/status")
        def _status():
            return status(engine)

    if not foundation._route_exists(app, "/api/property-brain/expertise-v300/run"):
        @app.post("/api/property-brain/expertise-v300/run")
        def _run(limit:int=Query(default=1000,ge=1,le=5000)):
            return run_expertise(engine, limit)

    if not foundation._route_exists(app, "/property-brain/expertise-v300"):
        @app.get("/property-brain/expertise-v300",response_class=HTMLResponse)
        def _dashboard():
            return HTMLResponse(DASHBOARD)

    return {
        "status":"REGISTERED","version":VERSION,"dashboard":"/property-brain/expertise-v300",
        "auto_run_on_start":True,"expertise_is_metric_gated":True,
        "production_writes":0,"whatsapp_live_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0,
    }
