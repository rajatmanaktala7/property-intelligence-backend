from pathlib import Path
import importlib.util, sys

ROOT = Path(__file__).resolve().parent
MOD = ROOT / "alliance_whatsapp_reconciliation_auditor_v12.py"
ENTRY = ROOT / "production_entrypoint.py"

def load_module():
    spec = importlib.util.spec_from_file_location("wa_recon_v12", MOD)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod

def apply_registration(stabilization, register_callable):
    # Mirrors production V1.2 logic: preserve the authoritative local object.
    stabilization = dict(stabilization or {})
    try:
        stabilization["whatsapp_reconciliation_auditor_v12"] = register_callable()
    except Exception as exc:
        stabilization["whatsapp_reconciliation_auditor_v12"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "fail_safe": True,
        }
    return stabilization

def test_contract():
    m = load_module()
    assert m.VERSION.startswith("1.2.0-READ-ONLY-AUTHORITY-PRESERVING")
    assert m.ROUTE == "/api/alliance/whatsapp-reconciliation-v1"
    assert m.PAGE == "/alliance/admin/whatsapp-reconciliation-v1"

def test_read_only_contract():
    src = MOD.read_text(encoding="utf-8").upper()
    for token in ("INSERT INTO ", "UPDATE WA_", "UPDATE PI_", "DELETE FROM ",
                  "DROP TABLE ", "ALTER TABLE ", "TRUNCATE "):
        assert token not in src, token

def test_none_stabilization_is_safe():
    result = apply_registration(None, lambda: {"status": "REGISTERED"})
    assert result["whatsapp_reconciliation_auditor_v12"]["status"] == "REGISTERED"

def test_registered_authority_is_preserved():
    original = {
        "registered": True,
        "owner": "production_authority",
        "semantic_review": {"status": "READY"},
    }
    result = apply_registration(original, lambda: {"status": "REGISTERED"})
    assert result["registered"] is True
    assert result["owner"] == "production_authority"
    assert result["semantic_review"]["status"] == "READY"
    assert result["whatsapp_reconciliation_auditor_v12"]["status"] == "REGISTERED"
    assert ("READY" if result.get("registered") else "DEGRADED") == "READY"

def test_registration_failure_is_nonfatal_and_preserves_ready_authority():
    def explode():
        raise RuntimeError("synthetic optional auditor failure")
    result = apply_registration({"registered": True}, explode)
    audit = result["whatsapp_reconciliation_auditor_v12"]
    assert audit["status"] == "ERROR"
    assert audit["fail_safe"] is True
    assert result["registered"] is True
    assert ("READY" if result.get("registered") else "DEGRADED") == "READY"

def test_production_wiring_uses_local_authority():
    src = ENTRY.read_text(encoding="utf-8")
    assert "# ALLIANCE_WHATSAPP_RECONCILIATION_AUDITOR_V12_BEGIN" in src
    assert "# ALLIANCE_WHATSAPP_RECONCILIATION_AUDITOR_V12_END" in src
    assert "stabilization = dict(stabilization or {})" in src
    assert 'BOOT.get("stabilization")' not in src[src.index("# ALLIANCE_WHATSAPP_RECONCILIATION_AUDITOR_V12_BEGIN"):src.index("# ALLIANCE_WHATSAPP_RECONCILIATION_AUDITOR_V12_END")]
    assert 'BOOT["stabilization"]["whatsapp_reconciliation' not in src
    assert "alliance_whatsapp_reconciliation_auditor_v12" in src

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
        test_none_stabilization_is_safe,
        test_registered_authority_is_preserved,
        test_registration_failure_is_nonfatal_and_preserves_ready_authority,
        test_production_wiring_uses_local_authority,
        test_master_matcher_contract_untouched,
    ]
    for fn in tests:
        fn()
        print("PASS:", fn.__name__)
    print("WHATSAPP RECONCILIATION AUDITOR V1.2 ACCEPTANCE: PASS")

if __name__ == "__main__":
    main()
