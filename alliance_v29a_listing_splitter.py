
import re
import json
from sqlalchemy import text
from fastapi import Request
from fastapi.responses import HTMLResponse

MODULE_VERSION = "2.9.4B-BOUNDARY-CLEAN-ENTITY-SPLITTER"
BATCH_SIZE = 20

AREA_RE = re.compile(
    r"\b(\d{3,5})\s*(?:sq\.?\s*ft|sqft|square\s*feet|sft|sq\s*ft)\b",
    re.I
)

LISTING_START_RE = re.compile(
    r"\b(?:office space|shop|showroom|restaurant|retail space|commercial property|bar|cafe)"
    r"\s+in\s+.{0,160}?\s+(?:for rent|for sale|on lease|to let)\b",
    re.I
)

LOCATION_SIGNAL_RE = re.compile(
    r"\b(?:connaught place|connaught cir|connaught circus|inner circle|middle circle|outer circle|kg marg|k\.?g\.?\s*marg|tolstoy road|rajiv chowk)\b",
    re.I
)

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
        CREATE TABLE IF NOT EXISTS ai_v294_split_progress(
          discovery_id BIGINT PRIMARY KEY,
          next_index INT NOT NULL DEFAULT 0,
          total_entities INT NOT NULL DEFAULT 0,
          complete BOOLEAN NOT NULL DEFAULT FALSE,
          updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """))

def _parse_indian_rent(text_blob):
    t = _norm(text_blob).lower()

    m = re.search(
        r"(?:₹|rs\.?|inr)\s*(\d+)(?:\s*\.\s*(\d{1,2}))?"
        r"\s*(lacs?|lakhs?|lac|lakh|crores?|crore|cr)\b",
        t, re.I
    )
    if m:
        whole = m.group(1)
        frac = m.group(2)
        num = float(whole + ("." + frac if frac else ""))
        unit = m.group(3).lower()
        return round(num * (100000 if unit in {"lac","lacs","lakh","lakhs"} else 10000000), 2)

    m = re.search(r"(?:₹|rs\.?|inr)\s*([\d][\d,\s]{2,15})", t, re.I)
    if m:
        raw = re.sub(r"[^\d]", "", m.group(1))
        try:
            v = float(raw)
            if 1000 <= v <= 100000000:
                return v
        except Exception:
            pass
    return None

def _detect_type(text_blob):
    t = _norm(text_blob).lower()
    for x in ["restaurant","cafe","bar","shop","showroom","retail space","office space","commercial property"]:
        if t.startswith(x) or f" {x} " in f" {t} ":
            return x.upper().replace(" ","_")
    return "COMMERCIAL"

def _detect_transaction(text_blob):
    t = _norm(text_blob).lower()
    if any(x in t for x in ["for rent","lease","to let","on lease"]):
        return "LEASE"
    if any(x in t for x in ["for sale","sale","outright"]):
        return "SALE"
    return "UNKNOWN"

def _extract_location(text_blob):
    t = _norm(text_blob)
    for pat in [
        r"\b(?:block\s+[a-z0-9-]+\s*,?\s*)?connaught place\b",
        r"\bconnaught cir(?:cle)?\b",
        r"\b(?:inner|middle|outer) circle\b",
        r"\b(?:kg|k\.?g\.?)\s*marg\b",
        r"\btolstoy road\b",
        r"\brajiv chowk\b",
    ]:
        m = re.search(pat, t, re.I)
        if m:
            return _norm(m.group(0))
    return None

def split_clean_entities(snippet):
    text = _norm(snippet)
    if not text:
        return []

    starts = [m.start() for m in LISTING_START_RE.finditer(text)]
    if not starts:
        return []

    blocks = []
    for i, start in enumerate(starts):
        end = starts[i+1] if i+1 < len(starts) else len(text)
        block = text[start:end].strip(" ,;-")

        areas = list(AREA_RE.finditer(block))
        if not areas or not LOCATION_SIGNAL_RE.search(block):
            continue

        # Keep only the first explicit area for this listing.
        if len(areas) > 1:
            first = areas[0]
            block = block[:min(len(block), first.end()+120)].strip(" ,;-")

        blocks.append(block)

    seen = set()
    out = []
    for b in blocks:
        key = re.sub(r"\W+"," ",b.lower())
        if key not in seen:
            seen.add(key)
            out.append(b)
    return out

def extract_entity(block):
    t = _norm(block)
    areas = [float(m.group(1)) for m in AREA_RE.finditer(t)]
    area = areas[0] if areas else None
    return {
        "property_name": t[:220],
        "location": _extract_location(t),
        "property_type": _detect_type(t),
        "transaction_type": _detect_transaction(t),
        "area_min_sqft": area,
        "area_max_sqft": area,
        "monthly_rent": _parse_indian_rent(t),
        "raw_entity_text": t,
    }

def score_entity(req, ent):
    score = 0
    reasons = []
    hard = False

    rtx = str(req.get("transaction_type") or "").upper()
    ptx = str(ent.get("transaction_type") or "").upper()
    if rtx and ptx != "UNKNOWN":
        if rtx == ptx:
            score += 20
            reasons.append("Transaction aligned")
        else:
            hard = True
            reasons.append("Transaction mismatch")

    if ent.get("location"):
        score += 30
        reasons.append("Connaught Place location signal")
    else:
        reasons.append("Location needs verification")

    area = ent.get("area_min_sqft")
    rmin = req.get("minimum_area_sqft")
    rmax = req.get("maximum_area_sqft")
    try:
        if area is not None and rmin is not None and rmax is not None:
            area=float(area); rmin=float(rmin); rmax=float(rmax)
            if rmin <= area <= rmax:
                score += 30
                reasons.append("Area inside requirement")
            elif rmin*0.9 <= area <= rmax*1.1:
                score += 18
                reasons.append("Area near requirement")
            else:
                hard = True
                reasons.append("Area materially outside requirement")
    except Exception:
        reasons.append("Area parse issue")

    ptype = str(ent.get("property_type") or "")
    if any(x in ptype for x in ["RESTAURANT","CAFE","BAR","SHOP","SHOWROOM","RETAIL"]):
        score += 15
        reasons.append("Potential F&B / retail suitability")
    elif "OFFICE_SPACE" in ptype:
        score += 5
        reasons.append("Office use requires suitability verification")

    if ent.get("monthly_rent") is not None:
        score += 5
        reasons.append("Rent evidence present")

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

def split_discovery_batch(engine, discovery_id, batch_size=BATCH_SIZE):
    _ensure_schema(engine)

    with engine.connect() as c:
        d = c.execute(text("""
          SELECT *
          FROM ai_v28_external_discovery
          WHERE discovery_id=:id
          LIMIT 1
        """), {"id":int(discovery_id)}).mappings().first()

    if not d:
        return {"version":MODULE_VERSION,"status":"NOT_FOUND","detail":"Discovery not found"}

    with engine.connect() as c:
        req = c.execute(text("""
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
        """), {"code":d["requirement_code"]}).mappings().first()

    blocks = split_clean_entities(d.get("snippet") or "")
    total = len(blocks)

    # V2.9.4 is a new splitter model: clear old split rows for this discovery
    # on first run only, so stale contaminated entities do not remain.
    with engine.begin() as c:
        prog = c.execute(text("""
          SELECT next_index,total_entities,complete
          FROM ai_v294_split_progress
          WHERE discovery_id=:id
        """), {"id":int(discovery_id)}).mappings().first()

        if not prog:
            c.execute(text("""
              DELETE FROM ai_v29a_split_external_entity
              WHERE discovery_id=:id
            """), {"id":int(discovery_id)})
            c.execute(text("""
              INSERT INTO ai_v294_split_progress(discovery_id,next_index,total_entities,complete,updated_at)
              VALUES(:id,0,:total,FALSE,NOW())
            """), {"id":int(discovery_id),"total":total})
            start = 0
        else:
            start = int(prog["next_index"] or 0)

    batch_size = max(1,min(int(batch_size or BATCH_SIZE),50))

    if start >= total:
        return {
            "version":MODULE_VERSION,
            "discovery_id":int(discovery_id),
            "evaluated_this_batch":0,
            "remaining_unprocessed":0,
            "complete":True,
            "next_step":"VERIFY_INDIVIDUAL_ENTITIES",
        }

    batch = blocks[start:start+batch_size]
    rows = []

    for offset, block in enumerate(batch):
        idx = start + offset + 1
        ent = extract_entity(block)
        score,status,reasons = score_entity(req or {},ent)
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
            """), rows)

    next_index = start + len(batch)
    complete = next_index >= total

    with engine.begin() as c:
        c.execute(text("""
          UPDATE ai_v294_split_progress
          SET next_index=:next_index,total_entities=:total,complete=:complete,updated_at=NOW()
          WHERE discovery_id=:id
        """), {
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
        "total_entities":total,
        "remaining_unprocessed":max(0,total-next_index),
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
<html><head><meta charset="utf-8"><title>V2.9.4 Boundary Clean Splitter</title></head>
<body style="font-family:Arial;background:#f5f7fa">
<div style="max-width:1050px;margin:30px auto;background:white;padding:28px;border-radius:14px">
<h1>V2.9.4B Boundary-Clean Entity Splitter</h1>
<p>One listing = one entity. Neighboring listings are isolated at true listing starts.</p>
<p>Indian rent formats are normalized correctly.</p>
</div></body></html>""")
    return app
