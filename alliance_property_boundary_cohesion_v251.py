from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional

from fastapi import Query
from fastapi.responses import JSONResponse

import alliance_property_boundary_intelligence_v25 as v25
import alliance_property_shadow_extraction_v24 as v24
import alliance_property_benchmark_stabilizer_v245a as stabilizer
from property_brain.stages.s3_entity_segmentation_v23 import EntityBlock

VERSION = "2.5.1-BOUNDARY-COHESION-FIX"
MODE = "READ_ONLY_SHADOW_BOUNDARY_COHESION"

# Strong physical-property starts. These are OWN-TEXT anchors only.
DLF_START_RE = re.compile(r"(?i)\bDLF\s*PHASE\s*[-:]?\s*[1-5]\b|\bDLFPHASE[1-5]\b")
SUSHANT_START_RE = re.compile(
    r"(?i)\bS?H?USHANT\s*LOK\s*[-:]?\s*1\b|\bSHUSHANTLOK1\b|\bSUSHANTLOK1\b"
)
KNOWN_LOCALITY_START_RE = re.compile(
    r"(?i)\b(?:JUHU|JVPD|GULMOHAR\s+ROAD|BANDRA\s+WEST|KHAR\s+WEST|"
    r"DWARKA(?:\s+SECTOR\s*\d+)?|KALKAJI|GREATER\s+KAILASH|GK[-\s]*[12])\b"
)
NUMBERED_START_RE = re.compile(r"^\s*\d{1,2}[.)]\s*")

# Project/building-like names, but only if followed by a property fact.
PROJECT_START_RE = re.compile(
    r"(?i)^\s*(?:✨|â¨|🔹|🔸|•|▪)?\s*"
    r"[A-Z][A-Z0-9 &'./-]{2,45}"
    r"(?=\s+(?:âªï¸\s*)?(?:\d(?:\.\d+)?\s*BHK|"
    r"\d[\d,]*(?:\.\d+)?\s*(?:SQFT|SQ\.?\s*FT|CARPET|SYDS?|YARDS?)|"
    r"BUNGALOW|VILLA|FLAT|APARTMENT|OFFICE|SHOP|SHOWROOM))"
)

CONTINUATION_FACT_RE = re.compile(
    r"(?i)(?:"
    r"\b\d(?:\.\d+)?\s*BHK\b|"
    r"\b\d[\d,]*(?:\.\d+)?\s*(?:SQFT|SQ\.?\s*FT|CARPET|SYDS?|YARDS?|GAJ)\b|"
    r"\b(?:FURNISHED|SEMI[-\s]*FURNISHED|UNFURNISHED|BARE\s*SHELL)\b|"
    r"\b(?:MAINT|MAINTENANCE|CAR\s+PARKING|PARKING)\b|"
    r"\b(?:RENT|OUTRIGHT|SALE|LEASE)\b|"
    r"(?:₹|RS\.?|INR)?\s*\d+(?:\.\d+)?\s*(?:CR|L|LAC|LAKH|K)\b"
    r")"
)

CONTACT_OR_FOOTER_RE = re.compile(
    r"(?i)^\s*(?:FOR\s+SITE\s+VISITS|CONTACT|CALL|WHATSAPP|BROKER|CONSULTANT|"
    r"PANASA\s+ESTATE|PREMIUM\s+PROPERTIES)\b"
)

HARD_SECTION_RE = re.compile(
    r"(?i)^\s*(?:PREMIUM\s+)?(?:"
    r"RENTAL\s+PROPERTIES|RENTALS|PREMIUM\s+BUNGALOWS|"
    r"\d(?:/\d)?\s*BHK\s+OUTRIGHT|OUTRIGHT\s+PROPERTIES|"
    r"FOR\s+SALE|FOR\s+RENT|COMMERCIAL)\b"
)


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _strong_start(text_value: str) -> bool:
    s = _norm(text_value)
    if not s:
        return False
    if NUMBERED_START_RE.search(s):
        return True
    if DLF_START_RE.search(s) or SUSHANT_START_RE.search(s):
        return True
    if KNOWN_LOCALITY_START_RE.search(s):
        return True
    if PROJECT_START_RE.search(s):
        return True
    return False


def _is_continuation(text_value: str) -> bool:
    s = _norm(text_value)
    if not s:
        return False
    if CONTACT_OR_FOOTER_RE.search(s):
        return False
    if HARD_SECTION_RE.search(s):
        return False
    return bool(CONTINUATION_FACT_RE.search(s))


def _safe_join(parts: List[str]) -> str:
    return _norm(" | ".join(_norm(x) for x in parts if _norm(x)))


def _flush(
    entities: List[EntityBlock],
    pending: List[str],
    context: Dict[str, Optional[str]],
) -> None:
    if not pending:
        return

    text_value = _safe_join(pending)
    if not v25._is_entity_candidate(text_value):
        pending.clear()
        return

    inherited: List[str] = []
    if context.get("transaction"):
        inherited.append(context["transaction"])

    parent_refs: List[str] = []
    if context.get("property_family"):
        parent_refs.append(context["property_family"])

    entities.append(
        EntityBlock(
            index=len(entities) + 1,
            own_text=text_value,
            inherited_context=inherited,
            sibling_facts_do_not_copy=[],
            parent_context_reference_only=parent_refs,
            method="v251_cohesive_boundary",
            needs_split=False,
            reason=None,
        )
    )
    pending.clear()


def reconstruct_entities_v251(text_value: str) -> List[EntityBlock]:
    """
    Cohesion-first reconstruction.

    A property starts on a strong OWN-TEXT anchor. Subsequent property-fact
    fragments remain with it until:
      1) another strong property start,
      2) a transaction/family section heading,
      3) a broker/contact/footer boundary.

    No sibling price/area/config/floor/micro-location is inherited.
    """
    pieces = v25._presegment(text_value)
    entities: List[EntityBlock] = []
    context: Dict[str, Optional[str]] = {
        "transaction": None,
        "property_family": None,
    }
    pending: List[str] = []

    for piece in pieces:
        p = _norm(piece)
        if not p:
            continue

        if v25._looks_like_section(p) or HARD_SECTION_RE.search(p):
            _flush(entities, pending, context)
            context = v25._section_context(p, context)
            continue

        if CONTACT_OR_FOOTER_RE.search(p):
            _flush(entities, pending, context)
            continue

        # Dense pieces may contain multiple proven project anchors.
        dense = v25._split_dense_piece(p)

        if len(dense) >= 2:
            _flush(entities, pending, context)
            for chunk in dense:
                if v25._is_entity_candidate(chunk):
                    pending[:] = [chunk]
                    _flush(entities, pending, context)
            continue

        chunk = dense[0] if dense else p

        if _strong_start(chunk):
            if pending:
                _flush(entities, pending, context)
            pending.append(chunk)
            continue

        if pending and _is_continuation(chunk):
            pending.append(chunk)
            continue

        if pending:
            # Weak non-property text ends the current entity rather than being copied.
            _flush(entities, pending, context)

        # Standalone atomic property that lacks our known start dictionaries.
        if v25._is_entity_candidate(chunk):
            pending.append(chunk)
            # Keep open briefly for possible continuation on the next fragment.
            continue

        # Non-entity text may still be a safe context header.
        context = v25._section_context(chunk, context)

    _flush(entities, pending, context)
    return entities


def _extract_entity(entity: EntityBlock, burst_group_id: str) -> Dict[str, Any]:
    candidate = v24._extract_entity(entity, burst_group_id, shared_defaults={})
    row = v25._enrich_own_text(candidate)

    provenance = dict(row.get("provenance") or {})
    provenance["v251"] = {
        "boundary": "COHESIVE_OWN_TEXT_ENTITY",
        "continuation_fragments_joined": True,
        "strong_new_property_anchor_required_to_split": True,
        "section_transaction_context_only": True,
        "price_inherited": False,
        "area_inherited": False,
        "configuration_inherited": False,
        "floor_inherited": False,
        "micro_location_inherited": False,
        "sibling_property_specific_facts_used": False,
    }
    row["provenance"] = provenance
    return row


def _reason_counts(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    counter: Counter[str] = Counter()
    for row in rows:
        if row.get("quality") != "CLEAN":
            counter.update(row.get("review_reasons") or [])
    return dict(counter.most_common())


def _benchmark(engine, limit: int) -> Dict[str, Any]:
    db_rows = v24._load_bursts(engine, limit)

    base25 = v25._benchmark(engine, limit)
    base25_candidates = [
        c
        for b in (base25.get("bursts") or [])
        for c in (b.get("candidates") or [])
    ]

    bursts = []
    candidates: List[Dict[str, Any]] = []

    for row in db_rows:
        entities = reconstruct_entities_v251(row.get("burst_text") or "")
        out = []

        for entity in entities:
            c = _extract_entity(entity, row["burst_group_id"])
            candidates.append(c)
            out.append(c)

        bursts.append({
            "burst_group_id": row["burst_group_id"],
            "source_type": row.get("source_type"),
            "source_group": row.get("source_group"),
            "v25_entity_count": next(
                (
                    b.get("v25_entity_count")
                    for b in (base25.get("bursts") or [])
                    if b.get("burst_group_id") == row["burst_group_id"]
                ),
                None,
            ),
            "v251_entity_count": len(entities),
            "candidates": out,
        })

    total = len(candidates)
    clean = sum(1 for x in candidates if x.get("quality") == "CLEAN")

    counts = {
        "burst_sample_size": len(db_rows),
        "v25_entity_count": len(base25_candidates),
        "v251_entity_count": total,
        "v25_clean": sum(
            1 for x in base25_candidates if x.get("quality") == "CLEAN"
        ),
        "v251_clean": clean,
        "v251_under_review": total - clean,
        "v251_clean_rate": round(clean / total, 4) if total else 0.0,
        "v251_boundary_needs_split": sum(
            1 for x in candidates if x.get("boundary_needs_split")
        ),
        "v251_availability": sum(
            1 for x in candidates if x.get("classification") == "AVAILABILITY"
        ),
        "v251_requirements": sum(
            1 for x in candidates if x.get("classification") == "REQUIREMENT"
        ),
        "v251_ambiguous_or_noise": sum(
            1 for x in candidates if x.get("classification") in ("AMBIGUOUS", "NOISE")
        ),
        "own_text_transaction_recoveries": sum(
            1
            for x in candidates
            if (x.get("own_text_intelligence_v25") or {}).get(
                "transaction_recovered"
            )
        ),
        "own_text_location_recoveries": sum(
            1
            for x in candidates
            if (x.get("own_text_intelligence_v25") or {}).get(
                "location_recovered"
            )
        ),
        "llm_used": sum(
            1
            for x in candidates
            if bool((x.get("ai_understanding") or {}).get("llm_used"))
        ),
        "privacy_redacted": total,
    }

    return {
        "status": "READY",
        "version": VERSION,
        "mode": MODE,
        "base_v25_version": v25.VERSION,
        "counts": counts,
        "under_review_reasons_v251": _reason_counts(candidates),
        "safety_contract": {
            "read_only_shadow": True,
            "database_writes": False,
            "canonical_tables_modified": False,
            "orchestrator_modified": False,
            "live_reconstructor_replaced": False,
            "matcher_modified": False,
            "whatsapp_live_modified": False,
            "raw_data_deleted": False,
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

    fragmented = (
        "PREMIUM RENTAL PROPERTIES\n"
        "SHUSHANTLOK1\n"
        "215 SYDS\n"
        "FULLY FURNISHED\n"
        "3BHK+SER\n"
        "RENT 90K+MAINT\n"
        "DLF PHASE 4\n"
        "360 SYDS\n"
        "4BHK+SER\n"
        "1.40 LAC+MAINT"
    )
    entities = reconstruct_entities_v251(fragmented)
    tests.append({
        "name": "fragmented_rental_cohesion",
        "entity_count": len(entities),
        "texts": [e.own_text for e in entities],
        "transactions": [e.inherited_context for e in entities],
    })

    mixed = (
        "4/5 BHK OUTRIGHT\n"
        "GULMOHAR ROAD 4 BHK 1750 Carpet 10.50 Cr\n"
        "JVPD 4 BHK 1950 Carpet 14.50 Cr\n"
        "PREMIUM RENTAL PROPERTIES\n"
        "DLH LEGACY 3 BHK 1250 Sq.ft Semi-Furnished Rent 3.00 Lakhs\n"
        "PARK GRANDEUR 3 BHK 1300 Sq.ft Semi-Furnished Rent 2.50 Lakhs"
    )
    mixed_entities = reconstruct_entities_v251(mixed)
    tests.append({
        "name": "mixed_properties_stay_separate",
        "entity_count": len(mixed_entities),
        "texts": [e.own_text for e in mixed_entities],
        "transactions": [e.inherited_context for e in mixed_entities],
    })

    passed = (
        len(entities) == 2
        and "SHUSHANTLOK1" in entities[0].own_text
        and "215 SYDS" in entities[0].own_text
        and "RENT 90K+MAINT" in entities[0].own_text
        and "DLF PHASE 4" in entities[1].own_text
        and "360 SYDS" in entities[1].own_text
        and "1.40 LAC+MAINT" in entities[1].own_text
        and all("RENT" in e.inherited_context for e in entities)
        and len(mixed_entities) >= 4
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
    status_route = "/api/v7/property-ai/boundary-cohesion-v251/status"

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
            "base_v25_version": v25.VERSION,
            "read_only_shadow": True,
            "database_writes": False,
            "canonical_tables_modified": False,
            "orchestrator_modified": False,
            "live_reconstructor_replaced": False,
            "matcher_modified": False,
            "whatsapp_live_modified": False,
            "raw_data_deleted": False,
        })

    @app.get("/api/v7/property-ai/boundary-cohesion-v251/regression-test")
    def regression_test():
        return JSONResponse(_regression_demo())

    @app.get("/api/v7/property-ai/boundary-cohesion-v251/preview")
    def preview(limit: int = Query(25, ge=1, le=100)):
        return JSONResponse(_benchmark(engine, limit))

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": status_route,
        "regression": "/api/v7/property-ai/boundary-cohesion-v251/regression-test",
        "preview": "/api/v7/property-ai/boundary-cohesion-v251/preview?limit=25",
        "writes_enabled": False,
    }

