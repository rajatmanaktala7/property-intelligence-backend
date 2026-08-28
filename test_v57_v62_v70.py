from pathlib import Path
import importlib.util
wa=Path('alliance_live_feed_purity.py').read_text(encoding='utf-8')
th=Path('alliance_results_theme_v70.py').read_text(encoding='utf-8')
acc=Path('alliance_match_accuracy_v61.py').read_text(encoding='utf-8')
assert 'split_multi_property' in wa
assert 'build_clean_description' in wa
assert '<th>Record</th>' not in wa
assert '<th>Date</th><th>ID</th>' not in wa
assert 'Run Matcher' in wa
assert 'technical_ids_hidden' in th
assert 'font-size:13px' in th
assert 'RECONSTRUCTED-INVENTORY-ACCURACY' in acc
print('V5.7 + V6.2 + V7.0 TESTS: PASS')
