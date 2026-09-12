from pathlib import Path
import importlib.util
ROOT=Path(__file__).resolve().parent
MOD=ROOT/"alliance_database_rectification_v3.py"

def load():
    spec=importlib.util.spec_from_file_location("rect3",MOD)
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def test_identity():
    m=load()
    a={"message_id":"ABC","source":"WhatsApp Chat with GOA TOP.txt","raw_text":"Plot 500 sqm","sender":"+91 99999 00000","sent_at":"2026-01-01"}
    b={"message_id":"ABC","source":"goa top","raw_text":"Plot 500 sqm","sender":"9999900000","sent_at":"2026-01-01"}
    assert m._fingerprints(a)["mid_key"]==m._fingerprints(b)["mid_key"]

def test_alias_relaxed():
    m=load()
    a={"source":"A","raw_text":"Villa for sale","sender":"+91 9999900000","sent_at":"1"}
    b={"source":"B","raw_text":"Villa for sale","sender":"9999900000","sent_at":"2"}
    assert m._phone("+91 9999900000")=="9999900000"
    assert m._phone("09999900000")=="9999900000"
    assert m._fingerprints(a)["relaxed"]==m._fingerprints(b)["relaxed"]
    assert m._fingerprints(a)["exact"]!=m._fingerprints(b)["exact"]

def test_non_destructive():
    s=MOD.read_text(encoding="utf-8").upper()
    for token in ("UPDATE WA_MESSAGES","DELETE FROM WA_MESSAGES","UPDATE WAI_RAW_MESSAGES","DELETE FROM WAI_RAW_MESSAGES",
                  "UPDATE PI_MASTER_PROPERTIES","DELETE FROM PI_MASTER_PROPERTIES","TRUNCATE WA_","TRUNCATE WAI_"):
        assert token not in s

def test_protected():
    s=MOD.read_text(encoding="utf-8")
    assert "_auth(core,request)" in s
    assert "@app.get(" in s and "@app.post(" in s

if __name__=="__main__":
    for f in (test_identity,test_alias_relaxed,test_non_destructive,test_protected):
        f(); print("PASS:",f.__name__)
    print("DATABASE RECTIFICATION V3 ACCEPTANCE: PASS")
