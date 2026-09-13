from alliance_master_inventory_role_firewall_v1 import classify_inventory_role

cases = [
    ("Restaurant on lease in panjim Rent 1.20 lakhs.", "PROPERTY_SUPPLY"),
    ("Available for Lease - Prime Commercial Shop, Panjim. Suitable for restaurant.", "PROPERTY_SUPPLY"),
    ("55sqm shop available for rent in panjim double height.", "PROPERTY_SUPPLY"),
    ("Need Restaurant for rent long term in Panaji.", "PROPERTY_DEMAND"),
    ("WANT PLACE FOR RENT IN PANJIM CITY For Cafe purpose.", "PROPERTY_DEMAND"),
    ("Need 2 bhk unfurnished flat near panaji market for restaurant staff.", "PROPERTY_DEMAND"),
    ("Urgent Requirement. Need Minimum 15 rooms premium Hotel for lease.", "PROPERTY_DEMAND"),
]
for raw, expected in cases:
    got = classify_inventory_role(raw)
    assert got["role"] == expected, (raw, got)

ctx = classify_inventory_role("2BHK apartment near restaurant for staff")
assert ctx["restaurant_context_only"] is True, ctx
assert ctx["restaurant_asset_allowed"] is False, ctx

print("ALLIANCE MASTER INVENTORY ROLE FIREWALL V1: PASS")
