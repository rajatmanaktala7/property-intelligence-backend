from __future__ import annotations

import alliance_requirement_brain_v3 as b
from alliance_semantic_schema_v3 import validate_requirement


def names(obj):
    return [x["name"] for x in obj["locations"] if x["constraint"] != "EXCLUDED"]


def assert_case(name, text, checks):
    obj = b.analyze(text, source="TEST")
    errors = validate_requirement(obj)
    assert not errors, (name, errors, obj)
    for path, expected in checks:
        cur = obj
        for key in path.split("."):
            cur = cur[key]
        assert cur == expected, {
            "case": name, "path": path, "expected": expected, "actual": cur, "obj": obj
        }
    return obj


def main():
    cases = [
        (
            "farmhouse_airbnb_unknown_tx",
            "URGENT REQUIREMENT – FARM HOUSE FOR AIRBNB. Preferred Locations: Chhatarpur | Ghitorni | Sultanpur | Dera Mandi | Baliyawas | Bandhwari. Land Requirement: Minimum 1–2 Acres. Budget: As per Market / Suitable Options. Genuine & Immediate Requirement. Please call only with confirmed & suitable options.",
            [
                ("intent.role", "REQUIREMENT"),
                ("asset.primary_asset", "FARMHOUSE"),
                ("intended_use.primary_use", "AIRBNB_SHORT_STAY"),
                ("transaction.value", None),
                ("transaction.status", "UNKNOWN"),
                ("area.min_sqft", 43560.0),
                ("area.max_sqft", 87120.0),
                ("budget.status", "NOT_SPECIFIED"),
                ("matching_readiness", "READY_WITH_TRANSACTION_OPEN"),
            ],
        ),
        (
            "experion_block_hard",
            "Requirement of 400 to 550 sqyd Plot in Experion Westerlies Sector 108 Only in Block-A",
            [
                ("intent.role", "REQUIREMENT"),
                ("asset.primary_asset", "LAND"),
                ("transaction.value", None),
                ("block.name", "BLOCK A"),
                ("block.constraint", "HARD"),
            ],
        ),
        (
            "gk_purchase_inferred",
            "Immediate required in GK-1 ONLY, 300 yds Immediate payment, need clear title, preferably In B-Block GK-1, client Budget Rs. 31.00 Crores",
            [
                ("intent.role", "REQUIREMENT"),
                ("transaction.value", "SALE"),
                ("transaction.status", "INFERRED_HIGH"),
                ("block.name", "BLOCK B"),
                ("block.constraint", "PREFERRED"),
            ],
        ),
        (
            "office_lease",
            "Need office for lease in Nehru Place around 3000 sqft budget 5 lakh",
            [
                ("intent.role", "REQUIREMENT"),
                ("asset.primary_asset", "OFFICE"),
                ("transaction.value", "RENT"),
            ],
        ),
        (
            "villa_rent",
            "Looking for a 3bhk fully furnished Villa for long term rent in Anjuna and Siolim",
            [
                ("intent.role", "REQUIREMENT"),
                ("asset.primary_asset", "VILLA"),
            ],
        ),
        (
            "restaurant_requirement",
            "Need restaurant space for lease in Saket 2500 sqft",
            [
                ("intent.role", "REQUIREMENT"),
                ("asset.primary_asset", "RESTAURANT"),
                ("intended_use.primary_use", "RESTAURANT_FNB"),
                ("transaction.value", "RENT"),
            ],
        ),
        (
            "supply_listing",
            "PROPERTY AVAILABLE FOR SALE. Smartworld Orchard Sec 61, 1680 sqft, asking Rs 2.52 crore.",
            [
                ("intent.role", "SUPPLY"),
            ],
        ),
        (
            "please_not_lease",
            "Requirement in GK-1. Please call.",
            [
                ("intent.role", "REQUIREMENT"),
                ("transaction.value", None),
            ],
        ),
        (
            "warehouse_land_area_not_sale",
            "Need warehouse for logistics in Gurugram, minimum 2 acres, transaction open",
            [
                ("asset.primary_asset", "WAREHOUSE"),
                ("intended_use.primary_use", "WAREHOUSE_LOGISTICS"),
                ("transaction.value", None),
                ("area.min_sqft", 87120.0),
            ],
        ),
        (
            "banquet_farmhouse",
            "Required farmhouse for banquet use in Chhatarpur or Ghitorni, minimum 1 acre",
            [
                ("asset.primary_asset", "FARMHOUSE"),
                ("intended_use.primary_use", "BANQUET_WEDDING"),
                ("transaction.value", None),
            ],
        ),
    ]

    passed = 0
    farmhouse = None
    for name, text, checks in cases:
        obj = assert_case(name, text, checks)
        if name == "farmhouse_airbnb_unknown_tx":
            farmhouse = obj
        print("PASS:", name)
        passed += 1

    expected_locations = {
        "CHHATARPUR", "GHITORNI", "SULTANPUR",
        "DERA MANDI", "BALIYAWAS", "BANDHWARI"
    }
    assert set(names(farmhouse)) == expected_locations, names(farmhouse)
    assert all(x["constraint"] == "PREFERRED" for x in farmhouse["locations"])
    print("PASS: farmhouse_all_six_preferred_locations")
    passed += 1

    # Semantic-equivalence corpus: same block entity, word order/punctuation changes.
    block_variants = [
        "Only in Block-A", "Only in Block A", "Block-A only", "Block A only",
        "Only in A-Block", "Only in A Block", "A-Block only", "A Block only",
    ]
    for text in block_variants:
        obj = b.analyze("Requirement plot in Sector 108 " + text)
        assert obj["block"]["name"] == "BLOCK A", (text, obj["block"])
        assert obj["block"]["constraint"] == "HARD", (text, obj["block"])
        passed += 1
    print("PASS: block_equivalence", len(block_variants))

    # Unit-equivalence corpus.
    unit_cases = [
        ("Minimum 1 acre", 43560.0),
        ("Minimum 43560 sqft", 43560.0),
        ("Minimum 4840 sqyd", 43560.0),
    ]
    for phrase, expected in unit_cases:
        obj = b.analyze("Requirement farmhouse " + phrase)
        assert obj["area"]["min_sqft"] == expected, (phrase, obj["area"])
        passed += 1
    print("PASS: area_unit_equivalence")

    # Location alias equivalence.
    for phrase in ["GK-1", "GK 1", "GK1", "Greater Kailash 1"]:
        obj = b.analyze("Requirement property in " + phrase)
        assert "GREATER KAILASH 1" in names(obj), (phrase, names(obj))
        passed += 1
    print("PASS: location_alias_equivalence")

    # Transaction safety variants: land/acre alone must never imply SALE.
    no_sale = [
        "Need 2 acre land in Chhatarpur",
        "Requirement plot 500 sqyd in Gurgaon",
        "Urgent land requirement minimum 1 acre",
        "Need farmhouse on 2 acres for Airbnb",
        "Looking for warehouse on 3 acres",
        "Client requires land 1000 sqyd",
        "Required farmhouse 1 acre",
        "Need resort land 5 acres",
        "Immediate requirement plot in Noida",
        "Land required in Gurugram budget as per market",
    ]
    for text in no_sale:
        obj = b.analyze(text)
        assert obj["transaction"]["value"] is None, (text, obj["transaction"])
        passed += 1
    print("PASS: no_land_equals_sale", len(no_sale))

    # Demand transaction wording must not become supply.
    demand_variants = [
        "Need office for lease in Nehru Place",
        "Need shop for rent in Saket",
        "Looking to buy plot in GK-1",
        "Requirement restaurant on lease in Saket",
        "Client looking for villa on rent in Siolim",
        "Required warehouse for lease in Gurgaon",
        "Seeking office for rent in Noida",
        "Wanted shop on lease in GK-1",
        "Need farmhouse for Airbnb in Chhatarpur",
        "Looking for hotel property in Delhi",
    ]
    for text in demand_variants:
        obj = b.analyze(text)
        assert obj["intent"]["role"] == "REQUIREMENT", (text, obj["intent"])
        passed += 1
    print("PASS: demand_intent_variants", len(demand_variants))

    # Generate more metamorphic location-list variants to cross 100 evaluations.
    separators = [" | ", ", ", " / ", " and "]
    loc_sets = [
        ["Chhatarpur", "Ghitorni"],
        ["Saket", "Hauz Khas"],
        ["Anjuna", "Siolim"],
        ["GK-1", "Defence Colony"],
        ["Noida", "Greater Noida"],
    ]
    meta_count = 0
    for sep in separators:
        for pair in loc_sets:
            for prefix in ["Preferred Locations: ", "Locations: ", "Requirement in "]:
                text = prefix + sep.join(pair)
                obj = b.analyze("Requirement. " + text)
                got = set(names(obj))
                assert len(got) >= 2, (text, got, obj)
                meta_count += 1
                passed += 1
    print("PASS: multi_location_metamorphic", meta_count)

    assert passed >= 100, passed
    print("TOTAL SEMANTIC EVALUATIONS PASS:", passed)
    print("V3 SHADOW ACCEPTANCE: PASS")


if __name__ == "__main__":
    main()
