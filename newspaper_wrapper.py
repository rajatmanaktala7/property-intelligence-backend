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
    return {
        "status":"OK",
        "service":"Alliance Property Intelligence",
        "wrapper":"V3.8-STABLE-CONSOLIDATED",
        "core_app_loaded":True,
        "alliance_v2":ALLIANCE_V2_STATUS,
        "optional_modules":OPTIONAL_MODULES,
        "timestamp_utc":datetime.now(timezone.utc).isoformat(),
    }

@app.get("/module-health")
def module_health():
    return {
        "wrapper":"V3.8-STABLE-CONSOLIDATED",
        "alliance_v2":ALLIANCE_V2_STATUS,
        "modules":OPTIONAL_MODULES,
    }

try:
    import alliance_v44_whatsapp_property_master as _v44
    _v44.register(core)

    import alliance_auto_updater as _auto44
    _auto44.start(core)

    @app.get("/api/v44/auto-update/status")
    def _v44_auto_status():
        return _auto44.STATE
except Exception as e:
    print("Alliance V4.4 registration warning:", type(e).__name__, str(e))

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

# IMPORTANT:
# Do NOT intercept /whatsapp-live/feed in middleware here.
# production_entrypoint loads alliance_live_feed_purity afterwards.
# V5.1 must remain the final owner for:
#   /whatsapp-live
#   /whatsapp-live/feed
#   /whatsapp-live/requirements

@app.get("/api/live-bootstrap-status")
def live_bootstrap_status():
    paths=[]
    for r in app.router.routes:
        p=getattr(r,"path",None)
        if p in {
            "/whatsapp-live",
            "/whatsapp-live/feed",
            "/api/v451/live/status",
            "/api/v451/live/properties",
            "/api/v383/status",
            "/api/v46/status",
            "/api/v51/status",
        }:
            paths.append(p)

    return {
        "status":"OK" if V451_ERROR is None else "DEGRADED",
        "v383_error":V383_ERROR,
        "v46_error":V46_ERROR,
        "v451_error":V451_ERROR,
        "registered_paths":sorted(set(paths)),
        "live_feed_owner":"ROUTE_LAYER_FINAL_OWNER",
        "middleware_intercepts_whatsapp_feed":False,
        "expected_final_module":"alliance_live_feed_purity V5.1",
    }
