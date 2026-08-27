from __future__ import annotations

import re, hashlib
from typing import Any, List, Optional
from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

VERSION = "LIVE-FEED-PURITY-1.0"

PROPERTY_HINTS = ["available for rent","available for sale","for sale","for rent","to-let","to let","rent","lease","sale","asking","demand","bhk","sqft","sq ft","villa","flat","apartment","office","shop","showroom","warehouse","commercial","farm house","farmhouse","plot","floor"]
REQUIREMENT_HINTS = ["wanted","requirement","required","looking for","need ","immediate required","urgent requirement","rental requirement","wanted for rent","client budget","tenant meeting"]
NOISE_HINTS = ["good morning","good night","good evening","happy birthday","रक्षाबंधन","शुभरात्रि","शुभकामनाएं","instagram.com","youtube.com","facebook.com/share","please remove such content","keep this group","music lovers"]

def _norm(v: Any) -> str:
    return re.sub(r"\s+"," ",str(v or "")).strip()

def _phone(v: Any) -> Optional[str]:
    d=re.sub(r"\D","",str(v or ""))
    if len(d)==12 and d.startswith("91"): d=d[2:]
    if len(d)==11 and d.startswith("0"): d=d[1:]
    return "+91"+d if len(d)==10 and d[0] in "6789" else None

def _phones(s: str) -> List[str]:
    out=[]
    for m in re.finditer(r"(?:\+?91[\s\-]*)?[6-9](?:[\s\-]*\d){9}",s or ""):
        p=_phone(m.group(0))
        if p and p not in out: out.append(p)
    return out

def _classify(textv: str) -> str:
    low=_norm(textv).lower()
    req=sum(1 for x in REQUIREMENT_HINTS if x in low)
    inv=sum(1 for x in PROPERTY_HINTS if x in low)
    if req and req>=inv: return "PROPERTY_REQUIREMENT"
    if inv: return "PROPERTY_INVENTORY"
    if any(x in low for x in NOISE_HINTS) or len(low)<18: return "REJECTED"
    return "REVIEW"

def _looks_new(line: str) -> bool:
    low=_norm(line).lower()
    if re.search(r"\b\d+\s*(?:bhk|bed)\b",low): return True
    if re.search(r"\b\d{2,6}\s*(?:sqft|sq\.?\s*ft|sqyd|sq yards?)\b",low): return True
    if any(x in low for x in ["available for rent","available for sale","for sale","for rent","to-let","to let"]): return True
    return False

def split_property_entities(textv: str) -> List[str]:
    if _classify(textv)!="PROPERTY_INVENTORY": return [textv]
    lines=[_norm(x) for x in str(textv or "").replace("\r","").split("\n") if _norm(x)]
    if len(lines)<=6: return [textv]
    blocks=[]; cur=[]; strong=0
    for line in lines:
        new=_looks_new(line)
        if new and cur and strong>=1 and len(cur)>=2:
            blocks.append("\n".join(cur)); cur=[line]; strong=1
        else:
            cur.append(line)
            if new: strong+=1
    if cur: blocks.append("\n".join(cur))
    meaningful=[b for b in blocks if _classify(b)=="PROPERTY_INVENTORY"]
    return meaningful if len(meaningful)>=2 else [textv]

def _ensure_tables(engine):
    ddl=[
      '''CREATE TABLE IF NOT EXISTS alliance_live_feed_entities(
      id BIGSERIAL PRIMARY KEY, source_event_id TEXT NOT NULL, entity_index INTEGER NOT NULL DEFAULT 1,
      entity_code TEXT UNIQUE NOT NULL, classification TEXT NOT NULL, raw_message TEXT NOT NULL,
      entity_text TEXT NOT NULL, source_group TEXT, sender_name TEXT, sender_phone TEXT,
      contact_phones TEXT, canonical_property_code TEXT, status TEXT DEFAULT 'ACTIVE',
      created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW(),
      UNIQUE(source_event_id,entity_index))''',
      "CREATE INDEX IF NOT EXISTS idx_live_feed_entities_class ON alliance_live_feed_entities(classification,status)",
      "CREATE INDEX IF NOT EXISTS idx_live_feed_entities_created ON alliance_live_feed_entities(created_at DESC)"
    ]
    with engine.begin() as c:
        for stmt in ddl: c.execute(text(stmt))

def _code(event_id,idx,body):
    return "LFE-"+hashlib.sha1(f"{event_id}|{idx}|{_norm(body).lower()}".encode()).hexdigest()[:12].upper()

def _source_events(core,limit=2500):
    with core.engine.connect() as c:
        if c.execute(text("SELECT to_regclass('public.wa_bridge_events') IS NOT NULL")).scalar():
            return c.execute(text('''SELECT e.id::text source_event_id,COALESCE(g.group_name,'') source_group,
                COALESCE(e.sender_name,'') sender_name,COALESCE(e.sender_phone,'') sender_phone,
                COALESCE(e.raw_text,'') raw_message,e.created_at
                FROM wa_bridge_events e LEFT JOIN wa_bridge_groups g ON g.group_id=e.group_id
                ORDER BY e.id DESC LIMIT :lim'''),{"lim":limit}).mappings().all()
    return []

def _sync(core,limit=2500):
    import alliance_v383_database_foundation as canon
    _ensure_tables(core.engine)
    events=_source_events(core,limit)
    counts={"source_events":0,"entities":0,"inventory":0,"requirements":0,"rejected":0}
    for ev in reversed(events):
        raw=str(ev["raw_message"] or "")
        parts=split_property_entities(raw) if _classify(raw)=="PROPERTY_INVENTORY" else [raw]
        with core.engine.begin() as c:
            for idx,part in enumerate(parts,1):
                cls=_classify(part); code=_code(str(ev["source_event_id"]),idx,part)
                phones=_phones(part); pc=None
                if cls=="PROPERTY_INVENTORY":
                    low=part.lower()
                    tx="SALE" if any(x in low for x in ["for sale","sale","asking","demand"]) else ("RENT" if any(x in low for x in ["for rent","rent","lease","to let","to-let"]) else "UNKNOWN")
                    ptype="Residential" if any(x in low for x in ["bhk","flat","apartment","villa","floor"]) else ("Office" if "office" in low else ("Commercial Shop" if any(x in low for x in ["shop","showroom","retail"]) else "Property"))
                    loc="UNKNOWN"
                    for a in c.execute(text("SELECT alias_text,canonical_location FROM alliance_location_aliases WHERE approved=TRUE ORDER BY length(alias_text) DESC")).mappings():
                        if a["alias_text"] and a["alias_text"].lower() in low:
                            loc=a["canonical_location"]; break
                    m=re.search(r"(\d{2,6})\s*(?:sqft|sq\.?\s*ft|sqyd|sq yards?)",low,re.I)
                    area=float(m.group(1)) if m else None
                    pc=canon._upsert_property(c,{"property_name":part[:220],"location":loc,"city":"Delhi NCR","building_project":part[:120],"property_type":ptype,"transaction_type":tx,"area_sqft":area,"floor":None,"intended_use_tags":ptype})
                    lc=canon._upsert_listing(c,pc,{"source_type":"WHATSAPP","source_table":"alliance_live_feed_entities","source_record_id":code,"source_name":ev["source_group"] or "WhatsApp","raw_text":part,"availability_status":"UNKNOWN","verification_status":"UNVERIFIED","verification_confidence":0,"captured_at":ev["created_at"]})
                    if phones: canon._upsert_contact(c,lc,ev["sender_name"],phones[0],"BROKER",True)
                    counts["inventory"]+=1
                elif cls=="PROPERTY_REQUIREMENT": counts["requirements"]+=1
                elif cls=="REJECTED": counts["rejected"]+=1
                c.execute(text('''INSERT INTO alliance_live_feed_entities(
                    source_event_id,entity_index,entity_code,classification,raw_message,entity_text,
                    source_group,sender_name,sender_phone,contact_phones,canonical_property_code,status,created_at,updated_at)
                    VALUES(:sid,:idx,:code,:cls,:raw,:entity,:grp,:sn,:sp,:phones,:pc,'ACTIVE',COALESCE(:created,NOW()),NOW())
                    ON CONFLICT(source_event_id,entity_index) DO UPDATE SET
                    entity_code=EXCLUDED.entity_code,classification=EXCLUDED.classification,raw_message=EXCLUDED.raw_message,
                    entity_text=EXCLUDED.entity_text,source_group=EXCLUDED.source_group,sender_name=EXCLUDED.sender_name,
                    sender_phone=EXCLUDED.sender_phone,contact_phones=EXCLUDED.contact_phones,
                    canonical_property_code=EXCLUDED.canonical_property_code,status='ACTIVE',updated_at=NOW()'''),
                    {"sid":str(ev["source_event_id"]),"idx":idx,"code":code,"cls":cls,"raw":raw,"entity":part,"grp":ev["source_group"],"sn":ev["sender_name"],"sp":ev["sender_phone"],"phones":" | ".join(phones),"pc":pc,"created":ev["created_at"]})
                counts["entities"]+=1
        counts["source_events"]+=1
    return {"status":"OK","version":VERSION,**counts}

def _rows(core,q="",limit=1000):
    _ensure_tables(core.engine); p={"lim":limit}; where=["classification='PROPERTY_INVENTORY'","status='ACTIVE'"]
    if q.strip():
        where.append("(entity_text ILIKE :q OR source_group ILIKE :q OR sender_name ILIKE :q OR contact_phones ILIKE :q)"); p["q"]="%"+q.strip()+"%"
    with core.engine.connect() as c:
        return c.execute(text(f'''SELECT entity_code,created_at,source_group,sender_name,contact_phones,entity_text,canonical_property_code
            FROM alliance_live_feed_entities WHERE {" AND ".join(where)} ORDER BY created_at DESC,id DESC LIMIT :lim'''),p).mappings().all()

def _esc(v):
    return str(v or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def register(wrapped):
    app=wrapped.app; core=wrapped.core
    app.router.routes[:]=[r for r in app.router.routes if not (getattr(r,"path",None) in {"/whatsapp-live/feed","/api/live-feed-purity/status"} and "GET" in (getattr(r,"methods",set()) or set()))]
    def status(): return _sync(core,2500)
    def feed(request:Request):
        _sync(core,2500)
        q=str(request.query_params.get("q") or "").strip(); rows=_rows(core,q,1000)
        trs="".join(f"<tr><td>{_esc(r['created_at'])}</td><td>{_esc(r['source_group'])}</td><td>{_esc(r['sender_name'])}</td><td style='min-width:520px;white-space:pre-wrap'>{_esc(r['entity_text'])}</td><td>{_esc(r['contact_phones'] or '—')}</td><td>{_esc(r['canonical_property_code'] or '—')}</td></tr>" for r in rows)
        html=f'''<!doctype html><html><head><meta charset="utf-8"><title>Clean Live Property Feed</title></head><body>
        <h2>Clean Live Property Feed</h2><form method=get><input name=q value="{_esc(q)}" placeholder="Search property, location, broker or phone"><button>Search</button></form>
        <p>{len(rows)} clean property entities. Noise removed, requirements separated, multi-property posts split.</p>
        <table border="1" cellspacing="0" cellpadding="6"><tr><th>Received</th><th>Group</th><th>Sender</th><th>Property Entity</th><th>Contact</th><th>Canonical Property</th></tr>{trs}</table></body></html>'''
        return HTMLResponse(html)
    app.add_api_route("/api/live-feed-purity/status",status,methods=["GET"])
    app.add_api_route("/whatsapp-live/feed",feed,methods=["GET"])
    return {"status":"REGISTERED","version":VERSION}
