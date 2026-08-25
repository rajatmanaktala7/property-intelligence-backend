
import json
from collections import defaultdict
from sqlalchemy import text
from alliance_v2_schema import VERSION
from alliance_v2_normalize import norm, phone, cid, pid

INTEGRATION_VERSION = "2.2-PURITY-AWARE-MATCHER"

def _source_label(group_name, recovered=False):
    g = str(group_name or "").strip()
    base = "WhatsApp Property Database"
    if g:
        base += f" | {g}"
    if recovered:
        base += " | Purity Recovered"
    return base

def _contact_upsert(c, row):
    p = phone(row.get("phone"))
    if not p:
        return None
    contact_id = cid(p)
    confidence = 90 if norm(row.get("status")) in {"verified", "approved"} else 65
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
        "i": contact_id,
        "p": p,
        "n": row.get("display_name") or row.get("poster_name"),
        "co": row.get("firm_name"),
        "cf": confidence,
    })
    return contact_id

def _safe_num(v):
    try:
        if v is None:
            return None
        x = float(v)
        if x != x or abs(x) > 1_000_000_000_000:
            return None
        return x
    except Exception:
        return None

def _fetch_source_rows(source_engine):
    with source_engine.connect() as src:
        rows = src.execute(text("""
          SELECT
            l.id,l.transaction,l.property_type,l.location,l.region,
            l.budget_text,l.budget_numeric,l.area_text,l.area_sqft_numeric,
            l.summary,l.confidence_score,l.status,l.duplicate_of,l.created_at,
            l.verified_at,l.verified_by,l.source_group_name,l.poster_name,l.raw_listing_text,
            ct.phone,ct.display_name,ct.firm_name,ct.trust_score
          FROM wai_listings l
          LEFT JOIN wai_contacts ct ON ct.id=l.contact_id
          ORDER BY l.created_at DESC
        """)).mappings().all()
        return {str(r["id"]): dict(r) for r in rows}

def _fetch_purity(primary_engine):
    with primary_engine.connect() as c:
        rows = c.execute(text("""
          SELECT *
          FROM ai_whatsapp_purity
          ORDER BY purity_score DESC,last_recovered_at DESC
        """)).mappings().all()
        return {str(r["listing_id"]): dict(r) for r in rows}

def _choose_cluster_representatives(source_rows, purity_rows):
    groups = defaultdict(list)
    no_cluster = []
    for listing_id, p in purity_rows.items():
        if p.get("review_status") != "USABLE":
            continue
        cluster = str(p.get("duplicate_cluster_key") or "").strip()
        item = (listing_id, p, source_rows.get(listing_id, {}))
        if cluster:
            groups[cluster].append(item)
        else:
            no_cluster.append(item)

    representatives = {x[0] for x in no_cluster}
    for items in groups.values():
        def rank(x):
            _, p, s = x
            verified = 1 if norm(s.get("status")) in {"verified", "approved"} else 0
            score = _safe_num(p.get("purity_score")) or 0
            created = str(s.get("created_at") or "")
            return (verified, score, created)
        representatives.add(max(items, key=rank)[0])
    return representatives

def _property_name(row, p):
    summary = str(row.get("summary") or "").strip()
    if summary:
        return summary[:180]
    loc = p.get("recovered_location") or row.get("location") or "Unknown"
    typ = p.get("recovered_property_type") or row.get("property_type") or "Property"
    return f"{loc} {typ}"[:180]

def _upsert_property(c, row, p, representative):
    rid = str(row.get("id"))
    loc = p.get("recovered_location")
    typ = p.get("recovered_property_type") or "UNKNOWN"
    tx = p.get("recovered_transaction") or "UNKNOWN"
    amin = _safe_num(p.get("recovered_area_min_sqft"))
    amax = _safe_num(p.get("recovered_area_max_sqft")) or amin
    budget = _safe_num(p.get("recovered_budget"))
    frontage = _safe_num(p.get("recovered_frontage_ft"))
    suitable = p.get("recovered_suitable_for")
    purity_score = _safe_num(p.get("purity_score")) or 0
    original_tx = norm(row.get("transaction"))
    recovered = (
        norm(loc) != norm(row.get("location")) or
        norm(typ) != norm(row.get("property_type")) or
        norm(tx) != original_tx
    )

    monthly = sale_price = None
    if tx == "SALE":
        sale_price = budget
    elif tx in {"LEASE", "LEASE_OR_SALE"}:
        monthly = budget

    ver = "VERIFIED" if norm(row.get("status")) in {"verified", "approved"} else "UNVERIFIED"
    inactive = norm(row.get("status")) in {"rejected", "inactive", "closed", "deleted"}
    eligible = bool(
        representative and p.get("review_status") == "USABLE" and not inactive and
        loc and amin and typ != "UNKNOWN" and tx in {"LEASE", "SALE", "LEASE_OR_SALE"}
    )

    contact_ref = _contact_upsert(c, row)
    identity = pid("wai_listings", rid)
    name = _property_name(row, p)
    payload = {
        "original": row,
        "purity_recovery": p,
        "integration_version": INTEGRATION_VERSION,
        "matcher_representative": representative,
    }

    c.execute(text("""
      INSERT INTO ai_property_identity(property_identity_id,canonical_label,last_seen_at)
      VALUES(:i,:n,NOW())
      ON CONFLICT(property_identity_id) DO UPDATE SET
        canonical_label=EXCLUDED.canonical_label,last_seen_at=NOW()
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
        :i,'WHATSAPP',:sn,'wai_listings',:rid,:name,:typ,NULL,:loc,:ln,:tx,
        :amin,:amax,:rr,NULL,:monthly,:sale,
        :floor,:floor,:raw,:front,:suitable,NULL,
        :ver,:avail,:contact,:complete,:confidence,:source_conf,:eligible,
        CAST(:payload AS jsonb),:version,NOW()
      )
      ON CONFLICT(source_table,source_record_id) DO UPDATE SET
        property_identity_id=EXCLUDED.property_identity_id,
        source_type=EXCLUDED.source_type,
        source_name=EXCLUDED.source_name,
        property_name=EXCLUDED.property_name,
        canonical_property_type=EXCLUDED.canonical_property_type,
        location_raw=EXCLUDED.location_raw,
        location_normalized=EXCLUDED.location_normalized,
        transaction_type=EXCLUDED.transaction_type,
        area_min_sqft=EXCLUDED.area_min_sqft,
        area_max_sqft=EXCLUDED.area_max_sqft,
        rent_original=EXCLUDED.rent_original,
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
        "i": identity,
        "sn": _source_label(row.get("source_group_name"), recovered),
        "rid": rid,
        "name": name,
        "typ": typ,
        "loc": loc,
        "ln": norm(loc),
        "tx": tx,
        "amin": amin,
        "amax": amax,
        "rr": str(row.get("budget_text") or ""),
        "monthly": monthly,
        "sale": sale_price,
        "floor": str(p.get("recovered_required_floor") or ""),
        "raw": str(row.get("raw_listing_text") or ""),
        "front": frontage,
        "suitable": suitable,
        "ver": ver,
        "avail": "INACTIVE" if inactive else "UNKNOWN",
        "contact": contact_ref,
        "complete": min(100, round(purity_score)),
        "confidence": min(100, round(purity_score, 2)),
        "source_conf": 90 if ver == "VERIFIED" else 70,
        "eligible": eligible,
        "payload": json.dumps(payload, default=str),
        "version": f"{VERSION}+{INTEGRATION_VERSION}",
    })
    return 1, 1 if eligible else 0, 1 if recovered else 0

def _upsert_requirement(c, row, p, representative):
    rid = str(row.get("id"))
    loc = p.get("recovered_location")
    typ = p.get("recovered_property_type") or "UNKNOWN"
    tx = p.get("recovered_transaction") or "UNKNOWN"
    amin = _safe_num(p.get("recovered_area_min_sqft"))
    amax = _safe_num(p.get("recovered_area_max_sqft")) or amin
    rent = _safe_num(p.get("recovered_budget")) if tx in {"LEASE", "LEASE_OR_SALE"} else None
    frontage = _safe_num(p.get("recovered_frontage_ft"))
    floor = p.get("recovered_required_floor")
    suitable = p.get("recovered_suitable_for")
    ver = "VERIFIED" if norm(row.get("status")) in {"verified", "approved"} else "UNVERIFIED"
    inactive = norm(row.get("status")) in {"rejected", "inactive", "closed", "deleted"}
    eligible = bool(
        representative and p.get("review_status") == "USABLE" and not inactive and
        loc and amin and amax and typ != "UNKNOWN" and tx in {"LEASE", "SALE", "LEASE_OR_SALE"}
    )
    types = [] if typ == "UNKNOWN" else [typ]
    code = f"WA-REQ-{rid.replace('-', '').upper()[:16]}"
    raw = str(row.get("raw_listing_text") or row.get("summary") or "")
    payload = {
        "original": row,
        "purity_recovery": p,
        "integration_version": INTEGRATION_VERSION,
        "matcher_representative": representative,
    }

    q = c.execute(text("""
      INSERT INTO ai_requirement_index(
        source_table,source_record_id,requirement_code,source_type,source_name,client_name,company_name,
        transaction_type,requirement_types,preferred_locations_raw,minimum_area_sqft,maximum_area_sqft,
        maximum_monthly_rent,minimum_frontage_ft,required_floor,suitable_for,additional_points,
        verification_status,status,match_eligible,original_payload,normalization_version,updated_at
      )
      VALUES(
        'wai_listings',:rid,:code,'WHATSAPP',:sn,:client,:company,:tx,CAST(:types AS jsonb),
        :loc,:amin,:amax,:rent,:front,:floor,:suitable,:add,:ver,:status,:eligible,
        CAST(:payload AS jsonb),:version,NOW()
      )
      ON CONFLICT(source_table,source_record_id) DO UPDATE SET
        requirement_code=EXCLUDED.requirement_code,
        source_type=EXCLUDED.source_type,
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
        "rid": rid,
        "code": code,
        "sn": _source_label(row.get("source_group_name"), True),
        "client": row.get("display_name") or row.get("poster_name"),
        "company": row.get("firm_name"),
        "tx": tx,
        "types": json.dumps(types),
        "loc": loc,
        "amin": amin,
        "amax": amax,
        "rent": rent,
        "front": frontage,
        "floor": floor,
        "suitable": suitable,
        "add": raw,
        "ver": ver,
        "status": "INACTIVE" if inactive else "ACTIVE",
        "eligible": eligible,
        "payload": json.dumps(payload, default=str),
        "version": f"{VERSION}+{INTEGRATION_VERSION}",
    }).scalar()

    c.execute(text("DELETE FROM ai_requirement_location WHERE requirement_index_id=:q"), {"q": q})
    if loc:
        for part in [x.strip() for x in str(loc).replace("/", ",").split(",") if x.strip()]:
            c.execute(text("""
              INSERT INTO ai_requirement_location(requirement_index_id,location_raw,location_normalized)
              VALUES(:q,:r,:n)
              ON CONFLICT(requirement_index_id,location_normalized)
              DO UPDATE SET location_raw=EXCLUDED.location_raw
            """), {"q": q, "r": part, "n": norm(part)})

    return 1, 1 if eligible else 0

def promote_purity_to_matcher(primary_engine, source_engine):
    source_rows = _fetch_source_rows(source_engine)
    purity_rows = _fetch_purity(primary_engine)
    representatives = _choose_cluster_representatives(source_rows, purity_rows)

    result = {
        "version": INTEGRATION_VERSION,
        "usable_purity_rows": 0,
        "promoted_properties": 0,
        "match_eligible_properties": 0,
        "recovered_properties": 0,
        "promoted_requirements": 0,
        "match_eligible_requirements": 0,
        "duplicate_rows_suppressed_from_matcher": 0,
        "review_rows_kept_out_of_matcher": 0,
    }

    for p in purity_rows.values():
        if p.get("review_status") == "USABLE":
            result["usable_purity_rows"] += 1
        else:
            result["review_rows_kept_out_of_matcher"] += 1

    with primary_engine.begin() as c:
        c.execute(text("""
          UPDATE ai_property_match_index x
          SET match_eligible=FALSE,updated_at=NOW()
          FROM ai_whatsapp_purity p
          WHERE x.source_table='wai_listings'
            AND x.source_record_id=p.listing_id::text
            AND p.review_status <> 'USABLE'
        """))
        c.execute(text("""
          UPDATE ai_requirement_index x
          SET match_eligible=FALSE,updated_at=NOW()
          FROM ai_whatsapp_purity p
          WHERE x.source_table='wai_listings'
            AND x.source_record_id=p.listing_id::text
            AND p.review_status <> 'USABLE'
        """))

        for listing_id, p in purity_rows.items():
            if p.get("review_status") != "USABLE":
                continue
            row = source_rows.get(listing_id)
            if not row:
                continue
            representative = listing_id in representatives
            if not representative:
                result["duplicate_rows_suppressed_from_matcher"] += 1

            role = p.get("recovered_role")
            if role == "SUPPLY":
                n, eligible, recovered = _upsert_property(c, row, p, representative)
                result["promoted_properties"] += n
                result["match_eligible_properties"] += eligible
                result["recovered_properties"] += recovered
            elif role == "REQUIREMENT":
                n, eligible = _upsert_requirement(c, row, p, representative)
                result["promoted_requirements"] += n
                result["match_eligible_requirements"] += eligible

    return result
