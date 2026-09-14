from pathlib import Path
s=Path("alliance_app_rectifier_v1.py").read_text(encoding="utf-8")
for x in [
 "pi_requirement_gate_v1191","pi_master_properties_v711","/alliance/final/requirements",
 "/alliance/final/databases","/newspaper-v83","missing","tara","padding:4px 6px","line-height:1.15",
 "wa_properties","MASTER_ONLY","Research this asset","commercial_research_post","_remove_exact(app,\"/commercial-intelligence\",\"GET\")"
]:
    assert x in s,x
assert "font-size:" not in s
e=Path("production_entrypoint.py").read_text(encoding="utf-8")
assert "ALLIANCE_APP_RECTIFIER_V1" in e
print("ALLIANCE APP RECTIFIER V1.0.3: PASS")
