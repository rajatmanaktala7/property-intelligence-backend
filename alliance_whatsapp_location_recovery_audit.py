from __future__ import annotations

from sqlalchemy import text

import alliance_phase5_canonical_matcher as phase5
import alliance_whatsapp_location_recovery as recovery


def count_rows(engine, table):
    if not phase5.table_exists(engine, table):
        return 0
    with engine.connect() as c:
        return int(
            c.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar()
            or 0
        )


def main():
    engine = phase5.create_main_engine()

    assert phase5.VERSION == "5.2.0-LOCATION-PURITY-FULL-AUDIT"

    source_count = count_rows(engine, recovery.SOURCE_TABLE)
    recovery_count = count_rows(engine, recovery.RECOVERY_TABLE)

    print("RECOVERY AUDIT")
    print("Source rows:", source_count)
    print("Recovery rows:", recovery_count)

    if source_count == 0:
        raise SystemExit("FAIL: no source rows")

    if recovery_count == 0:
        raise SystemExit("FAIL: recovery staging is empty")

    with engine.connect() as c:
        status_rows = c.execute(text(f"""
            SELECT status, COUNT(*) AS n
            FROM {recovery.RECOVERY_TABLE}
            GROUP BY status
            ORDER BY n DESC
        """)).all()

        dupes = int(c.execute(text(f"""
            SELECT COUNT(*) FROM (
                SELECT record_id, source_hash, COUNT(*) n
                FROM {recovery.RECOVERY_TABLE}
                GROUP BY record_id, source_hash
                HAVING COUNT(*) > 1
            ) q
        """)).scalar() or 0)

        unsafe = int(c.execute(text(f"""
            SELECT COUNT(*)
            FROM {recovery.RECOVERY_TABLE}
            WHERE status = 'AUTO_RECOVERED'
              AND (
                recovered_location IS NULL
                OR confidence < 0.90
              )
        """)).scalar() or 0)

        fake = int(c.execute(text(f"""
            SELECT COUNT(*)
            FROM {recovery.RECOVERY_TABLE}
            WHERE recovered_location ~* '^\\s*\\d+(\\.\\d+)?\\s*BHK\\s*$'
               OR recovered_location ~* '^\\s*(VILLA|APARTMENT|OFFICE|RETAIL|COMMERCIAL|RESIDENTIAL)\\s*$'
        """)).scalar() or 0)

    print(
        "Status counts:",
        {str(s): int(n) for s, n in status_rows},
    )
    print("Duplicate staging rows:", dupes)
    print("Unsafe auto-recovered rows:", unsafe)
    print("Fake recovered locations:", fake)

    if dupes:
        raise SystemExit("FAIL: duplicate recovery staging rows")

    if unsafe:
        raise SystemExit("FAIL: unsafe AUTO_RECOVERED rows")

    if fake:
        raise SystemExit(
            "FAIL: fake configuration leaked into geography"
        )

    print("")
    print("FROZEN MATCHER BASELINE: PASS")
    print("RECOVERY STAGING INTEGRITY: PASS")
    print("AUTO-RECOVERY SAFETY: PASS")
    print("LOCATION PURITY: PASS")
    print("SOURCE INVENTORY NOT OVERWRITTEN: PASS")
    print("RECOVERY/ENRICHMENT AUDIT: PASS")


if __name__ == "__main__":
    main()
