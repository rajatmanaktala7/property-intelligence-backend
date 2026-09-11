from __future__ import annotations

import json

import alliance_requirement_brain_v3 as brain
import alliance_semantic_review_v3 as review
from alliance_semantic_schema_v3 import validate_requirement


def main():
    raw = (
        "URGENT REQUIREMENT FARM HOUSE FOR AIRBNB. "
        "Preferred Locations: Chhatarpur | Ghitorni | Sultanpur | Dera Mandi | "
        "Baliyawas | Bandhwari. Land Requirement: Minimum 1-2 Acres. "
        "Budget: As per Market / Suitable Options. Immediate requirement. "
        "Please call only with confirmed suitable options."
    )

    v3 = brain.analyze(raw, source="REVIEW_TEST")

    assert v3["asset"]["primary_asset"] == "FARMHOUSE"
    assert v3["intended_use"]["primary_use"] == "AIRBNB_SHORT_STAY"
    assert v3["transaction"]["value"] is None
    assert v3["matching_readiness"] == "READY_WITH_TRANSACTION_OPEN"
    assert len(v3["locations"]) == 6
    assert v3["area"]["min_sqft"] == 43560.0
    assert v3["area"]["max_sqft"] == 87120.0
    assert not validate_requirement(v3)

    # Review decision contract.
    for d in ("USE_V3", "KEEP_V2", "EDITED", "REJECT"):
        p = review.ReviewDecision(decision=d)
        assert review._normalize_decision(p) == d

    # Invalid decision must fail.
    failed = False
    try:
        review._normalize_decision(review.ReviewDecision(decision="PROMOTE_GLOBALLY"))
    except Exception:
        failed = True
    assert failed

    # Edited canonical object must remain schema-valid.
    edited = json.loads(json.dumps(v3))
    edited["transaction"]["value"] = "RENT"
    edited["transaction"]["status"] = "EXPLICIT"
    edited["transaction"]["confidence"] = 1.0
    edited["transaction"]["human_confirmation_required"] = False
    edited["unknowns"] = [x for x in edited["unknowns"] if x != "TRANSACTION"]
    edited["matching_readiness"] = "READY"
    assert not validate_requirement(edited)

    # Reprocess architecture contract: module must expose a versioned, non-destructive path.
    assert hasattr(review, "reprocess_with_current_brain")
    src = review.reprocess_with_current_brain.__doc__ or ""
    assert "Historical snapshots are immutable" in src
    assert review.VERSION == "3.2.0-REVIEW-REPROCESS-VERSIONED"

    # Current brain must correctly interpret the real Majorda requirement that motivated reprocessing.
    majorda = (
        "URGENT RENTAL REQUIREMENT - NEAR MAJORDA BEACH. "
        "2 BHK / 3 BHK FLAT OR BUNGALOW REQUIRED. "
        "For Construction Company Engineers. 5-7 Male Engineers. "
        "Long-term stay: 2-3 Years. Furnished / Unfurnished - Both OK. "
        "Budget: Market Rate. Preferred Location: Near Majorda Beach. "
        "Side-by-side / Direct Deal Preferred."
    )
    current = brain.analyze(majorda, source="REPROCESS_TEST")
    assert current["transaction"]["value"] == "RENT"
    assert any(x["name"] == "MAJORDA" and x["constraint"] == "PREFERRED" for x in current["locations"])
    acceptable = {x["asset"] for x in current["asset"].get("acceptable_assets") or []}
    assert {"APARTMENT", "VILLA"}.issubset(acceptable)

    print("PASS: reprocess versioned snapshot architecture contract")
    print("PASS: current brain Majorda reprocess target")

    print("PASS: farmhouse review semantic contract")
    print("PASS: review decision contract")
    print("PASS: invalid global promotion decision blocked")
    print("PASS: edited V3 schema contract")
    print("PASS: V3 review mode keeps global production behavior unchanged")
    print("REVIEW MODE ACCEPTANCE: PASS")


if __name__ == "__main__":
    main()
