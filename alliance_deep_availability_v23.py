from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Body, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_world_topper_tutor_v22 as tutor_v22

VERSION = "2.3.0-DEEP-AVAILABILITY-INTELLIGENCE"
MODE = "MAXIMUM_EXPLICIT_EXTRACTION_PLUS_TUTOR"
EXTRACTOR_VERSION = "ALLIANCE_AVAILABILITY_EXTRACTOR_V1"

STATE = {
    "worker_started": False,
    "worker_alive": False,
    "last_poll_at": None,
    "last_extract_at": None,
    "last_error": None,
    "rows_seen": 0,
    "rows_profiled": 0,
}
_LOCK = threading.Lock()
_STARTED = False

DDL = [
    """
    CREATE TABLE IF NOT EXISTS alliance_topper_availability_intelligence (
        intelligence_id UUID PRIMARY KEY,
        source_system TEXT NOT NULL DEFAULT 'WHATSAPP_LIVE',
        entity_id TEXT NOT NULL,
        source_id TEXT,
        message_id TEXT,
        source_item_no INTEGER,
        raw_text TEXT NOT NULL,
        parent_message_text TEXT,
        deep_profile JSONB NOT NULL,
        evidence_map JSONB NOT NULL,
        missing_fields JSONB NOT NULL,
        extraction_warnings JSONB NOT NULL,
        learning_signals JSONB NOT NULL,
        completeness_score NUMERIC(5,2),
        extractor_version TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(source_system, entity_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS alliance_topper_field_mastery (
        mastery_id UUID PRIMARY KEY,
        extractor_version TEXT NOT NULL,
        field_name TEXT NOT NULL,
        observed_count INTEGER NOT NULL DEFAULT 0,
        extracted_count INTEGER NOT NULL DEFAULT 0,
        missing_count INTEGER NOT NULL DEFAULT 0,
        warning_count INTEGER NOT NULL DEFAULT 0,
        extraction_rate NUMERIC(6,4),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(extractor_version, field_name)
    )
    """,
]

PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s\-]?)?[6-9](?:[\s\-]?\d){9}(?!\d)")
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.I)
URL_RE = re.compile(r"https?://[^\s<>]+", re.I)

FIELD_PATTERNS = {
    "configuration": [
        re.compile(r"\b(\d+(?:\.\d+)?)\s*BHK(?:\s*\+\s*(SQ|SERVANT|STUDY))?\b", re.I),
    ],
    "floor": [
        re.compile(r"\b(?:ON\s+)?(\d+(?:ST|ND|RD|TH)?|GROUND|LOWER\s+GROUND|UPPER\s+GROUND)\s+FLOOR\b", re.I),
        re.compile(r"\bFLOOR\s*[:\-]\s*([A-Z0-9+\- ]{1,30})", re.I),
    ],
    "total_floors": [
        re.compile(r"\b(?:TOTAL\s+FLOORS?|OF)\s*[:\-]?\s*(\d+)\b", re.I),
        re.compile(r"\b(\d+)\s*FLOOR\s+BUILDING\b", re.I),
    ],
    "furnishing": [
        re.compile(r"\b(FULLY\s+FURNISHED|SEMI[\s\-]?FURNISHED|UNFURNISHED|BARE\s+SHELL|WARM\s+SHELL)\b", re.I),
    ],
    "facing": [
        re.compile(r"\b(NORTH(?:\s+EAST)?|SOUTH(?:\s+EAST)?|NORTH(?:\s+WEST)?|SOUTH(?:\s+WEST)?|EAST|WEST)\s+FACING\b", re.I),
    ],
    "view": [
        re.compile(r"\b(ARAVALI|POOL|PARK|GREEN\s+BELT|SEA|RIVER|GOLF|SUN|CITY)\s+FACING\b", re.I),
        re.compile(r"\bWITH\s+(SEA|PARK|POOL|GREEN\s+BELT|ARAVALI|GOLF)\s+VIEW\b", re.I),
    ],
    "parking": [
        re.compile(r"\b(\d+)\s+(?:CAR\s+)?PARKING\b", re.I),
        re.compile(r"\bPARKING\s*[:\-]\s*(\d+)\b", re.I),
    ],
    "road_width": [
        re.compile(r"\b(\d+(?:\.\d+)?)\s*(M|MTR|MTRS|METRE|METRES|FT|FEET)\s+(?:WIDE\s+)?ROAD\b", re.I),
    ],
    "frontage": [
        re.compile(r"\bFRONTAGE\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*(FT|FEET|M|MTR|MTRS|METRE|METRES)?\b", re.I),
    ],
    "property_age": [
        re.compile(r"\b(\d+(?:\.\d+)?)\s*YEARS?\s+OLD\b", re.I),
        re.compile(r"\bAGE\s*[:\-]\s*(\d+(?:\.\d+)?)\s*YEARS?\b", re.I),
    ],
    "possession": [
        re.compile(r"\b(READY\s+TO\s+MOVE|IMMEDIATE(?:LY)?|VACANT|POSSESSION\s+IN\s+[A-Z0-9 ]+|UNDER\s+CONSTRUCTION)\b", re.I),
    ],
    "availability": [
        re.compile(r"\b(AVAILABLE|VACANT|RENTED|PRE[\s\-]?RENTED|LEASED|OWNER\s+OCCUPIED)\b", re.I),
    ],
    "lift": [
        re.compile(r"\b(NO\s+LIFT|WITH\s+LIFT|LIFT\s+AVAILABLE|ELEVATOR)\b", re.I),
    ],
    "power": [
        re.compile(r"\bPOWER\s*(?:LOAD)?\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*(KW|KVA)\b", re.I),
    ],
    "ceiling_height": [
        re.compile(r"\bCEILING\s+HEIGHT\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*(FT|FEET|M|METRE|METRES)\b", re.I),
    ],
    "deposit": [
        re.compile(r"\b(?:SECURITY|DEPOSIT)\s*[:\-]?\s*(?:RS\.?|₹)?\s*([\d,.]+)\s*(K|L|LAC|LAKH|CR|CRORE)?\b", re.I),
    ],
    "maintenance": [
        re.compile(r"\b(?:CAM|MAINTENANCE)\s*[:\-]?\s*(?:RS\.?|₹)?\s*([\d,.]+)\s*(K|L|LAC|LAKH)?\b", re.I),
    ],
    "brokerage": [
        re.compile(r"\b(\d+(?:\.\d+)?)\s*MONTH(?:S)?\s+BROKERAGE\b", re.I),
        re.compile(r"\bBROKERAGE\s*[:\-]?\s*([A-Z0-9.% ]{1,30})", re.I),
    ],
    "property_id": [
        re.compile(r"\b(?:PROPERTY\s*ID|ID)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-_/ ]{2,40})", re.I),
    ],
}

AREA_PATTERNS = [
    ("sqft", re.compile(r"(?<!\d)(\d[\d,.]*)\s*(?:SQ\.?\s*FT|SQFT|SFT)\b", re.I)),
    ("sqyd", re.compile(r"(?<!\d)(\d[\d,.]*)\s*(?:SQ\.?\s*YDS?|SQYD|SQYARD|SQ\s*YARDS?|YARDS?|GAJ)\b", re.I)),
    ("sqm", re.compile(r"(?<!\d)(\d[\d,.]*)\s*(?:SQ\.?\s*M(?:TRS?)?|SQM|SQ\s*METRES?|SQ\s*METERS?)\b", re.I)),
    ("acre", re.compile(r"(?<!\d)(\d[\d,.]*)\s*ACRES?\b", re.I)),
]

MONEY_PATTERNS = [
    ("rent", re.compile(r"\bRENT\s*[:@\-]?\s*(?:RS\.?|₹)?\s*([\d,.]+)\s*(K|L|LAC|LAKH|CR|CRORE)?\b", re.I)),
    ("sale_total", re.compile(r"\b(?:TOTAL|ASKING|DEMAND|SALE\s+PRICE)\s*[:@\-]?\s*(?:RS\.?|₹)?\s*([\d,.]+)\s*(K|L|LAC|LAKH|CR|CRORE)?\b", re.I)),
    ("rate", re.compile(r"(?:RS\.?|₹)?\s*([\d,.]+)\s*/?\s*(?:PER\s*)?(SQ\.?\s*FT|SQFT|SFT|SQ\.?\s*YD|SQYD)\b", re.I)),
]

AMENITY_TERMS = [
    "gated society","club house","clubhouse","swimming pool","gym","garden","park",
    "security","power backup","generator","cctv","lift","elevator","terrace","basement",
    "balcony","servant room","study room","store room","modular kitchen","ac","air conditioning",
    "corner","wide road","green belt","pool facing","park facing","sea view","golf facing",
    "north facing","south facing","east facing","west facing","sun facing","aravali facing",
]

USE_TERMS = [
    "restaurant","cafe","banquet","hotel","guest house","office","showroom","shop","retail",
    "warehouse","clinic","hospital","school","gym","salon","spa","bar","lounge","club",
    "residential","villa","apartment","flat","kothi","bungalow","plot","land",
]

def _now():
    return datetime.now(timezone.utc).isoformat()

def _engine(core):
    return foundation._engine_from_core(core)

def _app(core):
    return getattr(core, "app", None) or core

def _install(engine):
    with engine.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))

def _wa():
    import whatsapp_live_bridge as wb
    return wb

def _num(value):
    try:
        return float(str(value).replace(",", ""))
    except Exception:
        return None

def _money_to_inr(value, suffix):
    n = _num(value)
    if n is None:
        return None
    s = str(suffix or "").upper()
    if s == "K":
        return n * 1_000
    if s in {"L","LAC","LAKH"}:
        return n * 100_000
    if s in {"CR","CRORE"}:
        return n * 10_000_000
    return n

def _add_evidence(evidence, field, value, match_text, scope):
    evidence.setdefault(field, []).append({
        "value": value,
        "evidence": match_text,
        "scope": scope,
        "provenance": "EXPLICIT_TEXT",
    })

def _first(patterns, text_value, field, evidence, scope):
    for pat in patterns:
        m = pat.search(text_value)
        if m:
            value = " ".join([g for g in m.groups() if g]) if m.groups() else m.group(0)
            value = re.sub(r"\s+", " ", value).strip()
            _add_evidence(evidence, field, value, m.group(0), scope)
            return value
    return None

def _extract_areas(text_value, evidence, scope):
    out = []
    seen = set()
    for unit, pat in AREA_PATTERNS:
        for m in pat.finditer(text_value):
            value = _num(m.group(1))
            if value is None:
                continue
            key = (value, unit)
            if key in seen:
                continue
            seen.add(key)
            item = {"value": value, "unit": unit, "raw": m.group(0)}
            out.append(item)
            _add_evidence(evidence, "areas", item, m.group(0), scope)
    return out

def _extract_money(text_value, evidence, scope):
    out = []
    seen = set()
    for role, pat in MONEY_PATTERNS:
        for m in pat.finditer(text_value):
            raw = m.group(0)
            if role == "rate":
                value = _num(m.group(1))
                item = {"role": "rate", "value": value, "basis": m.group(2), "raw": raw}
            else:
                value = _money_to_inr(m.group(1), m.group(2))
                item = {"role": role, "value_inr": value, "raw": raw}
            key = json.dumps(item, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
            _add_evidence(evidence, "money", item, raw, scope)
    return out

def _extract_dimensions(text_value, evidence, scope):
    out = []
    pat = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+(?:\.\d+)?)\s*(FT|FEET|M|MTR|MTRS|METRE|METRES|YD|YARDS?)?\b", re.I)
    for m in pat.finditer(text_value):
        item = {
            "length": _num(m.group(1)),
            "width": _num(m.group(2)),
            "unit": (m.group(3) or "UNSTATED").upper(),
            "raw": m.group(0),
        }
        out.append(item)
        _add_evidence(evidence, "dimensions", item, m.group(0), scope)
    return out

def _extract_contacts(text_value, evidence, scope):
    out = []
    seen = set()
    for m in PHONE_RE.finditer(text_value):
        digits = re.sub(r"\D", "", m.group(0))
        phone = digits[-10:] if len(digits) >= 10 else digits
        if phone in seen:
            continue
        seen.add(phone)
        item = {"phone": phone, "provenance": "EXPLICIT_MESSAGE_CONTACT" if scope == "ATOMIC" else "SOURCE_SHARED_CONTACT"}
        out.append(item)
        _add_evidence(evidence, "contacts", item, m.group(0), scope)
    return out

def _extract_simple_terms(text_value, terms, field, evidence, scope):
    out = []
    low = text_value.casefold()
    for term in terms:
        if term.casefold() in low:
            out.append(term)
            _add_evidence(evidence, field, term, term, scope)
    return sorted(set(out))

def _derive_existing(row):
    return {
        "property_type": row.get("property_type"),
        "transaction_type": row.get("transaction_type"),
        "city": row.get("city"),
        "location": row.get("location"),
        "locality": row.get("locality"),
        "address": row.get("address"),
        "landmark": row.get("landmark"),
        "area_sqft": row.get("area_sqft"),
        "available_area_sqft": row.get("available_area_sqft"),
        "floor": row.get("floor"),
        "frontage": row.get("frontage"),
        "rent_inr": row.get("rent_inr"),
        "sale_price_inr": row.get("sale_price_inr"),
        "cam_inr": row.get("cam_inr"),
        "possession": row.get("possession"),
        "parking": row.get("parking"),
        "suitable_for": row.get("suitable_for"),
        "nearby_brands": row.get("nearby_brands"),
        "availability": row.get("availability"),
        "broker_name": row.get("broker_name"),
        "broker_phone": row.get("broker_phone"),
        "owner_name": row.get("owner_name"),
        "owner_phone": row.get("owner_phone"),
        "sender_name": row.get("sender_name"),
        "sender_phone": row.get("sender_phone"),
        "verification_status": row.get("verification_status"),
        "confidence": row.get("confidence"),
    }

def extract_deep_profile(row):
    raw = str(row.get("raw_text") or "")
    parent = str(row.get("parent_message_text") or "")
    evidence = {}
    warnings = []
    learning = []

    profile = {
        "identity": {
            "entity_id": row.get("wa_property_id"),
            "source_id": str(row.get("source_id") or "") or None,
            "message_id": str(row.get("message_id") or "") or None,
            "source_item_no": row.get("source_item_no"),
            "fingerprint": row.get("fingerprint"),
        },
        "existing_live_record": _derive_existing(row),
        "atomic_explicit": {},
        "parent_shared_context": {},
        "commercial": {},
        "physical": {},
        "contact_provenance": {},
        "classification_hints": {},
        "source_evidence": {
            "raw_text": raw,
            "parent_message_text": parent,
        },
    }

    atomic = profile["atomic_explicit"]
    shared = profile["parent_shared_context"]
    commercial = profile["commercial"]
    physical = profile["physical"]

    # Maximum explicit extraction from atomic text.
    for field, patterns in FIELD_PATTERNS.items():
        value = _first(patterns, raw, field, evidence, "ATOMIC")
        if value is not None:
            atomic[field] = value

    atomic["areas"] = _extract_areas(raw, evidence, "ATOMIC")
    atomic["money_mentions"] = _extract_money(raw, evidence, "ATOMIC")
    atomic["dimensions"] = _extract_dimensions(raw, evidence, "ATOMIC")
    atomic["contacts"] = _extract_contacts(raw, evidence, "ATOMIC")
    atomic["emails"] = sorted(set(EMAIL_RE.findall(raw)))
    atomic["urls"] = sorted(set(URL_RE.findall(raw)))
    atomic["amenities"] = _extract_simple_terms(raw, AMENITY_TERMS, "amenities", evidence, "ATOMIC")
    atomic["suitable_uses"] = _extract_simple_terms(raw, USE_TERMS, "suitable_uses", evidence, "ATOMIC")

    # Parent facts are stored separately and never silently copied into atomic truth.
    if parent and parent != raw:
        shared["areas"] = _extract_areas(parent, evidence, "PARENT")
        shared["money_mentions"] = _extract_money(parent, evidence, "PARENT")
        shared["dimensions"] = _extract_dimensions(parent, evidence, "PARENT")
        shared["contacts"] = _extract_contacts(parent, evidence, "PARENT")
        shared["amenities"] = _extract_simple_terms(parent, AMENITY_TERMS, "amenities", evidence, "PARENT")
        shared["suitable_uses"] = _extract_simple_terms(parent, USE_TERMS, "suitable_uses", evidence, "PARENT")
        for field in ("furnishing","possession","availability","brokerage","property_id"):
            value = _first(FIELD_PATTERNS[field], parent, field, evidence, "PARENT")
            if value is not None:
                shared[field] = value

    # Commercial interpretation only when supported by explicit text/current live record.
    tx = str(row.get("transaction_type") or "UNKNOWN").upper()
    commercial["transaction_type_live"] = tx
    commercial["rent_inr_live"] = row.get("rent_inr")
    commercial["sale_price_inr_live"] = row.get("sale_price_inr")
    commercial["cam_inr_live"] = row.get("cam_inr")
    commercial["availability_live"] = row.get("availability")

    # Contact chain.
    profile["contact_provenance"] = {
        "owner": {"name": row.get("owner_name"), "phone": row.get("owner_phone")},
        "broker": {"name": row.get("broker_name"), "phone": row.get("broker_phone")},
        "sender": {"name": row.get("sender_name"), "phone": row.get("sender_phone")},
        "atomic_contacts": atomic.get("contacts", []),
        "shared_contacts": shared.get("contacts", []),
    }

    # Physical synthesis without guessing.
    physical["areas_explicit"] = atomic.get("areas", [])
    physical["dimensions_explicit"] = atomic.get("dimensions", [])
    physical["floor_explicit"] = atomic.get("floor")
    physical["furnishing_explicit"] = atomic.get("furnishing")
    physical["parking_explicit"] = atomic.get("parking")
    physical["facing_explicit"] = atomic.get("facing")
    physical["view_explicit"] = atomic.get("view")
    physical["road_width_explicit"] = atomic.get("road_width")
    physical["frontage_explicit"] = atomic.get("frontage")
    physical["property_age_explicit"] = atomic.get("property_age")

    # Classification hints from source words, still labelled as hints.
    text_all = raw + "\n" + parent
    profile["classification_hints"]["has_sale_signal"] = bool(re.search(r"\b(?:sale|sell|asking|demand|cr|crore)\b", text_all, re.I))
    profile["classification_hints"]["has_rent_signal"] = bool(re.search(r"\b(?:rent|rental|lease|to let)\b", text_all, re.I))
    profile["classification_hints"]["has_requirement_signal"] = bool(re.search(r"\b(?:requirement|required|wanted|looking for|client wants)\b", text_all, re.I))
    profile["classification_hints"]["multi_asset_parent_signal"] = len(re.findall(r"(?:^|\n)\s*(?:\d+[.)]|📍|✨|✅)", parent)) >= 2

    # Conflict/warning lessons.
    if row.get("city") and str(row.get("city")).casefold() not in text_all.casefold():
        warnings.append("LIVE_CITY_NOT_LITERAL_IN_EVIDENCE")
        learning.append("Teach context ownership: city must be explicit or come from a proven scoped header.")
    if row.get("locality") and str(row.get("locality")).casefold() not in text_all.casefold():
        warnings.append("LIVE_LOCALITY_NOT_LITERAL_IN_EVIDENCE")
        learning.append("Teach locality grounding: sibling or broker service area must not leak.")
    if tx == "SALE" and not profile["classification_hints"]["has_sale_signal"]:
        warnings.append("SALE_WITH_WEAK_TEXT_SIGNAL")
    if tx == "RENT" and not profile["classification_hints"]["has_rent_signal"]:
        warnings.append("RENT_WITH_WEAK_TEXT_SIGNAL")
    if profile["classification_hints"]["multi_asset_parent_signal"] and row.get("source_item_no") is None:
        warnings.append("MULTI_ASSET_PARENT_WITHOUT_ATOMIC_ITEM_NUMBER")
        learning.append("Teach atomic splitting from repeated inventory markers.")
    if not any([row.get("owner_phone"), row.get("broker_phone"), row.get("sender_phone")]) and not atomic["contacts"]:
        warnings.append("CONTACT_CHAIN_EMPTY")
        learning.append("Teach WhatsApp sender lineage recovery when explicit/shared contacts are absent.")

    # Missingness is useful training data.
    desired = {
        "project_or_property_name": bool(row.get("location") or row.get("address")),
        "transaction_type": tx not in {"", "UNKNOWN", "AMBIGUOUS"},
        "city": bool(row.get("city")),
        "locality": bool(row.get("locality") or row.get("location")),
        "property_type": bool(row.get("property_type")),
        "area": bool(row.get("area_sqft") or atomic["areas"]),
        "money": bool(row.get("rent_inr") or row.get("sale_price_inr") or atomic["money_mentions"]),
        "floor": bool(row.get("floor") or atomic.get("floor")),
        "furnishing": bool(atomic.get("furnishing")),
        "parking": bool(row.get("parking") or atomic.get("parking")),
        "facing_or_view": bool(atomic.get("facing") or atomic.get("view")),
        "contact": bool(row.get("owner_phone") or row.get("broker_phone") or row.get("sender_phone") or atomic["contacts"]),
        "possession": bool(row.get("possession") or atomic.get("possession")),
        "availability": bool(row.get("availability") or atomic.get("availability")),
    }
    missing = [k for k, present in desired.items() if not present]
    completeness = round(100.0 * (len(desired) - len(missing)) / len(desired), 2)

    return {
        "deep_profile": profile,
        "evidence_map": evidence,
        "missing_fields": missing,
        "warnings": warnings,
        "learning_signals": sorted(set(learning)),
        "completeness_score": completeness,
    }

def _upsert_profile(engine, row):
    result = extract_deep_profile(row)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO alliance_topper_availability_intelligence
                (intelligence_id,source_system,entity_id,source_id,message_id,source_item_no,
                 raw_text,parent_message_text,deep_profile,evidence_map,missing_fields,
                 extraction_warnings,learning_signals,completeness_score,extractor_version)
                VALUES
                (:id,'WHATSAPP_LIVE',:eid,:sid,:mid,:item,:raw,:parent,
                 CAST(:profile AS jsonb),CAST(:evidence AS jsonb),CAST(:missing AS jsonb),
                 CAST(:warnings AS jsonb),CAST(:learning AS jsonb),:score,:ver)
                ON CONFLICT(source_system,entity_id) DO UPDATE SET
                 source_id=EXCLUDED.source_id,message_id=EXCLUDED.message_id,
                 source_item_no=EXCLUDED.source_item_no,raw_text=EXCLUDED.raw_text,
                 parent_message_text=EXCLUDED.parent_message_text,
                 deep_profile=EXCLUDED.deep_profile,evidence_map=EXCLUDED.evidence_map,
                 missing_fields=EXCLUDED.missing_fields,
                 extraction_warnings=EXCLUDED.extraction_warnings,
                 learning_signals=EXCLUDED.learning_signals,
                 completeness_score=EXCLUDED.completeness_score,
                 extractor_version=EXCLUDED.extractor_version,updated_at=now()
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "eid": str(row.get("wa_property_id")),
                "sid": str(row.get("source_id") or "") or None,
                "mid": str(row.get("message_id") or "") or None,
                "item": row.get("source_item_no"),
                "raw": str(row.get("raw_text") or ""),
                "parent": str(row.get("parent_message_text") or ""),
                "profile": json.dumps(foundation._json_safe(result["deep_profile"]), ensure_ascii=False),
                "evidence": json.dumps(foundation._json_safe(result["evidence_map"]), ensure_ascii=False),
                "missing": json.dumps(result["missing_fields"]),
                "warnings": json.dumps(result["warnings"]),
                "learning": json.dumps(result["learning_signals"]),
                "score": result["completeness_score"],
                "ver": EXTRACTOR_VERSION,
            },
        )
    return result

def _update_mastery(engine):
    fields = [
        "project_or_property_name","transaction_type","city","locality","property_type","area",
        "money","floor","furnishing","parking","facing_or_view","contact","possession","availability"
    ]
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT missing_fields, extraction_warnings
                FROM alliance_topper_availability_intelligence
                WHERE extractor_version=:v
                """
            ),
            {"v": EXTRACTOR_VERSION},
        ).mappings().all()
    total = len(rows)
    stats = {}
    for field in fields:
        missing = 0
        warns = 0
        for row in rows:
            mf = foundation._loads(row.get("missing_fields"), [])
            ww = foundation._loads(row.get("extraction_warnings"), [])
            if field in mf:
                missing += 1
            warns += sum(1 for w in ww if field.upper() in str(w).upper())
        extracted = total - missing
        rate = round(extracted / total, 4) if total else 0
        stats[field] = {"observed": total, "extracted": extracted, "missing": missing, "rate": rate}
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO alliance_topper_field_mastery
                    (mastery_id,extractor_version,field_name,observed_count,extracted_count,
                     missing_count,warning_count,extraction_rate)
                    VALUES (:id,:v,:f,:o,:e,:m,:w,:r)
                    ON CONFLICT(extractor_version,field_name) DO UPDATE SET
                     observed_count=EXCLUDED.observed_count,
                     extracted_count=EXCLUDED.extracted_count,
                     missing_count=EXCLUDED.missing_count,
                     warning_count=EXCLUDED.warning_count,
                     extraction_rate=EXCLUDED.extraction_rate,
                     updated_at=now()
                    """
                ),
                {
                    "id": str(uuid.uuid4()), "v": EXTRACTOR_VERSION, "f": field,
                    "o": total, "e": extracted, "m": missing, "w": warns, "r": rate,
                },
            )
    return stats

def run_extract(engine, limit=500):
    _install(engine)
    wb = _wa()
    if wb.wa_engine is None:
        return {"status": "NOT_CONFIGURED", "reason": "WHATSAPP_DATABASE_URL missing", "profiled": 0}

    with wb.wa_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT * FROM wa_properties
                WHERE COALESCE(record_status,'ACTIVE')='ACTIVE'
                ORDER BY id DESC
                LIMIT :n
                """
            ),
            {"n": int(limit)},
        ).mappings().all()

    profiled = 0
    failures = []
    scores = []
    for rr in rows:
        row = dict(rr)
        try:
            result = _upsert_profile(engine, row)
            profiled += 1
            scores.append(result["completeness_score"])
        except Exception as exc:
            failures.append(f"{row.get('wa_property_id')}:{type(exc).__name__}:{exc}"[:500])

    mastery = _update_mastery(engine)
    STATE["rows_seen"] += len(rows)
    STATE["rows_profiled"] += profiled
    STATE["last_extract_at"] = _now()
    STATE["last_error"] = failures[-1] if failures else None

    return {
        "status": "PASS" if not failures else "PARTIAL",
        "version": VERSION,
        "seen": len(rows),
        "profiled": profiled,
        "failed": len(failures),
        "average_completeness_score": round(sum(scores)/len(scores), 2) if scores else 0,
        "field_mastery": mastery,
        "errors": failures[:10],
        "live_inventory_writes": 0,
        "production_writes": 0,
        "purpose": "Extract maximum explicit intelligence from every WhatsApp availability and teach the Tutor from missing/conflicting fields.",
    }

def status(engine):
    _install(engine)
    with engine.connect() as conn:
        counts = conn.execute(
            text(
                """
                SELECT count(*) n,
                       avg(completeness_score) avg_score,
                       count(*) FILTER (WHERE jsonb_array_length(extraction_warnings)>0) warned
                FROM alliance_topper_availability_intelligence
                WHERE extractor_version=:v
                """
            ),
            {"v": EXTRACTOR_VERSION},
        ).mappings().first()
        mastery = conn.execute(
            text(
                """
                SELECT field_name,observed_count,extracted_count,missing_count,extraction_rate
                FROM alliance_topper_field_mastery
                WHERE extractor_version=:v
                ORDER BY extraction_rate ASC, field_name
                """
            ),
            {"v": EXTRACTOR_VERSION},
        ).mappings().all()
        recent = conn.execute(
            text(
                """
                SELECT entity_id,completeness_score,missing_fields,extraction_warnings,updated_at
                FROM alliance_topper_availability_intelligence
                WHERE extractor_version=:v
                ORDER BY updated_at DESC LIMIT 10
                """
            ),
            {"v": EXTRACTOR_VERSION},
        ).mappings().all()

    return foundation._json_safe({
        "status": "PASS",
        "version": VERSION,
        "mode": MODE,
        "extractor_version": EXTRACTOR_VERSION,
        "worker": dict(STATE),
        "availability_profiles": int(counts["n"] or 0) if counts else 0,
        "average_completeness_score": round(float(counts["avg_score"] or 0), 2) if counts else 0,
        "profiles_with_warnings": int(counts["warned"] or 0) if counts else 0,
        "weakest_fields_first": [dict(x) for x in mastery],
        "recent_profiles": [dict(x) for x in recent],
        "whatsapp_live_relationship": "READ_ONLY_DEEP_EXTRACTOR",
        "live_inventory_writes": 0,
        "production_writes": 0,
    })

def get_profile(engine, entity_id):
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT * FROM alliance_topper_availability_intelligence
                WHERE source_system='WHATSAPP_LIVE' AND entity_id=:eid
                """
            ),
            {"eid": entity_id},
        ).mappings().first()
    return foundation._json_safe(dict(row)) if row else {"status": "NOT_FOUND", "entity_id": entity_id}

def _worker(core):
    engine = _engine(core)
    STATE["worker_alive"] = True
    try:
        while True:
            STATE["last_poll_at"] = _now()
            try:
                run_extract(engine, 600)
                STATE["last_error"] = None
            except Exception as exc:
                STATE["last_error"] = f"{type(exc).__name__}: {exc}"[:500]
            time.sleep(20)
    finally:
        STATE["worker_alive"] = False

def start_worker(core):
    global _STARTED
    with _LOCK:
        if _STARTED:
            return dict(STATE)
        t = threading.Thread(target=_worker, args=(core,), name="alliance-deep-availability-extractor", daemon=True)
        t.start()
        _STARTED = True
        STATE["worker_started"] = True
        return dict(STATE)

DASHBOARD = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alliance Deep Availability Intelligence</title>
<style>
body{font-family:Arial,sans-serif;background:#efe8dd;color:#27221d;margin:0}
main{max-width:1200px;margin:28px auto;padding:24px}
.card{background:#fff;border-radius:14px;padding:20px;margin:14px 0;box-shadow:0 2px 9px #00000012}
button{padding:12px 18px;border:0;border-radius:9px;cursor:pointer;margin-right:8px}
.primary{background:#27221d;color:#fff}
pre{white-space:pre-wrap;overflow:auto;background:#f8f4ee;padding:14px;border-radius:9px}
input{padding:10px;width:360px}
</style></head>
<body><main>
<h1>Alliance Deep Availability Intelligence</h1>
<p>Extract maximum explicit intelligence from every WhatsApp availability. Parent context is stored separately. No guessed geography. No writes back to WhatsApp or production inventory.</p>
<div class="card">
<button class="primary" onclick="runExtract()">Extract Latest 600 Availabilities</button>
<button onclick="refreshStatus()">Refresh Status</button>
</div>
<div class="card"><h3>Find Property Intelligence Card</h3>
<input id="eid" placeholder="WAP-..."><button onclick="loadProfile()">Open</button>
<pre id="profile">Enter a WhatsApp property ID.</pre></div>
<div class="card"><h3>Status / Field Mastery</h3><pre id="status">Loading...</pre></div>
<div class="card"><h3>Action Result</h3><pre id="result">No action yet.</pre></div>
<script>
async function api(path,method="GET"){
 const r=await fetch(path,{method,headers:{"Content-Type":"application/json"}});
 const t=await r.text(); let d={}; try{d=JSON.parse(t)}catch(e){d={raw:t}}
 if(!r.ok) throw new Error(d.detail||d.raw||("HTTP "+r.status)); return d;
}
async function refreshStatus(){
 try{document.getElementById("status").textContent=JSON.stringify(await api("/api/property-brain/deep-v23/status"),null,2)}
 catch(e){document.getElementById("status").textContent="ERROR: "+e.message}
}
async function runExtract(){
 document.getElementById("result").textContent="Extracting...";
 try{
  const d=await api("/api/property-brain/deep-v23/run?limit=600","POST");
  document.getElementById("result").textContent=JSON.stringify(d,null,2); await refreshStatus();
 }catch(e){document.getElementById("result").textContent="ERROR: "+e.message}
}
async function loadProfile(){
 const id=document.getElementById("eid").value.trim();
 if(!id)return;
 try{document.getElementById("profile").textContent=JSON.stringify(await api("/api/property-brain/deep-v23/profile/"+encodeURIComponent(id)),null,2)}
 catch(e){document.getElementById("profile").textContent="ERROR: "+e.message}
}
refreshStatus();
</script>
</main></body></html>
"""

def register(core):
    engine = _engine(core)
    app = _app(core)
    _install(engine)

    if not foundation._route_exists(app, "/api/property-brain/deep-v23/status"):
        @app.get("/api/property-brain/deep-v23/status")
        def deep_status():
            return status(engine)

    if not foundation._route_exists(app, "/api/property-brain/deep-v23/run"):
        @app.post("/api/property-brain/deep-v23/run")
        def deep_run(limit: int = Query(default=500, ge=1, le=5000)):
            return run_extract(engine, limit)

    if not foundation._route_exists(app, "/api/property-brain/deep-v23/profile/{entity_id}"):
        @app.get("/api/property-brain/deep-v23/profile/{entity_id}")
        def deep_profile(entity_id: str):
            return get_profile(engine, entity_id)

    if not foundation._route_exists(app, "/property-brain/deep-v23"):
        @app.get("/property-brain/deep-v23", response_class=HTMLResponse)
        def deep_dashboard():
            return HTMLResponse(DASHBOARD)

    start_worker(core)
    return {
        "status": "REGISTERED",
        "version": VERSION,
        "mode": MODE,
        "dashboard": "/property-brain/deep-v23",
        "whatsapp_live_relationship": "READ_ONLY_DEEP_EXTRACTOR",
        "live_inventory_writes": 0,
        "production_writes": 0,
    }
