from __future__ import annotations

import json

import alliance_phase5_canonical_matcher as phase5
import alliance_whatsapp_live_clean_os as live_os


FROZEN_MARKERS = {
    "phase5": "5.2.0-LOCATION-PURITY-FULL-AUDIT",
}


def main():
    if phase5.VERSION != FROZEN_MARKERS["phase5"]:
        raise SystemExit(
            "FAIL: frozen matcher baseline changed: " + str(phase5.VERSION)
        )

    snap = live_os.audit_snapshot()

    print("WHATSAPP LIVE CLEAN OS AUDIT")
    print(json.dumps(snap, indent=2, default=str))

    failures = {}

    if snap["coverage"]["property_backlog"] != 0:
        failures["property_backlog"] = snap["coverage"]["property_backlog"]

    if snap["coverage"]["requirement_backlog"] != 0:
        failures["requirement_backlog"] = snap["coverage"]["requirement_backlog"]

    for k, v in snap["safety"].items():
        if int(v or 0) != 0:
            failures[k] = v

    if snap["operational"]["synced_properties"] > snap["operational"]["master_live_properties"]:
        failures["missing_master_property_projection"] = (
            snap["operational"]["synced_properties"]
            - snap["operational"]["master_live_properties"]
        )

    if snap["operational"]["staged_requirements"] > snap["operational"]["gate_live_requirements"]:
        failures["missing_requirement_gate_projection"] = (
            snap["operational"]["staged_requirements"]
            - snap["operational"]["gate_live_requirements"]
        )

    if failures:
        print("")
        print("AUDIT FAILURES:", failures)
        raise SystemExit(2)

    print("")
    print("LOSSLESS LIVE SOURCE COVERAGE: PASS")
    print("PROPERTY -> AVAILABILITY INTAKE PROJECTION: PASS")
    print("REQUIREMENT -> VERIFICATION GATE PROJECTION: PASS")
    print("IDEMPOTENT DEDUPLICATION: PASS")
    print("NO PROPERTY AUTO-VERIFICATION: PASS")
    print("NO REQUIREMENT AUTO-MATCHER PROMOTION: PASS")
    print("FROZEN MATCHER BASELINE: PASS")
    print("WHATSAPP LIVE CLEAN OS AUDIT: PASS")


if __name__ == "__main__":
    main()
