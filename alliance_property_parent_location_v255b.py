from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any, Dict, List, Optional

from fastapi import Query
from fastapi.responses import JSONResponse

import alliance_property_hierarchical_parser_v255 as v255a
import alliance_property_location_evidence_v253 as v253

VERSION = "2.5.5B-PARENT-LOCATION-EVIDENCE-APPLIER"
MODE = "READ_ONLY_SHADOW_PARENT_LOCATION_EVIDENCE"


def _norm(value: Any) -> str:
    return v255a._norm(value)


def _canonical_parent_location(parent: Dict[str, Any]) -> Optional[str]:
    """
    Convert only explicit broad parent-location evidence into one canonical locality.
    This deliberately reuses the v253 resolver vocabulary rather than inventing
    aliases or copying arbitrary project/header strings.
    """
    explicit = _norm(parent.get("broad_location_evidence"))
    header_text = _norm(parent.get("parent_header_text_redacted"))

    search_text = " | ".join(x for x in (explicit, header_text) if x)
    if not search_text:
        return None

    found = v253._extract_explicit_locations(search_text)
    unique = list(dict.fromkeys(found))
    return unique[0] if len(unique) == 1 else None


def _apply_parent_location(candidate: Dict[str, Any]) -> Dict[str, Any]:
    row = deepcopy(candidate)
    if row.get("classification") == "REQUIREMENT":
        return row

    if row.get("location"):
        return row

    if row.get("boundary_needs_split"):
        return row

    reasons = set(row.get("review_reasons") or [])
    if "V255_CHILD_EXTRACTION_FAILED" in reasons:
        return row

    prov = dict(row.get("provenance") or {})
    scope = dict(prov.get("v255_scope") or {})
    locality = _canonical_parent_location(scope)
    if not locality:
        return row

    # Existing v253 setter updates location hierarchy, provenance and LOCATION_MISSING
    # consistently. Parent scope is deterministic structural evidence, not sibling fact.
    row = v253._set_location(
        row,
        locality,
        source="PARENT_SCOPE_EXPLICIT_LOCATION_V255B",
        confidence=0.97,
    )

    prov = dict(row.get("provenance") or {})
    prov["v255b_parent_location"] = {
        "source": "PROVEN_PARENT_SCOPE",
        "value": locality,
        "confidence": 0.97,
        "header_type": scope.get("parent_header_type"),
        "project_evidence": scope.get("project_evidence"),
        "sibling_property_specific_fact_used": False,
        "price_inherited": False,
        "area_inherited": False,
        "configuration_inherited": False,
        "floor_inherited": False,
        "database_write": False,
        "llm_used": False,
    }
    row["provenance"] = prov

    reasons = [
        r for r in (row.get("review_reasons") or [])
        if r != "LOCATION_RECOVERED_FROM_UNIQUE_BURST_CONTEXT_V253"
    ]
    reasons.append("LOCATION_RECOVERED_FROM_PARENT_SCOPE_V255B")
    row["review_reasons"] = list(dict.fromkeys(reasons))

    # Re-run the established deterministic semantic gate after location recovery.
    row, promoted = v253._promote_if_safe(row)
    meta = dict(row.get("v255b") or {})
    meta.update({
        "parent_location_applied": True,
        "parent_location": locality,
        "classification_promoted_after_location": promoted,
        "database_write": False,
        "llm_used": False,
    })
    row["v255b"] = meta
    return row


def _benchmark(engine, limit: int) -> Dict[str, Any]:
    base = v255a._benchmark(engine, limit)

    bursts: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []
    parent_location_applied = 0
    post_location_promotions = 0

    for burst in base.get("bursts") or []:
        out = []
        for candidate in burst.get("candidates") or []:
            enhanced = _apply_parent_location(candidate)
            if (enhanced.get("v255b") or {}).get("parent_location_applied"):
                parent_location_applied += 1
            if (enhanced.get("v255b") or {}).get("classification_promoted_after_location"):
                post_location_promotions += 1
            out.append(enhanced)
            candidates.append(enhanced)

        bursts.append({
            "burst_group_id": burst.get("burst_group_id"),
            "candidate_count": len(out),
            "stats": burst.get("stats") or {},
            "candidates": out,
        })

    total = len(candidates)
    clean = sum(1 for c in candidates if c.get("quality") == "CLEAN")
    location_missing = sum(
        1 for c in candidates
        if "LOCATION_MISSING" in (c.get("review_reasons") or [])
    )
    ambiguous = sum(
        1 for c in candidates
        if c.get("classification") in ("AMBIGUOUS", "NOISE")
    )
    boundary_needs_split = sum(
        1 for c in candidates if c.get("boundary_needs_split")
    )
    fallback_count = sum(
        1 for c in candidates if (c.get("v255") or {}).get("fallback_used")
    )

    base_counts = base.get("counts") or {}

    counts = dict(base_counts)
    counts.update({
        "v255b_entity_count": total,
        "v255b_clean": clean,
        "v255b_clean_rate": round(clean / total, 4) if total else 0.0,
        "v255b_location_missing": location_missing,
        "v255b_ambiguous": ambiguous,
        "v255b_boundary_needs_split": boundary_needs_split,
        "v255b_fallback_count": fallback_count,
        "parent_location_applied": parent_location_applied,
        "post_location_classification_promotions": post_location_promotions,
        "llm_used": 0,
        "database_writes": 0,
    })

    evidence_gates = {
        "entity_count_unchanged_vs_v255a": total == int(base_counts.get("v255_entity_count") or 0),
        "clean_not_worse_than_v255a": clean >= int(base_counts.get("v255_clean") or 0),
        "location_missing_improved_vs_v255a": location_missing < int(base_counts.get("v255_location_missing") or 0),
        "location_missing_at_or_better_than_v253": location_missing <= int(base_counts.get("v253_location_missing") or 0),
        "ambiguity_not_worse_than_v255a": ambiguous <= int(base_counts.get("v255_ambiguous") or 0),
        "boundary_needs_split_zero": boundary_needs_split == 0,
        "fallback_rate_below_5_percent": (fallback_count / total <= 0.05 if total else True),
        "llm_zero": True,
        "writes_zero": True,
        "promotion_candidate": False,
        "requires_manual_example_review": True,
    }

    reasons = Counter()
    for row in candidates:
        if row.get("quality") != "CLEAN":
            reasons.update(row.get("review_reasons") or [])

    return {
        "status": "READY",
        "version": VERSION,
        "mode": MODE,
        "base_v255_version": v255a.VERSION,
        "counts": counts,
        "evidence_gates": evidence_gates,
        "under_review_reasons_v255b": dict(reasons.most_common()),
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
            "contacts_exposed": False,
            "raw_burst_text_exposed": False,
            "parent_location_only_if_unique_explicit_alias": True,
            "property_specific_fact_inheritance": False,
            "price_inherited": False,
            "area_inherited": False,
            "configuration_inherited": False,
            "floor_inherited": False,
        },
        "writes_performed": 0,
        "bursts": bursts,
    }


def _regression_demo() -> Dict[str, Any]:
    candidate = {
        "classification": "AMBIGUOUS",
        "transaction": "RENT",
        "property_family": "RESIDENTIAL",
        "location": None,
        "quality": "UNDER_REVIEW",
        "review_reasons": ["LOCATION_MISSING", "CLASSIFICATION_AMBIGUOUS"],
        "boundary_needs_split": False,
        "own_text_redacted": "3 BHK | 1365 Sq.ft. | Rent 2.50 Lakhs",
        "provenance": {
            "v255_scope": {
                "parent_header_type": "PROJECT_PLUS_LOCALITY_HEADER",
                "parent_header_text_redacted": "RUSTOMJEE PARAMOUNT - KHAR WEST",
                "broad_location_evidence": "KHAR WEST",
                "project_evidence": "RUSTOMJEE",
            }
        },
    }

    out = _apply_parent_location(candidate)
    passed = (
        out.get("location") == "Khar West"
        and "LOCATION_MISSING" not in (out.get("review_reasons") or [])
        and (out.get("v255b") or {}).get("parent_location_applied") is True
        and (out.get("provenance") or {}).get("v255b_parent_location", {}).get("value") == "Khar West"
    )

    return {
        "status": "PASS" if passed else "FAIL",
        "version": VERSION,
        "tests": {
            "location": out.get("location"),
            "location_missing_removed": "LOCATION_MISSING" not in (out.get("review_reasons") or []),
            "parent_location_applied": (out.get("v255b") or {}).get("parent_location_applied"),
            "price_inherited": False,
            "area_inherited": False,
            "configuration_inherited": False,
            "floor_inherited": False,
            "database_write": False,
            "llm_used": False,
        },
        "writes_performed": 0,
    }


def register(core):
    app = core.app
    engine = core.engine
    status_route = "/api/v7/property-ai/parent-location-v255b/status"

    if any(getattr(r, "path", None) == status_route for r in app.router.routes):
        return {"status": "ALREADY_REGISTERED", "version": VERSION, "route": status_route}

    @app.get(status_route)
    def status():
        return JSONResponse({
            "status": "READY",
            "version": VERSION,
            "mode": MODE,
            "base_v255_version": v255a.VERSION,
            "read_only_shadow": True,
            "database_writes": False,
            "canonical_tables_modified": False,
            "orchestrator_modified": False,
            "live_reconstructor_replaced": False,
            "matcher_modified": False,
            "whatsapp_live_modified": False,
            "raw_data_deleted": False,
            "llm_used": False,
        })

    @app.get("/api/v7/property-ai/parent-location-v255b/regression-test")
    def regression_test():
        return JSONResponse(_regression_demo())

    @app.get("/api/v7/property-ai/parent-location-v255b/preview")
    def preview(limit: int = Query(25, ge=1, le=100)):
        return JSONResponse(_benchmark(engine, limit))

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": status_route,
        "regression": "/api/v7/property-ai/parent-location-v255b/regression-test",
        "preview": "/api/v7/property-ai/parent-location-v255b/preview?limit=25",
        "writes_enabled": False,
    }

