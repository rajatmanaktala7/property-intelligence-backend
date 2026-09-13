from pathlib import Path
import importlib.util
ROOT=Path(__file__).resolve().parent
MOD=ROOT/"alliance_master_requirement_authority_v1.py"

def load():
    spec=importlib.util.spec_from_file_location("mra",MOD)
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def test_contract():
    m=load()
    assert m.VERSION.startswith("1.4.0")
    assert m.MASTER_REQUIREMENT_TABLE=="pi_requirement_gate_v1191"
    assert m.MASTER_PROPERTY_TABLE=="pi_master_properties_v711"

def test_phone_identity_guard():
    m=load()
    assert m._phone("+91 98100 60315")=="9810060315"
    assert m._phone("9810060315")=="9810060315"
    assert m._phone("19958796943374")==""      # LID-like, must not become contact
    assert m._phone("260786287075336")==""     # LID-like, must not become contact
    a=m._sender_identity("19958796943374","Kedar","19958796943374@lid")
    assert a["phone"]=="" and a["sender_id"] and a["status"]=="OPAQUE_SENDER_ID"
    b=m._sender_identity("", "+91 93100 75464", "")
    assert b["phone"]=="9310075464"
    c=m._sender_identity("", "Ajit", "9810060315@s.whatsapp.net")
    assert c["phone"]=="9810060315"

def test_connection_scope_regression():
    s=MOD.read_text(encoding="utf-8")
    needle='bymid.update({str(x["mid"]):dict(x) for x in c.execute(q,params).mappings()})'
    assert needle in s
    before=s[max(0,s.index(needle)-1000):s.index(needle)]
    assert "with eng.connect() as c:" in before

def test_security_and_no_business_writes():
    s=MOD.read_text(encoding="utf-8")
    assert "AUTHENTICATED_STAFF_ONLY" in s and "MASTER_ONLY" in s
    assert "OPAQUE_SENDER_ID" in s and "Sender ID (not phone)" in s
    u=s.upper()
    for token in ("INSERT INTO PI_REQUIREMENT","UPDATE PI_REQUIREMENT","DELETE FROM PI_REQUIREMENT",
                  "INSERT INTO PI_MASTER_PROPERTIES","UPDATE PI_MASTER_PROPERTIES","DELETE FROM PI_MASTER_PROPERTIES",
                  "INSERT INTO WA_","UPDATE WA_","DELETE FROM WA_"):
        assert token not in u

if __name__=="__main__":
    for f in (test_contract,test_phone_identity_guard,test_connection_scope_regression,test_security_and_no_business_writes):
        f(); print("PASS:",f.__name__)
    print("MASTER REQUIREMENT AUTHORITY V1.2.2 ACCEPTANCE: PASS")
