from pathlib import Path
import importlib.util
ROOT=Path(__file__).resolve().parent
MOD=ROOT/"alliance_database_rectification_v4.py"

def load():
    spec=importlib.util.spec_from_file_location("rect4",MOD)
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def test_resolution():
    m=load()
    wf={"src":"group a","epoch":1000}
    c=[{"src":"group a","epoch":999,"id":"x"},{"src":"group b","epoch":999,"id":"y"}]
    assert m._classify_one(wf,c)[0]=="RESOLVED_UNIQUE_SOURCE_LINEAGE"
    c=[{"src":"x","epoch":1100,"id":"z"},{"src":"y","epoch":2000,"id":"q"}]
    assert m._classify_one(wf,c)[0]=="RESOLVED_UNIQUE_NEAR_TIMESTAMP"
    assert m._classify_one(wf,[])[0]=="TRUE_WAI_MISSING"

def test_non_destructive():
    s=MOD.read_text(encoding="utf-8").upper()
    forbidden=("UPDATE WA_MESSAGES","DELETE FROM WA_MESSAGES","UPDATE WAI_RAW_MESSAGES","DELETE FROM WAI_RAW_MESSAGES",
               "INSERT INTO WAI_RAW_MESSAGES","UPDATE PI_MASTER_PROPERTIES","DELETE FROM PI_MASTER_PROPERTIES",
               "INSERT INTO PI_MASTER_PROPERTIES")
    for t in forbidden: assert t not in s

def test_lock_and_auth():
    s=MOD.read_text(encoding="utf-8")
    assert "pg_try_advisory_lock" in s
    assert "_auth(core,request)" in s
    assert "DETERMINISTIC_PLAN_ONLY" in s

if __name__=="__main__":
    for f in (test_resolution,test_non_destructive,test_lock_and_auth):
        f(); print("PASS:",f.__name__)
    print("DATABASE RECTIFICATION V4 ACCEPTANCE: PASS")
