from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from fastapi import Query
from fastapi.responses import JSONResponse

import alliance_property_locality_classification_v252 as v252
import alliance_property_boundary_cohesion_v251 as v251
import alliance_property_shadow_extraction_v24 as v24

VERSION = "2.5.3-DETERMINISTIC-LOCATION-EVIDENCE-RESOLVER"
MODE = "READ_ONLY_SHADOW_LOCATION_EVIDENCE"

# Broad locality aliases only. These are accepted only when literally evidenced
# in OWN text or, for burst context, when exactly one broad locality is present
# across the complete burst.
ALIAS_GROUPS: Dict[str, Tuple[str, ...]] = {
    "Sushant Lok 1": (
        r"\bSUSHANT\s*LOK\s*[-:]?\s*1\b",
        r"\bSHUSHANT\s*LOK\s*[-:]?\s*1\b",
        r"\bSUSHANTLOK1\b",
        r"\bSHUSHANTLOK1\b",
    ),
    "DLF Phase 1": (r"\bDLF\s*PHASE\s*[-:]?\s*1\b", r"\bDLFPHASE1\b"),
    "DLF Phase 2": (r"\bDLF\s*PHASE\s*[-:]?\s*2\b", r"\bDLFPHASE2\b"),
    "DLF Phase 3": (r"\bDLF\s*PHASE\s*[-:]?\s*3\b", r"\bDLFPHASE3\b"),
    "DLF Phase 4": (r"\bDLF\s*PHASE\s*[-:]?\s*4\b", r"\bDLFPHASE4\b"),
    "DLF Phase 5": (r"\bDLF\s*PHASE\s*[-:]?\s*5\b", r"\bDLFPHASE5\b"),
    "Kalkaji": (r"\bKALKAJI\b",),
    "Greater Kailash 1": (r"\bGREATER\s+KAILASH\s*[-:]?\s*1\b", r"\bGK[-\s]*1\b"),
    "Greater Kailash 2": (r"\bGREATER\s+KAILASH\s*[-:]?\s*2\b", r"\bGK[-\s]*2\b"),
    "Saket": (r"\bSAKET\b",),
    "Hauz Khas": (r"\bHAUZ\s+KHAS\b",),
    "Defence Colony": (r"\bDEFEN[CS]E\s+COLONY\b",),
    "Lajpat Nagar": (r"\bLAJPAT\s+NAGAR\b",),
    "South Extension": (r"\bSOUTH\s+EXTENSION\b", r"\bSOUTH\s+EXTN\b"),
    "Vasant Kunj": (r"\bVASANT\s+KUNJ\b",),
    "Vasant Vihar": (r"\bVASANT\s+VIHAR\b",),
    "Nehru Place": (r"\bNEHRU\s+PLACE\b",),
    "Okhla": (r"\bOKHLA\b",),
    "Jasola": (r"\bJASOLA\b",),
    "Noida": (r"\bNOIDA\b",),
    "Greater Noida": (r"\bGREATER\s+NOIDA\b",),
    "Gurgaon": (r"\bGURGAON\b", r"\bGURUGRAM\b"),
    "Golf Course Road": (r"\bGOLF\s+COURSE\s+ROAD\b",),
    "Golf Course Extension Road": (r"\bGOLF\s+COURSE\s+EXT(?:ENSION)?\s+ROAD\b",),
    "Sohna Road": (r"\bSOHNA\s+ROAD\b",),
    "MG Road Gurgaon": (r"\bMG\s+ROAD\b",),
    "Juhu": (r"\bJUHU\b",),
    "JVPD": (r"\bJVPD\b",),
    "Gulmohar Road": (r"\bGULMOHAR\s+ROAD\b",),
    "Bandra West": (r"\bBANDRA\s+WEST\b",),
    "Khar West": (r"\bKHAR\s+WEST\b",),
    "Santacruz West": (r"\bSANTA\s*CRUZ\s+WEST\b", r"\bSANTACRUZ\s+WEST\b"),
    "Andheri West": (r"\bANDHERI\s+WEST\b",),
    "Vile Parle West": (r"\bVILE\s+PARLE\s+WEST\b",),
    "Lokhandwala": (r"\bLOKHANDWALA\b",),
    "Siolim": (r"\bSIOLIM\b",),
    "Assagao": (r"\bASSAGAO\b",),
    "Vagator": (r"\bVAGATOR\b",),
    "Anjuna": (r"\bANJUNA\b",),
}

COMPILED_ALIASES: List[Tuple[str, re.Pattern]] = []
for canonical, patterns in ALIAS_GROUPS.items():
    for p in patterns:
        COMPILED_ALIASES.append((canonical, re.compile(p, re.I)))

DWARKA_SECTOR_RE = re.compile(
    r"(?i)(?:\bDWARKA\s+SECTOR\s*[-:]?\s*(\d{1,2})\b|"
    r"\bSECTOR\s*[-:]?\s*(\d{1,2})\s*,?\s*DWARKA\b)"
)

DELHI_BLOCK_WITH_LOCALITY_RE = re.compile(
    r"(?i)\b([A-Z])\s*BLOCK\s*,?\s*(KALKAJI|SAKET|GREATER\s+KAILASH\s*[12]?|"
    r"LAJPAT\s+NAGAR|VASANT\s+KUNJ|VASANT\s+VIHAR)\b"
)

PROPERTY_SPECIFIC_FACT_RE = re.compile(
    r"(?i)(?:"
    r"\b\d(?:\.\d+)?\s*BHK\b|"
    r"\b\d[\d,]*(?:\.\d+)?\s*(?:SQFT|SQ\.?\s*FT|SFT|SYDS?|YARDS?|GAJ|SQMT|SQM|ACRES?|CARPET)\b|"
    r"(?:₹|RS\.?|INR)?\s*\d+(?:\.\d+)?\s*(?:CR|CRORE|CRORES|LAC|LACS|LAKH|LAKHS|L|K)(?=\s|$|\+|/|-)|"
    r"\b(?:FLOOR|FURNISHED|SEMI[-\s]*FURNISHED|UNFURNISHED|BARE\s*SHELL)\b"
    r")"
)

HARD_LOCATION_BLOCKERS = {
    "LOCATION_NOT_SUPPORTED_BY_OWN_TEXT",
    "MICRO_LOCATION_WITHOUT_PARENT_LOCALITY",
    "BOUNDARY_NEEDS_SPLIT",
}

NON_BLOCKING_EVIDENCE_REASONS = {
    "OWN_CONFIGURATION_ACCEPTED_AS_PROPERTY_FACT",
    "OWN_TEXT_TOTAL_MONEY_RECOVERED",
    "ASSET_FAMILY_CORRECTED_FROM_INTENDED_USE",
    "LOCATION_RESOLVED_FROM_OWN_TEXT",
    "LOCATION_RECOVERED_FROM_OWN_TEXT_V25",
    "LOCATION_RECOVERED_FROM_OWN_TEXT_V252",
    "LOCATION_RECOVERED_FROM_OWN_TEXT_V253",
    "LOCATION_RECOVERED_FROM_UNIQUE_BURST_CONTEXT_V253",
    "DETERMINISTIC_AVAILABILITY_PROMOTION_V252",
    "DETERMINISTIC_AVAILABILITY_PROMOTION_V253",
}

PROMOTION_BLOCKERS = {
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


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _extract_explicit_locations(text_value: str) -> List[str]:
    s = _norm(text_value)
    if not s:
        return []

    found: List[str] = []

    m = DWARKA_SECTOR_RE.search(s)
    if m:
        sector = m.group(1) or m.group(2)
        found.append(f"Sector {sector}, Dwarka")

    for canonical, pattern in COMPILED_ALIASES:
        if pattern.search(s):
            found.append(canonical)

    # Prefer the more specific locality when generic Gurgaon co-occurs with a
    # named Gurgaon micromarket.
    specific_gurgaon = {
        "DLF Phase 1", "DLF Phase 2", "DLF Phase 3", "DLF Phase 4", "DLF Phase 5",
        "Sushant Lok 1", "Golf Course Road", "Golf Course Extension Road",
        "Sohna Road", "MG Road Gurgaon",
    }
    if any(x in found for x in specific_gurgaon):
        found = [x for x in found if x != "Gurgaon"]

    # Prefer specific Mumbai sublocality over generic Juhu only when both were
    # explicitly detected from the same text.
    return list(dict.fromkeys(found))


def _own_location(text_value: str) -> Optional[str]:
    found = _extract_explicit_locations(text_value)
    return found[0] if len(found) == 1 else None


def _unique_burst_location(burst_text: str) -> Optional[str]:
    found = _extract_explicit_locations(burst_text)
    unique: Set[str] = set(found)
    return next(iter(unique)) if len(unique) == 1 else None


def _safe_for_burst_context(row: Dict[str, Any]) -> bool:
    if row.get("classification") == "REQUIREMENT":
        return False
    if row.get("location"):
        return False
    if row.get("boundary_needs_split"):
        return False
    reasons = set(row.get("review_reasons") or [])
    if reasons.intersection(HARD_LOCATION_BLOCKERS):
        # LOCATION_NOT_SUPPORTED_BY_OWN_TEXT and MICRO_LOCATION_WITHOUT_PARENT
        # require manual review rather than contextual rescue.
        return False
    return True


def _set_location(
    row: Dict[str, Any],
    locality: str,
    *,
    source: str,
    confidence: float,
) -> Dict[str, Any]:
    out = deepcopy(row)
    out["location"] = locality
    out["display_location"] = locality

    hierarchy = dict(out.get("location_hierarchy") or {})
    hierarchy["locality"] = locality
    hierarchy["display_location"] = locality
    hierarchy["source"] = source
    hierarchy["confidence"] = confidence
    out["location_hierarchy"] = hierarchy

    reasons = [
        r for r in (out.get("review_reasons") or [])
        if r not in ("LOCATION_MISSING", "MICRO_LOCATION_WITHOUT_PARENT_LOCALITY")
    ]
    reasons.append(
        "LOCATION_RECOVERED_FROM_OWN_TEXT_V253"
        if source == "OWN_TEXT_EXPLICIT_ALIAS_V253"
        else "LOCATION_RECOVERED_FROM_UNIQUE_BURST_CONTEXT_V253"
    )
    out["review_reasons"] = list(dict.fromkeys(reasons))

    prov = dict(out.get("provenance") or {})
    prov["v253_location"] = {
        "source": source,
        "value": locality,
        "confidence": confidence,
        "sibling_property_specific_fact_used": False,
        "price_inherited": False,
        "area_inherited": False,
        "configuration_inherited": False,
        "micro_location_inherited": False,
    }
    out["provenance"] = prov
    return out


def _has_identity(row: Dict[str, Any]) -> bool:
    own = _norm(row.get("own_text_redacted"))
    try:
        return v252._has_own_identity(row)
    except Exception:
        return bool(re.search(
            r"(?i)\b(?:\d(?:\.\d+)?\s*BHK|VILLA|BUNGALOW|FLAT|APARTMENT|"
            r"OFFICE|SHOWROOM|SHOP|WAREHOUSE|PLOT|LAND|BLOCK|SECTOR|PHASE)\b",
            own,
        ))


def _has_property_fact(row: Dict[str, Any]) -> bool:
    try:
        return v252._has_own_property_fact(row)
    except Exception:
        return bool(PROPERTY_SPECIFIC_FACT_RE.search(_norm(row.get("own_text_redacted"))))


def _promote_if_safe(row: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    out = deepcopy(row)
    if out.get("classification") != "AMBIGUOUS":
        return out, False
    if out.get("transaction") not in ("SALE", "RENT"):
        return out, False
    if out.get("property_family") not in ("RESIDENTIAL", "COMMERCIAL", "LAND"):
        return out, False
    if not out.get("location"):
        return out, False
    if out.get("boundary_needs_split"):
        return out, False
    if not _has_identity(out) or not _has_property_fact(out):
        return out, False

    reasons = set(out.get("review_reasons") or [])
    if reasons.intersection(PROMOTION_BLOCKERS):
        return out, False

    out["classification"] = "AVAILABILITY"
    reasons_list = [
        r for r in (out.get("review_reasons") or [])
        if r != "CLASSIFICATION_AMBIGUOUS"
    ]
    reasons_list.append("DETERMINISTIC_AVAILABILITY_PROMOTION_V253")
    out["review_reasons"] = list(dict.fromkeys(reasons_list))

    effective = set(out["review_reasons"]) - NON_BLOCKING_EVIDENCE_REASONS
    out["quality"] = "CLEAN" if not effective.intersection(PROMOTION_BLOCKERS) else "UNDER_REVIEW"

    prov = dict(out.get("provenance") or {})
    prov["v253_classification"] = {
        "method": "DETERMINISTIC_SEMANTIC_GATE",
        "from": "AMBIGUOUS",
        "to": "AVAILABILITY",
        "llm_used": False,
    }
    out["provenance"] = prov
    return out, True


def enhance_candidate(
    candidate: Dict[str, Any],
    *,
    unique_burst_location: Optional[str] = None,
) -> Dict[str, Any]:
    row = deepcopy(candidate)
    own_recovered = False
    burst_recovered = False

    if not row.get("location"):
        own = _own_location(_norm(row.get("own_text_redacted")))
        if own:
            row = _set_location(
                row,
                own,
                source="OWN_TEXT_EXPLICIT_ALIAS_V253",
                confidence=0.99,
            )
            own_recovered = True
        elif unique_burst_location and _safe_for_burst_context(row):
            row = _set_location(
                row,
                unique_burst_location,
                source="UNIQUE_BURST_BROAD_LOCALITY_V253",
                confidence=0.90,
            )
            burst_recovered = True

    row, promoted = _promote_if_safe(row)
    row["v253"] = {
        "own_text_location_recovered": own_recovered,
        "unique_burst_location_recovered": burst_recovered,
        "classification_promoted": promoted,
        "llm_used": False,
        "database_write": False,
    }
    return row


def _reason_counts(rows: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counter: Counter[str] = Counter()
    for row in rows:
        if row.get("quality") != "CLEAN":
            counter.update(row.get("review_reasons") or [])
    return dict(counter.most_common())


def _benchmark(engine, limit: int) -> Dict[str, Any]:
    base = v252._benchmark(engine, limit)
    raw_bursts = v24._load_bursts(engine, limit)
    raw_map = {r.get("burst_group_id"): r.get("burst_text") or "" for r in raw_bursts}

    before_rows: List[Dict[str, Any]] = []
    after_rows: List[Dict[str, Any]] = []
    bursts: List[Dict[str, Any]] = []

    for burst in base.get("bursts") or []:
        burst_id = burst.get("burst_group_id")
        unique_loc = _unique_burst_location(raw_map.get(burst_id, ""))
        out_candidates = []

        for candidate in burst.get("candidates") or []:
            before_rows.append(candidate)
            enhanced = enhance_candidate(candidate, unique_burst_location=unique_loc)
            after_rows.append(enhanced)
            out_candidates.append(enhanced)

        bursts.append({
            "burst_group_id": burst_id,
            "v252_entity_count": burst.get("v252_entity_count"),
            "v253_entity_count": len(out_candidates),
            "unique_burst_location": unique_loc,
            "candidates": out_candidates,
        })

    total = len(after_rows)
    clean_before = sum(1 for x in before_rows if x.get("quality") == "CLEAN")
    clean_after = sum(1 for x in after_rows if x.get("quality") == "CLEAN")

    counts = {
        "burst_sample_size": len(bursts),
        "entity_count": total,
        "v252_clean": clean_before,
        "v253_clean": clean_after,
        "true_newly_recovered_clean": sum(
            1 for b, a in zip(before_rows, after_rows)
            if b.get("quality") != "CLEAN" and a.get("quality") == "CLEAN"
        ),
        "v253_under_review": total - clean_after,
        "v253_clean_rate": round(clean_after / total, 4) if total else 0.0,
        "location_missing_before": sum(
            1 for x in before_rows
            if "LOCATION_MISSING" in (x.get("review_reasons") or [])
        ),
        "location_missing_after": sum(
            1 for x in after_rows
            if "LOCATION_MISSING" in (x.get("review_reasons") or [])
        ),
        "ambiguous_before": sum(1 for x in before_rows if x.get("classification") == "AMBIGUOUS"),
        "ambiguous_after": sum(1 for x in after_rows if x.get("classification") == "AMBIGUOUS"),
        "own_text_location_recoveries_v253": sum(
            1 for x in after_rows if (x.get("v253") or {}).get("own_text_location_recovered")
        ),
        "unique_burst_location_recoveries_v253": sum(
            1 for x in after_rows if (x.get("v253") or {}).get("unique_burst_location_recovered")
        ),
        "classification_promotions_v253": sum(
            1 for x in after_rows if (x.get("v253") or {}).get("classification_promoted")
        ),
        "boundary_needs_split": sum(1 for x in after_rows if x.get("boundary_needs_split")),
        "transaction_missing": sum(
            1 for x in after_rows
            if "TRANSACTION_MISSING" in (x.get("review_reasons") or [])
        ),
        "llm_used": sum(
            1 for x in after_rows
            if bool((x.get("ai_understanding") or {}).get("llm_used"))
        ),
        "privacy_redacted": total,
    }

    unresolved = []
    for x in after_rows:
        if "LOCATION_MISSING" in (x.get("review_reasons") or []):
            unresolved.append({
                "burst_group_id": x.get("burst_group_id"),
                "classification": x.get("classification"),
                "transaction": x.get("transaction"),
                "property_family": x.get("property_family"),
                "own_text_redacted": x.get("own_text_redacted"),
                "review_reasons": x.get("review_reasons"),
            })

    return {
        "status": "READY",
        "version": VERSION,
        "mode": MODE,
        "base_v252_version": v252.VERSION,
        "counts": counts,
        "under_review_reasons_v253": _reason_counts(after_rows),
        "unresolved_location_sample": unresolved[:50],
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
            "own_text_explicit_alias_only": True,
            "burst_context_requires_unique_broad_locality": True,
            "requirement_burst_inheritance": False,
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

    own = {
        "classification": "AMBIGUOUS",
        "transaction": "RENT",
        "property_family": "RESIDENTIAL",
        "location": None,
        "own_text_redacted": "DLFPHASE2 300 SYDS 4BHK+SER 1.20LAC+MAINT",
        "review_reasons": ["CLASSIFICATION_AMBIGUOUS", "LOCATION_MISSING"],
        "quality": "UNDER_REVIEW",
        "boundary_needs_split": False,
        "provenance": {},
    }
    own_out = enhance_candidate(own)
    tests.append({
        "name": "explicit_alias_recovery",
        "location": own_out.get("location"),
        "classification": own_out.get("classification"),
        "quality": own_out.get("quality"),
    })

    burst = {
        "classification": "AMBIGUOUS",
        "transaction": "SALE",
        "property_family": "RESIDENTIAL",
        "location": None,
        "own_text_redacted": "4 BHK 2000 SQFT 8.50 CR",
        "review_reasons": ["CLASSIFICATION_AMBIGUOUS", "LOCATION_MISSING"],
        "quality": "UNDER_REVIEW",
        "boundary_needs_split": False,
        "provenance": {},
    }
    burst_out = enhance_candidate(burst, unique_burst_location="Kalkaji")
    tests.append({
        "name": "unique_burst_context_recovery",
        "location": burst_out.get("location"),
        "classification": burst_out.get("classification"),
    })

    multi = _unique_burst_location("Juhu 4 BHK 10 Cr\nBandra West 3 BHK 8 Cr")
    tests.append({
        "name": "multi_locality_burst_rejected",
        "unique_burst_location": multi,
    })

    req = {
        "classification": "REQUIREMENT",
        "transaction": "RENT",
        "property_family": "RESIDENTIAL",
        "location": None,
        "own_text_redacted": "Need 3 BHK 1800 SQFT",
        "review_reasons": ["LOCATION_MISSING"],
        "quality": "UNDER_REVIEW",
        "boundary_needs_split": False,
        "provenance": {},
    }
    req_out = enhance_candidate(req, unique_burst_location="Juhu")
    tests.append({
        "name": "requirement_not_inherited",
        "location": req_out.get("location"),
        "classification": req_out.get("classification"),
    })

    rate = {
        "classification": "AMBIGUOUS",
        "transaction": "SALE",
        "property_family": "LAND",
        "location": None,
        "own_text_redacted": "JUHU LAND 1000 SQFT 55000 PSF",
        "review_reasons": ["CLASSIFICATION_AMBIGUOUS", "LOCATION_MISSING", "AMBIGUOUS_RATE_NOT_TOTAL_PRICE"],
        "quality": "UNDER_REVIEW",
        "boundary_needs_split": False,
        "provenance": {},
    }
    rate_out = enhance_candidate(rate)
    tests.append({
        "name": "rate_guard_survives_location_recovery",
        "location": rate_out.get("location"),
        "classification": rate_out.get("classification"),
        "quality": rate_out.get("quality"),
    })

    passed = (
        own_out.get("location") == "DLF Phase 2"
        and own_out.get("classification") == "AVAILABILITY"
        and own_out.get("quality") == "CLEAN"
        and burst_out.get("location") == "Kalkaji"
        and multi is None
        and req_out.get("location") is None
        and req_out.get("classification") == "REQUIREMENT"
        and rate_out.get("location") == "Juhu"
        and rate_out.get("classification") == "AMBIGUOUS"
        and rate_out.get("quality") == "UNDER_REVIEW"
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
    status_route = "/api/v7/property-ai/location-evidence-v253/status"

    if any(getattr(r, "path", None) == status_route for r in app.router.routes):
        return {"status": "ALREADY_REGISTERED", "version": VERSION, "route": status_route}

    @app.get(status_route)
    def status():
        return JSONResponse({
            "status": "READY",
            "version": VERSION,
            "mode": MODE,
            "base_v252_version": v252.VERSION,
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

    @app.get("/api/v7/property-ai/location-evidence-v253/regression-test")
    def regression_test():
        return JSONResponse(_regression_demo())

    @app.get("/api/v7/property-ai/location-evidence-v253/preview")
    def preview(limit: int = Query(25, ge=1, le=100)):
        return JSONResponse(_benchmark(engine, limit))

    @app.get("/api/v7/property-ai/location-evidence-v253/unresolved")
    def unresolved(limit: int = Query(25, ge=1, le=100)):
        result = _benchmark(engine, limit)
        return JSONResponse({
            "status": result["status"],
            "version": VERSION,
            "counts": result["counts"],
            "unresolved_location_sample": result["unresolved_location_sample"],
            "writes_performed": 0,
        })

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": status_route,
        "regression": "/api/v7/property-ai/location-evidence-v253/regression-test",
        "preview": "/api/v7/property-ai/location-evidence-v253/preview?limit=25",
        "unresolved": "/api/v7/property-ai/location-evidence-v253/unresolved?limit=25",
        "writes_enabled": False,
    }

