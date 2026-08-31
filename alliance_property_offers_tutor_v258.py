from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi.responses import JSONResponse
from sqlalchemy import inspect, text

import alliance_property_evidence_grammar_v257b as v257b

VERSION = "2.5.8A-PROPERTY-OFFERS-TUTOR-FOUNDATION"
MODE = "CONTROLLED_SCHEMA_PLUS_TUTOR_EVALUATION"

DDL = [
    """
    CREATE TABLE IF NOT EXISTS pb_property_offers(
        offer_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        property_id UUID NOT NULL REFERENCES pb_canonical_properties(property_id) ON DELETE CASCADE,
        transaction_type TEXT NOT NULL CHECK (transaction_type IN ('SALE','RENT')),
        offer_status TEXT NOT NULL DEFAULT 'UNDER_REVIEW',
        rent_value NUMERIC,
        rent_period TEXT,
        sale_price_value NUMERIC,
        rate_value NUMERIC,
        rate_unit TEXT,
        cam_value NUMERIC,
        security_deposit_value NUMERIC,
        furnishing TEXT,
        negotiable BOOLEAN,
        possession TEXT,
        availability_status TEXT,
        source_raw_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
        source_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
        ai_understanding JSONB NOT NULL DEFAULT '{}'::jsonb,
        confidence NUMERIC,
        verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
        last_verified_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_pb_property_offers_property_id
    ON pb_property_offers(property_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_pb_property_offers_tx_status
    ON pb_property_offers(transaction_type, offer_status)
    """,
    """
    CREATE TABLE IF NOT EXISTS alliance_ai_training_cases(
        case_id BIGSERIAL PRIMARY KEY,
        case_key TEXT UNIQUE NOT NULL,
        skill TEXT NOT NULL,
        input_payload JSONB NOT NULL,
        expected_payload JSONB NOT NULL,
        severity TEXT NOT NULL DEFAULT 'NORMAL',
        active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS alliance_ai_training_runs(
        run_id BIGSERIAL PRIMARY KEY,
        version TEXT NOT NULL,
        started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        finished_at TIMESTAMPTZ,
        total_cases INTEGER NOT NULL DEFAULT 0,
        passed_cases INTEGER NOT NULL DEFAULT 0,
        failed_cases INTEGER NOT NULL DEFAULT 0,
        critical_failures INTEGER NOT NULL DEFAULT 0,
        score NUMERIC NOT NULL DEFAULT 0,
        payload JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
]

CURRICULUM: List[Dict[str, Any]] = [
    {
        "case_key": "AREA_SYDS_DLF",
        "skill": "AREA_GRAMMAR",
        "severity": "HIGH",
        "input_payload": {
            "classification": "AVAILABILITY",
            "transaction": "RENT",
            "property_family": "RESIDENTIAL",
            "location": "DLF Phase 2",
            "review_reasons": ["PROPERTY_SPECIFIC_FACT_MISSING"],
            "own_text_redacted": "DLF PHASE 2 | 400 SYDS 4BHK+ SER | 1.60LAC+ MAINT | FULLY FURNISHED | 1.75LAC+MAINT",
        },
        "expected_payload": {
            "area_mentions_min": 1,
            "integrity_class": "MULTIPLE_OFFERS_POSSIBLE",
            "offer_selected": False,
        },
    },
    {
        "case_key": "AREA_SYDS_SUSHANT",
        "skill": "AREA_GRAMMAR",
        "severity": "HIGH",
        "input_payload": {
            "classification": "AVAILABILITY",
            "transaction": "RENT",
            "property_family": "RESIDENTIAL",
            "location": "Sushant Lok 1",
            "review_reasons": ["PROPERTY_SPECIFIC_FACT_MISSING"],
            "own_text_redacted": "SHUSHANTLOK1 | 300 SYDS 4BHK+ SER | 1.10 LAC+MAINT | FULLY FURNISHED | 1.30 LAC+MAINT",
        },
        "expected_payload": {
            "area_mentions_min": 1,
            "integrity_class": "MULTIPLE_OFFERS_POSSIBLE",
            "offer_selected": False,
        },
    },
    {
        "case_key": "INCIDENTAL_REQUIREMENT_AVAILABILITY",
        "skill": "INTENT_DIRECTION",
        "severity": "CRITICAL",
        "input_payload": {
            "classification": "REQUIREMENT",
            "transaction": "RENT",
            "property_family": "RESIDENTIAL",
            "location": None,
            "review_reasons": [],
            "own_text_redacted": "Luxury Independent Floor | 3 BHK | Fully Furnished / Semi-Furnished As per client requirement | Ready to Move In | Urgent Rent Owner Going Abroad",
        },
        "expected_payload": {
            "intent_direction": "AVAILABILITY_LIKELY",
            "suspected_upstream_requirement_misroute": True,
        },
    },
    {
        "case_key": "REAL_REQUIREMENT_GOA",
        "skill": "REQUIREMENT_INTENT",
        "severity": "CRITICAL",
        "input_payload": {
            "classification": "REQUIREMENT",
            "transaction": None,
            "property_family": "RESIDENTIAL",
            "location": "Vagator",
            "review_reasons": ["TRANSACTION_MISSING"],
            "own_text_redacted": "Looking for a 3/4 BHK independent villa with private pool. Preferred Locations Vagator Anjuna Siolim Assagao. Budget 1.5 to 2.25 Lakh/month.",
        },
        "expected_payload": {
            "intent_direction": "DEMAND_LIKELY",
            "requirement_complete": True,
            "transaction_required_for_requirement_gate": False,
        },
    },
    {
        "case_key": "RATE_NOT_TOTAL_PRICE",
        "skill": "MONEY_SAFETY",
        "severity": "CRITICAL",
        "input_payload": {
            "classification": "AVAILABILITY",
            "transaction": "SALE",
            "property_family": "LAND",
            "location": "Noida",
            "review_reasons": ["AMBIGUOUS_RATE_NOT_TOTAL_PRICE"],
            "own_text_redacted": "Plot size 500 yards Rate is Rs 50000 per yard",
        },
        "expected_payload": {
            "hard_blocker": "AMBIGUOUS_RATE_NOT_TOTAL_PRICE",
            "price_totalized_from_rate": False,
        },
    },
    {
        "case_key": "TRANSACTION_MISSING_STAYS_BLOCKED",
        "skill": "TRANSACTION_SAFETY",
        "severity": "CRITICAL",
        "input_payload": {
            "classification": "AVAILABILITY",
            "transaction": None,
            "property_family": "LAND",
            "location": "Noida",
            "review_reasons": ["TRANSACTION_MISSING", "PROPERTY_SPECIFIC_FACT_MISSING"],
            "own_text_redacted": "Total Land Area 57 Acer 285 Bigha. Covered area 6 lac square feet.",
        },
        "expected_payload": {
            "hard_blocker": "TRANSACTION_MISSING",
            "transaction_inferred": False,
        },
    },
    {
        "case_key": "MULTI_OFFER_PRESERVE",
        "skill": "OFFER_SEPARATION",
        "severity": "CRITICAL",
        "input_payload": {
            "classification": "AVAILABILITY",
            "transaction": "RENT",
            "property_family": "RESIDENTIAL",
            "location": "DLF Phase 2",
            "review_reasons": [],
            "own_text_redacted": "DLF Phase 2 400 SYDS 4BHK 1.60 LAC FULLY FURNISHED 1.75 LAC",
        },
        "expected_payload": {
            "integrity_class": "MULTIPLE_OFFERS_POSSIBLE",
            "multiple_offer_values_preserved_unresolved": True,
            "offer_selected": False,
        },
    },
]

def _table_exists(engine, name: str) -> bool:
    try:
        return inspect(engine).has_table(name)
    except Exception:
        return False

def _status(engine) -> Dict[str, Any]:
    return {
        "status": "READY",
        "version": VERSION,
        "mode": MODE,
        "v257b_version": v257b.VERSION,
        "tables": {
            "pb_property_offers": _table_exists(engine, "pb_property_offers"),
            "alliance_ai_training_cases": _table_exists(engine, "alliance_ai_training_cases"),
            "alliance_ai_training_runs": _table_exists(engine, "alliance_ai_training_runs"),
        },
        "canonical_property_writes_enabled": False,
        "offer_writes_enabled": False,
        "matcher_modified": False,
        "whatsapp_live_modified": False,
        "llm_training": False,
        "training_type": "DETERMINISTIC_CURRICULUM_AND_EVALUATION",
    }

def _install(engine) -> Dict[str, Any]:
    executed = 0
    try:
        with engine.begin() as c:
            for ddl in DDL:
                c.execute(text(ddl))
                executed += 1
        return {
            "status": "INSTALLED",
            "version": VERSION,
            "ddl_statements": executed,
            "status_after": _status(engine),
        }
    except Exception as exc:
        return {
            "status": "ERROR",
            "version": VERSION,
            "error": f"{type(exc).__name__}: {exc}",
            "executed_before_error": executed,
        }

def _seed_curriculum(engine) -> Dict[str, Any]:
    if not _table_exists(engine, "alliance_ai_training_cases"):
        return {"status": "INSTALL_REQUIRED", "seeded": 0}

    seeded = 0
    with engine.begin() as c:
        for case in CURRICULUM:
            c.execute(
                text("""
                    INSERT INTO alliance_ai_training_cases
                    (case_key, skill, input_payload, expected_payload, severity, active, updated_at)
                    VALUES
                    (:case_key, :skill, CAST(:input_payload AS jsonb),
                     CAST(:expected_payload AS jsonb), :severity, TRUE, NOW())
                    ON CONFLICT(case_key)
                    DO UPDATE SET
                        skill = EXCLUDED.skill,
                        input_payload = EXCLUDED.input_payload,
                        expected_payload = EXCLUDED.expected_payload,
                        severity = EXCLUDED.severity,
                        active = TRUE,
                        updated_at = NOW()
                """),
                {
                    "case_key": case["case_key"],
                    "skill": case["skill"],
                    "input_payload": json.dumps(case["input_payload"]),
                    "expected_payload": json.dumps(case["expected_payload"]),
                    "severity": case["severity"],
                },
            )
            seeded += 1
    return {"status": "SEEDED", "seeded": seeded}

def _evaluate_case(case: Dict[str, Any]) -> Dict[str, Any]:
    row = v257b._analyze_candidate_v257b(dict(case["input_payload"]))
    meta = row["v257b"]
    expected = case["expected_payload"]
    checks = {}

    if "area_mentions_min" in expected:
        checks["area_mentions_min"] = (
            int(meta["fact_evidence"]["area_mentions"]) >= int(expected["area_mentions_min"])
        )
    if "integrity_class" in expected:
        checks["integrity_class"] = (
            meta["record_integrity"]["class"] == expected["integrity_class"]
        )
    if "offer_selected" in expected:
        checks["offer_selected"] = meta["offer_selected"] == expected["offer_selected"]
    if "intent_direction" in expected:
        checks["intent_direction"] = (
            meta["intent_direction"]["direction"] == expected["intent_direction"]
        )
    if "suspected_upstream_requirement_misroute" in expected:
        gate = meta.get("requirement_gate") or {}
        checks["suspected_upstream_requirement_misroute"] = (
            gate.get("suspected_upstream_requirement_misroute")
            == expected["suspected_upstream_requirement_misroute"]
        )
    if "requirement_complete" in expected:
        gate = meta.get("requirement_gate") or {}
        checks["requirement_complete"] = (
            gate.get("complete_enough_for_requirement_review")
            == expected["requirement_complete"]
        )
    if "transaction_required_for_requirement_gate" in expected:
        gate = meta.get("requirement_gate") or {}
        checks["transaction_required_for_requirement_gate"] = (
            gate.get("transaction_required_for_requirement_gate")
            == expected["transaction_required_for_requirement_gate"]
        )
    if "hard_blocker" in expected:
        checks["hard_blocker"] = expected["hard_blocker"] in meta["hard_blockers"]
    if "price_totalized_from_rate" in expected:
        checks["price_totalized_from_rate"] = (
            meta["price_totalized_from_rate"] == expected["price_totalized_from_rate"]
        )
    if "transaction_inferred" in expected:
        checks["transaction_inferred"] = (
            meta["transaction_inferred"] == expected["transaction_inferred"]
        )
    if "multiple_offer_values_preserved_unresolved" in expected:
        checks["multiple_offer_values_preserved_unresolved"] = (
            meta["multiple_offer_values_preserved_unresolved"]
            == expected["multiple_offer_values_preserved_unresolved"]
        )

    passed = all(checks.values()) if checks else False
    return {
        "case_key": case["case_key"],
        "skill": case["skill"],
        "severity": case["severity"],
        "passed": passed,
        "checks": checks,
    }

def _run_curriculum(engine, persist: bool = True) -> Dict[str, Any]:
    if not _table_exists(engine, "alliance_ai_training_cases"):
        cases = CURRICULUM
    else:
        with engine.connect() as c:
            rows = c.execute(text("""
                SELECT case_key, skill, input_payload, expected_payload, severity
                FROM alliance_ai_training_cases
                WHERE active = TRUE
                ORDER BY case_id
            """)).mappings().all()
            cases = [
                {
                    "case_key": r["case_key"],
                    "skill": r["skill"],
                    "input_payload": dict(r["input_payload"]),
                    "expected_payload": dict(r["expected_payload"]),
                    "severity": r["severity"],
                }
                for r in rows
            ]
        if not cases:
            cases = CURRICULUM

    results = [_evaluate_case(c) for c in cases]
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed
    critical_failures = sum(
        1 for r in results if (not r["passed"] and r["severity"] == "CRITICAL")
    )
    score = round((passed / total) * 100, 2) if total else 0.0
    production_gate = bool(total > 0 and score == 100.0 and critical_failures == 0)

    payload = {
        "status": "PASS" if production_gate else "TRAINING_REQUIRED",
        "version": VERSION,
        "total_cases": total,
        "passed_cases": passed,
        "failed_cases": failed,
        "critical_failures": critical_failures,
        "score": score,
        "production_gate_passed": production_gate,
        "results": results,
        "safety": {
            "canonical_property_writes": 0,
            "offer_writes": 0,
            "matcher_changes": 0,
            "whatsapp_live_changes": 0,
            "llm_fine_tuning": False,
        },
    }

    if persist and _table_exists(engine, "alliance_ai_training_runs"):
        try:
            with engine.begin() as c:
                c.execute(
                    text("""
                        INSERT INTO alliance_ai_training_runs
                        (version, finished_at, total_cases, passed_cases,
                         failed_cases, critical_failures, score, payload)
                        VALUES
                        (:version, NOW(), :total, :passed, :failed,
                         :critical_failures, :score, CAST(:payload AS jsonb))
                    """),
                    {
                        "version": VERSION,
                        "total": total,
                        "passed": passed,
                        "failed": failed,
                        "critical_failures": critical_failures,
                        "score": score,
                        "payload": json.dumps(payload),
                    },
                )
        except Exception:
            payload["training_run_persist_warning"] = True

    return payload

def _install_and_train(engine) -> Dict[str, Any]:
    installed = _install(engine)
    if installed.get("status") != "INSTALLED":
        return installed
    seeded = _seed_curriculum(engine)
    training = _run_curriculum(engine, persist=True)
    return {
        "status": "READY" if training.get("production_gate_passed") else "TRAINING_REQUIRED",
        "version": VERSION,
        "foundation": installed,
        "curriculum": seeded,
        "training": training,
        "next_step": (
            "CONTROLLED_WRITE_GATE"
            if training.get("production_gate_passed")
            else "FIX_FAILED_TRAINING_CASES_BEFORE_CONTROLLED_WRITES"
        ),
    }

def register(core):
    app = core.app
    engine = core.engine
    status_route = "/api/v7/property-ai/property-offers-tutor-v258/status"

    if any(getattr(r, "path", None) == status_route for r in app.router.routes):
        return {"status": "ALREADY_REGISTERED", "version": VERSION, "route": status_route}

    @app.get(status_route)
    def status():
        return JSONResponse(_status(engine))

    @app.post("/api/v7/property-ai/property-offers-tutor-v258/install")
    def install():
        return JSONResponse(_install(engine))

    @app.post("/api/v7/property-ai/property-offers-tutor-v258/seed-curriculum")
    def seed_curriculum():
        return JSONResponse(_seed_curriculum(engine))

    @app.get("/api/v7/property-ai/property-offers-tutor-v258/exam")
    def exam():
        return JSONResponse(_run_curriculum(engine, persist=False))

    @app.post("/api/v7/property-ai/property-offers-tutor-v258/install-and-train")
    def install_and_train():
        return JSONResponse(_install_and_train(engine))

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": status_route,
        "install": "/api/v7/property-ai/property-offers-tutor-v258/install",
        "seed": "/api/v7/property-ai/property-offers-tutor-v258/seed-curriculum",
        "exam": "/api/v7/property-ai/property-offers-tutor-v258/exam",
        "install_and_train": "/api/v7/property-ai/property-offers-tutor-v258/install-and-train",
    }

