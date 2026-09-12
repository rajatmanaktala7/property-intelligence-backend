from pathlib import Path
import alliance_master_matcher_contract_v1 as master_matcher

def test_semantic_authority_bridge():
    src = Path("production_entrypoint.py").read_text(encoding="utf-8")
    assert "ALLIANCE_SEMANTIC_REVIEW_AUTHORITATIVE_APP_BRIDGE_V3" in src
    assert "app=wrapped.app" in src
    assert "engine=wrapped.core.engine" in src
    assert "semantic_review_v3_final.register(" in src
    assert "semantic_authority" in src
    assert '"/semantic-v3/review"' in src
    assert '"/api/semantic-v3/review-stats"' in src
    assert '"owner": "wrapped.app"' in src

def test_core_app_authority():
    src = Path("production_entrypoint.py").read_text(encoding="utf-8")
    assert "CORE_APP = wrapped.app" in src

def test_matcher_unchanged():
    deal = Path("alliance_deal_match_ai_v60.py").read_text(encoding="utf-8")
    assert master_matcher.MATCHER_SOURCE_CONTRACT == "MASTER_ONLY"
    assert master_matcher.MASTER_TABLE == "pi_master_properties_v711"
    assert "result = phase5.run_match(" in deal
    assert "result = whatsapp_first.run_match(" not in deal
    assert '"contacts_exposed": False,' in deal

def test_export_v2_unchanged():
    src = Path("whatsapp_intelligence.py").read_text(encoding="utf-8")
    assert "Workbook(write_only=True)" in src
    assert "stream_results=True" in src
    assert '@router.get("/export-status")' in src
