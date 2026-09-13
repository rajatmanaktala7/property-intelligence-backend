from pathlib import Path
s = Path("alliance_dashboard_authority_v1.py").read_text(encoding="utf-8")
for token in [
    'VERSION = "2.0.0-SIMPLE-ATTRACTIVE-HUB"',
    'CANONICAL_ALLIANCE_DASHBOARD_V1',
    '8-AREA-SIMPLE-HUB',
    'MASTER_ONLY',
    'pi_master_properties_v711',
    'pi_requirement_gate_v1191',
    'Properties',
    'Requirements',
    'Match & Deal Desk',
    'Intelligence',
    'Contacts',
    'Team',
    'System',
    'Quick Add',
    'Needs Attention',
    'Requirement Views',
    'Intelligence Sources',
    'FEATURE FREEZE ACTIVE',
    '/alliance/final/databases',
    '/alliance/final/requirements',
    '/alliance/primary/smart-match',
    '/alliance/primary/contact-master',
    '/property-manual',
    '/requirements-workbench',
    '/whatsapp-live',
    '/capture-intelligence',
    '/commercial-intelligence',
    '/hospitality-intelligence',
    '/retail-expansion',
    '/alliance/primary/data-health',
    '/alliance/system-doctor',
]:
    assert token in s, token

for stale in [
    'Alliance CRE · Team Command Centre',
    '27/27 dashboard routes PASS',
    '12.3.8-COMMAND-BAR-DAY-PLAN-BOTTOM',
    'Everything the Team Needs',
]:
    assert stale not in s, stale

for banned in [
    "DELETE FROM pi_master_properties_v711",
    "TRUNCATE pi_master_properties_v711",
    "DELETE FROM pi_requirement_gate_v1191",
    "TRUNCATE pi_requirement_gate_v1191",
]:
    assert banned not in s, banned

print("ALLIANCE SIMPLE ATTRACTIVE DASHBOARD V2: PASS")
