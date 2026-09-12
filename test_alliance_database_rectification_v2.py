from pathlib import Path
import importlib.util
ROOT=Path(__file__).resolve().parent
MOD=ROOT/"alliance_database_rectification_v2.py"

def load():
    spec=importlib.util.spec_from_file_location("rect2",MOD)
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def test_classification():
    m=load()
    assert m._classify({"declared_messages":10,"actual_messages":10,"wai_messages":10,"alias_count":1}) == ("RECONCILED","NONE",False)
    assert m._classify({"declared_messages":9,"actual_messages":10,"wai_messages":10,"alias_count":1}) == ("STALE_SOURCE_COUNTER","LOW",True)
    assert m._classify({"declared_messages":10,"actual_messages":10,"wai_messages":9,"alias_count":1})[0] == "WA_TO_WAI_MISSING"
    assert m._classify({"declared_messages":10,"actual_messages":9,"wai_messages":10,"alias_count":1})[0] == "WAI_ONLY_OR_DUPLICATE_IMPORT"

def test_non_destructive_contract():
    s=MOD.read_text(encoding="utf-8").upper()
    forbidden=("UPDATE WA_SOURCES","UPDATE WA_MESSAGES","DELETE FROM WA_","TRUNCATE WA_","UPDATE PI_MASTER_PROPERTIES","DELETE FROM PI_MASTER_PROPERTIES")
    for token in forbidden:
        assert token not in s

def test_routes_protected():
    s=MOD.read_text(encoding="utf-8")
    assert "_auth(core,request)" in s
    assert "@app.get(" in s
    assert "@app.post(" in s

if __name__=="__main__":
    for f in (test_classification,test_non_destructive_contract,test_routes_protected):
        f(); print("PASS:",f.__name__)
    print("DATABASE RECTIFICATION V2 ACCEPTANCE: PASS")
