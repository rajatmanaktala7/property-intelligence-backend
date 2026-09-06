from __future__ import annotations

import asyncio
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse


VERSION = "3.4-SAFE-WHATSAPP-QUEUE"

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
# Main problem fixed here:
# - /whatsapp-live/api/ingest can be hit in bursts.
# - /api/team-dashboard-v376/freshness can be polled too aggressively.
# - Those requests must never consume every core request slot.
#
# Design:
# - health/diagnostics bypass the Alliance core completely
# - normal application requests use their own bounded gate
# - WhatsApp ingest uses a separate tiny gate + rate shield
# - freshness polling uses a separate tiny gate + rate shield
# - rejected flood traffic never enters the core/database
# - no data is deleted or mutated by the shield
# -----------------------------------------------------------------------------

MAX_CORE_REQUESTS = 8
CORE_GATE_WAIT_SECONDS = 1.5

INGEST_MAX_CONCURRENT = 2
INGEST_MIN_INTERVAL_SECONDS = 0.15
INGEST_GATE_WAIT_SECONDS = 0.05

FRESHNESS_MAX_CONCURRENT = 1
FRESHNESS_MIN_INTERVAL_SECONDS = 1.0
FRESHNESS_GATE_WAIT_SECONDS = 0.05

_CORE_GATE: asyncio.Semaphore | None = None
_INGEST_GATE: asyncio.Semaphore | None = None
_FRESHNESS_GATE: asyncio.Semaphore | None = None

_RATE_LOCK = threading.Lock()
_LAST_ALLOWED = {
    "ingest": 0.0,
    "freshness": 0.0,
}

RUNTIME = {
    "active_core_requests": 0,
    "peak_core_requests": 0,
    "rejected_core_requests": 0,
    "completed_core_requests": 0,
    "last_core_path": None,
    "last_core_started_at": None,
    "last_core_completed_at": None,

    "active_ingest_requests": 0,
    "accepted_ingest_requests": 0,
    "rejected_ingest_requests": 0,

    "active_freshness_requests": 0,
    "accepted_freshness_requests": 0,
    "rejected_freshness_requests": 0,
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_core_gate() -> asyncio.Semaphore:
    global _CORE_GATE
    if _CORE_GATE is None:
        _CORE_GATE = asyncio.Semaphore(MAX_CORE_REQUESTS)
    return _CORE_GATE


def _get_ingest_gate() -> asyncio.Semaphore:
    global _INGEST_GATE
    if _INGEST_GATE is None:
        _INGEST_GATE = asyncio.Semaphore(INGEST_MAX_CONCURRENT)
    return _INGEST_GATE


def _get_freshness_gate() -> asyncio.Semaphore:
    global _FRESHNESS_GATE
    if _FRESHNESS_GATE is None:
        _FRESHNESS_GATE = asyncio.Semaphore(FRESHNESS_MAX_CONCURRENT)
    return _FRESHNESS_GATE


def _rate_allowed(bucket: str, min_interval: float) -> bool:
    now = time.monotonic()
    with _RATE_LOCK:
        last = float(_LAST_ALLOWED.get(bucket, 0.0) or 0.0)
        if now - last < min_interval:
            return False
        _LAST_ALLOWED[bucket] = now
        return True


# -----------------------------------------------------------------------------
# HEALTH SHELL
# -----------------------------------------------------------------------------

health_app = FastAPI(
    title="Alliance Health Shell",
    version=VERSION,
)


@health_app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "service": "alliance-property-intelligence",
        "health_shell": True,
        "version": VERSION,
        "boot_state": BOOT["state"],
        "core_loaded": BOOT["core_loaded"],
        "active_core_requests": RUNTIME["active_core_requests"],
        "active_ingest_requests": RUNTIME["active_ingest_requests"],
        "active_freshness_requests": RUNTIME["active_freshness_requests"],
        "timestamp_utc": _utcnow(),
    }


@health_app.get("/readyz")
async def readyz():
    return JSONResponse(
        status_code=200 if BOOT["core_loaded"] else 503,
        content={
            "status": "ready" if BOOT["core_loaded"] else "starting",
            "version": VERSION,
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
        "version": VERSION,
        **BOOT,
        "runtime": dict(RUNTIME),
        "max_core_requests": MAX_CORE_REQUESTS,
        "timestamp_utc": _utcnow(),
    }


@health_app.get("/runtime-status")
async def runtime_status():
    return {
        "status": "OK",
        "version": VERSION,
        "boot_state": BOOT["state"],
        "core_loaded": BOOT["core_loaded"],
        "max_core_requests": MAX_CORE_REQUESTS,
        "core_gate_wait_seconds": CORE_GATE_WAIT_SECONDS,
        "ingest_max_concurrent": INGEST_MAX_CONCURRENT,
        "ingest_min_interval_seconds": INGEST_MIN_INTERVAL_SECONDS,
        "freshness_max_concurrent": FRESHNESS_MAX_CONCURRENT,
        "freshness_min_interval_seconds": FRESHNESS_MIN_INTERVAL_SECONDS,
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
<p><a href="/healthz">Health</a> ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â· <a href="/boot-status">Boot Status</a></p>
</main>
</body>
</html>""",
        status_code=503,
    )


@health_app.get("/whatsapp-queue-status")
async def whatsapp_queue_status():
    try:
        import alliance_whatsapp_safe_ingest_v5 as safe_wa
        return safe_wa.queue_status()
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status":"ERROR","error":f"{type(exc).__name__}: {exc}"},
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
        "/whatsapp-queue-status",
        "/whatsapp-live",
        "/whatsapp-live/feed",
        "/commercial-intelligence",
    ]
    present = {w: any(x["path"] == w for x in routes) for w in wanted}
    return {
        "status": "OK",
        "version": VERSION,
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
        "version": VERSION,
        "late_registration": LATE_REGISTRATION,
        "boot_state": BOOT.get("state"),
        "core_loaded": BOOT.get("core_loaded"),
        "v383_registered": "/api/v383/status" in routes,
        "v46_registered": "/api/v46/status" in routes,
        "v451_registered": "/api/v451/live/status" in routes,
        "v451_properties_registered": "/api/v451/live/properties" in routes,
        "whatsapp_live_registered": "/whatsapp-live" in routes,
        "whatsapp_feed_registered": "/whatsapp-live/feed" in routes,
        "commercial_intelligence_registered": "/commercial-intelligence" in routes,
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

        # 7.3.7 Historical evidence repair is fail-safe and dry-run by default.
        try:
            import alliance_historical_repair_v737 as historical_repair_v737
            repair_result = historical_repair_v737.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["historical_repair_v737"] = repair_result
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["historical_repair_v737"] = {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "fail_safe": True,
            }
            print("[historical-repair-v737] warning:", type(exc).__name__, str(exc))

        # 7.3.8 historical source recovery is audit-only and fail-safe.
        try:
            import alliance_source_recovery_v738 as source_recovery_v738
            recovery_result = source_recovery_v738.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["source_recovery_v738"] = recovery_result
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["source_recovery_v738"] = {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "fail_safe": True,
            }
            print("[source-recovery-v738] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_BUSINESS_OS_V800
        try:
            import alliance_business_os_v800 as alliance_business_os_v800
            business_v800_result = alliance_business_os_v800.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["business_os_v800"] = business_v800_result
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["business_os_v800"] = {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "fail_safe": True,
            }
            print("[business-os-v800] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_CRE_OS_V820
        try:
            import alliance_cre_os_v820 as alliance_cre_os_v820
            business_v820_result = alliance_cre_os_v820.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["business_os_v820"] = business_v820_result
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["business_os_v820"] = {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "fail_safe": True,
            }
            print("[business-os-v820] warning:", type(exc).__name__, str(exc))

        import alliance_live_feed_purity as live_feed_purity
        live_feed_purity.register(wrapped)

        try:
            late = _late_register_intelligence(wrapped)
            stabilization = dict(stabilization or {})
            stabilization["late_registration_check"] = late
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["late_registration_check"] = {
                "error": f"{type(exc).__name__}: {exc}"
            }

        try:
            import alliance_optional_property_modules as optional_property_modules
            optional_result = optional_property_modules.register(wrapped)
            stabilization = dict(stabilization or {})
            stabilization["optional_property_modules"] = optional_result
            print("[optional-property-modules]", optional_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["optional_property_modules"] = {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "fail_safe": True,
            }
            print(
                "[optional-property-modules] warning:",
                type(exc).__name__,
                str(exc),
            )

        # ALLIANCE_CRE_OS_V820_FINAL_ROUTE
        try:
            import alliance_cre_os_v820 as alliance_cre_os_v820
            business_v820_final = alliance_cre_os_v820.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["business_os_v820_final"] = business_v820_final
            print("[business-os-v820-final]", business_v820_final)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["business_os_v820_final"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[business-os-v820-final] warning:", type(exc).__name__, str(exc))
        # ALLIANCE_MAGAZINE_FRESH_V822_FINAL_ROUTE
        try:
            import alliance_magazine_fresh_v822 as magazine_fresh_v822
            magazine_fresh_result = magazine_fresh_v822.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["magazine_fresh_v822"] = magazine_fresh_result
            print("[magazine-fresh-v822]", magazine_fresh_result)
            # CRE OS 8.2.7.1: authoritative Magazine page route takeover.
            magazine_routes = [
                r for r in list(wrapped.app.router.routes)
                if getattr(r, "path", None) == "/magazine-master-import"
                and "GET" in set(getattr(r, "methods", set()) or set())
                and getattr(getattr(r, "endpoint", None), "__module__", "") == "alliance_magazine_fresh_v822"
            ]
            if not magazine_routes:
                raise RuntimeError("8.2.7 Magazine GET route was not registered")
            chosen = magazine_routes[-1]
            wrapped.app.router.routes.remove(chosen)
            wrapped.app.router.routes.insert(0, chosen)
            stabilization["magazine_route_takeover_v8271"] = {
                "status": "AUTHORITATIVE",
                "path": "/magazine-master-import",
                "module": "alliance_magazine_fresh_v822",
            }
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["magazine_fresh_v822"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[magazine-fresh-v822] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_MAGAZINE_FASTLANE_V840
        try:
            import alliance_magazine_fastlane_v840 as magazine_fastlane_v840
            fastlane_result = magazine_fastlane_v840.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["magazine_fastlane_v840"] = fastlane_result
            print("[magazine-fastlane-v840]", fastlane_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["magazine_fastlane_v840"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[magazine-fastlane-v840] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_MAGAZINE_ORGANIZER_V850
        try:
            import alliance_magazine_organizer_v850 as magazine_organizer_v850
            organizer_result = magazine_organizer_v850.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["magazine_organizer_v850"] = organizer_result
            print("[magazine-organizer-v850]", organizer_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["magazine_organizer_v850"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[magazine-organizer-v850] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_MAGAZINE_COMPLETE_V860
        try:
            import alliance_magazine_complete_v860 as magazine_complete_v860
            complete_result = magazine_complete_v860.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["magazine_complete_v860"] = complete_result

            complete_routes = [
                r for r in list(wrapped.app.router.routes)
                if getattr(r, "path", None) == "/magazine-organizer"
                and "GET" in set(getattr(r, "methods", set()) or set())
                and getattr(getattr(r, "endpoint", None), "__module__", "") == "alliance_magazine_complete_v860"
            ]
            if not complete_routes:
                raise RuntimeError("8.6 complete Magazine GET route was not registered")
            chosen = complete_routes[-1]
            wrapped.app.router.routes.remove(chosen)
            wrapped.app.router.routes.insert(0, chosen)
            stabilization["magazine_complete_v860_takeover"] = {
                "status":"AUTHORITATIVE",
                "path":"/magazine-organizer",
                "alias":"/magazine-complete",
                "module":"alliance_magazine_complete_v860",
            }
            print("[magazine-complete-v860]", complete_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["magazine_complete_v860"] = {
                "status":"ERROR",
                "error":f"{type(exc).__name__}: {exc}",
                "fail_safe":True
            }
            print("[magazine-complete-v860] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_FINAL_DATABASE_GRID_V870
        try:
            import alliance_final_database_grid_v870 as alliance_final_database_grid_v870
            grid_result = alliance_final_database_grid_v870.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["final_database_grid_v870"] = grid_result
            print("[final-database-grid-v870]", grid_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["final_database_grid_v870"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[final-database-grid-v870] warning:", type(exc).__name__, str(exc))


        # ALLIANCE_FINAL_5X5_DATABASES_V910
        try:
            import alliance_final_5x5_databases_v910 as final_5x5_v910
            final_5x5_result = final_5x5_v910.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["final_5x5_databases_v910"] = final_5x5_result

            from fastapi.responses import RedirectResponse
            route_specs = [
                ("/alliance/primary/databases","/alliance/final/databases"),
                ("/alliance/primary/database/{source}","/alliance/final/database/{source}"),
                ("/alliance/primary/properties","/alliance/final/database/master"),
                ("/alliance/primary/requirements-hub","/alliance/final/requirements"),
                ("/alliance/primary/requirements/source/{source}","/alliance/final/requirements/{source}"),
                ("/alliance/primary/requirements","/alliance/final/requirements/master"),
            ]
            for old_path,target in route_specs:
                keep=[]
                for r in list(wrapped.app.router.routes):
                    if getattr(r,"path",None)==old_path and "GET" in set(getattr(r,"methods",set()) or set()):
                        continue
                    keep.append(r)
                wrapped.app.router.routes[:]=keep
                if "{source}" in old_path:
                    async def _redir_source(request: Request, source:str, _target=target):
                        qs=request.url.query
                        url=_target.replace("{source}",source)
                        return RedirectResponse(url+("?" + qs if qs else ""),status_code=307)
                    wrapped.app.add_api_route(old_path,_redir_source,methods=["GET"],include_in_schema=False)
                else:
                    async def _redir(request: Request, _target=target):
                        qs=request.url.query
                        return RedirectResponse(_target+("?" + qs if qs else ""),status_code=307)
                    wrapped.app.add_api_route(old_path,_redir,methods=["GET"],include_in_schema=False)
            print("[final-5x5-v910]", final_5x5_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["final_5x5_databases_v910"]={"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[final-5x5-v910] warning:",type(exc).__name__,str(exc))


        # ALLIANCE_FINAL_WORKFLOW_V920
        try:
            import alliance_final_workflow_v920 as final_workflow_v920
            workflow_result = final_workflow_v920.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["final_workflow_v920"] = workflow_result
            print("[final-workflow-v920]", workflow_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["final_workflow_v920"]={"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[final-workflow-v920] warning:",type(exc).__name__,str(exc))

        # ALLIANCE_ORGANIZED_MAIN_V930
        try:
            import alliance_organized_main_v930 as organized_main_v930
            r930=organized_main_v930.register(wrapped.core)
            stabilization=dict(stabilization or {})
            stabilization["organized_main_v930"]=r930
            print("[organized-main-v930]",r930)
        except Exception as exc:
            stabilization=dict(stabilization or {})
            stabilization["organized_main_v930"]={"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[organized-main-v930] warning:",type(exc).__name__,str(exc))

        # ALLIANCE_ROUTE_RESCUE_V932
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

        # ALLIANCE_CRE_OS_V1180_FINAL_UI_AUTHORITY
        try:
            import alliance_cre_os_v1180 as cre_v1180
            cre1180_result = cre_v1180.register(wrapped)
            stabilization = dict(stabilization or {})
            stabilization["cre_v1180_final_ui"] = cre1180_result
            print("[cre-v1180-final-ui]", cre1180_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["cre_v1180_final_ui"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[cre-v1180-final-ui] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_CRE_11_9_1_GENUINE_REQUIREMENT_GATE
        try:
            import alliance_requirement_gate_v1191 as requirement_gate_v1191
            req_gate_result=requirement_gate_v1191.register(wrapped.core)
            stabilization=dict(stabilization or {})
            stabilization["requirement_gate_v1191"]=req_gate_result
            print("[requirement-gate-v1191]",req_gate_result)
        except Exception as exc:
            stabilization=dict(stabilization or {})
            stabilization["requirement_gate_v1191"]={"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[requirement-gate-v1191] warning:",type(exc).__name__,str(exc))

        # 11.9.11 SIMPLE REQUIREMENT MATCHER + MAGAZINE HIERARCHY
        try:
            import alliance_simple_match_magazine_hierarchy_v11911 as simple11911
            simple11911_result = simple11911.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["simple_match_magazine_hierarchy_v11911"] = simple11911_result
            print("[simple-match-magazine-hierarchy-v11911]", simple11911_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["simple_match_magazine_hierarchy_v11911"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[simple-match-magazine-hierarchy-v11911] warning:", type(exc).__name__, str(exc))

        # DISABLED BY 12.0.3: alliance_legacy_magazine_hierarchy_v11913
        # Legacy magazine mutator disabled to enforce a single reconciliation writer.
        # 11.9.15 READ-ONLY MAGAZINE EVIDENCE ADMIN
        try:
            import alliance_magazine_evidence_admin_v11915 as mag_ev11915
            mag_ev11915_result = mag_ev11915.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["magazine_evidence_admin_v11915"] = mag_ev11915_result
            print("[magazine-evidence-admin-v11915]", mag_ev11915_result)
        except Exception as exc:
            print("[magazine-evidence-admin-v11915] warning:", type(exc).__name__, str(exc))

        # DISABLED BY 12.0.3: alliance_magazine_hierarchy_repair_v11916
        # Legacy magazine mutator disabled to enforce a single reconciliation writer.
        # DISABLED BY 12.0.3: alliance_magazine_block_hierarchy_v11917
        # Legacy magazine mutator disabled to enforce a single reconciliation writer.
        # DISABLED BY 12.0.3: alliance_magazine_final_block_fill_v11918
        # Legacy magazine mutator disabled to enforce a single reconciliation writer.
        # DISABLED BY 12.0.3: alliance_magazine_ai_doctor_v11920
        # Legacy magazine mutator disabled to enforce a single reconciliation writer.
        # DISABLED BY 12.0.3: alliance_magazine_layout_rebuild_v11921
        # Legacy magazine mutator disabled to enforce a single reconciliation writer.
        # DISABLED BY 12.0.3: alliance_data_settlement_v11922
        # Legacy magazine mutator disabled to enforce a single reconciliation writer.
        # DISABLED BY 12.0.3: alliance_golden_data_foundation_v12000
        # Legacy magazine mutator disabled to enforce a single reconciliation writer.
        # DISABLED BY 12.0.3: alliance_golden_data_progress_v12001
        # Legacy magazine mutator disabled to enforce a single reconciliation writer.
        # DISABLED BY 12.0.3: alliance_golden_data_streaming_v12002
        # Legacy magazine mutator disabled to enforce a single reconciliation writer.
        # 12.0.3 SINGLE-WRITER GOLDEN DATA FOUNDATION
        try:
            import alliance_golden_data_single_writer_v12003 as gold12003
            gold12003_result = gold12003.register(wrapped.core)
            print("[golden-data-v12003]", gold12003_result)
        except Exception as exc:
            print("[golden-data-v12003] warning:", type(exc).__name__, str(exc))

        # 12.0.4 CERTIFICATION + DEDUPLICATION + HUMAN REVIEW WORKBENCH
        try:
            import alliance_magazine_certification_v12004 as cert12004
            cert12004_result = cert12004.register(wrapped.core)
            print("[magazine-certification-v12004]", cert12004_result)
        except Exception as exc:
            print("[magazine-certification-v12004] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_MAGAZINE_CERTIFICATION_HARDENING_V12005
        try:
            import alliance_magazine_certification_v12005 as magazine_certification_v12005
            hardening_result = magazine_certification_v12005.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["magazine_certification_v12005"] = hardening_result
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["magazine_certification_v12005"] = {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "fail_safe": True,
            }
            print("[magazine-certification-v12005] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_MAGAZINE_CERTIFICATION_ASSISTANT_V12006
        try:
            import alliance_magazine_certification_assistant_v12006 as certassist12006
            certassist12006_result = certassist12006.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["magazine_certification_assistant_v12006"] = certassist12006_result
            print("[magazine-certification-assistant-v12006]", certassist12006_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["magazine_certification_assistant_v12006"] = {
                "status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True
            }
            print("[magazine-certification-assistant-v12006] warning:", type(exc).__name__, str(exc))

        CORE_APP = wrapped.app
        try:
            import alliance_whatsapp_safe_ingest_v5 as safe_wa
            safe_wa.start_worker()
            stabilization = dict(stabilization or {})
            stabilization["whatsapp_safe_queue"] = safe_wa.queue_status()
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["whatsapp_safe_queue"] = {
                "status":"ERROR",
                "error":f"{type(exc).__name__}: {exc}",
            }
            print("[whatsapp-safe-queue] warning:", type(exc).__name__, str(exc))
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

    Health paths bypass the core.
    Normal routes, ingest traffic and freshness polling are isolated from one
    another so one noisy subsystem cannot starve the entire application.
    """

    HEALTH_PATHS = {
        "/healthz",
        "/readyz",
        "/boot-status",
        "/runtime-status",
        "/core-route-status",
        "/api/live-bootstrap-status",
    }

    INGEST_PATHS = {
        "/whatsapp-live/api/ingest",
    }

    FRESHNESS_PATHS = {
        "/api/team-dashboard-v376/freshness",
    }

    async def _send_busy(self, scope, receive, send, message, retry_after="3"):
        response = PlainTextResponse(
            message,
            status_code=429,
            headers={
                "Retry-After": retry_after,
                "Cache-Control": "no-store",
            },
        )
        await response(scope, receive, send)

    async def _serve_core(self, scope: dict[str, Any], receive, send):
        if CORE_APP is None or not BOOT["core_loaded"]:
            await health_app(scope, receive, send)
            return

        gate = _get_core_gate()
        acquired = False
        path = scope.get("path", "")

        try:
            try:
                await asyncio.wait_for(
                    gate.acquire(),
                    timeout=CORE_GATE_WAIT_SECONDS,
                )
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
                    0,
                    RUNTIME["active_core_requests"] - 1,
                )
                RUNTIME["completed_core_requests"] += 1
                RUNTIME["last_core_completed_at"] = _utcnow()
                gate.release()

    async def _serve_ingest(self, scope: dict[str, Any], receive, send):
        # SAFE QUEUE MODE: acknowledge quickly and process outside Uvicorn's event loop.
        try:
            import alliance_whatsapp_safe_ingest_v5 as safe_wa
            RUNTIME["active_ingest_requests"] += 1
            RUNTIME["accepted_ingest_requests"] += 1
            await safe_wa.handle_ingest(scope, receive, send)
        except Exception as exc:
            RUNTIME["rejected_ingest_requests"] += 1
            response = JSONResponse(
                status_code=503,
                content={
                    "status":"ERROR",
                    "message":"Safe WhatsApp ingest receiver failed",
                    "detail":f"{type(exc).__name__}: {exc}",
                },
                headers={"Retry-After":"5","Cache-Control":"no-store"},
            )
            await response(scope, receive, send)
        finally:
            RUNTIME["active_ingest_requests"] = max(
                0, RUNTIME["active_ingest_requests"] - 1
            )

    async def _serve_freshness(self, scope: dict[str, Any], receive, send):
        # EMERGENCY STABILITY MODE:
        # Dashboard freshness polling is non-essential. Keep it outside the core
        # until the blocking middleware/request path is refactored.
        RUNTIME["rejected_freshness_requests"] += 1
        response = JSONResponse(
            status_code=503,
            content={
                "status": "PAUSED",
                "reason": "production_stability",
                "retry_after_seconds": 30,
            },
            headers={
                "Retry-After": "30",
                "Cache-Control": "no-store",
            },
        )
        await response(scope, receive, send)

    async def __call__(self, scope: dict[str, Any], receive, send):
        scope_type = scope.get("type")

        if scope_type not in {"http", "websocket", "lifespan"}:
            await health_app(scope, receive, send)
            return

        if scope_type == "lifespan":
            await health_app(scope, receive, send)
            return

        path = scope.get("path", "")

        if path in self.HEALTH_PATHS:
            await health_app(scope, receive, send)
            return

        if scope_type == "websocket":
            if BOOT["core_loaded"] and CORE_APP is not None:
                await CORE_APP(scope, receive, send)
            else:
                await health_app(scope, receive, send)
            return

        if path in self.INGEST_PATHS:
            await self._serve_ingest(scope, receive, send)
            return

        if path in self.FRESHNESS_PATHS:
            await self._serve_freshness(scope, receive, send)
            return

        await self._serve_core(scope, receive, send)


app = HealthFirstDispatcher()


# 7.3.7 HISTORICAL EVIDENCE REPAIR REGISTRATION




