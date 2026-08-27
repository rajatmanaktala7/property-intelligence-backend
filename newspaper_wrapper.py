
import app as core
from datetime import datetime, timezone

app = core.app

# Alliance V2 is useful, but failure must not take down the core app.
try:
    from alliance_v2_routes import register as register_alliance_v2
    register_alliance_v2(core)
    ALLIANCE_V2_STATUS={"status":"HEALTHY","error":None}
    print("Alliance V2: routes registered successfully")
except Exception as e:
    ALLIANCE_V2_STATUS={"status":"DEGRADED","error":f"{type(e).__name__}: {e}"}
    print("Alliance V2 registration warning:",type(e).__name__,str(e))

# Exactly one optional-module registry. No direct V3.8.1 / V3.8.2 stacking.
try:
    import alliance_module_registry as registry
    OPTIONAL_MODULES=registry.register_all(core)
except Exception as e:
    OPTIONAL_MODULES={
        "registry":{
            "status":"DEGRADED",
            "error":f"{type(e).__name__}: {e}"
        }
    }

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

# ALLIANCE V4.4 AUTO-UPDATING WHATSAPP MASTER
try:
    import alliance_v44_whatsapp_property_master as _v44
    _v44.register(core)

    import alliance_auto_updater as _auto44
    _auto44.start(core)

    @app.get("/api/v44/auto-update/status")
    def _v44_auto_status():
        return _auto44.STATE

    print("Alliance V4.4 auto-updating WhatsApp master registered successfully")
except Exception as e:
    print("Alliance V4.4 registration warning:", type(e).__name__, str(e))

# ALLIANCE V3.8.3 CANONICAL DATABASE FOUNDATION
V383_ERROR = None
try:
    import alliance_v383_database_foundation as _v383
    _v383.register(core)
    print("Alliance V3.8.3 canonical database foundation registered successfully")
except Exception as e:
    V383_ERROR = f"{type(e).__name__}: {e}"
    print("Alliance V3.8.3 database foundation warning:", V383_ERROR)

# ALLIANCE V4.6 UNIFIED LIVE INTELLIGENCE
V46_ERROR = None
try:
    import alliance_v46_unified_intelligence as _v46
    _v46.register(core)
    print("Alliance V4.6 unified live intelligence registered successfully")
except Exception as e:
    V46_ERROR = f"{type(e).__name__}: {e}"
    print("Alliance V4.6 registration warning:", V46_ERROR)

# ALLIANCE V4.5.1 CANONICAL LIVE FEED - REGISTER LAST SO IT OWNS LIVE ROUTES
V451_ERROR = None
try:
    import alliance_v45_live_whatsapp_takeover as _v451
    _v451.register(core)
    print("Alliance V4.5.1 canonical live feed registered successfully")
except Exception as e:
    V451_ERROR = f"{type(e).__name__}: {e}"
    print("Alliance V4.5.1 live feed warning:", V451_ERROR)

@app.get("/api/live-bootstrap-status")
def live_bootstrap_status():
    paths = []
    for r in app.router.routes:
        p = getattr(r, "path", None)
        if p in {
            "/whatsapp-live",
            "/whatsapp-live/feed",
            "/api/v451/live/status",
            "/api/v451/live/properties",
            "/api/v383/status",
            "/api/v46/status",
        }:
            paths.append(p)
    return {
        "status": "OK" if V451_ERROR is None else "DEGRADED",
        "v383_error": V383_ERROR,
        "v46_error": V46_ERROR,
        "v451_error": V451_ERROR,
        "registered_paths": sorted(set(paths)),
        "live_feed_owner": "V4.5.1 canonical live feed",
    }
