from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from fastapi import Query
from fastapi.responses import JSONResponse

import alliance_property_location_evidence_v253 as v253

VERSION = "2.5.3A-UNRESOLVED-LOCATION-DIAGNOSTIC"
MODE = "READ_ONLY_DIAGNOSTIC"


def _safe_text(row: Dict[str, Any]) -> str:
    return str(row.get("own_text_redacted") or "").strip()


def _diagnose(engine, limit: int) -> Dict[str, Any]:
    base = v253._benchmark(engine, limit)

    unresolved: List[Dict[str, Any]] = []
    reason_counter: Counter[str] = Counter()

    for burst in base.get("bursts") or []:
        burst_id = burst.get("burst_group_id")
        unique_burst_location = burst.get("unique_burst_location")
        candidates = burst.get("candidates") or []

        for idx, candidate in enumerate(candidates):
            reasons = list(candidate.get("review_reasons") or [])
            if "LOCATION_MISSING" not in reasons:
                continue

            reason_counter.update(reasons)

            prev_row = candidates[idx - 1] if idx > 0 else None
            next_row = candidates[idx + 1] if idx + 1 < len(candidates) else None

            unresolved.append({
                "burst_group_id": burst_id,
                "candidate_index": idx + 1,
                "entity_count_in_burst": len(candidates),
                "unique_burst_location": unique_burst_location,
                "classification": candidate.get("classification"),
                "transaction": candidate.get("transaction"),
                "property_family": candidate.get("property_family"),
                "own_text_redacted": _safe_text(candidate),
                "previous_candidate_text_redacted": _safe_text(prev_row or {}),
                "next_candidate_text_redacted": _safe_text(next_row or {}),
                "review_reasons": reasons,
                "v253": candidate.get("v253") or {},
                "location_hierarchy": candidate.get("location_hierarchy") or {},
                "provenance_location": (candidate.get("provenance") or {}).get("v253_location"),
            })

    return {
        "status": "READY",
        "version": VERSION,
        "mode": MODE,
        "base_v253_version": v253.VERSION,
        "counts": {
            "burst_sample_size": (base.get("counts") or {}).get("burst_sample_size"),
            "entity_count": (base.get("counts") or {}).get("entity_count"),
            "v253_clean": (base.get("counts") or {}).get("v253_clean"),
            "v253_clean_rate": (base.get("counts") or {}).get("v253_clean_rate"),
            "location_missing_after": (base.get("counts") or {}).get("location_missing_after"),
            "unresolved_diagnostic_rows": len(unresolved),
            "llm_used": (base.get("counts") or {}).get("llm_used"),
            "boundary_needs_split": (base.get("counts") or {}).get("boundary_needs_split"),
        },
        "unresolved_reason_counts": dict(reason_counter.most_common()),
        "unresolved": unresolved,
        "safety_contract": {
            "read_only": True,
            "database_writes": False,
            "canonical_tables_modified": False,
            "orchestrator_modified": False,
            "live_reconstructor_replaced": False,
            "matcher_modified": False,
            "whatsapp_live_modified": False,
            "raw_data_deleted": False,
            "raw_burst_text_exposed": False,
            "contacts_exposed": False,
            "uses_redacted_candidate_text_only": True,
        },
        "writes_performed": 0,
    }


def _regression_demo() -> Dict[str, Any]:
    fake = {
        "bursts": [{
            "burst_group_id": "demo-burst",
            "unique_burst_location": None,
            "candidates": [
                {
                    "own_text_redacted": "PROJECT / LOCALITY HEADER",
                    "review_reasons": [],
                },
                {
                    "classification": "AVAILABILITY",
                    "transaction": "RENT",
                    "property_family": "RESIDENTIAL",
                    "own_text_redacted": "3 BHK 1300 Sq.ft Rent 2.50 Lakhs",
                    "review_reasons": ["LOCATION_MISSING"],
                    "v253": {},
                },
                {
                    "own_text_redacted": "NEXT PROPERTY",
                    "review_reasons": [],
                },
            ],
        }],
        "counts": {
            "burst_sample_size": 1,
            "entity_count": 3,
            "v253_clean": 2,
            "v253_clean_rate": 0.6667,
            "location_missing_after": 1,
            "llm_used": 0,
            "boundary_needs_split": 0,
        },
    }

    # Exercise the same window logic without needing a DB.
    burst = fake["bursts"][0]
    c = burst["candidates"][1]
    row = {
        "burst_group_id": burst["burst_group_id"],
        "candidate_index": 2,
        "previous_candidate_text_redacted": _safe_text(burst["candidates"][0]),
        "own_text_redacted": _safe_text(c),
        "next_candidate_text_redacted": _safe_text(burst["candidates"][2]),
    }

    passed = (
        row["burst_group_id"] == "demo-burst"
        and row["candidate_index"] == 2
        and row["previous_candidate_text_redacted"] == "PROJECT / LOCALITY HEADER"
        and row["next_candidate_text_redacted"] == "NEXT PROPERTY"
    )

    return {
        "status": "PASS" if passed else "FAIL",
        "version": VERSION,
        "row": row,
        "writes_performed": 0,
    }


def register(core):
    app = core.app
    engine = core.engine
    status_route = "/api/v7/property-ai/location-diagnostic-v253a/status"

    if any(getattr(r, "path", None) == status_route for r in app.router.routes):
        return {"status": "ALREADY_REGISTERED", "version": VERSION, "route": status_route}

    @app.get(status_route)
    def status():
        return JSONResponse({
            "status": "READY",
            "version": VERSION,
            "mode": MODE,
            "base_v253_version": v253.VERSION,
            "read_only": True,
            "database_writes": False,
            "raw_burst_text_exposed": False,
            "contacts_exposed": False,
        })

    @app.get("/api/v7/property-ai/location-diagnostic-v253a/regression-test")
    def regression_test():
        return JSONResponse(_regression_demo())

    @app.get("/api/v7/property-ai/location-diagnostic-v253a/unresolved")
    def unresolved(limit: int = Query(25, ge=1, le=100)):
        return JSONResponse(_diagnose(engine, limit))

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": status_route,
        "regression": "/api/v7/property-ai/location-diagnostic-v253a/regression-test",
        "unresolved": "/api/v7/property-ai/location-diagnostic-v253a/unresolved?limit=25",
        "writes_enabled": False,
    }

