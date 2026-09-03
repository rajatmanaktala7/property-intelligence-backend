from __future__ import annotations

import inspect
from starlette.responses import HTMLResponse

VERSION = "19.3-NEWSPAPER-DASHBOARD-LINK"

LINK_HTML = """
<a href="/capture-intelligence?source_type=NEWSPAPER"
   style="display:block;padding:11px 14px;margin:6px 0;border-radius:9px;
          text-decoration:none;background:#eef6ff;color:#0b5ed7;font-weight:700">
  📰 Newspaper Capture
</a>
"""

def _inject(html: str) -> str:
    if "Newspaper Capture" in html:
        return html

    # Best placement: directly beside/near WhatsApp Capture when possible.
    markers = [
        ">WhatsApp Capture<",
        "> Capture Property<",
        ">Property Database<",
        "</nav>",
        "</aside>",
    ]

    for marker in markers:
        pos = html.find(marker)
        if pos >= 0:
            if marker.startswith(">"):
                # Insert before the opening tag that owns the visible marker.
                start = html.rfind("<a", 0, pos)
                if start >= 0:
                    return html[:start] + LINK_HTML + html[start:]
            else:
                return html[:pos] + LINK_HTML + html[pos:]

    # Safe fallback: add a compact floating shortcut before </body>.
    fallback = """
<div style="position:fixed;right:18px;bottom:18px;z-index:9999">
  <a href="/capture-intelligence?source_type=NEWSPAPER"
     style="display:inline-block;padding:12px 15px;border-radius:10px;background:#0b5ed7;
            color:white;text-decoration:none;font-weight:700;box-shadow:0 4px 18px #0002">
    📰 Newspaper Capture
  </a>
</div>
"""
    if "</body>" in html:
        return html.replace("</body>", fallback + "</body>", 1)
    return html + fallback


def register(wrapped):
    app = wrapped.app

    target = None
    for route in app.router.routes:
        if getattr(route, "path", None) == "/workspace" and "GET" in set(getattr(route, "methods", set()) or set()):
            target = route
            break

    if target is None:
        return {
            "status": "SKIPPED",
            "version": VERSION,
            "reason": "/workspace GET route not found",
        }

    if getattr(target.endpoint, "_newspaper_dashboard_v193", False):
        return {
            "status": "ALREADY_REGISTERED",
            "version": VERSION,
            "route": "/workspace",
        }

    original = target.endpoint

    async def wrapped_workspace(*args, **kwargs):
        result = original(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result

        # Redirects and non-HTML responses are preserved untouched.
        ctype = ""
        try:
            ctype = result.headers.get("content-type", "")
        except Exception:
            pass

        if not isinstance(result, HTMLResponse) and "text/html" not in ctype.lower():
            return result

        try:
            body = bytes(result.body).decode("utf-8")
        except Exception:
            return result

        body = _inject(body)

        headers = dict(result.headers)
        headers.pop("content-length", None)
        return HTMLResponse(
            content=body,
            status_code=result.status_code,
            headers=headers,
        )

    wrapped_workspace._newspaper_dashboard_v193 = True
    target.endpoint = wrapped_workspace

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": "/workspace",
        "link": "/capture-intelligence?source_type=NEWSPAPER",
        "scope": "dashboard-navigation-only",
    }
