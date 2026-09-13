from pathlib import Path
P=Path("ALLIANCE_FEATURE_FREEZE_TEAM_READY.md")
s=P.read_text(encoding="utf-8")
required=[
"Status: ACTIVE","Baseline: 5f587fe","Matcher authority remains MASTER_ONLY",
"pi_master_properties_v711","Raw/parallel WhatsApp records must never become matcher candidates",
"Human-verified requirement decisions remain protected","AI must never auto-enable matcher eligibility",
"Opaque WhatsApp IDs must never be guessed into phone numbers","Contacts remain staff-only",
"Requirement gold benchmark >= 98% overall","Critical semantic fields >= 99%",
"Zero invented contacts, budgets, locations, or phone identities",
"CERTIFICATION_IN_PROGRESS, not TEAM_READY"]
for x in required: assert x in s, x
print("ALLIANCE TEAM READY FEATURE FREEZE V1: PASS")
