from __future__ import annotations
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

VERSION = "11.7.2-FINAL-ROUTE-TAKEOVER"

AUTHORITATIVE_PATHS = (
    "/alliance/source/manual",
    "/alliance/source/newspaper",
    "/alliance/source/magazine",
    "/alliance/source/whatsapp",
    "/alliance/final/database/manual",
    "/property-manual",
    "/manual-property-v18",
    "/manual-property",
    "/alliance/property-add/manual",
    "/alliance/property-edit/{source}/{record_id}",
    "/alliance/property-archive/{source}/{record_id}",
    "/alliance/property-media/{source}/{record_id}",
    "/alliance/media-file/{store}/{media_id}",
)

def _move_front(app, path):
    found = [r for r in list(app.router.routes) if getattr(r, "path", None) == path]
    for r in found:
        try:
            app.router.routes.remove(r)
        except ValueError:
            pass
    for r in reversed(found):
        app.router.routes.insert(0, r)
    return len(found)

class DashboardBackMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        ctype = (response.headers.get("content-type") or "").lower()
        path = request.url.path

        if (
            "text/html" not in ctype
            or path in ("/login", "/logout", "/alliance/primary", "/team-dashboard-v376", "/team-dashboard-live")
            or response.status_code >= 400
        ):
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        try:
            txt = body.decode("utf-8")
        except Exception:
            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )

        marker = 'data-alliance-back-dashboard="11.7.2"'
        if marker not in txt:
            button = '''<a data-alliance-back-dashboard="11.7.2"
href="/alliance/primary"
style="position:fixed;right:16px;bottom:16px;z-index:2147483647;background:#1f6f43;color:#fff;text-decoration:none;font:700 13px Arial;padding:10px 14px;border:1px solid #14532d;border-radius:4px;box-shadow:0 2px 8px rgba(0,0,0,.22)">← Back to Dashboard</a>'''
            low = txt.lower()
            idx = low.rfind("</body>")
            txt = txt[:idx] + button + txt[idx:] if idx >= 0 else txt + button

        headers = dict(response.headers)
        headers.pop("content-length", None)
        headers["Cache-Control"] = "no-store"
        headers["X-Alliance-Nav-Version"] = VERSION
        return Response(
            content=txt.encode("utf-8"),
            status_code=response.status_code,
            headers=headers,
            media_type="text/html",
        )

def register(wrapped):
    app = wrapped.app
    result = {"status": "REGISTERED", "version": VERSION}

    try:
        import alliance_cre_os_v1171 as v1171
        result["cre1171_refresh"] = v1171.register(wrapped)
    except Exception as ex:
        result["cre1171_refresh_error"] = f"{type(ex).__name__}: {ex}"

    result["authoritative_routes"] = {
        path: _move_front(app, path) for path in reversed(AUTHORITATIVE_PATHS)
    }

    result["commercial_intelligence_moved"] = _move_front(app, "/commercial-intelligence")

    if not getattr(app.state, "alliance_dashboard_back_v1172", False):
        app.add_middleware(DashboardBackMiddleware)
        app.state.alliance_dashboard_back_v1172 = True
        result["dashboard_back_middleware"] = "INSTALLED"
    else:
        result["dashboard_back_middleware"] = "ALREADY_INSTALLED"

    result["route_count"] = len(app.router.routes)
    return result
