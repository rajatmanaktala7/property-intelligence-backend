from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any, Dict, List
from uuid import uuid4

from fastapi import Query
from fastapi.responses import JSONResponse
from sqlalchemy import text

from property_brain.schemas import Segment
from property_brain.stages.s4_extractor import extract as base_extract
from property_brain.stages.s3_entity_segmentation_v23 import (
    VERSION as RECONSTRUCTOR_ENGINE_VERSION,
    reconstruct_entities,
)

import alliance_property_ai_v1 as property_ai

VERSION = "2.4.0-SHADOW-ENTITY-EXTRACTION-BRIDGE"

PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)")
EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")

AMBIGUOUS_RATE_RE = re.compile(
    r"(?i)(?:"
    r"\bRATE\b.{0,40}\b(?:PER|P\.?S\.?F\.?|SQ\.?\s*FT|SQFT|SFT)\b|"
    r"\b(?:PER|/)\s*(?:SQ\.?\s*FT|SQFT|SFT|FT|FOOT)\b|"
    r"\b\d[\d,]*(?:\.\d+)?\s*/\s*(?:K\s*)?(?:PER\s*)?(?:FUT|FT|SQFT|SFT)\b"
    r")"
)

PRICE_RATE_WORD_RE = re.compile(
    r"(?i)\b(?:RATE|PER\s+SQ\.?\s*FT|PER\s+SQFT|PSF|P\.S\.F)\b"
)

CONFIG_RE = re.compile(
    r"(?i)\b(?:\d(?:\.\d)?\s*BHK|\d\s*\.?\s*BR\.?|\d\s*BEDROOMS?)\b"
)

PROJECT_HINT_RE = re.compile(
    r"(?i)\b(?:PROJECT|TOWER|SOCIETY|GARDEN|RESIDENCY|RESIDENCES|HEIGHTS|ENCLAVE|ESTATE)\b"
)


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _redact(value: str) -> str:
    out = PHONE_RE.sub("[PHONE_REDACTED]", str(value or ""))
    out = EMAIL_RE.sub("[EMAIL_REDACTED]", out)
    return out


def _safe_parent_reference(values: List[str]) -> List[str]:
    return [
        _redact(_norm(value))
        for value in (values or [])
        if _norm(value)
    ]


def _explicit_transaction(entity) -> str | None:
    allowed = {"SALE", "RENT"}
    for item in entity.inherited_context or []:
        token = _norm(item).upper()
        if token in allowed:
            return token
    return None


def _money_guard(fields: Dict[str, Any], raw_text: str) -> tuple[Dict[str, Any], List[str]]:
    fields = dict(fields or {})
    flags: List[str] = []

    money = fields.get("money")
    transaction = fields.get("transaction")
    raw_upper = _norm(raw_text).upper()

    # Rate-per-area is not the same as total sale/rent consideration.
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


def _location_guard(fields: Dict[str, Any], own_text: str) -> tuple[Dict[str, Any], List[str]]:
    fields = dict(fields or {})
    flags: List[str] = []

    location = _norm(fields.get("location_raw"))

    if location and location.lower() not in _norm(own_text).lower():
        fields["location_raw"] = None
        flags.append("LOCATION_NOT_SUPPORTED_BY_OWN_TEXT")

    return fields, flags


def _classification_guard(classification: str, entity) -> tuple[str, List[str]]:
    flags: List[str] = []

    if entity.method == "requirement_continuation_merged":
        if classification != "REQUIREMENT":
            classification = "REQUIREMENT"
            flags.append("REQUIREMENT_BOUNDARY_CLASSIFICATION_ENFORCED")

    return classification, flags


def _quality(classification: str, fields: Dict[str, Any], entity, flags: List[str]) -> tuple[str, List[str]]:
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
        # Requirements may legally contain multiple acceptable locations.
        # Phase 2.4 does not auto-collapse them into one canonical location.

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

    quality = "CLEAN" if not any(reason in blockers for reason in reasons) else "UNDER_REVIEW"
    return quality, list(dict.fromkeys(reasons))


def _extract_entity(entity, burst_group_id: str) -> Dict[str, Any]:
    # Privacy boundary: the existing Property AI may call Gemini, so only redacted
    # entity text is supplied to it.
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

    # Contacts are never part of the shadow candidate payload.
    fields.pop("contact_numbers", None)
    fields["raw_text"] = safe_text

    fields, money_flags = _money_guard(fields, safe_text)
    fields, location_flags = _location_guard(fields, safe_text)

    flags = class_flags + money_flags + location_flags

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
        "configuration": fields.get("configuration"),
        "area": fields.get("area"),
        "money": fields.get("money"),
        "suitable_uses": fields.get("suitable_uses") or [],
        "features": fields.get("features") or {},
        "negotiable": fields.get("negotiable"),
        "field_confidence": enhanced.field_confidence,
        "source_evidence": fields.get("source_evidence") or {},
        "ai_understanding": fields.get("ai_understanding") or {},
        "quality": quality,
        "review_reasons": reasons,
        "own_text_redacted": safe_text,
        "parent_context_reference_only": parent_reference,
        "inherited_context": list(entity.inherited_context or []),
        "sibling_count": len(entity.sibling_facts_do_not_copy or []),
        "privacy": {
            "phone_redacted_before_property_ai": True,
            "email_redacted_before_property_ai": True,
            "contacts_in_output": False,
        },
        "provenance": {
            "own_text": "OWN_FACT",
            "inherited_context": "EXPLICIT_GLOBAL_INTENT_ONLY",
            "parent_context_reference_only": "REFERENCE_ONLY_NOT_AUTO_APPLIED",
            "sibling_facts": "DO_NOT_COPY",
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

    return [dict(row) for row in rows]


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
        "llm_used": 0,
        "privacy_redacted": 0,
    }

    bursts = []

    for row in rows:
        entities = reconstruct_entities(row.get("burst_text") or "")
        candidates = []

        for entity in entities:
            candidate = _extract_entity(
                entity,
                row["burst_group_id"],
            )

            counts["reconstructed_entity_count"] += 1
            counts["clean_candidates"] += int(candidate["quality"] == "CLEAN")
            counts["under_review"] += int(candidate["quality"] != "CLEAN")
            counts["availability"] += int(candidate["classification"] == "AVAILABILITY")
            counts["requirements"] += int(candidate["classification"] == "REQUIREMENT")
            counts["ambiguous_or_noise"] += int(
                candidate["classification"] in ("AMBIGUOUS", "NOISE")
            )
            counts["boundary_needs_split"] += int(candidate["boundary_needs_split"])
            counts["ambiguous_money_rejected"] += int(
                "AMBIGUOUS_RATE_NOT_TOTAL_PRICE" in candidate["review_reasons"]
            )
            counts["llm_used"] += int(
                bool(
                    (candidate.get("ai_understanding") or {}).get("llm_used")
                )
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
            "SHADOW ONLY. Validate field extraction after Phase 2.3.3 "
            "entity reconstruction. CLEAN means candidate-safe only; "
            "nothing is written to canonical tables."
        ),
        "bursts": bursts,
    }


def _orchid_demo():
    raw = """
APARTMENT AVL FOR SALE IN ORCHID GARDEN SUN CITY, GOLF COURSE ROAD,
(Duplex Penthouse)
4.br 3363 sqft asking Price 11 CR
3.br 2013 sq ft north facing asking rate 35000/ k. Per fut
FOR RENT DUPLEX PENTHOUSE
4.br 3363 sqft furnished rental asking 1.5L+maintenance
3.br + study + staff room 2400 sq ft renovated flat rent 1.25L
"""

    entities = reconstruct_entities(raw)

    return {
        "entity_count": len(entities),
        "candidates": [
            _extract_entity(entity, "demo")
            for entity in entities
        ],
    }


def register(core):
    app = core.app
    engine = core.engine

    status_route = "/api/v7/property-ai/shadow-extraction/status"

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
        return JSONResponse(
            {
                "status": "READY",
                "version": VERSION,
                "reconstructor_engine_version": RECONSTRUCTOR_ENGINE_VERSION,
                "mode": "READ_ONLY_SHADOW_EXTRACTION",
                "property_ai_engine": property_ai.VERSION,
                "privacy_redaction_before_property_ai": True,
                "ambiguous_rate_guard": True,
                "location_own_text_guard": True,
                "parent_context_auto_apply": False,
                "database_writes": False,
                "canonical_tables_modified": False,
                "orchestrator_modified_by_phase24": False,
                "matcher_modified": False,
                "whatsapp_live_modified": False,
                "raw_data_deleted": False,
            }
        )

    @app.get("/api/v7/property-ai/shadow-extraction/orchid-test")
    def orchid_test():
        return JSONResponse(_orchid_demo())

    @app.get("/api/v7/property-ai/shadow-extraction/preview")
    def preview(
        limit: int = Query(25, ge=1, le=100),
    ):
        return JSONResponse(
            _benchmark(
                engine,
                limit,
            )
        )

    app.state.alliance_property_shadow_extraction_v24_registered = True

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": status_route,
        "preview": "/api/v7/property-ai/shadow-extraction/preview?limit=25",
        "writes_enabled": False,
    }
