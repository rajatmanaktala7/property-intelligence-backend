from __future__ import annotations
import hashlib
import re
from sqlalchemy import text

VERSION = "19.6-NEWSPAPER-LIVE-BRIDGE"
DDL = """
CREATE TABLE IF NOT EXISTS pi_newspaper_capture_sync(
 source_id BIGINT NOT NULL,
 property_id TEXT NOT NULL,
 newspaper_record_id TEXT,
 sync_status TEXT DEFAULT 'SYNCED',
 created_at TIMESTAMPTZ DEFAULT NOW(),
 PRIMARY KEY(source_id, property_id)
);
"""

def _setup(core):
    with core.engine.begin() as c:
        c.execute(text(DDL))

def _norm(v):
    s = str(v or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def _phones(v):
    vals=[]
    for part in re.split(r"[/,;| ]+", str(v or "")):
        d=re.sub(r"\D","",part)
        if len(d)>10 and d.startswith("91"):
            d=d[-10:]
        if len(d)>=8 and d not in vals:
            vals.append(d)
    return " / ".join(vals) if vals else "-"

def _lead_type(r):
    tx=_norm(r.get("rent_or_sale"))
    ptype=_norm(r.get("property_type"))
    if "rent" in tx: return "Available - Rent"
    if "lease" in tx: return "Available - Lease"
    if "sale" in tx or "sell" in tx: return "Available - Sale"
    if any(x in ptype for x in ("office","retail","commercial","shop","showroom","industrial","warehouse")):
        return "Available - Commercial"
    return "Unknown"

def _area(r):
    v=r.get("available_area_sqft")
    if v is None:
        v=r.get("maximum_area_sqft") or r.get("minimum_area_sqft")
    if v is None: return "Unknown"
    try: return f"{float(v):g} sq ft"
    except Exception: return str(v)

def _details(r):
    parts=[]
    for label,key in [
        ("Property","property_name"),("Type","property_type"),("Floor","floor"),
        ("Possession","possession"),("Parking","parking"),
        ("Suitable","suitable_category"),("Nearby","nearby_brands"),("Remarks","remarks")
    ]:
        val=str(r.get(key) or "").strip()
        if val and val.lower() not in {"na","n/a","none","unknown"}:
            parts.append(f"{label}: {val}")
    return " | ".join(parts) if parts else "Details not fully readable"

def _price(r):
    remarks=str(r.get("remarks") or "").strip()
    m=re.search(r"(?:₹|rs\.?|inr)?\s*\d[\d,.]*\s*(?:cr|crore|lac|lakh|lakhs|k|thousand)?", remarks, re.I)
    return m.group(0).strip() if m else "Price on request"

def _fp(row):
    parts=[_norm(row["lead_type"]),_norm(row["locality"]),_norm(row["area"]),
           _norm(row["configuration_details"])[:220],_norm(row["price"]),_norm(row["phone_numbers"])]
    return hashlib.sha256("||".join(parts).encode()).hexdigest()

def sync_source(core, source_id:int):
    _setup(core)
    with core.engine.connect() as c:
        src=c.execute(text("""
            SELECT s.id,s.source_type,s.original_filename,s.source_reference,
                   sf.mime_type,sf.content,sf.sha256,
                   j.model
            FROM pi_sources s
            JOIN pi_source_files sf ON sf.source_id=s.id
            LEFT JOIN LATERAL (
              SELECT model FROM pi_ai_jobs x
              WHERE x.source_id=s.id ORDER BY x.created_at DESC LIMIT 1
            ) j ON TRUE
            WHERE s.id=:s
        """),{"s":source_id}).mappings().first()
    if not src:
        return {"status":"SKIPPED","reason":"source/original missing","source_id":source_id}
    if str(src["source_type"] or "").upper()!="NEWSPAPER":
        return {"status":"SKIPPED","reason":"not newspaper","source_id":source_id}

    source_hash=f"v196-{src['sha256']}"
    with core.engine.begin() as c:
        nsid=c.execute(text("""
            INSERT INTO pi_newspaper_sources(
              source_hash,original_filename,mime_type,image_content,source_label,
              ai_model,extraction_status,updated_at
            ) VALUES(:h,:f,:m,:b,:label,:model,'PROCESSING',NOW())
            ON CONFLICT(source_hash) DO UPDATE SET
              original_filename=EXCLUDED.original_filename,
              mime_type=EXCLUDED.mime_type,
              image_content=EXCLUDED.image_content,
              source_label=EXCLUDED.source_label,
              ai_model=EXCLUDED.ai_model,
              updated_at=NOW()
            RETURNING id
        """),{
            "h":source_hash,
            "f":src["original_filename"] or f"source-{source_id}",
            "m":src["mime_type"] or "application/octet-stream",
            "b":bytes(src["content"]),
            "label":src["source_reference"] or "Newspaper - Property",
            "model":src["model"],
        }).scalar_one()

    with core.engine.connect() as c:
        rows=c.execute(text("""
            SELECT property_id,property_name,property_type,city,location,
                   available_area_sqft,minimum_area_sqft,maximum_area_sqft,
                   floor,rent_or_sale,possession,nearby_brands,suitable_category,
                   parking,owner_name,owner_contact,broker_name,broker_contact,
                   remarks,source,extraction_confidence
            FROM pi_properties
            WHERE source_id=:s
            ORDER BY id
        """),{"s":source_id}).mappings().all()

    inserted=duplicates=already=0
    for raw in rows:
        r=dict(raw)
        with core.engine.connect() as c:
            done=c.execute(text("""
              SELECT newspaper_record_id FROM pi_newspaper_capture_sync
              WHERE source_id=:s AND property_id=:p
            """),{"s":source_id,"p":r["property_id"]}).first()
        if done:
            already+=1
            continue

        agency=str(r.get("broker_name") or r.get("owner_name") or "-").strip() or "-"
        row={
            "lead_type":_lead_type(r),
            "locality":str(r.get("location") or r.get("property_name") or "Unknown").strip(),
            "area":_area(r),
            "configuration_details":_details(r),
            "price":_price(r),
            "agency_brand":agency,
            "contact_person":agency,
            "phone_numbers":_phones(r.get("broker_contact") or r.get("owner_contact")),
            "notes":f"Persistent Newspaper Capture source {source_id}; core property {r['property_id']}",
            "source":str(r.get("source") or "Newspaper - Property"),
            "completeness":"Partial",
        }
        fp=_fp(row)
        with core.engine.begin() as c:
            exists=c.execute(text("SELECT record_id FROM pi_newspaper_properties WHERE fingerprint=:f LIMIT 1"),{"f":fp}).first()
            if exists:
                c.execute(text("""
                  INSERT INTO pi_newspaper_capture_sync(source_id,property_id,newspaper_record_id,sync_status)
                  VALUES(:s,:p,:rid,'DUPLICATE')
                  ON CONFLICT(source_id,property_id) DO NOTHING
                """),{"s":source_id,"p":r["property_id"],"rid":exists[0]})
                duplicates+=1
                continue
            rid="NEWS-"+hashlib.sha1(f"{source_id}|{r['property_id']}|{fp}".encode()).hexdigest()[:12].upper()
            c.execute(text("""
              INSERT INTO pi_newspaper_properties(
                record_id,source_id,fingerprint,lead_type,locality,area,
                configuration_details,price,agency_brand,contact_person,
                phone_numbers,notes,source,completeness,verification,team_member
              ) VALUES(
                :rid,:nsid,:fp,:lead_type,:locality,:area,
                :configuration_details,:price,:agency_brand,:contact_person,
                :phone_numbers,:notes,:source,:completeness,'Unverified',''
              )
            """),{"rid":rid,"nsid":nsid,"fp":fp,**row})
            c.execute(text("""
              INSERT INTO pi_newspaper_capture_sync(source_id,property_id,newspaper_record_id,sync_status)
              VALUES(:s,:p,:rid,'SYNCED')
              ON CONFLICT(source_id,property_id) DO NOTHING
            """),{"s":source_id,"p":r["property_id"],"rid":rid})
            inserted+=1

    with core.engine.begin() as c:
        c.execute(text("""
          UPDATE pi_newspaper_sources
          SET extraction_status='COMPLETED',
              extracted_records=(SELECT COUNT(*) FROM pi_newspaper_capture_sync WHERE source_id=:s AND sync_status='SYNCED'),
              duplicate_records=(SELECT COUNT(*) FROM pi_newspaper_capture_sync WHERE source_id=:s AND sync_status='DUPLICATE'),
              updated_at=NOW()
          WHERE id=:nsid
        """),{"s":source_id,"nsid":nsid})

    return {"status":"SYNCED","version":VERSION,"source_id":source_id,"newspaper_source_id":nsid,
            "core_records":len(rows),"inserted":inserted,"duplicates":duplicates,"already_synced":already,
            "database":"/newspaper-v83#newspaper-database"}
