from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_evidence_first_v24 as v24

VERSION = "2.5.0-CONTEXT-OWNERSHIP-ACTIVE-LEARNING"
MODE = "ATOMIC_FIRST_SIBLING_AWARE_PARENT_SCOPE"
RESOLVER_VERSION = "ALLIANCE_CONTEXT_OWNER_V1"

STATE = {
    "worker_started": False,
    "worker_alive": False,
    "last_poll_at": None,
    "last_run_at": None,
    "last_error": None,
    "rows_seen": 0,
    "ownership_rows": 0,
}
_LOCK = threading.Lock()
_STARTED = False

DDL = [
    """
    CREATE TABLE IF NOT EXISTS alliance_context_ownership_v25 (
        ownership_id UUID PRIMARY KEY,
        entity_id TEXT NOT NULL UNIQUE,
        message_id TEXT,
        source_id TEXT,
        source_item_no INTEGER,
        owned_fields JSONB NOT NULL DEFAULT '{}'::jsonb,
        rejected_inheritance JSONB NOT NULL DEFAULT '{}'::jsonb,
        sibling_context JSONB NOT NULL DEFAULT '{}'::jsonb,
        tutor_lessons JSONB NOT NULL DEFAULT '[]'::jsonb,
        confidence_score NUMERIC(5,2),
        resolver_version TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS alliance_active_learning_v25 (
        learning_id UUID PRIMARY KEY,
        entity_id TEXT NOT NULL,
        message_id TEXT,
        category TEXT NOT NULL,
        priority_score NUMERIC(6,2) NOT NULL,
        reason TEXT NOT NULL,
        evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
        signature TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'OPEN',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(signature)
    )
    """,
]

CRITICAL_FIELDS = ("city", "locality", "property_type", "transaction_type")
SHAREABLE_FIELDS = ("contacts",)
SUPPORT_FIELDS = (
    "areas","money","configuration","floor","furnishing","parking","possession",
    "availability","facing_view","frontage","road_width","age","ceiling_height",
    "power_load","security_deposit","cam_maintenance","brokerage","amenities","suitable_uses"
)

def _now():
    return datetime.now(timezone.utc).isoformat()

def _engine(core):
    return foundation._engine_from_core(core)

def _app(core):
    return getattr(core, "app", None) or core

def _wa():
    import whatsapp_live_bridge as wb
    return wb

def _install(engine):
    with engine.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))

def _loads(v, default):
    return foundation._loads(v, default)

def _value_set(items):
    vals = []
    for x in items or []:
        val = x.get("value") if isinstance(x, dict) else x
        if isinstance(val, dict):
            val = json.dumps(val, sort_keys=True)
        if val not in (None, "", "UNKNOWN"):
            vals.append(str(val).strip())
    return sorted(set(vals), key=str.casefold)

def _sibling_profiles(wa_conn, message_id):
    if not message_id:
        return []
    return [
        dict(x) for x in wa_conn.execute(
            text(
                """
                SELECT wa_property_id,message_id,source_item_no,raw_text,parent_message_text
                FROM wa_properties
                WHERE message_id=:mid
                  AND COALESCE(record_status,'ACTIVE')='ACTIVE'
                ORDER BY COALESCE(source_item_no,999999),id
                """
            ),
            {"mid": message_id},
        ).mappings().all()
    ]

def _safe_parent_for_critical(field, parent_items, atomic_profiles, this_entity):
    parent_vals = _value_set(parent_items)
    if len(parent_vals) != 1:
        return False, "PARENT_NOT_UNIQUE", parent_vals
    candidate = parent_vals[0]

    sibling_explicit = []
    for p in atomic_profiles:
        vals = _value_set((p.get("atomic") or {}).get(field) or [])
        if vals:
            sibling_explicit.extend(vals)

    sibling_unique = sorted(set(sibling_explicit), key=str.casefold)
    if not sibling_unique:
        if len(atomic_profiles) == 1:
            return True, "SINGLE_PROPERTY_MESSAGE", parent_vals
        return False, "MULTI_PROPERTY_PARENT_WITHOUT_SIBLING_CONFIRMATION", parent_vals

    normalized = {x.casefold() for x in sibling_unique}
    if len(normalized) == 1 and candidate.casefold() in normalized:
        return True, "ALL_EXPLICIT_SIBLINGS_AGREE", parent_vals

    return False, "SIBLING_CONFLICT_OR_MIXED_CONTEXT", parent_vals

def _analyse_one(engine, wa_conn, row):
    entity_id = str(row["entity_id"])
    profile = _loads(row.get("extracted_profile"), {})
    atomic = profile.get("atomic_explicit") or {}
    parent = profile.get("parent_context_candidates") or {}
    fq = _loads(row.get("field_quality"), {})

    siblings = _sibling_profiles(wa_conn, row.get("message_id"))
    sibling_profiles = []
    for sib in siblings:
        with engine.connect() as conn:
            vp = conn.execute(
                text(
                    "SELECT extracted_profile FROM alliance_topper_availability_v24 "
                    "WHERE entity_id=:eid"
                ),
                {"eid": sib["wa_property_id"]},
            ).first()
        sp = _loads(vp[0] if vp else None, {})
        sibling_profiles.append({
            "entity_id": sib["wa_property_id"],
            "atomic": sp.get("atomic_explicit") or {},
        })

    owned = {}
    rejected = {}
    lessons = []
    supported = 0
    total = 0

    for field in CRITICAL_FIELDS:
        total += 1
        a = atomic.get(field) or []
        p = parent.get(field) or []
        if a:
            owned[field] = {
                "status": "OWNED_ATOMIC",
                "values": _value_set(a),
                "evidence": a,
            }
            supported += 1
        elif p:
            ok, reason, vals = _safe_parent_for_critical(field, p, sibling_profiles, entity_id)
            if ok:
                owned[field] = {
                    "status": "OWNED_PARENT_SCOPED",
                    "values": vals,
                    "evidence": p,
                    "scope_reason": reason,
                }
                supported += 1
            else:
                rejected[field] = {
                    "status": "REJECTED_PARENT_INHERITANCE",
                    "values": vals,
                    "reason": reason,
                    "evidence": p,
                }
                lessons.append(f"{field}: parent context rejected because {reason}.")
        else:
            live = (fq.get(field) or {}).get("live_value")
            if live not in (None, "", "UNKNOWN"):
                rejected[field] = {
                    "status": "LIVE_ONLY_UNPROVEN",
                    "live_value": live,
                    "reason": "NO_ATOMIC_OR_SAFE_PARENT_EVIDENCE",
                }
                lessons.append(f"{field}: populated live value is not trusted without owned evidence.")

    for field in SHAREABLE_FIELDS:
        total += 1
        a = atomic.get(field) or []
        p = parent.get(field) or []
        lineage = profile.get("contact_lineage") or {}
        if a:
            owned[field] = {"status": "OWNED_ATOMIC", "values": _value_set(a), "evidence": a}
            supported += 1
        elif p:
            owned[field] = {"status": "OWNED_SHARED_PARENT", "values": _value_set(p), "evidence": p}
            supported += 1
        else:
            phones = [
                lineage.get("owner_phone"),
                lineage.get("broker_phone"),
                lineage.get("sender_phone"),
            ]
            phones = [str(x).strip() for x in phones if str(x or "").strip()]
            if phones:
                owned[field] = {
                    "status": "OWNED_LINEAGE_FALLBACK",
                    "values": sorted(set(phones)),
                    "provenance": "WHATSAPP_SENDER_OR_LIVE_CONTACT_LINEAGE",
                }
                supported += 1
            else:
                rejected[field] = {"status": "MISSING_CONTACT_CHAIN"}

    for field in SUPPORT_FIELDS:
        total += 1
        a = atomic.get(field) or []
        p = parent.get(field) or []
        if a:
            owned[field] = {"status": "OWNED_ATOMIC", "values": _value_set(a), "evidence": a}
            supported += 1
        elif p:
            # Non-critical fields can be parent candidates, but remain labelled.
            owned[field] = {
                "status": "PARENT_CANDIDATE_NOT_ATOMIC_TRUTH",
                "values": _value_set(p),
                "evidence": p,
            }
            supported += 0.5

    score = round(100.0 * supported / max(total, 1), 2)
    sibling_context = {
        "sibling_count": len(siblings),
        "sibling_entity_ids": [x["wa_property_id"] for x in siblings],
        "multi_property_message": len(siblings) > 1,
    }

    return {
        "owned_fields": owned,
        "rejected_inheritance": rejected,
        "sibling_context": sibling_context,
        "tutor_lessons": sorted(set(lessons)),
        "confidence_score": score,
    }

def _priority_cases(engine, limit=20):
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT o.entity_id,o.message_id,o.rejected_inheritance,o.tutor_lessons,
                       o.confidence_score,v.conflicts,v.review_reasons,v.raw_text,v.parent_message_text
                FROM alliance_context_ownership_v25 o
                JOIN alliance_topper_availability_v24 v ON v.entity_id=o.entity_id
                WHERE o.resolver_version=:rv
                ORDER BY o.confidence_score ASC,o.updated_at DESC
                LIMIT 500
                """
            ),
            {"rv": RESOLVER_VERSION},
        ).mappings().all()

    scored = []
    for r in rows:
        rejected = _loads(r.get("rejected_inheritance"), {})
        conflicts = _loads(r.get("conflicts"), [])
        review = _loads(r.get("review_reasons"), [])
        categories = []
        if rejected.get("city") or any(x.get("field") == "city" for x in conflicts):
            categories.append("CITY_CONTEXT")
        if rejected.get("locality") or any(x.get("field") == "locality" for x in conflicts):
            categories.append("LOCALITY_CONTEXT")
        if rejected.get("property_type") or any(x.get("field") == "property_type" for x in conflicts):
            categories.append("PROPERTY_TYPE")
        if rejected.get("transaction_type") or any(x.get("field") == "transaction_type" for x in conflicts):
            categories.append("TRANSACTION")
        if rejected.get("contacts"):
            categories.append("CONTACT")
        if not categories:
            continue

        base = 100 - float(r.get("confidence_score") or 0)
        base += min(20, 5 * len(conflicts))
        base += min(15, 3 * len(review))
        category = categories[0]
        signature = f"{category}|{json.dumps(rejected.get(category.split('_')[0].lower(),{}),sort_keys=True)[:180]}"
        scored.append({
            "entity_id": r["entity_id"],
            "message_id": r.get("message_id"),
            "category": category,
            "priority_score": round(base, 2),
            "reason": ", ".join(categories),
            "evidence": {
                "rejected_inheritance": rejected,
                "conflicts": conflicts,
                "review_reasons": review,
                "raw_text": r.get("raw_text"),
                "parent_message_text": r.get("parent_message_text"),
            },
            "signature": signature,
        })

    scored.sort(key=lambda x: x["priority_score"], reverse=True)
    chosen = []
    seen_sig = set()
    for item in scored:
        sig = item["signature"]
        if sig in seen_sig:
            continue
        seen_sig.add(sig)
        chosen.append(item)
        if len(chosen) >= limit:
            break
    return chosen

def _save_learning_queue(engine, cases):
    with engine.begin() as conn:
        for c in cases:
            conn.execute(
                text(
                    """
                    INSERT INTO alliance_active_learning_v25
                    (learning_id,entity_id,message_id,category,priority_score,reason,evidence,signature,status)
                    VALUES (:id,:eid,:mid,:cat,:p,:reason,CAST(:ev AS jsonb),:sig,'OPEN')
                    ON CONFLICT(signature) DO UPDATE SET
                      priority_score=GREATEST(alliance_active_learning_v25.priority_score,EXCLUDED.priority_score),
                      reason=EXCLUDED.reason,evidence=EXCLUDED.evidence
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "eid": c["entity_id"],
                    "mid": c.get("message_id"),
                    "cat": c["category"],
                    "p": c["priority_score"],
                    "reason": c["reason"],
                    "ev": json.dumps(foundation._json_safe(c["evidence"]), ensure_ascii=False),
                    "sig": c["signature"],
                },
            )

def run(engine, limit=1000):
    _install(engine)
    wb = _wa()
    if wb.wa_engine is None:
        return {"status": "NOT_CONFIGURED", "reason": "WHATSAPP_DATABASE_URL missing"}

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT entity_id,message_id,source_id,source_item_no,field_quality,
                       extracted_profile,conflicts,review_reasons,raw_text,parent_message_text
                FROM alliance_topper_availability_v24
                WHERE extractor_version='ALLIANCE_AVAILABILITY_EXTRACTOR_V2'
                ORDER BY updated_at DESC LIMIT :n
                """
            ),
            {"n": int(limit)},
        ).mappings().all()

    failures = []
    with wb.wa_engine.connect() as wa_conn:
        for rr in rows:
            row = dict(rr)
            try:
                result = _analyse_one(engine, wa_conn, row)
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            """
                            INSERT INTO alliance_context_ownership_v25
                            (ownership_id,entity_id,message_id,source_id,source_item_no,
                             owned_fields,rejected_inheritance,sibling_context,tutor_lessons,
                             confidence_score,resolver_version)
                            VALUES (:id,:eid,:mid,:sid,:item,CAST(:owned AS jsonb),
                             CAST(:rej AS jsonb),CAST(:sib AS jsonb),CAST(:lessons AS jsonb),
                             :score,:rv)
                            ON CONFLICT(entity_id) DO UPDATE SET
                             message_id=EXCLUDED.message_id,source_id=EXCLUDED.source_id,
                             source_item_no=EXCLUDED.source_item_no,owned_fields=EXCLUDED.owned_fields,
                             rejected_inheritance=EXCLUDED.rejected_inheritance,
                             sibling_context=EXCLUDED.sibling_context,tutor_lessons=EXCLUDED.tutor_lessons,
                             confidence_score=EXCLUDED.confidence_score,
                             resolver_version=EXCLUDED.resolver_version,updated_at=now()
                            """
                        ),
                        {
                            "id": str(uuid.uuid4()), "eid": row["entity_id"],
                            "mid": row.get("message_id"), "sid": row.get("source_id"),
                            "item": row.get("source_item_no"),
                            "owned": json.dumps(result["owned_fields"], ensure_ascii=False),
                            "rej": json.dumps(result["rejected_inheritance"], ensure_ascii=False),
                            "sib": json.dumps(result["sibling_context"], ensure_ascii=False),
                            "lessons": json.dumps(result["tutor_lessons"], ensure_ascii=False),
                            "score": result["confidence_score"], "rv": RESOLVER_VERSION,
                        },
                    )
            except Exception as exc:
                failures.append(f"{row.get('entity_id')}:{type(exc).__name__}:{exc}"[:500])

    cases = _priority_cases(engine, 20)
    _save_learning_queue(engine, cases)
    STATE["rows_seen"] += len(rows)
    STATE["ownership_rows"] += len(rows) - len(failures)
    STATE["last_run_at"] = _now()
    STATE["last_error"] = failures[-1] if failures else None

    return {
        "status": "PASS" if not failures else "PARTIAL",
        "version": VERSION,
        "seen": len(rows),
        "resolved": len(rows) - len(failures),
        "failed": len(failures),
        "selected_teaching_cases": len(cases),
        "teaching_cases": foundation._json_safe(cases),
        "errors": failures[:10],
        "production_writes": 0,
        "whatsapp_live_writes": 0,
        "gold_v1_mutations": 0,
    }

def status(engine):
    _install(engine)
    with engine.connect() as conn:
        summary = conn.execute(
            text(
                """
                SELECT count(*) n,avg(confidence_score) avg_score,
                       count(*) FILTER (WHERE jsonb_object_length(rejected_inheritance)>0) rejected
                FROM alliance_context_ownership_v25
                WHERE resolver_version=:rv
                """
            ),
            {"rv": RESOLVER_VERSION},
        ).mappings().first()
        queue = conn.execute(
            text(
                """
                SELECT learning_id,entity_id,category,priority_score,reason,status,created_at
                FROM alliance_active_learning_v25
                WHERE status='OPEN'
                ORDER BY priority_score DESC,created_at DESC LIMIT 20
                """
            )
        ).mappings().all()

    return foundation._json_safe({
        "status": "PASS",
        "version": VERSION,
        "mode": MODE,
        "resolver_version": RESOLVER_VERSION,
        "worker": dict(STATE),
        "ownership_profiles": int(summary["n"] or 0) if summary else 0,
        "average_ownership_confidence": round(float(summary["avg_score"] or 0), 2) if summary else 0,
        "profiles_with_rejected_inheritance": int(summary["rejected"] or 0) if summary else 0,
        "active_learning_queue": [dict(x) for x in queue],
        "teaching_policy": "Atomic first. Parent context only when sibling-aware scope is safe. Contacts may be shared. Gold V1 immutable.",
        "whatsapp_live_relationship": "READ_ONLY_CONTEXT_RESOLVER",
        "production_writes": 0,
        "whatsapp_live_writes": 0,
        "gold_v1_mutations": 0,
    })

def get_case(engine, entity_id):
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT o.*,v.raw_text,v.parent_message_text,v.field_quality,v.conflicts
                FROM alliance_context_ownership_v25 o
                JOIN alliance_topper_availability_v24 v ON v.entity_id=o.entity_id
                WHERE o.entity_id=:eid
                """
            ),
            {"eid": entity_id},
        ).mappings().first()
    return foundation._json_safe(dict(row)) if row else {"status":"NOT_FOUND","entity_id":entity_id}

def _worker(core):
    engine = _engine(core)
    STATE["worker_alive"] = True
    try:
        while True:
            STATE["last_poll_at"] = _now()
            try:
                run(engine, 1000)
                STATE["last_error"] = None
            except Exception as exc:
                STATE["last_error"] = f"{type(exc).__name__}: {exc}"[:500]
            time.sleep(45)
    finally:
        STATE["worker_alive"] = False

def start_worker(core):
    global _STARTED
    with _LOCK:
        if _STARTED:
            return dict(STATE)
        t = threading.Thread(target=_worker, args=(core,), name="alliance-context-owner-v25", daemon=True)
        t.start()
        _STARTED = True
        STATE["worker_started"] = True
        return dict(STATE)

DASHBOARD = r"""
<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alliance Context Ownership 2.5</title>
<style>
body{font-family:Arial;background:#eee8de;color:#25211d;margin:0}
main{max-width:1200px;margin:28px auto;padding:24px}
.card{background:white;border-radius:14px;padding:20px;margin:14px 0;box-shadow:0 2px 9px #00000012}
button{padding:12px 18px;border:0;border-radius:9px;cursor:pointer;margin-right:8px}
.primary{background:#25211d;color:white}
pre{white-space:pre-wrap;overflow:auto;background:#f8f4ee;padding:14px;border-radius:9px}
input{padding:10px;width:360px}
</style></head><body><main>
<h1>Context Ownership + Active Learning 2.5</h1>
<p>Atomic facts first. Parent inheritance only when sibling-aware scope proves it safe. The system automatically selects the most educational conflicts for teaching.</p>
<div class="card"><button class="primary" onclick="runNow()">Resolve Latest 1000</button>
<button onclick="refreshStatus()">Refresh</button></div>
<div class="card"><input id="eid" placeholder="WAP-..."><button onclick="openCase()">Open Case</button>
<pre id="case">Enter property ID.</pre></div>
<div class="card"><h3>Status + Teaching Queue</h3><pre id="status">Loading...</pre></div>
<div class="card"><h3>Action Result</h3><pre id="result">No action yet.</pre></div>
<script>
async function api(path,method="GET"){const r=await fetch(path,{method});const t=await r.text();let d={};try{d=JSON.parse(t)}catch(e){d={raw:t}}if(!r.ok)throw new Error(d.detail||d.raw||("HTTP "+r.status));return d}
async function refreshStatus(){try{document.getElementById("status").textContent=JSON.stringify(await api("/api/property-brain/context-v25/status"),null,2)}catch(e){document.getElementById("status").textContent="ERROR: "+e.message}}
async function runNow(){try{document.getElementById("result").textContent="Resolving...";const d=await api("/api/property-brain/context-v25/run?limit=1000","POST");document.getElementById("result").textContent=JSON.stringify(d,null,2);await refreshStatus()}catch(e){document.getElementById("result").textContent="ERROR: "+e.message}}
async function openCase(){const id=document.getElementById("eid").value.trim();if(!id)return;try{document.getElementById("case").textContent=JSON.stringify(await api("/api/property-brain/context-v25/case/"+encodeURIComponent(id)),null,2)}catch(e){document.getElementById("case").textContent="ERROR: "+e.message}}
refreshStatus();
</script></main></body></html>
"""

def register(core):
    engine = _engine(core)
    app = _app(core)
    _install(engine)

    if not foundation._route_exists(app, "/api/property-brain/context-v25/status"):
        @app.get("/api/property-brain/context-v25/status")
        def context_status():
            return status(engine)

    if not foundation._route_exists(app, "/api/property-brain/context-v25/run"):
        @app.post("/api/property-brain/context-v25/run")
        def context_run(limit: int = Query(default=1000, ge=1, le=5000)):
            return run(engine, limit)

    if not foundation._route_exists(app, "/api/property-brain/context-v25/case/{entity_id}"):
        @app.get("/api/property-brain/context-v25/case/{entity_id}")
        def context_case(entity_id: str):
            return get_case(engine, entity_id)

    if not foundation._route_exists(app, "/property-brain/context-v25"):
        @app.get("/property-brain/context-v25", response_class=HTMLResponse)
        def context_dashboard():
            return HTMLResponse(DASHBOARD)

    start_worker(core)
    return {
        "status":"REGISTERED",
        "version":VERSION,
        "dashboard":"/property-brain/context-v25",
        "production_writes":0,
        "whatsapp_live_writes":0,
        "gold_v1_mutations":0,
    }
