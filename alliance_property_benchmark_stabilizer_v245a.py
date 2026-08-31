from __future__ import annotations

import contextvars
import hashlib
import json
from collections import Counter
from typing import Any, Dict, List

from fastapi import Query
from fastapi.responses import JSONResponse

import alliance_property_ai_v1 as property_ai
import alliance_property_shadow_extraction_v24 as v24
import alliance_property_context_recovery_v245 as v245

VERSION = "2.4.5A-DETERMINISTIC-BENCHMARK-STABILIZER"
MODE = "READ_ONLY_DETERMINISTIC_BENCHMARK"

# Task-local flag. This avoids globally disabling Gemini for normal application use.
_BENCHMARK_NO_LLM = contextvars.ContextVar(
    "alliance_v245a_benchmark_no_llm",
    default=False,
)

_ORIGINAL_GEMINI = getattr(
    property_ai,
    "_alliance_v245a_original_gemini",
    property_ai._gemini_understanding,
)


def _benchmark_aware_gemini(raw: str):
    if _BENCHMARK_NO_LLM.get():
        return None
    return _ORIGINAL_GEMINI(raw)


# Install the task-local wrapper once.
if not getattr(property_ai, "_alliance_v245a_guard_installed", False):
    property_ai._alliance_v245a_original_gemini = _ORIGINAL_GEMINI
    property_ai._gemini_understanding = _benchmark_aware_gemini
    property_ai._alliance_v245a_guard_installed = True


def _candidate_projection(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """
    Stable projection used only for benchmark fingerprinting.
    Excludes volatile metadata and keeps semantic fields.
    """
    hierarchy = candidate.get("location_hierarchy") or {}
    money = candidate.get("money")
    area = candidate.get("area")

    return {
        "classification": candidate.get("classification"),
        "transaction": candidate.get("transaction"),
        "property_family": candidate.get("property_family"),
        "location": candidate.get("location"),
        "display_location": candidate.get("display_location"),
        "configuration": candidate.get("configuration"),
        "area": area,
        "money": money,
        "quality": candidate.get("quality"),
        "review_reasons": sorted(candidate.get("review_reasons") or []),
        "boundary_method": candidate.get("boundary_method"),
        "boundary_needs_split": candidate.get("boundary_needs_split"),
        "location_hierarchy": {
            "locality": hierarchy.get("locality"),
            "block": hierarchy.get("block"),
            "sector": hierarchy.get("sector"),
            "phase": hierarchy.get("phase"),
            "display_location": hierarchy.get("display_location"),
        },
        "own_text_redacted": candidate.get("own_text_redacted"),
    }


def _fingerprint(candidates: List[Dict[str, Any]]) -> str:
    payload = [
        _candidate_projection(c)
        for c in candidates
    ]
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _reason_counts(candidates: List[Dict[str, Any]]) -> Dict[str, int]:
    counter: Counter[str] = Counter()
    for c in candidates:
        if c.get("quality") != "CLEAN":
            counter.update(c.get("review_reasons") or [])
    return dict(counter.most_common())


def _true_recovery(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    before_reasons = set(before.get("review_reasons") or [])
    after_reasons = set(after.get("review_reasons") or [])

    fields_changed = []
    for field in (
        "classification",
        "transaction",
        "property_family",
        "location",
        "display_location",
        "configuration",
        "area",
        "money",
    ):
        if before.get(field) != after.get(field):
            fields_changed.append(field)

    return {
        "quality_before": before.get("quality"),
        "quality_after": after.get("quality"),
        "became_clean": (
            before.get("quality") != "CLEAN"
            and after.get("quality") == "CLEAN"
        ),
        "reasons_removed": sorted(before_reasons - after_reasons),
        "reasons_added": sorted(after_reasons - before_reasons),
        "fields_changed": fields_changed,
    }


def _run_deterministic_base(engine, limit: int) -> Dict[str, Any]:
    token = _BENCHMARK_NO_LLM.set(True)
    try:
        return v24._benchmark(engine, limit)
    finally:
        _BENCHMARK_NO_LLM.reset(token)


def _benchmark(engine, limit: int) -> Dict[str, Any]:
    # One exact deterministic baseline. Recovery is applied to these same objects.
    base_result = _run_deterministic_base(engine, limit)

    before_candidates: List[Dict[str, Any]] = []
    after_candidates: List[Dict[str, Any]] = []
    bursts = []
    recovery_audit = []

    for burst in base_result.get("bursts") or []:
        out_burst = dict(burst)
        out_candidates = []

        for candidate in burst.get("candidates") or []:
            before = candidate
            after = v245.recover_candidate(before)

            before_candidates.append(before)
            after_candidates.append(after)
            out_candidates.append(after)

            audit = _true_recovery(before, after)
            if (
                audit["became_clean"]
                or audit["fields_changed"]
                or audit["reasons_removed"]
            ):
                recovery_audit.append({
                    "burst_group_id": burst.get("burst_group_id"),
                    "entity_index": candidate.get("entity_index"),
                    "own_text_redacted": candidate.get("own_text_redacted"),
                    **audit,
                })

        out_burst["candidates"] = out_candidates
        bursts.append(out_burst)

    total = len(before_candidates)
    clean_before = sum(
        1 for c in before_candidates
        if c.get("quality") == "CLEAN"
    )
    clean_after = sum(
        1 for c in after_candidates
        if c.get("quality") == "CLEAN"
    )

    true_recovered = [
        x for x in recovery_audit
        if x.get("became_clean")
    ]

    llm_before = sum(
        1 for c in before_candidates
        if bool((c.get("ai_understanding") or {}).get("llm_used"))
    )
    llm_after = sum(
        1 for c in after_candidates
        if bool((c.get("ai_understanding") or {}).get("llm_used"))
    )

    counts = {
        "burst_sample_size": len(bursts),
        "reconstructed_entity_count": total,
        "clean_before": clean_before,
        "clean_after": clean_after,
        "true_newly_recovered_clean": len(true_recovered),
        "under_review_before": total - clean_before,
        "under_review_after": total - clean_after,
        "clean_rate_before": round(clean_before / total, 4) if total else 0.0,
        "clean_rate_after": round(clean_after / total, 4) if total else 0.0,
        "availability_before": sum(
            1 for c in before_candidates
            if c.get("classification") == "AVAILABILITY"
        ),
        "availability_after": sum(
            1 for c in after_candidates
            if c.get("classification") == "AVAILABILITY"
        ),
        "requirements_before": sum(
            1 for c in before_candidates
            if c.get("classification") == "REQUIREMENT"
        ),
        "requirements_after": sum(
            1 for c in after_candidates
            if c.get("classification") == "REQUIREMENT"
        ),
        "ambiguous_or_noise_before": sum(
            1 for c in before_candidates
            if c.get("classification") in ("AMBIGUOUS", "NOISE")
        ),
        "ambiguous_or_noise_after": sum(
            1 for c in after_candidates
            if c.get("classification") in ("AMBIGUOUS", "NOISE")
        ),
        "llm_used_before": llm_before,
        "llm_used_after": llm_after,
        "privacy_redacted": total,
    }

    safety = {
        "benchmark_llm_disabled_task_locally": True,
        "normal_property_ai_llm_preserved": True,
        "same_candidate_objects_compared_before_after": True,
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
    }

    return {
        "status": "READY",
        "version": VERSION,
        "mode": MODE,
        "base_version": v24.VERSION,
        "recovery_version": v245.VERSION,
        "benchmark_fingerprint": _fingerprint(before_candidates),
        "counts": counts,
        "under_review_reasons_before": _reason_counts(before_candidates),
        "under_review_reasons_after": _reason_counts(after_candidates),
        "true_recovery_audit": true_recovered,
        "all_changed_candidate_audit": recovery_audit,
        "safety_contract": safety,
        "writes_performed": 0,
        "bursts": bursts,
    }


def _repeatability_check(engine, limit: int) -> Dict[str, Any]:
    first = _run_deterministic_base(engine, limit)
    second = _run_deterministic_base(engine, limit)

    c1 = [
        c for b in (first.get("bursts") or [])
        for c in (b.get("candidates") or [])
    ]
    c2 = [
        c for b in (second.get("bursts") or [])
        for c in (b.get("candidates") or [])
    ]

    f1 = _fingerprint(c1)
    f2 = _fingerprint(c2)

    return {
        "status": "READY",
        "version": VERSION,
        "limit": limit,
        "first_entity_count": len(c1),
        "second_entity_count": len(c2),
        "first_fingerprint": f1,
        "second_fingerprint": f2,
        "identical": (
            len(c1) == len(c2)
            and f1 == f2
        ),
        "llm_used_first": sum(
            1 for c in c1
            if bool((c.get("ai_understanding") or {}).get("llm_used"))
        ),
        "llm_used_second": sum(
            1 for c in c2
            if bool((c.get("ai_understanding") or {}).get("llm_used"))
        ),
        "writes_performed": 0,
    }


def register(core):
    app = core.app
    engine = core.engine
    status_route = (
        "/api/v7/property-ai/benchmark-stabilizer-v245a/status"
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
            "mode": MODE,
            "base_version": v24.VERSION,
            "recovery_version": v245.VERSION,
            "benchmark_llm_disabled_task_locally": True,
            "normal_property_ai_llm_preserved": True,
            "same_candidate_objects_compared_before_after": True,
            "database_writes": False,
            "canonical_tables_modified": False,
            "matcher_modified": False,
            "whatsapp_live_modified": False,
            "raw_data_deleted": False,
        })

    @app.get(
        "/api/v7/property-ai/benchmark-stabilizer-v245a/preview"
    )
    def preview(
        limit: int = Query(25, ge=1, le=100)
    ):
        return JSONResponse(_benchmark(engine, limit))

    @app.get(
        "/api/v7/property-ai/benchmark-stabilizer-v245a/repeatability"
    )
    def repeatability(
        limit: int = Query(5, ge=1, le=25)
    ):
        return JSONResponse(_repeatability_check(engine, limit))

    app.state.alliance_property_benchmark_stabilizer_v245a_registered = True

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": status_route,
        "preview": (
            "/api/v7/property-ai/"
            "benchmark-stabilizer-v245a/preview?limit=25"
        ),
        "repeatability": (
            "/api/v7/property-ai/"
            "benchmark-stabilizer-v245a/repeatability?limit=5"
        ),
        "writes_enabled": False,
    }

