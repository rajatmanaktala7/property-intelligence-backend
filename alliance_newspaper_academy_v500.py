from __future__ import annotations

import hashlib
import html
import inspect
import json
import os
import re
import threading
import time
from datetime import datetime, timezone

from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_autonomous_student_v438 as champion

VERSION = "5.0.1-ALLIANCE-NEWSPAPER-AUTONOMOUS-ACADEMY-MIXED-TX-REPAIR"
MODE = "CUMULATIVE_MIXED_SALE_RENT_REPAIR_AND_PROPERTY_TABLE_ONLY_AUDIT_NO_PRODUCTION_WRITES"
CHAMPION_VERSION = "4.3.8-ALLIANCE-STUDENT-DEMAND-OBJECT-CLOSURE"
CHAMPION_PREDICTOR_SHA256 = "8b5014fa5ec5e38b75f4da15945b357230573c88a096be9c4b2e0609ecd52694"

STATE = {
    "status": "NOT_STARTED",
    "version": VERSION,
    "last_run": None,
    "last_error": None,
    "last_result": None,
    "runs": 0,
    "production_writes": 0,
    "gold_mutations": 0,
    "whatsapp_writes": 0,
}
_CORE = None
_STARTED = False
_LOCK = threading.Lock()

DDL = [
    """CREATE TABLE IF NOT EXISTS alliance_newspaper_academy_runs(
        run_id BIGSERIAL PRIMARY KEY,
        version TEXT NOT NULL,
        champion_version TEXT NOT NULL,
        champion_hash TEXT,
        status TEXT NOT NULL,
        curriculum_accuracy NUMERIC(8,4),
        live_rows_scanned INTEGER DEFAULT 0,
        suspicious_rows INTEGER DEFAULT 0,
        metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS alliance_newspaper_academy_lessons(
        lesson_key TEXT PRIMARY KEY,
        lesson_family TEXT NOT NULL,
        description TEXT NOT NULL,
        examples_seen INTEGER DEFAULT 0,
        last_seen_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )""",
]

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

def _explicit_tx(raw):
    n = _norm(raw)

    # Parent-level mixed offering must be preserved before ordinary SALE logic.
    # Examples: "Sale / Rent", "Sale & Rent", "Sale or Rent", "sale and lease".
    mixed_offer = bool(re.search(
        r"\b(?:sale\s*(?:/|&|or|and)\s*(?:rent|lease)|"
        r"(?:rent|lease)\s*(?:/|&|or|and)\s*sale)\b", n
    ))
    if mixed_offer:
        return "AMBIGUOUS"

    sale = bool(re.search(r"\b(?:for\s+sale|available\s+for\s+sale|sale\s+@|asking\s+(?:rs|₹|\d)|price\s+(?:rs|₹|\d)|buy|purchase|resale)\b", n))
    rent = bool(re.search(r"\b(?:for\s+rent|to[- ]?let|available\s+for\s+rent|rent\s+(?:rs|₹|\d)|rental\s+requirement)\b", n))
    lease = bool(re.search(r"\b(?:for\s+lease|available\s+for\s+lease|lease\s+office|company\s+lease)\b", n))
    occupied = bool(re.search(r"\b(?:pre[- ]?rented|pre[- ]?leased|rented\s+(?:commercial|shop|office|property)|leased\s+to)\b", n))

    # Pre-rented/pre-leased is occupancy/income context, not a RENT offering.
    if sale and occupied:
        return "SALE"
    if sale and (rent or lease):
        return "AMBIGUOUS"
    if sale:
        return "SALE"
    if rent or lease:
        return "RENT"
    return "UNKNOWN"

def _class_guard(raw, lead_type=""):
    n = _norm((lead_type or "") + " " + (raw or ""))
    if re.search(r"\b(?:requirement|required|wanted|wants\s+to\s+(?:buy|purchase|rent|lease)|looking\s+for\s+(?:a\s+)?(?:flat|plot|office|shop|space|property|building|land|hotel|farmhouse))\b", n):
        return "REQUIREMENT"
    if re.search(r"\b(?:available|for\s+sale|for\s+rent|for\s+lease|to[- ]?let|asking|price|rent\s+\d|plot|flat|floor|office|showroom|land|building|bungalow|kothi|farmhouse)\b", n):
        return "PROPERTY_AVAILABILITY"
    return "UNKNOWN"

def _asset_class(raw):
    n = _norm(raw)
    for pats, label in [
        (("industrial", "factory", "shed"), "INDUSTRIAL"),
        (("office", "showroom", "shop", "retail", "commercial"), "COMMERCIAL"),
        (("farm land", "farmland", "agricultural land", " land "), "LAND"),
        (("hotel", "guest house", "banquet"), "HOSPITALITY"),
        (("flat", "apartment", "builder floor", "kothi", "bungalow", "house", "bhk"), "RESIDENTIAL"),
    ]:
        if any(x in (" " + n + " ") for x in pats):
            return label
    return "UNKNOWN"

def _areas(raw):
    s = str(raw or "")
    out = []
    patterns = [
        (r"(?i)\b(\d[\d,]*(?:\.\d+)?)\s*(sq\.?\s*ft|sqft|sft|square\s*feet)\b", "SQFT"),
        (r"(?i)\b(\d[\d,]*(?:\.\d+)?)\s*(sq\.?\s*(?:yd|yard)s?|sqyds?|yds?|yards?)\b", "SQYD"),
        (r"(?i)\b(\d[\d,]*(?:\.\d+)?)\s*(sq\.?\s*(?:m|mt|mtr|metre|meter)s?|sqm|sqmt|sq\.mtr)\b", "SQM"),
        (r"(?i)\b(\d+(?:\.\d+)?)\s*(acre|acres)\b", "ACRE"),
        (r"(?i)\b(\d+(?:\.\d+)?)\s*(bigha|bighas)\b", "BIGHA"),
    ]
    for pat, unit in patterns:
        for m in re.finditer(pat, s):
            try:
                value = float(m.group(1).replace(",", ""))
            except Exception:
                continue
            out.append({"value": value, "unit": unit, "evidence": m.group(0)})
    return out

def _money(raw):
    s = str(raw or "")
    out = []
    pats = [
        (r"(?i)(?:₹|rs\.?|inr)\s*([\d,.]+)\s*(cr|crore|crores|lac|lakh|lakhs|k)?(?:\s*(?:/|per)\s*(sq\.?\s*ft|sqft|sq\.?\s*yd|sqyd|month|pm))?", "CURRENCY"),
        (r"(?i)\b(\d+(?:\.\d+)?)\s*(cr|crore|crores|lac|lakh|lakhs)\b(?:\s*(?:/|per)\s*(sq\.?\s*ft|sqft|sq\.?\s*yd|sqyd|month|pm))?", "MAGNITUDE"),
        (r"(?i)\b(?:rent|asking|price|rate)\s*[:@-]?\s*(\d[\d,.]*)\s*(cr|crore|crores|lac|lakh|lakhs|k)?\b", "LABELED"),
    ]
    seen = set()
    for pat, kind in pats:
        for m in re.finditer(pat, s):
            ev = m.group(0).strip()
            key = ev.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append({"evidence": ev, "kind": kind})
    return out

def _phone_tokens(raw):
    s = str(raw or "")
    tokens = []
    for m in re.finditer(r"(?<!\d)(?:\+?91[ -]?)?0?\d(?:[\d -]{7,13})\d(?!\d)", s):
        digits = re.sub(r"\D", "", m.group(0))
        if digits.startswith("91") and len(digits) == 12:
            digits = digits[2:]
        elif digits.startswith("0") and len(digits) == 11:
            digits = digits[1:]
        tokens.append({"raw": m.group(0).strip(), "digits": digits, "valid_mobile": len(digits) == 10 and digits[0] in "6789"})
    return tokens

def _locality_polluted(locality):
    n = _norm(locality)
    if not n or n in {"na", "n/a", "unknown", "not specified", "-"}:
        return True
    if re.search(r"\b(?:sq\.?\s*ft|sqft|sq\.?\s*yd|sqyd|bhk|rk|floor|flr|terrace|rooms?|ground\s+floor)\b", n):
        return True
    if re.fullmatch(r"[\d\W_]+", n):
        return True
    return False

def _numeric_only(v):
    return bool(re.fullmatch(r"\s*[\d,.]+\s*", str(v or "")))

def _price_has_money_evidence(price, monies):
    digits = re.sub(r"\D", "", str(price or ""))
    if not digits:
        return False
    for m in monies:
        if digits in re.sub(r"\D", "", m.get("evidence", "")):
            return True
    return False

def _multi_property(raw):
    n = _norm(raw)
    loc_sep = len(re.findall(r"\b(?:sector|sec[- ]?|phase|block|gk[- ]?|vasant|defence|panchsheel|sundar|golf links|noida|gurgaon|gurugram|dwarka)\b", n))
    explicit = bool(re.search(r"\b(?:options|list of \d+ properties|also available|various locations|plots\s*&\s*builder floors|sale\s*/\s*rent)\b", n))
    return explicit or loc_sep >= 3

def analyze_record(raw, lead_type="", locality="", area="", price="", phone=""):
    raw = str(raw or "")
    evidence = (lead_type or "") + " " + raw
    base = champion.predict_message(raw)
    c = _class_guard(raw, lead_type)
    tx = _explicit_tx(evidence)
    areas = _areas(raw)
    monies = _money(raw)
    phones = _phone_tokens((phone or "") + " " + raw)
    reasons = []

    if _numeric_only(price) and not _price_has_money_evidence(price, monies):
        reasons.append("NUMERIC_PRICE_WITHOUT_MONEY_EVIDENCE")
    if area and str(area).strip().lower() not in {"unknown", "unspecified", "-", "na", "n/a"} and areas:
        area_norm = _norm(area)
        first = areas[0]
        if first["unit"] == "SQYD" and ("sq ft" in area_norm or "sqft" in area_norm):
            reasons.append("AREA_UNIT_CONFLICT_SQYD_AS_SQFT")
        if first["unit"] == "SQM" and ("sq ft" in area_norm or "sqft" in area_norm):
            reasons.append("AREA_UNIT_CONFLICT_SQM_AS_SQFT")
    if (not area or str(area).strip().lower() in {"unknown", "unspecified", "-", "na", "n/a"}) and areas:
        reasons.append("AREA_PRESENT_IN_EVIDENCE_BUT_MISSING_FIELD")
    if _locality_polluted(locality):
        reasons.append("LOCALITY_FRAGMENT_OR_MISSING")
    if phone and str(phone).strip() not in {"-", "NA", "Unknown"} and not any(x["valid_mobile"] for x in phones):
        reasons.append("PHONE_INVALID_OR_OCR_CONFLICT")
    if (not phone or str(phone).strip() in {"-", "NA", "Unknown"}) and any(x["valid_mobile"] for x in phones):
        reasons.append("PHONE_PRESENT_IN_EVIDENCE_BUT_MISSING_FIELD")
    if _multi_property(raw):
        reasons.append("MULTI_PROPERTY_REQUIRES_ATOMIC_SPLIT")
    if c == "REQUIREMENT" and "available" in _norm(lead_type) and "wanted" not in _norm(lead_type):
        reasons.append("LEAD_TYPE_CLASS_CONFLICT")
    if tx != "UNKNOWN" and "commercial" in _norm(lead_type) and not any(x in _norm(lead_type) for x in ["sale", "rent", "lease"]):
        reasons.append("COMMERCIAL_IS_ASSET_CLASS_NOT_TRANSACTION")
    if re.search(r"(?i)\b(?:pre[- ]?rented|pre[- ]?leased|rented\s+commercial|leased\s+to)\b", raw) and tx == "SALE":
        reasons.append("SALE_WITH_TENANTED_OCCUPANCY_SEPARATE_RENT_INCOME")

    return {
        "champion": {"class": base.get("class"), "transaction": base.get("transaction"), "ownership": base.get("ownership")},
        "challenger": {
            "class": c if c != "UNKNOWN" else base.get("class"),
            "transaction": tx if tx != "UNKNOWN" else base.get("transaction"),
            "asset_class": _asset_class(raw),
            "areas": areas,
            "money_mentions": monies,
            "phones": phones,
            "multi_property": _multi_property(raw),
        },
        "risk_reasons": sorted(set(reasons)),
        "safe_for_auto_clean": len(reasons) == 0,
    }

CURRICULUM = [
    ("height_not_price", "Industrial building 23,226 sq ft, basement + ground, 17 feet height, available for rent", "Available - Rent", "Infocity-II Gurgaon", "23226 sq ft", "17", "9811688550", "PROPERTY_AVAILABILITY", "RENT", "NUMERIC_PRICE_WITHOUT_MONEY_EVIDENCE"),
    ("rooms_not_price", "40 RK Rooms, fully furnished, rent 18 Lac p.m., suits hotel hospital", "Available - Rent", "Sector-52", "Unknown", "40", "9311139322", "PROPERTY_AVAILABILITY", "RENT", "NUMERIC_PRICE_WITHOUT_MONEY_EVIDENCE"),
    ("bhk_not_price", "4 BHK apartment available for sale in DLF Phase V", "Available - Sale", "DLF Phase V", "Unknown", "4", "9811255772", "PROPERTY_AVAILABILITY", "SALE", "NUMERIC_PRICE_WITHOUT_MONEY_EVIDENCE"),
    ("sector_not_price", "Prime bungalow in Sector-44 Noida, 450 sqm, for sale", "Available - Sale", "Sector-44 Noida", "Unknown", "44", "9228812255", "PROPERTY_AVAILABILITY", "SALE", "NUMERIC_PRICE_WITHOUT_MONEY_EVIDENCE"),
    ("road_not_price", "Industrial plot 1000 sqm, 18 mtr wide road, near metro, for sale", "Available - Sale", "Phase-2 Noida", "Unknown", "18", "9228812244", "PROPERTY_AVAILABILITY", "SALE", "NUMERIC_PRICE_WITHOUT_MONEY_EVIDENCE"),
    ("sqyd_unit", "Greater Kailash top floor 300 sq.yds, 3 beds, for rent", "Available - Rent", "Greater Kailash-I", "300 sq ft", "3", "9999107880", "PROPERTY_AVAILABILITY", "RENT", "AREA_UNIT_CONFLICT_SQYD_AS_SQFT"),
    ("sqm_unit", "Laburnum flat 4 BHK first floor area 354 sq mtr for sale", "Available - Sale", "Sushant Lok-1", "354 sq ft", "4", "9871232311", "PROPERTY_AVAILABILITY", "SALE", "AREA_UNIT_CONFLICT_SQM_AS_SQFT"),
    ("acre_missing_area", "2 Acre Land prime location main road facing preferred company lease", "Available - Lease", "Dhankot Village", "Unknown", "2", "9818962387", "PROPERTY_AVAILABILITY", "RENT", "AREA_PRESENT_IN_EVIDENCE_BUT_MISSING_FIELD"),
    ("locality_area_fragment", "Space available for immediate lease, industrial, 13,500 sq.ft", "Available - Commercial", "13500 sq.ft", "Unknown", "Price on request", "9811688550", "PROPERTY_AVAILABILITY", "RENT", "LOCALITY_FRAGMENT_OR_MISSING"),
    ("locality_room_fragment", "40 RK Rooms for rent fully furnished", "Available - Commercial", "40 RK Rooms", "Unknown", "Price on request", "9311139322", "PROPERTY_AVAILABILITY", "RENT", "LOCALITY_FRAGMENT_OR_MISSING"),
    ("commercial_not_tx_sale", "Industrial shed for sale in Greater Noida", "Available - Commercial", "Sector Site-C Greater Noida", "2400 sq.m", "Price on request", "9910008130", "PROPERTY_AVAILABILITY", "SALE", "COMMERCIAL_IS_ASSET_CLASS_NOT_TRANSACTION"),
    ("commercial_not_tx_rent", "Showroom space available for rent in GK-1", "Available - Commercial", "GK-1", "600 sq ft", "Price on request", "9811045571", "PROPERTY_AVAILABILITY", "RENT", "COMMERCIAL_IS_ASSET_CLASS_NOT_TRANSACTION"),
    ("requirement_buy", "Resale apartment wanted in DLF Privana Sector-77", "Requirement - Buy", "DLF Privana Sector-77", "Unspecified", "Price on request", "9810265671", "REQUIREMENT", "SALE", None),
    ("mixed_sale_rent", "Industrial plot with builtup facility available for Sale / Rent", "Available - Commercial", "Bhiwadi", "7630 sq.mtr", "Price on request", "9810092360", "PROPERTY_AVAILABILITY", "AMBIGUOUS", "MULTI_PROPERTY_REQUIRES_ATOMIC_SPLIT"),
    ("pre_rented_sale", "Pre-rented retail shop for sale, tenant paying rent 2.25 lakh per month, asking 4.5 Cr", "Available - Sale", "South Delhi", "2400 sq ft", "4.5 Cr", "7982834260", "PROPERTY_AVAILABILITY", "SALE", "SALE_WITH_TENANTED_OCCUPANCY_SEPARATE_RENT_INCOME"),
    ("multi_location", "Westend 1200 sq yds 2nd floor; Vasant Vihar 1200 sq yds 3rd terrace; Panchsheel Park 1200 sq yds 3rd terrace; GK 1200 sq yds 3rd terrace, all for sale", "Available - Sale", "Westend / Vasant Vihar / Panchsheel Park / GK", "400-1200 yds", "Price on request", "9810120612", "PROPERTY_AVAILABILITY", "SALE", "MULTI_PROPERTY_REQUIRES_ATOMIC_SPLIT"),
    ("phone_missing_column", "3-Star Hotel for sale, 21 Rooms, WhatsApp: 9818407171", "Available - Sale", "Prime Main Road", "12000 sq ft", "Price on request", "-", "PROPERTY_AVAILABILITY", "SALE", "PHONE_PRESENT_IN_EVIDENCE_BUT_MISSING_FIELD"),
    ("valid_rate_not_total", "Plot for sale, 2400 sq yd, Rate @ 65000/sq yd", "Available - Sale", "Gurgaon", "2400 sq yd", "65000", "9818962387", "PROPERTY_AVAILABILITY", "SALE", None),
]

def _score_curriculum():
    errors = []
    comparable = 0
    for name, raw, lead, loc, area, price, phone, exp_class, exp_tx, exp_reason in CURRICULUM:
        a = analyze_record(raw, lead, loc, area, price, phone)
        got_class = a["challenger"]["class"]
        got_tx = a["challenger"]["transaction"]
        comparable += 2
        if got_class != exp_class:
            errors.append({"name": name, "field": "class", "expected": exp_class, "got": got_class})
        if got_tx != exp_tx:
            errors.append({"name": name, "field": "transaction", "expected": exp_tx, "got": got_tx})
        if exp_reason:
            comparable += 1
            if exp_reason not in a["risk_reasons"]:
                errors.append({"name": name, "field": "lesson", "expected": exp_reason, "got": a["risk_reasons"]})
    correct = comparable - len(errors)
    return {"cases": len(CURRICULUM), "comparable_checks": comparable, "correct_checks": correct,
            "accuracy": round(100.0 * correct / max(comparable, 1), 4), "errors": errors}

def _discover_newspaper_tables(engine):
    # Audit only record-bearing newspaper property/classified tables.
    # Academy metadata, source manifests and sync-control tables do not contain
    # property locality fields and previously created false locality alarms.
    with engine.connect() as c:
        tables = [str(x) for x in c.execute(text("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema=current_schema()
              AND (table_name ILIKE '%newspaper%' OR table_name ILIKE '%classified%')
            ORDER BY table_name
        """)).scalars().all()]
    excluded_exact = {
        "alliance_newspaper_academy_lessons",
        "alliance_newspaper_academy_runs",
        "pi_newspaper_capture_sync",
        "pi_newspaper_sources",
    }
    return [
        t for t in tables
        if t not in excluded_exact
        and not t.startswith("alliance_newspaper_academy_")
        and not t.endswith("_generation")
        and not t.endswith("_format")
    ]

def _pick(row, names):
    low = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        if name in low:
            return low[name]
    return None

def _audit_table(engine, table_name, limit=5000):
    safe = re.sub(r"[^a-zA-Z0-9_]", "", table_name)
    if safe != table_name:
        return {"table": table_name, "status": "SKIPPED_UNSAFE_NAME", "rows": 0}
    with engine.connect() as c:
        rows = [dict(r) for r in c.execute(text(f"SELECT * FROM {safe} LIMIT :n"), {"n": int(limit)}).mappings()]
    metrics = {"rows": len(rows), "suspicious": 0, "numeric_price": 0, "area_conflict": 0,
               "missing_area_with_evidence": 0, "locality_pollution": 0, "phone_problem": 0,
               "multi_property": 0, "lead_type_conflict": 0, "commercial_tx_conflict": 0,
               "pre_rented_sale": 0}
    lesson_counts = {}
    for r in rows:
        raw = _pick(r, ["description", "raw_text", "details", "property_details", "remarks", "ad_text", "source_text"]) or ""
        lead = _pick(r, ["lead type", "lead_type", "transaction", "rent_or_sale", "type"]) or ""
        loc = _pick(r, ["locality", "location", "area_name", "city_location"]) or ""
        area = _pick(r, ["area", "property_area", "available_area", "available_area_sqft"]) or ""
        price = _pick(r, ["price", "rent", "price_rent", "asking_price"]) or ""
        phone = _pick(r, ["phone", "contact_phone", "contact_number", "mobile"]) or ""
        a = analyze_record(raw, lead, loc, area, price, phone)
        rs = a["risk_reasons"]
        if rs: metrics["suspicious"] += 1
        for reason in rs: lesson_counts[reason] = lesson_counts.get(reason, 0) + 1
        if "NUMERIC_PRICE_WITHOUT_MONEY_EVIDENCE" in rs: metrics["numeric_price"] += 1
        if any(x.startswith("AREA_UNIT_CONFLICT") for x in rs): metrics["area_conflict"] += 1
        if "AREA_PRESENT_IN_EVIDENCE_BUT_MISSING_FIELD" in rs: metrics["missing_area_with_evidence"] += 1
        if "LOCALITY_FRAGMENT_OR_MISSING" in rs: metrics["locality_pollution"] += 1
        if any("PHONE" in x for x in rs): metrics["phone_problem"] += 1
        if "MULTI_PROPERTY_REQUIRES_ATOMIC_SPLIT" in rs: metrics["multi_property"] += 1
        if "LEAD_TYPE_CLASS_CONFLICT" in rs: metrics["lead_type_conflict"] += 1
        if "COMMERCIAL_IS_ASSET_CLASS_NOT_TRANSACTION" in rs: metrics["commercial_tx_conflict"] += 1
        if "SALE_WITH_TENANTED_OCCUPANCY_SEPARATE_RENT_INCOME" in rs: metrics["pre_rented_sale"] += 1
    return {"table": table_name, "status": "AUDITED", "metrics": metrics, "lesson_counts": lesson_counts}

LESSON_DESCRIPTIONS = {
    "NUMERIC_PRICE_WITHOUT_MONEY_EVIDENCE": "A bare number is not price without price/currency/rent/rate evidence. It may be BHK, room count, sector, floor, road width, height or area.",
    "AREA_UNIT_CONFLICT_SQYD_AS_SQFT": "Preserve source sq yd. Never relabel sq yd as sq ft. Normalize into a separate field only.",
    "AREA_UNIT_CONFLICT_SQM_AS_SQFT": "Preserve source sq m. Never relabel sq m as sq ft. Normalize into a separate field only.",
    "AREA_PRESENT_IN_EVIDENCE_BUT_MISSING_FIELD": "If source evidence contains area, populate original area/unit while preserving evidence.",
    "LOCALITY_FRAGMENT_OR_MISSING": "Locality must be geography/project, never area/BHK/floor/room-count fragments.",
    "PHONE_INVALID_OR_OCR_CONFLICT": "Never guess malformed phone digits. Re-read source image or abstain.",
    "PHONE_PRESENT_IN_EVIDENCE_BUT_MISSING_FIELD": "Recover explicit phone/WhatsApp number from full ad evidence.",
    "MULTI_PROPERTY_REQUIRES_ATOMIC_SPLIT": "One physical property per child entity. Keep an inventory-group parent only for provenance.",
    "COMMERCIAL_IS_ASSET_CLASS_NOT_TRANSACTION": "Commercial/Residential/Industrial are asset classes, not SALE/RENT transaction labels.",
    "SALE_WITH_TENANTED_OCCUPANCY_SEPARATE_RENT_INCOME": "Pre-rented/pre-leased asset offered for capital consideration remains SALE; occupancy/rent income are separate fields.",
    "LEAD_TYPE_CLASS_CONFLICT": "Requirement grammar and availability grammar must be separated; do not rely on the old lead-type label as truth.",
}

def _save_lessons(engine, counts):
    with engine.begin() as c:
        for key, count in counts.items():
            desc = LESSON_DESCRIPTIONS.get(key, key.replace("_", " ").title())
            family = key.split("_")[0]
            c.execute(text("""
                INSERT INTO alliance_newspaper_academy_lessons(
                    lesson_key,lesson_family,description,examples_seen,last_seen_at,updated_at
                ) VALUES(:k,:f,:d,:n,NOW(),NOW())
                ON CONFLICT(lesson_key) DO UPDATE SET
                    examples_seen=alliance_newspaper_academy_lessons.examples_seen+EXCLUDED.examples_seen,
                    last_seen_at=NOW(), updated_at=NOW()
            """), {"k": key, "f": family, "d": desc, "n": int(count)})

def run_once(core, limit=5000):
    if not _LOCK.acquire(blocking=False):
        return {"status": "SKIPPED", "reason": "TRAINING_RUN_ALREADY_ACTIVE"}
    try:
        STATE["status"] = "TRAINING"
        STATE["last_error"] = None
        engine = _engine(core)
        if engine is None:
            raise RuntimeError("Core database engine unavailable")
        _install(engine)
        ch = _champion_hash()
        if champion.VERSION != CHAMPION_VERSION:
            raise RuntimeError(f"Champion version changed: {champion.VERSION}")
        if ch != CHAMPION_PREDICTOR_SHA256:
            raise RuntimeError(f"Champion predictor hash changed: {ch}")

        curriculum = _score_curriculum()
        tables = _discover_newspaper_tables(engine)
        audits, all_counts = [], {}
        live_rows = suspicious = 0
        for t in tables[:5]:
            try:
                a = _audit_table(engine, t, limit=limit)
                audits.append(a)
                m = a.get("metrics") or {}
                live_rows += int(m.get("rows") or 0)
                suspicious += int(m.get("suspicious") or 0)
                for k, v in (a.get("lesson_counts") or {}).items():
                    all_counts[k] = all_counts.get(k, 0) + int(v or 0)
            except Exception as exc:
                audits.append({"table": t, "status": "ERROR", "error": f"{type(exc).__name__}: {exc}"})

        _save_lessons(engine, all_counts)
        status = "TRAINING_PASS" if curriculum["accuracy"] == 100.0 else "TRAINING_HOLD"
        result = {
            "version": VERSION, "mode": MODE, "status": status,
            "champion": {"version": champion.VERSION, "predictor_sha256": ch, "immutable": True},
            "curriculum": curriculum, "newspaper_tables_found": tables, "live_audits": audits,
            "live_rows_scanned": live_rows, "suspicious_rows": suspicious,
            "learned_lesson_counts": all_counts,
            "next_gate": "FRESH_UNSEEN_NEWSPAPER_IMAGE_EXAM" if status == "TRAINING_PASS" else "REPAIR_CHALLENGER_BEFORE_EXAM",
            "scientific_policy": "Existing extracted database rows are not treated as truth. Automated lessons are invariant/evidence rules. Certification must use fresh unseen newspaper images with independent examiner truth.",
            "safety": {"production_writes": 0, "gold_mutations": 0, "whatsapp_writes": 0, "champion_mutations": 0},
        }
        with engine.begin() as c:
            c.execute(text("""
                INSERT INTO alliance_newspaper_academy_runs(
                    version,champion_version,champion_hash,status,curriculum_accuracy,
                    live_rows_scanned,suspicious_rows,metrics
                ) VALUES(:v,:cv,:ch,:s,:a,:lr,:sr,CAST(:m AS JSONB))
            """), {"v": VERSION, "cv": champion.VERSION, "ch": ch, "s": status,
                    "a": curriculum["accuracy"], "lr": live_rows, "sr": suspicious,
                    "m": json.dumps(result, ensure_ascii=False)})
        STATE.update({"status": status, "last_run": _utc(), "last_result": result, "runs": STATE["runs"] + 1})
        return result
    except Exception as exc:
        STATE["status"] = "ERROR"
        STATE["last_run"] = _utc()
        STATE["last_error"] = f"{type(exc).__name__}: {exc}"
        return {"status": "ERROR", "error": STATE["last_error"], "version": VERSION}
    finally:
        _LOCK.release()

def status(core):
    if STATE.get("last_result"):
        return STATE["last_result"]
    return run_once(core)

def _dashboard(core):
    s = status(core)
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Newspaper Academy 5.0</title><style>body{{font-family:Arial;background:#f5f7fb;color:#142033;margin:0}}header{{background:#102235;color:white;padding:18px}}.wrap{{padding:18px;max-width:1250px;margin:auto}}.card{{background:white;border:1px solid #e2e8f0;border-radius:12px;padding:14px;margin-bottom:12px}}pre{{white-space:pre-wrap;overflow:auto;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px}}.ok{{font-weight:bold}}</style></head><body><header><b>Alliance Newspaper Autonomous Academy 5.0</b><br><small>Certified Champion 4.3.8 remains immutable</small></header><div class='wrap'><div class='card ok'>{html.escape(str(s.get('status')))} · Curriculum {html.escape(str((s.get('curriculum') or {}).get('accuracy')))}%</div><div class='card'>Automation: newspaper failure mining → invariant lessons → challenger regression → fresh unseen image exam gate. No production/Gold/WhatsApp writes.</div><pre>{html.escape(json.dumps(s, ensure_ascii=False, indent=2))}</pre></div></body></html>"""

def register(core):
    app = _app(core)
    if not _route_exists(app, "/api/property-brain/newspaper-academy-v500/status"):
        @app.get("/api/property-brain/newspaper-academy-v500/status")
        def newspaper_academy_status():
            return status(core)
    if not _route_exists(app, "/api/property-brain/newspaper-academy-v500/run"):
        @app.post("/api/property-brain/newspaper-academy-v500/run")
        def newspaper_academy_run():
            return run_once(core)
    if not _route_exists(app, "/property-brain/newspaper-academy-v500"):
        @app.get("/property-brain/newspaper-academy-v500", response_class=HTMLResponse)
        def newspaper_academy_page():
            return HTMLResponse(_dashboard(core))
    return {"status": "REGISTERED", "version": VERSION, "route": "/property-brain/newspaper-academy-v500"}

def _loop(core):
    time.sleep(int(os.getenv("ALLIANCE_NEWSPAPER_ACADEMY_START_DELAY", "20")))
    run_once(core)
    interval = max(3600, int(os.getenv("ALLIANCE_NEWSPAPER_ACADEMY_SECONDS", "21600")))
    while True:
        time.sleep(interval)
        run_once(core)

def start(core):
    global _CORE, _STARTED
    _CORE = core
    register(core)
    if _STARTED:
        return STATE
    _STARTED = True
    threading.Thread(target=_loop, args=(core,), name="alliance-newspaper-academy", daemon=True).start()
    return STATE
