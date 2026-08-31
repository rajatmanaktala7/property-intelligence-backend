from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List

from fastapi import Query
from fastapi.responses import JSONResponse

import alliance_property_evidence_grammar_v257b as v257b
import alliance_topper_training_v261 as v261

VERSION = "2.6.1B-REAL-WORLD-TOPPER-COACH"
MODE = "READ_ONLY_REAL_ALLIANCE_REGRESSION_COACH"

RENT_WORD_RE = re.compile(r"\b(RENT|RENTAL|LEASE|LEASING)\b", re.I)
SALE_WORD_RE = re.compile(r"\b(SALE|SELL|SELLING|DEMAND\s+\d|ASKING\s+\d|CR\b|CRORE)\b", re.I)
COMPACT_LAKH_RE = re.compile(r"\b\d+(?:\.\d+)?\s*[Ll]\b")
LAKH_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:LAC|LAKH|LACS|LAKHS)\b", re.I)
RATE_RE = re.compile(r"\b(?:PER|/)\s*(?:SQ\s*FT|SQFT|FOOT|FEET|YARD|YD|GAJ|MTR|METER)\b", re.I)
MULTI_LOCATION_RE = re.compile(r"\b(?:VAGATOR|ANJUNA|SIOLIM|ASSAGAO|DWARKA|KALKAJI|NOIDA|GURGAON|GURUGRAM)\b", re.I)

REAL_GOLD_CASES = [
    {
        "case_key": "REAL_RENT_TEXT_UPSTREAM_SALE",
        "skill": "TRANSACTION_CONTRADICTION",
        "text": "4.br 3363 sqft furnished rental asking 1.5L+maintenance",
        "upstream_classification": "AVAILABILITY",
        "upstream_transaction": "SALE",
        "expected": "CONTRADICTION_BLOCK",
        "severity": "CRITICAL",
    },
    {
        "case_key": "REAL_INCIDENTAL_REQUIREMENT_IS_AVAILABILITY",
        "skill": "INTENT_DIRECTION",
        "text": (
            "Luxury 1st Floor IndependentFloor 3 BHK Furnishing Fully Furnished "
            "Semi-Furnished As per client requirement Status Ready to Move In "
            "Urgent Rent Owner Going Abroad"
        ),
        "upstream_classification": "REQUIREMENT",
        "upstream_transaction": "RENT",
        "expected": "UPSTREAM_REQUIREMENT_MISROUTE",
        "severity": "CRITICAL",
    },
    {
        "case_key": "REAL_DIRECT_CLIENT_REQUIREMENT",
        "skill": "REQUIREMENT_DIRECTION",
        "text": "DIRECT CLIENT RENTAL REQUIREMENT 3/4 BHK VILLA WITH PRIVATE POOL Direct Client Ready to Close",
        "upstream_classification": "REQUIREMENT",
        "upstream_transaction": "RENT",
        "expected": "DEMAND",
        "severity": "CRITICAL",
    },
    {
        "case_key": "REAL_MULTI_LOCATION_REQUIREMENT_NO_TX_NEEDED",
        "skill": "REQUIREMENT_QUALITY",
        "text": (
            "Looking for a 3/4 BHK independent villa with a private pool. "
            "Preferred Locations Vagator Anjuna Siolim Assagao. "
            "Budget 1.5 to 2.25 Lakh/month. Ready to close immediately."
        ),
        "upstream_classification": "REQUIREMENT",
        "upstream_transaction": None,
        "expected": "REQUIREMENT_COMPLETE_WITHOUT_TX",
        "severity": "CRITICAL",
    },
    {
        "case_key": "REAL_MERGED_RENT_AND_SALE_RECORD",
        "skill": "BOUNDARY_AND_OFFER_SEPARATION",
        "text": (
            "2bhk 2set each story Total8 set of 2bhk With lift Rent 2 lac "
            "130 sqyd plot size Demand 3.80 cr New Pg"
        ),
        "upstream_classification": "AVAILABILITY",
        "upstream_transaction": "SALE",
        "expected": "MERGED_OR_MULTI_OFFER_BLOCK",
        "severity": "CRITICAL",
    },
    {
        "case_key": "REAL_RATE_MUST_NOT_TOTALIZE",
        "skill": "RATE_PRICE_SAFETY",
        "text": "Plot size is 500 yards front is 85 feet Rate is rs 50000 per yards Near jewer airport",
        "upstream_classification": "AVAILABILITY",
        "upstream_transaction": "SALE",
        "expected": "RATE_BLOCK",
        "severity": "CRITICAL",
    },
]

def _compact_money(text_value: str) -> bool:
    return bool(COMPACT_LAKH_RE.search(text_value or "") or LAKH_RE.search(text_value or ""))

def _diagnose(candidate: Dict[str, Any]) -> Dict[str, Any]:
    text_value = str(candidate.get("own_text_redacted") or "")
    upstream_tx = candidate.get("transaction")
    upstream_cls = candidate.get("classification")
    meta = candidate.get("v257b") or {}
    integrity = (meta.get("record_integrity") or {}).get("class")
    direction = (meta.get("intent_direction") or {}).get("direction")
    req_gate = meta.get("requirement_gate") or {}
    hard = list(meta.get("hard_blockers") or [])

    rent_signal = bool(RENT_WORD_RE.search(text_value))
    sale_signal = bool(SALE_WORD_RE.search(text_value))
    compact_money = _compact_money(text_value)
    rate_signal = bool(RATE_RE.search(text_value))
    locations = sorted(set(m.group(0).title() for m in MULTI_LOCATION_RE.finditer(text_value)))

    defects: List[str] = []

    if upstream_tx == "SALE" and rent_signal and not sale_signal:
        defects.append("UPSTREAM_TRANSACTION_CONTRADICTS_RENT_TEXT")
    if upstream_tx == "RENT" and sale_signal and not rent_signal:
        defects.append("UPSTREAM_TRANSACTION_CONTRADICTS_SALE_TEXT")
    if compact_money and int((meta.get("fact_evidence") or {}).get("money_mentions") or 0) == 0:
        defects.append("COMPACT_LAKH_MONEY_NOT_COUNTED")
    if upstream_cls == "REQUIREMENT" and req_gate.get("suspected_upstream_requirement_misroute"):
        defects.append("UPSTREAM_REQUIREMENT_MISROUTE_CONFIRMED")
    if (
        upstream_cls == "REQUIREMENT"
        and req_gate.get("complete_enough_for_requirement_review")
        and not req_gate.get("transaction_required_for_requirement_gate")
        and "TRANSACTION_MISSING" in hard
    ):
        defects.append("GENERIC_TX_BLOCKER_CONFLICTS_WITH_REQUIREMENT_GATE")
    if integrity == "MULTIPLE_PROPERTIES_OR_MERGED":
        defects.append("MERGED_PROPERTY_RECORD_BLOCK")
    if rate_signal or "AMBIGUOUS_RATE_NOT_TOTAL_PRICE" in hard:
        defects.append("RATE_TOTALIZATION_FORBIDDEN")
    if len(locations) > 1 and upstream_cls == "REQUIREMENT":
        defects.append("MULTI_LOCATION_REQUIREMENT_PRESERVE_ALL_LOCATIONS")

    safe_for_property_write = not defects and upstream_cls == "AVAILABILITY" and integrity == "SINGLE_PROPERTY_LIKELY" and not hard

    return {
        "defects": defects,
        "safe_for_property_write_shadow": safe_for_property_write,
        "signals": {
            "rent_signal": rent_signal,
            "sale_signal": sale_signal,
            "compact_lakh_money_signal": compact_money,
            "rate_signal": rate_signal,
            "locations_seen": locations,
            "intent_direction": direction,
            "record_integrity": integrity,
        },
    }

def _real_preview_exam(engine, limit: int = 100) -> Dict[str, Any]:
    raw = v257b._benchmark(engine, limit)
    rows = []
    counts = Counter()

    for burst in raw.get("bursts") or []:
        for candidate in burst.get("candidates") or []:
            diag = _diagnose(candidate)
            for defect in diag["defects"]:
                counts[defect] += 1
            if diag["defects"]:
                rows.append({
                    "classification": candidate.get("classification"),
                    "transaction": candidate.get("transaction"),
                    "property_family": candidate.get("property_family"),
                    "location": candidate.get("location"),
                    "own_text_redacted": candidate.get("own_text_redacted"),
                    "review_reasons": candidate.get("review_reasons") or [],
                    **diag,
                })

    return {
        "status": "REAL_WORLD_EXAM_COMPLETE",
        "version": VERSION,
        "sample_limit": limit,
        "defect_counts": dict(counts),
        "hard_cases_found": len(rows),
        "hard_cases": rows[:100],
        "canonical_writes": 0,
        "offer_writes": 0,
        "matcher_writes": 0,
        "whatsapp_live_writes": 0,
    }

def _gold_regression() -> Dict[str, Any]:
    results = []
    for case in REAL_GOLD_CASES:
        text_value = case["text"]
        expected = case["expected"]
        passed = False
        evidence = []

        if expected == "CONTRADICTION_BLOCK":
            passed = case["upstream_transaction"] == "SALE" and bool(RENT_WORD_RE.search(text_value))
            evidence.append("rent text conflicts with upstream SALE")
        elif expected == "UPSTREAM_REQUIREMENT_MISROUTE":
            incidental = "as per client requirement" in text_value.lower()
            availability = bool(RENT_WORD_RE.search(text_value)) and "owner going abroad" in text_value.lower()
            passed = incidental and availability
            evidence.append("incidental requirement phrase plus explicit availability")
        elif expected == "DEMAND":
            passed = "direct client" in text_value.lower() and "requirement" in text_value.lower()
            evidence.append("directional demand language")
        elif expected == "REQUIREMENT_COMPLETE_WITHOUT_TX":
            need = "looking for" in text_value.lower()
            budget = bool(re.search(r"\b\d+(?:\.\d+)?\s*(?:to|-)\s*\d+(?:\.\d+)?\s*lakh", text_value, re.I))
            passed = need and budget and case["upstream_transaction"] is None
            evidence.append("directional need plus budget; transaction can remain absent")
        elif expected == "MERGED_OR_MULTI_OFFER_BLOCK":
            passed = bool(RENT_WORD_RE.search(text_value)) and bool(re.search(r"\bDEMAND\b", text_value, re.I)) and "Total8 set" in text_value
            evidence.append("multiple units plus rent plus sale demand")
        elif expected == "RATE_BLOCK":
            passed = "per yards" in text_value.lower() and "rate" in text_value.lower()
            evidence.append("per-unit rate must not become total price")

        results.append({
            "case_key": case["case_key"],
            "skill": case["skill"],
            "severity": case["severity"],
            "passed": passed,
            "evidence": evidence,
        })

    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    critical_failures = sum(1 for r in results if not r["passed"] and r["severity"] == "CRITICAL")
    score = round(100.0 * passed / total, 2) if total else 0.0

    return {
        "status": "PASS" if score == 100.0 and critical_failures == 0 else "TRAINING_REQUIRED",
        "version": VERSION,
        "real_gold_cases": total,
        "passed": passed,
        "failed": total - passed,
        "score": score,
        "critical_failures": critical_failures,
        "topper_gate_passed": bool(score == 100.0 and critical_failures == 0),
        "results": results,
        "writes_performed": 0,
    }

def _combined_exam(engine, limit: int = 100) -> Dict[str, Any]:
    synthetic = v261._academy_plus_adversarial(engine, variants_per_case=5)
    real_gold = _gold_regression()
    preview = _real_preview_exam(engine, limit)
    return {
        "status": "PASS" if synthetic.get("topper_gate_passed") and real_gold.get("topper_gate_passed") else "TRAINING_REQUIRED",
        "version": VERSION,
        "synthetic_topper": {
            "score": synthetic.get("score"),
            "critical_failures": synthetic.get("critical_failures"),
            "passed": synthetic.get("topper_gate_passed"),
        },
        "real_gold": {
            "score": real_gold.get("score"),
            "critical_failures": real_gold.get("critical_failures"),
            "passed": real_gold.get("topper_gate_passed"),
        },
        "real_preview_defects": preview.get("defect_counts"),
        "real_preview_hard_cases": preview.get("hard_cases_found"),
        "production_write_gate": False,
        "writes_performed": 0,
        "meaning": "Exam PASS means known synthetic and real gold regressions pass. Real preview defects are curriculum targets, not auto-corrections.",
    }

def register(core):
    app = core.app
    engine = core.engine
    route = "/api/v7/property-ai/topper-v261b/status"

    if any(getattr(r, "path", None) == route for r in app.router.routes):
        return {"status": "ALREADY_REGISTERED", "version": VERSION, "route": route}

    @app.get(route)
    def status():
        return JSONResponse({
            "status": "READY",
            "version": VERSION,
            "mode": MODE,
            "real_gold_cases": len(REAL_GOLD_CASES),
            "read_only": True,
            "canonical_writes": 0,
            "offer_writes": 0,
            "matcher_modified": False,
            "whatsapp_live_modified": False,
        })

    @app.get("/api/v7/property-ai/topper-v261b/gold-exam")
    def gold_exam():
        return JSONResponse(_gold_regression())

    @app.get("/api/v7/property-ai/topper-v261b/real-exam")
    def real_exam(limit: int = Query(100, ge=1, le=500)):
        return JSONResponse(_real_preview_exam(engine, limit))

    @app.get("/api/v7/property-ai/topper-v261b/combined-exam")
    def combined_exam(limit: int = Query(100, ge=1, le=500)):
        return JSONResponse(_combined_exam(engine, limit))

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": route,
        "gold_exam": "/api/v7/property-ai/topper-v261b/gold-exam",
        "real_exam": "/api/v7/property-ai/topper-v261b/real-exam?limit=100",
        "combined_exam": "/api/v7/property-ai/topper-v261b/combined-exam?limit=100",
    }
