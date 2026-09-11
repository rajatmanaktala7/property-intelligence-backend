from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastapi import APIRouter
from sqlalchemy import text

import alliance_phase5_canonical_matcher as phase5

VERSION = "1.4.0-MASTER-LIVE-UNIFIED-OPERATIONAL-BRIDGE"
POLL_SECONDS = 90
BATCH_SIZE = 2500

LEDGER_TABLE = "pi_whatsapp_live_clean_ledger"
RUN_TABLE = "pi_whatsapp_live_clean_runs"
CURSOR_TABLE = "pi_whatsapp_live_clean_cursor"

MASTER_TABLE = "pi_whatsapp_property_master"
MASTER_PROPERTIES = "pi_master_properties_v711"
MASTER_WORKFLOW = "pi_master_workflow_v720"
MASTER_LINKS = "pi_master_source_links_v711"
REQ_GATE = "pi_requirement_gate_v1191"

_STARTED = False
_LOCK = threading.Lock()

RUNTIME = {
    "status": "NOT_STARTED",
    "last_run_at": None,
    "last_error": None,
    "last_result": None,
}

PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?([6-9]\d{9})(?!\d)")

DDL = [
    f"""CREATE TABLE IF NOT EXISTS {LEDGER_TABLE}(
        id BIGSERIAL PRIMARY KEY,
        source_entity_type TEXT NOT NULL,
        source_table TEXT NOT NULL,
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
        UNIQUE(source_entity_type,source_table,source_id,source_hash)
    )""",
    f"""CREATE INDEX IF NOT EXISTS idx_wa_clean_ledger_source
        ON {LEDGER_TABLE}(source_entity_type,source_table,source_id)""",
    f"""CREATE INDEX IF NOT EXISTS idx_wa_clean_ledger_status
        ON {LEDGER_TABLE}(clean_status,classification)""",
    f"""CREATE TABLE IF NOT EXISTS {CURSOR_TABLE}(
        source_table TEXT PRIMARY KEY,
        last_numeric_id BIGINT NOT NULL DEFAULT 0,
        last_run_at TIMESTAMPTZ,
        details JSONB NOT NULL DEFAULT '{{}}'::jsonb
    )""",
    f"""CREATE TABLE IF NOT EXISTS {RUN_TABLE}(
        id BIGSERIAL PRIMARY KEY,
        version TEXT NOT NULL,
        result JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
]


def _main_engine():
    return phase5.create_main_engine()


def _wa_engine():
    try:
        import whatsapp_live_bridge as live
        return live.wa_engine
    except Exception:
        return None


def _table_exists(engine, name: str) -> bool:
    try:
        with engine.connect() as c:
            return bool(c.execute(text("""
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema='public' AND table_name=:n
            """), {"n": name}).first())
    except Exception:
        return False


def _columns(engine, name: str) -> List[str]:
    if not _table_exists(engine, name):
        return []
    with engine.connect() as c:
        return [
            str(x)
            for x in c.execute(text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name=:n
                ORDER BY ordinal_position
            """), {"n": name}).scalars().all()
        ]


def _ensure_schema(engine):
    with engine.begin() as c:
        for stmt in DDL:
            c.execute(text(stmt))


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


def _float(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return None


def _phones(*values: Any) -> List[str]:
    out = []
    seen = set()
    for value in values:
        for p in PHONE_RE.findall(str(value or "")):
            if p not in seen:
                seen.add(p)
                out.append(p)
    return out


def _tx(*values: Any) -> Optional[str]:
    blob = " ".join(_upper(v) for v in values if v not in (None, ""))
    if not blob:
        return None
    sale = any(x in blob for x in ("SALE", "RESALE", "SELL", "PURCHASE", "BUY"))
    rent = any(x in blob for x in ("RENT", "LEASE", "TO LET", "TOLET"))
    if sale and not rent:
        return "SALE"
    if rent and not sale:
        return "RENT"
    if sale and rent:
        explicit = phase5.canonical_transaction(blob)
        if explicit in {"SALE", "RENT"}:
            return explicit
    return None


def _hash_payload(row: Dict[str, Any], keys: Sequence[str]) -> str:
    payload = {k: _safe(row.get(k)) for k in keys}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def _location_from_master(row: Dict[str, Any]) -> Optional[str]:
    blob = " ".join(
        _norm(row.get(k))
        for k in (
            "description",
            "configuration_details",
            "raw_message",
            "source",
        )
        if row.get(k)
    )
    if blob:
        locs = phase5.canonical_locations(blob)
        if locs:
            return locs[0]
        if "NORTH GOA" in phase5.norm(blob):
            return "NORTH GOA"

    for k in ("description", "configuration_details"):
        v = row.get(k)
        if v:
            loc = phase5.candidate_location(v)
            if loc:
                return loc

    return None


def _recovery_map(engine, record_ids: Sequence[str]) -> Dict[str, str]:
    if not record_ids or not _table_exists(engine, "pi_whatsapp_location_promotion_queue"):
        return {}
    sql = text("""
        SELECT DISTINCT ON (record_id)
            record_id,recovered_location
        FROM pi_whatsapp_location_promotion_queue
        WHERE record_id = ANY(:ids)
          AND confidence_score >= 90
          AND promotion_eligible = TRUE
          AND recovered_location IS NOT NULL
        ORDER BY record_id,updated_at DESC,id DESC
    """)
    with engine.connect() as c:
        rows = c.execute(sql, {"ids": list(record_ids)}).mappings().all()
    return {
        str(r["record_id"]): str(r["recovered_location"])
        for r in rows
        if r.get("record_id") and r.get("recovered_location")
    }


def _master_property_clean(row: Dict[str, Any], recovered_location: Optional[str] = None):
    tx = _tx(row.get("lead_type"), row.get("description"), row.get("raw_message"))
    loc = _location_from_master(row) or recovered_location
    phones = _phones(
        row.get("phone_numbers"),
        row.get("contact_name_number"),
        row.get("all_contacts"),
        row.get("raw_message"),
    )
    area = phase5.area_to_sqft(row.get("area"))
    fam, subtype = phase5.family_subtype(
        row.get("configuration_details"),
        row.get("description"),
        row.get("raw_message"),
    )
    raw = phase5.sanitize_text(
        row.get("raw_message")
        or row.get("description")
        or row.get("configuration_details")
        or ""
    )
    ptxt = row.get("price")
    price = phase5.money_value(ptxt)

    normalized = {
        "transaction_type": tx,
        "locality": loc,
        "family": fam,
        "subtype": subtype,
        "area_sqft": area,
        "price": price,
        "price_text": phase5.sanitize_text(ptxt),
        "phones": phones,
        "raw_text": raw,
        "verification": _norm(row.get("verification")) or "Unverified",
        "record_id": _norm(row.get("record_id")),
        "canonical_key": _norm(row.get("canonical_key")),
        "source": phase5.sanitize_text(row.get("source") or "WhatsApp Master"),
        "captured_on": _safe(row.get("captured_on")),
        "location_source": "DIRECT_MASTER" if _location_from_master(row) else (
            "HIGH_CONFIDENCE_RECOVERY" if recovered_location else None
        ),
    }

    if tx not in {"SALE", "RENT"}:
        return False, normalized, "Transaction missing/unclear"
    if not loc:
        return False, normalized, "Location missing/unclear"
    if area is None:
        return False, normalized, "Area missing/unusable"
    if not phones:
        return False, normalized, "Contact missing"
    if not fam:
        return False, normalized, "Property family missing/unclear"
    if len(raw) < 8:
        return False, normalized, "Property description too weak"

    return True, normalized, ""


def _master_source_key(row: Dict[str, Any]) -> str:
    return _norm(row.get("canonical_key") or row.get("record_id") or row.get("id"))


def _canonical_id_for_master(row: Dict[str, Any]) -> str:
    stable = _master_source_key(row)
    return "WA-MASTER-PROP-" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:24].upper()


def _master_id(cid: str) -> str:
    return "MP-" + hashlib.sha256(cid.encode("utf-8")).hexdigest()[:16].upper()


def _price_fields(tx: str, normalized: Dict[str, Any]):
    price = normalized.get("price")
    if price is None:
        return None, None
    return str(price), "RENT_AMOUNT" if tx == "RENT" else "SALE_AMOUNT"


def _sync_master_property(engine, row: Dict[str, Any], normalized: Dict[str, Any], source_hash: str):
    cid = _canonical_id_for_master(row)
    mid = _master_id(cid)
    tx = normalized["transaction_type"]
    price_raw, price_kind = _price_fields(tx, normalized)

    clean = {
        "whatsapp_master_clean": {
            "source_table": MASTER_TABLE,
            "source_pk": str(row.get("id") or ""),
            "record_id": row.get("record_id"),
            "canonical_key": row.get("canonical_key"),
            "source_hash": source_hash,
            "location_source": normalized.get("location_source"),
            "verification_policy": "VERIFY_FIRST",
            "bridge_version": VERSION,
        },
        "original_message": normalized.get("raw_text"),
        "property_type": normalized.get("subtype") or normalized.get("family"),
        "family": normalized.get("family"),
        "subtype": normalized.get("subtype"),
        "source": normalized.get("source"),
        "source_verification": normalized.get("verification"),
    }

    with engine.begin() as c:
        c.execute(text(f"""
            INSERT INTO {MASTER_PROPERTIES}(
                master_property_id,canonical_id,source_type,transaction_type,
                locality,city,area_value,area_unit,area_sqft,
                price_raw,price_kind,phones,clean_record,source_count,
                promotion_status,source_version,created_at,updated_at
            )
            VALUES(
                :mid,:cid,'WHATSAPP_MASTER_CLEAN',:tx,
                :loc,NULL,:area,'SQFT',:area,
                :price,:pk,CAST(:phones AS JSONB),CAST(:clean AS JSONB),1,
                'PROMOTED_VALIDATED',:ver,NOW(),NOW()
            )
            ON CONFLICT(canonical_id) DO UPDATE SET
                transaction_type=EXCLUDED.transaction_type,
                locality=EXCLUDED.locality,
                area_value=EXCLUDED.area_value,
                area_unit='SQFT',
                area_sqft=EXCLUDED.area_sqft,
                price_raw=COALESCE(EXCLUDED.price_raw,{MASTER_PROPERTIES}.price_raw),
                price_kind=COALESCE(EXCLUDED.price_kind,{MASTER_PROPERTIES}.price_kind),
                phones=CASE WHEN jsonb_array_length(EXCLUDED.phones)>0
                            THEN EXCLUDED.phones ELSE {MASTER_PROPERTIES}.phones END,
                clean_record=COALESCE({MASTER_PROPERTIES}.clean_record,'{{}}'::jsonb)
                             || EXCLUDED.clean_record,
                source_count=GREATEST({MASTER_PROPERTIES}.source_count,1),
                promotion_status='PROMOTED_VALIDATED',
                source_version=:ver,
                updated_at=NOW()
        """), {
            "mid": mid,
            "cid": cid,
            "tx": tx,
            "loc": normalized["locality"],
            "area": normalized["area_sqft"],
            "price": price_raw,
            "pk": price_kind,
            "phones": json.dumps(normalized.get("phones") or []),
            "clean": json.dumps(clean, ensure_ascii=False, default=str),
            "ver": VERSION,
        })

        c.execute(text(f"""
            INSERT INTO {MASTER_WORKFLOW}(
                canonical_id,entity_type,verification_status,
                availability_status,updated_at
            )
            VALUES(:cid,'PROPERTY','UNVERIFIED','UNKNOWN',NOW())
            ON CONFLICT(canonical_id) DO UPDATE SET
                entity_type='PROPERTY',
                verification_status=CASE
                    WHEN {MASTER_WORKFLOW}.verification_status='VERIFIED'
                    THEN 'VERIFIED' ELSE 'UNVERIFIED' END,
                availability_status=CASE
                    WHEN {MASTER_WORKFLOW}.verification_status='VERIFIED'
                    THEN {MASTER_WORKFLOW}.availability_status
                    ELSE 'UNKNOWN' END,
                updated_at=NOW()
        """), {"cid": cid})

        c.execute(text(f"""
            INSERT INTO {MASTER_LINKS}(
                master_entity_type,master_id,canonical_id,source_type,
                source_table,source_pk,source_row_hash
            )
            VALUES(
                'PROPERTY',:mid,:cid,'WHATSAPP_MASTER_CLEAN',
                :st,:spk,:sh
            )
            ON CONFLICT DO NOTHING
        """), {
            "mid": mid,
            "cid": cid,
            "st": MASTER_TABLE,
            "spk": str(row.get("id") or row.get("record_id") or ""),
            "sh": source_hash,
        })

        if _table_exists(engine, "pi_master_action_state_v730"):
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
                    review_status=CASE
                        WHEN pi_master_action_state_v730.stage IN ('VERIFIED','UNAVAILABLE')
                        THEN pi_master_action_state_v730.review_status
                        ELSE 'READY_FOR_REVIEW' END,
                    updated_at=NOW()
            """), {"cid": cid})

    return cid


def _ledger(engine, *, entity_type: str, source_table: str, source_id: str,
            source_hash: str, classification: str, clean_status: str,
            target_table: Optional[str], target_id: Optional[str],
            reason: Optional[str], normalized: Dict[str, Any]):
    with engine.begin() as c:
        c.execute(text(f"""
            INSERT INTO {LEDGER_TABLE}(
                source_entity_type,source_table,source_id,source_hash,
                classification,clean_status,target_table,target_id,reason,
                normalized_json,first_seen_at,last_seen_at,synced_at
            )
            VALUES(
                :et,:st,:sid,:sh,:cl,:cs,:tt,:tid,:reason,
                CAST(:norm AS JSONB),NOW(),NOW(),
                CASE WHEN :cs IN ('SYNCED','STAGED') THEN NOW() ELSE NULL END
            )
            ON CONFLICT(source_entity_type,source_table,source_id,source_hash)
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
            "st": source_table,
            "sid": source_id,
            "sh": source_hash,
            "cl": classification,
            "cs": clean_status,
            "tt": target_table,
            "tid": target_id,
            "reason": reason,
            "norm": json.dumps(_safe(normalized), ensure_ascii=False, default=str),
        })


def _already_accounted(engine, entity_type: str, source_table: str,
                       source_id: str, source_hash: str) -> bool:
    with engine.connect() as c:
        return bool(c.execute(text(f"""
            SELECT 1
            FROM {LEDGER_TABLE}
            WHERE source_entity_type=:et
              AND source_table=:st
              AND source_id=:sid
              AND source_hash=:sh
              AND clean_status IN ('SYNCED','STAGED','NEEDS_REVIEW')
            LIMIT 1
        """), {
            "et": entity_type,
            "st": source_table,
            "sid": source_id,
            "sh": source_hash,
        }).first())


def _get_cursor(engine, source_table: str) -> int:
    with engine.connect() as c:
        return int(c.execute(text(f"""
            SELECT last_numeric_id
            FROM {CURSOR_TABLE}
            WHERE source_table=:st
        """), {"st": source_table}).scalar() or 0)


def _set_cursor(engine, source_table: str, last_id: int, details: Dict[str, Any]):
    with engine.begin() as c:
        c.execute(text(f"""
            INSERT INTO {CURSOR_TABLE}(source_table,last_numeric_id,last_run_at,details)
            VALUES(:st,:lid,NOW(),CAST(:d AS JSONB))
            ON CONFLICT(source_table) DO UPDATE SET
                last_numeric_id=GREATEST({CURSOR_TABLE}.last_numeric_id,EXCLUDED.last_numeric_id),
                last_run_at=NOW(),
                details=EXCLUDED.details
        """), {
            "st": source_table,
            "lid": int(last_id),
            "d": json.dumps(_safe(details), ensure_ascii=False),
        })


def _master_columns(engine) -> List[str]:
    wanted = [
        "id","generation_id","canonical_key","record_id","lead_type",
        "description","area","configuration_details","price",
        "contact_name_number","contact_name","phone_numbers",
        "source","source_count","all_contacts","all_sources",
        "captured_on","verification","raw_message","furnishing","floor",
        "created_at",
    ]
    cols = set(_columns(engine, MASTER_TABLE))
    return [x for x in wanted if x in cols]


def _fetch_master_batch(engine, after_id: int, limit: int):
    cols = _master_columns(engine)
    if "id" not in cols:
        return cols, []
    qcols = ", ".join('"' + x + '"' for x in cols)
    with engine.connect() as c:
        rows = [
            dict(r)
            for r in c.execute(text(
                f'SELECT {qcols} FROM "{MASTER_TABLE}" '
                'WHERE id>:after ORDER BY id ASC LIMIT :lim'
            ), {"after": int(after_id), "lim": int(limit)}).mappings().all()
        ]
    return cols, rows


def _sync_master_backlog(engine, counters: Counter, full_replay: bool = False):
    if not _table_exists(engine, MASTER_TABLE):
        counters["master_missing"] += 1
        return

    cursor = 0 if full_replay else _get_cursor(engine, MASTER_TABLE)
    processed_this_run = 0

    while True:
        cols, rows = _fetch_master_batch(engine, cursor, BATCH_SIZE)
        if not rows:
            break

        ids = [str(r.get("record_id") or "") for r in rows if r.get("record_id")]
        recovery = _recovery_map(engine, ids)

        for row in rows:
            row_id = int(row.get("id") or 0)
            cursor = max(cursor, row_id)
            sid = _master_source_key(row)
            if not sid:
                counters["master_errors"] += 1
                continue

            sh = _hash_payload(row, cols)

            if _already_accounted(engine, "PROPERTY", MASTER_TABLE, sid, sh):
                counters["master_unchanged"] += 1
                continue

            try:
                recovered = recovery.get(str(row.get("record_id") or ""))
                ok, normalized, reason = _master_property_clean(row, recovered)

                if not ok:
                    _ledger(
                        engine,
                        entity_type="PROPERTY",
                        source_table=MASTER_TABLE,
                        source_id=sid,
                        source_hash=sh,
                        classification="PROPERTY",
                        clean_status="NEEDS_REVIEW",
                        target_table=None,
                        target_id=None,
                        reason=reason,
                        normalized=normalized,
                    )
                    counters["master_review"] += 1
                    continue

                cid = _sync_master_property(engine, row, normalized, sh)
                _ledger(
                    engine,
                    entity_type="PROPERTY",
                    source_table=MASTER_TABLE,
                    source_id=sid,
                    source_hash=sh,
                    classification="PROPERTY",
                    clean_status="SYNCED",
                    target_table=MASTER_PROPERTIES,
                    target_id=cid,
                    reason="VERIFY_FIRST",
                    normalized=normalized,
                )
                counters["master_synced"] += 1
            except Exception as exc:
                counters["master_errors"] += 1
                _ledger(
                    engine,
                    entity_type="PROPERTY",
                    source_table=MASTER_TABLE,
                    source_id=sid,
                    source_hash=sh,
                    classification="PROPERTY",
                    clean_status="ERROR",
                    target_table=None,
                    target_id=None,
                    reason=f"{type(exc).__name__}: {exc}"[:1000],
                    normalized={"record_id": row.get("record_id"), "id": row.get("id")},
                )

        processed_this_run += len(rows)
        _set_cursor(engine, MASTER_TABLE, cursor, {
            "version": VERSION,
            "processed_this_run": processed_this_run,
        })
        print(
            f"Master projection: processed={processed_this_run} cursor={cursor}",
            flush=True,
        )

        if len(rows) < BATCH_SIZE:
            break


def _live_property_clean(row: Dict[str, Any]):
    raw = _norm(row.get("raw_text") or row.get("parent_message_text"))
    tx = _tx(row.get("transaction_type"), raw)
    loc = None

    for k in ("locality", "location", "city"):
        v = _norm(row.get(k))
        if v:
            loc = phase5.candidate_location(v)
            if loc:
                break

    if not loc and raw:
        locs = phase5.canonical_locations(raw)
        loc = locs[0] if locs else None

    phones = _phones(
        row.get("owner_phone"),
        row.get("broker_phone"),
        row.get("sender_phone"),
        raw,
    )

    ptype = _norm(row.get("property_type"))
    area = _float(row.get("available_area_sqft") or row.get("area_sqft"))
    fam, subtype = phase5.family_subtype(ptype, raw)
    rent = _float(row.get("rent_inr"))
    sale = _float(row.get("sale_price_inr"))

    normalized = {
        "transaction_type": tx,
        "locality": loc,
        "family": fam,
        "subtype": subtype,
        "area_sqft": area,
        "rent_inr": rent,
        "sale_price_inr": sale,
        "phones": phones,
        "raw_text": phase5.sanitize_text(raw),
    }

    if tx not in {"SALE", "RENT"}:
        return False, normalized, "Transaction missing/unclear"
    if not loc:
        return False, normalized, "Location missing/unclear"
    if not phones:
        return False, normalized, "Contact missing"
    if area is None:
        return False, normalized, "Area missing/unusable"
    if not fam:
        return False, normalized, "Property family missing/unclear"

    return True, normalized, ""


def _live_source_id(row: Dict[str, Any]) -> str:
    return _norm(row.get("wa_property_id") or row.get("id"))


def _sync_live_property(engine, row: Dict[str, Any], normalized: Dict[str, Any], source_hash: str):
    sid = _live_source_id(row)
    cid = "WA-LIVE-PROP-" + hashlib.sha256(sid.encode("utf-8")).hexdigest()[:24].upper()
    mid = _master_id(cid)
    tx = normalized["transaction_type"]
    price = normalized.get("rent_inr") if tx == "RENT" else normalized.get("sale_price_inr")

    clean = {
        "whatsapp_live_clean": {
            "source_table": "wa_properties",
            "source_id": sid,
            "source_hash": source_hash,
            "verification_policy": "VERIFY_FIRST",
            "bridge_version": VERSION,
        },
        "original_message": normalized.get("raw_text"),
        "family": normalized.get("family"),
        "subtype": normalized.get("subtype"),
    }

    with engine.begin() as c:
        c.execute(text(f"""
            INSERT INTO {MASTER_PROPERTIES}(
                master_property_id,canonical_id,source_type,transaction_type,
                locality,city,area_value,area_unit,area_sqft,
                price_raw,price_kind,phones,clean_record,source_count,
                promotion_status,source_version,created_at,updated_at
            )
            VALUES(
                :mid,:cid,'WHATSAPP_LIVE_CLEAN',:tx,
                :loc,NULL,:area,'SQFT',:area,
                :price,:pk,CAST(:phones AS JSONB),CAST(:clean AS JSONB),1,
                'PROMOTED_VALIDATED',:ver,NOW(),NOW()
            )
            ON CONFLICT(canonical_id) DO UPDATE SET
                transaction_type=EXCLUDED.transaction_type,
                locality=EXCLUDED.locality,
                area_value=EXCLUDED.area_value,
                area_unit='SQFT',
                area_sqft=EXCLUDED.area_sqft,
                price_raw=COALESCE(EXCLUDED.price_raw,{MASTER_PROPERTIES}.price_raw),
                price_kind=COALESCE(EXCLUDED.price_kind,{MASTER_PROPERTIES}.price_kind),
                phones=CASE WHEN jsonb_array_length(EXCLUDED.phones)>0
                            THEN EXCLUDED.phones ELSE {MASTER_PROPERTIES}.phones END,
                clean_record=COALESCE({MASTER_PROPERTIES}.clean_record,'{{}}'::jsonb)
                             || EXCLUDED.clean_record,
                promotion_status='PROMOTED_VALIDATED',
                source_version=:ver,
                updated_at=NOW()
        """), {
            "mid": mid,
            "cid": cid,
            "tx": tx,
            "loc": normalized["locality"],
            "area": normalized["area_sqft"],
            "price": str(price) if price is not None else None,
            "pk": ("RENT_AMOUNT" if tx == "RENT" else "SALE_AMOUNT") if price is not None else None,
            "phones": json.dumps(normalized.get("phones") or []),
            "clean": json.dumps(clean, ensure_ascii=False),
            "ver": VERSION,
        })

        c.execute(text(f"""
            INSERT INTO {MASTER_WORKFLOW}(
                canonical_id,entity_type,verification_status,availability_status,updated_at
            )
            VALUES(:cid,'PROPERTY','UNVERIFIED','UNKNOWN',NOW())
            ON CONFLICT(canonical_id) DO UPDATE SET
                entity_type='PROPERTY',
                verification_status=CASE
                    WHEN {MASTER_WORKFLOW}.verification_status='VERIFIED'
                    THEN 'VERIFIED' ELSE 'UNVERIFIED' END,
                availability_status=CASE
                    WHEN {MASTER_WORKFLOW}.verification_status='VERIFIED'
                    THEN {MASTER_WORKFLOW}.availability_status
                    ELSE 'UNKNOWN' END,
                updated_at=NOW()
        """), {"cid": cid})

        c.execute(text(f"""
            INSERT INTO {MASTER_LINKS}(
                master_entity_type,master_id,canonical_id,source_type,
                source_table,source_pk,source_row_hash
            )
            VALUES(
                'PROPERTY',:mid,:cid,'WHATSAPP_LIVE_CLEAN',
                'wa_properties',:sid,:sh
            )
            ON CONFLICT DO NOTHING
        """), {"mid": mid, "cid": cid, "sid": sid, "sh": source_hash})

    return cid


def _fetch_all_rows(engine, table_name: str):
    cols = _columns(engine, table_name)
    if not cols:
        return
    order_col = "id" if "id" in cols else (
        "wa_property_id" if "wa_property_id" in cols else (
            "wa_requirement_id" if "wa_requirement_id" in cols else cols[0]
        )
    )
    qcols = ", ".join('"' + x + '"' for x in cols)
    offset = 0
    while True:
        with engine.connect() as c:
            rows = [
                dict(r)
                for r in c.execute(text(
                    f'SELECT {qcols} FROM "{table_name}" '
                    f'ORDER BY "{order_col}" ASC NULLS LAST '
                    'OFFSET :off LIMIT :lim'
                ), {"off": offset, "lim": BATCH_SIZE}).mappings().all()
            ]
        if not rows:
            break
        yield cols, rows
        offset += len(rows)
        if len(rows) < BATCH_SIZE:
            break


def _sync_live_properties(main_engine, wa_engine, counters: Counter):
    if wa_engine is None or not _table_exists(wa_engine, "wa_properties"):
        return

    for cols, rows in _fetch_all_rows(wa_engine, "wa_properties"):
        for row in rows:
            sid = _live_source_id(row)
            if not sid:
                counters["live_errors"] += 1
                continue
            sh = _hash_payload(row, cols)

            if _already_accounted(main_engine, "PROPERTY", "wa_properties", sid, sh):
                counters["live_unchanged"] += 1
                continue

            try:
                ok, normalized, reason = _live_property_clean(row)
                if not ok:
                    _ledger(
                        main_engine,
                        entity_type="PROPERTY",
                        source_table="wa_properties",
                        source_id=sid,
                        source_hash=sh,
                        classification="PROPERTY",
                        clean_status="NEEDS_REVIEW",
                        target_table=None,
                        target_id=None,
                        reason=reason,
                        normalized=normalized,
                    )
                    counters["live_review"] += 1
                    continue

                cid = _sync_live_property(main_engine, row, normalized, sh)
                _ledger(
                    main_engine,
                    entity_type="PROPERTY",
                    source_table="wa_properties",
                    source_id=sid,
                    source_hash=sh,
                    classification="PROPERTY",
                    clean_status="SYNCED",
                    target_table=MASTER_PROPERTIES,
                    target_id=cid,
                    reason="VERIFY_FIRST",
                    normalized=normalized,
                )
                counters["live_synced"] += 1
            except Exception as exc:
                counters["live_errors"] += 1
                _ledger(
                    main_engine,
                    entity_type="PROPERTY",
                    source_table="wa_properties",
                    source_id=sid,
                    source_hash=sh,
                    classification="PROPERTY",
                    clean_status="ERROR",
                    target_table=None,
                    target_id=None,
                    reason=f"{type(exc).__name__}: {exc}"[:1000],
                    normalized={"source_id": sid},
                )


def _requirement_source_id(row: Dict[str, Any]) -> str:
    return _norm(row.get("wa_requirement_id") or row.get("id"))


def _requirement_clean(row: Dict[str, Any]):
    raw = _norm(row.get("raw_text"))
    tx = _tx(row.get("transaction_type"), raw)

    preferred = row.get("preferred_locations")
    if isinstance(preferred, list):
        vals = preferred
    elif preferred:
        try:
            x = json.loads(str(preferred))
            vals = x if isinstance(x, list) else [preferred]
        except Exception:
            vals = re.split(r"[,;/|]+", str(preferred))
    else:
        vals = []

    locs = []
    for v in vals:
        x = _norm(v)
        if not x:
            continue
        loc = phase5.candidate_location(x) or x
        if loc not in locs:
            locs.append(loc)

    if not locs and raw:
        locs = phase5.canonical_locations(raw)

    phones = _phones(row.get("contact_phone"), raw)
    ptype = _norm(row.get("property_type") or row.get("suitable_category"))
    fam, subtype = phase5.family_subtype(ptype, raw)

    normalized = {
        "transaction_type": tx,
        "gate_transaction_type": "LEASE" if tx == "RENT" else "PURCHASE" if tx == "SALE" else None,
        "locations": locs,
        "property_type": subtype or ptype or fam,
        "area_min_sqft": _float(row.get("minimum_area_sqft")),
        "area_max_sqft": _float(row.get("maximum_area_sqft")),
        "budget_min": _float(row.get("budget_min_inr")),
        "budget_max": _float(row.get("budget_max_inr")),
        "contact_name": _norm(row.get("contact_name")),
        "contact_numbers": phones,
        "raw_text": phase5.sanitize_text(raw),
        "source_confidence": _float(row.get("confidence")),
    }

    if not raw:
        return False, normalized, "Requirement text missing"
    if not any([tx, locs, normalized["property_type"],
                normalized["area_min_sqft"], normalized["budget_max"]]):
        return False, normalized, "Insufficient requirement meaning"

    return True, normalized, ""


def _stage_requirement(engine, row: Dict[str, Any], normalized: Dict[str, Any], sh: str):
    sid = _requirement_source_id(row)
    original = normalized["raw_text"]
    msg_hash = hashlib.sha256(_norm(original).lower().encode("utf-8")).hexdigest()
    evidence_key = f"WA-LIVE-REQ:{sid}:{sh[:20]}"

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

    with engine.begin() as c:
        c.execute(text(f"""
            INSERT INTO {REQ_GATE}(
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
            ON CONFLICT(evidence_key) DO UPDATE SET
                original_message=EXCLUDED.original_message,
                message_hash=EXCLUDED.message_hash,
                genuine_confidence=GREATEST(
                    {REQ_GATE}.genuine_confidence,EXCLUDED.genuine_confidence
                ),
                transaction_type=COALESCE({REQ_GATE}.transaction_type,EXCLUDED.transaction_type),
                property_category=COALESCE({REQ_GATE}.property_category,EXCLUDED.property_category),
                intended_use=COALESCE({REQ_GATE}.intended_use,EXCLUDED.intended_use),
                locations=CASE
                    WHEN jsonb_array_length({REQ_GATE}.locations)>0
                    THEN {REQ_GATE}.locations ELSE EXCLUDED.locations END,
                area_min_sqft=COALESCE({REQ_GATE}.area_min_sqft,EXCLUDED.area_min_sqft),
                area_max_sqft=COALESCE({REQ_GATE}.area_max_sqft,EXCLUDED.area_max_sqft),
                budget_min=COALESCE({REQ_GATE}.budget_min,EXCLUDED.budget_min),
                budget_max=COALESCE({REQ_GATE}.budget_max,EXCLUDED.budget_max),
                contact_numbers=CASE
                    WHEN jsonb_array_length({REQ_GATE}.contact_numbers)>0
                    THEN {REQ_GATE}.contact_numbers ELSE EXCLUDED.contact_numbers END,
                extracted_fields=COALESCE({REQ_GATE}.extracted_fields,'{{}}'::jsonb)
                                 || EXCLUDED.extracted_fields,
                evidence_quality='LIVE_CLEAN',
                matcher_eligible=CASE
                    WHEN {REQ_GATE}.classification='VERIFIED ACTIVE'
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
                "bridge_version": VERSION,
                "source_wa_requirement_id": sid,
                "source_hash": sh,
                "verification_policy": "HUMAN_GATE_REQUIRED",
            }),
        })

        return str(c.execute(text(f"""
            SELECT id FROM {REQ_GATE} WHERE evidence_key=:ek
        """), {"ek": evidence_key}).scalar_one())


def _sync_requirements(main_engine, wa_engine, counters: Counter):
    if wa_engine is None or not _table_exists(wa_engine, "wa_requirements"):
        return

    for cols, rows in _fetch_all_rows(wa_engine, "wa_requirements"):
        for row in rows:
            sid = _requirement_source_id(row)
            if not sid:
                counters["requirement_errors"] += 1
                continue
            sh = _hash_payload(row, cols)

            if _already_accounted(main_engine, "REQUIREMENT", "wa_requirements", sid, sh):
                counters["requirement_unchanged"] += 1
                continue

            try:
                ok, normalized, reason = _requirement_clean(row)
                if not ok:
                    _ledger(
                        main_engine,
                        entity_type="REQUIREMENT",
                        source_table="wa_requirements",
                        source_id=sid,
                        source_hash=sh,
                        classification="REQUIREMENT",
                        clean_status="NEEDS_REVIEW",
                        target_table=None,
                        target_id=None,
                        reason=reason,
                        normalized=normalized,
                    )
                    counters["requirement_review"] += 1
                    continue

                gid = _stage_requirement(main_engine, row, normalized, sh)
                _ledger(
                    main_engine,
                    entity_type="REQUIREMENT",
                    source_table="wa_requirements",
                    source_id=sid,
                    source_hash=sh,
                    classification="REQUIREMENT",
                    clean_status="STAGED",
                    target_table=REQ_GATE,
                    target_id=gid,
                    reason="HUMAN_VERIFICATION_REQUIRED",
                    normalized=normalized,
                )
                counters["requirements_staged"] += 1
            except Exception as exc:
                counters["requirement_errors"] += 1
                _ledger(
                    main_engine,
                    entity_type="REQUIREMENT",
                    source_table="wa_requirements",
                    source_id=sid,
                    source_hash=sh,
                    classification="REQUIREMENT",
                    clean_status="ERROR",
                    target_table=None,
                    target_id=None,
                    reason=f"{type(exc).__name__}: {exc}"[:1000],
                    normalized={"source_id": sid},
                )


def _count(engine, sql: str, params: Optional[dict] = None) -> int:
    with engine.connect() as c:
        return int(c.execute(text(sql), params or {}).scalar() or 0)


def audit_snapshot() -> Dict[str, Any]:
    main = _main_engine()
    wa = _wa_engine()
    _ensure_schema(main)

    master_total = _count(main, f"SELECT COUNT(*) FROM {MASTER_TABLE}") if _table_exists(main, MASTER_TABLE) else 0
    master_max_id = _count(main, f"SELECT COALESCE(MAX(id),0) FROM {MASTER_TABLE}") if _table_exists(main, MASTER_TABLE) else 0
    master_cursor = _get_cursor(main, MASTER_TABLE)

    live_props = _count(wa, "SELECT COUNT(*) FROM wa_properties") if wa and _table_exists(wa, "wa_properties") else 0
    live_reqs = _count(wa, "SELECT COUNT(*) FROM wa_requirements") if wa and _table_exists(wa, "wa_requirements") else 0

    with main.connect() as c:
        master_accounted = int(c.execute(text(f"""
            SELECT COUNT(DISTINCT source_id)
            FROM {LEDGER_TABLE}
            WHERE source_entity_type='PROPERTY'
              AND source_table=:st
        """), {"st": MASTER_TABLE}).scalar() or 0)

        master_synced = int(c.execute(text(f"""
            SELECT COUNT(DISTINCT source_id)
            FROM {LEDGER_TABLE}
            WHERE source_entity_type='PROPERTY'
              AND source_table=:st
              AND clean_status='SYNCED'
        """), {"st": MASTER_TABLE}).scalar() or 0)

        master_review = int(c.execute(text(f"""
            SELECT COUNT(DISTINCT source_id)
            FROM {LEDGER_TABLE}
            WHERE source_entity_type='PROPERTY'
              AND source_table=:st
              AND clean_status='NEEDS_REVIEW'
        """), {"st": MASTER_TABLE}).scalar() or 0)

        live_accounted = int(c.execute(text(f"""
            SELECT COUNT(DISTINCT source_id)
            FROM {LEDGER_TABLE}
            WHERE source_entity_type='PROPERTY'
              AND source_table='wa_properties'
        """)).scalar() or 0)

        req_accounted = int(c.execute(text(f"""
            SELECT COUNT(DISTINCT source_id)
            FROM {LEDGER_TABLE}
            WHERE source_entity_type='REQUIREMENT'
              AND source_table='wa_requirements'
        """)).scalar() or 0)

        projected_master_props = int(c.execute(text(f"""
            SELECT COUNT(*) FROM {MASTER_PROPERTIES}
            WHERE source_type='WHATSAPP_MASTER_CLEAN'
        """)).scalar() or 0)

        projected_live_props = int(c.execute(text(f"""
            SELECT COUNT(*) FROM {MASTER_PROPERTIES}
            WHERE source_type='WHATSAPP_LIVE_CLEAN'
        """)).scalar() or 0)

        staged_reqs = int(c.execute(text(f"""
            SELECT COUNT(*) FROM {REQ_GATE}
            WHERE source_type='WHATSAPP_LIVE_CLEAN'
        """)).scalar() or 0)

        unsafe_reqs = int(c.execute(text(f"""
            SELECT COUNT(*) FROM {REQ_GATE}
            WHERE source_type='WHATSAPP_LIVE_CLEAN'
              AND classification<>'VERIFIED ACTIVE'
              AND matcher_eligible=TRUE
        """)).scalar() or 0)

        unsafe_props = int(c.execute(text(f"""
            SELECT COUNT(*)
            FROM {MASTER_PROPERTIES} p
            JOIN {MASTER_WORKFLOW} w ON w.canonical_id=p.canonical_id
            WHERE p.source_type IN ('WHATSAPP_MASTER_CLEAN','WHATSAPP_LIVE_CLEAN')
              AND w.verification_status='VERIFIED'
              AND w.verified_by IS NULL
        """)).scalar() or 0)

        error_counts = {
            str(r["source_table"]): int(r["n"])
            for r in c.execute(text(f"""
                SELECT source_table,COUNT(*) n
                FROM {LEDGER_TABLE}
                WHERE clean_status='ERROR'
                GROUP BY source_table
            """)).mappings().all()
        }

        review_reasons = [
            {"reason": str(r["reason"] or "UNKNOWN"), "count": int(r["n"])}
            for r in c.execute(text(f"""
                SELECT reason,COUNT(*) n
                FROM {LEDGER_TABLE}
                WHERE clean_status='NEEDS_REVIEW'
                  AND source_table=:st
                GROUP BY reason
                ORDER BY n DESC
                LIMIT 20
            """), {"st": MASTER_TABLE}).mappings().all()
        ]

    return {
        "version": VERSION,
        "master_database": {
            "source_table": MASTER_TABLE,
            "total_rows": master_total,
            "max_id": master_max_id,
            "cursor": master_cursor,
            "numeric_backlog": max(master_max_id - master_cursor, 0),
            "accounted_distinct_sources": master_accounted,
            "synced_distinct_sources": master_synced,
            "review_distinct_sources": master_review,
            "projected_operational_properties": projected_master_props,
            "top_review_reasons": review_reasons,
        },
        "live_database": {
            "wa_properties": live_props,
            "property_sources_accounted": live_accounted,
            "property_backlog": max(live_props - live_accounted, 0),
            "wa_requirements": live_reqs,
            "requirement_sources_accounted": req_accounted,
            "requirement_backlog": max(live_reqs - req_accounted, 0),
            "projected_live_properties": projected_live_props,
            "staged_requirements": staged_reqs,
        },
        "safety": {
            "unverified_requirements_matcher_eligible": unsafe_reqs,
            "properties_auto_verified_without_actor": unsafe_props,
            "error_counts": error_counts,
        },
    }


def run_sync(full_master_replay: bool = False) -> Dict[str, Any]:
    main = _main_engine()
    wa = _wa_engine()
    _ensure_schema(main)

    counters = Counter()

    _sync_master_backlog(main, counters, full_replay=full_master_replay)
    _sync_live_properties(main, wa, counters)
    _sync_requirements(main, wa, counters)

    audit = audit_snapshot()
    result = {
        "version": VERSION,
        "counters": dict(counters),
        "audit": audit,
        "policy": {
            "master_database_is_first_class_source": True,
            "live_database_is_first_class_source": True,
            "property_projection": "UNVERIFIED_UNKNOWN_VERIFY_FIRST",
            "requirement_projection": "HUMAN_GATE_MATCHER_FALSE",
            "source_mutation": False,
            "matcher_phase5_mutation": False,
        },
    }

    with main.begin() as c:
        c.execute(text(f"""
            INSERT INTO {RUN_TABLE}(version,result)
            VALUES(:v,CAST(:r AS JSONB))
        """), {
            "v": VERSION,
            "r": json.dumps(_safe(result), ensure_ascii=False),
        })

    RUNTIME.update(
        status="OK",
        last_run_at=datetime.now(timezone.utc).isoformat(),
        last_error=None,
        last_result=result,
    )
    return result


def _worker():
    while True:
        try:
            run_sync(full_master_replay=False)
        except Exception as exc:
            RUNTIME.update(
                status="ERROR",
                last_run_at=datetime.now(timezone.utc).isoformat(),
                last_error=f"{type(exc).__name__}: {exc}",
            )
            print("WhatsApp Unified Operational Bridge error:", repr(exc), flush=True)
        time.sleep(POLL_SECONDS)


def start_worker():
    global _STARTED
    with _LOCK:
        if _STARTED:
            return False
        _STARTED = True

    threading.Thread(
        target=_worker,
        name="whatsapp-master-live-unified-bridge",
        daemon=True,
    ).start()
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
        return run_sync(full_master_replay=False)

    app.include_router(router)

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "worker_started": start_worker(),
        "poll_seconds": POLL_SECONDS,
        "master_source": MASTER_TABLE,
        "live_property_source": "wa_properties",
        "live_requirement_source": "wa_requirements",
    }


if __name__ == "__main__":
    import sys
    full = "--full-master-replay" in sys.argv
    print(json.dumps(run_sync(full_master_replay=full), indent=2, default=str))
