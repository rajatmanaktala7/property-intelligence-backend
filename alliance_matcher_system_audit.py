from __future__ import annotations

from collections import Counter
from sqlalchemy import text

import alliance_phase5_canonical_matcher as phase5
import alliance_whatsapp_first_match_v1 as engine


REGRESSION_REQUIREMENTS = [
    (
        "kothi_south_delhi_b",
        "Urgent Requirement Kothi 300Yd To 400Yd In South Delhi "
        "B Cataegory Colonies As Per Market Price Cheque Circle Rate "
        "Preferable Regards Sunil Kumar 9212451029 K S Associates",
        {
            "transaction": "SALE",
            "family": "RESIDENTIAL",
            "subtype": "VILLA",
            "area_min_sqft": 2700.0,
            "area_max_sqft": 3600.0,
            "budget_max": None,
            "location_scope": "REGION",
        },
    ),
    (
        "restaurant_rent",
        "Restaurant for rent in Saket 2000 sqft budget 4 lakh",
        {
            "transaction": "RENT",
            "family": "COMMERCIAL",
            "subtype": "RESTAURANT",
        },
    ),
    (
        "retail_sale",
        "High street retail shop for sale in Sector 63A 415 sqft 62.25 lakh",
        {
            "transaction": "SALE",
            "family": "COMMERCIAL",
            "subtype": "RETAIL",
            "location": "SECTOR 63A",
        },
    ),
    (
        "villa_goa",
        "Villa for sale in Siolim North Goa 400 sqm budget 10 crore",
        {
            "transaction": "SALE",
            "family": "RESIDENTIAL",
            "subtype": "VILLA",
        },
    ),
    (
        "office_rent",
        "Office on lease in Nehru Place 2500 sqft budget 3 lakh",
        {
            "transaction": "RENT",
            "family": "COMMERCIAL",
            "subtype": "OFFICE",
        },
    ),
    (
        "warehouse_rent",
        "Warehouse for rent in Sector 45 10000 sqft",
        {
            "transaction": "RENT",
            "family": "COMMERCIAL",
            "subtype": "WAREHOUSE",
        },
    ),
]


def _assert_regressions():
    failures = []

    for name, raw, expected in REGRESSION_REQUIREMENTS:
        req = phase5.parse_requirement(raw)

        for key, value in expected.items():
            actual = req.get(key)

            if isinstance(value, float):
                ok = actual is not None and abs(float(actual) - value) < 0.01
            else:
                ok = actual == value

            if not ok:
                failures.append(
                    f"{name}: {key} expected={value!r} actual={actual!r}"
                )

    # Phone + K must never become budget.
    if phase5.parse_budget("9212451029 K S Associates") != (None, None):
        failures.append("phone_number_k_budget_guard")

    # Plain yd conversion must work on property data too.
    if phase5.area_to_sqft("300yd") != 2700.0:
        failures.append("property_area_300yd_conversion")

    return failures


def _candidate_self_audit(candidates):
    reasons = Counter()
    failures = []
    contact_leaks = 0

    for p in candidates:
        req = {
            "raw": "SYNTHETIC_SELF_AUDIT",
            "primary_locations": [p.get("location")] if p.get("location") else [],
            "location": p.get("location"),
            "location_scope": "LOCALITY",
            "transaction": p.get("transaction"),
            "family": p.get("family"),
            "subtype": p.get("subtype"),
            "acceptable_subtypes": (
                [p.get("subtype")]
                if p.get("subtype")
                else []
            ),
            "area_min_sqft": p.get("area_sqft"),
            "area_max_sqft": p.get("area_sqft"),
            "budget_min": None,
            "budget_max": (
                p.get("price")
                if p.get("price_comparable")
                else None
            ),
        }

        ok, code, _ = phase5.eligible(req, p, "EXACT")

        if not ok:
            reasons[code] += 1

            if len(failures) < 100:
                failures.append({
                    "source_table": p.get("source_table"),
                    "record_id": p.get("record_id"),
                    "location": p.get("location"),
                    "transaction": p.get("transaction"),
                    "family": p.get("family"),
                    "subtype": p.get("subtype"),
                    "area_sqft": p.get("area_sqft"),
                    "reason": code,
                })

        payload = repr(phase5.sanitize_public_payload(p))

        if phase5.PHONE_RE.search(payload) or phase5.EMAIL_RE.search(payload):
            contact_leaks += 1

    return failures, dict(reasons), contact_leaks


def _raw_counts(db):
    out = {}

    with db.connect() as c:
        for table in ("pi_properties", "pi_whatsapp_property_master"):
            if phase5.table_exists(db, table):
                out[table] = int(
                    c.execute(
                        text(f'SELECT COUNT(*) FROM "{table}"')
                    ).scalar()
                    or 0
                )
            else:
                out[table] = 0

    return out


def main():
    failures = _assert_regressions()

    if failures:
        print("REGRESSION SUITE: FAIL")
        for x in failures:
            print(" -", x)
        raise SystemExit(2)

    print("REGRESSION SUITE: PASS")

    db = phase5.create_main_engine()

    raw_counts = _raw_counts(db)

    rows, loaded_counts = phase5.load_candidates(
        db,
        pi_limit=50000,
        wa_limit=50000,
    )

    candidates = phase5.dedupe_candidates(rows)

    self_failures, self_reasons, contact_leaks = _candidate_self_audit(
        candidates
    )

    print("")
    print("DATABASE-WIDE MATCHER AUDIT")
    print("Raw pi_properties:", raw_counts.get("pi_properties", 0))
    print(
        "Raw pi_whatsapp_property_master:",
        raw_counts.get("pi_whatsapp_property_master", 0),
    )
    print("Loaded canonical:", loaded_counts.get("pi_properties", 0))
    print(
        "Loaded WhatsApp:",
        loaded_counts.get("pi_whatsapp_property_master", 0),
    )
    print("Deduped candidates:", len(candidates))
    print("Self-match failures:", len(self_failures))
    print("Contact leaks:", contact_leaks)
    print("Self-match rejection reasons:", self_reasons)

    if self_failures:
        print("")
        print("FIRST SELF-MATCH FAILURES")
        for row in self_failures[:25]:
            print(row)

    if contact_leaks:
        raise SystemExit("FAIL: contact leak detected")

    if self_failures:
        raise SystemExit(
            "FAIL: normalized properties exist that cannot pass their own hard gates"
        )

    # End-to-end smoke run. We do not require inventory to exist for a sample.
    sample_queries = [
        "Kothi 300Yd To 400Yd in South Delhi B Category Colonies "
        "as per market price cheque circle rate",
        "Restaurant for rent in Saket 2000 sqft budget 4 lakh",
        "Retail shop for sale in Sector 63A 415 sqft 62.25 lakh",
    ]

    for q in sample_queries:
        result = engine.run_match(
            db,
            q,
            min_score=70,
            limit=10,
        )

        if "summary" not in result:
            raise SystemExit("FAIL: end-to-end matcher returned no summary")

    print("")
    print("END-TO-END WHATSAPP MATCHER SMOKE TEST: PASS")
    print("DATABASE-WIDE SELF-MATCH TEST: PASS")
    print("CONTACT LEAK TEST: PASS")
    print("SYSTEM AUDIT: PASS")


if __name__ == "__main__":
    main()
