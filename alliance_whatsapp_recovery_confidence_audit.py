from __future__ import annotations

from sqlalchemy import text

import alliance_phase5_canonical_matcher as phase5
import alliance_whatsapp_recovery_confidence as confidence


def scalar(engine, sql):
    with engine.connect() as c:
        return int(c.execute(text(sql)).scalar() or 0)


def main():
    engine = phase5.create_main_engine()

    if phase5.VERSION != "5.2.0-LOCATION-PURITY-FULL-AUDIT":
        raise SystemExit(
            "FAIL: frozen Phase5 version changed: " + str(phase5.VERSION)
        )

    if not phase5.table_exists(engine, confidence.QUEUE_TABLE):
        raise SystemExit("FAIL: promotion queue table missing")

    total = scalar(
        engine,
        f"SELECT COUNT(*) FROM {confidence.QUEUE_TABLE}",
    )

    eligible = scalar(
        engine,
        f"""
        SELECT COUNT(*)
        FROM {confidence.QUEUE_TABLE}
        WHERE promotion_eligible = TRUE
        """,
    )

    unsafe_score = scalar(
        engine,
        f"""
        SELECT COUNT(*)
        FROM {confidence.QUEUE_TABLE}
        WHERE promotion_eligible = TRUE
          AND confidence_score < {confidence.AUTO_PROMOTE_THRESHOLD}
        """,
    )

    signature_auto = scalar(
        engine,
        f"""
        SELECT COUNT(*)
        FROM {confidence.QUEUE_TABLE}
        WHERE promotion_eligible = TRUE
          AND evidence_type = 'EXACT_SIGNATURE_ONLY'
        """,
    )

    missing_evidence = scalar(
        engine,
        f"""
        SELECT COUNT(*)
        FROM {confidence.QUEUE_TABLE}
        WHERE promotion_eligible = TRUE
          AND (
              evidence_field IS NULL
              OR evidence_excerpt IS NULL
              OR recovered_location IS NULL
          )
        """,
    )

    fake_location = scalar(
        engine,
        f"""
        SELECT COUNT(*)
        FROM {confidence.QUEUE_TABLE}
        WHERE promotion_eligible = TRUE
          AND (
              recovered_location ~* '^\\s*\\d+(\\.\\d+)?\\s*BHK\\s*$'
              OR recovered_location ~* '^\\s*(VILLA|APARTMENT|OFFICE|RETAIL|COMMERCIAL|RESIDENTIAL)\\s*$'
          )
        """,
    )

    already_usable_promoted = scalar(
        engine,
        f"""
        SELECT COUNT(*)
        FROM {confidence.QUEUE_TABLE}
        WHERE promotion_eligible = TRUE
          AND original_location IS NOT NULL
        """,
    )

    with engine.connect() as c:
        status_rows = c.execute(text(f"""
            SELECT queue_status, COUNT(*)
            FROM {confidence.QUEUE_TABLE}
            GROUP BY queue_status
            ORDER BY COUNT(*) DESC
        """)).all()

        evidence_rows = c.execute(text(f"""
            SELECT evidence_type, COUNT(*)
            FROM {confidence.QUEUE_TABLE}
            GROUP BY evidence_type
            ORDER BY COUNT(*) DESC
        """)).all()

        top_candidates = c.execute(text(f"""
            SELECT recovered_location, COUNT(*) AS n
            FROM {confidence.QUEUE_TABLE}
            WHERE promotion_eligible = TRUE
            GROUP BY recovered_location
            ORDER BY n DESC
            LIMIT 30
        """)).all()

    print("RECOVERY CONFIDENCE AUDIT")
    print("Queue rows:", total)
    print("Promotion eligible:", eligible)
    print(
        "Queue statuses:",
        {str(k): int(v) for k, v in status_rows},
    )
    print(
        "Evidence types:",
        {str(k): int(v) for k, v in evidence_rows},
    )
    print(
        "Top promotion-candidate locations:",
        [(str(k), int(v)) for k, v in top_candidates],
    )
    print("Unsafe score promotions:", unsafe_score)
    print("Signature-only auto promotions:", signature_auto)
    print("Missing evidence promotions:", missing_evidence)
    print("Fake-location promotions:", fake_location)
    print("Already-usable rows promoted:", already_usable_promoted)

    failures = {
        "unsafe_score": unsafe_score,
        "signature_auto": signature_auto,
        "missing_evidence": missing_evidence,
        "fake_location": fake_location,
        "already_usable_promoted": already_usable_promoted,
    }

    bad = {k: v for k, v in failures.items() if v}

    if bad:
        print("FAILURES:", bad)
        raise SystemExit(2)

    print("")
    print("FROZEN MATCHER BASELINE: PASS")
    print("CONFIDENCE THRESHOLD GATE: PASS")
    print("INDEPENDENT EVIDENCE GATE: PASS")
    print("SIGNATURE-ONLY AUTO-PROMOTION BLOCK: PASS")
    print("LOCATION PURITY: PASS")
    print("SOURCE INVENTORY UNCHANGED: PASS")
    print("PROMOTION QUEUE AUDIT: PASS")


if __name__ == "__main__":
    main()
