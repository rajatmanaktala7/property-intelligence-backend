from __future__ import annotations

import os
import re
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import create_engine, text

VERSION = "5.0.3-PHASE5-SAFE-PAYLOAD-SCRUB"
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
    "DLF PHASE 1": ["DLF PHASE 1", "DLF PHASE-I", "DLF 1"],
    "DLF PHASE 2": ["DLF PHASE 2", "DLF PHASE-II", "DLF 2"],
    "DLF PHASE 3": ["DLF PHASE 3", "DLF PHASE-III", "DLF 3"],
    "DLF PHASE 4": ["DLF PHASE 4", "DLF PHASE-IV", "DLF 4"],
    "DLF PHASE 5": ["DLF PHASE 5", "DLF PHASE-V", "DLF 5"],
    "SUSHANT LOK 1": ["SUSHANT LOK 1", "SUSHANT LOK-I"],
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
    "JUHU": ["JUHU", "JVPD", "GULMOHAR ROAD"],
    "BANDRA WEST": ["BANDRA WEST"],
    "KHAR WEST": ["KHAR WEST"],
}

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

PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)")
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)

def norm(v: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", str(v or "").upper())).strip()

def sanitize_text(v: Any) -> str:
    s = str(v or "")
    s = PHONE_RE.sub("[CONTACT HIDDEN]", s)
    s = EMAIL_RE.sub("[EMAIL HIDDEN]", s)
    return re.sub(r"\s+", " ", s).strip()

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

def canonical_location(*vals: Any) -> Optional[str]:
    blob = norm(" ".join(str(v or "") for v in vals))
    if not blob:
        return None
    found = []
    for canon, aliases in LOCATION_ALIASES.items():
        for a in aliases:
            aa = norm(a)
            if aa and re.search(r"(?<![A-Z0-9])" + re.escape(aa) + r"(?![A-Z0-9])", blob):
                found.append((len(aa), canon))
    if found:
        found.sort(reverse=True)
        return found[0][1]
    return None

def candidate_location(raw: Any) -> Optional[str]:
    """
    Prefer known alias canonicalization, but do not throw away a specific
    canonical locality merely because it is absent from LOCATION_ALIASES.
    City-only values remain invalid.
    """
    if raw in (None, ""):
        return None
    known = canonical_location(raw)
    if known:
        return known
    cleaned = norm(raw)
    if not cleaned or cleaned in CITY_ONLY:
        return None
    # Reject values that are clearly too generic to be a micro-location.
    if cleaned in {"UNKNOWN", "NA", "N A", "NONE", "DELHI NCR", "NCR"}:
        return None
    # Keep the database's canonical locality as the matching token.
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

def area_to_sqft(v: Any, unit: Any = None) -> Optional[float]:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        num = float(v)
        u = norm(unit)
    else:
        s = str(v).replace(",", "")
        m = re.search(r"(\d+(?:\.\d+)?)", s)
        if not m:
            return None
        num = float(m.group(1))
        u = norm(unit or s)
    if num <= 0:
        return None
    if any(x in u for x in ["SQ M", "SQM", "SQUARE M"]):
        return num * 10.7639104167
    if any(x in u for x in ["SQ YD", "SQYD", "YARD", "GAJ"]):
        return num * 9.0
    if any(x in u for x in ["ACRE"]):
        return num * 43560.0
    return num

def parse_requirement_area(raw: str) -> Tuple[Optional[float], Optional[float]]:
    s = str(raw or "").replace(",", "")
    factor = 1.0
    if re.search(r"(?i)(sq\.?\s*m|sqm|square\s*met)", s):
        factor = 10.7639104167
    elif re.search(r"(?i)(sq\.?\s*yd|sqyd|yard|gaj)", s):
        factor = 9.0
    elif re.search(r"(?i)acre", s):
        factor = 43560.0
    m = re.search(r"(?i)\b(\d+(?:\.\d+)?)\s*(?:-|to|–)\s*(\d+(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|sft|sq\.?\s*m|sqm|sq\.?\s*yd|sqyd|yards?|gaj|acres?)?\b", s)
    if m:
        a, b = float(m.group(1))*factor, float(m.group(2))*factor
        return min(a,b), max(a,b)
    m = re.search(r"(?i)\b(\d+(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|sft|sq\.?\s*m|sqm|sq\.?\s*yd|sqyd|yards?|gaj|acres?)\b", s)
    if m:
        x = float(m.group(1))*factor
        return x*0.90, x*1.10
    return None, None

def money_value(raw: Any) -> Optional[float]:
    if raw in (None, ""):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).replace(",", "")
    m = re.search(r"(?i)(?:₹\s*)?(\d+(?:\.\d+)?)\s*(cr|crore|crores|lac|lakh|lakhs|k)\b", s)
    if not m:
        return None
    n = float(m.group(1)); u = m.group(2).lower()
    if u.startswith("cr"):
        n *= 10_000_000
    elif u in {"lac", "lakh", "lakhs"}:
        n *= 100_000
    elif u == "k":
        n *= 1_000
    return n

def parse_budget(raw: str) -> Tuple[Optional[float], Optional[float]]:
    vals = []
    for m in re.finditer(r"(?i)(?:₹\s*)?(\d+(?:\.\d+)?)\s*(cr|crore|crores|lac|lakh|lakhs|k)\b", str(raw or "")):
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
    return {
        "raw": str(raw or "").strip(),
        "location": canonical_location(raw),
        "transaction": canonical_transaction(raw),
        "family": fam,
        "subtype": sub,
        "area_min_sqft": amin,
        "area_max_sqft": amax,
        "budget_min": bmin,
        "budget_max": bmax,
    }

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

def load_whatsapp_master(engine, limit: int = 10000) -> List[Dict[str, Any]]:
    if not table_exists(engine, "pi_whatsapp_property_master"):
        return []
    g = _live_wa_generation(engine)
    wanted = [
        "record_id", "lead_type", "description", "area", "configuration_details",
        "price", "source", "captured_on", "verification", "furnishing", "floor",
    ]
    cols = table_columns(engine, "pi_whatsapp_property_master")
    selected = [x for x in wanted if x in cols]
    qcols = ", ".join('"' + x + '"' for x in selected)
    with engine.connect() as c:
        rows = [dict(r) for r in c.execute(text(
            f'SELECT {qcols} FROM pi_whatsapp_property_master '
            'WHERE generation_id=:g ORDER BY captured_on DESC NULLS LAST, id DESC LIMIT :lim'
        ), {"g": g, "lim": int(limit)}).mappings().all()]
    out = []
    for d in rows:
        tx = norm(d.get("lead_type"))
        if tx not in {"SALE", "RENT"}:
            continue
        desc = str(d.get("description") or "")
        loc = canonical_location(desc)
        if not loc:
            cfg_loc = candidate_location(d.get("configuration_details"))
            loc = cfg_loc if cfg_loc and cfg_loc not in {"COMMERCIAL", "APARTMENT", "VILLA", "OFFICE", "RETAIL", "RESTAURANT", "BANQUET", "HOTEL", "WAREHOUSE", "LAND"} else None
        if not loc:
            continue
        fam, sub = family_subtype(d.get("configuration_details"), desc)
        atext = d.get("area")
        area_sqft = area_to_sqft(atext)
        if area_sqft is None:
            continue
        ptext = d.get("price")
        price = money_value(ptext)
        comparable = price is not None and bool(re.search(r"(?i)\b(cr|crore|lac|lakh|k)\b", str(ptext or "")))
        ver = d.get("verification") or "UNVERIFIED"
        if not _available(ver):
            continue
        out.append({
            "source_bucket": "WHATSAPP_PHASE41",
            "source_table": "pi_whatsapp_property_master",
            "record_id": str(d.get("record_id") or ""),
            "description": sanitize_text(desc),
            "location": loc,
            "transaction": tx,
            "family": fam,
            "subtype": sub,
            "area_sqft": area_sqft,
            "area_unit_verified": True,
            "price": price if comparable else None,
            "price_text": sanitize_text(ptext),
            "price_comparable": comparable,
            "quality": "READY",
            "verification": ver,
            "captured_on": d.get("captured_on"),
            "source_name": sanitize_text(d.get("source") or "WhatsApp"),
            "review_reasons": None,
        })
    return out

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
    pf, ps = p.get("family"), p.get("subtype")
    if rf:
        if not pf:
            return False, "PROPERTY_FAMILY_UNKNOWN"
        if rf != pf:
            return False, "WRONG_PROPERTY_FAMILY"
    if rs:
        if not ps:
            return False, "PROPERTY_SUBTYPE_UNKNOWN"
        if rs != ps:
            return False, "WRONG_PROPERTY_SUBTYPE"
    return True, "TYPE_ELIGIBLE"

def eligible(req: Dict[str, Any], p: Dict[str, Any], location_mode: str = "EXACT") -> Tuple[bool, str, List[str]]:
    why = []
    rloc = req.get("location")
    ploc = p.get("location")
    if not rloc:
        return False, "REQUIREMENT_LOCATION_UNKNOWN", ["Requirement needs a specific locality"]
    if location_mode == "EXACT":
        if ploc != rloc:
            return False, "WRONG_LOCATION", [f"Required {rloc}; candidate {ploc or 'unknown'}"]
        why.append(f"Exact location {rloc}")
    else:
        alternatives = approved_alternatives(req)
        if ploc not in alternatives:
            return False, "NOT_APPROVED_ALTERNATIVE", [f"{ploc or 'unknown'} not in approved alternatives"]
        why.append(f"Approved alternative {ploc} for {rloc}")

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
    loc = req.get("location")
    if not loc:
        return []
    keys = []
    if req.get("subtype"):
        keys.append(req["subtype"])
    if req.get("family") and req["family"] not in keys:
        keys.append(req["family"])
    out = []
    for key in keys:
        for alt in APPROVED_EQUIVALENCE.get(key, {}).get(loc, []):
            if alt not in out:
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
