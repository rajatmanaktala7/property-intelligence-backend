import importlib.util
from pathlib import Path
P=Path(__file__).resolve().parent/"alliance_semantic_auto_trainer_v4.py"
spec=importlib.util.spec_from_file_location("v4",P)
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
assert m._norm_tx("purchase")=="SALE"
assert m._norm_tx("rent")=="LEASE"
assert m._explicit_locations("Need plot in Mapusa Thivim Comvale")==["MAPUSA","THIVIM","COLVALE"]
assert m._phones("call 9810060315 budget 1 cr")==["9810060315"]
assert m.NO_LIMIT_RE.search("budget no limit")
src=P.read_text(encoding="utf-8")
for token in ["HUMAN_VERIFIED_PROTECTED","CERTIFIED_98_PLUS","MIN_GOLD = 100","TARGET_CRITICAL = 98.0","TARGET_OVERALL = 98.0","SEMANTIC_AUTO_CORRECTION_V4","POSSIBLE_SUPPLY_NOT_REQUIREMENT","PHONE_BUDGET_COLLISION","NO_LIMIT_BUDGET_CONTRADICTION"]:
    assert token in src, token
assert "matcher_eligible=TRUE" not in src
assert "classification='VERIFIED ACTIVE'" not in src
print("ALLIANCE SEMANTIC AUTO-TRAINER V4 ACCEPTANCE: PASS")
