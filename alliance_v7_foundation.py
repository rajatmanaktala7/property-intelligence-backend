from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import Query
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import inspect, text

VERSION = "7.0.0-PHASE0-PHASE1-FOUNDATION"

EXPECTED_PROPERTY_BRAIN_TABLES = [
    "pb_raw_evidence",
    "pb_line_tags",
    "pb_bursts",
    "pb_segments",
    "pb_extractions",
    "pb_location_aliases",
    "pb_canonical_properties",
    "pb_property_sources",
    "pb_requirements",
    "pb_corrections",
    "pb_feedback_outcomes",
    "pb_review_queue",
]

FOUNDATION_DDL = [
    """
    CREATE TABLE IF NOT EXISTS alliance_v7_system_state(
        key TEXT PRIMARY KEY,
        value JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS alliance_v7_audit_snapshots(
        id BIGSERIAL PRIMARY KEY,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        version TEXT NOT NULL,
        payload JSONB NOT NULL
    )
    """,
    """
    ALTER TABLE pb_canonical_properties
    ADD COLUMN IF NOT EXISTS ai_understanding JSONB NOT NULL DEFAULT '{}'::jsonb
    """,
    """
    ALTER TABLE pb_canonical_properties
    ADD COLUMN IF NOT EXISTS data_quality_status TEXT NOT NULL DEFAULT 'UNDER_REVIEW'
    """,
    """
    ALTER TABLE pb_canonical_properties
    ADD COLUMN IF NOT EXISTS suitable_uses JSONB NOT NULL DEFAULT '[]'::jsonb
    """,
    """
    ALTER TABLE pb_canonical_properties
    ADD COLUMN IF NOT EXISTS negotiability BOOLEAN
    """,
    """
    ALTER TABLE pb_canonical_properties
    ADD COLUMN IF NOT EXISTS source_evidence JSONB NOT NULL DEFAULT '{}'::jsonb
    """,
    """
    ALTER TABLE pb_canonical_properties
    ADD COLUMN IF NOT EXISTS entity_version INTEGER NOT NULL DEFAULT 1
    """,
    """
    ALTER TABLE pb_requirements
    ADD COLUMN IF NOT EXISTS ai_understanding JSONB NOT NULL DEFAULT '{}'::jsonb
    """,
    """
    ALTER TABLE pb_requirements
    ADD COLUMN IF NOT EXISTS constraint_model JSONB NOT NULL DEFAULT '{}'::jsonb
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_pb_can_quality
    ON pb_canonical_properties(data_quality_status)
    """,
]


def _table_exists(engine, name: str) -> bool:
    try:
        return inspect(engine).has_table(name)
    except Exception:
        return False


def _safe_count(engine, table: str):
    if not _table_exists(engine, table):
        return None
    try:
        with engine.connect() as c:
            return int(c.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0)
    except Exception:
        return None


def _scalar(engine, sql: str, params: Dict[str, Any] | None = None):
    try:
        with engine.connect() as c:
            return c.execute(text(sql), params or {}).scalar()
    except Exception:
        return None


def _rows(engine, sql: str, params: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    try:
        with engine.connect() as c:
            return [dict(x) for x in c.execute(text(sql), params or {}).mappings().all()]
    except Exception:
        return []


def foundation_status(engine) -> Dict[str, Any]:
    existing = {t: _table_exists(engine, t) for t in EXPECTED_PROPERTY_BRAIN_TABLES}

    v7_tables = {
        "alliance_v7_system_state": _table_exists(engine, "alliance_v7_system_state"),
        "alliance_v7_audit_snapshots": _table_exists(engine, "alliance_v7_audit_snapshots"),
    }

    columns = {}

    if _table_exists(engine, "pb_canonical_properties"):
        try:
            columns["pb_canonical_properties"] = sorted(
                x["name"] for x in inspect(engine).get_columns("pb_canonical_properties")
            )
        except Exception:
            columns["pb_canonical_properties"] = []

    if _table_exists(engine, "pb_requirements"):
        try:
            columns["pb_requirements"] = sorted(
                x["name"] for x in inspect(engine).get_columns("pb_requirements")
            )
        except Exception:
            columns["pb_requirements"] = []

    required_pb_columns = {
        "ai_understanding",
        "data_quality_status",
        "suitable_uses",
        "negotiability",
        "source_evidence",
        "entity_version",
    }

    required_req_columns = {
        "ai_understanding",
        "constraint_model",
    }

    present_pb = set(columns.get("pb_canonical_properties", []))
    present_req = set(columns.get("pb_requirements", []))

    ready = (
        all(existing.values())
        and all(v7_tables.values())
        and required_pb_columns.issubset(present_pb)
        and required_req_columns.issubset(present_req)
    )

    return {
        "version": VERSION,
        "status": "READY" if ready else "INSTALL_REQUIRED",
        "property_brain_tables": existing,
        "v7_tables": v7_tables,
        "missing_property_columns": sorted(required_pb_columns - present_pb),
        "missing_requirement_columns": sorted(required_req_columns - present_req),
        "non_destructive": True,
        "existing_property_brain_reused": True,
    }


def install_foundation(engine) -> Dict[str, Any]:
    missing_pb = [t for t in EXPECTED_PROPERTY_BRAIN_TABLES if not _table_exists(engine, t)]

    if missing_pb:
        try:
            from property_brain.db import setup
            setup(engine)
        except Exception as exc:
            return {
                "status": "ERROR",
                "version": VERSION,
                "message": "Existing Property Brain foundation could not be initialized.",
                "missing_tables": missing_pb,
                "error": f"{type(exc).__name__}: {exc}",
            }

    executed = []

    try:
        with engine.begin() as c:
            for ddl in FOUNDATION_DDL:
                c.execute(text(ddl))
                executed.append(ddl.strip().splitlines()[0][:120])

            c.execute(
                text("""
                    INSERT INTO alliance_v7_system_state(key, value, updated_at)
                    VALUES(
                        'foundation',
                        CAST(:payload AS jsonb),
                        NOW()
                    )
                    ON CONFLICT(key)
                    DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                """),
                {
                    "payload": (
                        '{"version":"%s","phase":"PHASE_0_PHASE_1",'
                        '"status":"INSTALLED","non_destructive":true}'
                    ) % VERSION
                },
            )

    except Exception as exc:
        return {
            "status": "ERROR",
            "version": VERSION,
            "error": f"{type(exc).__name__}: {exc}",
            "executed_before_error": executed,
        }

    return {
        "status": "INSTALLED",
        "version": VERSION,
        "executed_statements": len(executed),
        "foundation": foundation_status(engine),
        "next_phase": "PHASE_2_PROPERTY_AI",
    }


def audit(engine, save_snapshot: bool = False) -> Dict[str, Any]:
    counts = {}

    for table in [
        "pi_properties",
        "pi_whatsapp_property_master",
        *EXPECTED_PROPERTY_BRAIN_TABLES,
    ]:
        counts[table] = _safe_count(engine, table)

    canonical = {}

    if _table_exists(engine, "pb_canonical_properties"):
        canonical = {
            "total": _safe_count(engine, "pb_canonical_properties"),
            "missing_location": _scalar(
                engine,
                """
                SELECT COUNT(*)
                FROM pb_canonical_properties
                WHERE locality IS NULL OR BTRIM(locality) = ''
                """
            ),
            "missing_transaction": _scalar(
                engine,
                """
                SELECT COUNT(*)
                FROM pb_canonical_properties
                WHERE transaction_type IS NULL OR BTRIM(transaction_type) = ''
                """
            ),
            "missing_property_family": _scalar(
                engine,
                """
                SELECT COUNT(*)
                FROM pb_canonical_properties
                WHERE property_family IS NULL OR BTRIM(property_family) = ''
                """
            ),
            "missing_area": _scalar(
                engine,
                """
                SELECT COUNT(*)
                FROM pb_canonical_properties
                WHERE area_sqft IS NULL OR area_sqft <= 0
                """
            ),
            "missing_commercial_terms": _scalar(
                engine,
                """
                SELECT COUNT(*)
                FROM pb_canonical_properties
                WHERE COALESCE(rent_value,0) <= 0
                  AND COALESCE(sale_price_value,0) <= 0
                """
            ),
            "unverified": _scalar(
                engine,
                """
                SELECT COUNT(*)
                FROM pb_canonical_properties
                WHERE UPPER(COALESCE(verification_status,'')) <> 'VERIFIED'
                """
            ),
            "duplicate_fingerprints": _scalar(
                engine,
                """
                SELECT COUNT(*)
                FROM (
                    SELECT fingerprint
                    FROM pb_canonical_properties
                    WHERE fingerprint IS NOT NULL
                      AND BTRIM(fingerprint) <> ''
                    GROUP BY fingerprint
                    HAVING COUNT(*) > 1
                ) d
                """
            ),
        }

    review = {}

    if _table_exists(engine, "pb_review_queue"):
        review = {
            "open_total": _scalar(
                engine,
                "SELECT COUNT(*) FROM pb_review_queue WHERE status='OPEN'"
            ),
            "by_queue_type": _rows(
                engine,
                """
                SELECT queue_type, COUNT(*) AS count
                FROM pb_review_queue
                WHERE status='OPEN'
                GROUP BY queue_type
                ORDER BY count DESC
                """
            ),
            "top_reasons": _rows(
                engine,
                """
                SELECT reason, COUNT(*) AS count
                FROM pb_review_queue
                WHERE status='OPEN'
                GROUP BY reason
                ORDER BY count DESC
                LIMIT 20
                """
            ),
        }

    extractions = {}

    if _table_exists(engine, "pb_extractions"):
        extractions = {
            "by_classification": _rows(
                engine,
                """
                SELECT classification, COUNT(*) AS count
                FROM pb_extractions
                GROUP BY classification
                ORDER BY count DESC
                """
            ),
            "by_gate_outcome": _rows(
                engine,
                """
                SELECT COALESCE(gate_outcome,'UNKNOWN') AS gate_outcome,
                       COUNT(*) AS count
                FROM pb_extractions
                GROUP BY COALESCE(gate_outcome,'UNKNOWN')
                ORDER BY count DESC
                """
            ),
        }

    payload = {
        "version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "phase": "PHASE_0_AUDIT",
        "counts": counts,
        "canonical_quality": canonical,
        "review_queue": review,
        "extractions": extractions,
        "foundation": foundation_status(engine),
        "interpretation": {
            "goal": "Measure missing inventory and false-negative risk before Property AI cutover.",
            "raw_data_deleted": False,
            "matcher_modified": False,
            "whatsapp_live_modified": False,
        },
    }

    if save_snapshot and _table_exists(engine, "alliance_v7_audit_snapshots"):
        try:
            import json

            with engine.begin() as c:
                c.execute(
                    text("""
                        INSERT INTO alliance_v7_audit_snapshots(version, payload)
                        VALUES(:v, CAST(:p AS jsonb))
                    """),
                    {
                        "v": VERSION,
                        "p": json.dumps(payload, default=str),
                    },
                )

            payload["snapshot_saved"] = True

        except Exception as exc:
            payload["snapshot_saved"] = False
            payload["snapshot_error"] = f"{type(exc).__name__}: {exc}"

    return payload


def review_sample(engine, limit: int = 100) -> Dict[str, Any]:
    limit = max(1, min(int(limit), 250))

    if not _table_exists(engine, "pb_review_queue"):
        return {
            "version": VERSION,
            "status": "NO_REVIEW_QUEUE",
            "rows": [],
        }

    rows = _rows(
        engine,
        """
        SELECT
            review_id::text AS review_id,
            queue_type,
            target_type,
            target_id::text AS target_id,
            reason,
            status,
            created_at,
            payload
        FROM pb_review_queue
        WHERE status = 'OPEN'
        ORDER BY created_at DESC
        LIMIT :lim
        """,
        {"lim": limit},
    )

    return {
        "version": VERSION,
        "status": "OK",
        "count": len(rows),
        "rows": rows,
        "purpose": "Ground-truth benchmark sample for Phase 2 Property AI.",
    }


def _page() -> str:
    return """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alliance AI V7 Foundation</title>
<style>
body{font-family:Arial,sans-serif;background:#f5f7fb;color:#172033;margin:0;padding:30px}
.card{max-width:900px;margin:auto;background:white;border-radius:16px;padding:28px;box-shadow:0 10px 30px rgba(0,0,0,.08)}
code{background:#eef2f7;padding:3px 6px;border-radius:6px}
a{color:#2457d6}
li{margin:10px 0}
</style>
</head>
<body>
<div class="card">
<h1>Alliance Property Intelligence AI V7</h1>
<p><b>Phase 0 + Phase 1 Foundation</b></p>
<p>This module reuses the existing property_brain canonical data model.</p>
<ul>
<li><a href="/api/v7/foundation/status">Foundation status</a></li>
<li><a href="/api/v7/audit">Live Phase 0 audit</a></li>
<li><a href="/api/v7/audit/sample?limit=100">100-record review sample</a></li>
</ul>
</div>
</body>
</html>
"""


def register(core):
    app = core.app
    engine = core.engine

    if getattr(app.state, "alliance_v7_foundation_registered", False):
        return {
            "status": "ALREADY_REGISTERED",
            "version": VERSION,
            "route": "/v7/foundation",
        }

    @app.get("/v7/foundation", response_class=HTMLResponse)
    def v7_foundation_page():
        return HTMLResponse(_page())

    @app.get("/api/v7/foundation/status")
    def v7_foundation_status():
        return JSONResponse(foundation_status(engine))

    @app.post("/api/v7/foundation/install")
    def v7_foundation_install():
        result = install_foundation(engine)
        code = 200 if result.get("status") != "ERROR" else 500
        return JSONResponse(result, status_code=code)

    @app.get("/api/v7/audit")
    def v7_audit(save_snapshot: bool = Query(False)):
        return JSONResponse(audit(engine, save_snapshot=save_snapshot))

    @app.get("/api/v7/audit/sample")
    def v7_audit_sample(limit: int = Query(100, ge=1, le=250)):
        return JSONResponse(review_sample(engine, limit=limit))

    app.state.alliance_v7_foundation_registered = True

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": "/v7/foundation",
        "startup_ddl": False,
        "non_destructive": True,
        "existing_property_brain_reused": True,
    }
