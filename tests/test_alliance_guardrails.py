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
