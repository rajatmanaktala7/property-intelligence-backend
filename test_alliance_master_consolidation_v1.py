from pathlib import Path
s=Path("alliance_master_consolidation_v1.py").read_text(encoding="utf-8")
for x in ["1.0.1-MASTER-CONSOLIDATION","pi_requirement_gate_v1191","pi_master_properties_v711","pi_alliance_contact_master_v1","MASTER_ONLY","PHONE_NOT_RECOVERABLE_FROM_SOURCE","EXACT_WHATSAPP_MESSAGE_SENDER","marketing_eligible BOOLEAN NOT NULL DEFAULT FALSE","/alliance/final/requirements","/alliance/primary/contact-master","Run Matcher","Newspaper Capture","Commercial","Hospitality","Retail","ALLIANCE_MASTER_CONSOLIDATION_V1"]: assert x in s,x
for x in ["UPDATE pi_requirement_gate_v1191","DELETE FROM pi_requirement_gate_v1191","TRUNCATE pi_requirement_gate_v1191","UPDATE pi_master_properties_v711","DELETE FROM pi_master_properties_v711","TRUNCATE pi_master_properties_v711"]: assert x not in s,x
d=Path("alliance_automated_deal_desk_v1.py").read_text(encoding="utf-8")
assert "MASTER_ONLY" in d and "automatic_send" in d
print("ALLIANCE MASTER CONSOLIDATION V1.0.1: PASS")
