from pathlib import Path
import py_compile

for f in ("newspaper_intelligence.py","fast_manual_forms.py"):
    py_compile.compile(f,doraise=True)

n=Path("newspaper_intelligence.py").read_text(encoding="utf-8")
f=Path("fast_manual_forms.py").read_text(encoding="utf-8")

checks={
 "newspaper auto optimization":"optimizeNewspaperFile" in n,
 "newspaper server cap":"max_side = 2800" in n,
 "google optional":"Google Location (Optional)" in f,
 "manual sqm":"Sq m, sqm, sqmt, sqft and acre are accepted" in f,
 "sale amount label":"Sale Amount *" in f and "syncAmountLabel" in f,
 "amount table":"<th>Amount</th>" in f,
}
for k,v in checks.items(): print(("PASS " if v else "FAIL ")+k)
if not all(checks.values()): raise SystemExit("TEST FAILED")
print("ALL V20.1 PANEL TESTS PASSED")
