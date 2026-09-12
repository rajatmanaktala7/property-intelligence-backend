from pathlib import Path
import alliance_master_matcher_contract_v1 as master_matcher

def test_master_matcher_preserved():
    deal = Path("alliance_deal_match_ai_v60.py").read_text(encoding="utf-8")
    assert master_matcher.MATCHER_SOURCE_CONTRACT == "MASTER_ONLY"
    assert master_matcher.MASTER_TABLE == "pi_master_properties_v711"
    assert "result = phase5.run_match(" in deal
    assert "result = whatsapp_first.run_match(" not in deal
    assert '"contacts_exposed": False,' in deal

def test_runtime_status_nonblocking():
    src = Path("production_entrypoint.py").read_text(encoding="utf-8")
    block = src.split('@health_app.get("/runtime-status")',1)[1].split('@health_app.get("/", response_class=HTMLResponse)',1)[0]
    assert "import app as _core_app" not in block
    assert ".engine.pool" not in block
    assert '"diagnostic": "/api/system/db-pool-status"' in block

def test_semantic_final_authority():
    src = Path("production_entrypoint.py").read_text(encoding="utf-8")
    assert "ALLIANCE_SEMANTIC_REVIEW_FINAL_AUTHORITY_V2" in src
    final_pos = src.index("ALLIANCE_SEMANTIC_REVIEW_FINAL_AUTHORITY_V2")
    boot_pos = src.index('BOOT["core_loaded"] = True', final_pos)
    assert final_pos < boot_pos
    assert '"/semantic-v3/review" not in semantic_paths' in src

def test_export_background_streaming():
    src = Path("whatsapp_intelligence.py").read_text(encoding="utf-8")
    block = src.split('_ALLIANCE_EXPORT_STATE =',1)[1].split('@router.get("/health")',1)[0]
    assert "Workbook(write_only=True)" in block
    assert "stream_results=True" in block
    assert ".yield_per(1000)" in block
    assert '@router.get("/export-status")' in block
    assert 'status_code=202' in block
    assert ".mappings().all()" not in block
    assert "_alliance_xlsx_safe_v1" in block
