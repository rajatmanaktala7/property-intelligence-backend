from pathlib import Path
import importlib.util, sys
ROOT=Path(__file__).resolve().parent
MOD=ROOT/"alliance_whatsapp_source_reconciliation_v2.py"
ENTRY=ROOT/"production_entrypoint.py"

def load():
    spec=importlib.util.spec_from_file_location("wa_source_recon_v2",MOD)
    m=importlib.util.module_from_spec(spec); sys.modules[spec.name]=m; spec.loader.exec_module(m); return m

def test_contract():
    m=load()
    assert m.VERSION.startswith("2.1.0-READ-ONLY-AUTHORITY-AUTH-MULTISTORE")
    assert m.API_ROUTE=="/api/alliance/whatsapp-reconciliation-v2"
    assert m.PAGE_ROUTE=="/alliance/admin/whatsapp-reconciliation-v2"

def test_read_only():
    s=MOD.read_text(encoding="utf-8").upper()
    for x in ("INSERT INTO ","UPDATE WA_","UPDATE WAI_","UPDATE PI_","DELETE FROM ","DROP TABLE ","ALTER TABLE ","TRUNCATE "):
        assert x not in s,x

def test_multistore_and_privacy():
    s=MOD.read_text(encoding="utf-8")
    for x in ("wa_sources","wa_messages","wa_properties","wai_groups","wai_raw_messages",
              "pi_master_source_links_v711","pi_whatsapp_live_clean_ledger","_auth_guard","html.escape","CACHE_SECONDS"):
        assert x in s,x
    assert "configured_sources" not in s
    assert "observed_sources" in s


def test_authoritative_auth_contract():
    s=MOD.read_text(encoding="utf-8")
    assert "core.need_login(request)" in s
    assert 'getattr(request, "session"' not in s
    assert "ALLIANCE_AUTHORITY_REQUIRED" in s

def test_wiring_preserves_authority():
    s=ENTRY.read_text(encoding="utf-8")
    assert "# ALLIANCE_WHATSAPP_SOURCE_RECONCILIATION_V2_BEGIN" in s
    seg=s[s.index("# ALLIANCE_WHATSAPP_SOURCE_RECONCILIATION_V2_BEGIN"):s.index("# ALLIANCE_WHATSAPP_SOURCE_RECONCILIATION_V2_END")]
    assert "stabilization = dict(stabilization or {})" in seg
    assert 'BOOT.get("stabilization")' not in seg

def test_master_only_unchanged():
    s=(ROOT/"alliance_master_matcher_contract_v1.py").read_text(encoding="utf-8")
    assert "MASTER_ONLY" in s and "pi_master_properties_v711" in s

def main():
    for f in (test_contract,test_read_only,test_multistore_and_privacy,test_authoritative_auth_contract,test_wiring_preserves_authority,test_master_only_unchanged):
        f(); print("PASS:",f.__name__)
    print("WHATSAPP SOURCE RECONCILIATION V2 ACCEPTANCE: PASS")
if __name__=="__main__": main()
