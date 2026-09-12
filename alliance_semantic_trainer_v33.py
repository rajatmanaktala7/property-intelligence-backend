from __future__ import annotations

import json
from typing import Any, Dict, List
from sqlalchemy import text
import alliance_requirement_brain_v3 as brain

VERSION = "3.4.0-ALL-REQUIREMENTS-INDIVIDUAL-AUTO-TRAINER"

def audit_text(raw_text: str, source: str = "AUTO_TRAINER") -> Dict[str, Any]:
    obj = brain.analyze(raw_text, source=source)
    issues: List[str] = []
    role = str((obj.get("intent") or {}).get("role") or "")
    locations = obj.get("locations") or []
    regions = obj.get("regions") or []
    search_geo = obj.get("search_geography") or {}
    asset = obj.get("asset") or {}
    tx = obj.get("transaction") or {}
    options = obj.get("requirement_options") or []

    if role == "REQUIREMENT":
        if not locations and not regions:
            issues.append("NO_GEOGRAPHY_EXTRACTED")
        if regions and not search_geo.get("candidate_locations"):
            issues.append("REGION_WITHOUT_SEARCH_GEOGRAPHY")
        if str(asset.get("primary_asset") or "") == "UNKNOWN":
            issues.append("ASSET_UNKNOWN")
        if tx.get("value") is None:
            issues.append("TRANSACTION_OPEN")
        if len(options) > 1:
            for idx, option in enumerate(options, start=1):
                if not option.get("locations"):
                    issues.append(f"COMPOUND_OPTION_{idx}_NO_LOCATION")

    score = 100
    weights = {
        "NO_GEOGRAPHY_EXTRACTED": 35,
        "REGION_WITHOUT_SEARCH_GEOGRAPHY": 30,
        "ASSET_UNKNOWN": 30,
        "TRANSACTION_OPEN": 15,
    }
    for issue in issues:
        score -= weights.get(issue, 10)

    return {
        "trainer_version": VERSION,
        "brain_version": brain.VERSION,
        "source": source,
        "quality_score": max(0, score),
        "requires_review": bool(issues),
        "issues": issues,
        "locations": [x.get("name") for x in locations],
        "regions": [x.get("name") for x in regions],
        "search_locations": list(search_geo.get("candidate_locations") or []),
        "compound_option_count": len(options),
        "matching_readiness": obj.get("matching_readiness"),
        "semantic": obj,
    }

def ensure_schema(engine) -> None:
    with engine.begin() as c:
        c.execute(text("""
            CREATE TABLE IF NOT EXISTS pi_semantic_trainer_v34_audit(
                id BIGSERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                shadow_run_id BIGINT,
                source TEXT,
                raw_text TEXT NOT NULL,
                brain_version TEXT NOT NULL,
                trainer_version TEXT NOT NULL,
                quality_score INTEGER NOT NULL,
                requires_review BOOLEAN NOT NULL,
                issues JSONB NOT NULL,
                audit_snapshot JSONB NOT NULL
            )
        """))
        c.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_semantic_trainer_v34_run_brain
            ON pi_semantic_trainer_v34_audit(shadow_run_id, brain_version)
            WHERE shadow_run_id IS NOT NULL
        """))

def record_audit(engine, raw_text: str, source: str = "AUTO_TRAINER",
                 shadow_run_id: int | None = None) -> Dict[str, Any]:
    result = audit_text(raw_text, source=source)
    ensure_schema(engine)
    params = {
        "shadow_run_id": int(shadow_run_id) if shadow_run_id is not None else None,
        "source": source,
        "raw_text": raw_text,
        "brain_version": brain.VERSION,
        "trainer_version": VERSION,
        "quality_score": result["quality_score"],
        "requires_review": result["requires_review"],
        "issues": json.dumps(result["issues"]),
        "snapshot": json.dumps(result, default=str),
    }
    with engine.begin() as c:
        row_id = c.execute(text("""
            INSERT INTO pi_semantic_trainer_v34_audit(
                shadow_run_id, source, raw_text, brain_version,
                trainer_version, quality_score, requires_review,
                issues, audit_snapshot
            ) VALUES(
                :shadow_run_id, :source, :raw_text, :brain_version,
                :trainer_version, :quality_score, :requires_review,
                CAST(:issues AS JSONB), CAST(:snapshot AS JSONB)
            )
            ON CONFLICT DO NOTHING
            RETURNING id
        """), params).scalar()
    result["audit_id"] = int(row_id) if row_id is not None else None
    result["persisted"] = True
    return result

def audit_all_missing(engine, batch_size: int = 500) -> Dict[str, Any]:
    ensure_schema(engine)
    total = 0
    review = 0
    failures = []
    while True:
        with engine.connect() as c:
            rows = c.execute(text("""
                SELECT s.id, s.source, s.raw_text
                FROM pi_semantic_shadow_v3_runs s
                LEFT JOIN pi_semantic_trainer_v34_audit a
                  ON a.shadow_run_id=s.id
                 AND a.brain_version=:brain_version
                WHERE a.id IS NULL
                ORDER BY s.id ASC
                LIMIT :limit
            """), {
                "brain_version": brain.VERSION,
                "limit": max(1, min(int(batch_size), 2000)),
            }).mappings().all()
        if not rows:
            break
        for row in rows:
            try:
                res = record_audit(
                    engine,
                    row["raw_text"],
                    source=str(row.get("source") or "HISTORICAL_SHADOW"),
                    shadow_run_id=int(row["id"]),
                )
                total += 1
                if res["requires_review"]:
                    review += 1
            except Exception as exc:
                failures.append({
                    "shadow_run_id": int(row["id"]),
                    "error": f"{type(exc).__name__}: {exc}",
                })
        if failures:
            break
    return {
        "status": "OK" if not failures else "PARTIAL",
        "trainer_version": VERSION,
        "brain_version": brain.VERSION,
        "audited_individually": total,
        "requires_review": review,
        "failures": failures[:50],
    }

def status(engine) -> Dict[str, Any]:
    ensure_schema(engine)
    with engine.connect() as c:
        total_shadow = int(c.execute(text(
            "SELECT COUNT(*) FROM pi_semantic_shadow_v3_runs"
        )).scalar_one())
        row = dict(c.execute(text("""
            SELECT
                COUNT(*) AS audited,
                COUNT(*) FILTER (WHERE requires_review) AS requires_review
            FROM pi_semantic_trainer_v34_audit
            WHERE brain_version=:brain_version
        """), {"brain_version": brain.VERSION}).mappings().one())
    return {
        "status": "OK",
        "trainer_version": VERSION,
        "brain_version": brain.VERSION,
        "total_shadow_requirements": total_shadow,
        **row,
        "complete": int(row["audited"] or 0) >= total_shadow,
    }
