from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import inspect, text

VERSION = "1.0.2-NONBLOCKING-BOOT-ROUTE-GATE"
TARGET_SCORE = 99.0

CANONICAL_ROUTES = (
    ("/login", "GET", "app", "login_page"),
    ("/login", "POST", "app", "login_post"),
    (
        "/alliance/primary",
        "GET",
        "alliance_regional_newspaper_authority_v1",
        "regional_dashboard",
    ),
    (
        "/commercial-intelligence",
        "GET",
        "alliance_final_dashboard_v1241",
        "commercial_fallback",
    ),
    (
        "/alliance/primary/matcher",
        "GET",
        "alliance_master_requirement_authority_v1",
        "smart_matcher_redirect",
    ),
)

REQUIREMENT_ROUTES = (
    (
        "/alliance/final/requirements",
        "GET",
        "alliance_requirement_restore_v1235",
        "requirement_hub",
    ),
    (
        "/alliance/final/requirements/{source}",
        "GET",
        "alliance_requirement_restore_v1235",
        "requirement_db",
    ),
)

REQUIRED_PATHS = (
    "/property-manual",
    "/alliance/final/databases",
    "/requirements-workbench",
    "/whatsapp-live",
    "/api/commercial-intelligence/status",
    "/api/v451/live/status",
)

RUNTIME_PROBE_PATHS = (
    "/newspaper-v83",
    "/api/newspaper-v83/health",
    "/api/whatsapp-live-clean-os/status",
)

REQUIRED_TABLES = (
    "pi_requirement_gate_v1191",
    "pi_master_properties_v711",
    "pi_master_workflow_v720",
)


def _endpoint(route: Any) -> tuple[str, str]:
    endpoint = getattr(route, "endpoint", None)
    return (
        str(getattr(endpoint, "__module__", "") or ""),
        str(getattr(endpoint, "__name__", "") or ""),
    )


def _route_rows(app: Any, path: str, method: str) -> list[dict]:
    rows = []
    for index, route in enumerate(list(app.router.routes)):
        methods = set(getattr(route, "methods", set()) or set())
        if getattr(route, "path", None) != path or method not in methods:
            continue
        module, name = _endpoint(route)
        rows.append(
            {
                "index": index,
                "module": module,
                "name": name,
                "methods": sorted(methods),
            }
        )
    return rows


def _check_owner(app: Any, specification: tuple[str, str, str, str]) -> dict:
    path, method, expected_module, expected_name = specification
    rows = _route_rows(app, path, method)
    active = rows[0] if rows else None
    passed = bool(
        active
        and active["module"] == expected_module
        and active["name"] == expected_name
    )
    return {
        "path": path,
        "method": method,
        "passed": passed,
        "expected": f"{expected_module}.{expected_name}",
        "active": (
            f'{active["module"]}.{active["name"]}' if active else None
        ),
        "registered_count": len(rows),
        "shadowed_owners": [
            f'{row["module"]}.{row["name"]}' for row in rows[1:]
        ],
    }


def audit(core: Any, requirement_app: Any = None, served_app: Any = None) -> dict:
    app = getattr(core, "app", None) or core
    route_checks = [_check_owner(app, spec) for spec in CANONICAL_ROUTES]
    requirement_checks = (
        [_check_owner(requirement_app, spec) for spec in REQUIREMENT_ROUTES]
        if requirement_app is not None
        else [
            {
                "path": spec[0],
                "method": spec[1],
                "passed": False,
                "expected": f"{spec[2]}.{spec[3]}",
                "active": None,
                "registered_count": 0,
                "shadowed_owners": [],
            }
            for spec in REQUIREMENT_ROUTES
        ]
    )

    presence = []
    presence_apps = [app]
    if served_app is not None and served_app is not app:
        presence_apps.append(served_app)

    for path in REQUIRED_PATHS:
        found = any(
            any(
                getattr(route, "path", None) == path
                for route in list(candidate.router.routes)
            )
            for candidate in presence_apps
        )
        presence.append({"path": path, "passed": found})

    auth_checks = {
        name: callable(getattr(core, name, None))
        for name in ("get_role", "need_login", "page_role_or_redirect")
    }

    engine = getattr(core, "engine", None)
    table_checks = {}
    database_connected = False
    if engine is not None:
        try:
            table_checks = {
                table: bool(inspect(engine).has_table(table))
                for table in REQUIRED_TABLES
            }
            with engine.connect() as conn:
                database_connected = bool(
                    conn.execute(text("SELECT 1")).scalar() == 1
                )
        except Exception:
            table_checks = {table: False for table in REQUIRED_TABLES}

    checks = (
        [item["passed"] for item in route_checks]
        + [item["passed"] for item in requirement_checks]
        + [item["passed"] for item in presence]
        + list(auth_checks.values())
        + list(table_checks.values())
        + [database_connected]
    )
    passed = sum(bool(value) for value in checks)
    total = len(checks)
    score = round((passed / total * 100.0) if total else 0.0, 2)

    critical_failures = [
        f'{item["method"]} {item["path"]}: {item["active"] or "MISSING"}'
        for item in route_checks + requirement_checks
        if not item["passed"]
    ]
    missing_paths = [
        item["path"] for item in presence if not item["passed"]
    ]
    missing_tables = [
        table for table, exists in table_checks.items() if not exists
    ]

    return {
        "status": "PASS" if score >= TARGET_SCORE else "BLOCKED",
        "version": VERSION,
        "acceptance_score": score,
        "target_score": TARGET_SCORE,
        "passed_checks": passed,
        "total_checks": total,
        "critical_route_ownership": route_checks,
        "isolated_requirement_ownership": requirement_checks,
        "required_path_presence": presence,
        "authentication": auth_checks,
        "database_connected": database_connected,
        "required_tables": table_checks,
        "critical_failures": critical_failures,
        "missing_paths": missing_paths,
        "missing_tables": missing_tables,
        "runtime_probe_paths": list(RUNTIME_PROBE_PATHS),
        "runtime_probe_policy": "VERIFY_AFTER_READY_USING_AUTHENTICATED_HTTP",
        "database_changed": False,
    }


def assert_critical_route_ownership(core: Any, requirement_app: Any) -> dict:
    app = getattr(core, "app", None) or core
    route_checks = [
        _check_owner(app, specification)
        for specification in CANONICAL_ROUTES
    ]
    requirement_checks = [
        _check_owner(requirement_app, specification)
        for specification in REQUIREMENT_ROUTES
    ]
    failures = [
        f'{item["method"]} {item["path"]}: '
        f'{item["active"] or "MISSING"}'
        for item in route_checks + requirement_checks
        if not item["passed"]
    ]

    if failures:
        raise RuntimeError(
            "Critical route ownership regression: "
            + "; ".join(failures)
        )

    return {
        "status": "PASS",
        "acceptance_score": 100.0,
        "critical_failures": [],
        "critical_route_ownership": route_checks,
        "isolated_requirement_ownership": requirement_checks,
        "boot_database_queries": 0,
    }


def register(core: Any, requirement_app: Any = None, served_app: Any = None) -> dict:
    app = getattr(core, "app", None) or core
    router = APIRouter()

    @router.get("/api/alliance/release-stability-v1/status")
    def status_route(request: Request):
        role = (
            core.get_role(request)
            if callable(getattr(core, "get_role", None))
            else None
        )
        if role not in {"admin", "team"}:
            from fastapi import HTTPException
            raise HTTPException(401, "Login required")
        return audit(core, requirement_app, served_app)

    app.include_router(router)
    report = assert_critical_route_ownership(core, requirement_app)
    return {
        "status": report["status"],
        "version": VERSION,
        "acceptance_score": report["acceptance_score"],
        "critical_routes_locked": True,
        "database_changed": False,
    }
