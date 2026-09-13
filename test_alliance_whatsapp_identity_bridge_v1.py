from pathlib import Path
import importlib.util
P=Path(__file__).resolve().parent/"alliance_whatsapp_identity_bridge_v1.py"
spec=importlib.util.spec_from_file_location("wb",P)
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
assert m._phone("+91 98100 60315")=="9810060315"
assert m._phone("9810060315@s.whatsapp.net")=="9810060315"
assert m._phone("19958796943374@lid")==""
assert m._lid("19958796943374@lid")=="19958796943374"
assert m._lid("19958796943374")=="19958796943374"
obj={"participant":{"lid":"19958796943374@lid","phone":"+91 98100 60315"}}
lids,phones=m._pairs_from_object(obj)
assert {x[0] for x in lids}=={"19958796943374"}
assert {x[0] for x in phones}=={"9810060315"}
src=P.read_text(encoding="utf-8")
assert "UPDATE wa_" not in src
assert "DELETE FROM wa_" not in src
assert "INSERT INTO wa_" not in src
assert "RESOLVED_UNIQUE_EXACT_EVIDENCE" in src
assert "AMBIGUOUS_CONFLICT" in src
print("WHATSAPP UPSTREAM IDENTITY BRIDGE V1 ACCEPTANCE: PASS")
