
from __future__ import annotations
import re, json, hashlib
from fastapi import Request, Query
from sqlalchemy import text

MODULE_VERSION="3.3.0-CONTACT-VAULT-SOURCE-SEGREGATION"

HOSPITALITY_BUCKETS={"CAFE","LOUNGE","RESTAURANT","BANQUET","CLUB","GUEST_HOUSE","HOTEL","BAR","CLOUD_KITCHEN"}
SOURCE_BUCKETS={
 "WHATSAPP_GROUP":"WHATSAPP_GROUP",
 "WHATSAPP":"WHATSAPP_GROUP",
 "MAGAZINE":"MAGAZINE",
 "NEWSPAPER":"NEWSPAPER",
 "HOSPITALITY_BOT":"HOSPITALITY_BOT",
 "WEB_DISCOVERY":"HOSPITALITY_BOT",
 "LEGACY_TABLE":"LEGACY_DATABASE",
 "MANUAL":"MANUAL",
 "RETAIL_EXPANSION":"RETAIL_EXPANSION",
}
def _n(v): return re.sub(r"\s+"," ",str(v or "")).strip()
def _digits(v):
    d=re.sub(r"\D","",str(v or ""))
    if len(d)>10 and d.startswith("91"): d=d[-10:]
    return d[-10:] if len(d)>=10 else d
def _bucket(source_type,category):
    s=_n(source_type).upper().replace(" ","_")
    c=_n(category).upper()
    if s in SOURCE_BUCKETS: return SOURCE_BUCKETS[s]
    if "WHATSAPP" in s: return "WHATSAPP_GROUP"
    if "MAGAZINE" in s: return "MAGAZINE"
    if "NEWSPAPER" in s: return "NEWSPAPER"
    if c in HOSPITALITY_BUCKETS: return "HOSPITALITY_BOT"
    return "OTHER_DATABASE"
def _contact_key(phone,email,business,category):
    raw="|".join([_digits(phone),_n(email).lower(),_n(business).lower(),_n(category).upper()])
    return hashlib.sha256(raw.encode()).hexdigest()
def _ready(engine):
    try:
        with engine.connect() as c:
            return bool(c.execute(text("SELECT to_regclass('public.ai_marketing_contact_vault') IS NOT NULL")).scalar())
    except Exception:return False
def ensure_schema(engine):
    if _ready(engine): return {"status":"READY","created":False}
    with engine.begin() as c:
        c.execute(text("SET LOCAL lock_timeout='2s'"))
        c.execute(text("SET LOCAL statement_timeout='6s'"))
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_marketing_contact_vault(
          contact_id BIGSERIAL PRIMARY KEY,
          contact_key TEXT UNIQUE NOT NULL,
          source_bucket TEXT NOT NULL,
          source_type TEXT,
          source_name TEXT,
          source_record_id TEXT,
          source_url TEXT,
          business_name TEXT,
          category TEXT,
          contact_name TEXT,
          role_title TEXT,
          phone TEXT,
          whatsapp_phone TEXT,
          email TEXT,
          website TEXT,
          location TEXT,
          city TEXT,
          verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
          whatsapp_status TEXT NOT NULL DEFAULT 'NOT_VERIFIED',
          marketing_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
          consent_basis TEXT,
          dnd BOOLEAN NOT NULL DEFAULT FALSE,
          evidence_text TEXT,
          first_seen_at TIMESTAMPTZ DEFAULT NOW(),
          last_seen_at TIMESTAMPTZ DEFAULT NOW(),
          updated_at TIMESTAMPTZ DEFAULT NOW()
        )"""))
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_marketing_contact_source_history(
          history_id BIGSERIAL PRIMARY KEY,
          contact_id BIGINT REFERENCES ai_marketing_contact_vault(contact_id),
          source_bucket TEXT NOT NULL, source_type TEXT, source_name TEXT,
          source_record_id TEXT, source_url TEXT, evidence_text TEXT,
          seen_at TIMESTAMPTZ DEFAULT NOW()
        )"""))
    return {"status":"READY","created":True}

def upsert(engine,p,src):
    phone=_digits(p.get("phone") or p.get("contact_phone") or p.get("whatsapp_phone"))
    email=_n(p.get("email"))
    business=_n(p.get("business_name") or p.get("company_name") or p.get("brand_name"))
    category=_n(p.get("category")).upper() or "OTHER"
    if not phone and not email: return None
    bucket=_bucket(src.get("source_type"),category)
    key=_contact_key(phone,email,business,category)
    with engine.begin() as c:
        cid=c.execute(text("""
        INSERT INTO ai_marketing_contact_vault(
          contact_key,source_bucket,source_type,source_name,source_record_id,source_url,
          business_name,category,contact_name,role_title,phone,whatsapp_phone,email,
          website,location,city,verification_status,whatsapp_status,marketing_status,
          consent_basis,dnd,evidence_text,last_seen_at,updated_at)
        VALUES(:k,:b,:st,:sn,:sr,:su,:bn,:cat,:cn,:role,:ph,:wa,:em,:web,:loc,:city,
          :vs,:ws,:ms,:cb,:dnd,:ev,NOW(),NOW())
        ON CONFLICT(contact_key) DO UPDATE SET
          last_seen_at=NOW(),updated_at=NOW(),
          verification_status=CASE WHEN ai_marketing_contact_vault.verification_status='VERIFIED'
             THEN 'VERIFIED' ELSE EXCLUDED.verification_status END,
          source_bucket=EXCLUDED.source_bucket,
          source_type=COALESCE(EXCLUDED.source_type,ai_marketing_contact_vault.source_type),
          source_name=COALESCE(EXCLUDED.source_name,ai_marketing_contact_vault.source_name),
          source_url=COALESCE(EXCLUDED.source_url,ai_marketing_contact_vault.source_url),
          contact_name=COALESCE(NULLIF(EXCLUDED.contact_name,''),ai_marketing_contact_vault.contact_name),
          role_title=COALESCE(NULLIF(EXCLUDED.role_title,''),ai_marketing_contact_vault.role_title),
          whatsapp_phone=COALESCE(NULLIF(EXCLUDED.whatsapp_phone,''),ai_marketing_contact_vault.whatsapp_phone),
          email=COALESCE(NULLIF(EXCLUDED.email,''),ai_marketing_contact_vault.email),
          website=COALESCE(NULLIF(EXCLUDED.website,''),ai_marketing_contact_vault.website),
          evidence_text=COALESCE(NULLIF(EXCLUDED.evidence_text,''),ai_marketing_contact_vault.evidence_text)
        RETURNING contact_id
        """),{"k":key,"b":bucket,"st":src.get("source_type"),"sn":src.get("source_name"),
        "sr":src.get("source_record_id"),"su":src.get("source_url"),"bn":business,"cat":category,
        "cn":p.get("contact_name"),"role":p.get("role_title"),"ph":phone or None,
        "wa":_digits(p.get("whatsapp_phone")) or None,"em":email or None,"web":p.get("website"),
        "loc":p.get("location"),"city":p.get("city"),
        "vs":_n(p.get("verification_status")).upper() or "UNVERIFIED",
        "ws":_n(p.get("whatsapp_status")).upper() or "NOT_VERIFIED",
        "ms":_n(p.get("marketing_status")).upper() or "REVIEW_REQUIRED",
        "cb":p.get("consent_basis"),"dnd":bool(p.get("dnd",False)),
        "ev":src.get("evidence_text")}).scalar_one()
        c.execute(text("""INSERT INTO ai_marketing_contact_source_history(
          contact_id,source_bucket,source_type,source_name,source_record_id,source_url,evidence_text)
          VALUES(:id,:b,:st,:sn,:sr,:su,:ev)"""),
          {"id":cid,"b":bucket,"st":src.get("source_type"),"sn":src.get("source_name"),
           "sr":src.get("source_record_id"),"su":src.get("source_url"),"ev":src.get("evidence_text")})
    return int(cid)

def adopt_hospitality(engine,limit=10000):
    ensure_schema(engine); saved=0
    with engine.connect() as c:
        rows=c.execute(text("""SELECT e.*,h.source_type,h.source_name,h.source_url,h.source_record_id,h.evidence_text
        FROM ai_hospitality_entity e
        LEFT JOIN LATERAL (
          SELECT * FROM ai_hospitality_source_history x WHERE x.hospitality_id=e.hospitality_id
          ORDER BY x.seen_at DESC LIMIT 1
        ) h ON TRUE LIMIT :lim"""),{"lim":limit}).mappings().all()
    for r in rows:
        if upsert(engine,{"business_name":r["business_name"],"category":r["category"],
          "contact_name":r["contact_name"],"contact_phone":r["contact_phone"],
          "whatsapp_phone":r["whatsapp_phone"],"email":r["email"],"website":r["website"],
          "location":r["location"],"city":r["city"],"verification_status":r["verification_status"]},
          {"source_type":r["source_type"] or "HOSPITALITY_BOT","source_name":r["source_name"],
           "source_url":r["source_url"],"source_record_id":r["source_record_id"],
           "evidence_text":r["evidence_text"]}): saved+=1
    return saved

def adopt_source_tables(engine,limit_each=10000):
    ensure_schema(engine)
    with engine.connect() as c:
        tables=[x[0] for x in c.execute(text("""SELECT table_name FROM information_schema.tables
        WHERE table_schema='public' AND table_type='BASE TABLE'""")).all()]
    targets=[t for t in tables if any(k in t.lower() for k in
      ["whatsapp","magazine","newspaper"]) and not t.startswith("ai_marketing_contact_")]
    total=0; details=[]
    for table in targets:
        n=0
        try:
            with engine.connect() as c:
                rows=c.execute(text(f'SELECT * FROM "{table}" LIMIT :lim'),{"lim":limit_each}).mappings().all()
            for i,row in enumerate(rows,1):
                d=dict(row)
                phone=next((d.get(k) for k in ["contact_phone","phone","contact_no","contact_number","mobile","whatsapp","broker_phone","owner_phone"] if d.get(k)),None)
                email=next((d.get(k) for k in ["email","email_id","contact_email"] if d.get(k)),None)
                if not phone and not email: continue
                low=table.lower()
                st="WHATSAPP_GROUP" if "whatsapp" in low else "MAGAZINE" if "magazine" in low else "NEWSPAPER"
                business=next((d.get(k) for k in ["business_name","brand_name","company_name","property_name","name"] if d.get(k)),table)
                category=next((d.get(k) for k in ["category","business_type","property_type"] if d.get(k)),"OTHER")
                cid=upsert(engine,{"business_name":business,"category":category,
                    "contact_name":d.get("contact_name") or d.get("broker_name") or d.get("owner_name"),
                    "contact_phone":phone,"email":email,"location":d.get("location") or d.get("address"),
                    "city":d.get("city"),"verification_status":d.get("verification_status") or "UNVERIFIED"},
                    {"source_type":st,"source_name":table,
                     "source_record_id":str(d.get("id") or d.get("record_id") or i)})
                if cid:n+=1;total+=1
            details.append({"table":table,"contacts_adopted_or_merged":n})
        except Exception as e: details.append({"table":table,"error":str(e)})
    return total,details

def register(core):
    app,engine=core.app,core.engine
    @app.get("/api/v3/contacts/status")
    def status(req:Request):
        if hasattr(core,"need_login"):core.need_login(req)
        return {"version":MODULE_VERSION,"status":"OK","schema_ready":_ready(engine),
          "startup_schema_ddl":False,"persistent_storage":True,"non_destructive":True}
    @app.post("/api/v3/contacts/setup")
    def setup(req:Request):
        if hasattr(core,"need_login"):core.need_login(req)
        return {"version":MODULE_VERSION,**ensure_schema(engine)}
    @app.post("/api/v3/contacts/adopt-existing")
    def adopt(req:Request):
        if hasattr(core,"need_login"):core.need_login(req)
        h=adopt_hospitality(engine)
        s,d=adopt_source_tables(engine)
        return {"version":MODULE_VERSION,"hospitality_contacts":h,"source_contacts":s,
          "total_contacts_processed":h+s,"details":d,"source_data_deleted":False}
    @app.get("/api/v3/contacts")
    def contacts(req:Request,bucket:str=Query("ALL"),category:str=Query("ALL"),limit:int=Query(200,ge=1,le=2000)):
        if hasattr(core,"need_login"):core.need_login(req)
        ensure_schema(engine)
        with engine.connect() as c:
            rows=c.execute(text("""SELECT * FROM ai_marketing_contact_vault
              WHERE (:b='ALL' OR source_bucket=:b) AND (:cat='ALL' OR category=:cat)
              ORDER BY updated_at DESC LIMIT :lim"""),
              {"b":bucket.upper(),"cat":category.upper(),"lim":limit}).mappings().all()
        return {"version":MODULE_VERSION,"bucket":bucket.upper(),"category":category.upper(),
          "count":len(rows),"contacts":[dict(x) for x in rows]}
    @app.get("/api/v3/contacts/whatsapp-ready")
    def wa_ready(req:Request,bucket:str=Query("ALL"),category:str=Query("ALL"),limit:int=Query(500,ge=1,le=2000)):
        if hasattr(core,"need_login"):core.need_login(req)
        ensure_schema(engine)
        with engine.connect() as c:
            rows=c.execute(text("""SELECT * FROM ai_marketing_contact_vault
              WHERE (:b='ALL' OR source_bucket=:b) AND (:cat='ALL' OR category=:cat)
                AND COALESCE(whatsapp_phone,phone) IS NOT NULL
                AND verification_status='VERIFIED'
                AND whatsapp_status='VERIFIED'
                AND marketing_status IN ('APPROVED','OPTED_IN')
                AND dnd=FALSE
              ORDER BY updated_at DESC LIMIT :lim"""),
              {"b":bucket.upper(),"cat":category.upper(),"lim":limit}).mappings().all()
        return {"version":MODULE_VERSION,"marketing_safety":"VERIFIED_AND_APPROVED_ONLY",
          "count":len(rows),"contacts":[dict(x) for x in rows]}
