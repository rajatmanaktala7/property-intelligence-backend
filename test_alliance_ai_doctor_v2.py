from pathlib import Path
import alliance_ai_doctor_v2 as d

assert d.INTERVAL_SECONDS == 300
assert d.MARKER == "ALLIANCE_AI_DOCTOR_V2"
assert d.CRITICAL["/alliance/master-requirement-matcher"] == "GET"
assert d.CRITICAL["/commercial-intelligence/research/{asset_code}"] == "POST"
assert d.CRITICAL["/api/newspaper-v83/process"] == "POST"
assert "ALLIANCE_NEWSPAPER_DOCTOR_V2" in d.NEWSPAPER_PAGE

req=Path("alliance_requirement_restore_v1235.py").read_text(encoding="utf-8")
com=Path("alliance_commercial_intelligence_ai.py").read_text(encoding="utf-8")

assert "ALLIANCE_CANONICAL_AUTH_V2" in req
assert "canonical_app" in req
assert "ALLIANCE_COMMERCIAL_DOCTOR_V2" in com
assert "_research_asset" in com
assert "/commercial-intelligence/research/{asset_code}" in com

print("ALLIANCE_AI_DOCTOR_V2_TEST=PASS")
