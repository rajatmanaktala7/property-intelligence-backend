from __future__ import annotations

import json
from typing import Any, Dict

from sqlalchemy import text

import alliance_requirement_brain_v3 as brain

VERSION = "3.1.0-SHADOW-CAPTURE-REVIEW-MODE"
MODE = "REVIEW"


def ensure_schema(engine) -> None:
    with engine.begin() as c:
        c.execute(text("""
            CREATE TABLE IF NOT EXISTS pi_semantic_shadow_v3_runs(
                id BIGSERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                source TEXT NOT NULL DEFAULT 'DEAL_MATCH',
                raw_text TEXT NOT NULL,
                v2_snapshot JSONB,
                v3_snapshot JSONB NOT NULL,
                comparison JSONB NOT NULL,
                brain_version TEXT NOT NULL,
                reviewed BOOLEAN NOT NULL DEFAULT FALSE,
                human_correction JSONB,
                notes TEXT
            )
        """))
        for stmt in (
            "ALTER TABLE pi_semantic_shadow_v3_runs ADD COLUMN IF NOT EXISTS review_decision TEXT",
            "ALTER TABLE pi_semantic_shadow_v3_runs ADD COLUMN IF NOT EXISTS reviewer TEXT",
            "ALTER TABLE pi_semantic_shadow_v3_runs ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ",
            "ALTER TABLE pi_semantic_shadow_v3_runs ADD COLUMN IF NOT EXISTS approved_snapshot JSONB",
            "ALTER TABLE pi_semantic_shadow_v3_runs ADD COLUMN IF NOT EXISTS correction_reason TEXT",
        ):
            c.execute(text(stmt))
        c.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_semantic_shadow_v3_created
            ON pi_semantic_shadow_v3_runs(created_at DESC)
        """))


def _v2_snapshot(raw_text: str) -> Dict[str, Any]:
    try:
        import alliance_requirement_intelligence_os_v2 as v2
        _normalized, req, intel = v2.build_canonical_requirement(raw_text)
        return {
            "version": getattr(v2, "VERSION", None),
            "role": (intel.get("intent") or {}).get("role"),
            "transaction": req.get("transaction"),
            "family": req.get("family"),
            "subtype": req.get("subtype"),
            "primary_locations": req.get("primary_locations"),
            "location": req.get("location"),
            "area_min_sqft": req.get("area_min_sqft"),
            "area_max_sqft": req.get("area_max_sqft"),
            "budget_max": req.get("budget_max"),
            "hard_constraints": intel.get("hard_constraints"),
            "preferences": intel.get("preferences"),
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def compare(v2: Dict[str, Any], v3: Dict[str, Any]) -> Dict[str, Any]:
    v3_locations = [
        x["name"] for x in v3.get("locations") or []
        if x.get("constraint") != "EXCLUDED"
    ]
    v3_transaction = (v3.get("transaction") or {}).get("value")
    v3_asset = (v3.get("asset") or {}).get("primary_asset")
    return {
        "transaction_changed": v2.get("transaction") != v3_transaction,
        "asset_changed": (v2.get("subtype") or v2.get("family")) != v3_asset,
        "location_count_v2": len(
            v2.get("primary_locations")
            or ([v2.get("location")] if v2.get("location") else [])
        ),
        "location_count_v3": len(v3_locations),
        "locations_v3": v3_locations,
        "unknown_transaction_preserved": v3_transaction is None,
        "v3_matching_readiness": v3.get("matching_readiness"),
    }


def analyze(raw_text: str, source: str = "DEAL_MATCH") -> Dict[str, Any]:
    v2 = _v2_snapshot(raw_text)
    v3 = brain.analyze(raw_text, source=source)
    return {
        "version": VERSION,
        "mode": MODE,
        "production_behavior_changed": False,
        "v2": v2,
        "v3": v3,
        "comparison": compare(v2, v3),
    }


def capture(engine, raw_text: str, source: str = "DEAL_MATCH") -> Dict[str, Any]:
    result = analyze(raw_text, source=source)
    try:
        ensure_schema(engine)
        with engine.begin() as c:
            inserted = c.execute(text("""
                INSERT INTO pi_semantic_shadow_v3_runs(
                    source, raw_text, v2_snapshot, v3_snapshot,
                    comparison, brain_version
                ) VALUES(
                    :source, :raw_text,
                    CAST(:v2 AS JSONB),
                    CAST(:v3 AS JSONB),
                    CAST(:comparison AS JSONB),
                    :brain_version
                )
                RETURNING id
            """), {
                "source": source,
                "raw_text": raw_text,
                "v2": json.dumps(result["v2"], default=str),
                "v3": json.dumps(result["v3"], default=str),
                "comparison": json.dumps(result["comparison"], default=str),
                "brain_version": brain.VERSION,
            }).scalar_one()
        result["persisted"] = True
        result["review_id"] = int(inserted)
        result["review_url"] = f"/semantic-v3/review/{inserted}"
    except Exception as exc:
        result["persisted"] = False
        result["persistence_error"] = f"{type(exc).__name__}: {exc}"
    return result


def register(core) -> Dict[str, Any]:
    app = core.app
    registered = []

    try:
        ensure_schema(core.engine)
        schema_status = "READY"
    except Exception as exc:
        schema_status = f"ERROR:{type(exc).__name__}:{exc}"

    paths = {getattr(r, "path", None) for r in app.router.routes}

    if "/api/semantic-v3/status" not in paths:
        @app.get("/api/semantic-v3/status")
        def semantic_v3_status():
            return {
                "status": "OK",
                "version": VERSION,
                "brain_version": brain.VERSION,
                "mode": MODE,
                "production_behavior_changed": False,
                "production_matching_brain": "V2.3",
                "v3_global_authoritative": False,
                "schema_status": schema_status,
            }
        registered.append("/api/semantic-v3/status")

    if "/api/semantic-v3/analyze" not in paths:
        @app.get("/api/semantic-v3/analyze")
        def semantic_v3_analyze(q: str):
            return analyze(q, source="MANUAL_REVIEW_API")
        registered.append("/api/semantic-v3/analyze")

    if "/api/semantic-v3/recent" not in paths:
        @app.get("/api/semantic-v3/recent")
        def semantic_v3_recent(limit: int = 20):
            limit = max(1, min(int(limit), 100))
            ensure_schema(core.engine)
            with core.engine.connect() as c:
                rows = c.execute(text("""
                    SELECT id, created_at, source, brain_version,
                           comparison, reviewed, review_decision,
                           reviewer, reviewed_at
                    FROM pi_semantic_shadow_v3_runs
                    ORDER BY id DESC
                    LIMIT :limit
                """), {"limit": limit}).mappings().all()
            return {
                "status": "OK",
                "mode": MODE,
                "count": len(rows),
                "rows": [dict(r) for r in rows],
            }
        registered.append("/api/semantic-v3/recent")

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "brain_version": brain.VERSION,
        "mode": MODE,
        "registered_routes": registered,
        "schema_status": schema_status,
        "production_behavior_changed": False,
    }
