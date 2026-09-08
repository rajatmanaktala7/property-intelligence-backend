from __future__ import annotations
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

VERSION="12.4.24-TEAM-WORKFLOW-REPAIR-AUTHORITY"
TARGET_GETS=(
    "/alliance/primary/requirements",
    "/alliance/primary/availability",
    "/alliance/primary/matcher",
)

def _app(core): return getattr(core,"app",None) or core

def _remove_extra_nav(app):
    removed=0
    try:
        kept=[]
        for m in list(getattr(app,"user_middleware",[]) or []):
            if getattr(getattr(m,"cls",None),"__name__","")=="BackToDashboardMiddleware":
                removed+=1
                continue
            kept.append(m)
        app.user_middleware=kept
        app.middleware_stack=None
    except Exception:
        pass
    try: app.state.alliance_back_to_dashboard_v1221=False
    except Exception: pass
    return removed

def _takeover(app,path):
    candidates=[]
    for r in list(app.router.routes):
        if getattr(r,"path",None)==path and "GET" in set(getattr(r,"methods",set()) or set()):
            ep=getattr(r,"endpoint",None)
            if getattr(ep,"__module__","")=="alliance_primary_workspace_v730":
                candidates.append(r)
    if not candidates:
        return {"path":path,"status":"MISSING_WORKSPACE_ROUTE"}
    chosen=candidates[-1]
    endpoint=chosen.endpoint
    app.router.routes[:]=[
        r for r in list(app.router.routes)
        if not (getattr(r,"path",None)==path and "GET" in set(getattr(r,"methods",set()) or set()))
    ]
    app.add_api_route(path,endpoint,methods=["GET"],response_class=HTMLResponse,include_in_schema=False)
    return {"path":path,"status":"AUTHORITATIVE","module":getattr(endpoint,"__module__",""),"name":getattr(endpoint,"__name__","")}

def register(core):
    app=_app(core)
    nav_removed=_remove_extra_nav(app)
    routes=[_takeover(app,p) for p in TARGET_GETS]
    app.router.routes[:]=[r for r in list(app.router.routes) if getattr(r,"path",None)!="/api/alliance/workflow-repair-12424/status"]

    @app.get("/api/alliance/workflow-repair-12424/status")
    def status(req:Request):
        state={}
        for p in TARGET_GETS:
            state[p]=[
                {"module":getattr(getattr(r,"endpoint",None),"__module__",""),
                 "name":getattr(getattr(r,"endpoint",None),"__name__","")}
                for r in app.router.routes
                if getattr(r,"path",None)==p and "GET" in set(getattr(r,"methods",set()) or set())
            ]
        middleware=[getattr(getattr(m,"cls",None),"__name__","") for m in list(getattr(app,"user_middleware",[]) or [])]
        ok=all(len(state[p])==1 and state[p][0]["module"]=="alliance_primary_workspace_v730" for p in TARGET_GETS)
        ok=ok and "BackToDashboardMiddleware" not in middleware
        return JSONResponse({"status":"PASS" if ok else "CHECK","version":VERSION,"routes":state,"middleware":middleware,"extra_global_navigation_removed":"BackToDashboardMiddleware" not in middleware})

    try: app.middleware_stack=None
    except Exception: pass
    return {"status":"AUTHORITATIVE","version":VERSION,"routes":routes,"extra_global_navigation_removed":nav_removed,"matcher_algorithm_changed":False}
