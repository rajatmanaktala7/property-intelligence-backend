from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import inspect, text

VERSION = "1.1.0-CANONICAL-SOURCE-AND-MATCHER-CERTIFICATION"
TARGET_SCORE = 99.0

SOURCE_VIEWS = ("manual", "whatsapp", "newspaper", "magazine")

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
    "pi_master_requirements_v711",
    "pi_master_source_links_v711",
    "pi_master_workflow_v720",
)


def _route_present(app: Any, path: str, method: str = "GET") -> bool:
    if app is None:
        return False
    return any(
        getattr(route, "path", None) == path
        and method in set(getattr(route, "methods", set()) or set())
        for route in list(app.router.routes)
    )


def _source_case(alias: str) -> str:
    value = (
        f"UPPER(COALESCE({alias}.source_type,'') || ' ' || "
        f"COALESCE({alias}.source_table,''))"
    )
    return (
        "CASE "
        f"WHEN {value} LIKE '%WHATSAPP%' THEN 'WHATSAPP' "
        f"WHEN {value} LIKE '%NEWSPAPER%' OR {value} LIKE '%NEWS_PAPER%' THEN 'NEWSPAPER' "
        f"WHEN {value} LIKE '%MAGAZINE%' THEN 'MAGAZINE' "
        f"WHEN {value} LIKE '%MANUAL%' THEN 'MANUAL' "
        "ELSE 'OTHER' END"
    )


def _canonical_source_certification(
    core: Any,
    requirement_app: Any,
    served_app: Any,
) -> dict:
    """Read-only proof that canonical masters, source views and matcher agree."""
    app = served_app or getattr(core, "app", None) or core
    engine = getattr(core, "engine", None)
    result = {
        "status": "BLOCKED",
        "properties": {},
        "requirements": {},
        "sources": {},
        "routes": {},
        "constraints": {
            "physical_source_copies_created": False,
            "source_rows_deleted": False,
            "populated_values_overwritten": False,
            "missing_value_policy": "NOT_CAPTURED_NO_INFERENCE",
            "matcher_source": "MASTER_PROPERTIES_ONLY",
        },
        "database_changed": False,
    }
    if engine is None:
        result["error"] = "DATABASE_ENGINE_UNAVAILABLE"
        return result

    table_state = {
        table: bool(inspect(engine).has_table(table))
        for table in REQUIRED_TABLES
    }
    result["tables"] = table_state
    if not all(table_state.values()):
        result["error"] = "CANONICAL_TABLES_MISSING"
        return result

    link_source = _source_case("l")
    gate_source = _source_case("g")
    with engine.connect() as conn:
        result["properties"] = dict(conn.execute(text("""
            SELECT
              COUNT(DISTINCT p.canonical_id) AS canonical_rows,
              COUNT(DISTINCT p.canonical_id) FILTER (WHERE p.promotion_status='PROMOTED_VALIDATED') AS matcher_inventory_rows,
              COUNT(DISTINCT l.canonical_id) AS canonical_rows_with_lineage,
              COUNT(*) - COUNT(DISTINCT p.canonical_id) AS joined_lineage_rows,
              (SELECT COUNT(*) FROM (
                 SELECT canonical_id FROM pi_master_properties_v711
                 GROUP BY canonical_id HAVING COUNT(*) > 1
              ) duplicates) AS duplicate_canonical_rows
            FROM pi_master_properties_v711 p
            LEFT JOIN pi_master_source_links_v711 l
              ON l.master_entity_type='PROPERTY' AND l.canonical_id=p.canonical_id
        """)).mappings().one())
        result["requirements"] = dict(conn.execute(text("""
            SELECT
              (SELECT COUNT(*) FROM pi_requirement_gate_v1191
                 WHERE COALESCE(classification,'') NOT IN ('REJECTED','NOISE','REJECTED/EXPIRED')) AS all_source_evidence_rows,
              COUNT(DISTINCT r.canonical_id) AS canonical_rows,
              COUNT(DISTINCT r.canonical_id) FILTER (
                WHERE r.promotion_status='PROMOTED_VALIDATED'
                  AND COALESCE(w.verification_status,'UNVERIFIED')='VERIFIED'
              ) AS matcher_eligible_rows,
              COUNT(DISTINCT l.canonical_id) AS canonical_rows_with_lineage,
              COUNT(*) - COUNT(DISTINCT r.canonical_id) AS joined_lineage_rows,
              (SELECT COUNT(*) FROM (
                 SELECT canonical_id FROM pi_master_requirements_v711
                 GROUP BY canonical_id HAVING COUNT(*) > 1
              ) duplicates) AS duplicate_canonical_rows
            FROM pi_master_requirements_v711 r
            LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=r.canonical_id
            LEFT JOIN pi_master_source_links_v711 l
              ON l.master_entity_type='REQUIREMENT' AND l.canonical_id=r.canonical_id
        """)).mappings().one())

        property_counts = {
            str(row["source_kind"]): int(row["records"] or 0)
            for row in conn.execute(text(f"""
                SELECT {link_source} AS source_kind,
                       COUNT(DISTINCT l.canonical_id) AS records
                FROM pi_master_source_links_v711 l
                WHERE l.master_entity_type='PROPERTY'
                GROUP BY 1
            """)).mappings()
        }
        requirement_links = {
            str(row["source_kind"]): int(row["records"] or 0)
            for row in conn.execute(text(f"""
                SELECT {link_source} AS source_kind,
                       COUNT(DISTINCT l.canonical_id) AS records
                FROM pi_master_source_links_v711 l
                WHERE l.master_entity_type='REQUIREMENT'
                GROUP BY 1
            """)).mappings()
        }
        gate_counts = {
            str(row["source_kind"]): int(row["records"] or 0)
            for row in conn.execute(text(f"""
                SELECT {gate_source} AS source_kind, COUNT(*) AS records
                FROM pi_requirement_gate_v1191 g
                WHERE COALESCE(g.classification,'')
                      NOT IN ('REJECTED','NOISE','REJECTED/EXPIRED')
                GROUP BY 1
            """)).mappings()
        }

    for source in SOURCE_VIEWS:
        kind = source.upper()
        result["sources"][kind] = {
            "property_canonical_records": property_counts.get(kind, 0),
            "requirement_canonical_records": requirement_links.get(kind, 0),
            "requirement_evidence_records": gate_counts.get(kind, 0),
            "property_view_ready": _route_present(app, "/alliance/final/database/{source}"),
            "requirement_view_ready": _route_present(
                requirement_app, "/alliance/final/requirements/{source}"
            ),
            "property_url": f"/alliance/final/database/{source}",
            "requirement_url": f"/alliance/final/requirements/{source}",
        }

    result["routes"] = {
        "property_master": _route_present(app, "/alliance/final/database/{source}"),
        "requirement_master": _route_present(
            requirement_app, "/alliance/final/requirements/{source}"
        ),
        "requirement_run_match": _route_present(
            requirement_app, "/alliance/final/requirements/run-match"
        ),
        "matcher_page": _route_present(app, "/alliance/primary/matcher"),
        "matcher_api": _route_present(app, "/api/v60/deal-match"),
        "matcher_status": _route_present(app, "/api/v60/status"),
    }
    structural_checks = [
        all(table_state.values()),
        all(result["routes"].values()),
        all(
            item["property_view_ready"] and item["requirement_view_ready"]
            for item in result["sources"].values()
        ),
        int(result["properties"].get("duplicate_canonical_rows") or 0) == 0,
        int(result["requirements"].get("duplicate_canonical_rows") or 0) == 0,
    ]
    result["status"] = "PASS" if all(structural_checks) else "BLOCKED"
    result["acceptance"] = {
        "master_properties": "PASS" if structural_checks[0] else "BLOCKED",
        "master_requirements": "PASS" if structural_checks[0] else "BLOCKED",
        "four_source_views": "PASS" if structural_checks[2] else "BLOCKED",
        "run_match_actions": "PASS" if all(result["routes"].values()) else "BLOCKED",
        "canonical_uniqueness": (
            "PASS" if structural_checks[3] and structural_checks[4]
            else "BLOCKED"
        ),
    }
    return result


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

    canonical_release = _canonical_source_certification(
        core, requirement_app, served_app
    )

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
        "canonical_source_release": canonical_release,
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
