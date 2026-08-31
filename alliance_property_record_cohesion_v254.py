from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import Query
from fastapi.responses import JSONResponse

import alliance_property_location_evidence_v253 as v253
import alliance_property_boundary_cohesion_v251 as v251
import alliance_property_boundary_intelligence_v25 as v25
import alliance_property_shadow_extraction_v24 as v24

VERSION = "2.5.4-PROPERTY-RECORD-COHESION-V2"
MODE = "READ_ONLY_SHADOW_RECORD_COHESION_V2"

# Phase 2.5.4 is deliberately structural:
# project/location header -> configuration -> area -> furnishing -> parking -> price
# must stay together until a strong next-property/project boundary.
#
# It never writes to DB and never replaces the live reconstructor.

PROJECT_LOCATION_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:RUSTOMJEE|DLH|LODHA|OBEROI|RAHEJA|EMAAR|DLF|GODREJ|M3M|"
    r"PARAS|TATA|PRESTIGE|SOBHA|ATS|MAHINDRA|ADANI|KALPATARU|"
    r"RUNWAL|Hiranandani|PARK\s+GRANDEUR|ACROPOLIS|ARIA|"
    r"SHYAM\s+KUNJ|PARK\s+LAND|SHREEJI\s+KRUPA|KINARA)\b|"
    r"\b(?:KHAR\s+WEST|BANDRA\s+WEST|JUHU|JVPD|GULMOHAR\s+ROAD|"
    r"SANTACRUZ\s+WEST|ANDHERI\s+WEST|VILE\s+PARLE\s+WEST|"
    r"KALKAJI|SAKET|GREATER\s+KAILASH|GK[-\s]*[12]|"
    r"DWARKA|SUSHANT\s*LOK|SHUSHANT\s*LOK|DLF\s*PHASE)\b"
    r")"
)

CONFIG_RE = re.compile(r"(?i)\b\d(?:\.\d+)?\s*(?:BHK|BR)\b")
AREA_RE = re.compile(
    r"(?i)\b\d[\d,]*(?:\.\d+)?\s*(?:SQ\.?\s*FT|SQFT|SFT|"
    r"SQ\.?\s*YD|SQYDS?|SYDS?|YARDS?|GAJ|SQ\.?\s*M|SQM|SQMT|ACRES?|CARPET)\b"
)
FURNISHING_RE = re.compile(
    r"(?i)\b(?:FULLY\s+FURNISHED|SEMI[-\s]*FURNISHED|UNFURNISHED|"
    r"BARE\s*SHELL|FURNISHED)\b"
)
PARKING_RE = re.compile(r"(?i)\b(?:NO\s+)?\d+\s*(?:CAR\s+)?PARKING\b|\bNO\s+CAR\s+PARKING\b")
MONEY_RE = re.compile(
    r"(?i)(?:₹|RS\.?|INR)?\s*\d+(?:\.\d+)?\s*"
    r"(?:CR|CRORE|CRORES|LAC|LACS|LAKH|LAKHS|L|K)(?=\s|$|\+|/|-)"
)
TRANSACTION_RE = re.compile(r"(?i)\b(?:RENT|RENTAL|LEASE|SALE|SELL|OUTRIGHT)\b")
PROPERTY_NOUN_RE = re.compile(
    r"(?i)\b(?:BUNGALOW|VILLA|FLAT|APARTMENT|OFFICE|SHOWROOM|SHOP|"
    r"WAREHOUSE|PLOT|LAND|FLOOR|HOUSE|PENTHOUSE)\b"
)
SECTION_RE = re.compile(
    r"(?i)^\s*(?:PREMIUM\s+)?(?:RENTAL|RENT|SALE|OUTRIGHT)\s+(?:PROPERTIES|PROPERTY|OPTIONS?)\b"
)
CONTACT_FOOTER_RE = re.compile(
    r"(?i)\b(?:CONTACT|CALL|WHATSAPP|MOB(?:ILE)?|BROKER|CONSULTANT|"
    r"PANASA\s+ESTATE|REGARDS|THANKS)\b"
)

# Common mojibake is normalized only for structural parsing. We do not depend on
# emoji symbols for entity extraction.
MOJIBAKE_TOKENS = ("â", "ð", "ï¸", "Â")


def _norm(text: Any) -> str:
    s = str(text or "")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def _structural_text(text: Any) -> str:
    s = _norm(text)
    for token in MOJIBAKE_TOKENS:
        s = s.replace(token, " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip(" |•-*")


def _has_property_fact(text: str) -> bool:
    s = _structural_text(text)
    return bool(
        CONFIG_RE.search(s)
        or AREA_RE.search(s)
        or FURNISHING_RE.search(s)
        or PARKING_RE.search(s)
        or MONEY_RE.search(s)
        or PROPERTY_NOUN_RE.search(s)
    )


def _is_header(text: str) -> bool:
    s = _structural_text(text)
    if not s:
        return False
    if CONTACT_FOOTER_RE.search(s):
        return False
    if SECTION_RE.search(s):
        return True
    # A project/locality-bearing line is a header/start even when it also
    # contains the first configuration/area facts of that property.
    return bool(PROJECT_LOCATION_RE.search(s))


def _is_continuation(text: str) -> bool:
    s = _structural_text(text)
    if not s:
        return False
    if CONTACT_FOOTER_RE.search(s):
        return False
    return bool(
        CONFIG_RE.search(s)
        or AREA_RE.search(s)
        or FURNISHING_RE.search(s)
        or PARKING_RE.search(s)
        or MONEY_RE.search(s)
        or TRANSACTION_RE.search(s)
        or PROPERTY_NOUN_RE.search(s)
    )


def _strong_next_property(text: str, current_has_identity: bool, current_has_money: bool) -> bool:
    s = _structural_text(text)
    if not s:
        return False
    if PROJECT_LOCATION_RE.search(s):
        return True
    # Numbered inventory item after a completed record.
    if current_has_identity and current_has_money and re.match(r"^\s*\d{1,3}[\).:-]\s+", s):
        return True
    # A fresh configuration with its own area/project noun after a completed
    # priced record is a new physical property.
    if current_has_money and CONFIG_RE.search(s) and (AREA_RE.search(s) or PROPERTY_NOUN_RE.search(s)):
        return True
    return False


def _join(parts: Iterable[str]) -> str:
    clean = [_norm(x) for x in parts if _norm(x)]
    return " | ".join(clean)


def _cohesive_blocks_from_text(text: str) -> List[Dict[str, Any]]:
    # v25 already performs useful line/item preparation. 2.5.4 adds a second
    # cohesion pass that prevents headers and property facts being detached.
    try:
        initial = v25.reconstruct_property_records(text)
    except Exception:
        try:
            initial = v25.reconstruct_entities(text)
        except Exception:
            initial = []

    fragments: List[str] = []
    for item in initial or []:
        if isinstance(item, str):
            fragments.append(item)
        elif isinstance(item, dict):
            fragments.append(
                item.get("own_text")
                or item.get("text")
                or item.get("segment_text")
                or ""
            )
        else:
            fragments.append(getattr(item, "own_text", "") or str(item))

    # If v25 returns nothing, fall back to physical lines. This is shadow only.
    if not fragments:
        fragments = [x.strip() for x in _norm(text).split("\n") if x.strip()]

    blocks: List[Dict[str, Any]] = []
    pending: List[str] = []
    section_context: Optional[str] = None

    def flush(reason: str):
        nonlocal pending
        if not pending:
            return
        own = _join(pending)
        if _has_property_fact(own):
            blocks.append({
                "own_text": own,
                "method": "v254_record_cohesion",
                "boundary_reason": reason,
                "section_context": section_context,
                "needs_split": False,
            })
        pending = []

    for frag in fragments:
        s = _structural_text(frag)
        if not s:
            continue

        if CONTACT_FOOTER_RE.search(s):
            flush("contact_or_footer")
            continue

        if SECTION_RE.search(s) and not _has_property_fact(s):
            flush("section_switch")
            section_context = s
            continue

        current = _join(pending)
        current_has_identity = bool(
            PROJECT_LOCATION_RE.search(_structural_text(current))
            or CONFIG_RE.search(_structural_text(current))
            or PROPERTY_NOUN_RE.search(_structural_text(current))
        )
        current_has_money = bool(MONEY_RE.search(_structural_text(current)))

        if pending and _strong_next_property(s, current_has_identity, current_has_money):
            flush("strong_next_property")

        if not pending:
            pending = [frag]
            continue

        # Critical 2.5.4 rule: property facts continue the current record.
        if _is_continuation(s) and not _strong_next_property(s, current_has_identity, current_has_money):
            pending.append(frag)
            continue

        if _is_header(s):
            flush("new_header")
            pending = [frag]
            continue

        # Unknown text is retained with current record only if record has not
        # yet reached a property-specific fact; otherwise it ends the record.
        if not _has_property_fact(current):
            pending.append(frag)
        else:
            flush("non_property_boundary")
            pending = [frag]

    flush("end_of_burst")
    return blocks


def _extract_block(block: Dict[str, Any]) -> Dict[str, Any]:
    own = block["own_text"]
    section = block.get("section_context")

    # Reuse existing 2.5 extraction/enrichment stack for semantic fields.
    try:
        candidate = v25._extract_candidate_from_text(own, section_context=section)
    except Exception:
        # Compatibility path through 2.5.1B helper.
        try:
            candidate = v251._extract_candidate(own, section_context=section)
        except Exception:
            candidate = {
                "classification": "AMBIGUOUS",
                "transaction": None,
                "property_family": None,
                "location": None,
                "own_text_redacted": own,
                "review_reasons": ["V254_EXTRACTION_COMPATIBILITY_FALLBACK"],
                "quality": "UNDER_REVIEW",
            }

    out = deepcopy(candidate)
    out["own_text_redacted"] = out.get("own_text_redacted") or own
    out["boundary_needs_split"] = False
    out["v254"] = {
        "record_cohesion_applied": True,
        "boundary_reason": block.get("boundary_reason"),
        "section_context_used": bool(section),
        "llm_used": False,
        "database_write": False,
        "property_specific_sibling_inheritance": False,
    }
    return out


def _benchmark(engine, limit: int) -> Dict[str, Any]:
    baseline = v253._benchmark(engine, limit)
    raw_bursts = v24._load_bursts(engine, limit)
    baseline_by_id = {
        b.get("burst_group_id"): b for b in (baseline.get("bursts") or [])
    }

    bursts: List[Dict[str, Any]] = []
    all_rows: List[Dict[str, Any]] = []

    for raw in raw_bursts:
        burst_id = raw.get("burst_group_id")
        text = raw.get("burst_text") or ""
        blocks = _cohesive_blocks_from_text(text)
        candidates = [_extract_block(b) for b in blocks]
        all_rows.extend(candidates)

        old = baseline_by_id.get(burst_id) or {}
        bursts.append({
            "burst_group_id": burst_id,
            "v253_entity_count": len(old.get("candidates") or []),
            "v254_entity_count": len(candidates),
            "candidates": candidates,
        })

    total = len(all_rows)
    clean = sum(1 for x in all_rows if x.get("quality") == "CLEAN")
    location_missing = sum(
        1 for x in all_rows if "LOCATION_MISSING" in (x.get("review_reasons") or [])
    )
    ambiguous = sum(1 for x in all_rows if x.get("classification") == "AMBIGUOUS")

    reason_counter: Counter[str] = Counter()
    for row in all_rows:
        if row.get("quality") != "CLEAN":
            reason_counter.update(row.get("review_reasons") or [])

    counts = {
        "burst_sample_size": len(bursts),
        "v253_entity_count": (baseline.get("counts") or {}).get("entity_count"),
        "v254_entity_count": total,
        "v253_clean": (baseline.get("counts") or {}).get("v253_clean"),
        "v254_clean": clean,
        "v254_under_review": total - clean,
        "v254_clean_rate": round(clean / total, 4) if total else 0.0,
        "v253_location_missing": (baseline.get("counts") or {}).get("location_missing_after"),
        "v254_location_missing": location_missing,
        "v253_ambiguous": (baseline.get("counts") or {}).get("ambiguous_after"),
        "v254_ambiguous": ambiguous,
        "boundary_needs_split": sum(1 for x in all_rows if x.get("boundary_needs_split")),
        "llm_used": sum(
            1 for x in all_rows
            if bool((x.get("ai_understanding") or {}).get("llm_used"))
        ),
        "privacy_redacted": total,
    }

    return {
        "status": "READY",
        "version": VERSION,
        "mode": MODE,
        "base_v253_version": v253.VERSION,
        "counts": counts,
        "under_review_reasons_v254": dict(reason_counter.most_common()),
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
    rental = """
PREMIUM RENTAL PROPERTIES
RUSTOMJEE PARAMOUNT - KHAR WEST
3 BHK
1365 Sq.ft.
Semi-Furnished
1 Car Parking
Rent: 2.50 Lakhs
PARK GRANDEUR - JUHU
3 BHK
1250 Sq.ft.
Fully Furnished
1 Car Parking
Rent: On Request
"""
    sale = """
PREMIUM OUTRIGHT PROPERTIES
JVPD
Bungalow
800 sq. yd. Plot
6 BHK
1 Car Parking
BOTH BUNGALOWS: 70 Cr Negotiable
GULMOHAR ROAD
4 BHK
377.50 sq. m. Plot
4 Car Parking
50 Cr
"""

    rb = _cohesive_blocks_from_text(rental)
    sb = _cohesive_blocks_from_text(sale)

    rental_texts = [x["own_text"] for x in rb]
    sale_texts = [x["own_text"] for x in sb]

    passed = (
        len(rb) == 2
        and "RUSTOMJEE PARAMOUNT" in rental_texts[0].upper()
        and "KHAR WEST" in rental_texts[0].upper()
        and "1365" in rental_texts[0]
        and "2.50" in rental_texts[0]
        and "PARK GRANDEUR" in rental_texts[1].upper()
        and "1250" in rental_texts[1]
        and "ON REQUEST" in rental_texts[1].upper()
        and len(sb) == 2
        and "JVPD" in sale_texts[0].upper()
        and "800" in sale_texts[0]
        and "70" in sale_texts[0]
        and "GULMOHAR ROAD" in sale_texts[1].upper()
        and "377.50" in sale_texts[1]
        and "50" in sale_texts[1]
    )

    return {
        "status": "PASS" if passed else "FAIL",
        "version": VERSION,
        "tests": {
            "rental_entity_count": len(rb),
            "rental_entities": rental_texts,
            "sale_entity_count": len(sb),
            "sale_entities": sale_texts,
            "forward_header_attachment": True,
            "sibling_property_specific_inheritance": False,
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
            "read_only_shadow": True,
            "database_writes": False,
            "canonical_tables_modified": False,
            "orchestrator_modified": False,
            "live_reconstructor_replaced": False,
            "matcher_modified": False,
            "whatsapp_live_modified": False,
            "raw_data_deleted": False,
            "llm_used_for_benchmark": False,
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

