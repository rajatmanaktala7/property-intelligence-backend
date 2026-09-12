from pathlib import Path
import importlib.util
ROOT=Path(__file__).resolve().parent
MOD=ROOT/"alliance_database_rectification_v1.py"

def load():
    spec=importlib.util.spec_from_file_location("rect",MOD)
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def test_contract():
    m=load()
    assert m.VERSION.startswith("1.1.0-CANONICAL-RECTIFICATION")
    assert m.RUN_EVERY_SECONDS==900
    assert m._canon_name("WhatsApp Chat with GOA TOP  REAL ESTATE AGENT.txt")=="goa top real estate agent"

def test_non_destructive_policy():
    s=MOD.read_text(encoding="utf-8").upper()
    for bad in ("DELETE FROM WA_","UPDATE WA_SOURCES","UPDATE WA_MESSAGES","TRUNCATE WA_","DROP TABLE WA_"):
        assert bad not in s

def test_master_only_unchanged():
    p=(ROOT/"alliance_master_matcher_contract_v1.py").read_text(encoding="utf-8")
    assert "MASTER_ONLY" in p
    assert "pi_master_properties_v711" in p


def test_core_app_adapter_contract():
    m=load()
    class App: pass
    class Core: pass
    c=Core(); c.app=App()
    assert m._app(c) is c.app
    assert m._app(c.app) is c.app
    source=MOD.read_text(encoding="utf-8")
    assert "@app.get(" in source
    assert "@app.post(" in source
    assert "@core.get(" not in source
    assert "@core.post(" not in source

if __name__=="__main__":
    for f in (test_contract,test_non_destructive_policy,test_master_only_unchanged,test_core_app_adapter_contract):
        f(); print("PASS:",f.__name__)
    print("DATABASE RECTIFICATION V1 ACCEPTANCE: PASS")
