from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Set

from fastapi import Query
from fastapi.responses import JSONResponse

import alliance_property_project_location_v256a as v256a

VERSION = "2.5.6B-REMAINING-RECORD-DIAGNOSTIC"
MODE = "READ_ONLY_REMAINING_RECORD_DIAGNOSTIC"

AMBIGUOUS_PROJECT_HEADERS = {
    "ACROPOLIS",
    "ARIA",
    "SHYAM KUNJ",
}

HARD_FAILURE_REASONS = {
    "V255_CHILD_EXTRACTION_FAILED",
    "BOUNDARY_NEEDS_SPLIT",
    "TRANSACTION_MISSING",
    "AMBIGUOUS_RATE_NOT_TOTAL_PRICE",
    "IMPLAUSIBLE_SALE_TOTAL_REJECTED",
    "IMPLAUSIBLE_RENT_TOTAL_REJECTED",
    "PHONE_LIKE_OR_EXTREME_MONEY_REJECTED",
    "PHONE_LIKE_MONEY_REJECTED",
}


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _scope(row: Dict[str, Any]) -> Dict[str, Any]:
    return dict((row.get("provenance") or {}).get("v255_scope") or {})


def _header(row: Dict[str, Any]) -> str:
    return v256a._normalize_project_header(
        _scope(row).get("parent_header_text_redacted")
    )


def _signature(value: Any) -> str:
    s = _norm(value).upper()
    s = re.sub(r"\d+(?:[.,]\d+)*", "#", s)
    s = re.sub(r"\s+", " ", s).strip()
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()[:12]


def _safe_example(burst_id: Any, idx: int, row: Dict[str, Any]) -> Dict[str, Any]:
    scope = _scope(row)
    return {
        "burst_group_id": burst_id,
        "candidate_index": idx,
        "classification": row.get("classification"),
        "transaction": row.get("transaction"),
        "property_family": row.get("property_family"),
        "location": row.get("location"),
        "quality": row.get("quality"),
        "parent_header_type": scope.get("parent_header_type"),
        "parent_header_applied": bool(scope.get("parent_header_applied")),
        "parent_header_text_redacted": _norm(scope.get("parent_header_text_redacted")),
        "project_evidence": scope.get("project_evidence"),
        "fallback_used": bool((row.get("v255") or {}).get("fallback_used")),
        "boundary_needs_split": bool(row.get("boundary_needs_split")),
        "own_text_redacted": _norm(
            row.get("own_text_redacted") or row.get("clean_description") or ""
        ),
        "review_reasons": list(row.get("review_reasons") or []),
    }


def _diagnostic_labels(row: Dict[str, Any]) -> List[str]:
    reasons: Set[str] = set(row.get("review_reasons") or [])
    scope = _scope(row)
    header = _header(row)
    labels: List[str] = []

    if "LOCATION_MISSING" in reasons:
        if (
            scope.get("parent_header_applied")
            and scope.get("parent_header_type") == "PROJECT_HEADER"
            and header in AMBIGUOUS_PROJECT_HEADERS
        ):
            labels.append("AMBIGUOUS_PROJECT_IDENTITY")

        if not scope.get("parent_header_applied"):
            labels.append("NO_PARENT_SCOPE_LOCATION")

        if (
            "AMBIGUOUS_PROJECT_IDENTITY" not in labels
            and "NO_PARENT_SCOPE_LOCATION" not in labels
        ):
            labels.append("OTHER_LOCATION_MISSING")

    if "PROPERTY_SPECIFIC_FACT_MISSING" in reasons:
        labels.append("PROPERTY_SPECIFIC_FACT_MISSING")

    if row.get("classification") in ("AMBIGUOUS", "NOISE") or "CLASSIFICATION_AMBIGUOUS" in reasons:
        labels.append("CLASSIFICATION_AMBIGUOUS")

    hard = sorted(
        r for r in reasons
        if r in HARD_FAILURE_REASONS
    )
    if row.get("boundary_needs_split") and "BOUNDARY_NEEDS_SPLIT" not in hard:
        hard.append("BOUNDARY_NEEDS_SPLIT")
    if hard:
        labels.append("HARD_FAILURE")

    if not labels and row.get("quality") != "CLEAN":
        labels.append("OTHER_UNDER_REVIEW")

    return list(dict.fromkeys(labels))


def _diagnose(engine, limit: int) -> Dict[str, Any]:
    base = v256a._benchmark(engine, limit)
    base_counts = base.get("counts") or {}

    bucket_counts = Counter()
    ambiguous_projects = defaultdict(lambda: {
        "count": 0,
        "transactions": Counter(),
        "property_families": Counter(),
        "distinct_bursts": set(),
        "examples": [],
    })
    no_scope_patterns = defaultdict(lambda: {
        "count": 0,
        "fallback_count": 0,
        "transactions": Counter(),
        "property_families": Counter(),
        "examples": [],
    })
    property_fact_patterns = defaultdict(lambda: {
        "count": 0,
        "examples": [],
    })
    classification_patterns = defaultdict(lambda: {
        "count": 0,
        "transactions": Counter(),
        "property_families": Counter(),
        "examples": [],
    })
    hard_failures: List[Dict[str, Any]] = []
    other_location: List[Dict[str, Any]] = []
    under_review_examples: List[Dict[str, Any]] = []

    all_candidates = 0
    clean = 0
    under_review = 0

    for burst in base.get("bursts") or []:
        bid = burst.get("burst_group_id")
        for idx, row in enumerate(burst.get("candidates") or [], start=1):
            all_candidates += 1
            if row.get("quality") == "CLEAN":
                clean += 1
                continue

            under_review += 1
            example = _safe_example(bid, idx, row)
            if len(under_review_examples) < 20:
                under_review_examples.append(example)

            labels = _diagnostic_labels(row)
            for label in labels:
                bucket_counts[label] += 1

            header = _header(row)

            if "AMBIGUOUS_PROJECT_IDENTITY" in labels:
                rec = ambiguous_projects[header or "UNKNOWN"]
                rec["count"] += 1
                rec["transactions"][str(row.get("transaction") or "NONE")] += 1
                rec["property_families"][str(row.get("property_family") or "NONE")] += 1
                rec["distinct_bursts"].add(str(bid))
                if len(rec["examples"]) < 4:
                    rec["examples"].append(example)

            if "NO_PARENT_SCOPE_LOCATION" in labels:
                sig = _signature(example["own_text_redacted"])
                rec = no_scope_patterns[sig]
                rec["count"] += 1
                rec["transactions"][str(row.get("transaction") or "NONE")] += 1
                rec["property_families"][str(row.get("property_family") or "NONE")] += 1
                if example["fallback_used"]:
                    rec["fallback_count"] += 1
                if len(rec["examples"]) < 4:
                    rec["examples"].append(example)

            if "PROPERTY_SPECIFIC_FACT_MISSING" in labels:
                sig = _signature(example["own_text_redacted"])
                rec = property_fact_patterns[sig]
                rec["count"] += 1
                if len(rec["examples"]) < 4:
                    rec["examples"].append(example)

            if "CLASSIFICATION_AMBIGUOUS" in labels:
                reasons = set(example["review_reasons"])
                key_parts = [
                    "LOC_MISSING" if "LOCATION_MISSING" in reasons else "LOC_OK",
                    str(row.get("transaction") or "NO_TX"),
                    str(row.get("property_family") or "NO_FAMILY"),
                    "FACT_MISSING" if "PROPERTY_SPECIFIC_FACT_MISSING" in reasons else "FACT_PRESENT",
                ]
                key = "|".join(key_parts)
                rec = classification_patterns[key]
                rec["count"] += 1
                rec["transactions"][str(row.get("transaction") or "NONE")] += 1
                rec["property_families"][str(row.get("property_family") or "NONE")] += 1
                if len(rec["examples"]) < 4:
                    rec["examples"].append(example)

            if "HARD_FAILURE" in labels:
                if len(hard_failures) < 25:
                    hard_failures.append(example)

            if "OTHER_LOCATION_MISSING" in labels:
                if len(other_location) < 20:
                    other_location.append(example)

    ambiguous_rows = []
    for header, rec in ambiguous_projects.items():
        ambiguous_rows.append({
            "project_header": header,
            "count": rec["count"],
            "transactions": dict(rec["transactions"]),
            "property_families": dict(rec["property_families"]),
            "distinct_bursts": len(rec["distinct_bursts"]),
            "examples": rec["examples"],
        })
    ambiguous_rows.sort(key=lambda x: (-x["count"], x["project_header"]))

    no_scope_rows = []
    for sig, rec in no_scope_patterns.items():
        no_scope_rows.append({
            "signature": sig,
            "count": rec["count"],
            "fallback_count": rec["fallback_count"],
            "transactions": dict(rec["transactions"]),
            "property_families": dict(rec["property_families"]),
            "examples": rec["examples"],
        })
    no_scope_rows.sort(key=lambda x: (-x["count"], x["signature"]))

    fact_rows = []
    for sig, rec in property_fact_patterns.items():
        fact_rows.append({
            "signature": sig,
            "count": rec["count"],
            "examples": rec["examples"],
        })
    fact_rows.sort(key=lambda x: (-x["count"], x["signature"]))

    classification_rows = []
    for pattern, rec in classification_patterns.items():
        classification_rows.append({
            "pattern": pattern,
            "count": rec["count"],
            "transactions": dict(rec["transactions"]),
            "property_families": dict(rec["property_families"]),
            "examples": rec["examples"],
        })
    classification_rows.sort(key=lambda x: (-x["count"], x["pattern"]))

    location_missing = int(base_counts.get("v256a_location_missing") or 0)
    ambiguous = int(base_counts.get("v256a_ambiguous") or 0)

    return {
        "status": "READY",
        "version": VERSION,
        "mode": MODE,
        "base_v256a_version": v256a.VERSION,
        "counts": {
            "burst_sample_size": base_counts.get("burst_sample_size"),
            "v256a_entity_count": base_counts.get("v256a_entity_count"),
            "v256a_clean": base_counts.get("v256a_clean"),
            "v256a_clean_rate": base_counts.get("v256a_clean_rate"),
            "v256a_location_missing": location_missing,
            "v256a_ambiguous": ambiguous,
            "under_review_total": under_review,
            "diagnosed_entity_total": all_candidates,
            "bucket_counts_overlap_allowed": dict(bucket_counts.most_common()),
            "ambiguous_project_identity_count": bucket_counts.get("AMBIGUOUS_PROJECT_IDENTITY", 0),
            "no_parent_scope_location_count": bucket_counts.get("NO_PARENT_SCOPE_LOCATION", 0),
            "property_specific_fact_missing_count": bucket_counts.get("PROPERTY_SPECIFIC_FACT_MISSING", 0),
            "classification_ambiguous_count": bucket_counts.get("CLASSIFICATION_AMBIGUOUS", 0),
            "hard_failure_count": bucket_counts.get("HARD_FAILURE", 0),
            "other_location_missing_count": bucket_counts.get("OTHER_LOCATION_MISSING", 0),
            "database_writes": 0,
            "llm_used": 0,
        },
        "ambiguous_project_inventory": ambiguous_rows,
        "no_parent_scope_inventory": no_scope_rows,
        "property_specific_fact_missing_inventory": fact_rows,
        "classification_ambiguity_inventory": classification_rows,
        "hard_failures": hard_failures,
        "other_location_missing": other_location,
        "under_review_sample": under_review_examples,
        "diagnostic_contract": {
            "buckets_overlap_by_design": True,
            "no_recovery_logic_applied": True,
            "no_project_location_mapping_added": True,
            "no_classification_promotion_added": True,
            "no_boundary_repair_added": True,
            "examples_are_redacted_candidate_text_only": True,
        },
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
            "recovery_logic_changed": False,
        },
        "writes_performed": 0,
    }


def _regression_demo() -> Dict[str, Any]:
    def row(*, reasons, classification="AMBIGUOUS", tx="SALE", family="RESIDENTIAL",
            header_applied=False, header_type=None, header=None, boundary=False):
        return {
            "classification": classification,
            "transaction": tx,
            "property_family": family,
            "quality": "UNDER_REVIEW",
            "review_reasons": reasons,
            "boundary_needs_split": boundary,
            "provenance": {
                "v255_scope": {
                    "parent_header_applied": header_applied,
                    "parent_header_type": header_type,
                    "parent_header_text_redacted": header,
                }
            },
        }

    ambiguous_project = row(
        reasons=["LOCATION_MISSING", "CLASSIFICATION_AMBIGUOUS"],
        header_applied=True,
        header_type="PROJECT_HEADER",
        header="ACROPOLIS",
    )
    no_scope = row(
        reasons=["LOCATION_MISSING", "PROPERTY_SPECIFIC_FACT_MISSING"],
        header_applied=False,
    )
    hard = row(
        reasons=["TRANSACTION_MISSING", "V255_CHILD_EXTRACTION_FAILED"],
        tx=None,
    )

    a = _diagnostic_labels(ambiguous_project)
    n = _diagnostic_labels(no_scope)
    h = _diagnostic_labels(hard)

    passed = (
        "AMBIGUOUS_PROJECT_IDENTITY" in a
        and "CLASSIFICATION_AMBIGUOUS" in a
        and "NO_PARENT_SCOPE_LOCATION" in n
        and "PROPERTY_SPECIFIC_FACT_MISSING" in n
        and "HARD_FAILURE" in h
    )

    return {
        "status": "PASS" if passed else "FAIL",
        "version": VERSION,
        "tests": {
            "ambiguous_project_bucket": a,
            "no_scope_bucket": n,
            "hard_failure_bucket": h,
            "no_recovery_logic_applied": True,
            "database_write": False,
            "llm_used": False,
        },
        "writes_performed": 0,
    }


def register(core):
    app = core.app
    engine = core.engine
    status_route = "/api/v7/property-ai/remaining-diagnostic-v256b/status"

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

    @app.get("/api/v7/property-ai/remaining-diagnostic-v256b/regression-test")
    def regression_test():
        return JSONResponse(_regression_demo())

    @app.get("/api/v7/property-ai/remaining-diagnostic-v256b/diagnostic")
    def diagnostic(limit: int = Query(25, ge=1, le=100)):
        return JSONResponse(_diagnose(engine, limit))

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": status_route,
        "regression": "/api/v7/property-ai/remaining-diagnostic-v256b/regression-test",
        "diagnostic": "/api/v7/property-ai/remaining-diagnostic-v256b/diagnostic?limit=25",
        "writes_enabled": False,
    }

