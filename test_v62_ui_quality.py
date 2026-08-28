from pathlib import Path
wa=Path("alliance_live_feed_purity.py").read_text(encoding="utf-8")
np=Path("newspaper_upload_v83.py").read_text(encoding="utf-8")
ui=Path("alliance_ui_quality_v62.py").read_text(encoding="utf-8")
assert 'Contact Name</th><th>Contact Number' in wa
assert 'single_matcher_button_position' in wa
assert 'RedirectResponse("/deal-match-ai-v60",303)' in wa
assert "xhr.upload.onprogress" in np
assert "x.upload.onprogress" in ui
assert "UPLOAD 100%" in ui
print("V6.2 UI QUALITY TESTS: PASS")
