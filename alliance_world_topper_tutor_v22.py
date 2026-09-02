from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Body, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_property_brain_gold_v2 as gold_v2

VERSION = "2.2.0-WORLD-TOPPER-TUTOR-WHATSAPP-LIVE"
MODE = "GOLD_V1_MASTER_TUTOR_PLUS_LIVE_SHADOW"
SNAPSHOT_VERSION = "GOLD_V1_100"
TUTOR_VERSION = "ALLIANCE_WORLD_TOPPER_TUTOR_V1"

STATE = {
    "worker_started": False,
    "worker_alive": False,
    "last_poll_at": None,
    "last_shadow_at": None,
    "last_error": None,
    "live_rows_seen": 0,
    "shadow_rows_created": 0,
}
_LOCK = threading.Lock()
_STARTED = False

DDL = [
    """
    CREATE TABLE IF NOT EXISTS alliance_topper_curriculum_rules (
        rule_id UUID PRIMARY KEY,
        rule_code TEXT UNIQUE NOT NULL,
        rule_group TEXT NOT NULL,
        rule_text TEXT NOT NULL,
        severity TEXT NOT NULL DEFAULT 'HIGH',
        source TEXT NOT NULL DEFAULT 'HUMAN_GOLD',
        active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS alliance_topper_mastery_state (
        mastery_id UUID PRIMARY KEY,
        tutor_version TEXT NOT NULL,
        snapshot_version TEXT NOT NULL,
        gold_count INTEGER NOT NULL,
        mastery_profile JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS alliance_topper_error_memory (
        error_id UUID PRIMARY KEY,
        tutor_version TEXT NOT NULL,
        benchmark_run_id UUID,
        span_id UUID,
        category TEXT NOT NULL,
        lesson TEXT NOT NULL,
        evidence_excerpt TEXT,
        severity TEXT NOT NULL DEFAULT 'HIGH',
        resolved BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS alliance_topper_live_shadow (
        shadow_id UUID PRIMARY KEY,
        source_system TEXT NOT NULL DEFAULT 'WHATSAPP_LIVE',
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        source_id TEXT,
        message_id TEXT,
        source_item_no INTEGER,
        raw_text TEXT,
        parent_message_text TEXT,
        sender_name TEXT,
        sender_phone TEXT,
        current_record JSONB NOT NULL DEFAULT '{}'::jsonb,
        tutor_analysis JSONB NOT NULL DEFAULT '{}'::jsonb,
        tutor_decision TEXT NOT NULL,
        tutor_score NUMERIC(5,2),
        blockers JSONB NOT NULL DEFAULT '[]'::jsonb,
        warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(source_system, entity_type, entity_id)
    )
    """,
]

CURRICULUM = [
    ("ATOMIC_001", "BOUNDARY", "One physical property or one requirement must be one atomic entity.", "CRITICAL"),
    ("ATOMIC_002", "BOUNDARY", "Multiple unidentified assets remain an INVENTORY_GROUP. Never invent unit-level identity.", "CRITICAL"),
    ("CTX_001", "CONTEXT", "Atomic child text owns its own facts first. Parent context may be inherited only when scope is proven.", "CRITICAL"),
    ("CTX_002", "CONTEXT", "A city, locality or project appearing elsewhere in the parent source must not leak into an unrelated child.", "CRITICAL"),
    ("CTX_003", "CONTEXT", "Broker service area is not property locality.", "CRITICAL"),
    ("GEO_001", "GEOGRAPHY", "Never infer city from locality or project knowledge unless the source explicitly supports it.", "CRITICAL"),
    ("GEO_002", "GEOGRAPHY", "Keep source truth separate from normalization. Example: GGN can normalize to Gurgaon while original evidence remains GGN.", "HIGH"),
    ("CONTACT_001", "CONTACT", "Contact precedence is explicit message contact, then shared source contact, then WhatsApp sender lineage.", "CRITICAL"),
    ("CONTACT_002", "CONTACT", "Do not infer owner or broker role from a phone number unless source evidence proves the role.", "HIGH"),
    ("TX_001", "TRANSACTION", "Business/setup/stake sale remains SALE even when the premises itself pays rent.", "CRITICAL"),
    ("TX_002", "TRANSACTION", "A property offered for sale and explicitly already rented may be BOTH.", "HIGH"),
    ("TX_003", "TRANSACTION", "An occupancy requirement is RENT unless the source clearly asks to buy.", "HIGH"),
    ("MONEY_001", "MONEY", "Do not turn ambiguous money into total price, rate or rent without evidence.", "CRITICAL"),
    ("AREA_001", "AREA", "Do not assign a unit to an ambiguous number unless the source states the unit.", "HIGH"),
    ("LIVE_001", "WHATSAPP_LIVE", "WhatsApp Live ingestion continues independently. Tutor observes and learns in shadow mode without slowing or rewriting live inventory.", "CRITICAL"),
    ("LIVE_002", "WHATSAPP_LIVE", "Every live availability must retain raw message, parent message, sender provenance and source lineage.", "CRITICAL"),
    ("PROMOTE_001", "PROMOTION", "No tutor prediction is allowed to overwrite production inventory until benchmark and safety gates pass.", "CRITICAL"),
]

PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s\-]?)?[6-9](?:[\s\-]?\d){9}(?!\d)")
CITY_TERMS = re.compile(r"\b(?:Delhi|New Delhi|Noida|Gurgaon|Gurugram|GGN|Mumbai|Goa|Panjim|Panaji)\b", re.I)
LOCALITY_TERMS = re.compile(r"\b(?:Sector\s*\d+[A-Za-z]?|Sec\.?\s*\d+[A-Za-z]?|[A-Za-z][A-Za-z ]{2,30}(?: Nagar| Vihar| Enclave| Colony| West| East))\b", re.I)

def _now():
    return datetime.now(timezone.utc).isoformat()

def _engine(core):
    return foundation._engine_from_core(core)

def _app(core):
    return getattr(core, "app", None) or core

def _install(engine):
    with engine.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))
        for code, group, rule_text, severity in CURRICULUM:
            conn.execute(
                text(
                    """
                    INSERT INTO alliance_topper_curriculum_rules
                    (rule_id,rule_code,rule_group,rule_text,severity,source,active)
                    VALUES (:id,:code,:grp,:txt,:sev,'HUMAN_GOLD',TRUE)
                    ON CONFLICT(rule_code) DO UPDATE SET
                      rule_group=EXCLUDED.rule_group,
                      rule_text=EXCLUDED.rule_text,
                      severity=EXCLUDED.severity,
                      active=TRUE,
                      updated_at=now()
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "code": code,
                    "grp": group,
                    "txt": rule_text,
                    "sev": severity,
                },
            )

def _snapshot(engine):
    gold_v2.freeze_gold_v1(engine)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT snapshot_payload FROM alliance_gold_dataset_snapshots "
                "WHERE snapshot_version=:v"
            ),
            {"v": SNAPSHOT_VERSION},
        ).first()
    payload = foundation._loads(row[0] if row else None, [])
    if len(payload) != 100:
        raise RuntimeError("GOLD_V1_100 snapshot is missing or not exactly 100 examples")
    return payload

def _lesson_for(category):
    return {
        "content_type": "Classify intent before extracting fields. Requirement, availability, inventory group, fragment and noise are distinct business objects.",
        "transaction_type": "Determine commercial intent from evidence, not isolated money words. Separate sale, rent, both and unknown.",
        "city": "Use only city evidence owned by the atomic span or proven inherited header. Never use unrelated parent geography.",
        "locality": "Locality must belong to the atomic child. Do not inherit service area or sibling locality.",
        "project_name": "Project must be explicitly attached to the child or a proven project header.",
        "contacts": "Resolve explicit contact first, then shared footer, then WhatsApp sender/JID lineage.",
        "areas_presence": "Preserve stated area and role; do not invent units.",
        "money_presence": "Preserve stated money and its role; do not guess total/rate/rent.",
    }.get(category, "Review this Gold mismatch and convert the human correction into a reusable deterministic lesson.")

def train(engine):
    _install(engine)
    rows = _snapshot(engine)

    with engine.connect() as conn:
        latest = conn.execute(
            text(
                """
                SELECT run_id,engine_version,metrics,created_at
                FROM alliance_gold_evaluation_runs
                ORDER BY created_at DESC LIMIT 1
                """
            )
        ).mappings().first()

    run_id = str(latest["run_id"]) if latest else None
    error_counts = {}
    if run_id:
        with engine.connect() as conn:
            case_rows = conn.execute(
                text(
                    """
                    SELECT span_id,comparison
                    FROM alliance_gold_benchmark_cases
                    WHERE run_id=:rid
                    """
                ),
                {"rid": run_id},
            ).mappings().all()
        with engine.begin() as conn:
            for case in case_rows:
                comp = foundation._loads(case.get("comparison"), {})
                mismatches = list(comp.get("mismatches") or [])
                if not mismatches:
                    checks = comp.get("checks") or {}
                    mismatches = [k for k, ok in checks.items() if not ok]
                for category in mismatches:
                    error_counts[category] = error_counts.get(category, 0) + 1
                    exists = conn.execute(
                        text(
                            """
                            SELECT 1 FROM alliance_topper_error_memory
                            WHERE tutor_version=:tv AND benchmark_run_id=:rid
                              AND span_id=:sid AND category=:cat
                            LIMIT 1
                            """
                        ),
                        {
                            "tv": TUTOR_VERSION,
                            "rid": run_id,
                            "sid": str(case["span_id"]),
                            "cat": category,
                        },
                    ).first()
                    if not exists:
                        conn.execute(
                            text(
                                """
                                INSERT INTO alliance_topper_error_memory
                                (error_id,tutor_version,benchmark_run_id,span_id,
                                 category,lesson,severity)
                                VALUES (:id,:tv,:rid,:sid,:cat,:lesson,'HIGH')
                                """
                            ),
                            {
                                "id": str(uuid.uuid4()),
                                "tv": TUTOR_VERSION,
                                "rid": run_id,
                                "sid": str(case["span_id"]),
                                "cat": category,
                                "lesson": _lesson_for(category),
                            },
                        )

    distributions = {"content_type": {}, "transaction_type": {}}
    for row in rows:
        ct = str(row.get("content_type") or "UNKNOWN")
        tx = str(row.get("transaction_type") or "UNKNOWN")
        distributions["content_type"][ct] = distributions["content_type"].get(ct, 0) + 1
        distributions["transaction_type"][tx] = distributions["transaction_type"].get(tx, 0) + 1

    profile = {
        "tutor_version": TUTOR_VERSION,
        "snapshot_version": SNAPSHOT_VERSION,
        "gold_count": 100,
        "teaching_method": [
            "human_gold_ground_truth",
            "error_memory",
            "context_ownership",
            "retrieval_before_guessing",
            "zero_tolerance_hallucination",
            "whatsapp_live_shadow_practice",
            "promotion_only_after_exam_pass",
        ],
        "curriculum_rule_count": len(CURRICULUM),
        "gold_distribution": distributions,
        "latest_benchmark_run_id": run_id,
        "latest_benchmark_engine": latest.get("engine_version") if latest else None,
        "error_curriculum": dict(sorted(error_counts.items(), key=lambda kv: kv[1], reverse=True)),
        "production_write_permission": False,
    }

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO alliance_topper_mastery_state
                (mastery_id,tutor_version,snapshot_version,gold_count,mastery_profile)
                VALUES (:id,:tv,:sv,100,CAST(:p AS jsonb))
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "tv": TUTOR_VERSION,
                "sv": SNAPSHOT_VERSION,
                "p": json.dumps(profile, ensure_ascii=False),
            },
        )
    return {
        "status": "TRAINED",
        "profile": profile,
        "next": "Tutor will now observe WhatsApp Live availabilities in shadow mode and build error memory without changing live inventory.",
        "production_writes": 0,
    }

def _wa():
    import whatsapp_live_bridge as wb
    return wb

def _evidence_supports(value, raw, parent):
    if value in (None, "", "UNKNOWN"):
        return True
    v = re.sub(r"\s+", " ", str(value)).strip().casefold()
    r = re.sub(r"\s+", " ", str(raw or "")).casefold()
    p = re.sub(r"\s+", " ", str(parent or "")).casefold()
    return v in r or v in p

def _analyse_property(row):
    raw = str(row.get("raw_text") or "")
    parent = str(row.get("parent_message_text") or "")
    blockers = []
    warnings = []

    # Raw evidence is mandatory.
    if not raw.strip():
        blockers.append("MISSING_RAW_TEXT")

    # Context ownership checks.
    for field in ("city", "location", "locality"):
        value = row.get(field)
        if value not in (None, "", "UNKNOWN") and not _evidence_supports(value, raw, parent):
            blockers.append("UNSUPPORTED_" + field.upper())

    # Contacts/provenance.
    phones = [
        row.get("owner_phone"),
        row.get("broker_phone"),
        row.get("sender_phone"),
    ]
    if not any(str(x or "").strip() for x in phones):
        warnings.append("NO_CONTACT_OR_SENDER_PHONE")

    # Transaction evidence.
    tx = str(row.get("transaction_type") or "UNKNOWN").upper()
    if tx == "SALE" and not re.search(r"\b(?:sale|sell|asking|demand|cr|crore|lakh|lac)\b", raw + "\n" + parent, re.I):
        warnings.append("SALE_TRANSACTION_WEAKLY_GROUNDED")
    if tx == "RENT" and not re.search(r"\b(?:rent|lease|to let)\b", raw + "\n" + parent, re.I):
        warnings.append("RENT_TRANSACTION_WEAKLY_GROUNDED")

    # Atomicity.
    if row.get("source_item_no") is None and len(re.findall(r"\b(?:\d+\)|\d+\.|📍|✨)\s*", parent)) >= 2:
        warnings.append("POSSIBLE_MULTI_PROPERTY_PARENT_WITHOUT_ITEM_NUMBER")

    # Money and area sanity.
    if row.get("area_sqft") is not None and not re.search(r"\b(?:sq\s*ft|sqft|sft|sq\s*yd|sqyd|sqm|sq\s*m|acre|gaj|yard)\b", raw + "\n" + parent, re.I):
        warnings.append("AREA_VALUE_WITHOUT_VISIBLE_UNIT")
    if row.get("rent_inr") is not None and row.get("sale_price_inr") is not None and tx not in {"BOTH", "UNKNOWN"}:
        warnings.append("BOTH_MONEY_TYPES_BUT_TRANSACTION_NOT_BOTH_OR_UNKNOWN")

    score = 100.0
    score -= 22.0 * len(blockers)
    score -= 6.0 * len(warnings)
    score = max(0.0, round(score, 2))
    decision = "REVIEW" if blockers or score < 85 else "SHADOW_PASS"

    return {
        "decision": decision,
        "score": score,
        "blockers": blockers,
        "warnings": warnings,
        "teacher_notes": [
            "Live row was observed only. No WhatsApp or production row was modified.",
            "Any unsupported geography must be removed or justified by owned source context before future promotion.",
        ],
    }

def _analyse_requirement(row):
    raw = str(row.get("raw_text") or "")
    blockers = []
    warnings = []
    if not raw.strip():
        blockers.append("MISSING_RAW_TEXT")
    if str(row.get("city") or "").strip() and not _evidence_supports(row.get("city"), raw, ""):
        blockers.append("UNSUPPORTED_CITY")
    if str(row.get("transaction_type") or "UNKNOWN").upper() == "RENT":
        if not re.search(r"\b(?:rent|lease|rental)\b", raw, re.I):
            warnings.append("RENT_REQUIREMENT_WEAKLY_GROUNDED")
    if not str(row.get("contact_phone") or "").strip():
        warnings.append("NO_EXPLICIT_REQUIREMENT_CONTACT")
    score = max(0.0, round(100 - 22 * len(blockers) - 6 * len(warnings), 2))
    return {
        "decision": "REVIEW" if blockers or score < 85 else "SHADOW_PASS",
        "score": score,
        "blockers": blockers,
        "warnings": warnings,
        "teacher_notes": ["Requirement observed in shadow mode only."],
    }

def _shadow_exists(engine, entity_type, entity_id):
    with engine.connect() as conn:
        return bool(
            conn.execute(
                text(
                    """
                    SELECT 1 FROM alliance_topper_live_shadow
                    WHERE source_system='WHATSAPP_LIVE'
                      AND entity_type=:et AND entity_id=:eid
                    """
                ),
                {"et": entity_type, "eid": entity_id},
            ).first()
        )

def shadow_once(engine, limit=100):
    _install(engine)
    wb = _wa()
    if wb.wa_engine is None:
        return {"status": "NOT_CONFIGURED", "reason": "WHATSAPP_DATABASE_URL is not configured", "created": 0}

    created = 0
    seen = 0
    errors = []

    with wb.wa_engine.connect() as conn:
        properties = conn.execute(
            text(
                """
                SELECT * FROM wa_properties
                WHERE COALESCE(record_status,'ACTIVE')='ACTIVE'
                ORDER BY id DESC LIMIT :n
                """
            ),
            {"n": int(limit)},
        ).mappings().all()
        requirements = conn.execute(
            text(
                """
                SELECT * FROM wa_requirements
                WHERE COALESCE(status,'ACTIVE')='ACTIVE'
                ORDER BY id DESC LIMIT :n
                """
            ),
            {"n": int(max(20, limit // 2))},
        ).mappings().all()

    for entity_type, rows in (("PROPERTY", properties), ("REQUIREMENT", requirements)):
        for rr in rows:
            row = dict(rr)
            entity_id = str(row.get("wa_property_id") if entity_type == "PROPERTY" else row.get("wa_requirement_id"))
            if not entity_id:
                continue
            seen += 1
            if _shadow_exists(engine, entity_type, entity_id):
                continue

            analysis = _analyse_property(row) if entity_type == "PROPERTY" else _analyse_requirement(row)
            try:
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            """
                            INSERT INTO alliance_topper_live_shadow
                            (shadow_id,source_system,entity_type,entity_id,source_id,message_id,
                             source_item_no,raw_text,parent_message_text,sender_name,sender_phone,
                             current_record,tutor_analysis,tutor_decision,tutor_score,blockers,warnings)
                            VALUES
                            (:id,'WHATSAPP_LIVE',:et,:eid,:sid,:mid,:item,:raw,:parent,:sn,:sp,
                             CAST(:record AS jsonb),CAST(:analysis AS jsonb),:decision,:score,
                             CAST(:blockers AS jsonb),CAST(:warnings AS jsonb))
                            ON CONFLICT(source_system,entity_type,entity_id) DO NOTHING
                            """
                        ),
                        {
                            "id": str(uuid.uuid4()),
                            "et": entity_type,
                            "eid": entity_id,
                            "sid": str(row.get("source_id") or "") or None,
                            "mid": str(row.get("message_id") or "") or None,
                            "item": row.get("source_item_no"),
                            "raw": str(row.get("raw_text") or ""),
                            "parent": str(row.get("parent_message_text") or "") if entity_type == "PROPERTY" else None,
                            "sn": row.get("sender_name") if entity_type == "PROPERTY" else row.get("contact_name"),
                            "sp": row.get("sender_phone") if entity_type == "PROPERTY" else row.get("contact_phone"),
                            "record": json.dumps(foundation._json_safe(row), ensure_ascii=False),
                            "analysis": json.dumps(analysis, ensure_ascii=False),
                            "decision": analysis["decision"],
                            "score": analysis["score"],
                            "blockers": json.dumps(analysis["blockers"]),
                            "warnings": json.dumps(analysis["warnings"]),
                        },
                    )
                created += 1
            except Exception as exc:
                errors.append(f"{entity_type}:{entity_id}:{type(exc).__name__}:{exc}"[:400])

    STATE["live_rows_seen"] += seen
    STATE["shadow_rows_created"] += created
    STATE["last_shadow_at"] = _now()
    if errors:
        STATE["last_error"] = errors[-1]
    return {
        "status": "PASS" if not errors else "PARTIAL",
        "seen": seen,
        "created": created,
        "errors": errors[:10],
        "production_writes": 0,
        "whatsapp_live_writes": 0,
    }

def _worker(core):
    engine = _engine(core)
    STATE["worker_alive"] = True
    try:
        while True:
            STATE["last_poll_at"] = _now()
            try:
                shadow_once(engine, 120)
                STATE["last_error"] = None
            except Exception as exc:
                STATE["last_error"] = f"{type(exc).__name__}: {exc}"[:500]
            time.sleep(15)
    finally:
        STATE["worker_alive"] = False

def start_worker(core):
    global _STARTED
    with _LOCK:
        if _STARTED:
            return dict(STATE)
        t = threading.Thread(target=_worker, args=(core,), name="alliance-world-topper-live-shadow", daemon=True)
        t.start()
        _STARTED = True
        STATE["worker_started"] = True
        return dict(STATE)

def status(engine):
    _install(engine)
    with engine.connect() as conn:
        counts = conn.execute(
            text(
                """
                SELECT tutor_decision,count(*) n
                FROM alliance_topper_live_shadow
                GROUP BY tutor_decision
                """
            )
        ).mappings().all()
        latest_profile = conn.execute(
            text(
                """
                SELECT tutor_version,snapshot_version,gold_count,mastery_profile,created_at
                FROM alliance_topper_mastery_state
                ORDER BY created_at DESC LIMIT 1
                """
            )
        ).mappings().first()
        top_errors = conn.execute(
            text(
                """
                SELECT category,count(*) n
                FROM alliance_topper_error_memory
                GROUP BY category ORDER BY n DESC LIMIT 10
                """
            )
        ).mappings().all()

    wa_status = None
    try:
        import alliance_whatsapp_safe_ingest_v5 as safe_wa
        wa_status = safe_wa.queue_status()
    except Exception as exc:
        wa_status = {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}

    return foundation._json_safe({
        "status": "PASS",
        "version": VERSION,
        "mode": MODE,
        "snapshot_version": SNAPSHOT_VERSION,
        "tutor_version": TUTOR_VERSION,
        "worker": dict(STATE),
        "live_shadow_counts": {str(r["tutor_decision"]): int(r["n"]) for r in counts},
        "latest_mastery_profile": dict(latest_profile) if latest_profile else None,
        "error_memory": {str(r["category"]): int(r["n"]) for r in top_errors},
        "whatsapp_live_queue": wa_status,
        "whatsapp_live_relationship": "READ_ONLY_OBSERVER",
        "production_write_permission": False,
        "production_writes": 0,
        "whatsapp_live_writes": 0,
    })

DASHBOARD = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alliance World Topper Tutor</title>
<style>
body{font-family:Arial,sans-serif;background:#efe8dd;color:#27221d;margin:0}
main{max-width:1180px;margin:28px auto;padding:24px}
.card{background:#fff;border-radius:14px;padding:20px;margin:14px 0;box-shadow:0 2px 9px #00000012}
button{padding:12px 18px;border:0;border-radius:9px;cursor:pointer;margin-right:8px}
.primary{background:#27221d;color:#fff}
pre{white-space:pre-wrap;overflow:auto;background:#f8f4ee;padding:14px;border-radius:9px}
</style>
</head>
<body><main>
<h1>Alliance World Topper Tutor</h1>
<p>Human Gold teaches the Brain. WhatsApp Live remains the continuous real-world practice stream. Tutor observes in shadow mode and never rewrites live or production inventory.</p>
<div class="card">
<button class="primary" onclick="train()">Train From Gold V1</button>
<button onclick="shadow()">Run WhatsApp Live Shadow Now</button>
<button onclick="refreshStatus()">Refresh Status</button>
</div>
<div class="card"><h3>Status</h3><pre id="status">Loading...</pre></div>
<div class="card"><h3>Action Result</h3><pre id="result">No action yet.</pre></div>
<script>
async function api(path,method="GET"){
 const r=await fetch(path,{method,headers:{"Content-Type":"application/json"}});
 const t=await r.text(); let d={}; try{d=JSON.parse(t)}catch(e){d={raw:t}}
 if(!r.ok) throw new Error(d.detail||d.raw||("HTTP "+r.status)); return d;
}
async function refreshStatus(){
 try{document.getElementById("status").textContent=JSON.stringify(await api("/api/property-brain/topper-v22/status"),null,2)}
 catch(e){document.getElementById("status").textContent="ERROR: "+e.message}
}
async function train(){
 document.getElementById("result").textContent="Building Gold curriculum and error memory...";
 try{
   const d=await api("/api/property-brain/topper-v22/train","POST");
   document.getElementById("result").textContent=JSON.stringify(d,null,2); await refreshStatus();
 }catch(e){document.getElementById("result").textContent="ERROR: "+e.message}
}
async function shadow(){
 document.getElementById("result").textContent="Reading recent WhatsApp Live availabilities in shadow mode...";
 try{
   const d=await api("/api/property-brain/topper-v22/live-shadow/run?limit=150","POST");
   document.getElementById("result").textContent=JSON.stringify(d,null,2); await refreshStatus();
 }catch(e){document.getElementById("result").textContent="ERROR: "+e.message}
}
refreshStatus();
</script>
</main></body></html>
"""

def register(core):
    engine = _engine(core)
    app = _app(core)
    _install(engine)

    if not foundation._route_exists(app, "/api/property-brain/topper-v22/status"):
        @app.get("/api/property-brain/topper-v22/status")
        def topper_status():
            return status(engine)

    if not foundation._route_exists(app, "/api/property-brain/topper-v22/train"):
        @app.post("/api/property-brain/topper-v22/train")
        def topper_train(payload: Dict[str, Any] = Body(default={})):
            return train(engine)

    if not foundation._route_exists(app, "/api/property-brain/topper-v22/live-shadow/run"):
        @app.post("/api/property-brain/topper-v22/live-shadow/run")
        def topper_live_shadow(limit: int = Query(default=100, ge=1, le=1000)):
            return shadow_once(engine, limit)

    if not foundation._route_exists(app, "/property-brain/topper-v22"):
        @app.get("/property-brain/topper-v22", response_class=HTMLResponse)
        def topper_dashboard():
            return HTMLResponse(DASHBOARD)

    start_worker(core)

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "mode": MODE,
        "dashboard": "/property-brain/topper-v22",
        "gold_training": "/api/property-brain/topper-v22/train",
        "live_shadow": "/api/property-brain/topper-v22/live-shadow/run",
        "whatsapp_live_relationship": "READ_ONLY_OBSERVER",
        "production_write_permission": False,
        "production_writes": 0,
        "whatsapp_live_writes": 0,
    }
