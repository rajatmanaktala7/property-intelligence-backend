from alliance_requirement_advisor_v1 import build_advice
req={"location":"PANAJI","transaction":"RENT","property_family":"COMMERCIAL"}
exact=[{"record_id":"P1","score":96.0,"unknown":["area"],"conflicts":[]}]
o=build_advice(req,exact,[],[],{"PROPERTY_SUPPLY":10,"PROPERTY_DEMAND":6})
assert o["decision"]=="VERIFY_BEFORE_SHARE",o
assert o["inventory_role_quality"]["demand_excluded"]==6,o
assert build_advice(req,[],[],[],{})["decision"]=="NO_SAFE_MATCH"
assert build_advice({"transaction":"RENT","property_family":"COMMERCIAL"},[],[],[],{})["decision"]=="CLARIFY_CLIENT"
print("ALLIANCE REQUIREMENT ADVISOR V1.0.2: PASS")
