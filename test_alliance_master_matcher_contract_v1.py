import alliance_master_matcher_contract_v1 as m

def test_contract_constants():
    assert m.MATCHER_SOURCE_CONTRACT == "MASTER_ONLY"
    assert m.MASTER_TABLE == "pi_master_properties_v711"

def test_sort_verified_before_unverified_tie():
    a={"send_eligible":True,"availability_verification":"VERIFIED","match_score":90,"data_completeness":5,"captured_on":"2026-01-01","record_id":"A"}
    b={"send_eligible":False,"availability_verification":"UNVERIFIED","match_score":99,"data_completeness":7,"captured_on":"2026-01-02","record_id":"B"}
    assert m._sort_key(a) > m._sort_key(b)

def test_detail_contract_without_contacts():
    p={"record_id":"CAN-TEST","description":"Villa","location":"MAPUSA","transaction":"SALE","family":"RESIDENTIAL","subtype":"VILLA","area_sqft":1000,"price":None,"price_text":"","price_comparable":False,"quality":"READY","verification":"UNVERIFIED","detail_url":"/alliance/primary/property/CAN-TEST"}
    x=m.public_item(p,88.0,"EXACT",[])
    assert x["detail_url"]=="/alliance/primary/property/CAN-TEST"
    assert "9876543210" not in repr(x)


def test_v60_route_uses_master_engine_and_clickable_detail():
    from pathlib import Path
    src = Path("alliance_deal_match_ai_v60.py").read_text(encoding="utf-8")
    assert "result = phase5.run_match(" in src
    assert "result = whatsapp_first.run_match(" not in src
    assert 'ENGINE_VERSION = getattr(phase5' in src
    assert "ALLIANCE_MASTER_DETAIL_RENDER_V1" in src
    assert "View Full Property" in src
    assert "_master_detail_link(r)" in src
    assert 'pi_whatsapp_property_master' not in src
    assert "Master Database Search Coverage" in src
