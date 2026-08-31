from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Query
from fastapi.responses import JSONResponse

import alliance_property_location_evidence_v253 as v253
import alliance_property_boundary_cohesion_v251 as v251
import alliance_property_boundary_intelligence_v25 as v25
import alliance_property_shadow_extraction_v24 as v24

VERSION = "2.5.4B-PROPERTY-RECORD-COHESION-COMPATIBILITY-FIX"
MODE = "READ_ONLY_SHADOW_HEADER_BRIDGE_V254B"

# 2.5.4A corrects two implementation defects in 2.5.4:
# 1) it now calls the real v251 reconstruction/extraction APIs;
# 2) it adds only a conservative HEADER -> IMMEDIATE NEXT PROPERTY bridge
#    before v251 reconstruction, so location/project headers are not discarded.
#
# No sibling fact inheritance. No writes. No live replacement.

KNOWN_LOCATION_RE = re.compile(
    r"(?i)\b(?:"
    r"KHAR\s+WEST|BANDRA\s+WEST|JUHU|JVPD|GULMOHAR\s+ROAD|"
    r"SANTACRUZ\s+WEST|ANDHERI\s+WEST|VILE\s+PARLE\s+WEST|"
    r"KALKAJI|SAKET|GREATER\s+KAILASH|GK[-\s]*[12]|"
    r"DWARKA(?:\s+SECTOR\s*\d+)?|"
    r"SUSHANT\s*LOK(?:\s*1)?|SHUSHANT\s*LOK(?:\s*1)?|"
    r"DLF\s*PHASE\s*[1-5]"
    r")\b"
)

KNOWN_PROJECT_RE = re.compile(
    r"(?i)\b(?:"
    r"RUSTOMJEE|LODHA|OBEROI|RAHEJA|EMAAR|DLF|GODREJ|M3M|"
    r"PARAS|TATA|PRESTIGE|SOBHA|ATS|MAHINDRA|ADANI|KALPATARU|"
    r"RUNWAL|HIRANANDANI|PARK\s+GRANDEUR|ACROPOLIS|ARIA|"
    r"SHYAM\s+KUNJ|PARK\s+LAND|SHREEJI\s+KRUPA|KINARA|DLH"
    r")\b"
)

CONFIG_RE = getattr(v25, "CONFIG_RE")
AREA_RE = getattr(v25, "AREA_RE")
MONEY_RE = getattr(v25, "MONEY_RE")
PROPERTY_RE = getattr(v25, "PROPERTY_RE")

CONTACT_OR_FOOTER_RE = getattr(
    v251,
    "CONTACT_OR_FOOTER_RE",
    re.compile(r"(?i)^\s*(?:CONTACT|CALL|WHATSAPP|BROKER|CONSULTANT)\b"),
)
HARD_SECTION_RE = getattr(
    v251,
    "HARD_SECTION_RE",
    re.compile(r"(?i)^\s*(?:PREMIUM\s+)?(?:RENTAL|RENT|SALE|OUTRIGHT|COMMERCIAL)\b"),
)


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()


def _has_property_specific_fact(text_value: str) -> bool:
    s = _norm(text_value)
    return bool(
        CONFIG_RE.search(s)
        or AREA_RE.search(s)
        or MONEY_RE.search(s)
        or PROPERTY_RE.search(s)
    )


def _safe_header(text_value: str) -> bool:
    """
    Header bridge eligibility:
    - short
    - not a section/footer
    - contains known locality and/or known project token
    - contains NO area/config/price/property-specific fact
    """
    s = _norm(text_value).strip(" -*|:•▪🔹🔸✨")
    if not s or len(s) > 100:
        return False
    if CONTACT_OR_FOOTER_RE.search(s):
        return False
    if v25._looks_like_section(s) or HARD_SECTION_RE.search(s):
        return False
    if _has_property_specific_fact(s):
        return False
    return bool(KNOWN_LOCATION_RE.search(s) or KNOWN_PROJECT_RE.search(s))


def _prepare_text_with_forward_header_bridge(text_value: str) -> Tuple[str, Dict[str, int]]:
    """
    Attach a safe header only to the immediate following property-bearing piece.

    Never carries a header across:
    - another header,
    - a transaction/family section,
    - a contact/footer,
    - more than one following piece.

    This is association, not sibling inheritance.
    """
    pieces = v25._presegment(text_value)
    out: List[str] = []
    pending_header: Optional[str] = None

    stats = {
        "headers_seen": 0,
        "headers_attached": 0,
        "headers_dropped_without_immediate_property": 0,
    }

    for raw_piece in pieces:
        piece = _norm(raw_piece)
        if not piece:
            continue

        is_section = bool(v25._looks_like_section(piece) or HARD_SECTION_RE.search(piece))
        is_footer = bool(CONTACT_OR_FOOTER_RE.search(piece))

        if is_section or is_footer:
            if pending_header:
                stats["headers_dropped_without_immediate_property"] += 1
                pending_header = None
            out.append(piece)
            continue

        if _safe_header(piece):
            stats["headers_seen"] += 1
            if pending_header:
                # Never concatenate two uncertain headers.
                stats["headers_dropped_without_immediate_property"] += 1
            pending_header = piece
            continue

        if pending_header:
            if _has_property_specific_fact(piece):
                out.append(f"{pending_header} | {piece}")
                stats["headers_attached"] += 1
                pending_header = None
                continue
            # Header may attach only to the immediate next useful piece.
            stats["headers_dropped_without_immediate_property"] += 1
            pending_header = None

        out.append(piece)

    if pending_header:
        stats["headers_dropped_without_immediate_property"] += 1

    return "\n".join(out), stats


def reconstruct_entities_v254(text_value: str):
    prepared, stats = _prepare_text_with_forward_header_bridge(text_value)
    entities = v251.reconstruct_entities_v251(prepared)
    return entities, stats


def _extract_entity(entity, burst_group_id: str) -> Dict[str, Any]:
    # IMPORTANT: use the actual established v251 extraction API.
    row = v251._extract_entity(entity, burst_group_id)

    provenance = dict(row.get("provenance") or {})
    provenance["v254a"] = {
        "boundary": "V251_PLUS_IMMEDIATE_FORWARD_HEADER_BRIDGE",
        "header_attaches_forward_only": True,
        "immediate_next_piece_only": True,
        "price_inherited_from_sibling": False,
        "area_inherited_from_sibling": False,
        "configuration_inherited_from_sibling": False,
        "floor_inherited_from_sibling": False,
        "location_inherited_from_sibling": False,
        "database_write": False,
        "llm_used": False,
    }
    row["provenance"] = provenance
    row["v254"] = {
        "compatibility_fix": True,
        "real_v251_extractor_used": True,
        "fallback_used": False,
        "llm_used": False,
        "database_write": False,
    }
    return row


def _reason_counts(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    counter: Counter[str] = Counter()
    for row in rows:
        if row.get("quality") != "CLEAN":
            counter.update(row.get("review_reasons") or [])
    return dict(counter.most_common())


def _benchmark(engine, limit: int) -> Dict[str, Any]:
    baseline = v253._benchmark(engine, limit)
    db_rows = v24._load_bursts(engine, limit)

    baseline_candidates = [
        c
        for b in (baseline.get("bursts") or [])
        for c in (b.get("candidates") or [])
    ]

    bursts: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []

    header_seen = 0
    header_attached = 0
    header_dropped = 0

    for row in db_rows:
        burst_id = row["burst_group_id"]
        entities, bridge_stats = reconstruct_entities_v254(row.get("burst_text") or "")

        header_seen += bridge_stats["headers_seen"]
        header_attached += bridge_stats["headers_attached"]
        header_dropped += bridge_stats["headers_dropped_without_immediate_property"]

        out: List[Dict[str, Any]] = []
        for entity in entities:
            c = _extract_entity(entity, burst_id)
            out.append(c)
            candidates.append(c)

        bursts.append({
            "burst_group_id": burst_id,
            "v254_entity_count": len(entities),
            "header_bridge": bridge_stats,
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
    fallback_count = sum(
        1 for c in candidates
        if bool((c.get("v254") or {}).get("fallback_used"))
    )

    base_counts = baseline.get("counts") or {}
    base_total = (
        base_counts.get("entity_count")
        or base_counts.get("v253_entity_count")
        or len(baseline_candidates)
    )
    base_clean = (
        base_counts.get("v253_clean")
        if base_counts.get("v253_clean") is not None
        else sum(1 for c in baseline_candidates if c.get("quality") == "CLEAN")
    )
    base_location_missing = (
        base_counts.get("location_missing_after")
        if base_counts.get("location_missing_after") is not None
        else sum(
            1 for c in baseline_candidates
            if "LOCATION_MISSING" in (c.get("review_reasons") or [])
        )
    )
    base_ambiguous = (
        base_counts.get("ambiguous_after")
        if base_counts.get("ambiguous_after") is not None
        else sum(
            1 for c in baseline_candidates
            if c.get("classification") in ("AMBIGUOUS", "NOISE")
        )
    )

    counts = {
        "burst_sample_size": len(db_rows),
        "v253_entity_count": base_total,
        "v254_entity_count": total,
        "entity_count_delta_vs_v253": total - int(base_total or 0),
        "v253_clean": base_clean,
        "v254_clean": clean,
        "v254_under_review": total - clean,
        "v254_clean_rate": round(clean / total, 4) if total else 0.0,
        "v253_location_missing": base_location_missing,
        "v254_location_missing": location_missing,
        "v253_ambiguous": base_ambiguous,
        "v254_ambiguous": ambiguous,
        "safe_headers_seen": header_seen,
        "safe_headers_attached": header_attached,
        "safe_headers_dropped": header_dropped,
        "compatibility_fallback_count": fallback_count,
        "boundary_needs_split": sum(
            1 for c in candidates if c.get("boundary_needs_split")
        ),
        "llm_used": sum(
            1 for c in candidates
            if bool((c.get("ai_understanding") or {}).get("llm_used"))
        ),
        "privacy_redacted": total,
    }

    return {
        "status": "READY",
        "version": VERSION,
        "mode": MODE,
        "base_v253_version": v253.VERSION,
        "base_v251_version": v251.VERSION,
        "counts": counts,
        "under_review_reasons_v254": _reason_counts(candidates),
        "safety_contract": {
            "read_only_shadow": True,
            "database_writes": False,
            "canonical_tables_modified": False,
            "orchestrator_modified": False,
            "live_reconstructor_replaced": False,
            "matcher_modified": False,
            "whatsapp_live_modified": False,
            "raw_data_deleted": False,
            "llm_used_for_benchmark": False,
            "header_attaches_forward_only": True,
            "immediate_next_piece_only": True,
            "price_inherited_from_sibling": False,
            "area_inherited_from_sibling": False,
            "configuration_inherited_from_sibling": False,
            "floor_inherited_from_sibling": False,
            "location_inherited_from_sibling": False,
        },
        "writes_performed": 0,
        "bursts": bursts,
    }


def _regression_demo() -> Dict[str, Any]:
    sample = (
        "PREMIUM RENTAL PROPERTIES\n"
        "RUSTOMJEE PARAMOUNT - KHAR WEST\n"
        "3 BHK | 1365 Sq.ft. | Semi-Furnished | 1 Car Parking | Rent: 2.50 Lakhs\n"
        "PARK GRANDEUR - JUHU\n"
        "3 BHK | 1250 Sq.ft. | Fully Furnished | 1 Car Parking | Rent: On Request\n"
    )
    prepared, bridge = _prepare_text_with_forward_header_bridge(sample)
    entities = v251.reconstruct_entities_v251(prepared)

    leak_test = (
        "PREMIUM RENTAL PROPERTIES\n"
        "RUSTOMJEE PARAMOUNT - KHAR WEST\n"
        "CONTACT BROKER\n"
        "3 BHK | 1200 Sq.ft. | Rent: 2.00 Lakhs\n"
    )
    leak_prepared, leak_bridge = _prepare_text_with_forward_header_bridge(leak_test)

    texts = [e.own_text for e in entities]

    passed = (
        bridge["headers_attached"] == 2
        and len(entities) == 2
        and "RUSTOMJEE PARAMOUNT" in texts[0].upper()
        and "KHAR WEST" in texts[0].upper()
        and "1365" in texts[0]
        and "PARK GRANDEUR" in texts[1].upper()
        and "JUHU" in texts[1].upper()
        and "1250" in texts[1]
        and leak_bridge["headers_attached"] == 0
        and "RUSTOMJEE PARAMOUNT" not in leak_prepared.upper()
    )

    return {
        "status": "PASS" if passed else "FAIL",
        "version": VERSION,
        "tests": {
            "prepared_text": prepared,
            "entity_count": len(entities),
            "entities": texts,
            "headers_seen": bridge["headers_seen"],
            "headers_attached": bridge["headers_attached"],
            "cross_footer_leak_blocked": leak_bridge["headers_attached"] == 0,
            "real_v251_reconstructor_used": True,
            "real_v251_extractor_available": callable(getattr(v251, "_extract_entity", None)),
        },
        "writes_performed": 0,
    }


def register(core):
    app = core.app
    engine = core.engine
    status_route = "/api/v7/property-ai/record-cohesion-v254/status"

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
            "llm_used_for_benchmark": False,
            "compatibility_fallback_removed": True,
        })

    @app.get("/api/v7/property-ai/record-cohesion-v254/regression-test")
    def regression_test():
        return JSONResponse(_regression_demo())

    @app.get("/api/v7/property-ai/record-cohesion-v254/preview")
    def preview(limit: int = Query(25, ge=1, le=100)):
        return JSONResponse(_benchmark(engine, limit))

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": status_route,
        "regression": "/api/v7/property-ai/record-cohesion-v254/regression-test",
        "preview": "/api/v7/property-ai/record-cohesion-v254/preview?limit=25",
        "writes_enabled": False,
    }

