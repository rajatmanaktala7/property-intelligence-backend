from pathlib import Path


def test_system_doctor_inspects_the_live_master_matcher_contract():
    source = Path("alliance_system_doctor.py").read_text(encoding="utf-8")
    assert "import alliance_master_matcher_contract_v1 as matcher" in source
    assert "import alliance_whatsapp_first_match_v1 as matcher" not in source
    assert 'getattr(matcher, "MATCHER_SOURCE_CONTRACT", None) != "MASTER_ONLY"' in source
    assert 'getattr(matcher, "MASTER_TABLE", None) != "pi_master_properties_v711"' in source


def test_root_matcher_diagnostic_uses_only_the_live_master_contract():
    source = Path("alliance_root_matcher_authority_v12411.py").read_text(encoding="utf-8")
    assert "import alliance_master_matcher_contract_v1 as matcher" in source
    assert "result = matcher.run_match(core.engine, q, min_score=70.0, limit=20)" in source
    assert "wa.run_match" not in source
    assert "SOURCE_EVIDENCE_ONLY_NOT_A_MATCH_CANDIDATE_SOURCE" in source
