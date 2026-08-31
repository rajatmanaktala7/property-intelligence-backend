from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Query
from fastapi.responses import JSONResponse

import alliance_property_shadow_extraction_v24 as v24
import alliance_property_context_recovery_v245 as v245
import alliance_property_benchmark_stabilizer_v245a as stabilizer

VERSION = "2.4.6-DETERMINISTIC-SHARED-CONTEXT-INTELLIGENCE"
MODE = "READ_ONLY_DETERMINISTIC_CONTEXT_RECOVERY"

AREA_RE = v245.AREA_RE
MONEY_RE = v245.MONEY_RE
CONFIG_RE = v245.CONFIG_RE
MICRO_RE = v245.MICRO_RE

# Floors / unit-specific details are property-specific and are never inherited.
FLOOR_RE = re.compile(
    r"(?i)\b(?:GROUND|LOWER\s+GROUND|UPPER\s+GROUND|"
    r"\d{1,2}(?:ST|ND|RD|TH)?\s+FLOOR|BASEMENT|"
    r"FIRST\s+FLOOR|SECOND\s+FLOOR|THIRD\s+FLOOR)\b"
)

# Project/unit markers are also property-specific unless already in own text.
UNIT_RE = re.compile(
    r"(?i)\b(?:UNIT|SHOP|OFFICE|PLOT|FLAT|APARTMENT)\s*(?:NO\.?|NUMBER|#)?\s*[A-Z0-9/-]+\b"
)

HARD_BLOCKERS = set(v245.HARD_SAFETY_BLOCKERS) | {
    "TRANSACTION_CONTEXT_CONFLICT",
    "PROPERTY_FAMILY_CONTEXT_CONFLICT",
    "LOCALITY_CONTEXT_CONFLICT",
}

POSITIVE_AUDIT_REASONS = {
    "OWN_CONFIGURATION_ACCEPTED_AS_PROPERTY_FACT",
    "OWN_TEXT_TOTAL_MONEY_RECOVERED",
    "ASSET_FAMILY_CORRECTED_FROM_INTENDED_USE",
    "LOCATION_RESOLVED_FROM_OWN_TEXT",
    "BROAD_LOCALITY_RECOVERED_FROM_FACT_FREE_PARENT_REFERENCE",
    "BROAD_LOCALITY_RECOVERED_FROM_SHARED_CONTEXT_V246",
    "TRANSACTION_RECOVERED_FROM_SHARED_CONTEXT_V246",
    "PROPERTY_FAMILY_RECOVERED_FROM_SHARED_CONTEXT_V246",
    "DETERMINISTIC_AVAILABILITY_PROMOTION_V245",
    "DETERMINISTIC_AVAILABILITY_PROMOTION_V246",
}


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _unique(values: List[str]) -> List[str]:
    out: List[str] = []
    for value in values:
        key = v24._norm_key(value)
        if key and not any(v24._norm_key(x) == key for x in out):
            out.append(value)
    return out


def _own_text(row: Dict[str, Any]) -> str:
    return _norm(row.get("own_text_redacted"))


def _has_own_identity_anchor(row: Dict[str, Any]) -> bool:
    own = _own_text(row)
    if not own:
        return False
    return bool(
        v24._asset_family_from_text(own)
        or CONFIG_RE.search(own)
        or MICRO_RE.search(own)
    )


def _has_own_property_fact(row: Dict[str, Any]) -> bool:
    own = _own_text(row)
    return bool(
        row.get("area")
        or row.get("money")
        or CONFIG_RE.search(own)
    )


def _reference_has_property_specific_fact(text_value: str) -> bool:
    s = _norm(text_value)
    if not s:
        return True
    return bool(
        AREA_RE.search(s)
        or MONEY_RE.search(s)
        or CONFIG_RE.search(s)
        or MICRO_RE.search(s)
        or FLOOR_RE.search(s)
        or UNIT_RE.search(s)
    )


def _shared_context_references(row: Dict[str, Any]) -> List[str]:
    """
    Only parent/header/inherited context is considered.
    Sibling facts are deliberately excluded.
    """
    refs: List[str] = []

    for key in ("parent_context_reference_only", "inherited_context"):
        for value in row.get(key) or []:
            text_value = _norm(value)
            if text_value:
                refs.append(text_value)

    return _unique(refs)


def _safe_context_refs(row: Dict[str, Any]) -> List[str]:
    return [
        ref for ref in _shared_context_references(row)
        if not _reference_has_property_specific_fact(ref)
    ]


def _context_transactions(refs: List[str]) -> List[str]:
    values: List[str] = []
    for ref in refs:
        tx = v24._explicit_transaction_from_text(ref)
        if tx:
            values.append(tx)

        key = v24._norm_key(ref)
        if key == "SALE":
            values.append("SALE")
        elif key == "RENT":
            values.append("RENT")

    return _unique(values)


def _context_families(refs: List[str]) -> List[str]:
    values = []
    for ref in refs:
        family = v24._asset_family_from_text(ref)
        if family:
            values.append(family)
    return _unique(values)


def _context_localities(refs: List[str]) -> List[str]:
    values = []
    for ref in refs:
        locality = v24._broad_locality_from_text(ref)
        if locality:
            values.append(locality)
    return _unique(values)


def _append_reason(row: Dict[str, Any], reason: str) -> None:
    reasons = list(row.get("review_reasons") or [])
    if reason not in reasons:
        reasons.append(reason)
    row["review_reasons"] = reasons


def _remove_reason(row: Dict[str, Any], reason: str) -> None:
    row["review_reasons"] = [
        x for x in (row.get("review_reasons") or [])
        if x != reason
    ]


def _recover_transaction(row: Dict[str, Any], refs: List[str]) -> Tuple[bool, Optional[str]]:
    if row.get("transaction"):
        return False, None

    own_tx = v24._explicit_transaction_from_text(_own_text(row))
    if own_tx:
        row["transaction"] = own_tx
        _remove_reason(row, "TRANSACTION_MISSING")
        return True, "OWN_TEXT_EXPLICIT"

    values = _context_transactions(refs)
    if len(values) == 1:
        row["transaction"] = values[0]
        _remove_reason(row, "TRANSACTION_MISSING")
        _append_reason(row, "TRANSACTION_RECOVERED_FROM_SHARED_CONTEXT_V246")
        return True, "SAFE_SHARED_CONTEXT"

    if len(values) > 1:
        _append_reason(row, "TRANSACTION_CONTEXT_CONFLICT")

    return False, None


def _recover_family(row: Dict[str, Any], refs: List[str]) -> Tuple[bool, Optional[str]]:
    if row.get("property_family"):
        return False, None

    own_family = v24._asset_family_from_text(_own_text(row))
    if own_family:
        row["property_family"] = own_family
        _remove_reason(row, "PROPERTY_FAMILY_MISSING")
        return True, "OWN_TEXT_ASSET_IDENTITY"

    values = _context_families(refs)
    if len(values) == 1:
        row["property_family"] = values[0]
        _remove_reason(row, "PROPERTY_FAMILY_MISSING")
        _append_reason(row, "PROPERTY_FAMILY_RECOVERED_FROM_SHARED_CONTEXT_V246")
        return True, "SAFE_SHARED_CONTEXT"

    if len(values) > 1:
        _append_reason(row, "PROPERTY_FAMILY_CONTEXT_CONFLICT")

    return False, None


def _recover_locality(row: Dict[str, Any], refs: List[str]) -> Tuple[bool, Optional[str]]:
    if row.get("location"):
        return False, None

    own_locality = v24._broad_locality_from_text(_own_text(row))
    if own_locality:
        row["location"] = own_locality
        _remove_reason(row, "LOCATION_MISSING")
        return True, "OWN_TEXT_BROAD_LOCALITY"

    values = _context_localities(refs)
    if len(values) != 1:
        if len(values) > 1:
            _append_reason(row, "LOCALITY_CONTEXT_CONFLICT")
        return False, None

    locality = values[0]
    row["location"] = locality
    _remove_reason(row, "LOCATION_MISSING")
    _append_reason(row, "BROAD_LOCALITY_RECOVERED_FROM_SHARED_CONTEXT_V246")

    hierarchy = dict(row.get("location_hierarchy") or {})
    hierarchy["locality"] = locality

    # Micro-location may exist only because it came from child's own text.
    micro = (
        hierarchy.get("block")
        or hierarchy.get("sector")
        or hierarchy.get("phase")
    )
    hierarchy["display_location"] = (
        f"{micro}, {locality}" if micro else locality
    )
    hierarchy["source"] = "SAFE_SHARED_CONTEXT_BROAD_LOCALITY_ONLY"
    hierarchy["confidence"] = 0.88

    row["location_hierarchy"] = hierarchy
    row["display_location"] = hierarchy["display_location"]
    return True, "SAFE_SHARED_CONTEXT"


def _recompute_quality(row: Dict[str, Any]) -> None:
    if row.get("classification") != "AVAILABILITY":
        return

    reasons = list(row.get("review_reasons") or [])

    blockers = set(reasons).intersection(HARD_BLOCKERS)

    if not row.get("transaction"):
        blockers.add("TRANSACTION_MISSING")
    if not row.get("property_family"):
        blockers.add("PROPERTY_FAMILY_MISSING")
    if not row.get("location"):
        blockers.add("LOCATION_MISSING")
    if not _has_own_property_fact(row):
        blockers.add("PROPERTY_SPECIFIC_FACT_MISSING")

    # Positive audit markers never block cleanliness by themselves.
    blockers -= POSITIVE_AUDIT_REASONS

    row["quality"] = "CLEAN" if not blockers else "UNDER_REVIEW"


def _semantic_promote(row: Dict[str, Any]) -> bool:
    if row.get("classification") != "AMBIGUOUS":
        return False

    if row.get("boundary_needs_split"):
        return False

    reasons = set(row.get("review_reasons") or [])
    if reasons.intersection(HARD_BLOCKERS):
        return False

    if not row.get("transaction"):
        return False
    if row.get("property_family") not in ("RESIDENTIAL", "COMMERCIAL", "LAND"):
        return False
    if not row.get("location"):
        return False
    if not _has_own_identity_anchor(row):
        return False
    if not _has_own_property_fact(row):
        return False

    row["classification"] = "AVAILABILITY"
    _remove_reason(row, "CLASSIFICATION_AMBIGUOUS")
    _append_reason(row, "DETERMINISTIC_AVAILABILITY_PROMOTION_V246")

    provenance = dict(row.get("provenance") or {})
    provenance["semantic_classification_v246"] = {
        "method": "DETERMINISTIC_SHARED_CONTEXT_GATE",
        "own_identity_anchor_required": True,
        "own_property_fact_required": True,
        "sibling_property_specific_facts_used": False,
        "llm_required_for_promotion": False,
    }
    row["provenance"] = provenance
    _recompute_quality(row)
    return True


def recover_candidate(base_row: Dict[str, Any]) -> Dict[str, Any]:
    # First preserve all safe 2.4.5 improvements.
    row = v245.recover_candidate(base_row)

    if row.get("classification") == "REQUIREMENT":
        row["context_intelligence_v246"] = {
            "applied": False,
            "requirement_preserved": True,
            "sibling_property_specific_facts_used": False,
            "database_write": False,
        }
        return row

    refs = _safe_context_refs(row)

    before = {
        "quality": row.get("quality"),
        "classification": row.get("classification"),
        "transaction": row.get("transaction"),
        "property_family": row.get("property_family"),
        "location": row.get("location"),
    }

    tx_recovered, tx_source = _recover_transaction(row, refs)
    family_recovered, family_source = _recover_family(row, refs)
    locality_recovered, locality_source = _recover_locality(row, refs)

    promoted = _semantic_promote(row)
    _recompute_quality(row)

    provenance = dict(row.get("provenance") or {})
    provenance["context_intelligence_v246"] = {
        "safe_context_reference_count": len(refs),
        "transaction_source": tx_source,
        "property_family_source": family_source,
        "broad_locality_source": locality_source,
        "price_inherited": False,
        "area_inherited": False,
        "configuration_inherited": False,
        "micro_location_inherited": False,
        "sibling_property_specific_facts_used": False,
    }
    row["provenance"] = provenance

    row["context_intelligence_v246"] = {
        "applied": bool(
            tx_recovered
            or family_recovered
            or locality_recovered
            or promoted
            or before["quality"] != row.get("quality")
        ),
        "safe_context_reference_count": len(refs),
        "transaction_recovered": tx_recovered,
        "property_family_recovered": family_recovered,
        "broad_locality_recovered": locality_recovered,
        "semantic_promoted": promoted,
        "quality_before_v246": before["quality"],
        "quality_after_v246": row.get("quality"),
        "classification_before_v246": before["classification"],
        "classification_after_v246": row.get("classification"),
        "sibling_property_specific_facts_used": False,
        "database_write": False,
    }
    return row


def _reason_counts(candidates: List[Dict[str, Any]]) -> Dict[str, int]:
    counter: Counter[str] = Counter()
    for c in candidates:
        if c.get("quality") != "CLEAN":
            counter.update(c.get("review_reasons") or [])
    return dict(counter.most_common())


def _benchmark(engine, limit: int) -> Dict[str, Any]:
    # Use the fixed, LLM-free 2.4.5A baseline.
    base_result = stabilizer._run_deterministic_base(engine, limit)

    before_candidates: List[Dict[str, Any]] = []
    after_v245_candidates: List[Dict[str, Any]] = []
    after_v246_candidates: List[Dict[str, Any]] = []
    bursts = []
    audit = []

    for burst in base_result.get("bursts") or []:
        out_burst = dict(burst)
        out_candidates = []

        for candidate in burst.get("candidates") or []:
            before = candidate
            after245 = v245.recover_candidate(before)
            after246 = recover_candidate(before)

            before_candidates.append(before)
            after_v245_candidates.append(after245)
            after_v246_candidates.append(after246)
            out_candidates.append(after246)

            if (
                after245.get("quality") != after246.get("quality")
                or after245.get("classification") != after246.get("classification")
                or after245.get("transaction") != after246.get("transaction")
                or after245.get("property_family") != after246.get("property_family")
                or after245.get("location") != after246.get("location")
            ):
                audit.append({
                    "burst_group_id": burst.get("burst_group_id"),
                    "entity_index": candidate.get("entity_index"),
                    "own_text_redacted": candidate.get("own_text_redacted"),
                    "quality_before_v246": after245.get("quality"),
                    "quality_after_v246": after246.get("quality"),
                    "classification_before_v246": after245.get("classification"),
                    "classification_after_v246": after246.get("classification"),
                    "transaction_before_v246": after245.get("transaction"),
                    "transaction_after_v246": after246.get("transaction"),
                    "property_family_before_v246": after245.get("property_family"),
                    "property_family_after_v246": after246.get("property_family"),
                    "location_before_v246": after245.get("location"),
                    "location_after_v246": after246.get("location"),
                    "reasons_after_v246": after246.get("review_reasons") or [],
                    "context_intelligence_v246": after246.get("context_intelligence_v246") or {},
                })

        out_burst["candidates"] = out_candidates
        bursts.append(out_burst)

    total = len(before_candidates)

    def clean_count(rows):
        return sum(1 for x in rows if x.get("quality") == "CLEAN")

    clean0 = clean_count(before_candidates)
    clean245 = clean_count(after_v245_candidates)
    clean246 = clean_count(after_v246_candidates)

    true_new = sum(
        1
        for old, new in zip(after_v245_candidates, after_v246_candidates)
        if old.get("quality") != "CLEAN"
        and new.get("quality") == "CLEAN"
    )

    counts = {
        "burst_sample_size": len(bursts),
        "reconstructed_entity_count": total,
        "deterministic_baseline_clean": clean0,
        "after_v245_clean": clean245,
        "after_v246_clean": clean246,
        "true_newly_recovered_clean_v246": true_new,
        "under_review_after_v246": total - clean246,
        "clean_rate_after_v246": round(clean246 / total, 4) if total else 0.0,
        "availability_after_v246": sum(
            1 for x in after_v246_candidates
            if x.get("classification") == "AVAILABILITY"
        ),
        "requirements_after_v246": sum(
            1 for x in after_v246_candidates
            if x.get("classification") == "REQUIREMENT"
        ),
        "ambiguous_or_noise_after_v246": sum(
            1 for x in after_v246_candidates
            if x.get("classification") in ("AMBIGUOUS", "NOISE")
        ),
        "transaction_recoveries_v246": sum(
            1 for x in after_v246_candidates
            if (x.get("context_intelligence_v246") or {}).get("transaction_recovered")
        ),
        "property_family_recoveries_v246": sum(
            1 for x in after_v246_candidates
            if (x.get("context_intelligence_v246") or {}).get("property_family_recovered")
        ),
        "broad_locality_recoveries_v246": sum(
            1 for x in after_v246_candidates
            if (x.get("context_intelligence_v246") or {}).get("broad_locality_recovered")
        ),
        "semantic_promotions_v246": sum(
            1 for x in after_v246_candidates
            if (x.get("context_intelligence_v246") or {}).get("semantic_promoted")
        ),
        "llm_used": sum(
            1 for x in after_v246_candidates
            if bool((x.get("ai_understanding") or {}).get("llm_used"))
        ),
        "privacy_redacted": total,
    }

    return {
        "status": "READY",
        "version": VERSION,
        "mode": MODE,
        "base_version": v24.VERSION,
        "v245_version": v245.VERSION,
        "benchmark_stabilizer_version": stabilizer.VERSION,
        "benchmark_fingerprint": stabilizer._fingerprint(before_candidates),
        "counts": counts,
        "under_review_reasons_before_v246": _reason_counts(after_v245_candidates),
        "under_review_reasons_after_v246": _reason_counts(after_v246_candidates),
        "changed_candidate_audit": audit,
        "safety_contract": {
            "deterministic_llm_free_benchmark": True,
            "shared_context_sources_only": True,
            "property_specific_sibling_inheritance": False,
            "parent_price_inheritance": False,
            "parent_area_inheritance": False,
            "parent_configuration_inheritance": False,
            "parent_micro_location_inheritance": False,
            "own_identity_anchor_required_for_promotion": True,
            "own_property_fact_required_for_promotion": True,
            "dangerous_money_guards_preserved": True,
            "requirements_not_promoted": True,
            "database_writes": False,
            "canonical_tables_modified": False,
            "matcher_modified": False,
            "whatsapp_live_modified": False,
            "raw_data_deleted": False,
        },
        "writes_performed": 0,
        "bursts": bursts,
    }


def _regression_demo() -> Dict[str, Any]:
    """
    Synthetic safety checks only. Real acceptance is the deterministic 196-entity benchmark.
    """
    tests = []

    safe = {
        "classification": "AMBIGUOUS",
        "transaction": None,
        "property_family": None,
        "location": None,
        "area": {"value": 100, "unit": "sqyd", "sqft": 900},
        "money": {"value": 82500000, "raw": "8.25 cr"},
        "own_text_redacted": "C Block 100 yards 8.25 cr",
        "parent_context_reference_only": ["Kalkaji Residential Floors For Sale"],
        "inherited_context": [],
        "review_reasons": [
            "CLASSIFICATION_AMBIGUOUS",
            "TRANSACTION_MISSING",
            "PROPERTY_FAMILY_MISSING",
            "LOCATION_MISSING",
        ],
        "location_hierarchy": {"block": "C Block"},
        "boundary_needs_split": False,
        "quality": "UNDER_REVIEW",
        "provenance": {},
    }
    safe_out = recover_candidate(safe)
    tests.append({
        "name": "safe_shared_header_positive",
        "classification": safe_out.get("classification"),
        "transaction": safe_out.get("transaction"),
        "property_family": safe_out.get("property_family"),
        "location": safe_out.get("location"),
        "quality": safe_out.get("quality"),
    })

    unsafe = dict(safe)
    unsafe["parent_context_reference_only"] = [
        "Kalkaji Residential Floors For Sale 200 yards 14.25 cr"
    ]
    unsafe_out = recover_candidate(unsafe)
    tests.append({
        "name": "property_specific_parent_not_inherited",
        "transaction": unsafe_out.get("transaction"),
        "property_family": unsafe_out.get("property_family"),
        "location": unsafe_out.get("location"),
        "quality": unsafe_out.get("quality"),
    })

    requirement = dict(safe)
    requirement["classification"] = "REQUIREMENT"
    requirement_out = recover_candidate(requirement)
    tests.append({
        "name": "requirement_preserved",
        "classification": requirement_out.get("classification"),
    })

    passed = (
        safe_out.get("transaction") == "SALE"
        and safe_out.get("property_family") == "RESIDENTIAL"
        and safe_out.get("location") == "Kalkaji"
        and safe_out.get("classification") == "AVAILABILITY"
        and unsafe_out.get("transaction") is None
        and unsafe_out.get("location") is None
        and requirement_out.get("classification") == "REQUIREMENT"
    )

    return {
        "status": "PASS" if passed else "FAIL",
        "version": VERSION,
        "tests": tests,
        "writes_performed": 0,
    }


def register(core):
    app = core.app
    engine = core.engine
    status_route = "/api/v7/property-ai/context-intelligence-v246/status"

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
            "mode": MODE,
            "base_version": v24.VERSION,
            "v245_version": v245.VERSION,
            "benchmark_stabilizer_version": stabilizer.VERSION,
            "deterministic_llm_free_benchmark": True,
            "shared_context_sources_only": True,
            "property_specific_sibling_inheritance": False,
            "parent_price_inheritance": False,
            "parent_area_inheritance": False,
            "parent_configuration_inheritance": False,
            "parent_micro_location_inheritance": False,
            "dangerous_money_guards_preserved": True,
            "requirements_not_promoted": True,
            "database_writes": False,
            "canonical_tables_modified": False,
            "matcher_modified": False,
            "whatsapp_live_modified": False,
            "raw_data_deleted": False,
        })

    @app.get("/api/v7/property-ai/context-intelligence-v246/regression-test")
    def regression_test():
        return JSONResponse(_regression_demo())

    @app.get("/api/v7/property-ai/context-intelligence-v246/preview")
    def preview(limit: int = Query(25, ge=1, le=100)):
        return JSONResponse(_benchmark(engine, limit))

    app.state.alliance_property_context_intelligence_v246_registered = True

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": status_route,
        "preview": "/api/v7/property-ai/context-intelligence-v246/preview?limit=25",
        "regression": "/api/v7/property-ai/context-intelligence-v246/regression-test",
        "writes_enabled": False,
    }

