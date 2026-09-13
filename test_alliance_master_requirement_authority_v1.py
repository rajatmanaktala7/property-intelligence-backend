from pathlib import Path
import importlib.util
ROOT=Path(__file__).resolve().parent
MOD=ROOT/"alliance_master_requirement_authority_v1.py"
def load():
    spec=importlib.util.spec_from_file_location("mra",MOD)
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def test_contract():
    m=load()
    assert m.MASTER_REQUIREMENT_TABLE=="pi_requirement_gate_v1191"
    assert m.MASTER_PROPERTY_TABLE=="pi_master_properties_v711"
    assert m._category("WHATSAPP_LIVE_CLEAN","wa_requirements")=="WHATSAPP"
    assert m._category("MANUAL","requirement-manual")=="MANUAL"
    assert m._category("NEWSPAPER","capture")=="NEWSPAPER"
def test_existing_matcher_engine():
    s=MOD.read_text(encoding="utf-8")
    assert "alliance_phase5_canonical_matcher as phase5" in s
    assert "phase5.run_match(core.engine" in s
    assert "MASTER_ONLY" in s
def test_security_and_scope():
    s=MOD.read_text(encoding="utf-8")
    assert "_auth(core,request)" in s
    assert "AUTHENTICATED_STAFF_ONLY" in s
    assert "SMART_MATCHER_ROUTE" in s and "app.router.routes.remove(route)" in s
def test_no_business_db_writes():
    s=MOD.read_text(encoding="utf-8").upper()
    for token in ("INSERT INTO PI_REQUIREMENT","UPDATE PI_REQUIREMENT","DELETE FROM PI_REQUIREMENT",
                  "INSERT INTO PI_MASTER_PROPERTIES","UPDATE PI_MASTER_PROPERTIES","DELETE FROM PI_MASTER_PROPERTIES",
                  "INSERT INTO WA_","UPDATE WA_","DELETE FROM WA_"):
        assert token not in s
if __name__=="__main__":
    for f in (test_contract,test_existing_matcher_engine,test_security_and_scope,test_no_business_db_writes):
        f(); print("PASS:",f.__name__)
    print("MASTER REQUIREMENT AUTHORITY V1.1 ACCEPTANCE: PASS")
