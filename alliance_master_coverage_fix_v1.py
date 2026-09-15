from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import inspect, text

VERSION = "1.0.0-MASTER-AUTHORITY-AND-AVAILABILITY-COVERAGE"
CONFIRM = "APPLY_MASTER_COVERAGE_FIX"


def _role(core, request):
    role = (
        core.get_role(request)
        if callable(getattr(core, "get_role", None))
        else None
    )
    if role != "admin":
        raise HTTPException(403, "Admin required")


def _exists(engine, table):
    try:
        return bool(inspect(engine).has_table(table))
    except Exception:
        return False


def audit(core):
    engine = core.engine

    required = (
        "pi_requirement_gate_v1191",
        "pi_master_properties_v711",
        "pi_master_workflow_v720",
    )

    missing = [name for name in required if not _exists(engine, name)]

    if missing:
        return {
            "status": "BLOCKED",
            "version": VERSION,
            "missing_tables": missing,
            "database_changed": False,
        }

    with engine.connect() as c:
        gate_total = int(
            c.execute(
                text("SELECT COUNT(*) FROM pi_requirement_gate_v1191")
            ).scalar()
            or 0
        )

        gate_active = int(
            c.execute(
                text("""
                    SELECT COUNT(*)
                    FROM pi_requirement_gate_v1191
                    WHERE COALESCE(classification,'')
                          NOT IN ('REJECTED','NOISE')
                """)
            ).scalar()
            or 0
        )

        matcher_eligible = int(
            c.execute(
                text("""
                    SELECT COUNT(*)
                    FROM pi_requirement_gate_v1191
                    WHERE matcher_eligible=TRUE
                """)
            ).scalar()
            or 0
        )

        canonical_properties = int(
            c.execute(
                text("SELECT COUNT(*) FROM pi_master_properties_v711")
            ).scalar()
            or 0
        )

        workflow_covered = int(
            c.execute(
                text("""
                    SELECT COUNT(DISTINCT p.canonical_id)
                    FROM pi_master_properties_v711 p
                    JOIN pi_master_workflow_v720 w
                      ON w.canonical_id=p.canonical_id
                    WHERE w.entity_type='PROPERTY'
                """)
            ).scalar()
            or 0
        )

        missing_property_workflow = int(
            c.execute(
                text("""
                    SELECT COUNT(*)
                    FROM pi_master_properties_v711 p
                    WHERE NOT EXISTS(
                        SELECT 1
                        FROM pi_master_workflow_v720 w
                        WHERE w.canonical_id=p.canonical_id
                          AND w.entity_type='PROPERTY'
                    )
                """)
            ).scalar()
            or 0
        )

        availability = {
            str(row["status"]): int(row["count"])
            for row in c.execute(
                text("""
                    SELECT COALESCE(w.availability_status,'UNKNOWN') AS status,
                           COUNT(*) AS count
                    FROM pi_master_properties_v711 p
                    LEFT JOIN pi_master_workflow_v720 w
                      ON w.canonical_id=p.canonical_id
                     AND w.entity_type='PROPERTY'
                    GROUP BY 1
                    ORDER BY 1
                """)
            ).mappings()
        }

        unified_manual_rows = 0
        unified_manual_linked = 0

        if _exists(engine, "pi_unified_manual_requirements"):
            unified_manual_rows = int(
                c.execute(
                    text(
                        "SELECT COUNT(*) "
                        "FROM pi_unified_manual_requirements"
                    )
                ).scalar()
                or 0
            )

            if _exists(engine, "pi_master_source_links_v711"):
                unified_manual_linked = int(
                    c.execute(
                        text("""
                            SELECT COUNT(DISTINCT source_pk)
                            FROM pi_master_source_links_v711
                            WHERE master_entity_type='REQUIREMENT'
                              AND source_table=
                                  'pi_unified_manual_requirements'
                        """)
                    ).scalar()
                    or 0
                )

        manual_unlinked = max(
            unified_manual_rows - unified_manual_linked,
            0,
        )

    return {
        "status": "READY",
        "version": VERSION,
        "requirements": {
            "gate_total": gate_total,
            "gate_active": gate_active,
            "matcher_eligible": matcher_eligible,
            "visible_master_authority":
                "pi_requirement_gate_v1191",
            "manual_unified_rows": unified_manual_rows,
            "manual_unified_linked": unified_manual_linked,
            "manual_unified_unlinked": manual_unlinked,
        },
        "properties": {
            "canonical_rows": canonical_properties,
            "workflow_covered": workflow_covered,
            "missing_workflow": missing_property_workflow,
            "availability": availability,
        },
        "legacy_pi_requirements_policy":
            "AUDIT_REQUIRED_BEFORE_PROMOTION",
        "database_changed": False,
    }


def apply(core):
    engine = core.engine
    before = audit(core)

    if before.get("status") != "READY":
        raise RuntimeError("Coverage audit is not ready")

    workflow_inserted = 0
    manual_gate_created = 0

    with engine.begin() as c:
        workflow_inserted = int(
            c.execute(
                text("""
                    INSERT INTO pi_master_workflow_v720(
                        canonical_id,
                        entity_type,
                        verification_status,
                        availability_status,
                        updated_at
                    )
                    SELECT
                        p.canonical_id,
                        'PROPERTY',
                        'UNVERIFIED',
                        'UNKNOWN',
                        NOW()
                    FROM pi_master_properties_v711 p
                    WHERE NOT EXISTS(
                        SELECT 1
                        FROM pi_master_workflow_v720 w
                        WHERE w.canonical_id=p.canonical_id
                    )
                    ON CONFLICT(canonical_id) DO NOTHING
                """)
            ).rowcount
            or 0
        )

    # Recover only records already classified by the existing requirement
    # restoration layer as MANUAL. Its evidence key makes this idempotent.
    try:
        import alliance_requirement_restore_v1235 as restore

        rows, _ = restore._source_rows(
            engine,
            "MANUAL",
            per_table=5000,
        )

        for row in rows:
            if (
                str(row.get("source_table") or "")
                != "pi_unified_manual_requirements"
            ):
                continue

            with engine.connect() as c:
                before_count = int(
                    c.execute(
                        text("""
                            SELECT COUNT(*)
                            FROM pi_requirement_gate_v1191
                            WHERE source_table=:table
                              AND source_pk=:pk
                        """),
                        {
                            "table": row["source_table"],
                            "pk": str(row["source_pk"]),
                        },
                    ).scalar()
                    or 0
                )

            restore._ensure_gate_row(engine, row)

            with engine.connect() as c:
                after_count = int(
                    c.execute(
                        text("""
                            SELECT COUNT(*)
                            FROM pi_requirement_gate_v1191
                            WHERE source_table=:table
                              AND source_pk=:pk
                        """),
                        {
                            "table": row["source_table"],
                            "pk": str(row["source_pk"]),
                        },
                    ).scalar()
                    or 0
                )

            if after_count > before_count:
                manual_gate_created += 1

    except Exception as exc:
        raise RuntimeError(
            "Manual requirement recovery failed: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    after = audit(core)

    return {
        "status": "APPLIED",
        "version": VERSION,
        "workflow_rows_inserted": workflow_inserted,
        "manual_gate_rows_created": manual_gate_created,
        "before": before,
        "after": after,
        "safety": {
            "properties_marked_available": 0,
            "default_availability": "UNKNOWN",
            "default_verification": "UNVERIFIED",
            "legacy_pi_requirements_promoted": 0,
            "automatic_matcher_eligibility": False,
        },
    }


def register(core):
    app = getattr(core, "app", None) or core
    router = APIRouter()

    @router.get("/api/alliance/master-coverage-v1/audit")
    def audit_route(request: Request):
        _role(core, request)
        return audit(core)

    @router.post("/api/alliance/master-coverage-v1/apply")
    def apply_route(
        request: Request,
        confirm: str = Query(...),
    ):
        _role(core, request)

        if confirm != CONFIRM:
            raise HTTPException(
                400,
                "Exact confirmation phrase required",
            )

        return apply(core)

    app.include_router(router)

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "automatic_apply": False,
        "properties_marked_available": False,
        "legacy_pi_requirements_promoted": False,
    }
