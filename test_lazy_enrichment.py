from pathlib import Path
import ast
import py_compile

root = Path(__file__).resolve().parent
api = root / "property_brain" / "api.py"

py_compile.compile(str(api), doraise=True)
tree = ast.parse(api.read_text(encoding="utf-8"))

text = api.read_text(encoding="utf-8")

assert '@router.get("/enrichment/status")' in text
assert '@router.post("/enrichment/batch/{limit}")' in text
assert '@router.post("/enrichment/property/{property_id}")' in text
assert "from .stages.s8b_property_enrichment import enrich_property" in text
assert "startup_import" in text
assert "production_entrypoint" not in text

print("LAZY PROPERTY ENRICHMENT TEST: PASS")
