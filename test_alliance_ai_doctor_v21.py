from pathlib import Path
import alliance_ai_doctor_v21 as d
assert d.VERSION == "2.1.0-ALLIANCE-CONTINUOUS-DOCTOR"
assert d.INTERVAL_SECONDS == 300
assert d.CRITICAL["/alliance/master-requirement-matcher"] == "GET"
assert d.CRITICAL["/commercial-intelligence/research/{asset_code}"] == "POST"
assert d.CRITICAL["/api/newspaper-v83/process"] == "POST"
req=Path("alliance_requirement_restore_v1235.py").read_text(encoding="utf-8")
gov=Path("alliance_government_commercial_sources_v1.py").read_text(encoding="utf-8")
assert "ALLIANCE_REQUIREMENT_CANONICAL_AUTH_V21" in req
assert "ALLIANCE_COMMERCIAL_RENDERER_V21" in gov
print("ALLIANCE_AI_DOCTOR_V21_TEST=PASS")
