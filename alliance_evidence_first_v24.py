from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_deep_availability_v23 as deep_v23

VERSION = "2.4.0-EVIDENCE-FIRST-MAXIMUM-EXTRACTION"
MODE = "SOURCE_TRUTH_VS_LIVE_RECORD_FIELD_QUALITY"
EXTRACTOR_VERSION = "ALLIANCE_AVAILABILITY_EXTRACTOR_V2"

STATE = {
    "worker_started": False,
    "worker_alive": False,
    "last_poll_at": None,
    "last_run_at": None,
    "last_error": None,
    "rows_seen": 0,
    "rows_profiled": 0,
}
_LOCK = threading.Lock()
_STARTED = False

DDL = [
    """
    CREATE TABLE IF NOT EXISTS alliance_topper_availability_v24 (
        intelligence_id UUID PRIMARY KEY,
        entity_id TEXT NOT NULL UNIQUE,
        source_id TEXT,
        message_id TEXT,
        source_item_no INTEGER,
        raw_text TEXT NOT NULL,
        parent_message_text TEXT,
        field_quality JSONB NOT NULL DEFAULT '{}'::jsonb,
        extracted_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
        conflicts JSONB NOT NULL DEFAULT '[]'::jsonb,
        review_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
        tutor_lessons JSONB NOT NULL DEFAULT '[]'::jsonb,
        source_coverage_score NUMERIC(5,2),
        extractor_version TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS alliance_topper_v24_field_stats (
        stat_id UUID PRIMARY KEY,
        extractor_version TEXT NOT NULL,
        field_name TEXT NOT NULL,
        profiles INTEGER NOT NULL DEFAULT 0,
        explicit_atomic INTEGER NOT NULL DEFAULT 0,
        supported_parent INTEGER NOT NULL DEFAULT 0,
        live_only INTEGER NOT NULL DEFAULT 0,
        conflict INTEGER NOT NULL DEFAULT 0,
        missing INTEGER NOT NULL DEFAULT 0,
        explicit_rate NUMERIC(7,4),
        supported_rate NUMERIC(7,4),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(extractor_version, field_name)
    )
    """,
]

PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s\-]?)?[6-9](?:[\s\-]?\d){9}(?!\d)")
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.I)
URL_RE = re.compile(r"https?://[^\s<>]+", re.I)

CITY_ALIASES = {
    "GGN": "Gurgaon", "GURUGRAM": "Gurgaon", "GURGAON": "Gurgaon",
    "NEW DELHI": "Delhi", "DELHI": "Delhi", "NOIDA": "Noida",
    "GREATER NOIDA": "Greater Noida", "FARIDABAD": "Faridabad",
    "GHAZIABAD": "Ghaziabad", "GOA": "Goa", "PANJIM": "Panjim",
    "PANAJI": "Panjim", "MUMBAI": "Mumbai",
}

PROPERTY_TYPES = {
    "villa": "VILLA", "bungalow": "BUNGALOW", "kothi": "KOTHI",
    "apartment": "APARTMENT", "flat": "APARTMENT", "builder floor": "BUILDER_FLOOR",
    "plot": "PLOT", "land": "LAND", "office": "OFFICE", "shop": "SHOP",
    "showroom": "SHOWROOM", "warehouse": "WAREHOUSE", "factory": "FACTORY",
    "hotel": "HOTEL", "banquet": "BANQUET", "restaurant": "RESTAURANT",
    "cafe": "CAFE", "guest house": "GUEST_HOUSE", "guesthouse": "GUEST_HOUSE",
    "club": "CLUB", "lounge": "LOUNGE", "farmhouse": "FARMHOUSE",
}

USE_TERMS = [
    "restaurant","cafe","banquet","hotel","guest house","office","showroom","shop",
    "retail","warehouse","clinic","hospital","school","gym","salon","spa","bar",
    "lounge","club","residential","villa","apartment","flat","kothi","bungalow",
    "plot","land","co-working",
]

AMENITIES = [
    "gated society","clubhouse","club house","swimming pool","pool","gym","garden",
    "park","security","power backup","generator","cctv","lift","elevator","terrace",
    "basement","balcony","servant room","study room","store room","modular kitchen",
    "air conditioning","ac","corner","green belt","park facing","pool facing","sea view",
    "golf facing","aravali facing","sun facing","wide road","fire noc","sprinkler",
]

def _now():
    return datetime.now(timezone.utc).isoformat()

def _engine(core):
    return foundation._engine_from_core(core)

def _app(core):
    return getattr(core, "app", None) or core

def _wa():
    import whatsapp_live_bridge as wb
    return wb

def _install(engine):
    with engine.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))

def _norm(v):
    if v is None:
        return None
    s = re.sub(r"\s+", " ", str(v)).strip()
    return s.casefold() if s else None

def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return None

def _money(v, suffix):
    n = _num(v)
    if n is None:
        return None
    s = str(suffix or "").upper()
    if s == "K":
        return n * 1000
    if s in {"L","LAC","LAKH"}:
        return n * 100000
    if s in {"CR","CRORE"}:
        return n * 10000000
    return n

def _evidence(value, raw, scope):
    return {"value": value, "evidence": raw, "scope": scope}

def _cities(text_value, scope):
    out = []
    for alias, canonical in CITY_ALIASES.items():
        m = re.search(r"(?<![A-Za-z])" + re.escape(alias) + r"(?![A-Za-z])", text_value, re.I)
        if m:
            out.append(_evidence(canonical, m.group(0), scope))
    dedup = {}
    for x in out:
        dedup[x["value"]] = x
    return list(dedup.values())

def _localities(text_value, scope):
    out = []
    pats = [
        re.compile(r"\bSector\s*\d+[A-Za-z]?(?:\s*[/&,-]\s*\d+[A-Za-z]?)*\b", re.I),
        re.compile(r"\bSec\.?\s*\d+[A-Za-z]?\b", re.I),
        re.compile(r"\b(?:Malviya Nagar|Vasant Kunj|Vasant Vihar|Greater Kailash|GK\s*[12]|"
                   r"Defence Colony|South Extension|Safdarjung|Hauz Khas|Saket|Dwarka|"
                   r"Porvorim|Candolim|Panjim|Panaji|Siolim|Assagao|Anjuna|Vagator|Morjim)\b", re.I),
    ]
    seen = set()
    for pat in pats:
        for m in pat.finditer(text_value):
            val = re.sub(r"\s+", " ", m.group(0)).strip()
            if val.casefold() not in seen:
                seen.add(val.casefold())
                out.append(_evidence(val, m.group(0), scope))
    return out

def _property_types(text_value, scope):
    out = []
    low = text_value.casefold()
    for token, canonical in PROPERTY_TYPES.items():
        if token in low:
            out.append(_evidence(canonical, token, scope))
    dedup = {}
    for x in out:
        dedup[x["value"]] = x
    return list(dedup.values())

def _transaction(text_value, scope):
    sale = bool(re.search(r"\b(?:for sale|sale|sell|asking|demand|outright)\b", text_value, re.I))
    rent = bool(re.search(r"\b(?:for rent|rent|rental|lease|to let)\b", text_value, re.I))
    rented = bool(re.search(r"\b(?:pre[\s\-]?rented|rented|leased)\b", text_value, re.I))
    if sale and rented:
        return [_evidence("BOTH", "sale + rented/leased", scope)]
    if sale:
        return [_evidence("SALE", "sale signal", scope)]
    if rent:
        return [_evidence("RENT", "rent/lease signal", scope)]
    return []

def _areas(text_value, scope):
    out = []
    specs = [
        ("sqft", r"(?:sq\.?\s*ft|sqft|sft)"),
        ("sqyd", r"(?:sq\.?\s*yds?|sqyd|sqyard|sq\s*yards?|yards?|gaj)"),
        ("sqm", r"(?:sq\.?\s*m(?:trs?)?|sqm|sq\s*metres?|sq\s*meters?)"),
        ("acre", r"(?:acres?)"),
    ]
    seen = set()
    for unit, unit_pat in specs:
        pat = re.compile(r"(?<!\d)(\d[\d,.]*)\s*" + unit_pat + r"\b", re.I)
        for m in pat.finditer(text_value):
            value = _num(m.group(1))
            key = (value, unit)
            if value is not None and key not in seen:
                seen.add(key)
                out.append(_evidence({"value": value, "unit": unit}, m.group(0), scope))
    return out

def _money_mentions(text_value, scope):
    out = []
    pats = [
        ("RENT", re.compile(r"\bRent\s*[:@\-]?\s*(?:Rs\.?|₹)?\s*([\d,.]+)\s*(K|L|Lac|Lakh|Cr|Crore)?\b", re.I)),
        ("SALE_TOTAL", re.compile(r"\b(?:Demand|Asking|Total|Sale Price)\s*[:@\-]?\s*(?:Rs\.?|₹)?\s*([\d,.]+)\s*(K|L|Lac|Lakh|Cr|Crore)?\b", re.I)),
        ("RATE", re.compile(r"(?:Rs\.?|₹)?\s*([\d,.]+)\s*/?\s*(?:per\s*)?(sq\.?\s*ft|sqft|sft|sq\.?\s*yd|sqyd)\b", re.I)),
    ]
    for role, pat in pats:
        for m in pat.finditer(text_value):
            if role == "RATE":
                val = {"role": role, "value": _num(m.group(1)), "basis": m.group(2)}
            else:
                val = {"role": role, "value_inr": _money(m.group(1), m.group(2))}
            out.append(_evidence(val, m.group(0), scope))
    return out

def _contacts(text_value, scope):
    out, seen = [], set()
    for m in PHONE_RE.finditer(text_value):
        digits = re.sub(r"\D", "", m.group(0))
        phone = digits[-10:] if len(digits) >= 10 else digits
        if phone not in seen:
            seen.add(phone)
            out.append(_evidence(phone, m.group(0), scope))
    return out

def _generic_field(text_value, scope, patterns):
    for pat in patterns:
        m = pat.search(text_value)
        if m:
            value = " ".join([g for g in m.groups() if g]) if m.groups() else m.group(0)
            return [_evidence(re.sub(r"\s+", " ", value).strip(), m.group(0), scope)]
    return []

def _terms(text_value, scope, terms):
    out = []
    low = text_value.casefold()
    for term in terms:
        if term.casefold() in low:
            out.append(_evidence(term, term, scope))
    return out

def _extract_scope(text_value, scope):
    data = {
        "city": _cities(text_value, scope),
        "locality": _localities(text_value, scope),
        "property_type": _property_types(text_value, scope),
        "transaction_type": _transaction(text_value, scope),
        "areas": _areas(text_value, scope),
        "money": _money_mentions(text_value, scope),
        "contacts": _contacts(text_value, scope),
        "emails": [_evidence(x, x, scope) for x in sorted(set(EMAIL_RE.findall(text_value)))],
        "urls": [_evidence(x, x, scope) for x in sorted(set(URL_RE.findall(text_value)))],
        "configuration": _generic_field(text_value, scope, [
            re.compile(r"\b(\d+(?:\.\d+)?)\s*BHK(?:\s*\+\s*(SQ|SERVANT|STUDY))?\b", re.I)
        ]),
        "floor": _generic_field(text_value, scope, [
            re.compile(r"\b(?:on\s+)?(\d+(?:st|nd|rd|th)?|ground|lower ground|upper ground)\s+floor\b", re.I),
            re.compile(r"\bfloor\s*[:\-]\s*([A-Z0-9+\- ]{1,30})", re.I),
        ]),
        "furnishing": _generic_field(text_value, scope, [
            re.compile(r"\b(fully furnished|semi[\s\-]?furnished|unfurnished|bare shell|warm shell)\b", re.I)
        ]),
        "parking": _generic_field(text_value, scope, [
            re.compile(r"\b(\d+)\s+(?:car\s+)?parking\b", re.I),
            re.compile(r"\bparking\s*[:\-]\s*(\d+)\b", re.I),
        ]),
        "possession": _generic_field(text_value, scope, [
            re.compile(r"\b(ready to move|immediate(?:ly)?|vacant|under construction|possession in [A-Z0-9 ]+)\b", re.I)
        ]),
        "availability": _generic_field(text_value, scope, [
            re.compile(r"\b(available|vacant|rented|pre[\s\-]?rented|leased|owner occupied)\b", re.I)
        ]),
        "facing_view": _generic_field(text_value, scope, [
            re.compile(r"\b(north(?: east| west)?|south(?: east| west)?|east|west|park|pool|sea|golf|sun|aravali|green belt)\s+(?:facing|view)\b", re.I),
            re.compile(r"\bwith\s+(sea|park|pool|green belt|aravali|golf)\s+view\b", re.I),
        ]),
        "frontage": _generic_field(text_value, scope, [
            re.compile(r"\bfrontage\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*(ft|feet|m|mtr|mtrs|metre|metres)?\b", re.I)
        ]),
        "road_width": _generic_field(text_value, scope, [
            re.compile(r"\b(\d+(?:\.\d+)?)\s*(m|mtr|mtrs|metre|metres|ft|feet)\s+(?:wide\s+)?road\b", re.I)
        ]),
        "age": _generic_field(text_value, scope, [
            re.compile(r"\b(\d+(?:\.\d+)?)\s*years?\s+old\b", re.I)
        ]),
        "ceiling_height": _generic_field(text_value, scope, [
            re.compile(r"\bceiling\s+height\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*(ft|feet|m|metre|metres)\b", re.I)
        ]),
        "power_load": _generic_field(text_value, scope, [
            re.compile(r"\bpower\s*(?:load)?\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*(kw|kva)\b", re.I)
        ]),
        "security_deposit": _generic_field(text_value, scope, [
            re.compile(r"\b(?:security|deposit)\s*[:\-]?\s*(?:Rs\.?|₹)?\s*([\d,.]+)\s*(K|L|Lac|Lakh|Cr|Crore)?\b", re.I)
        ]),
        "cam_maintenance": _generic_field(text_value, scope, [
            re.compile(r"\b(?:cam|maintenance)\s*[:\-]?\s*(?:Rs\.?|₹)?\s*([\d,.]+)\s*(K|L|Lac|Lakh)?\b", re.I)
        ]),
        "brokerage": _generic_field(text_value, scope, [
            re.compile(r"\b(\d+(?:\.\d+)?)\s*months?\s+brokerage\b", re.I),
            re.compile(r"\bbrokerage\s*[:\-]\s*([A-Z0-9.% ]{1,30})", re.I),
        ]),
        "amenities": _terms(text_value, scope, AMENITIES),
        "suitable_uses": _terms(text_value, scope, USE_TERMS),
    }
    return data

def _live_value(row, field):
    mapping = {
        "city": row.get("city"),
        "locality": row.get("locality") or row.get("location"),
        "property_type": row.get("property_type"),
        "transaction_type": row.get("transaction_type"),
        "floor": row.get("floor"),
        "parking": row.get("parking"),
        "possession": row.get("possession"),
        "availability": row.get("availability"),
        "frontage": row.get("frontage"),
        "areas": row.get("area_sqft") or row.get("available_area_sqft"),
        "money": row.get("rent_inr") or row.get("sale_price_inr") or row.get("cam_inr"),
        "contacts": row.get("owner_phone") or row.get("broker_phone") or row.get("sender_phone"),
    }
    return mapping.get(field)

def _quality(row, atomic, parent):
    fields = [
        "city","locality","property_type","transaction_type","areas","money","contacts",
        "configuration","floor","furnishing","parking","possession","availability",
        "facing_view","frontage","road_width","age","ceiling_height","power_load",
        "security_deposit","cam_maintenance","brokerage","amenities","suitable_uses",
        "emails","urls",
    ]
    quality = {}
    conflicts = []
    review = []
    lessons = []
    supported_count = 0

    for field in fields:
        a = atomic.get(field) or []
        p = parent.get(field) or []
        live = _live_value(row, field)

        if a:
            status = "EXPLICIT_ATOMIC"
            supported_count += 1
        elif p:
            status = "SUPPORTED_PARENT"
            supported_count += 1
        elif live not in (None, "", "UNKNOWN"):
            status = "LIVE_ONLY_UNPROVEN"
        else:
            status = "MISSING"

        if live not in (None, "", "UNKNOWN") and a:
            atomic_values = [str(x.get("value")) for x in a]
            if not any(_norm(live) == _norm(v) or _norm(live) in _norm(v) or _norm(v) in _norm(live) for v in atomic_values):
                if field in {"city","locality","property_type","transaction_type","floor","parking","possession","availability","frontage"}:
                    status = "CONFLICT"
                    conflicts.append({
                        "field": field,
                        "live_value": live,
                        "atomic_values": atomic_values,
                    })

        quality[field] = {
            "status": status,
            "live_value": live,
            "atomic_evidence": a,
            "parent_evidence": p,
        }

        if status == "LIVE_ONLY_UNPROVEN" and field in {"city","locality","transaction_type"}:
            review.append("UNPROVEN_" + field.upper())
            lessons.append("Do not reward populated " + field + " unless source evidence supports it.")
        if status == "CONFLICT":
            review.append("CONFLICT_" + field.upper())
            lessons.append("Prefer atomic evidence over conflicting live-record value for " + field + ".")

    score = round(100 * supported_count / len(fields), 2)
    return quality, conflicts, sorted(set(review)), sorted(set(lessons)), score

def profile_row(row):
    raw = str(row.get("raw_text") or "")
    parent_text = str(row.get("parent_message_text") or "")
    atomic = _extract_scope(raw, "ATOMIC")
    parent = _extract_scope(parent_text, "PARENT") if parent_text and parent_text != raw else {}

    quality, conflicts, review, lessons, score = _quality(row, atomic, parent)

    profile = {
        "entity_id": row.get("wa_property_id"),
        "atomic_explicit": atomic,
        "parent_context_candidates": parent,
        "contact_lineage": {
            "owner_name": row.get("owner_name"),
            "owner_phone": row.get("owner_phone"),
            "broker_name": row.get("broker_name"),
            "broker_phone": row.get("broker_phone"),
            "sender_name": row.get("sender_name"),
            "sender_phone": row.get("sender_phone"),
        },
        "live_record_snapshot": {
            k: foundation._json_safe(v)
            for k, v in row.items()
            if k not in {"raw_text","parent_message_text"}
        },
        "source_truth_policy": {
            "atomic_beats_parent": True,
            "parent_is_candidate_not_truth": True,
            "live_record_is_not_counted_as_extracted_without_evidence": True,
            "no_silent_geography_inference": True,
            "no_silent_owner_broker_role_inference": True,
        },
    }
    return {
        "field_quality": quality,
        "profile": profile,
        "conflicts": conflicts,
        "review_reasons": review,
        "tutor_lessons": lessons,
        "source_coverage_score": score,
    }

def _upsert(engine, row):
    result = profile_row(row)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO alliance_topper_availability_v24
                (intelligence_id,entity_id,source_id,message_id,source_item_no,raw_text,
                 parent_message_text,field_quality,extracted_profile,conflicts,
                 review_reasons,tutor_lessons,source_coverage_score,extractor_version)
                VALUES
                (:id,:eid,:sid,:mid,:item,:raw,:parent,CAST(:fq AS jsonb),
                 CAST(:profile AS jsonb),CAST(:conf AS jsonb),CAST(:review AS jsonb),
                 CAST(:lessons AS jsonb),:score,:ver)
                ON CONFLICT(entity_id) DO UPDATE SET
                 source_id=EXCLUDED.source_id,message_id=EXCLUDED.message_id,
                 source_item_no=EXCLUDED.source_item_no,raw_text=EXCLUDED.raw_text,
                 parent_message_text=EXCLUDED.parent_message_text,
                 field_quality=EXCLUDED.field_quality,extracted_profile=EXCLUDED.extracted_profile,
                 conflicts=EXCLUDED.conflicts,review_reasons=EXCLUDED.review_reasons,
                 tutor_lessons=EXCLUDED.tutor_lessons,source_coverage_score=EXCLUDED.source_coverage_score,
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
                "fq": json.dumps(foundation._json_safe(result["field_quality"]), ensure_ascii=False),
                "profile": json.dumps(foundation._json_safe(result["profile"]), ensure_ascii=False),
                "conf": json.dumps(result["conflicts"], ensure_ascii=False),
                "review": json.dumps(result["review_reasons"], ensure_ascii=False),
                "lessons": json.dumps(result["tutor_lessons"], ensure_ascii=False),
                "score": result["source_coverage_score"],
                "ver": EXTRACTOR_VERSION,
            },
        )
    return result

def _rebuild_stats(engine):
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT field_quality FROM alliance_topper_availability_v24 WHERE extractor_version=:v"),
            {"v": EXTRACTOR_VERSION},
        ).mappings().all()

    totals = {}
    for row in rows:
        fq = foundation._loads(row.get("field_quality"), {})
        for field, info in fq.items():
            st = str(info.get("status") or "MISSING")
            d = totals.setdefault(field, {
                "profiles": 0, "EXPLICIT_ATOMIC": 0, "SUPPORTED_PARENT": 0,
                "LIVE_ONLY_UNPROVEN": 0, "CONFLICT": 0, "MISSING": 0,
            })
            d["profiles"] += 1
            d[st] = d.get(st, 0) + 1

    with engine.begin() as conn:
        for field, d in totals.items():
            n = max(1, d["profiles"])
            explicit_rate = round(d["EXPLICIT_ATOMIC"] / n, 4)
            supported_rate = round((d["EXPLICIT_ATOMIC"] + d["SUPPORTED_PARENT"]) / n, 4)
            conn.execute(
                text(
                    """
                    INSERT INTO alliance_topper_v24_field_stats
                    (stat_id,extractor_version,field_name,profiles,explicit_atomic,
                     supported_parent,live_only,conflict,missing,explicit_rate,supported_rate)
                    VALUES (:id,:v,:f,:p,:ea,:sp,:lo,:cf,:mi,:er,:sr)
                    ON CONFLICT(extractor_version,field_name) DO UPDATE SET
                     profiles=EXCLUDED.profiles,explicit_atomic=EXCLUDED.explicit_atomic,
                     supported_parent=EXCLUDED.supported_parent,live_only=EXCLUDED.live_only,
                     conflict=EXCLUDED.conflict,missing=EXCLUDED.missing,
                     explicit_rate=EXCLUDED.explicit_rate,supported_rate=EXCLUDED.supported_rate,
                     updated_at=now()
                    """
                ),
                {
                    "id": str(uuid.uuid4()), "v": EXTRACTOR_VERSION, "f": field,
                    "p": d["profiles"], "ea": d["EXPLICIT_ATOMIC"], "sp": d["SUPPORTED_PARENT"],
                    "lo": d["LIVE_ONLY_UNPROVEN"], "cf": d["CONFLICT"], "mi": d["MISSING"],
                    "er": explicit_rate, "sr": supported_rate,
                },
            )
    return totals

def run(engine, limit=1000):
    _install(engine)
    wb = _wa()
    if wb.wa_engine is None:
        return {"status": "NOT_CONFIGURED", "profiled": 0}

    with wb.wa_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT * FROM wa_properties
                WHERE COALESCE(record_status,'ACTIVE')='ACTIVE'
                ORDER BY id DESC LIMIT :n
                """
            ),
            {"n": int(limit)},
        ).mappings().all()

    failures = []
    scores = []
    for rr in rows:
        row = dict(rr)
        try:
            result = _upsert(engine, row)
            scores.append(result["source_coverage_score"])
        except Exception as exc:
            failures.append(f"{row.get('wa_property_id')}:{type(exc).__name__}:{exc}"[:500])

    stats = _rebuild_stats(engine)
    STATE["rows_seen"] += len(rows)
    STATE["rows_profiled"] += len(rows) - len(failures)
    STATE["last_run_at"] = _now()
    STATE["last_error"] = failures[-1] if failures else None

    return {
        "status": "PASS" if not failures else "PARTIAL",
        "version": VERSION,
        "seen": len(rows),
        "profiled": len(rows) - len(failures),
        "failed": len(failures),
        "average_source_coverage_score": round(sum(scores) / len(scores), 2) if scores else 0,
        "field_stats": stats,
        "errors": failures[:10],
        "important_change": "100% no longer means the live DB field is merely populated. Extraction quality is now measured from source evidence.",
        "live_inventory_writes": 0,
        "production_writes": 0,
    }

def status(engine):
    _install(engine)
    with engine.connect() as conn:
        summary = conn.execute(
            text(
                """
                SELECT count(*) n,avg(source_coverage_score) avg_score,
                       count(*) FILTER (WHERE jsonb_array_length(conflicts)>0) conflicts,
                       count(*) FILTER (WHERE jsonb_array_length(review_reasons)>0) reviews
                FROM alliance_topper_availability_v24
                WHERE extractor_version=:v
                """
            ),
            {"v": EXTRACTOR_VERSION},
        ).mappings().first()
        stats = conn.execute(
            text(
                """
                SELECT field_name,profiles,explicit_atomic,supported_parent,live_only,
                       conflict,missing,explicit_rate,supported_rate
                FROM alliance_topper_v24_field_stats
                WHERE extractor_version=:v
                ORDER BY supported_rate ASC,field_name
                """
            ),
            {"v": EXTRACTOR_VERSION},
        ).mappings().all()
        recent = conn.execute(
            text(
                """
                SELECT entity_id,source_coverage_score,review_reasons,conflicts,updated_at
                FROM alliance_topper_availability_v24
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
        "profiles": int(summary["n"] or 0) if summary else 0,
        "average_source_coverage_score": round(float(summary["avg_score"] or 0), 2) if summary else 0,
        "profiles_with_conflicts": int(summary["conflicts"] or 0) if summary else 0,
        "profiles_needing_review": int(summary["reviews"] or 0) if summary else 0,
        "field_truth_stats": [dict(x) for x in stats],
        "recent_profiles": [dict(x) for x in recent],
        "whatsapp_live_relationship": "READ_ONLY_EVIDENCE_FIRST",
        "live_inventory_writes": 0,
        "production_writes": 0,
    })

def get_profile(engine, entity_id):
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM alliance_topper_availability_v24 WHERE entity_id=:eid"),
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
                run(engine, 1000)
                STATE["last_error"] = None
            except Exception as exc:
                STATE["last_error"] = f"{type(exc).__name__}: {exc}"[:500]
            time.sleep(30)
    finally:
        STATE["worker_alive"] = False

def start_worker(core):
    global _STARTED
    with _LOCK:
        if _STARTED:
            return dict(STATE)
        t = threading.Thread(target=_worker, args=(core,), name="alliance-evidence-first-v24", daemon=True)
        t.start()
        _STARTED = True
        STATE["worker_started"] = True
        return dict(STATE)

DASHBOARD = r"""
<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alliance Evidence First Extractor 2.4</title>
<style>
body{font-family:Arial;background:#eee8de;color:#25211d;margin:0}
main{max-width:1200px;margin:28px auto;padding:24px}
.card{background:white;border-radius:14px;padding:20px;margin:14px 0;box-shadow:0 2px 9px #00000012}
button{padding:12px 18px;border:0;border-radius:9px;cursor:pointer;margin-right:8px}
.primary{background:#25211d;color:white}
pre{white-space:pre-wrap;overflow:auto;background:#f8f4ee;padding:14px;border-radius:9px}
input{padding:10px;width:360px}
</style></head><body><main>
<h1>Evidence-First Maximum Extraction 2.4</h1>
<p>Truth score now comes from the WhatsApp evidence itself, not from whether a live database column happens to be populated.</p>
<div class="card"><button class="primary" onclick="runNow()">Analyse Latest 1000</button>
<button onclick="refreshStatus()">Refresh</button></div>
<div class="card"><input id="eid" placeholder="WAP-..."><button onclick="profile()">Open Intelligence Card</button>
<pre id="profile">Enter property ID.</pre></div>
<div class="card"><h3>Truth Metrics</h3><pre id="status">Loading...</pre></div>
<div class="card"><h3>Action Result</h3><pre id="result">No action yet.</pre></div>
<script>
async function api(path,method="GET"){const r=await fetch(path,{method});const t=await r.text();let d={};try{d=JSON.parse(t)}catch(e){d={raw:t}}if(!r.ok)throw new Error(d.detail||d.raw||("HTTP "+r.status));return d}
async function refreshStatus(){try{document.getElementById("status").textContent=JSON.stringify(await api("/api/property-brain/evidence-v24/status"),null,2)}catch(e){document.getElementById("status").textContent="ERROR: "+e.message}}
async function runNow(){try{document.getElementById("result").textContent="Analysing...";const d=await api("/api/property-brain/evidence-v24/run?limit=1000","POST");document.getElementById("result").textContent=JSON.stringify(d,null,2);await refreshStatus()}catch(e){document.getElementById("result").textContent="ERROR: "+e.message}}
async function profile(){const id=document.getElementById("eid").value.trim();if(!id)return;try{document.getElementById("profile").textContent=JSON.stringify(await api("/api/property-brain/evidence-v24/profile/"+encodeURIComponent(id)),null,2)}catch(e){document.getElementById("profile").textContent="ERROR: "+e.message}}
refreshStatus();
</script></main></body></html>
"""

def register(core):
    engine = _engine(core)
    app = _app(core)
    _install(engine)

    if not foundation._route_exists(app, "/api/property-brain/evidence-v24/status"):
        @app.get("/api/property-brain/evidence-v24/status")
        def evidence_status():
            return status(engine)

    if not foundation._route_exists(app, "/api/property-brain/evidence-v24/run"):
        @app.post("/api/property-brain/evidence-v24/run")
        def evidence_run(limit: int = Query(default=1000, ge=1, le=5000)):
            return run(engine, limit)

    if not foundation._route_exists(app, "/api/property-brain/evidence-v24/profile/{entity_id}"):
        @app.get("/api/property-brain/evidence-v24/profile/{entity_id}")
        def evidence_profile(entity_id: str):
            return get_profile(engine, entity_id)

    if not foundation._route_exists(app, "/property-brain/evidence-v24"):
        @app.get("/property-brain/evidence-v24", response_class=HTMLResponse)
        def evidence_dashboard():
            return HTMLResponse(DASHBOARD)

    start_worker(core)
    return {
        "status": "REGISTERED",
        "version": VERSION,
        "dashboard": "/property-brain/evidence-v24",
        "whatsapp_live_relationship": "READ_ONLY_EVIDENCE_FIRST",
        "live_inventory_writes": 0,
        "production_writes": 0,
    }
