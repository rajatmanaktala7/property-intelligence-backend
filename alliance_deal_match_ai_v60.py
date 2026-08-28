from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Request, Form, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

VERSION = "6.0.0-ALLIANCE-DEAL-MATCH-AI"
ROUTE = "/deal-match-ai-v60"

# ---------------------------------------------------------------------
# PRINCIPLE
# ---------------------------------------------------------------------
# Accuracy first:
# 1. Parse requirement.
# 2. Normalize location / transaction / property type.
# 3. Apply hard eligibility gates.
# 4. Only eligible candidates receive a score.
# 5. Exact location and nearby alternatives are never mixed.
# 6. Preserve every source database; read only.
# 7. No startup DDL. Feedback tables are lazy.
# ---------------------------------------------------------------------

LOCATION_ALIASES = {
    "SAKET": [
        "SAKET", "SAKET DISTRICT CENTRE", "DISTRICT CENTRE SAKET",
        "DLF AVENUE SAKET", "SELECT CITYWALK", "SELECT CITY WALK",
        "SOUTH COURT SAKET", "MGF METROPOLITAN SAKET"
    ],
    "MALVIYA NAGAR": ["MALVIYA NAGAR"],
    "HAUZ KHAS": ["HAUZ KHAS"],
    "GREEN PARK": ["GREEN PARK"],
    "GREATER KAILASH 1": ["GK 1", "GK-1", "GK1", "GREATER KAILASH 1", "GREATER KAILASH-I"],
    "GREATER KAILASH 2": ["GK 2", "GK-2", "GK2", "GREATER KAILASH 2", "GREATER KAILASH-II"],
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
    "SAFDARJUNG": ["SAFDARJUNG", "SAFDARJUNG ENCLAVE"],
    "OKHLA": ["OKHLA"],
    "JASOLA": ["JASOLA"],
    "ADCHINI": ["ADCHINI"],
    "MEHRAULI": ["MEHRAULI"],
    "CHHATARPUR": ["CHHATARPUR", "CHATTARPUR"],
    "CONNAUGHT PLACE": ["CONNAUGHT PLACE", "CP", "CONNAUGHT CIRCUS"],
    "RAJOURI GARDEN": ["RAJOURI GARDEN"],
    "PITAMPURA": ["PITAMPURA"],
    "ROHINI": ["ROHINI"],
    "DWARKA": ["DWARKA"],
    "MOTI NAGAR": ["MOTI NAGAR"],
    "PATEL NAGAR": ["PATEL NAGAR", "EAST PATEL NAGAR", "WEST PATEL NAGAR"],
    "NOIDA": ["NOIDA"],
    "GREATER NOIDA": ["GREATER NOIDA", "GR NOIDA"],
    "GURUGRAM": ["GURUGRAM", "GURGAON"],
    "DLF PHASE 1": ["DLF PHASE 1", "DLFPHASE1", "DLF PHASE-I", "DLF 1"],
    "DLF PHASE 2": ["DLF PHASE 2", "DLFPHASE2", "DLF PHASE-II", "DLF 2"],
    "DLF PHASE 3": ["DLF PHASE 3", "DLFPHASE3", "DLF PHASE-III", "DLF 3"],
    "DLF PHASE 4": ["DLF PHASE 4", "DLFPHASE4", "DLF PHASE-IV", "DLF 4"],
    "DLF PHASE 5": ["DLF PHASE 5", "DLFPHASE5", "DLF PHASE-V", "DLF 5"],
    "SUSHANT LOK 1": ["SUSHANT LOK 1", "SUSHANTLOK1", "SUSHANT LOK-I"],
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

# Commercially sensible nearby map. Exact results are always separate.
NEARBY = {
    "SAKET": ["MALVIYA NAGAR", "PANCHSHEEL PARK", "HAUZ KHAS", "ADCHINI", "MEHRAULI"],
    "MALVIYA NAGAR": ["SAKET", "HAUZ KHAS", "PANCHSHEEL PARK", "ADCHINI"],
    "KALKAJI": ["NEHRU PLACE", "CR PARK", "EAST OF KAILASH", "GREATER KAILASH 1"],
    "NEHRU PLACE": ["KALKAJI", "CR PARK", "EAST OF KAILASH", "KAILASH COLONY"],
    "GREATER KAILASH 1": ["GREATER KAILASH 2", "KAILASH COLONY", "CR PARK", "DEFENCE COLONY"],
    "GREATER KAILASH 2": ["GREATER KAILASH 1", "CR PARK", "KALKAJI"],
    "DEFENCE COLONY": ["SOUTH EXTENSION", "GREATER KAILASH 1", "GREEN PARK"],
    "GREEN PARK": ["HAUZ KHAS", "SAFDARJUNG", "SOUTH EXTENSION"],
    "VASANT KUNJ": ["VASANT VIHAR", "CHHATARPUR", "MEHRAULI"],
    "DLF PHASE 1": ["DLF PHASE 2", "DLF PHASE 4", "SUSHANT LOK 1"],
    "DLF PHASE 2": ["DLF PHASE 1", "DLF PHASE 3", "DLF PHASE 4"],
    "DLF PHASE 4": ["DLF PHASE 1", "DLF PHASE 2", "SUSHANT LOK 1"],
    "SIOLIM": ["ASSAGAO", "VAGATOR", "ANJUNA"],
    "ASSAGAO": ["SIOLIM", "ANJUNA", "VAGATOR"],
    "PANAJI": ["MIRAMAR", "CARANZALEM", "DONA PAULA"],
    "JUHU": ["BANDRA WEST", "KHAR WEST"],
}

TRANSACTION_ALIASES = {
    "RENT": ["RENT", "RENTAL", "LEASE", "LEASING", "TO LET", "TOLET"],
    "SALE": ["SALE", "SELL", "BUY", "PURCHASE", "OUTRIGHT", "RESALE"],
}

TYPE_FAMILIES = {
    "COMMERCIAL": [
        "COMMERCIAL", "OFFICE", "SHOP", "SHOWROOM", "RETAIL", "HIGH STREET",
        "RESTAURANT", "CAFE", "LOUNGE", "BANQUET", "HOTEL", "GUEST HOUSE",
        "WAREHOUSE", "GODOWN", "INDUSTRIAL", "BUSINESS CENTRE", "CO-WORKING"
    ],
    "RESIDENTIAL": [
        "RESIDENTIAL", "APARTMENT", "FLAT", "BUILDER FLOOR", "INDEPENDENT FLOOR",
        "VILLA", "KOTHI", "HOUSE", "BUNGALOW", "PENTHOUSE", "BHK"
    ],
    "LAND": ["PLOT", "LAND", "FARMHOUSE", "FARM HOUSE", "ACRE", "SQ YD", "SQYD"],
}

SUBTYPE_ALIASES = {
    "OFFICE": ["OFFICE", "CORPORATE OFFICE", "BUSINESS CENTRE", "CO-WORKING", "COWORKING"],
    "RETAIL": ["SHOP", "SHOWROOM", "RETAIL", "HIGH STREET"],
    "RESTAURANT": ["RESTAURANT", "CAFE", "LOUNGE", "F&B", "FNB"],
    "BANQUET": ["BANQUET", "MARRIAGE HALL", "WEDDING"],
    "HOTEL": ["HOTEL", "GUEST HOUSE", "HOSPITALITY"],
    "WAREHOUSE": ["WAREHOUSE", "GODOWN", "INDUSTRIAL"],
    "APARTMENT": ["APARTMENT", "FLAT", "BHK"],
    "BUILDER FLOOR": ["BUILDER FLOOR", "INDEPENDENT FLOOR", "FLOOR"],
    "VILLA": ["VILLA", "KOTHI", "BUNGALOW", "INDEPENDENT HOUSE"],
    "LAND": ["PLOT", "LAND", "FARMHOUSE", "FARM HOUSE"],
}

SOURCE_TABLES = [
    # canonical / unified
    ("Canonical", "alliance_canonical_properties"),
    ("Canonical Listings", "alliance_property_listings"),
    # master / manual
    ("Master", "alliance_master_listings"),
    ("Manual", "manual_properties"),
    ("Manual", "property_records"),
    ("Master", "pi_properties"),
    ("Master", "properties"),
    ("Master", "master_properties"),
    # newspaper / magazine
    ("Newspaper", "pi_newspaper_properties"),
    ("Newspaper", "newspaper_properties"),
    ("Magazine", "pi_magazine_properties"),
    ("Magazine", "magazine_properties"),
    ("Magazine", "magazine_property_records"),
    # WhatsApp raw-clean fallback. V6 has a dedicated latest-generation adapter first.
    ("WhatsApp", "wa_properties"),
]

FIELD_CANDIDATES = {
    "id": ["property_code","record_id","listing_id","wa_property_id","property_id","id"],
    "description": ["description","raw_summary","raw_text","notes","remarks","property_name","title","parent_message_text"],
    "location": ["location","locality","micro_market","area_name","address","preferred_location"],
    "city": ["city","region"],
    "transaction": ["transaction","transaction_type","lead_type","rent_sale","listing_type"],
    "property_type": ["property_type","category","asset_type","type","configuration"],
    "subtype": ["subtype","property_subtype","suitable_for","suitable_category","configuration"],
    "area": ["available_area","available_area_sqft","area_sqft","area","size","builtup_area","carpet_area"],
    "price": ["rent_inr","rent","sale_price_inr","sale_price","budget_inr","price","asking_price"],
    "budget_text": ["budget_text","price_text","rent_text"],
    "contact": ["contact_number","contact_numbers","phone","owner_phone","broker_phone","sender_phone"],
    "contact_name": ["contact_name","owner_name","broker_name","poster_name","sender_name"],
    "source": ["source","source_group","group_name","source_name","source_type"],
    "verification": ["verification","verified","verification_status","availability","status"],
    "captured": ["captured_on","captured_at","created_at","updated_at","last_seen","message_date"],
    "frontage": ["frontage","frontage_ft"],
    "floor": ["floor","floor_preference"],
    "parking": ["parking","car_parking"],
    "nearby_brands": ["nearby_brands","nearby_brand"],
    "suitable_for": ["suitable_for","suitable_category","ideal_for"],
}

STOPWORDS = {
    "LOOKING","PROPERTY","PROPERTIES","REQUIRE","REQUIRED","REQUIREMENT","NEED","NEEDED",
    "WANT","WANTED","FOR","THE","WITH","AND","IN","AT","OF","TO","A","AN","CLIENT","URGENT",
    "AVAILABLE","AVAILABILITY","PLEASE","PLS","SHARE","OPTION","OPTIONS"
}

def norm(v: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", str(v or "").upper())).strip()

def esc(v: Any) -> str:
    s = str(v or "")
    return (s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
             .replace('"',"&quot;").replace("'","&#39;"))

def table_exists(engine, name: str) -> bool:
    try:
        with engine.connect() as c:
            return bool(c.execute(text("""
                SELECT 1 FROM information_schema.tables
                WHERE table_schema='public' AND table_name=:n
            """), {"n": name}).first())
    except Exception:
        return False

def columns(engine, name: str) -> List[str]:
    try:
        with engine.connect() as c:
            return [r[0] for r in c.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema='public' AND table_name=:n ORDER BY ordinal_position
            """), {"n": name}).all()]
    except Exception:
        return []

def pick(d: Dict[str, Any], key: str) -> Any:
    for n in FIELD_CANDIDATES.get(key, []):
        if n in d and d[n] not in (None, ""):
            return d[n]
    return None

def to_float(v: Any) -> Optional[float]:
    if v in (None, "", "UNKNOWN", "—"):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").strip()
    m = re.search(r"(-?\d+(?:\.\d+)?)", s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None

def money_value(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = norm(v)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(CR|CRORE|LAC|LAKH|K)?", s)
    if not m:
        return None
    n = float(m.group(1))
    u = m.group(2) or ""
    if u in ("CR","CRORE"):
        n *= 10_000_000
    elif u in ("LAC","LAKH"):
        n *= 100_000
    elif u == "K":
        n *= 1_000
    return n

def canonical_location(*values: Any) -> Optional[str]:
    blob = norm(" ".join(str(v or "") for v in values))
    if not blob:
        return None
    # Longest aliases first, preventing "DELHI" style broad contamination.
    found = []
    for canon, aliases in LOCATION_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            a = norm(alias)
            if re.search(r"(?<![A-Z0-9])" + re.escape(a) + r"(?![A-Z0-9])", blob):
                found.append((len(a), canon))
    if found:
        found.sort(reverse=True)
        return found[0][1]
    return None

def canonical_transaction(*values: Any) -> Optional[str]:
    blob = norm(" ".join(str(v or "") for v in values))
    if not blob:
        return None
    hits = []
    for canon, aliases in TRANSACTION_ALIASES.items():
        if any(norm(a) in blob for a in aliases):
            hits.append(canon)
    if len(hits) == 1:
        return hits[0]
    # Prefer explicit "for rent" / "for sale".
    if "FOR RENT" in blob or "TO LET" in blob or "LEASE" in blob:
        return "RENT"
    if "FOR SALE" in blob or "OUTRIGHT" in blob or "RESALE" in blob:
        return "SALE"
    return None

def property_family(*values: Any) -> Optional[str]:
    blob = norm(" ".join(str(v or "") for v in values))
    if not blob:
        return None
    scores = {}
    for fam, words in TYPE_FAMILIES.items():
        scores[fam] = sum(1 for w in words if norm(w) in blob)
    fam = max(scores, key=scores.get)
    return fam if scores[fam] > 0 else None

def property_subtype(*values: Any) -> Optional[str]:
    blob = norm(" ".join(str(v or "") for v in values))
    best = None
    best_len = 0
    for sub, words in SUBTYPE_ALIASES.items():
        for w in words:
            ww = norm(w)
            if ww in blob and len(ww) > best_len:
                best, best_len = sub, len(ww)
    return best

def parse_area_range(raw: str) -> Tuple[Optional[float], Optional[float]]:
    s = raw or ""
    # explicit ranges: 2500-3000 sqft
    m = re.search(r"(?i)\b(\d{2,7})\s*(?:-|to|–)\s*(\d{2,7})\s*(?:sq\.?\s*ft|sqft|sft)?\b", s)
    if m:
        a,b = float(m.group(1)), float(m.group(2))
        return min(a,b), max(a,b)
    vals = [float(x) for x in re.findall(r"(?i)\b(\d{2,7})\s*(?:sq\.?\s*ft|sqft|sft)\b", s)]
    if len(vals) >= 2:
        return min(vals), max(vals)
    if len(vals) == 1:
        return vals[0] * 0.90, vals[0] * 1.10
    return None, None

def parse_budget(raw: str) -> Tuple[Optional[float], Optional[float]]:
    s = raw or ""
    pat = r"(?i)₹?\s*(\d+(?:\.\d+)?)\s*(cr|crore|lac|lakh|k)"
    vals = []
    for n,u in re.findall(pat, s):
        x = float(n)
        u = u.lower()
        if u in ("cr","crore"): x *= 10_000_000
        elif u in ("lac","lakh"): x *= 100_000
        elif u == "k": x *= 1_000
        vals.append(x)
    if len(vals) >= 2:
        return min(vals), max(vals)
    if len(vals) == 1:
        return None, vals[0]
    return None, None

def parse_requirement(raw: str, mode: str = "SMART") -> Dict[str, Any]:
    raw = str(raw or "").strip()
    n = norm(raw)
    loc = canonical_location(raw)
    txn = canonical_transaction(raw)
    fam = property_family(raw)
    subtype = property_subtype(raw)
    amin, amax = parse_area_range(raw)
    bmin, bmax = parse_budget(raw)
    frontage = None
    m = re.search(r"(?i)\bfrontage\s*(?:min(?:imum)?\s*)?(\d+(?:\.\d+)?)\s*(?:ft|feet)?", raw)
    if m:
        frontage = float(m.group(1))
    floor = None
    for f in ["GROUND FLOOR","GROUND","FIRST FLOOR","1ST FLOOR","SECOND FLOOR","2ND FLOOR","LOWER FLOOR","UPPER GROUND","BASEMENT"]:
        if f in n:
            floor = f
            break
    tokens = [x for x in n.split() if len(x) > 2 and x not in STOPWORDS]
    return {
        "raw": raw,
        "mode": mode.upper() if mode else "SMART",
        "location": loc,
        "transaction": txn,
        "family": fam,
        "subtype": subtype,
        "area_min": amin,
        "area_max": amax,
        "budget_min": bmin,
        "budget_max": bmax,
        "frontage_min": frontage,
        "floor": floor,
        "tokens": tokens,
    }

def _source_engine(core, source: str):
    if source == "WhatsApp":
        try:
            import whatsapp_live_bridge as live
            if live.wa_engine is not None:
                return live.wa_engine
        except Exception:
            pass
    return core.engine

def _latest_wa_generation(engine):
    try:
        with engine.connect() as c:
            return c.execute(text("""
                SELECT generation_id FROM pi_whatsapp_property_master_generation
                WHERE status='COMPLETED'
                ORDER BY completed_at DESC NULLS LAST,id DESC LIMIT 1
            """)).scalar()
    except Exception:
        return None

def _wa_master_candidates(core, limit=5000):
    engine = _source_engine(core, "WhatsApp")
    if engine is None or not table_exists(engine, "pi_whatsapp_property_master"):
        return []
    gen = _latest_wa_generation(engine)
    if not gen:
        return []
    try:
        with engine.connect() as c:
            rows = c.execute(text("""
                SELECT record_id,lead_type,description,area,configuration_details,price,
                       contact_name_number,source,captured_on,verification,source_count
                FROM pi_whatsapp_property_master
                WHERE generation_id=:g
                ORDER BY captured_on DESC NULLS LAST,id DESC LIMIT :lim
            """), {"g": gen, "lim": limit}).mappings().all()
        out = []
        for r in rows:
            d = dict(r)
            out.append({
                "source_bucket":"WhatsApp",
                "source_table":"pi_whatsapp_property_master",
                "record_id":d.get("record_id"),
                "description":d.get("description") or "",
                "location_raw":d.get("configuration_details") or "",
                "transaction_raw":d.get("lead_type") or "",
                "property_type_raw":d.get("configuration_details") or d.get("description") or "",
                "subtype_raw":d.get("configuration_details") or "",
                "area":to_float(d.get("area")),
                "price":money_value(d.get("price")),
                "price_text":d.get("price"),
                "contact":d.get("contact_name_number"),
                "contact_name":None,
                "source_name":d.get("source") or "WhatsApp",
                "verification":d.get("verification"),
                "captured_on":d.get("captured_on"),
                "frontage":None,
                "floor":None,
                "parking":None,
                "nearby_brands":None,
                "suitable_for":None,
                "raw":d,
            })
        return out
    except Exception:
        return []

def _generic_candidates(core, source_bucket: str, table: str, limit=5000):
    engine = _source_engine(core, source_bucket)
    if engine is None or not table_exists(engine, table):
        return []
    cols = columns(engine, table)
    if not cols:
        return []
    # Prevent duplicate canonical listing joins from exploding the candidate set.
    safe_cols = cols[:100]
    select = ", ".join('"' + c.replace('"','') + '"' for c in safe_cols)
    order_col = next((c for c in ["id","created_at","updated_at","captured_at","last_seen"] if c in cols), safe_cols[0])
    try:
        with engine.connect() as c:
            rows = c.execute(text(f'SELECT {select} FROM "{table}" ORDER BY "{order_col}" DESC LIMIT :lim'), {"lim":limit}).mappings().all()
    except Exception:
        return []
    out = []
    for r in rows:
        d = dict(r)
        desc = pick(d,"description")
        loc = pick(d,"location")
        tx = pick(d,"transaction")
        ptype = pick(d,"property_type")
        subtype = pick(d,"subtype")
        area = to_float(pick(d,"area"))
        price = money_value(pick(d,"price"))
        budget_text = pick(d,"budget_text")
        if price is None:
            price = money_value(budget_text)
        contact = pick(d,"contact")
        contact_name = pick(d,"contact_name")
        source_name = pick(d,"source") or source_bucket
        verification = pick(d,"verification")
        captured = pick(d,"captured")
        rid = pick(d,"id")
        text_blob = " | ".join(str(x or "") for x in [desc,loc,tx,ptype,subtype,budget_text,pick(d,"suitable_for")])
        # Hide obvious requirement rows from supply adapters.
        nn = norm(text_blob)
        if ("REQUIREMENT" in nn or "LOOKING FOR" in nn or "WANTED" in nn) and not any(x in nn for x in ["AVAILABLE","FOR RENT","FOR SALE","TO LET"]):
            continue
        out.append({
            "source_bucket":source_bucket,
            "source_table":table,
            "record_id":rid,
            "description":desc or text_blob[:700],
            "location_raw":loc or "",
            "transaction_raw":tx or "",
            "property_type_raw":ptype or "",
            "subtype_raw":subtype or "",
            "area":area,
            "price":price,
            "price_text":budget_text or pick(d,"price"),
            "contact":" · ".join(str(x) for x in [contact_name,contact] if x),
            "contact_name":contact_name,
            "source_name":source_name,
            "verification":verification,
            "captured_on":captured,
            "frontage":to_float(pick(d,"frontage")),
            "floor":pick(d,"floor"),
            "parking":pick(d,"parking"),
            "nearby_brands":pick(d,"nearby_brands"),
            "suitable_for":pick(d,"suitable_for"),
            "raw":d,
        })
    return out

def all_candidates(core) -> Tuple[List[Dict[str,Any]], Dict[str,Any]]:
    candidates = []
    diagnostics = {"sources":[], "tables_checked":[]}
    # Dedicated clean WhatsApp adapter first.
    wa = _wa_master_candidates(core)
    if wa:
        candidates.extend(wa)
        diagnostics["sources"].append({"source":"WhatsApp","table":"pi_whatsapp_property_master","rows":len(wa)})

    seen_tables = set()
    for source_bucket, table in SOURCE_TABLES:
        if table == "wa_properties" and wa:
            continue
        key = (source_bucket, table)
        if key in seen_tables:
            continue
        seen_tables.add(key)
        diagnostics["tables_checked"].append(table)
        rows = _generic_candidates(core, source_bucket, table)
        if rows:
            candidates.extend(rows)
            diagnostics["sources"].append({"source":source_bucket,"table":table,"rows":len(rows)})

    return candidates, diagnostics

def _verified(v: Any) -> bool:
    n = norm(v)
    return n in {"VERIFIED","AVAILABLE VERIFIED","ACTIVE VERIFIED","YES","TRUE"} or "VERIFIED" in n

def _freshness_points(v: Any) -> Tuple[float,str]:
    if not v:
        return 0.0, "Freshness unknown"
    try:
        if hasattr(v, "date"):
            dt = v
        else:
            s = str(v).replace("Z","+00:00")
            dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        days = max(0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).days)
        if days <= 7: return 5.0, "Fresh ≤7 days"
        if days <= 30: return 3.0, "Fresh ≤30 days"
        if days <= 90: return 1.0, "Older than 30 days"
        return 0.0, "Stale >90 days"
    except Exception:
        return 0.0, "Freshness unknown"

def normalize_candidate(c: Dict[str,Any]) -> Dict[str,Any]:
    blob = " | ".join(str(c.get(k) or "") for k in [
        "description","location_raw","transaction_raw","property_type_raw","subtype_raw",
        "suitable_for","nearby_brands","source_name"
    ])
    x = dict(c)
    x["location"] = canonical_location(c.get("location_raw"), c.get("description"))
    x["transaction"] = canonical_transaction(c.get("transaction_raw"), c.get("description"))
    x["family"] = property_family(c.get("property_type_raw"), c.get("subtype_raw"), c.get("description"))
    x["subtype"] = property_subtype(c.get("subtype_raw"), c.get("property_type_raw"), c.get("description"))
    x["blob"] = norm(blob)
    return x

def eligibility(req: Dict[str,Any], p: Dict[str,Any], allow_nearby=False) -> Tuple[bool, str, List[str]]:
    reasons = []
    # ---------------- Location gate ----------------
    rloc = req.get("location")
    ploc = p.get("location")
    location_class = "UNKNOWN"
    if rloc:
        if ploc == rloc:
            location_class = "EXACT"
            reasons.append(f"Exact location {rloc}")
        elif allow_nearby and ploc in NEARBY.get(rloc, []):
            location_class = "NEARBY"
            reasons.append(f"Nearby alternative {ploc} for {rloc}")
        else:
            return False, "WRONG_LOCATION", [f"Required {rloc}; candidate {ploc or 'unknown'}"]
    else:
        reasons.append("Requirement location missing")

    # ---------------- Transaction hard gate ----------------
    rtx = req.get("transaction")
    ptx = p.get("transaction")
    if rtx:
        if not ptx:
            return False, "TRANSACTION_UNKNOWN", [f"Requirement transaction {rtx}; candidate transaction unknown"]
        if rtx != ptx:
            return False, "WRONG_TRANSACTION", [f"Required {rtx}; candidate {ptx}"]
        reasons.append(f"Transaction {rtx}")

    # ---------------- Family hard gate ----------------
    rfam = req.get("family")
    pfam = p.get("family")
    if rfam:
        if not pfam:
            return False, "PROPERTY_TYPE_UNKNOWN", [f"Required {rfam}; candidate type unknown"]
        if rfam != pfam:
            return False, "WRONG_PROPERTY_TYPE", [f"Required {rfam}; candidate {pfam}"]
        reasons.append(f"Property family {rfam}")

    # ---------------- Subtype gate when explicit ----------------
    rsub = req.get("subtype")
    psub = p.get("subtype")
    if rsub:
        # family match is necessary but subtype mismatch should normally reject.
        # "Commercial" generic requests have no subtype.
        if psub and psub != rsub:
            # Restaurant can use generic retail/commercial if description explicitly says suitable for restaurant.
            suitability = norm(p.get("suitable_for"))
            if not (rsub in suitability or rsub in p.get("blob","")):
                return False, "WRONG_SUBTYPE", [f"Required subtype {rsub}; candidate {psub}"]
        reasons.append(f"Subtype compatible with {rsub}")

    return True, location_class, reasons

def score_candidate(req: Dict[str,Any], p: Dict[str,Any], location_class: str, gate_reasons: List[str]) -> Tuple[float,float,List[str],List[str]]:
    score = 0.0
    reasons = list(gate_reasons)
    missing = []

    # Score only AFTER eligibility.
    # Location 30
    if location_class == "EXACT":
        score += 30
    elif location_class == "NEARBY":
        score += 18
    else:
        score += 8
        missing.append("Requirement location")

    # Use suitability 20
    if req.get("subtype"):
        if p.get("subtype") == req["subtype"]:
            score += 20
            reasons.append("Exact intended-use subtype")
        elif req["subtype"] in norm(p.get("suitable_for")) or req["subtype"] in p.get("blob",""):
            score += 16
            reasons.append("Suitable for intended use")
        else:
            score += 10
            reasons.append("Family-compatible use")
    elif req.get("family"):
        score += 18
        reasons.append("Property family eligible")
    else:
        score += 10
        missing.append("Property type")

    # Area 15
    area = p.get("area")
    amin, amax = req.get("area_min"), req.get("area_max")
    if amin or amax:
        if area is None:
            missing.append("Area")
        else:
            lo = amin if amin is not None else amax
            hi = amax if amax is not None else amin
            if lo <= area <= hi:
                score += 15
                reasons.append("Area inside requirement")
            else:
                target = (lo + hi) / 2.0
                ratio = abs(area-target)/max(target,1)
                if ratio <= .10:
                    score += 12
                    reasons.append("Area within 10%")
                elif ratio <= .20:
                    score += 8
                    reasons.append("Area within 20%")
                elif ratio <= .30:
                    score += 4
                    reasons.append("Area within 30%")
                else:
                    reasons.append("Area outside preferred band")
    else:
        score += 6
        missing.append("Requirement area")

    # Price / rent 12
    price = p.get("price")
    bmax = req.get("budget_max")
    bmin = req.get("budget_min")
    if bmax:
        if price is None:
            missing.append("Price/Rent")
        elif price <= bmax:
            if bmin and price < bmin * .65:
                score += 6
                reasons.append("Price below stated range; verify quality")
            else:
                score += 12
                reasons.append("Within budget")
        elif price <= bmax*1.10:
            score += 7
            reasons.append("Within 10% above budget")
        elif price <= bmax*1.20:
            score += 3
            reasons.append("Within 20% above budget")
        else:
            reasons.append("Above budget")
    else:
        score += 5
        missing.append("Requirement budget")

    # Floor/frontage 8
    spec_points = 0.0
    if req.get("frontage_min"):
        if p.get("frontage") is None:
            missing.append("Frontage")
        elif p["frontage"] >= req["frontage_min"]:
            spec_points += 4
            reasons.append("Frontage fits")
        else:
            reasons.append("Frontage below requirement")
    else:
        spec_points += 2
    if req.get("floor"):
        if req["floor"] in norm(p.get("floor")) or req["floor"] in p.get("blob",""):
            spec_points += 4
            reasons.append("Floor preference fits")
        else:
            missing.append("Floor fit")
    else:
        spec_points += 2
    score += min(8,spec_points)

    # Freshness 5
    fp, fr = _freshness_points(p.get("captured_on"))
    score += fp
    reasons.append(fr)

    # Verification 5
    if _verified(p.get("verification")):
        score += 5
        reasons.append("Verified source record")
    else:
        missing.append("Availability verification")

    # Contact completeness 3
    if p.get("contact"):
        score += 3
        reasons.append("Contact available")
    else:
        missing.append("Contact")

    # Source confidence 2
    src = p.get("source_bucket")
    if src in {"Manual","Canonical","Master","Newspaper","Magazine","WhatsApp"}:
        score += 2

    score = min(100.0, round(score,1))

    # Separate deal probability - never confuse with match score.
    # Conservative: influenced by verification/freshness/contact and score.
    deal_prob = score * 0.55
    if _verified(p.get("verification")): deal_prob += 12
    if p.get("contact"): deal_prob += 8
    deal_prob += fp * 2
    if location_class == "NEARBY": deal_prob -= 8
    deal_prob = max(0.0, min(95.0, round(deal_prob,1)))

    return score, deal_prob, reasons, sorted(set(missing))

def dedupe_key(p: Dict[str,Any]) -> str:
    loc = p.get("location") or ""
    fam = p.get("family") or ""
    sub = p.get("subtype") or ""
    tx = p.get("transaction") or ""
    area = p.get("area")
    price = p.get("price")
    desc = norm(p.get("description"))[:120]
    # Broker/source deliberately excluded.
    return "|".join([
        loc,fam,sub,tx,
        str(round(area,-1) if isinstance(area,(int,float)) else ""),
        str(round(price,-3) if isinstance(price,(int,float)) else ""),
        desc
    ])

def run_match(core, requirement_text: str, mode: str="SMART", min_score: float=70.0, limit: int=100) -> Dict[str,Any]:
    req = parse_requirement(requirement_text, mode)
    raw_candidates, diagnostics = all_candidates(core)
    exact, nearby, rejected = [], [], []
    seen = {}

    for c in raw_candidates:
        p = normalize_candidate(c)
        key = dedupe_key(p)
        if key in seen:
            # Preserve merged provenance without deleting source records.
            seen[key].setdefault("merged_sources", []).append({
                "source":p.get("source_bucket"),
                "table":p.get("source_table"),
                "record_id":p.get("record_id"),
                "source_name":p.get("source_name"),
                "contact":p.get("contact"),
            })
            continue

        ok, loc_class, gate = eligibility(req,p,False)
        if ok:
            score, dp, why, missing = score_candidate(req,p,loc_class,gate)
            item={**p,"match_score":score,"deal_probability":dp,"why":why,"missing":missing,
                  "match_class":"EXACT","merged_sources":[]}
            seen[key]=item
            if score >= min_score:
                exact.append(item)
            continue

        if req.get("mode") in {"SMART","EXPANSION"}:
            ok2, loc_class2, gate2 = eligibility(req,p,True)
            if ok2 and loc_class2 == "NEARBY":
                score, dp, why, missing = score_candidate(req,p,loc_class2,gate2)
                item={**p,"match_score":score,"deal_probability":dp,"why":why,"missing":missing,
                      "match_class":"NEARBY","merged_sources":[]}
                seen[key]=item
                if score >= max(60.0,min_score-10):
                    nearby.append(item)
                continue

        if len(rejected) < 300:
            rejected.append({
                "record_id":p.get("record_id"),"source":p.get("source_bucket"),
                "location":p.get("location"),"transaction":p.get("transaction"),
                "family":p.get("family"),"description":p.get("description"),
                "reason":loc_class if not ok else "NOT_ELIGIBLE",
                "detail":gate,
            })

    exact.sort(key=lambda x:(x["match_score"],x["deal_probability"]), reverse=True)
    nearby.sort(key=lambda x:(x["match_score"],x["deal_probability"]), reverse=True)

    return {
        "version":VERSION,
        "requirement":req,
        "summary":{
            "raw_candidates":len(raw_candidates),
            "exact_matches":len(exact),
            "nearby_alternatives":len(nearby),
            "rejected_before_scoring":len(rejected),
            "inventory_gap":len(exact)==0,
        },
        "exact":exact[:limit],
        "nearby":nearby[:limit],
        "rejected_sample":rejected[:100],
        "diagnostics":diagnostics,
    }

def _ensure_feedback_tables(engine):
    with engine.begin() as c:
        c.execute(text("""CREATE TABLE IF NOT EXISTS ai_deal_match_feedback(
            id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            requirement_text TEXT,
            source_bucket TEXT,
            source_table TEXT,
            record_id TEXT,
            match_score NUMERIC(5,2),
            feedback TEXT,
            notes TEXT
        )"""))

def _page(title, body):
    return f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>
    <title>{esc(title)}</title><style>
    *{{box-sizing:border-box}}body{{margin:0;font-family:Arial;background:#f1e7d8;color:#2f251d}}
    header{{background:#3f3329;color:#fff;padding:18px 24px}}nav{{background:#fff8ef;padding:10px 18px;display:flex;gap:8px;flex-wrap:wrap}}
    nav a,.btn,button{{background:#6b513d;color:#fff;text-decoration:none;border:0;border-radius:8px;padding:9px 12px;font-weight:800;cursor:pointer}}
    main{{max-width:1900px;margin:auto;padding:18px}}.card{{background:#fffdf9;border:1px solid #d7c4b1;border-radius:12px;padding:15px;margin-bottom:14px}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}}input,select,textarea{{width:100%;padding:10px;border:1px solid #ccb8a5;border-radius:8px}}
    table{{width:100%;border-collapse:collapse;min-width:1700px;background:#fff}}th,td{{padding:9px;border-bottom:1px solid #eee1d2;text-align:left;vertical-align:top;font-size:12px}}
    th{{background:#f7ecdf;position:sticky;top:0}}.scroll{{overflow:auto;max-height:70vh}}.desc{{min-width:360px;max-width:600px;line-height:1.4}}
    .loc{{font-weight:800;min-width:130px}}.score{{font-size:17px;font-weight:900}}.green{{color:#19723d;font-weight:800}}.amber{{color:#9b6500;font-weight:800}}
    .pill{{display:inline-block;padding:4px 7px;border-radius:999px;background:#efe5d9;margin:2px}}.muted{{color:#77685c}}
    </style></head><body><header><h2 style='margin:0'>Alliance Deal Match AI</h2>
    <small>Accuracy first · hard eligibility gates · all property databases · exact location before alternatives</small></header>
    <nav><a href='/team-dashboard-v376'>← Dashboard</a><a href='/workspace'>Working Space</a><a href='/whatsapp-live'>WhatsApp Workspace</a></nav>
    <main>{body}</main></body></html>"""

def _result_table(rows):
    trs=[]
    for r in rows:
        why="; ".join(r.get("why") or [])
        missing=", ".join(r.get("missing") or []) or "None"
        provenance = f"{r.get('source_bucket')} · {r.get('source_table')} · {r.get('record_id')}"
        if r.get("merged_sources"):
            provenance += f" · +{len(r['merged_sources'])} merged source listing(s)"
        trs.append(f"""<tr>
          <td class=score>{esc(r.get('match_score'))}%</td>
          <td>{esc(r.get('deal_probability'))}%</td>
          <td class=loc>{esc(r.get('location') or 'Unknown')}</td>
          <td>{esc(r.get('transaction') or 'Unknown')}</td>
          <td>{esc(r.get('family') or 'Unknown')} / {esc(r.get('subtype') or 'Generic')}</td>
          <td class=desc>{esc(r.get('description'))}</td>
          <td>{esc(r.get('area'))}</td>
          <td>{esc(r.get('price_text') or r.get('price'))}</td>
          <td>{esc(r.get('contact'))}</td>
          <td>{esc(r.get('verification'))}</td>
          <td>{esc(r.get('captured_on'))}</td>
          <td>{esc(provenance)}</td>
          <td>{esc(why)}</td>
          <td>{esc(missing)}</td>
        </tr>""")
    return "".join(trs)

def render_form():
    body="""<div class=card><h2>High-Accuracy Property Matcher</h2>
      <p class=muted>Write the requirement naturally. Example: <b>Commercial restaurant space for rent in Saket, 2500-3000 sqft, budget 4 lakh, minimum frontage 25 ft.</b></p>
      <form method=get action='/deal-match-ai-v60'>
      <div class=grid>
        <div style='grid-column:1/-1'><label>Requirement</label><textarea name=q rows=4 required></textarea></div>
        <div><label>Location Mode</label><select name=mode><option value=SMART selected>SMART - exact first + separate nearby</option><option value=STRICT>STRICT - exact location only</option><option value=EXPANSION>EXPANSION - exact + nearby options</option></select></div>
        <div><label>Minimum Match %</label><input type=number name=min_score value=70 min=40 max=100></div>
        <div style='align-self:end'><button>Run AI Matcher</button></div>
      </div></form></div>
      <div class=card><h3>Why this matcher is different</h3>
      <p><span class=pill>Location hard gate</span><span class=pill>Transaction hard gate</span><span class=pill>Property type hard gate</span>
      <span class=pill>Source-preserving dedupe</span><span class=pill>Exact vs nearby separated</span><span class=pill>Match score ≠ deal probability</span></p></div>"""
    return HTMLResponse(_page("Alliance Deal Match AI",body))

def render_results(core, q, mode, min_score):
    res=run_match(core,q,mode,min_score,100)
    req=res["requirement"]; s=res["summary"]
    parsed=f"""<div class=grid>
      <div><b>Location</b><br>{esc(req.get('location') or 'Not identified')}</div>
      <div><b>Transaction</b><br>{esc(req.get('transaction') or 'Not identified')}</div>
      <div><b>Property Family</b><br>{esc(req.get('family') or 'Not identified')}</div>
      <div><b>Subtype / Use</b><br>{esc(req.get('subtype') or 'Generic')}</div>
      <div><b>Area</b><br>{esc(req.get('area_min'))} - {esc(req.get('area_max'))}</div>
      <div><b>Budget Max</b><br>{esc(req.get('budget_max'))}</div>
      <div><b>Mode</b><br>{esc(req.get('mode'))}</div>
    </div>"""
    src_cards="".join(f"<span class=pill>{esc(x['source'])}: {esc(x['rows'])}</span>" for x in res["diagnostics"]["sources"])
    body=f"""<div class=card><h2>Requirement Intelligence Card</h2><p>{esc(q)}</p>{parsed}</div>
    <div class=card><h3>Search Coverage</h3><p>{src_cards or 'No readable property source tables found.'}</p>
      <p><b>{s['raw_candidates']}</b> candidates read · <b>{s['exact_matches']}</b> exact matches ·
      <b>{s['nearby_alternatives']}</b> nearby alternatives</p></div>
    <div class=card><h2>A. Exact Location Matches</h2>
      <p class=green>Only properties that passed mandatory eligibility gates appear here.</p>
      <div class=scroll><table><tr><th>Match</th><th>Deal Probability</th><th>Location</th><th>Transaction</th><th>Type</th><th>Description</th><th>Area</th><th>Price/Rent</th><th>Contact</th><th>Verification</th><th>Freshness</th><th>Source</th><th>Why Matched</th><th>Missing Info</th></tr>
      {_result_table(res['exact']) or '<tr><td colspan=14>No exact match found. Treat this as an inventory gap instead of returning false matches.</td></tr>'}</table></div></div>
    <div class=card><h2>B. Smart Nearby Alternatives</h2>
      <p class=amber>Alternatives are deliberately separate and never mixed into exact-location results.</p>
      <div class=scroll><table><tr><th>Match</th><th>Deal Probability</th><th>Location</th><th>Transaction</th><th>Type</th><th>Description</th><th>Area</th><th>Price/Rent</th><th>Contact</th><th>Verification</th><th>Freshness</th><th>Source</th><th>Why Matched</th><th>Missing Info</th></tr>
      {_result_table(res['nearby']) or '<tr><td colspan=14>No suitable nearby alternatives.</td></tr>'}</table></div></div>
    <div class=card><a class=btn href='/deal-match-ai-v60'>Run Another Requirement</a></div>"""
    return HTMLResponse(_page("Alliance Deal Match AI Results",body))

def register(core):
    app = core.app

    # Avoid duplicate registration.
    if any(getattr(r,"path",None)==ROUTE for r in app.router.routes):
        return {"status":"ALREADY_REGISTERED","version":VERSION}

    @app.get(ROUTE, response_class=HTMLResponse)
    def deal_match_page(q: str = Query("", max_length=5000), mode: str = Query("SMART"), min_score: float = Query(70, ge=40, le=100)):
        if not q.strip():
            return render_form()
        return render_results(core,q.strip(),mode,min_score)

    @app.get("/api/v60/deal-match")
    def deal_match_api(q: str = Query(..., min_length=2, max_length=5000), mode: str = Query("SMART"), min_score: float = Query(70, ge=40, le=100), limit: int = Query(50, ge=1, le=200)):
        return JSONResponse(run_match(core,q,mode,min_score,limit))

    @app.get("/api/v60/status")
    def deal_match_status():
        cands, diag = all_candidates(core)
        return {
            "status":"OK",
            "version":VERSION,
            "accuracy_model":"ELIGIBILITY_FIRST_SCORING_SECOND",
            "hard_gates":["LOCATION","TRANSACTION","PROPERTY_FAMILY","EXPLICIT_SUBTYPE"],
            "location_modes":["STRICT","SMART","EXPANSION"],
            "sources":diag["sources"],
            "candidate_count":len(cands),
            "startup_ddl":False,
            "source_data_mutation":False,
            "legacy_matcher_modified":False,
            "exact_and_nearby_separated":True,
            "match_score_separate_from_deal_probability":True,
        }

    @app.post("/api/v60/feedback")
    async def deal_match_feedback(request: Request):
        payload = await request.json()
        fb = str(payload.get("feedback") or "").strip()
        if fb not in {
            "GOOD_MATCH","BAD_MATCH","WRONG_LOCATION","WRONG_TRANSACTION","WRONG_PROPERTY_TYPE",
            "TOO_EXPENSIVE","WRONG_AREA","WRONG_FLOOR","WRONG_USE","UNAVAILABLE",
            "CLIENT_INTERESTED","SITE_VISIT","NEGOTIATION","DEAL_CLOSED"
        }:
            raise HTTPException(400,"Unsupported feedback")
        _ensure_feedback_tables(core.engine)
        with core.engine.begin() as c:
            c.execute(text("""INSERT INTO ai_deal_match_feedback(
              requirement_text,source_bucket,source_table,record_id,match_score,feedback,notes
            ) VALUES(:q,:sb,:st,:rid,:ms,:fb,:notes)"""),{
              "q":payload.get("requirement_text"),"sb":payload.get("source_bucket"),
              "st":payload.get("source_table"),"rid":payload.get("record_id"),
              "ms":payload.get("match_score"),"fb":fb,"notes":payload.get("notes")
            })
        return {"status":"OK","feedback":fb}

    return {"status":"REGISTERED","version":VERSION,"route":ROUTE}
