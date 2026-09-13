from pathlib import Path
s = Path("alliance_dashboard_authority_v1.py").read_text(encoding="utf-8")
entry = Path("production_entrypoint.py").read_text(encoding="utf-8")
for token in [
    'VERSION = "1.0.0-CANONICAL-ALLIANCE-DASHBOARD"',
    'CANONICAL_ALLIANCE_DASHBOARD_V1',
    'pi_master_properties_v711',
    'pi_requirement_gate_v1191',
    'MASTER_ONLY',
    'legacy_dashboard_replaced',
    'request.url.path != "/alliance/primary"',
    'Cache-Control',
    '/api/alliance/dashboard-authority-v1/status',
    '/api/alliance/dashboard-authority-v1/public-status',
    'Marketing / Contact Master',
    'Automated Deal Desk',
]:
    assert token in s, token
for stale in [
    '27/27 dashboard routes PASS',
    '12.3.8-COMMAND-BAR-DAY-PLAN-BOTTOM',
]:
    assert stale not in s, stale
assert "ALLIANCE_MASTER_CONSOLIDATION_V1" in entry
assert "ALLIANCE_DASHBOARD_AUTHORITY_V1" in entry
assert entry.index("        # ALLIANCE_DASHBOARD_AUTHORITY_V1") > entry.index("        # ALLIANCE_MASTER_CONSOLIDATION_V1")
for banned in [
    "DELETE FROM pi_master_properties_v711",
    "TRUNCATE pi_master_properties_v711",
    "DELETE FROM pi_requirement_gate_v1191",
    "TRUNCATE pi_requirement_gate_v1191",
]:
    assert banned not in s, banned
print("ALLIANCE CANONICAL DASHBOARD AUTHORITY V1: PASS")
