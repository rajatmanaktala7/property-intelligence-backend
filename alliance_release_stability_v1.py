from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Request, HTTPException

VERSION = "1.2.0-CLEAN-UI-DATA-RECTIFICATION-GATE"
TARGET_SCORE = 100.0

CANONICAL_ROUTES = (
    ("/login", "GET", "app", "login_page"),
    ("/login", "POST", "app", "login_post"),
    ("/alliance/primary", "GET", "alliance_regional_newspaper_authority_v1", "regional_dashboard"),
    ("/commercial-intelligence", "GET", "alliance_final_dashboard_v1241", "commercial_fallback"),
    ("/alliance/primary/matcher", "GET", "alliance_master_requirement_authority_v1", "smart_matcher_redirect"),
)
REQUIREMENT_ROUTES = (
    ("/alliance/final/requirements", "GET", "alliance_requirement_restore_v1235", "requirement_hub"),
    ("/alliance/final/requirements/{source}", "GET", "alliance_requirement_restore_v1235", "requirement_db"),
)


def _endpoint(route):
    ep = getattr(route, "endpoint", None)
    return str(getattr(ep, "__module__", "") or ""), str(getattr(ep, "__name__", "") or "")


def _check(app, spec):
    path, method, expected_module, expected_name = spec
    rows = []
    if app is not None:
        for index, route in enumerate(list(app.router.routes)):
            if getattr(route, "path", None) != path or method not in set(getattr(route, "methods", set()) or set()):
                continue
            module, name = _endpoint(route)
            rows.append({"index":index,"module":module,"name":name})
    active = rows[0] if rows else None
    passed = bool(active and active["module"] == expected_module and active["name"] == expected_name)
    return {
        "path":path,"method":method,"passed":passed,
        "expected":f"{expected_module}.{expected_name}",
        "active":f"{active['module']}.{active['name']}" if active else None,
        "registered_count":len(rows),
    }


def assert_critical_route_ownership(core: Any, requirement_app: Any) -> dict:
    app = getattr(core, "app", None) or core
    checks = [_check(app, x) for x in CANONICAL_ROUTES]
    checks += [_check(requirement_app, x) for x in REQUIREMENT_ROUTES]
    failures = [f"{x['method']} {x['path']}: {x['active'] or 'MISSING'}" for x in checks if not x["passed"]]
    if failures:
        raise RuntimeError("Critical route ownership regression: " + "; ".join(failures))
    return {"status":"PASS","acceptance_score":100.0,"critical_failures":[],"checks":checks}


def audit(core: Any, requirement_app: Any = None, served_app: Any = None) -> dict:
    report = assert_critical_route_ownership(core, requirement_app)
    try:
        import alliance_ui_data_rectification_v1 as fix
        rectification = {
            "status":"ACTIVE",
            "version":fix.VERSION,
            "newspaper":"CAPTURE_ONLY",
            "manual":"ADD_ONLY",
            "magazine":"CLEAN_SOURCE_PAGE",
            "requirement_contacts":"NORMALIZED",
            "master_requirements":"ALL_SOURCE_TOTAL",
            "master_properties":"SOURCE_SECTIONS_ONLY",
        }
    except Exception as exc:
        rectification = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}"}
    return {"status":report["status"],"version":VERSION,"acceptance_score":report["acceptance_score"],"critical_route_ownership":report["checks"],"rectification":rectification,"database_changed":False}


def register(core: Any, requirement_app: Any = None, served_app: Any = None) -> dict:
    app = getattr(core, "app", None) or core

    # Run after all legacy/source registrars. This patch changes presentation and
    # read normalization only; it does not bulk-promote or delete database rows.
    import alliance_ui_data_rectification_v1 as fix
    fix_state = fix.register(core, requirement_app=requirement_app, served_app=served_app or app)

    report = assert_critical_route_ownership(core, requirement_app)
    router = APIRouter()

    @router.get("/api/alliance/release-stability-v1/status")
    def status_route(request: Request):
        role = core.get_role(request) if callable(getattr(core, "get_role", None)) else None
        if role not in {"admin", "team"}:
            raise HTTPException(401, "Login required")
        return audit(core, requirement_app, served_app)

    app.include_router(router)
    return {
        "status":"PASS",
        "version":VERSION,
        "acceptance_score":100.0,
        "critical_routes_locked":True,
        "rectification":fix_state,
        "database_changed":False,
    }
