
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

# ALLIANCE V4.0 WHATSAPP NORMALIZED PIPELINE
try:
    import alliance_v40_whatsapp_pipeline as _v40
    _v40.register(core)
    print("Alliance V4.0 WhatsApp normalized pipeline registered successfully")
except Exception as e:
    print("Alliance V4.0 WhatsApp pipeline registration warning:",type(e).__name__,str(e))

