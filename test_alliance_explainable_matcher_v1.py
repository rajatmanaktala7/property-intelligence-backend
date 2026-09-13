from alliance_explainable_matcher_v1 import parse_requirement, _rank_one

raw = """Requirement County Centre Court - Sec 88A 1565 sqft Tower 5 preference- unit 18 - floor 10-15 Target 2.15 cr Tower 4 - floor 10-15 Target 2.05 to 2.10 cr. DM with details"""
r = parse_requirement(raw)

assert r["project"] == "COUNTY CENTRE COURT", r
assert r["location"] == "SECTOR 88A", r
assert r["area_requested"] == 1565.0, r
assert r["area_unit"] == "SQFT", r
assert r["transaction"] is None, r
assert r["property_family"] == "RESIDENTIAL_APARTMENT", r
assert len(r["branches"]) == 2, r
assert r["branches"][0]["tower"] == "5", r
assert r["branches"][0]["floor_min"] == 10 and r["branches"][0]["floor_max"] == 15, r
assert r["branches"][0]["unit_preference"] == "18", r
assert r["branches"][0]["budget_max"] == 21_500_000, r
assert r["branches"][1]["tower"] == "4", r
assert r["branches"][1]["budget_min"] == 20_500_000, r
assert r["branches"][1]["budget_max"] == 21_000_000, r

# Missing project/area in property must be UNKNOWN, not a hard contradiction.
x = _rank_one(r, {
    "id":"P1","project":None,"location":"Sector 88A","family":"Residential Apartment",
    "transaction":None,"area":None,"price":20_800_000,"tower":"5","floor":12,"unit":"18",
    "verified":True,"availability":"AVAILABLE","updated":None
})
assert "project" in x["unknown"], x
assert "area" in x["unknown"], x
assert "project" not in x["hard_conflicts"], x
assert "area" not in x["hard_conflicts"], x

# Known conflicting location remains a genuine blocker.
y = _rank_one(r, {
    "id":"P2","project":"County Centre Court","location":"Sector 99","family":"Residential Apartment",
    "transaction":None,"area":1565,"price":20_800_000,"tower":"5","floor":12,"unit":"18",
    "verified":True,"availability":"AVAILABLE","updated":None
})
assert "location" in y["hard_conflicts"], y
assert y["category"] == "REJECTED_CONFLICT", y


r2 = parse_requirement("Requirement plot budget 90 to 100 lakh")
assert r2["budget_max"] == 10_000_000, r2

print("ALLIANCE EXPLAINABLE MATCHER V1: PASS")
