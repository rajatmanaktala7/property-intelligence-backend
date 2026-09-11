from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import text

import alliance_phase5_canonical_matcher as phase5
import alliance_whatsapp_location_recovery as recovery

VERSION = "1.0.0-RECOVERY-CONFIDENCE-PROMOTION-QUEUE"

SOURCE_TABLE = "pi_whatsapp_property_master"
RECOVERY_TABLE = "pi_whatsapp_location_recovery"
QUEUE_TABLE = "pi_whatsapp_location_promotion_queue"

AUTO_PROMOTE_THRESHOLD = 90
REVIEW_THRESHOLD = 70

# Strong evidence fields. raw_message/original message are especially useful because
# the frozen matcher did not use them for property-side locality normalization.
RAW_MESSAGE_FIELDS = (
    "raw_message",
    "original_message",
    "message",
    "whatsapp_message",
    "source_text",
)

STRUCTURED_LOCATION_FIELDS = (
    "location",
    "locality",
    "address",
    "project_name",
    "building_name",
    "society_name",
)

GENERIC_CITY_LOCATIONS = {
    "GURUGRAM",
    "GURGAON",
    "DELHI",
    "NEW DELHI",
    "NOIDA",
    "GREATER NOIDA",
    "GOA",
    "MUMBAI",
    "BENGALURU",
    "BANGALORE",
    "HYDERABAD",
}

def ensure_queue(engine):
    statements = [
        f"""CREATE TABLE IF NOT EXISTS {QUEUE_TABLE} (
            id BIGSERIAL PRIMARY KEY,
            record_id TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            original_location TEXT,
            recovered_location TEXT NOT NULL,
            confidence_score INTEGER NOT NULL,
            confidence_band TEXT NOT NULL,
            evidence_type TEXT NOT NULL,
            evidence_field TEXT,
            evidence_excerpt TEXT,
            recovery_method TEXT,
            recovery_confidence DOUBLE PRECISION,
            queue_status TEXT NOT NULL,
            promotion_eligible BOOLEAN NOT NULL DEFAULT FALSE,
            review_reason TEXT,
            source_snapshot_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            evidence_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(record_id, source_hash)
        )""",
        f"CREATE INDEX IF NOT EXISTS idx_pi_wa_promo_status ON {QUEUE_TABLE}(queue_status)",
        f"CREATE INDEX IF NOT EXISTS idx_pi_wa_promo_score ON {QUEUE_TABLE}(confidence_score DESC)",
        f"CREATE INDEX IF NOT EXISTS idx_pi_wa_promo_location ON {QUEUE_TABLE}(recovered_location)",
    ]

    with engine.begin() as c:
        for stmt in statements:
            c.execute(text(stmt))


def table_columns(engine, table_name: str) -> List[str]:
    return sorted(phase5.table_columns(engine, table_name))


def _source_select_columns(engine) -> List[str]:
    cols = table_columns(engine, SOURCE_TABLE)
    wanted = [
        "record_id",
        "lead_type",
        "description",
        "configuration_details",
        "area",
        "price",
        "verification",
        "source",
        "captured_on",
        "generation_id",
        "location",
        "locality",
        "address",
        "project_name",
        "building_name",
        "society_name",
        "raw_message",
        "original_message",
        "message",
        "whatsapp_message",
        "source_text",
    ]
    return [c for c in wanted if c in cols]


def _load_source_by_record_ids(engine, record_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    if not record_ids:
        return {}

    cols = _source_select_columns(engine)
    if "record_id" not in cols:
        raise RuntimeError("record_id column missing from source table")

    qcols = ", ".join('"' + c + '"' for c in cols)
    sql = text(
        f'SELECT {qcols} FROM "{SOURCE_TABLE}" '
        'WHERE record_id = ANY(:ids)'
    )

    with engine.connect() as c:
        rows = c.execute(sql, {"ids": list(record_ids)}).mappings().all()

    # Preserve one deterministic representative per record_id.
    out = {}
    for row in rows:
        d = dict(row)
        rid = str(d.get("record_id") or "")
        if rid and rid not in out:
            out[rid] = d
    return out


def _latest_recovery_rows(engine, offset: int, limit: int):
    sql = text(f"""
        SELECT DISTINCT ON (record_id)
            record_id,
            source_hash,
            recovered_location,
            recovery_method,
            confidence,
            evidence_json,
            status,
            review_reason,
            source_snapshot_json,
            updated_at
        FROM {RECOVERY_TABLE}
        ORDER BY record_id, updated_at DESC, id DESC
        OFFSET :off LIMIT :lim
    """)

    with engine.connect() as c:
        return [
            dict(r)
            for r in c.execute(
                sql,
                {"off": int(offset), "lim": int(limit)},
            ).mappings().all()
        ]


def _latest_recovery_count(engine) -> int:
    with engine.connect() as c:
        return int(c.execute(text(f"""
            SELECT COUNT(DISTINCT record_id)
            FROM {RECOVERY_TABLE}
        """)).scalar() or 0)


def _frozen_original_location(row: Dict[str, Any]) -> Optional[str]:
    """
    Reconstruct the exact property-side location logic used by the frozen
    full-inventory audit: description + configuration, then NORTH GOA,
    then configuration_details fallback.
    """
    desc = str(row.get("description") or "")
    cfg = str(row.get("configuration_details") or "")
    blob = (desc + " " + cfg).strip()

    loc = phase5.canonical_location(blob)

    if not loc and "NORTH GOA" in phase5.norm(blob):
        loc = "NORTH GOA"

    if not loc:
        loc = phase5.candidate_location(cfg)

    return loc


def _find_explicit_location(
    value: Any,
    expected_location: str,
) -> Tuple[bool, Optional[str]]:
    if value in (None, ""):
        return False, None

    txt = str(value)
    norm_txt = phase5.norm(txt)

    # Use recovery's lightweight detector if available.
    detector = getattr(recovery, "fast_locations", None)
    if detector:
        locations = detector(txt)
    else:
        locations = phase5.canonical_locations(txt)

    if expected_location in locations:
        return True, phase5.sanitize_text(txt)[:500]

    # Region case.
    if expected_location == "NORTH GOA" and "NORTH GOA" in norm_txt:
        return True, phase5.sanitize_text(txt)[:500]

    return False, None


def _score(
    source: Dict[str, Any],
    rec: Dict[str, Any],
) -> Dict[str, Any]:
    recovered = str(rec.get("recovered_location") or "").strip()
    original = _frozen_original_location(source)

    result = {
        "record_id": str(rec.get("record_id") or ""),
        "source_hash": str(rec.get("source_hash") or ""),
        "original_location": original,
        "recovered_location": recovered,
        "confidence_score": 0,
        "confidence_band": "REJECT",
        "evidence_type": "NONE",
        "evidence_field": None,
        "evidence_excerpt": None,
        "recovery_method": rec.get("recovery_method"),
        "recovery_confidence": float(rec.get("confidence") or 0.0),
        "queue_status": "REJECTED",
        "promotion_eligible": False,
        "review_reason": None,
        "source_snapshot_json": rec.get("source_snapshot_json") or {},
        "evidence_json": rec.get("evidence_json") or {},
    }

    if not recovered:
        result["review_reason"] = "No recovered geography"
        return result

    # Already usable under frozen matcher is not a recovery promotion candidate.
    if original:
        result["confidence_score"] = 100
        result["confidence_band"] = "ALREADY_USABLE"
        result["evidence_type"] = "FROZEN_MATCHER_ALREADY_HAS_LOCATION"
        result["queue_status"] = "ALREADY_USABLE"
        result["promotion_eligible"] = False
        result["review_reason"] = "Frozen matcher already resolves this row"
        return result

    # Strongest independent evidence: raw/original message explicitly says the recovered location.
    for field in RAW_MESSAGE_FIELDS:
        if field not in source:
            continue
        ok, excerpt = _find_explicit_location(source.get(field), recovered)
        if ok:
            score = 98
            if recovered in GENERIC_CITY_LOCATIONS:
                score = 90

            result.update({
                "confidence_score": score,
                "confidence_band": "HIGH",
                "evidence_type": "EXPLICIT_RAW_MESSAGE_LOCATION",
                "evidence_field": field,
                "evidence_excerpt": excerpt,
                "queue_status": "READY_FOR_REVIEW",
                "promotion_eligible": score >= AUTO_PROMOTE_THRESHOLD,
                "review_reason": (
                    "Explicit geography found in source message; source inventory "
                    "remains unchanged until approved"
                ),
            })
            return result

    # Strong structured geography field.
    for field in STRUCTURED_LOCATION_FIELDS:
        if field not in source:
            continue
        ok, excerpt = _find_explicit_location(source.get(field), recovered)
        if ok:
            score = 95
            if recovered in GENERIC_CITY_LOCATIONS:
                score = 88

            result.update({
                "confidence_score": score,
                "confidence_band": "HIGH" if score >= 90 else "MEDIUM",
                "evidence_type": "STRUCTURED_LOCATION_EVIDENCE",
                "evidence_field": field,
                "evidence_excerpt": excerpt,
                "queue_status": "READY_FOR_REVIEW" if score >= 90 else "NEEDS_REVIEW",
                "promotion_eligible": score >= AUTO_PROMOTE_THRESHOLD,
                "review_reason": "Structured geography evidence",
            })
            return result

    # Exact-signature propagation is deliberately review-only.
    if rec.get("recovery_method") == "EXACT_SIGNATURE_PROPAGATION":
        result.update({
            "confidence_score": 75,
            "confidence_band": "MEDIUM",
            "evidence_type": "EXACT_SIGNATURE_ONLY",
            "queue_status": "NEEDS_REVIEW",
            "promotion_eligible": False,
            "review_reason": (
                "Signature propagation alone is insufficient for automatic promotion"
            ),
        })
        return result

    result["review_reason"] = (
        "Recovered location lacks independent explicit source evidence"
    )
    return result


def _write_queue_batch(engine, rows: Sequence[Dict[str, Any]]):
    if not rows:
        return

    sql = text(f"""
        INSERT INTO {QUEUE_TABLE} (
            record_id,
            source_hash,
            original_location,
            recovered_location,
            confidence_score,
            confidence_band,
            evidence_type,
            evidence_field,
            evidence_excerpt,
            recovery_method,
            recovery_confidence,
            queue_status,
            promotion_eligible,
            review_reason,
            source_snapshot_json,
            evidence_json,
            created_at,
            updated_at
        ) VALUES (
            :record_id,
            :source_hash,
            :original_location,
            :recovered_location,
            :confidence_score,
            :confidence_band,
            :evidence_type,
            :evidence_field,
            :evidence_excerpt,
            :recovery_method,
            :recovery_confidence,
            :queue_status,
            :promotion_eligible,
            :review_reason,
            CAST(:source_snapshot_json AS JSONB),
            CAST(:evidence_json AS JSONB),
            NOW(),
            NOW()
        )
        ON CONFLICT (record_id, source_hash)
        DO UPDATE SET
            original_location = EXCLUDED.original_location,
            recovered_location = EXCLUDED.recovered_location,
            confidence_score = EXCLUDED.confidence_score,
            confidence_band = EXCLUDED.confidence_band,
            evidence_type = EXCLUDED.evidence_type,
            evidence_field = EXCLUDED.evidence_field,
            evidence_excerpt = EXCLUDED.evidence_excerpt,
            recovery_method = EXCLUDED.recovery_method,
            recovery_confidence = EXCLUDED.recovery_confidence,
            queue_status = EXCLUDED.queue_status,
            promotion_eligible = EXCLUDED.promotion_eligible,
            review_reason = EXCLUDED.review_reason,
            source_snapshot_json = EXCLUDED.source_snapshot_json,
            evidence_json = EXCLUDED.evidence_json,
            updated_at = NOW()
    """)

    params = []
    for row in rows:
        d = dict(row)
        d["source_snapshot_json"] = json.dumps(
            d.get("source_snapshot_json") or {},
            ensure_ascii=False,
            default=str,
        )
        d["evidence_json"] = json.dumps(
            d.get("evidence_json") or {},
            ensure_ascii=False,
            default=str,
        )
        params.append(d)

    with engine.begin() as c:
        c.execute(sql, params)


def build_queue(engine, batch_size: int = 2000):
    ensure_queue(engine)

    total = _latest_recovery_count(engine)
    print("CONFIDENCE ENGINE:", VERSION, flush=True)
    print("Latest recovery records:", total, flush=True)

    counts = Counter()
    score_counts = Counter()
    processed = 0

    for offset in range(0, total, batch_size):
        rec_rows = _latest_recovery_rows(engine, offset, batch_size)
        ids = [str(r.get("record_id") or "") for r in rec_rows]
        source_map = _load_source_by_record_ids(engine, ids)

        queue_rows = []

        for rec in rec_rows:
            rid = str(rec.get("record_id") or "")
            source = source_map.get(rid)

            if not source:
                # Do not fabricate a source row.
                q = {
                    "record_id": rid,
                    "source_hash": str(rec.get("source_hash") or ""),
                    "original_location": None,
                    "recovered_location": str(rec.get("recovered_location") or ""),
                    "confidence_score": 0,
                    "confidence_band": "REJECT",
                    "evidence_type": "SOURCE_ROW_NOT_FOUND",
                    "evidence_field": None,
                    "evidence_excerpt": None,
                    "recovery_method": rec.get("recovery_method"),
                    "recovery_confidence": float(rec.get("confidence") or 0.0),
                    "queue_status": "REJECTED",
                    "promotion_eligible": False,
                    "review_reason": "Source row not found",
                    "source_snapshot_json": rec.get("source_snapshot_json") or {},
                    "evidence_json": rec.get("evidence_json") or {},
                }
            else:
                q = _score(source, rec)

            queue_rows.append(q)
            counts[q["queue_status"]] += 1
            score_counts[q["confidence_band"]] += 1

        _write_queue_batch(engine, queue_rows)
        processed += len(rec_rows)

        print(
            f"Confidence pass: {processed}/{total}",
            flush=True,
        )

    summary = {
        "version": VERSION,
        "processed": processed,
        "queue_status_counts": dict(counts),
        "confidence_band_counts": dict(score_counts),
        "auto_promotion_threshold": AUTO_PROMOTE_THRESHOLD,
        "review_threshold": REVIEW_THRESHOLD,
        "policy": {
            "matcher_files_untouched": True,
            "source_inventory_untouched": True,
            "queue_only": True,
            "promotion_requires_separate_approval_action": True,
            "signature_only_never_auto_promoted": True,
        },
    }

    print("")
    print("CONFIDENCE / PROMOTION QUEUE SUMMARY")
    print(json.dumps(summary, indent=2))
    return summary


def main():
    engine = phase5.create_main_engine()
    build_queue(engine)


if __name__ == "__main__":
    main()
