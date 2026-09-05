from __future__ import annotations
from fastapi import Request
from fastapi.responses import RedirectResponse

VERSION = "10.0.1-LEGACY-DASHBOARD-CLEANUP"
LEGACY_DASHBOARD_PATHS = ("/team-dashboard-v376",)

def _app(core):
    return getattr(core, "app", None) or core

def _remove_get(app, path):
    app.router.routes[:] = [
        r for r in list(app.router.routes)
        if not (getattr(r, "path", None) == path and "GET" in set(getattr(r, "methods", set()) or set()))
    ]

def _move_front(app, path):
    found = [r for r in list(app.router.routes)
             if getattr(r, "path", None) == path and "GET" in set(getattr(r, "methods", set()) or set())]
    for r in found:
        try:
            app.router.routes.remove(r)
        except ValueError:
            pass
    for r in reversed(found):
        app.router.routes.insert(0, r)

def register(core):
    app = _app(core)
    if app is None:
        raise RuntimeError("10.0.1 requires FastAPI app")

    for path in LEGACY_DASHBOARD_PATHS:
        _remove_get(app, path)

        async def _redirect(request: Request):
            qs = request.url.query
            target = "/alliance/primary"
            return RedirectResponse(target + (("?" + qs) if qs else ""), status_code=307)

        app.add_api_route(path, _redirect, methods=["GET"], include_in_schema=False)
        _move_front(app, path)

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "redirected": {"/team-dashboard-v376": "/alliance/primary"},
        "removed_dummy_sections": [
            "LIVE","WhatsApp Records","Newspaper Records","Hospitality","Retail Signals",
            "Live Data Freshness","WhatsApp UNKNOWN","Newspaper No data","Magazine No data",
            "Hospitality No data","Retail No data","Manual Property No data"
        ],
    }
