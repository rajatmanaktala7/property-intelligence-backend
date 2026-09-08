from __future__ import annotations

import re

VERSION = "12.4.12-UNIVERSAL-TOP-PREVIOUS-AND-DASHBOARD"
DASHBOARD_URL = "/alliance/primary"

EXCLUDED_EXACT = {
    "/",
    "/alliance/primary",
    "/login",
    "/logout",
    "/health",
    "/healthz",
    "/readyz",
    "/boot-status",
    "/runtime-status",
    "/core-route-status",
    "/docs",
    "/redoc",
    "/openapi.json",
}

EXCLUDED_PREFIXES = (
    "/api/",
    "/alliance/media-file/",
    "/alliance/property-media/",
    "/media/",
    "/static/",
    "/assets/",
    "/download/",
)

def _app(core):
    return getattr(core, "app", None) or core

def _should_show(path: str) -> bool:
    path = str(path or "")
    if not path:
        return False
    if path in EXCLUDED_EXACT:
        return False
    if any(path.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
        return False
    return True

TOP_BAR = '''
<style id="alliance-universal-top-nav-style">
.alliance-universal-top-nav {
    width:100%;
    display:flex;
    align-items:center;
    gap:10px;
    flex-wrap:wrap;
    padding:10px 16px;
    background:#eef4ff;
    border-bottom:1px solid #cddcf7;
    position:relative;
    z-index:99999;
    font-family:Arial,sans-serif;
}
.alliance-universal-top-nav button,
.alliance-universal-top-nav a {
    display:inline-flex;
    align-items:center;
    padding:9px 14px;
    border:0;
    border-radius:8px;
    color:#fff !important;
    text-decoration:none !important;
    font-size:13px;
    font-weight:800;
    cursor:pointer;
}
.alliance-universal-prev { background:#475467; }
.alliance-universal-dashboard { background:#102a43; }
.alliance-universal-top-nav span {
    color:#475467;
    font-size:12px;
    font-weight:600;
}
@media(max-width:640px){
    .alliance-universal-top-nav span{display:none}
    .alliance-universal-top-nav{padding:8px 10px}
}
</style>
<div id="alliance-universal-top-nav" class="alliance-universal-top-nav">
    <button type="button" class="alliance-universal-prev" onclick="history.back()">&larr; Previous Page</button>
    <a class="alliance-universal-dashboard" href="/alliance/primary">&larr; Back to Dashboard</a>
    <span>Alliance CRE · Navigation</span>
</div>
'''

class BackToDashboardMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method") or "").upper()
        path = str(scope.get("path") or "")

        if method != "GET" or not _should_show(path):
            await self.app(scope, receive, send)
            return

        response_start = None
        body_parts = []

        async def capture_send(message):
            nonlocal response_start

            if message["type"] == "http.response.start":
                response_start = message
                return

            if message["type"] != "http.response.body":
                await send(message)
                return

            body_parts.append(message.get("body", b""))

            if message.get("more_body", False):
                return

            if response_start is None:
                await send(message)
                return

            headers = list(response_start.get("headers", []))
            content_type = ""

            for key, value in headers:
                if key.lower() == b"content-type":
                    content_type = value.decode("latin-1", errors="ignore").lower()
                    break

            original_body = b"".join(body_parts)

            if "text/html" not in content_type:
                await send(response_start)
                await send({
                    "type": "http.response.body",
                    "body": original_body,
                    "more_body": False,
                })
                return

            try:
                page = original_body.decode("utf-8")
            except UnicodeDecodeError:
                await send(response_start)
                await send({
                    "type": "http.response.body",
                    "body": original_body,
                    "more_body": False,
                })
                return

            page = re.sub(
                r'<style id="alliance-back-dashboard-style">.*?</div>\s*',
                "",
                page,
                count=1,
                flags=re.I | re.S,
            )

            if 'id="alliance-universal-top-nav"' not in page:
                body_tag = re.search(r"<body(?:\s[^>]*)?>", page, flags=re.I)
                if body_tag:
                    pos = body_tag.end()
                    page = page[:pos] + TOP_BAR + page[pos:]
                else:
                    page = TOP_BAR + page

            new_body = page.encode("utf-8")
            new_headers = [
                (key, value)
                for key, value in headers
                if key.lower() != b"content-length"
            ]
            new_headers.append(
                (b"content-length", str(len(new_body)).encode("ascii"))
            )

            new_start = dict(response_start)
            new_start["headers"] = new_headers

            await send(new_start)
            await send({
                "type": "http.response.body",
                "body": new_body,
                "more_body": False,
            })

        await self.app(scope, receive, capture_send)

def register(core):
    app = _app(core)
    if app is None:
        raise RuntimeError("Universal top navigation requires FastAPI app")

    if getattr(app.state, "alliance_back_to_dashboard_v1221", False):
        return {
            "status": "ALREADY_REGISTERED",
            "version": VERSION,
            "navigation": "TOP_ONLY_PREVIOUS_AND_DASHBOARD",
        }

    app.add_middleware(BackToDashboardMiddleware)
    app.state.alliance_back_to_dashboard_v1221 = True

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "dashboard": DASHBOARD_URL,
        "navigation": "TOP_ONLY_PREVIOUS_AND_DASHBOARD",
        "database_mutation": False,
        "matcher_changed": False,
    }
