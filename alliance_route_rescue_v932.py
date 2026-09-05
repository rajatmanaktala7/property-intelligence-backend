from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

VERSION="11.3.0-STABLE-ROUTE-RESCUE"

FINAL_PATHS=(
    "/team-dashboard-v376",
    "/team-dashboard-live",
    "/alliance/primary",
    "/alliance/final/databases",
    "/alliance/final/requirements",
    "/alliance/final/database/{source}",
    "/alliance/final/requirements/{source}",
    "/alliance/primary/availability",
    "/alliance/primary/matcher",
    "/alliance/primary/followups",
    "/alliance/primary/reports",
    "/alliance/primary/contacts",
    "/alliance/primary/ai-control",
    "/alliance/primary/data-health",
)

def _get_routes(app,path):
    return [r for r in list(app.router.routes)
            if getattr(r,"path",None)==path and "GET" in set(getattr(r,"methods",set()) or set())]

def _move_front(app,path):
    found=_get_routes(app,path)
    for r in found:
        try: app.router.routes.remove(r)
        except ValueError: pass
    for r in reversed(found):
        app.router.routes.insert(0,r)
    return len(found)

def _remove_get(app,path):
    app.router.routes[:] = [
        r for r in list(app.router.routes)
        if not (getattr(r,"path",None)==path and "GET" in set(getattr(r,"methods",set()) or set()))
    ]

def register(wrapped):
    app=wrapped.app
    core=wrapped.core

    renderer=None
    renderer_error=None
    try:
        import alliance_cre_os_v1000 as renderer_mod
        renderer=renderer_mod.register(core)
    except Exception as exc:
        renderer_error=f"{type(exc).__name__}: {exc}"
        print("[cre11.3-5x5-renderer] warning:",renderer_error)

    cre11=None
    cre11_error=None
    try:
        import alliance_cre_os_v1100 as os11
        cre11=os11.register(core)
    except Exception as exc:
        cre11_error=f"{type(exc).__name__}: {exc}"
        print("[cre11.3-dashboard] warning:",cre11_error)

    moved={}
    for p in reversed(FINAL_PATHS):
        moved[p]=_move_front(app,p)

    # Read-only route audit endpoint for quick production acceptance testing.
    _remove_get(app,"/alliance/cre11/route-audit")
    async def route_audit(req:Request):
        need_login=getattr(core,"need_login",None)
        if need_login:
            need_login(req)
        rows=[]
        for path in FINAL_PATHS:
            rs=_get_routes(app,path)
            rows.append({
                "path":path,
                "count":len(rs),
                "endpoint":getattr(getattr(rs[0],"endpoint",None),"__name__",None) if rs else None,
                "ok":bool(rs),
            })
        return JSONResponse({
            "status":"OK" if all(x["ok"] for x in rows) else "MISSING_ROUTE",
            "version":VERSION,
            "renderer_error":renderer_error,
            "cre11_error":cre11_error,
            "routes":rows,
            "route_count":len(app.router.routes),
        }, headers={"Cache-Control":"no-store","X-Alliance-CRE-Version":"11.3.0"})
    app.add_api_route("/alliance/cre11/route-audit",route_audit,methods=["GET"],include_in_schema=False)
    _move_front(app,"/alliance/cre11/route-audit")

    return {
        "status":"REGISTERED",
        "version":VERSION,
        "renderer":renderer,
        "renderer_error":renderer_error,
        "cre11":cre11,
        "cre11_error":cre11_error,
        "moved":moved,
        "route_count":len(app.router.routes),
        "policy":"NO ENDPOINT WRAPPING; PRESERVE FASTAPI QUERY INJECTION",
    }
