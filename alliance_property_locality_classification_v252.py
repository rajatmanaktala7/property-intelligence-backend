from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Query
from fastapi.responses import JSONResponse

import alliance_property_boundary_cohesion_v251 as v251
import alliance_property_shadow_extraction_v24 as v24

VERSION = "2.5.2-DETERMINISTIC-LOCALITY-CLASSIFICATION-RECOVERY"
MODE = "READ_ONLY_SHADOW_LOCALITY_CLASSIFICATION"

# Own-text locality patterns only. No sibling/property-specific inheritance.
LOCALITY_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)\bSHUSHANT\s*LOK\s*[-:]?\s*1\b|\bSUSHANT\s*LOK\s*[-:]?\s*1\b|\bSHUSHANTLOK1\b|\bSUSHANTLOK1\b"), "Sushant Lok 1"),
    (re.compile(r"(?i)\bDLF\s*PHASE\s*[-:]?\s*([1-5])\b|\bDLFPHASE([1-5])\b"), "__DLF_PHASE__"),
    (re.compile(r"(?i)\bGREATER\s+KAILASH\s*[-:]?\s*2\b|\bGK[-\s]*2\b"), "Greater Kailash 2"),
    (re.compile(r"(?i)\bGREATER\s+KAILASH\s*[-:]?\s*1\b|\bGK[-\s]*1\b"), "Greater Kailash 1"),
    (re.compile(r"(?i)\bDWARKA\s+SECTOR\s*[-:]?\s*(\d{1,2})\b|\bSECTOR\s*[-:]?\s*(\d{1,2})\s*,?\s*DWARKA\b"), "__DWARKA_SECTOR__"),
    (re.compile(r"(?i)\bKALKAJI\b"), "Kalkaji"),
    (re.compile(r"(?i)\bGULMOHAR\s+ROAD\b"), "Gulmohar Road"),
    (re.compile(r"(?i)\bJVPD\b"), "JVPD"),
    (re.compile(r"(?i)\bJUHU\b"), "Juhu"),
    (re.compile(r"(?i)\bBANDRA\s+WEST\b"), "Bandra West"),
    (re.compile(r"(?i)\bKHAR\s+WEST\b"), "Khar West"),
]

HARD_BLOCKERS = {
    "BOUNDARY_NEEDS_SPLIT",
    "AMBIGUOUS_RATE_NOT_TOTAL_PRICE",
    "IMPLAUSIBLE_SALE_TOTAL_REJECTED",
    "IMPLAUSIBLE_RENT_TOTAL_REJECTED",
    "PHONE_LIKE_OR_EXTREME_MONEY_REJECTED",
    "PHONE_LIKE_MONEY_REJECTED",
    "LOCATION_NOT_SUPPORTED_BY_OWN_TEXT",
    "MICRO_LOCATION_WITHOUT_PARENT_LOCALITY",
    "TRANSACTION_MISSING",
    "PROPERTY_FAMILY_MISSING",
    "LOCATION_MISSING",
    "PROPERTY_SPECIFIC_FACT_MISSING",
    "NOISE",
}

NON_BLOCKING_EVIDENCE_REASONS = {
    "OWN_CONFIGURATION_ACCEPTED_AS_PROPERTY_FACT",
    "OWN_TEXT_TOTAL_MONEY_RECOVERED",
    "ASSET_FAMILY_CORRECTED_FROM_INTENDED_USE",
    "LOCATION_RESOLVED_FROM_OWN_TEXT",
    "LOCATION_RECOVERED_FROM_OWN_TEXT_V25",
    "LOCATION_RECOVERED_FROM_OWN_TEXT_V252",
    "DETERMINISTIC_AVAILABILITY_PROMOTION_V252",
}

GENERIC_LANDMARK_RE = re.compile(
    r"(?i)^\s*(?:OPP\.?|OPPOSITE|NEAR|NEXT\s+TO|BEHIND|ADJOINING|"
    r"FACING|CLOSE\s+TO)\b"
)

IDENTITY_RE = re.compile(
    r"(?i)(?:"
    r"\b\d(?:\.\d+)?\s*BHK\b|"
    r"\b(?:VILLA|BUNGALOW|KOTHI|APARTMENT|FLAT|BUILDER\s+FLOOR|"
    r"OFFICE|SHOWROOM|SHOP|WAREHOUSE|GODOWN|PLOT|LAND|FARMHOUSE|"
    r"HOTEL|BANQUET|RESTAURANT|CAFE|CLUB|LOUNGE|GUEST\s*HOUSE)\b|"
    r"\b(?:BLOCK|SECTOR|PHASE)\s*[A-Z0-9-]+\b"
    r")"
)

PROPERTY_FACT_RE = re.compile(
    r"(?i)(?:"
    r"\b\d[\d,]*(?:\.\d+)?\s*(?:SQFT|SQ\.?\s*FT|SFT|SYDS?|YARDS?|GAJ|"
    r"SQMT|SQM|ACRES?|CARPET)\b|"
    r"\b\d(?:\.\d+)?\s*BHK\b|"
    r"(?:₹|RS\.?|INR)?\s*\d+(?:\.\d+)?\s*(?:CR|CRORE|CRORES|LAC|LACS|LAKH|LAKHS|L|K)(?=\s|$|\+|/|-)"
    r")"
)


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _own_locality(text_value: str) -> Optional[str]:
    s = _norm(text_value)
    if not s or GENERIC_LANDMARK_RE.search(s):
        # A landmark-only fragment must not become a locality by itself.
        # Named locality later in the same string is still handled below.
        pass

    for pattern, label in LOCALITY_PATTERNS:
        m = pattern.search(s)
        if not m:
            continue

        if label == "__DLF_PHASE__":
            phase = m.group(1) or m.group(2)
            return f"DLF Phase {phase}"

        if label == "__DWARKA_SECTOR__":
            sector = m.group(1) or m.group(2)
            return f"Sector {sector}, Dwarka"

        return label

    # Reuse V24 broad-locality resolver only when it is supported by own text.
    try:
        locality = v24._broad_locality_from_text(s)
    except Exception:
        locality = None

    if locality and _norm(locality).lower() in s.lower():
        return locality

    return None


def _has_own_identity(row: Dict[str, Any]) -> bool:
    own = _norm(row.get("own_text_redacted"))
    return bool(IDENTITY_RE.search(own))


def _has_own_property_fact(row: Dict[str, Any]) -> bool:
    own = _norm(row.get("own_text_redacted"))
    return bool(PROPERTY_FACT_RE.search(own))


def _clean_reasons(row: Dict[str, Any]) -> List[str]:
    return list(dict.fromkeys(row.get("review_reasons") or []))


def _recover_locality(row: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    out = deepcopy(row)
    if out.get("location"):
        return out, False

    own = _norm(out.get("own_text_redacted"))
    locality = _own_locality(own)
    if not locality:
        return out, False

    out["location"] = locality
    out["display_location"] = locality

    hierarchy = dict(out.get("location_hierarchy") or {})
    hierarchy["locality"] = locality
    hierarchy["display_location"] = locality
    hierarchy["source"] = "OWN_TEXT_DETERMINISTIC_V252"
    hierarchy["confidence"] = 0.98
    out["location_hierarchy"] = hierarchy

    reasons = [
        r for r in _clean_reasons(out)
        if r not in ("LOCATION_MISSING", "MICRO_LOCATION_WITHOUT_PARENT_LOCALITY")
    ]
    reasons.append("LOCATION_RECOVERED_FROM_OWN_TEXT_V252")
    out["review_reasons"] = list(dict.fromkeys(reasons))

    prov = dict(out.get("provenance") or {})
    prov["v252_location"] = {
        "source": "OWN_TEXT_ONLY",
        "value": locality,
        "confidence": 0.98,
        "sibling_used": False,
        "parent_property_specific_fact_used": False,
    }
    out["provenance"] = prov
    return out, True


def _eligible_for_promotion(row: Dict[str, Any]) -> bool:
    if row.get("classification") != "AMBIGUOUS":
        return False
    if row.get("transaction") not in ("SALE", "RENT"):
        return False
    if row.get("property_family") not in ("RESIDENTIAL", "COMMERCIAL", "LAND"):
        return False
    if not row.get("location"):
        return False
    if row.get("boundary_needs_split"):
        return False
    if not _has_own_identity(row):
        return False
    if not _has_own_property_fact(row):
        return False

    reasons = set(_clean_reasons(row))
    if reasons.intersection(HARD_BLOCKERS):
        return False

    return True


def _promote(row: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    out = deepcopy(row)
    if not _eligible_for_promotion(out):
        return out, False

    out["classification"] = "AVAILABILITY"
    reasons = [r for r in _clean_reasons(out) if r != "CLASSIFICATION_AMBIGUOUS"]
    reasons.append("DETERMINISTIC_AVAILABILITY_PROMOTION_V252")
    out["review_reasons"] = list(dict.fromkeys(reasons))

    effective = set(out["review_reasons"])
    effective -= NON_BLOCKING_EVIDENCE_REASONS
    out["quality"] = "CLEAN" if not effective.intersection(HARD_BLOCKERS) else "UNDER_REVIEW"

    prov = dict(out.get("provenance") or {})
    prov["v252_classification"] = {
        "from": "AMBIGUOUS",
        "to": "AVAILABILITY",
        "method": "DETERMINISTIC_SEMANTIC_GATE",
        "transaction_present": True,
        "family_present": True,
        "locality_present": True,
        "own_identity_anchor": True,
        "own_property_fact": True,
        "dangerous_blocker_present": False,
        "llm_used": False,
    }
    out["provenance"] = prov
    return out, True


def enhance_candidate(candidate: Dict[str, Any]) -> Dict[str, Any]:
    row, location_recovered = _recover_locality(candidate)
    row, promoted = _promote(row)

    row["v252"] = {
        "location_recovered": location_recovered,
        "classification_promoted": promoted,
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
    base = v251._benchmark(engine, limit)

    bursts = []
    before_rows: List[Dict[str, Any]] = []
    after_rows: List[Dict[str, Any]] = []

    for burst in base.get("bursts") or []:
        out_candidates = []
        for candidate in burst.get("candidates") or []:
            before_rows.append(candidate)
            enhanced = enhance_candidate(candidate)
            after_rows.append(enhanced)
            out_candidates.append(enhanced)

        bursts.append({
            "burst_group_id": burst.get("burst_group_id"),
            "source_type": burst.get("source_type"),
            "source_group": burst.get("source_group"),
            "v251_entity_count": burst.get("v251_entity_count"),
            "v252_entity_count": len(out_candidates),
            "candidates": out_candidates,
        })

    total = len(after_rows)
    clean_before = sum(1 for x in before_rows if x.get("quality") == "CLEAN")
    clean_after = sum(1 for x in after_rows if x.get("quality") == "CLEAN")

    counts = {
        "burst_sample_size": len(bursts),
        "entity_count": total,
        "v251_clean": clean_before,
        "v252_clean": clean_after,
        "true_newly_recovered_clean": sum(
            1 for b, a in zip(before_rows, after_rows)
            if b.get("quality") != "CLEAN" and a.get("quality") == "CLEAN"
        ),
        "v252_under_review": total - clean_after,
        "v252_clean_rate": round(clean_after / total, 4) if total else 0.0,
        "location_missing_before": sum(
            1 for x in before_rows if "LOCATION_MISSING" in (x.get("review_reasons") or [])
        ),
        "location_missing_after": sum(
            1 for x in after_rows if "LOCATION_MISSING" in (x.get("review_reasons") or [])
        ),
        "ambiguous_before": sum(
            1 for x in before_rows if x.get("classification") == "AMBIGUOUS"
        ),
        "ambiguous_after": sum(
            1 for x in after_rows if x.get("classification") == "AMBIGUOUS"
        ),
        "location_recoveries_v252": sum(
            1 for x in after_rows if (x.get("v252") or {}).get("location_recovered")
        ),
        "classification_promotions_v252": sum(
            1 for x in after_rows if (x.get("v252") or {}).get("classification_promoted")
        ),
        "boundary_needs_split": sum(
            1 for x in after_rows if x.get("boundary_needs_split")
        ),
        "transaction_missing": sum(
            1 for x in after_rows if "TRANSACTION_MISSING" in (x.get("review_reasons") or [])
        ),
        "llm_used": sum(
            1 for x in after_rows if bool((x.get("ai_understanding") or {}).get("llm_used"))
        ),
        "privacy_redacted": total,
    }

    return {
        "status": "READY",
        "version": VERSION,
        "mode": MODE,
        "base_v251_version": v251.VERSION,
        "counts": counts,
        "under_review_reasons_v252": _reason_counts(after_rows),
        "safety_contract": {
            "read_only_shadow": True,
            "database_writes": False,
            "canonical_tables_modified": False,
            "orchestrator_modified": False,
            "live_reconstructor_replaced": False,
            "matcher_modified": False,
            "whatsapp_live_modified": False,
            "raw_data_deleted": False,
            "location_source_own_text_only": True,
            "classification_deterministic_only": True,
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

    loc = {
        "classification": "AMBIGUOUS",
        "transaction": "RENT",
        "property_family": "RESIDENTIAL",
        "location": None,
        "own_text_redacted": "SHUSHANTLOK1 300 SYDS 4BHK+ SER 1.10 LAC+MAINT FULLY FURNISHED",
        "review_reasons": ["CLASSIFICATION_AMBIGUOUS", "LOCATION_MISSING"],
        "quality": "UNDER_REVIEW",
        "boundary_needs_split": False,
        "provenance": {},
    }
    loc_out = enhance_candidate(loc)
    tests.append({
        "name": "own_text_locality_plus_promotion",
        "location": loc_out.get("location"),
        "classification": loc_out.get("classification"),
        "quality": loc_out.get("quality"),
    })

    rate = {
        "classification": "AMBIGUOUS",
        "transaction": "SALE",
        "property_family": "LAND",
        "location": "Juhu",
        "own_text_redacted": "JUHU LAND 1000 SQFT 55000 PSF",
        "review_reasons": ["CLASSIFICATION_AMBIGUOUS", "AMBIGUOUS_RATE_NOT_TOTAL_PRICE"],
        "quality": "UNDER_REVIEW",
        "boundary_needs_split": False,
        "provenance": {},
    }
    rate_out = enhance_candidate(rate)
    tests.append({
        "name": "rate_only_not_promoted",
        "classification": rate_out.get("classification"),
        "quality": rate_out.get("quality"),
    })

    landmark = {
        "classification": "AMBIGUOUS",
        "transaction": "SALE",
        "property_family": "RESIDENTIAL",
        "location": None,
        "own_text_redacted": "Opp. Ekol Mondel School 1100 Usable Carpet 2 BHK OUTRIGHT 5.50 Cr",
        "review_reasons": ["CLASSIFICATION_AMBIGUOUS", "LOCATION_MISSING"],
        "quality": "UNDER_REVIEW",
        "boundary_needs_split": False,
        "provenance": {},
    }
    landmark_out = enhance_candidate(landmark)
    tests.append({
        "name": "landmark_not_hallucinated_as_locality",
        "location": landmark_out.get("location"),
        "classification": landmark_out.get("classification"),
    })

    requirement = {
        "classification": "REQUIREMENT",
        "transaction": "RENT",
        "property_family": "RESIDENTIAL",
        "location": None,
        "own_text_redacted": "Need 3 BHK in Juhu or Bandra West on rent",
        "review_reasons": [],
        "quality": "CLEAN",
        "boundary_needs_split": False,
        "provenance": {},
    }
    requirement_out = enhance_candidate(requirement)
    tests.append({
        "name": "requirement_never_promoted",
        "classification": requirement_out.get("classification"),
    })

    passed = (
        loc_out.get("location") == "Sushant Lok 1"
        and loc_out.get("classification") == "AVAILABILITY"
        and loc_out.get("quality") == "CLEAN"
        and rate_out.get("classification") == "AMBIGUOUS"
        and rate_out.get("quality") == "UNDER_REVIEW"
        and landmark_out.get("location") is None
        and landmark_out.get("classification") == "AMBIGUOUS"
        and requirement_out.get("classification") == "REQUIREMENT"
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
    status_route = "/api/v7/property-ai/locality-classification-v252/status"

    if any(getattr(r, "path", None) == status_route for r in app.router.routes):
        return {"status": "ALREADY_REGISTERED", "version": VERSION, "route": status_route}

    @app.get(status_route)
    def status():
        return JSONResponse({
            "status": "READY",
            "version": VERSION,
            "mode": MODE,
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
        })

    @app.get("/api/v7/property-ai/locality-classification-v252/regression-test")
    def regression_test():
        return JSONResponse(_regression_demo())

    @app.get("/api/v7/property-ai/locality-classification-v252/preview")
    def preview(limit: int = Query(25, ge=1, le=100)):
        return JSONResponse(_benchmark(engine, limit))

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": status_route,
        "regression": "/api/v7/property-ai/locality-classification-v252/regression-test",
        "preview": "/api/v7/property-ai/locality-classification-v252/preview?limit=25",
        "writes_enabled": False,
    }

