from alliance_regional_newspaper_authority_v1 import *
assert MATCHER_SOURCE_CONTRACT=="MASTER_ONLY"
assert PROPERTY_AUTHORITY=="pi_master_properties_v711"
assert REQUIREMENT_AUTHORITY=="pi_requirement_gate_v1191"
for row,want in [
({"city":"Goa","location":"Mapusa"},"GOA"),({"location":"Panjim"},"GOA"),({"location":"Thivim"},"GOA"),
({"city":"Delhi NCR","location":"Gurugram"},"DELHI_NCR"),({"location":"Noida Sector 62"},"DELHI_NCR"),
({"location":"Mumbai"},"OTHER"),({"location":"Goa and Delhi NCR"},"REVIEW_REQUIRED"),({"location":"Unknown"},"REVIEW_REQUIRED")]:
    got=classify_region(row)["market_region"]; assert got==want,(row,got,want)
assert classify_region({"description":"occupancy certificate ready"})["market_region"]=="REVIEW_REQUIRED"
print("ALLIANCE REGIONAL + NEWSPAPER AUTHORITY V1: PASS")
