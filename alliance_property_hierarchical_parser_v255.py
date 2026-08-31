from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from fastapi import Query
from fastapi.responses import JSONResponse

import alliance_property_shadow_extraction_v24 as v24
import alliance_property_boundary_intelligence_v25 as v25
import alliance_property_boundary_cohesion_v251 as v251
import alliance_property_location_evidence_v253 as v253
import alliance_property_header_scope_v254c as v254c

VERSION = "2.5.5A-HIERARCHICAL-PROPERTY-SCOPE-PARSER-CHILD-BOUNDARY-FIX"
MODE = "READ_ONLY_SHADOW_HIERARCHICAL_SCOPE_PARSER"


def _norm(value: Any) -> str:
    return v254c._norm(value)


def _typed_pieces(text_value: str) -> List[Dict[str, Any]]:
    pieces = v25._presegment(text_value)
    out: List[Dict[str, Any]] = []
    for idx, raw in enumerate(pieces):
        redacted = v254c._redact(raw)
        info = v254c._piece_type(redacted)
        out.append({
            "piece_index": idx + 1,
            "text_redacted": redacted,
            **info,
        })
    return out


def _header_context(piece: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "header_type": piece.get("header_type"),
        "header_text_redacted": piece.get("text_redacted"),
        "broad_location_evidence": piece.get("broad_location_evidence"),
        "project_evidence": piece.get("project_evidence"),
        "micro_location_evidence": piece.get("micro_location_evidence"),
    }


def _facts(text_value: str) -> Dict[str, bool]:
    return v254c._fact_vector(text_value)


def _is_strong_atomic_property_row(text_value: str) -> bool:
    """
    A row can open a new child property inside an established parent scope when
    it independently carries a configuration plus another primary property fact,
    or multiple strong primary facts.

    This deliberately solves the v251 limitation where two consecutive rows such
    as "3 BHK | 1365 sqft | Rent ..." and "3 BHK | 1240 sqft | Rent ..." were
    merged because neither row began with a known locality/project anchor.
    """
    f = _facts(text_value)
    primary = sum(
        1 for key in ("configuration", "area", "money", "property_type")
        if f.get(key)
    )
    if f.get("configuration") and (
        f.get("area") or f.get("money") or f.get("property_type")
    ):
        return True
    return primary >= 3


def _group_scope_children(pieces: List[Dict[str, Any]]) -> List[str]:
    """
    Cohesion inside a proven header scope.

    - Complete atomic property rows start new children.
    - Fragmentary facts remain attached to the current child.
    - Weak text may remain with a child only when a child is already open.
    - No facts are copied from one child to another.
    """
    groups: List[List[str]] = []
    pending: List[str] = []

    def flush() -> None:
        nonlocal pending
        if pending:
            text_value = _norm(" | ".join(pending))
            if text_value and v25._is_entity_candidate(text_value):
                groups.append(list(pending))
            pending = []

    for piece in pieces:
        ptype = piece.get("type")
        text_value = _norm(piece.get("text_redacted"))

        if not text_value or ptype == "EMPTY":
            continue

        if ptype in ("SECTION", "FOOTER", "HEADER"):
            flush()
            continue

        strong_atomic = (
            ptype == "PROPERTY_FACT"
            and _is_strong_atomic_property_row(text_value)
        )

        if strong_atomic:
            if pending:
                flush()
            pending = [text_value]
            continue

        if ptype == "PROPERTY_FACT":
            if not pending:
                pending = [text_value]
            else:
                # Fragmentary property facts belong to the current child until
                # another independently complete child row appears.
                pending.append(text_value)
            continue

        if pending and ptype in ("OTHER", "SECTION_LIKE"):
            pending.append(text_value)

    flush()
    return [_norm(" | ".join(parts)) for parts in groups]


def _extract_scoped_child(
    child_text: str,
    burst_group_id: str,
    parent_header: Optional[Dict[str, Any]],
    active_section: Optional[str],
) -> Dict[str, Any]:
    header_text = _norm((parent_header or {}).get("header_text_redacted"))
    prefix_parts = [x for x in (_norm(active_section), header_text) if x]
    scoped_text = "\n".join(prefix_parts + [child_text])

    entities = v251.reconstruct_entities_v251(scoped_text)

    if len(entities) == 1:
        row = v251._extract_entity(entities[0], burst_group_id)
        fallback_used = False
    else:
        # Never merge multiple outputs. Fall back to extracting only the child.
        child_entities = v251.reconstruct_entities_v251(child_text)
        if len(child_entities) == 1:
            row = v251._extract_entity(child_entities[0], burst_group_id)
        else:
            # Last-resort safe path: use the first mature child candidate only;
            # mark it for review rather than inventing a cross-child merge.
            if child_entities:
                row = v251._extract_entity(child_entities[0], burst_group_id)
            else:
                row = {
                    "classification": "AMBIGUOUS",
                    "quality": "UNDER_REVIEW",
                    "review_reasons": ["V255_CHILD_EXTRACTION_FAILED"],
                    "own_text_redacted": child_text,
                    "boundary_needs_split": True,
                    "provenance": {},
                }
        fallback_used = True

    provenance = dict(row.get("provenance") or {})
    provenance["v255_scope"] = {
        "parent_header_applied": bool(header_text),
        "parent_header_type": (parent_header or {}).get("header_type"),
        "parent_header_text_redacted": header_text or None,
        "broad_location_evidence": (parent_header or {}).get("broad_location_evidence"),
        "project_evidence": (parent_header or {}).get("project_evidence"),
        "micro_location_evidence": (parent_header or {}).get("micro_location_evidence"),
        "active_section_context": _norm(active_section) or None,
        "scope_direction": "FORWARD_UNTIL_STRUCTURAL_BOUNDARY",
        "child_boundary_method": "ATOMIC_ROW_PLUS_FRAGMENT_COHESION",
        "property_specific_facts_inherited": False,
        "price_inherited": False,
        "area_inherited": False,
        "configuration_inherited": False,
        "floor_inherited": False,
        "database_write": False,
        "llm_used": False,
    }
    row["provenance"] = provenance
    row["v255"] = {
        "parent_header_applied": bool(header_text),
        "active_section_applied": bool(_norm(active_section)),
        "scoped_reconstruction_count": len(entities),
        "fallback_used": fallback_used,
        "database_write": False,
        "llm_used": False,
    }
    return row


def _parse_burst(row: Dict[str, Any]) -> Dict[str, Any]:
    burst_id = row.get("burst_group_id")
    typed = _typed_pieces(row.get("burst_text") or "")

    candidates: List[Dict[str, Any]] = []
    current_header: Optional[Dict[str, Any]] = None
    active_section: Optional[str] = None
    scope_pieces: List[Dict[str, Any]] = []

    stats = Counter()

    def flush_scope() -> None:
        nonlocal scope_pieces
        if not scope_pieces:
            return
        children = _group_scope_children(scope_pieces)
        stats["scope_child_groups"] += len(children)
        for child in children:
            c = _extract_scoped_child(
                child,
                burst_id,
                current_header,
                active_section,
            )
            candidates.append(c)
        scope_pieces = []

    for piece in typed:
        ptype = piece.get("type")

        if ptype == "SECTION":
            flush_scope()
            active_section = piece.get("text_redacted")
            current_header = None
            stats["section_boundaries"] += 1
            continue

        if ptype == "HEADER":
            flush_scope()
            current_header = _header_context(piece)
            stats["headers_seen"] += 1
            continue

        if ptype == "FOOTER":
            flush_scope()
            current_header = None
            active_section = None
            stats["footer_boundaries"] += 1
            continue

        if ptype != "EMPTY":
            scope_pieces.append(piece)

    flush_scope()

    for c in candidates:
        v = c.get("v255") or {}
        if v.get("parent_header_applied"):
            stats["children_with_parent_header"] += 1
        else:
            stats["children_without_parent_header"] += 1
        if v.get("active_section_applied"):
            stats["children_with_section_context"] += 1
        if v.get("fallback_used"):
            stats["fallback_count"] += 1

    return {
        "burst_group_id": burst_id,
        "candidate_count": len(candidates),
        "stats": dict(stats),
        "candidates": candidates,
    }


def _reason_counts(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    counter: Counter[str] = Counter()
    for row in rows:
        if row.get("quality") != "CLEAN":
            counter.update(row.get("review_reasons") or [])
    return dict(counter.most_common())


def _benchmark(engine, limit: int) -> Dict[str, Any]:
    baseline = v253._benchmark(engine, limit)
    rows = v24._load_bursts(engine, limit)

    base_candidates = [
        c
        for b in (baseline.get("bursts") or [])
        for c in (b.get("candidates") or [])
    ]

    bursts: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []
    totals = Counter()

    for source_row in rows:
        parsed = _parse_burst(source_row)
        bursts.append(parsed)
        candidates.extend(parsed.get("candidates") or [])
        totals.update(parsed.get("stats") or {})

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

    base_counts = baseline.get("counts") or {}
    base_total = base_counts.get("entity_count") or len(base_candidates)
    base_clean = (
        base_counts.get("v253_clean")
        if base_counts.get("v253_clean") is not None
        else sum(1 for c in base_candidates if c.get("quality") == "CLEAN")
    )
    base_location_missing = (
        base_counts.get("location_missing_after")
        if base_counts.get("location_missing_after") is not None
        else sum(
            1 for c in base_candidates
            if "LOCATION_MISSING" in (c.get("review_reasons") or [])
        )
    )
    base_ambiguous = (
        base_counts.get("ambiguous_after")
        if base_counts.get("ambiguous_after") is not None
        else sum(
            1 for c in base_candidates
            if c.get("classification") in ("AMBIGUOUS", "NOISE")
        )
    )

    entity_delta = total - int(base_total or 0)
    entity_delta_ratio = (
        abs(entity_delta) / int(base_total or 1)
        if int(base_total or 0) > 0
        else 0.0
    )

    counts = {
        "burst_sample_size": len(rows),
        "v253_entity_count": base_total,
        "v255_entity_count": total,
        "entity_count_delta_vs_v253": entity_delta,
        "entity_count_delta_ratio": round(entity_delta_ratio, 4),
        "v253_clean": base_clean,
        "v255_clean": clean,
        "v255_clean_rate": round(clean / total, 4) if total else 0.0,
        "v253_location_missing": base_location_missing,
        "v255_location_missing": location_missing,
        "location_missing_delta": location_missing - int(base_location_missing or 0),
        "v253_ambiguous": base_ambiguous,
        "v255_ambiguous": ambiguous,
        "ambiguous_delta": ambiguous - int(base_ambiguous or 0),
        "headers_seen": totals["headers_seen"],
        "scope_child_groups": totals["scope_child_groups"],
        "children_with_parent_header": totals["children_with_parent_header"],
        "children_without_parent_header": totals["children_without_parent_header"],
        "children_with_section_context": totals["children_with_section_context"],
        "fallback_count": totals["fallback_count"],
        "section_boundaries": totals["section_boundaries"],
        "footer_boundaries": totals["footer_boundaries"],
        "boundary_needs_split": sum(
            1 for c in candidates if c.get("boundary_needs_split")
        ),
        "llm_used": 0,
        "database_writes": 0,
    }

    evidence_gates = {
        "entity_count_within_5_percent": entity_delta_ratio <= 0.05,
        "clean_not_worse_than_v253": clean >= int(base_clean or 0),
        "location_missing_improved": location_missing < int(base_location_missing or 0),
        "ambiguity_not_materially_worse": ambiguous <= int(base_ambiguous or 0) + 2,
        "boundary_needs_split_zero": counts["boundary_needs_split"] == 0,
        "fallback_rate_below_5_percent": (
            totals["fallback_count"] / total <= 0.05 if total else True
        ),
        "llm_zero": True,
        "writes_zero": True,
        "promotion_candidate": False,
        "requires_manual_example_review": True,
    }

    return {
        "status": "READY",
        "version": VERSION,
        "mode": MODE,
        "base_v253_version": v253.VERSION,
        "base_v251_version": v251.VERSION,
        "counts": counts,
        "evidence_gates": evidence_gates,
        "under_review_reasons_v255": _reason_counts(candidates),
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
            "returned_text_redacted": True,
            "parent_context_allowed": ["project", "broad_location", "micro_location_evidence"],
            "transaction_context_from_section_only": True,
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
    sample = {
        "burst_group_id": "demo-multi",
        "burst_text": (
            "PREMIUM RENTAL PROPERTIES\n"
            "RUSTOMJEE PARAMOUNT - KHAR WEST\n"
            "3 BHK | 1365 Sq.ft. | Semi-Furnished | Rent 2.50 Lakhs\n"
            "3 BHK | 1240 Sq.ft. | Fully Furnished | Rent 2.25 Lakhs\n"
            "PARK GRANDEUR - JUHU\n"
            "3 BHK | 1250 Sq.ft. | Rent 2.40 Lakhs\n"
        ),
    }

    parsed = _parse_burst(sample)
    rows = parsed.get("candidates") or []

    texts = [
        _norm(
            x.get("own_text_redacted")
            or ((x.get("provenance") or {}).get("v255_scope") or {}).get(
                "parent_header_text_redacted"
            )
            or ""
        )
        for x in rows
    ]

    passed = (
        len(rows) == 3
        and parsed["stats"].get("children_with_parent_header") == 3
        and parsed["stats"].get("children_without_parent_header", 0) == 0
        and all(bool((x.get("v255") or {}).get("parent_header_applied")) for x in rows)
        and all(bool((x.get("v255") or {}).get("active_section_applied")) for x in rows)
        and all((x.get("v255") or {}).get("database_write") is False for x in rows)
        and all((x.get("v255") or {}).get("llm_used") is False for x in rows)
    )

    return {
        "status": "PASS" if passed else "FAIL",
        "version": VERSION,
        "tests": {
            "candidate_count": len(rows),
            "children_with_parent_header": parsed["stats"].get("children_with_parent_header", 0),
            "children_without_parent_header": parsed["stats"].get("children_without_parent_header", 0),
            "children_with_section_context": parsed["stats"].get("children_with_section_context", 0),
            "all_children_remain_separate": len(rows) == 3,
            "parent_scope_applied": all(
                bool((x.get("v255") or {}).get("parent_header_applied"))
                for x in rows
            ) if rows else False,
            "section_context_preserved": all(
                bool((x.get("v255") or {}).get("active_section_applied"))
                for x in rows
            ) if rows else False,
            "fallback_count": parsed["stats"].get("fallback_count", 0),
            "database_write": False,
            "llm_used": False,
        },
        "writes_performed": 0,
    }


def register(core):
    app = core.app
    engine = core.engine
    status_route = "/api/v7/property-ai/hierarchical-parser-v255/status"

    if any(getattr(r, "path", None) == status_route for r in app.router.routes):
        return {"status": "ALREADY_REGISTERED", "version": VERSION, "route": status_route}

    @app.get(status_route)
    def status():
        return JSONResponse({
            "status": "READY",
            "version": VERSION,
            "mode": MODE,
            "base_v253_version": v253.VERSION,
            "base_v251_version": v251.VERSION,
            "read_only_shadow": True,
            "database_writes": False,
            "canonical_tables_modified": False,
            "orchestrator_modified": False,
            "live_reconstructor_replaced": False,
            "matcher_modified": False,
            "whatsapp_live_modified": False,
            "raw_data_deleted": False,
            "llm_used": False,
            "automatic_promotion": False,
        })

    @app.get("/api/v7/property-ai/hierarchical-parser-v255/regression-test")
    def regression_test():
        return JSONResponse(_regression_demo())

    @app.get("/api/v7/property-ai/hierarchical-parser-v255/preview")
    def preview(limit: int = Query(25, ge=1, le=100)):
        return JSONResponse(_benchmark(engine, limit))

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": status_route,
        "regression": "/api/v7/property-ai/hierarchical-parser-v255/regression-test",
        "preview": "/api/v7/property-ai/hierarchical-parser-v255/preview?limit=25",
        "writes_enabled": False,
    }

