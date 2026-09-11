from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any, Dict, List, Sequence, Tuple

from sqlalchemy import text

import alliance_phase5_canonical_matcher as phase5

VERSION = "1.1.0-FAST-RESUMABLE-LOCATION-RECOVERY"

SOURCE_TABLE = "pi_whatsapp_property_master"
RECOVERY_TABLE = "pi_whatsapp_location_recovery"
BATCH_SIZE = 5000

PREFERRED_TEXT_COLUMNS = [
    "location", "locality", "city", "property_name", "project_name",
    "building_name", "society_name", "address", "description",
    "configuration_details", "remarks", "raw_message",
    "original_message", "message", "whatsapp_message",
    "source_text", "source",
]

def _alias_pairs():
    out = []
    seen = set()

    for source in (
        getattr(phase5, "LOCATION_ALIASES", {}),
        getattr(phase5, "EXTRA_LOCATION_ALIASES", {}),
    ):
        for canon, aliases in source.items():
            for alias in aliases:
                a = phase5.norm(alias)
                key = (canon, a)
                if not a or key in seen:
                    continue
                seen.add(key)
                out.append((canon, a))

    # Longest aliases first to reduce ambiguous early hits.
    out.sort(key=lambda x: len(x[1]), reverse=True)
    return out

ALIAS_PAIRS = _alias_pairs()

SECTOR_RE = re.compile(
    r"(?<![A-Z0-9])(?:SEC|SECTOR)\s*-?\s*(\d{1,3}[A-Z]?)(?![A-Z0-9])",
    re.I,
)

def ensure_table(engine):
    stmts = [
        f"""CREATE TABLE IF NOT EXISTS {RECOVERY_TABLE} (
            id BIGSERIAL PRIMARY KEY,
            record_id TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            recovered_location TEXT,
            recovery_method TEXT NOT NULL,
            confidence DOUBLE PRECISION NOT NULL,
            evidence_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            status TEXT NOT NULL,
            review_reason TEXT,
            source_snapshot_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(record_id, source_hash)
        )""",
        f"CREATE INDEX IF NOT EXISTS idx_pi_wa_loc_recovery_status ON {RECOVERY_TABLE}(status)",
        f"CREATE INDEX IF NOT EXISTS idx_pi_wa_loc_recovery_location ON {RECOVERY_TABLE}(recovered_location)",
        f"CREATE INDEX IF NOT EXISTS idx_pi_wa_loc_recovery_record_id ON {RECOVERY_TABLE}(record_id)",
    ]

    with engine.begin() as c:
        for stmt in stmts:
            c.execute(text(stmt))

def source_columns(engine) -> List[str]:
    cols = sorted(phase5.table_columns(engine, SOURCE_TABLE))
    wanted = [c for c in PREFERRED_TEXT_COLUMNS if c in cols]
    required = [
        c for c in (
            "record_id", "lead_type", "area", "price",
            "captured_on", "verification", "generation_id",
        )
        if c in cols
    ]

    out = []
    for c in required + wanted:
        if c not in out:
            out.append(c)
    return out

def count_rows(engine, table_name: str) -> int:
    with engine.connect() as c:
        return int(
            c.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar()
            or 0
        )

def fetch_rows(engine, cols: Sequence[str], offset: int, limit: int):
    qcols = ", ".join('"' + c + '"' for c in cols)
    order_col = "record_id" if "record_id" in cols else cols[0]
    sql = (
        f'SELECT {qcols} FROM "{SOURCE_TABLE}" '
        f'ORDER BY "{order_col}" NULLS LAST OFFSET :off LIMIT :lim'
    )

    with engine.connect() as c:
        return [
            dict(r)
            for r in c.execute(
                text(sql),
                {"off": int(offset), "lim": int(limit)},
            ).mappings().all()
        ]

def _source_hash(row: Dict[str, Any], cols: Sequence[str]) -> str:
    payload = {
        c: str(row.get(c) or "")
        for c in cols
        if c != "captured_on"
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _snapshot(row: Dict[str, Any], cols: Sequence[str]):
    out = {}
    for c in cols:
        v = row.get(c)
        if v is None:
            continue
        out[c] = phase5.sanitize_text(v) if isinstance(v, str) else str(v)
    return out

def fast_locations(value: Any) -> List[str]:
    blob = phase5.norm(value)
    if not blob:
        return []

    hits: List[Tuple[int, str]] = []

    # Deterministic dictionary aliases.
    padded = f" {blob} "
    for canon, alias in ALIAS_PAIRS:
        pos = padded.find(f" {alias} ")
        if pos >= 0:
            hits.append((pos, canon))

    # Generic sector support without calling canonical_locations().
    for m in SECTOR_RE.finditer(str(value or "")):
        hits.append((m.start(), f"SECTOR {m.group(1).upper()}"))

    if "NORTH GOA" in blob:
        hits.append((blob.find("NORTH GOA"), "NORTH GOA"))

    hits.sort(key=lambda x: x[0])

    out = []
    seen = set()
    for _, loc in hits:
        if loc not in seen:
            seen.add(loc)
            out.append(loc)
    return out

def explicit_location(row, text_cols):
    for col in text_cols:
        value = row.get(col)
        if value in (None, ""):
            continue

        locations = fast_locations(value)
        if locations:
            return (
                locations[0],
                f"EXPLICIT_FAST:{col}",
                0.99,
                {"column": col, "matched_locations": locations[:5]},
            )

    # Structured fields may contain valid non-dictionary locality values.
    for col in ("location", "locality", "address"):
        if col not in row:
            continue
        value = row.get(col)
        if value in (None, ""):
            continue

        loc = phase5.candidate_location(value)
        if loc:
            return (
                loc,
                f"STRUCTURED_LOCATION_FIELD:{col}",
                0.95,
                {"column": col, "value": phase5.sanitize_text(value)},
            )

    return None, None, 0.0, {}

def signature(row):
    return "|".join([
        phase5.norm(row.get("lead_type")),
        phase5.norm(row.get("description")),
        phase5.norm(row.get("configuration_details")),
        phase5.norm(row.get("area")),
        phase5.norm(row.get("price")),
    ])

def build_signature_map(engine, cols, batch_size=BATCH_SIZE):
    total = count_rows(engine, SOURCE_TABLE)
    text_cols = [c for c in cols if c in PREFERRED_TEXT_COLUMNS]

    # signature -> {location: count}
    by_sig: Dict[str, Counter] = {}

    processed = 0
    for offset in range(0, total, batch_size):
        rows = fetch_rows(engine, cols, offset, batch_size)

        for row in rows:
            loc, _, conf, _ = explicit_location(row, text_cols)
            if not loc or conf < 0.90:
                continue

            sig = signature(row)
            if not sig.strip("|"):
                continue

            if sig not in by_sig:
                by_sig[sig] = Counter()
            by_sig[sig][loc] += 1

        processed += len(rows)
        print(
            f"Signature pass: {processed}/{total}",
            flush=True,
        )

    out = {}
    for sig, counts in by_sig.items():
        if len(counts) != 1:
            continue
        loc, n = counts.most_common(1)[0]
        out[sig] = (loc, n)

    return out

def recover_row(row, cols, signature_map):
    rid = str(row.get("record_id") or "")
    shash = _source_hash(row, cols)
    text_cols = [c for c in cols if c in PREFERRED_TEXT_COLUMNS]

    loc, method, conf, evidence = explicit_location(row, text_cols)

    if loc and conf >= 0.90:
        return {
            "record_id": rid,
            "source_hash": shash,
            "recovered_location": loc,
            "recovery_method": method,
            "confidence": conf,
            "evidence_json": evidence,
            "status": "ALREADY_VALID",
            "review_reason": None,
            "source_snapshot_json": _snapshot(row, cols),
        }

    sig = signature(row)
    if sig in signature_map:
        loc2, support = signature_map[sig]
        return {
            "record_id": rid,
            "source_hash": shash,
            "recovered_location": loc2,
            "recovery_method": "EXACT_SIGNATURE_PROPAGATION",
            "confidence": 0.96,
            "evidence_json": {
                "signature_support_count": support,
                "signature_hash": hashlib.sha256(
                    sig.encode("utf-8")
                ).hexdigest(),
            },
            "status": "AUTO_RECOVERED",
            "review_reason": None,
            "source_snapshot_json": _snapshot(row, cols),
        }

    return {
        "record_id": rid,
        "source_hash": shash,
        "recovered_location": None,
        "recovery_method": "NO_DETERMINISTIC_LOCATION",
        "confidence": 0.0,
        "evidence_json": {
            "text_columns_checked": list(text_cols),
        },
        "status": "UNRECOVERED",
        "review_reason": "No deterministic geography found",
        "source_snapshot_json": _snapshot(row, cols),
    }

UPSERT_SQL = text(f"""
    INSERT INTO {RECOVERY_TABLE} (
        record_id,
        source_hash,
        recovered_location,
        recovery_method,
        confidence,
        evidence_json,
        status,
        review_reason,
        source_snapshot_json,
        created_at,
        updated_at
    ) VALUES (
        :record_id,
        :source_hash,
        :recovered_location,
        :recovery_method,
        :confidence,
        CAST(:evidence_json AS JSONB),
        :status,
        :review_reason,
        CAST(:source_snapshot_json AS JSONB),
        NOW(),
        NOW()
    )
    ON CONFLICT (record_id, source_hash)
    DO UPDATE SET
        recovered_location = EXCLUDED.recovered_location,
        recovery_method = EXCLUDED.recovery_method,
        confidence = EXCLUDED.confidence,
        evidence_json = EXCLUDED.evidence_json,
        status = EXCLUDED.status,
        review_reason = EXCLUDED.review_reason,
        source_snapshot_json = EXCLUDED.source_snapshot_json,
        updated_at = NOW()
""")

def write_batch(engine, results):
    params = []

    for result in results:
        p = dict(result)
        p["evidence_json"] = json.dumps(
            result.get("evidence_json") or {},
            ensure_ascii=False,
            default=str,
        )
        p["source_snapshot_json"] = json.dumps(
            result.get("source_snapshot_json") or {},
            ensure_ascii=False,
            default=str,
        )
        params.append(p)

    if not params:
        return

    with engine.begin() as c:
        c.execute(UPSERT_SQL, params)

def run_recovery(engine, batch_size=BATCH_SIZE):
    if not phase5.table_exists(engine, SOURCE_TABLE):
        raise RuntimeError(f"{SOURCE_TABLE} not found")

    ensure_table(engine)

    cols = source_columns(engine)
    if "record_id" not in cols:
        raise RuntimeError("record_id column required")

    total = count_rows(engine, SOURCE_TABLE)

    print("RECOVERY ENGINE:", VERSION, flush=True)
    print("Source rows:", total, flush=True)
    print("Columns inspected:", cols, flush=True)
    print("Building fast deterministic signature map...", flush=True)

    signature_map = build_signature_map(
        engine,
        cols,
        batch_size=batch_size,
    )

    print(
        "Deterministic signature map entries:",
        len(signature_map),
        flush=True,
    )

    counts = Counter()
    locations = Counter()
    processed = 0

    for offset in range(0, total, batch_size):
        rows = fetch_rows(engine, cols, offset, batch_size)

        batch_results = []

        for row in rows:
            result = recover_row(
                row,
                cols,
                signature_map,
            )

            batch_results.append(result)
            counts[result["status"]] += 1

            if result.get("recovered_location"):
                locations[
                    result["recovered_location"]
                ] += 1

        # Batch write makes the run resumable/idempotent.
        write_batch(engine, batch_results)

        processed += len(rows)

        print(
            f"Recovery pass: {processed}/{total}",
            flush=True,
        )

    summary = {
        "version": VERSION,
        "source_rows": total,
        "processed": processed,
        "status_counts": dict(counts),
        "unique_recovered_locations": len(locations),
        "top_recovered_locations": locations.most_common(30),
        "signature_map_entries": len(signature_map),
        "policy": {
            "source_table_unchanged": True,
            "matcher_unchanged": True,
            "auto_recovered_not_match_eligible_automatically": True,
            "recovery_table_only": True,
        },
    }

    print("")
    print("RECOVERY SUMMARY")
    print(
        json.dumps(
            summary,
            indent=2,
            default=str,
        )
    )

    return summary

def main():
    engine = phase5.create_main_engine()
    run_recovery(engine)

if __name__ == "__main__":
    main()
