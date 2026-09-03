from __future__ import annotations

import hashlib
import html
import inspect
import json
import os
import re
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone

from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_autonomous_student_v438 as champion
import alliance_newspaper_academy_v500 as newspaper

VERSION = "5.1.1-ALLIANCE-MAGAZINE-AUTONOMOUS-ACADEMY-SEMANTIC-CLOSURE"
MODE = "CUMULATIVE_LISTING_SEMANTICS_PRICE_EVIDENCE_LOCALITY_HYGIENE_NO_SOURCE_MUTATION"
CHAMPION_VERSION = "4.3.8-ALLIANCE-STUDENT-DEMAND-OBJECT-CLOSURE"
CHAMPION_PREDICTOR_SHA256 = "8b5014fa5ec5e38b75f4da15945b357230573c88a096be9c4b2e0609ecd52694"

STATE = {
    "status": "NOT_STARTED",
    "version": VERSION,
    "last_run": None,
    "last_error": None,
    "last_result": None,
    "runs": 0,
    "source_mutations": 0,
    "production_writes": 0,
    "gold_mutations": 0,
    "whatsapp_writes": 0,
}
_STARTED = False
_CORE = None
_LOCK = threading.Lock()

DDL = [
    """CREATE TABLE IF NOT EXISTS alliance_magazine_academy_runs(
        run_id BIGSERIAL PRIMARY KEY,
        version TEXT NOT NULL,
        champion_version TEXT NOT NULL,
        champion_hash TEXT,
        status TEXT NOT NULL,
        curriculum_accuracy NUMERIC(8,4),
        rows_scanned INTEGER DEFAULT 0,
        suspicious_rows INTEGER DEFAULT 0,
        shadow_rows INTEGER DEFAULT 0,
        metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS alliance_magazine_academy_lessons(
        lesson_key TEXT PRIMARY KEY,
        lesson_family TEXT NOT NULL,
        description TEXT NOT NULL,
        examples_seen INTEGER DEFAULT 0,
        last_seen_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS pi_magazine_clean_shadow(
        source_id TEXT PRIMARY KEY,
        academy_version TEXT NOT NULL,
        source_hash TEXT,
        predicted_class TEXT,
        predicted_transaction TEXT,
        predicted_asset_class TEXT,
        occupancy_status TEXT,
        locality_clean TEXT,
        original_area TEXT,
        original_area_unit TEXT,
        normalized_area_sqft NUMERIC,
        price_kind TEXT,
        contact_quality TEXT,
        duplicate_group_id TEXT,
        atomicity_status TEXT,
        risk_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
        evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
        auto_status TEXT NOT NULL DEFAULT 'REVIEW',
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )""",
]

LESSONS = {
    "COMMERCIAL_IS_ASSET_CLASS_NOT_TRANSACTION":
        "Commercial/residential/industrial are asset classes, never transaction labels.",
    "MIXED_SALE_RENT_PARENT_AMBIGUOUS":
        "A parent advertisement explicitly offering Sale/Rent or Sale/Lease stays AMBIGUOUS until atomic children are split.",
    "PRE_RENTED_SALE_OCCUPANCY_SEPARATE":
        "Pre-rented/pre-leased investment offered for consideration is SALE with occupied/tenanted status and separate rent income.",
    "NUMERIC_PRICE_WITHOUT_MONEY_EVIDENCE":
        "A bare number is not price without currency/rate/rent/asking evidence.",
    "AREA_UNIT_CONFLICT":
        "Preserve exact source area and unit; normalize separately and never relabel units.",
    "AREA_PRESENT_BUT_STRUCTURED_FIELD_MISSING":
        "If raw evidence contains area but structured area is missing, recover it with provenance.",
    "LOCALITY_FRAGMENT_OR_MISSING":
        "Locality must be geography/project identity, not area, floor, BHK, room count or ad-heading fragments.",
    "PHONE_INVALID_OR_OCR_CONFLICT":
        "Never guess malformed contact digits; keep source evidence and abstain.",
    "MULTI_PROPERTY_REQUIRES_ATOMIC_SPLIT":
        "One physical property per atomic child. Multi-property advertisements remain group parents for provenance.",
    "REQUIREMENT_NOT_IN_PROPERTY_INVENTORY":
        "Wanted/required/looking-to-buy-or-rent specifications are requirements, not availability inventory.",
    "DUPLICATE_CANONICAL_ENTITY":
        "Repeated magazine appearances become multiple evidence links to one canonical entity, not duplicate properties.",
}

def _utc():
    return datetime.now(timezone.utc).isoformat()

def _engine(core):
    return getattr(core, "engine", None)

def _app(core):
    return getattr(core, "app", None) or core

def _route_exists(app, path):
    try:
        return any(getattr(r, "path", None) == path for r in app.routes)
    except Exception:
        return False

def _install(engine):
    with engine.begin() as c:
        for stmt in DDL:
            c.execute(text(stmt))

def _champion_hash():
    try:
        payload = inspect.getsource(champion.predict_message) + inspect.getsource(champion.leading_demand_object)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    except Exception:
        return "UNAVAILABLE"

def _norm(v):
    return re.sub(r"\s+", " ", str(v or "").strip().lower())

def _table_exists(engine, table):
    with engine.connect() as c:
        return bool(c.execute(text("""
            SELECT EXISTS(
                SELECT 1 FROM information_schema.tables
                WHERE table_schema=current_schema() AND table_name=:t
            )
        """), {"t": table}).scalar())

def _columns(engine, table):
    with engine.connect() as c:
        return [str(x) for x in c.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name=:t
            ORDER BY ordinal_position
        """), {"t": table}).scalars().all()]

def _row_get(row, *names):
    low = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        if name.lower() in low:
            return low[name.lower()]
    return None

def _transaction(raw, listing_type=""):
    raw_n = _norm(raw)
    lt = _norm(listing_type)
    n = _norm((listing_type or "") + " " + (raw or ""))

    mixed = bool(re.search(
        r"\b(?:sale\s*(?:/|&|or|and)\s*(?:rent|lease)|"
        r"(?:rent|lease)\s*(?:/|&|or|and)\s*sale)\b", n
    ))
    if mixed:
        return "AMBIGUOUS"

    occupied = bool(re.search(
        r"\b(?:pre[- ]?rented|pre[- ]?leased|pre[- ]?tenanted|"
        r"rented\s+(?:shop|office|showroom|property|building)|leased\s+to)\b", n
    ))

    # Magazine listing labels are structured evidence when they explicitly say
    # Sale/Rent/Lease. Asset-class-only labels such as Commercial are not.
    lt_sale = bool(re.search(r"\b(?:sale|resale|buy)\b", lt))
    lt_rent = bool(re.search(r"\b(?:rent|rental|lease)\b", lt))

    sale = lt_sale or bool(re.search(
        r"\b(?:for\s+sale|sale\b|resale|asking\s+(?:rs|₹|\d[\d,.]*)|"
        r"price\s+(?:rs|₹|\d[\d,.]*)|@\s*\d+(?:\.\d+)?\s*(?:cr|crore|lac|lakh))\b", n
    ))
    rent = lt_rent or bool(re.search(
        r"\b(?:for\s+rent|to[- ]?let|rent\s+(?:rs\.?|₹|\d[\d,.]*)|"
        r"rental\b|for\s+lease|on\s+lease|available\s+for\s+lease|"
        r"company\s+lease|lease\s+out)\b", n
    ))

    # Occupancy income is not a rent offering when the asset is explicitly sold.
    if sale and occupied:
        return "SALE"
    if sale and rent:
        return "AMBIGUOUS"
    if sale:
        return "SALE"
    if rent:
        return "RENT"
    return "UNKNOWN"

def _occupancy(raw):
    n = _norm(raw)
    if re.search(r"\b(?:pre[- ]?rented|pre[- ]?leased|pre[- ]?tenanted|leased\s+to|rented\s+to)\b", n):
        return "TENANTED"
    if re.search(r"\b(?:vacant|ready\s+possession|ready\s+to\s+move|vacant\s+possession)\b", n):
        return "VACANT_OR_READY"
    return "UNKNOWN"

def _classify(raw, listing_type=""):
    n = _norm((listing_type or "") + " " + (raw or ""))
    lt = _norm(listing_type)

    demand = bool(re.search(
        r"\b(?:requirement|required|wanted|looking\s+for|seeking|need(?:ed)?|"
        r"wants?\s+to\s+(?:buy|purchase|rent|lease))\b", n
    ))
    demand_asset = bool(re.search(
        r"\b(?:property|plot|flat|apartment|office|shop|showroom|land|building|"
        r"warehouse|farmhouse|hotel|floor|space|villa|kothi|rooms?|rk|bhk)\b", n
    ))
    if demand and demand_asset:
        return "REQUIREMENT"

    # Explicit structured listing labels Sale/Rent/Lease indicate supply-side
    # inventory unless demand grammar above owns the object.
    if re.search(r"\b(?:sale|resale|rent|rental|lease|available)\b", lt):
        return "PROPERTY_AVAILABILITY"

    if re.search(
        r"\b(?:available|for\s+sale|for\s+rent|for\s+lease|on\s+lease|to[- ]?let|"
        r"plot|flat|apartment|office|shop|showroom|land|building|floor|warehouse|hotel|"
        r"rooms?|rk|bhk)\b", n
    ):
        return "PROPERTY_AVAILABILITY"

    p = champion.predict_message(raw or "")
    cls = p.get("class") or "UNKNOWN"
    # A group-like magazine row with concrete asset evidence must not collapse
    # to UNRESOLVED solely because the legacy Champion did not own magazine syntax.
    if cls in {"UNRESOLVED", "NOISE"} and re.search(
        r"\b(?:plot|flat|apartment|office|shop|showroom|land|building|floor|warehouse|hotel|"
        r"rooms?|rk|bhk)\b", n
    ):
        return "PROPERTY_AVAILABILITY"
    return cls

def _asset_class(raw, category=""):
    n = _norm((category or "") + " " + (raw or ""))
    rules = [
        (r"\b(?:industrial|factory|shed|warehouse|godown)\b", "INDUSTRIAL"),
        (r"\b(?:office|showroom|shop|retail|commercial|sco)\b", "COMMERCIAL"),
        (r"\b(?:hotel|banquet|guest\s*house|resort|restaurant|cafe)\b", "HOSPITALITY"),
        (r"\b(?:farm\s*land|farmland|agricultural|plot|land)\b", "LAND_OR_PLOT"),
        (r"\b(?:flat|apartment|builder\s*floor|kothi|bungalow|house|villa|bhk)\b", "RESIDENTIAL"),
    ]
    for pat, label in rules:
        if re.search(pat, n):
            return label
    return "UNKNOWN"

def _area_candidates(raw):
    s = str(raw or "")
    pats = [
        (r"(?i)\b(\d[\d,]*(?:\.\d+)?)\s*(sq\.?\s*ft|sqft|sft|square\s*feet)\b", "SQFT", 1.0),
        (r"(?i)\b(\d[\d,]*(?:\.\d+)?)\s*(sq\.?\s*(?:yd|yard)s?|sqyds?|yds?|yards?)\b", "SQYD", 9.0),
        (r"(?i)\b(\d[\d,]*(?:\.\d+)?)\s*(sq\.?\s*(?:m|mt|mtr|metre|meter)s?|sqm|sqmt|sq\.mtr)\b", "SQM", 10.7639104167),
        (r"(?i)\b(\d+(?:\.\d+)?)\s*(acre|acres)\b", "ACRE", 43560.0),
    ]
    out = []
    for pat, unit, mult in pats:
        for m in re.finditer(pat, s):
            try:
                val = float(m.group(1).replace(",", ""))
            except Exception:
                continue
            out.append({
                "value": val,
                "unit": unit,
                "normalized_sqft": round(val * mult, 2),
                "evidence": m.group(0),
            })
    return out

def _price_kind(raw, price):
    # Classify the structured Price field itself. Raw text may contain a
    # different legitimate rent/price and must not sanitize a contaminated
    # bare number copied from BHK/room/sector/area.
    p = _norm(price)
    if not p:
        return "UNKNOWN"
    if re.search(r"\b(?:/|per)\s*(?:sq\.?\s*ft|sqft|sq\.?\s*yd|sqyd|month|pm)\b", p):
        return "RATE_OR_RENT_RATE"
    if re.search(r"\b(?:cr|crore|crores|lac|lakh|lakhs|₹|rs\.?|inr)\b", p):
        return "MONEY_AMOUNT"
    if re.fullmatch(r"[\d,.]+", p):
        return "BARE_NUMBER"
    return "TEXT_PRICE"

def _bare_price_supported_by_same_number(raw, price):
    digits = re.sub(r"\D", "", str(price or ""))
    if not digits:
        return False
    # Require the SAME number to appear in explicit money context.
    money_patterns = [
        rf"(?i)(?:₹|rs\.?|inr)\s*{re.escape(str(price).strip())}\b",
        rf"(?i)\b(?:price|asking|rent|rate)\s*[:@-]?\s*{re.escape(str(price).strip())}\b",
        rf"(?i)\b{re.escape(str(price).strip())}\s*(?:cr|crore|crores|lac|lakh|lakhs)\b",
    ]
    return any(re.search(pat, str(raw or "")) for pat in money_patterns)

def _phones(raw):
    s = str(raw or "")
    out = []
    for m in re.finditer(r"(?<!\d)(?:\+?91[ -]?)?0?\d(?:[\d -]{7,13})\d(?!\d)", s):
        digits = re.sub(r"\D", "", m.group(0))
        if digits.startswith("91") and len(digits) == 12:
            digits = digits[2:]
        elif digits.startswith("0") and len(digits) == 11:
            digits = digits[1:]
        valid = len(digits) == 10 and digits[:1] in "6789"
        out.append({"raw": m.group(0).strip(), "digits": digits, "valid": valid})
    return out

def _locality_clean(locality):
    v = str(locality or "").strip()
    n = _norm(v)
    if not n or n in {"unknown", "na", "n/a", "-", "not specified"}:
        return None
    if re.search(r"\b(?:sq\.?\s*ft|sqft|sq\.?\s*yd|sqyd|bhk|rk|rooms?|floor|flr|terrace)\b", n):
        return None
    if re.fullmatch(r"[\d\W_]+", n):
        return None
    return v

def _locality_status(locality):
    v = str(locality or "").strip()
    n = _norm(v)
    if not n or n in {"unknown", "na", "n/a", "-", "not specified"}:
        return "MISSING"
    if _locality_clean(locality) is None:
        return "POLLUTED"
    return "VALID_OR_UNPROVEN"

def _multi_property(raw):
    n = _norm(raw)
    if re.search(r"\b(?:also\s+available|many\s+more|options|list\s+of\s+\d+|various\s+locations)\b", n):
        return True
    if len(re.findall(r"\b(?:sector|phase|block|gk[- ]?\d|vasant|defence|panchsheel|noida|gurgaon|gurugram|dwarka)\b", n)) >= 3:
        return True
    if len(_area_candidates(raw)) >= 3 and re.search(r"[,;/&]", str(raw or "")):
        return True
    return False

def _source_hash(row):
    payload = json.dumps(row, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def _identity_key(locality, area, category, contacts, raw):
    loc = _norm(locality)
    cat = _norm(category)
    area_n = re.sub(r"\s+", "", _norm(area))
    phone = ""
    for p in contacts:
        if p.get("valid"):
            phone = p.get("digits") or ""
            break
    # Phone alone must never create duplicate identity.
    text_signal = " ".join(re.findall(r"[a-z0-9]+", _norm(raw)))[:180]
    return hashlib.sha256(f"{loc}|{area_n}|{cat}|{text_signal}".encode()).hexdigest()[:24], phone

def analyze(row):
    sid = str(_row_get(row, "source_id", "id", "record_id") or "").strip()
    raw = str(_row_get(row, "original_raw_text", "raw_text", "remarks", "description", "configuration") or "")
    listing = str(_row_get(row, "listing_type", "lead_type", "transaction", "record_status") or "")
    category = str(_row_get(row, "category", "property_type") or "")
    locality = _row_get(row, "locality", "location")
    area = _row_get(row, "area", "available_area", "area_original")
    unit = _row_get(row, "area_unit", "unit")
    price = _row_get(row, "price", "asking_price")
    valid_mobiles = str(_row_get(row, "valid_mobiles", "phone", "phones", "contact_phone") or "")
    partial = str(_row_get(row, "partial_contacts") or "")
    contacts = _phones(valid_mobiles + " " + partial + " " + raw)

    cls = _classify(raw, listing)
    tx = _transaction(raw, listing)
    occ = _occupancy(raw)
    asset = _asset_class(raw, category)
    areas = _area_candidates(raw)
    loc_clean = _locality_clean(locality)
    reasons = []

    if cls == "REQUIREMENT":
        reasons.append("REQUIREMENT_NOT_IN_PROPERTY_INVENTORY")
    if "commercial" in _norm(listing) and not re.search(r"\b(?:sale|rent|lease)\b", _norm(listing)):
        reasons.append("COMMERCIAL_IS_ASSET_CLASS_NOT_TRANSACTION")
    if tx == "AMBIGUOUS" and re.search(r"(?i)\bsale\s*(?:/|&|or|and)\s*(?:rent|lease)\b", raw + " " + listing):
        reasons.append("MIXED_SALE_RENT_PARENT_AMBIGUOUS")
    if occ == "TENANTED" and tx == "SALE":
        reasons.append("PRE_RENTED_SALE_OCCUPANCY_SEPARATE")
    if _price_kind(raw, price) == "BARE_NUMBER":
        if not _bare_price_supported_by_same_number(raw, price):
            reasons.append("NUMERIC_PRICE_WITHOUT_MONEY_EVIDENCE")

    structured_area_missing = not str(area or "").strip() or _norm(area) in {"unknown", "na", "n/a", "-"}
    if structured_area_missing and areas:
        reasons.append("AREA_PRESENT_BUT_STRUCTURED_FIELD_MISSING")
    if area and unit and areas:
        u = _norm(unit)
        source_unit = areas[0]["unit"]
        if (source_unit == "SQYD" and "ft" in u) or (source_unit == "SQM" and "ft" in u):
            reasons.append("AREA_UNIT_CONFLICT")

    loc_status = _locality_status(locality)
    # Missing locality is incomplete data, but not automatically a wrong
    # extraction. Only a non-empty polluted locality is a proven quality defect.
    if loc_status == "POLLUTED":
        reasons.append("LOCALITY_FRAGMENT_OR_MISSING")

    phone_quality = "NO_CONTACT"
    if contacts:
        if any(p["valid"] for p in contacts):
            phone_quality = "VALID_MOBILE_PRESENT"
        else:
            phone_quality = "OCR_CONFLICT_OR_INVALID"
            reasons.append("PHONE_INVALID_OR_OCR_CONFLICT")

    atomic = "GROUP_PARENT" if _multi_property(raw) else "ATOMIC_OR_UNPROVEN"
    if atomic == "GROUP_PARENT":
        reasons.append("MULTI_PROPERTY_REQUIRES_ATOMIC_SPLIT")

    first_area = areas[0] if areas else None
    return {
        "source_id": sid,
        "source_hash": _source_hash(row),
        "predicted_class": cls,
        "predicted_transaction": tx,
        "predicted_asset_class": asset,
        "occupancy_status": occ,
        "locality_clean": loc_clean,
        "original_area": str(area or "") or None,
        "original_area_unit": str(unit or "") or (first_area["unit"] if first_area else None),
        "normalized_area_sqft": first_area["normalized_sqft"] if first_area else None,
        "price_kind": _price_kind(raw, price),
        "contact_quality": phone_quality,
        "atomicity_status": atomic,
        "risk_reasons": sorted(set(reasons)),
        "evidence": {
            "raw_text": raw,
            "listing_type": listing,
            "category": category,
            "locality_original": locality,
            "locality_status": _locality_status(locality),
            "area_candidates": areas,
            "contacts": contacts,
            "price_original": price,
        },
        "auto_status": "SAFE_SHADOW" if not reasons else "REVIEW",
        "_identity": _identity_key(locality, area, category, contacts, raw),
    }

CURRICULUM = [
    ("commercial_sale",
     {"source_id":"M1","listing_type":"Available - Commercial","category":"Industrial","locality":"Noida","area":"2400","area_unit":"SQM","price":"Price on request","valid_mobiles":"9910008130","original_raw_text":"Industrial Shed for Sale in Greater Noida, 2400 sq.m"},
     "PROPERTY_AVAILABILITY","SALE","COMMERCIAL_IS_ASSET_CLASS_NOT_TRANSACTION"),
    ("mixed_sale_rent",
     {"source_id":"M2","listing_type":"Available","category":"Industrial","locality":"Bhiwadi","area":"7630","area_unit":"SQM","price":"Price on request","valid_mobiles":"9810092360","original_raw_text":"Industrial plot with built-up facility available for Sale / Rent"},
     "PROPERTY_AVAILABILITY","AMBIGUOUS","MIXED_SALE_RENT_PARENT_AMBIGUOUS"),
    ("pre_rented_sale",
     {"source_id":"M3","listing_type":"Sale","category":"Commercial","locality":"South Delhi","area":"2400","area_unit":"SQFT","price":"4.5 Cr","valid_mobiles":"7982834260","original_raw_text":"Pre-rented commercial showroom for sale, tenant rent 2.25 lakh pm, asking 4.5 Cr"},
     "PROPERTY_AVAILABILITY","SALE","PRE_RENTED_SALE_OCCUPANCY_SEPARATE"),
    ("numeric_not_price",
     {"source_id":"M4","listing_type":"Rent","category":"Hotel","locality":"Sector 52","area":"","area_unit":"","price":"40","valid_mobiles":"9311139322","original_raw_text":"40 RK Rooms fully furnished, rent 18 Lac p.m."},
     "PROPERTY_AVAILABILITY","RENT","NUMERIC_PRICE_WITHOUT_MONEY_EVIDENCE"),
    ("sqyd_preserve",
     {"source_id":"M5","listing_type":"Sale","category":"Residential","locality":"GK-1","area":"300","area_unit":"SQFT","price":"7 Cr","valid_mobiles":"9999107880","original_raw_text":"300 sq yds 4 BHK floor for sale asking 7 Cr"},
     "PROPERTY_AVAILABILITY","SALE","AREA_UNIT_CONFLICT"),
    ("locality_fragment",
     {"source_id":"M6","listing_type":"Rent","category":"Commercial","locality":"2245 sqft","area":"","area_unit":"","price":"Price on request","valid_mobiles":"9910606875","original_raw_text":"Jasola Baani Tower 2245 sqft office available for rent"},
     "PROPERTY_AVAILABILITY","RENT","LOCALITY_FRAGMENT_OR_MISSING"),
    ("requirement",
     {"source_id":"M7","listing_type":"Wanted","category":"Office","locality":"Gurugram","area":"5000","area_unit":"SQFT","price":"","valid_mobiles":"9810000000","original_raw_text":"Required 5000 sqft office on lease in Gurugram for corporate client"},
     "REQUIREMENT","RENT","REQUIREMENT_NOT_IN_PROPERTY_INVENTORY"),
    ("multi_property",
     {"source_id":"M8","listing_type":"Sale","category":"Residential","locality":"South Delhi","area":"","area_unit":"","price":"Varies","valid_mobiles":"8287970846","original_raw_text":"GK-1 208 yds @ 7 Cr; Defence Colony 325 yds @ 13.5 Cr; Lajpat Nagar 300 yds @ 10.5 Cr"},
     "PROPERTY_AVAILABILITY","SALE","MULTI_PROPERTY_REQUIRES_ATOMIC_SPLIT"),
]

def _score_curriculum():
    errors = []
    total = 0
    correct = 0
    for name, row, exp_cls, exp_tx, exp_reason in CURRICULUM:
        a = analyze(row)
        for field, got, exp in [
            ("class", a["predicted_class"], exp_cls),
            ("transaction", a["predicted_transaction"], exp_tx),
        ]:
            total += 1
            if got == exp:
                correct += 1
            else:
                errors.append({"name":name,"field":field,"expected":exp,"got":got})
        total += 1
        if exp_reason in a["risk_reasons"]:
            correct += 1
        else:
            errors.append({"name":name,"field":"lesson","expected":exp_reason,"got":a["risk_reasons"]})
    return {
        "cases": len(CURRICULUM),
        "checks": total,
        "correct": correct,
        "accuracy": round(100*correct/max(total,1),4),
        "errors": errors,
    }

def _fetch_rows(engine, limit=10000):
    if not _table_exists(engine, "pi_magazine_master"):
        return []
    cols = _columns(engine, "pi_magazine_master")
    order = "source_id" if "source_id" in cols else cols[0]
    with engine.connect() as c:
        return [dict(r) for r in c.execute(
            text(f"SELECT * FROM pi_magazine_master ORDER BY {order} LIMIT :n"),
            {"n": int(limit)}
        ).mappings()]

def _duplicate_groups(analyses):
    buckets = defaultdict(list)
    for a in analyses:
        ident, phone = a.pop("_identity")
        buckets[ident].append((a, phone))
    groups = {}
    gid_no = 0
    for ident, vals in buckets.items():
        if len(vals) < 2:
            continue
        # Same-phone alone is never sufficient; identity key excludes phone.
        gid_no += 1
        gid = f"MAG-DUP-{gid_no:05d}"
        for a, _phone in vals:
            groups[a["source_id"]] = gid
            if "DUPLICATE_CANONICAL_ENTITY" not in a["risk_reasons"]:
                a["risk_reasons"].append("DUPLICATE_CANONICAL_ENTITY")
                a["risk_reasons"].sort()
                a["auto_status"] = "REVIEW"
    return groups

def _write_shadow(engine, analyses, groups):
    with engine.begin() as c:
        for a in analyses:
            sid = a["source_id"]
            if not sid:
                continue
            c.execute(text("""
                INSERT INTO pi_magazine_clean_shadow(
                    source_id,academy_version,source_hash,predicted_class,predicted_transaction,
                    predicted_asset_class,occupancy_status,locality_clean,original_area,
                    original_area_unit,normalized_area_sqft,price_kind,contact_quality,
                    duplicate_group_id,atomicity_status,risk_reasons,evidence,auto_status,updated_at
                ) VALUES(
                    :sid,:v,:sh,:cl,:tx,:asset,:occ,:loc,:oa,:ou,:sqft,:pk,:cq,:dg,:atom,
                    CAST(:rr AS JSONB),CAST(:ev AS JSONB),:st,NOW()
                )
                ON CONFLICT(source_id) DO UPDATE SET
                    academy_version=EXCLUDED.academy_version,
                    source_hash=EXCLUDED.source_hash,
                    predicted_class=EXCLUDED.predicted_class,
                    predicted_transaction=EXCLUDED.predicted_transaction,
                    predicted_asset_class=EXCLUDED.predicted_asset_class,
                    occupancy_status=EXCLUDED.occupancy_status,
                    locality_clean=EXCLUDED.locality_clean,
                    original_area=EXCLUDED.original_area,
                    original_area_unit=EXCLUDED.original_area_unit,
                    normalized_area_sqft=EXCLUDED.normalized_area_sqft,
                    price_kind=EXCLUDED.price_kind,
                    contact_quality=EXCLUDED.contact_quality,
                    duplicate_group_id=EXCLUDED.duplicate_group_id,
                    atomicity_status=EXCLUDED.atomicity_status,
                    risk_reasons=EXCLUDED.risk_reasons,
                    evidence=EXCLUDED.evidence,
                    auto_status=EXCLUDED.auto_status,
                    updated_at=NOW()
            """), {
                "sid":sid,"v":VERSION,"sh":a["source_hash"],"cl":a["predicted_class"],
                "tx":a["predicted_transaction"],"asset":a["predicted_asset_class"],
                "occ":a["occupancy_status"],"loc":a["locality_clean"],
                "oa":a["original_area"],"ou":a["original_area_unit"],
                "sqft":a["normalized_area_sqft"],"pk":a["price_kind"],
                "cq":a["contact_quality"],"dg":groups.get(sid),
                "atom":a["atomicity_status"],
                "rr":json.dumps(a["risk_reasons"], ensure_ascii=False),
                "ev":json.dumps(a["evidence"], ensure_ascii=False, default=str),
                "st":a["auto_status"],
            })

def _save_lessons(engine, counts):
    with engine.begin() as c:
        for key, count in counts.items():
            desc = LESSONS.get(key, key.replace("_"," ").title())
            c.execute(text("""
                INSERT INTO alliance_magazine_academy_lessons(
                    lesson_key,lesson_family,description,examples_seen,last_seen_at,updated_at
                ) VALUES(:k,:f,:d,:n,NOW(),NOW())
                ON CONFLICT(lesson_key) DO UPDATE SET
                    examples_seen=alliance_magazine_academy_lessons.examples_seen+EXCLUDED.examples_seen,
                    last_seen_at=NOW(),updated_at=NOW()
            """), {"k":key,"f":key.split("_")[0],"d":desc,"n":int(count)})

def run_once(core, limit=10000):
    if not _LOCK.acquire(blocking=False):
        return {"status":"SKIPPED","reason":"MAGAZINE_TRAINING_ALREADY_ACTIVE"}
    try:
        engine = _engine(core)
        if engine is None:
            raise RuntimeError("Core engine unavailable")
        _install(engine)

        ch = _champion_hash()
        if champion.VERSION != CHAMPION_VERSION:
            raise RuntimeError(f"Champion version changed: {champion.VERSION}")
        if ch != CHAMPION_PREDICTOR_SHA256:
            raise RuntimeError(f"Champion hash changed: {ch}")

        curriculum = _score_curriculum()
        rows = _fetch_rows(engine, limit=limit)
        analyses = [analyze(r) for r in rows]
        groups = _duplicate_groups(analyses)

        counts = defaultdict(int)
        suspicious = 0
        safe = 0
        requirements = 0
        mixed = 0
        pre_rented_sales = 0
        multi = 0
        invalid_contacts = 0
        for a in analyses:
            if a["risk_reasons"]:
                suspicious += 1
            else:
                safe += 1
            if a["predicted_class"] == "REQUIREMENT":
                requirements += 1
            if a["predicted_transaction"] == "AMBIGUOUS":
                mixed += 1
            if a["occupancy_status"] == "TENANTED" and a["predicted_transaction"] == "SALE":
                pre_rented_sales += 1
            if a["atomicity_status"] == "GROUP_PARENT":
                multi += 1
            if a["contact_quality"] == "OCR_CONFLICT_OR_INVALID":
                invalid_contacts += 1
            for reason in a["risk_reasons"]:
                counts[reason] += 1

        _save_lessons(engine, counts)
        _write_shadow(engine, analyses, groups)

        status = "TRAINING_PASS" if curriculum["accuracy"] == 100.0 else "TRAINING_HOLD"
        result = {
            "version": VERSION,
            "mode": MODE,
            "status": status,
            "champion": {"version": champion.VERSION, "sha256": ch, "immutable": True},
            "curriculum": curriculum,
            "magazine_master_found": _table_exists(engine, "pi_magazine_master"),
            "magazine_master_columns": _columns(engine, "pi_magazine_master") if _table_exists(engine, "pi_magazine_master") else [],
            "rows_scanned": len(rows),
            "suspicious_rows": suspicious,
            "safe_shadow_rows": safe,
            "shadow_rows_written": len(analyses),
            "requirements_detected": requirements,
            "mixed_transaction_rows": mixed,
            "pre_rented_sale_rows": pre_rented_sales,
            "multi_property_group_rows": multi,
            "invalid_or_ocr_contact_rows": invalid_contacts,
            "duplicate_groups": len(set(groups.values())),
            "duplicate_records": len(groups),
            "lesson_counts": dict(counts),
            "next_gate": "FRESH_UNSEEN_MAGAZINE_PAGE_EXAM" if status == "TRAINING_PASS" else "REPAIR_CHALLENGER",
            "policy": "pi_magazine_master is immutable source evidence. Corrections go only to pi_magazine_clean_shadow until a fresh unseen magazine-page exam independently certifies the challenger.",
            "safety": {
                "source_mutations": 0,
                "production_writes": 0,
                "gold_mutations": 0,
                "whatsapp_writes": 0,
                "champion_mutations": 0,
            },
        }
        with engine.begin() as c:
            c.execute(text("""
                INSERT INTO alliance_magazine_academy_runs(
                    version,champion_version,champion_hash,status,curriculum_accuracy,
                    rows_scanned,suspicious_rows,shadow_rows,metrics
                ) VALUES(:v,:cv,:ch,:s,:a,:r,:sr,:sh,CAST(:m AS JSONB))
            """), {
                "v":VERSION,"cv":champion.VERSION,"ch":ch,"s":status,
                "a":curriculum["accuracy"],"r":len(rows),"sr":suspicious,
                "sh":len(analyses),"m":json.dumps(result,ensure_ascii=False),
            })

        STATE.update({
            "status":status,"last_run":_utc(),"last_result":result,
            "last_error":None,"runs":STATE["runs"]+1,
        })
        return result
    except Exception as exc:
        STATE["status"] = "ERROR"
        STATE["last_run"] = _utc()
        STATE["last_error"] = f"{type(exc).__name__}: {exc}"
        return {"status":"ERROR","version":VERSION,"error":STATE["last_error"]}
    finally:
        _LOCK.release()

def status(core):
    if STATE.get("last_result"):
        return STATE["last_result"]
    return run_once(core)

def _dashboard(core):
    s = status(core)
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Magazine Academy 5.1</title><style>
body{{font-family:Arial;margin:0;background:#f4f7fb;color:#172033}}
header{{background:#102235;color:#fff;padding:18px}}.wrap{{max-width:1250px;margin:auto;padding:18px}}
.card{{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:14px;margin-bottom:12px}}
pre{{white-space:pre-wrap;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px;overflow:auto}}
</style></head><body><header><b>Alliance Magazine Autonomous Academy 5.1</b><br>
<small>Forensic audit + shadow clean database · Champion 4.3.8 immutable</small></header>
<div class='wrap'><div class='card'><b>{html.escape(str(s.get("status")))}</b> ·
Curriculum {html.escape(str((s.get("curriculum") or {}).get("accuracy")))}% ·
Rows {html.escape(str(s.get("rows_scanned",0)))} · Suspicious {html.escape(str(s.get("suspicious_rows",0)))}</div>
<div class='card'>Original magazine master is never overwritten. Every correction is evidence-backed in pi_magazine_clean_shadow.</div>
<pre>{html.escape(json.dumps(s,ensure_ascii=False,indent=2))}</pre></div></body></html>"""

def register(core):
    app = _app(core)
    if not _route_exists(app, "/api/property-brain/magazine-academy-v510/status"):
        @app.get("/api/property-brain/magazine-academy-v510/status")
        def magazine_status():
            return status(core)
    if not _route_exists(app, "/api/property-brain/magazine-academy-v510/run"):
        @app.post("/api/property-brain/magazine-academy-v510/run")
        def magazine_run():
            return run_once(core)
    if not _route_exists(app, "/property-brain/magazine-academy-v510"):
        @app.get("/property-brain/magazine-academy-v510", response_class=HTMLResponse)
        def magazine_page():
            return HTMLResponse(_dashboard(core))
    return {"status":"REGISTERED","version":VERSION,"route":"/property-brain/magazine-academy-v510"}

def _loop(core):
    time.sleep(int(os.getenv("ALLIANCE_MAGAZINE_ACADEMY_START_DELAY","25")))
    run_once(core)
    interval=max(3600,int(os.getenv("ALLIANCE_MAGAZINE_ACADEMY_SECONDS","21600")))
    while True:
        time.sleep(interval)
        run_once(core)

def start(core):
    global _CORE,_STARTED
    _CORE=core
    register(core)
    if _STARTED:
        return STATE
    _STARTED=True
    threading.Thread(target=_loop,args=(core,),name="alliance-magazine-academy",daemon=True).start()
    return STATE
