from pathlib import Path
import importlib.util, sys

ROOT = Path(__file__).resolve().parent
MOD = ROOT / "alliance_whatsapp_reconciliation_auditor_v11.py"
ENTRY = ROOT / "production_entrypoint.py"

def load_module():
    spec = importlib.util.spec_from_file_location("wa_recon_v11", MOD)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod

def registration_pattern(boot, register_callable):
    # Exact fail-safe pattern used by production wiring.
    stabilization = dict(boot.get("stabilization") or {})
    try:
        stabilization["whatsapp_reconciliation_auditor_v11"] = register_callable()
    except Exception as exc:
        stabilization["whatsapp_reconciliation_auditor_v11"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "fail_safe": True,
        }
    boot["stabilization"] = stabilization
    return boot

def test_contract():
    m = load_module()
    assert m.VERSION.startswith("1.1.0-READ-ONLY-FAIL-SAFE")
    assert m.ROUTE == "/api/alliance/whatsapp-reconciliation-v1"
    assert m.PAGE == "/alliance/admin/whatsapp-reconciliation-v1"

def test_read_only_contract():
    src = MOD.read_text(encoding="utf-8").upper()
    for token in ("INSERT INTO ", "UPDATE WA_", "UPDATE PI_", "DELETE FROM ",
                  "DROP TABLE ", "ALTER TABLE ", "TRUNCATE "):
        assert token not in src, token

def test_none_stabilization_success():
    boot = {"stabilization": None}
    registration_pattern(boot, lambda: {"status": "REGISTERED"})
    assert isinstance(boot["stabilization"], dict)
    assert boot["stabilization"]["whatsapp_reconciliation_auditor_v11"]["status"] == "REGISTERED"

def test_none_stabilization_failure_is_nonfatal():
    def explode():
        raise RuntimeError("synthetic auditor failure")
    boot = {"stabilization": None}
    registration_pattern(boot, explode)
    result = boot["stabilization"]["whatsapp_reconciliation_auditor_v11"]
    assert result["status"] == "ERROR"
    assert result["fail_safe"] is True
    assert "synthetic auditor failure" in result["error"]

def test_existing_stabilization_preserved():
    boot = {"stabilization": {"semantic_review": {"status": "READY"}}}
    registration_pattern(boot, lambda: {"status": "REGISTERED"})
    assert boot["stabilization"]["semantic_review"]["status"] == "READY"
    assert boot["stabilization"]["whatsapp_reconciliation_auditor_v11"]["status"] == "REGISTERED"

def test_production_wiring():
    src = ENTRY.read_text(encoding="utf-8")
    assert "# ALLIANCE_WHATSAPP_RECONCILIATION_AUDITOR_V11_BEGIN" in src
    assert "# ALLIANCE_WHATSAPP_RECONCILIATION_AUDITOR_V11_END" in src
    assert 'stabilization = dict(BOOT.get("stabilization") or {})' in src
    assert 'BOOT["stabilization"] = stabilization' in src
    assert 'BOOT["stabilization"]["whatsapp_reconciliation' not in src
    assert "alliance_whatsapp_reconciliation_auditor_v11" in src

def test_master_matcher_contract_untouched():
    p = ROOT / "alliance_master_matcher_contract_v1.py"
    assert p.exists()
    src = p.read_text(encoding="utf-8")
    assert "MASTER_ONLY" in src
    assert "pi_master_properties_v711" in src

def main():
    tests = [
        test_contract,
        test_read_only_contract,
        test_none_stabilization_success,
        test_none_stabilization_failure_is_nonfatal,
        test_existing_stabilization_preserved,
        test_production_wiring,
        test_master_matcher_contract_untouched,
    ]
    for fn in tests:
        fn()
        print("PASS:", fn.__name__)
    print("WHATSAPP RECONCILIATION AUDITOR V1.1 ACCEPTANCE: PASS")

if __name__ == "__main__":
    main()
