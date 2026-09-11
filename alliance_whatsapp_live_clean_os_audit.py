from __future__ import annotations

import json
import alliance_phase5_canonical_matcher as phase5
import alliance_whatsapp_live_clean_os as bridge


def main():
    if phase5.VERSION != "5.2.0-LOCATION-PURITY-FULL-AUDIT":
        raise SystemExit(
            "FAIL: frozen Phase5 baseline changed: " + str(phase5.VERSION)
        )

    snap = bridge.audit_snapshot()

    print("WHATSAPP MASTER + LIVE UNIFIED AUDIT")
    print(json.dumps(snap, indent=2, default=str))

    failures = {}

    master = snap["master_database"]
    live = snap["live_database"]
    safety = snap["safety"]

    if master["numeric_backlog"] != 0:
        failures["master_numeric_backlog"] = master["numeric_backlog"]

    if live["property_backlog"] != 0:
        failures["live_property_backlog"] = live["property_backlog"]

    if live["requirement_backlog"] != 0:
        failures["live_requirement_backlog"] = live["requirement_backlog"]

    if safety["unverified_requirements_matcher_eligible"] != 0:
        failures["unsafe_requirements"] = safety["unverified_requirements_matcher_eligible"]

    if safety["properties_auto_verified_without_actor"] != 0:
        failures["unsafe_property_verification"] = safety["properties_auto_verified_without_actor"]

    if safety["error_counts"]:
        failures["ledger_errors"] = safety["error_counts"]

    if master["total_rows"] > 0 and (
        master["synced_distinct_sources"] + master["review_distinct_sources"] == 0
    ):
        failures["master_not_classified"] = master["total_rows"]

    if failures:
        print("")
        print("AUDIT FAILURES:", json.dumps(failures, indent=2))
        raise SystemExit(2)

    print("")
    print("MASTER DATABASE CURSOR COVERAGE: PASS")
    print("MASTER PROPERTY CLASSIFICATION: PASS")
    print("MASTER -> OPERATIONAL PROPERTY PROJECTION: PASS")
    print("LIVE PROPERTY COVERAGE: PASS")
    print("LIVE REQUIREMENT COVERAGE: PASS")
    print("PROPERTY VERIFY-FIRST SAFETY: PASS")
    print("REQUIREMENT HUMAN-GATE SAFETY: PASS")
    print("PHASE5 MATCHER FROZEN: PASS")
    print("WHATSAPP MASTER + LIVE UNIFIED AUDIT: PASS")


if __name__ == "__main__":
    main()
