from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from alliance_semantic_schema_v3 import assert_valid_requirement

VERSION = "3.2.1-MULTI-ASSET-RENTAL-SEMANTICS"

# Deterministic aliases are utilities, not business truth.
LOCATION_ALIASES = {
    "CHHATARPUR": ["CHHATARPUR", "CHATTARPUR"],
    "GHITORNI": ["GHITORNI"],
    "SULTANPUR": ["SULTANPUR"],
    "DERA MANDI": ["DERA MANDI", "DERAMANDI"],
    "BALIYAWAS": ["BALIYAWAS", "BALIAWAS"],
    "BANDHWARI": ["BANDHWARI", "BANDWARI"],
    "SAKET": ["SAKET"],
    "MALVIYA NAGAR": ["MALVIYA NAGAR"],
    "HAUZ KHAS": ["HAUZ KHAS"],
    "GREEN PARK": ["GREEN PARK"],
    "GREATER KAILASH 1": ["GREATER KAILASH 1", "GK 1", "GK-1", "GK1"],
    "GREATER KAILASH 2": ["GREATER KAILASH 2", "GK 2", "GK-2", "GK2"],
    "CR PARK": ["CR PARK", "C R PARK", "CHITTARANJAN PARK"],
    "KALKAJI": ["KALKAJI"],
    "NEHRU PLACE": ["NEHRU PLACE"],
    "EAST OF KAILASH": ["EAST OF KAILASH"],
    "KAILASH COLONY": ["KAILASH COLONY"],
    "DEFENCE COLONY": ["DEFENCE COLONY"],
    "SOUTH EXTENSION": ["SOUTH EXTENSION", "SOUTH EX"],
    "VASANT KUNJ": ["VASANT KUNJ"],
    "VASANT VIHAR": ["VASANT VIHAR"],
    "PANCHSHEEL PARK": ["PANCHSHEEL PARK"],
    "SAFDARJUNG ENCLAVE": ["SAFDARJUNG ENCLAVE", "SAFDARJUNG"],
    "OKHLA": ["OKHLA"],
    "JASOLA": ["JASOLA"],
    "ADCHINI": ["ADCHINI"],
    "MEHRAULI": ["MEHRAULI"],
    "CONNAUGHT PLACE": ["CONNAUGHT PLACE", "CP"],
    "RAJOURI GARDEN": ["RAJOURI GARDEN"],
    "PITAMPURA": ["PITAMPURA"],
    "ROHINI": ["ROHINI"],
    "DWARKA": ["DWARKA"],
    "NOIDA": ["NOIDA"],
    "GREATER NOIDA": ["GREATER NOIDA", "GR NOIDA"],
    "GURUGRAM": ["GURUGRAM", "GURGAON"],
    "DLF PHASE 1": ["DLF PHASE 1", "DLF PHASE-I", "DLF 1", "DLF PH 1", "DLF PH-1"],
    "DLF PHASE 2": ["DLF PHASE 2", "DLF PHASE-II", "DLF 2", "DLF PH 2", "DLF PH-2"],
    "DLF PHASE 3": ["DLF PHASE 3", "DLF PHASE-III", "DLF 3", "DLF PH 3", "DLF PH-3"],
    "DLF PHASE 4": ["DLF PHASE 4", "DLF PHASE-IV", "DLF 4", "DLF PH 4", "DLF PH-4"],
    "DLF PHASE 5": ["DLF PHASE 5", "DLF PHASE-V", "DLF 5", "DLF PH 5", "DLF PH-5"],
    "SUSHANT LOK 1": ["SUSHANT LOK 1", "SUSHANT LOK"],
    "SOUTH CITY 1": ["SOUTH CITY 1"],
    "GREENWOOD CITY": ["GREENWOOD CITY", "GREEN WOOD CITY"],
    "SIOLIM": ["SIOLIM"],
    "MAJORDA": ["MAJORDA", "MAJORDA BEACH"],
    "BETALBATIM": ["BETALBATIM"],
    "COLVA": ["COLVA", "COLVA BEACH"],
    "UTORDA": ["UTORDA", "UTORDA BEACH"],
    "BENAULIM": ["BENAULIM", "BENAULIM BEACH"],
    "ASSAGAO": ["ASSAGAO"],
    "VAGATOR": ["VAGATOR"],
    "ANJUNA": ["ANJUNA"],
    "PARRA": ["PARRA"],
    "SALIGAO": ["SALIGAO"],
    "MORJIM": ["MORJIM"],
    "ASHWEM": ["ASHWEM", "ASHVEM"],
    "MANDREM": ["MANDREM"],
    "ARAMBOL": ["ARAMBOL"],
    "CANDOLIM": ["CANDOLIM"],
    "CALANGUTE": ["CALANGUTE"],
    "BAGA": ["BAGA"],
    "ARPORA": ["ARPORA"],
    "MAPUSA": ["MAPUSA"],
    "PORVORIM": ["PORVORIM"],
    "PANAJI": ["PANAJI", "PANJIM"],
    "JUHU": ["JUHU", "JVPD"],
    "BANDRA WEST": ["BANDRA WEST"],
    "KHAR WEST": ["KHAR WEST"],
}

ASSET_PATTERNS = [
    ("FARMHOUSE", "HOSPITALITY_REAL_ESTATE", [r"\bFARM\s*HOUSE\b", r"\bFARMHOUSE\b"]),
    ("BANQUET", "HOSPITALITY_REAL_ESTATE", [r"\bBANQUET\b", r"\bMARRIAGE\s+HALL\b", r"\bWEDDING\s+VENUE\b"]),
    ("HOTEL", "HOSPITALITY_REAL_ESTATE", [r"\bHOTEL\b", r"\bGUEST\s*HOUSE\b", r"\bRESORT\b"]),
    ("RESTAURANT", "RETAIL_FNB", [r"\bRESTAURANT\b", r"\bCAFE\b", r"\bLOUNGE\b", r"\bCLUB\b"]),
    ("OFFICE", "OFFICE", [r"\bOFFICE\b", r"\bCORPORATE\s+OFFICE\b", r"\bCOWORK(?:ING)?\b", r"\bCO[- ]?WORKING\b"]),
    ("WAREHOUSE", "INDUSTRIAL", [r"\bWAREHOUSE\b", r"\bGODOWN\b", r"\bLOGISTICS\b"]),
    ("RETAIL_UNIT", "RETAIL_FNB", [r"\bSHOP\b", r"\bSHOWROOM\b", r"\bRETAIL\b", r"\bHIGH\s+STREET\b"]),
    ("VILLA", "RESIDENTIAL", [r"\bVILLA\b", r"\bBUNGALOW\b", r"\bKOTHI\b", r"\bINDEPENDENT\s+HOUSE\b"]),
    ("BUILDER_FLOOR", "RESIDENTIAL", [r"\bBUILDER\s+FLOOR\b", r"\bINDEPENDENT\s+FLOOR\b"]),
    ("APARTMENT", "RESIDENTIAL", [r"\bAPARTMENT\b", r"\bFLAT\b", r"\b\d+(?:\.\d+)?\s*BHK\b"]),
    ("LAND", "LAND", [r"\bPLOT\b", r"\bLAND\b"]),
]

USE_PATTERNS = [
    ("AIRBNB_SHORT_STAY", [r"\bAIRBNB\b", r"\bSHORT[- ]?STAY\b", r"\bVACATION\s+RENTAL\b"]),
    ("BANQUET_WEDDING", [r"\bBANQUET\b", r"\bWEDDING\b", r"\bMARRIAGE\b"]),
    ("RESTAURANT_FNB", [r"\bRESTAURANT\b", r"\bCAFE\b", r"\bLOUNGE\b", r"\bF&B\b", r"\bFNB\b", r"\bFOOD\s+AND\s+BEVERAGE\b"]),
    ("HOTEL_HOSPITALITY", [r"\bHOTEL\b", r"\bGUEST\s*HOUSE\b", r"\bRESORT\b", r"\bHOSPITALITY\b"]),
    ("OFFICE_USE", [r"\bOFFICE\b", r"\bCORPORATE\b", r"\bCOWORK(?:ING)?\b"]),
    ("RETAIL_USE", [r"\bRETAIL\b", r"\bSHOWROOM\b", r"\bSHOP\b"]),
    ("WAREHOUSE_LOGISTICS", [r"\bWAREHOUSE\b", r"\bGODOWN\b", r"\bLOGISTICS\b"]),
    ("STAFF_ACCOMMODATION", [r"\bFOR\s+(?:CONSTRUCTION\s+COMPANY\s+)?ENGINEERS\b", r"\b(?:STAFF|EMPLOYEE|ENGINEER|ENGINEERS|WORKFORCE)\s+(?:ACCOMMODATION|STAY)\b"]),
    ("RESIDENTIAL_USE", [r"\bRESIDENTIAL\b", r"\bFAMILY\b"]),
]


def semantic_norm(value: Any) -> str:
    s = str(value or "").upper()
    s = s.replace("–", "-").replace("—", "-").replace("−", "-")
    s = re.sub(r"[^A-Z0-9₹./+\- ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _evidence(raw: str, pattern: str) -> Optional[str]:
    m = re.search(pattern, str(raw or ""), re.I)
    return m.group(0) if m else None


def classify_intent(raw: str) -> Dict[str, Any]:
    n = semantic_norm(raw)
    demand_hits = []
    supply_hits = []

    demand_patterns = [
        r"\bREQUIREMENT\b", r"\bREQUIRED\b", r"\bNEED(?:ED)?\b",
        r"\bLOOKING\s+FOR\b", r"\bLOOKING\s+TO\b", r"\bWANTED\b",
        r"\bSEEKING\b", r"\bCLIENT\s+(?:IS\s+)?LOOKING\b",
    ]
    supply_patterns = [
        r"\bPROPERTY\s+AVAILABLE\b", r"\bAVAILABLE\s+FOR\s+(?:SALE|RENT|LEASE)\b",
        r"\bOWNER\s+DIRECT\b", r"\bDIRECT\s+OWNER\b", r"\bTOTAL\s+DEAL\s+VALUE\b",
    ]

    for p in demand_patterns:
        if re.search(p, n):
            demand_hits.append(p)
    for p in supply_patterns:
        if re.search(p, n):
            supply_hits.append(p)

    structured_listing = bool(
        re.search(r"\b(?:₹|RS|INR)\s*\d[\d,.]*\s*(?:PER\s+SQ|PSF|/\s*SQ)", n)
        and re.search(r"\b(?:TOTAL\s+DEAL\s+VALUE|TOTAL\s+VALUE|ASKING)\b", n)
    )

    if structured_listing:
        supply_hits.append("STRUCTURED_RATE_PLUS_VALUE")

    if demand_hits and not supply_hits:
        return {"role": "REQUIREMENT", "confidence": 0.99, "evidence": demand_hits}
    if supply_hits and not demand_hits:
        return {"role": "SUPPLY", "confidence": 0.98, "evidence": supply_hits}
    if demand_hits and supply_hits:
        return {"role": "AMBIGUOUS", "confidence": 0.55, "evidence": demand_hits + supply_hits}
    return {"role": "AMBIGUOUS", "confidence": 0.25, "evidence": []}


def extract_asset(raw: str) -> Dict[str, Any]:
    # Preserve every explicit acceptable asset, while keeping legacy primary fields.
    matches = []
    seen = set()
    for asset, family, patterns in ASSET_PATTERNS:
        evidence = []
        for p in patterns:
            for m in re.finditer(p, str(raw or ""), re.I):
                ev = m.group(0)
                if ev not in evidence:
                    evidence.append(ev)
        if evidence and asset not in seen:
            seen.add(asset)
            matches.append({"asset":asset,"family":family,"status":"EXPLICIT","confidence":0.98,"evidence":evidence})
    if not matches:
        return {"primary_asset":"UNKNOWN","asset_family":"UNKNOWN","acceptable_assets":[],"status":"UNKNOWN","confidence":0.0,"evidence":[]}

    n = semantic_norm(raw)
    explicit_flat = bool(re.search(r"\b(?:FLAT|APARTMENT)\b", n))
    explicit_other = bool(re.search(r"\b(?:VILLA|BUNGALOW|KOTHI|INDEPENDENT\s+HOUSE|BUILDER\s+FLOOR|INDEPENDENT\s+FLOOR)\b", n))
    if explicit_other and not explicit_flat:
        matches = [x for x in matches if not (x["asset"]=="APARTMENT" and all("BHK" in semantic_norm(ev) for ev in x["evidence"]))]
    primary = matches[0]
    if explicit_flat:
        primary = next((x for x in matches if x["asset"]=="APARTMENT"), primary)
    return {"primary_asset":primary["asset"],"asset_family":primary["family"],"acceptable_assets":matches,"status":"EXPLICIT","confidence":0.98,"evidence":[ev for x in matches for ev in x["evidence"]]}


def extract_use(raw: str) -> Dict[str, Any]:
    for use, patterns in USE_PATTERNS:
        for p in patterns:
            ev = _evidence(raw, p)
            if ev:
                return {
                    "primary_use": use,
                    "status": "EXPLICIT",
                    "confidence": 0.98,
                    "evidence": [ev],
                }
    return {
        "primary_use": None,
        "status": "UNKNOWN",
        "confidence": 0.0,
        "evidence": [],
    }


def extract_transaction(raw: str) -> Dict[str, Any]:
    n = semantic_norm(raw)

    rent_patterns = [
        r"\bFOR\s+RENT\b", r"\bON\s+RENT\b", r"\bFOR\s+LEASE\b",
        r"\bON\s+LEASE\b", r"\bLEASE\s+REQUIRED\b", r"\bRENT\s+REQUIRED\b",
        r"\bLOOKING\s+TO\s+(?:RENT|LEASE)\b",
        r"\bRENTAL\s+REQUIREMENT\b",
        r"\bRENTAL\s+REQUIRED\b",
        r"\bRENTAL\s+NEED(?:ED)?\b",
        r"\b(?:LONG[- ]?TERM\s+)?RENTAL\b",
        r"\bREQUIREMENT\s+FOR\s+RENT(?:AL)?\b",
    ]
    sale_patterns = [
        r"\bFOR\s+SALE\b", r"\bTO\s+BUY\b", r"\bLOOKING\s+TO\s+BUY\b",
        r"\bPURCHASE\b", r"\bOUTRIGHT\b", r"\bBUYER\s+REQUIREMENT\b",
    ]

    rent_ev = [p for p in rent_patterns if re.search(p, n)]
    sale_ev = [p for p in sale_patterns if re.search(p, n)]

    if rent_ev and not sale_ev:
        return {"value": "RENT", "status": "EXPLICIT", "confidence": 1.0, "evidence": rent_ev, "human_confirmation_required": False}
    if sale_ev and not rent_ev:
        return {"value": "SALE", "status": "EXPLICIT", "confidence": 1.0, "evidence": sale_ev, "human_confirmation_required": False}
    if rent_ev and sale_ev:
        return {"value": None, "status": "CONFLICTING", "confidence": 0.0, "evidence": rent_ev + sale_ev, "human_confirmation_required": True}

    purchase_cues = []
    for p in [
        r"\bIMMEDIATE\s+PAYMENT\b",
        r"\bCLEAR\s+TITLE\b",
        r"\bREGISTRY\b",
        r"\bREGISTRATION\b",
        r"\bCHEQUE\b",
    ]:
        if re.search(p, n):
            purchase_cues.append(p)

    if len(purchase_cues) >= 2:
        return {
            "value": "SALE",
            "status": "INFERRED_HIGH",
            "confidence": 0.90,
            "evidence": purchase_cues,
            "human_confirmation_required": True,
        }

    return {
        "value": None,
        "status": "UNKNOWN",
        "confidence": 0.0,
        "evidence": [],
        "human_confirmation_required": True,
    }


def _canon_location(value: str) -> str:
    n = semantic_norm(value)
    for canon, aliases in LOCATION_ALIASES.items():
        for alias in aliases:
            if semantic_norm(alias) == n:
                return canon
    return n


def _labelled_location_segment(raw: str) -> Optional[str]:
    text = str(raw or "")
    m = re.search(
        r"(?is)\b(?:PREFERRED\s+LOCATIONS?|LOCATIONS?)\s*:\s*(.+?)(?="
        r"(?:[🏠👷👥📅🛋️💰📍🤝]|\b(?:LAND\s+REQUIREMENT|AREA\s+REQUIREMENT|BUDGET|URGENT|GENUINE|"
        r"TRANSACTION|PROPERTY\s+TYPE|USE|REQUIREMENT|FURNISHED|UNFURNISHED|LONG[- ]?TERM|SIDE[- ]?BY[- ]?SIDE|DIRECT\s+DEAL)\b\s*:?)|$)",
        text,
    )
    return m.group(1).strip() if m else None


def extract_locations(raw: str) -> List[Dict[str, Any]]:
    n = semantic_norm(raw)
    labelled = _labelled_location_segment(raw)
    found: List[Tuple[str, str, str]] = []

    if labelled:
        pieces = re.split(r"\s*(?:\||,|;|/|\bAND\b)\s*", labelled, flags=re.I)
        for piece in pieces:
            cleaned = re.sub(r"^[^\w]+|[^\w -]+$", "", piece).strip()
            cleaned = re.sub(r"\b(?:PREFERRED|LOCATION|LOCATIONS)\b", "", cleaned, flags=re.I).strip()
            cleaned = re.sub(r"^(?:NEAR|AROUND|CLOSE\s+TO|VICINITY\s+OF)\s+", "", cleaned, flags=re.I).strip()
            if not cleaned or len(cleaned) < 2:
                continue
            canon = _canon_location(cleaned)
            if canon and canon not in {"PREFERRED", "LOCATIONS", "LOCATION"}:
                found.append((canon, "PREFERRED", cleaned))

    # Known aliases anywhere in text. Longest alias first avoids GK/GK1 collisions.
    alias_rows = []
    for canon, aliases in LOCATION_ALIASES.items():
        for alias in aliases:
            alias_rows.append((len(alias), canon, alias))
    alias_rows.sort(reverse=True)

    for _len, canon, alias in alias_rows:
        a = semantic_norm(alias)
        if re.search(rf"(?<![A-Z0-9]){re.escape(a)}(?![A-Z0-9])", n):
            # Explicit "only" has hard semantics for that location.
            hard = bool(
                re.search(rf"(?<![A-Z0-9]){re.escape(a)}\s+ONLY(?![A-Z0-9])", n)
                or re.search(rf"\bONLY\s+(?:IN\s+)?{re.escape(a)}(?![A-Z0-9])", n)
            )
            pref = bool(
                re.search(rf"\bPREFER(?:RED|ABLY)?\b.{{0,80}}{re.escape(a)}", n)
                or re.search(rf"{re.escape(a)}.{{0,30}}\bPREFER(?:RED|ABLY)?\b", n)
            )
            constraint = "HARD" if hard else ("PREFERRED" if pref or labelled else "ACCEPTABLE")
            found.append((canon, constraint, alias))

    # Explicit exclusions.
    for canon, aliases in LOCATION_ALIASES.items():
        for alias in aliases:
            a = semantic_norm(alias)
            if re.search(rf"\b(?:NO|EXCLUDE|EXCLUDING|NOT\s+IN)\s+{re.escape(a)}\b", n):
                found.append((canon, "EXCLUDED", alias))

    priority = {"EXCLUDED": 5, "HARD": 4, "PREFERRED": 3, "ACCEPTABLE": 2, "AI_ALTERNATIVE": 1}
    merged: Dict[str, Dict[str, Any]] = {}
    for canon, constraint, ev in found:
        old = merged.get(canon)
        if old is None or priority[constraint] > priority[old["constraint"]]:
            merged[canon] = {
                "name": canon,
                "kind": "LOCALITY",
                "constraint": constraint,
                "status": "EXPLICIT",
                "confidence": 0.99,
                "evidence": [ev],
            }
        elif ev not in old["evidence"]:
            old["evidence"].append(ev)

    return list(merged.values())


def extract_area(raw: str) -> Dict[str, Any]:
    n = semantic_norm(raw)

    unit_patterns = [
        ("ACRE", 43560.0, r"(?:ACRE|ACRES)"),
        ("SQYD", 9.0, r"(?:SQ\s*YD|SQYD|SQ\s*YARD|SQ\s*YARDS|YDS|YARD|YARDS)"),
        ("SQFT", 1.0, r"(?:SQ\s*FT|SQFT|SFT)"),
    ]

    for unit, factor, up in unit_patterns:
        m = re.search(
            rf"\b(?:MINIMUM|MIN|AT\s+LEAST)?\s*(\d+(?:\.\d+)?)\s*(?:-|TO)\s*(\d+(?:\.\d+)?)\s*{up}\b",
            n,
        )
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
            return {
                "dimension": "LAND_AREA" if unit == "ACRE" else "AREA",
                "min_sqft": lo * factor,
                "max_sqft": hi * factor,
                "original_min": lo,
                "original_max": hi,
                "original_unit": unit,
                "constraint": "HARD_MIN_PREFERRED_RANGE" if re.search(r"\b(?:MINIMUM|MIN|AT\s+LEAST)\b", m.group(0)) else "RANGE",
                "status": "EXPLICIT",
                "confidence": 1.0,
                "evidence": [m.group(0)],
            }

        m = re.search(rf"\b(MINIMUM|MIN|AT\s+LEAST)\s*(\d+(?:\.\d+)?)\s*{up}\b", n)
        if m:
            val = float(m.group(2))
            return {
                "dimension": "LAND_AREA" if unit == "ACRE" else "AREA",
                "min_sqft": val * factor,
                "max_sqft": None,
                "original_min": val,
                "original_max": None,
                "original_unit": unit,
                "constraint": "HARD_MIN",
                "status": "EXPLICIT",
                "confidence": 1.0,
                "evidence": [m.group(0)],
            }

        m = re.search(rf"\b(\d+(?:\.\d+)?)\s*{up}\b", n)
        if m:
            val = float(m.group(1))
            return {
                "dimension": "LAND_AREA" if unit == "ACRE" else "AREA",
                "min_sqft": val * factor,
                "max_sqft": val * factor,
                "original_min": val,
                "original_max": val,
                "original_unit": unit,
                "constraint": "EXACT_STATED",
                "status": "EXPLICIT",
                "confidence": 1.0,
                "evidence": [m.group(0)],
            }

    return {
        "dimension": None,
        "min_sqft": None,
        "max_sqft": None,
        "original_min": None,
        "original_max": None,
        "original_unit": None,
        "constraint": None,
        "status": "UNKNOWN",
        "confidence": 0.0,
        "evidence": [],
    }


def extract_budget(raw: str) -> Dict[str, Any]:
    n = semantic_norm(raw)
    if re.search(r"\b(?:AS\s+PER\s+MARKET|MARKET\s+RATE|SUITABLE\s+OPTIONS)\b", n):
        return {
            "min": None,
            "max": None,
            "status": "NOT_SPECIFIED",
            "raw": "AS PER MARKET",
            "confidence": 1.0,
            "evidence": ["AS PER MARKET / SUITABLE OPTIONS"],
        }

    m = re.search(r"\b(?:BUDGET|UPTO|UP\s+TO|MAX(?:IMUM)?)?\s*(?:RS|INR|₹)?\s*(\d+(?:\.\d+)?)\s*(CRORE|CRORES|CR|LAKH|LAKHS|LAC)\b", n)
    if m:
        value = float(m.group(1))
        unit = m.group(2)
        amount = value * (10_000_000 if unit in {"CRORE", "CRORES", "CR"} else 100_000)
        return {
            "min": None,
            "max": amount,
            "status": "EXPLICIT",
            "raw": m.group(0),
            "confidence": 0.99,
            "evidence": [m.group(0)],
        }

    return {
        "min": None,
        "max": None,
        "status": "UNKNOWN",
        "raw": None,
        "confidence": 0.0,
        "evidence": [],
    }


def extract_project_block(raw: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    n = semantic_norm(raw)

    block = None
    m = re.search(r"\b([A-Z])(?:\s*-\s*|\s+)BLOCK\b", n)
    if m:
        block = f"BLOCK {m.group(1)}"
    else:
        m = re.search(r"\bBLOCK(?:\s*-\s*|\s+)([A-Z0-9]+)\b", n)
        if m and m.group(1) not in {"GK", "GREATER", "SECTOR", "SEC"}:
            block = f"BLOCK {m.group(1)}"

    block_constraint = None
    if block:
        token = re.escape(block.split()[-1])
        block_form = rf"(?:BLOCK(?:\s*-\s*|\s+){token}|{token}(?:\s*-\s*|\s+)BLOCK)"
        if re.search(rf"\b{block_form}\s+ONLY\b|\bONLY\s+(?:IN\s+)?{block_form}\b", n):
            block_constraint = "HARD"
        elif re.search(rf"\bPREFER(?:RED|ABLY)?\b.{{0,50}}{block_form}\b|\b{block_form}.{{0,30}}\bPREFER(?:RED|ABLY)?\b", n):
            block_constraint = "PREFERRED"
        else:
            block_constraint = "ACCEPTABLE"

    project = None
    m = re.search(r"\b(?:PLOT|LAND|PROPERTY|SPACE)\s+(?:IN|AT)\s+(.+?)\s+(?=SECTOR\s+\d+\b)", n)
    if m:
        project = m.group(1).strip()

    return (
        {
            "name": project,
            "constraint": "HARD" if project and block_constraint == "HARD" else ("ACCEPTABLE" if project else None),
            "status": "EXPLICIT" if project else "UNKNOWN",
            "confidence": 0.95 if project else 0.0,
            "evidence": [project] if project else [],
        },
        {
            "name": block,
            "constraint": block_constraint,
            "status": "EXPLICIT" if block else "UNKNOWN",
            "confidence": 0.99 if block else 0.0,
            "evidence": [block] if block else [],
        },
    )


def analyze(raw: str, source: str = "UNKNOWN") -> Dict[str, Any]:
    intent = classify_intent(raw)
    asset = extract_asset(raw)
    use = extract_use(raw)
    transaction = extract_transaction(raw)
    locations = extract_locations(raw)
    area = extract_area(raw)
    budget = extract_budget(raw)
    project, block = extract_project_block(raw)
    n = semantic_norm(raw)

    urgency = {
        "level": "IMMEDIATE" if re.search(r"\b(?:URGENT|IMMEDIATE|IMMEDIATELY)\b", n) else "NORMAL",
        "status": "EXPLICIT" if re.search(r"\b(?:URGENT|IMMEDIATE|IMMEDIATELY)\b", n) else "UNKNOWN",
    }
    verification = {
        "confirmed_inventory_required": bool(re.search(r"\bCONFIRMED\b|\bVERIFY(?:IED)?\b", n)),
        "human_verification_required_before_client_share": True,
    }

    bhk_values = sorted({int(x) for x in re.findall(r"\b(\d+)\s*BHK\b", n)})
    furnishing = []
    if re.search(r"\bFURNISHED\b", n): furnishing.append("FURNISHED")
    if re.search(r"\bUNFURNISHED\b", n): furnishing.append("UNFURNISHED")
    mo = re.search(r"\b(\d+)\s*(?:-|TO)\s*(\d+)\s+(?:MALE\s+)?(?:ENGINEERS?|PEOPLE|PERSONS?|STAFF|EMPLOYEES?)\b", n)
    occupants = {"min":int(mo.group(1)),"max":int(mo.group(2)),"status":"EXPLICIT"} if mo else None
    mt = re.search(r"\b(\d+)\s*(?:-|TO)\s*(\d+)\s+YEARS?\b", n)
    tenure = {"min_years":int(mt.group(1)),"max_years":int(mt.group(2)),"status":"EXPLICIT"} if mt else None
    requirement_details = {"bedrooms_bhk":bhk_values,"furnishing_options":furnishing,"occupants":occupants,"tenure":tenure,
        "direct_deal_preferred":bool(re.search(r"\bDIRECT\s+DEAL\s+PREFERRED\b",n)),
        "side_by_side_preferred":bool(re.search(r"\bSIDE[- ]?BY[- ]?SIDE\b",n))}

    hard_constraints = [f"LOCATION:{x['name']}" for x in locations if x["constraint"] == "HARD"]
    hard_constraints += [f"EXCLUDE_LOCATION:{x['name']}" for x in locations if x["constraint"] == "EXCLUDED"]
    if block.get("constraint") == "HARD" and block.get("name"):
        hard_constraints.append(f"BLOCK:{block['name']}")
    if project.get("constraint") == "HARD" and project.get("name"):
        hard_constraints.append(f"PROJECT:{project['name']}")
    if area.get("constraint") in {"HARD_MIN", "HARD_MIN_PREFERRED_RANGE"} and area.get("min_sqft") is not None:
        hard_constraints.append(f"AREA_MIN_SQFT:{area['min_sqft']}")

    preferences = [f"LOCATION:{x['name']}" for x in locations if x["constraint"] == "PREFERRED"]
    if block.get("constraint") == "PREFERRED" and block.get("name"):
        preferences.append(f"BLOCK:{block['name']}")

    unknowns = []
    if transaction["value"] is None:
        unknowns.append("TRANSACTION")
    if asset["primary_asset"] == "UNKNOWN":
        unknowns.append("ASSET")
    if not locations:
        unknowns.append("LOCATION")
    if budget["status"] in {"UNKNOWN", "NOT_SPECIFIED"}:
        unknowns.append("BUDGET")

    readiness = "BLOCKED_NOT_REQUIREMENT"
    if intent["role"] == "REQUIREMENT":
        if asset["primary_asset"] == "UNKNOWN":
            readiness = "NEEDS_ASSET_CONFIRMATION"
        elif transaction["value"] is None:
            readiness = "READY_WITH_TRANSACTION_OPEN"
        else:
            readiness = "READY"

    obj = {
        "schema_version": "3.0",
        "brain_version": VERSION,
        "intent": intent,
        "asset": asset,
        "intended_use": use,
        "transaction": transaction,
        "locations": locations,
        "area": area,
        "budget": budget,
        "project": project,
        "block": block,
        "urgency": urgency,
        "verification": verification,
        "requirement_details": requirement_details,
        "hard_constraints": hard_constraints,
        "preferences": preferences,
        "unknowns": unknowns,
        "matching_readiness": readiness,
        "raw_text": raw,
        "provenance": {
            "source": source,
            "extraction_version": VERSION,
        },
        "field_confidence": {
            "intent": intent["confidence"],
            "asset": asset["confidence"],
            "intended_use": use["confidence"],
            "transaction": transaction["confidence"],
            "locations": min([x["confidence"] for x in locations], default=0.0),
            "area": area["confidence"],
            "budget": budget["confidence"],
            "project": project["confidence"],
            "block": block["confidence"],
        },
        "evidence": {
            "intent": intent.get("evidence", []),
            "asset": asset.get("evidence", []),
            "intended_use": use.get("evidence", []),
            "transaction": transaction.get("evidence", []),
            "locations": {x["name"]: x.get("evidence", []) for x in locations},
            "area": area.get("evidence", []),
            "budget": budget.get("evidence", []),
            "project": project.get("evidence", []),
            "block": block.get("evidence", []),
        },
    }

    return assert_valid_requirement(obj)
