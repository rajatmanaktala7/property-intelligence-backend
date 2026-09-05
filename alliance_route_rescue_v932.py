from __future__ import annotations

VERSION="11.0.0-ROUTE-PRIORITY-RESCUE"

FINAL_PATHS=(
    "/team-dashboard-v376",
    "/team-dashboard-live",
    "/alliance/primary",
    "/alliance/final/databases",
    "/alliance/final/requirements",
    "/alliance/final/database/{source}",
    "/alliance/final/requirements/{source}",
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

def register(wrapped):
    app=wrapped.app
    core=wrapped.core

    cre10=None
    cre10_error=None
    try:
        import alliance_cre_os_v1000 as os10
        cre10=os10.register(core)
    except Exception as exc:
        cre10_error=f"{type(exc).__name__}: {exc}"
        print("[alliance-cre-os-v1000] warning:",cre10_error)

    cre11=None
    cre11_error=None
    try:
        import alliance_cre_os_v1100 as os11
        cre11=os11.register(core)
    except Exception as exc:
        cre11_error=f"{type(exc).__name__}: {exc}"
        print("[alliance-cre-os-v1100] warning:",cre11_error)

    moved={}
    for p in reversed(FINAL_PATHS):
        moved[p]=_move_front(app,p)

    return {
        "status":"REGISTERED",
        "version":VERSION,
        "cre10":cre10,
        "cre10_error":cre10_error,
        "cre11":cre11,
        "cre11_error":cre11_error,
        "moved":moved,
        "route_count":len(app.router.routes),
    }
