from __future__ import annotations

import asyncio
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse


VERSION = "4.1-ISOLATED-DATABASE-MATCHER-AUTHORITY"

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
REQUIREMENT_APP = None
DATABASE_APP = None
MATCHER_APP = None

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

MAX_CORE_REQUESTS = 5
CORE_GATE_WAIT_SECONDS = 1.0

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
    # Health-shell invariant: this endpoint must never import the core DB engine,
    # acquire a DB connection, or wait on application work.
    return {
        "db_pool_status": {
            "status": "DEFERRED",
            "diagnostic": "/api/system/db-pool-status",
            "reason": "runtime-status is intentionally nonblocking",
        },
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
<p><a href="/healthz">Health</a> ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã¢â‚¬Â¦Ãƒâ€šÃ‚Â¡ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€¦Ã‚Â¡ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â· <a href="/boot-status">Boot Status</a></p>
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
                endpoint = getattr(r, "endpoint", None)
                routes.append({
                    "path": p,
                    "methods": methods,
                    "endpoint_name": getattr(endpoint, "__name__", None),
                    "endpoint_module": getattr(endpoint, "__module__", None),
                })

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

    # WhatsApp Live Clean Operational Bridge
    try:
        import alliance_whatsapp_live_clean_os as wa_clean_os
        wa_clean_result = wa_clean_os.register(core)
        results["wa_clean_os"] = {
            "status": "REGISTERED",
            "error": None,
            "result": wa_clean_result,
        }
    except Exception as exc:
        results["wa_clean_os"] = {
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
    global CORE_APP, REQUIREMENT_APP, DATABASE_APP, MATCHER_APP

    BOOT["state"] = "LOADING_CORE"

    try:
        # Publish the lightweight base app BEFORE importing newspaper_wrapper.
        # newspaper_wrapper performs substantial legacy registration at import
        # time; login/core routes must remain available while that work runs.
        import app as early_core
        CORE_APP = early_core.app

        # WhatsApp ingest is a critical production path. Start the safe queue
        # worker immediately, before heavy legacy/background registration.
        try:
            import alliance_whatsapp_safe_ingest_v5 as early_safe_wa
            early_safe_wa.start_worker()
            print("[early-whatsapp-safe-queue] READY")
        except Exception as exc:
            print("[early-whatsapp-safe-queue] warning:", type(exc).__name__, str(exc))

        # Publish supporting Team routes first, but keep its old dashboard
        # on /alliance/legacy/team-command-centre.
        try:
            import alliance_team_dashboard_v1220 as early_team_dashboard_v1220
            early_team_dashboard_v1220.register(early_core)
            print("[early-team-support-v1220] READY")
        except Exception as exc:
            print("[early-team-support-v1220] warning:", type(exc).__name__, str(exc))

        # Primary dashboard authority: latest clean canonical dashboard.
        try:
            import alliance_dashboard_authority_v1 as early_dashboard_authority_v1
            early_dashboard_authority_v1.register(early_core)
            print("[early-canonical-dashboard-v1] READY")
        except Exception as exc:
            print("[early-canonical-dashboard-v1] warning:", type(exc).__name__, str(exc))

        import newspaper_wrapper as wrapped
        CORE_APP = wrapped.app

        # ALLIANCE_EARLY_REQUIREMENT_AUTHORITY_V2
        # Requirement authority must become public before the broader production
        # surface registration begins. production_surface.register() can be a
        # long-running bootstrap and previously left REQUIREMENT_APP unset.
        try:
            import alliance_requirement_restore_v1235 as earliest_reqrestore_v1235
            earliest_requirement_app = FastAPI(
                title="Alliance Requirement Authority",
                docs_url=None,
                redoc_url=None,
                openapi_url=None,
            )
            earliest_reqrestore_result = earliest_reqrestore_v1235.register(
                wrapped.core,
                served_app=earliest_requirement_app,
            )
            REQUIREMENT_APP = earliest_requirement_app
            print("[earliest-requirement-authority-v2]", earliest_reqrestore_result)
        except Exception as exc:
            print("[earliest-requirement-authority-v2] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_EARLY_DATABASE_AND_MATCHER_AUTHORITIES_V1
        # Serve settled database/search and matcher workspaces from isolated
        # authorities so legacy route ordering cannot intercept them.
        try:
            import alliance_property_authority_v11 as property_authority_v11
            isolated_database_app = FastAPI(title="Alliance Database Authority", docs_url=None, redoc_url=None, openapi_url=None)
            property_authority_v11.register(wrapped.core, served_app=isolated_database_app)
            # Keep Manual Property on the same canonical database renderer as
            # Master/WhatsApp/Newspaper/Magazine. The canonical renderer already
            # reads Manual source data; no special table/UI override is needed.
            DATABASE_APP = isolated_database_app
            print("[early-database-authority-v1] READY + UNIFIED PROPERTY FORMAT")
        except Exception as exc:
            print("[early-database-authority-v1] warning:", type(exc).__name__, str(exc))

        try:
            import alliance_master_requirement_authority_v1 as matcher_authority_v1
            isolated_matcher_app = FastAPI(title="Alliance Matcher Authority", docs_url=None, redoc_url=None, openapi_url=None)
            matcher_authority_v1.register(wrapped.core, served_app=isolated_matcher_app)
            MATCHER_APP = isolated_matcher_app
            print("[early-matcher-authority-v1] READY")
        except Exception as exc:
            print("[early-matcher-authority-v1] warning:", type(exc).__name__, str(exc))

        # CRITICAL WORKSPACE READY CHECKPOINT
        # Dashboard + Requirements + Property DB + Matcher are the production
        # authorities. Legacy/optional registrars below may continue loading,
        # but they are no longer allowed to determine site availability.
        critical_ready = (
            CORE_APP is not None
            and REQUIREMENT_APP is not None
            and DATABASE_APP is not None
            and MATCHER_APP is not None
        )
        if critical_ready:
            BOOT["critical_ready"] = True
            BOOT["core_loaded"] = True
            BOOT["state"] = "READY"
            BOOT["stabilization"] = {
                "critical_workspace":"READY",
                "primary_dashboard_owner":"alliance_dashboard_authority_v1",
                "primary_dashboard_version":"2.3.0-CLEAN-SOURCE-NAVIGATION",
                "requirement_owner":"alliance_requirement_restore_v1235",
                "database_owner":"alliance_property_authority_v11",
                "matcher_owner":"alliance_master_requirement_authority_v1",
                "legacy_loading":"BACKGROUND_COMPATIBILITY",
            }
            BOOT["completed_at"] = _utcnow()
            print("[critical-workspace] READY: dashboard + requirements + database + matcher")

        import alliance_production_surface as production_surface
        stabilization = production_surface.register(wrapped)

        # ALLIANCE_EARLY_REQUIREMENT_AUTHORITY_V1
        # The Requirement workspace is operationally independent of the long
        # post-core registration chain. Create its isolated authority immediately
        # after wrapped.core exists so Railway health readiness cannot expose a
        # legacy Requirement page while the rest of Alliance is still booting.
        try:
            import alliance_requirement_restore_v1235 as early_reqrestore_v1235
            early_requirement_app = FastAPI(
                title="Alliance Requirement Authority",
                docs_url=None,
                redoc_url=None,
                openapi_url=None,
            )
            early_reqrestore_result = early_reqrestore_v1235.register(
                wrapped.core,
                served_app=early_requirement_app,
            )
            REQUIREMENT_APP = early_requirement_app
            stabilization = dict(stabilization or {})
            stabilization["early_requirement_authority_v1"] = {
                "status": "READY",
                "owner": "alliance_requirement_restore_v1235",
                "registration": early_reqrestore_result,
            }
            print("[early-requirement-authority-v1]", stabilization["early_requirement_authority_v1"])
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["early_requirement_authority_v1"] = {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "fail_safe": True,
            }
            print("[early-requirement-authority-v1] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_EXPLAINABLE_MATCHER_V1 - register only after wrapped.core exists
        try:
            import alliance_explainable_matcher_v1 as _alliance_explainable_matcher_v1
            _alliance_explainable_matcher_v1.register(wrapped.core)
            print("ALLIANCE_EXPLAINABLE_MATCHER_V1: REGISTERED_AFTER_CORE_LOAD")
        except Exception as _alliance_explainable_matcher_v1_error:
            print("ALLIANCE_EXPLAINABLE_MATCHER_V1 registration error:", type(_alliance_explainable_matcher_v1_error).__name__, _alliance_explainable_matcher_v1_error)

        # ALLIANCE_COMMERCIAL_INTELLIGENCE_AI_POST_CORE
        # Restore the existing Commercial Intelligence router after wrapped.core is live.
        try:
            import alliance_commercial_intelligence_ai as _alliance_commercial_ai
            _commercial_ai_result = _alliance_commercial_ai.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["commercial_intelligence_ai"] = _commercial_ai_result
            print("[commercial-intelligence-ai]", _commercial_ai_result)
        except Exception as _commercial_ai_exc:
            stabilization = dict(stabilization or {})
            stabilization["commercial_intelligence_ai"] = {
                "status":"ERROR",
                "error":f"{type(_commercial_ai_exc).__name__}: {_commercial_ai_exc}",
                "fail_safe":True,
            }
            print("[commercial-intelligence-ai] warning:", type(_commercial_ai_exc).__name__, str(_commercial_ai_exc))
        # ALLIANCE_HOSPITALITY_BOT_RUNTIME_V1
        # Patch only the worker function; preserve the existing public route.
        try:
            import alliance_hospitality_bot_runtime_v1 as _hospitality_runtime_v1
            _hospitality_runtime_result = _hospitality_runtime_v1.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["hospitality_bot_runtime_v1"] = _hospitality_runtime_result
            print("[hospitality-bot-runtime-v1]", _hospitality_runtime_result)
        except Exception as _hospitality_runtime_exc:
            stabilization = dict(stabilization or {})
            stabilization["hospitality_bot_runtime_v1"] = {
                "status": "ERROR",
                "error": f"{type(_hospitality_runtime_exc).__name__}: {_hospitality_runtime_exc}",
                "fail_safe": True,
            }
            print(
                "[hospitality-bot-runtime-v1] warning:",
                type(_hospitality_runtime_exc).__name__,
                str(_hospitality_runtime_exc),
            )

        # Deal Match Intent Guard V1 - additive only; frozen V6.6 remains unchanged.
        try:
            import alliance_deal_match_intent_guard_v1 as deal_intent_guard_v1
            deal_intent_result = deal_intent_guard_v1.install(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["deal_match_intent_guard_v1"] = deal_intent_result
            print("[deal-match-intent-guard-v1]", deal_intent_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["deal_match_intent_guard_v1"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[deal-match-intent-guard-v1] warning:", type(exc).__name__, str(exc))

        # 12.3.0 Alliance Team Operations
        try:
            import alliance_team_operations_v1230 as teamops_v1230
            teamops_result = teamops_v1230.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["team_operations_v1230"] = teamops_result
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["team_operations_v1230"] = {
                "status":"ERROR",
                "error":f"{type(exc).__name__}: {exc}",
                "fail_safe":True,
            }
            print("[team-operations-v1230] warning:", type(exc).__name__, str(exc))

        # Register universal Back-to-Dashboard layer if the module is present.
        try:
            stabilization = dict(stabilization or {})
            stabilization["back_to_dashboard_v1221"] = {"status":"DISABLED_BY_12_4_26"}
        except ModuleNotFoundError:
            print("[back-dashboard-v1221] module not present; continuing safely")
        except Exception as exc:
            print("[back-dashboard-v1221] warning:", type(exc).__name__, str(exc))

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

        if not BOOT.get("critical_ready"):
            import alliance_live_feed_purity as live_feed_purity
            live_feed_purity.register(wrapped)
        else:
            stabilization = dict(stabilization or {})
            stabilization["live_feed_purity"] = {
                "status":"DISABLED_LATE_MIDDLEWARE",
                "reason":"Middleware cannot be added after critical workspace starts serving."
            }

        # ALLIANCE_BABY_V6_REQUIREMENT_ANSWER_MACHINE
        try:
            import alliance_baby_requirement_answer_machine_v6 as baby_v6
            baby_v6_result = baby_v6.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["baby_v6_requirement_answer_machine"] = baby_v6_result
            print("[baby-v6-answer-machine]", baby_v6_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["baby_v6_requirement_answer_machine"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[baby-v6-answer-machine] warning:", type(exc).__name__, str(exc))

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

        # ALLIANCE_MAGAZINE_CERTIFICATION_ASSISTANT_V120061
        try:
            import alliance_magazine_certification_assistant_v120061 as certassist120061
            certassist120061_result = certassist120061.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["magazine_certification_assistant_v120061"] = certassist120061_result
            print("[magazine-certification-assistant-v120061]", certassist120061_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["magazine_certification_assistant_v120061"] = {
                "status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True
            }
            print("[magazine-certification-assistant-v120061] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_MAGAZINE_EVIDENCE_PROMOTION_GATE_V120062
        try:
            import alliance_magazine_evidence_promotion_gate_v120062 as promotion120062
            promotion120062_result = promotion120062.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["magazine_evidence_promotion_gate_v120062"] = promotion120062_result
            print("[magazine-evidence-promotion-gate-v120062]", promotion120062_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["magazine_evidence_promotion_gate_v120062"] = {
                "status":"ERROR",
                "error":f"{type(exc).__name__}: {exc}",
                "fail_safe":True
            }
            print("[magazine-evidence-promotion-gate-v120062] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_MAGAZINE_EVIDENCE_RECOVERY_V12007
        try:
            import alliance_magazine_evidence_recovery_v12007 as recovery12007
            recovery12007_result = recovery12007.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["magazine_evidence_recovery_v12007"] = recovery12007_result
            print("[magazine-evidence-recovery-v12007]", recovery12007_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["magazine_evidence_recovery_v12007"] = {
                "status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True
            }
            print("[magazine-evidence-recovery-v12007] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_MAGAZINE_RECTIFIER_V12008
        try:
            import alliance_magazine_rectifier_v12008 as rectifier12008
            rectifier12008_result = rectifier12008.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["magazine_rectifier_v12008"] = rectifier12008_result
            print("[magazine-rectifier-v12008]", rectifier12008_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["magazine_rectifier_v12008"] = {
                "status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True
            }
            print("[magazine-rectifier-v12008] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_MAGAZINE_SETTLEMENT_V12009
        try:
            import alliance_magazine_settlement_v12009 as settlement12009
            settlement12009_result = settlement12009.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["magazine_settlement_v12009"] = settlement12009_result
            print("[magazine-settlement-v12009]", settlement12009_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["magazine_settlement_v12009"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[magazine-settlement-v12009] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_PRODUCTION_GOLDEN_CUTOVER_V1210
        try:
            import alliance_production_golden_cutover_v1210 as cutover1210
            cutover1210_result = cutover1210.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["production_golden_cutover_v1210"] = cutover1210_result
            print("[production-golden-cutover-v1210]", cutover1210_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["production_golden_cutover_v1210"] = {
                "status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True
            }
            print("[production-golden-cutover-v1210] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_SMART_MATCHER_INTELLIGENCE_V1211
        try:
            import alliance_smart_matcher_v1211 as smartmatcher1211
            smartmatcher1211_result = smartmatcher1211.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["smart_matcher_v1211"] = smartmatcher1211_result
            print("[smart-matcher-v1211]", smartmatcher1211_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["smart_matcher_v1211"] = {
                "status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True
            }
            print("[smart-matcher-v1211] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_TEAM_OPERATING_DASHBOARD_V1220
        try:
            import alliance_team_dashboard_v1220 as teamdash1220
            teamdash1220_result = teamdash1220.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["team_dashboard_v1220"] = teamdash1220_result
            print("[team-dashboard-v1220]", teamdash1220_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["team_dashboard_v1220"] = {
                "status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True
            }
            print("[team-dashboard-v1220] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_REQUIREMENT_SOURCE_RESTORE_V1235
        try:
            import alliance_requirement_restore_v1235 as reqrestore_v1235
            reqrestore_result = reqrestore_v1235.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["requirement_restore_v1235"] = reqrestore_result
            print("[requirement-restore-v1235]", reqrestore_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["requirement_restore_v1235"] = {
                "status":"ERROR",
                "error":f"{type(exc).__name__}: {exc}",
                "fail_safe":True,
            }
            print("[requirement-restore-v1235] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_FINAL_DASHBOARD_V1241
        try:
            import alliance_final_dashboard_v1241 as finaldash_v1241
            stabilization = dict(stabilization or {})
            stabilization["final_dashboard_v1241"] = finaldash_v1241.register(wrapped.core)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["final_dashboard_v1241"] = {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "fail_safe": True,
            }
            print("[final-dashboard-v1241] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_OPERATIONS_HOTFIX_V1244
        try:
            import alliance_operations_hotfix_v1244 as operations_v1244
            stabilization = dict(stabilization or {})
            stabilization["operations_hotfix_v1244"] = operations_v1244.register(wrapped.core)
            print("[operations-hotfix-v1244]", stabilization["operations_hotfix_v1244"])
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["operations_hotfix_v1244"] = {
                "status":"ERROR",
                "error":f"{type(exc).__name__}: {exc}",
                "fail_safe":True,
            }
            print("[operations-hotfix-v1244] warning:",type(exc).__name__,str(exc))

        # ALLIANCE_ROOT_MATCHER_AUTHORITY_V12411
        try:
            import alliance_root_matcher_authority_v12411 as rootmatcher_v12411
            stabilization = dict(stabilization or {})
            stabilization["root_matcher_authority_v12411"] = rootmatcher_v12411.register(wrapped.core)
            print("[root-matcher-authority-v12411]", stabilization["root_matcher_authority_v12411"])
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["root_matcher_authority_v12411"] = {
                "status":"ERROR",
                "error":f"{type(exc).__name__}: {exc}",
                "fail_safe":True,
            }
            print("[root-matcher-authority-v12411] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_BOT_PAGES_AUTHORITY_V12414
        try:
            import alliance_bot_pages_authority_v12414 as botpages_v12414
            stabilization = dict(stabilization or {})
            stabilization["bot_pages_authority_v12414"] = botpages_v12414.register(wrapped.core)
            print("[bot-pages-authority-v12414]", stabilization["bot_pages_authority_v12414"])
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["bot_pages_authority_v12414"] = {
                "status":"ERROR",
                "error":f"{type(exc).__name__}: {exc}",
                "fail_safe":True,
            }
            print("[bot-pages-authority-v12414] warning:",type(exc).__name__,str(exc))

        # ALLIANCE_HOSPITALITY_DATA_RECOVERY_V12415
        try:
            import alliance_hospitality_data_recovery_v12415 as hospdata_v12415
            stabilization = dict(stabilization or {})
            stabilization["hospitality_data_recovery_v12415"] = hospdata_v12415.register(wrapped.core)
            print("[hospitality-data-recovery-v12415]", stabilization["hospitality_data_recovery_v12415"])
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["hospitality_data_recovery_v12415"] = {
                "status":"ERROR",
                "error":f"{type(exc).__name__}: {exc}",
                "fail_safe":True,
            }
            print("[hospitality-data-recovery-v12415] warning:",type(exc).__name__,str(exc))

        # ALLIANCE_HOSPITALITY_PURITY_V12416
        try:
            import alliance_hospitality_purity_v12416 as hosp_purity_v12416
            stabilization = dict(stabilization or {})
            stabilization["hospitality_purity_v12416"] = hosp_purity_v12416.register(wrapped.core)
            print("[hospitality-purity-v12416]", stabilization["hospitality_purity_v12416"])
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["hospitality_purity_v12416"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[hospitality-purity-v12416] warning:",type(exc).__name__,str(exc))
        # ALLIANCE_HOSPITALITY_PHONE_RECOVERY_V12417
        try:
            import alliance_hospitality_phone_recovery_v12417 as phone_v12417
            stabilization = dict(stabilization or {})
            stabilization["hospitality_phone_recovery_v12417"] = phone_v12417.register(wrapped.core)
            print("[hospitality-phone-v12417]", stabilization["hospitality_phone_recovery_v12417"])
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["hospitality_phone_recovery_v12417"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[hospitality-phone-v12417] warning:",type(exc).__name__,str(exc))

        # ALLIANCE_HOSPITALITY_CONTACT_BOT_V12422
        try:
            import alliance_hospitality_contact_bot_v12422 as hosp_contact_v12422
            stabilization = dict(stabilization or {})
            stabilization["hospitality_contact_bot_v12422"] = hosp_contact_v12422.register(wrapped.core)
            print("[hospitality-contact-bot-v12422]", stabilization["hospitality_contact_bot_v12422"])
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["hospitality_contact_bot_v12422"] = {
                "status":"ERROR",
                "error":f"{type(exc).__name__}: {exc}",
                "fail_safe":True,
            }
            print("[hospitality-contact-bot-v12422] warning:",type(exc).__name__,str(exc))

        # ALLIANCE_HOSPITALITY_CONTACT_BOT_V12423
        try:
            import alliance_hospitality_contact_bot_v12423 as hosp_contact_v12423
            stabilization = dict(stabilization or {})
            stabilization["hospitality_contact_bot_v12423"] = hosp_contact_v12423.register(wrapped.core)
            print("[hospitality-contact-bot-v12423]", stabilization["hospitality_contact_bot_v12423"])
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["hospitality_contact_bot_v12423"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[hospitality-contact-bot-v12423] warning:",type(exc).__name__,str(exc))

        # ALLIANCE_UNIVERSAL_NAV_FINAL_V12419A
        try:
            import alliance_back_to_dashboard_v1221 as back_v1221_final
            app_obj = wrapped.core.app
            try:
                if getattr(app_obj.state, 'alliance_back_to_dashboard_v1221', False):
                    app_obj.state.alliance_back_to_dashboard_v1221 = False
            except Exception:
                pass
            try:
                app_obj.user_middleware = [m for m in list(app_obj.user_middleware) if getattr(m, 'cls', None).__name__ != 'BackToDashboardMiddleware']
                app_obj.middleware_stack = None
            except Exception:
                pass
            nav_final_result = {'status':'DISABLED_BY_12_4_26'}
            stabilization = dict(stabilization or {})
            stabilization['universal_nav_final_v12419a'] = nav_final_result
            print('[universal-nav-final-v12419a]', nav_final_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization['universal_nav_final_v12419a'] = {'status':'ERROR','error':f'{type(exc).__name__}: {exc}','fail_safe':True}
            print('[universal-nav-final-v12419a] warning:', type(exc).__name__, str(exc))

        # ALLIANCE_HOSPITALITY_AUTO_V12425
        try:
            import alliance_hospitality_auto_v12425 as hosp_auto_v12425
            stabilization = dict(stabilization or {})
            stabilization["hospitality_auto_v12425"] = hosp_auto_v12425.register(wrapped.core)
            print("[hospitality-auto-v12425]", stabilization["hospitality_auto_v12425"])
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["hospitality_auto_v12425"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[hospitality-auto-v12425] warning:",type(exc).__name__,str(exc))

        # ALLIANCE_TEAM_WORKFLOW_REPAIR_V12424
        try:
            import alliance_team_workflow_repair_v12424 as workflow_v12424
            stabilization = dict(stabilization or {})
            stabilization["team_workflow_repair_v12424"] = workflow_v12424.register(wrapped.core)
            print("[team-workflow-repair-v12424]", stabilization["team_workflow_repair_v12424"])
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["team_workflow_repair_v12424"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[team-workflow-repair-v12424] warning:",type(exc).__name__,str(exc))

        # ALLIANCE_CANONICAL_LIFECYCLE_HARDENING_V12426A
        try:
            import alliance_operational_master_bridge_v12426 as bridge_v12426
            stabilization = dict(stabilization or {})
            stabilization["canonical_bridge_v12426a"] = bridge_v12426.register(wrapped.core)
            print("[canonical-bridge-v12426a]", stabilization["canonical_bridge_v12426a"])
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["canonical_bridge_v12426a"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[canonical-bridge-v12426a] warning:",type(exc).__name__,str(exc))

        # ALLIANCE_CANONICAL_DATABASE_HYGIENE_V1
        try:
            import alliance_canonical_database_hygiene_v1 as database_hygiene_v1
            stabilization = dict(stabilization or {})
            stabilization["canonical_database_hygiene_v1"] = database_hygiene_v1.register(wrapped.core)
            print("[canonical-database-hygiene-v1]", stabilization["canonical_database_hygiene_v1"])
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["canonical_database_hygiene_v1"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[canonical-database-hygiene-v1] warning:",type(exc).__name__,str(exc))

        # ALLIANCE_TEAM_READINESS_GUARD_V12426C
        try:
            import alliance_team_readiness_v12426c as readiness_v12426c
            stabilization = dict(stabilization or {})
            stabilization["team_readiness_v12426c"] = readiness_v12426c.register(wrapped.core)
            print("[team-readiness-v12426c]", stabilization["team_readiness_v12426c"])
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["team_readiness_v12426c"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[team-readiness-v12426c] warning:",type(exc).__name__,str(exc))

        # ALLIANCE_UNIVERSAL_LINK_AUTHORITY_V12427A
        try:
            import alliance_link_authority_v12427 as link_v12427
            stabilization = dict(stabilization or {})
            stabilization["link_authority_v12427"] = link_v12427.register(wrapped.core)
            print("[link-authority-v12427]", stabilization["link_authority_v12427"])
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["link_authority_v12427"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[link-authority-v12427] warning:",type(exc).__name__,str(exc))

        # ALLIANCE_SYSTEM_DOCTOR_PERMANENT
        try:
            import alliance_system_doctor as system_doctor
            stabilization = dict(stabilization or {})
            stabilization["alliance_system_doctor"] = system_doctor.register(wrapped.core)
            print("[alliance-system-doctor]", stabilization["alliance_system_doctor"])
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["alliance_system_doctor"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[alliance-system-doctor] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_BABY_CRE_COPILOT_V1
        try:
            import alliance_baby_cre_copilot_v1 as alliance_baby
            stabilization = dict(stabilization or {})
            stabilization["alliance_baby_cre_copilot_v1"] = alliance_baby.register(wrapped.core)
            print("[alliance-baby-cre-copilot-v1]", stabilization["alliance_baby_cre_copilot_v1"])
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["alliance_baby_cre_copilot_v1"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[alliance-baby-cre-copilot-v1] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_BABY_AUTONOMOUS_SATISFACTION_GATE_V1
        try:
            import alliance_baby_autonomous_satisfaction_gate_v1 as _baby_sat
            stabilization = dict(stabilization or {})
            stabilization["alliance_baby_autonomous_satisfaction_gate_v1"] = _baby_sat.register(wrapped.core)
            print("[alliance-baby-autonomous-satisfaction-gate-v1]", stabilization["alliance_baby_autonomous_satisfaction_gate_v1"])
        except Exception as _baby_sat_err:
            stabilization = dict(stabilization or {})
            stabilization["alliance_baby_autonomous_satisfaction_gate_v1"] = {
                "status":"ERROR",
                "error":f"{type(_baby_sat_err).__name__}: {_baby_sat_err}",
                "fail_safe":True,
            }
            print("[alliance-baby-autonomous-satisfaction-gate-v1] warning:", type(_baby_sat_err).__name__, str(_baby_sat_err))

        # ALLIANCE_BABY_GEOGRAPHIC_INTELLIGENCE_V3
        try:
            import alliance_baby_geographic_intelligence_v3 as _baby_geo_v3
            stabilization = dict(stabilization or {})
            stabilization["alliance_baby_geographic_intelligence_v3"] = {"status":"LOADED","version":_baby_geo_v3.VERSION,"exam":_baby_geo_v3.exam()}
            print("[alliance-baby-geographic-intelligence-v3]", stabilization["alliance_baby_geographic_intelligence_v3"])
        except Exception as _baby_geo_err:
            stabilization = dict(stabilization or {})
            stabilization["alliance_baby_geographic_intelligence_v3"] = {"status":"ERROR","error":f"{type(_baby_geo_err).__name__}: {_baby_geo_err}","fail_safe":True}
            print("[alliance-baby-geographic-intelligence-v3] warning:",type(_baby_geo_err).__name__,str(_baby_geo_err))

        # ALLIANCE_BABY_V3_LIVE_CERTIFICATION
        try:
            import alliance_baby_v3_live_certification as _baby_v3_cert
            stabilization = dict(stabilization or {})
            stabilization["alliance_baby_v3_live_certification"] = _baby_v3_cert.register(wrapped.core)
            print("[alliance-baby-v3-live-certification]", stabilization["alliance_baby_v3_live_certification"])
        except Exception as _baby_v3_cert_err:
            stabilization = dict(stabilization or {})
            stabilization["alliance_baby_v3_live_certification"] = {"status":"ERROR","error":f"{type(_baby_v3_cert_err).__name__}: {_baby_v3_cert_err}","fail_safe":True}
            print("[alliance-baby-v3-live-certification] warning:",type(_baby_v3_cert_err).__name__,str(_baby_v3_cert_err))

        # ALLIANCE_BABY_USE_CASE_MARKET_INTELLIGENCE_V4
        try:
            import alliance_baby_use_case_market_intelligence_v4 as _baby_v4
            stabilization = dict(stabilization or {})
            stabilization["alliance_baby_use_case_market_intelligence_v4"] = {"status":"LOADED","version":_baby_v4.VERSION,"exam":_baby_v4.exam()}
            print("[alliance-baby-use-case-market-v4]", stabilization["alliance_baby_use_case_market_intelligence_v4"])
        except Exception as _baby_v4_err:
            stabilization = dict(stabilization or {})
            stabilization["alliance_baby_use_case_market_intelligence_v4"] = {"status":"ERROR","error":f"{type(_baby_v4_err).__name__}: {_baby_v4_err}","fail_safe":True}
            print("[alliance-baby-use-case-market-v4] warning:",type(_baby_v4_err).__name__,str(_baby_v4_err))

        # ALLIANCE_BABY_MARKET_EVIDENCE_COLLECTOR_V41
        try:
            import alliance_baby_market_evidence_collector_v41 as _baby_v41
            stabilization = dict(stabilization or {})
            stabilization["alliance_baby_market_evidence_collector_v41"] = _baby_v41.register(wrapped.core)
            print("[alliance-baby-market-evidence-v41]", stabilization["alliance_baby_market_evidence_collector_v41"])
        except Exception as _baby_v41_err:
            stabilization = dict(stabilization or {})
            stabilization["alliance_baby_market_evidence_collector_v41"] = {"status":"ERROR","error":f"{type(_baby_v41_err).__name__}: {_baby_v41_err}","fail_safe":True}
            print("[alliance-baby-market-evidence-v41] warning:",type(_baby_v41_err).__name__,str(_baby_v41_err))

        # ALLIANCE_BABY_MARKET_EVIDENCE_TRUTH_GATE_V42
        try:
            import alliance_baby_market_evidence_truth_gate_v42 as _baby_v42
            stabilization = dict(stabilization or {})
            stabilization["alliance_baby_market_evidence_truth_gate_v42"] = {"status":"LOADED","version":_baby_v42.VERSION,"exam":_baby_v42.exam()}
            print("[alliance-baby-market-evidence-truth-v42]", stabilization["alliance_baby_market_evidence_truth_gate_v42"])
        except Exception as _baby_v42_err:
            stabilization = dict(stabilization or {})
            stabilization["alliance_baby_market_evidence_truth_gate_v42"] = {"status":"ERROR","error":f"{type(_baby_v42_err).__name__}: {_baby_v42_err}","fail_safe":True}

        # ALLIANCE_BABY_EXTERNAL_MARKET_EVIDENCE_V5
        try:
            import alliance_baby_external_market_evidence_v5 as _baby_v5
            stabilization = dict(stabilization or {})
            stabilization["alliance_baby_external_market_evidence_v5"] = _baby_v5.register(wrapped.core)
            print("[alliance-baby-external-market-evidence-v5]", stabilization["alliance_baby_external_market_evidence_v5"])
        except Exception as _baby_v5_err:
            stabilization = dict(stabilization or {})
            stabilization["alliance_baby_external_market_evidence_v5"] = {"status":"ERROR","error":f"{type(_baby_v5_err).__name__}: {_baby_v5_err}","fail_safe":True}
            print("[alliance-baby-external-market-evidence-v5] warning:",type(_baby_v5_err).__name__,str(_baby_v5_err))

        # ALLIANCE_BUSINESS_AUTOPILOT_V1
        try:
            import alliance_business_autopilot_v1 as business_autopilot_v1
            stabilization = dict(stabilization or {})
            stabilization["alliance_business_autopilot_v1"] = business_autopilot_v1.register(wrapped.core)
            print("[alliance-business-autopilot-v1]", stabilization["alliance_business_autopilot_v1"])
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["alliance_business_autopilot_v1"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[alliance-business-autopilot-v1] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_DEEP_RUNTIME_AUDITOR
        try:
            import alliance_deep_runtime_auditor as deep_audit
            stabilization = dict(stabilization or {})
            stabilization["alliance_deep_runtime_auditor"] = deep_audit.register(wrapped.core)
            print("[alliance-deep-runtime-auditor]", stabilization["alliance_deep_runtime_auditor"])
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["alliance_deep_runtime_auditor"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[alliance-deep-runtime-auditor] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_SEMANTIC_REVIEW_LATE_SELF_HEAL_V1
        try:
            import alliance_semantic_review_v3 as semantic_review_v3
            semantic_review_result = semantic_review_v3.register(wrapped.core)
            semantic_review_paths = {
                getattr(r, "path", None) for r in wrapped.app.router.routes
            }
            if "/semantic-v3/review" not in semantic_review_paths:
                raise RuntimeError("semantic review route missing after late registration")
            stabilization = dict(stabilization or {})
            stabilization["semantic_review_late_self_heal_v1"] = {
                "status": "READY",
                "registration": semantic_review_result,
                "route": "/semantic-v3/review",
            }
            print("[semantic-review-late-self-heal-v1] READY")
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["semantic_review_late_self_heal_v1"] = {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "fail_safe": True,
            }
            print("[semantic-review-late-self-heal-v1] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_DATABASE_RECTIFICATION_V1_BEGIN
        stabilization = dict(stabilization or {})
        try:
            import alliance_database_rectification_v1 as database_rectification_v1
            stabilization["database_rectification_v1"] = database_rectification_v1.register(wrapped.core)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["database_rectification_v1"] = {
                "status":"ERROR",
                "error":f"{type(exc).__name__}: {exc}",
                "fail_safe":True,
            }
            print("[database-rectification-v1] warning:", type(exc).__name__, str(exc))
        # ALLIANCE_DATABASE_RECTIFICATION_V1_END

        # ALLIANCE_DATABASE_RECTIFICATION_V2_BEGIN
        stabilization = dict(stabilization or {})
        try:
            import alliance_database_rectification_v2 as database_rectification_v2
            stabilization["database_rectification_v2"] = database_rectification_v2.register(wrapped.core)
        except Exception as exc:
            stabilization["database_rectification_v2"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
        # ALLIANCE_DATABASE_RECTIFICATION_V2_END

        # ALLIANCE_DATABASE_RECTIFICATION_V3_BEGIN
        stabilization = dict(stabilization or {})
        try:
            import alliance_database_rectification_v3 as database_rectification_v3
            stabilization["database_rectification_v3"] = database_rectification_v3.register(wrapped.core)
        except Exception as exc:
            stabilization["database_rectification_v3"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
        # ALLIANCE_DATABASE_RECTIFICATION_V3_END

        # ALLIANCE_DATABASE_RECTIFICATION_V4_BEGIN
        stabilization = dict(stabilization or {})
        try:
            import alliance_database_rectification_v4 as database_rectification_v4
            stabilization["database_rectification_v4"] = database_rectification_v4.register(wrapped.core)
        except Exception as exc:
            stabilization["database_rectification_v4"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
        # ALLIANCE_DATABASE_RECTIFICATION_V4_END

        # ALLIANCE_MASTER_REQUIREMENT_AUTHORITY_V1_BEGIN
        stabilization = dict(stabilization or {})
        try:
            import alliance_master_requirement_authority_v1 as master_requirement_authority_v1
            stabilization["master_requirement_authority_v1"] = master_requirement_authority_v1.register(wrapped.core)
        except Exception as exc:
            stabilization["master_requirement_authority_v1"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
        # ALLIANCE_MASTER_REQUIREMENT_AUTHORITY_V1_END

        # ALLIANCE_MASTER_MATCHER_FINAL_ROUTE_AUTHORITY_V1
        # Reassert the settled current Master matcher after legacy registrations.
        # No URL, database, schema or contact changes.
        try:
            import alliance_master_requirement_authority_v1 as final_master_matcher_v1
            matcher_app = getattr(wrapped, "app", None) or getattr(wrapped.core, "app", None) or wrapped.core
            matcher_paths = {
                "/alliance/primary/matcher",
                "/alliance/master-requirement-matcher",
            }
            matcher_app.router.routes[:] = [
                r for r in matcher_app.router.routes
                if not (
                    getattr(r, "path", None) in matcher_paths
                    and "GET" in set(getattr(r, "methods", set()) or set())
                )
            ]
            final_matcher_result = final_master_matcher_v1.register(wrapped.core)
            stabilization = dict(stabilization or {})
            stabilization["master_matcher_final_route_authority_v1"] = final_matcher_result
            print("[master-matcher-final-route-authority-v1]", final_matcher_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["master_matcher_final_route_authority_v1"] = {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "fail_safe": True,
            }
            print("[master-matcher-final-route-authority-v1] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_FAST_MANUAL_FORMS
        # The current manual property/requirement forms and their operational
        # source tables must be registered before final database authorities.
        try:
            import fast_manual_forms as fast_manual_forms_v19
            fast_manual_result = fast_manual_forms_v19.register(wrapped)
            stabilization = dict(stabilization or {})
            stabilization["fast_manual_forms_v19"] = fast_manual_result
            print("[fast-manual-forms-v19]", fast_manual_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["fast_manual_forms_v19"] = {
                "status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True
            }
            print("[fast-manual-forms-v19] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_MANUAL_DATABASE_RESTORE_V1150
        # Final exact-route authority for the settled Manual Property database.
        try:
            import alliance_manual_database_restore_v1150 as manual_db_restore_v1150
            manual_db_restore_result = manual_db_restore_v1150.register(wrapped)
            stabilization = dict(stabilization or {})
            stabilization["manual_database_restore_v1150"] = manual_db_restore_result
            print("[manual-database-restore-v1150]", manual_db_restore_result)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["manual_database_restore_v1150"] = {
                "status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True
            }
            print("[manual-database-restore-v1150] warning:", type(exc).__name__, str(exc))

        # ALLIANCE_FINAL_DATABASE_ROUTE_AUTHORITY_V2
        # Rebuild the public database authority after every legacy database/manual
        # registrar. This prevents alliance_manual_database_restore_v1150 or any
        # older route owner from serving a stale Manual Property page.
        try:
            import alliance_property_authority_v11 as final_database_v910

            final_database_paths = {
                "/alliance/primary/databases",
                "/alliance/final/databases",
                "/alliance/final/database/{source}",
                "/alliance/final/requirements",
                "/alliance/final/requirements/{source}",
            }

            # Remove stale GET owners from the core route table, then install the
            # exact same current renderer there as a fallback. The outer dispatcher
            # still serves the isolated app first for /alliance/final/database/*.
            wrapped.app.router.routes[:] = [
                r for r in wrapped.app.router.routes
                if not (
                    getattr(r, "path", None) in final_database_paths
                    and "GET" in set(getattr(r, "methods", set()) or set())
                )
            ]

            isolated_database_app = FastAPI(
                title="Alliance Final Database Authority",
                docs_url=None,
                redoc_url=None,
                openapi_url=None,
            )
            isolated_result = final_database_v910.register(
                wrapped.core,
                served_app=isolated_database_app,
            )
            core_result = final_database_v910.register(
                wrapped.core,
                served_app=wrapped.app,
            )
            DATABASE_APP = isolated_database_app

            stabilization = dict(stabilization or {})
            stabilization["final_database_route_authority_v2"] = {
                "status": "READY",
                "owner": "alliance_property_authority_v11",
                "version": getattr(final_database_v910, "VERSION", None),
                "requirements_touched": False,
                "registered_after_manual_restore": True,
                "isolated_registration": isolated_result,
                "core_registration": core_result,
            }
            print(
                "[final-database-route-authority-v2]",
                stabilization["final_database_route_authority_v2"],
            )
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["final_database_route_authority_v2"] = {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "fail_safe": True,
            }
            print(
                "[final-database-route-authority-v2] warning:",
                type(exc).__name__,
                str(exc),
            )

        # ALLIANCE_FINAL_REQUIREMENT_ROUTE_AUTHORITY_V1
        # Final takeover runs after all legacy requirement route registrars.
        try:
            import alliance_requirement_restore_v1235 as final_reqrestore_v1235
            isolated_requirement_app = FastAPI(
                title="Alliance Requirement Authority",
                docs_url=None,
                redoc_url=None,
                openapi_url=None,
            )
            final_reqrestore_result = final_reqrestore_v1235.register(
                wrapped.core,
                served_app=isolated_requirement_app,
            )

            # ALLIANCE_REQUIREMENT_DUAL_RUNTIME_AUTHORITY_V1
            # The public domain has historically reached both the outer dispatcher
            # and the wrapped core route table. Keep the isolated authority, but
            # install the exact same settled requirement handler on the core app
            # after every legacy registrar as well. register() removes the legacy
            # GET owners for the settled requirement paths before adding itself.
            # No database/schema/data/link changes are made here.
            core_reqrestore_result = final_reqrestore_v1235.register(
                wrapped.core,
                served_app=wrapped.app,
            )

            REQUIREMENT_APP = isolated_requirement_app
            stabilization = dict(stabilization or {})
            stabilization["final_requirement_route_authority_v1"] = {
                "status": "READY",
                "owner": "alliance_requirement_restore_v1235",
                "registered_after_legacy_routes": True,
                "dual_runtime_authority": True,
                "isolated_registration": final_reqrestore_result,
                "core_registration": core_reqrestore_result,
                "registration": final_reqrestore_result,
            }
            print(
                "[final-requirement-route-authority-v1]",
                stabilization["final_requirement_route_authority_v1"],
            )
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["final_requirement_route_authority_v1"] = {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "fail_safe": True,
            }
            print(
                "[final-requirement-route-authority-v1] warning:",
                type(exc).__name__,
                str(exc),
            )

        # ALLIANCE_ISOLATED_APP_GLOBAL_PUBLISH_V1
        # Publish the already-created isolated authorities to the outer
        # HealthFirstDispatcher. No routes, URLs, databases or data change.
        globals()["REQUIREMENT_APP"] = locals().get(
            "REQUIREMENT_APP",
            globals().get("REQUIREMENT_APP")
        )
        globals()["DATABASE_APP"] = locals().get(
            "DATABASE_APP",
            globals().get("DATABASE_APP")
        )
        globals()["MATCHER_APP"] = locals().get(
            "MATCHER_APP",
            globals().get("MATCHER_APP")
        )

        print(
            "[isolated-app-global-publish-v1]",
            {
                "requirement": globals().get("REQUIREMENT_APP") is not None,
                "database": globals().get("DATABASE_APP") is not None,
                "matcher": globals().get("MATCHER_APP") is not None,
            }
        )

# ALLIANCE_WHATSAPP_HISTORICAL_CONTACT_RECOVERY_V1
        try:
            import alliance_whatsapp_historical_contact_recovery_v1 as wa_contact_recovery_v1
            stabilization = dict(stabilization or {})
            stabilization["whatsapp_historical_contact_recovery_v1"] = wa_contact_recovery_v1.register(wrapped.core)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["whatsapp_historical_contact_recovery_v1"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}

        # ALLIANCE_REQUIREMENT_SOURCE_TRUTH_AUDITOR_V1
        try:
            import alliance_requirement_source_truth_auditor_v1 as req_truth_v1
            stabilization = dict(stabilization or {})
            stabilization["requirement_source_truth_auditor_v1"] = req_truth_v1.register(wrapped.core)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["requirement_source_truth_auditor_v1"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}

        # ALLIANCE_MASTER_RECONCILIATION_AUDITOR_V1
        try:
            import alliance_master_reconciliation_auditor_v1 as master_recon_v1
            stabilization = dict(stabilization or {})
            stabilization["master_reconciliation_auditor_v1"] = master_recon_v1.register(wrapped.core)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["master_reconciliation_auditor_v1"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}

        # ALLIANCE_MASTER_COVERAGE_FIX_V1
        try:
            import alliance_master_coverage_fix_v1 as master_coverage_v1
            stabilization = dict(stabilization or {})
            stabilization["master_coverage_fix_v1"] = (
                master_coverage_v1.register(wrapped.core)
            )
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["master_coverage_fix_v1"] = {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "fail_safe": True,
            }

        # ALLIANCE_ASTRA_CLEAN_CONTACT_MASTER_V1
        try:
            import alliance_astra_clean_contact_master_v1 as astra_clean_contact_master_v1
            stabilization = dict(stabilization or {})
            stabilization["astra_clean_contact_master_v1"] = astra_clean_contact_master_v1.register(wrapped.core)
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["astra_clean_contact_master_v1"] = {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "fail_safe": True,
            }

        CORE_APP = wrapped.app

        # ALLIANCE_REQUIREMENT_RESTORATION_V2
        try:
            import alliance_requirement_restoration_v2 as req_restore_v2
            stabilization = dict(stabilization or {})
            stabilization["requirement_restoration_v2"] = (
                req_restore_v2.register(wrapped.core)
            )
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["requirement_restoration_v2"] = {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "fail_safe": True,
            }
        # ALLIANCE_REGIONAL_NEWSPAPER_AUTHORITY_V1 - post-core authority
        try:
            import alliance_regional_newspaper_authority_v1 as _alliance_regional_newspaper_authority_v1
            _regional_newspaper_result = _alliance_regional_newspaper_authority_v1.register(wrapped.core, served_app=wrapped.app)
            stabilization = dict(stabilization or {})
            stabilization["regional_newspaper_authority_v1"] = _regional_newspaper_result
            print("ALLIANCE_REGIONAL_NEWSPAPER_AUTHORITY_V1: REGISTERED", _regional_newspaper_result)
        except Exception as _regional_newspaper_error:
            stabilization = dict(stabilization or {})
            stabilization["regional_newspaper_authority_v1"] = {"status":"ERROR","error":f"{type(_regional_newspaper_error).__name__}: {_regional_newspaper_error}","fail_safe":True}
            print("ALLIANCE_REGIONAL_NEWSPAPER_AUTHORITY_V1 registration error:", type(_regional_newspaper_error).__name__, str(_regional_newspaper_error))


        # ALLIANCE_AUTOMATED_DEAL_DESK_V1
        try:
            import alliance_automated_deal_desk_v1 as _alliance_deal_desk_v1
            AUTOMATED_DEAL_DESK_V1 = _alliance_deal_desk_v1.register(wrapped)
            stabilization = dict(stabilization or {})
            stabilization["automated_deal_desk_v1"] = AUTOMATED_DEAL_DESK_V1
        except Exception as _deal_desk_exc:
            AUTOMATED_DEAL_DESK_V1 = {
                "status":"ERROR",
                "version":"1.0.0-AUTOMATED-DEAL-DESK",
                "error":f"{type(_deal_desk_exc).__name__}: {_deal_desk_exc}",
                "fail_safe":True,
            }
            stabilization = dict(stabilization or {})
            stabilization["automated_deal_desk_v1"] = AUTOMATED_DEAL_DESK_V1
            print("[deal-desk-v1] warning:", type(_deal_desk_exc).__name__, str(_deal_desk_exc))

        # ALLIANCE_RUNTIME_GUARDIAN_V1
        try:
            import alliance_runtime_guardian_v1 as _runtime_guardian_v1
            RUNTIME_GUARDIAN_V1 = _runtime_guardian_v1.register(wrapped)
            stabilization = dict(stabilization or {})
            stabilization["runtime_guardian_v1"] = RUNTIME_GUARDIAN_V1
        except Exception as _guardian_exc:
            RUNTIME_GUARDIAN_V1 = {
                "status":"ERROR",
                "version":"1.0.0-RUNTIME-GUARDIAN",
                "error":f"{type(_guardian_exc).__name__}: {_guardian_exc}",
                "fail_safe":True,
            }
            stabilization = dict(stabilization or {})
            stabilization["runtime_guardian_v1"] = RUNTIME_GUARDIAN_V1
            print("[runtime-guardian-v1] warning:", type(_guardian_exc).__name__, str(_guardian_exc))

        # ALLIANCE_MASTER_CONSOLIDATION_V1
        try:
            import alliance_master_consolidation_v1 as _amc
            MASTER_CONSOLIDATION_V1 = _amc.register(wrapped)
            stabilization = dict(stabilization or {})
            stabilization["master_consolidation_v1"] = MASTER_CONSOLIDATION_V1
        except Exception as _x:
            MASTER_CONSOLIDATION_V1={"status":"ERROR","version":"1.0.1-MASTER-CONSOLIDATION","error":f"{type(_x).__name__}: {_x}","fail_safe":True}
            stabilization = dict(stabilization or {})
            stabilization["master_consolidation_v1"]=MASTER_CONSOLIDATION_V1
            print("[master-consolidation-v1] warning:",type(_x).__name__,str(_x))

        # ALLIANCE_DASHBOARD_AUTHORITY_V1
        # Already installed before serving starts; do not add middleware late.
        stabilization = dict(stabilization or {})
        stabilization["dashboard_authority_v1"] = {
            "status":"READY_EARLY",
            "owner":"alliance_dashboard_authority_v1",
            "version":"2.3.0-CLEAN-SOURCE-NAVIGATION"
        }

        # ALLIANCE_TEAM_READY_CERTIFICATION_V1
        try:
            import alliance_team_ready_certification_v1 as _team_ready_cert_v1
            TEAM_READY_CERTIFICATION_V1 = _team_ready_cert_v1.register(wrapped)
            stabilization = dict(stabilization or {})
            stabilization["team_ready_certification_v1"] = TEAM_READY_CERTIFICATION_V1
        except Exception as _team_ready_cert_v1_exc:
            TEAM_READY_CERTIFICATION_V1 = {
                "status": "ERROR",
                "version": "1.0.0-TEAM-READY-GOLD-CERTIFICATION",
                "error": f"{type(_team_ready_cert_v1_exc).__name__}: {_team_ready_cert_v1_exc}",
                "fail_safe": True,
            }
            stabilization = dict(stabilization or {})
            stabilization["team_ready_certification_v1"] = TEAM_READY_CERTIFICATION_V1
            print("[team-ready-cert-v1] warning:", type(_team_ready_cert_v1_exc).__name__, str(_team_ready_cert_v1_exc))

        # ALLIANCE_SEMANTIC_AUTO_TRAINER_V4
        try:
            import alliance_semantic_auto_trainer_v4 as _semantic_auto_trainer_v4
            SEMANTIC_AUTO_TRAINER_V4 = _semantic_auto_trainer_v4.register(wrapped)
            stabilization = dict(stabilization or {})
            stabilization["semantic_auto_trainer_v4"] = SEMANTIC_AUTO_TRAINER_V4
        except Exception as _semantic_auto_trainer_v4_exc:
            SEMANTIC_AUTO_TRAINER_V4 = {
                "status": "ERROR",
                "version": "4.0.0-AUTO-TRAINER-98-CERTIFICATION-GATE",
                "error": f"{type(_semantic_auto_trainer_v4_exc).__name__}: {_semantic_auto_trainer_v4_exc}",
                "fail_safe": True,
            }
            stabilization = dict(stabilization or {})
            stabilization["semantic_auto_trainer_v4"] = SEMANTIC_AUTO_TRAINER_V4
            print("[semantic-auto-trainer-v4] warning:", type(_semantic_auto_trainer_v4_exc).__name__, str(_semantic_auto_trainer_v4_exc))

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
        # ALLIANCE_SEMANTIC_REVIEW_AUTHORITATIVE_APP_BRIDGE_V3
        try:
            from types import SimpleNamespace
            import alliance_semantic_review_v3 as semantic_review_v3_final

            # CORE_APP is served from wrapped.app.
            # The semantic registrar expects an object exposing .app and .engine.
            # Passing wrapped.core registers on wrapped.core.app, which is not
            # necessarily the served authoritative app.
            semantic_authority = SimpleNamespace(
                app=wrapped.app,
                engine=wrapped.core.engine,
            )

            semantic_registration_v3 = semantic_review_v3_final.register(
                semantic_authority
            )

            semantic_paths = {
                getattr(r, "path", None) for r in wrapped.app.router.routes
            }
            required_semantic_routes = {
                "/semantic-v3/review",
                "/semantic-v3/review/{run_id}",
                "/api/semantic-v3/review/{run_id}",
                "/api/semantic-v3/review-stats",
            }
            missing_semantic_routes = sorted(
                required_semantic_routes - semantic_paths
            )
            if missing_semantic_routes:
                raise RuntimeError(
                    "authoritative semantic routes missing: "
                    + ",".join(missing_semantic_routes)
                )

            stabilization = dict(stabilization or {})
            stabilization["semantic_review_authoritative_app_bridge_v3"] = {
                "status": "READY",
                "owner": "wrapped.app",
                "served_as": "CORE_APP",
                "registration": semantic_registration_v3,
                "routes": sorted(required_semantic_routes),
            }
            print("[semantic-review-authoritative-app-bridge-v3] READY")
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["semantic_review_authoritative_app_bridge_v3"] = {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "fail_safe": True,
            }
            print(
                "[semantic-review-authoritative-app-bridge-v3] warning:",
                type(exc).__name__,
                str(exc),
            )



        # ALLIANCE_WHATSAPP_SOURCE_RECONCILIATION_V2_BEGIN
        stabilization = dict(stabilization or {})
        try:
            import alliance_whatsapp_source_reconciliation_v2 as wa_source_recon_v2
            stabilization["whatsapp_source_reconciliation_v2"] = wa_source_recon_v2.register(wrapped.core)
        except Exception as exc:
            stabilization["whatsapp_source_reconciliation_v2"] = {
                "status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True
            }
        # ALLIANCE_WHATSAPP_SOURCE_RECONCILIATION_V2_END

        # ALLIANCE_AI_DOCTOR_V21
        try:
            import alliance_ai_doctor_v21 as _alliance_ai_doctor_v21
            _doctor_v21_result = _alliance_ai_doctor_v21.register(wrapped.core, served_app=wrapped.app)
            stabilization = dict(stabilization or {})
            stabilization["alliance_ai_doctor_v21"] = _doctor_v21_result
            print("[alliance-ai-doctor-v21]", _doctor_v21_result)
        except Exception as _doctor_v21_exc:
            stabilization = dict(stabilization or {})
            stabilization["alliance_ai_doctor_v21"] = {"status":"ERROR","error":f"{type(_doctor_v21_exc).__name__}: {_doctor_v21_exc}","fail_safe":True}
            print("[alliance-ai-doctor-v21] ERROR", type(_doctor_v21_exc).__name__, str(_doctor_v21_exc))

        # ALLIANCE_CONTACT_PROVENANCE_V705_STARTUP_RESTORE
        # Restore the required parent lifecycle before Promotion Integrity 7.1.1.
        # Existing authority only. No database/schema/link redesign.
        try:
            import alliance_contact_provenance_v705 as contact_provenance_v705
            stabilization = dict(stabilization or {})
            stabilization["contact_provenance_v705"] = (
                contact_provenance_v705.start(wrapped.core)
            )
            print(
                "[contact-provenance-v705]",
                stabilization["contact_provenance_v705"],
            )
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["contact_provenance_v705"] = {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "fail_safe": True,
            }
            print(
                "[contact-provenance-v705] warning:",
                type(exc).__name__,
                str(exc),
            )

        # ALLIANCE_PROMOTION_INTEGRITY_V711_STARTUP_RESTORE
        # Restore existing validated source -> Master promotion lifecycle.
        # No database/schema/data/link changes are made here.
        try:
            import alliance_promotion_integrity_v711 as promotion_integrity_v711
            stabilization = dict(stabilization or {})
            stabilization["promotion_integrity_v711"] = (
                promotion_integrity_v711.start(wrapped.core)
            )
            print(
                "[promotion-integrity-v711]",
                stabilization["promotion_integrity_v711"],
            )
        except Exception as exc:
            stabilization = dict(stabilization or {})
            stabilization["promotion_integrity_v711"] = {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "fail_safe": True,
            }
            print(
                "[promotion-integrity-v711] warning:",
                type(exc).__name__,
                str(exc),
            )

        # ALLIANCE_RELEASE_STABILITY_V1
        # Runs last. A critical route-owner regression blocks the new release.
        try:
            import alliance_release_stability_v1 as release_stability_v1
            stabilization = dict(stabilization or {})
            stabilization["release_stability_v1"] = (
                release_stability_v1.register(
                    wrapped.core,
                    requirement_app=REQUIREMENT_APP,
                    served_app=wrapped.app,
                )
            )
        except Exception as exc:
            raise RuntimeError(
                "Alliance critical-flow release gate failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        # ALLIANCE_CLEAN_CORE_V2_FINAL_SHELL
        # Disabled: it removes/replaces /alliance/primary and caused dashboard regressions.
        stabilization = dict(stabilization or {})
        stabilization["clean_core_v2"] = {
            "status":"DISABLED",
            "reason":"TEAM_COMMAND_CENTRE_V1220_IS_SOLE_PRIMARY_OWNER"
        }

        BOOT["core_loaded"] = True
        BOOT["state"] = "READY" if BOOT.get("critical_ready") else ("READY" if stabilization.get("registered") else "DEGRADED")
        stabilization["critical_workspace"]="READY" if BOOT.get("critical_ready") else "UNKNOWN"
        stabilization["primary_dashboard_owner"]="alliance_dashboard_authority_v1"
        stabilization["primary_dashboard_version"]="2.3.0-CLEAN-SOURCE-NAVIGATION"
        stabilization["requirement_owner"]="alliance_requirement_restore_v1235"
        stabilization["database_owner"]="alliance_property_authority_v11"
        stabilization["matcher_owner"]="alliance_master_requirement_authority_v1"
        BOOT["stabilization"] = stabilization
        BOOT["completed_at"] = _utcnow()
        print("[health-first] Alliance core application loaded successfully")

    except Exception as exc:
        if BOOT.get("critical_ready"):
            BOOT["core_loaded"] = True
            BOOT["state"] = "READY"
            BOOT["error"] = None
            BOOT["trace"] = None
            BOOT["legacy_error"] = f"{type(exc).__name__}: {exc}"
            BOOT["legacy_trace"] = traceback.format_exc(limit=30)
            BOOT["completed_at"] = _utcnow()
            print("[legacy-background] degraded after critical READY:", type(exc).__name__, str(exc))
        else:
            BOOT["core_loaded"] = False
            BOOT["state"] = "FAILED"
            BOOT["error"] = f"{type(exc).__name__}: {exc}"
            BOOT["trace"] = traceback.format_exc(limit=30)
            BOOT["completed_at"] = _utcnow()
            print("[health-first] Alliance core failed:", type(exc).__name__, str(exc))


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
        "/whatsapp-queue-status",
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
        if CORE_APP is None:
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

        # GLOBAL DASHBOARD CANONICALIZATION
        # Any historical Dashboard/Home URL must land on the single current
        # Alliance Command Centre. This is enforced at the outermost ASGI
        # boundary, before legacy middleware or routes can serve an old UI.
        legacy_dashboard_paths = {
            "/workspace",
            "/team-dashboard",
            "/team-workspace-clean",
            "/simple-dashboard",
            "/v14-dashboard",
            "/v15-dashboard",
            "/final-dashboard",
            "/final-dashboard-v2",
            "/final-dashboard-v3",
            "/data-command-center",
            "/alliance",
            "/alliance/legacy/team-command-centre",
            "/alliance/legacy/business-os-command",
            "/alliance/legacy/production-surface-home",
        }
        if path in legacy_dashboard_paths:
            response = RedirectResponse(
                url="/alliance/primary",
                status_code=307,
                headers={"Cache-Control":"no-store"},
            )
            await response(scope, receive, send)
            return

        # ALLIANCE_ISOLATED_REQUIREMENT_DISPATCH_V2
        # Preserve the original ASGI scope and pi_session cookie. Match the
        # settled requirement namespace even when an upstream proxy supplies a
        # root_path or a non-canonical raw path. This is the outermost public
        # ASGI boundary, so legacy core routes cannot intercept these requests.
        root_path = str(scope.get("root_path", "") or "")
        raw_path = scope.get("raw_path", b"")
        if isinstance(raw_path, (bytes, bytearray)):
            raw_path = raw_path.decode("utf-8", "ignore")
        else:
            raw_path = str(raw_path or "")
        requirement_namespace = "/alliance/final/requirements"
        is_requirement_request = (
            path.startswith(requirement_namespace)
            or (root_path + path).startswith(requirement_namespace)
            or raw_path.startswith(requirement_namespace)
        )
        if (
            is_requirement_request
            and REQUIREMENT_APP is not None
        ):
            await REQUIREMENT_APP(scope, receive, send)
            return

        database_namespace = "/alliance/final/database"
        if (
            (path == "/alliance/final/databases" or path.startswith(database_namespace + "/"))
            and DATABASE_APP is not None
        ):
            await DATABASE_APP(scope, receive, send)
            return

        matcher_paths = (
            "/alliance/primary/matcher",
            "/alliance/master-requirement-matcher",
        )
        if (
            (path in matcher_paths or path.startswith("/alliance/master-requirement-matcher/"))
            and MATCHER_APP is not None
        ):
            await MATCHER_APP(scope, receive, send)
            return

        await self._serve_core(scope, receive, send)


app = HealthFirstDispatcher()


# 7.3.7 HISTORICAL EVIDENCE REPAIR REGISTRATION
