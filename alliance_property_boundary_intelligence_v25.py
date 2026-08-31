from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Query
from fastapi.responses import JSONResponse

import alliance_property_shadow_extraction_v24 as v24
import alliance_property_benchmark_stabilizer_v245a as stabilizer
from property_brain.stages.s3_entity_segmentation_v23 import EntityBlock

VERSION = "2.5.0-PROPERTY-BOUNDARY-OWN-TEXT-INTELLIGENCE"
MODE = "READ_ONLY_SHADOW_BOUNDARY_AND_OWN_TEXT"

# Property-fact signals
AREA_RE = re.compile(
    r"(?i)\b\d[\d,]*(?:\.\d+)?\s*(?:SQ\.?\s*FT|SQFT|SFT|SQ\.?\s*YD|SQYD|SYDS?|YARDS?|GAJ|"
    r"ACRES?|SQ\.?\s*M|SQMT|SQM|CARPET)\b"
)
MONEY_RE = re.compile(
    r"(?i)(?:₹|RS\.?|INR)?\s*\d[\d,]*(?:\.\d+)?\s*(?:CR|CRORE|CRORES|L|LAC|LACS|LAKH|LAKHS|K|THOUSAND)\b"
)
CONFIG_RE = re.compile(
    r"(?i)\b(?:\d(?:\.\d+)?\s*BHK|\d\s*\.?\s*BR\.?|[2-9](?:/[2-9])+\s*BHK)\b"
)
PROPERTY_RE = re.compile(
    r"(?i)\b(?:APARTMENT|FLAT|BUILDER\s+FLOOR|INDEPENDENT\s+FLOOR|VILLA|KOTHI|BUNGALOW|"
    r"PLOT|LAND|OFFICE|SHOWROOM|SHOP|WAREHOUSE|GODOWN|FARMHOUSE|BANQUET|HOTEL|"
    r"GUEST\s*HOUSE|RESTAURANT|CAFE|CLUB|LOUNGE|PENTHOUSE|COMMERCIAL\s+SPACE)\b"
)

EXPLICIT_SALE_RE = re.compile(
    r"(?i)\b(?:OUTRIGHT|FOR\s+SALE|SALE|RESALE|ASKING|DEMAND)\b"
)
EXPLICIT_RENT_RE = re.compile(
    r"(?i)\b(?:FOR\s+RENT|RENTAL|RENT|LEASE|TO\s+LET)\b"
)
MAINT_RE = re.compile(r"(?i)\b(?:MAINT|MAINTENANCE)\b")
FURNISH_RE = re.compile(r"(?i)\b(?:FURNISHED|SEMI[-\s]*FURNISHED|UNFURNISHED)\b")
RATE_RE = re.compile(
    r"(?i)\b(?:PSF|P\.?S\.?F\.?|PER\s+SQ\.?\s*FT|PER\s+SQFT|/\s*SQFT|/\s*FT|PER\s+ACRE)\b"
)

# Section headings that may carry global transaction/family context.
SALE_SECTION_RE = re.compile(
    r"(?i)^\s*(?:PREMIUM\s+)?(?:\d(?:/\d)?\s*BHK\s+)?(?:OUTRIGHT|SALE|FOR\s+SALE)\b"
)
RENT_SECTION_RE = re.compile(
    r"(?i)^\s*(?:PREMIUM\s+)?(?:RENTAL\s+PROPERTIES|RENTALS|FOR\s+RENT|RENT)\b"
)
BUNGALOW_SECTION_RE = re.compile(r"(?i)^\s*(?:PREMIUM\s+)?BUNGALOWS?\b")
COMMERCIAL_SECTION_RE = re.compile(r"(?i)^\s*COMMERCIAL\b")

# Common listing delimiters observed in WhatsApp broker inventory.
ITEM_MARKER_RE = re.compile(
    r"(?:\n\s*(?:✨|🔹|🔸|▪|•|➤|➡|🏠|🏢)\s*|\s+â¨\s+|\s+âªï¸\s+(?=[A-Z][A-Z0-9 &.'/-]{2,40}\s+âªï¸)|"
    r"\n\s*(?=\d{1,2}[.)]\s*))"
)

# Project/property-name anchor followed quickly by a concrete fact.
PROJECT_ANCHOR_RE = re.compile(
    r"(?i)(?:(?<=^)|(?<=\n)|(?<=â¨)\s*)"
    r"([A-Z][A-Z0-9 &'./-]{2,45})"
    r"(?=\s+(?:âªï¸\s*)?(?:\d(?:\.\d+)?\s*BHK|\d[\d,]*\s*(?:SQFT|SQ\.?\s*FT|CARPET)|"
    r"GROUND|LOWER|MIDDLE|HIGHER|FULLY|SEMI|BARE|BUNGALOW))"
)

# Location normalization from OWN text only.
SUSHANT_RE = re.compile(r"(?i)\bS?H?USHANT\s*LOK\s*[-:]?\s*1\b|\bSHUSHANTLOK1\b|\bSUSHANTLOK1\b")
DLF_RE = re.compile(r"(?i)\bDLF\s*PHASE\s*[-:]?\s*([1-5])\b|\bDLFPHASE([1-5])\b")
JUHU_RE = re.compile(r"(?i)\bJUHU\b")
JVPD_RE = re.compile(r"(?i)\bJVPD\b")
GULMOHAR_RE = re.compile(r"(?i)\bGULMOHAR\s+ROAD\b")
BANDRA_WEST_RE = re.compile(r"(?i)\bBANDRA\s+WEST\b")
KHAR_WEST_RE = re.compile(r"(?i)\bKHAR\s+WEST\b")

PHONE_RE = getattr(v24, "PHONE_RE", re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)"))
EMAIL_RE = getattr(v24, "EMAIL_RE", re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"))


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()


def _redact(value: str) -> str:
    out = PHONE_RE.sub("[PHONE_REDACTED]", str(value or ""))
    return EMAIL_RE.sub("[EMAIL_REDACTED]", out)


def _fact_count(text_value: str) -> int:
    s = str(text_value or "")
    return (
        int(bool(AREA_RE.search(s)))
        + int(bool(MONEY_RE.search(s)))
        + int(bool(CONFIG_RE.search(s)))
        + int(bool(PROPERTY_RE.search(s)))
    )


def _is_entity_candidate(text_value: str) -> bool:
    s = _norm(text_value)
    if len(s) < 8:
        return False
    has_identity = bool(CONFIG_RE.search(s) or PROPERTY_RE.search(s) or PROJECT_ANCHOR_RE.search(s))
    has_fact = bool(AREA_RE.search(s) or MONEY_RE.search(s) or CONFIG_RE.search(s))
    return has_identity and has_fact


def _section_context(text_value: str, current: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
    s = _norm(text_value)
    ctx = dict(current)

    if SALE_SECTION_RE.search(s):
        ctx["transaction"] = "SALE"
    elif RENT_SECTION_RE.search(s):
        ctx["transaction"] = "RENT"

    if BUNGALOW_SECTION_RE.search(s):
        ctx["property_family"] = "RESIDENTIAL"
    elif COMMERCIAL_SECTION_RE.search(s):
        ctx["property_family"] = "COMMERCIAL"

    return ctx


def _looks_like_section(text_value: str) -> bool:
    s = _norm(text_value).strip(" -*|:")
    if not s or len(s) > 100:
        return False
    return bool(
        SALE_SECTION_RE.search(s)
        or RENT_SECTION_RE.search(s)
        or BUNGALOW_SECTION_RE.search(s)
        or COMMERCIAL_SECTION_RE.search(s)
    )


def _presegment(text_value: str) -> List[str]:
    raw = str(text_value or "").replace("\r\n", "\n").replace("\r", "\n")

    # Turn obvious visual item separators into newlines without changing content.
    raw = re.sub(r"\s+â¨\s+", "\nâ¨ ", raw)
    raw = re.sub(r"\s+✨\s+", "\n✨ ", raw)

    # Preserve explicit section transitions.
    raw = re.sub(
        r"(?i)\s+(?=(?:PREMIUM\s+)?(?:RENTAL\s+PROPERTIES|PREMIUM\s+BUNGALOWS|"
        r"\d(?:/\d)?\s*BHK\s+OUTRIGHT|OUTRIGHT\s+PROPERTIES|FOR\s+SALE|FOR\s+RENT)\b)",
        "\n",
        raw,
    )

    # Existing numbered list boundaries.
    raw = re.sub(r"\s+(?=\d{1,2}[.)]\s+)", "\n", raw)

    return [x.strip() for x in raw.split("\n") if x.strip()]


def _split_dense_piece(piece: str) -> List[str]:
    """
    Split only when multiple strong project/property anchors occur.
    If confidence is weak, return the original piece unchanged and let it remain reviewable.
    """
    s = str(piece or "")
    matches = list(PROJECT_ANCHOR_RE.finditer(s))

    if len(matches) < 2:
        return [_norm(s)]

    parts: List[str] = []
    prefix = _norm(s[:matches[0].start()])

    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(s)
        chunk = _norm(s[m.start():end].strip(" -*|"))
        if _is_entity_candidate(chunk):
            parts.append(chunk)

    if len(parts) >= 2:
        return parts

    return [_norm(s)]


def reconstruct_entities_v25(text_value: str) -> List[EntityBlock]:
    """
    Conservative boundary reconstructor.
    One output block should represent one physical property.
    Section headings may carry transaction/family only.
    Price, area, configuration, floor and micro-location are never copied.
    """
    pieces = _presegment(text_value)
    entities: List[EntityBlock] = []
    context: Dict[str, Optional[str]] = {"transaction": None, "property_family": None}

    for piece in pieces:
        if _looks_like_section(piece):
            context = _section_context(piece, context)
            continue

        for chunk in _split_dense_piece(piece):
            if not _is_entity_candidate(chunk):
                # A non-entity may still change section context.
                context = _section_context(chunk, context)
                continue

            inherited: List[str] = []
            if context.get("transaction"):
                inherited.append(context["transaction"])
            # Family is reference-only because generic family headings are weaker.
            parent_refs: List[str] = []
            if context.get("property_family"):
                parent_refs.append(context["property_family"])

            entities.append(
                EntityBlock(
                    index=len(entities) + 1,
                    own_text=chunk,
                    inherited_context=inherited,
                    sibling_facts_do_not_copy=[],
                    parent_context_reference_only=parent_refs,
                    method="v25_boundary",
                    needs_split=False,
                    reason=None,
                )
            )

    # Fail-safe: if we somehow produced nothing, do not invent an entity.
    return entities


def _own_location(text_value: str) -> Optional[str]:
    s = _norm(text_value)

    m = DLF_RE.search(s)
    if m:
        phase = m.group(1) or m.group(2)
        return f"DLF Phase {phase}"

    if SUSHANT_RE.search(s):
        return "Sushant Lok 1"
    if BANDRA_WEST_RE.search(s):
        return "Bandra West"
    if KHAR_WEST_RE.search(s):
        return "Khar West"
    if GULMOHAR_RE.search(s):
        return "Gulmohar Road"
    if JVPD_RE.search(s):
        return "JVPD"
    if JUHU_RE.search(s):
        return "Juhu"

    try:
        return v24._broad_locality_from_text(s)
    except Exception:
        return None


def _strict_rent_from_own_text(text_value: str, family: Optional[str]) -> bool:
    """
    Conservative RENT inference:
    - explicit RENT always qualifies;
    - otherwise requires residential identity plus lakh/k amount AND
      maintenance/furnishing context, with no sale/rate/crore signal.
    """
    s = _norm(text_value)

    if EXPLICIT_RENT_RE.search(s) and not EXPLICIT_SALE_RE.search(s):
        return True

    if family != "RESIDENTIAL":
        return False
    if EXPLICIT_SALE_RE.search(s) or RATE_RE.search(s):
        return False
    if re.search(r"(?i)\b(?:CR|CRORE|CRORES)\b", s):
        return False
    if not MONEY_RE.search(s):
        return False
    if not (CONFIG_RE.search(s) or PROPERTY_RE.search(s)):
        return False
    if not (MAINT_RE.search(s) or FURNISH_RE.search(s)):
        return False

    # Only rent-like denominations.
    return bool(re.search(r"(?i)\b\d+(?:\.\d+)?\s*(?:K|L|LAC|LACS|LAKH|LAKHS)\b", s))


def _strict_sale_from_own_text(text_value: str) -> bool:
    s = _norm(text_value)
    if RATE_RE.search(s):
        return False
    if EXPLICIT_RENT_RE.search(s):
        return False
    if re.search(r"(?i)\b(?:OUTRIGHT|FOR\s+SALE|RESALE)\b", s):
        return True
    # Crore total with a property identity is a strong sale signal.
    return bool(
        re.search(r"(?i)\b\d+(?:\.\d+)?\s*(?:CR|CRORE|CRORES)\b", s)
        and (CONFIG_RE.search(s) or PROPERTY_RE.search(s))
    )


def _enrich_own_text(candidate: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(candidate)
    row["review_reasons"] = list(candidate.get("review_reasons") or [])
    row["location_hierarchy"] = dict(candidate.get("location_hierarchy") or {})
    row["provenance"] = dict(candidate.get("provenance") or {})

    own = _norm(row.get("own_text_redacted"))
    tx_before = row.get("transaction")
    loc_before = row.get("location")

    if not row.get("transaction"):
        if _strict_rent_from_own_text(own, row.get("property_family")):
            row["transaction"] = "RENT"
            row["review_reasons"] = [
                x for x in row["review_reasons"] if x != "TRANSACTION_MISSING"
            ]
            row["review_reasons"].append("TRANSACTION_RECOVERED_FROM_OWN_TEXT_RENT_V25")
        elif _strict_sale_from_own_text(own):
            row["transaction"] = "SALE"
            row["review_reasons"] = [
                x for x in row["review_reasons"] if x != "TRANSACTION_MISSING"
            ]
            row["review_reasons"].append("TRANSACTION_RECOVERED_FROM_OWN_TEXT_SALE_V25")

    if not row.get("location"):
        locality = _own_location(own)
        if locality:
            row["location"] = locality
            row["display_location"] = locality
            hierarchy = dict(row.get("location_hierarchy") or {})
            hierarchy["locality"] = locality
            hierarchy["display_location"] = locality
            hierarchy["source"] = "OWN_TEXT_NORMALIZATION_V25"
            hierarchy["confidence"] = 0.97
            row["location_hierarchy"] = hierarchy
            row["review_reasons"] = [
                x for x in row["review_reasons"] if x not in (
                    "LOCATION_MISSING",
                    "MICRO_LOCATION_WITHOUT_PARENT_LOCALITY",
                )
            ]
            row["review_reasons"].append("LOCATION_RECOVERED_FROM_OWN_TEXT_V25")

    # Recompute quality conservatively for AVAILABILITY only.
    if row.get("classification") == "AVAILABILITY":
        blockers = {
            "BOUNDARY_NEEDS_SPLIT",
            "AMBIGUOUS_RATE_NOT_TOTAL_PRICE",
            "IMPLAUSIBLE_SALE_TOTAL_REJECTED",
            "IMPLAUSIBLE_RENT_TOTAL_REJECTED",
            "PHONE_LIKE_OR_EXTREME_MONEY_REJECTED",
            "PHONE_LIKE_MONEY_REJECTED",
            "LOCATION_NOT_SUPPORTED_BY_OWN_TEXT",
            "TRANSACTION_MISSING",
            "PROPERTY_FAMILY_MISSING",
            "LOCATION_MISSING",
            "PROPERTY_SPECIFIC_FACT_MISSING",
            "NOISE",
        }

        effective = set(row.get("review_reasons") or [])
        effective.discard("TRANSACTION_RECOVERED_FROM_OWN_TEXT_RENT_V25")
        effective.discard("TRANSACTION_RECOVERED_FROM_OWN_TEXT_SALE_V25")
        effective.discard("LOCATION_RECOVERED_FROM_OWN_TEXT_V25")
        effective.discard("OWN_CONFIGURATION_ACCEPTED_AS_PROPERTY_FACT")
        effective.discard("OWN_TEXT_TOTAL_MONEY_RECOVERED")
        effective.discard("ASSET_FAMILY_CORRECTED_FROM_INTENDED_USE")
        effective.discard("LOCATION_RESOLVED_FROM_OWN_TEXT")

        if not row.get("transaction"):
            effective.add("TRANSACTION_MISSING")
        if not row.get("property_family"):
            effective.add("PROPERTY_FAMILY_MISSING")
        if not row.get("location"):
            effective.add("LOCATION_MISSING")

        row["quality"] = "CLEAN" if not effective.intersection(blockers) else "UNDER_REVIEW"

    row["own_text_intelligence_v25"] = {
        "transaction_before": tx_before,
        "transaction_after": row.get("transaction"),
        "location_before": loc_before,
        "location_after": row.get("location"),
        "transaction_recovered": tx_before != row.get("transaction"),
        "location_recovered": loc_before != row.get("location"),
        "llm_used": False,
        "database_write": False,
    }

    prov = dict(row.get("provenance") or {})
    prov["v25"] = {
        "boundary": "OWN_TEXT_ATOMIC_ENTITY",
        "section_transaction_context_only": True,
        "price_inherited": False,
        "area_inherited": False,
        "configuration_inherited": False,
        "floor_inherited": False,
        "micro_location_inherited": False,
        "sibling_property_specific_facts_used": False,
    }
    row["provenance"] = prov
    return row


def _extract_v25_entity(entity: EntityBlock, burst_group_id: str) -> Dict[str, Any]:
    # Reuse established privacy/money/location guards from V24.
    candidate = v24._extract_entity(entity, burst_group_id, shared_defaults={})
    return _enrich_own_text(candidate)


def _reason_counts(candidates: List[Dict[str, Any]]) -> Dict[str, int]:
    counter: Counter[str] = Counter()
    for c in candidates:
        if c.get("quality") != "CLEAN":
            counter.update(c.get("review_reasons") or [])
    return dict(counter.most_common())


def _benchmark(engine, limit: int) -> Dict[str, Any]:
    rows = v24._load_bursts(engine, limit)

    old_base = stabilizer._run_deterministic_base(engine, limit)
    old_candidates = [
        c
        for b in (old_base.get("bursts") or [])
        for c in (b.get("candidates") or [])
    ]

    bursts = []
    candidates: List[Dict[str, Any]] = []

    for row in rows:
        entities = reconstruct_entities_v25(row.get("burst_text") or "")
        out_candidates = []

        for entity in entities:
            c = _extract_v25_entity(entity, row["burst_group_id"])
            out_candidates.append(c)
            candidates.append(c)

        bursts.append({
            "burst_group_id": row["burst_group_id"],
            "source_type": row.get("source_type"),
            "source_group": row.get("source_group"),
            "old_segment_count": int(row.get("old_segment_count") or 0),
            "v25_entity_count": len(entities),
            "candidates": out_candidates,
        })

    total = len(candidates)
    old_boundary_needs_split = sum(
        1 for c in old_candidates if c.get("boundary_needs_split")
    )
    new_boundary_needs_split = sum(
        1 for c in candidates if c.get("boundary_needs_split")
    )

    counts = {
        "burst_sample_size": len(rows),
        "old_reconstructed_entity_count": len(old_candidates),
        "v25_reconstructed_entity_count": total,
        "old_boundary_needs_split": old_boundary_needs_split,
        "v25_boundary_needs_split": new_boundary_needs_split,
        "v25_clean": sum(1 for c in candidates if c.get("quality") == "CLEAN"),
        "v25_under_review": sum(1 for c in candidates if c.get("quality") != "CLEAN"),
        "v25_clean_rate": round(
            sum(1 for c in candidates if c.get("quality") == "CLEAN") / total, 4
        ) if total else 0.0,
        "v25_availability": sum(
            1 for c in candidates if c.get("classification") == "AVAILABILITY"
        ),
        "v25_requirements": sum(
            1 for c in candidates if c.get("classification") == "REQUIREMENT"
        ),
        "v25_ambiguous_or_noise": sum(
            1 for c in candidates if c.get("classification") in ("AMBIGUOUS", "NOISE")
        ),
        "own_text_transaction_recoveries": sum(
            1 for c in candidates
            if (c.get("own_text_intelligence_v25") or {}).get("transaction_recovered")
        ),
        "own_text_location_recoveries": sum(
            1 for c in candidates
            if (c.get("own_text_intelligence_v25") or {}).get("location_recovered")
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
        "old_reconstructor_version": getattr(v24, "RECONSTRUCTOR_ENGINE_VERSION", None),
        "counts": counts,
        "under_review_reasons_v25": _reason_counts(candidates),
        "safety_contract": {
            "read_only_shadow": True,
            "database_writes": False,
            "canonical_tables_modified": False,
            "orchestrator_modified": False,
            "live_reconstructor_replaced": False,
            "matcher_modified": False,
            "whatsapp_live_modified": False,
            "raw_data_deleted": False,
            "llm_disabled_for_benchmark": True,
            "contacts_redacted_before_property_ai": True,
            "price_inherited": False,
            "area_inherited": False,
            "configuration_inherited": False,
            "floor_inherited": False,
            "micro_location_inherited": False,
            "sibling_property_specific_facts_used": False,
        },
        "writes_performed": 0,
        "bursts": bursts,
    }


def _regression_demo() -> Dict[str, Any]:
    tests = []

    bundle = (
        "4/5 BHK OUTRIGHT - JUHU\n"
        "✨ GULMOHAR ROAD 4 BHK 1750 Carpet 10.50 Cr Negotiable\n"
        "✨ JVPD 4 BHK 1950 Carpet 14.50 Cr Negotiable\n"
        "PREMIUM BUNGALOWS\n"
        "✨ RUIYA PARK BUNGALOW 6250 Carpet 35 Cr Negotiable\n"
        "PREMIUM RENTAL PROPERTIES\n"
        "✨ DLH LEGACY 3 BHK 1250 Sq.ft Semi-Furnished 2 Car Parking Rent: 3.00 Lakhs\n"
        "✨ PARK GRANDEUR 3 BHK 1300 Sq.ft Semi-Furnished Rent: 2.50 Lakhs"
    )
    entities = reconstruct_entities_v25(bundle)
    tests.append({
        "name": "mixed_sale_rent_bundle_split",
        "entity_count": len(entities),
        "transactions": [e.inherited_context for e in entities],
        "texts": [e.own_text for e in entities],
    })

    rent_candidate = {
        "classification": "AVAILABILITY",
        "transaction": None,
        "property_family": "RESIDENTIAL",
        "location": "DLF Phase 2",
        "own_text_redacted": "DLFPHASE2 300 SYDS 4BHK+ SER 1.20LAC + MAINT FULLY FURNISHED",
        "review_reasons": ["TRANSACTION_MISSING", "OWN_CONFIGURATION_ACCEPTED_AS_PROPERTY_FACT"],
        "quality": "UNDER_REVIEW",
        "provenance": {},
    }
    rent_out = _enrich_own_text(rent_candidate)
    tests.append({
        "name": "rent_from_own_text",
        "transaction": rent_out.get("transaction"),
        "quality": rent_out.get("quality"),
    })

    loc_candidate = {
        "classification": "AVAILABILITY",
        "transaction": "RENT",
        "property_family": "RESIDENTIAL",
        "location": None,
        "own_text_redacted": "SHUSHANTLOK1 215 SYDS FULLY FURNISHED 3BHK+SER RENT 90K+MAINT",
        "review_reasons": ["LOCATION_MISSING", "OWN_CONFIGURATION_ACCEPTED_AS_PROPERTY_FACT"],
        "quality": "UNDER_REVIEW",
        "provenance": {},
    }
    loc_out = _enrich_own_text(loc_candidate)
    tests.append({
        "name": "sushant_lok_normalization",
        "location": loc_out.get("location"),
        "quality": loc_out.get("quality"),
    })

    passed = (
        len(entities) >= 5
        and any("SALE" in e.inherited_context for e in entities[:3])
        and any("RENT" in e.inherited_context for e in entities[-2:])
        and rent_out.get("transaction") == "RENT"
        and loc_out.get("location") == "Sushant Lok 1"
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
    status_route = "/api/v7/property-ai/boundary-intelligence-v25/status"

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
            "read_only_shadow": True,
            "database_writes": False,
            "canonical_tables_modified": False,
            "orchestrator_modified": False,
            "live_reconstructor_replaced": False,
            "matcher_modified": False,
            "whatsapp_live_modified": False,
            "raw_data_deleted": False,
            "llm_disabled_for_benchmark": True,
            "contacts_redacted_before_property_ai": True,
        })

    @app.get("/api/v7/property-ai/boundary-intelligence-v25/regression-test")
    def regression_test():
        return JSONResponse(_regression_demo())

    @app.get("/api/v7/property-ai/boundary-intelligence-v25/preview")
    def preview(limit: int = Query(25, ge=1, le=100)):
        return JSONResponse(_benchmark(engine, limit))

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": status_route,
        "regression": "/api/v7/property-ai/boundary-intelligence-v25/regression-test",
        "preview": "/api/v7/property-ai/boundary-intelligence-v25/preview?limit=25",
        "writes_enabled": False,
    }

