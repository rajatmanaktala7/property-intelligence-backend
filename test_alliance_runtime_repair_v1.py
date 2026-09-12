from pathlib import Path

def test_semantic_review_late_self_heal_source():
    src = Path("production_entrypoint.py").read_text(encoding="utf-8")
    assert "ALLIANCE_SEMANTIC_REVIEW_LATE_SELF_HEAL_V1" in src
    assert '"/semantic-v3/review" not in semantic_review_paths' in src
    assert "semantic_review_v3.register(wrapped.core)" in src

def test_deep_audit_capacity():
    src = Path("alliance_deep_runtime_auditor.py").read_text(encoding="utf-8")
    assert ("MAX_PAGES=5000" in src) or ("MAX_PAGES = 5000" in src)

def test_matcher_authority_untouched():
    import alliance_master_matcher_contract_v1 as m
    src = Path("alliance_deal_match_ai_v60.py").read_text(encoding="utf-8")
    assert m.MATCHER_SOURCE_CONTRACT == "MASTER_ONLY"
    assert m.MASTER_TABLE == "pi_master_properties_v711"
    assert "result = phase5.run_match(" in src
    assert "result = whatsapp_first.run_match(" not in src
    assert '"contacts_exposed": False,' in src
