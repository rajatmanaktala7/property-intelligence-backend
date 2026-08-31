from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List

from fastapi import Query
from fastapi.responses import JSONResponse

import alliance_property_parent_location_v255b as v255b

VERSION = "2.5.5D-PROJECT-HEADER-INVENTORY-DIAGNOSTIC"
MODE = "READ_ONLY_PROJECT_HEADER_INVENTORY"


def _norm(value: Any) -> str:
    return v255b._norm(value)


def _scope(row: Dict[str, Any]) -> Dict[str, Any]:
    return dict((row.get("provenance") or {}).get("v255_scope") or {})


def _clean_header(value: Any) -> str:
    s = _norm(value)
    # Remove common mojibake / decorative prefixes without changing words.
    s = re.sub(r"^[^A-Za-z0-9]+", "", s)
    return _norm(s)


def _text_signature(value: Any) -> str:
    s = _norm(value).upper()
    # Normalize changing numeric/property values only for pattern grouping.
    s = re.sub(r"\d+(?:[.,]\d+)*", "#", s)
    s = re.sub(r"\s+", " ", s).strip()
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()[:12]


def _diagnose(engine, limit: int) -> Dict[str, Any]:
    base = v255b._benchmark(engine, limit)

    projects: Dict[str, Dict[str, Any]] = {}
    no_scope_groups: Dict[str, Dict[str, Any]] = {}
    boundary_failures: List[Dict[str, Any]] = []

    total_missing = 0
    project_header_missing = 0
    no_scope_missing = 0

    for burst in base.get("bursts") or []:
        bid = burst.get("burst_group_id")
        for idx, row in enumerate(burst.get("candidates") or [], start=1):
            reasons = set(row.get("review_reasons") or [])
            if "LOCATION_MISSING" not in reasons:
                continue

            total_missing += 1
            scope = _scope(row)
            own = _norm(row.get("own_text_redacted") or row.get("clean_description") or "")

            if "V255_CHILD_EXTRACTION_FAILED" in reasons or row.get("boundary_needs_split"):
                boundary_failures.append({
                    "burst_group_id": bid,
                    "candidate_index": idx,
                    "classification": row.get("classification"),
                    "transaction": row.get("transaction"),
                    "property_family": row.get("property_family"),
                    "own_text_redacted": own,
                    "review_reasons": list(row.get("review_reasons") or []),
                    "fallback_used": bool((row.get("v255") or {}).get("fallback_used")),
                    "boundary_needs_split": bool(row.get("boundary_needs_split")),
                    "database_write": False,
                    "llm_used": False,
                })

            if scope.get("parent_header_applied") and scope.get("parent_header_type") == "PROJECT_HEADER":
                project_header_missing += 1
                raw_header = _norm(scope.get("parent_header_text_redacted"))
                clean_header = _clean_header(raw_header)
                key = clean_header.upper() or raw_header.upper() or "UNKNOWN_PROJECT_HEADER"

                rec = projects.setdefault(key, {
                    "project_header": clean_header or raw_header,
                    "raw_header_example": raw_header,
                    "project_evidence": scope.get("project_evidence"),
                    "count": 0,
                    "transactions": Counter(),
                    "property_families": Counter(),
                    "burst_ids": set(),
                    "examples": [],
                })
                rec["count"] += 1
                rec["transactions"][str(row.get("transaction") or "NONE")] += 1
                rec["property_families"][str(row.get("property_family") or "NONE")] += 1
                rec["burst_ids"].add(str(bid))
                if len(rec["examples"]) < 3:
                    rec["examples"].append({
                        "burst_group_id": bid,
                        "candidate_index": idx,
                        "own_text_redacted": own,
                        "classification": row.get("classification"),
                        "transaction": row.get("transaction"),
                        "property_family": row.get("property_family"),
                    })
                continue

            if not scope.get("parent_header_applied"):
                no_scope_missing += 1
                sig = _text_signature(own)
                rec = no_scope_groups.setdefault(sig, {
                    "signature": sig,
                    "count": 0,
                    "transactions": Counter(),
                    "property_families": Counter(),
                    "fallback_count": 0,
                    "examples": [],
                })
                rec["count"] += 1
                rec["transactions"][str(row.get("transaction") or "NONE")] += 1
                rec["property_families"][str(row.get("property_family") or "NONE")] += 1
                if bool((row.get("v255") or {}).get("fallback_used")):
                    rec["fallback_count"] += 1
                if len(rec["examples"]) < 3:
                    rec["examples"].append({
                        "burst_group_id": bid,
                        "candidate_index": idx,
                        "own_text_redacted": own,
                        "classification": row.get("classification"),
                        "transaction": row.get("transaction"),
                        "property_family": row.get("property_family"),
                        "review_reasons": list(row.get("review_reasons") or []),
                    })

    project_rows = []
    for rec in projects.values():
        project_rows.append({
            **{k: v for k, v in rec.items() if k not in ("transactions", "property_families", "burst_ids")},
            "transactions": dict(rec["transactions"]),
            "property_families": dict(rec["property_families"]),
            "distinct_bursts": len(rec["burst_ids"]),
        })
    project_rows.sort(key=lambda x: (-x["count"], x["project_header"]))

    no_scope_rows = []
    for rec in no_scope_groups.values():
        no_scope_rows.append({
            **{k: v for k, v in rec.items() if k not in ("transactions", "property_families")},
            "transactions": dict(rec["transactions"]),
            "property_families": dict(rec["property_families"]),
        })
    no_scope_rows.sort(key=lambda x: (-x["count"], x["signature"]))

    return {
        "status": "READY",
        "version": VERSION,
        "mode": MODE,
        "base_v255b_version": v255b.VERSION,
        "counts": {
            "burst_sample_size": (base.get("counts") or {}).get("burst_sample_size"),
            "v255b_entity_count": (base.get("counts") or {}).get("v255b_entity_count"),
            "location_missing_total": total_missing,
            "project_header_location_missing": project_header_missing,
            "parent_scope_not_applied": no_scope_missing,
            "distinct_project_headers": len(project_rows),
            "distinct_no_scope_patterns": len(no_scope_rows),
            "boundary_or_child_failures": len(boundary_failures),
            "database_writes": 0,
            "llm_used": 0,
        },
        "project_header_inventory": project_rows,
        "no_scope_pattern_inventory": no_scope_rows,
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
            "project_location_mapping_applied": False,
            "recovery_logic_changed": False,
        },
        "writes_performed": 0,
    }


def _regression_demo() -> Dict[str, Any]:
    h1 = _clean_header("â¨ ARIA")
    h2 = _clean_header("â¨ PARK GRANDEUR")
    passed = h1 == "ARIA" and h2 == "PARK GRANDEUR"
    return {
        "status": "PASS" if passed else "FAIL",
        "version": VERSION,
        "tests": {
            "aria_header": h1,
            "park_grandeur_header": h2,
            "project_location_mapping_applied": False,
            "database_write": False,
            "llm_used": False,
        },
        "writes_performed": 0,
    }


def register(core):
    app = core.app
    engine = core.engine
    status_route = "/api/v7/property-ai/project-header-inventory-v255d/status"

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
            "project_location_mapping_applied": False,
            "recovery_logic_changed": False,
            "llm_used": False,
        })

    @app.get("/api/v7/property-ai/project-header-inventory-v255d/regression-test")
    def regression_test():
        return JSONResponse(_regression_demo())

    @app.get("/api/v7/property-ai/project-header-inventory-v255d/diagnostic")
    def diagnostic(limit: int = Query(25, ge=1, le=100)):
        return JSONResponse(_diagnose(engine, limit))

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": status_route,
        "regression": "/api/v7/property-ai/project-header-inventory-v255d/regression-test",
        "diagnostic": "/api/v7/property-ai/project-header-inventory-v255d/diagnostic?limit=25",
        "writes_enabled": False,
    }

