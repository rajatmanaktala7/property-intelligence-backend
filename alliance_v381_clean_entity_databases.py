
from __future__ import annotations
import os, re, hashlib
from pathlib import Path
from fastapi import APIRouter, Request, Body, Query, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text, create_engine

VERSION = "3.8.1-CLEAN-ENTITY-DATABASES-CRUD"

SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_clean_property_entity(
  id BIGSERIAL PRIMARY KEY,
  entity_code TEXT UNIQUE NOT NULL,
  source_type TEXT NOT NULL,
  source_table TEXT,
  source_record_id TEXT,
  parent_message_id TEXT,
  source_name TEXT,
  property_name TEXT,
  description TEXT,
  location TEXT,
  property_type TEXT,
  transaction_type TEXT,
  area_sqft NUMERIC(14,2),
  rent_inr NUMERIC(16,2),
  sale_price_inr NUMERIC(16,2),
  contact_name TEXT,
  contact_phone TEXT,
  verification_status TEXT DEFAULT 'UNVERIFIED',
  capture_date TIMESTAMPTZ,
  active BOOLEAN DEFAULT TRUE,
  raw_text TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(source_type,source_table,source_record_id)
);
"""

def _db_url(u):
    u=(u or "").strip()
    if u.startswith("postgres://"):
        return u.replace("postgres://","postgresql+psycopg://",1)
    if u.startswith("postgresql://"):
        return u.replace("postgresql://","postgresql+psycopg://",1)
    return u

def _wa_engine():
    u=os.getenv("WHATSAPP_DATABASE_URL","").strip()
    return create_engine(_db_url(u),pool_pre_ping=True,pool_recycle=300) if u else None

def _ensure_schema(engine):
    with engine.begin() as c:
        for stmt in [x.strip() for x in SCHEMA.split(";") if x.strip()]:
            c.execute(text(stmt))

def _num(v):
    if v in (None,""): return None
    try: return float(v)
    except: pass
    m=re.search(r"(\d+(?:\.\d+)?)",str(v).replace(",",""))
    return float(m.group(1)) if m else None

def _money(v):
    if v in (None,""): return None
    if isinstance(v,(int,float)): return float(v)
    s=str(v).lower().replace(",","").replace("₹","")
    m=re.search(r"(\d+(?:\.\d+)?)",s)
    if not m: return None
    n=float(m.group(1))
    if "crore" in s or re.search(r"\bcr\b",s): n*=10000000
    elif "lakh" in s or "lac" in s or re.search(r"\bl\b",s): n*=100000
    return n

def _phone(s):
    m=re.search(r"(?<!\d)(?:\+?91[\s-]?)?([6-9]\d{9})(?!\d)",s or "")
    return m.group(1) if m else None

def _clean(s):
    return re.sub(r"[\*_`]+","",str(s or "")).strip()

def _split_blocks(raw):
    raw=(raw or "").replace("\r","\n")
    parts=re.split(r"\n\s*[_\-=]{5,}\s*\n",raw)
    return [p.strip() for p in parts if re.search(r"\b\d{3,6}\s*(?:sq\.?\s*ft|sqft|sft)\b",p,re.I)]

def _extract_block(block,parent):
    clean=_clean(block)
    low=clean.lower()

    m=re.search(r"(?:size\s*[-:]?\s*)?(\d{3,6}(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|sft)",clean,re.I)
    area=float(m.group(1)) if m else None

    tenant=None
    for pat in [r"tenant\s*[-:]\s*([^\n]+)",r"brand\s*[-:]\s*([^\n]+)",r"leased\s+to\s+([^\n]+)"]:
        mm=re.search(pat,clean,re.I)
        if mm:
            tenant=_clean(mm.group(1))
            break

    project=None
    for name in ["ELAN MIRACLE","AIPL JOY GALLERY","AIPL JOY CENTRAL","AIPL JOY STREET"]:
        if name.lower() in low:
            project=name.title()
            break

    loc=None
    lm=re.search(r"(sector\s*\d+[A-Za-z]?\s*,?\s*(?:gurugram|gurgaon)?)",clean,re.I)
    if lm: loc=_clean(lm.group(1))
    elif "dwarka expressway" in low:
        sm=re.search(r"dwarka expressway\s*sector\s*(\d+)",clean,re.I)
        loc="Dwarka Expressway Sector "+sm.group(1) if sm else "Dwarka Expressway"

    floor=None
    fm=re.search(r"\b(ground floor|first floor|second floor|lower ground|basement)\b",clean,re.I)
    if fm: floor=fm.group(1).title()

    sale=None
    sm=re.search(r"(?:demand|asking)\s*[-:]\s*(?:rs\.?\s*)?([\d.]+\s*(?:cr|crore|lakh|lac))",clean,re.I)
    if sm: sale=_money(sm.group(1))

    rent=None
    rm=re.search(r"rent\s*[-:]\s*(?:rs\.?\s*)?([\d,.]+)\s*(?:/-)?\s*per\s*month",clean,re.I)
    if rm: rent=_money(rm.group(1))
    else:
        rm2=re.search(r"rent\s*[-:]\s*([\d.]+\s*l)\b",clean,re.I)
        if rm2: rent=_money(rm2.group(1))

    phone=_phone(parent)
    contact=None
    cm=re.search(r"(?:more details call|contact)\s*\n?\s*([A-Za-z ]{2,40})\s*\n?\s*(?:\+?91[\s-]?)?[6-9]\d{9}",parent or "",re.I|re.S)
    if cm: contact=_clean(cm.group(1))

    title=" · ".join(x for x in [project,tenant,f"{int(area)} sqft" if area else None] if x) or "WhatsApp Property"
    desc=" | ".join(x for x in [project,tenant,loc,floor,f"Area {int(area)} sqft" if area else None] if x)
    if rent: desc += (" | " if desc else "") + f"Rent ₹{rent:,.0f}/month"
    if sale: desc += (" | " if desc else "") + f"Asking ₹{sale:,.0f}"
    desc=(desc+"\n"+clean).strip()

    return {
        "property_name":title,
        "description":desc,
        "location":loc,
        "property_type":"Restaurant" if "restaurant" in low else "Commercial Shop",
        "transaction_type":"SALE" if sale else ("LEASE" if "lease" in low or rent else "UNKNOWN"),
        "area_sqft":area,
        "rent_inr":rent,
        "sale_price_inr":sale,
        "contact_name":contact,
        "contact_phone":phone,
        "raw_text":block
    }

def _exists(engine,name):
    try:
        with engine.connect() as c:
            return bool(c.execute(text("SELECT to_regclass(:n) IS NOT NULL"),{"n":"public."+name}).scalar())
    except:
        return False

def _upsert(c,row):
    row=dict(row)
    row["entity_code"]="CLE-"+hashlib.sha1(
        f"{row['source_type']}|{row['source_table']}|{row['source_record_id']}".encode()
    ).hexdigest()[:12].upper()

    c.execute(text("""
      INSERT INTO ai_clean_property_entity(
        entity_code,source_type,source_table,source_record_id,parent_message_id,source_name,
        property_name,description,location,property_type,transaction_type,area_sqft,rent_inr,
        sale_price_inr,contact_name,contact_phone,verification_status,capture_date,active,raw_text)
      VALUES(
        :entity_code,:source_type,:source_table,:source_record_id,:parent_message_id,:source_name,
        :property_name,:description,:location,:property_type,:transaction_type,:area_sqft,:rent_inr,
        :sale_price_inr,:contact_name,:contact_phone,:verification_status,:capture_date,TRUE,:raw_text)
      ON CONFLICT(source_type,source_table,source_record_id) DO UPDATE SET
        property_name=EXCLUDED.property_name,
        description=EXCLUDED.description,
        location=EXCLUDED.location,
        property_type=EXCLUDED.property_type,
        transaction_type=EXCLUDED.transaction_type,
        area_sqft=EXCLUDED.area_sqft,
        rent_inr=EXCLUDED.rent_inr,
        sale_price_inr=EXCLUDED.sale_price_inr,
        contact_name=COALESCE(EXCLUDED.contact_name,ai_clean_property_entity.contact_name),
        contact_phone=COALESCE(EXCLUDED.contact_phone,ai_clean_property_entity.contact_phone),
        verification_status=EXCLUDED.verification_status,
        capture_date=EXCLUDED.capture_date,
        active=TRUE,
        raw_text=EXCLUDED.raw_text,
        updated_at=NOW()
    """),row)

def register(core):
    app=core.app
    engine=core.engine
    need_login=core.need_login
    page_role_or_redirect=core.page_role_or_redirect
    _ensure_schema(engine)
    router=APIRouter()

    @router.get("/api/v381/status")
    def status(req:Request):
        need_login(req)
        return {"version":VERSION,"status":"OK","description":True,"edit":True,"delete":True}

    @router.post("/api/v381/sync/whatsapp")
    def sync_whatsapp(req:Request,limit:int=Query(1000,ge=1,le=5000)):
        need_login(req)
        w=_wa_engine()
        if w is None: raise HTTPException(503,"WHATSAPP_DATABASE_URL not configured")
        with w.connect() as wc:
            rows=wc.execute(text("""
                SELECT m.message_id,m.raw_text,m.created_at,s.group_name,m.sender_name,m.sender_phone
                FROM wa_messages m
                LEFT JOIN wa_sources s ON s.source_id=m.source_id
                WHERE m.classification='PROPERTY_INVENTORY'
                ORDER BY m.id DESC LIMIT :lim
            """),{"lim":limit}).mappings().all()

        count=0
        with engine.begin() as c:
            for r in rows:
                blocks=_split_blocks(r["raw_text"]) or [r["raw_text"]]
                for i,b in enumerate(blocks,1):
                    x=_extract_block(b,r["raw_text"])
                    if not x["contact_name"]: x["contact_name"]=r.get("sender_name")
                    if not x["contact_phone"]: x["contact_phone"]=r.get("sender_phone")
                    _upsert(c,{
                        "source_type":"WHATSAPP",
                        "source_table":"wa_messages",
                        "source_record_id":f"{r['message_id']}:{i}",
                        "parent_message_id":str(r["message_id"]),
                        "source_name":r.get("group_name") or "WhatsApp",
                        "verification_status":"UNVERIFIED",
                        "capture_date":r.get("created_at"),
                        **x
                    })
                    count+=1
        return {"status":"OK","clean_entities_created_or_updated":count}

    @router.post("/api/v381/sync/source/{source}")
    def sync_source(source:str,req:Request,limit:int=Query(5000,ge=1,le=10000)):
        need_login(req)
        src=source.upper()
        if src=="WHATSAPP":
            return sync_whatsapp(req,min(limit,5000))

        table={"NEWSPAPER":"pi_newspaper_properties","MAGAZINE":"pi_magazine_master"}.get(src)
        if src=="MANUAL":
            for t in ["ai_manual_property_final","pi_manual_property_final","manual_property_final"]:
                if _exists(engine,t):
                    table=t
                    break

        if not table or not _exists(engine,table):
            return {"status":"NOT_READY","source":src,"count":0}

        with engine.connect() as c:
            rows=[dict(x) for x in c.execute(text(f'SELECT * FROM "{table}" LIMIT :lim'),{"lim":limit}).mappings().all()]

        count=0
        with engine.begin() as c:
            for i,d in enumerate(rows):
                rid=str(d.get("record_id") or d.get("source_id") or d.get("property_code") or d.get("id") or i)

                if src=="NEWSPAPER":
                    desc=d.get("notes") or d.get("configuration_details") or ""
                    row=dict(
                        source_type=src,source_table=table,source_record_id=rid,parent_message_id=None,
                        source_name=d.get("source") or "Newspaper",
                        property_name=d.get("locality") or d.get("agency_brand") or "Newspaper Property",
                        description=desc,location=d.get("locality"),property_type=d.get("lead_type"),
                        transaction_type=d.get("lead_type"),area_sqft=_num(d.get("area")),
                        rent_inr=_money(d.get("price")),sale_price_inr=None,
                        contact_name=d.get("contact_person") or d.get("agency_brand"),
                        contact_phone=d.get("phone_numbers"),
                        verification_status=d.get("verification") or "UNVERIFIED",
                        capture_date=d.get("created_at") or d.get("updated_at"),raw_text=desc
                    )
                elif src=="MAGAZINE":
                    desc=" | ".join(str(x) for x in [d.get("configuration"),d.get("status_remarks"),d.get("original_raw_text")] if x)
                    row=dict(
                        source_type=src,source_table=table,source_record_id=rid,parent_message_id=None,
                        source_name="Magazine Master",
                        property_name=d.get("locality") or d.get("plot_block") or "Magazine Property",
                        description=desc,location=d.get("locality"),property_type=d.get("category"),
                        transaction_type=d.get("listing_type"),area_sqft=_num(d.get("area")),
                        rent_inr=_money(d.get("price")),sale_price_inr=None,
                        contact_name=d.get("contact_name_company"),
                        contact_phone=d.get("valid_mobiles") or d.get("valid_landlines"),
                        verification_status=d.get("record_status") or "UNVERIFIED",
                        capture_date=d.get("updated_at"),raw_text=d.get("original_raw_text") or desc
                    )
                else:
                    desc=d.get("description") or d.get("remarks") or d.get("notes") or d.get("property_name") or ""
                    row=dict(
                        source_type=src,source_table=table,source_record_id=rid,parent_message_id=None,
                        source_name="Manual Property Database",
                        property_name=d.get("property_name") or d.get("property_code") or d.get("location") or "Manual Property",
                        description=desc,location=d.get("location") or d.get("locality"),
                        property_type=d.get("property_type"),
                        transaction_type=d.get("transaction_type") or d.get("rent_sale"),
                        area_sqft=_num(d.get("area_sqft") or d.get("available_area") or d.get("area")),
                        rent_inr=_money(d.get("rent_amount") or d.get("rent_inr") or d.get("rent")),
                        sale_price_inr=_money(d.get("sale_price") or d.get("sale_price_inr")),
                        contact_name=d.get("owner_broker_name") or d.get("owner_name") or d.get("broker_name"),
                        contact_phone=d.get("contact_number") or d.get("owner_phone") or d.get("broker_phone"),
                        verification_status=d.get("verification_status") or d.get("verification") or "UNVERIFIED",
                        capture_date=d.get("created_at") or d.get("updated_at"),raw_text=desc
                    )
                _upsert(c,row)
                count+=1

        return {"status":"OK","source":src,"source_table":table,"count":count}

    @router.get("/api/v381/entities")
    def entities(req:Request,source:str="ALL",q:str="",limit:int=Query(1000,ge=1,le=5000)):
        need_login(req)
        wh=["active=TRUE"]
        p={"lim":limit}
        if source.upper()!="ALL":
            wh.append("source_type=:s")
            p["s"]=source.upper()
        if q.strip():
            wh.append("(COALESCE(property_name,'') ILIKE :q OR COALESCE(description,'') ILIKE :q OR COALESCE(location,'') ILIKE :q OR COALESCE(contact_name,'') ILIKE :q OR COALESCE(contact_phone,'') ILIKE :q)")
            p["q"]="%"+q.strip()+"%"

        with engine.connect() as c:
            rows=c.execute(text(
                "SELECT * FROM ai_clean_property_entity WHERE "+" AND ".join(wh)+
                " ORDER BY capture_date DESC NULLS LAST,id DESC LIMIT :lim"
            ),p).mappings().all()

        out=[]
        for r in rows:
            d=dict(r)
            for k,v in list(d.items()):
                if hasattr(v,"isoformat"): d[k]=v.isoformat()
            out.append(d)
        return {"version":VERSION,"count":len(out),"rows":out}

    @router.post("/api/v381/entity/{entity_code}")
    def edit_entity(entity_code:str,req:Request,payload:dict=Body(...)):
        need_login(req)
        allowed={"property_name","description","location","property_type","transaction_type","area_sqft","rent_inr","sale_price_inr","contact_name","contact_phone","verification_status"}
        vals={k:v for k,v in payload.items() if k in allowed}
        if not vals: return {"status":"NO_CHANGES"}

        sets=[]
        p={"e":entity_code}
        for i,(k,v) in enumerate(vals.items()):
            key=f"v{i}"
            sets.append(f"{k}=:{key}")
            p[key]=v

        with engine.begin() as c:
            c.execute(text(
                "UPDATE ai_clean_property_entity SET "+",".join(sets)+
                ",updated_at=NOW() WHERE entity_code=:e"
            ),p)

        return {"status":"UPDATED","entity_code":entity_code}

    @router.delete("/api/v381/entity/{entity_code}")
    def delete_entity(entity_code:str,req:Request):
        need_login(req)
        with engine.begin() as c:
            c.execute(text(
                "UPDATE ai_clean_property_entity SET active=FALSE,updated_at=NOW() WHERE entity_code=:e"
            ),{"e":entity_code})
        return {"status":"DELETED","entity_code":entity_code,"mode":"SOFT_DELETE"}

    @router.get("/v381/property-databases",response_class=HTMLResponse)
    def page(req:Request):
        if not page_role_or_redirect(req):
            return RedirectResponse("/login",303)
        html=Path(__file__).with_name("v381_page.html").read_text(encoding="utf-8")
        return HTMLResponse(html)

    app.include_router(router)
    return router
