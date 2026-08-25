
from __future__ import annotations
from fastapi import Request

MODULE_VERSION="3.6.1-LAZY-LIVE-DASHBOARD-STARTUP-SAFE"
_ACTIVATED=False
_ACTIVATION_ERROR=None

def register(core):
    app=core.app

    @app.get("/api/v3/live/status")
    def status(req:Request):
        if hasattr(core,"need_login"):
            core.need_login(req)
        return {
            "version":MODULE_VERSION,
            "status":"OK",
            "startup_safe":True,
            "db_access":False,
            "startup_schema_ddl":False,
            "full_dashboard_activated":_ACTIVATED,
            "activation_error":_ACTIVATION_ERROR,
            "dashboard":"/v3/control-centre",
            "same_app":True,
        }

    @app.post("/api/v3/live/activate")
    def activate(req:Request):
        global _ACTIVATED,_ACTIVATION_ERROR
        if hasattr(core,"need_login"):
            core.need_login(req)

        if _ACTIVATED:
            return {
                "version":MODULE_VERSION,
                "status":"ACTIVE",
                "mounted_now":False,
                "full_dashboard_activated":True,
                "dashboard":"/v3/control-centre",
            }

        try:
            import alliance_v36_live_dashboard as impl
            impl.register(core)
            _ACTIVATED=True
            _ACTIVATION_ERROR=None
            return {
                "version":MODULE_VERSION,
                "status":"ACTIVE",
                "mounted_now":True,
                "full_dashboard_activated":True,
                "implementation_version":impl.MODULE_VERSION,
                "dashboard":"/v3/control-centre",
            }
        except Exception as exc:
            _ACTIVATION_ERROR=f"{type(exc).__name__}: {exc}"
            return {
                "version":MODULE_VERSION,
                "status":"ACTIVATION_ERROR",
                "mounted_now":False,
                "full_dashboard_activated":False,
                "error_type":type(exc).__name__,
                "message":str(exc),
            }
