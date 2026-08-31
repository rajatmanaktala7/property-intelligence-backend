from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from fastapi import Query
from fastapi.responses import JSONResponse

import alliance_property_parent_location_v255b as v255b
import alliance_property_location_evidence_v253 as v253

VERSION = "2.5.5C-REMAINING-LOCATION-AND-BOUNDARY-DIAGNOSTIC"
MODE = "READ_ONLY_DIAGNOSTIC"


def _norm(value: Any) -> str:
    return v255b._norm(value)


def _redacted_text(row: Dict[str, Any]) -> str:
    return _norm(
        row.get("own_text_redacted")
        or row.get("clean_description")
        or ""
    )


def _scope(row: Dict[str, Any]) -> Dict[str, Any]:
    return dict((row.get("provenance") or {}).get("v255_scope") or {})


def _parent_location_candidates(scope: Dict[str, Any]) -> List[str]:
    text = " | ".join(
        x for x in (
            _norm(scope.get("broad_location_evidence")),
            _norm(scope.get("parent_header_text_redacted")),
        )
        if x
    )
    if not text:
        return []
    return list(dict.fromkeys(v253._extract_explicit_locations(text)))


def _category(row: Dict[str, Any]) -> str:
    reasons = set(row.get("review_reasons") or [])
    scope = _scope(row)

    if "V255_CHILD_EXTRACTION_FAILED" in reasons or row.get("boundary_needs_split"):
        return "BOUNDARY_OR_CHILD_EXTRACTION_FAILURE"

    if not scope:
        return "NO_PARENT_SCOPE"

    if not scope.get("parent_header_applied"):
        return "PARENT_SCOPE_NOT_APPLIED"

    parent_text = _norm(scope.get("parent_header_text_redacted"))
    broad = _norm(scope.get("broad_location_evidence"))
    candidates = _parent_location_candidates(scope)

    if not parent_text and not broad:
        return "PARENT_SCOPE_WITHOUT_LOCATION_TEXT"

    if len(candidates) == 0:
        return "PARENT_HEADER_LOCATION_NOT_IN_V253_VOCABULARY"

    if len(candidates) > 1:
        return "PARENT_HEADER_MULTI_LOCATION_AMBIGUOUS"

    return "UNEXPLAINED_AFTER_SAFE_PARENT_LOCATION"


def _diagnose(engine, limit: int) -> Dict[str, Any]:
    base = v255b._benchmark(engine, limit)

    missing: List[Dict[str, Any]] = []
    boundary_failures: List[Dict[str, Any]] = []

    for burst in base.get("bursts") or []:
        bid = burst.get("burst_group_id")
        for idx, row in enumerate(burst.get("candidates") or [], start=1):
            reasons = list(row.get("review_reasons") or [])
            scope = _scope(row)

            item = {
                "burst_group_id": bid,
                "candidate_index": idx,
                "classification": row.get("classification"),
                "transaction": row.get("transaction"),
                "property_family": row.get("property_family"),
                "quality": row.get("quality"),
                "location": row.get("location"),
                "own_text_redacted": _redacted_text(row),
                "review_reasons": reasons,
                "parent_header_applied": scope.get("parent_header_applied"),
                "parent_header_type": scope.get("parent_header_type"),
                "parent_header_text_redacted": scope.get("parent_header_text_redacted"),
                "broad_location_evidence": scope.get("broad_location_evidence"),
                "project_evidence": scope.get("project_evidence"),
                "parent_location_candidates": _parent_location_candidates(scope),
                "v255_fallback_used": bool((row.get("v255") or {}).get("fallback_used")),
                "v255b_parent_location_applied": bool(
                    (row.get("v255b") or {}).get("parent_location_applied")
                ),
                "boundary_needs_split": bool(row.get("boundary_needs_split")),
                "database_write": False,
                "llm_used": False,
            }

            if "LOCATION_MISSING" in reasons:
                item["diagnostic_category"] = _category(row)
                missing.append(item)

            if (
                "V255_CHILD_EXTRACTION_FAILED" in reasons
                or row.get("boundary_needs_split")
            ):
                boundary_failures.append(item)

    cats = Counter(x.get("diagnostic_category") for x in missing)
    by_header_type = Counter(x.get("parent_header_type") or "NONE" for x in missing)

    examples = defaultdict(list)
    for row in missing:
        cat = row.get("diagnostic_category")
        if len(examples[cat]) < 8:
            examples[cat].append(row)

    return {
        "status": "READY",
        "version": VERSION,
        "mode": MODE,
        "base_v255b_version": v255b.VERSION,
        "counts": {
            "burst_sample_size": (base.get("counts") or {}).get("burst_sample_size"),
            "v255b_entity_count": (base.get("counts") or {}).get("v255b_entity_count"),
            "v255b_location_missing": (base.get("counts") or {}).get("v255b_location_missing"),
            "diagnosed_location_missing": len(missing),
            "boundary_or_child_failures": len(boundary_failures),
            "database_writes": 0,
            "llm_used": 0,
        },
        "location_missing_categories": dict(cats.most_common()),
        "location_missing_by_parent_header_type": dict(by_header_type.most_common()),
        "priority_examples": dict(examples),
        "boundary_failures": boundary_failures[:10],
        "safety_contract": {
            "read_only_diagnostic": True,
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
            "returned_text_redacted": True,
            "recovery_logic_changed": False,
        },
        "writes_performed": 0,
    }


def _regression_demo() -> Dict[str, Any]:
    no_scope = {
        "review_reasons": ["LOCATION_MISSING"],
        "provenance": {},
    }
    unknown_header = {
        "review_reasons": ["LOCATION_MISSING"],
        "provenance": {
            "v255_scope": {
                "parent_header_applied": True,
                "parent_header_type": "PROJECT_HEADER",
                "parent_header_text_redacted": "SOME UNKNOWN TOWER",
                "broad_location_evidence": None,
            }
        },
    }
    known_header = {
        "review_reasons": ["LOCATION_MISSING"],
        "provenance": {
            "v255_scope": {
                "parent_header_applied": True,
                "parent_header_type": "PROJECT_PLUS_LOCALITY_HEADER",
                "parent_header_text_redacted": "RUSTOMJEE - KHAR WEST",
                "broad_location_evidence": "KHAR WEST",
            }
        },
    }
    boundary = {
        "review_reasons": ["LOCATION_MISSING", "V255_CHILD_EXTRACTION_FAILED"],
        "boundary_needs_split": True,
        "provenance": {},
    }

    tests = {
        "no_scope": _category(no_scope),
        "unknown_header": _category(unknown_header),
        "known_header": _category(known_header),
        "boundary": _category(boundary),
    }

    passed = (
        tests["no_scope"] == "NO_PARENT_SCOPE"
        and tests["unknown_header"] == "PARENT_HEADER_LOCATION_NOT_IN_V253_VOCABULARY"
        and tests["known_header"] == "UNEXPLAINED_AFTER_SAFE_PARENT_LOCATION"
        and tests["boundary"] == "BOUNDARY_OR_CHILD_EXTRACTION_FAILURE"
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
    status_route = "/api/v7/property-ai/location-boundary-diagnostic-v255c/status"

    if any(getattr(r, "path", None) == status_route for r in app.router.routes):
        return {"status": "ALREADY_REGISTERED", "version": VERSION, "route": status_route}

    @app.get(status_route)
    def status():
        return JSONResponse({
            "status": "READY",
            "version": VERSION,
            "mode": MODE,
            "base_v255b_version": v255b.VERSION,
            "read_only_diagnostic": True,
            "database_writes": False,
            "canonical_tables_modified": False,
            "orchestrator_modified": False,
            "live_reconstructor_replaced": False,
            "matcher_modified": False,
            "whatsapp_live_modified": False,
            "raw_data_deleted": False,
            "llm_used": False,
            "recovery_logic_changed": False,
        })

    @app.get("/api/v7/property-ai/location-boundary-diagnostic-v255c/regression-test")
    def regression_test():
        return JSONResponse(_regression_demo())

    @app.get("/api/v7/property-ai/location-boundary-diagnostic-v255c/diagnostic")
    def diagnostic(limit: int = Query(25, ge=1, le=100)):
        return JSONResponse(_diagnose(engine, limit))

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": status_route,
        "regression": "/api/v7/property-ai/location-boundary-diagnostic-v255c/regression-test",
        "diagnostic": "/api/v7/property-ai/location-boundary-diagnostic-v255c/diagnostic?limit=25",
        "writes_enabled": False,
    }

