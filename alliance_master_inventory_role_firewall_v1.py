from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

VERSION = "1.0.0-MASTER-INVENTORY-ROLE-FIREWALL"
MARKER = "ALLIANCE_MASTER_INVENTORY_ROLE_FIREWALL_V1"

DEMAND_PATTERNS = [
    r"\bNEED(?:ED|ING)?\b",
    r"\bWANT(?:ED|ING)?\b",
    r"\bLOOKING\s+FOR\b",
    r"\bREQUIREMENT\b",
    r"\bREQUIRES?\b",
    r"\bCLIENT\s+(?:NEEDS?|REQUIRES?|LOOKING)\b",
    r"\bIF\s+AVAILABLE\b",
    r"\bANY\s+OPTIONS?\b",
    r"\bTENANT\s+(?:NEEDS?|TYPE)\b",
]
SUPPLY_PATTERNS = [
    r"\bAVAILABLE\s+FOR\s+(?:RENT|LEASE|SALE)\b",
    r"\bFOR\s+(?:RENT|LEASE|SALE)\b",
    r"\bON\s+(?:RENT|LEASE|SALE)\b",
    r"\bSHOP\s+AVAILABLE\b",
    r"\bOFFICE\s+AVAILABLE\b",
    r"\bRESTAURANT\s+(?:AVAILABLE|ON\s+LEASE)\b",
    r"\bPROPERTY\s+AVAILABLE\b",
    r"\bASKING\b",
    r"\bRENT\s*[:\-]?\s*(?:₹|RS\.?)?\s*\d",
    r"\bPRICE\s*[:\-]?\s*(?:₹|RS\.?)?\s*\d",
]
CONTEXT_ONLY_RESTAURANT = [
    r"\bRESTAURANT\s+STAFF\b",
    r"\bNEAR\s+(?:BY\s+)?(?:A\s+)?RESTAURANT\b",
]

def _norm(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "").upper()).strip()

def _hits(patterns: Iterable[str], text: str) -> List[str]:
    return [p for p in patterns if re.search(p, text, re.I)]

def classify_inventory_role(raw_text: Any, structured_family: Any = None, structured_transaction: Any = None) -> Dict[str, Any]:
    t = _norm(raw_text)
    demand_hits = _hits(DEMAND_PATTERNS, t)
    supply_hits = _hits(SUPPLY_PATTERNS, t)
    demand_score = len(demand_hits) * 2
    supply_score = len(supply_hits) * 2
    if structured_family:
        supply_score += 1
    if structured_transaction:
        supply_score += 1

    strong_demand = bool(re.search(r"\b(NEED|WANT|LOOKING FOR|REQUIREMENT|CLIENT REQUIRES?|IF AVAILABLE)\b", t, re.I))
    strong_supply = bool(re.search(r"\b(AVAILABLE FOR|ON LEASE|ON RENT|FOR SALE|SHOP AVAILABLE|PROPERTY AVAILABLE)\b", t, re.I))

    if strong_demand and not strong_supply:
        role = "PROPERTY_DEMAND"
    elif strong_supply and supply_score >= demand_score:
        role = "PROPERTY_SUPPLY"
    elif demand_score >= supply_score + 2:
        role = "PROPERTY_DEMAND"
    elif supply_score >= demand_score + 2:
        role = "PROPERTY_SUPPLY"
    elif not t:
        role = "NOISE"
    else:
        role = "AMBIGUOUS"

    restaurant_context_only = any(re.search(p, t, re.I) for p in CONTEXT_ONLY_RESTAURANT)
    restaurant_supply_evidence = bool(re.search(
        r"\b(RESTAURANT\s+(?:AVAILABLE|ON\s+LEASE)|SUITABLE\s+FOR\s+RESTAURANT|RUNNING\s+RESTAURANT\s+(?:AVAILABLE|FOR\s+LEASE))\b",
        t, re.I
    ))
    restaurant_asset_allowed = restaurant_supply_evidence and not restaurant_context_only

    return {
        "role": role,
        "demand_score": demand_score,
        "supply_score": supply_score,
        "restaurant_context_only": restaurant_context_only,
        "restaurant_asset_allowed": restaurant_asset_allowed,
        "eligible_for_property_matcher": role == "PROPERTY_SUPPLY",
        "reason": (
            "supply evidence dominates" if role == "PROPERTY_SUPPLY" else
            "demand evidence dominates" if role == "PROPERTY_DEMAND" else
            "insufficient role evidence" if role == "AMBIGUOUS" else
            "empty/noise"
        ),
    }
