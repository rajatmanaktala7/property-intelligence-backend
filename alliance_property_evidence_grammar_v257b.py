from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy
from typing import Any, Dict, List

from fastapi import Query
from fastapi.responses import JSONResponse

import alliance_property_record_integrity_v257a as v257a
import alliance_property_project_location_v256a as v256a

VERSION = "2.5.7B-EVIDENCE-GRAMMAR-INTENT-DIRECTION-FIX"
MODE = "READ_ONLY_SHADOW_EVIDENCE_GRAMMAR_INTENT_DIRECTION"

# Broader but still deterministic area vocabulary.
AREA_RE_V257B = re.compile(
    r"\b\d[\d,]*(?:\.\d+)?\s*(?:"
    r"SQ\.?\s*FT|SQFT|SQ\.?\s*FEET|SFT|"
    r"SQ\.?\s*YD|SQYD|SQYDS|SYD|SYDS|"
    r"YARD|YARDS|YD|YDS|GAJ|"
    r"SQ\.?\s*M|SQM|SQ\.?\s*MT|SQMT|"
    r"ACRE|ACRES|ACER|BIGHA|BIGHE|CARPET"
    r")\b",
    re.I,
)

CONFIG_RE = v257a.CONFIG_RE
TOTAL_MONEY_RE = v257a.TOTAL_MONEY_RE
RATE_RE = v257a.RATE_RE
PROPERTY_NOUN_RE = v257a.PROPERTY_NOUN_RE

# Directional demand language. "requirement" alone is intentionally NOT enough.
DEMAND_DIRECTION_RE = re.compile(
    r"\b(?:"
    r"LOOKING\s+FOR|"
    r"REQUIRED\s*:?\s*(?:FOR\s+)?(?:A|AN|THE)?|"
    r"WANTED\s*:?\s*(?:A|AN|THE)?|"
    r"NEED(?:ED)?\s*:?\s*(?:A|AN|THE)?|"
    r"REQUIREMENT\s+FOR|"
    r"RENTAL\s+REQUIREMENT|"
    r"PURCHASE\s+REQUIREMENT|"
    r"BUYER\s+REQUIREMENT|"
    r"DIRECT\s+CLIENT\s+(?:RENTAL|PURCHASE)?\s*REQUIREMENT"
    r")\b",
    re.I,
)

INCIDENTAL_REQUIREMENT_RE = re.compile(
    r"\b(?:AS\s+PER\s+(?:CLIENT|TENANT|BUYER)\s+REQUIREMENT|"
    r"CLIENT\s+REQUIREMENT\s+CAN\s+BE|"
    r"AS\s+PER\s+REQUIREMENT)\b",
    re.I,
)

AVAILABILITY_DIRECTION_RE = re.compile(
    r"\b(?:"
    r"FOR\s+RENT|FOR\s+SALE|AVAILABLE|AVAILABILITY|"
    r"OWNER\s+(?:GOING|WANTS|ASKING|DEMAND)|"
    r"ASKING\s+(?:PRICE|RENT|RATE)|"
    r"DEMAND\s+\d|READY\s+TO\s+MOVE|"
    r"URGENT\s+RENT|URGENT\s+SALE|PRE\s*RENTED"
    r")\b",
    re.I,
)

HARD_BLOCKERS = set(v257a.HARD_BLOCKERS)


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _text(row: Dict[str, Any]) -> str:
    return _norm(row.get("own_text_redacted") or row.get("clean_description") or "")


def _reasons(row: Dict[str, Any]) -> set:
    return set(row.get("review_reasons") or [])


def _fact_evidence_v257b(row: Dict[str, Any]) -> Dict[str, Any]:
    text = _text(row)
    configs = CONFIG_RE.findall(text)
    areas = AREA_RE_V257B.findall(text)
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
        or (has_family and (monies or has_location))
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
        "area_vocabulary_v257b": True,
    }


def _intent_direction(row: Dict[str, Any]) -> Dict[str, Any]:
    text = _text(row)
    demand = bool(DEMAND_DIRECTION_RE.search(text))
    incidental = bool(INCIDENTAL_REQUIREMENT_RE.search(text))
    availability = bool(AVAILABILITY_DIRECTION_RE.search(text))

    if demand and not availability:
        direction = "DEMAND_LIKELY"
    elif availability and not demand:
        direction = "AVAILABILITY_LIKELY"
    elif demand and availability:
        direction = "MIXED_OR_CONTEXTUAL"
    else:
        direction = "UNRESOLVED"

    # Incidental "client requirement" can never create demand by itself.
    demand_without_incidental_only = demand
    if incidental and not DEMAND_DIRECTION_RE.search(
        re.sub(INCIDENTAL_REQUIREMENT_RE, " ", text)
    ):
        demand_without_incidental_only = False
        if availability:
            direction = "AVAILABILITY_LIKELY"
        elif direction == "DEMAND_LIKELY":
            direction = "UNRESOLVED"

    return {
        "direction": direction,
        "directional_demand_signal": bool(demand_without_incidental_only),
        "availability_signal": availability,
        "incidental_requirement_phrase": incidental,
    }


def _integrity_class_v257b(row: Dict[str, Any], facts: Dict[str, Any]) -> Dict[str, Any]:
    # Preserve V257A's conservative structure, but use corrected area evidence.
    text = _text(row)
    sale_hits = len(v257a.SALE_RE.findall(text))
    rent_hits = len(v257a.RENT_RE.findall(text))
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
    elif many_money_values and cfg_hits == 1 and area_hits == 1:
        # One physical-property signature but two monetary values.
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


def _requirement_gate_v257b(row: Dict[str, Any], intent: Dict[str, Any]) -> Dict[str, Any]:
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
    has_property_signal = bool(
        row.get("property_family")
        or CONFIG_RE.search(text)
        or PROPERTY_NOUN_RE.search(text)
    )
    has_need_signal = intent["directional_demand_signal"]

    complete_enough = bool(
        has_need_signal
        and has_property_signal
        and (has_location_signal or has_budget_signal)
    )

    suspected_upstream_misroute = bool(
        row.get("classification") == "REQUIREMENT"
        and intent["direction"] == "AVAILABILITY_LIKELY"
        and not has_need_signal
    )

    return {
        "route": "REQUIREMENT_QUALITY_GATE_V257B",
        "has_directional_need_signal": has_need_signal,
        "has_property_signal": has_property_signal,
        "has_location_signal": has_location_signal,
        "has_budget_signal": has_budget_signal,
        "complete_enough_for_requirement_review": complete_enough,
        "suspected_upstream_requirement_misroute": suspected_upstream_misroute,
        "transaction_required_for_requirement_gate": False,
        "availability_promotion_allowed": False,
    }


def _analyze_candidate_v257b(candidate: Dict[str, Any]) -> Dict[str, Any]:
    row = deepcopy(candidate)
    facts = _fact_evidence_v257b(row)
    intent = _intent_direction(row)
    integrity = _integrity_class_v257b(row, facts)
    reasons = _reasons(row)

    requirement_gate = None
    if row.get("classification") == "REQUIREMENT":
        requirement_gate = _requirement_gate_v257b(row, intent)

    # In V257B, a "property fact missing" flag is considered a likely false flag
    # whenever strong property facts exist. We do NOT clear any other blockers.
    fact_missing_false_positive = bool(
        "PROPERTY_SPECIFIC_FACT_MISSING" in reasons
        and facts["strong_property_fact_gate"]
        and integrity["class"] not in (
            "BOUNDARY_OR_EXTRACTION_FAILURE",
            "MULTIPLE_PROPERTIES_OR_MERGED",
        )
    )

    hard_blockers = sorted(r for r in reasons if r in HARD_BLOCKERS)
    if row.get("boundary_needs_split") and "BOUNDARY_NEEDS_SPLIT" not in hard_blockers:
        hard_blockers.append("BOUNDARY_NEEDS_SPLIT")

    promotion_eligible = bool(
        row.get("classification") == "AMBIGUOUS"
        and row.get("transaction") in ("SALE", "RENT")
        and bool(row.get("property_family"))
        and bool(row.get("location"))
        and facts["strong_property_fact_gate"]
        and integrity["class"] == "SINGLE_PROPERTY_LIKELY"
        and intent["direction"] != "DEMAND_LIKELY"
        and not hard_blockers
    )

    row["v257b"] = {
        "record_integrity": integrity,
        "fact_evidence": facts,
        "intent_direction": intent,
        "requirement_gate": requirement_gate,
        "property_specific_fact_missing_false_positive_shadow": fact_missing_false_positive,
        "availability_promotion_eligible_shadow": promotion_eligible,
        "hard_blockers": hard_blockers,
        "multiple_offer_values_preserved_unresolved": integrity["class"] == "MULTIPLE_OFFERS_POSSIBLE",
        "classification_changed": False,
        "transaction_inferred": False,
        "price_totalized_from_rate": False,
        "offer_selected": False,
        "database_write": False,
        "llm_used": False,
    }
    return row


def _benchmark(engine, limit: int) -> Dict[str, Any]:
    base = v256a._benchmark(engine, limit)
    base_counts = base.get("counts") or {}

    all_rows: List[Dict[str, Any]] = []
    bursts = []
    integrity_counts = Counter()
    intent_counts = Counter()
    area_recovered = 0
    fact_false_positives = 0
    promotion_eligible = 0
    requirement_count = 0
    requirement_complete = 0
    requirement_misroutes = 0
    multiple_offers = 0

    for burst in base.get("bursts") or []:
        out = []
        for candidate in burst.get("candidates") or []:
            old = v257a._analyze_candidate(candidate)
            old_area = int(old["v257a"]["fact_evidence"]["area_mentions"])
            row = _analyze_candidate_v257b(candidate)
            meta = row["v257b"]

            if int(meta["fact_evidence"]["area_mentions"]) > old_area:
                area_recovered += 1
            integrity_counts[meta["record_integrity"]["class"]] += 1
            intent_counts[meta["intent_direction"]["direction"]] += 1
            if meta["property_specific_fact_missing_false_positive_shadow"]:
                fact_false_positives += 1
            if meta["availability_promotion_eligible_shadow"]:
                promotion_eligible += 1
            if meta["multiple_offer_values_preserved_unresolved"]:
                multiple_offers += 1

            gate = meta["requirement_gate"]
            if gate is not None:
                requirement_count += 1
                if gate["complete_enough_for_requirement_review"]:
                    requirement_complete += 1
                if gate["suspected_upstream_requirement_misroute"]:
                    requirement_misroutes += 1

            out.append(row)
            all_rows.append(row)

        bursts.append({
            "burst_group_id": burst.get("burst_group_id"),
            "candidate_count": len(out),
            "candidates": out,
        })

    examples = {
        "area_vocabulary_recovered": [],
        "fact_missing_false_positive": [],
        "promotion_eligible": [],
        "multiple_offers_possible": [],
        "requirement_complete": [],
        "suspected_requirement_misroute": [],
        "hard_blocked": [],
    }

    for row in all_rows:
        old = v257a._analyze_candidate(row)
        meta = row["v257b"]
        item = {
            "classification": row.get("classification"),
            "transaction": row.get("transaction"),
            "property_family": row.get("property_family"),
            "location": row.get("location"),
            "quality": row.get("quality"),
            "own_text_redacted": _text(row),
            "review_reasons": list(row.get("review_reasons") or []),
            "v257b": meta,
        }

        if (
            int(meta["fact_evidence"]["area_mentions"])
            > int(old["v257a"]["fact_evidence"]["area_mentions"])
            and len(examples["area_vocabulary_recovered"]) < 15
        ):
            examples["area_vocabulary_recovered"].append(item)
        if meta["property_specific_fact_missing_false_positive_shadow"] and len(examples["fact_missing_false_positive"]) < 15:
            examples["fact_missing_false_positive"].append(item)
        if meta["availability_promotion_eligible_shadow"] and len(examples["promotion_eligible"]) < 15:
            examples["promotion_eligible"].append(item)
        if meta["multiple_offer_values_preserved_unresolved"] and len(examples["multiple_offers_possible"]) < 15:
            examples["multiple_offers_possible"].append(item)
        gate = meta["requirement_gate"]
        if gate and gate["complete_enough_for_requirement_review"] and len(examples["requirement_complete"]) < 15:
            examples["requirement_complete"].append(item)
        if gate and gate["suspected_upstream_requirement_misroute"] and len(examples["suspected_requirement_misroute"]) < 15:
            examples["suspected_requirement_misroute"].append(item)
        if meta["hard_blockers"] and len(examples["hard_blocked"]) < 15:
            examples["hard_blocked"].append(item)

    total = len(all_rows)
    counts = dict(base_counts)
    counts.update({
        "v257b_entity_count": total,
        "record_integrity_classes_v257b": dict(integrity_counts.most_common()),
        "intent_direction_classes_v257b": dict(intent_counts.most_common()),
        "area_evidence_recovered_vs_v257a": area_recovered,
        "property_specific_fact_false_positives_shadow": fact_false_positives,
        "availability_promotion_eligible_shadow": promotion_eligible,
        "multiple_offer_records_preserved_unresolved": multiple_offers,
        "requirements_routed_to_separate_gate": requirement_count,
        "requirements_complete_enough_for_review": requirement_complete,
        "suspected_upstream_requirement_misroutes": requirement_misroutes,
        "classification_changes_performed": 0,
        "transaction_inferences_performed": 0,
        "offer_selections_performed": 0,
        "database_writes": 0,
        "llm_used": 0,
    })

    return {
        "status": "READY",
        "version": VERSION,
        "mode": MODE,
        "base_v257a_version": v257a.VERSION,
        "counts": counts,
        "evidence_gates": {
            "entity_count_unchanged_vs_v256a": total == int(base_counts.get("v256a_entity_count") or 0),
            "syd_area_vocabulary_active": True,
            "directional_requirement_intent_active": True,
            "incidental_requirement_phrase_not_sufficient": True,
            "multiple_offer_values_not_collapsed": True,
            "no_classification_changes": True,
            "no_transaction_inference": True,
            "no_rate_totalization": True,
            "database_writes_zero": True,
            "llm_zero": True,
            "promotion_candidate": False,
            "requires_manual_example_review": True,
        },
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
            "offer_selected": False,
            "sibling_property_specific_fact_inheritance": False,
        },
        "writes_performed": 0,
        "bursts": bursts,
    }


def _regression_demo() -> Dict[str, Any]:
    syd = {
        "classification": "AVAILABILITY",
        "transaction": "RENT",
        "property_family": "RESIDENTIAL",
        "location": "DLF Phase 2",
        "review_reasons": ["PROPERTY_SPECIFIC_FACT_MISSING"],
        "boundary_needs_split": False,
        "own_text_redacted": "DLF PHASE 2 | 400 SYDS 4BHK+ SER | 1.60LAC+ MAINT | FULLY FURNISHED | 1.75LAC+MAINT",
    }
    incidental = {
        "classification": "REQUIREMENT",
        "transaction": "RENT",
        "property_family": "RESIDENTIAL",
        "location": None,
        "review_reasons": [],
        "boundary_needs_split": False,
        "own_text_redacted": "Luxury 1st Floor Independent Floor | 3 BHK | Fully Furnished / Semi-Furnished (As per client requirement) | Ready to Move In | Urgent Rent Owner Going Abroad",
    }
    real_req = {
        "classification": "REQUIREMENT",
        "transaction": None,
        "property_family": "RESIDENTIAL",
        "location": "Vagator",
        "review_reasons": ["TRANSACTION_MISSING"],
        "boundary_needs_split": False,
        "own_text_redacted": "Looking for a 3/4 BHK independent villa. Preferred Locations: Vagator Anjuna Siolim Assagao. Budget 1.5 to 2.25 Lakh/month",
    }
    rate = {
        "classification": "AVAILABILITY",
        "transaction": "SALE",
        "property_family": "LAND",
        "location": "Noida",
        "review_reasons": ["AMBIGUOUS_RATE_NOT_TOTAL_PRICE"],
        "boundary_needs_split": False,
        "own_text_redacted": "Plot size 500 yards Rate is Rs 50000 per yard",
    }

    a = _analyze_candidate_v257b(syd)["v257b"]
    b = _analyze_candidate_v257b(incidental)["v257b"]
    c = _analyze_candidate_v257b(real_req)["v257b"]
    d = _analyze_candidate_v257b(rate)["v257b"]

    passed = bool(
        a["fact_evidence"]["area_mentions"] >= 1
        and a["record_integrity"]["class"] == "MULTIPLE_OFFERS_POSSIBLE"
        and a["offer_selected"] is False
        and b["intent_direction"]["direction"] == "AVAILABILITY_LIKELY"
        and b["requirement_gate"]["suspected_upstream_requirement_misroute"] is True
        and c["requirement_gate"]["complete_enough_for_requirement_review"] is True
        and c["requirement_gate"]["transaction_required_for_requirement_gate"] is False
        and "AMBIGUOUS_RATE_NOT_TOTAL_PRICE" in d["hard_blockers"]
        and d["price_totalized_from_rate"] is False
    )

    return {
        "status": "PASS" if passed else "FAIL",
        "version": VERSION,
        "tests": {
            "syd_area_recognized": a["fact_evidence"]["area_mentions"] >= 1,
            "multiple_offer_guard_preserved": a["record_integrity"]["class"],
            "offer_selected": a["offer_selected"],
            "incidental_requirement_not_demand": b["intent_direction"],
            "availability_misroute_detected": b["requirement_gate"]["suspected_upstream_requirement_misroute"],
            "real_requirement_complete": c["requirement_gate"]["complete_enough_for_requirement_review"],
            "requirement_transaction_not_required": c["requirement_gate"]["transaction_required_for_requirement_gate"] is False,
            "rate_hard_blocked": "AMBIGUOUS_RATE_NOT_TOTAL_PRICE" in d["hard_blockers"],
            "rate_not_totalized": d["price_totalized_from_rate"] is False,
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
    status_route = "/api/v7/property-ai/evidence-grammar-v257b/status"

    if any(getattr(r, "path", None) == status_route for r in app.router.routes):
        return {"status": "ALREADY_REGISTERED", "version": VERSION, "route": status_route}

    @app.get(status_route)
    def status():
        return JSONResponse({
            "status": "READY",
            "version": VERSION,
            "mode": MODE,
            "base_v257a_version": v257a.VERSION,
            "read_only_shadow": True,
            "database_writes": False,
            "syd_area_vocabulary_active": True,
            "directional_requirement_intent_active": True,
            "multiple_offer_values_not_collapsed": True,
            "classification_changed": False,
            "transaction_inferred": False,
            "price_totalized_from_rate": False,
            "offer_selected": False,
            "matcher_modified": False,
            "whatsapp_live_modified": False,
            "llm_used": False,
        })

    @app.get("/api/v7/property-ai/evidence-grammar-v257b/regression-test")
    def regression_test():
        return JSONResponse(_regression_demo())

    @app.get("/api/v7/property-ai/evidence-grammar-v257b/preview")
    def preview(limit: int = Query(25, ge=1, le=100)):
        return JSONResponse(_benchmark(engine, limit))

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": status_route,
        "regression": "/api/v7/property-ai/evidence-grammar-v257b/regression-test",
        "preview": "/api/v7/property-ai/evidence-grammar-v257b/preview?limit=25",
        "writes_enabled": False,
    }

