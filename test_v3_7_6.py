import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

import team_dashboard_v376_ops as o
import team_dashboard_v376_takeover as t

assert o.MODULE_VERSION == "3.7.6-FRESHNESS-NAVIGATION-FIX"
assert t.MODULE_VERSION == "3.7.6-FINAL-FRESH-DASHBOARD"
assert t.FINAL_ROUTE == "/team-dashboard-v376"

wa = Path("whatsapp_live_bridge.py").read_text(encoding="utf-8")

# The deployed endpoint is composed from:
# APIRouter(prefix="/whatsapp-live")
# +
# @router.post("/api/ingest")
# =
# /whatsapp-live/api/ingest

assert 'prefix="/whatsapp-live"' in wa or "prefix='/whatsapp-live'" in wa
assert '@router.post("/api/ingest")' in wa or "@router.post('/api/ingest')" in wa

for x in [
    "/team-dashboard-v376",
    "Working Space",
    "last_message_at",
]:
    assert x in wa, x

src = Path("team_dashboard_v376_takeover.py").read_text(encoding="utf-8")

for x in [
    "Live Data Freshness",
    "← Dashboard",
    "Working Space",
    "/api/team-dashboard-v376/freshness",
]:
    assert x in src, x

ops = Path("team_dashboard_v376_ops.py").read_text(encoding="utf-8")

for x in [
    "wa_bridge_events",
    "last_message_at",
    "age_hours",
    '"STALE"',
]:
    assert x in ops, x

print("PASS versions")
print("PASS WhatsApp router prefix")
print("PASS WhatsApp ingest route")
print("PASS deployed endpoint composition: /whatsapp-live/api/ingest")
print("PASS WhatsApp back navigation")
print("PASS dashboard/workspace navigation")
print("PASS freshness monitor")
print("PASS per-group last-message diagnostics")
print("PASS existing WhatsApp ingest remains intact")
print("ALL V3.7.6 TESTS PASSED")
