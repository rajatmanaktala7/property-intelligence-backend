from __future__ import annotations

import re

VERSION = "12.2.1-BACK-TO-DASHBOARD"
DASHBOARD_URL = "/alliance/primary"


# Pages where the button must appear.
EXACT_PATHS = {
    "/requirements-workbench",
    "/property-manual",
}

PREFIX_PATHS = (
    "/alliance/primary/",
    "/alliance/source/magazine",
    "/alliance/property-add/",
)


def _app(core):
    return getattr(core, "app", None) or core


def _should_show(path: str) -> bool:
    # Do not show "Back to Dashboard" while already on Dashboard.
    if path == DASHBOARD_URL:
        return False

    if path in EXACT_PATHS:
        return True

    return any(path.startswith(prefix) for prefix in PREFIX_PATHS)


BACK_BAR = """
<style id="alliance-back-dashboard-style">

.alliance-back-dashboard-bar {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 16px;
    background: #eef4ff;
    border-bottom: 1px solid #cddcf7;
    position: relative;
    z-index: 9998;
    font-family: Arial, sans-serif;
}

.alliance-back-dashboard-link {
    display: inline-flex;
    align-items: center;
    padding: 9px 14px;
    background: #102a43;
    color: #ffffff !important;
    text-decoration: none !important;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 800;
}

.alliance-back-dashboard-link:hover {
    opacity: 0.92;
}

.alliance-back-dashboard-label {
    color: #475467;
    font-size: 12px;
    font-weight: 600;
}

@media (max-width: 640px) {
    .alliance-back-dashboard-label {
        display: none;
    }

    .alliance-back-dashboard-bar {
        padding: 8px 10px;
    }
}

</style>

<div
    id="alliance-back-dashboard"
    class="alliance-back-dashboard-bar"
>
    <a
        class="alliance-back-dashboard-link"
        href="/alliance/primary"
    >
        ← Back to Dashboard
    </a>

    <span class="alliance-back-dashboard-label">
        Alliance CRE · Team Command Centre
    </span>
</div>
"""


class BackToDashboardMiddleware:

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):

        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method") or "").upper()
        path = str(scope.get("path") or "")

        # Only touch GET HTML team pages.
        # POST/API/database actions remain completely unchanged.
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

            headers = list(
                response_start.get("headers", [])
            )

            content_type = ""

            for key, value in headers:
                if key.lower() == b"content-type":
                    content_type = value.decode(
                        "latin-1",
                        errors="ignore",
                    ).lower()
                    break

            original_body = b"".join(body_parts)

            # Never alter JSON, downloads, images, APIs etc.
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

            # Prevent duplicate insertion.
            if 'id="alliance-back-dashboard"' not in page:

                body_tag = re.search(
                    r"<body(?:\\s[^>]*)?>",
                    page,
                    flags=re.I,
                )

                if body_tag:
                    position = body_tag.end()

                    page = (
                        page[:position]
                        + BACK_BAR
                        + page[position:]
                    )
                else:
                    page = BACK_BAR + page

            new_body = page.encode("utf-8")

            # Recalculate Content-Length.
            new_headers = [
                (key, value)
                for key, value in headers
                if key.lower() != b"content-length"
            ]

            new_headers.append(
                (
                    b"content-length",
                    str(len(new_body)).encode("ascii"),
                )
            )

            new_start = dict(response_start)
            new_start["headers"] = new_headers

            await send(new_start)

            await send({
                "type": "http.response.body",
                "body": new_body,
                "more_body": False,
            })

        await self.app(
            scope,
            receive,
            capture_send,
        )


def register(core):

    app = _app(core)

    if app is None:
        raise RuntimeError(
            "Alliance 12.2.1 requires FastAPI app"
        )

    # Avoid double registration.
    if getattr(
        app.state,
        "alliance_back_to_dashboard_v1221",
        False,
    ):
        return {
            "status": "ALREADY_REGISTERED",
            "version": VERSION,
        }

    app.add_middleware(
        BackToDashboardMiddleware
    )

    app.state.alliance_back_to_dashboard_v1221 = True

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "dashboard": DASHBOARD_URL,
        "database_mutation": False,
        "matcher_changed": False,
    }