from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone

from fastapi import Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation

VERSION = "2.5.1-CONTEXT-OWNERSHIP-HOTFIX"
MODE = "ATOMIC_FIRST_SIBLING_AWARE_PARENT_SCOPE_FAST_SAFE"
RESOLVER_VERSION = "ALLIANCE_CONTEXT_OWNER_V1_1"

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

def _safe_parent_for_critical(field, parent_items, sibling_atomic_profiles):
    parent_vals = _value_set(parent_items)
    if len(parent_vals) != 1:
        return False, "PARENT_NOT_UNIQUE", parent_vals

    candidate = parent_vals[0]
    sibling_explicit = []
    for atomic in sibling_atomic_profiles:
        sibling_explicit.extend(_value_set((atomic or {}).get(field) or []))

    sibling_unique = sorted(set(sibling_explicit), key=str.casefold)
    if not sibling_unique:
        if len(sibling_atomic_profiles) == 1:
            return True, "SINGLE_PROPERTY_MESSAGE", parent_vals
        return False, "MULTI_PROPERTY_PARENT_WITHOUT_SIBLING_CONFIRMATION", parent_vals

    normalized = {x.casefold() for x in sibling_unique}
    if len(normalized) == 1 and candidate.casefold() in normalized:
        return True, "ALL_EXPLICIT_SIBLINGS_AGREE", parent_vals

    return False, "SIBLING_CONFLICT_OR_MIXED_CONTEXT", parent_vals

def _build_context_cache(engine, wb, limit):
    # Read v2.4 rows once.
    with engine.connect() as conn:
        rows = [
            dict(x) for x in conn.execute(
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
        ]

    entity_profile = {}
    for r in rows:
        p = _loads(r.get("extracted_profile"), {})
        entity_profile[str(r["entity_id"])] = p.get("atomic_explicit") or {}

    message_ids = sorted({str(r.get("message_id")) for r in rows if r.get("message_id")})
    sibling_map = {}

    if message_ids:
        # Avoid UUID=varchar mismatch by comparing textual representations.
        with wb.wa_engine.connect() as wa_conn:
            sib_rows = wa_conn.execute(
                text(
                    """
                    SELECT wa_property_id,message_id::text AS message_id,source_item_no
                    FROM wa_properties
                    WHERE message_id::text = ANY(:mids)
                      AND COALESCE(record_status,'ACTIVE')='ACTIVE'
                    ORDER BY message_id,COALESCE(source_item_no,999999),id
                    """
                ),
                {"mids": message_ids},
            ).mappings().all()

        # Fetch any sibling v2.4 profiles not already in selected 1000 in one query.
        sibling_entity_ids = [str(x["wa_property_id"]) for x in sib_rows]
        missing_ids = [eid for eid in sibling_entity_ids if eid not in entity_profile]
        if missing_ids:
            with engine.connect() as conn:
                more = conn.execute(
                    text(
                        """
                        SELECT entity_id,extracted_profile
                        FROM alliance_topper_availability_v24
                        WHERE entity_id = ANY(:eids)
                        """
                    ),
                    {"eids": missing_ids},
                ).mappings().all()
            for x in more:
                p = _loads(x.get("extracted_profile"), {})
                entity_profile[str(x["entity_id"])] = p.get("atomic_explicit") or {}

        for s in sib_rows:
            mid = str(s["message_id"])
            sibling_map.setdefault(mid, []).append({
                "entity_id": str(s["wa_property_id"]),
                "source_item_no": s.get("source_item_no"),
                "atomic": entity_profile.get(str(s["wa_property_id"]), {}),
            })

    return rows, sibling_map

def _analyse_one(row, sibling_map):
    profile = _loads(row.get("extracted_profile"), {})
    atomic = profile.get("atomic_explicit") or {}
    parent = profile.get("parent_context_candidates") or {}
    fq = _loads(row.get("field_quality"), {})
    lineage = profile.get("contact_lineage") or {}

    mid = str(row.get("message_id") or "")
    siblings = sibling_map.get(mid, [])
    sibling_atomic = [x.get("atomic") or {} for x in siblings]

    owned, rejected, lessons = {}, {}, []
    supported, total = 0.0, 0

    for field in CRITICAL_FIELDS:
        total += 1
        a, p = atomic.get(field) or [], parent.get(field) or []
        if a:
            owned[field] = {"status":"OWNED_ATOMIC","values":_value_set(a),"evidence":a}
            supported += 1
        elif p:
            ok, reason, vals = _safe_parent_for_critical(field, p, sibling_atomic)
            if ok:
                owned[field] = {
                    "status":"OWNED_PARENT_SCOPED","values":vals,"evidence":p,
                    "scope_reason":reason
                }
                supported += 1
            else:
                rejected[field] = {
                    "status":"REJECTED_PARENT_INHERITANCE","values":vals,
                    "reason":reason,"evidence":p
                }
                lessons.append(f"{field}: reject parent inheritance because {reason}.")
        else:
            live = (fq.get(field) or {}).get("live_value")
            if live not in (None, "", "UNKNOWN"):
                rejected[field] = {
                    "status":"LIVE_ONLY_UNPROVEN","live_value":live,
                    "reason":"NO_ATOMIC_OR_SAFE_PARENT_EVIDENCE"
                }
                lessons.append(f"{field}: live value needs owned evidence before trust.")

    total += 1
    a, p = atomic.get("contacts") or [], parent.get("contacts") or []
    if a:
        owned["contacts"] = {"status":"OWNED_ATOMIC","values":_value_set(a),"evidence":a}
        supported += 1
    elif p:
        owned["contacts"] = {"status":"OWNED_SHARED_PARENT","values":_value_set(p),"evidence":p}
        supported += 1
    else:
        phones = [
            lineage.get("owner_phone"),
            lineage.get("broker_phone"),
            lineage.get("sender_phone"),
        ]
        phones = sorted({str(x).strip() for x in phones if str(x or "").strip()})
        if phones:
            owned["contacts"] = {
                "status":"OWNED_LINEAGE_FALLBACK","values":phones,
                "provenance":"WHATSAPP_SENDER_OR_LIVE_CONTACT_LINEAGE"
            }
            supported += 1
        else:
            rejected["contacts"] = {"status":"MISSING_CONTACT_CHAIN"}

    for field in SUPPORT_FIELDS:
        total += 1
        a, p = atomic.get(field) or [], parent.get(field) or []
        if a:
            owned[field] = {"status":"OWNED_ATOMIC","values":_value_set(a),"evidence":a}
            supported += 1
        elif p:
            owned[field] = {
                "status":"PARENT_CANDIDATE_NOT_ATOMIC_TRUTH",
                "values":_value_set(p),"evidence":p
            }
            supported += 0.5

    score = round(100.0 * supported / max(total, 1), 2)
    return {
        "owned_fields": owned,
        "rejected_inheritance": rejected,
        "sibling_context": {
            "sibling_count": len(siblings),
            "sibling_entity_ids": [x["entity_id"] for x in siblings],
            "multi_property_message": len(siblings) > 1,
        },
        "tutor_lessons": sorted(set(lessons)),
        "confidence_score": score,
    }

def _priority_cases(engine, limit=20):
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT o.entity_id,o.message_id,o.rejected_inheritance,o.confidence_score,
                       v.conflicts,v.review_reasons,v.raw_text,v.parent_message_text
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
        cats = []
        if rejected.get("city") or any(x.get("field")=="city" for x in conflicts): cats.append("CITY_CONTEXT")
        if rejected.get("locality") or any(x.get("field")=="locality" for x in conflicts): cats.append("LOCALITY_CONTEXT")
        if rejected.get("property_type") or any(x.get("field")=="property_type" for x in conflicts): cats.append("PROPERTY_TYPE")
        if rejected.get("transaction_type") or any(x.get("field")=="transaction_type" for x in conflicts): cats.append("TRANSACTION")
        if rejected.get("contacts"): cats.append("CONTACT")
        if not cats:
            continue

        category = cats[0]
        keyfield = {
            "CITY_CONTEXT":"city","LOCALITY_CONTEXT":"locality",
            "PROPERTY_TYPE":"property_type","TRANSACTION":"transaction_type",
            "CONTACT":"contacts"
        }[category]
        signature = f"{category}|{json.dumps(rejected.get(keyfield,{}),sort_keys=True)[:180]}"
        score = 100 - float(r.get("confidence_score") or 0) + min(20,5*len(conflicts)) + min(15,3*len(review))
        scored.append({
            "entity_id": r["entity_id"], "message_id": r.get("message_id"),
            "category": category, "priority_score": round(score,2),
            "reason": ", ".join(cats),
            "evidence": {
                "rejected_inheritance": rejected, "conflicts": conflicts,
                "review_reasons": review, "raw_text": r.get("raw_text"),
                "parent_message_text": r.get("parent_message_text")
            },
            "signature": signature,
        })

    scored.sort(key=lambda x:x["priority_score"], reverse=True)
    chosen, seen = [], set()
    for item in scored:
        if item["signature"] in seen:
            continue
        seen.add(item["signature"])
        chosen.append(item)
        if len(chosen) >= limit:
            break
    return chosen

def _save_queue(engine, cases):
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
                    "id":str(uuid.uuid4()),"eid":c["entity_id"],"mid":c.get("message_id"),
                    "cat":c["category"],"p":c["priority_score"],"reason":c["reason"],
                    "ev":json.dumps(foundation._json_safe(c["evidence"]),ensure_ascii=False),
                    "sig":c["signature"],
                },
            )

def run(engine, limit=1000):
    _install(engine)
    wb = _wa()
    if wb.wa_engine is None:
        return {"status":"NOT_CONFIGURED","reason":"WHATSAPP_DATABASE_URL missing"}

    rows, sibling_map = _build_context_cache(engine, wb, limit)
    failures = []

    for row in rows:
        try:
            result = _analyse_one(row, sibling_map)
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
                        "id":str(uuid.uuid4()),"eid":row["entity_id"],
                        "mid":row.get("message_id"),"sid":row.get("source_id"),
                        "item":row.get("source_item_no"),
                        "owned":json.dumps(result["owned_fields"],ensure_ascii=False),
                        "rej":json.dumps(result["rejected_inheritance"],ensure_ascii=False),
                        "sib":json.dumps(result["sibling_context"],ensure_ascii=False),
                        "lessons":json.dumps(result["tutor_lessons"],ensure_ascii=False),
                        "score":result["confidence_score"],"rv":RESOLVER_VERSION,
                    },
                )
        except Exception as exc:
            failures.append(f"{row.get('entity_id')}:{type(exc).__name__}:{exc}"[:500])

    cases = _priority_cases(engine,20)
    _save_queue(engine,cases)

    STATE["rows_seen"] += len(rows)
    STATE["ownership_rows"] += len(rows)-len(failures)
    STATE["last_run_at"] = _now()
    STATE["last_error"] = failures[-1] if failures else None

    return {
        "status":"PASS" if not failures else "PARTIAL","version":VERSION,
        "seen":len(rows),"resolved":len(rows)-len(failures),"failed":len(failures),
        "selected_teaching_cases":len(cases),
        "teaching_cases":foundation._json_safe(cases),
        "errors":failures[:10],
        "production_writes":0,"whatsapp_live_writes":0,"gold_v1_mutations":0,
    }

def status(engine):
    _install(engine)
    with engine.connect() as conn:
        # Portable JSONB emptiness test. Avoid jsonb_object_length dependency.
        summary = conn.execute(
            text(
                """
                SELECT count(*) n,avg(confidence_score) avg_score,
                       count(*) FILTER (WHERE rejected_inheritance <> '{}'::jsonb) rejected
                FROM alliance_context_ownership_v25
                WHERE resolver_version=:rv
                """
            ),
            {"rv":RESOLVER_VERSION},
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
        "status":"PASS","version":VERSION,"mode":MODE,"resolver_version":RESOLVER_VERSION,
        "worker":dict(STATE),
        "ownership_profiles":int(summary["n"] or 0) if summary else 0,
        "average_ownership_confidence":round(float(summary["avg_score"] or 0),2) if summary else 0,
        "profiles_with_rejected_inheritance":int(summary["rejected"] or 0) if summary else 0,
        "active_learning_queue":[dict(x) for x in queue],
        "fixes_applied":[
            "UUID/text message_id comparison fixed",
            "N+1 sibling queries removed",
            "status JSONB query made portable",
            "same Gold/WhatsApp/production safety preserved"
        ],
        "whatsapp_live_relationship":"READ_ONLY_CONTEXT_RESOLVER",
        "production_writes":0,"whatsapp_live_writes":0,"gold_v1_mutations":0,
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
            {"eid":entity_id},
        ).mappings().first()
    return foundation._json_safe(dict(row)) if row else {"status":"NOT_FOUND","entity_id":entity_id}

def _worker(core):
    engine=_engine(core)
    STATE["worker_alive"]=True
    try:
        while True:
            STATE["last_poll_at"]=_now()
            try:
                run(engine,1000)
                STATE["last_error"]=None
            except Exception as exc:
                STATE["last_error"]=f"{type(exc).__name__}: {exc}"[:500]
            time.sleep(45)
    finally:
        STATE["worker_alive"]=False

def start_worker(core):
    global _STARTED
    with _LOCK:
        if _STARTED:
            return dict(STATE)
        t=threading.Thread(target=_worker,args=(core,),name="alliance-context-owner-v25-hotfix",daemon=True)
        t.start()
        _STARTED=True
        STATE["worker_started"]=True
        return dict(STATE)

DASHBOARD=r"""
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alliance Context Ownership 2.5.1</title>
<style>body{font-family:Arial;background:#eee8de;color:#25211d;margin:0}main{max-width:1200px;margin:28px auto;padding:24px}.card{background:#fff;border-radius:14px;padding:20px;margin:14px 0;box-shadow:0 2px 9px #00000012}button{padding:12px 18px;border:0;border-radius:9px;cursor:pointer;margin-right:8px}.primary{background:#25211d;color:#fff}pre{white-space:pre-wrap;overflow:auto;background:#f8f4ee;padding:14px;border-radius:9px}input{padding:10px;width:360px}</style>
</head><body><main><h1>Context Ownership + Active Learning 2.5.1</h1>
<p>Fast sibling-aware context ownership. WhatsApp Live, Gold V1 and production remain untouched.</p>
<div class="card"><button class="primary" onclick="runNow()">Resolve Latest 1000</button><button onclick="refreshStatus()">Refresh</button></div>
<div class="card"><input id="eid" placeholder="WAP-..."><button onclick="openCase()">Open Case</button><pre id="case">Enter property ID.</pre></div>
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
    engine=_engine(core)
    app=_app(core)
    _install(engine)
    if not foundation._route_exists(app,"/api/property-brain/context-v25/status"):
        @app.get("/api/property-brain/context-v25/status")
        def context_status(): return status(engine)
    if not foundation._route_exists(app,"/api/property-brain/context-v25/run"):
        @app.post("/api/property-brain/context-v25/run")
        def context_run(limit:int=Query(default=1000,ge=1,le=5000)): return run(engine,limit)
    if not foundation._route_exists(app,"/api/property-brain/context-v25/case/{entity_id}"):
        @app.get("/api/property-brain/context-v25/case/{entity_id}")
        def context_case(entity_id:str): return get_case(engine,entity_id)
    if not foundation._route_exists(app,"/property-brain/context-v25"):
        @app.get("/property-brain/context-v25",response_class=HTMLResponse)
        def context_dashboard(): return HTMLResponse(DASHBOARD)
    start_worker(core)
    return {"status":"REGISTERED","version":VERSION,"dashboard":"/property-brain/context-v25","production_writes":0,"whatsapp_live_writes":0,"gold_v1_mutations":0}
