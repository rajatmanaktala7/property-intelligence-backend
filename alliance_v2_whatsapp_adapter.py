import os
import re
import json
from sqlalchemy import create_engine, text
from alliance_v2_schema import VERSION
from alliance_v2_whatsapp_purity import build_purity
from alliance_v2_purity_matcher_integration import promote_purity_to_matcher
from alliance_v2_normalize import (
    norm, ptype, area, num, money, phone, cid, pid,
    floor_norm, infer_frontage, infer_required_floor, infer_suitable
)

SOURCE_TYPE = "WHATSAPP"
SOURCE_NAME = "WhatsApp Property Database"

def _db_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url

def _source_engine(primary_engine):
    wa = (os.getenv("WHATSAPP_DATABASE_URL") or "").strip()
    primary = (os.getenv("DATABASE_URL") or "").strip()
    if not wa or wa == primary:
        return primary_engine, False
    return create_engine(
        _db_url(wa),
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={"connect_timeout": 10},
    ), True

def _table_exists(conn, table):
    return bool(conn.execute(text("SELECT to_regclass(:t)"), {"t": table}).scalar())

def _tx_supply(v):
    s = norm(v)
    if s in {"rent", "lease", "leasing"}:
        return "LEASE"
    if s in {"sale", "sell", "selling"}:
        return "SALE"
    return "UNKNOWN"

def _tx_requirement(raw):
    s = norm(raw)
    lease_words = ["rent", "lease", "leasing", "tenant", "on rent"]
    sale_words = ["buy", "buyer", "purchase", "sale", "for purchase"]
    has_lease = any(x in s for x in lease_words)
    has_sale = any(x in s for x in sale_words)
    if has_lease and has_sale:
        return "LEASE_OR_SALE"
    if has_lease:
        return "LEASE"
    if has_sale:
        return "SALE"
    return "UNKNOWN"

def _area_range(area_text, numeric_value):
    amin, amax = area(area_text)
    if amin is None:
        amin = num(numeric_value)
        amax = amin
    if amax is None:
        amax = amin
    if amin is not None and amax is not None and amin > amax:
        amin, amax = amax, amin
    return amin, amax

def _verification(status):
    s = norm(status)
    if s in {"verified", "approved"}:
        return "VERIFIED"
    if s in {"rejected", "auto reject", "auto rejected"}:
        return "REJECTED"
    return "UNVERIFIED"

def _availability(status):
    s = norm(status)
    if s in {"rejected", "inactive", "closed", "deleted"}:
        return "INACTIVE"
    return "UNKNOWN"

def _source_label(group_name):
    g = str(group_name or "").strip()
    return f"{SOURCE_NAME} | {g}" if g else SOURCE_NAME

def _confidence(row, completeness, verified):
    raw = num(row.get("confidence_score")) or 0
    raw = max(0, min(100, raw))
    source = 90 if verified else 65
    return round(min(100, max(completeness, (raw * 0.65) + (source * 0.35))), 2), source

def _contact_upsert(c, row, source_conf):
    p = phone(row.get("phone"))
    if not p:
        return None
    i = cid(p)
    c.execute(text("""
        INSERT INTO ai_contact_identity(
          contact_id,normalized_phone,display_name,company_name,confidence,updated_at
        )
        VALUES(:i,:p,:n,:co,:cf,NOW())
        ON CONFLICT(contact_id) DO UPDATE SET
          display_name=COALESCE(EXCLUDED.display_name,ai_contact_identity.display_name),
          company_name=COALESCE(EXCLUDED.company_name,ai_contact_identity.company_name),
          confidence=GREATEST(ai_contact_identity.confidence,EXCLUDED.confidence),
          updated_at=NOW()
    """), {
        "i": i, "p": p,
        "n": row.get("display_name") or row.get("poster_name"),
        "co": row.get("firm_name"),
        "cf": source_conf,
    })
    return i

def _index_supply(c, row):
    rid = str(row.get("id") or "")
    if not rid:
        return 0, 0
    raw = str(row.get("raw_listing_text") or row.get("summary") or "")
    loc = row.get("location")
    typ = ptype(row.get("property_type"))
    tran = _tx_supply(row.get("transaction"))
    amin, amax = _area_range(row.get("area_text"), row.get("area_sqft_numeric"))
    budget = num(row.get("budget_numeric")) or money(row.get("budget_text"))
    ver = _verification(row.get("status"))
    avail = _availability(row.get("status"))
    duplicate = bool(row.get("duplicate_of"))
    floor_n = floor_norm(raw)
    front = infer_frontage(raw)
    suitable = infer_suitable(raw)
    completeness = (
        (20 if loc else 0) +
        (20 if amin else 0) +
        (15 if tran != "UNKNOWN" else 0) +
        (15 if typ != "UNKNOWN" else 0) +
        (10 if row.get("phone") else 0) +
        (10 if budget else 0) +
        (5 if floor_n else 0) +
        (5 if front else 0)
    )
    combined_conf, source_conf = _confidence(row, completeness, ver == "VERIFIED")
    ct = _contact_upsert(c, row, source_conf)
    eligible = bool(
        not duplicate and
        ver != "REJECTED" and
        loc and amin and
        tran in {"LEASE", "SALE", "LEASE_OR_SALE"} and
        typ != "UNKNOWN" and
        avail != "INACTIVE"
    )
    identity = pid("wai_listings", rid)
    name = str(row.get("summary") or "").strip()[:180] or f"{loc or 'Unknown'} {typ}"
    source_name = _source_label(row.get("source_group_name"))
    rpsf = monthly = sale_price = None
    if tran == "SALE":
        sale_price = budget
    elif budget:
        bt = norm(row.get("budget_text"))
        if any(x in bt for x in ["psf", "per sq", "per sqft", "sq ft"]):
            rpsf = budget
            monthly = round(budget * amin, 2) if amin else None
        else:
            monthly = budget
            rpsf = round(budget / amin, 2) if amin else None

    payload = json.dumps(row, default=str)
    c.execute(text("""
      INSERT INTO ai_property_identity(property_identity_id,canonical_label,last_seen_at)
      VALUES(:i,:n,NOW())
      ON CONFLICT(property_identity_id) DO UPDATE SET
        canonical_label=COALESCE(EXCLUDED.canonical_label,ai_property_identity.canonical_label),
        last_seen_at=NOW()
    """), {"i": identity, "n": name})

    c.execute(text("""
      INSERT INTO ai_property_match_index(
        property_identity_id,source_type,source_name,source_table,source_record_id,property_name,
        canonical_property_type,city,location_raw,location_normalized,transaction_type,
        area_min_sqft,area_max_sqft,rent_original,rent_psf_month,monthly_rent,sale_price,
        floor_raw,floor_normalized,frontage_raw,frontage_ft,suitable_for,nearby_brands,
        verification_status,availability_status,contact_reference_id,data_completeness_score,
        data_confidence_score,source_confidence_score,match_eligible,original_payload,
        normalization_version,updated_at
      )
      VALUES(
        :i,'WHATSAPP',:sn,'wai_listings',:rid,:name,:typ,NULL,:loc,:ln,:tr,
        :amin,:amax,:rr,:rp,:mr,:sp,:fr,:fn,:frr,:fft,:suit,NULL,:ver,:av,:ct,
        :comp,:dc,:sc,:el,CAST(:pl AS jsonb),:v,NOW()
      )
      ON CONFLICT(source_table,source_record_id) DO UPDATE SET
        property_name=EXCLUDED.property_name,
        source_name=EXCLUDED.source_name,
        canonical_property_type=EXCLUDED.canonical_property_type,
        location_raw=EXCLUDED.location_raw,
        location_normalized=EXCLUDED.location_normalized,
        transaction_type=EXCLUDED.transaction_type,
        area_min_sqft=EXCLUDED.area_min_sqft,
        area_max_sqft=EXCLUDED.area_max_sqft,
        rent_original=EXCLUDED.rent_original,
        rent_psf_month=EXCLUDED.rent_psf_month,
        monthly_rent=EXCLUDED.monthly_rent,
        sale_price=EXCLUDED.sale_price,
        floor_raw=EXCLUDED.floor_raw,
        floor_normalized=EXCLUDED.floor_normalized,
        frontage_raw=EXCLUDED.frontage_raw,
        frontage_ft=EXCLUDED.frontage_ft,
        suitable_for=EXCLUDED.suitable_for,
        verification_status=EXCLUDED.verification_status,
        availability_status=EXCLUDED.availability_status,
        contact_reference_id=EXCLUDED.contact_reference_id,
        data_completeness_score=EXCLUDED.data_completeness_score,
        data_confidence_score=EXCLUDED.data_confidence_score,
        source_confidence_score=EXCLUDED.source_confidence_score,
        match_eligible=EXCLUDED.match_eligible,
        original_payload=EXCLUDED.original_payload,
        normalization_version=EXCLUDED.normalization_version,
        updated_at=NOW()
    """), {
        "i": identity, "sn": source_name, "rid": rid, "name": name, "typ": typ,
        "loc": loc, "ln": norm(loc), "tr": tran, "amin": amin, "amax": amax,
        "rr": str(row.get("budget_text") or ""), "rp": rpsf, "mr": monthly, "sp": sale_price,
        "fr": raw, "fn": floor_n, "frr": raw, "fft": front, "suit": suitable,
        "ver": ver, "av": avail, "ct": ct, "comp": completeness, "dc": combined_conf,
        "sc": source_conf, "el": eligible, "pl": payload, "v": VERSION,
    })
    return 1, 1 if eligible else 0

def _index_requirement(c, row):
    rid = str(row.get("id") or "")
    if not rid:
        return 0, 0
    raw = str(row.get("raw_listing_text") or row.get("summary") or "")
    loc = row.get("location")
    amin, amax = _area_range(row.get("area_text"), row.get("area_sqft_numeric"))
    tran = _tx_requirement(raw)
    typ = ptype(row.get("property_type"))
    types = [] if typ == "UNKNOWN" else [typ]
    ver = _verification(row.get("status"))
    duplicate = bool(row.get("duplicate_of"))
    budget = num(row.get("budget_numeric")) or money(row.get("budget_text"))
    max_rent = budget if tran in {"LEASE", "LEASE_OR_SALE"} else None
    front = infer_frontage(raw)
    floor = infer_required_floor(raw)
    suitable = infer_suitable(raw)
    status = "INACTIVE" if ver == "REJECTED" else "ACTIVE"
    eligible = bool(
        not duplicate and ver != "REJECTED" and
        loc and amin and amax and tran != "UNKNOWN" and types
    )
    code = f"WA-REQ-{rid.replace('-', '').upper()[:16]}"
    payload = json.dumps(row, default=str)
    source_name = _source_label(row.get("source_group_name"))
    q = c.execute(text("""
      INSERT INTO ai_requirement_index(
        source_table,source_record_id,requirement_code,source_type,source_name,client_name,company_name,
        transaction_type,requirement_types,preferred_locations_raw,minimum_area_sqft,maximum_area_sqft,
        maximum_monthly_rent,minimum_frontage_ft,required_floor,suitable_for,additional_points,
        verification_status,status,match_eligible,original_payload,normalization_version,updated_at
      )
      VALUES(
        'wai_listings',:rid,:code,'WHATSAPP',:sn,:cl,:co,:tr,CAST(:ty AS jsonb),:loc,:amin,:amax,
        :rent,:front,:floor,:suit,:add,:ver,:status,:el,CAST(:pl AS jsonb),:v,NOW()
      )
      ON CONFLICT(source_table,source_record_id) DO UPDATE SET
        requirement_code=EXCLUDED.requirement_code,
        source_name=EXCLUDED.source_name,
        client_name=EXCLUDED.client_name,
        company_name=EXCLUDED.company_name,
        transaction_type=EXCLUDED.transaction_type,
        requirement_types=EXCLUDED.requirement_types,
        preferred_locations_raw=EXCLUDED.preferred_locations_raw,
        minimum_area_sqft=EXCLUDED.minimum_area_sqft,
        maximum_area_sqft=EXCLUDED.maximum_area_sqft,
        maximum_monthly_rent=EXCLUDED.maximum_monthly_rent,
        minimum_frontage_ft=EXCLUDED.minimum_frontage_ft,
        required_floor=EXCLUDED.required_floor,
        suitable_for=EXCLUDED.suitable_for,
        additional_points=EXCLUDED.additional_points,
        verification_status=EXCLUDED.verification_status,
        status=EXCLUDED.status,
        match_eligible=EXCLUDED.match_eligible,
        original_payload=EXCLUDED.original_payload,
        normalization_version=EXCLUDED.normalization_version,
        updated_at=NOW()
      RETURNING requirement_index_id
    """), {
        "rid": rid, "code": code, "sn": source_name,
        "cl": row.get("display_name") or row.get("poster_name"),
        "co": row.get("firm_name"), "tr": tran, "ty": json.dumps(types),
        "loc": loc, "amin": amin, "amax": amax, "rent": max_rent,
        "front": front, "floor": floor, "suit": suitable, "add": raw,
        "ver": ver, "status": status, "el": eligible, "pl": payload, "v": VERSION,
    }).scalar()

    c.execute(text("DELETE FROM ai_requirement_location WHERE requirement_index_id=:q"), {"q": q})
    for part in re.split(r"[,;/|]+|\bor\b|\band\b", str(loc or ""), flags=re.I):
        part = part.strip()
        if len(part) > 1:
            c.execute(text("""
              INSERT INTO ai_requirement_location(requirement_index_id,location_raw,location_normalized)
              VALUES(:q,:r,:n)
              ON CONFLICT(requirement_index_id,location_normalized)
              DO UPDATE SET location_raw=EXCLUDED.location_raw
            """), {"q": q, "r": part, "n": norm(part)})
    return 1, 1 if eligible else 0

def _read_rows(source_conn):
    return [
        dict(x._mapping) for x in source_conn.execute(text("""
          SELECT
            l.id,l.transaction,l.property_type,l.location,l.region,
            l.budget_text,l.budget_numeric,l.area_text,l.area_sqft_numeric,
            l.summary,l.confidence_score,l.status,l.duplicate_of,l.created_at,
            l.verified_at,l.verified_by,l.source_group_name,l.poster_name,l.raw_listing_text,
            ct.phone,ct.display_name,ct.firm_name,ct.trust_score,
            rm.sent_at,rm.sender_phone,rm.sender_display_name
          FROM wai_listings l
          LEFT JOIN wai_contacts ct ON ct.id=l.contact_id
          LEFT JOIN wai_raw_messages rm ON rm.id=l.source_message_id
          ORDER BY l.created_at ASC
        """)).fetchall()
    ]

def rebuild_whatsapp(primary_engine):
    source_engine, dispose_source = _source_engine(primary_engine)
    result = {
        "table": "wai_listings",
        "source_type": "WHATSAPP",
        "indexed_properties": 0,
        "match_eligible_properties": 0,
        "indexed_requirements": 0,
        "match_eligible_requirements": 0,
        "skipped_unknown_transaction": 0,
        "duplicates_preserved_not_matchable": 0,
        "source_database": "WHATSAPP_DATABASE_URL" if dispose_source else "DATABASE_URL",
    }
    try:
        with source_engine.connect() as src:
            if not _table_exists(src, "wai_listings"):
                result["status"] = "table_not_found"
                return result
            rows = _read_rows(src)

        purity = build_purity(primary_engine, source_engine)
        result["purity"] = purity

        with primary_engine.begin() as c:
            for row in rows:
                tr = norm(row.get("transaction"))
                if row.get("duplicate_of"):
                    result["duplicates_preserved_not_matchable"] += 1
                if tr == "requirement":
                    n, eligible = _index_requirement(c, row)
                    result["indexed_requirements"] += n
                    result["match_eligible_requirements"] += eligible
                elif tr in {"sale", "rent", "lease", "selling"}:
                    n, eligible = _index_supply(c, row)
                    result["indexed_properties"] += n
                    result["match_eligible_properties"] += eligible
                else:
                    result["skipped_unknown_transaction"] += 1
        promotion = promote_purity_to_matcher(primary_engine, source_engine)
        result["purity_matcher"] = promotion
        result["match_eligible_properties"] = promotion.get("match_eligible_properties", result["match_eligible_properties"])
        result["match_eligible_requirements"] = promotion.get("match_eligible_requirements", result["match_eligible_requirements"])
        result["status"] = "ok"
        return result
    finally:
        if dispose_source:
            source_engine.dispose()
