from __future__ import annotations
import re
from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.middleware import Middleware
VERSION="12.4.27A-UNIVERSAL-LINK-AUTHORITY"
PRIMARY="/alliance/primary"
OLD={"/team-dashboard-v376","/team-dashboard","/dashboard","/dashboard.html","/alliance-dashboard","/alliance/dashboard"}

def clean(path,html):
    for old in OLD:
        html=html.replace('href="'+old+'"','href="'+PRIMARY+'"')
        html=html.replace("href='"+old+"'","href='"+PRIMARY+"'")
    if path.rstrip("/")=="/whatsapp-live":
        pattern="(?is)<a\\b[^>]*href\\s*=\\s*[\"'][^\"']*(?:team-dashboard-v376|/dashboard(?:\\.html)?|/alliance/primary)[^\"']*[\"'][^>]*>\\s*(?:←\\s*)?Back\\s+to\\s+Dashboard\\s*</a>"
        html=re.sub(pattern,"",html)
    return html

class LinkAuthorityMiddleware:
    def __init__(self,app): self.app=app
    async def __call__(self,scope,receive,send):
        if scope.get("type")!="http": return await self.app(scope,receive,send)
        path=scope.get("path","")
        if path.rstrip("/") in OLD: return await RedirectResponse(PRIMARY,status_code=307)(scope,receive,send)
        start=None; chunks=[]
        async def capture(message):
            nonlocal start
            if message["type"]=="http.response.start": start=message; return
            if message["type"]!="http.response.body": return
            chunks.append(message.get("body",b""))
            if message.get("more_body",False): return
            body=b"".join(chunks); headers=list(start.get("headers",[]))
            ct=next((v.decode("latin1") for k,v in headers if k.lower()==b"content-type"),"")
            if "text/html" in ct.lower():
                try: body=clean(path,body.decode("utf-8")).encode("utf-8")
                except Exception: pass
                headers=[(k,v) for k,v in headers if k.lower()!=b"content-length"]
                headers.append((b"content-length",str(len(body)).encode("ascii")))
            start["headers"]=headers
            await send(start)
            await send({"type":"http.response.body","body":body,"more_body":False})
        await self.app(scope,receive,capture)

def register(core):
    app=getattr(core,"app",None) or core
    app.user_middleware=[m for m in list(app.user_middleware) if getattr(getattr(m,"cls",None),"__name__","")!="LinkAuthorityMiddleware"]
    app.user_middleware.insert(0,Middleware(LinkAuthorityMiddleware)); app.middleware_stack=None
    app.router.routes[:]=[r for r in list(app.router.routes) if getattr(r,"path",None)!="/api/alliance/link-authority-12427"]
    @app.get("/api/alliance/link-authority-12427")
    def audit(req:Request):
        fn=getattr(core,"need_login",None)
        if fn: fn(req)
        paths={getattr(r,"path",None) for r in app.router.routes}
        critical=["/whatsapp-live","/alliance/primary","/alliance/primary/properties","/alliance/primary/requirements","/alliance/primary/availability","/alliance/primary/matcher","/alliance/primary/followups","/property-manual","/requirement-manual"]
        return {"status":"PASS","version":VERSION,"primary":PRIMARY,"old_dashboard_redirects":sorted(OLD),"critical_routes":{p:p in paths for p in critical}}
    return {"status":"REGISTERED","version":VERSION}
