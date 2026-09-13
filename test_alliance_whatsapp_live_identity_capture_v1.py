from pathlib import Path
import importlib.util
P=Path(__file__).resolve().parent/"alliance_whatsapp_live_identity_capture_v1.py"
spec=importlib.util.spec_from_file_location("cap",P)
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
payload={"external_message_id":"abc","group_jid":"1203630@g.us",
         "participant":{"lid":"19958796943374@lid","phone_jid":"9810060315@s.whatsapp.net"},
         "sender_name":"Kedar"}
snap=m.identity_snapshot(payload)
lids,phones=m._extract_pairs(snap)
assert {x[0] for x in lids}=={"19958796943374"}
assert {x[0] for x in phones}=={"9810060315"}
assert m._phone("19958796943374@lid")==""
assert m._phone("9810060315@s.whatsapp.net")=="9810060315"
src=P.read_text(encoding="utf-8")
assert "pi_whatsapp_live_identity_events_v1" in src
assert "LIVE_INGEST_SAME_PAYLOAD" in src
assert "AMBIGUOUS_CONFLICT" in src
assert "UPDATE wa_" not in src
assert "DELETE FROM wa_" not in src
print("WHATSAPP LIVE IDENTITY CAPTURE V1.5.1 ACCEPTANCE: PASS")
