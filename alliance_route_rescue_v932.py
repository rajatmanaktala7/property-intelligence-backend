from __future__ import annotations
from fastapi import Request
from fastapi.responses import RedirectResponse, HTMLResponse

VERSION="10.0.0-ROUTE-PRIORITY-RESCUE"

FINAL_PATHS=(
    "/alliance/primary",
    "/alliance/final/databases",
    "/alliance/final/requirements",
    "/alliance/final/database/{source}",
    "/alliance/final/requirements/{source}",
)

ALIASES=(
    ("/alliance/primary/databases","/alliance/final/databases"),
    ("/alliance/primary/database/{source}","/alliance/final/database/{source}"),
    ("/alliance/primary/properties","/alliance/final/database/master"),
    ("/alliance/primary/requirements-hub","/alliance/final/requirements"),
    ("/alliance/primary/requirements/source/{source}","/alliance/final/requirements/{source}"),
    ("/alliance/primary/requirements","/alliance/final/requirements/master"),
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
    keep=[]; removed=0
    for r in list(app.router.routes):
        if getattr(r,"path",None)==path and "GET" in set(getattr(r,"methods",set()) or set()):
            removed+=1
        else:
            keep.append(r)
    app.router.routes[:]=keep
    return removed

def register(wrapped):
    app=wrapped.app
    core=wrapped.core

    # Register final OS 10.0 LAST so the team dashboard owns the visible routes.
    final_result=None
    final_error=None
    try:
        import alliance_cre_os_v1000 as final_os
        final_result=final_os.register(core)
    except Exception as exc:
        final_error=f"{type(exc).__name__}: {exc}"
        print("[alliance-cre-os-v1000] warning:", final_error)

    # Stable aliases are recreated directly.
    for old,target in ALIASES:
        if old in (
            "/alliance/primary/databases",
            "/alliance/primary/database/{source}",
            "/alliance/primary/properties",
            "/alliance/primary/requirements-hub",
            "/alliance/primary/requirements/source/{source}",
            "/alliance/primary/requirements",
        ):
            # OS 10.0 normally owns these already. Only create if still absent.
            if _get_routes(app,old):
                continue
        _remove_get(app,old)
        if "{source}" in old:
            async def _src(request:Request,source:str,_target=target):
                qs=request.url.query
                url=_target.replace("{source}",source)
                return RedirectResponse(url+("?" + qs if qs else ""),status_code=307)
            app.add_api_route(old,_src,methods=["GET"],include_in_schema=False)
        else:
            async def _plain(request:Request,_target=target):
                qs=request.url.query
                return RedirectResponse(_target+("?" + qs if qs else ""),status_code=307)
            app.add_api_route(old,_plain,methods=["GET"],include_in_schema=False)

    prioritized=list(FINAL_PATHS)+[x[0] for x in ALIASES]+[
        "/alliance/primary/contacts",
        "/alliance/primary/ai-control",
        "/alliance/primary/data-health",
    ]
    moved={}
    for p in reversed(prioritized):
        moved[p]=_move_front(app,p)

    if not _get_routes(app,"/alliance/primary"):
        async def _fallback(request:Request):
            detail = final_error or "Final dashboard route was not registered."
            return HTMLResponse(f"""<!doctype html><html><body style="font-family:Arial">
            <h2>Alliance CRE Operating System</h2>
            <p>Safe fallback is active.</p>
            <p>{detail}</p>
            <p><a href="/alliance/final/databases">5 Property Databases</a></p>
            <p><a href="/alliance/final/requirements">5 Requirement Databases</a></p>
            <p><a href="/alliance/primary/matcher">Matcher</a> ·
            <a href="/alliance/primary/availability">Verification</a> ·
            <a href="/alliance/primary/followups">Follow-ups</a></p>
            </body></html>""")
        app.add_api_route("/alliance/primary",_fallback,methods=["GET"],include_in_schema=False)
        _move_front(app,"/alliance/primary")
        moved["/alliance/primary"]="FALLBACK_ADDED"

    return {
        "status":"REGISTERED",
        "version":VERSION,
        "final_os_v1000":final_result,
        "final_os_error":final_error,
        "moved":moved,
        "route_count":len(app.router.routes),
    }
