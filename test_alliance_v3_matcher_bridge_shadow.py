from __future__ import annotations

import alliance_requirement_brain_v3 as brain
import alliance_v3_matcher_bridge_shadow as bridge

def _assets(variants):
    return {x["semantic_asset"] for x in variants}

def main():
    majorda = (
        "URGENT RENTAL REQUIREMENT - NEAR MAJORDA BEACH. "
        "2 BHK / 3 BHK FLAT OR BUNGALOW REQUIRED. "
        "For Construction Company Engineers. 5-7 Male Engineers. "
        "Long-term stay: 2-3 Years. Furnished / Unfurnished - Both OK. "
        "Budget: Market Rate. Preferred Location: Near Majorda Beach."
    )
    m = bridge.analyze_and_build(majorda)
    assert m["status"] == "READY", m
    assert _assets(m["variants"]) == {"APARTMENT", "VILLA"}, m["variants"]
    for v in m["variants"]:
        assert v["transaction"] == "RENT"
        assert v["primary_locations"] == ["MAJORDA"]
        assert v["location_only"] is False
    amap = {v["semantic_asset"]:(v["family"],v["subtype"]) for v in m["variants"]}
    assert amap["APARTMENT"] == ("RESIDENTIAL","APARTMENT")
    assert amap["VILLA"] == ("RESIDENTIAL","VILLA")
    print("PASS: Majorda multi-asset -> frozen matcher variants")

    farmhouse = (
        "URGENT REQUIREMENT - FARM HOUSE FOR AIRBNB. "
        "Preferred Locations: Chhatarpur | Ghitorni | Sultanpur | Dera Mandi | Baliyawas | Bandhwari. "
        "Land Requirement: Minimum 1-2 Acres. Budget: As per Market."
    )
    f = bridge.analyze_and_build(farmhouse)
    assert f["status"] == "BLOCKED"
    assert f["reason"] == "TRANSACTION_REQUIRES_HUMAN_CONFIRMATION"
    assert f["variants"] == []
    print("PASS: farmhouse UNKNOWN transaction stays blocked")

    villa = bridge.adapt_candidate({
        "record_id":"V1",
        "description":"3 BHK bungalow near Majorda Beach",
        "location":"MAJORDA",
        "transaction":"RENT",
        "family":"LAND",
        "subtype":"LAND",
        "area_sqft":2400,
        "verification":"UNVERIFIED",
        "availability_status":"UNKNOWN",
    })
    assert villa["semantic_asset"] == "VILLA", villa
    assert villa["family"] == "RESIDENTIAL"
    assert villa["subtype"] == "VILLA"
    print("PASS: property semantic reprofile corrects frozen compatibility bucket")

    flat = bridge.adapt_candidate({
        "record_id":"A1",
        "description":"2 BHK flat near Majorda",
        "location":"MAJORDA",
        "transaction":"RENT",
        "family":"RESIDENTIAL",
        "subtype":"APARTMENT",
        "area_sqft":1200,
        "verification":"UNVERIFIED",
        "availability_status":"UNKNOWN",
    })
    assert flat["semantic_asset"] == "APARTMENT", flat
    assert flat["family"] == "RESIDENTIAL"
    assert flat["subtype"] == "APARTMENT"
    print("PASS: apartment property semantic profile")

    assert bridge.FROZEN_COMPAT["FARMHOUSE"] == ("LAND","LAND")
    print("PASS: farmhouse semantic truth separated from frozen compatibility")

    current = brain.analyze(majorda, source="BRIDGE_TEST")
    acceptable = {x["asset"] for x in current["asset"].get("acceptable_assets") or []}
    assert {"APARTMENT","VILLA"}.issubset(acceptable)
    print("PASS: current V3 semantic brain contract")
    print("V3 MATCHER BRIDGE SHADOW ACCEPTANCE: PASS")

if __name__ == "__main__":
    main()
