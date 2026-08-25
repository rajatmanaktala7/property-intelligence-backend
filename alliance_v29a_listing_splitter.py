
import re
import json
from sqlalchemy import text
from fastapi import Request
from fastapi.responses import HTMLResponse

MODULE_VERSION = "2.9A3-BATCHED-MULTI-LISTING-SPLITTER"
BATCH_SIZE = 20

AREA_RE = re.compile(
    r"\b(\d{3,5})\s*(?:sq\.?\s*ft|sqft|square\s*feet|sft|sq\s*ft)\b",
    re.I
)

PROPERTY_TYPES = [
    "restaurant","cafe","bar","shop","showroom",
    "retail space","office space","commercial property"
]

def _norm(v):
    return re.sub(r"\s+", " ", str(v or "").strip())

def _ensure_schema(engine):
    with engine.begin() as c:
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_v29a_split_external_entity(
          split_entity_id BIGSERIAL PRIMARY KEY,
          discovery_id BIGINT NOT NULL,
          action_id BIGINT NOT NULL,
          requirement_code TEXT NOT NULL,
          external_entity_code TEXT NOT NULL UNIQUE,
          source_url TEXT NOT NULL,
          provider TEXT,
          property_name TEXT,
          location TEXT,
          property_type TEXT,
          transaction_type TEXT,
          area_min_sqft NUMERIC(14,2),
          area_max_sqft NUMERIC(14,2),
          monthly_rent NUMERIC(16,2),
          raw_entity_text TEXT NOT NULL,
          splitter_score NUMERIC(6,2) DEFAULT 0,
          splitter_status TEXT NOT NULL DEFAULT 'UNREVIEWED',
          splitter_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
          created_at TIMESTAMPTZ DEFAULT NOW(),
          updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """))
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_v29a_split_progress(
          discovery_id BIGINT PRIMARY KEY,
          next_index INT NOT NULL DEFAULT 0,
          total_blocks INT NOT NULL DEFAULT 0,
          complete BOOLEAN NOT NULL DEFAULT FALSE,
          updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """))

def _detect_type(text_blob):
    t = _norm(text_blob).lower()
    for x in PROPERTY_TYPES:
        if x in t:
            return x.upper().replace(" ","_")
    return "COMMERCIAL"

def _detect_transaction(text_blob):
    t = _norm(text_blob).lower()
    if any(x in t for x in ["for rent","rent","lease","to let"]):
        return "LEASE"
    if any(x in t for x in ["for sale","sale","outright"]):
        return "SALE"
    return "UNKNOWN"

def _extract_rent(text_blob):
    t = _norm(text_blob).lower()
    m = re.search(r"(?:₹|rs\.?|inr)\s*([\d,.]+)\s*(lacs?|lakhs?|crores?|cr)?", t, re.I)
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",",""))
    except Exception:
        return None
    u = (m.group(2) or "").lower()
    if u in {"lac","lacs","lakh","lakhs"}:
        v *= 100000
    elif u in {"crore","crores","cr"}:
        v *= 10000000
    return v

def _extract_location(text_blob):
    t = _norm(text_blob)
    for pat in [
        r"([^,.]{0,120}connaught place[^,.]{0,120})",
        r"([^,.]{0,120}connaught cir[^,.]{0,120})",
        r"([^,.]{0,120}(?:inner|middle|outer) circle[^,.]{0,120})",
        r"([^,.]{0,120}(?:kg|k\.g\.) marg[^,.]{0,120})",
        r"([^,.]{0,120}tolstoy road[^,.]{0,120})",
    ]:
        m = re.search(pat, t, re.I)
        if m:
            return _norm(m.group(1))
    return None

def split_aggregator_snippet(snippet):
    text = _norm(snippet)
    if not text:
        return []

    matches = list(AREA_RE.finditer(text))
    blocks = []

    for i, m in enumerate(matches):
        prev_end = matches[i-1].end() if i > 0 else 0
        start = max(prev_end, m.start() - 260)
        next_start = matches[i+1].start() if i+1 < len(matches) else len(text)
        end = min(next_start, m.end() + 220)
        block = text[start:end].strip(" ,;-")
        low = block.lower()

        has_type = any(x in low for x in PROPERTY_TYPES)
        has_loc = any(x in low for x in [
            "connaught place","connaught cir","inner circle",
            "middle circle","outer circle","kg marg","k.g. marg","tolstoy road"
        ])
        if has_type and has_loc:
            blocks.append(block)

    seen=set()
    out=[]
    for block in blocks:
        m=AREA_RE.search(block)
        area=m.group(1) if m else ""
        key=(area,re.sub(r"\W+"," ",block.lower())[:180])
        if key not in seen:
            seen.add(key)
            out.append(block)
    return out

def extract_entity(block):
    t=_norm(block)
    areas=[float(m.group(1)) for m in AREA_RE.finditer(t)]
    area=areas[-1] if areas else None
    return {
        "property_name":t[:220].strip(),
        "location":_extract_location(t),
        "property_type":_detect_type(t),
        "transaction_type":_detect_transaction(t),
        "area_min_sqft":area,
        "area_max_sqft":area,
        "monthly_rent":_extract_rent(t),
        "raw_entity_text":t,
    }

def score_entity(req,ent):
    score=0
    reasons=[]
    hard=False

    rtx=str(req.get("transaction_type") or "").upper()
    ptx=str(ent.get("transaction_type") or "").upper()
    if rtx and ptx!="UNKNOWN":
        if rtx==ptx:
            score+=20; reasons.append("Transaction aligned")
        else:
            hard=True; reasons.append("Transaction mismatch")

    if ent.get("location"):
        score+=30; reasons.append("Connaught Place location signal")
    else:
        reasons.append("Location needs verification")

    amin=ent.get("area_min_sqft")
    rmin=req.get("minimum_area_sqft")
    rmax=req.get("maximum_area_sqft")
    try:
        if amin is not None and rmin is not None and rmax is not None:
            amin=float(amin); rmin=float(rmin); rmax=float(rmax)
            if rmin<=amin<=rmax:
                score+=30; reasons.append("Area inside requirement")
            elif rmin*0.9<=amin<=rmax*1.1:
                score+=18; reasons.append("Area near requirement")
            else:
                hard=True; reasons.append("Area materially outside requirement")
        else:
            reasons.append("Area missing")
    except Exception:
        reasons.append("Area parse issue")

    ptype=str(ent.get("property_type") or "")
    if any(x in ptype for x in ["RESTAURANT","CAFE","BAR","SHOP","SHOWROOM","RETAIL"]):
        score+=15; reasons.append("Potential F&B / retail suitability")
    elif "OFFICE_SPACE" in ptype:
        score+=5; reasons.append("Office use requires suitability verification")

    if ent.get("monthly_rent") is not None:
        score+=5; reasons.append("Rent evidence present")

    score=max(0,min(100,score))
    if hard:
        score=min(score,59)

    if hard:
        status="REJECT"
    elif score>=80:
        status="VERIFY_FIRST"
    elif score>=65:
        status="REVIEW"
    else:
        status="LOW_PRIORITY"

    return score,status,reasons

def split_discovery_batch(engine,discovery_id,batch_size=BATCH_SIZE):
    _ensure_schema(engine)

    with engine.connect() as c:
        d=c.execute(text("""
          SELECT *
          FROM ai_v28_external_discovery
          WHERE discovery_id=:id
          LIMIT 1
        """),{"id":int(discovery_id)}).mappings().first()

    if not d:
        return {"version":MODULE_VERSION,"status":"NOT_FOUND","detail":"Discovery not found"}

    with engine.connect() as c:
        req=c.execute(text("""
          SELECT requirement_code,
                 preferred_locations_raw AS locations,
                 transaction_type,
                 minimum_area_sqft,
                 maximum_area_sqft,
                 suitable_for
          FROM ai_requirement_index
          WHERE requirement_code=:code
          ORDER BY requirement_index_id DESC
          LIMIT 1
        """),{"code":d["requirement_code"]}).mappings().first()

    blocks=split_aggregator_snippet(d.get("snippet") or "")
    total=len(blocks)

    with engine.begin() as c:
        progress=c.execute(text("""
          INSERT INTO ai_v29a_split_progress(discovery_id,next_index,total_blocks,complete,updated_at)
          VALUES(:id,0,:total,FALSE,NOW())
          ON CONFLICT(discovery_id) DO UPDATE SET
            total_blocks=EXCLUDED.total_blocks,
            updated_at=NOW()
          RETURNING next_index,total_blocks,complete
        """),{"id":int(discovery_id),"total":total}).mappings().one()

    start=int(progress["next_index"] or 0)
    if start>=total:
        return {
            "version":MODULE_VERSION,
            "discovery_id":int(discovery_id),
            "batch_size":int(batch_size),
            "evaluated_this_batch":0,
            "remaining_unprocessed":0,
            "complete":True,
            "next_step":"COMPLETE",
        }

    batch_size=max(1,min(int(batch_size or BATCH_SIZE),50))
    batch=blocks[start:start+batch_size]

    rows=[]
    for offset,block in enumerate(batch):
        idx=start+offset+1
        ent=extract_entity(block)
        score,status,reasons=score_entity(req or {},ent)
        rows.append({
            "discovery_id":int(discovery_id),
            "action_id":d["action_id"],
            "requirement_code":d["requirement_code"],
            "external_entity_code":f"EXT-{int(discovery_id)}-{idx:03d}",
            "source_url":d["source_url"],
            "provider":d["provider"],
            "property_name":ent["property_name"],
            "location":ent["location"],
            "property_type":ent["property_type"],
            "transaction_type":ent["transaction_type"],
            "area_min_sqft":ent["area_min_sqft"],
            "area_max_sqft":ent["area_max_sqft"],
            "monthly_rent":ent["monthly_rent"],
            "raw_entity_text":ent["raw_entity_text"],
            "splitter_score":score,
            "splitter_status":status,
            "splitter_reasons":json.dumps(reasons),
        })

    if rows:
        with engine.begin() as c:
            c.execute(text("""
              INSERT INTO ai_v29a_split_external_entity(
                discovery_id,action_id,requirement_code,external_entity_code,
                source_url,provider,property_name,location,property_type,
                transaction_type,area_min_sqft,area_max_sqft,monthly_rent,
                raw_entity_text,splitter_score,splitter_status,splitter_reasons,
                created_at,updated_at
              )
              VALUES(
                :discovery_id,:action_id,:requirement_code,:external_entity_code,
                :source_url,:provider,:property_name,:location,:property_type,
                :transaction_type,:area_min_sqft,:area_max_sqft,:monthly_rent,
                :raw_entity_text,:splitter_score,:splitter_status,
                CAST(:splitter_reasons AS jsonb),NOW(),NOW()
              )
              ON CONFLICT(external_entity_code) DO UPDATE SET
                property_name=EXCLUDED.property_name,
                location=EXCLUDED.location,
                property_type=EXCLUDED.property_type,
                transaction_type=EXCLUDED.transaction_type,
                area_min_sqft=EXCLUDED.area_min_sqft,
                area_max_sqft=EXCLUDED.area_max_sqft,
                monthly_rent=EXCLUDED.monthly_rent,
                raw_entity_text=EXCLUDED.raw_entity_text,
                splitter_score=EXCLUDED.splitter_score,
                splitter_status=EXCLUDED.splitter_status,
                splitter_reasons=EXCLUDED.splitter_reasons,
                updated_at=NOW()
            """),rows)

    next_index=start+len(batch)
    complete=next_index>=total
    remaining=max(0,total-next_index)

    with engine.begin() as c:
        c.execute(text("""
          UPDATE ai_v29a_split_progress
          SET next_index=:next_index,total_blocks=:total,complete=:complete,updated_at=NOW()
          WHERE discovery_id=:id
        """),{
            "next_index":next_index,
            "total":total,
            "complete":complete,
            "id":int(discovery_id),
        })

    return {
        "version":MODULE_VERSION,
        "discovery_id":int(discovery_id),
        "action_id":d["action_id"],
        "requirement_code":d["requirement_code"],
        "batch_size":batch_size,
        "evaluated_this_batch":len(batch),
        "entities_created_or_updated":len(rows),
        "verify_first_this_batch":sum(1 for x in rows if x["splitter_status"]=="VERIFY_FIRST"),
        "review_this_batch":sum(1 for x in rows if x["splitter_status"]=="REVIEW"),
        "rejected_this_batch":sum(1 for x in rows if x["splitter_status"]=="REJECT"),
        "processed_through_index":next_index,
        "total_blocks":total,
        "remaining_unprocessed":remaining,
        "complete":complete,
        "next_step":"Run next splitter batch" if not complete else "VERIFY_INDIVIDUAL_ENTITIES",
    }

def register_v29a_routes(core):
    app,engine=core.app,core.engine
    _ensure_schema(engine)

    @app.post("/api/v2/intelligence/v29a/split/{discovery_id}")
    def split(discovery_id:int,req:Request,batch_size:int=BATCH_SIZE):
        if hasattr(core,"need_login"):
            core.need_login(req)
        try:
            return split_discovery_batch(engine,discovery_id,batch_size)
        except Exception as exc:
            return {"version":MODULE_VERSION,"status":"ERROR","message":str(exc)}

    @app.get("/api/v2/intelligence/v29a/entities/{discovery_id}")
    def entities(discovery_id:int,req:Request,limit:int=200):
        if hasattr(core,"need_login"):
            core.need_login(req)
        limit=max(1,min(int(limit or 200),500))
        with engine.connect() as c:
            rows=c.execute(text("""
              SELECT *
              FROM ai_v29a_split_external_entity
              WHERE discovery_id=:id
              ORDER BY splitter_score DESC,external_entity_code
              LIMIT :lim
            """),{"id":int(discovery_id),"lim":limit}).mappings().all()
        return {"version":MODULE_VERSION,"count":len(rows),"entities":[dict(x) for x in rows]}

    @app.get("/v2/external-listing-splitter",response_class=HTMLResponse)
    def dashboard(req:Request):
        if hasattr(core,"need_login"):
            core.need_login(req)
        return HTMLResponse("""<!doctype html>
<html><head><meta charset="utf-8"><title>V2.9A3 Splitter</title></head>
<body style="font-family:Arial;background:#f5f7fa">
<div style="max-width:1050px;margin:30px auto;background:white;padding:28px;border-radius:14px">
<h1>V2.9A3 Batched Multi-Listing Splitter</h1>
<p>Processes aggregator pages in small batches to avoid Railway timeouts.</p>
<p>Default batch size: 20 entities.</p>
</div></body></html>""")
    return app
