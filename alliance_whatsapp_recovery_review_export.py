from __future__ import annotations

import csv
from pathlib import Path
from sqlalchemy import text

import alliance_phase5_canonical_matcher as phase5
import alliance_whatsapp_recovery_confidence as confidence


def main():
    engine = phase5.create_main_engine()

    out = Path("/tmp/alliance_location_promotion_review.csv")

    sql = text(f"""
        SELECT
            record_id,
            recovered_location,
            confidence_score,
            confidence_band,
            evidence_type,
            evidence_field,
            evidence_excerpt,
            queue_status,
            promotion_eligible,
            review_reason
        FROM {confidence.QUEUE_TABLE}
        WHERE queue_status IN ('READY_FOR_REVIEW', 'NEEDS_REVIEW')
        ORDER BY
            promotion_eligible DESC,
            confidence_score DESC,
            recovered_location,
            record_id
    """)

    with engine.connect() as c:
        rows = [dict(r) for r in c.execute(sql).mappings().all()]

    fields = [
        "record_id",
        "recovered_location",
        "confidence_score",
        "confidence_band",
        "evidence_type",
        "evidence_field",
        "evidence_excerpt",
        "queue_status",
        "promotion_eligible",
        "review_reason",
    ]

    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    print("REVIEW CSV:", out)
    print("Rows:", len(rows))
    print("No source inventory changes were made.")


if __name__ == "__main__":
    main()
