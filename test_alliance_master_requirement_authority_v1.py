from pathlib import Path
import importlib.util
ROOT=Path(__file__).resolve().parent
MOD=ROOT/"alliance_master_requirement_authority_v1.py"
def load():
    spec=importlib.util.spec_from_file_location("mra",MOD); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def test_contract():
    m=load()
    assert m.VERSION.startswith("1.2.0")
    assert m.MASTER_REQUIREMENT_TABLE=="pi_requirement_gate_v1191"
    assert m.MASTER_PROPERTY_TABLE=="pi_master_properties_v711"
def test_sender_policy():
    m=load()
    assert m._phone("+91 98112 34567")=="9811234567"
    s=MOD.read_text(encoding="utf-8")
    assert "wa_messages" in s and "sender_phone" in s and "wa_requirements" in s
    assert "WHATSAPP_SENDER" in s and "ALWAYS_SHOW_WHATSAPP_SENDER" in s
def test_property_sender_lineage():
    s=MOD.read_text(encoding="utf-8")
    assert "pi_master_source_links_v711" in s
    assert "wa_properties" in s
    assert "WhatsApp Sender" in s
def test_security_and_no_business_writes():
    s=MOD.read_text(encoding="utf-8")
    assert "_auth(core,request)" in s and "AUTHENTICATED_STAFF_ONLY" in s
    u=s.upper()
    for token in ("INSERT INTO PI_REQUIREMENT","UPDATE PI_REQUIREMENT","DELETE FROM PI_REQUIREMENT",
                  "INSERT INTO PI_MASTER_PROPERTIES","UPDATE PI_MASTER_PROPERTIES","DELETE FROM PI_MASTER_PROPERTIES",
                  "INSERT INTO WA_","UPDATE WA_","DELETE FROM WA_"):
        assert token not in u
if __name__=="__main__":
    for f in (test_contract,test_sender_policy,test_property_sender_lineage,test_security_and_no_business_writes):
        f(); print("PASS:",f.__name__)
    print("MASTER REQUIREMENT AUTHORITY V1.2 ACCEPTANCE: PASS")
