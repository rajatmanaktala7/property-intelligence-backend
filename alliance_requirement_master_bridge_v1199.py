from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from fastapi import HTTPException
from sqlalchemy import text

VERSION = "11.9.9-VERIFIED-MASTER-PROMOTION"

DDL = [
    """CREATE TABLE IF NOT EXISTS pi_requirement_gate_master_map_v1199(
        gate_id BIGINT PRIMARY KEY,
        master_requirement_id TEXT NOT NULL,
        canonical_id TEXT NOT NULL,
        message_hash TEXT NOT NULL,
        promotion_state TEXT NOT NULL DEFAULT 'ACTIVE',
        created_by_gate BOOLEAN NOT NULL DEFAULT FALSE,
        promoted_by TEXT,
        promoted_at TIMESTAMPTZ,
        last_synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        withdrawn_at TIMESTAMPTZ,
        details JSONB NOT NULL DEFAULT '{}'::jsonb
    )""",
    """CREATE INDEX IF NOT EXISTS idx_req_gate_master_map_canonical_v1199
       ON pi_requirement_gate_master_map_v1199(canonical_id,promotion_state)""",
]

def _norm(v: Any) -> str:
    return " ".join(str(v or "").split()).strip()

def _to_float(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except Exception:
        return None

def _json_list(v: Any):
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if v is None:
        return []
    if isinstance(v, str):
        try:
            x = json.loads(v)
            if isinstance(x, list):
                return [str(y).strip() for y in x if str(y).strip()]
        except Exception:
            pass
        return [x.strip() for x in v.replace(";", ",").split(",") if x.strip()]
    return [str(v)]

def _master_tx(v: Any) -> Optional[str]:
    tx = _norm(v).upper()
    if tx in {"LEASE", "RENT"}:
        return "RENT"
    if tx in {"PURCHASE", "BUY", "SALE"}:
        return "SALE"
    return None

def _area_reference(row: Dict[str, Any]) -> Optional[float]:
    a = _to_float(row.get("area_min_sqft"))
    b = _to_float(row.get("area_max_sqft"))
    if a is not None and b is not None:
        return round((a + b) / 2.0, 4)
    return a if a is not None else b

def _budget_text(row: Dict[str, Any]) -> Optional[str]:
    a = _to_float(row.get("budget_min"))
    b = _to_float(row.get("budget_max"))
    if a is not None and b is not None:
        return str(b) if a == b else f"{a:g}-{b:g}"
    return str(b) if b is not None else str(a) if a is not None else None

def ensure_schema(engine) -> None:
    with engine.begin() as c:
        for ddl in DDL:
            c.execute(text(ddl))

def _lookup_existing_master(conn, row: Dict[str, Any]):
    # 1) Existing bridge mapping for this exact gate row.
    mapped = conn.execute(
        text("""SELECT * FROM pi_requirement_gate_master_map_v1199
                WHERE gate_id=:gid FOR UPDATE"""),
        {"gid": row["id"]},
    ).mappings().first()
    if mapped:
        return (
            str(mapped["canonical_id"]),
            str(mapped["master_requirement_id"]),
            bool(mapped["created_by_gate"]),
            "EXISTING_GATE_MAP",
        )

    # 2) Existing canonical requirement with the same original source row.
    if row.get("source_table") and row.get("source_pk"):
        existing = conn.execute(
            text("""SELECT l.canonical_id,l.master_id
                    FROM pi_master_source_links_v711 l
                    JOIN pi_master_requirements_v711 r
                      ON r.canonical_id=l.canonical_id
                    WHERE l.master_entity_type='REQUIREMENT'
                      AND l.source_table=:tb
                      AND l.source_pk=:pk
                    ORDER BY l.id
                    LIMIT 1"""),
            {"tb": str(row["source_table"]), "pk": str(row["source_pk"])},
        ).mappings().first()
        if existing:
            return (
                str(existing["canonical_id"]),
                str(existing["master_id"]),
                False,
                "REUSED_ORIGINAL_SOURCE_MASTER",
            )

    # 3) Deterministic Gate canonical ID. Repeating Verify cannot duplicate it.
    message_hash = _norm(row.get("message_hash"))
    cid = "GATE-REQ-" + hashlib.sha256(
        ("REQ-GATE:" + message_hash).encode("utf-8")
    ).hexdigest()[:24].upper()
    existing_mid = conn.execute(
        text("""SELECT master_requirement_id
                FROM pi_master_requirements_v711
                WHERE canonical_id=:cid"""),
        {"cid": cid},
    ).scalar()
    if existing_mid:
        return cid, str(existing_mid), True, "REUSED_DETERMINISTIC_GATE_MASTER"

    mid = "MR-" + hashlib.sha256(cid.encode("utf-8")).hexdigest()[:16].upper()
    return cid, mid, True, "CREATED_GATE_MASTER"

def _withdraw(conn, row: Dict[str, Any], actor: str, new_status: str):
    mp = conn.execute(
        text("""SELECT * FROM pi_requirement_gate_master_map_v1199
                WHERE gate_id=:gid FOR UPDATE"""),
        {"gid": row["id"]},
    ).mappings().first()
    if not mp:
        return {"state": "NO_MASTER_MAPPING", "matcher_visible": False}

    cid = str(mp["canonical_id"])
    conn.execute(
        text("""UPDATE pi_requirement_gate_master_map_v1199
                SET promotion_state='WITHDRAWN',
                    withdrawn_at=NOW(),
                    last_synced_at=NOW(),
                    details=COALESCE(details,'{}'::jsonb) || CAST(:d AS JSONB)
                WHERE gate_id=:gid"""),
        {
            "gid": row["id"],
            "d": json.dumps({
                "withdrawn_by": actor,
                "gate_status": new_status,
                "version": VERSION,
            }),
        },
    )

    # Never deactivate a legacy Master row merely because Gate reviewed a copy.
    if bool(mp["created_by_gate"]):
        others = int(conn.execute(
            text("""SELECT COUNT(*)
                    FROM pi_requirement_gate_master_map_v1199
                    WHERE canonical_id=:cid
                      AND gate_id<>:gid
                      AND promotion_state='ACTIVE'"""),
            {"cid": cid, "gid": row["id"]},
        ).scalar() or 0)
        if others == 0:
            conn.execute(
                text("""UPDATE pi_master_requirements_v711
                        SET promotion_status='GATE_WITHDRAWN',updated_at=NOW()
                        WHERE canonical_id=:cid
                          AND source_type='REQUIREMENT_GATE_VERIFIED'"""),
                {"cid": cid},
            )
            conn.execute(
                text("""DELETE FROM pi_master_matches_v720
                        WHERE requirement_canonical_id=:cid"""),
                {"cid": cid},
            )

    return {
        "state": "WITHDRAWN",
        "canonical_id": cid,
        "master_requirement_id": str(mp["master_requirement_id"]),
        "created_by_gate": bool(mp["created_by_gate"]),
        "matcher_visible": False if bool(mp["created_by_gate"]) else None,
    }

def sync(conn, gid: int, actor: str, new_status: str) -> Dict[str, Any]:
    row0 = conn.execute(
        text("""SELECT * FROM pi_requirement_gate_v1191
                WHERE id=:gid FOR UPDATE"""),
        {"gid": gid},
    ).mappings().first()
    if not row0:
        raise HTTPException(404, "Requirement not found")
    row = dict(row0)

    if new_status != "VERIFIED ACTIVE":
        result = _withdraw(conn, row, actor, new_status)
        conn.execute(
            text("""INSERT INTO pi_requirement_gate_audit_v1191(
                        gate_id,action,actor,old_status,new_status,details)
                    VALUES(:gid,'MASTER_WITHDRAW_SYNC',:actor,:old,:new,
                           CAST(:d AS JSONB))"""),
            {
                "gid": gid, "actor": actor,
                "old": row.get("classification"), "new": new_status,
                "d": json.dumps({"result": result, "version": VERSION}),
            },
        )
        return result

    tx = _master_tx(row.get("transaction_type"))
    locations = _json_list(row.get("locations"))
    phones = _json_list(row.get("contact_numbers"))

    # Keep Master clean. If these are not verified, the Gate click rolls back.
    if not tx:
        raise HTTPException(
            400, "Cannot verify into Master: set transaction to LEASE or PURCHASE first."
        )
    if not locations:
        raise HTTPException(
            400, "Cannot verify into Master: at least one verified location is required."
        )
    if not phones:
        raise HTTPException(
            400, "Cannot verify into Master: at least one verified contact number is required."
        )

    cid, mid, created_by_gate, dedup_mode = _lookup_existing_master(conn, row)
    area = _area_reference(row)
    budget = _budget_text(row)
    budget_kind = (
        "RENT_AMOUNT" if budget and tx == "RENT"
        else "SALE_AMOUNT" if budget and tx == "SALE"
        else None
    )
    msg_hash = _norm(row.get("message_hash"))

    clean = {
        "requirement_gate": {
            "gate_id": gid,
            "status": "VERIFIED ACTIVE",
            "original_message": row.get("original_message"),
            "message_hash": msg_hash,
            "source_type": row.get("source_type"),
            "source_table": row.get("source_table"),
            "source_pk": row.get("source_pk"),
            "source_group": row.get("source_group"),
            "gate_transaction_type": row.get("transaction_type"),
            "master_transaction_type": tx,
            "property_category": row.get("property_category"),
            "intended_use": row.get("intended_use"),
            "locations": locations,
            "alternate_locations": _json_list(row.get("alternate_locations")),
            "area_min_sqft": _to_float(row.get("area_min_sqft")),
            "area_max_sqft": _to_float(row.get("area_max_sqft")),
            "area_match_reference_sqft": area,
            "budget_min": _to_float(row.get("budget_min")),
            "budget_max": _to_float(row.get("budget_max")),
            "floor_requirement": row.get("floor_requirement"),
            "frontage_requirement": row.get("frontage_requirement"),
            "parking_requirement": row.get("parking_requirement"),
            "company_brand_person": row.get("company_brand_person"),
            "contact_numbers": phones,
            "evidence_quality": row.get("evidence_quality"),
            "genuine_confidence": float(row.get("genuine_confidence") or 0),
            "verified_by": row.get("verified_by") or actor,
            "verified_at": str(row.get("verified_at") or ""),
            "verification_notes": row.get("verification_notes"),
            "bridge_version": VERSION,
        },
        "intended_use": row.get("intended_use"),
        "property_category": row.get("property_category"),
        "floor_requirement": row.get("floor_requirement"),
        "area_min_sqft": _to_float(row.get("area_min_sqft")),
        "area_max_sqft": _to_float(row.get("area_max_sqft")),
    }

    conn.execute(
        text("""INSERT INTO pi_master_requirements_v711(
                    master_requirement_id,canonical_id,source_type,
                    transaction_type,locality,city,
                    area_value,area_unit,area_sqft,
                    budget_raw,budget_kind,phones,clean_record,
                    source_count,promotion_status,source_version,
                    created_at,updated_at)
                VALUES(
                    :mid,:cid,'REQUIREMENT_GATE_VERIFIED',
                    :tx,:loc,NULL,
                    :area,'SQFT',:area,
                    :budget,:bk,CAST(:phones AS JSONB),CAST(:clean AS JSONB),
                    1,'PROMOTED_VALIDATED',:ver,NOW(),NOW())
                ON CONFLICT(canonical_id) DO UPDATE SET
                    transaction_type=EXCLUDED.transaction_type,
                    locality=COALESCE(EXCLUDED.locality,pi_master_requirements_v711.locality),
                    area_value=COALESCE(EXCLUDED.area_value,pi_master_requirements_v711.area_value),
                    area_unit=CASE WHEN EXCLUDED.area_value IS NOT NULL
                                   THEN 'SQFT' ELSE pi_master_requirements_v711.area_unit END,
                    area_sqft=COALESCE(EXCLUDED.area_sqft,pi_master_requirements_v711.area_sqft),
                    budget_raw=COALESCE(EXCLUDED.budget_raw,pi_master_requirements_v711.budget_raw),
                    budget_kind=COALESCE(EXCLUDED.budget_kind,pi_master_requirements_v711.budget_kind),
                    phones=CASE WHEN jsonb_array_length(EXCLUDED.phones)>0
                                THEN EXCLUDED.phones ELSE pi_master_requirements_v711.phones END,
                    clean_record=COALESCE(pi_master_requirements_v711.clean_record,'{}'::jsonb)
                                 || EXCLUDED.clean_record,
                    promotion_status='PROMOTED_VALIDATED',
                    source_version=:ver,
                    updated_at=NOW()"""),
        {
            "mid": mid, "cid": cid, "tx": tx, "loc": locations[0],
            "area": area, "budget": budget, "bk": budget_kind,
            "phones": json.dumps(phones),
            "clean": json.dumps(clean, ensure_ascii=False, default=str),
            "ver": VERSION,
        },
    )
    mid = str(conn.execute(
        text("""SELECT master_requirement_id FROM pi_master_requirements_v711
                WHERE canonical_id=:cid"""),
        {"cid": cid},
    ).scalar_one())

    conn.execute(
        text("""INSERT INTO pi_master_source_links_v711(
                    master_entity_type,master_id,canonical_id,
                    source_type,source_table,source_pk,source_row_hash)
                VALUES('REQUIREMENT',:mid,:cid,'REQUIREMENT_GATE_VERIFIED',
                       'pi_requirement_gate_v1191',:pk,:rh)
                ON CONFLICT DO NOTHING"""),
        {"mid": mid, "cid": cid, "pk": str(gid), "rh": msg_hash},
    )
    if row.get("source_table") and row.get("source_pk"):
        conn.execute(
            text("""INSERT INTO pi_master_source_links_v711(
                        master_entity_type,master_id,canonical_id,
                        source_type,source_table,source_pk,source_row_hash)
                    VALUES('REQUIREMENT',:mid,:cid,:st,:tb,:pk,:rh)
                    ON CONFLICT DO NOTHING"""),
            {
                "mid": mid, "cid": cid,
                "st": str(row.get("source_type") or "REQUIREMENT_GATE_SOURCE"),
                "tb": str(row["source_table"]), "pk": str(row["source_pk"]),
                "rh": msg_hash,
            },
        )

    conn.execute(
        text("""INSERT INTO pi_requirement_gate_master_map_v1199(
                    gate_id,master_requirement_id,canonical_id,message_hash,
                    promotion_state,created_by_gate,promoted_by,promoted_at,
                    last_synced_at,withdrawn_at,details)
                VALUES(:gid,:mid,:cid,:rh,'ACTIVE',:created,:actor,NOW(),
                       NOW(),NULL,CAST(:d AS JSONB))
                ON CONFLICT(gate_id) DO UPDATE SET
                    master_requirement_id=EXCLUDED.master_requirement_id,
                    canonical_id=EXCLUDED.canonical_id,
                    message_hash=EXCLUDED.message_hash,
                    promotion_state='ACTIVE',
                    created_by_gate=EXCLUDED.created_by_gate,
                    promoted_by=EXCLUDED.promoted_by,
                    promoted_at=COALESCE(
                        pi_requirement_gate_master_map_v1199.promoted_at,
                        EXCLUDED.promoted_at),
                    last_synced_at=NOW(),
                    withdrawn_at=NULL,
                    details=EXCLUDED.details"""),
        {
            "gid": gid, "mid": mid, "cid": cid, "rh": msg_hash,
            "created": created_by_gate, "actor": actor,
            "d": json.dumps({
                "dedup_mode": dedup_mode,
                "source_table": row.get("source_table"),
                "source_pk": row.get("source_pk"),
                "version": VERSION,
            }),
        },
    )
    result = {
        "state": "ACTIVE",
        "master_requirement_id": mid,
        "canonical_id": cid,
        "dedup_mode": dedup_mode,
        "created_by_gate": created_by_gate,
        "matcher_visible": True,
    }
    conn.execute(
        text("""INSERT INTO pi_requirement_gate_audit_v1191(
                    gate_id,action,actor,old_status,new_status,details)
                VALUES(:gid,'MASTER_PROMOTED_SYNC',:actor,:old,'VERIFIED ACTIVE',
                       CAST(:d AS JSONB))"""),
        {
            "gid": gid, "actor": actor, "old": row.get("classification"),
            "d": json.dumps({"result": result, "version": VERSION}),
        },
    )
    return result

def reconcile(engine) -> Dict[str, int]:
    """Sync only rows already human VERIFIED ACTIVE. Never touches unverified rows."""
    stats = {"verified_seen": 0, "synced": 0, "blocked": 0}
    with engine.connect() as c:
        ids = [
            int(r[0]) for r in c.execute(
                text("""SELECT id FROM pi_requirement_gate_v1191
                        WHERE classification='VERIFIED ACTIVE'
                          AND matcher_eligible=TRUE
                        ORDER BY id""")
            ).all()
        ]
    for gid in ids:
        stats["verified_seen"] += 1
        try:
            with engine.begin() as c:
                sync(c, gid, "SYSTEM-11.9.9-RECONCILE", "VERIFIED ACTIVE")
            stats["synced"] += 1
        except Exception as exc:
            stats["blocked"] += 1
            try:
                with engine.begin() as c:
                    c.execute(
                        text("""INSERT INTO pi_requirement_gate_audit_v1191(
                                    gate_id,action,actor,old_status,new_status,details)
                                VALUES(:gid,'MASTER_PROMOTION_BLOCKED',
                                       'SYSTEM-11.9.9-RECONCILE',
                                       'VERIFIED ACTIVE','VERIFIED ACTIVE',
                                       CAST(:d AS JSONB))"""),
                        {
                            "gid": gid,
                            "d": json.dumps({"error": str(exc)[:500], "version": VERSION}),
                        },
                    )
            except Exception:
                pass
    return stats
