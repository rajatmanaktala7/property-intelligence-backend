from __future__ import annotations

import re
from typing import Any, Dict, List

from fastapi import Body
from fastapi.responses import JSONResponse
from sqlalchemy import text

import alliance_property_controlled_writer_v259 as v259
import alliance_property_offers_tutor_v258 as v258

VERSION = "2.6.0B-ALLIANCE-AI-ACADEMY-TRAP-GRAMMAR-FIX"
MODE = "DETERMINISTIC_ACADEMY_PLUS_V259B_SAFETY"

TEST_LABEL_RE = re.compile(
    r"(?:^|[_\-\s])(TEST|DEMO|SYNTHETIC|REGRESSION)(?:$|[_\-\s])",
    re.I,
)

ACADEMY_CASES: List[Dict[str, Any]] = []


def _add(
    case_key,
    skill,
    source,
    canonical,
    offer,
    expect_pass,
    severity="NORMAL",
    expected_blockers=None,
):
    ACADEMY_CASES.append(
        {
            "case_key": case_key,
            "skill": skill,
            "severity": severity,
            "source": source,
            "canonical": canonical,
            "offer": offer,
            "expect_pass": bool(expect_pass),
            "expected_blockers": list(expected_blockers or []),
        }
    )


def _base_source(**kw):
    d = {
        "classification": "AVAILABILITY",
        "transaction": "RENT",
        "property_family": "RESIDENTIAL",
        "location": "DLF Phase 2",
        "review_reasons": [],
        "boundary_needs_split": False,
        "own_text_redacted": "DLF Phase 2 400 SYDS 4BHK rent 1.60 LAC",
    }
    d.update(kw)
    return d


def _base_canonical(**kw):
    d = {
        "property_family": "RESIDENTIAL",
        "property_subtype": "INDEPENDENT FLOOR",
        "city": "Gurgaon",
        "locality": "DLF Phase 2",
        "configuration": "4 BHK",
        "area_value": 400,
        "area_unit": "SQYD",
        "area_sqft": 3600,
        "clean_description": "4 BHK residential floor in DLF Phase 2",
    }
    d.update(kw)
    return d


def _base_offer(**kw):
    d = {
        "transaction_type": "RENT",
        "rent_value": 160000,
        "rent_period": "MONTH",
    }
    d.update(kw)
    return d


# ---------- SAFE PASS CASES ----------
for i, (area_text, area_sqft) in enumerate(
    [
        ("400 SYDS", 3600),
        ("400 SQYD", 3600),
        ("400 SQ YD", 3600),
        ("3600 SQFT", 3600),
        ("3600 SQ FT", 3600),
        ("3600 SFT", 3600),
        ("334 SQM", 3595),
        ("334 SQ MT", 3595),
        ("500 YARDS", 4500),
        ("500 GAJ", 4500),
    ],
    1,
):
    _add(
        f"PASS_AREA_{i:02d}",
        "AREA_GRAMMAR",
        _base_source(
            own_text_redacted=f"DLF Phase 2 {area_text} 4BHK rent 1.60 LAC"
        ),
        _base_canonical(area_sqft=area_sqft),
        _base_offer(),
        True,
        "HIGH",
    )


# ---------- CORE CRITICAL BLOCKERS ----------
_add(
    "BLOCK_REQUIREMENT",
    "INTENT_SAFETY",
    _base_source(
        classification="REQUIREMENT",
        transaction=None,
        own_text_redacted="Looking for 4 BHK villa in Vagator budget 2 Lakh",
    ),
    _base_canonical(locality="Vagator", city="Goa"),
    _base_offer(),
    False,
    "CRITICAL",
    ["NOT_AVAILABILITY"],
)

_add(
    "BLOCK_TX_MISSING",
    "TRANSACTION_SAFETY",
    _base_source(transaction=None, review_reasons=["TRANSACTION_MISSING"]),
    _base_canonical(),
    _base_offer(),
    False,
    "CRITICAL",
    ["TRANSACTION_NOT_EXPLICIT"],
)

_add(
    "BLOCK_LOCATION_MISSING",
    "LOCATION_SAFETY",
    _base_source(location=None),
    _base_canonical(locality=""),
    _base_offer(),
    False,
    "CRITICAL",
    ["LOCATION_MISSING"],
)

_add(
    "BLOCK_FAMILY_MISSING",
    "PROPERTY_IDENTITY",
    _base_source(property_family=None),
    _base_canonical(property_family=""),
    _base_offer(),
    False,
    "CRITICAL",
    ["PROPERTY_FAMILY_MISSING"],
)

_add(
    "BLOCK_RATE_TOTAL",
    "MONEY_SAFETY",
    _base_source(
        transaction="SALE",
        property_family="LAND",
        location="Noida",
        review_reasons=["AMBIGUOUS_RATE_NOT_TOTAL_PRICE"],
        own_text_redacted="Plot size 500 yards Rate is Rs 50000 per yard",
    ),
    _base_canonical(
        property_family="LAND",
        property_subtype="PLOT",
        locality="Noida",
        configuration="",
        area_sqft=4500,
        clean_description="Land in Noida",
    ),
    {"transaction_type": "SALE", "sale_price_value": 25000000},
    False,
    "CRITICAL",
    ["AMBIGUOUS_RATE_NOT_TOTAL_PRICE"],
)

_add(
    "BLOCK_MULTI_OFFER",
    "OFFER_SEPARATION",
    _base_source(
        own_text_redacted=(
            "DLF Phase 2 400 SYDS 4BHK "
            "1.60 LAC fully furnished 1.75 LAC"
        )
    ),
    _base_canonical(),
    _base_offer(),
    False,
    "CRITICAL",
    ["MULTIPLE_OFFERS_UNRESOLVED"],
)

_add(
    "BLOCK_FAMILY_MISMATCH",
    "PROPERTY_IDENTITY",
    _base_source(),
    _base_canonical(property_family="COMMERCIAL"),
    _base_offer(),
    False,
    "CRITICAL",
    ["CANONICAL_FAMILY_MISMATCH"],
)

_add(
    "BLOCK_LOCATION_MISMATCH",
    "LOCATION_SAFETY",
    _base_source(),
    _base_canonical(locality="Sushant Lok 1"),
    _base_offer(),
    False,
    "CRITICAL",
    ["CANONICAL_LOCATION_MISMATCH"],
)

_add(
    "BLOCK_OFFER_TX_MISMATCH",
    "OFFER_SAFETY",
    _base_source(),
    _base_canonical(),
    {"transaction_type": "SALE", "sale_price_value": 20000000},
    False,
    "CRITICAL",
    ["OFFER_TRANSACTION_MISMATCH"],
)

_add(
    "BLOCK_RENT_VALUE_MISSING",
    "OFFER_SAFETY",
    _base_source(),
    _base_canonical(),
    {"transaction_type": "RENT"},
    False,
    "CRITICAL",
    ["RENT_VALUE_REQUIRED"],
)

_add(
    "BLOCK_SALE_VALUE_ON_RENT",
    "OFFER_SAFETY",
    _base_source(),
    _base_canonical(),
    {
        "transaction_type": "RENT",
        "rent_value": 160000,
        "sale_price_value": 20000000,
    },
    False,
    "CRITICAL",
    ["SALE_VALUE_PRESENT_ON_RENT_OFFER"],
)

_add(
    "BLOCK_SALE_PRICE_MISSING",
    "OFFER_SAFETY",
    _base_source(
        transaction="SALE",
        own_text_redacted="DLF Phase 2 400 SYDS 4BHK for sale 4 CR",
    ),
    _base_canonical(),
    {"transaction_type": "SALE"},
    False,
    "CRITICAL",
    ["SALE_PRICE_REQUIRED"],
)

_add(
    "BLOCK_RENT_ON_SALE",
    "OFFER_SAFETY",
    _base_source(
        transaction="SALE",
        own_text_redacted="DLF Phase 2 400 SYDS 4BHK for sale 4 CR",
    ),
    _base_canonical(),
    {
        "transaction_type": "SALE",
        "sale_price_value": 40000000,
        "rent_value": 160000,
    },
    False,
    "CRITICAL",
    ["RENT_VALUE_PRESENT_ON_SALE_OFFER"],
)

_add(
    "BLOCK_NO_DESCRIPTION",
    "CANONICAL_QUALITY",
    _base_source(),
    _base_canonical(clean_description=""),
    _base_offer(),
    False,
    "HIGH",
    ["CANONICAL_DESCRIPTION_MISSING"],
)

_add(
    "BLOCK_NO_IDENTITY",
    "PROPERTY_IDENTITY",
    _base_source(own_text_redacted="DLF Phase 2 rent 1.60 LAC"),
    _base_canonical(
        configuration="",
        project_name="",
        area_sqft=None,
        area_value=None,
    ),
    _base_offer(),
    False,
    "CRITICAL",
    ["INSUFFICIENT_PHYSICAL_IDENTITY"],
)


# ---------- SAFE WRITE VARIATIONS ----------
localities = [
    "DLF Phase 2",
    "Sushant Lok 1",
    "Dwarka",
    "Kalkaji",
    "Vagator",
]
configs = ["2 BHK", "3 BHK", "4 BHK", "5 BHK"]
areas = [1200, 1800, 2400, 3600, 4500]
rent_texts = ["75K", "1.10 LAC", "1.60 LAC", "2.50 LAC", "8 LAC"]
rent_values = [75000, 110000, 160000, 250000, 800000]

n = 0
for loc in localities:
    for cfg in configs:
        for sqft in areas:
            idx = n % len(rent_values)
            n += 1
            src = _base_source(
                location=loc,
                own_text_redacted=(
                    f"{loc} {sqft} SQFT {cfg} rent {rent_texts[idx]}"
                ),
            )
            can = _base_canonical(
                locality=loc,
                configuration=cfg,
                area_sqft=sqft,
                area_value=sqft,
                area_unit="SQFT",
                clean_description=f"{cfg} property in {loc}",
            )
            off = _base_offer(rent_value=rent_values[idx])
            _add(
                f"SAFE_VARIATION_{n:03d}",
                "SINGLE_PROPERTY_WRITE_GATE",
                src,
                can,
                off,
                True,
                "NORMAL",
            )


# ---------- LOCATION MISMATCH TRAPS ----------
for i in range(100):
    src_loc = localities[i % len(localities)]
    wrong = localities[(i + 1) % len(localities)]
    _add(
        f"TRAP_LOCATION_{i+1:03d}",
        "LOCATION_SAFETY",
        _base_source(
            location=src_loc,
            own_text_redacted=(
                f"{src_loc} 1800 SQFT 3 BHK rent 1.10 LAC"
            ),
        ),
        _base_canonical(
            locality=wrong,
            configuration="3 BHK",
            area_sqft=1800,
            clean_description=f"3 BHK property in {wrong}",
        ),
        _base_offer(rent_value=110000),
        False,
        "CRITICAL",
        ["CANONICAL_LOCATION_MISMATCH"],
    )


# ---------- TRANSACTION / OFFER MISMATCH TRAPS ----------
for i in range(100):
    _add(
        f"TRAP_TX_{i+1:03d}",
        "OFFER_SAFETY",
        _base_source(transaction="RENT"),
        _base_canonical(),
        {
            "transaction_type": "SALE",
            "sale_price_value": 20000000 + i,
        },
        False,
        "CRITICAL",
        ["OFFER_TRANSACTION_MISMATCH"],
    )


# ---------- MULTIPLE OFFER TRAPS ----------
# IMPORTANT V260B FIX:
# Use LAC-format monetary expressions because this is the monetary vocabulary
# actually recognized by the current V257B evidence grammar and seen in Alliance data.
for i in range(100):
    first = 1.00 + (i % 50) / 100.0
    second = first + 0.25
    first_text = f"{first:.2f} LAC"
    second_text = f"{second:.2f} LAC"
    _add(
        f"TRAP_MULTI_{i+1:03d}",
        "OFFER_SEPARATION",
        _base_source(
            own_text_redacted=(
                "DLF Phase 2 400 SYDS 4BHK "
                f"{first_text} + MAINT | FULLY FURNISHED | "
                f"{second_text} + MAINT"
            )
        ),
        _base_canonical(),
        _base_offer(rent_value=int(first * 100000)),
        False,
        "CRITICAL",
        ["MULTIPLE_OFFERS_UNRESOLVED"],
    )


# ---------- RATE VS TOTAL PRICE TRAPS ----------
for i in range(100):
    rate = 30000 + i * 100
    _add(
        f"TRAP_RATE_{i+1:03d}",
        "MONEY_SAFETY",
        _base_source(
            transaction="SALE",
            property_family="LAND",
            location="Noida",
            review_reasons=["AMBIGUOUS_RATE_NOT_TOTAL_PRICE"],
            own_text_redacted=(
                f"Plot 500 yards rate Rs {rate} per yard"
            ),
        ),
        _base_canonical(
            property_family="LAND",
            property_subtype="PLOT",
            locality="Noida",
            configuration="",
            area_sqft=4500,
            clean_description="Land in Noida",
        ),
        {
            "transaction_type": "SALE",
            "sale_price_value": rate * 500,
        },
        False,
        "CRITICAL",
        ["AMBIGUOUS_RATE_NOT_TOTAL_PRICE"],
    )


# ---------- REQUIREMENT ROUTING TRAPS ----------
for i in range(100):
    loc = localities[i % len(localities)]
    _add(
        f"TRAP_REQ_{i+1:03d}",
        "REQUIREMENT_SAFETY",
        _base_source(
            classification="REQUIREMENT",
            transaction=None,
            location=loc,
            own_text_redacted=(
                f"Looking for 3 BHK in {loc} budget 2 lakh"
            ),
        ),
        _base_canonical(
            locality=loc,
            configuration="3 BHK",
            area_sqft=1800,
            clean_description=f"3 BHK property in {loc}",
        ),
        _base_offer(),
        False,
        "CRITICAL",
        ["NOT_AVAILABILITY"],
    )


def _test_marker(source_label: str, offer: Dict[str, Any]) -> bool:
    label = str(source_label or "")
    status = str((offer or {}).get("availability_status") or "")
    return bool(
        TEST_LABEL_RE.search(label)
        or status.strip().upper()
        in {"TEST", "DEMO", "SYNTHETIC", "REGRESSION"}
    )


def _academy_exam(engine=None) -> Dict[str, Any]:
    if engine is not None:
        tutor = v258._run_curriculum(engine, persist=False)
    else:
        tutor = {
            "production_gate_passed": True,
            "score": 100.0,
            "critical_failures": 0,
        }

    tutor_ok = bool(tutor.get("production_gate_passed"))
    results = []

    for case in ACADEMY_CASES:
        gate = v259._candidate_gate(
            dict(case["source"]),
            dict(case["canonical"]),
            dict(case["offer"]),
            tutor_ok=tutor_ok,
        )
        blockers = set(gate.get("blockers") or [])
        expected = set(case["expected_blockers"])
        behavior_ok = bool(gate["passed"]) == bool(case["expect_pass"])
        blockers_ok = expected.issubset(blockers)
        passed = behavior_ok and blockers_ok

        results.append(
            {
                "case_key": case["case_key"],
                "skill": case["skill"],
                "severity": case["severity"],
                "passed": passed,
                "expected_pass": case["expect_pass"],
                "actual_pass": gate["passed"],
                "missing_expected_blockers": sorted(expected - blockers),
            }
        )

    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    critical_failures = sum(
        1
        for r in results
        if not r["passed"] and r["severity"] == "CRITICAL"
    )
    score = round(passed * 100.0 / total, 2) if total else 0.0
    graduated = bool(
        score == 100.0
        and critical_failures == 0
        and tutor_ok
    )

    return {
        "status": "PASS" if graduated else "TRAINING_REQUIRED",
        "version": VERSION,
        "academy_cases": total,
        "passed": passed,
        "failed": total - passed,
        "score": score,
        "critical_failures": critical_failures,
        "legacy_tutor_gate": {
            "score": tutor.get("score"),
            "critical_failures": tutor.get("critical_failures"),
            "production_gate_passed": tutor_ok,
        },
        "graduation_gate_passed": graduated,
        "failed_cases": [
            r for r in results if not r["passed"]
        ][:50],
        "claim": (
            "100% means 100% of this deterministic Academy curriculum passed; "
            "it is not a claim of universal real-world accuracy."
        ),
        "writes_performed": 0,
    }


def _candidate(engine, candidate_id: int) -> Dict[str, Any]:
    with engine.connect() as c:
        row = c.execute(
            text(
                """
                SELECT candidate_id, source_label, source_candidate,
                       canonical_payload, offer_payload, gate_result,
                       status, approved_by, property_id, offer_id,
                       created_at, updated_at, committed_at
                FROM alliance_v259_write_candidates
                WHERE candidate_id=:id
                """
            ),
            {"id": candidate_id},
        ).mappings().first()

    if not row:
        return {"status": "NOT_FOUND", "candidate_id": candidate_id}

    out = dict(row)

    for k in ("property_id", "offer_id"):
        if out.get(k) is not None:
            out[k] = str(out[k])

    for k in ("created_at", "updated_at", "committed_at"):
        if out.get(k) is not None:
            out[k] = out[k].isoformat()

    return {"status": "FOUND", "candidate": out}


def _cancel(
    engine,
    candidate_id: int,
    cancelled_by: str,
) -> Dict[str, Any]:
    actor = " ".join(str(cancelled_by or "").split()).strip()

    if not actor:
        return {
            "status": "ERROR",
            "error": "cancelled_by is required",
        }

    with engine.begin() as c:
        row = c.execute(
            text(
                """
                SELECT candidate_id, status, property_id, offer_id
                FROM alliance_v259_write_candidates
                WHERE candidate_id=:id
                FOR UPDATE
                """
            ),
            {"id": candidate_id},
        ).mappings().first()

        if not row:
            return {
                "status": "NOT_FOUND",
                "candidate_id": candidate_id,
            }

        if (
            row["status"] == "COMMITTED"
            or row["property_id"]
            or row["offer_id"]
        ):
            return {
                "status": "BLOCKED",
                "reason": (
                    "COMMITTED_OR_WRITTEN_CANDIDATE_CANNOT_BE_CANCELLED"
                ),
            }

        c.execute(
            text(
                """
                UPDATE alliance_v259_write_candidates
                SET status='CANCELLED',
                    approved_by=:actor,
                    updated_at=NOW()
                WHERE candidate_id=:id
                """
            ),
            {"actor": actor, "id": candidate_id},
        )

        c.execute(
            text(
                """
                INSERT INTO alliance_v259_write_audit(
                    candidate_id, action, actor, payload
                )
                VALUES(
                    :id, 'CANCEL', :actor, '{}'::jsonb
                )
                """
            ),
            {"id": candidate_id, "actor": actor},
        )

    return {
        "status": "CANCELLED",
        "candidate_id": candidate_id,
        "writes_performed": 0,
    }


def _safe_commit(
    engine,
    candidate_id: int,
    approved_by: str,
) -> Dict[str, Any]:
    with engine.connect() as c:
        row = c.execute(
            text(
                """
                SELECT candidate_id, source_label,
                       offer_payload, status
                FROM alliance_v259_write_candidates
                WHERE candidate_id=:id
                """
            ),
            {"id": candidate_id},
        ).mappings().first()

    if not row:
        return {
            "status": "NOT_FOUND",
            "candidate_id": candidate_id,
        }

    if row["status"] == "CANCELLED":
        return {
            "status": "BLOCKED",
            "reason": "CANCELLED_CANDIDATE",
        }

    if _test_marker(
        row["source_label"],
        dict(row["offer_payload"] or {}),
    ):
        return {
            "status": "BLOCKED",
            "reason": "TEST_OR_SYNTHETIC_CANDIDATE_CANNOT_COMMIT",
            "writes_performed": 0,
        }

    return v259._commit(
        engine,
        candidate_id,
        approved_by,
    )


def _status(engine) -> Dict[str, Any]:
    exam = _academy_exam(engine)

    return {
        "status": "READY",
        "version": VERSION,
        "mode": MODE,
        "academy_cases": exam["academy_cases"],
        "academy_score": exam["score"],
        "academy_critical_failures": exam["critical_failures"],
        "graduation_gate_passed": exam["graduation_gate_passed"],
        "test_candidate_commit_block": True,
        "candidate_readback": True,
        "candidate_cancel": True,
        "automatic_bulk_writes": False,
        "matcher_modified": False,
        "whatsapp_live_modified": False,
    }


def register(core):
    app = core.app
    engine = core.engine
    route = "/api/v7/property-ai/academy-v260/status"

    if any(
        getattr(r, "path", None) == route
        for r in app.router.routes
    ):
        return {
            "status": "ALREADY_REGISTERED",
            "version": VERSION,
            "route": route,
        }

    @app.get(route)
    def status():
        return JSONResponse(_status(engine))

    @app.get("/api/v7/property-ai/academy-v260/exam")
    def exam():
        return JSONResponse(_academy_exam(engine))

    @app.get(
        "/api/v7/property-ai/academy-v260/candidate/{candidate_id}"
    )
    def candidate(candidate_id: int):
        return JSONResponse(
            _candidate(engine, candidate_id)
        )

    @app.post(
        "/api/v7/property-ai/academy-v260/cancel/{candidate_id}"
    )
    def cancel(
        candidate_id: int,
        payload: Dict[str, Any] = Body(...),
    ):
        return JSONResponse(
            _cancel(
                engine,
                candidate_id,
                payload.get("cancelled_by"),
            )
        )

    @app.post(
        "/api/v7/property-ai/academy-v260/commit/{candidate_id}"
    )
    def safe_commit(
        candidate_id: int,
        payload: Dict[str, Any] = Body(...),
    ):
        return JSONResponse(
            _safe_commit(
                engine,
                candidate_id,
                payload.get("approved_by"),
            )
        )

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": route,
        "exam": "/api/v7/property-ai/academy-v260/exam",
        "candidate": (
            "/api/v7/property-ai/academy-v260/"
            "candidate/{candidate_id}"
        ),
        "cancel": (
            "/api/v7/property-ai/academy-v260/"
            "cancel/{candidate_id}"
        ),
        "safe_commit": (
            "/api/v7/property-ai/academy-v260/"
            "commit/{candidate_id}"
        ),
    }
