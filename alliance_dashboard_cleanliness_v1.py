from __future__ import annotations

import json
import re
from sqlalchemy import text

VERSION = "1.0.5-CLEAN-DASHBOARD-NONBLOCKING"

UNKNOWN = {"", "unknown", "na", "n/a", "none", "null", "not specified"}
PROPERTY_WORDS = (
    "commercial", "retail", "shop", "showroom", "office", "warehouse",
    "industrial", "factory", "plot", "land", "farmhouse", "villa",
    "apartment", "flat", "building", "hotel", "resort", "banquet",
    "restaurant", "cafe", "lounge", "club", "guest house", "bhk"
)

def norm(v):
    return re.sub(r"\s+", " ", str(v or "")).strip()

def unknown(v):
    return norm(v).lower() in UNKNOWN

def amount(v, suffix=""):
    s = norm(v).lower().replace("₹", "").replace("rs.", "").replace("rs", "")
    s = s.replace("inr", "").replace(",", "")
    try:
        n = float(s)
    except Exception:
        return None
    suffix = (suffix or "").lower()
    if suffix in ("l", "lac", "lacs", "lakh", "lakhs"):
        n *= 100000
    elif suffix in ("cr", "crore", "crores"):
        n *= 10000000
    elif suffix in ("k", "thousand"):
        n *= 1000
    return n

PRICE = (
    r"(?:₹\s*|rs\.?\s*)?"
    r"(\d[\d,]*(?:\.\d+)?)\s*"
    r"(l|lac|lacs|lakh|lakhs|cr|crore|crores|k|thousand)?"
)

def extract_prices(raw):
    raw = norm(raw)
    sale = None
    rent = None
    sale_patterns = [
        r"(?:for\s+sale|sale\s+price|sale\s+demand|selling\s+price|selling|sale)\s*[-:=@]?\s*" + PRICE,
        PRICE + r"\s*(?:for\s+sale|sale)",
    ]
    rent_patterns = [
        r"(?:for\s+rent|asking\s+rent|monthly\s+rent|rent|lease)\s*[-:=@]?\s*" + PRICE,
        PRICE + r"\s*(?:per\s+month|monthly|pm|rent)",
    ]
    for pattern in sale_patterns:
        m = re.search(pattern, raw, re.I)
        if m:
            sale = amount(m.group(1), m.group(2) or "")
            break
    for pattern in rent_patterns:
        m = re.search(pattern, raw, re.I)
        if m:
            rent = amount(m.group(1), m.group(2) or "")
            break
    display = []
    if sale:
        display.append("Sale ₹%.2f Cr" % (sale / 10000000))
    if rent:
        display.append("Rent ₹%.2f L/month" % (rent / 100000))
    return sale, rent, " | ".join(display)

def repair_transaction(raw, current=None):
    low = norm(raw).lower()
    sale = bool(re.search(r"\b(sale|selling|sell|resale)\b", low))
    rent = bool(re.search(r"\b(rent|lease|leasing|to let)\b", low))
    if sale and rent:
        return "SALE_RENT"
    if sale:
        return "SALE"
    if rent:
        return "RENT"
    return norm(current).upper() or "UNKNOWN"

def repair_locality(raw, current=None):
    low = norm(raw).lower()
    if "gurgaon" in low or "gurugram" in low:
        m = re.search(r"\b(?:sector|sec)[\s\-]*(\d{1,3}[a-z]?)", raw, re.I)
        if m:
            return "Gurugram, Sector " + m.group(1).upper()
        return "Gurugram"
    return norm(current) or "UNKNOWN"

def repair_property_type(raw, current=None):
    low = norm(raw).lower()
    if "showroom" in low:
        return "Commercial Showroom"
    if "office" in low:
        return "Office"
    if "shop" in low:
        return "Commercial Shop"
    if "commercial" in low or "retail" in low:
        return "Commercial / Retail"
    return norm(current) or "UNKNOWN"

def quality_status(row):
    raw = norm(
        row.get("raw_property_text") or row.get("raw_message") or
        row.get("message_text") or row.get("raw_text")
    )
    low = raw.lower()
    reasons = []
    if len(raw) < 20:
        reasons.append("TOO_SHORT")
    if not any(word in low for word in PROPERTY_WORDS):
        reasons.append("NO_PROPERTY_SIGNAL")

    requirement_words = (
        "required", "requirement", "looking for", "wanted",
        "client needs", "client requirement"
    )
    availability_words = (
        "available", "for sale", "for rent", "selling", "lease",
        "to let", "owner", "broker"
    )
    if any(x in low for x in requirement_words) and not any(x in low for x in availability_words):
        reasons.append("REQUIREMENT_NOT_INVENTORY")

    if unknown(row.get("locality")):
        reasons.append("UNKNOWN_LOCATION")
    if unknown(row.get("property_type")):
        reasons.append("UNKNOWN_PROPERTY_TYPE")
    if unknown(row.get("transaction_type") or row.get("transaction")):
        reasons.append("UNKNOWN_TRANSACTION")

    confidence = row.get("extraction_confidence") if row.get("extraction_confidence") is not None else row.get("confidence")
    try:
        confidence = float(confidence or 0)
    except Exception:
        confidence = 0
    if confidence and confidence < 65:
        reasons.append("LOW_CONFIDENCE")

    return ("UNDER_REVIEW", reasons) if reasons else ("ACTIVE", [])

def get_columns(conn):
    rows = conn.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='v81_wa_clean_properties'"
    )).fetchall()
    return {row[0] for row in rows}

def ensure_columns(engine):
    statements = [
        "ALTER TABLE v81_wa_clean_properties ADD COLUMN IF NOT EXISTS sale_price_inr NUMERIC(16,2)",
        "ALTER TABLE v81_wa_clean_properties ADD COLUMN IF NOT EXISTS rent_inr NUMERIC(16,2)",
        "ALTER TABLE v81_wa_clean_properties ADD COLUMN IF NOT EXISTS rent_per_sqft NUMERIC(14,2)",
        "ALTER TABLE v81_wa_clean_properties ADD COLUMN IF NOT EXISTS dashboard_status TEXT DEFAULT 'UNDER_REVIEW'",
        "ALTER TABLE v81_wa_clean_properties ADD COLUMN IF NOT EXISTS dashboard_exclusion_reasons JSONB DEFAULT '[]'::jsonb",
    ]
    with engine.begin() as conn:
        for sql in statements:
            conn.execute(text(sql))

def harden_clean_database(engine):
    """Explicit maintenance operation. Never called automatically at application startup."""
    ensure_columns(engine)
    totals = {"scanned": 0, "active": 0, "under_review": 0, "prices_repaired": 0}

    with engine.begin() as conn:
        columns = get_columns(conn)
        rows = conn.execute(text("SELECT * FROM v81_wa_clean_properties ORDER BY id")).mappings().all()

        for source in rows:
            row = dict(source)
            raw = (
                row.get("raw_property_text") or row.get("raw_message") or
                row.get("message_text") or row.get("raw_text") or ""
            )
            transaction = repair_transaction(raw, row.get("transaction_type") or row.get("transaction"))
            locality = repair_locality(raw, row.get("locality"))
            property_type = repair_property_type(raw, row.get("property_type"))
            sale, rent, price_display = extract_prices(raw)

            row["transaction_type"] = transaction
            row["locality"] = locality
            row["property_type"] = property_type
            status, reasons = quality_status(row)

            totals["scanned"] += 1
            totals["active" if status == "ACTIVE" else "under_review"] += 1
            if sale or rent:
                totals["prices_repaired"] += 1

            sets = [
                "sale_price_inr=:sale",
                "rent_inr=:rent",
                "dashboard_status=:status",
                "dashboard_exclusion_reasons=CAST(:reasons AS JSONB)",
                "record_status=:status",
            ]
            params = {
                "id": row["id"],
                "sale": sale,
                "rent": rent,
                "status": status,
                "reasons": json.dumps(reasons),
            }

            if "transaction_type" in columns:
                sets.append("transaction_type=:transaction")
                params["transaction"] = transaction
            elif "transaction" in columns:
                sets.append("transaction=:transaction")
                params["transaction"] = transaction

            if "locality" in columns:
                sets.append("locality=:locality")
                params["locality"] = locality
            if "property_type" in columns:
                sets.append("property_type=:property_type")
                params["property_type"] = property_type
            if "price_inr" in columns:
                sets.append("price_inr=COALESCE(:primary_price,price_inr)")
                params["primary_price"] = sale if sale is not None else rent
            if "price_display" in columns:
                sets.append("price_display=COALESCE(NULLIF(:price_display,''),price_display)")
                params["price_display"] = price_display
            if "updated_at" in columns:
                sets.append("updated_at=NOW()")

            conn.execute(
                text("UPDATE v81_wa_clean_properties SET " + ", ".join(sets) + " WHERE id=:id"),
                params,
            )

    return totals

def install_patch():
    """
    Patch explicit rebuild calls only.
    IMPORTANT: no full-table database scan is performed at application startup.
    """
    import whatsapp_clean_refinery_v81 as refinery
    import whatsapp_capture_v8 as capture

    original = getattr(refinery, "_alliance_original_rebuild", None)
    if original is None:
        original = refinery.rebuild_clean_database
        refinery._alliance_original_rebuild = original

    def hardened_rebuild(engine):
        result = original(engine)
        cleanup = harden_clean_database(engine)
        if isinstance(result, dict):
            result["dashboard_cleanliness"] = cleanup
        return result

    refinery.rebuild_clean_database = hardened_rebuild
    capture.rebuild_clean_database = hardened_rebuild

    return {
        "startup_scan": False,
        "mode": "ON_DEMAND_ONLY",
    }

def register(wrapped):
    result = {
        "version": VERSION,
        "status": "STARTING",
        "fail_safe": True,
    }
    try:
        result["startup_cleanup"] = install_patch()
        result["status"] = "REGISTERED"
    except Exception as exc:
        result["status"] = "ERROR"
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result
