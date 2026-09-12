from pathlib import Path
import importlib.util,sys
ROOT=Path(__file__).resolve().parent
MOD=ROOT/"alliance_whatsapp_reconciliation_auditor_v1.py"
ENTRY=ROOT/"production_entrypoint.py"
def load():
    s=importlib.util.spec_from_file_location("wa_recon",MOD);m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m);return m
def main():
    m=load()
    assert m.ROUTE=="/api/alliance/whatsapp-reconciliation-v1"
    assert m.PAGE=="/alliance/admin/whatsapp-reconciliation-v1"
    src=MOD.read_text(encoding="utf-8").upper()
    for x in ("INSERT INTO ","UPDATE WA_","UPDATE PI_","DELETE FROM ","DROP TABLE ","ALTER TABLE "): assert x not in src,x
    e=ENTRY.read_text(encoding="utf-8")
    assert "ALLIANCE_WHATSAPP_RECONCILIATION_AUDITOR_V1" in e
    p=ROOT/"alliance_master_matcher_contract_v1.py"; assert p.exists()
    c=p.read_text(encoding="utf-8"); assert "MASTER_ONLY" in c and "pi_master_properties_v711" in c
    print("WHATSAPP RECONCILIATION AUDITOR V1 TESTS: PASS")
if __name__=="__main__": main()
