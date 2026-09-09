from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]

def txt(name): return (ROOT/name).read_text(encoding="utf-8")

def test_dashboard_authority_not_legacy():
    d = txt("alliance_deal_match_ai_v60.py")
    assert "/team-dashboard-v376" not in d
    assert 'href="/alliance/primary">Dashboard' in d

def test_matcher_master_is_authority():
    w = txt("alliance_whatsapp_first_match_v1.py")
    assert '"primary_source": "pi_properties"' in w
    assert '"matching_path": "CANONICAL_MASTER_ONLY"' in w
    assert "WHATSAPP_PRIMARY" not in w

def test_multi_location_contract():
    sys.path.insert(0,str(ROOT))
    import alliance_phase5_canonical_matcher as p
    r = p.parse_requirement("PURCHASE REQUIREMENT 3/4BHK VILLA Location: Candolim Calangute and around. Budget 4-5cr Ready client")
    assert r.get("primary_locations")[:2] == ["CANDOLIM","CALANGUTE"]
    assert r.get("location_intent") == "AROUND"

def test_contract_safety_flags():
    sys.path.insert(0,str(ROOT))
    import alliance_core_contract as c
    assert c.PROTECTED_INVARIANTS["client_contact_exposure"] is False
    assert c.PROTECTED_INVARIANTS["historical_backfill"] == "NOT_AUTHORIZED"
    assert c.PROTECTED_INVARIANTS["property_verification_separate_from_availability"] is True


def test_requirement_manual_canonical_alias_is_protected():
    sys.path.insert(0, str(ROOT))
    import alliance_core_contract as contract
    source = txt("fast_manual_forms.py")
    assert "@app.get('/requirement-manual')" in source
    assert "RedirectResponse(f'/fast-requirement-entry?division={d}',307)" in source
    assert contract.ROUTE_REGISTRY['requirement_manual']['path'] == '/requirement-manual'

def test_system_doctor_checks_registered_canonical_routes():
    source = txt("alliance_system_doctor.py")
    assert 'VERSION = "1.3.1-PROPERTY-TABLE-COLUMN-CLOSURE"' in source
    assert '"resolution": "REGISTERED_ROUTE" if present else "MISSING"' in source
    assert '"route_details": route_details' in source

def test_finalized_table_presentation_is_protected():
    primary = txt("alliance_primary_workspace_v730.py")
    final = txt("alliance_final_5x5_databases_v910.py")
    assert "width:max-content;min-width:100%;font-size:11px" in primary
    assert "width:max-content;min-width:100%;font-size:11px" in final
    assert "Property ID" in final and "Requirement ID" in final
    assert "Contact No." in final and "Assigned To" in final and "Source" in final

def test_database_hub_routes_are_protected():
    final = txt("alliance_final_5x5_databases_v910.py")
    assert '@app.get("/alliance/primary/databases")' in final
    assert '@app.get("/alliance/primary/requirements-hub")' in final
    doctor = txt("alliance_system_doctor.py")
    assert '"table_view_routes": table_view_checks' in doctor
    assert '"table_presentation": presentation' in doctor


def test_green_link_authority_is_protected():
    doctor = txt("alliance_system_doctor.py")
    contract_src = txt("alliance_core_contract.py")
    final = txt("alliance_final_5x5_databases_v910.py")
    assert "def _green_checks(mapping):" in doctor
    assert "🟢 PASS" in doctor and "🔴 FAIL" in doctor
    assert '"property_databases":' in contract_src
    assert '"requirement_databases":' in contract_src
    assert "/alliance/primary/reports" not in final


def test_property_table_final_columns_are_protected():
    final = txt("alliance_final_5x5_databases_v910.py")
    doctor = txt("alliance_system_doctor.py")
    for col in ["Property ID","Location","Description / Address","Rent/Sale","Amount","Contact No.","Status","Assigned To","Source"]:
        assert col in final
    assert '"property_table_columns": all(x in final_src' in doctor
