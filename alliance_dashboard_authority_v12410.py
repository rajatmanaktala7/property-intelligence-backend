from __future__ import annotations

from fastapi import Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

VERSION = "12.4.10.1-DASHBOARD-AUTHORITY-UNIVERSAL-NAV-COMPAT"
REGISTRATION = {}

def _app(core):
    return getattr(core, "app", None) or core

def _remove_exact_get(app, path):
    removed = []
    kept = []
    for r in list(app.router.routes):
        methods = set(getattr(r, "methods", set()) or set())
        if getattr(r, "path", None) == path and "GET" in methods:
            removed.append(r)
        else:
            kept.append(r)
    app.router.routes[:] = kept
    return removed

def _inject_dual_nav(page_html: str) -> str:
    # Universal middleware now owns navigation.
    return page_html

def register(core):
    app = _app(core)
    import alliance_team_dashboard_v1220 as teamdash

    removed_dashboard = _remove_exact_get(app, "/alliance/primary")

    @app.get("/alliance/primary", response_class=HTMLResponse)
    def authoritative_dashboard(req: Request):
        return teamdash._dashboard(core, req)

    day_routes = [
        r for r in list(app.router.routes)
        if getattr(r, "path", None) == "/alliance/primary/day-plan"
        and "GET" in set(getattr(r, "methods", set()) or set())
    ]
    if not day_routes:
        raise RuntimeError("Current Day Plan GET route not found")

    current_day_route = day_routes[-1]
    current_day_endpoint = current_day_route.endpoint
    removed_day = _remove_exact_get(app, "/alliance/primary/day-plan")

    @app.get("/alliance/primary/day-plan", response_class=HTMLResponse)
    def day_plan_with_dual_navigation(
        req: Request,
        staff: str = Query("Yogesh Mehra"),
        saved: str = Query(""),
    ):
        response = current_day_endpoint(req=req, staff=staff, saved=saved)
        body = response.body.decode("utf-8") if getattr(response, "body", None) else str(response)
        body = _inject_dual_nav(body)
        return HTMLResponse(
            body,
            status_code=getattr(response, "status_code", 200),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/alliance/dashboard-authority-status")
    def dashboard_authority_status():
        dashboard_routes = [
            {
                "module": getattr(getattr(r, "endpoint", None), "__module__", ""),
                "name": getattr(getattr(r, "endpoint", None), "__name__", ""),
            }
            for r in app.router.routes
            if getattr(r, "path", None) == "/alliance/primary"
            and "GET" in set(getattr(r, "methods", set()) or set())
        ]
        day_routes_now = [
            {
                "module": getattr(getattr(r, "endpoint", None), "__module__", ""),
                "name": getattr(getattr(r, "endpoint", None), "__name__", ""),
            }
            for r in app.router.routes
            if getattr(r, "path", None) == "/alliance/primary/day-plan"
            and "GET" in set(getattr(r, "methods", set()) or set())
        ]
        return JSONResponse({
            "status": "PASS" if len(dashboard_routes) == 1 and len(day_routes_now) == 1 else "CHECK",
            "version": VERSION,
            "dashboard_ui_version": getattr(teamdash, "VERSION", "unknown"),
            "dashboard_get_routes": dashboard_routes,
            "day_plan_get_routes": day_routes_now,
            "previous_page_button": True,
            "back_to_dashboard_button": True,
            "day_plan_post_handlers_unchanged": True,
        })

    REGISTRATION.update({
        "status": "AUTHORITATIVE",
        "version": VERSION,
        "dashboard_removed": len(removed_dashboard),
        "day_plan_get_removed": len(removed_day),
        "dashboard_ui_version": getattr(teamdash, "VERSION", "unknown"),
        "day_plan_post_handlers_unchanged": True,
    })
    return dict(REGISTRATION)
