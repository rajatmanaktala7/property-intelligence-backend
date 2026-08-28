from __future__ import annotations

import asyncio
import threading
import traceback
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse


BOOT = {
    "state": "STARTING",
    "core_loaded": False,
    "error": None,
    "trace": None,
    "started_at": datetime.now(timezone.utc).isoformat(),
    "completed_at": None,
    "stabilization": None,
}

CORE_APP = None

LATE_REGISTRATION = {
    "v383": {"status": "NOT_RUN", "error": None},
    "v46": {"status": "NOT_RUN", "error": None},
    "v451": {"status": "NOT_RUN", "error": None},
}

# -----------------------------------------------------------------------------
# PRODUCTION STABILITY GUARD
#
# Why this exists:
# - The Alliance application contains many synchronous/database-backed routes.
# - FastAPI executes sync routes in a threadpool.
# - If too many slow/stuck sync requests arrive together, the threadpool can be
#   exhausted and a sync /healthz route can also stop responding.
#
# Fix:
# - Health-shell handlers are fully async and bypass the core app.
# - Core request concurrency is bounded so browser tabs cannot flood the core.
# - If the core gate is saturated, new core requests get a fast 503 instead of
#   silently piling up and making the whole service appear dead.
# -----------------------------------------------------------------------------

MAX_CORE_REQUESTS = 12
CORE_GATE_WAIT_SECONDS = 2.5
_CORE_GATE: asyncio.Semaphore | None = None

RUNTIME = {
    "active_core_requests": 0,
    "peak_core_requests": 0,
    "rejected_core_requests": 0,
    "completed_core_requests": 0,
    "last_core_path": None,
    "last_core_started_at": None,
    "last_core_completed_at": None,
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_core_gate() -> asyncio.Semaphore:
    global _CORE_GATE
    if _CORE_GATE is None:
        _CORE_GATE = asyncio.Semaphore(MAX_CORE_REQUESTS)
    return _CORE_GATE


# -----------------------------------------------------------------------------
# HEALTH SHELL
# Created BEFORE importing the full Alliance app so Railway can always receive
# health/diagnostic responses independently of the main application.
# -----------------------------------------------------------------------------

health_app = FastAPI(
    title="Alliance Health Shell",
    version="3.1-HEALTH-FIRST-STABLE",
)


@health_app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "service": "alliance-property-intelligence",
        "health_shell": True,
        "boot_state": BOOT["state"],
        "core_loaded": BOOT["core_loaded"],
        "active_core_requests": RUNTIME["active_core_requests"],
        "timestamp_utc": _utcnow(),
    }


@health_app.get("/readyz")
async def readyz():
    return JSONResponse(
        status_code=200 if BOOT["core_loaded"] else 503,
        content={
            "status": "ready" if BOOT["core_loaded"] else "starting",
            "boot_state": BOOT["state"],
            "core_loaded": BOOT["core_loaded"],
            "error": BOOT["error"],
            "active_core_requests": RUNTIME["active_core_requests"],
        },
    )


@health_app.get("/boot-status")
async def boot_status():
    return {
        "service": "Alliance AI Deal Intelligence OS",
        **BOOT,
        "runtime": dict(RUNTIME),
        "max_core_requests": MAX_CORE_REQUESTS,
        "timestamp_utc": _utcnow(),
    }


@health_app.get("/runtime-status")
async def runtime_status():
    return {
        "status": "OK",
        "boot_state": BOOT["state"],
        "core_loaded": BOOT["core_loaded"],
        "max_core_requests": MAX_CORE_REQUESTS,
        "core_gate_wait_seconds": CORE_GATE_WAIT_SECONDS,
        **RUNTIME,
        "timestamp_utc": _utcnow(),
    }


@health_app.get("/", response_class=HTMLResponse)
async def shell_home():
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


@health_app.get("/core-route-status")
async def core_route_status():
    routes = []
    if CORE_APP is not None and hasattr(CORE_APP, "router"):
        for r in CORE_APP.router.routes:
            p = getattr(r, "path", None)
            methods = sorted(list(getattr(r, "methods", set()) or set()))
            if p:
                routes.append({"path": p, "methods": methods})

    wanted = [
        "/api/v383/status",
        "/api/v46/status",
        "/api/v451/live/status",
        "/api/v451/live/properties",
        "/api/live-bootstrap-status",
        "/whatsapp-live",
        "/whatsapp-live/feed",
    ]
    present = {w: any(x["path"] == w for x in routes) for w in wanted}
    return {
        "status": "OK",
        "boot_state": BOOT.get("state"),
        "core_loaded": BOOT.get("core_loaded"),
        "core_app_present": CORE_APP is not None,
        "late_registration": LATE_REGISTRATION,
        "wanted_routes_present": present,
        "route_count": len(routes),
        "core_app_id": id(CORE_APP) if CORE_APP is not None else None,
        "stabilization": BOOT.get("stabilization"),
        "runtime": dict(RUNTIME),
        "routes": routes,
    }


@health_app.get("/api/live-bootstrap-status")
async def shell_live_bootstrap_status():
    routes = []
    if CORE_APP is not None and hasattr(CORE_APP, "router"):
        routes = [
            getattr(r, "path", None)
            for r in CORE_APP.router.routes
            if getattr(r, "path", None)
        ]
    return {
        "status": "OK" if BOOT.get("core_loaded") else "STARTING",
        "served_by": "production_entrypoint health shell",
        "late_registration": LATE_REGISTRATION,
        "boot_state": BOOT.get("state"),
        "core_loaded": BOOT.get("core_loaded"),
        "v383_registered": "/api/v383/status" in routes,
        "v46_registered": "/api/v46/status" in routes,
        "v451_registered": "/api/v451/live/status" in routes,
        "v451_properties_registered": "/api/v451/live/properties" in routes,
        "whatsapp_live_registered": "/whatsapp-live" in routes,
        "whatsapp_feed_registered": "/whatsapp-live/feed" in routes,
        "core_bootstrap_route_registered": routes.count("/api/live-bootstrap-status") > 0,
        "route_count": len(routes),
        "runtime": dict(RUNTIME),
    }


def _route_exists(app_obj, path):
    try:
        return any(getattr(r, "path", None) == path for r in app_obj.router.routes)
    except Exception:
        return False


def _late_register_intelligence(wrapped):
    core = wrapped.core
    authoritative_app = wrapped.app

    def route_exists(path):
        return any(
            getattr(r, "path", None) == path
            for r in authoritative_app.router.routes
        )

    results = {}

    try:
        if route_exists("/api/v383/status"):
            results["v383"] = {"status": "ALREADY_REGISTERED", "error": None}
        else:
            import alliance_v383_database_foundation as v383

            v383.register(core)
            results["v383"] = {
                "status": "REGISTERED" if route_exists("/api/v383/status") else "NO_ROUTE",
                "error": None,
            }
    except Exception as exc:
        results["v383"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
        }

    try:
        if route_exists("/api/v46/status"):
            results["v46"] = {"status": "ALREADY_REGISTERED", "error": None}
        else:
            import alliance_v46_unified_intelligence as v46

            v46.register(core)
            results["v46"] = {
                "status": "REGISTERED" if route_exists("/api/v46/status") else "NO_ROUTE",
                "error": None,
            }
    except Exception as exc:
        results["v46"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
        }

    try:
        if route_exists("/api/v451/live/status"):
            results["v451"] = {"status": "ALREADY_REGISTERED", "error": None}
        else:
            import alliance_v45_live_whatsapp_takeover as v451

            v451.register(core)
            results["v451"] = {
                "status": "REGISTERED"
                if route_exists("/api/v451/live/status")
                else "NO_ROUTE",
                "error": None,
            }
    except Exception as exc:
        results["v451"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
        }

    LATE_REGISTRATION.clear()
    LATE_REGISTRATION.update(results)

    required = [
        "/api/v383/status",
        "/api/v46/status",
        "/api/v451/live/status",
        "/api/v451/live/properties",
        "/whatsapp-live",
        "/whatsapp-live/feed",
    ]
    missing = [p for p in required if not route_exists(p)]
    return {
        "results": dict(results),
        "missing_routes": missing,
        "route_count": len(authoritative_app.router.routes),
        "authoritative_app_id": id(authoritative_app),
        "core_app_id": id(core.app),
        "same_app_object": authoritative_app is core.app,
    }


def _load_core():
    global CORE_APP

    BOOT["state"] = "LOADING_CORE"

    try:
        import newspaper_wrapper as wrapped

        import alliance_production_surface as production_surface

        stabilization = production_surface.register(wrapped)

        import alliance_live_feed_purity as live_feed_purity

        live_feed_purity.register(wrapped)

        # Safe late-registration check. It does not duplicate routes; each module
        # is registered only when its expected route is genuinely absent.
        try:
            late = _late_register_intelligence(wrapped)
            stabilization = dict(stabilization or {})
            stabilization["late_registration_check"] = late
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["late_registration_check"] = {
                "error": f"{type(exc).__name__}: {exc}"
            }

        CORE_APP = wrapped.app
        BOOT["core_loaded"] = True
        BOOT["state"] = "READY" if stabilization.get("registered") else "DEGRADED"
        BOOT["stabilization"] = stabilization
        BOOT["completed_at"] = _utcnow()
        print("[health-first] Alliance core application loaded successfully")

    except Exception as exc:
        BOOT["core_loaded"] = False
        BOOT["state"] = "FAILED"
        BOOT["error"] = f"{type(exc).__name__}: {exc}"
        BOOT["trace"] = traceback.format_exc(limit=30)
        BOOT["completed_at"] = _utcnow()

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
    Stable ASGI dispatcher.

    Health/diagnostic endpoints always bypass the full Alliance application.
    Core HTTP requests are concurrency-bounded to prevent multiple browser tabs
    or slow DB routes from exhausting the sync worker pool.
    """

    HEALTH_PATHS = {
        "/healthz",
        "/readyz",
        "/boot-status",
        "/runtime-status",
        "/core-route-status",
        "/api/live-bootstrap-status",
    }

    async def _serve_core(self, scope: dict[str, Any], receive, send):
        if CORE_APP is None or not BOOT["core_loaded"]:
            await health_app(scope, receive, send)
            return

        gate = _get_core_gate()
        acquired = False
        path = scope.get("path", "")

        try:
            try:
                await asyncio.wait_for(gate.acquire(), timeout=CORE_GATE_WAIT_SECONDS)
                acquired = True
            except asyncio.TimeoutError:
                RUNTIME["rejected_core_requests"] += 1
                response = PlainTextResponse(
                    "Alliance is busy processing earlier requests. Please retry.",
                    status_code=503,
                    headers={"Retry-After": "3"},
                )
                await response(scope, receive, send)
                return

            RUNTIME["active_core_requests"] += 1
            RUNTIME["peak_core_requests"] = max(
                RUNTIME["peak_core_requests"],
                RUNTIME["active_core_requests"],
            )
            RUNTIME["last_core_path"] = path
            RUNTIME["last_core_started_at"] = _utcnow()

            await CORE_APP(scope, receive, send)

        finally:
            if acquired:
                RUNTIME["active_core_requests"] = max(
                    0, RUNTIME["active_core_requests"] - 1
                )
                RUNTIME["completed_core_requests"] += 1
                RUNTIME["last_core_completed_at"] = _utcnow()
                gate.release()

    async def __call__(self, scope: dict[str, Any], receive, send):
        scope_type = scope.get("type")

        if scope_type not in {"http", "websocket", "lifespan"}:
            await health_app(scope, receive, send)
            return

        if scope_type == "lifespan":
            # Uvicorn lifecycle belongs to the tiny health shell.
            await health_app(scope, receive, send)
            return

        path = scope.get("path", "")

        if path in self.HEALTH_PATHS:
            await health_app(scope, receive, send)
            return

        if scope_type == "websocket":
            # Do not semaphore-gate long-lived websocket sessions.
            if BOOT["core_loaded"] and CORE_APP is not None:
                await CORE_APP(scope, receive, send)
            else:
                await health_app(scope, receive, send)
            return

        await self._serve_core(scope, receive, send)


app = HealthFirstDispatcher()
