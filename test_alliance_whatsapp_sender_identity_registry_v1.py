from pathlib import Path
import importlib.util
P=Path(__file__).resolve().parent/'alliance_whatsapp_sender_identity_registry_v1.py'
spec=importlib.util.spec_from_file_location('r',P)
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
assert m._phone('+91 98100 60315')=='9810060315'
assert m._phone('19958796943374')==''
assert m._opaque('19958796943374@lid')=='19958796943374'
assert m._opaque('9810060315@s.whatsapp.net')==''
assert m.REGISTRY_TABLE=='pi_whatsapp_sender_identity_registry_v1'
print('WHATSAPP SENDER IDENTITY REGISTRY V1 ACCEPTANCE: PASS')

assert m.VERSION.startswith("1.1.0")
src=P.read_text(encoding="utf-8")
assert "inspect(engine)" in src
assert "information_schema.tables" not in src
