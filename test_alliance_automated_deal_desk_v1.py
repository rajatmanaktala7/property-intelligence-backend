from pathlib import Path
s=Path("alliance_automated_deal_desk_v1.py").read_text(encoding="utf-8")
for x in [
'VERSION = "1.0.0-AUTOMATED-DEAL-DESK"',
'phase5.run_match',
'pi_master_properties_v711',
'pi_requirement_gate_v1191',
'pi_master_source_links_v711',
'wa_messages',
'EVIDENCE_ONLY_NO_GUESSING',
'draft_requires_team_approval',
'automatic_send":False',
'/alliance/primary/deal-desk',
'/api/alliance/deal-desk-v1/run',
'core.need_login(req)',
'Open WhatsApp with Draft',
'EXACT_VERIFIED',
'EXACT_NEEDS_VERIFICATION',
'ALTERNATIVE'
]:
    assert x in s,x
for banned in [
'UPDATE pi_master_properties_v711',
'DELETE FROM pi_master_properties_v711',
'TRUNCATE pi_master_properties_v711',
'UPDATE pi_requirement_gate_v1191',
'DELETE FROM pi_requirement_gate_v1191',
'TRUNCATE pi_requirement_gate_v1191',
'parallel_whatsapp_match_candidates'
]:
    assert banned not in s,banned
print("ALLIANCE AUTOMATED DEAL DESK V1: PASS")
