from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy
from typing import Any, Dict, List, Optional

from fastapi import Query
from fastapi.responses import JSONResponse

import alliance_property_project_location_v256a as v256a

VERSION = "2.5.7A-PROPERTY-RECORD-INTEGRITY-CLASSIFICATION-SHADOW"
MODE = "READ_ONLY_SHADOW_RECORD_INTEGRITY_CLASSIFICATION"

HARD_BLOCKERS = {
    "V255_CHILD_EXTRACTION_FAILED",
    "AMBIGUOUS_RATE_NOT_TOTAL_PRICE",
    "TRANSACTION_MISSING",
    "IMPLAUSIBLE_SALE_TOTAL_REJECTED",
    "IMPLAUSIBLE_RENT_TOTAL_REJECTED",
    "PHONE_LIKE_OR_EXTREME_MONEY_REJECTED",
    "PHONE_LIKE_MONEY_REJECTED",
}

CONFIG_RE = re.compile(r"\b(?:[1-9]\d*)\s*(?:BHK|BR)\b", re.I)
AREA_RE = re.compile(
    r"\b\d[\d,]*(?:\.\d+)?\s*(?:SQ\.?\s*FT|SQFT|SQ\.?\s*YD|SQYD|SQ\.?\s*M|SQM|"
    r"SQ\.?\s*MT|SQMT|YARDS?|YDS?|GAJ|ACRES?|ACER|BIGHA|CARPET)\b",
    re.I,
)
TOTAL_MONEY_RE = re.compile(
    r"(?:₹|RS\.?|INR)?\s*\d+(?:\.\d+)?\s*(?:CR|CRORE|LAC|LAKH|LACS|LAKHS|K)\b",
    re.I,
)
RATE_RE = re.compile(
    r"(?:PER\s*(?:SQ\.?\s*FT|SQFT|YARD|YD|GAJ)|/\s*(?:SQ\.?\s*FT|SQFT|YARD|YD|GAJ)|"
    r"\bRATE\b)",
    re.I,
)
SALE_RE = re.compile(r"\b(?:FOR\s+SALE|SALE|SELL|RESALE|ASKING\s*PRICE|DEMAND)\b", re.I)
RENT_RE = re.compile(r"\b(?:FOR\s+RENT|RENT|LEASE|RENTAL|PER\s+MONTH|/MONTH|P\.?M\.?)\b", re.I)
PROPERTY_NOUN_RE = re.compile(
    r"\b(?:FLAT|APARTMENT|PENTHOUSE|VILLA|KOTHI|FLOOR|PLOT|LAND|SHOP|SHOWROOM|"
    r"OFFICE|SPACE|WAREHOUSE|GODOWN|BANQUET|FARMHOUSE|BUILDING|UNIT|HOUSE)\b",
    re.I,
)


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _text(row: Dict[str, Any]) -> str:
    return _norm(row.get("own_text_redacted") or row.get("clean_description") or "")


def _reasons(row: Dict[str, Any]) -> set:
    return set(row.get("review_reasons") or [])


def _fact_evidence(row: Dict[str, Any]) -> Dict[str, Any]:
    text = _text(row)
    configs = CONFIG_RE.findall(text)
    areas = AREA_RE.findall(text)
    monies = TOTAL_MONEY_RE.findall(text)
    has_rate_language = bool(RATE_RE.search(text))
    has_property_noun = bool(PROPERTY_NOUN_RE.search(text))
    has_location = bool(_norm(row.get("location")))
    has_tx = row.get("transaction") in ("SALE", "RENT")
    has_family = bool(row.get("property_family"))

    property_fact_gate = bool(
        configs
        or areas
        or has_property_noun
        or (
            has_family
            and (monies or has_location)
        )
    )

    strong_property_fact_gate = bool(
        (configs and (areas or monies or has_location))
        or (areas and (has_property_noun or monies or has_location))
        or (has_property_noun and monies and has_location)
    )

    return {
        "configuration_mentions": len(configs),
        "area_mentions": len(areas),
        "money_mentions": len(monies),
        "has_rate_language": has_rate_language,
        "has_property_noun": has_property_noun,
        "has_location": has_location,
        "has_transaction": has_tx,
        "has_property_family": has_family,
        "property_fact_gate": property_fact_gate,
        "strong_property_fact_gate": strong_property_fact_gate,
    }


def _integrity_class(row: Dict[str, Any], facts: Dict[str, Any]) -> Dict[str, Any]:
    text = _text(row)
    sale_hits = len(SALE_RE.findall(text))
    rent_hits = len(RENT_RE.findall(text))
    cfg_hits = int(facts["configuration_mentions"])
    area_hits = int(facts["area_mentions"])
    money_hits = int(facts["money_mentions"])

    mixed_transaction_text = sale_hits > 0 and rent_hits > 0
    repeated_record_signals = cfg_hits >= 2 and area_hits >= 2
    many_money_values = money_hits >= 2

    if row.get("boundary_needs_split") or "V255_CHILD_EXTRACTION_FAILED" in _reasons(row):
        klass = "BOUNDARY_OR_EXTRACTION_FAILURE"
    elif mixed_transaction_text and (cfg_hits >= 1 or area_hits >= 1):
        klass = "MULTIPLE_PROPERTIES_OR_MERGED"
    elif repeated_record_signals and money_hits >= 2:
        klass = "MULTIPLE_PROPERTIES_OR_MERGED"
    elif many_money_values and cfg_hits <= 1 and area_hits <= 1:
        klass = "MULTIPLE_OFFERS_POSSIBLE"
    elif facts["strong_property_fact_gate"]:
        klass = "SINGLE_PROPERTY_LIKELY"
    elif facts["property_fact_gate"]:
        klass = "PROPERTY_FRAGMENT"
    else:
        klass = "INSUFFICIENT_OR_NOISE"

    return {
        "class": klass,
        "sale_text_hits": sale_hits,
        "rent_text_hits": rent_hits,
        "mixed_transaction_text": mixed_transaction_text,
        "repeated_record_signals": repeated_record_signals,
        "multiple_money_values": many_money_values,
    }


def _requirement_gate(row: Dict[str, Any]) -> Dict[str, Any]:
    text = _text(row)
    locations = []
    for key in ("acceptable_locations", "preferred_locations"):
        val = row.get(key)
        if isinstance(val, list):
            locations.extend([_norm(x) for x in val if _norm(x)])

    has_location_signal = bool(
        locations
        or row.get("location")
        or re.search(r"\bPREFERRED\s+LOCATIONS?\b", text, re.I)
    )
    has_budget_signal = bool(
        row.get("budget_min")
        or row.get("budget_max")
        or re.search(r"\bBUDGET\b", text, re.I)
        or TOTAL_MONEY_RE.search(text)
    )
    has_need_signal = bool(
        re.search(r"\b(?:LOOKING\s+FOR|REQUIRED|REQUIREMENT|NEED|WANTED)\b", text, re.I)
    )
    has_property_signal = bool(
        row.get("property_family")
        or CONFIG_RE.search(text)
        or PROPERTY_NOUN_RE.search(text)
    )
    complete_enough = has_need_signal and has_property_signal and (has_location_signal or has_budget_signal)

    return {
        "route": "REQUIREMENT_QUALITY_GATE",
        "has_need_signal": has_need_signal,
        "has_property_signal": has_property_signal,
        "has_location_signal": has_location_signal,
        "has_budget_signal": has_budget_signal,
        "complete_enough_for_requirement_review": complete_enough,
        "transaction_required_for_requirement_gate": False,
        "availability_promotion_allowed": False,
    }


def _analyze_candidate(candidate: Dict[str, Any]) -> Dict[str, Any]:
    row = deepcopy(candidate)
    reasons = _reasons(row)
    facts = _fact_evidence(row)
    integrity = _integrity_class(row, facts)

    requirement = None
    if row.get("classification") == "REQUIREMENT":
        requirement = _requirement_gate(row)

    fact_missing_false_positive = bool(
        "PROPERTY_SPECIFIC_FACT_MISSING" in reasons
        and facts["strong_property_fact_gate"]
        and integrity["class"] == "SINGLE_PROPERTY_LIKELY"
    )

    hard_blockers = sorted(
        r for r in reasons if r in HARD_BLOCKERS
    )
    if row.get("boundary_needs_split") and "BOUNDARY_NEEDS_SPLIT" not in hard_blockers:
        hard_blockers.append("BOUNDARY_NEEDS_SPLIT")

    availability_promotion_eligible = bool(
        row.get("classification") == "AMBIGUOUS"
        and row.get("transaction") in ("SALE", "RENT")
        and bool(row.get("property_family"))
        and bool(row.get("location"))
        and facts["strong_property_fact_gate"]
        and integrity["class"] == "SINGLE_PROPERTY_LIKELY"
        and not hard_blockers
    )

    row["v257a"] = {
        "record_integrity": integrity,
        "fact_evidence": facts,
        "requirement_gate": requirement,
        "property_specific_fact_missing_false_positive": fact_missing_false_positive,
        "availability_promotion_eligible_shadow": availability_promotion_eligible,
        "hard_blockers": hard_blockers,
        "classification_changed": False,
        "transaction_inferred": False,
        "price_totalized_from_rate": False,
        "database_write": False,
        "llm_used": False,
    }
    return row


def _benchmark(engine, limit: int) -> Dict[str, Any]:
    base = v256a._benchmark(engine, limit)
    base_counts = base.get("counts") or {}

    bursts: List[Dict[str, Any]] = []
    all_rows: List[Dict[str, Any]] = []
    integrity_counts = Counter()
    requirement_count = 0
    requirement_complete = 0
    fact_false_positives = 0
    promotion_eligible = 0

    for burst in base.get("bursts") or []:
        out = []
        for candidate in burst.get("candidates") or []:
            row = _analyze_candidate(candidate)
            meta = row["v257a"]
            integrity_counts[meta["record_integrity"]["class"]] += 1
            if meta["requirement_gate"] is not None:
                requirement_count += 1
                if meta["requirement_gate"]["complete_enough_for_requirement_review"]:
                    requirement_complete += 1
            if meta["property_specific_fact_missing_false_positive"]:
                fact_false_positives += 1
            if meta["availability_promotion_eligible_shadow"]:
                promotion_eligible += 1
            out.append(row)
            all_rows.append(row)

        bursts.append({
            "burst_group_id": burst.get("burst_group_id"),
            "candidate_count": len(out),
            "candidates": out,
        })

    examples = {
        "promotion_eligible": [],
        "fact_missing_false_positive": [],
        "multiple_properties_or_merged": [],
        "multiple_offers_possible": [],
        "requirement_gate": [],
        "hard_blocked": [],
    }

    for row in all_rows:
        meta = row["v257a"]
        item = {
            "classification": row.get("classification"),
            "transaction": row.get("transaction"),
            "property_family": row.get("property_family"),
            "location": row.get("location"),
            "quality": row.get("quality"),
            "own_text_redacted": _text(row),
            "review_reasons": list(row.get("review_reasons") or []),
            "v257a": meta,
        }
        if meta["availability_promotion_eligible_shadow"] and len(examples["promotion_eligible"]) < 15:
            examples["promotion_eligible"].append(item)
        if meta["property_specific_fact_missing_false_positive"] and len(examples["fact_missing_false_positive"]) < 15:
            examples["fact_missing_false_positive"].append(item)
        if meta["record_integrity"]["class"] == "MULTIPLE_PROPERTIES_OR_MERGED" and len(examples["multiple_properties_or_merged"]) < 15:
            examples["multiple_properties_or_merged"].append(item)
        if meta["record_integrity"]["class"] == "MULTIPLE_OFFERS_POSSIBLE" and len(examples["multiple_offers_possible"]) < 15:
            examples["multiple_offers_possible"].append(item)
        if meta["requirement_gate"] is not None and len(examples["requirement_gate"]) < 15:
            examples["requirement_gate"].append(item)
        if meta["hard_blockers"] and len(examples["hard_blocked"]) < 15:
            examples["hard_blocked"].append(item)

    total = len(all_rows)
    counts = dict(base_counts)
    counts.update({
        "v257a_entity_count": total,
        "record_integrity_classes": dict(integrity_counts.most_common()),
        "property_specific_fact_false_positives_shadow": fact_false_positives,
        "availability_promotion_eligible_shadow": promotion_eligible,
        "requirements_routed_to_separate_gate": requirement_count,
        "requirements_complete_enough_for_review": requirement_complete,
        "classification_changes_performed": 0,
        "transaction_inferences_performed": 0,
        "database_writes": 0,
        "llm_used": 0,
    })

    gates = {
        "entity_count_unchanged_vs_v256a": total == int(base_counts.get("v256a_entity_count") or 0),
        "no_classification_changes": True,
        "no_transaction_inference": True,
        "no_rate_totalization": True,
        "requirements_separated_from_availability_promotion": True,
        "database_writes_zero": True,
        "llm_zero": True,
        "promotion_candidate": False,
        "requires_manual_example_review": True,
    }

    return {
        "status": "READY",
        "version": VERSION,
        "mode": MODE,
        "base_v256a_version": v256a.VERSION,
        "counts": counts,
        "evidence_gates": gates,
        "examples": examples,
        "safety_contract": {
            "read_only_shadow": True,
            "database_writes": False,
            "canonical_tables_modified": False,
            "orchestrator_modified": False,
            "live_reconstructor_replaced": False,
            "matcher_modified": False,
            "whatsapp_live_modified": False,
            "raw_data_deleted": False,
            "llm_used": False,
            "classification_changed": False,
            "transaction_inferred": False,
            "price_totalized_from_rate": False,
            "sibling_property_specific_fact_inheritance": False,
            "requirements_use_separate_quality_gate": True,
        },
        "writes_performed": 0,
        "bursts": bursts,
    }


def _regression_demo() -> Dict[str, Any]:
    single = {
        "classification": "AMBIGUOUS",
        "transaction": "RENT",
        "property_family": "RESIDENTIAL",
        "location": "Sushant Lok 1",
        "quality": "UNDER_REVIEW",
        "review_reasons": ["CLASSIFICATION_AMBIGUOUS"],
        "boundary_needs_split": False,
        "own_text_redacted": "Sushant Lok 1 | 215 SYDS 3BHK+ SER | 75K+MAINT",
    }
    multi_offer = {
        "classification": "AVAILABILITY",
        "transaction": "RENT",
        "property_family": "RESIDENTIAL",
        "location": "DLF Phase 2",
        "quality": "UNDER_REVIEW",
        "review_reasons": ["PROPERTY_SPECIFIC_FACT_MISSING"],
        "boundary_needs_split": False,
        "own_text_redacted": "DLF PHASE 2 | 400 SYDS 4BHK+ SER | 1.60LAC+ MAINT | FULLY FURNISHED | 1.75LAC+MAINT",
    }
    requirement = {
        "classification": "REQUIREMENT",
        "transaction": None,
        "property_family": "RESIDENTIAL",
        "location": "Vagator",
        "quality": "UNDER_REVIEW",
        "review_reasons": ["TRANSACTION_MISSING", "MULTI_LOCATION_REQUIREMENT_PRESERVED"],
        "boundary_needs_split": False,
        "own_text_redacted": "Looking for a 3/4 BHK independent villa. Preferred Locations: Vagator, Anjuna, Siolim, Assagao. Budget: 1.5-2.25 Lakh/month",
    }
    rate = {
        "classification": "AVAILABILITY",
        "transaction": "SALE",
        "property_family": "LAND",
        "location": "Noida",
        "quality": "UNDER_REVIEW",
        "review_reasons": ["AMBIGUOUS_RATE_NOT_TOTAL_PRICE"],
        "boundary_needs_split": False,
        "own_text_redacted": "Plot size 500 yards Rate is Rs 50000 per yard",
    }

    a = _analyze_candidate(single)["v257a"]
    b = _analyze_candidate(multi_offer)["v257a"]
    c = _analyze_candidate(requirement)["v257a"]
    d = _analyze_candidate(rate)["v257a"]

    passed = (
        a["availability_promotion_eligible_shadow"] is True
        and b["record_integrity"]["class"] == "MULTIPLE_OFFERS_POSSIBLE"
        and c["requirement_gate"] is not None
        and c["requirement_gate"]["transaction_required_for_requirement_gate"] is False
        and d["availability_promotion_eligible_shadow"] is False
        and "AMBIGUOUS_RATE_NOT_TOTAL_PRICE" in d["hard_blockers"]
    )

    return {
        "status": "PASS" if passed else "FAIL",
        "version": VERSION,
        "tests": {
            "single_property_promotion_eligible_shadow": a["availability_promotion_eligible_shadow"],
            "multiple_offer_guard": b["record_integrity"]["class"],
            "requirement_separate_gate": c["requirement_gate"],
            "rate_not_totalized": d["price_totalized_from_rate"] is False,
            "rate_hard_blocked": "AMBIGUOUS_RATE_NOT_TOTAL_PRICE" in d["hard_blockers"],
            "classification_changed": False,
            "transaction_inferred": False,
            "database_write": False,
            "llm_used": False,
        },
        "writes_performed": 0,
    }


def register(core):
    app = core.app
    engine = core.engine
    status_route = "/api/v7/property-ai/record-integrity-v257a/status"

    if any(getattr(r, "path", None) == status_route for r in app.router.routes):
        return {
            "status": "ALREADY_REGISTERED",
            "version": VERSION,
            "route": status_route,
        }

    @app.get(status_route)
    def status():
        return JSONResponse({
            "status": "READY",
            "version": VERSION,
            "mode": MODE,
            "base_v256a_version": v256a.VERSION,
            "read_only_shadow": True,
            "database_writes": False,
            "classification_changed": False,
            "transaction_inferred": False,
            "price_totalized_from_rate": False,
            "requirements_use_separate_quality_gate": True,
            "matcher_modified": False,
            "whatsapp_live_modified": False,
            "llm_used": False,
        })

    @app.get("/api/v7/property-ai/record-integrity-v257a/regression-test")
    def regression_test():
        return JSONResponse(_regression_demo())

    @app.get("/api/v7/property-ai/record-integrity-v257a/preview")
    def preview(limit: int = Query(25, ge=1, le=100)):
        return JSONResponse(_benchmark(engine, limit))

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": status_route,
        "regression": "/api/v7/property-ai/record-integrity-v257a/regression-test",
        "preview": "/api/v7/property-ai/record-integrity-v257a/preview?limit=25",
        "writes_enabled": False,
    }

