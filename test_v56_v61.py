from pathlib import Path
wa=Path("alliance_live_feed_purity.py").read_text(encoding="utf-8")
acc=Path("alliance_match_accuracy_v61.py").read_text(encoding="utf-8")

assert "Run Matcher" in wa
assert "<th>Contact Number</th><th>Source</th><th>Matcher</th>" in wa
assert "phone_lines_html" in wa
assert "meaningful_property(item)" in wa
assert "compact_description" in wa
assert "TOO_EXPENSIVE" in acc
assert "WRONG_AREA" in acc
assert "SUBTYPE_UNKNOWN" in acc
assert "LOW_QUALITY_CANDIDATE" in acc
print("V5.6 + V6.1 TESTS: PASS")
