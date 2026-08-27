
from __future__ import annotations

import threading
import traceback
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse


BOOT = {
    "state": "STARTING",
    "core_loaded": False,
    "error": None,
    "trace": None,
    "started_at": datetime.now(timezone.utc).isoformat(),
    "completed_at": None,
}

CORE_APP = None


# ------------------------------------------------------------
# HEALTH SHELL
# This app is created BEFORE importing the full Alliance app.
# Railway can therefore receive /healthz immediately.
# ------------------------------------------------------------

health_app = FastAPI(
    title="Alliance Health Shell",
    version="3.0-HEALTH-FIRST",
)


@health_app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "service": "alliance-property-intelligence",
        "health_shell": True,
        "boot_state": BOOT["state"],
        "core_loaded": BOOT["core_loaded"],
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


@health_app.get("/readyz")
def readyz():
    return JSONResponse(
        status_code=200 if BOOT["core_loaded"] else 503,
        content={
            "status": "ready" if BOOT["core_loaded"] else "starting",
            "boot_state": BOOT["state"],
            "core_loaded": BOOT["core_loaded"],
            "error": BOOT["error"],
        },
    )


@health_app.get("/boot-status")
def boot_status():
    return {
        "service": "Alliance AI Deal Intelligence OS",
        **BOOT,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


@health_app.get("/", response_class=HTMLResponse)
def shell_home():
    if BOOT["core_loaded"]:
        return HTMLResponse(
            "<html><body><h3>Alliance core loaded.</h3>"
            "<p>Refresh this page.</p></body></html>",
            status_code=503,
        )

    err = (BOOT["error"] or "Core application is still loading.").replace(
        "<", "&lt;"
    ).replace(">", "&gt;")

    return HTMLResponse(
        f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alliance Starting</title>
<style>
body{{font-family:Arial;background:#efe4d2;color:#2d261f;margin:0}}
main{{max-width:850px;margin:60px auto;background:white;padding:28px;border-radius:14px}}
code{{background:#f5eee5;padding:4px 6px;border-radius:5px}}
</style>
</head>
<body>
<main>
<h1>Alliance is starting</h1>
<p>The health service is online while the main application loads independently.</p>
<p><b>Boot state:</b> <code>{BOOT["state"]}</code></p>
<p><b>Detail:</b> <code>{err}</code></p>
<p><a href="/healthz">Health</a> · <a href="/boot-status">Boot Status</a></p>
</main>
</body>
</html>""",
        status_code=503,
    )


def _load_core():
    global CORE_APP

    BOOT["state"] = "LOADING_CORE"

    try:
        import newspaper_wrapper as wrapped

        CORE_APP = wrapped.app
        BOOT["core_loaded"] = True
        BOOT["state"] = "READY"
        BOOT["completed_at"] = datetime.now(timezone.utc).isoformat()
        print("[health-first] Alliance core application loaded successfully")

    except Exception as exc:
        BOOT["core_loaded"] = False
        BOOT["state"] = "FAILED"
        BOOT["error"] = f"{type(exc).__name__}: {exc}"
        BOOT["trace"] = traceback.format_exc(limit=30)
        BOOT["completed_at"] = datetime.now(timezone.utc).isoformat()

        print(
            "[health-first] Alliance core failed:",
            type(exc).__name__,
            str(exc),
        )


_loader = threading.Thread(
    target=_load_core,
    name="alliance-core-loader",
    daemon=True,
)
_loader.start()


class HealthFirstDispatcher:
    """
    ASGI dispatcher.

    /healthz, /readyz and /boot-status always go to the tiny health shell.
    Every other route goes to the Alliance core once it is ready.
    Until then, requests get a controlled 503 rather than a Railway 502.
    """

    HEALTH_PATHS = {
        "/healthz",
        "/readyz",
        "/boot-status",
    }

    async def __call__(self, scope: dict[str, Any], receive, send):
        if scope["type"] not in {"http", "websocket", "lifespan"}:
            await health_app(scope, receive, send)
            return

        if scope["type"] == "lifespan":
            # Uvicorn lifecycle belongs to the shell.
            # The existing Alliance core remains a request application.
            await health_app(scope, receive, send)
            return

        path = scope.get("path", "")

        if path in self.HEALTH_PATHS:
            await health_app(scope, receive, send)
            return

        if BOOT["core_loaded"] and CORE_APP is not None:
            await CORE_APP(scope, receive, send)
            return

        # Core is not ready. Return controlled shell response.
        await health_app(scope, receive, send)


app = HealthFirstDispatcher()
