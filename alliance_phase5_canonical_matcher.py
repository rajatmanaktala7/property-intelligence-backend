from __future__ import annotations

import os
import re
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import create_engine, text

VERSION = "5.2.0-LOCATION-PURITY-FULL-AUDIT"
LIVE_WA_GENERATION_FALLBACK = "159d9eab-5be5-5313-9af5-8f9913522087"

# Phase 5 rules:
# - Canonical/match-eligible inventory only.
# - Transaction, location, area and explicit property-use gates run before scoring.
# - Price participates only when explicitly comparable.
# - Price is excluded from identity/deduplication.
# - Exact-location matches are never mixed with alternatives.
# - Smart alternatives are returned only when the exact search has no usable result.
# - Unverified inventory may be retained for internal verification, but is never send-eligible.
# - Contacts are never selected by the matcher and never emitted in result payloads.
# - Read-only engine. No INSERT/UPDATE/DELETE/DDL.

LOCATION_ALIASES = {
    "SAKET": ["SAKET", "DISTRICT CENTRE SAKET", "SAKET DISTRICT CENTRE", "DLF AVENUE SAKET", "SELECT CITYWALK", "SELECT CITY WALK"],
    "MALVIYA NAGAR": ["MALVIYA NAGAR"],
    "HAUZ KHAS": ["HAUZ KHAS"],
    "GREEN PARK": ["GREEN PARK"],
    "GREATER KAILASH 1": ["GREATER KAILASH 1", "GREATER KAILASH-I", "GK 1", "GK-1", "GK1"],
    "GREATER KAILASH 2": ["GREATER KAILASH 2", "GREATER KAILASH-II", "GK 2", "GK-2", "GK2"],
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
    "SAFDARJUNG": ["SAFDARJUNG ENCLAVE", "SAFDARJUNG"],
    "OKHLA": ["OKHLA"],
    "JASOLA": ["JASOLA"],
    "ADCHINI": ["ADCHINI"],
    "MEHRAULI": ["MEHRAULI"],
    "CHHATARPUR": ["CHHATARPUR", "CHATTARPUR"],
    "CONNAUGHT PLACE": ["CONNAUGHT PLACE", "CONNAUGHT CIRCUS", "CP"],
    "RAJOURI GARDEN": ["RAJOURI GARDEN"],
    "PITAMPURA": ["PITAMPURA"],
    "ROHINI": ["ROHINI"],
    "DWARKA": ["DWARKA"],
    "NOIDA": ["NOIDA"],
    "GREATER NOIDA": ["GREATER NOIDA", "GR NOIDA"],
    "GURUGRAM": ["GURUGRAM", "GURGAON"],
    "DLF PHASE 1": ["DLF PHASE 1", "DLF PHASE-I", "DLF 1", "DLF PH 1", "DLF PH-1", "DLF PH1", "DLF PHASE1"],
    "DLF PHASE 2": ["DLF PHASE 2", "DLF PHASE-II", "DLF 2", "DLF PH 2", "DLF PH-2", "DLF PH2", "DLF PHASE2"],
    "DLF PHASE 3": ["DLF PHASE 3", "DLF PHASE-III", "DLF 3", "DLF PH 3", "DLF PH-3", "DLF PH3", "DLF PHASE3"],
    "DLF PHASE 4": ["DLF PHASE 4", "DLF PHASE-IV", "DLF 4", "DLF PH 4", "DLF PH-4", "DLF PH4", "DLF PHASE4"],
    "DLF PHASE 5": ["DLF PHASE 5", "DLF PHASE-V", "DLF 5", "DLF PH 5", "DLF PH-5", "DLF PH5", "DLF PHASE5"],
    "SUSHANT LOK 1": ["SUSHANT LOK 1", "SUSHANT LOK-I", "SUSHANT LOK", "SUSHANT LOK A B C BLOCK", "SUSHANT LOK 1 A B C BLOCK"],
    "SOUTH CITY 1": ["SOUTH CITY 1", "SOUTH CITY-I", "SOUTH CITY I"],
    "GREENWOOD CITY": ["GREENWOOD CITY", "GREEN WOOD CITY"],
    "SECTOR 27": ["SECTOR 27", "SEC 27", "SEC-27", "SEC27"],
    "SECTOR 28": ["SECTOR 28", "SEC 28", "SEC-28", "SEC28"],
    "SECTOR 43": ["SECTOR 43", "SEC 43", "SEC-43", "SEC43"],
    "SECTOR 45": ["SECTOR 45", "SEC 45", "SEC-45", "SEC45"],
    "SIOLIM": ["SIOLIM"],
    "ASSAGAO": ["ASSAGAO"],
    "VAGATOR": ["VAGATOR"],
    "ANJUNA": ["ANJUNA"],
    "PANAJI": ["PANAJI", "PANJIM"],
    "MIRAMAR": ["MIRAMAR"],
    "CARANZALEM": ["CARANZALEM"],
    "DONA PAULA": ["DONA PAULA", "DONAPAULA"],
    "PORVORIM": ["PORVORIM"],
    "SALIGAO": ["SALIGAO"],
    "ALDONA": ["ALDONA"],
    "MORJIM": ["MORJIM"],
    "ASHWEM": ["ASHWEM", "ASHVEM"],
    "MANDREM": ["MANDREM"],
    "ARAMBOL": ["ARAMBOL"],
    "CANDOLIM": ["CANDOLIM"],
    "CALANGUTE": ["CALANGUTE"],
    "BAGA": ["BAGA"],
    "ARPORA": ["ARPORA"],
    "MAPUSA": ["MAPUSA"],
    "THIVIM": ["THIVIM", "TIVIM"],
    "COLVALE": ["COLVALE", "COMVALE"],
    "PARRA": ["PARRA"],
    "MOIRA": ["MOIRA"],
    "REIS MAGOS": ["REIS MAGOS", "REISMAGOS"],
    "NERUL GOA": ["NERUL GOA", "NERUL, GOA"],
    "SANGOLDA": ["SANGOLDA"],
    "PILERNE": ["PILERNE"],
    "JUHU": ["JUHU", "JVPD", "GULMOHAR ROAD"],
    "BANDRA WEST": ["BANDRA WEST"],
    "KHAR WEST": ["KHAR WEST"],
}

NORTH_GOA_LOCALITIES = {
    "SIOLIM", "ASSAGAO", "VAGATOR", "ANJUNA", "MORJIM", "ASHWEM",
    "MANDREM", "ARAMBOL", "CANDOLIM", "CALANGUTE", "BAGA", "ARPORA",
    "MAPUSA", "THIVIM", "COLVALE", "PORVORIM", "SALIGAO", "ALDONA", "PARRA", "MOIRA",
    "REIS MAGOS", "NERUL GOA", "SANGOLDA", "PILERNE",
}

SOUTH_DELHI_B_CATEGORY_LOCALITIES = {
    "DEFENCE COLONY",
    "GEETANJALI ENCLAVE",
    "GREATER KAILASH 1",
    "GREATER KAILASH 2",
    "GREATER KAILASH 3",
    "GREATER KAILASH 4",
    "GREEN PARK",
    "GREEN PARK EXTENSION",
    "GULMOHAR PARK",
    "HAMDARD NAGAR",
    "SAFDARJUNG DEVELOPMENT AREA",
    "SAFDARJUNG ENCLAVE",
    "SARVAPRIYA VIHAR",
    "SARVODAYA ENCLAVE",
}

SOUTH_DELHI_CORE_LOCALITIES = {
    "SAKET",
    "MALVIYA NAGAR",
    "HAUZ KHAS",
    "GREEN PARK",
    "GREATER KAILASH 1",
    "GREATER KAILASH 2",
    "GREATER KAILASH 3",
    "GREATER KAILASH 4",
    "CR PARK",
    "KALKAJI",
    "NEHRU PLACE",
    "EAST OF KAILASH",
    "KAILASH COLONY",
    "DEFENCE COLONY",
    "SOUTH EXTENSION",
    "VASANT KUNJ",
    "VASANT VIHAR",
    "PANCHSHEEL PARK",
    "SAFDARJUNG ENCLAVE",
    "SAFDARJUNG DEVELOPMENT AREA",
    "MEHRAULI",
    "CHHATARPUR",
    "GEETANJALI ENCLAVE",
    "GULMOHAR PARK",
    "SARVAPRIYA VIHAR",
    "SARVODAYA ENCLAVE",
}

EXTRA_LOCATION_ALIASES = {
    "GEETANJALI ENCLAVE": ["GEETANJALI ENCLAVE", "GITANJALI ENCLAVE"],
    "GREATER KAILASH 3": ["GREATER KAILASH 3", "GREATER KAILASH III", "GK 3", "GK-3", "GK3"],
    "GREATER KAILASH 4": ["GREATER KAILASH 4", "GREATER KAILASH IV", "GK 4", "GK-4", "GK4", "NRI COLONY"],
    "GREEN PARK EXTENSION": ["GREEN PARK EXTENSION", "GREEN PARK EXTN", "GREEN PARK EXT"],
    "GULMOHAR PARK": ["GULMOHAR PARK"],
    "HAMDARD NAGAR": ["HAMDARD NAGAR"],
    "SAFDARJUNG DEVELOPMENT AREA": ["SAFDARJUNG DEVELOPMENT AREA", "SAFDARJUNG DEV AREA", "SDA"],
    "SAFDARJUNG ENCLAVE": ["SAFDARJUNG ENCLAVE", "SAFDARJUNG"],
    "SARVAPRIYA VIHAR": ["SARVAPRIYA VIHAR", "SARYAPRIYA VIHAR"],
    "SARVODAYA ENCLAVE": ["SARVODAYA ENCLAVE"],
}


def location_aliases(canon: str) -> List[str]:
    out = []
    for source in (LOCATION_ALIASES, EXTRA_LOCATION_ALIASES):
        for value in source.get(canon, []):
            if value not in out:
                out.append(value)
    return out

CITY_ONLY = {
    "DELHI", "NEW DELHI", "GURUGRAM", "GURGAON", "NOIDA", "GREATER NOIDA",
    "FARIDABAD", "GOA", "MUMBAI", "BENGALURU", "BANGALORE", "HYDERABAD"
}

TRANSACTION_ALIASES = {
    "RENT": ["FOR RENT", "RENTAL", "RENT", "LEASE", "LEASING", "TO LET", "TOLET"],
    "SALE": ["FOR SALE", "SALE", "RESALE", "OUTRIGHT", "SELL", "PURCHASE", "BUY"],
}

FAMILY_WORDS = {
    "COMMERCIAL": ["COMMERCIAL", "OFFICE", "SHOP", "SHOWROOM", "RETAIL", "RESTAURANT", "CAFE", "LOUNGE", "BANQUET", "HOTEL", "GUEST HOUSE", "WAREHOUSE", "GODOWN"],
    "RESIDENTIAL": ["RESIDENTIAL", "APARTMENT", "FLAT", "BUILDER FLOOR", "INDEPENDENT FLOOR", "VILLA", "KOTHI", "BUNGALOW", "PENTHOUSE", "BHK"],
    "LAND": ["PLOT", "LAND", "FARMHOUSE", "FARM HOUSE", "ACRE"],
}

SUBTYPE_WORDS = {
    "OFFICE": ["OFFICE", "CORPORATE OFFICE", "BUSINESS CENTRE", "CO-WORKING", "COWORKING"],
    "RETAIL": ["SHOP", "SHOWROOM", "RETAIL", "HIGH STREET"],
    "RESTAURANT": ["RESTAURANT", "CAFE", "LOUNGE", "F&B", "FNB"],
    "BANQUET": ["BANQUET", "MARRIAGE HALL", "WEDDING"],
    "HOTEL": ["HOTEL", "GUEST HOUSE", "HOSPITALITY"],
    "WAREHOUSE": ["WAREHOUSE", "GODOWN", "INDUSTRIAL"],
    "APARTMENT": ["APARTMENT", "FLAT", "BHK"],
    "BUILDER FLOOR": ["BUILDER FLOOR", "INDEPENDENT FLOOR"],
    "VILLA": ["VILLA", "KOTHI", "BUNGALOW", "INDEPENDENT HOUSE"],
    "LAND": ["PLOT", "LAND", "FARMHOUSE", "FARM HOUSE"],
}

# Approved business heuristic, not an assertion of equal property value.
# Alternatives remain separate from exact matches.
APPROVED_EQUIVALENCE = {
    "RESTAURANT": {
        "SAKET": ["MALVIYA NAGAR", "HAUZ KHAS", "GREEN PARK", "GREATER KAILASH 1", "VASANT KUNJ"],
        "GREATER KAILASH 1": ["GREATER KAILASH 2", "KAILASH COLONY", "DEFENCE COLONY", "HAUZ KHAS"],
        "KALKAJI": ["NEHRU PLACE", "CR PARK", "EAST OF KAILASH", "GREATER KAILASH 1"],
    },
    "RETAIL": {
        "SAKET": ["VASANT KUNJ", "GREATER KAILASH 1", "MALVIYA NAGAR", "HAUZ KHAS"],
        "RAJOURI GARDEN": ["PITAMPURA", "ROHINI", "DWARKA"],
    },
    "OFFICE": {
        "SAKET": ["NEHRU PLACE", "JASOLA", "OKHLA", "MALVIYA NAGAR"],
        "NEHRU PLACE": ["JASOLA", "OKHLA", "KALKAJI"],
    },
    "RESIDENTIAL": {
        "SAKET": ["PANCHSHEEL PARK", "GREATER KAILASH 1", "GREATER KAILASH 2", "VASANT KUNJ"],
        "VASANT KUNJ": ["VASANT VIHAR", "CHHATARPUR", "MEHRAULI"],
        "SIOLIM": ["ASSAGAO", "VAGATOR", "ANJUNA"],
    },
    "VILLA": {
        "SIOLIM": ["ASSAGAO", "VAGATOR", "ANJUNA"],
    },
    "COMMERCIAL": {
        "DLF PHASE 1": ["DLF PHASE 2", "DLF PHASE 4", "SUSHANT LOK 1"],
        "DLF PHASE 2": ["DLF PHASE 1", "DLF PHASE 3", "DLF PHASE 4"],
    },
}

PHONE_RE = re.compile(
    r"(?<![A-Za-z0-9.])(?:\+?91[\s-]?)?[6-9]\d{9}(?![A-Za-z0-9.])"
)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)

def norm(v: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", str(v or "").upper())).strip()

def sanitize_text(v: Any) -> str:
    s = str(v or "")
    s = PHONE_RE.sub("[CONTACT HIDDEN]", s)
    s = EMAIL_RE.sub("[EMAIL HIDDEN]", s)
    return re.sub(r"\s+", " ", s).strip()


def text_contains_contact(v: Any) -> bool:
    if not isinstance(v, str):
        return False
    return bool(PHONE_RE.search(v) or EMAIL_RE.search(v))

def public_payload_contact_paths(value: Any, path: str = "$") -> List[str]:
    hits: List[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            hits.extend(public_payload_contact_paths(v, f"{path}.{k}"))
        return hits
    if isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            hits.extend(public_payload_contact_paths(v, f"{path}[{i}]"))
        return hits
    if text_contains_contact(value):
        hits.append(path)
    return hits

def db_url(raw: str) -> str:
    u = (raw or "").strip()
    if u.startswith("postgres://"):
        return u.replace("postgres://", "postgresql+psycopg://", 1)
    if u.startswith("postgresql://"):
        return u.replace("postgresql://", "postgresql+psycopg://", 1)
    return u

def create_main_engine():
    u = db_url(os.getenv("DATABASE_URL", ""))
    if not u:
        raise RuntimeError("DATABASE_URL not configured")
    return create_engine(u, pool_pre_ping=True, pool_recycle=300, connect_args={"connect_timeout": 5})

def table_exists(engine, table: str) -> bool:
    with engine.connect() as c:
        return bool(c.execute(text("""
            SELECT 1 FROM information_schema.tables
            WHERE table_schema='public' AND table_name=:t
        """), {"t": table}).first())

def table_columns(engine, table: str) -> set[str]:
    with engine.connect() as c:
        return {r[0] for r in c.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name=:t
        """), {"t": table}).all()}

def _select_available(engine, table: str, wanted: List[str], where: str = "", params: Optional[dict] = None, limit: int = 10000):
    cols = table_columns(engine, table)
    selected = [x for x in wanted if x in cols]
    if not selected:
        return []
    qcols = ", ".join('"' + x.replace('"', '') + '"' for x in selected)
    sql = f'SELECT {qcols} FROM "{table}" {where} LIMIT :lim'
    p = dict(params or {})
    p["lim"] = int(limit)
    with engine.connect() as c:
        return [dict(r) for r in c.execute(text(sql), p).mappings().all()]


def sanitize_public_payload(value):
    """
    Recursively remove phone numbers and email addresses from every
    user-visible matcher field without changing matching logic.
    """
    if isinstance(value, dict):
        return {
            k: sanitize_public_payload(v)
            for k, v in value.items()
        }

    if isinstance(value, list):
        return [
            sanitize_public_payload(v)
            for v in value
        ]

    if isinstance(value, tuple):
        return tuple(
            sanitize_public_payload(v)
            for v in value
        )

    if isinstance(value, str):
        return sanitize_text(value)

    return value

def canonical_locations(*vals: Any) -> List[str]:
    raw_text = " ".join(str(v or "") for v in vals)
    blob = norm(raw_text)
    if not blob:
        return []

    hits = []

    for alias_map in (LOCATION_ALIASES, EXTRA_LOCATION_ALIASES):
        for canon, aliases in alias_map.items():
            positions = []
            for a in aliases:
                aa = norm(a)
                if not aa:
                    continue
                m = re.search(
                    r"(?<![A-Z0-9])" + re.escape(aa) + r"(?![A-Z0-9])",
                    blob,
                )
                if m:
                    positions.append(m.start())
            if positions:
                hits.append((min(positions), canon))

    # Generic Gurgaon/Noida/Delhi sector intelligence.
    for m in re.finditer(
        r"(?<![A-Z0-9])(?:SEC|SECTOR)\s*-?\s*(\d{1,3}[A-Z]?)(?![A-Z0-9])",
        blob,
    ):
        hits.append((m.start(), "SECTOR " + m.group(1)))

    hits.sort(key=lambda x: x[0])

    out = []
    seen = set()
    for _, canon in hits:
        if canon not in seen:
            seen.add(canon)
            out.append(canon)

    return out

def canonical_location(*vals: Any) -> Optional[str]:
    locations = canonical_locations(*vals)
    return locations[0] if locations else None

INVALID_LOCATION_VALUES = {
    "UNKNOWN", "NA", "N A", "NONE", "DELHI NCR", "NCR",
    "COMMERCIAL", "RESIDENTIAL", "APARTMENT", "FLAT", "VILLA",
    "OFFICE", "RETAIL", "SHOP", "SHOWROOM", "RESTAURANT", "CAFE",
    "LOUNGE", "BANQUET", "HOTEL", "WAREHOUSE", "GODOWN", "LAND",
    "PLOT", "BUILDER FLOOR", "INDEPENDENT FLOOR", "PENTHOUSE",
    "FARMHOUSE", "FARM HOUSE",
}

def location_value_is_plausible(raw: Any) -> bool:
    cleaned = norm(raw)
    if not cleaned or cleaned in CITY_ONLY or cleaned in INVALID_LOCATION_VALUES:
        return False
    if re.fullmatch(r"\d+(?:\.\d+)?\s*BHK", cleaned):
        return False
    if re.fullmatch(r"(?:GROUND|LOWER GROUND|UPPER GROUND|\d+(?:ST|ND|RD|TH)?)\s*FLOOR", cleaned):
        return False
    if re.fullmatch(r"\d+(?:\.\d+)?\s*(?:SQ\s*FT|SQFT|SFT|SQ\s*M|SQM|SQ\s*YD|SQYD|YD|YDS|YARD|GAJ|ACRE|ACRES)", cleaned):
        return False
    if re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
        return False
    if re.search(r"\b(?:CR|CRORE|LAC|LAKH|LAKHS)\b", cleaned) and re.search(r"\d", cleaned):
        return False
    property_tokens = {"BHK","BEDROOM","BEDROOMS","APARTMENT","FLAT","VILLA","KOTHI","OFFICE","SHOP","RETAIL","COMMERCIAL","RESIDENTIAL","FLOOR","FURNISHED","UNFURNISHED","SEMIFURNISHED","SEMI","FURNISH","READY","SALE","RENT","LEASE"}
    words = set(cleaned.split())
    alpha_words = {w for w in words if not w.isdigit()}
    if alpha_words and alpha_words.issubset(property_tokens):
        return False
    return True

def candidate_location(raw: Any) -> Optional[str]:
    if raw in (None, ""):
        return None
    known = canonical_location(raw)
    if known:
        return known
    cleaned = norm(raw)
    if not location_value_is_plausible(cleaned):
        return None
    return cleaned

def canonical_transaction(*vals: Any) -> Optional[str]:
    blob = norm(" ".join(str(v or "") for v in vals))
    if not blob:
        return None
    sale = any(norm(x) in blob for x in TRANSACTION_ALIASES["SALE"])
    rent = any(norm(x) in blob for x in TRANSACTION_ALIASES["RENT"])
    if sale and rent:
        return None
    if sale:
        return "SALE"
    if rent:
        return "RENT"
    return None

def family_subtype(*vals: Any) -> Tuple[Optional[str], Optional[str]]:
    blob = norm(" ".join(str(v or "") for v in vals))
    subtype = None
    best = 0
    for s, words in SUBTYPE_WORDS.items():
        for w in words:
            ww = norm(w)
            if ww in blob and len(ww) > best:
                subtype, best = s, len(ww)
    fam = None
    fam_scores = {k: sum(1 for w in ws if norm(w) in blob) for k, ws in FAMILY_WORDS.items()}
    if fam_scores:
        winner = max(fam_scores, key=fam_scores.get)
        if fam_scores[winner] > 0:
            fam = winner
    if subtype in {"OFFICE", "RETAIL", "RESTAURANT", "BANQUET", "HOTEL", "WAREHOUSE"}:
        fam = "COMMERCIAL"
    elif subtype in {"APARTMENT", "BUILDER FLOOR", "VILLA"}:
        fam = "RESIDENTIAL"
    elif subtype == "LAND":
        fam = "LAND"
    return fam, subtype

def _area_factor(unit: Any) -> float:
    u = norm(unit)

    if any(x in u for x in ["SQ M", "SQM", "SQUARE M", "SQ MT", "SQMTR"]):
        return 10.7639104167

    if any(x in u for x in ["SQ YD", "SQYD", "YD", "YDS", "YARD", "GAJ"]):
        return 9.0

    if "ACRE" in u:
        return 43560.0

    return 1.0


def area_to_sqft(v: Any, unit: Any = None) -> Optional[float]:
    if v in (None, ""):
        return None

    if isinstance(v, (int, float)):
        num = float(v)
        factor = _area_factor(unit)
        return num * factor if num > 0 else None

    s = str(v).replace(",", "")
    m = re.search(r"(?i)(\d+(?:\.\d+)?)", s)
    if not m:
        return None

    num = float(m.group(1))
    if num <= 0:
        return None

    return num * _area_factor(unit or s)

def parse_requirement_area(raw: str) -> Tuple[Optional[float], Optional[float]]:
    s = str(raw or "").replace(",", "")

    # Common WhatsApp property-area forms:
    # 300Yd To 400Yd
    # 300-400 Sq Yd
    # 1680 Sq Ft
    # 500 sqm
    # 1-2 acres
    area_unit = (
        r"(?:sq\.?\s*ft|sqft|sft|square\s*feet|"
        r"sq\.?\s*m|sqm|sq\s*mt|sqmtr|square\s*met(?:er|re)s?|"
        r"sq\.?\s*yd|sqyd|yds?|yards?|gaj|acres?)"
    )

    range_match = re.search(
        rf"(?i)\b(\d+(?:\.\d+)?)\s*({area_unit})?\s*"
        rf"(?:-|to|–|—)\s*"
        rf"(\d+(?:\.\d+)?)\s*({area_unit})\b",
        s,
    )

    if range_match:
        n1 = float(range_match.group(1))
        u1 = range_match.group(2) or range_match.group(4)
        n2 = float(range_match.group(3))
        u2 = range_match.group(4) or u1

        a = n1 * _area_factor(u1)
        b = n2 * _area_factor(u2)
        return min(a, b), max(a, b)

    single = re.search(
        rf"(?i)\b(\d+(?:\.\d+)?)\s*({area_unit})\b",
        s,
    )

    if single:
        x = float(single.group(1)) * _area_factor(single.group(2))
        return x * 0.90, x * 1.10

    return None, None

def money_value(raw: Any) -> Optional[float]:
    if raw in (None, ""):
        return None

    if isinstance(raw, (int, float)):
        return float(raw)

    s = sanitize_text(raw).replace(",", "")
    m = re.search(
        r"(?i)(?:₹\s*)?(\d+(?:\.\d+)?)\s*"
        r"(cr|crore|crores|lac|lakh|lakhs|k)\b",
        s,
    )
    if not m:
        return None

    n = float(m.group(1))
    u = m.group(2).lower()

    if u.startswith("cr"):
        n *= 10_000_000
    elif u in {"lac", "lakh", "lakhs"}:
        n *= 100_000
    elif u == "k":
        n *= 1_000

    return n


def parse_budget(raw: str) -> Tuple[Optional[float], Optional[float]]:
    safe = sanitize_text(raw).replace(",", "")
    safe_norm = norm(safe)

    if any(token in safe_norm for token in (
        "BUDGET NO LIMIT",
        "NO BUDGET LIMIT",
        "UNLIMITED BUDGET",
        "BUDGET UNLIMITED",
        "BUDGET OPEN",
        "OPEN BUDGET",
    )):
        return None, None

    vals = []

    for m in re.finditer(
        r"(?i)(?:₹\s*)?(\d+(?:\.\d+)?)\s*"
        r"(cr|crore|crores|lac|lakh|lakhs|k)\b",
        safe,
    ):
        left = safe[max(0, m.start() - 28):m.start()]
        right = safe[m.end():m.end() + 28]
        context = norm(left + " " + right)

        # Exclude rate/deposit/commission figures from total budget.
        if any(
            token in context
            for token in (
                "PER SQ FT",
                "PER SQFT",
                "PSF",
                "SECURITY DEPOSIT",
                "DEPOSIT",
                "BROKERAGE",
                "COMMISSION",
                "CAM",
                "MAINTENANCE",
                "TOKEN AMOUNT",
            )
        ):
            continue

        x = money_value(m.group(0))
        if x is not None:
            vals.append(x)

    if len(vals) >= 2:
        return min(vals), max(vals)

    if len(vals) == 1:
        return None, vals[0]

    return None, None

def parse_requirement(raw: str) -> Dict[str, Any]:
    fam, sub = family_subtype(raw)
    amin, amax = parse_requirement_area(raw)
    bmin, bmax = parse_budget(raw)

    raw_text = str(raw or "")
    raw_norm = norm(raw_text)
    explicit_locations = canonical_locations(raw_text)

    south_delhi_b = (
        "SOUTH DELHI" in raw_norm
        and any(
            token in raw_norm
            for token in (
                "B CATEGORY",
                "B CATAEGORY",
                "CATEGORY B",
                "CAT B",
            )
        )
    )

    if explicit_locations:
        location = explicit_locations[0]
        primary_locations = explicit_locations
        location_scope = "LOCALITY"
        location_resolution = "STATIC_ALIAS"

    elif "NORTH GOA" in raw_norm:
        location = "NORTH GOA"
        primary_locations = ["NORTH GOA"]
        location_scope = "REGION"
        location_resolution = "REGION_RULE"

    elif south_delhi_b:
        location = "SOUTH DELHI B CATEGORY"
        primary_locations = sorted(SOUTH_DELHI_B_CATEGORY_LOCALITIES)
        location_scope = "REGION"
        location_resolution = "REGION_RULE"

    elif "SOUTH DELHI" in raw_norm and not explicit_locations:
        location = "SOUTH DELHI"
        primary_locations = sorted(SOUTH_DELHI_CORE_LOCALITIES)
        location_scope = "REGION"
        location_resolution = "REGION_RULE"

    else:
        location = explicit_locations[0] if explicit_locations else None
        primary_locations = explicit_locations
        location_scope = "LOCALITY"
        location_resolution = "STATIC_ALIAS" if explicit_locations else "UNRESOLVED"

    transaction = canonical_transaction(raw_text)
    transaction_source = "EXPLICIT" if transaction else None
    transaction_confidence = 1.0 if transaction else 0.0

    # Strong sale intent used in resale/property-mandate WhatsApp language.
    if not transaction:
        sale_signal = (
            ("CIRCLE RATE" in raw_norm and "MARKET PRICE" in raw_norm)
            or (
                "CIRCLE RATE" in raw_norm
                and ("CHEQUE" in raw_norm or "CHECK" in raw_norm)
            )
            or "TOTAL DEAL VALUE" in raw_norm
            or "REGISTRY VALUE" in raw_norm
        )

        rent_signal = any(
            token in raw_norm
            for token in (
                "MONTHLY RENT",
                "SECURITY DEPOSIT",
                "LOCK IN",
                "LOCKIN",
                "LEASE TERM",
            )
        )

        if sale_signal and not rent_signal:
            transaction = "SALE"
            transaction_source = "INFERRED_STRONG"
            transaction_confidence = 0.90

        elif rent_signal and not sale_signal:
            transaction = "RENT"
            transaction_source = "INFERRED_STRONG"
            transaction_confidence = 0.90

    acceptable_subtypes = []

    for subtype_name, words in SUBTYPE_WORDS.items():
        if any(
            norm(word) and norm(word) in raw_norm
            for word in words
        ):
            if subtype_name not in acceptable_subtypes:
                acceptable_subtypes.append(subtype_name)

    if sub and sub not in acceptable_subtypes:
        acceptable_subtypes.insert(0, sub)

    # Bare BHK can be apartment or builder floor.
    if fam == "RESIDENTIAL" and re.search(r"\b\d+(?:\.\d+)?\s*BHK\b", raw_norm):
        explicit_home_type = any(
            x in raw_norm
            for x in (
                "APARTMENT",
                "FLAT",
                "VILLA",
                "KOTHI",
                "BUNGALOW",
                "BUILDER FLOOR",
                "INDEPENDENT FLOOR",
            )
        )

        if not explicit_home_type:
            for x in ("APARTMENT", "BUILDER FLOOR"):
                if x not in acceptable_subtypes:
                    acceptable_subtypes.append(x)

    return {
        "raw": raw_text.strip(),
        "primary_locations": primary_locations,
        "location": location,
        "location_intent": (
            "AROUND"
            if re.search(
                r"\b(AROUND|NEARBY|SURROUNDING|VICINITY)\b",
                raw_norm,
            )
            else "EXACT"
        ),
        "location_scope": location_scope,
        "location_resolution": location_resolution,
        "transaction": transaction,
        "transaction_source": transaction_source,
        "transaction_confidence": transaction_confidence,
        "family": fam,
        "subtype": sub,
        "acceptable_subtypes": acceptable_subtypes,
        "area_min_sqft": amin,
        "area_max_sqft": amax,
        "budget_min": bmin,
        "budget_max": bmax,
    }


def enrich_requirement_with_inventory_locations(
    req: Dict[str, Any],
    raw: str,
    candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    # Static aliases remain preferred.
    if req.get("primary_locations"):
        return req

    raw_norm = norm(raw)
    if not raw_norm:
        return req

    hits = []
    seen = set()

    for p in candidates:
        loc = str(p.get("location") or "").strip()
        loc_norm = norm(loc)

        if not loc or not loc_norm or loc_norm in seen:
            continue

        if loc_norm in CITY_ONLY:
            continue

        # Avoid matching tiny ambiguous location tokens.
        if len(loc_norm) < 4:
            continue

        if re.search(
            r"(?<![A-Z0-9])" + re.escape(loc_norm) + r"(?![A-Z0-9])",
            raw_norm,
        ):
            seen.add(loc_norm)
            hits.append(loc)

    if hits:
        out = dict(req)
        out["primary_locations"] = hits
        out["location"] = hits[0]
        out["location_scope"] = "LOCALITY"
        out["location_resolution"] = "INVENTORY_DYNAMIC"
        return out

    return req

def _verified(v: Any) -> bool:
    n = norm(v)
    if not n:
        return False
    if "UNVERIFIED" in n or "NOT AVAILABLE" in n or "VERIFY LATER" in n:
        return False
    return n in {"VERIFIED", "AVAILABLE VERIFIED", "ACTIVE VERIFIED", "YES", "TRUE"} or n.startswith("VERIFIED ")

def _available(v: Any) -> bool:
    n = norm(v)
    return "NOT AVAILABLE" not in n and "UNAVAILABLE" not in n

def _freshness(v: Any) -> Tuple[int, str]:
    if not v:
        return 0, "FRESHNESS_UNKNOWN"
    try:
        dt = v if hasattr(v, "tzinfo") else datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        days = max(0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).days)
        if days <= 7:
            return 2, "FRESH_7D"
        if days <= 30:
            return 1, "FRESH_30D"
        return 0, "STALE_OR_OLD"
    except Exception:
        return 0, "FRESHNESS_UNKNOWN"

def load_pi_properties(engine, limit: int = 15000) -> List[Dict[str, Any]]:
    if not table_exists(engine, "pi_properties"):
        return []
    wanted = [
        "id", "property_name", "location", "city", "property_type", "rent_or_sale",
        "available_area_sqft", "remarks", "source", "created_at", "updated_at",
        "canonical_transaction", "canonical_city", "canonical_locality",
        "canonical_property_type", "canonical_area_value", "canonical_area_unit",
        "canonical_area_sqft", "canonical_sale_price_display", "canonical_sale_price_normalized",
        "canonical_monthly_rent_display", "canonical_monthly_rent_normalized",
        "price_match_status", "price_comparable", "data_quality_status", "match_eligible",
        "final_send_eligible", "verification_required_before_final_send",
        "availability_verification_status", "canonical_review_reasons",
        "canonical_normalizer_version", "canonical_normalized_at",
    ]
    rows = _select_available(engine, "pi_properties", wanted, limit=limit)
    out = []
    for d in rows:
        if not bool(d.get("match_eligible")):
            continue
        q = str(d.get("data_quality_status") or "")
        if q not in {"READY", "READY_LEGACY"}:
            continue
        tx = norm(d.get("canonical_transaction"))
        if tx not in {"SALE", "RENT"}:
            continue
        loc_raw = d.get("canonical_locality") or d.get("location")
        loc = candidate_location(loc_raw)
        if not loc:
            continue
        fam, sub = family_subtype(d.get("canonical_property_type"), d.get("property_type"), d.get("property_name"), d.get("remarks"))
        area_sqft = d.get("canonical_area_sqft")
        try:
            area_sqft = float(area_sqft) if area_sqft not in (None, "") else None
        except Exception:
            area_sqft = None
        comparable = bool(d.get("price_comparable"))
        if tx == "SALE":
            price = d.get("canonical_sale_price_normalized") if comparable else None
            price_text = d.get("canonical_sale_price_display")
        else:
            price = d.get("canonical_monthly_rent_normalized") if comparable else None
            price_text = d.get("canonical_monthly_rent_display")
        try:
            price = float(price) if price not in (None, "") else None
        except Exception:
            price = None
        ver = d.get("availability_verification_status")
        captured = d.get("canonical_normalized_at") or d.get("updated_at") or d.get("created_at")
        out.append({
            "source_bucket": "CANONICAL_DB",
            "source_table": "pi_properties",
            "record_id": str(d.get("id") or ""),
            "description": sanitize_text(d.get("property_name") or d.get("remarks") or loc_raw),
            "location": loc,
            "transaction": tx,
            "family": fam,
            "subtype": sub,
            "area_sqft": area_sqft,
            "area_unit_verified": bool(d.get("canonical_area_unit_verified", q == "READY")),
            "price": price,
            "price_text": sanitize_text(price_text),
            "price_comparable": comparable and price is not None,
            "quality": q,
            "verification": ver or "UNVERIFIED",
            "captured_on": captured,
            "source_name": sanitize_text(d.get("source") or "Property Database"),
            "review_reasons": d.get("canonical_review_reasons"),
        })
    return out

def _live_wa_generation(engine) -> str:
    try:
        import alliance_v44_whatsapp_property_master as v44
        return str(v44.LIVE_GENERATION_ID)
    except Exception:
        return LIVE_WA_GENERATION_FALLBACK

def _whatsapp_requirement_terms(req: Dict[str, Any]) -> List[str]:
    locations = list(req.get("primary_locations") or [])
    loc = req.get("location")
    if loc and loc not in locations:
        locations.append(loc)
    if not locations:
        return []
    if "NORTH GOA" in locations:
        out = ["NORTH GOA"]
        for canon in sorted(NORTH_GOA_LOCALITIES):
            out.append(canon)
            out.extend(LOCATION_ALIASES.get(canon, []))
    else:
        out = []
        for requested_loc in locations:
            out.append(requested_loc)
            out.extend(location_aliases(requested_loc))
    clean = []
    seen = set()
    for x in out:
        x = str(x or "").strip()
        k = norm(x)
        if x and k and k not in seen:
            seen.add(k)
            clean.append(x)
    return clean

def load_whatsapp_master_for_requirement(
    engine,
    req: Dict[str, Any],
    limit: int = 20000,
) -> List[Dict[str, Any]]:
    if not table_exists(engine, "pi_whatsapp_property_master"):
        return []

    wanted = [
        "record_id", "lead_type", "description", "area", "configuration_details",
        "price", "source", "captured_on", "verification", "furnishing", "floor",
        "generation_id",
    ]
    cols = table_columns(engine, "pi_whatsapp_property_master")
    selected = [x for x in wanted if x in cols]
    if not selected:
        return []

    qcols = ", ".join('"' + x + '"' for x in selected)
    where = []
    params: Dict[str, Any] = {"lim": int(max(1, min(limit, 50000)))}

    tx = norm(req.get("transaction"))
    if tx in {"SALE", "RENT"} and "lead_type" in cols:
        where.append("UPPER(COALESCE(lead_type,'')) = :tx")
        params["tx"] = tx

    blob_parts = []
    for c in ("description", "configuration_details", "source"):
        if c in cols:
            blob_parts.append(f"COALESCE({c},'')")
    blob = " || ' ' || ".join(blob_parts) if blob_parts else "''"

    terms = _whatsapp_requirement_terms(req)
    if terms:
        loc_parts = []
        for i, term in enumerate(terms):
            key = f"loc{i}"
            loc_parts.append(f"({blob}) ILIKE :{key}")
            params[key] = f"%{term}%"
        where.append("(" + " OR ".join(loc_parts) + ")")

    sql = f"SELECT {qcols} FROM pi_whatsapp_property_master"
    if where:
        sql += " WHERE " + " AND ".join(where)
    if "captured_on" in cols:
        sql += " ORDER BY captured_on DESC NULLS LAST"
    sql += " LIMIT :lim"

    with engine.connect() as c:
        rows = [dict(r) for r in c.execute(text(sql), params).mappings().all()]

    out = []
    for d in rows:
        txv = norm(d.get("lead_type"))
        if txv not in {"SALE", "RENT"}:
            continue

        desc = str(d.get("description") or "")
        cfg = str(d.get("configuration_details") or "")
        blob_text = (desc + " " + cfg).strip()

        loc = canonical_location(blob_text)
        if not loc and "NORTH GOA" in norm(blob_text):
            loc = "NORTH GOA"
        if not loc:
            cfg_loc = candidate_location(cfg)
            loc = cfg_loc if cfg_loc else None
        if not loc:
            continue

        fam, sub = family_subtype(cfg, desc)
        area_sqft = area_to_sqft(d.get("area"))

        ptext = d.get("price")
        price = money_value(ptext)
        comparable = price is not None and bool(
            re.search(r"(?i)\b(cr|crore|lac|lakh|lakhs|k)\b", str(ptext or ""))
        )

        ver = d.get("verification") or "UNVERIFIED"
        if not _available(ver):
            continue

        out.append({
            "source_bucket": "WHATSAPP_ALL_STORED",
            "source_table": "pi_whatsapp_property_master",
            "record_id": str(d.get("record_id") or ""),
            "description": sanitize_text(desc),
            "location": loc,
            "transaction": txv,
            "family": fam,
            "subtype": sub,
            "area_sqft": area_sqft,
            "area_unit_verified": bool(area_sqft is not None),
            "price": price if comparable else None,
            "price_text": sanitize_text(ptext),
            "price_comparable": comparable,
            "quality": "READY",
            "verification": ver,
            "captured_on": d.get("captured_on"),
            "source_name": sanitize_text(d.get("source") or "WhatsApp"),
            "review_reasons": None,
            "generation_id": str(d.get("generation_id") or "") if "generation_id" in d else None,
        })
    return out

def load_whatsapp_master(engine, limit: int = 10000) -> List[Dict[str, Any]]:
    return load_whatsapp_master_for_requirement(engine, {}, limit=limit)


def load_candidates(engine, pi_limit: int = 15000, wa_limit: int = 10000) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    pi = load_pi_properties(engine, pi_limit)
    wa = load_whatsapp_master(engine, wa_limit)
    rows = pi + wa
    return rows, {
        "pi_properties": len(pi),
        "pi_whatsapp_property_master": len(wa),
        "total_before_dedupe": len(rows),
    }

def identity_key(p: Dict[str, Any]) -> str:
    # Price deliberately excluded.
    area = p.get("area_sqft")
    area_bucket = str(int(round(float(area) / 25.0) * 25)) if isinstance(area, (int, float)) else ""
    return "|".join([
        norm(p.get("transaction")),
        norm(p.get("location")),
        norm(p.get("family")),
        norm(p.get("subtype")),
        area_bucket,
        norm(p.get("description"))[:120],
    ])

def dedupe_candidates(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Dict[str, Dict[str, Any]] = {}
    for p in rows:
        k = identity_key(p)
        if k not in seen:
            seen[k] = dict(p)
            seen[k]["provenance_count"] = 1
            continue
        x = seen[k]
        x["provenance_count"] = int(x.get("provenance_count") or 1) + 1
        # Prefer verified, then strict READY, then fresher record. Never merge contacts.
        rank_x = (1 if _verified(x.get("verification")) else 0, 1 if x.get("quality") == "READY" else 0)
        rank_p = (1 if _verified(p.get("verification")) else 0, 1 if p.get("quality") == "READY" else 0)
        if rank_p > rank_x:
            keep_count = x["provenance_count"]
            seen[k] = dict(p)
            seen[k]["provenance_count"] = keep_count
    return list(seen.values())

def _area_gate(req: Dict[str, Any], p: Dict[str, Any]) -> Tuple[bool, str]:
    lo, hi = req.get("area_min_sqft"), req.get("area_max_sqft")
    if lo is None and hi is None:
        return True, "AREA_NOT_REQUESTED"
    area = p.get("area_sqft")
    if area is None:
        return False, "AREA_UNKNOWN"
    lo = lo if lo is not None else hi
    hi = hi if hi is not None else lo
    # hard envelope: requirement band plus 20% tolerance.
    hard_lo = float(lo) * 0.80
    hard_hi = float(hi) * 1.20
    if float(area) < hard_lo or float(area) > hard_hi:
        return False, "AREA_OUTSIDE_HARD_ENVELOPE"
    return True, "AREA_ELIGIBLE"

def _type_gate(req: Dict[str, Any], p: Dict[str, Any]) -> Tuple[bool, str]:
    rf, rs = req.get("family"), req.get("subtype")
    accepted = req.get("acceptable_subtypes") or ([rs] if rs else [])
    pf, ps = p.get("family"), p.get("subtype")
    if rf:
        if not pf:
            return False, "PROPERTY_FAMILY_UNKNOWN"
        if rf != pf:
            return False, "WRONG_PROPERTY_FAMILY"
    if accepted:
        if not ps:
            return False, "PROPERTY_SUBTYPE_UNKNOWN"
        if ps not in accepted:
            return False, "WRONG_PROPERTY_SUBTYPE"
    return True, "TYPE_ELIGIBLE"

def eligible(req: Dict[str, Any], p: Dict[str, Any], location_mode: str = "EXACT") -> Tuple[bool, str, List[str]]:
    why = []
    rloc = req.get("location")
    requested = list(req.get("primary_locations") or [])
    if rloc and rloc not in requested:
        requested.append(rloc)
    ploc = p.get("location")
    if not requested:
        return False, "REQUIREMENT_LOCATION_UNKNOWN", ["Requirement needs a specific locality"]
    if location_mode == "EXACT":
        if "NORTH GOA" in requested:
            if ploc not in (NORTH_GOA_LOCALITIES | {"NORTH GOA"}):
                return False, "WRONG_LOCATION", [f"Required NORTH GOA; candidate {ploc or 'unknown'}"]
            why.append(f"North Goa region match: {ploc}")
        else:
            if ploc not in requested:
                return False, "WRONG_LOCATION", [f"Required one of {', '.join(requested)}; candidate {ploc or 'unknown'}"]
            why.append(f"Exact requested location {ploc}")
    else:
        alternatives = approved_alternatives(req)
        if ploc not in alternatives:
            return False, "NOT_APPROVED_ALTERNATIVE", [f"{ploc or 'unknown'} not in approved alternatives"]
        why.append(f"Approved alternative {ploc} for {', '.join(requested)}")

    rtx = req.get("transaction")
    ptx = p.get("transaction")
    if not rtx:
        return False, "REQUIREMENT_TRANSACTION_UNKNOWN", ["Requirement must say Sale or Rent"]
    if ptx != rtx:
        return False, "WRONG_TRANSACTION", [f"Required {rtx}; candidate {ptx or 'unknown'}"]
    why.append(f"Transaction {rtx}")

    ok, code = _area_gate(req, p)
    if not ok:
        return False, code, [code]
    why.append(code)

    ok, code = _type_gate(req, p)
    if not ok:
        return False, code, [code]
    why.append(code)

    bmax = req.get("budget_max")
    if bmax and p.get("price_comparable") and p.get("price") is not None:
        if float(p["price"]) > float(bmax) * 1.25:
            return False, "ABOVE_BUDGET_HARD", ["Comparable price >25% above budget"]
    return True, "ELIGIBLE", why

def approved_alternatives(req: Dict[str, Any]) -> List[str]:
    locations = list(req.get("primary_locations") or [])
    loc = req.get("location")
    if loc and loc not in locations:
        locations.append(loc)
    if not locations:
        return []
    keys = []
    if req.get("subtype"):
        keys.append(req["subtype"])
    if req.get("family") and req["family"] not in keys:
        keys.append(req["family"])
    out = []
    for key in keys:
        mapping = APPROVED_EQUIVALENCE.get(key, {})
        for requested_loc in locations:
            for alt in mapping.get(requested_loc, []):
                if alt not in locations and alt not in out:
                    out.append(alt)
    return out

def score(req: Dict[str, Any], p: Dict[str, Any], mode: str, gate_why: List[str]) -> Tuple[float, List[str]]:
    pts = 0.0
    why = list(gate_why)

    pts += 30 if mode == "EXACT" else 20

    # Transaction already hard-gated.
    pts += 20

    lo, hi = req.get("area_min_sqft"), req.get("area_max_sqft")
    area = p.get("area_sqft")
    if lo is None and hi is None:
        pts += 20
        why.append("No area constraint supplied")
    else:
        lo = lo if lo is not None else hi
        hi = hi if hi is not None else lo
        if lo <= area <= hi:
            pts += 20
            why.append("Area inside requested band")
        else:
            target = (float(lo) + float(hi)) / 2
            gap = abs(float(area) - target) / max(target, 1.0)
            if gap <= 0.10:
                pts += 17
            elif gap <= 0.20:
                pts += 12
            else:
                pts += 7
            why.append(f"Area tolerated; deviation {round(gap*100,1)}%")

    # Type/use already hard-gated.
    if req.get("subtype"):
        pts += 15
        why.append("Exact intended use")
    elif req.get("family"):
        pts += 13
        why.append("Property family match")
    else:
        pts += 10
        why.append("Property type not specified")

    bmax = req.get("budget_max")
    if not bmax:
        pts += 10
        why.append("No budget constraint supplied")
    elif p.get("price_comparable") and p.get("price") is not None:
        pr = float(p["price"])
        if pr <= float(bmax):
            pts += 10
            why.append("Comparable price within budget")
        elif pr <= float(bmax)*1.10:
            pts += 7
            why.append("Comparable price within 10% above budget")
        elif pr <= float(bmax)*1.20:
            pts += 3
            why.append("Comparable price within 20% above budget")
        else:
            why.append("Comparable price above preferred budget")
    else:
        why.append("Price not comparable; no price points awarded")

    fresh_pts, fresh_reason = _freshness(p.get("captured_on"))
    pts += fresh_pts
    why.append(fresh_reason)
    if _verified(p.get("verification")):
        pts += 3
        why.append("Availability verified")
    else:
        why.append("Verification required before sending")

    return round(min(100.0, pts), 1), why

def public_item(p: Dict[str, Any], match_score: float, match_class: str, why: List[str]) -> Dict[str, Any]:
    verified = _verified(p.get("verification"))
    strict_ready = str(p.get("quality") or "").upper() == "READY"
    send_eligible = bool(verified and strict_ready)
    return {
        "record_id": p.get("record_id"),
        "source_bucket": p.get("source_bucket"),
        "source_table": p.get("source_table"),
        "property": sanitize_text(p.get("description")),
        "location": p.get("location"),
        "transaction": p.get("transaction"),
        "family": p.get("family"),
        "subtype": p.get("subtype"),
        "area_sqft": round(float(p["area_sqft"]), 2) if p.get("area_sqft") is not None else None,
        "price_display": sanitize_text(p.get("price_text")) if p.get("price_comparable") else "Not comparable / verify",
        "price_comparable": bool(p.get("price_comparable")),
        "data_quality": p.get("quality"),
        "availability_verification": "VERIFIED" if verified else "UNVERIFIED",
        "match_score": match_score,
        "match_class": match_class,
        "send_eligible": send_eligible,
        "verification_required": not send_eligible,
        "provenance_count": int(p.get("provenance_count") or 1),
        "why": why,
    }

def run_match(engine, requirement_text: str, min_score: float = 70.0, limit: int = 50) -> Dict[str, Any]:
    req = parse_requirement(requirement_text)
    raw, source_counts = load_candidates(engine)
    candidates = dedupe_candidates(raw)
    exact_verified, exact_verify = [], []
    rejected = []

    for p in candidates:
        ok, code, gate = eligible(req, p, "EXACT")
        if not ok:
            if len(rejected) < 200:
                rejected.append({"record_id": p.get("record_id"), "reason": code})
            continue
        ms, why = score(req, p, "EXACT", gate)
        if ms < min_score:
            continue
        item = public_item(p, ms, "EXACT", why)
        (exact_verified if item["send_eligible"] else exact_verify).append(item)

    exact_verified.sort(key=lambda x: x["match_score"], reverse=True)
    exact_verify.sort(key=lambda x: x["match_score"], reverse=True)

    # Smart alternatives only when there is no VERIFIED exact result.
    # Unverified exact inventory remains visible internally for verification, but does not block smart alternatives.
    alternatives = []
    exact_usable_count = len(exact_verified)
    if exact_usable_count == 0:
        allowed = set(approved_alternatives(req))
        if allowed:
            for p in candidates:
                if p.get("location") not in allowed:
                    continue
                ok, code, gate = eligible(req, p, "ALTERNATIVE")
                if not ok:
                    continue
                ms, why = score(req, p, "ALTERNATIVE", gate)
                if ms >= max(60.0, min_score - 10.0):
                    alternatives.append(public_item(p, ms, "APPROVED_ALTERNATIVE", why))
            alternatives.sort(key=lambda x: (x["send_eligible"], x["match_score"]), reverse=True)

    result = {
        "version": VERSION,
        "requirement": req,
        "summary": {
            **source_counts,
            "deduped_candidates": len(candidates),
            "exact_verified": len(exact_verified),
            "exact_needs_verification": len(exact_verify),
            "approved_alternatives": len(alternatives),
            "inventory_gap": exact_usable_count == 0,
            "contacts_exposed": False,
            "price_used_only_when_comparable": True,
            "price_excluded_from_identity": True,
        },
        "exact_verified": exact_verified[:limit],
        "exact_needs_verification": exact_verify[:limit],
        "alternatives": alternatives[:limit],
        "rejected_sample": rejected[:100],
    }
    # Scrub every public string before enforcing the final contact-leak invariant.
    # This prevents legitimate matcher requests from crashing simply because
    # a requirement/source text contained contact information.
    result = sanitize_public_payload(result)

    # Final safety invariant stays active.
    payload = repr(result)
    if PHONE_RE.search(payload) or EMAIL_RE.search(payload):
        raise RuntimeError("CONTACT_LEAK_GUARD_TRIGGERED")

    return result

def self_test() -> Dict[str, bool]:
    r_unknown = parse_requirement("Need property in Saket 2000 sqft")
    r_dual = parse_requirement("Need shop for sale or rent in Saket 2000 sqft")
    r_rest = parse_requirement("Restaurant for rent in Saket 2000 sqft")
    r_apt = parse_requirement("Apartment for sale in Saket 2000 sqft")
    alt = approved_alternatives(r_rest)
    apt_alt = approved_alternatives(r_apt)
    p1 = {
        "record_id":"T1","description":"Saket restaurant 2000 sqft call 9876543210",
        "location":"SAKET","transaction":"RENT","family":"COMMERCIAL","subtype":"RESTAURANT",
        "area_sqft":2000.0,"price":None,"price_text":"On Request","price_comparable":False,
        "quality":"READY","verification":"UNVERIFIED","captured_on":None,
        "source_bucket":"TEST","source_table":"test","provenance_count":1,
    }
    pub = public_item(p1, 95.0, "EXACT", [])
    legacy_verified = public_item({**p1, "quality":"READY_LEGACY", "verification":"VERIFIED"}, 95.0, "EXACT", [])
    ready_verified = public_item({**p1, "quality":"READY", "verification":"VERIFIED"}, 95.0, "EXACT", [])
    return {
        "unknown_transaction_not_defaulted": r_unknown["transaction"] is None,
        "dual_transaction_rejected_as_unknown": r_dual["transaction"] is None,
        "restaurant_saket_has_use_aware_alternatives": "HAUZ KHAS" in alt and "GREATER KAILASH 1" in alt,
        "apartment_saket_inherits_residential_alternatives": "PANCHSHEEL PARK" in apt_alt and "VASANT KUNJ" in apt_alt,
        "specific_unlisted_canonical_locality_preserved": candidate_location("GK-3") == "GK 3",
        "city_only_candidate_location_rejected": candidate_location("Delhi") is None,
        "phone_hidden_from_public_item": "9876543210" not in repr(pub),
        "price_not_comparable_without_explicit_price": pub["price_comparable"] is False,
        "price_excluded_identity": identity_key(p1) == identity_key({**p1, "price": 999999999.0, "price_text":"₹99 Cr"}),
        "unverified_not_send_eligible": pub["send_eligible"] is False,
        "ready_legacy_verified_still_not_send_eligible": legacy_verified["send_eligible"] is False,
        "strict_ready_verified_send_eligible": ready_verified["send_eligible"] is True,
    }
