from __future__ import annotations
VERSION="11.4.0-STABLE-PRODUCTION-ROUTES"

FINAL_PATHS=(
 "/team-dashboard-v376","/team-dashboard-live","/alliance/primary",
 "/alliance/final/databases","/alliance/final/requirements",
 "/alliance/final/database/{source}","/alliance/final/requirements/{source}",
 "/alliance/primary/availability","/alliance/primary/matcher",
 "/alliance/primary/followups","/alliance/primary/reports",
 "/alliance/primary/contacts","/alliance/primary/ai-control",
 "/alliance/primary/data-health","/commercial-intelligence",
)

def _move_front(app,path):
    found=[r for r in list(app.router.routes) if getattr(r,"path",None)==path]
    for r in found:
        try: app.router.routes.remove(r)
        except ValueError: pass
    for r in reversed(found): app.router.routes.insert(0,r)
    return len(found)

def register(wrapped):
    app=wrapped.app; core=wrapped.core
    result={"status":"REGISTERED","version":VERSION}

    try:
        import alliance_cre_os_v1000 as r
        result["renderer"]=r.register(core)
    except Exception as e:
        result["renderer_error"]=f"{type(e).__name__}: {e}"

    try:
        import alliance_cre_os_v1100 as d
        result["cre11"]=d.register(core)
    except Exception as e:
        result["cre11_error"]=f"{type(e).__name__}: {e}"

    try:
        import alliance_browser_auth_v1140 as a
        result["auth"]=a.register(wrapped)
    except Exception as e:
        result["auth_error"]=f"{type(e).__name__}: {e}"

    result["moved"]={p:_move_front(app,p) for p in reversed(FINAL_PATHS)}
    result["route_count"]=len(app.router.routes)
    return result
