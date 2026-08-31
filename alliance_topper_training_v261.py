from __future__ import annotations

import copy
import json
import random
import re
from collections import Counter
from typing import Any, Dict, List

from fastapi import Body, Query
from fastapi.responses import JSONResponse
from sqlalchemy import inspect, text

import alliance_ai_academy_v260 as v260
import alliance_property_controlled_writer_v259 as v259
import alliance_property_evidence_grammar_v257b as v257b

VERSION = "2.6.1-ALLIANCE-TOPPER-TRAINING-LAB"
MODE = "ADVERSARIAL_REGRESSION_PLUS_REAL_DATA_GOLD_LAB"

DDL = [
    """
    CREATE TABLE IF NOT EXISTS alliance_v261_gold_cases(
        gold_id BIGSERIAL PRIMARY KEY,
        case_key TEXT UNIQUE NOT NULL,
        skill TEXT NOT NULL,
        severity TEXT NOT NULL DEFAULT 'NORMAL',
        source_candidate JSONB NOT NULL,
        canonical_payload JSONB NOT NULL,
        offer_payload JSONB NOT NULL,
        expected_pass BOOLEAN NOT NULL,
        expected_blockers JSONB NOT NULL DEFAULT '[]'::jsonb,
        reviewed_by TEXT NOT NULL,
        active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS alliance_v261_exam_runs(
        run_id BIGSERIAL PRIMARY KEY,
        version TEXT NOT NULL,
        exam_type TEXT NOT NULL,
        total INTEGER NOT NULL,
        passed INTEGER NOT NULL,
        failed INTEGER NOT NULL,
        critical_failures INTEGER NOT NULL,
        score NUMERIC NOT NULL,
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
]

def _table_exists(engine, name: str) -> bool:
    try:
        return inspect(engine).has_table(name)
    except Exception:
        return False

def _mutate_text(text_value: str, variant: int) -> str:
    s = str(text_value or "")
    if variant == 0:
        return s.upper()
    if variant == 1:
        return s.lower()
    if variant == 2:
        return " | ".join(part.strip() for part in re.split(r"\s{2,}|\|", s) if part.strip()) or s
    if variant == 3:
        return re.sub(r"\s+", "  ", s)
    if variant == 4:
        return s.replace(" LAC", "LAC").replace(" SQFT", "SQ FT").replace(" SYDS", " YARDS")
    return s

def _evaluate_case(case: Dict[str, Any], tutor_ok: bool = True) -> Dict[str, Any]:
    gate = v259._candidate_gate(
        dict(case["source"]),
        dict(case["canonical"]),
        dict(case["offer"]),
        tutor_ok=tutor_ok,
    )
    expected = set(case.get("expected_blockers") or [])
    actual = set(gate.get("blockers") or [])
    behavior_ok = bool(gate.get("passed")) == bool(case.get("expect_pass"))
    blockers_ok = expected.issubset(actual)
    passed = bool(behavior_ok and blockers_ok)
    return {
        "case_key": case["case_key"],
        "skill": case.get("skill"),
        "severity": case.get("severity", "NORMAL"),
        "passed": passed,
        "expected_pass": bool(case.get("expect_pass")),
        "actual_pass": bool(gate.get("passed")),
        "missing_expected_blockers": sorted(expected - actual),
        "actual_blockers": sorted(actual),
    }

def _academy_plus_adversarial(engine=None, variants_per_case: int = 5) -> Dict[str, Any]:
    base_exam = v260._academy_exam(engine)
    tutor_ok = bool((base_exam.get("legacy_tutor_gate") or {}).get("production_gate_passed", True))
    results = []

    for base in v260.ACADEMY_CASES:
        results.append(_evaluate_case(base, tutor_ok=tutor_ok))
        for variant in range(variants_per_case):
            c = copy.deepcopy(base)
            c["case_key"] = f"{base['case_key']}__ADV_{variant+1}"
            src = dict(c["source"])
            src["own_text_redacted"] = _mutate_text(src.get("own_text_redacted", ""), variant)
            c["source"] = src
            results.append(_evaluate_case(c, tutor_ok=tutor_ok))

    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    critical_failures = sum(
        1 for r in results
        if not r["passed"] and r["severity"] == "CRITICAL"
    )
    score = round((passed * 100.0 / total), 2) if total else 0.0
    graduated = bool(score == 100.0 and critical_failures == 0 and base_exam.get("graduation_gate_passed"))

    return {
        "status": "PASS" if graduated else "TRAINING_REQUIRED",
        "version": VERSION,
        "base_academy_score": base_exam.get("score"),
        "base_academy_passed": bool(base_exam.get("graduation_gate_passed")),
        "variants_per_case": variants_per_case,
        "total_exam_cases": total,
        "passed": passed,
        "failed": total - passed,
        "score": score,
        "critical_failures": critical_failures,
        "topper_gate_passed": graduated,
        "failed_cases": [r for r in results if not r["passed"]][:100],
        "writes_performed": 0,
        "meaning": "100% means all known Academy plus adversarial regression cases passed. Unknown future cases still require gold review.",
    }

def _gold_exam(engine) -> Dict[str, Any]:
    if not _table_exists(engine, "alliance_v261_gold_cases"):
        return {
            "status": "INSTALL_REQUIRED",
            "version": VERSION,
            "gold_cases": 0,
            "score": None,
            "critical_failures": None,
            "gold_gate_passed": False,
        }

    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT case_key, skill, severity, source_candidate,
                   canonical_payload, offer_payload, expected_pass,
                   expected_blockers
            FROM alliance_v261_gold_cases
            WHERE active = TRUE
            ORDER BY gold_id
        """)).mappings().all()

    results = []
    for row in rows:
        case = {
            "case_key": row["case_key"],
            "skill": row["skill"],
            "severity": row["severity"],
            "source": dict(row["source_candidate"]),
            "canonical": dict(row["canonical_payload"]),
            "offer": dict(row["offer_payload"]),
            "expect_pass": bool(row["expected_pass"]),
            "expected_blockers": list(row["expected_blockers"] or []),
        }
        results.append(_evaluate_case(case, tutor_ok=True))

    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    critical_failures = sum(
        1 for r in results
        if not r["passed"] and r["severity"] == "CRITICAL"
    )
    score = round(passed * 100.0 / total, 2) if total else None
    gate = bool(total >= 100 and score is not None and score >= 98.0 and critical_failures == 0)

    return {
        "status": "PASS" if gate else "BUILDING_GOLD_SET",
        "version": VERSION,
        "gold_cases": total,
        "passed": passed,
        "failed": total - passed,
        "score": score,
        "critical_failures": critical_failures,
        "gold_gate_passed": gate,
        "minimum_gold_cases_for_gate": 100,
        "minimum_score_for_gate": 98.0,
        "failed_cases": [r for r in results if not r["passed"]][:100],
        "writes_performed": 0,
    }

def _real_data_preview(engine, limit: int) -> Dict[str, Any]:
    data = v257b._benchmark(engine, limit)
    items = []
    counts = Counter()

    for burst in data.get("bursts") or []:
        for candidate in burst.get("candidates") or []:
            meta = candidate.get("v257b") or {}
            integrity = ((meta.get("record_integrity") or {}).get("class") or "UNKNOWN")
            direction = ((meta.get("intent_direction") or {}).get("direction") or "UNKNOWN")
            hard = list(meta.get("hard_blockers") or [])
            if hard:
                bucket = "HARD_BLOCKED"
            elif meta.get("multiple_offer_values_preserved_unresolved"):
                bucket = "MULTIPLE_OFFERS"
            elif candidate.get("classification") == "REQUIREMENT":
                bucket = "REQUIREMENT"
            elif integrity == "SINGLE_PROPERTY_LIKELY":
                bucket = "SINGLE_PROPERTY_CANDIDATE"
            else:
                bucket = "NEEDS_HUMAN_REVIEW"
            counts[bucket] += 1
            items.append({
                "bucket": bucket,
                "classification": candidate.get("classification"),
                "transaction": candidate.get("transaction"),
                "property_family": candidate.get("property_family"),
                "location": candidate.get("location"),
                "review_reasons": candidate.get("review_reasons") or [],
                "own_text_redacted": candidate.get("own_text_redacted"),
                "record_integrity": integrity,
                "intent_direction": direction,
                "hard_blockers": hard,
                "v257b": meta,
            })

    return {
        "status": "READY_FOR_HUMAN_GOLD_REVIEW",
        "version": VERSION,
        "requested_limit": limit,
        "sampled_candidates": len(items),
        "bucket_counts": dict(counts),
        "candidates": items,
        "database_writes": 0,
        "canonical_writes": 0,
        "offer_writes": 0,
    }

def _save_gold(engine, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not _table_exists(engine, "alliance_v261_gold_cases"):
        return {"status": "INSTALL_REQUIRED"}

    case_key = " ".join(str(payload.get("case_key") or "").split()).strip()
    reviewed_by = " ".join(str(payload.get("reviewed_by") or "").split()).strip()
    if not case_key or not reviewed_by:
        return {"status": "ERROR", "error": "case_key and reviewed_by are required"}

    source = dict(payload.get("source_candidate") or {})
    canonical = dict(payload.get("canonical") or {})
    offer = dict(payload.get("offer") or {})
    expected_pass = bool(payload.get("expected_pass"))
    expected_blockers = list(payload.get("expected_blockers") or [])
    skill = str(payload.get("skill") or "REAL_DATA_GOLD")
    severity = str(payload.get("severity") or "NORMAL").upper()

    with engine.begin() as c:
        gold_id = c.execute(text("""
            INSERT INTO alliance_v261_gold_cases(
                case_key, skill, severity, source_candidate,
                canonical_payload, offer_payload, expected_pass,
                expected_blockers, reviewed_by, active, updated_at
            )
            VALUES(
                :case_key, :skill, :severity,
                CAST(:source AS jsonb), CAST(:canonical AS jsonb),
                CAST(:offer AS jsonb), :expected_pass,
                CAST(:expected_blockers AS jsonb),
                :reviewed_by, TRUE, NOW()
            )
            ON CONFLICT(case_key) DO UPDATE SET
                skill=EXCLUDED.skill,
                severity=EXCLUDED.severity,
                source_candidate=EXCLUDED.source_candidate,
                canonical_payload=EXCLUDED.canonical_payload,
                offer_payload=EXCLUDED.offer_payload,
                expected_pass=EXCLUDED.expected_pass,
                expected_blockers=EXCLUDED.expected_blockers,
                reviewed_by=EXCLUDED.reviewed_by,
                active=TRUE,
                updated_at=NOW()
            RETURNING gold_id
        """), {
            "case_key": case_key,
            "skill": skill,
            "severity": severity,
            "source": json.dumps(source),
            "canonical": json.dumps(canonical),
            "offer": json.dumps(offer),
            "expected_pass": expected_pass,
            "expected_blockers": json.dumps(expected_blockers),
            "reviewed_by": reviewed_by,
        }).scalar_one()

    return {
        "status": "GOLD_CASE_SAVED",
        "gold_id": int(gold_id),
        "case_key": case_key,
        "training_table_write_only": True,
        "canonical_writes": 0,
        "offer_writes": 0,
    }

def _status(engine) -> Dict[str, Any]:
    adv = _academy_plus_adversarial(engine, variants_per_case=5)
    gold = _gold_exam(engine)
    return {
        "status": "READY",
        "version": VERSION,
        "mode": MODE,
        "academy_plus_adversarial": {
            "score": adv.get("score"),
            "failed": adv.get("failed"),
            "critical_failures": adv.get("critical_failures"),
            "topper_gate_passed": adv.get("topper_gate_passed"),
            "total_exam_cases": adv.get("total_exam_cases"),
        },
        "real_gold": {
            "gold_cases": gold.get("gold_cases"),
            "score": gold.get("score"),
            "critical_failures": gold.get("critical_failures"),
            "gold_gate_passed": gold.get("gold_gate_passed"),
        },
        "canonical_property_writes": 0,
        "offer_writes": 0,
        "matcher_modified": False,
        "whatsapp_live_modified": False,
    }

def _install(engine) -> Dict[str, Any]:
    executed = 0
    with engine.begin() as c:
        for ddl in DDL:
            c.execute(text(ddl))
            executed += 1
    return {
        "status": "INSTALLED",
        "version": VERSION,
        "ddl_statements": executed,
        "tables": {
            "alliance_v261_gold_cases": _table_exists(engine, "alliance_v261_gold_cases"),
            "alliance_v261_exam_runs": _table_exists(engine, "alliance_v261_exam_runs"),
        },
        "canonical_writes": 0,
        "offer_writes": 0,
    }

def register(core):
    app = core.app
    engine = core.engine
    status_route = "/api/v7/property-ai/topper-v261/status"

    if any(getattr(r, "path", None) == status_route for r in app.router.routes):
        return {"status": "ALREADY_REGISTERED", "version": VERSION, "route": status_route}

    @app.get(status_route)
    def status():
        return JSONResponse(_status(engine))

    @app.post("/api/v7/property-ai/topper-v261/install")
    def install():
        return JSONResponse(_install(engine))

    @app.get("/api/v7/property-ai/topper-v261/exam")
    def exam(variants_per_case: int = Query(5, ge=1, le=10)):
        return JSONResponse(_academy_plus_adversarial(engine, variants_per_case))

    @app.get("/api/v7/property-ai/topper-v261/real-preview")
    def real_preview(limit: int = Query(100, ge=1, le=500)):
        return JSONResponse(_real_data_preview(engine, limit))

    @app.post("/api/v7/property-ai/topper-v261/gold-case")
    def gold_case(payload: Dict[str, Any] = Body(...)):
        return JSONResponse(_save_gold(engine, payload))

    @app.get("/api/v7/property-ai/topper-v261/gold-exam")
    def gold_exam():
        return JSONResponse(_gold_exam(engine))

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": status_route,
        "install": "/api/v7/property-ai/topper-v261/install",
        "exam": "/api/v7/property-ai/topper-v261/exam?variants_per_case=5",
        "real_preview": "/api/v7/property-ai/topper-v261/real-preview?limit=100",
        "gold_case": "/api/v7/property-ai/topper-v261/gold-case",
        "gold_exam": "/api/v7/property-ai/topper-v261/gold-exam",
    }
