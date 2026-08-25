
from __future__ import annotations
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

MODULE_VERSION="3.7.5-FINAL-DASHBOARD-ROUTE-TAKEOVER"
FINAL_ROUTE="/team-dashboard-v375"

def register(app,engine,need_login):
    # Import the already-built V3.7.4 dashboard only when this module registers.
    import team_dashboard_v374 as v374

    @app.get("/api/team-dashboard-v375/status")
    def status(req:Request):
        need_login(req)
        return {
            "version":MODULE_VERSION,
            "status":"OK",
            "final_dashboard":FINAL_ROUTE,
            "source_ui_version":v374.MODULE_VERSION,
            "route_takeover":True,
            "same_app":True,
            "same_domain":True,
        }

    @app.get(FINAL_ROUTE,response_class=HTMLResponse)
    def final_dashboard(req:Request):
        need_login(req)
        return HTMLResponse(v374.DASHBOARD_HTML)

    # This middleware is intentionally registered LAST.
    # Starlette executes the newest middleware outermost, so it wins over
    # older dashboard redirect middleware and route collisions.
    @app.middleware("http")
    async def final_dashboard_takeover(request,call_next):
        path=request.url.path.rstrip("/") or "/"
        if path in {
            "/workspace",
            "/team-dashboard-live",
            "/final-dashboard-v10",
            "/final-dashboard-v11",
            "/final-dashboard-v13",
        }:
            return RedirectResponse(FINAL_ROUTE,status_code=307)
        return await call_next(request)
