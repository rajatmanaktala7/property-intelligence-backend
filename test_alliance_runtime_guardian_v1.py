from pathlib import Path
s = Path("alliance_runtime_guardian_v1.py").read_text(encoding="utf-8")
for token in [
    'VERSION = "1.0.1-RUNTIME-GUARDIAN-POST-REGISTRATION"',
    'INTERVAL_SECONDS = 300',
    'SAFE_ROUTE_ALIAS',
    'CRITICAL_ROUTE_MISSING',
    'MASTER_ONLY',
    '/api/alliance/runtime-guardian-v1/status',
    '/api/alliance/runtime-guardian-v1/audit',
    'worker_started',
    'audit_and_repair(app)',
]:
    assert token in s, token

for banned in [
    'DELETE FROM pi_master_properties_v711',
    'TRUNCATE pi_master_properties_v711',
    'DELETE FROM pi_requirement_gate_v1191',
    'TRUNCATE pi_requirement_gate_v1191',
]:
    assert banned not in s, banned

print("ALLIANCE RUNTIME GUARDIAN V1: PASS")

assert 'time.sleep(8)' in s
assert 'WAITING_FOR_FULL_ROUTER' in s
print("GUARDIAN POST-REGISTRATION ORDER: PASS")
