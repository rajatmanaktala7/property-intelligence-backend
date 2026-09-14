from pathlib import Path
import ast, re

ROOT=Path(__file__).resolve().parent
REQ=(ROOT/"alliance_requirement_restore_v1235.py").read_text(encoding="utf-8-sig")
APP=(ROOT/"app.py").read_text(encoding="utf-8-sig")
ast.parse(REQ); ast.parse(APP)
required=(
'@app.get("/alliance/final/requirements/run-match"',
'def source_run_match(',
'@app.post("/alliance/final/requirements/verify-and-match"',
'def verify_and_match(',
'@app.post("/alliance/final/requirements/reject-source"',
'def reject_source(',
'/alliance/primary/matcher?requirement_id=',
'matcher_master_only',
)
missing=[x for x in required if x not in REQ]
assert not missing, missing
assert 'headers={"Location": "/login"}' in REQ
fm=re.search(r'(<form[^>]+action=["\']/login["\'][^>]*>)(.*?)(</form>)',APP,re.S|re.I)
assert fm
form=fm.group(0)
assert re.search(r'name=["\']role["\']',form,re.I)
assert re.search(r'name=["\']code["\']',form,re.I)
print("REQUIREMENT_WORKFLOW_CONTRACT_TEST=PASS")

ENTRY = (ROOT / "production_entrypoint.py").read_text(encoding="utf-8-sig")
ast.parse(ENTRY, filename="production_entrypoint.py")
assert "ALLIANCE_REQUIREMENT_AUTHORITY_V411" in REQ
assert "def _remove_method(app, path, method):" in REQ
assert "ALLIANCE_REQUIREMENT_AUTHORITY_V411" in ENTRY
assert "_req_authority_v411.register(wrapped.core)" in ENTRY
print("REQUIREMENT_ROUTE_AUTHORITY_V411_CONTRACT=PASS")
