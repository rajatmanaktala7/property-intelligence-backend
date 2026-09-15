from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import inspect, text

VERSION = "2.3.0-TRUTH-FIRST-CANONICAL-RESTORATION"
CONFIRM = "APPLY_REQUIREMENT_RESTORATION_V2"
SOURCE_TABLES = (
    ("pi_unified_manual_requirements", "MANUAL"),
    ("pi_retail_manual_requirements", "MANUAL"),
    ("pi_hospitality_manual_requirements", "MANUAL"),
    ("pi_requirements", "LEGACY_MANUAL"),
    ("pi_operational_requirements", "LEGACY_OPERATIONAL"),
)

DDL = """
CREATE TABLE IF NOT EXISTS pi_requirement_restoration_lineage_v2(
    source_table TEXT NOT NULL,
    source_pk TEXT NOT NULL,
    gate_id BIGINT NOT NULL,
    message_hash TEXT NOT NULL,
    source_type TEXT NOT NULL,
    decision TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(source_table,source_pk)
)
"""


def _role(core, request: Request) -> None:
    role = core.get_role(request) if callable(getattr(core, "get_role", None)) else None
    if role != "admin":
        raise HTTPException(403, "Admin required")


def _exists(engine, table: str) -> bool:
    try:
        return bool(inspect(engine).has_table(table))
    except Exception:
        return False


def _dict(value: Any) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _first(obj: dict, names: tuple[str, ...]) -> str:
    lowered = {str(k).lower(): v for k, v in obj.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, "", [], {}):
            if isinstance(value, (dict, list)):
                return json.dumps(value, ensure_ascii=False, default=str)
            return str(value).strip()
    return ""


def _source_pk(obj: dict, fallback: int) -> str:
    return _first(obj, ("id", "requirement_id", "record_id", "source_id", "pk")) or str(fallback)


def _direct_message(obj: dict) -> str:
    return _first(obj, (
        "original_message", "requirement_message", "message", "raw_message",
        "raw_text", "source_text", "requirement_text", "requirement",
        "description", "additional_points", "remarks", "notes", "content",
    ))


def _message(obj: dict) -> str:
    direct = _direct_message(obj)
    if direct:
        return " ".join(direct.split())

    fields = []
    labels = (
        ("Need", ("property_type", "required_property_type", "asset_type")),
        ("Use", ("intended_use", "use_case", "category", "business_category")),
        ("Location", ("preferred_location", "preferred_locations", "location", "locality", "city")),
        ("Transaction", ("transaction_type", "rent_or_sale", "deal_type")),
        ("Area", ("requirement_sqft", "required_area", "area_sqft", "area")),
        ("Budget", ("budget", "max_budget", "rent_budget", "sale_budget")),
        ("Company", ("company_name", "brand_name", "retailer_name", "client_company")),
    )
    for label, names in labels:
        value = _first(obj, names)
        if value:
            fields.append(f"{label}: {value}")
    return "; ".join(fields)


def _structured_signals(obj: dict) -> dict:
    return {
        "asset_or_use":_first(obj,("property_type","required_property_type","asset_type","intended_use","use_case","category","business_category")),
        "location":_first(obj,("preferred_location","preferred_locations","location","locality","city","area_name","micro_market")),
        "transaction":_first(obj,("transaction_type","transaction","rent_sale","rent_or_sale","deal_type")),
        "area":_first(obj,("requirement_sqft","required_area","area_sqft","area","area_min_sqft","area_max_sqft","minimum_area","maximum_area")),
        "budget":_first(obj,("budget","max_budget","rent_budget","sale_budget","budget_min","budget_max")),
        "company":_first(obj,("company_name","brand_name","retailer_name","client_company")),
        "contact":_first(obj,("contact_number","contact_no","phone","mobile","email")),
    }

_DEMAND_RE=re.compile(r"\b(?:need(?:ed)?|require(?:d|ment)?|looking\s+for|seeking|want(?:ed|s)?\s+to\s+(?:buy|purchase|rent|lease)|interested\s+in|client\s+(?:needs|requires|looking))\b",re.I)
_FRAGMENT_RE=re.compile(r"^(?:good\s+frontage|park(?:/north-east)?\s+facing|premium\s+office\s+space|\d+(?:\.\d+)?\s*(?:cr|crore)(?:\s+to\s+\d+(?:\.\d+)?\s*(?:cr|crore))?\s+commercials?)\b",re.I)

def _rows(engine, table: str, limit: int = 10000) -> list[dict]:
    if not _exists(engine, table):
        return []
    with engine.connect() as conn:
        raw = conn.execute(
            text(f'SELECT to_jsonb(t) FROM "{table}" t LIMIT :limit'),
            {"limit": int(limit)},
        ).scalars().all()
    return [_dict(value) for value in raw if _dict(value)]


def _decision(gate,obj,message):
    if not message.strip(): return "SKIP_EMPTY",{}
    x=gate.extract(message); cls=str(x.get("classification") or "RAW").upper()
    if cls=="REJECTED/EXPIRED": return "SKIP_SUPPLY_OR_NOISE",x
    direct=" ".join(_direct_message(obj).split()); signals=_structured_signals(obj)
    names=[k for k,v in signals.items() if v]; explicit=bool(_DEMAND_RE.search(direct)); fragment=bool(_FRAGMENT_RE.search(direct.strip()))
    context=bool(signals["asset_or_use"] and (signals["location"] or signals["transaction"])); support=sum(bool(signals[k]) for k in ("area","budget","company","contact"))
    direct_safe=bool(explicit and context and not fragment and len(direct)>=18)
    structured_safe=bool(not direct and cls in {"AI-QUALIFIED","NEEDS VERIFICATION","VERIFIED ACTIVE"} and signals["asset_or_use"] and signals["location"] and signals["transaction"] and support>=1)
    x["_restoration_evidence"]={"explicit_demand_language":explicit,"fragment_rejected":fragment,"matched_signal_names":names,"structured_signal_values":signals,"structured_signal_count":len(names),"core_context_present":context,"supporting_signal_count":support,"decision_rule":"EXPLICIT_DEMAND_PLUS_CONTEXT" if direct_safe else "COMPLETE_STRUCTURED_RECORD" if structured_safe else "HUMAN_REVIEW"}
    return ("RESTORE_TO_GATE" if direct_safe or structured_safe else "NEEDS_HUMAN_REVIEW"),x

def _gate_match(conn, table: str, pk: str, message_hash: str):
    return conn.execute(text("""
        SELECT id,source_table,source_pk,classification,matcher_eligible
        FROM pi_requirement_gate_v1191
        WHERE (source_table=:table AND source_pk=:pk)
           OR message_hash=:message_hash
        ORDER BY CASE WHEN source_table=:table AND source_pk=:pk THEN 0 ELSE 1 END,id
        LIMIT 1
    """), {"table": table, "pk": pk, "message_hash": message_hash}).mappings().first()


def audit(core, sample_limit: int = 20) -> dict:
    import alliance_requirement_gate_v1191 as gate

    engine = core.engine
    if not _exists(engine, "pi_requirement_gate_v1191"):
        return {"status": "BLOCKED", "version": VERSION, "missing_tables": ["pi_requirement_gate_v1191"], "database_changed": False}

    lineage_exists = _exists(engine, "pi_requirement_restoration_lineage_v2")
    summaries = []
    samples = []
    totals = {"rows": 0, "restorable": 0, "already_represented": 0, "needs_human_review": 0, "skipped_supply_or_noise": 0, "skipped_empty": 0}

    with engine.connect() as conn:
        for table, source_type in SOURCE_TABLES:
            source_rows = _rows(engine, table)
            summary = {"table": table, "source_type": source_type, "rows": len(source_rows), "restorable": 0, "already_represented": 0, "needs_human_review": 0, "skipped_supply_or_noise": 0, "skipped_empty": 0}
            for index, obj in enumerate(source_rows, 1):
                pk = _source_pk(obj, index)
                message = _message(obj)
                message_hash = gate._hash(message) if message else hashlib.sha256(b"").hexdigest()
                decision, extracted = _decision(gate, obj, message)
                represented = False
                if decision == "RESTORE_TO_GATE":
                    represented = bool(_gate_match(conn, table, pk, message_hash))
                    if not represented and lineage_exists:
                        represented = bool(conn.execute(text("""
                            SELECT 1 FROM pi_requirement_restoration_lineage_v2
                            WHERE source_table=:table AND source_pk=:pk
                        """), {"table": table, "pk": pk}).scalar())
                key = "already_represented" if represented else (
                    "restorable" if decision == "RESTORE_TO_GATE" else
                    "skipped_supply_or_noise" if decision == "SKIP_SUPPLY_OR_NOISE" else
                    "skipped_empty" if decision == "SKIP_EMPTY" else
                    "needs_human_review"
                )
                summary[key] += 1
                totals[key] += 1
                if len(samples) < max(0, min(int(sample_limit), 50)) and key == "restorable":
                    samples.append({
                        "table": table,
                        "source_pk": pk,
                        "classification": extracted.get("classification"),
                        "evidence_quality": extracted.get("evidence_quality"),
                        "message_preview": message[:240],
                        "restoration_evidence":
                            extracted.get("_restoration_evidence", {}),
                    })
            totals["rows"] += len(source_rows)
            summaries.append(summary)

        gate_active = int(conn.execute(text("""
            SELECT COUNT(*) FROM pi_requirement_gate_v1191
            WHERE COALESCE(classification,'') NOT IN ('REJECTED','NOISE','REJECTED/EXPIRED')
        """)).scalar() or 0)
        matcher_eligible = int(conn.execute(text("SELECT COUNT(*) FROM pi_requirement_gate_v1191 WHERE matcher_eligible=TRUE")).scalar() or 0)

    return {
        "status": "READY", "version": VERSION,
        "gate_active": gate_active, "matcher_eligible": matcher_eligible,
        "totals": totals, "sources": summaries, "restorable_samples": samples,
        "policy": "EXPLICIT_AUTHORITATIVE_TABLES_ONLY_DEDUPED_UNVERIFIED",
        "derived_tables_promoted": 0, "automatic_matcher_eligibility": False,
        "database_changed": False,
    }


def apply(core) -> dict:
    import alliance_requirement_gate_v1191 as gate
    import alliance_requirement_restore_v1235 as restore

    engine = core.engine
    before = audit(core, 0)
    if before.get("status") != "READY":
        raise RuntimeError("Requirement restoration audit is not ready")

    with engine.begin() as conn:
        conn.execute(text(DDL))

    counts = {"gate_rows_created": 0, "existing_gate_rows_linked": 0, "lineage_rows_written": 0, "skipped_supply_or_noise": 0, "skipped_empty": 0}

    for table, source_type in SOURCE_TABLES:
        for index, obj in enumerate(_rows(engine, table), 1):
            pk = _source_pk(obj, index)
            message = _message(obj)
            decision, extracted = _decision(gate, obj, message)
            if decision != "RESTORE_TO_GATE":
                key = (
                    "skipped_supply_or_noise"
                    if decision == "SKIP_SUPPLY_OR_NOISE"
                    else "skipped_empty"
                    if decision == "SKIP_EMPTY"
                    else "needs_human_review"
                )
                counts.setdefault(key, 0)
                counts[key] += 1
                continue

            message_hash = gate._hash(message)
            with engine.connect() as conn:
                existing = _gate_match(conn, table, pk, message_hash)

            if existing:
                gate_row = dict(existing)
                counts["existing_gate_rows_linked"] += 1
            else:
                normalized = restore._normalize_source_row(table, obj, index)
                normalized["source"] = source_type
                normalized["source_pk"] = pk
                normalized["message"] = message
                gate_row = restore._ensure_gate_row(engine, normalized)
                counts["gate_rows_created"] += 1

            with engine.begin() as conn:
                written = conn.execute(text("""
                    INSERT INTO pi_requirement_restoration_lineage_v2(
                        source_table,source_pk,gate_id,message_hash,source_type,decision,details,updated_at
                    ) VALUES(
                        :table,:pk,:gate_id,:message_hash,:source_type,'REPRESENTED_IN_GATE',CAST(:details AS JSONB),NOW()
                    )
                    ON CONFLICT(source_table,source_pk) DO UPDATE SET
                        gate_id=EXCLUDED.gate_id,
                        message_hash=EXCLUDED.message_hash,
                        source_type=EXCLUDED.source_type,
                        decision=EXCLUDED.decision,
                        details=EXCLUDED.details,
                        updated_at=NOW()
                """), {
                    "table": table, "pk": pk, "gate_id": int(gate_row["id"]),
                    "message_hash": message_hash, "source_type": source_type,
                    "details": json.dumps({"classification": extracted.get("classification"), "version": VERSION}),
                }).rowcount
                counts["lineage_rows_written"] += int(written or 0)

    after = audit(core, 0)
    return {
        "status": "APPLIED", "version": VERSION, "counts": counts,
        "before": before, "after": after,
        "safety": {
            "matcher_rows_automatically_enabled": 0,
            "derived_or_index_tables_promoted": 0,
            "supply_or_noise_rows_promoted": 0,
            "source_rows_modified": 0,
        },
    }


def register(core):
    app = getattr(core, "app", None) or core
    router = APIRouter()

    @router.get("/api/alliance/requirement-restoration-v2/audit")
    def audit_route(request: Request, sample_limit: int = Query(20, ge=0, le=50)):
        _role(core, request)
        return audit(core, sample_limit)

    @router.post("/api/alliance/requirement-restoration-v2/apply")
    def apply_route(request: Request, confirm: str = Query(...)):
        _role(core, request)
        if confirm != CONFIRM:
            raise HTTPException(400, "Exact confirmation phrase required")
        return apply(core)

    app.include_router(router)
    return {"status": "REGISTERED", "version": VERSION, "automatic_apply": False}
