import app as core
from datetime import datetime, timezone

app = core.app

try:
    from alliance_v2_routes import register as register_alliance_v2
    register_alliance_v2(core)
    ALLIANCE_V2_STATUS={"status":"HEALTHY","error":None}
except Exception as e:
    ALLIANCE_V2_STATUS={"status":"DEGRADED","error":f"{type(e).__name__}: {e}"}

try:
    import alliance_module_registry as registry
    OPTIONAL_MODULES=registry.register_all(core)
except Exception as e:
    OPTIONAL_MODULES={"registry":{"status":"DEGRADED","error":f"{type(e).__name__}: {e}"}}

@app.get("/production-health")
def production_health():
    return {"status":"OK","service":"Alliance Property Intelligence","wrapper":"V3.8+V6.2-UI-QUALITY",
      "core_app_loaded":True,"alliance_v2":ALLIANCE_V2_STATUS,"optional_modules":OPTIONAL_MODULES,
      "timestamp_utc":datetime.now(timezone.utc).isoformat()}

@app.get("/module-health")
def module_health():
    return {"wrapper":"V3.8+V6.2-UI-QUALITY","alliance_v2":ALLIANCE_V2_STATUS,"modules":OPTIONAL_MODULES}

try:
    import alliance_v44_whatsapp_property_master as _v44
    _v44.register(core)
    import alliance_auto_updater as _auto44
    _auto44.start(core)
    @app.get("/api/v44/auto-update/status")
    def _v44_auto_status():
        return _auto44.STATE
except Exception as e:
    print("Alliance V4.4 registration warning:",type(e).__name__,str(e))

V383_ERROR=None
try:
    import alliance_v383_database_foundation as _v383
    _v383.register(core)
except Exception as e:
    V383_ERROR=f"{type(e).__name__}: {e}"

V46_ERROR=None
try:
    import alliance_v46_unified_intelligence as _v46
    _v46.register(core)
except Exception as e:
    V46_ERROR=f"{type(e).__name__}: {e}"

V451_ERROR=None
try:
    import alliance_v45_live_whatsapp_takeover as _v451
    _v451.register(core)
except Exception as e:
    V451_ERROR=f"{type(e).__name__}: {e}"

V60_ERROR=None
V60_STATUS=None
try:
    import alliance_deal_match_ai_v60 as _v60
    V60_STATUS=_v60.register(core)
except Exception as e:
    V60_ERROR=f"{type(e).__name__}: {e}"
    print("Alliance V6 Deal Match AI registration warning:",V60_ERROR)

V62_ERROR=None
V62_STATUS=None
try:
    import alliance_ui_quality_v62 as _v62
    V62_STATUS=_v62.register(core)
except Exception as e:
    V62_ERROR=f"{type(e).__name__}: {e}"
    print("Alliance V6.2 UI quality registration warning:",V62_ERROR)

@app.get("/api/live-bootstrap-status")
def live_bootstrap_status():
    paths=[]
    for r in app.router.routes:
        p=getattr(r,"path",None)
        if p in {"/whatsapp-live","/whatsapp-live/feed","/api/v451/live/status","/api/v451/live/properties",
                 "/api/v383/status","/api/v46/status","/api/v51/status","/api/v52/status","/api/v54/status",
                 "/deal-match-ai-v60","/api/v60/status","/api/v60/deal-match","/api/v62/ui-quality/status"}:
            paths.append(p)
    return {"status":"OK" if all(x is None for x in [V451_ERROR,V60_ERROR,V62_ERROR]) else "DEGRADED",
      "v383_error":V383_ERROR,"v46_error":V46_ERROR,"v451_error":V451_ERROR,"v60_error":V60_ERROR,
      "v62_error":V62_ERROR,"v60_status":V60_STATUS,"v62_status":V62_STATUS,
      "registered_paths":sorted(set(paths)),"deal_match_ai_owner":"alliance_deal_match_ai_v60",
      "magazine_ui_owner":"alliance_ui_quality_v62"}
