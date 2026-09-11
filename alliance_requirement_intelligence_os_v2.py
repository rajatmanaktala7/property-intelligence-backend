from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

import alliance_phase5_canonical_matcher as phase5

VERSION = "2.3.0-UNIFIED-INTENT-POLICY"

_DEMAND = (
    (r"\bLOOKING\s+FOR\b", 50, "looking_for"),
    (r"\bLOOKING\s+TO\s+(?:BUY|RENT|LEASE)\b", 50, "looking_to_transact"),
    (r"\bREQUIREMENT\b", 48, "requirement"),
    (r"\bREQUIRED\b", 45, "required"),
    (r"\bNEED(?:ED)?\b", 42, "need"),
    (r"\bWANTED\b", 42, "wanted"),
    (r"\bSEEKING\b", 40, "seeking"),
    (r"\bCLIENT\s+(?:IS\s+)?LOOKING\b", 55, "client_looking"),
    (r"\bFOR\s+OUR\s+CLIENT\b", 45, "for_our_client"),
    (r"\bBUYER\s+REQUIREMENT\b", 55, "buyer_requirement"),
    (r"\bTENANT\s+REQUIREMENT\b", 55, "tenant_requirement"),
)

_SUPPLY = (
    (r"\bPROPERTY\s+AVAILABLE\b", 55, "property_available"),
    (r"\bAVAILABLE\s+FOR\s+(?:SALE|RENT|LEASE)\b", 55, "available_for"),
    (r"\bTOTAL\s+DEAL\s+VALUE\b", 55, "total_deal_value"),
    (r"\bCHEQUE\s+FLEXIBLE\b", 32, "cheque_flexible"),
    (r"\bCOVERED\s+CAR\s+PARKING\b", 18, "covered_parking"),
    (r"\b(?:BASEMENT|TERRACE)\s+INCLUDED\b", 22, "included_component"),
    (r"\bOWNER\s+DIRECT\b", 28, "owner_direct"),
    (r"\bDIRECT\s+OWNER\b", 28, "direct_owner"),
)


def norm(value: Any) -> str:
    s = str(value or "").upper()
    s = re.sub(r"[^A-Z0-9₹./+\- ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def semantic_norm(value: Any) -> str:
    s = str(value or "").upper()
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def safe_normalize(raw: str) -> str:
    s = str(raw or "")
    s = re.sub(r"(?i)\bPLEASE\b", "KINDLY", s)
    return s


def _score_patterns(text: str, patterns) -> Tuple[int, List[str]]:
    score = 0
    hits: List[str] = []
    for pattern, weight, label in patterns:
        if re.search(pattern, text, re.I):
            score += weight
            hits.append(label)
    return score, hits


def classify_intent(raw: str) -> Dict[str, Any]:
    t = norm(raw)
    demand_score, demand_hits = _score_patterns(t, _DEMAND)
    supply_score, supply_hits = _score_patterns(t, _SUPPLY)

    facts = {
        "area": bool(re.search(
            r"\b\d+(?:,\d{3})*(?:\.\d+)?\s*(?:SQ\.?\s*FT|SQFT|SFT|SQ\.?\s*YD|SQYD|YDS?|SQM|SQ\.?\s*M)\b",
            t, re.I,
        )),
        "rate_psf": bool(re.search(
            r"(?:₹|RS\.?|INR)?\s*\d[\d,]*(?:\.\d+)?\s*(?:/-)?\s*(?:PER\s+SQ\.?\s*FT|PSF|/\s*SQ\.?\s*FT)",
            t, re.I,
        )),
        "total_price": bool(re.search(
            r"(?:₹|RS\.?|INR)?\s*\d+(?:\.\d+)?\s*(?:CR|CRORE|CRORES|LAKH|LAKHS|LAC)\b",
            t, re.I,
        )),
        "floor": bool(re.search(
            r"\b(?:GROUND|LOWER\s+GROUND|UPPER\s+GROUND|1ST|2ND|3RD|4TH|5TH|\d+(?:ST|ND|RD|TH))\s+FLOOR\b",
            t, re.I,
        )),
        "facing": bool(re.search(
            r"\b(?:NORTH|SOUTH|EAST|WEST|ANANTRAJ)[ -]FACING\b|\bFACING\b",
            t, re.I,
        )),
        "unit_type": bool(re.search(
            r"\b\d+(?:\.\d+)?\s*BHK\b|\b(?:HIGH\s+STREET\s+)?RETAIL\b|\bSHOP\b|\bOFFICE\b|\bVILLA\b|\bPLOT\b",
            t, re.I,
        )),
        "project_header": bool(re.search(
            r"\b[A-Z][A-Z0-9 ]{3,}\s*\|\s*(?:SEC|SECTOR)\s*-?\s*\d{1,3}[A-Z]?\b",
            t,
        )),
    }

    fact_count = sum(facts.values())

    if facts["rate_psf"] and facts["total_price"]:
        supply_score += 45
        supply_hits.append("rate_plus_total_price")
    if facts["project_header"] and facts["area"] and facts["unit_type"]:
        supply_score += 38
        supply_hits.append("project_unit_area_listing_structure")
    if fact_count >= 5:
        supply_score += 30
        supply_hits.append("dense_asset_facts")
    elif fact_count >= 4:
        supply_score += 20
        supply_hits.append("multiple_asset_facts")

    strong_supply_labels = {
        "property_available",
        "available_for",
        "total_deal_value",
        "rate_plus_total_price",
        "project_unit_area_listing_structure",
        "dense_asset_facts",
        "owner_direct",
        "direct_owner",
    }
    strong_supply_anchor = any(
        label in strong_supply_labels for label in supply_hits
    )

    # Transaction words such as "for lease", "for rent" and "for sale"
    # are neutral. They describe transaction mode, not whether the text is
    # demand or supply. Clear demand verbs govern unless strong listing
    # evidence proves the message is inventory/supply.
    if demand_score >= 40 and not strong_supply_anchor:
        role = "REQUIREMENT"
        confidence = min(99, 84 + min(15, demand_score // 8))
    elif demand_score >= 45 and demand_score >= supply_score + 10:
        role = "REQUIREMENT"
        confidence = min(99, 80 + min(19, (demand_score - supply_score) // 3))
    elif (
        strong_supply_anchor
        and supply_score >= 55
        and supply_score >= demand_score + 10
    ):
        role = "SUPPLY"
        confidence = min(99, 82 + min(17, (supply_score - demand_score) // 4))
    else:
        role = "AMBIGUOUS"
        confidence = 45 if (demand_score or supply_score) else 0

    return {
        "role": role,
        "confidence": int(confidence),
        "demand_score": int(demand_score),
        "supply_score": int(supply_score),
        "demand_hits": demand_hits,
        "supply_hits": supply_hits,
        "structured_listing_facts": {**facts, "count": fact_count},
    }


def extract_block(raw: str) -> str | None:
    s = str(raw or "")

    m = re.search(r"(?i)\b([A-Z])\s*[- ]?BLOCK\b", s)
    if m:
        return f"BLOCK {m.group(1).upper()}"

    m = re.search(r"(?i)\bBLOCK\s*[-:]?\s*([A-Z0-9]+)\b", s)
    if not m:
        return None

    token = m.group(1).upper()
    if token in {"GK", "GREATER", "SECTOR", "SEC", "DELHI"}:
        return None

    return f"BLOCK {token}"


def _block_token(block: str | None) -> str | None:
    if not block:
        return None
    m = re.fullmatch(r"BLOCK\s+([A-Z0-9]+)", semantic_norm(block))
    return m.group(1) if m else None


def _block_alias_patterns(block: str | None) -> List[str]:
    """Return all canonical word-order forms for one block entity."""
    token = _block_token(block)
    if not token:
        return []
    t = re.escape(token)
    return [
        rf"BLOCK\s+{t}",
        rf"{t}\s+BLOCK",
    ]


def _entity_phrase_present(text: str, patterns: List[str]) -> bool:
    return any(re.search(rf"\b(?:{p})\b", text) for p in patterns)


def _entity_strict(text: str, patterns: List[str]) -> bool:
    for p in patterns:
        if re.search(rf"\bONLY\s+(?:IN\s+)?(?:{p})\b", text):
            return True
        if re.search(rf"\b(?:{p})\s+ONLY\b", text):
            return True
        if re.search(rf"\bSTRICTLY\s+(?:IN\s+)?(?:{p})\b", text):
            return True
        if re.search(rf"\bSPECIFICALLY\s+(?:IN\s+)?(?:{p})\b", text):
            return True
    return False


def _entity_preferred(text: str, patterns: List[str]) -> bool:
    for p in patterns:
        if re.search(
            rf"\b(?:PREFER|PREFERABLY|PREFERRED|PREFERENCE)\b.{{0,45}}\b(?:{p})\b",
            text,
        ):
            return True
        if re.search(
            rf"\b(?:{p})\b.{{0,20}}\b(?:PREFER|PREFERRED|PREFERENCE)\b",
            text,
        ):
            return True
    return False


def extract_project(raw: str) -> str | None:
    text = re.sub(r"[*_]+", "", str(raw or ""))
    patterns = (
        r"(?i)\b(?:PLOT|LAND|PROPERTY|SPACE)\s+(?:IN|AT)\s+(.+?)\s+(?=SECTOR\s*[-]?\s*\d+\b)",
        r"(?i)\b(?:IN|AT)\s+(.+?)\s+(?=SECTOR\s*[-]?\s*\d+\b)",
    )

    for pattern in patterns:
        m = re.search(pattern, text)
        if not m:
            continue

        p = semantic_norm(m.group(1))
        p = re.sub(r"\b(?:ONLY|REQUIRED|REQUIREMENT)$", "", p).strip()

        if p and len(p) >= 4 and p not in {"THE", "A", "AN"}:
            return p

    return None


def infer_transaction(raw: str) -> Tuple[str | None, str | None, float]:
    n = semantic_norm(raw)

    rent = bool(re.search(r"\b(RENT|RENTAL|LEASE|LEASING|TO LET)\b", n))
    sale = bool(re.search(r"\b(SALE|PURCHASE|BUY|BUYING|RESALE|SELL|SELLING)\b", n))

    if sale and not rent:
        return "SALE", "EXPLICIT", 1.0
    if rent and not sale:
        return "RENT", "EXPLICIT", 1.0
    if sale and rent:
        return None, "AMBIGUOUS", 0.0

    demand = bool(re.search(
        r"\b(REQUIREMENT|REQUIRED|NEED|NEEDED|LOOKING|WANTED|WANT|SEEKING|CLIENT)\b",
        n,
    ))

    rent_cue = bool(re.search(
        r"\b(RENT|RENTAL|LEASE|LEASING|MONTHLY RENT|SECURITY DEPOSIT|LOCK IN|LOCKIN|LEASE TERM)\b",
        n,
    ))

    if bool(re.search(r"\b(PLOT|LAND)\b", n)) and demand and not rent_cue:
        return "SALE", "INFERRED_LAND_DEMAND", 0.90

    purchase_cues = 0
    if re.search(r"\bIMMEDIATE PAYMENT\b", n):
        purchase_cues += 1
    if re.search(r"\bCLEAR TITLE\b", n):
        purchase_cues += 1
    if re.search(r"\b(?:BUDGET|CLIENT BUDGET)\b", n) and re.search(r"\b(?:CR|CRORE|CRORES)\b", n):
        purchase_cues += 1
    if re.search(r"\bREGISTRY\b|\bREGISTRATION\b|\bCHEQUE\b", n):
        purchase_cues += 1

    if demand and purchase_cues >= 2 and not rent_cue:
        return "SALE", "INFERRED_PURCHASE_MANDATE", 0.95

    return None, None, 0.0


def _block_semantics(raw: str, block: str | None) -> Dict[str, bool]:
    if not block:
        return {"block_strict": False, "block_preference": False}

    text = semantic_norm(raw)
    forms = _block_alias_patterns(block)

    preference = _entity_preferred(text, forms)
    strict = _entity_strict(text, forms)

    # Preference language controls this entity and overrides unrelated ONLY elsewhere.
    if preference:
        strict = False

    return {
        "block_strict": strict,
        "block_preference": preference,
    }


def _project_semantics(raw: str, project: str | None, block_strict: bool) -> Dict[str, bool]:
    if not project:
        return {"project_strict": False}

    n = semantic_norm(raw)
    p = semantic_norm(project)
    ep = re.escape(p)

    strict = bool(
        re.search(rf"\bONLY\s+(?:IN\s+)?{ep}\b", n)
        or re.search(rf"\b{ep}\s+ONLY\b", n)
        or re.search(rf"\bSTRICTLY\s+(?:IN\s+)?{ep}\b", n)
    )

    if block_strict:
        strict = True

    return {"project_strict": strict}


def _location_only(raw: str, locations: List[str]) -> bool:
    if not locations:
        return False

    n = semantic_norm(raw)

    for location in locations:
        loc = re.escape(semantic_norm(location))
        if (
            re.search(rf"\b{loc}\s+ONLY\b", n)
            or re.search(rf"\bONLY\s+(?:IN\s+)?{loc}\b", n)
        ):
            return True

    aliases = {
        "GREATER KAILASH 1": (r"\bGK\s*1\s+ONLY\b", r"\bGK1\s+ONLY\b"),
        "GREATER KAILASH 2": (r"\bGK\s*2\s+ONLY\b", r"\bGK2\s+ONLY\b"),
    }

    for canonical, patterns in aliases.items():
        if canonical in locations and any(re.search(p, n) for p in patterns):
            return True

    return False


def _semantic_preferences(raw: str, block: str | None) -> List[str]:
    n = semantic_norm(raw)
    out: List[str] = []

    block_sem = _block_semantics(raw, block)
    if block_sem["block_preference"] and block:
        out.append(f"BLOCK:{block}")

    for token, label in (
        ("FULLY FURNISHED", "FULLY_FURNISHED"),
        ("SEMI FURNISHED", "SEMI_FURNISHED"),
        ("PARKING", "PARKING"),
        ("LIFT", "LIFT"),
        ("EAST FACING", "EAST_FACING"),
        ("NORTH FACING", "NORTH_FACING"),
        ("NO PETS", "NO_PETS"),
        ("GATED", "GATED_COMPLEX"),
    ):
        if token in n and label not in out:
            out.append(label)

    return out


def analyze(raw: str) -> Dict[str, Any]:
    normalized = safe_normalize(raw)
    intent = classify_intent(raw)
    transaction, tx_source, tx_conf = infer_transaction(raw)
    block = extract_block(raw)
    project = extract_project(raw)

    phase_req = phase5.parse_requirement(normalized)

    locations = list(phase_req.get("primary_locations") or [])
    block_sem = _block_semantics(raw, block)
    project_sem = _project_semantics(raw, project, block_sem["block_strict"])
    location_only = _location_only(raw, locations)

    hard_constraints: List[str] = []

    if location_only:
        hard_constraints.append("LOCATION_ONLY")

    if project_sem["project_strict"] and project:
        hard_constraints.append(f"PROJECT:{project}")

    if block_sem["block_strict"] and block:
        hard_constraints.append(f"BLOCK:{block}")

    preferences = _semantic_preferences(raw, block)

    return {
        "version": VERSION,
        "intent": intent,
        "transaction": transaction,
        "transaction_source": tx_source,
        "transaction_confidence": tx_conf,
        "requires_human_transaction_confirmation": tx_source in {
            "INFERRED_LAND_DEMAND",
            "INFERRED_PURCHASE_MANDATE",
        },
        "project": project,
        "block": block,
        "project_strict": project_sem["project_strict"],
        "block_strict": block_sem["block_strict"],
        "block_preference": block_sem["block_preference"],
        "location_only": location_only,
        "hard_constraints": hard_constraints,
        "preferences": preferences,
        "baseline": {
            "primary_locations": phase_req.get("primary_locations"),
            "location": phase_req.get("location"),
            "family": phase_req.get("family"),
            "subtype": phase_req.get("subtype"),
            "acceptable_subtypes": phase_req.get("acceptable_subtypes"),
            "area_min_sqft": phase_req.get("area_min_sqft"),
            "area_max_sqft": phase_req.get("area_max_sqft"),
            "budget_min": phase_req.get("budget_min"),
            "budget_max": phase_req.get("budget_max"),
        },
    }


def build_canonical_requirement(raw: str) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    normalized = safe_normalize(raw)
    intel = analyze(raw)

    if intel.get("transaction") == "SALE" and not re.search(
        r"(?i)\b(SALE|PURCHASE|BUY|RESALE)\b", normalized
    ):
        normalized += " | FOR SALE"

    elif intel.get("transaction") == "RENT" and not re.search(
        r"(?i)\b(RENT|RENTAL|LEASE)\b", normalized
    ):
        normalized += " | FOR RENT"

    req = phase5.parse_requirement(normalized)

    if intel.get("transaction"):
        req["transaction"] = intel["transaction"]
        req["transaction_source"] = intel["transaction_source"]
        req["transaction_confidence"] = intel["transaction_confidence"]

    req["raw"] = raw
    req["requirement_intelligence"] = intel
    req["hard_constraints"] = list(intel.get("hard_constraints") or [])
    req["preferences"] = list(intel.get("preferences") or [])
    req["location_only"] = bool(intel.get("location_only"))
    req["project"] = intel.get("project")
    req["block"] = intel.get("block")

    return normalized, req, intel


def _candidate_blob(candidate: Dict[str, Any]) -> str:
    return semantic_norm(" ".join(str(candidate.get(k) or "") for k in (
        "description",
        "location",
        "source_name",
        "property_name",
        "remarks",
        "configuration_details",
    )))


def _contains_project(blob: str, project: str) -> bool:
    words = [w for w in semantic_norm(project).split() if len(w) >= 3]
    blob_words = set(blob.split())
    return bool(words) and all(w in blob_words for w in words)


def _contains_block(blob: str, block: str) -> bool:
    forms = _block_alias_patterns(block)
    return _entity_phrase_present(blob, forms)


def filter_candidates_by_hard_scope(
    candidates: List[Dict[str, Any]],
    intelligence: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:

    project = str(intelligence.get("project") or "").strip()
    block = str(intelligence.get("block") or "").strip()

    project_strict = bool(intelligence.get("project_strict"))
    block_strict = bool(intelligence.get("block_strict"))

    if not project_strict and not block_strict:
        return candidates, {
            "applied": False,
            "before": len(candidates),
            "after": len(candidates),
            "project_strict": False,
            "block_strict": False,
        }

    out = []

    for candidate in candidates:
        blob = _candidate_blob(candidate)

        if project_strict and project and not _contains_project(blob, project):
            continue

        if block_strict and block and not _contains_block(blob, block):
            continue

        out.append(candidate)

    return out, {
        "applied": True,
        "before": len(candidates),
        "after": len(out),
        "project": project or None,
        "block": block or None,
        "project_strict": project_strict,
        "block_strict": block_strict,
    }


def constraint_summary(raw: str) -> Dict[str, List[str]]:
    intel = analyze(raw)
    return {
        "hard_constraints": list(intel.get("hard_constraints") or []),
        "preferences": list(intel.get("preferences") or []),
    }


def regression_corpus() -> List[Dict[str, Any]]:
    return [
        {
            "name": "experion_strict_plot_purchase",
            "text": "Requirement of 400 to 550 sqyd Plot in Experion Westerlies Sector 108 Only in Block-A",
            "expect": {
                "role": "REQUIREMENT",
                "transaction": "SALE",
                "block": "BLOCK A",
                "block_strict": True,
                "project": "EXPERION WESTERLIES",
                "project_strict": True,
                "family": "LAND",
                "subtype": "LAND",
            },
        },
        {
            "name": "experion_prefix_block_strict",
            "text": "Requirement 500 sqyd plot in Experion Westerlies Sector 108, A-Block only",
            "expect": {
                "role": "REQUIREMENT",
                "transaction": "SALE",
                "block": "BLOCK A",
                "block_strict": True,
                "project": "EXPERION WESTERLIES",
                "project_strict": True,
            },
        },
        {
            "name": "preferred_block_not_hard",
            "text": "Required in GK-1 only, 300 yds, preferably B-Block, immediate payment, clear title, client budget Rs 31 Crores",
            "expect": {
                "role": "REQUIREMENT",
                "transaction": "SALE",
                "block": "BLOCK B",
                "block_strict": False,
                "block_preference": True,
                "location_only": True,
                "family": None,
                "subtype": None,
            },
        },
        {
            "name": "goa_villa_rent",
            "text": "Looking for a 3bhk fully furnished Villa in a gated complex for long term rent in and around Anjuna, Vagator, Assagao, Parra, Saligao and Siolim. Budget 1.3L",
            "expect": {
                "role": "REQUIREMENT",
                "transaction": "RENT",
                "family": "RESIDENTIAL",
                "subtype": "VILLA",
            },
        },
        {
            "name": "smartworld_supply",
            "text": "SMARTWORLD ORCHARD | SEC 61 3.5 BHK | 1,680 Sq. Ft. North-Facing Entry ₹15,000/- per Sq. Ft. Total Deal Value: ₹2.52 Cr. Cheque Flexible",
            "expect": {
                "role": "SUPPLY",
            },
        },
        {
            "name": "aipl_supply",
            "text": "AIPL JOY SQUARE | SEC 63A High Street Retail | 1st Floor 415 Sq. Ft. Anantraj Facing ₹15,000/- per Sq. Ft. Total Deal Value: ₹62.25 Lakhs 1 Covered Car Parking Including",
            "expect": {
                "role": "SUPPLY",
            },
        },
        {
            "name": "please_not_lease",
            "text": "Requirement in GK-1. Please call.",
            "expect": {
                "role": "REQUIREMENT",
                "transaction": None,
            },
        },
        {
            "name": "shop_rent_requirement",
            "text": "Need shop for rent in Saket ground floor 800 to 1200 sqft budget 3 lakh",
            "expect": {
                "role": "REQUIREMENT",
                "transaction": "RENT",
            },
        },
        {
            "name": "looking_to_buy_plot",
            "text": "Looking to buy plot in Greater Kailash 1 around 300 sqyd budget 30 crore",
            "expect": {
                "role": "REQUIREMENT",
                "transaction": "SALE",
            },
        },
        {
            "name": "office_available_for_lease",
            "text": "Office available for lease in Nehru Place 3000 sqft monthly rent 5 lakh",
            "expect": {
                "role": "SUPPLY",
                "transaction": "RENT",
            },
        },
        {
            "name": "office_lease_requirement",
            "text": "Need office for lease in Nehru Place around 3000 sqft budget 5 lakh",
            "expect": {
                "role": "REQUIREMENT",
                "transaction": "RENT",
                "family": "COMMERCIAL",
                "subtype": "OFFICE",
            },
        },
        {
            "name": "block_12_strict",
            "text": "Requirement for sale in Sector 50, Block 12 only, 2000 sqft",
            "expect": {
                "role": "REQUIREMENT",
                "transaction": "SALE",
                "block": "BLOCK 12",
                "block_strict": True,
            },
        },
        {
            "name": "a_block_preferred",
            "text": "Requirement 400 sqyd plot in Sector 108, A-Block preferred",
            "expect": {
                "role": "REQUIREMENT",
                "transaction": "SALE",
                "block": "BLOCK A",
                "block_strict": False,
                "block_preference": True,
            },
        },
    ]


def run_regressions() -> List[Dict[str, Any]]:
    results = []

    for case in regression_corpus():
        _, req, intel = build_canonical_requirement(case["text"])

        actual = {
            "role": intel["intent"]["role"],
            "transaction": req.get("transaction"),
            "block": intel.get("block"),
            "block_strict": intel.get("block_strict"),
            "block_preference": intel.get("block_preference"),
            "project": intel.get("project"),
            "project_strict": intel.get("project_strict"),
            "location_only": intel.get("location_only"),
            "family": req.get("family"),
            "subtype": req.get("subtype"),
        }

        for key, expected in case["expect"].items():
            assert actual.get(key) == expected, {
                "case": case["name"],
                "key": key,
                "expected": expected,
                "actual": actual.get(key),
                "full_actual": actual,
            }

        results.append({
            "name": case["name"],
            "status": "PASS",
            **actual,
        })

    direct = [
        ("Only in Block-A", "BLOCK A", True, False),
        ("A-Block only", "BLOCK A", True, False),
        ("Block A only", "BLOCK A", True, False),
        ("A Block only", "BLOCK A", True, False),
        ("Preferably B-Block", "BLOCK B", False, True),
        ("B Block preferred", "BLOCK B", False, True),
        ("Block B preferred", "BLOCK B", False, True),
        ("Block 12 only", "BLOCK 12", True, False),
    ]

    for raw, block, strict, preference in direct:
        got = extract_block(raw)
        sem = _block_semantics(raw, got)

        assert got == block, (raw, block, got)
        assert sem["block_strict"] is strict, (raw, sem)
        assert sem["block_preference"] is preference, (raw, sem)

    return results
