
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

# ALLIANCE V4.5 LIVE WHATSAPP TAKEOVER
try:
    import alliance_v45_live_whatsapp_takeover as _v45
    _v45.register(core)
    print("Alliance V4.5 Live WhatsApp takeover registered successfully")
except Exception as e:
    print("Alliance V4.5 takeover warning:",type(e).__name__,str(e))

# ALLIANCE V4.6 UNIFIED LIVE INTELLIGENCE
try:
    import alliance_v46_unified_intelligence as _v46
    _v46.register(core)
    print("Alliance V4.6 unified live intelligence registered successfully")
except Exception as e:
    print("Alliance V4.6 registration warning:",type(e).__name__,str(e))

# ALLIANCE V3.8.3 CANONICAL DATABASE FOUNDATION
try:
    import alliance_v383_database_foundation as _v383
    _v383.register(core)
    print("Alliance V3.8.3 canonical database foundation registered successfully")
except Exception as e:
    print("Alliance V3.8.3 database foundation warning:", type(e).__name__, str(e))
