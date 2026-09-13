from pathlib import Path
P=Path("alliance_team_ready_certification_v1.py")
s=P.read_text(encoding="utf-8")
for x in [
'VERSION="1.0.0-TEAM-READY-GOLD-CERTIFICATION"',
'GOLD_TARGET=100','OVERALL_TARGET=98.0','CRITICAL_TARGET=99.0',
'pi_alliance_gold_cases_v1','pi_requirement_gate_v1191',
'ORDER BY md5(CAST(id AS text)','production_requirement_mutation":False',
'matcher_eligibility_auto_changed":False','matcher_contract_required":"MASTER_ONLY"',
'/alliance/primary/team-ready-certification','/api/alliance/team-ready-certification-v1/status',
'foundation._engine_from_core(core)']:
    assert x in s,x
for banned in ['UPDATE pi_requirement_gate_v1191','DELETE FROM pi_requirement_gate_v1191','TRUNCATE pi_requirement_gate_v1191','matcher_eligible=TRUE','matcher_eligible = TRUE']:
    assert banned not in s,banned
print("ALLIANCE TEAM-READY GOLD CERTIFICATION V1: PASS")
