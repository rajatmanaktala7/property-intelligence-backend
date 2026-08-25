
from __future__ import annotations
from fastapi import Request

MODULE_VERSION="3.6.2-FAIL-SAFE-BOOTSTRAP"
_ACTIVATED=False
_LAST_RESULT=None

def register(core):
    app=core.app

    @app.get("/api/v3/live/status")
    def status(req:Request):
        if hasattr(core,"need_login"):core.need_login(req)
        return {
            "version":MODULE_VERSION,"status":"OK","startup_safe":True,
            "db_access":False,"startup_schema_ddl":False,
            "full_dashboard_activated":_ACTIVATED,
            "activation_result":_LAST_RESULT,"dashboard":"/v3/control-centre"
        }

    @app.post("/api/v3/live/activate")
    def activate(req:Request):
        global _ACTIVATED,_LAST_RESULT
        if hasattr(core,"need_login"):core.need_login(req)
        if _ACTIVATED:
            return {"version":MODULE_VERSION,"status":"ACTIVE","mounted_now":False,
                    "full_dashboard_activated":True,"activation_result":_LAST_RESULT}
        try:
            import alliance_v36_live_dashboard as impl
            result=impl.mount_features(core)
            _LAST_RESULT=result
            _ACTIVATED=True
            return {"version":MODULE_VERSION,"status":"ACTIVE","mounted_now":True,
                    "full_dashboard_activated":True,
                    "implementation_version":impl.MODULE_VERSION,**result}
        except Exception as exc:
            _LAST_RESULT={"fatal_error":f"{type(exc).__name__}: {exc}"}
            return {"version":MODULE_VERSION,"status":"ACTIVATION_ERROR",
                    "full_dashboard_activated":False,"error_type":type(exc).__name__,
                    "message":str(exc)}
