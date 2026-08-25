import re
from sqlalchemy import text
from alliance_v2_normalize import norm, ptype, area, money, num, infer_frontage, infer_required_floor, infer_suitable

VERSION = "1.1-SAFE-NUMERIC-RECOVERY"

LOCATION_ALIASES = {
    "cp": "Connaught Place",
    "connaught place": "Connaught Place",
    "connaught circus": "Connaught Place",
    "rajiv chowk": "Connaught Place",
    "inner circle": "Connaught Place",
    "outer circle": "Connaught Place",
    "gk": "Greater Kailash",
    "gk 1": "Greater Kailash 1",
    "gk1": "Greater Kailash 1",
    "gk 2": "Greater Kailash 2",
    "gk2": "Greater Kailash 2",
    "greater kailash": "Greater Kailash",
    "south ex": "South Extension",
    "south extension": "South Extension",
    "lajpat": "Lajpat Nagar",
    "lajpat nagar": "Lajpat Nagar",
    "def col": "Defence Colony",
    "defence colony": "Defence Colony",
    "vasant kunj": "Vasant Kunj",
    "vasant vihar": "Vasant Vihar",
    "hauz khas": "Hauz Khas",
    "kailash colony": "Kailash Colony",
    "pitampura": "Pitampura",
    "kohat": "Kohat Enclave",
    "kohat enclave": "Kohat Enclave",
    "rajouri": "Rajouri Garden",
    "rajouri garden": "Rajouri Garden",
    "dwarka": "Dwarka",
    "gurgaon": "Gurgaon",
    "gurugram": "Gurgaon",
    "noida": "Noida",
    "faridabad": "Faridabad",
    "ghaziabad": "Ghaziabad",
}

GENERIC_LOCATIONS = {
    "", "other", "others", "unknown", "not specified", "not available",
    "na", "n a", "none", "nil", "-"
}

REQ_WORDS = [
    "require", "required", "requirement", "wanted", "looking for", "looking to lease",
    "looking to buy", "need ", "needed", "client looking", "tenant looking",
    "buyer looking", "brand looking", "space required", "we need", "require space"
]
LEASE_WORDS = [
    "for rent", "on rent", "rent ", "lease", "leasing", "tenant", "monthly rent",
    "rental", "to let"
]
SALE_WORDS = [
    "for sale", "sale ", "selling", "sell ", "buyer", "purchase", "buy ", "asking price",
    "sale price", "outright"
]

TYPE_PATTERNS = [
    ("RESTAURANT", ["restaurant", "restro", "fine dine", "fine dining", "food outlet"]),
    ("CAFE", ["cafe", "coffee shop"]),
    ("BANQUET", ["banquet", "marriage hall", "wedding venue"]),
    ("HOTEL", ["hotel", "resort"]),
    ("GUEST_HOUSE", ["guest house", "guesthouse"]),
    ("CLUB", ["club"]),
    ("LOUNGE", ["lounge"]),
    ("RETAIL_SHOP", ["retail", "shop", "showroom", "high street"]),
    ("OFFICE", ["office", "workspace", "commercial office"]),
    ("WAREHOUSE", ["warehouse", "godown"]),
    ("INDUSTRIAL", ["industrial", "factory"]),
    ("FARMHOUSE", ["farmhouse", "farm house"]),
    ("LAND", ["plot", "land"]),
    ("VILLA", ["villa", "kothi", "independent house"]),
    ("RESIDENTIAL", ["apartment", "flat", "bhk", "builder floor"]),
]

def _safe_float(v, low=None, high=None):
    try:
        x = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    if x != x or x in (float("inf"), float("-inf")):
        return None
    if low is not None and x < low:
        return None
    if high is not None and x > high:
        return None
    return x

def _clean_area_pair(amin, amax):
    # Commercial/residential/land areas can be large, but phone-number sized values are not areas.
    a = _safe_float(amin, 1, 100_000_000)
    b = _safe_float(amax, 1, 100_000_000)
    if a is None and b is None:
        return None, None
    if a is None:
        a = b
    if b is None:
        b = a
    if a > b:
        a, b = b, a
    return round(a, 2), round(b, 2)

def canonical_location(*values):
    joined = " ".join(str(v or "") for v in values)
    n = norm(joined)
    if not n:
        return None

    for alias, canonical in sorted(LOCATION_ALIASES.items(), key=lambda x: len(x[0]), reverse=True):
        if alias in n:
            return canonical

    sec = re.search(r"\b(?:sector|sec)\s*[- ]?(\d{1,3}[a-z]?)\b", n, re.I)
    if sec:
        prefix = "Gurgaon" if ("gurgaon" in n or "gurugram" in n) else "Noida" if "noida" in n else ""
        return ((prefix + " ") if prefix else "") + "Sector " + sec.group(1).upper()

    # Do not promote placeholder locations such as "Other" into matchable geography.
    for v in values[:-1]:
        s = str(v or "").strip()
        if s and norm(s) not in GENERIC_LOCATIONS:
            return s
    return None

def detect_transaction(current, raw):
    cur = norm(current)
    t = norm(raw)
    if cur in {"sale", "selling", "sell"}:
        return "SUPPLY", "SALE", 100, "source_transaction"
    if cur in {"rent", "lease", "leasing"}:
        return "SUPPLY", "LEASE", 100, "source_transaction"
    if cur == "requirement":
        has_lease = any(w in t for w in LEASE_WORDS)
        has_sale = any(w in t for w in SALE_WORDS)
        if has_lease and has_sale:
            return "REQUIREMENT", "LEASE_OR_SALE", 90, "requirement_text"
        if has_lease:
            return "REQUIREMENT", "LEASE", 90, "requirement_text"
        if has_sale:
            return "REQUIREMENT", "SALE", 90, "requirement_text"
        return "REQUIREMENT", "UNKNOWN", 30, "requirement_without_transaction"

    is_req = any(w in t for w in REQ_WORDS)
    has_lease = any(w in t for w in LEASE_WORDS)
    has_sale = any(w in t for w in SALE_WORDS)
    if is_req:
        if has_lease and has_sale:
            return "REQUIREMENT", "LEASE_OR_SALE", 85, "recovered_requirement"
        if has_lease:
            return "REQUIREMENT", "LEASE", 85, "recovered_requirement"
        if has_sale:
            return "REQUIREMENT", "SALE", 85, "recovered_requirement"
        return "REQUIREMENT", "UNKNOWN", 65, "recovered_requirement_ambiguous"
    if has_lease and not has_sale:
        return "SUPPLY", "LEASE", 85, "recovered_supply"
    if has_sale and not has_lease:
        return "SUPPLY", "SALE", 85, "recovered_supply"
    if has_lease and has_sale:
        return "SUPPLY", "LEASE_OR_SALE", 65, "ambiguous_supply"
    return "UNKNOWN", "UNKNOWN", 0, "unresolved"

def detect_property_type(current, raw):
    cur = ptype(current)
    if cur != "UNKNOWN" and len(cur) < 80:
        return cur, 100, "source_property_type"
    t = norm(raw)
    for canonical, words in TYPE_PATTERNS:
        if any(w in t for w in words):
            return canonical, 85, "recovered_from_text"
    return "UNKNOWN", 0, "unresolved"

def _explicit_area_from_raw(raw):
    """
    Recover an area from free text ONLY when an area unit is present.
    This prevents '3rd floor' and 10-digit phone numbers from becoming areas.
    """
    s = str(raw or "").lower().replace(",", "")
    patterns = [
        r"\b\d+(?:\.\d+)?\s*(?:-|to|x)\s*\d+(?:\.\d+)?\s*(?:sq\.?\s*ft|sqft|sft|sf|square\s*feet)\b",
        r"\b\d+(?:\.\d+)?\s*(?:sq\.?\s*ft|sqft|sft|sf|square\s*feet)\b",
        r"\b\d+(?:\.\d+)?\s*(?:sq\.?\s*yd|sqyd|square\s*yard|yards?|yds?)\b",
        r"\b\d+(?:\.\d+)?\s*(?:sq\.?\s*m|sqm|square\s*met(?:er|re)s?)\b",
        r"\b\d+(?:\.\d+)?\s*(?:acre|acres)\b",
    ]
    for pat in patterns:
        m = re.search(pat, s, re.I)
        if m:
            return _clean_area_pair(*area(m.group(0)))
    return None, None

def recover_area(area_text, area_numeric, raw):
    # 1) Trust dedicated structured area fields, after sanity checks.
    amin, amax = area(area_text)
    amin, amax = _clean_area_pair(amin, amax)
    if amin is not None:
        return amin, amax

    n = _safe_float(area_numeric, 1, 100_000_000)
    if n is not None:
        return round(n, 2), round(n, 2)

    # 2) Free text is allowed only with explicit area units.
    return _explicit_area_from_raw(raw)

def _explicit_money_from_raw(raw):
    s = str(raw or "")
    # Only consider raw text as money when currency/rent/price language is present.
    if not re.search(
        r"(?:₹|\brs\.?\b|\binr\b|\bcrore\b|\bcr\b|\blakh\b|\blac\b|\bprice\b|\brent\b|\basking\b|\bbudget\b)",
        s, re.I
    ):
        return None
    v = money(s)
    return _safe_float(v, 0, 1_000_000_000_000)

def recover_budget(budget_text, budget_numeric, raw):
    v = _safe_float(budget_numeric, 0, 1_000_000_000_000)
    if v is not None:
        return round(v, 2)
    v = money(budget_text)
    v = _safe_float(v, 0, 1_000_000_000_000)
    if v is not None:
        return round(v, 2)
    return _explicit_money_from_raw(raw)

def quality_score(tx, loc, typ, amin, phone_present, raw_conf, verified):
    score = 0
    score += 22 if tx != "UNKNOWN" else 0
    score += 22 if loc else 0
    score += 18 if typ != "UNKNOWN" else 0
    score += 18 if amin else 0
    score += 8 if phone_present else 0
    score += 5 if verified else 0
    if raw_conf is not None:
        rc = _safe_float(raw_conf, 0, 100)
        if rc is not None:
            score += min(7, rc / 15)
    return round(min(100, score), 2)

def ensure_schema(c):
    c.execute(text("""
    CREATE TABLE IF NOT EXISTS ai_whatsapp_purity (
      listing_id UUID PRIMARY KEY,
      original_transaction TEXT,
      recovered_role TEXT,
      recovered_transaction TEXT,
      transaction_confidence NUMERIC(5,2),
      transaction_reason TEXT,
      original_location TEXT,
      recovered_location TEXT,
      original_property_type TEXT,
      recovered_property_type TEXT,
      property_type_confidence NUMERIC(5,2),
      property_type_reason TEXT,
      recovered_area_min_sqft NUMERIC(14,2),
      recovered_area_max_sqft NUMERIC(14,2),
      recovered_budget NUMERIC(18,2),
      recovered_frontage_ft NUMERIC(12,2),
      recovered_required_floor TEXT,
      recovered_suitable_for TEXT,
      purity_score NUMERIC(5,2),
      review_status TEXT,
      duplicate_cluster_key TEXT,
      source_group_name TEXT,
      poster_name TEXT,
      raw_text TEXT,
      last_recovered_at TIMESTAMPTZ DEFAULT NOW()
    )
    """))
    c.execute(text("CREATE INDEX IF NOT EXISTS ix_ai_whatsapp_purity_review ON ai_whatsapp_purity(review_status)"))
    c.execute(text("CREATE INDEX IF NOT EXISTS ix_ai_whatsapp_purity_location ON ai_whatsapp_purity(recovered_location)"))
    c.execute(text("CREATE INDEX IF NOT EXISTS ix_ai_whatsapp_purity_tx ON ai_whatsapp_purity(recovered_transaction)"))

def build_purity(primary_engine, source_engine):
    result = {
        "version": VERSION,
        "rows_processed": 0,
        "usable_supply": 0,
        "usable_requirements": 0,
        "needs_review": 0,
        "low_confidence": 0,
        "unknown_after_recovery": 0,
        "invalid_area_rejected": 0,
        "duplicate_clusters": 0,
    }

    with source_engine.connect() as src:
        rows = [dict(x._mapping) for x in src.execute(text("""
            SELECT
              l.id,l.transaction,l.property_type,l.location,l.region,l.area_text,l.area_sqft_numeric,
              l.budget_text,l.budget_numeric,l.summary,l.confidence_score,l.status,l.duplicate_of,
              l.source_group_name,l.poster_name,l.raw_listing_text,
              ct.phone,ct.display_name,ct.firm_name
            FROM wai_listings l
            LEFT JOIN wai_contacts ct ON ct.id=l.contact_id
            ORDER BY l.created_at ASC
        """)).fetchall()]

    with primary_engine.begin() as c:
        ensure_schema(c)

        for r in rows:
            raw = str(r.get("raw_listing_text") or r.get("summary") or "")
            role, tx, tx_conf, tx_reason = detect_transaction(r.get("transaction"), raw)
            loc = canonical_location(r.get("location"), r.get("region"), raw)
            typ, type_conf, type_reason = detect_property_type(r.get("property_type"), raw)
            amin, amax = recover_area(r.get("area_text"), r.get("area_sqft_numeric"), raw)
            budget = recover_budget(r.get("budget_text"), r.get("budget_numeric"), raw)
            frontage = _safe_float(infer_frontage(raw), 0, 100_000)
            floor = infer_required_floor(raw) if role == "REQUIREMENT" else None
            suitable = infer_suitable(raw)
            verified = norm(r.get("status")) in {"verified", "approved"}

            # Final defensive barrier before any NUMERIC insert.
            amin, amax = _clean_area_pair(amin, amax)
            if amin is None and (
                r.get("area_text") not in (None, "", "NA", "N/A") or
                r.get("area_sqft_numeric") not in (None, "")
            ):
                result["invalid_area_rejected"] += 1

            purity = quality_score(
                tx, loc, typ, amin, bool(r.get("phone")),
                r.get("confidence_score"), verified
            )

            if role == "UNKNOWN" or tx == "UNKNOWN":
                review = "UNKNOWN"
            elif purity >= 75 and loc and typ != "UNKNOWN" and amin:
                review = "USABLE"
            elif purity >= 50:
                review = "NEEDS_REVIEW"
            else:
                review = "LOW_CONFIDENCE"

            cluster = "|".join([
                norm(loc), typ, tx,
                str(round(float(amin or 0), -2) if amin else 0),
                norm(r.get("phone")),
            ])

            c.execute(text("""
              INSERT INTO ai_whatsapp_purity(
                listing_id,original_transaction,recovered_role,recovered_transaction,
                transaction_confidence,transaction_reason,original_location,recovered_location,
                original_property_type,recovered_property_type,property_type_confidence,property_type_reason,
                recovered_area_min_sqft,recovered_area_max_sqft,recovered_budget,recovered_frontage_ft,
                recovered_required_floor,recovered_suitable_for,purity_score,review_status,
                duplicate_cluster_key,source_group_name,poster_name,raw_text,last_recovered_at
              )
              VALUES(
                :id,:ot,:role,:tx,:tc,:tr,:ol,:loc,:opt,:pt,:pc,:pr,:amin,:amax,:budget,:front,
                :floor,:suit,:purity,:review,:cluster,:group,:poster,:raw,NOW()
              )
              ON CONFLICT(listing_id) DO UPDATE SET
                original_transaction=EXCLUDED.original_transaction,
                recovered_role=EXCLUDED.recovered_role,
                recovered_transaction=EXCLUDED.recovered_transaction,
                transaction_confidence=EXCLUDED.transaction_confidence,
                transaction_reason=EXCLUDED.transaction_reason,
                original_location=EXCLUDED.original_location,
                recovered_location=EXCLUDED.recovered_location,
                original_property_type=EXCLUDED.original_property_type,
                recovered_property_type=EXCLUDED.recovered_property_type,
                property_type_confidence=EXCLUDED.property_type_confidence,
                property_type_reason=EXCLUDED.property_type_reason,
                recovered_area_min_sqft=EXCLUDED.recovered_area_min_sqft,
                recovered_area_max_sqft=EXCLUDED.recovered_area_max_sqft,
                recovered_budget=EXCLUDED.recovered_budget,
                recovered_frontage_ft=EXCLUDED.recovered_frontage_ft,
                recovered_required_floor=EXCLUDED.recovered_required_floor,
                recovered_suitable_for=EXCLUDED.recovered_suitable_for,
                purity_score=EXCLUDED.purity_score,
                review_status=EXCLUDED.review_status,
                duplicate_cluster_key=EXCLUDED.duplicate_cluster_key,
                source_group_name=EXCLUDED.source_group_name,
                poster_name=EXCLUDED.poster_name,
                raw_text=EXCLUDED.raw_text,
                last_recovered_at=NOW()
            """), {
                "id": r["id"], "ot": r.get("transaction"), "role": role, "tx": tx,
                "tc": tx_conf, "tr": tx_reason, "ol": r.get("location"), "loc": loc,
                "opt": r.get("property_type"), "pt": typ, "pc": type_conf, "pr": type_reason,
                "amin": amin, "amax": amax, "budget": budget, "front": frontage,
                "floor": floor, "suit": suitable, "purity": purity, "review": review,
                "cluster": cluster, "group": r.get("source_group_name"),
                "poster": r.get("poster_name"), "raw": raw,
            })

            result["rows_processed"] += 1
            if role == "SUPPLY" and review == "USABLE":
                result["usable_supply"] += 1
            elif role == "REQUIREMENT" and review == "USABLE":
                result["usable_requirements"] += 1
            elif review == "NEEDS_REVIEW":
                result["needs_review"] += 1
            elif review == "LOW_CONFIDENCE":
                result["low_confidence"] += 1
            elif review == "UNKNOWN":
                result["unknown_after_recovery"] += 1

        result["duplicate_clusters"] = c.execute(text("""
          SELECT COUNT(*) FROM (
            SELECT duplicate_cluster_key
            FROM ai_whatsapp_purity
            WHERE duplicate_cluster_key IS NOT NULL
              AND duplicate_cluster_key <> ''
            GROUP BY duplicate_cluster_key
            HAVING COUNT(*) > 1
          ) q
        """)).scalar() or 0

    return result

def purity_rows(primary_engine, status="ALL", limit=500):
    with primary_engine.connect() as c:
        rows = c.execute(text("""
          SELECT * FROM ai_whatsapp_purity
          WHERE (:status='ALL' OR review_status=:status)
          ORDER BY purity_score DESC,last_recovered_at DESC
          LIMIT :lim
        """), {"status": status.upper(), "lim": int(limit)}).mappings().all()
        return [dict(x) for x in rows]
