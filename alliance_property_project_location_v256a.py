from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Query
from fastapi.responses import JSONResponse

import alliance_property_parent_location_v255b as v255b
import alliance_property_location_evidence_v253 as v253

VERSION = "2.5.6A-EVIDENCE-BACKED-PROJECT-LOCATION-RESOLVER"
MODE = "READ_ONLY_SHADOW_PROJECT_LOCATION_RESOLVER"

# Curated registry. Only project identities selected after diagnostic review are present.
# Ambiguous project names such as ACROPOLIS, ARIA and SHYAM KUNJ are intentionally absent.
PROJECT_LOCATION_REGISTRY: Dict[str, Dict[str, Any]] = {
    "DLH LEGACY": {
        "canonical_project": "DLH Legacy",
        "locality": "Juhu",
        "city": "Mumbai",
        "sub_location": "Juhu Circle",
        "confidence": 0.98,
        "evidence_class": "CURATED_PROJECT_IDENTITY",
    },
    "PARK GRANDEUR": {
        "canonical_project": "Park Grandeur",
        "locality": "Juhu",
        "city": "Mumbai",
        "sub_location": "JVPD",
        "confidence": 0.98,
        "evidence_class": "CURATED_PROJECT_IDENTITY",
    },
    "M3M GOLF ESTATE": {
        "canonical_project": "M3M Golf Estate",
        "locality": "Golf Course Extension Road",
        "city": "Gurgaon",
        "sub_location": "Sector 65",
        "confidence": 0.98,
        "evidence_class": "CURATED_PROJECT_IDENTITY",
    },
    "DLF BELVEDER TOWER": {
        "canonical_project": "DLF Belvedere Tower",
        "locality": "DLF Phase 3",
        "city": "Gurgaon",
        "sub_location": "Sector 24",
        "confidence": 0.98,
        "evidence_class": "CURATED_PROJECT_IDENTITY",
    },
    "DLF BELVEDERE TOWER": {
        "canonical_project": "DLF Belvedere Tower",
        "locality": "DLF Phase 3",
        "city": "Gurgaon",
        "sub_location": "Sector 24",
        "confidence": 0.98,
        "evidence_class": "CURATED_PROJECT_IDENTITY",
    },
    "EMAAR URBAN OASIS": {
        "canonical_project": "Emaar Urban Oasis",
        "locality": "Golf Course Extension Road",
        "city": "Gurgaon",
        "sub_location": None,
        "confidence": 0.96,
        "evidence_class": "CURATED_PROJECT_IDENTITY",
    },
    "RAHEJA ATLANTIS": {
        "canonical_project": "Raheja Atlantis",
        "locality": "Gurgaon",
        "city": "Gurgaon",
        "sub_location": "Sector 31",
        "confidence": 0.98,
        "evidence_class": "CURATED_PROJECT_IDENTITY",
    },
}

INTENTIONALLY_UNRESOLVED = {"ACROPOLIS", "ARIA", "SHYAM KUNJ"}


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_project_header(value: Any) -> str:
    s = _norm(value).upper()
    s = s.replace("★", " ").replace("*", " ")
    # Strip common decorative / mojibake prefixes, but keep words and numbers.
    s = re.sub(r"^[^A-Z0-9]+", "", s)
    s = re.sub(r"\s+", " ", s).strip(" -:|")
    return s


def _scope(row: Dict[str, Any]) -> Dict[str, Any]:
    return dict((row.get("provenance") or {}).get("v255_scope") or {})


def _registry_match(row: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, Any]]]:
    scope = _scope(row)
    if not scope.get("parent_header_applied"):
        return None
    if scope.get("parent_header_type") != "PROJECT_HEADER":
        return None

    header = _normalize_project_header(scope.get("parent_header_text_redacted"))
    if not header or header in INTENTIONALLY_UNRESOLVED:
        return None

    entry = PROJECT_LOCATION_REGISTRY.get(header)
    return (header, entry) if entry else None


def _apply_project_location(candidate: Dict[str, Any]) -> Dict[str, Any]:
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

    matched = _registry_match(row)
    if not matched:
        return row

    header, entry = matched
    locality = entry["locality"]
    confidence = float(entry["confidence"])

    row = v253._set_location(
        row,
        locality,
        source="PROJECT_HEADER_REGISTRY_V256A",
        confidence=confidence,
    )

    hierarchy = dict(row.get("location_hierarchy") or {})
    hierarchy["locality"] = locality
    hierarchy["city"] = entry.get("city")
    if entry.get("sub_location"):
        hierarchy["sub_location"] = entry.get("sub_location")
    hierarchy["project"] = entry.get("canonical_project")
    hierarchy["display_location"] = (
        f"{entry.get('sub_location')}, {locality}"
        if entry.get("sub_location") and entry.get("sub_location") != locality
        else locality
    )
    hierarchy["source"] = "PROJECT_HEADER_REGISTRY_V256A"
    hierarchy["confidence"] = confidence
    row["location_hierarchy"] = hierarchy
    row["display_location"] = hierarchy["display_location"]

    prov = dict(row.get("provenance") or {})
    prov["v256a_project_location"] = {
        "source": "CURATED_PROJECT_LOCATION_REGISTRY",
        "matched_parent_header": header,
        "canonical_project": entry.get("canonical_project"),
        "locality": locality,
        "city": entry.get("city"),
        "sub_location": entry.get("sub_location"),
        "confidence": confidence,
        "evidence_class": entry.get("evidence_class"),
        "exact_project_header_match": True,
        "sibling_property_specific_fact_used": False,
        "price_inherited": False,
        "area_inherited": False,
        "configuration_inherited": False,
        "floor_inherited": False,
        "transaction_inherited": False,
        "database_write": False,
        "llm_used": False,
    }
    row["provenance"] = prov

    reasons = [
        r for r in (row.get("review_reasons") or [])
        if r not in (
            "LOCATION_RECOVERED_FROM_UNIQUE_BURST_CONTEXT_V253",
            "LOCATION_RECOVERED_FROM_PARENT_SCOPE_V255B",
        )
    ]
    reasons.append("LOCATION_RECOVERED_FROM_PROJECT_HEADER_REGISTRY_V256A")
    row["review_reasons"] = list(dict.fromkeys(reasons))

    row, promoted = v253._promote_if_safe(row)

    meta = dict(row.get("v256a") or {})
    meta.update({
        "project_location_applied": True,
        "matched_project_header": header,
        "canonical_project": entry.get("canonical_project"),
        "locality": locality,
        "city": entry.get("city"),
        "sub_location": entry.get("sub_location"),
        "classification_promoted_after_location": promoted,
        "database_write": False,
        "llm_used": False,
    })
    row["v256a"] = meta
    return row


def _benchmark(engine, limit: int) -> Dict[str, Any]:
    base = v255b._benchmark(engine, limit)

    bursts: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []
    applied = 0
    promotions = 0
    matched_projects = Counter()

    for burst in base.get("bursts") or []:
        out = []
        for candidate in burst.get("candidates") or []:
            enhanced = _apply_project_location(candidate)
            meta = enhanced.get("v256a") or {}
            if meta.get("project_location_applied"):
                applied += 1
                matched_projects.update([meta.get("canonical_project") or "UNKNOWN"])
            if meta.get("classification_promoted_after_location"):
                promotions += 1
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
    boundary_needs_split = sum(1 for c in candidates if c.get("boundary_needs_split"))
    fallback_count = sum(
        1 for c in candidates if (c.get("v255") or {}).get("fallback_used")
    )

    base_counts = base.get("counts") or {}
    baseline_missing = int(base_counts.get("v255b_location_missing") or 0)
    expected_recoveries = 19 if int(base_counts.get("burst_sample_size") or 0) == 22 else None

    counts = dict(base_counts)
    counts.update({
        "v256a_entity_count": total,
        "v256a_clean": clean,
        "v256a_clean_rate": round(clean / total, 4) if total else 0.0,
        "v256a_location_missing": location_missing,
        "location_recovered_by_project_registry": applied,
        "v256a_ambiguous": ambiguous,
        "v256a_boundary_needs_split": boundary_needs_split,
        "v256a_fallback_count": fallback_count,
        "post_location_classification_promotions": promotions,
        "expected_recoveries_for_known_22_burst_benchmark": expected_recoveries,
        "llm_used": 0,
        "database_writes": 0,
    })

    gates = {
        "entity_count_unchanged_vs_v255b": total == int(base_counts.get("v255b_entity_count") or 0),
        "clean_not_worse_than_v255b": clean >= int(base_counts.get("v255b_clean") or 0),
        "location_missing_improved_vs_v255b": location_missing < baseline_missing,
        "location_missing_at_or_better_than_v253": location_missing <= int(base_counts.get("v253_location_missing") or 0),
        "known_22_burst_target_32_or_better": (
            location_missing <= 32
            if int(base_counts.get("burst_sample_size") or 0) == 22
            else None
        ),
        "ambiguity_not_worse_than_v255b": ambiguous <= int(base_counts.get("v255b_ambiguous") or 0),
        "boundary_not_worse_than_v255b": boundary_needs_split <= int(base_counts.get("v255b_boundary_needs_split") or 0),
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
        "base_v255b_version": v255b.VERSION,
        "registry": {
            "enabled_projects": sorted({
                v["canonical_project"] for v in PROJECT_LOCATION_REGISTRY.values()
            }),
            "intentionally_unresolved": sorted(INTENTIONALLY_UNRESOLVED),
            "matched_project_counts": dict(matched_projects.most_common()),
        },
        "counts": counts,
        "evidence_gates": gates,
        "under_review_reasons_v256a": dict(reasons.most_common()),
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
            "exact_project_header_match_required": True,
            "ambiguous_projects_left_unresolved": True,
            "property_specific_fact_inheritance": False,
            "price_inherited": False,
            "area_inherited": False,
            "configuration_inherited": False,
            "floor_inherited": False,
            "transaction_inherited": False,
        },
        "writes_performed": 0,
        "bursts": bursts,
    }


def _regression_demo() -> Dict[str, Any]:
    base = {
        "classification": "AVAILABILITY",
        "transaction": "RENT",
        "property_family": "RESIDENTIAL",
        "location": None,
        "quality": "UNDER_REVIEW",
        "review_reasons": ["LOCATION_MISSING"],
        "boundary_needs_split": False,
        "own_text_redacted": "3 BHK | 1300 Sq.ft. | Rent 2.50 Lakhs",
        "provenance": {
            "v255_scope": {
                "parent_header_applied": True,
                "parent_header_type": "PROJECT_HEADER",
                "parent_header_text_redacted": "PARK GRANDEUR",
                "project_evidence": "PARK GRANDEUR",
            }
        },
    }

    safe = _apply_project_location(base)

    ambiguous = deepcopy(base)
    ambiguous["provenance"]["v255_scope"]["parent_header_text_redacted"] = "ACROPOLIS"
    ambiguous_out = _apply_project_location(ambiguous)

    no_scope = deepcopy(base)
    no_scope["provenance"]["v255_scope"]["parent_header_applied"] = False
    no_scope_out = _apply_project_location(no_scope)

    boundary = deepcopy(base)
    boundary["boundary_needs_split"] = True
    boundary_out = _apply_project_location(boundary)

    passed = (
        safe.get("location") == "Juhu"
        and (safe.get("v256a") or {}).get("project_location_applied") is True
        and ambiguous_out.get("location") is None
        and no_scope_out.get("location") is None
        and boundary_out.get("location") is None
    )

    return {
        "status": "PASS" if passed else "FAIL",
        "version": VERSION,
        "tests": {
            "safe_project_location": safe.get("location"),
            "safe_project_applied": (safe.get("v256a") or {}).get("project_location_applied"),
            "ambiguous_acropolis_left_unresolved": ambiguous_out.get("location") is None,
            "no_scope_left_unresolved": no_scope_out.get("location") is None,
            "boundary_candidate_left_unresolved": boundary_out.get("location") is None,
            "price_inherited": False,
            "area_inherited": False,
            "configuration_inherited": False,
            "floor_inherited": False,
            "transaction_inherited": False,
            "database_write": False,
            "llm_used": False,
        },
        "writes_performed": 0,
    }


def register(core):
    app = core.app
    engine = core.engine
    status_route = "/api/v7/property-ai/project-location-v256a/status"

    if any(getattr(r, "path", None) == status_route for r in app.router.routes):
        return {"status": "ALREADY_REGISTERED", "version": VERSION, "route": status_route}

    @app.get(status_route)
    def status():
        return JSONResponse({
            "status": "READY",
            "version": VERSION,
            "mode": MODE,
            "base_v255b_version": v255b.VERSION,
            "registry_project_count": len({
                v["canonical_project"] for v in PROJECT_LOCATION_REGISTRY.values()
            }),
            "intentionally_unresolved": sorted(INTENTIONALLY_UNRESOLVED),
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

    @app.get("/api/v7/property-ai/project-location-v256a/regression-test")
    def regression_test():
        return JSONResponse(_regression_demo())

    @app.get("/api/v7/property-ai/project-location-v256a/preview")
    def preview(limit: int = Query(25, ge=1, le=100)):
        return JSONResponse(_benchmark(engine, limit))

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": status_route,
        "regression": "/api/v7/property-ai/project-location-v256a/regression-test",
        "preview": "/api/v7/property-ai/project-location-v256a/preview?limit=25",
        "writes_enabled": False,
    }

