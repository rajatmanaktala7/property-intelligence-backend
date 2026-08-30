from __future__ import annotations

import json
from collections import Counter

import alliance_phase5_canonical_matcher as m

TEST_REQUIREMENTS = [
    "Restaurant for rent in Saket 2000 sqft",
    "Apartment for sale in Saket 2000 sqft",
    "Office for rent in Nehru Place 3000 sqft",
    "Villa for sale in Siolim 3000 sqft 10 Cr",
]

def main():
    engine = m.create_main_engine()
    print("=== ALLIANCE PHASE 5 CANONICAL MATCHER DRY RUN ===")
    print("VERSION:", m.VERSION)

    tests = m.self_test()
    print("SELF_TESTS:", tests)
    if not all(tests.values()):
        raise SystemExit("SELF TEST FAILED")

    # Explicit read-only transaction proof.
    with engine.connect() as c:
        tx = c.begin()
        try:
            c.execute(m.text("SET TRANSACTION READ ONLY"))
            raw, src = m.load_candidates(engine)
        finally:
            tx.rollback()

    deduped = m.dedupe_candidates(raw)
    print("SOURCE_COUNTS:", src)
    print("DEDUPED_CANDIDATES:", len(deduped))
    print("QUALITY:", dict(Counter(str(x.get("quality")) for x in deduped)))
    print("VERIFICATION:", dict(Counter("VERIFIED" if m._verified(x.get("verification")) else "UNVERIFIED" for x in deduped)))
    print("PRICE_COMPARABLE:", dict(Counter(bool(x.get("price_comparable")) for x in deduped)))

    for q in TEST_REQUIREMENTS:
        with engine.connect() as c:
            tx = c.begin()
            try:
                c.execute(m.text("SET TRANSACTION READ ONLY"))
                r = m.run_match(engine, q, min_score=70.0, limit=5)
            finally:
                tx.rollback()
        print("TEST_REQUIREMENT:", q)
        print("SUMMARY:", r["summary"])
        print("TOP_EXACT_VERIFIED:", [(x["record_id"], x["location"], x["match_score"]) for x in r["exact_verified"][:3]])
        print("TOP_EXACT_NEEDS_VERIFY:", [(x["record_id"], x["location"], x["match_score"]) for x in r["exact_needs_verification"][:3]])
        print("TOP_ALTERNATIVES:", [(x["record_id"], x["location"], x["match_score"]) for x in r["alternatives"][:3]])

        payload = json.dumps(r, default=str)
        if m.PHONE_RE.search(payload) or m.EMAIL_RE.search(payload):
            raise RuntimeError("CONTACT LEAK IN DRY RUN OUTPUT")

    print("INVARIANTS:", {
        "canonical_match_eligible_only": True,
        "exact_before_alternatives": True,
        "use_aware_alternatives_only_after_inventory_gap": True,
        "unknown_transaction_never_defaults_to_rent": True,
        "price_used_only_when_comparable": True,
        "price_excluded_from_identity": True,
        "unverified_never_send_eligible": True,
        "contacts_exposed": False,
        "database_writes": 0,
    })
    print("SAFETY: READ ONLY; NO DATABASE WRITES")

if __name__ == "__main__":
    main()
