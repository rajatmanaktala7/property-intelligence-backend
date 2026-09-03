from __future__ import annotations

import inspect
from starlette.responses import HTMLResponse

VERSION = "19.4-TEAM-DASHBOARD-NEWSPAPER-CAPTURE"
TARGET_ROUTE = "/team-dashboard-v376"
CAPTURE_URL = "/capture-intelligence?source_type=NEWSPAPER"

CARD = """
<section class="alliance-newspaper-capture-v194"
         style="margin:18px 0;padding:18px;border:1px solid #d8e6f3;border-radius:14px;background:#f8fbff">
  <h3 style="margin:0 0 8px 0">Newspaper Capture</h3>
  <p style="margin:0 0 12px 0">
    Upload the full newspaper page. The original source is preserved before AI extraction.
  </p>
  <a href="/capture-intelligence?source_type=NEWSPAPER"
     style="display:inline-block;padding:10px 14px;border-radius:9px;background:#0b5ed7;
            color:white;text-decoration:none;font-weight:700">
    Upload &amp; Process Newspaper
  </a>
</section>
"""

TOP_LINK = """
<a class="alliance-newspaper-capture-v194-top"
   href="/capture-intelligence?source_type=NEWSPAPER"
   style="display:inline-block;margin:4px 6px 4px 0;padding:8px 11px;border-radius:8px;
          text-decoration:none;background:#eef6ff;color:#0b5ed7;font-weight:700">
  Newspaper Capture
</a>
"""

def _inject_dashboard(html: str) -> str:
    if "alliance-newspaper-capture-v194" in html:
        return html

    updated = html

    # Add a clear top navigation shortcut beside Newspaper Live when possible.
    newspaper_live_pos = updated.find(">Newspaper Live<")
    if newspaper_live_pos >= 0:
        start = updated.rfind("<a", 0, newspaper_live_pos)
        if start >= 0:
            updated = updated[:start] + TOP_LINK + updated[start:]

    # Add a full Team Daily Operations card immediately before the existing
    # Newspaper Live operation card when possible.
    marker = "<h3>Newspaper Live</h3>"
    pos = updated.find(marker)
    if pos < 0:
        marker = "Newspaper Live"
        pos = updated.find(marker)

    if pos >= 0:
        # Walk backwards to a likely card/container boundary.
        candidates = [
            updated.rfind("<section", 0, pos),
            updated.rfind("<div", 0, pos),
            updated.rfind("<article", 0, pos),
        ]
        start = max(candidates)
        if start >= 0:
            updated = updated[:start] + CARD + updated[start:]
        else:
            updated = updated[:pos] + CARD + updated[pos:]
    else:
        # Fallback: place under Team Daily Operations heading.
        heading = "Team Daily Operations"
        hpos = updated.find(heading)
        if hpos >= 0:
            close = updated.find(">", hpos)
            if close >= 0:
                updated = updated[:close+1] + CARD + updated[close+1:]
            else:
                updated += CARD
        elif "</body>" in updated:
            updated = updated.replace("</body>", CARD + "</body>", 1)
        else:
            updated += CARD

    return updated


def _html_response_with_same_status(result, body: str):
    headers = {}
    try:
        headers = dict(result.headers)
        headers.pop("content-length", None)
    except Exception:
        headers = {}

    return HTMLResponse(
        content=body,
        status_code=getattr(result, "status_code", 200),
        headers=headers,
    )


def register(wrapped):
    app = wrapped.app

    target = None
    for route in app.router.routes:
        if (
            getattr(route, "path", None) == TARGET_ROUTE
            and "GET" in set(getattr(route, "methods", set()) or set())
        ):
            target = route
            break

    if target is None:
        return {
            "status": "SKIPPED",
            "version": VERSION,
            "reason": f"{TARGET_ROUTE} GET route not found",
            "capture_url": CAPTURE_URL,
        }

    if getattr(target.endpoint, "_newspaper_dashboard_v194", False):
        return {
            "status": "ALREADY_REGISTERED",
            "version": VERSION,
            "route": TARGET_ROUTE,
            "capture_url": CAPTURE_URL,
        }

    original = target.endpoint

    async def wrapped_dashboard(*args, **kwargs):
        result = original(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result

        content_type = ""
        try:
            content_type = result.headers.get("content-type", "")
        except Exception:
            pass

        if not isinstance(result, HTMLResponse) and "text/html" not in content_type.lower():
            return result

        try:
            body = bytes(result.body).decode("utf-8")
        except Exception:
            return result

        body = _inject_dashboard(body)
        return _html_response_with_same_status(result, body)

    wrapped_dashboard._newspaper_dashboard_v194 = True
    target.endpoint = wrapped_dashboard

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": TARGET_ROUTE,
        "capture_url": CAPTURE_URL,
        "scope": "team-dashboard-navigation-only",
        "database_changes": False,
        "extractor_changes": False,
    }
