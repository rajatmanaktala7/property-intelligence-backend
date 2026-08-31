from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import Query
from fastapi.responses import JSONResponse
from sqlalchemy import text

from property_brain.schemas import Segment
import property_brain.stages.s4_extractor as s4_extractor
from property_brain.stages.s4_extractor import extract as base_extract
from property_brain.stages.s3_entity_segmentation_v23 import (
    VERSION as RECONSTRUCTOR_ENGINE_VERSION,
    reconstruct_entities,
)

import alliance_property_ai_v1 as property_ai

VERSION = "2.4.2-TEMPLATE-LOCATION-PROVENANCE-FIX"

PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)")
EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")

AMBIGUOUS_RATE_RE = re.compile(
    r"(?i)(?:"
    r"\bRATE\b.{0,50}\b(?:PER|P\.?S\.?F\.?|SQ\.?\s*FT|SQFT|SFT|ACRE|YARD|GAJ)\b|"
    r"\bPER\s+(?:SQ\.?\s*FT|SQFT|SFT|FT|FOOT|ACRE|ACRES|YARD|YARDS|GAJ)\b|"
    r"/\s*(?:SQ\.?\s*FT|SQFT|SFT|FT|FOOT|ACRE|ACRES|YARD|YARDS|GAJ)\b|"
    r"\b\d[\d,]*(?:\.\d+)?\s*/\s*(?:K\s*)?(?:PER\s*)?(?:FUT|FT|SQFT|SFT|ACRE|ACRES)\b"
    r")"
)

PRICE_RATE_WORD_RE = re.compile(
    r"(?i)\b(?:RATE|PER\s+SQ\.?\s*FT|PER\s+SQFT|PSF|P\.S\.F)\b"
)

OWN_MONEY_RE = re.compile(
    r"(?i)(?<!\d)(\d+(?:\.\d+)?)\s*(CR|CRORE|CRORES|L|LAC|LACS|LAKH|LAKHS|K|THOUSAND)\b"
)

EXPLICIT_SALE_RE = re.compile(
    r"(?i)\b(?:FOR\s+SALE|AVAILABLE\s+FOR\s+SALE|AVL\s+FOR\s+SALE|RESALE|OUTRIGHT)\b"
)
EXPLICIT_RENT_RE = re.compile(
    r"(?i)\b(?:FOR\s+RENT|AVAILABLE\s+FOR\s+RENT|AVL\s+FOR\s+RENT|TO\s+LET|FOR\s+LEASE)\b"
)

LAND_ASSET_RE = re.compile(
    r"(?i)\b(?:PLOT|LAND|ACRE|FARM\s*LAND|RESIDENTIAL\s+PLOT|COMMERCIAL\s+PLOT)\b"
)
RESIDENTIAL_ASSET_RE = re.compile(
    r"(?i)\b(?:VILLA|APARTMENT|FLAT|KOTHI|PENTHOUSE|BUILDER\s+FLOOR|INDEPENDENT\s+FLOOR|"
    r"\d(?:\.\d)?\s*BHK|\d\s*\.?\s*BR\.?)\b"
)
COMMERCIAL_ASSET_RE = re.compile(
    r"(?i)\b(?:OFFICE|SHOWROOM|SHOP|WAREHOUSE|GODOWN|BANQUET|RESTAURANT|CAFE|CLUB|LOUNGE|"
    r"COMMERCIAL\s+SPACE|COMMERCIAL\s+BUILDING|BASEMENT)\b"
)

DWARKA_SECTOR_RE_1 = re.compile(r"(?i)\bDWARKA\s+SECTOR\s*[-:]?\s*(\d{1,3}[A-Z]?)\b")
DWARKA_SECTOR_RE_2 = re.compile(r"(?i)\bSECTOR\s*[-:]?\s*(\d{1,3}[A-Z]?)\s*,?\s*DWARKA\b")

BUDGET_WORD_RE = re.compile(r"(?i)\bBUDGET\b")
BUDGET_MONEY_RE = re.compile(
    r"(?i)(\d+(?:\.\d+)?)\s*(CR|CRORE|CRORES|L|LAC|LACS|LAKH|LAKHS|K|THOUSAND)?"
)


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _norm_key(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper()).strip()


def _redact(value: str) -> str:
    out = PHONE_RE.sub("[PHONE_REDACTED]", str(value or ""))
    out = EMAIL_RE.sub("[EMAIL_REDACTED]", out)
    return out


def _safe_parent_reference(values: List[str]) -> List[str]:
    return [_redact(_norm(v)) for v in (values or []) if _norm(v)]


def _explicit_transaction_from_text(text_value: str) -> Optional[str]:
    sale = bool(EXPLICIT_SALE_RE.search(text_value))
    rent = bool(EXPLICIT_RENT_RE.search(text_value))
    if sale and not rent:
        return "SALE"
    if rent and not sale:
        return "RENT"
    return None


def _explicit_transaction(entity) -> Optional[str]:
    for item in entity.inherited_context or []:
        token = _norm(item).upper()
        if token in ("SALE", "RENT"):
            return token
    return None


def _asset_family_from_text(text_value: str) -> Optional[str]:
    if LAND_ASSET_RE.search(text_value):
        return "LAND"
    if RESIDENTIAL_ASSET_RE.search(text_value):
        return "RESIDENTIAL"
    if COMMERCIAL_ASSET_RE.search(text_value):
        return "COMMERCIAL"
    return None


def _money_to_inr(number: float, unit: str) -> float:
    u = str(unit or "").upper()
    if u in ("CR", "CRORE", "CRORES"):
        return number * 10000000
    if u in ("L", "LAC", "LACS", "LAKH", "LAKHS"):
        return number * 100000
    if u in ("K", "THOUSAND"):
        return number * 1000
    return number


def _own_money_total(text_value: str, transaction: Optional[str]) -> Optional[Dict[str, Any]]:
    if not transaction:
        return None
    if AMBIGUOUS_RATE_RE.search(text_value) or PRICE_RATE_WORD_RE.search(text_value):
        return None
    matches = list(OWN_MONEY_RE.finditer(text_value))
    if len(matches) != 1:
        return None
    m = matches[0]
    value = _money_to_inr(float(m.group(1)), m.group(2))
    if transaction == "SALE" and value < 100000:
        return None
    if transaction == "RENT" and value < 1000:
        return None
    return {"value": value, "raw": m.group(0).strip()}


def _location_alias_hits(text_value: str) -> List[str]:
    own_key = f" {_norm_key(text_value)} "
    hits = []
    aliases = getattr(s4_extractor, "ALIASES", {}) or {}
    for alias, canonical in aliases.items():
        alias_key = _norm_key(alias)
        if alias_key and f" {alias_key} " in own_key:
            hits.append((len(alias_key), str(canonical)))
    hits.sort(reverse=True)
    out = []
    for _, canonical in hits:
        if canonical not in out:
            out.append(canonical)
    return out


def _resolve_location_from_own_text(text_value: str) -> Optional[str]:
    m = DWARKA_SECTOR_RE_1.search(text_value) or DWARKA_SECTOR_RE_2.search(text_value)
    if m:
        return f"Dwarka Sector {m.group(1).upper()}"
    hits = _location_alias_hits(text_value)
    return hits[0] if hits else None


def _location_supported_by_own_text(location: str, own_text: str) -> bool:
    if not location:
        return False

    loc_key = _norm_key(location)
    own_key = _norm_key(own_text)

    if loc_key and loc_key in own_key:
        return True

    own_resolved = _resolve_location_from_own_text(own_text)
    if own_resolved and _norm_key(own_resolved) == loc_key:
        return True

    aliases = getattr(s4_extractor, "ALIASES", {}) or {}
    for alias, canonical in aliases.items():
        if _norm_key(canonical) != loc_key:
            continue
        alias_key = _norm_key(alias)
        if alias_key and f" {alias_key} " in f" {own_key} ":
            return True

    return False


def _acceptable_locations(text_value: str) -> List[str]:
    out = _location_alias_hits(text_value)
    for rx in (DWARKA_SECTOR_RE_1, DWARKA_SECTOR_RE_2):
        for m in rx.finditer(text_value):
            value = f"Dwarka Sector {m.group(1).upper()}"
            if value not in out:
                out.append(value)
    return out


def _budget_range(text_value: str) -> Optional[Dict[str, Any]]:
    m = BUDGET_WORD_RE.search(text_value)
    if not m:
        return None

    tail = text_value[m.end():m.end() + 120]
    items = []
    for mm in BUDGET_MONEY_RE.finditer(tail):
        try:
            num = float(mm.group(1))
        except Exception:
            continue
        unit = mm.group(2)
        if num <= 0 or num > 1000000000:
            continue
        items.append([num, unit])

    items = items[:2]
    if not items:
        return None

    if len(items) == 2:
        if not items[0][1] and items[1][1]:
            items[0][1] = items[1][1]
        if not items[1][1] and items[0][1]:
            items[1][1] = items[0][1]

    values = []
    for num, unit in items:
        if unit:
            values.append(_money_to_inr(num, unit))

    if not values:
        return None

    return {
        "min_inr": min(values),
        "max_inr": max(values),
        "evidence": _redact(_norm(tail[:100])),
    }


def _derive_group_defaults(entities) -> Dict[str, Any]:
    if len(entities) < 2:
        return {}

    if not all(e.method == "block_anchor" for e in entities):
        return {}

    first_text = entities[0].own_text
    tx = _explicit_transaction_from_text(first_text)
    family = _asset_family_from_text(first_text)
    location = _resolve_location_from_own_text(first_text)

    if not (tx and family and location):
        return {}

    return {
        "transaction": tx,
        "property_family": family,
        "location": location,
        "evidence": _redact(_norm(first_text[:220])),
        "scope": "BLOCK_ANCHOR_TEMPLATE_ONLY",
    }


def _money_guard(fields: Dict[str, Any], raw_text: str) -> tuple[Dict[str, Any], List[str]]:
    fields = dict(fields or {})
    flags: List[str] = []

    money = fields.get("money")
    transaction = fields.get("transaction")

    if AMBIGUOUS_RATE_RE.search(raw_text) or PRICE_RATE_WORD_RE.search(raw_text):
        if money:
            fields["money"] = None
        flags.append("AMBIGUOUS_RATE_NOT_TOTAL_PRICE")
        return fields, flags

    if isinstance(money, dict):
        try:
            value = float(money.get("value"))
        except Exception:
            value = None

        raw_money = _norm(money.get("raw"))

        if value is not None:
            if transaction == "SALE" and value < 100000:
                fields["money"] = None
                flags.append("IMPLAUSIBLE_SALE_TOTAL_REJECTED")
            elif transaction == "RENT" and value < 1000:
                fields["money"] = None
                flags.append("IMPLAUSIBLE_RENT_TOTAL_REJECTED")
            elif value >= 6000000000:
                fields["money"] = None
                flags.append("PHONE_LIKE_OR_EXTREME_MONEY_REJECTED")

        if raw_money and PHONE_RE.search(raw_money):
            fields["money"] = None
            flags.append("PHONE_LIKE_MONEY_REJECTED")

    return fields, list(dict.fromkeys(flags))


def _location_guard(
    fields: Dict[str, Any],
    own_text: str,
    trusted_template_location: Optional[str] = None,
) -> tuple[Dict[str, Any], List[str]]:
    """
    2.4.2 fix:
    A broad locality may be inherited only from the already-validated
    BLOCK_ANCHOR_TEMPLATE_ONLY context. Do not reject that location merely
    because a child row contains only 'K Block' or 'C Block'.

    All other extracted locations must still be supported by the child's own text.
    """
    fields = dict(fields or {})
    flags: List[str] = []

    own_resolved = _resolve_location_from_own_text(own_text)
    current = _norm(fields.get("location_raw"))
    trusted = _norm(trusted_template_location)

    if trusted and current and _norm_key(current) == _norm_key(trusted):
        flags.append("LOCATION_SUPPORTED_BY_FIELD_SCOPED_TEMPLATE")
        return fields, flags

    if not current and trusted:
        fields["location_raw"] = trusted
        flags.append("LOCATION_SUPPORTED_BY_FIELD_SCOPED_TEMPLATE")
        return fields, flags

    if not current and own_resolved:
        fields["location_raw"] = own_resolved
        flags.append("LOCATION_RESOLVED_FROM_OWN_TEXT")
    elif current and _location_supported_by_own_text(current, own_text):
        pass
    elif current:
        fields["location_raw"] = None
        flags.append("LOCATION_NOT_SUPPORTED_BY_OWN_TEXT")

    return fields, flags


def _classification_guard(classification: str, entity) -> tuple[str, List[str]]:
    flags: List[str] = []
    if entity.method == "requirement_continuation_merged" and classification != "REQUIREMENT":
        classification = "REQUIREMENT"
        flags.append("REQUIREMENT_BOUNDARY_CLASSIFICATION_ENFORCED")
    return classification, flags


def _quality(
    classification: str,
    fields: Dict[str, Any],
    entity,
    flags: List[str],
) -> tuple[str, List[str]]:
    reasons = list(flags)

    if entity.needs_split:
        reasons.append("BOUNDARY_NEEDS_SPLIT")

    transaction = fields.get("transaction")
    family = fields.get("property_family")
    location = fields.get("location_raw")
    area = fields.get("area")
    money = fields.get("money")

    if classification == "AVAILABILITY":
        if not transaction:
            reasons.append("TRANSACTION_MISSING")
        if not family:
            reasons.append("PROPERTY_FAMILY_MISSING")
        if not location:
            reasons.append("LOCATION_MISSING")
        if not (area or money):
            reasons.append("PROPERTY_SPECIFIC_FACT_MISSING")

    elif classification == "REQUIREMENT":
        if not family:
            reasons.append("PROPERTY_FAMILY_MISSING")
        if not transaction:
            reasons.append("TRANSACTION_MISSING")

    elif classification == "NOISE":
        reasons.append("NOISE")
    else:
        reasons.append("CLASSIFICATION_AMBIGUOUS")

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
        "CLASSIFICATION_AMBIGUOUS",
    }

    quality = "CLEAN" if not any(r in blockers for r in reasons) else "UNDER_REVIEW"
    return quality, list(dict.fromkeys(reasons))


def _extract_entity(
    entity,
    burst_group_id: str,
    shared_defaults: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    safe_text = _redact(entity.own_text)

    seg = Segment(
        segment_id=uuid4(),
        raw_ids=[],
        text=safe_text,
        split_method="single",
        burst_group_id=uuid4(),
        insufficient=bool(entity.needs_split),
    )

    base = base_extract(seg)

    explicit_tx = _explicit_transaction(entity)
    if explicit_tx:
        base.fields["transaction"] = explicit_tx
        base.field_confidence["transaction"] = 0.99

    enhanced = property_ai.enhance_extraction(base)
    classification, class_flags = _classification_guard(
        enhanced.classification,
        entity,
    )

    fields = dict(enhanced.fields or {})
    confidence = dict(enhanced.field_confidence or {})
    fields.pop("contact_numbers", None)
    fields["raw_text"] = safe_text

    flags = list(class_flags)
    inherited_fields: Dict[str, Any] = {}

    explicit_family = _asset_family_from_text(safe_text)
    if explicit_family and fields.get("property_family") != explicit_family:
        fields["property_family"] = explicit_family
        confidence["property_family"] = max(confidence.get("property_family", 0), 0.97)
        flags.append("ASSET_FAMILY_CORRECTED_FROM_INTENDED_USE")

    defaults = dict(shared_defaults or {})
    trusted_template_location = None

    if defaults:
        if not fields.get("transaction") and defaults.get("transaction"):
            fields["transaction"] = defaults["transaction"]
            confidence["transaction"] = 0.94
            inherited_fields["transaction"] = defaults["transaction"]

        if not fields.get("property_family") and defaults.get("property_family"):
            fields["property_family"] = defaults["property_family"]
            confidence["property_family"] = 0.94
            inherited_fields["property_family"] = defaults["property_family"]

        if defaults.get("location"):
            trusted_template_location = defaults["location"]
            if not fields.get("location_raw"):
                fields["location_raw"] = trusted_template_location
                confidence["location_raw"] = 0.92
                inherited_fields["location"] = trusted_template_location
            elif _norm_key(fields.get("location_raw")) == _norm_key(trusted_template_location):
                inherited_fields["location"] = trusted_template_location

        if inherited_fields:
            flags.append("FIELD_SCOPED_TEMPLATE_CONTEXT_USED")

    if not fields.get("money"):
        own_money = _own_money_total(safe_text, fields.get("transaction"))
        if own_money:
            fields["money"] = own_money
            confidence["money"] = 0.96
            flags.append("OWN_TEXT_TOTAL_MONEY_RECOVERED")

    fields, money_flags = _money_guard(fields, safe_text)
    fields, location_flags = _location_guard(
        fields,
        safe_text,
        trusted_template_location=trusted_template_location,
    )
    flags.extend(money_flags)
    flags.extend(location_flags)

    acceptable_locations = []
    budget_range = None

    if classification == "REQUIREMENT":
        acceptable_locations = _acceptable_locations(safe_text)
        if len(acceptable_locations) > 1:
            fields["location_raw"] = None
            flags.append("MULTI_LOCATION_REQUIREMENT_PRESERVED")
        budget_range = _budget_range(safe_text)

    quality, reasons = _quality(
        classification,
        fields,
        entity,
        flags,
    )

    parent_reference = _safe_parent_reference(
        entity.parent_context_reference_only
    )

    return {
        "entity_index": entity.index,
        "boundary_method": entity.method,
        "boundary_needs_split": entity.needs_split,
        "classification": classification,
        "transaction": fields.get("transaction"),
        "property_family": fields.get("property_family"),
        "location": fields.get("location_raw"),
        "acceptable_locations": acceptable_locations,
        "configuration": fields.get("configuration"),
        "area": fields.get("area"),
        "money": fields.get("money"),
        "budget_range": budget_range,
        "suitable_uses": fields.get("suitable_uses") or [],
        "features": fields.get("features") or {},
        "negotiable": fields.get("negotiable"),
        "field_confidence": confidence,
        "source_evidence": fields.get("source_evidence") or {},
        "ai_understanding": fields.get("ai_understanding") or {},
        "quality": quality,
        "review_reasons": reasons,
        "own_text_redacted": safe_text,
        "parent_context_reference_only": parent_reference,
        "inherited_context": list(entity.inherited_context or []),
        "field_scoped_template_context": inherited_fields,
        "field_scoped_template_evidence": (
            defaults.get("evidence") if inherited_fields else None
        ),
        "sibling_count": len(entity.sibling_facts_do_not_copy or []),
        "privacy": {
            "phone_redacted_before_property_ai": True,
            "email_redacted_before_property_ai": True,
            "contacts_in_output": False,
        },
        "provenance": {
            "own_text": "OWN_FACT",
            "inherited_context": "EXPLICIT_GLOBAL_INTENT_ONLY",
            "field_scoped_template_context": "TRANSACTION_FAMILY_BROAD_LOCATION_ONLY",
            "parent_context_reference_only": "REFERENCE_ONLY_NOT_AUTO_APPLIED",
            "sibling_facts": "DO_NOT_COPY",
            "property_specific_inheritance": False,
        },
    }


def _load_bursts(engine, limit: int):
    sql = """
    SELECT
        b.burst_group_id::text AS burst_group_id,
        b.source_type,
        b.source_group,
        b.captured_at,
        b.burst_text,
        COUNT(DISTINCT s.segment_id) AS old_segment_count,
        COUNT(DISTINCT rq.review_id)
            FILTER (WHERE rq.status='OPEN') AS open_review_count
    FROM pb_bursts b
    JOIN pb_segments s
      ON s.burst_group_id = b.burst_group_id
    LEFT JOIN pb_extractions e
      ON e.segment_id = s.segment_id
    LEFT JOIN pb_review_queue rq
      ON rq.target_type='extraction'
     AND rq.target_id=e.extraction_id
    GROUP BY
        b.burst_group_id,
        b.source_type,
        b.source_group,
        b.captured_at,
        b.burst_text
    HAVING COUNT(DISTINCT s.segment_id) >= 2
    ORDER BY
        COUNT(DISTINCT s.segment_id) DESC,
        b.captured_at DESC
    LIMIT :lim
    """

    with engine.connect() as c:
        rows = c.execute(text(sql), {"lim": limit}).mappings().all()

    return [dict(r) for r in rows]


def _benchmark(engine, limit: int):
    rows = _load_bursts(engine, limit)

    counts = {
        "burst_sample_size": len(rows),
        "reconstructed_entity_count": 0,
        "clean_candidates": 0,
        "under_review": 0,
        "availability": 0,
        "requirements": 0,
        "ambiguous_or_noise": 0,
        "ambiguous_money_rejected": 0,
        "boundary_needs_split": 0,
        "field_scoped_template_context_used": 0,
        "llm_used": 0,
        "privacy_redacted": 0,
    }

    bursts = []

    for row in rows:
        entities = reconstruct_entities(row.get("burst_text") or "")
        shared_defaults = _derive_group_defaults(entities)
        candidates = []

        for entity in entities:
            candidate = _extract_entity(
                entity,
                row["burst_group_id"],
                shared_defaults=shared_defaults,
            )

            counts["reconstructed_entity_count"] += 1
            counts["clean_candidates"] += int(candidate["quality"] == "CLEAN")
            counts["under_review"] += int(candidate["quality"] != "CLEAN")
            counts["availability"] += int(candidate["classification"] == "AVAILABILITY")
            counts["requirements"] += int(candidate["classification"] == "REQUIREMENT")
            counts["ambiguous_or_noise"] += int(candidate["classification"] in ("AMBIGUOUS", "NOISE"))
            counts["boundary_needs_split"] += int(candidate["boundary_needs_split"])
            counts["ambiguous_money_rejected"] += int(
                "AMBIGUOUS_RATE_NOT_TOTAL_PRICE" in candidate["review_reasons"]
            )
            counts["field_scoped_template_context_used"] += int(
                bool(candidate["field_scoped_template_context"])
            )
            counts["llm_used"] += int(
                bool((candidate.get("ai_understanding") or {}).get("llm_used"))
            )
            counts["privacy_redacted"] += 1

            candidates.append(candidate)

        bursts.append(
            {
                "burst_group_id": row["burst_group_id"],
                "source_type": row["source_type"],
                "source_group": row["source_group"],
                "captured_at": (
                    row["captured_at"].isoformat()
                    if row.get("captured_at")
                    else None
                ),
                "old_segment_count": int(row.get("old_segment_count") or 0),
                "open_review_count": int(row.get("open_review_count") or 0),
                "reconstructed_entity_count": len(entities),
                "shared_defaults": {
                    k: v for k, v in shared_defaults.items() if k != "evidence"
                },
                "candidates": candidates,
            }
        )

    total = counts["reconstructed_entity_count"] or 1
    counts["clean_candidate_rate"] = round(
        counts["clean_candidates"] / total,
        4,
    )

    return {
        "status": "READY",
        "version": VERSION,
        "reconstructor_engine_version": RECONSTRUCTOR_ENGINE_VERSION,
        "mode": "READ_ONLY_SHADOW_EXTRACTION",
        "counts": counts,
        "writes_performed": 0,
        "canonical_tables_modified": False,
        "matcher_modified": False,
        "whatsapp_live_modified": False,
        "raw_data_deleted": False,
        "decision": (
            "SHADOW ONLY. Phase 2.4.2 fixes broad template-locality provenance "
            "without allowing property-specific sibling inheritance."
        ),
        "bursts": bursts,
    }


def _regression_demo():
    cases = {}

    e = reconstruct_entities(
        "EXCLUSIVE MANDATE FOR RENT | GK-2 Luxury 1st Floor Independent Floor "
        "3 BHK Fully Furnished"
    )[0]
    cases["gk2"] = _extract_entity(e, "demo")

    e = reconstruct_entities(
        "PREMIUM COMMERCIAL BASEMENT AVAILABLE FOR RENT - SECTOR 12, DWARKA "
        "3200 sq ft Carpet Area"
    )[0]
    cases["dwarka_sector"] = _extract_entity(e, "demo")

    e = reconstruct_entities(
        "DIRECT CLIENT RENTAL REQUIREMENT 3/4 BHK VILLA WITH PRIVATE POOL "
        "for commercial purposes. Preferred Locations: Vagator Anjuna Siolim Assagao "
        "Budget 1.5 - 2.25 Lakh/month"
    )[0]
    cases["villa_requirement"] = _extract_entity(e, "demo")

    e = reconstruct_entities(
        "Land Available For Sale Dwarka Sector 24 Size 1 Acres Plot Price 70Cr/Acres"
    )[0]
    cases["per_acre_rate"] = _extract_entity(e, "demo")

    ents = reconstruct_entities(
        "Kothi for sale in kalkaji 100 yards K Block Price 8 cr "
        "K Block 100 yards 7.50cr "
        "C Block 100 yards 8.25 cr "
        "G Block 200 yards 17 cr"
    )
    defaults = _derive_group_defaults(ents)
    cases["kalkaji"] = {
        "defaults": defaults,
        "candidates": [
            _extract_entity(e, "demo", defaults)
            for e in ents
        ],
    }

    return cases


def register(core):
    app = core.app
    engine = core.engine
    status_route = "/api/v7/property-ai/shadow-extraction/status"

    if any(getattr(route, "path", None) == status_route for route in app.router.routes):
        return {
            "status": "ALREADY_REGISTERED",
            "version": VERSION,
            "route": status_route,
        }

    @app.get(status_route)
    def status():
        return JSONResponse(
            {
                "status": "READY",
                "version": VERSION,
                "reconstructor_engine_version": RECONSTRUCTOR_ENGINE_VERSION,
                "mode": "READ_ONLY_SHADOW_EXTRACTION",
                "property_ai_engine": property_ai.VERSION,
                "privacy_redaction_before_property_ai": True,
                "ambiguous_rate_guard": True,
                "rate_per_area_guard": True,
                "location_alias_equivalence_guard": True,
                "dwarka_sector_resolver": True,
                "asset_identity_over_intended_use": True,
                "field_scoped_template_context": True,
                "field_scoped_template_allowed": [
                    "transaction",
                    "property_family",
                    "broad_location",
                ],
                "template_location_provenance_guard": True,
                "property_specific_inheritance": False,
                "parent_context_auto_apply": False,
                "database_writes": False,
                "canonical_tables_modified": False,
                "orchestrator_modified_by_phase24": False,
                "matcher_modified": False,
                "whatsapp_live_modified": False,
                "raw_data_deleted": False,
            }
        )

    @app.get("/api/v7/property-ai/shadow-extraction/regression-test")
    def regression_test():
        return JSONResponse(_regression_demo())

    @app.get("/api/v7/property-ai/shadow-extraction/preview")
    def preview(limit: int = Query(25, ge=1, le=100)):
        return JSONResponse(_benchmark(engine, limit))

    app.state.alliance_property_shadow_extraction_v24_registered = True

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": status_route,
        "preview": "/api/v7/property-ai/shadow-extraction/preview?limit=25",
        "writes_enabled": False,
    }
