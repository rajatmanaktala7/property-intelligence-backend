from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from fastapi import APIRouter
from sqlalchemy import text

import alliance_phase5_canonical_matcher as phase5

VERSION = "1.0.0-WHATSAPP-LIVE-CLEAN-OPERATIONAL-BRIDGE"
POLL_SECONDS = 60
BATCH_SIZE = 2000

LEDGER_TABLE = "pi_whatsapp_live_clean_ledger"
RUN_TABLE = "pi_whatsapp_live_clean_runs"

_STARTED = False
_LOCK = threading.Lock()
RUNTIME = {
    "status": "NOT_STARTED",
    "last_run_at": None,
    "last_error": None,
    "last_result": None,
}

DDL = [
    f"""CREATE TABLE IF NOT EXISTS {LEDGER_TABLE}(
        id BIGSERIAL PRIMARY KEY,
        source_entity_type TEXT NOT NULL,
        source_id TEXT NOT NULL,
        source_hash TEXT NOT NULL,
        classification TEXT NOT NULL,
        clean_status TEXT NOT NULL,
        target_table TEXT,
        target_id TEXT,
        reason TEXT,
        normalized_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        synced_at TIMESTAMPTZ,
        UNIQUE(source_entity_type,source_id,source_hash)
    )""",
    f"""CREATE INDEX IF NOT EXISTS idx_wa_live_clean_source
        ON {LEDGER_TABLE}(source_entity_type,source_id)""",
    f"""CREATE INDEX IF NOT EXISTS idx_wa_live_clean_status
        ON {LEDGER_TABLE}(clean_status,classification)""",
    f"""CREATE TABLE IF NOT EXISTS {RUN_TABLE}(
        id BIGSERIAL PRIMARY KEY,
        version TEXT NOT NULL,
        source_properties INTEGER NOT NULL DEFAULT 0,
        source_requirements INTEGER NOT NULL DEFAULT 0,
        properties_synced INTEGER NOT NULL DEFAULT 0,
        requirements_staged INTEGER NOT NULL DEFAULT 0,
        needs_review INTEGER NOT NULL DEFAULT 0,
        skipped_unchanged INTEGER NOT NULL DEFAULT 0,
        errors INTEGER NOT NULL DEFAULT 0,
        result JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
]

PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?([6-9]\d{9})(?!\d)")


def _main_engine():
    return phase5.create_main_engine()


def _wa_engine():
    try:
        import whatsapp_live_bridge as live
        return live.wa_engine
    except Exception:
        return None


def _table_exists(engine, table_name: str) -> bool:
    try:
        with engine.connect() as c:
            return bool(c.execute(text("""
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema='public' AND table_name=:n
            """), {"n": table_name}).first())
    except Exception:
        return False


def _columns(engine, table_name: str) -> List[str]:
    with engine.connect() as c:
        return [
            str(x)
            for x in c.execute(text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name=:n
                ORDER BY ordinal_position
            """), {"n": table_name}).scalars().all()
        ]


def _ensure_schema(engine):
    with engine.begin() as c:
        for ddl in DDL:
            c.execute(text(ddl))


def _safe(v: Any):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, dict):
        return {str(k): _safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_safe(x) for x in v]
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def _norm(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def _upper(v: Any) -> str:
    return _norm(v).upper()


def _hash_row(row: Dict[str, Any], cols: Sequence[str]) -> str:
    payload = {c: _safe(row.get(c)) for c in cols}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def _phones(*values: Any) -> List[str]:
    out = []
    seen = set()
    for value in values:
        for p in PHONE_RE.findall(str(value or "")):
            if p not in seen:
                seen.add(p)
                out.append(p)
    return out


def _tx(v: Any, raw: Any = None) -> Optional[str]:
    blob = _upper(v) + " " + _upper(raw)
    if any(x in blob for x in ("RENT", "LEASE", "TO LET")):
        return "RENT"
    if any(x in blob for x in ("SALE", "SELL", "PURCHASE", "BUY")):
        return "SALE"
    return None


def _location(row: Dict[str, Any]) -> Optional[str]:
    for k in ("locality", "location", "city"):
        v = _norm(row.get(k))
        if not v:
            continue
        loc = phase5.candidate_location(v)
        if loc:
            return loc

    raw = " ".join(
        _norm(row.get(k))
        for k in ("raw_text", "parent_message_text", "description", "address")
        if row.get(k)
    )

    if raw:
        loc = phase5.canonical_location(raw)
        if loc:
            return loc
        if "NORTH GOA" in phase5.norm(raw):
            return "NORTH GOA"
    return None


def _city(row: Dict[str, Any], locality: Optional[str]) -> Optional[str]:
    c = _norm(row.get("city"))
    if c:
        return c
    # Do not guess city from locality here. Preserve only direct source evidence.
    return None


def _float(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return None


def _fetch_batches(engine, table_name: str, batch_size: int = BATCH_SIZE):
    cols = _columns(engine, table_name)
    if not cols:
        return

    order_col = "id" if "id" in cols else (
        "wa_property_id" if "wa_property_id" in cols else (
            "wa_requirement_id" if "wa_requirement_id" in cols else cols[0]
        )
    )
    qcols = ", ".join('"' + c + '"' for c in cols)

    offset = 0
    while True:
        with engine.connect() as c:
            rows = [
                dict(r)
                for r in c.execute(
                    text(
                        f'SELECT {qcols} FROM "{table_name}" '
                        f'ORDER BY "{order_col}" ASC NULLS LAST '
                        'OFFSET :off LIMIT :lim'
                    ),
                    {"off": offset, "lim": batch_size},
                ).mappings().all()
            ]

        if not rows:
            break

        yield cols, rows
        offset += len(rows)

        if len(rows) < batch_size:
            break


def _source_count(engine, table_name: str) -> int:
    if not _table_exists(engine, table_name):
        return 0
    with engine.connect() as c:
        return int(c.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar() or 0)


def _already_done(main_engine, entity_type: str, source_id: str, source_hash: str) -> bool:
    with main_engine.connect() as c:
        return bool(c.execute(text(f"""
            SELECT 1
            FROM {LEDGER_TABLE}
            WHERE source_entity_type=:et
              AND source_id=:sid
              AND source_hash=:sh
              AND clean_status IN ('SYNCED','STAGED','NEEDS_REVIEW')
            LIMIT 1
        """), {"et": entity_type, "sid": source_id, "sh": source_hash}).first())


def _ledger(main_engine, *, entity_type: str, source_id: str, source_hash: str,
            classification: str, clean_status: str, target_table: Optional[str],
            target_id: Optional[str], reason: Optional[str], normalized: Dict[str, Any]):
    with main_engine.begin() as c:
        c.execute(text(f"""
            INSERT INTO {LEDGER_TABLE}(
                source_entity_type,source_id,source_hash,classification,
                clean_status,target_table,target_id,reason,normalized_json,
                first_seen_at,last_seen_at,synced_at
            )
            VALUES(
                :et,:sid,:sh,:cl,:cs,:tt,:tid,:reason,
                CAST(:norm AS JSONB),NOW(),NOW(),
                CASE WHEN :cs IN ('SYNCED','STAGED') THEN NOW() ELSE NULL END
            )
            ON CONFLICT(source_entity_type,source_id,source_hash)
            DO UPDATE SET
                classification=EXCLUDED.classification,
                clean_status=EXCLUDED.clean_status,
                target_table=EXCLUDED.target_table,
                target_id=EXCLUDED.target_id,
                reason=EXCLUDED.reason,
                normalized_json=EXCLUDED.normalized_json,
                last_seen_at=NOW(),
                synced_at=CASE
                    WHEN EXCLUDED.clean_status IN ('SYNCED','STAGED') THEN NOW()
                    ELSE {LEDGER_TABLE}.synced_at
                END
        """), {
            "et": entity_type,
            "sid": source_id,
            "sh": source_hash,
            "cl": classification,
            "cs": clean_status,
            "tt": target_table,
            "tid": target_id,
            "reason": reason,
            "norm": json.dumps(_safe(normalized), ensure_ascii=False, default=str),
        })


def _property_source_id(row: Dict[str, Any]) -> str:
    return _norm(row.get("wa_property_id") or row.get("id"))


def _requirement_source_id(row: Dict[str, Any]) -> str:
    return _norm(row.get("wa_requirement_id") or row.get("id"))


def _property_clean(row: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], str]:
    raw = _norm(row.get("raw_text") or row.get("parent_message_text"))
    tx = _tx(row.get("transaction_type"), raw)
    loc = _location(row)
    phones = _phones(
        row.get("owner_phone"),
        row.get("broker_phone"),
        row.get("sender_phone"),
        raw,
    )
    ptype = _norm(row.get("property_type"))
    area = _float(row.get("available_area_sqft") or row.get("area_sqft"))
    rent = _float(row.get("rent_inr"))
    sale = _float(row.get("sale_price_inr"))

    normalized = {
        "transaction_type": tx,
        "locality": loc,
        "city": _city(row, loc),
        "property_type": ptype or None,
        "area_sqft": area,
        "rent_inr": rent,
        "sale_price_inr": sale,
        "phones": phones,
        "raw_text": phase5.sanitize_text(raw),
        "source_wa_property_id": _property_source_id(row),
        "source_confidence": _float(row.get("confidence")),
    }

    if not tx:
        return False, normalized, "Transaction missing/unclear"
    if not loc:
        return False, normalized, "Location missing/unclear"
    if not phones:
        return False, normalized, "No usable contact number"
    if not (ptype or area or rent or sale):
        return False, normalized, "Insufficient property meaning"
    if "REQUIREMENT" in _upper(raw) or "LOOKING FOR" in _upper(raw):
        return False, normalized, "Message appears to be demand/requirement"

    return True, normalized, ""


def _sync_property(main_engine, row: Dict[str, Any], normalized: Dict[str, Any], source_hash: str):
    sid = _property_source_id(row)
    cid = "WA-LIVE-PROP-" + hashlib.sha256(sid.encode("utf-8")).hexdigest()[:24].upper()
    mid = "MP-" + hashlib.sha256(cid.encode("utf-8")).hexdigest()[:16].upper()

    tx = normalized["transaction_type"]
    price = normalized.get("rent_inr") if tx == "RENT" else normalized.get("sale_price_inr")
    price_kind = "RENT_AMOUNT" if tx == "RENT" else "SALE_AMOUNT"

    clean_record = {
        "whatsapp_live_clean": {
            "source_id": sid,
            "source_hash": source_hash,
            "source_table": "wa_properties",
            "verification_policy": "VERIFY_FIRST",
            "source_confidence": normalized.get("source_confidence"),
            "bridge_version": VERSION,
        },
        "property_type": normalized.get("property_type"),
        "original_message": normalized.get("raw_text"),
        "availability": "UNKNOWN",
    }

    with main_engine.begin() as c:
        c.execute(text("""
            INSERT INTO pi_master_properties_v711(
                master_property_id,canonical_id,source_type,transaction_type,
                locality,city,area_value,area_unit,area_sqft,
                price_raw,price_kind,phones,clean_record,source_count,
                promotion_status,source_version,created_at,updated_at
            )
            VALUES(
                :mid,:cid,'WHATSAPP_LIVE_CLEAN',:tx,
                :loc,:city,:area,'SQFT',:area,
                :price,:pk,CAST(:phones AS JSONB),CAST(:clean AS JSONB),1,
                'PROMOTED_VALIDATED',:ver,NOW(),NOW()
            )
            ON CONFLICT(canonical_id)
            DO UPDATE SET
                transaction_type=EXCLUDED.transaction_type,
                locality=EXCLUDED.locality,
                city=COALESCE(EXCLUDED.city,pi_master_properties_v711.city),
                area_value=COALESCE(EXCLUDED.area_value,pi_master_properties_v711.area_value),
                area_unit=CASE WHEN EXCLUDED.area_value IS NOT NULL THEN 'SQFT'
                               ELSE pi_master_properties_v711.area_unit END,
                area_sqft=COALESCE(EXCLUDED.area_sqft,pi_master_properties_v711.area_sqft),
                price_raw=COALESCE(EXCLUDED.price_raw,pi_master_properties_v711.price_raw),
                price_kind=COALESCE(EXCLUDED.price_kind,pi_master_properties_v711.price_kind),
                phones=CASE WHEN jsonb_array_length(EXCLUDED.phones)>0
                            THEN EXCLUDED.phones ELSE pi_master_properties_v711.phones END,
                clean_record=COALESCE(pi_master_properties_v711.clean_record,'{}'::jsonb)
                             || EXCLUDED.clean_record,
                source_count=GREATEST(pi_master_properties_v711.source_count,1),
                promotion_status='PROMOTED_VALIDATED',
                source_version=:ver,
                updated_at=NOW()
        """), {
            "mid": mid,
            "cid": cid,
            "tx": tx,
            "loc": normalized.get("locality"),
            "city": normalized.get("city"),
            "area": normalized.get("area_sqft"),
            "price": str(price) if price is not None else None,
            "pk": price_kind if price is not None else None,
            "phones": json.dumps(normalized.get("phones") or []),
            "clean": json.dumps(clean_record, ensure_ascii=False, default=str),
            "ver": VERSION,
        })

        # Operational visibility without claiming availability.
        c.execute(text("""
            INSERT INTO pi_master_workflow_v720(
                canonical_id,entity_type,verification_status,
                availability_status,updated_at
            )
            VALUES(:cid,'PROPERTY','UNVERIFIED','UNKNOWN',NOW())
            ON CONFLICT(canonical_id)
            DO UPDATE SET
                entity_type='PROPERTY',
                verification_status=CASE
                    WHEN pi_master_workflow_v720.verification_status='VERIFIED'
                    THEN 'VERIFIED' ELSE 'UNVERIFIED' END,
                availability_status=CASE
                    WHEN pi_master_workflow_v720.verification_status='VERIFIED'
                    THEN pi_master_workflow_v720.availability_status
                    ELSE 'UNKNOWN' END,
                updated_at=NOW()
        """), {"cid": cid})

        c.execute(text("""
            INSERT INTO pi_master_source_links_v711(
                master_entity_type,master_id,canonical_id,source_type,
                source_table,source_pk,source_row_hash
            )
            VALUES(
                'PROPERTY',:mid,:cid,'WHATSAPP_LIVE_CLEAN',
                'wa_properties',:sid,:sh
            )
            ON CONFLICT DO NOTHING
        """), {"mid": mid, "cid": cid, "sid": sid, "sh": source_hash})

        # Team action state if the table exists.
        if _table_exists(main_engine, "pi_master_action_state_v730"):
            c.execute(text("""
                INSERT INTO pi_master_action_state_v730(
                    canonical_id,entity_type,stage,review_status,updated_at
                )
                VALUES(:cid,'PROPERTY','VERIFY_FIRST','READY_FOR_REVIEW',NOW())
                ON CONFLICT(canonical_id) DO UPDATE SET
                    entity_type='PROPERTY',
                    stage=CASE
                        WHEN pi_master_action_state_v730.stage IN ('VERIFIED','UNAVAILABLE')
                        THEN pi_master_action_state_v730.stage
                        ELSE 'VERIFY_FIRST' END,
                    updated_at=NOW()
            """), {"cid": cid})

    return cid


def _requirement_clean(row: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], str]:
    raw = _norm(row.get("raw_text"))
    tx = _tx(row.get("transaction_type"), raw)
    locs = []

    preferred = row.get("preferred_locations")
    if isinstance(preferred, list):
        values = preferred
    elif preferred:
        txt = str(preferred)
        try:
            parsed = json.loads(txt)
            values = parsed if isinstance(parsed, list) else [txt]
        except Exception:
            values = re.split(r"[,;/|]+", txt)
    else:
        values = []

    for v in values:
        x = _norm(v)
        if x and x not in locs:
            locs.append(x)

    if not locs:
        loc = _location(row)
        if loc:
            locs.append(loc)

    phones = _phones(row.get("contact_phone"), raw)
    ptype = _norm(row.get("property_type") or row.get("suitable_category"))

    normalized = {
        "transaction_type": tx,
        "gate_transaction_type": "LEASE" if tx == "RENT" else "PURCHASE" if tx == "SALE" else None,
        "locations": locs,
        "property_type": ptype or None,
        "area_min_sqft": _float(row.get("minimum_area_sqft")),
        "area_max_sqft": _float(row.get("maximum_area_sqft")),
        "budget_min": _float(row.get("budget_min_inr")),
        "budget_max": _float(row.get("budget_max_inr")),
        "contact_name": _norm(row.get("contact_name")),
        "contact_numbers": phones,
        "raw_text": phase5.sanitize_text(raw),
        "source_wa_requirement_id": _requirement_source_id(row),
        "source_confidence": _float(row.get("confidence")),
    }

    if not raw:
        return False, normalized, "Requirement message text missing"

    positive = bool(re.search(
        r"\b(need|needed|require|required|requirement|looking\s+for|wanted|seeking|client)\b",
        raw,
        re.I,
    ))

    enough_structure = bool(tx or locs or ptype or normalized["area_min_sqft"] or normalized["budget_max"])
    if not (positive or enough_structure):
        return False, normalized, "Insufficient requirement meaning"

    return True, normalized, ""


def _stage_requirement(main_engine, row: Dict[str, Any], normalized: Dict[str, Any], source_hash: str):
    sid = _requirement_source_id(row)
    original = normalized.get("raw_text") or ""
    msg_hash = hashlib.sha256(_norm(original).lower().encode("utf-8")).hexdigest()
    evidence_key = f"WA-LIVE-REQ:{sid}:{source_hash[:20]}"

    complete = bool(
        normalized.get("gate_transaction_type")
        and normalized.get("locations")
        and normalized.get("property_type")
    )
    classification = "AI-QUALIFIED" if complete else "NEEDS VERIFICATION"
    conf = normalized.get("source_confidence")
    if conf is None:
        conf = 0.90 if complete else 0.70
    elif conf > 1:
        conf = conf / 100.0
    conf = max(0.0, min(float(conf), 0.99))

    with main_engine.begin() as c:
        c.execute(text("""
            INSERT INTO pi_requirement_gate_v1191(
                evidence_key,source_type,source_table,source_pk,source_group,
                source_date,original_message,message_hash,classification,
                genuine_confidence,rejection_reason,transaction_type,
                property_category,intended_use,locations,alternate_locations,
                area_min_sqft,area_max_sqft,budget_min,budget_max,
                company_brand_person,contact_numbers,extracted_fields,
                evidence_quality,matcher_eligible,created_at,updated_at
            )
            VALUES(
                :ek,'WHATSAPP_LIVE_CLEAN','wa_requirements',:sid,:sg,
                NOW(),:msg,:mh,:cl,:conf,NULL,:tx,
                :pc,:iu,CAST(:locs AS JSONB),'[]'::jsonb,
                :amin,:amax,:bmin,:bmax,:person,CAST(:phones AS JSONB),
                CAST(:fields AS JSONB),'LIVE_CLEAN',FALSE,NOW(),NOW()
            )
            ON CONFLICT(evidence_key)
            DO UPDATE SET
                original_message=EXCLUDED.original_message,
                message_hash=EXCLUDED.message_hash,
                genuine_confidence=GREATEST(
                    pi_requirement_gate_v1191.genuine_confidence,
                    EXCLUDED.genuine_confidence
                ),
                transaction_type=COALESCE(
                    pi_requirement_gate_v1191.transaction_type,
                    EXCLUDED.transaction_type
                ),
                property_category=COALESCE(
                    pi_requirement_gate_v1191.property_category,
                    EXCLUDED.property_category
                ),
                intended_use=COALESCE(
                    pi_requirement_gate_v1191.intended_use,
                    EXCLUDED.intended_use
                ),
                locations=CASE
                    WHEN jsonb_array_length(pi_requirement_gate_v1191.locations)>0
                    THEN pi_requirement_gate_v1191.locations
                    ELSE EXCLUDED.locations END,
                area_min_sqft=COALESCE(
                    pi_requirement_gate_v1191.area_min_sqft,
                    EXCLUDED.area_min_sqft
                ),
                area_max_sqft=COALESCE(
                    pi_requirement_gate_v1191.area_max_sqft,
                    EXCLUDED.area_max_sqft
                ),
                budget_min=COALESCE(
                    pi_requirement_gate_v1191.budget_min,
                    EXCLUDED.budget_min
                ),
                budget_max=COALESCE(
                    pi_requirement_gate_v1191.budget_max,
                    EXCLUDED.budget_max
                ),
                contact_numbers=CASE
                    WHEN jsonb_array_length(pi_requirement_gate_v1191.contact_numbers)>0
                    THEN pi_requirement_gate_v1191.contact_numbers
                    ELSE EXCLUDED.contact_numbers END,
                extracted_fields=COALESCE(
                    pi_requirement_gate_v1191.extracted_fields,'{}'::jsonb
                ) || EXCLUDED.extracted_fields,
                evidence_quality='LIVE_CLEAN',
                matcher_eligible=CASE
                    WHEN pi_requirement_gate_v1191.classification='VERIFIED ACTIVE'
                    THEN TRUE ELSE FALSE END,
                updated_at=NOW()
        """), {
            "ek": evidence_key,
            "sid": sid,
            "sg": _norm(row.get("source_id")) or "WHATSAPP_LIVE",
            "msg": original,
            "mh": msg_hash,
            "cl": classification,
            "conf": conf,
            "tx": normalized.get("gate_transaction_type"),
            "pc": normalized.get("property_type"),
            "iu": normalized.get("property_type"),
            "locs": json.dumps(normalized.get("locations") or []),
            "amin": normalized.get("area_min_sqft"),
            "amax": normalized.get("area_max_sqft"),
            "bmin": normalized.get("budget_min"),
            "bmax": normalized.get("budget_max"),
            "person": normalized.get("contact_name"),
            "phones": json.dumps(normalized.get("contact_numbers") or []),
            "fields": json.dumps({
                "whatsapp_live_clean": True,
                "source_wa_requirement_id": sid,
                "source_hash": source_hash,
                "bridge_version": VERSION,
                "verification_policy": "HUMAN_GATE_REQUIRED",
            }),
        })

        gid = c.execute(text("""
            SELECT id
            FROM pi_requirement_gate_v1191
            WHERE evidence_key=:ek
        """), {"ek": evidence_key}).scalar_one()

    return str(gid)


def _sync_properties(main_engine, source_engine, counters: Counter):
    if not _table_exists(source_engine, "wa_properties"):
        return

    for cols, rows in _fetch_batches(source_engine, "wa_properties"):
        for row in rows:
            sid = _property_source_id(row)
            if not sid:
                counters["errors"] += 1
                continue

            sh = _hash_row(row, cols)
            if _already_done(main_engine, "PROPERTY", sid, sh):
                counters["skipped_unchanged"] += 1
                continue

            try:
                ok, normalized, reason = _property_clean(row)

                if not ok:
                    _ledger(
                        main_engine,
                        entity_type="PROPERTY",
                        source_id=sid,
                        source_hash=sh,
                        classification="PROPERTY",
                        clean_status="NEEDS_REVIEW",
                        target_table=None,
                        target_id=None,
                        reason=reason,
                        normalized=normalized,
                    )
                    counters["needs_review"] += 1
                    continue

                cid = _sync_property(main_engine, row, normalized, sh)
                _ledger(
                    main_engine,
                    entity_type="PROPERTY",
                    source_id=sid,
                    source_hash=sh,
                    classification="PROPERTY",
                    clean_status="SYNCED",
                    target_table="pi_master_properties_v711",
                    target_id=cid,
                    reason="VERIFY_FIRST",
                    normalized=normalized,
                )
                counters["properties_synced"] += 1
            except Exception as exc:
                counters["errors"] += 1
                _ledger(
                    main_engine,
                    entity_type="PROPERTY",
                    source_id=sid,
                    source_hash=sh,
                    classification="PROPERTY",
                    clean_status="ERROR",
                    target_table=None,
                    target_id=None,
                    reason=f"{type(exc).__name__}: {exc}"[:800],
                    normalized={"source_id": sid},
                )


def _sync_requirements(main_engine, source_engine, counters: Counter):
    if not _table_exists(source_engine, "wa_requirements"):
        return

    for cols, rows in _fetch_batches(source_engine, "wa_requirements"):
        for row in rows:
            sid = _requirement_source_id(row)
            if not sid:
                counters["errors"] += 1
                continue

            sh = _hash_row(row, cols)
            if _already_done(main_engine, "REQUIREMENT", sid, sh):
                counters["skipped_unchanged"] += 1
                continue

            try:
                ok, normalized, reason = _requirement_clean(row)

                if not ok:
                    _ledger(
                        main_engine,
                        entity_type="REQUIREMENT",
                        source_id=sid,
                        source_hash=sh,
                        classification="REQUIREMENT",
                        clean_status="NEEDS_REVIEW",
                        target_table=None,
                        target_id=None,
                        reason=reason,
                        normalized=normalized,
                    )
                    counters["needs_review"] += 1
                    continue

                gid = _stage_requirement(main_engine, row, normalized, sh)
                _ledger(
                    main_engine,
                    entity_type="REQUIREMENT",
                    source_id=sid,
                    source_hash=sh,
                    classification="REQUIREMENT",
                    clean_status="STAGED",
                    target_table="pi_requirement_gate_v1191",
                    target_id=gid,
                    reason="HUMAN_VERIFICATION_REQUIRED",
                    normalized=normalized,
                )
                counters["requirements_staged"] += 1
            except Exception as exc:
                counters["errors"] += 1
                _ledger(
                    main_engine,
                    entity_type="REQUIREMENT",
                    source_id=sid,
                    source_hash=sh,
                    classification="REQUIREMENT",
                    clean_status="ERROR",
                    target_table=None,
                    target_id=None,
                    reason=f"{type(exc).__name__}: {exc}"[:800],
                    normalized={"source_id": sid},
                )


def run_sync() -> Dict[str, Any]:
    main_engine = _main_engine()
    source_engine = _wa_engine()

    if source_engine is None:
        raise RuntimeError("WHATSAPP_DATABASE_URL / WhatsApp live database is not configured")

    _ensure_schema(main_engine)

    source_properties = _source_count(source_engine, "wa_properties")
    source_requirements = _source_count(source_engine, "wa_requirements")

    counters = Counter()
    _sync_properties(main_engine, source_engine, counters)
    _sync_requirements(main_engine, source_engine, counters)

    result = {
        "version": VERSION,
        "source_properties": source_properties,
        "source_requirements": source_requirements,
        "properties_synced": counters["properties_synced"],
        "requirements_staged": counters["requirements_staged"],
        "needs_review": counters["needs_review"],
        "skipped_unchanged": counters["skipped_unchanged"],
        "errors": counters["errors"],
        "policy": {
            "lossless_source": True,
            "idempotent_source_hash": True,
            "properties_verify_first": True,
            "requirements_human_gate_required": True,
            "requirements_auto_matcher_eligible": False,
            "frozen_matcher_untouched": True,
            "recovery_confidence_frozen": True,
        },
    }

    with main_engine.begin() as c:
        c.execute(text(f"""
            INSERT INTO {RUN_TABLE}(
                version,source_properties,source_requirements,
                properties_synced,requirements_staged,needs_review,
                skipped_unchanged,errors,result
            )
            VALUES(
                :v,:sp,:sr,:ps,:rs,:nr,:su,:er,CAST(:result AS JSONB)
            )
        """), {
            "v": VERSION,
            "sp": source_properties,
            "sr": source_requirements,
            "ps": counters["properties_synced"],
            "rs": counters["requirements_staged"],
            "nr": counters["needs_review"],
            "su": counters["skipped_unchanged"],
            "er": counters["errors"],
            "result": json.dumps(result, ensure_ascii=False),
        })

    RUNTIME.update(
        status="OK" if counters["errors"] == 0 else "DEGRADED",
        last_run_at=datetime.now(timezone.utc).isoformat(),
        last_error=None,
        last_result=result,
    )
    return result


def audit_snapshot() -> Dict[str, Any]:
    main_engine = _main_engine()
    source_engine = _wa_engine()

    if source_engine is None:
        raise RuntimeError("WhatsApp live database unavailable")

    _ensure_schema(main_engine)

    sp = _source_count(source_engine, "wa_properties")
    sr = _source_count(source_engine, "wa_requirements")

    with main_engine.connect() as c:
        ledger_props = int(c.execute(text(f"""
            SELECT COUNT(DISTINCT source_id)
            FROM {LEDGER_TABLE}
            WHERE source_entity_type='PROPERTY'
        """)).scalar() or 0)

        ledger_reqs = int(c.execute(text(f"""
            SELECT COUNT(DISTINCT source_id)
            FROM {LEDGER_TABLE}
            WHERE source_entity_type='REQUIREMENT'
        """)).scalar() or 0)

        synced_props = int(c.execute(text(f"""
            SELECT COUNT(DISTINCT source_id)
            FROM {LEDGER_TABLE}
            WHERE source_entity_type='PROPERTY'
              AND clean_status='SYNCED'
        """)).scalar() or 0)

        staged_reqs = int(c.execute(text(f"""
            SELECT COUNT(DISTINCT source_id)
            FROM {LEDGER_TABLE}
            WHERE source_entity_type='REQUIREMENT'
              AND clean_status='STAGED'
        """)).scalar() or 0)

        live_master_props = int(c.execute(text("""
            SELECT COUNT(*)
            FROM pi_master_properties_v711
            WHERE source_type='WHATSAPP_LIVE_CLEAN'
        """)).scalar() or 0)

        live_gate_reqs = int(c.execute(text("""
            SELECT COUNT(*)
            FROM pi_requirement_gate_v1191
            WHERE source_type='WHATSAPP_LIVE_CLEAN'
        """)).scalar() or 0)

        bad_auto_verified_props = int(c.execute(text("""
            SELECT COUNT(*)
            FROM pi_master_properties_v711 p
            JOIN pi_master_workflow_v720 w
              ON w.canonical_id=p.canonical_id
            WHERE p.source_type='WHATSAPP_LIVE_CLEAN'
              AND w.verification_status='VERIFIED'
              AND w.verified_by IS NULL
        """)).scalar() or 0)

        unsafe_requirements = int(c.execute(text("""
            SELECT COUNT(*)
            FROM pi_requirement_gate_v1191
            WHERE source_type='WHATSAPP_LIVE_CLEAN'
              AND classification<>'VERIFIED ACTIVE'
              AND matcher_eligible=TRUE
        """)).scalar() or 0)

        duplicate_prop_targets = int(c.execute(text("""
            SELECT COUNT(*) FROM (
                SELECT canonical_id,COUNT(*) n
                FROM pi_master_properties_v711
                WHERE source_type='WHATSAPP_LIVE_CLEAN'
                GROUP BY canonical_id HAVING COUNT(*)>1
            ) q
        """)).scalar() or 0)

        duplicate_gate_evidence = int(c.execute(text("""
            SELECT COUNT(*) FROM (
                SELECT evidence_key,COUNT(*) n
                FROM pi_requirement_gate_v1191
                WHERE source_type='WHATSAPP_LIVE_CLEAN'
                GROUP BY evidence_key HAVING COUNT(*)>1
            ) q
        """)).scalar() or 0)

    return {
        "version": VERSION,
        "source": {
            "wa_properties": sp,
            "wa_requirements": sr,
        },
        "coverage": {
            "property_source_ids_in_ledger": ledger_props,
            "requirement_source_ids_in_ledger": ledger_reqs,
            "property_backlog": max(sp - ledger_props, 0),
            "requirement_backlog": max(sr - ledger_reqs, 0),
        },
        "operational": {
            "synced_properties": synced_props,
            "master_live_properties": live_master_props,
            "staged_requirements": staged_reqs,
            "gate_live_requirements": live_gate_reqs,
        },
        "safety": {
            "properties_auto_verified_without_actor": bad_auto_verified_props,
            "unverified_requirements_matcher_eligible": unsafe_requirements,
            "duplicate_property_targets": duplicate_prop_targets,
            "duplicate_requirement_evidence": duplicate_gate_evidence,
        },
    }


def _worker():
    while True:
        try:
            run_sync()
        except Exception as exc:
            RUNTIME.update(
                status="ERROR",
                last_error=f"{type(exc).__name__}: {exc}",
                last_run_at=datetime.now(timezone.utc).isoformat(),
            )
            print("WhatsApp Live Clean OS sync error:", repr(exc))
        time.sleep(POLL_SECONDS)


def start_worker():
    global _STARTED

    with _LOCK:
        if _STARTED:
            return False
        _STARTED = True

    t = threading.Thread(
        target=_worker,
        name="whatsapp-live-clean-os",
        daemon=True,
    )
    t.start()
    return True


def register(core):
    app = getattr(core, "app", None) or core
    router = APIRouter()

    @router.get("/api/whatsapp-live-clean-os/status")
    def status():
        try:
            audit = audit_snapshot()
        except Exception as exc:
            audit = {"error": f"{type(exc).__name__}: {exc}"}
        return {
            "status": RUNTIME.get("status"),
            "version": VERSION,
            "runtime": dict(RUNTIME),
            "audit": audit,
        }

    @router.post("/api/whatsapp-live-clean-os/run")
    def run_now():
        return run_sync()

    app.include_router(router)
    started = start_worker()

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "worker_started": started,
        "poll_seconds": POLL_SECONDS,
    }


if __name__ == "__main__":
    print(json.dumps(run_sync(), indent=2, default=str))
