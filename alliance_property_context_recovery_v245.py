from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional

from fastapi import Query
from fastapi.responses import JSONResponse

import alliance_property_shadow_extraction_v24 as v24
from property_brain.stages.s3_entity_segmentation_v23 import EntityBlock, reconstruct_entities

VERSION = "2.4.5-DETERMINISTIC-CONTEXT-RECOVERY"
MODE = "READ_ONLY_SHADOW_CONTEXT_RECOVERY"

AREA_RE = re.compile(
    r"(?i)\b\d[\d,]*(?:\.\d+)?\s*(?:SQ\.?\s*FT|SQFT|SFT|SQ\.?\s*YD|SQYD|YARDS?|GAJ|SQ\.?\s*M|SQMT|SQM|ACRES?)\b"
)
MONEY_RE = re.compile(
    r"(?i)(?:(?:₹|RS\.?|INR)\s*)?\d[\d,]*(?:\.\d+)?\s*(?:CR|CRORE|CRORES|L|LAC|LACS|LAKH|LAKHS|K|THOUSAND)\b"
)
CONFIG_RE = re.compile(
    r"(?i)\b(?:\d(?:\.\d+)?\s*BHK|\d\s*\.?\s*BR\.?|STUDIO|PENTHOUSE|DUPLEX)\b"
)
MICRO_RE = re.compile(
    r"(?i)\b(?:[A-Z]\s*BLOCK|BLOCK\s*[A-Z]|SECTOR\s*[-:]?\s*\d{1,3}[A-Z]?|PHASE\s*[-:]?\s*(?:\d{1,2}|[IVX]{1,5}))\b"
)

HARD_SAFETY_BLOCKERS = {
    "BOUNDARY_NEEDS_SPLIT",
    "AMBIGUOUS_RATE_NOT_TOTAL_PRICE",
    "IMPLAUSIBLE_SALE_TOTAL_REJECTED",
    "IMPLAUSIBLE_RENT_TOTAL_REJECTED",
    "PHONE_LIKE_OR_EXTREME_MONEY_REJECTED",
    "PHONE_LIKE_MONEY_REJECTED",
    "LOCATION_NOT_SUPPORTED_BY_OWN_TEXT",
    "MICRO_LOCATION_WITHOUT_PARENT_LOCALITY",
    "NOISE",
}


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _own_configuration_fact(row: Dict[str, Any]) -> bool:
    own = _norm(row.get("own_text_redacted"))
    return bool(own and CONFIG_RE.search(own))


def _reference_is_fact_free(value: str) -> bool:
    s = _norm(value)
    if not s:
        return False
    return not any(
        (
            AREA_RE.search(s),
            MONEY_RE.search(s),
            CONFIG_RE.search(s),
            MICRO_RE.search(s),
        )
    )


def _safe_parent_locality(row: Dict[str, Any]) -> Optional[str]:
    refs = row.get("parent_context_reference_only") or []
    resolved: List[str] = []

    for ref in refs:
        text_value = _norm(ref)
        if not _reference_is_fact_free(text_value):
            continue
        loc = v24._broad_locality_from_text(text_value)
        if loc:
            resolved.append(loc)

    unique: List[str] = []
    for value in resolved:
        key = v24._norm_key(value)
        if not any(v24._norm_key(x) == key for x in unique):
            unique.append(value)

    return unique[0] if len(unique) == 1 else None


def _recompute_availability_quality(row: Dict[str, Any]) -> None:
    if row.get("classification") != "AVAILABILITY":
        return

    reasons = list(row.get("review_reasons") or [])
    own_config = _own_configuration_fact(row)

    if own_config:
        reasons = [
            r for r in reasons
            if r != "PROPERTY_SPECIFIC_FACT_MISSING"
        ]
        if "OWN_CONFIGURATION_ACCEPTED_AS_PROPERTY_FACT" not in reasons:
            reasons.append("OWN_CONFIGURATION_ACCEPTED_AS_PROPERTY_FACT")

    blockers = set(reasons).intersection(HARD_SAFETY_BLOCKERS)

    if not row.get("transaction"):
        blockers.add("TRANSACTION_MISSING")
    if not row.get("property_family"):
        blockers.add("PROPERTY_FAMILY_MISSING")
    if not row.get("location"):
        blockers.add("LOCATION_MISSING")

    has_property_fact = bool(
        row.get("area") or
        row.get("money") or
        own_config
    )
    if not has_property_fact:
        blockers.add("PROPERTY_SPECIFIC_FACT_MISSING")

    row["review_reasons"] = list(dict.fromkeys(reasons))
    row["quality"] = "CLEAN" if not blockers else "UNDER_REVIEW"


def _try_safe_parent_locality(row: Dict[str, Any]) -> bool:
    if row.get("classification") == "REQUIREMENT":
        return False
    if row.get("location"):
        return False

    locality = _safe_parent_locality(row)
    if not locality:
        return False

    row["location"] = locality

    hierarchy = dict(row.get("location_hierarchy") or {})
    hierarchy["locality"] = locality

    # Never invent or inherit block/sector/phase.
    micro = (
        hierarchy.get("block")
        or hierarchy.get("sector")
        or hierarchy.get("phase")
    )
    hierarchy["display_location"] = (
        f"{micro}, {locality}"
        if micro
        else locality
    )
    hierarchy["source"] = "FACT_FREE_PARENT_REFERENCE_BROAD_LOCALITY"
    hierarchy["confidence"] = 0.86

    row["location_hierarchy"] = hierarchy
    row["display_location"] = hierarchy["display_location"]

    reasons = [
        r for r in (row.get("review_reasons") or [])
        if r != "LOCATION_MISSING"
    ]
    reasons.append(
        "BROAD_LOCALITY_RECOVERED_FROM_FACT_FREE_PARENT_REFERENCE"
    )
    row["review_reasons"] = list(dict.fromkeys(reasons))

    provenance = dict(row.get("provenance") or {})
    provenance["context_recovery_v245"] = {
        "broad_locality": "FACT_FREE_PARENT_REFERENCE_ONLY",
        "micro_location_inherited": False,
        "sibling_property_specific_facts_used": False,
    }
    row["provenance"] = provenance
    return True


def _try_semantic_promotion_after_recovery(
    row: Dict[str, Any],
) -> bool:
    if row.get("classification") != "AMBIGUOUS":
        return False

    if _own_configuration_fact(row):
        row["review_reasons"] = [
            r for r in (row.get("review_reasons") or [])
            if r != "PROPERTY_SPECIFIC_FACT_MISSING"
        ]

    gate = v24._semantic_promotion_decision_244(row)
    row["semantic_quality_gate_v245"] = gate

    if not gate.get("eligible"):
        return False

    row["classification"] = "AVAILABILITY"
    reasons = [
        r for r in (row.get("review_reasons") or [])
        if r != "CLASSIFICATION_AMBIGUOUS"
    ]
    reasons.append("DETERMINISTIC_AVAILABILITY_PROMOTION_V245")
    row["review_reasons"] = list(dict.fromkeys(reasons))

    provenance = dict(row.get("provenance") or {})
    provenance["semantic_classification_v245"] = {
        "method": "DETERMINISTIC_POST_CONTEXT_RECOVERY_GATE",
        "sibling_property_specific_facts_used": False,
        "llm_required_for_promotion": False,
    }
    row["provenance"] = provenance

    _recompute_availability_quality(row)
    return True


def recover_candidate(base_row: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(base_row)
    row["review_reasons"] = list(
        base_row.get("review_reasons") or []
    )
    row["location_hierarchy"] = dict(
        base_row.get("location_hierarchy") or {}
    )
    row["provenance"] = dict(
        base_row.get("provenance") or {}
    )

    before_quality = row.get("quality")
    before_classification = row.get("classification")
    before_location = row.get("location")

    locality_recovered = _try_safe_parent_locality(row)
    semantic_promoted = _try_semantic_promotion_after_recovery(row)
    _recompute_availability_quality(row)

    row["context_recovery_v245"] = {
        "applied": bool(
            locality_recovered
            or semantic_promoted
            or before_quality != row.get("quality")
        ),
        "safe_parent_locality_recovered": locality_recovered,
        "own_configuration_fact": _own_configuration_fact(row),
        "semantic_promoted": semantic_promoted,
        "quality_before": before_quality,
        "quality_after": row.get("quality"),
        "classification_before": before_classification,
        "classification_after": row.get("classification"),
        "location_before": before_location,
        "location_after": row.get("location"),
        "sibling_property_specific_facts_used": False,
        "database_write": False,
    }
    return row


def _benchmark(engine, limit: int) -> Dict[str, Any]:
    base_result = v24._benchmark(engine, limit)

    bursts = []
    before_candidates: List[Dict[str, Any]] = []
    after_candidates: List[Dict[str, Any]] = []

    for burst in base_result.get("bursts") or []:
        out_burst = dict(burst)
        out_candidates = []

        for candidate in burst.get("candidates") or []:
            before_candidates.append(candidate)
            recovered = recover_candidate(candidate)
            after_candidates.append(recovered)
            out_candidates.append(recovered)

        out_burst["candidates"] = out_candidates
        bursts.append(out_burst)

    def reason_counts(
        candidates: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        counter: Counter[str] = Counter()
        for c in candidates:
            if c.get("quality") != "CLEAN":
                counter.update(c.get("review_reasons") or [])
        return dict(counter.most_common())

    total = len(after_candidates)
    clean_before = sum(
        1 for c in before_candidates
        if c.get("quality") == "CLEAN"
    )
    clean_after = sum(
        1 for c in after_candidates
        if c.get("quality") == "CLEAN"
    )

    counts = {
        "burst_sample_size": len(bursts),
        "reconstructed_entity_count": total,
        "clean_before": clean_before,
        "clean_after": clean_after,
        "newly_recovered_clean": clean_after - clean_before,
        "under_review_after": total - clean_after,
        "clean_rate_before": (
            round(clean_before / total, 4)
            if total else 0.0
        ),
        "clean_rate_after": (
            round(clean_after / total, 4)
            if total else 0.0
        ),
        "availability_after": sum(
            1 for c in after_candidates
            if c.get("classification") == "AVAILABILITY"
        ),
        "requirements_after": sum(
            1 for c in after_candidates
            if c.get("classification") == "REQUIREMENT"
        ),
        "ambiguous_or_noise_after": sum(
            1 for c in after_candidates
            if c.get("classification") in ("AMBIGUOUS", "NOISE")
        ),
        "safe_parent_locality_recoveries": sum(
            1 for c in after_candidates
            if (
                c.get("context_recovery_v245") or {}
            ).get("safe_parent_locality_recovered")
        ),
        "own_configuration_fact_acceptances": sum(
            1 for c in after_candidates
            if (
                c.get("context_recovery_v245") or {}
            ).get("own_configuration_fact")
        ),
        "semantic_promotions_v245": sum(
            1 for c in after_candidates
            if (
                c.get("context_recovery_v245") or {}
            ).get("semantic_promoted")
        ),
        "llm_used": sum(
            1 for c in after_candidates
            if bool(
                (
                    c.get("ai_understanding") or {}
                ).get("llm_used")
            )
        ),
        "privacy_redacted": total,
    }

    return {
        "status": "READY",
        "version": VERSION,
        "base_version": v24.VERSION,
        "mode": MODE,
        "counts": counts,
        "under_review_reasons_before": reason_counts(
            before_candidates
        ),
        "under_review_reasons_after": reason_counts(
            after_candidates
        ),
        "writes_performed": 0,
        "safety_contract": {
            "property_specific_sibling_inheritance": False,
            "parent_price_inheritance": False,
            "parent_area_inheritance": False,
            "parent_configuration_inheritance": False,
            "parent_micro_location_inheritance": False,
            "fact_free_parent_broad_locality_only": True,
            "dangerous_money_guards_preserved": True,
            "requirements_not_promoted": True,
            "contacts_in_output": False,
            "database_writes": False,
            "canonical_tables_modified": False,
            "matcher_modified": False,
            "whatsapp_live_modified": False,
            "raw_data_deleted": False,
        },
        "bursts": bursts,
    }


def _demo_entity(
    text_value: str,
    parent_refs: Optional[List[str]] = None,
) -> EntityBlock:
    return EntityBlock(
        index=1,
        own_text=text_value,
        inherited_context=[],
        sibling_facts_do_not_copy=[],
        parent_context_reference_only=parent_refs or [],
        method="single",
        needs_split=False,
        reason=None,
    )


def _regression_demo() -> Dict[str, Any]:
    cases: Dict[str, Any] = {}

    ents = reconstruct_entities(
        "3 BHK Flat available for rent in GK-2"
    )
    if ents:
        base = v24._extract_entity(ents[0], "demo")
        cases["configuration_fact_positive"] = (
            recover_candidate(base)
        )

    template = {
        "transaction": "SALE",
        "property_family": "RESIDENTIAL",
        "location": "Kalkaji",
        "evidence": "validated",
        "scope": "BLOCK_ANCHOR_TEMPLATE_ONLY",
    }

    price_base = v24._extract_entity(
        _demo_entity("8.25 cr"),
        "demo",
        template,
    )
    cases["price_only_negative"] = recover_candidate(price_base)

    block_base = v24._extract_entity(
        _demo_entity("C Block"),
        "demo",
        template,
    )
    cases["block_only_negative"] = recover_candidate(block_base)

    rate_ents = reconstruct_entities(
        "Land Available For Sale Dwarka Sector 24 "
        "Size 1 Acres Plot Price 70Cr/Acres"
    )
    if rate_ents:
        cases["rate_negative"] = recover_candidate(
            v24._extract_entity(rate_ents[0], "demo")
        )

    req_ents = reconstruct_entities(
        "DIRECT CLIENT RENTAL REQUIREMENT 3/4 BHK VILLA "
        "Preferred Locations: Vagator Anjuna Siolim Assagao "
        "Budget 1.5 - 2.25 Lakh/month"
    )
    if req_ents:
        cases["requirement_preserved"] = recover_candidate(
            v24._extract_entity(req_ents[0], "demo")
        )

    child = _demo_entity(
        "3 BHK Flat for rent 1800 sqft",
        ["Options available in Greater Kailash 2"],
    )
    child_base = v24._extract_entity(child, "demo")
    cases["fact_free_parent_locality_positive"] = (
        recover_candidate(child_base)
    )

    return cases


def register(core):
    app = core.app
    engine = core.engine
    status_route = (
        "/api/v7/property-ai/context-recovery-v245/status"
    )

    if any(
        getattr(route, "path", None) == status_route
        for route in app.router.routes
    ):
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
            "base_version": v24.VERSION,
            "mode": MODE,
            "own_configuration_is_property_fact": True,
            "fact_free_parent_broad_locality_recovery": True,
            "property_specific_sibling_inheritance": False,
            "parent_price_inheritance": False,
            "parent_area_inheritance": False,
            "parent_configuration_inheritance": False,
            "parent_micro_location_inheritance": False,
            "dangerous_money_guards_preserved": True,
            "requirements_not_promoted": True,
            "privacy_redaction_preserved": True,
            "database_writes": False,
            "canonical_tables_modified": False,
            "matcher_modified": False,
            "whatsapp_live_modified": False,
            "raw_data_deleted": False,
        })

    @app.get(
        "/api/v7/property-ai/context-recovery-v245/regression-test"
    )
    def regression_test():
        return JSONResponse(_regression_demo())

    @app.get(
        "/api/v7/property-ai/context-recovery-v245/preview"
    )
    def preview(
        limit: int = Query(25, ge=1, le=100)
    ):
        return JSONResponse(_benchmark(engine, limit))

    app.state.alliance_property_context_recovery_v245_registered = True

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": status_route,
        "preview": (
            "/api/v7/property-ai/"
            "context-recovery-v245/preview?limit=25"
        ),
        "writes_enabled": False,
    }

