from __future__ import annotations

import hashlib
import html
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text


VERSION = "2.1.0-ASTRA-INVALID-VALUE-DIAGNOSIS"
MARKER = "ALLIANCE_ASTRA_DATABASE_CLEAN_V2"
SOURCE_TOKENS = (
    "whatsapp", "newspaper", "magazine", "hospitality", "retail",
    "commercial", "manual",
)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s.()-]?)?([6-9](?:[\s.()-]?\d){9})(?!\d)")
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
AREA_RE = re.compile(
    r"(?<!\d)(\d{2,7}(?:\.\d+)?)\s*(sq\.?\s*ft|sqft|square\s*feet|sq\.?\s*yd|sqyd|square\s*yards?|sqm|sq\.?\s*m|square\s*met(?:er|re)s?)\b",
    re.I,
)
BAD_EMAIL_TOKENS = ("example.", "domain.", "test@", "noreply@", "no-reply@", "web.com")
BAD_LOCATIONS = {
    "", "unknown", "n/a", "na", "none", "null", "tara", "location",
    "address", "india", "all india", "test", "royal construction", "royal constructions",
}
BLANK_TEXT = {"", "unknown", "n/a", "na", "none", "null", "[]", "{}", "-"}


TABLE_SPECS = {
    "pi_master_properties_v711": {
        "label": "Canonical properties",
        "pk": ("canonical_id", "property_id", "id"),
        "critical": ("locality", "city", "property_type", "transaction_type", "area_sqft", "contact_numbers", "source_type"),
    },
    "pi_master_workflow_v720": {
        "label": "Property workflow",
        "pk": ("canonical_id", "property_id", "id"),
        "critical": ("availability_status", "verification_status", "assigned_to", "updated_at"),
        "audit_only": True,
    },
    "pi_master_requirements_v711": {
        "label": "Canonical requirements",
        "pk": ("canonical_id", "requirement_id", "id"),
        "critical": ("location", "city", "transaction_type", "property_type", "area_min_sqft", "budget_max", "contact_numbers", "source_type"),
    },
    "pi_requirement_gate_v1191": {
        "label": "Requirement evidence gate",
        "pk": ("id", "evidence_key"),
        "critical": ("original_message", "transaction_type", "property_category", "locations", "area_min_sqft", "budget_max", "contact_numbers", "evidence_quality"),
    },
    "pi_requirements": {
        "label": "Legacy/manual requirements",
        "pk": ("id", "requirement_id"),
        "critical": ("original_message", "location", "city", "property_type", "rent_or_sale", "budget", "contact_numbers", "source", "created_at"),
    },
    "pi_operational_requirements": {
        "label": "Operational requirements",
        "pk": ("id", "requirement_id"),
        "critical": ("original_message", "location", "city", "property_type", "transaction_type", "budget_max", "contact_numbers", "source_type", "created_at"),
    },
    "ai_whatsapp_requirement_supply_intelligence": {
        "label": "WhatsApp requirements",
        "pk": ("id", "requirement_id", "event_id"),
        "critical": ("classification", "raw_message", "location", "city", "property_type", "transaction_type", "budget", "sender_phone", "contact_numbers", "created_at"),
    },
    "pi_whatsapp_property_master": {
        "label": "WhatsApp property inventory",
        "pk": ("id", "property_id", "event_id"),
        "critical": ("location", "city", "property_type", "transaction_type", "area_sqft", "price", "contact_name", "contact_phone", "sender_phone", "message_date", "verification_status"),
    },
    "ai_hospitality_entity": {
        "label": "Hospitality intelligence",
        "pk": ("id", "canonical_key"),
        "critical": ("business_name", "category", "location", "city", "contact_name", "contact_phone", "whatsapp_phone", "email", "website", "verification_status"),
    },
    "ai_retail_contact": {
        "label": "Retail contacts",
        "pk": ("id", "contact_id"),
        "critical": ("company_name", "category", "person_name", "designation", "phone", "email", "website", "city", "source_url", "verification_status"),
    },
    "ai_retail_expansion_signal": {
        "label": "Retail expansion signals",
        "pk": ("id", "signal_id"),
        "critical": ("company_name", "headline", "location", "published_at", "source_url", "verification_status"),
    },
    "pi_clean_contacts_v1": {
        "label": "Marketing contacts",
        "pk": ("id", "canonical_key"),
        "critical": ("contact_name", "company_name", "phone", "whatsapp_phone", "email", "designation", "location", "source_count", "verification_status", "marketing_status"),
        "audit_only": True,
    },
    "pi_newspaper_properties": {
        "label": "Newspaper database",
        "pk": ("id", "record_id"),
        "critical": ("date_captured", "lead_type", "locality", "area", "configuration_details", "price", "agency_brand", "contact_person", "phone_numbers", "source", "verification_status"),
    },
    "pi_magazine_complete_v860": {
        "label": "Magazine database",
        "pk": ("property_id", "id", "source_record_id"),
        "critical": ("source_record_id", "location", "description", "property_category", "property_type", "area_value", "area_unit", "floor", "amount", "contact_name", "contacts", "source_date", "source_name", "verification_status"),
    },
    "aci_intel_assets": {
        "label": "Commercial assets",
        "pk": ("id", "asset_code"),
        "critical": ("asset_name", "asset_class", "city", "location", "developer_name", "lifecycle_status", "source_url", "last_researched_at"),
    },
    "aci_intel_contacts": {
        "label": "Commercial contacts",
        "pk": ("id", "contact_id"),
        "critical": ("contact_name", "company_name", "designation", "phone", "email", "source_url", "verification_status"),
    },
}

FIELD_RULES = {
    "phone": ("phone", "mobile", "mobile_no", "contact_no", "contact_number", "contact_phone", "sender_phone", "sender_mobile"),
    "contact_phone": ("contact_phone", "phone", "mobile", "contact_number", "sender_phone", "sender_mobile"),
    "sender_phone": ("sender_phone", "sender_mobile", "phone", "mobile", "contact_phone", "sender_jid", "remote_jid"),
    "whatsapp_phone": ("whatsapp_phone", "sender_phone", "sender_mobile", "phone", "mobile"),
    "phone_numbers": ("phone_numbers", "phones", "phone", "mobile", "contact_number", "sender_phone"),
    "contact_numbers": ("contact_numbers", "phones", "phone", "mobile", "contact_number", "sender_phone"),
    "contacts": ("contacts", "phone_numbers", "phones", "phone", "mobile", "contact_number"),
    "email": ("email", "contact_email", "email_id"),
    "contact_name": ("contact_name", "contact_person", "person_name", "sender_name", "client_name", "owner_name", "broker_name"),
    "contact_person": ("contact_person", "contact_name", "person_name", "sender_name", "owner_name", "broker_name"),
    "person_name": ("person_name", "contact_name", "contact_person", "sender_name"),
    "company_name": ("company_name", "business_name", "brand_name", "agency_brand", "retailer_name", "company"),
    "business_name": ("business_name", "company_name", "brand_name", "hotel_name", "restaurant_name", "property_name"),
    "agency_brand": ("agency_brand", "company_name", "brand_name", "business_name", "agency"),
    "designation": ("designation", "contact_role", "role", "job_title", "title"),
    "location": ("location", "locality", "micro_market", "address", "city"),
    "locality": ("locality", "micro_market", "location", "address"),
    "city": ("city", "district", "location_city"),
    "locations": ("locations", "location", "locality", "micro_market", "city"),
    "transaction_type": ("transaction_type", "transaction", "rent_or_sale", "deal_type"),
    "rent_or_sale": ("rent_or_sale", "transaction_type", "transaction", "deal_type"),
    "lead_type": ("lead_type", "classification", "record_type"),
    "property_type": ("property_type", "asset_type", "category", "property_category"),
    "property_category": ("property_category", "property_type", "asset_type", "category"),
    "category": ("category", "business_category", "property_category", "property_type"),
    "asset_class": ("asset_class", "property_category", "property_type", "category"),
    "area_sqft": ("area_sqft", "builtup_area_sqft", "area", "area_value"),
    "area_min_sqft": ("area_min_sqft", "min_area_sqft", "area_sqft", "area"),
    "area_max_sqft": ("area_max_sqft", "max_area_sqft", "area_sqft", "area"),
    "area_value": ("area_value", "area", "area_sqft"),
    "area_unit": ("area_unit", "unit"),
    "area": ("area", "area_sqft", "size"),
    "budget_min": ("budget_min", "min_budget", "budget_from"),
    "budget_max": ("budget_max", "max_budget", "budget_to", "budget"),
    "price": ("price", "amount", "asking_price", "budget"),
    "amount": ("amount", "price", "asking_price", "budget"),
    "budget": ("budget", "budget_max", "asking_price", "price"),
    "description": ("description", "original_description", "raw_text", "message", "original_message"),
    "original_message": ("original_message", "message", "raw_text", "description", "text"),
    "configuration_details": ("configuration_details", "configuration", "description", "raw_text"),
    "raw_message": ("raw_message", "original_message", "message", "raw_text", "description"),
    "source": ("source", "source_name", "source_type", "provider"),
    "source_type": ("source_type", "source", "provider"),
    "source_name": ("source_name", "source", "provider"),
    "source_url": ("source_url", "url", "evidence_url", "website"),
    "website": ("website", "company_website", "url"),
    "published_at": ("published_at", "published_date", "source_date", "date", "created_at"),
    "date_captured": ("date_captured", "captured_at", "source_date", "date", "created_at"),
    "source_date": ("source_date", "published_at", "date_captured", "date", "created_at"),
    "message_date": ("message_date", "message_timestamp", "captured_at", "date", "created_at"),
}

EVIDENCE_KEYS = (
    "raw_payload", "payload_json", "extracted_fields", "metadata", "data",
    "raw_text", "raw_message", "message", "original_message", "description", "notes",
    "evidence", "configuration_details", "original_description",
)

RUN_LOCK = threading.Lock()
RUNTIME = {
    "status": "NOT_STARTED",
    "run_id": None,
    "started_at": None,
    "completed_at": None,
    "last_error": None,
    "totals": {},
}


def _app(core):
    return getattr(core, "app", core)


def _engine(core):
    return getattr(core, "engine", None)


def _role(core, req):
    fn = getattr(core, "need_login", None)
    if callable(fn):
        return fn(req)
    raise HTTPException(401, "Login required")


def _qident(value):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(value or "")):
        raise ValueError("Unsafe identifier")
    return '"' + str(value) + '"'


def _json(value):
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (dict, list)):
                return parsed
        except Exception:
            pass
    return value


def _walk(value, keys):
    wanted = {str(k).lower() for k in keys}
    out = []

    def visit(item):
        item = _json(item)
        if isinstance(item, dict):
            for key, child in item.items():
                if str(key).lower() in wanted and child not in (None, "", [], {}):
                    out.append(child)
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return out


def _first(value, keys):
    for item in _walk(value, keys):
        if isinstance(item, (str, int, float)):
            clean = re.sub(r"\s+", " ", str(item)).strip()
            if clean and clean.lower() not in BLANK_TEXT:
                return clean
    return ""


def _phones(value):
    found = []
    items = value if isinstance(value, list) else [value]
    for raw in items:
        for match in PHONE_RE.finditer(str(raw or "").replace("@s.whatsapp.net", "")):
            digits = re.sub(r"\D", "", match.group(1))
            if len(digits) == 10 and digits not in found:
                found.append(digits)
    return found[:10]


def _emails(value):
    found = []
    items = value if isinstance(value, list) else [value]
    for raw in items:
        for match in EMAIL_RE.finditer(str(raw or "")):
            email_value = match.group(0).lower().strip(".,;:")
            if any(token in email_value for token in BAD_EMAIL_TOKENS):
                continue
            if email_value not in found:
                found.append(email_value)
    return found[:5]


def _source_kind(table):
    low = table.lower()
    if "whatsapp" in low or low.startswith("wa_"):
        return "WHATSAPP"
    if "newspaper" in low:
        return "NEWSPAPER"
    if "magazine" in low:
        return "MAGAZINE"
    if "hospitality" in low:
        return "HOSPITALITY"
    if "retail" in low:
        return "RETAIL"
    if "commercial" in low or low.startswith("aci_"):
        return "COMMERCIAL"
    return "MANUAL"


def _table_columns(engine, table):
    with engine.connect() as connection:
        rows = connection.execute(
            text("""SELECT column_name,data_type,udt_name
                FROM information_schema.columns
                WHERE table_schema=current_schema() AND table_name=:table
                ORDER BY ordinal_position"""),
            {"table": table},
        ).mappings().all()
    return {row["column_name"]: {"data_type": row["data_type"], "udt_name": row["udt_name"]} for row in rows}


def _tables(engine):
    with engine.connect() as connection:
        names = connection.execute(text("""SELECT table_name FROM information_schema.tables
            WHERE table_schema=current_schema() AND table_type='BASE TABLE'
            ORDER BY table_name""")).scalars().all()
    return [name for name in names if any(token in name.lower() for token in SOURCE_TOKENS)
            and name not in {"pi_clean_contacts_v1", "pi_clean_contact_evidence_v1"}][:160]


def ensure_schema(engine):
    with engine.begin() as connection:
        connection.execute(text("""CREATE TABLE IF NOT EXISTS pi_clean_contacts_v1(
            id BIGSERIAL PRIMARY KEY, canonical_key TEXT UNIQUE NOT NULL,
            contact_name TEXT, company_name TEXT, phone TEXT, whatsapp_phone TEXT,
            email TEXT, designation TEXT, location TEXT,
            source_count INTEGER NOT NULL DEFAULT 0,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
            marketing_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""))
        connection.execute(text("""CREATE TABLE IF NOT EXISTS pi_clean_contact_evidence_v1(
            id BIGSERIAL PRIMARY KEY,
            canonical_key TEXT NOT NULL REFERENCES pi_clean_contacts_v1(canonical_key) ON DELETE CASCADE,
            source_type TEXT NOT NULL, source_table TEXT NOT NULL,
            source_record_id TEXT NOT NULL, source_captured_at TEXT,
            evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(source_table,source_record_id,canonical_key))"""))
        connection.execute(text("""CREATE TABLE IF NOT EXISTS pi_astra_database_runs_v2(
            run_id TEXT PRIMARY KEY, status TEXT NOT NULL, phase TEXT,
            totals JSONB NOT NULL DEFAULT '{}'::jsonb,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ, error TEXT)"""))
        connection.execute(text("""CREATE TABLE IF NOT EXISTS pi_astra_field_recovery_v2(
            id BIGSERIAL PRIMARY KEY, run_id TEXT NOT NULL,
            table_name TEXT NOT NULL, record_pk TEXT NOT NULL,
            target_field TEXT NOT NULL, before_value TEXT,
            recovered_value TEXT NOT NULL, evidence_source TEXT NOT NULL,
            evidence_excerpt TEXT, confidence TEXT NOT NULL,
            action TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            recovery_key TEXT UNIQUE NOT NULL)"""))
        connection.execute(text("""CREATE TABLE IF NOT EXISTS pi_astra_scan_cursor_v2(
            table_name TEXT PRIMARY KEY, last_pk TEXT NOT NULL DEFAULT '',
            cycles_completed INTEGER NOT NULL DEFAULT 0,
            rows_scanned BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""))
        connection.execute(text("CREATE INDEX IF NOT EXISTS idx_astra_recovery_record_v2 ON pi_astra_field_recovery_v2(table_name,record_pk)"))


def sync(engine, limit_per_table=500, max_tables=40):
    """Build one evidence-backed marketing contact inventory without changing source rows."""
    ensure_schema(engine)
    stats = {"tables": 0, "rows": 0, "contacts_upserted": 0, "evidence_upserted": 0}
    excluded = ("audit", "run", "log", "test", "exam", "history", "backup", "archive")
    candidates = [table for table in _tables(engine) if not any(token in table.lower() for token in excluded)]
    priority = {"whatsapp": 0, "newspaper": 1, "magazine": 2, "hospitality": 3, "retail": 4, "commercial": 5, "manual": 6}
    candidates.sort(key=lambda table: min((priority[key] for key in priority if key in table.lower()), default=99))

    for table in candidates[:max(1, int(max_tables))]:
        try:
            with engine.connect() as connection:
                rows = connection.execute(
                    text(f"SELECT to_jsonb(t) AS data FROM {_qident(table)} t LIMIT :limit"),
                    {"limit": max(1, min(int(limit_per_table), 5000))},
                ).scalars().all()
        except Exception:
            continue

        prepared = []
        for position, raw in enumerate(rows, 1):
            obj = raw if isinstance(raw, dict) else _json(raw)
            if not isinstance(obj, dict):
                continue
            phone_values = _walk(obj, FIELD_RULES["phone_numbers"])
            email_values = _walk(obj, FIELD_RULES["email"])
            phones = _phones(phone_values)
            emails = _emails(email_values)
            # A contact explicitly written in the source message is valid evidence.
            # It is retained with the source row, never assigned to a guessed person.
            if not phones:
                phones = _phones(_all_evidence(obj))
            if not emails:
                emails = _emails(_all_evidence(obj))
            email_value = emails[0] if emails else ""
            if not phones and not email_value:
                continue
            name = _first(obj, FIELD_RULES["contact_name"])
            company = _first(obj, FIELD_RULES["company_name"])
            location = _clean_location(_first(obj, FIELD_RULES["location"]))
            designation = _first(obj, FIELD_RULES["designation"])
            source_id = _first(obj, ("id", "record_id", "event_id", "message_id", "source_id", "requirement_id")) or str(position)
            captured = _first(obj, ("captured_at", "created_at", "message_timestamp", "timestamp", "date"))
            for phone in phones or [""]:
                seed = "|".join((phone, email_value.lower(), name.lower(), company.lower()))
                prepared.append({
                    "key": "CONTACT-" + hashlib.sha256(seed.encode("utf-8", "ignore")).hexdigest()[:24].upper(),
                    "name": name, "company": company, "phone": phone,
                    "whatsapp": phone if _source_kind(table) == "WHATSAPP" else "",
                    "email": email_value, "designation": designation,
                    "location": location, "source_type": _source_kind(table),
                    "source_table": table, "source_id": source_id,
                    "captured": captured,
                })

        if prepared:
            with engine.begin() as connection:
                connection.execute(text("SET LOCAL lock_timeout = '2s'"))
                connection.execute(text("SET LOCAL statement_timeout = '30s'"))
                for item in prepared:
                    connection.execute(text("""INSERT INTO pi_clean_contacts_v1(
                        canonical_key,contact_name,company_name,phone,whatsapp_phone,email,
                        designation,location,source_count)
                        VALUES(:key,:name,:company,:phone,:whatsapp,:email,:designation,:location,1)
                        ON CONFLICT(canonical_key) DO UPDATE SET
                        contact_name=COALESCE(NULLIF(pi_clean_contacts_v1.contact_name,''),NULLIF(EXCLUDED.contact_name,'')),
                        company_name=COALESCE(NULLIF(pi_clean_contacts_v1.company_name,''),NULLIF(EXCLUDED.company_name,'')),
                        phone=COALESCE(NULLIF(pi_clean_contacts_v1.phone,''),NULLIF(EXCLUDED.phone,'')),
                        whatsapp_phone=COALESCE(NULLIF(pi_clean_contacts_v1.whatsapp_phone,''),NULLIF(EXCLUDED.whatsapp_phone,'')),
                        email=COALESCE(NULLIF(pi_clean_contacts_v1.email,''),NULLIF(EXCLUDED.email,'')),
                        designation=COALESCE(NULLIF(pi_clean_contacts_v1.designation,''),NULLIF(EXCLUDED.designation,'')),
                        location=COALESCE(NULLIF(pi_clean_contacts_v1.location,''),NULLIF(EXCLUDED.location,'')),
                        last_seen_at=NOW(),updated_at=NOW()"""), item)
                    result = connection.execute(text("""INSERT INTO pi_clean_contact_evidence_v1(
                        canonical_key,source_type,source_table,source_record_id,source_captured_at,evidence_json)
                        VALUES(:key,:source_type,:source_table,:source_id,:captured,CAST(:evidence AS JSONB))
                        ON CONFLICT(source_table,source_record_id,canonical_key) DO NOTHING"""), {
                            **item,
                            "evidence": json.dumps({
                                "name": item["name"], "company": item["company"],
                                "phone": item["phone"], "email": item["email"],
                                "location": item["location"],
                            }, ensure_ascii=False),
                        })
                    stats["contacts_upserted"] += 1
                    stats["evidence_upserted"] += max(0, result.rowcount or 0)
        stats["tables"] += 1
        stats["rows"] += len(prepared)

    with engine.begin() as connection:
        connection.execute(text("""UPDATE pi_clean_contacts_v1 target SET source_count=source.total
            FROM (SELECT canonical_key,COUNT(*) total FROM pi_clean_contact_evidence_v1 GROUP BY canonical_key) source
            WHERE target.canonical_key=source.canonical_key"""))
    return stats


def _is_blank(value):
    if value is None:
        return True
    if isinstance(value, (list, dict)):
        return not value
    return str(value).strip().lower() in BLANK_TEXT


def _clean_location(value):
    clean = re.sub(r"\s+", " ", str(value or "")).strip(" ,;|-")
    low = clean.lower()
    if low in BAD_LOCATIONS or len(clean) < 2 or len(clean) > 180:
        return ""
    if re.fullmatch(r"\d+", clean):
        return ""
    # Person/company/role labels are not geographic localities. Astra treats
    # them as invalid evidence even when the column is non-blank.
    if re.search(r"\b(construction|constructions|builder|builders|developer|developers|realty|properties|infra|infrastructure|owner|broker|dealer)\b", low):
        return ""
    return clean

def _location_invalid(value):
    raw = re.sub(r"\s+", " ", str(value or "")).strip(" ,;|-")
    return bool(raw) and not bool(_clean_location(raw))


def _all_evidence(obj):
    evidence = []
    for key in EVIDENCE_KEYS:
        evidence.extend(_walk(obj, (key,)))
    return evidence


def _explicit_transaction(values):
    joined = " ".join(str(value or "") for value in values).upper()
    sale = bool(re.search(r"\b(SALE|SELL|PURCHASE|BUY|OUTRIGHT)\b", joined))
    rent = bool(re.search(r"\b(RENT|RENTAL|LEASE|LEASING)\b", joined))
    if sale and not rent:
        return "SALE"
    if rent and not sale:
        return "LEASE"
    return ""


def _area(values):
    for raw in values:
        match = AREA_RE.search(str(raw or ""))
        if not match:
            continue
        number = float(match.group(1))
        unit = re.sub(r"[.\s]", "", match.group(2).lower())
        if "yd" in unit or "yard" in unit:
            number *= 9.0
        elif unit in {"sqm", "sqm", "squaremeter", "squaremetre", "squaremeters", "squaremetres"} or "met" in unit:
            number *= 10.7639
        if 20 <= number <= 100000000:
            return round(number, 2), match.group(0)
    return None, ""


def _candidate(obj, target):
    keys = FIELD_RULES.get(target, (target,))
    structured = _walk(obj, keys)
    evidence = _all_evidence(obj)

    if target in {"phone", "contact_phone", "sender_phone", "whatsapp_phone", "phone_numbers", "contact_numbers", "contacts"}:
        values = _phones(structured)
        if not values:
            values = _phones(evidence)
        if not values:
            return None, "", ""
        return (values if target in {"phone_numbers", "contact_numbers", "contacts"} else values[0]), "explicit phone", values[0]

    if target == "email":
        values = _emails(structured)
        if not values:
            values = _emails(evidence)
        if values:
            return values[0], "explicit email", values[0]
        return None, "", ""

    if target in {"location", "locality", "city", "locations"}:
        # Location recovery is structured-only. Narrative inference is forbidden.
        value = _clean_location(_first(obj, keys))
        if value:
            return ([value] if target == "locations" else value), "structured location", value
        return None, "", ""

    if target in {"contact_name", "contact_person", "person_name", "company_name", "business_name", "agency_brand", "designation"}:
        value = _first(obj, keys)
        if value and len(value) <= 180:
            return value, "structured identity", value
        return None, "", ""

    if target in {"transaction_type", "rent_or_sale"}:
        value = _explicit_transaction(structured + evidence)
        if value:
            return value, "explicit transaction language", value
        return None, "", ""

    if target in {"area_sqft", "area_min_sqft", "area_max_sqft", "area_value", "area"}:
        number, excerpt = _area(structured + evidence)
        if number is not None:
            return number, "explicit area and unit", excerpt
        return None, "", ""

    if target == "area_unit":
        number, excerpt = _area(structured + evidence)
        if number is not None:
            return "SQFT", "normalized explicit area unit", excerpt
        return None, "", ""

    if target in {"description", "original_message", "raw_message", "configuration_details"}:
        value = _first(obj, keys)
        if len(value) >= 8:
            return value[:10000], "structured source text", value[:180]
        return None, "", ""

    if target in {"source", "source_type", "source_name"}:
        value = _first(obj, keys)
        if not value:
            return None, "", ""
        return value[:180], "structured source", value[:180]

    if target in {"source_url", "website"}:
        value = _first(obj, keys)
        if value.startswith(("http://", "https://")) and len(value) <= 1000:
            return value, "structured URL", value[:180]
        return None, "", ""

    if target in {"published_at", "date_captured", "source_date", "message_date"}:
        value = _first(obj, keys)
        if value:
            return value, "structured source date", value[:100]
        return None, "", ""

    if target in {"property_type", "property_category", "category", "asset_class", "lead_type"}:
        value = _first(obj, keys)
        if value and len(value) <= 180:
            return value, "structured classification", value
        return None, "", ""

    # Money and other operational fields are never inferred from free text here.
    if target in {"budget", "budget_min", "budget_max", "price", "amount"}:
        value = _first(obj, keys)
        if value and re.fullmatch(r"[0-9,.]+", value):
            return float(value.replace(",", "")), "structured numeric value", value
    return None, "", ""


def _serialize(value, column, target):
    data_type = column.get("data_type")
    udt_name = column.get("udt_name")
    if data_type in {"json", "jsonb"}:
        return json.dumps(value if isinstance(value, (list, dict)) else [value], ensure_ascii=False), "json"
    if data_type == "ARRAY" or str(udt_name).startswith("_"):
        return value if isinstance(value, list) else [value], "array"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False), "plain"
    return value, "plain"


def _pk_for(spec, columns):
    return next((candidate for candidate in spec["pk"] if candidate in columns), None)


def _blank_sql(field):
    quoted = _qident(field)
    return f"({quoted} IS NULL OR LOWER(BTRIM(CAST({quoted} AS TEXT))) IN ('','unknown','n/a','na','none','null','[]','{{}}','-'))"


def _audit_table(engine, table, spec):
    columns = _table_columns(engine, table)
    if not columns:
        return {"table": table, "label": spec["label"], "present": False, "total": 0, "fields": {}}
    fields = [field for field in spec["critical"] if field in columns]
    expressions = ["COUNT(*) AS total"]
    for position, field in enumerate(fields):
        expressions.append(f"COUNT(*) FILTER (WHERE NOT {_blank_sql(field)}) AS f{position}")
    with engine.connect() as connection:
        row = connection.execute(text(f"SELECT {','.join(expressions)} FROM {_qident(table)}")).mappings().one()
    total = int(row["total"] or 0)
    detail = {}
    for position, field in enumerate(fields):
        complete = int(row[f"f{position}"] or 0)
        detail[field] = {
            "complete": complete,
            "missing": max(0, total - complete),
            "percent": round((complete * 100.0 / total), 2) if total else 100.0,
        }
    required_cells = total * len(fields)
    completed_cells = sum(item["complete"] for item in detail.values())
    return {
        "table": table, "label": spec["label"], "present": True,
        "total": total, "fields": detail,
        "completeness_percent": round(completed_cells * 100.0 / required_cells, 2) if required_cells else 100.0,
        "audit_only": bool(spec.get("audit_only")),
    }


def audit(engine):
    ensure_schema(engine)
    databases = []
    for table, spec in TABLE_SPECS.items():
        try:
            databases.append(_audit_table(engine, table, spec))
        except Exception as exc:
            databases.append({
                "table": table, "label": spec["label"], "present": True,
                "error": f"{type(exc).__name__}: {exc}",
            })
    present = [item for item in databases if item.get("present") and not item.get("error")]
    score = round(sum(item["completeness_percent"] for item in present) / len(present), 2) if present else 0.0
    with engine.connect() as connection:
        recovered = int(connection.execute(text("SELECT COUNT(*) FROM pi_astra_field_recovery_v2 WHERE action='UPDATED'")).scalar() or 0)
        review = int(connection.execute(text("SELECT COUNT(*) FROM pi_astra_field_recovery_v2 WHERE action='REVIEW_REQUIRED'")).scalar() or 0)
    invalid_locations = {}
    for table, field in (("pi_master_properties_v711","locality"),("pi_magazine_complete_v860","location"),("pi_operational_properties","location")):
        try:
            cols=_table_columns(engine,table)
            if field not in cols:
                continue
            with engine.connect() as connection:
                values=connection.execute(text(f"SELECT CAST({_qident(field)} AS TEXT) FROM {_qident(table)} WHERE {_qident(field)} IS NOT NULL")).scalars().all()
            bad=[str(v) for v in values if _location_invalid(v)]
            invalid_locations[table]={"count":len(bad),"sample":bad[:25]}
        except Exception as exc:
            invalid_locations[table]={"error":f"{type(exc).__name__}: {exc}"}
    return {
        "status": "READY", "version": VERSION,
        "quality_score": score, "databases": databases,
        "field_recoveries": recovered, "review_items": review,
        "invalid_locations": invalid_locations,
        "policy": "EVIDENCE_ONLY_NO_OVERWRITE_NO_GUESSING",
        "database_changed": False,
        "gpt_used": False,
    }


def _recover_table(engine, run_id, table, spec, limit):
    columns = _table_columns(engine, table)
    if not columns:
        return {
            "table": table, "scanned": 0, "updated": 0, "skipped": 0,
            "cycle_complete": True, "not_applicable": True,
            "recovery_status": "TABLE_NOT_PRESENT",
        }
    if spec.get("audit_only"):
        return {
            "table": table, "scanned": 0, "updated": 0, "skipped": 0,
            "cycle_complete": True, "not_applicable": True,
            "recovery_status": "AUDIT_ONLY",
        }
    targets = [field for field in spec["critical"] if field in columns and field in FIELD_RULES]
    if not targets:
        return {
            "table": table, "scanned": 0, "updated": 0, "skipped": 0,
            "cycle_complete": True, "not_applicable": True,
            "recovery_status": "NO_APPLICABLE_TARGET_COLUMNS",
        }
    where = " OR ".join(_blank_sql(field) for field in targets)
    pk = _pk_for(spec, columns)
    if not pk:
        with engine.connect() as connection:
            blank_row_exists = bool(connection.execute(text(
                f"SELECT EXISTS(SELECT 1 FROM {_qident(table)} WHERE {where} LIMIT 1)"
            )).scalar())
        if not blank_row_exists:
            return {
                "table": table, "scanned": 0, "updated": 0, "skipped": 0,
                "cycle_complete": True, "not_applicable": True,
                "recovery_status": "NO_BLANK_TARGET_ROWS",
            }
        return {
            "table": table, "scanned": 0, "updated": 0, "skipped": 0,
            "cycle_complete": False, "error": "No safe primary key for blank target rows",
            "recovery_status": "BLOCKED_NO_SAFE_PRIMARY_KEY",
        }
    with engine.begin() as connection:
        connection.execute(text("""INSERT INTO pi_astra_scan_cursor_v2(table_name)
            VALUES(:table) ON CONFLICT(table_name) DO NOTHING"""), {"table": table})
        cursor = connection.execute(text("""SELECT last_pk,cycles_completed,rows_scanned
            FROM pi_astra_scan_cursor_v2 WHERE table_name=:table"""), {"table": table}).mappings().one()
    last_pk = str(cursor.get("last_pk") or "")
    keyset = f" AND CAST({_qident(pk)} AS TEXT) > :last_pk" if last_pk else ""
    batch_limit = max(1, min(int(limit), 5000))
    with engine.connect() as connection:
        rows = connection.execute(text(
            f"SELECT to_jsonb(t) AS data FROM {_qident(table)} t WHERE ({where}){keyset} "
            f"ORDER BY CAST({_qident(pk)} AS TEXT) LIMIT :limit"
        ), {"limit": batch_limit, "last_pk": last_pk}).scalars().all()

    cycle_complete = not rows
    if not rows:
        with engine.begin() as connection:
            connection.execute(text("""UPDATE pi_astra_scan_cursor_v2
                SET last_pk='',cycles_completed=cycles_completed+1,updated_at=NOW()
                WHERE table_name=:table"""), {"table": table})

    result = {"table": table, "scanned": len(rows), "updated": 0, "skipped": 0,
              "fields": {}, "cycle_complete": cycle_complete,
              "cursor_before": last_pk, "cursor_after": last_pk}
    for raw in rows:
        obj = raw if isinstance(raw, dict) else _json(raw)
        if not isinstance(obj, dict) or obj.get(pk) is None:
            result["skipped"] += 1
            continue
        record_pk = str(obj[pk])
        for target in targets:
            if not _is_blank(obj.get(target)):
                continue
            value, evidence_source, excerpt = _candidate(obj, target)
            if value in (None, "", [], {}) and target in {"source", "source_type", "source_name"}:
                value = _source_kind(table)
                evidence_source = "source table identity"
                excerpt = table
            if value in (None, "", [], {}):
                continue
            bound, kind = _serialize(value, columns[target], target)
            recovery_key = hashlib.sha256(
                f"{table}|{record_pk}|{target}|{json.dumps(value, sort_keys=True, default=str)}".encode("utf-8", "ignore")
            ).hexdigest()
            quoted_target = _qident(target)
            if kind == "json":
                assignment = f"{quoted_target}=CAST(:value AS JSONB)"
            else:
                assignment = f"{quoted_target}=:value"
            try:
                with engine.begin() as connection:
                    connection.execute(text("SET LOCAL lock_timeout = '2s'"))
                    connection.execute(text("SET LOCAL statement_timeout = '10s'"))
                    update = connection.execute(text(
                        f"UPDATE {_qident(table)} SET {assignment} WHERE {_qident(pk)}=:pk AND {_blank_sql(target)}"
                    ), {"value": bound, "pk": obj[pk]})
                    if not (update.rowcount or 0):
                        continue
                    connection.execute(text("""INSERT INTO pi_astra_field_recovery_v2(
                        run_id,table_name,record_pk,target_field,before_value,recovered_value,
                        evidence_source,evidence_excerpt,confidence,action,recovery_key)
                        VALUES(:run_id,:table_name,:record_pk,:target_field,:before_value,:recovered_value,
                        :evidence_source,:evidence_excerpt,'HIGH','UPDATED',:recovery_key)
                        ON CONFLICT(recovery_key) DO NOTHING"""), {
                            "run_id": run_id, "table_name": table,
                            "record_pk": record_pk, "target_field": target,
                            "before_value": str(obj.get(target) or ""),
                            "recovered_value": json.dumps(value, ensure_ascii=False, default=str),
                            "evidence_source": evidence_source,
                            "evidence_excerpt": str(excerpt or "")[:500],
                            "recovery_key": recovery_key,
                        })
                result["updated"] += 1
                result["fields"][target] = result["fields"].get(target, 0) + 1
            except Exception as exc:
                errors = result.setdefault("errors", [])
                if len(errors) < 20:
                    errors.append(f"{record_pk}:{target}:{type(exc).__name__}")
    if rows:
        last_obj = rows[-1] if isinstance(rows[-1], dict) else _json(rows[-1])
        cursor_after = str((last_obj or {}).get(pk) or "")
        if cursor_after:
            with engine.begin() as connection:
                if len(rows) < batch_limit:
                    connection.execute(text("""UPDATE pi_astra_scan_cursor_v2
                        SET last_pk='',cycles_completed=cycles_completed+1,
                            rows_scanned=rows_scanned+:scanned,updated_at=NOW()
                        WHERE table_name=:table"""), {
                            "table": table, "scanned": len(rows),
                        })
                    result["cycle_complete"] = True
                    result["cursor_after"] = ""
                else:
                    connection.execute(text("""UPDATE pi_astra_scan_cursor_v2
                        SET last_pk=:last_pk,rows_scanned=rows_scanned+:scanned,updated_at=NOW()
                        WHERE table_name=:table"""), {
                            "table": table, "last_pk": cursor_after, "scanned": len(rows),
                        })
                    result["cursor_after"] = cursor_after
    return result


def run_recovery(engine, limit_per_table=1000, include_contacts=True):
    run_id = "ASTRA-" + uuid.uuid4().hex[:16].upper()
    started = datetime.now(timezone.utc).isoformat()
    with RUN_LOCK:
        RUNTIME.update({
            "status": "RUNNING", "run_id": run_id, "started_at": started,
            "completed_at": None, "last_error": None, "totals": {},
        })
    ensure_schema(engine)
    with engine.begin() as connection:
        connection.execute(text("""INSERT INTO pi_astra_database_runs_v2(run_id,status,phase)
            VALUES(:run_id,'RUNNING','CONTACT_SYNC')"""), {"run_id": run_id})
    try:
        contact_result = sync(engine, min(int(limit_per_table), 1000), 40) if include_contacts else {"status": "SKIPPED_ALREADY_SYNCED"}
        table_results = []
        with engine.begin() as connection:
            connection.execute(text("UPDATE pi_astra_database_runs_v2 SET phase='FIELD_RECOVERY' WHERE run_id=:run_id"), {"run_id": run_id})
        for table, spec in TABLE_SPECS.items():
            table_results.append(_recover_table(engine, run_id, table, spec, limit_per_table))
        totals = {
            "tables": len(table_results),
            "rows_scanned": sum(item.get("scanned", 0) for item in table_results),
            "fields_recovered": sum(item.get("updated", 0) for item in table_results),
            "cycles_completed": sum(1 for item in table_results if item.get("cycle_complete")),
            "contacts": contact_result,
            "table_results": table_results,
        }
        completed = datetime.now(timezone.utc).isoformat()
        with engine.begin() as connection:
            connection.execute(text("""UPDATE pi_astra_database_runs_v2
                SET status='COMPLETED',phase='DONE',totals=CAST(:totals AS JSONB),completed_at=NOW()
                WHERE run_id=:run_id"""), {"run_id": run_id, "totals": json.dumps(totals, ensure_ascii=False)})
        with RUN_LOCK:
            RUNTIME.update({"status": "COMPLETED", "completed_at": completed, "totals": totals})
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        with engine.begin() as connection:
            connection.execute(text("""UPDATE pi_astra_database_runs_v2
                SET status='ERROR',phase='FAILED',error=:error,completed_at=NOW()
                WHERE run_id=:run_id"""), {"run_id": run_id, "error": error[:2000]})
        with RUN_LOCK:
            RUNTIME.update({"status": "ERROR", "completed_at": datetime.now(timezone.utc).isoformat(), "last_error": error})


def _quality_page(report):
    rows = []
    for item in report["databases"]:
        if not item.get("present"):
            state = "Not installed"
        elif item.get("error"):
            state = html.escape(item["error"])
        else:
            missing = sum(field["missing"] for field in item["fields"].values())
            state = f"{item['completeness_percent']}% complete · {missing} blank critical cells"
        rows.append(
            "<tr><td>" + html.escape(item["label"]) + "</td><td>" + html.escape(item["table"]) +
            "</td><td>" + str(item.get("total", 0)) + "</td><td>" + state + "</td></tr>"
        )
    return f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>
    <title>Alliance Database Quality</title><style>
    body{{font:14px Arial;margin:0;background:#f4f7fb;color:#162235}}header{{background:#10223f;color:#fff;padding:20px}}
    main{{max-width:1400px;margin:auto;padding:18px}}.card{{background:#fff;border:1px solid #dce3ec;border-radius:12px;padding:16px;margin-bottom:14px}}
    table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #dce3ec;padding:9px;text-align:left}}th{{background:#edf3fa}}
    button,a.btn{{padding:10px 13px;background:#1d4ed8;color:#fff;border:0;border-radius:7px;text-decoration:none;cursor:pointer}}
    code{{background:#eef2f7;padding:3px 5px}}</style></head><body>
    <header><h1>Alliance Database Quality</h1><div>Evidence-backed recovery across canonical databases</div></header><main>
    <div class=card><h2>{report['quality_score']}% critical-field completeness</h2>
    <p>Recovered fields: <b>{report['field_recoveries']}</b>. Existing verified values are never overwritten. Uncertain values remain blank for review.</p>
    <button onclick="runClean(this)">Run bounded evidence recovery</button> <a class=btn href='/alliance/marketing-contacts'>Marketing contacts</a>
    <p id=result></p></div><div class=card><table><tr><th>Database</th><th>Canonical table</th><th>Rows</th><th>Quality</th></tr>{''.join(rows)}</table></div>
    <div class=card><b>Policy:</b> <code>EVIDENCE_ONLY_NO_OVERWRITE_NO_GUESSING</code><br><b>Automation:</b> deterministic Python/PostgreSQL; GPT is not used for this recovery.</div>
    <script>async function runClean(b){{b.disabled=true;document.getElementById('result').textContent='Recovery started in background. Refresh status shortly.';let r=await fetch('/api/alliance/astra-database-clean-v2/run',{{method:'POST'}});let x=await r.json();document.getElementById('result').textContent=JSON.stringify(x);b.disabled=false}}</script>
    </main></body></html>"""


def register(core):
    app = _app(core)
    engine = _engine(core)
    if engine is None:
        raise RuntimeError("Astra database clean requires database engine")

    @app.get("/api/alliance/clean-contact-master-v1/status", include_in_schema=False)
    def contact_status(req: Request):
        _role(core, req)
        ensure_schema(engine)
        with engine.connect() as connection:
            total = connection.execute(text("SELECT COUNT(*) FROM pi_clean_contacts_v1")).scalar() or 0
        return {"status": "READY", "version": VERSION, "contacts": total, "policy": "EVIDENCE_ONLY_REVIEW_REQUIRED", "gpt_used": False}

    @app.post("/api/alliance/clean-contact-master-v1/sync", include_in_schema=False)
    def run_contact_sync(req: Request):
        _role(core, req)
        return {"status": "APPLIED", "version": VERSION, **sync(engine)}

    @app.get("/api/alliance/astra-database-clean-v2/status", include_in_schema=False)
    def clean_status(req: Request):
        _role(core, req)
        ensure_schema(engine)
        return {"version": VERSION, **dict(RUNTIME), "policy": "EVIDENCE_ONLY_NO_OVERWRITE_NO_GUESSING", "gpt_used": False}

    @app.get("/api/alliance/astra-database-clean-v2/audit", include_in_schema=False)
    def clean_audit(req: Request):
        _role(core, req)
        return audit(engine)

    @app.post("/api/alliance/astra-database-clean-v2/run", include_in_schema=False)
    def clean_run(req: Request, limit_per_table: int = 1000, include_contacts: bool = False):
        role = _role(core, req)
        if role not in {"admin", "team"}:
            raise HTTPException(403, "Alliance role required")
        with RUN_LOCK:
            if RUNTIME["status"] == "RUNNING":
                return {"status": "ALREADY_RUNNING", "version": VERSION, "run_id": RUNTIME["run_id"]}
            RUNTIME["status"] = "QUEUED"
        worker = threading.Thread(
            target=run_recovery,
            args=(engine, max(50, min(int(limit_per_table), 5000)), bool(include_contacts)),
            name="alliance-astra-database-clean-v2",
            daemon=True,
        )
        worker.start()
        return {"status": "STARTED", "version": VERSION,
                "batch_per_table": max(50, min(int(limit_per_table), 5000)),
                "include_contacts": bool(include_contacts), "gpt_used": False}

    @app.get("/alliance/database-quality", response_class=HTMLResponse, include_in_schema=False)
    def quality_page(req: Request):
        _role(core, req)
        return HTMLResponse(_quality_page(audit(engine)), headers={"Cache-Control": "no-store"})

    @app.get("/alliance/marketing-contacts", response_class=HTMLResponse, include_in_schema=False)
    def contacts_page(req: Request, limit: int = 200):
        _role(core, req)
        ensure_schema(engine)
        with engine.connect() as connection:
            rows = connection.execute(text("""SELECT contact_name,company_name,phone,whatsapp_phone,email,
                designation,location,source_count,verification_status,marketing_status,last_seen_at
                FROM pi_clean_contacts_v1 ORDER BY last_seen_at DESC LIMIT :limit"""),
                {"limit": max(1, min(int(limit), 1000))}).mappings().all()
        body = "".join("<tr>" + "".join(
            f"<td>{html.escape(str(row.get(key) or ''))}</td>" for key in (
                "contact_name", "company_name", "phone", "whatsapp_phone", "email",
                "designation", "location", "source_count", "verification_status",
                "marketing_status", "last_seen_at",
            )) + "</tr>" for row in rows)
        return HTMLResponse(f"""<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Marketing Contacts</title>
        <style>body{{font:14px Arial;margin:24px;color:#162235}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #dce3ec;padding:8px;text-align:left}}th{{background:#10223f;color:#fff}}button,a{{padding:9px 12px;background:#1d4ed8;color:#fff;border:0;border-radius:6px;text-decoration:none}}</style>
        <h1>Marketing Contacts</h1><p>Evidence-backed contacts from WhatsApp, newspaper, magazine, hospitality, retail, commercial and manual sources. Marketing status defaults to Review Required.</p>
        <button onclick="fetch('/api/alliance/clean-contact-master-v1/sync',{{method:'POST'}}).then(r=>r.json()).then(()=>location.reload())">Sync all sources</button> <a href='/alliance/database-quality'>Database quality</a>
        <table><tr><th>Name</th><th>Company</th><th>Phone</th><th>WhatsApp</th><th>Email</th><th>Designation</th><th>Location</th><th>Sources</th><th>Verification</th><th>Marketing status</th><th>Last seen</th></tr>{body}</table>""")

    return {
        "status": "REGISTERED", "version": VERSION,
        "routes": [
            "/alliance/marketing-contacts", "/alliance/database-quality",
            "/api/alliance/clean-contact-master-v1/status",
            "/api/alliance/astra-database-clean-v2/status",
            "/api/alliance/astra-database-clean-v2/audit",
            "/api/alliance/astra-database-clean-v2/run",
        ],
        "boot_database_scan": False,
        "source_rows_deleted": False,
        "verified_values_overwritten": False,
        "gpt_used": False,
    }
