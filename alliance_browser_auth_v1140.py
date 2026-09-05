from __future__ import annotations
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

VERSION="11.4.0-BROWSER-AUTH-STABILITY"

PUBLIC_PREFIXES=(
    "/healthz","/readyz","/boot-status","/runtime-status",
    "/login","/logout","/favicon.ico","/static","/assets"
)

class BrowserAuthStabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request:Request, call_next):
        path=request.url.path
        response=await call_next(request)

        # APIs deliberately keep normal 401 behavior.
        if response.status_code != 401 or path.startswith("/api/"):
            return response

        # Never redirect login/logout/public health endpoints.
        if any(path.startswith(p) for p in PUBLIC_PREFIXES):
            return response

        accept=(request.headers.get("accept") or "").lower()
        # Browser navigation: one-way redirect to /login with no next-loop.
        if "text/html" in accept:
            return RedirectResponse(
                "/login",
                status_code=303,
                headers={
                    "Cache-Control":"no-store, no-cache, must-revalidate, max-age=0",
                    "Pragma":"no-cache",
                    "Expires":"0",
                    "X-Alliance-Auth-Fix":"11.4.0",
                },
            )
        return response

def register(wrapped):
    app=wrapped.app
    # Install once.
    names=[m.cls.__name__ for m in getattr(app,"user_middleware",[]) if getattr(m,"cls",None)]
    if "BrowserAuthStabilityMiddleware" not in names:
        app.add_middleware(BrowserAuthStabilityMiddleware)
    return {"status":"REGISTERED","version":VERSION,"policy":"browser 401 -> /login; API 401 preserved"}
