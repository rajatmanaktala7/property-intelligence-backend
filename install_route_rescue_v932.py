from pathlib import Path
from datetime import datetime
import shutil,py_compile
R=Path(__file__).resolve().parent
M=R/"alliance_route_rescue_v932.py"
P=R/"production_entrypoint.py"
M.write_text('from __future__ import annotations\nfrom fastapi import Request\nfrom fastapi.responses import RedirectResponse, HTMLResponse\n\nVERSION="9.3.2-ROUTE-PRIORITY-RESCUE"\n\nFINAL_PATHS=(\n    "/alliance/primary",\n    "/alliance/final/databases",\n    "/alliance/final/requirements",\n    "/alliance/final/database/{source}",\n    "/alliance/final/requirements/{source}",\n)\n\nALIASES=(\n    ("/alliance/primary/databases","/alliance/final/databases"),\n    ("/alliance/primary/database/{source}","/alliance/final/database/{source}"),\n    ("/alliance/primary/properties","/alliance/final/database/master"),\n    ("/alliance/primary/requirements-hub","/alliance/final/requirements"),\n    ("/alliance/primary/requirements/source/{source}","/alliance/final/requirements/{source}"),\n    ("/alliance/primary/requirements","/alliance/final/requirements/master"),\n)\n\ndef _get_routes(app,path):\n    return [r for r in list(app.router.routes)\n            if getattr(r,"path",None)==path and "GET" in set(getattr(r,"methods",set()) or set())]\n\ndef _move_front(app,path):\n    found=_get_routes(app,path)\n    for r in found:\n        try: app.router.routes.remove(r)\n        except ValueError: pass\n    for r in reversed(found):\n        app.router.routes.insert(0,r)\n    return len(found)\n\ndef _remove_get(app,path):\n    keep=[]; removed=0\n    for r in list(app.router.routes):\n        if getattr(r,"path",None)==path and "GET" in set(getattr(r,"methods",set()) or set()):\n            removed+=1\n        else:\n            keep.append(r)\n    app.router.routes[:]=keep\n    return removed\n\ndef register(wrapped):\n    app=wrapped.app\n    core=wrapped.core\n\n    # If 9.3 failed earlier, retry its registration once before route rescue.\n    if not _get_routes(app,"/alliance/primary"):\n        try:\n            import alliance_organized_main_v930 as organized\n            organized.register(core)\n        except Exception as exc:\n            print("[route-rescue-v932] retry warning:",type(exc).__name__,str(exc))\n\n    # Recreate stable aliases directly; do not depend on old redirect order.\n    for old,target in ALIASES:\n        _remove_get(app,old)\n        if "{source}" in old:\n            async def _src(request:Request,source:str,_target=target):\n                qs=request.url.query\n                url=_target.replace("{source}",source)\n                return RedirectResponse(url+("?" + qs if qs else ""),status_code=307)\n            app.add_api_route(old,_src,methods=["GET"],include_in_schema=False)\n        else:\n            async def _plain(request:Request,_target=target):\n                qs=request.url.query\n                return RedirectResponse(_target+("?" + qs if qs else ""),status_code=307)\n            app.add_api_route(old,_plain,methods=["GET"],include_in_schema=False)\n\n    # Put Alliance routes ahead of legacy catch-all/mount routes.\n    prioritized=list(FINAL_PATHS)+[x[0] for x in ALIASES]\n    moved={}\n    for p in reversed(prioritized):\n        moved[p]=_move_front(app,p)\n\n    # Last-resort visible route instead of JSON 404 if main route is still missing.\n    if not _get_routes(app,"/alliance/primary"):\n        async def _fallback(request:Request):\n            return HTMLResponse("""<!doctype html><html><body style="font-family:Arial">\n            <h2>Alliance CRE Operating System</h2>\n            <p>Main dashboard route recovery is active.</p>\n            <p><a href="/alliance/final/databases">5 Property Databases</a></p>\n            <p><a href="/alliance/final/requirements">5 Requirement Databases</a></p>\n            <p><a href="/alliance/primary/matcher">Matcher</a> ·\n            <a href="/alliance/primary/availability">Verification</a> ·\n            <a href="/alliance/primary/followups">Follow-ups</a></p>\n            </body></html>""")\n        app.add_api_route("/alliance/primary",_fallback,methods=["GET"],include_in_schema=False)\n        _move_front(app,"/alliance/primary")\n        moved["/alliance/primary"]="FALLBACK_ADDED"\n\n    return {"status":"REGISTERED","version":VERSION,"moved":moved,"route_count":len(app.router.routes)}\n',encoding="utf-8")
stamp=datetime.now().strftime("%Y%m%d-%H%M%S")
shutil.copy2(P,P.with_name("production_entrypoint.before-route-rescue-v932-"+stamp+".py"))
s=P.read_text(encoding="utf-8")
marker="# ALLIANCE_ROUTE_RESCUE_V932"
bridge="""        # ALLIANCE_ROUTE_RESCUE_V932
        try:
            import alliance_route_rescue_v932 as route_rescue_v932
            rescue_result=route_rescue_v932.register(wrapped)
            stabilization=dict(stabilization or {})
            stabilization["route_rescue_v932"]=rescue_result
            print("[route-rescue-v932]",rescue_result)
        except Exception as exc:
            stabilization=dict(stabilization or {})
            stabilization["route_rescue_v932"]={"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[route-rescue-v932] warning:",type(exc).__name__,str(exc))
"""
if marker not in s:
    anchor="        CORE_APP = wrapped.app"
    if anchor not in s: raise RuntimeError("CORE_APP anchor not found")
    s=s.replace(anchor,bridge+"\n"+anchor,1)
    P.write_text(s,encoding="utf-8")
py_compile.compile(str(M),doraise=True)
py_compile.compile(str(P),doraise=True)
print("Alliance 9.3.2 route rescue installed.")
print("Alliance main + 5x5 routes are now forced ahead of legacy catch-all routes.")
