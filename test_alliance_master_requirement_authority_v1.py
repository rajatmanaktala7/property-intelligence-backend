from pathlib import Path
import ast, importlib.util
ROOT=Path(__file__).resolve().parent
MOD=ROOT/"alliance_master_requirement_authority_v1.py"

def load():
    spec=importlib.util.spec_from_file_location("mra",MOD)
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def test_contract():
    m=load()
    assert m.VERSION.startswith("1.2.1")
    assert m.MASTER_REQUIREMENT_TABLE=="pi_requirement_gate_v1191"
    assert m.MASTER_PROPERTY_TABLE=="pi_master_properties_v711"

def test_sender_policy():
    m=load()
    assert m._phone("+91 98112 34567")=="9811234567"
    s=MOD.read_text(encoding="utf-8")
    assert "wa_messages" in s and "sender_phone" in s
    assert "WHATSAPP_SENDER" in s

def test_connection_scope_regression():
    s=MOD.read_text(encoding="utf-8")
    # The wa_messages query must execute inside its own active connection scope.
    needle='bymid.update({str(x["mid"]):dict(x) for x in c.execute(q,params).mappings()})'
    assert needle in s
    pos=s.index(needle)
    before=s[max(0,pos-900):pos]
    assert "with eng.connect() as c:" in before

def test_security_and_no_business_writes():
    s=MOD.read_text(encoding="utf-8")
    assert "_auth(core,request)" in s
    assert "AUTHENTICATED_STAFF_ONLY" in s
    u=s.upper()
    for token in ("INSERT INTO PI_REQUIREMENT","UPDATE PI_REQUIREMENT","DELETE FROM PI_REQUIREMENT",
                  "INSERT INTO PI_MASTER_PROPERTIES","UPDATE PI_MASTER_PROPERTIES","DELETE FROM PI_MASTER_PROPERTIES",
                  "INSERT INTO WA_","UPDATE WA_","DELETE FROM WA_"):
        assert token not in u

if __name__=="__main__":
    for f in (test_contract,test_sender_policy,test_connection_scope_regression,test_security_and_no_business_writes):
        f(); print("PASS:",f.__name__)
    print("MASTER REQUIREMENT AUTHORITY V1.2.1 ACCEPTANCE: PASS")
