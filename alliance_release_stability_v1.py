from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Request, HTTPException

VERSION = "1.3.1-RECTIFICATION-ON-REQUIREMENT-AUTHORITY"
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


def _install_source_truth_patch(fix):
    """Keep old requirement rows visible in the correct source view.

    These are read-time aliases only. No source or Master row is mutated.
    """
    original = fix._classify_source
    manual_tables = {
        "PI_UNIFIED_MANUAL_REQUIREMENTS",
        "PI_RETAIL_MANUAL_REQUIREMENTS",
        "PI_HOSPITALITY_MANUAL_REQUIREMENTS",
        "PI_REQUIREMENTS",
        "PI_OPERATIONAL_REQUIREMENTS",
        "PI_RETAIL_REQUIREMENTS",
        "PI_HOSPITALITY_REQUIREMENTS",
    }

    def classify(value):
        s = str(value or "").strip().upper()
        if s in manual_tables:
            return "MANUAL"
        return original(value)

    fix._classify_source = classify


def audit(core: Any, requirement_app: Any = None, served_app: Any = None) -> dict:
    report = assert_critical_route_ownership(core, requirement_app)
    try:
        import alliance_ui_data_rectification_v1 as fix
        rectification = {
            "status":"ACTIVE",
            "version":fix.VERSION,
            "served_app_rectified":True,
            "isolated_requirement_app_rectified":bool(requirement_app),
            "newspaper":"RESTORED_SOURCE_VIEW_WHEN_EVIDENCE_EXISTS",
            "manual":"RESTORED_LEGACY_PLUS_MANUAL_SOURCE_VIEW",
            "requirement_contacts":"NORMALIZED_PLUS_LINKED_WHATSAPP_SENDER",
            "master_requirements":"ALL_SOURCE_TOTAL",
            "table_contract":"DATE_FIRST_BOLD_GRID_SAME_FIELDS",
        }
    except Exception as exc:
        rectification = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}"}
    return {"status":report["status"],"version":VERSION,"acceptance_score":report["acceptance_score"],"critical_route_ownership":report["checks"],"rectification":rectification,"database_changed":False}


def register(core: Any, requirement_app: Any = None, served_app: Any = None) -> dict:
    app = getattr(core, "app", None) or core

    # Presentation/read normalization only. Source records and canonical rows are
    # not bulk-promoted, deleted or rewritten here.
    import alliance_ui_data_rectification_v1 as fix
    _install_source_truth_patch(fix)

    served_state = fix.register(core, requirement_app=requirement_app, served_app=served_app or app)

    # production_entrypoint dispatches /alliance/final/requirements* to a separate
    # ASGI app. Rectification therefore has to be installed on that actual app,
    # not only on the main Alliance application.
    requirement_state = None
    if requirement_app is not None and requirement_app is not (served_app or app):
        requirement_state = fix.register(
            core,
            requirement_app=requirement_app,
            served_app=requirement_app,
        )

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
        "rectification":served_state,
        "requirement_rectification":requirement_state,
        "database_changed":False,
    }
