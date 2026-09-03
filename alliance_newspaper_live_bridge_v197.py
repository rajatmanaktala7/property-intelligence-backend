from __future__ import annotations

import hashlib
import re
from sqlalchemy import text

VERSION = "19.7-NEWSPAPER-LIVE-DIRECT-SYNC"

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
    s=str(v or "").lower().strip()
    s=re.sub(r"[^a-z0-9]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def _phones(v):
    out=[]
    for part in re.split(r"[/,;| ]+",str(v or "")):
        d=re.sub(r"\D","",part)
        if len(d)>10 and d.startswith("91"):
            d=d[-10:]
        if len(d)>=8 and d not in out:
            out.append(d)
    return " / ".join(out) if out else "-"

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
    for k in ("available_area_sqft","maximum_area_sqft","minimum_area_sqft","area_sqft","area"):
        v=r.get(k)
        if v not in (None,""):
            try: return f"{float(v):g} sq ft"
            except Exception: return str(v)
    return "Unknown"

def _details(r):
    parts=[]
    for label,key in [
        ("Property","property_name"),("Type","property_type"),("Floor","floor"),
        ("Possession","possession"),("Parking","parking"),("Suitable","suitable_category"),
        ("Nearby","nearby_brands"),("Remarks","remarks")
    ]:
        val=str(r.get(key) or "").strip()
        if val and val.lower() not in {"na","n/a","none","unknown","-"}:
            parts.append(f"{label}: {val}")
    return " | ".join(parts) if parts else "Details not fully readable"

def _price(r):
    for k in ("price","sale_amount","rent_amount"):
        v=r.get(k)
        if v not in (None,""):
            return str(v)
    remarks=str(r.get("remarks") or "")
    m=re.search(r"(?:₹|rs\.?|inr)?\s*\d[\d,.]*\s*(?:cr|crore|lac|lakh|lakhs|k|thousand)?",remarks,re.I)
    return m.group(0).strip() if m else "Price on request"

def _fingerprint(row):
    parts=[
        _norm(row["lead_type"]),_norm(row["locality"]),_norm(row["area"]),
        _norm(row["configuration_details"])[:220],_norm(row["price"]),_norm(row["phone_numbers"])
    ]
    return hashlib.sha256("||".join(parts).encode()).hexdigest()

def _source(core, source_id):
    with core.engine.connect() as c:
        return c.execute(text("""
            SELECT s.id,s.source_type,s.original_filename,s.source_reference,
                   sf.mime_type,sf.content,sf.sha256
            FROM pi_sources s
            JOIN pi_source_files sf ON sf.source_id=s.id
            WHERE s.id=:s
        """),{"s":source_id}).mappings().first()

def sync_source(core, source_id:int):
    _setup(core)
    src=_source(core,source_id)
    if not src:
        return {"status":"ERROR","source_id":source_id,"error":"Saved source/original not found"}
    if str(src["source_type"] or "").upper()!="NEWSPAPER":
        return {"status":"SKIPPED","source_id":source_id,"error":"Source is not NEWSPAPER"}

    with core.engine.connect() as c:
        rows=[dict(x) for x in c.execute(
            text("SELECT * FROM pi_properties WHERE source_id=:s ORDER BY id"),
            {"s":source_id}
        ).mappings().all()]

    source_hash=f"v197-{src['sha256']}"
    with core.engine.begin() as c:
        nsid=c.execute(text("""
            INSERT INTO pi_newspaper_sources(
              source_hash,original_filename,mime_type,image_content,source_label,
              extraction_status,updated_at
            ) VALUES(:h,:f,:m,:b,:label,'PROCESSING',NOW())
            ON CONFLICT(source_hash) DO UPDATE SET
              original_filename=EXCLUDED.original_filename,
              mime_type=EXCLUDED.mime_type,
              image_content=EXCLUDED.image_content,
              source_label=EXCLUDED.source_label,
              updated_at=NOW()
            RETURNING id
        """),{
            "h":source_hash,
            "f":src["original_filename"] or f"source-{source_id}",
            "m":src["mime_type"] or "application/octet-stream",
            "b":bytes(src["content"]),
            "label":src["source_reference"] or "Newspaper - Property",
        }).scalar_one()

    inserted=duplicates=already=0
    for i,r in enumerate(rows,1):
        pid=str(r.get("property_id") or r.get("id") or f"{source_id}-{i}")
        with core.engine.connect() as c:
            done=c.execute(text("""
                SELECT newspaper_record_id FROM pi_newspaper_capture_sync
                WHERE source_id=:s AND property_id=:p
            """),{"s":source_id,"p":pid}).first()
        if done:
            already+=1
            continue

        agency=str(r.get("broker_name") or r.get("owner_name") or "-").strip() or "-"
        phone=_phones(r.get("broker_contact") or r.get("owner_contact"))
        row={
            "lead_type":_lead_type(r),
            "locality":str(r.get("location") or r.get("property_name") or "Unknown").strip() or "Unknown",
            "area":_area(r),
            "configuration_details":_details(r),
            "price":_price(r),
            "agency_brand":agency,
            "contact_person":agency,
            "phone_numbers":phone,
            "notes":f"Imported from Newspaper Capture Source {source_id}; core property {pid}",
            "source":str(r.get("source") or "Newspaper - Property"),
            "completeness":"Partial",
        }
        fp=_fingerprint(row)

        with core.engine.begin() as c:
            existing=c.execute(text(
                "SELECT record_id FROM pi_newspaper_properties WHERE fingerprint=:fp LIMIT 1"
            ),{"fp":fp}).first()
            if existing:
                c.execute(text("""
                    INSERT INTO pi_newspaper_capture_sync(source_id,property_id,newspaper_record_id,sync_status)
                    VALUES(:s,:p,:rid,'DUPLICATE')
                    ON CONFLICT(source_id,property_id) DO NOTHING
                """),{"s":source_id,"p":pid,"rid":existing[0]})
                duplicates+=1
                continue

            rid="NEWS-"+hashlib.sha1(f"{source_id}|{pid}|{fp}".encode()).hexdigest()[:12].upper()
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
            """),{"s":source_id,"p":pid,"rid":rid})
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

    with core.engine.connect() as c:
        total=int(c.execute(text("SELECT COUNT(*) FROM pi_newspaper_properties")).scalar() or 0)

    return {
        "status":"SYNCED","version":VERSION,"source_id":source_id,
        "core_rows_found":len(rows),"inserted":inserted,"duplicates":duplicates,
        "already_synced":already,"newspaper_live_total":total,
        "database_url":"/newspaper-v83#newspaper-database"
    }
