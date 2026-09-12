from __future__ import annotations

from typing import Any, Dict, List, Tuple

import alliance_phase5_canonical_matcher as phase5
import alliance_requirement_brain_v3 as brain
import alliance_property_semantic_adapter_v3 as property_semantics

VERSION = "1.0.0-V3-TO-FROZEN-MATCHER-SHADOW"

FROZEN_COMPAT = {
    "APARTMENT": ("RESIDENTIAL", "APARTMENT"),
    "VILLA": ("RESIDENTIAL", "VILLA"),
    "BUILDER_FLOOR": ("RESIDENTIAL", "BUILDER FLOOR"),
    "OFFICE": ("COMMERCIAL", "OFFICE"),
    "RETAIL_UNIT": ("COMMERCIAL", "RETAIL"),
    "RESTAURANT": ("COMMERCIAL", "RESTAURANT"),
    "BANQUET": ("COMMERCIAL", "BANQUET"),
    "HOTEL": ("COMMERCIAL", "HOTEL"),
    "WAREHOUSE": ("COMMERCIAL", "WAREHOUSE"),
    "FARMHOUSE": ("LAND", "LAND"),
    "LAND": ("LAND", "LAND"),
}

def _value(obj: Any, key: str, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else default

def _v3_asset_names(v3: Dict[str, Any]) -> List[str]:
    asset = v3.get("asset") or {}
    rows = asset.get("acceptable_assets") or []
    names = []
    for row in rows:
        name = str((row or {}).get("asset") or "").upper().strip()
        if name and name not in names:
            names.append(name)
    primary = str(asset.get("primary_asset") or "").upper().strip()
    if primary and primary != "UNKNOWN" and primary not in names:
        names.insert(0, primary)
    return names

def _v3_locations(v3: Dict[str, Any]) -> Tuple[List[str], bool]:
    rows = v3.get("locations") or []
    hard, preferred, acceptable = [], [], []
    for row in rows:
        name = str((row or {}).get("name") or "").upper().strip()
        constraint = str((row or {}).get("constraint") or "").upper().strip()
        if not name or constraint == "EXCLUDED":
            continue
        target = hard if constraint == "HARD" else preferred if constraint == "PREFERRED" else acceptable
        if name not in target:
            target.append(name)
    if hard:
        return hard, True
    merged = []
    for name in preferred + acceptable:
        if name not in merged:
            merged.append(name)
    if merged:
        return merged, False
    search_geo = v3.get("search_geography") or {}
    return [str(x).upper().strip() for x in (search_geo.get("candidate_locations") or []) if str(x).strip()], False

def _area(v3: Dict[str, Any]) -> Tuple[Any, Any]:
    area = v3.get("area") or {}
    return area.get("min_sqft"), area.get("max_sqft")

def _budget(v3: Dict[str, Any]):
    budget = v3.get("budget") or {}
    if str(budget.get("status") or "").upper() == "NOT_SPECIFIED":
        return None
    return budget.get("max_value") or budget.get("max")

def build_matcher_variants_from_v3(v3: Dict[str, Any]) -> Dict[str, Any]:
    intent = v3.get("intent") or {}
    role = str(intent.get("role") or "").upper()
    if role != "REQUIREMENT":
        return {"status": "BLOCKED", "reason": "INTENT_NOT_REQUIREMENT", "variants": [], "semantic": v3}

    tx = str(_value(v3.get("transaction") or {}, "value") or "").upper().strip()
    if tx not in {"SALE", "RENT"}:
        return {"status": "BLOCKED", "reason": "TRANSACTION_REQUIRES_HUMAN_CONFIRMATION", "variants": [], "semantic": v3}

    assets = _v3_asset_names(v3)
    if not assets:
        return {"status": "BLOCKED", "reason": "ASSET_UNKNOWN", "variants": [], "semantic": v3}

    locations, location_only = _v3_locations(v3)
    area_min, area_max = _area(v3)
    budget_max = _budget(v3)

    variants = []
    for semantic_asset in assets:
        compat = FROZEN_COMPAT.get(semantic_asset)
        if not compat:
            continue
        family, subtype = compat
        variants.append({
            "transaction": tx,
            "family": family,
            "subtype": subtype,
            "primary_locations": list(locations),
            "location_only": bool(location_only),
            "area_min_sqft": area_min,
            "area_max_sqft": area_max,
            "budget_max": budget_max,
            "semantic_asset": semantic_asset,
            "semantic_use": _value(v3.get("intended_use") or v3.get("use") or {}, "primary_use"),
            "semantic_brain_version": v3.get("brain_version") or getattr(brain, "VERSION", "UNKNOWN"),
            "adapter_version": VERSION,
            "region_expanded": bool((v3.get("search_geography") or {}).get("derived")),
            "source_region": (v3.get("search_geography") or {}).get("region"),
        })

    if not variants:
        return {"status": "BLOCKED", "reason": "NO_FROZEN_COMPATIBILITY_MAPPING", "variants": [], "semantic": v3}

    return {"status": "READY", "reason": None, "variants": variants, "semantic": v3}

def analyze_and_build(raw_text: str) -> Dict[str, Any]:
    v3 = brain.analyze(raw_text, source="V3_MATCHER_BRIDGE_SHADOW")
    result = build_matcher_variants_from_v3(v3)
    result["raw_text"] = str(raw_text or "")
    return result

def adapt_candidate(candidate: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(candidate)
    profile = property_semantics.profile_property(candidate)
    asset = profile.get("asset") or {}
    semantic_asset = str(asset.get("primary_asset") or "").upper().strip()
    compat = FROZEN_COMPAT.get(semantic_asset)
    if compat:
        out["family"], out["subtype"] = compat
    out["semantic_asset"] = semantic_asset or "UNKNOWN"
    out["semantic_profile_version"] = profile.get("version")
    out["semantic_derived_only"] = True
    return out

def evaluate_variant(req: Dict[str, Any], candidates: List[Dict[str, Any]], min_score: float = 70.0) -> Dict[str, Any]:
    exact_verified, exact_verify, rejected = [], [], []
    for raw_candidate in candidates:
        p = adapt_candidate(raw_candidate)
        ok, code, gate = phase5.eligible(req, p, "EXACT")
        if not ok:
            rejected.append({"record_id": p.get("record_id"), "reason": code, "semantic_asset": p.get("semantic_asset")})
            continue
        score, why = phase5.score(req, p, "EXACT", gate)
        if score < float(min_score):
            rejected.append({"record_id": p.get("record_id"), "reason": "BELOW_MIN_SCORE", "semantic_asset": p.get("semantic_asset")})
            continue
        item = phase5.public_item(p, score, "EXACT", why)
        item["semantic_asset"] = p.get("semantic_asset")
        item["matcher_variant_asset"] = req.get("semantic_asset")
        (exact_verified if item.get("send_eligible") else exact_verify).append(item)

    exact_verified.sort(key=lambda x: x.get("match_score", 0), reverse=True)
    exact_verify.sort(key=lambda x: x.get("match_score", 0), reverse=True)
    return {"exact_verified": exact_verified, "exact_needs_verification": exact_verify, "rejected": rejected}

def merge_variant_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    seen = set()
    verified, verify, rejected = [], [], []
    for result in results:
        for bucket_name, target in (("exact_verified", verified), ("exact_needs_verification", verify)):
            for row in result.get(bucket_name) or []:
                key = (str(row.get("source_table") or ""), str(row.get("record_id") or ""), str(row.get("location") or ""))
                if key in seen:
                    continue
                seen.add(key)
                target.append(row)
        rejected.extend(result.get("rejected") or [])

    verified.sort(key=lambda x: x.get("match_score", 0), reverse=True)
    verify.sort(key=lambda x: x.get("match_score", 0), reverse=True)
    return {
        "exact_verified": verified,
        "exact_needs_verification": verify,
        "rejected": rejected,
        "contacts_exposed": False,
        "production_behavior_changed": False,
        "frozen_matcher_changed": False,
        "adapter_version": VERSION,
    }
