
import os, re, json, hashlib, urllib.request
from sqlalchemy import text
from fastapi import Request, Body
from fastapi.responses import HTMLResponse

MODULE_VERSION = "3.1B-STARTUP-SAFE-HOSPITALITY-PERSISTENCE"
CATEGORIES = {"RESTAURANT","CAFE","LOUNGE","CLUB","BANQUET","GUEST_HOUSE","HOTEL","BAR","CLOUD_KITCHEN","OTHER"}

def _norm(v):
    return re.sub(r"\s+"," ",str(v or "").strip())

def _key(name="",phone="",website="",location=""):
    raw="|".join([_norm(name).lower(),re.sub(r"\D","",str(phone or ""))[-10:],_norm(website).lower(),_norm(location).lower()])
    return hashlib.sha256(raw.encode()).hexdigest()

def _schema_ready(engine):
    try:
        with engine.connect() as c:
            return bool(c.execute(text("""
              SELECT to_regclass('public.ai_hospitality_entity') IS NOT NULL
                 AND to_regclass('public.ai_hospitality_source_history') IS NOT NULL
                 AND to_regclass('public.ai_hospitality_run_history') IS NOT NULL
            """)).scalar())
    except Exception:
        return False

def ensure_schema_safe(engine):
    if _schema_ready(engine):
        return {"status":"READY","created":False}
    with engine.begin() as c:
        c.execute(text("SET LOCAL lock_timeout='2s'"))
        c.execute(text("SET LOCAL statement_timeout='5s'"))
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_hospitality_entity(
          hospitality_id BIGSERIAL PRIMARY KEY,
          canonical_key TEXT UNIQUE NOT NULL,
          business_name TEXT NOT NULL,
          category TEXT NOT NULL DEFAULT 'OTHER',
          location TEXT, city TEXT, contact_name TEXT, contact_phone TEXT,
          whatsapp_phone TEXT, email TEXT, website TEXT,
          verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
          outreach_status TEXT NOT NULL DEFAULT 'NOT_CONTACTED',
          assigned_to TEXT, notes TEXT,
          first_seen_at TIMESTAMPTZ DEFAULT NOW(),
          last_seen_at TIMESTAMPTZ DEFAULT NOW(),
          last_verified_at TIMESTAMPTZ,
          active BOOLEAN NOT NULL DEFAULT TRUE,
          created_at TIMESTAMPTZ DEFAULT NOW(),
          updated_at TIMESTAMPTZ DEFAULT NOW()
        )"""))
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_hospitality_source_history(
          source_history_id BIGSERIAL PRIMARY KEY,
          hospitality_id BIGINT REFERENCES ai_hospitality_entity(hospitality_id),
          source_type TEXT NOT NULL, source_name TEXT, source_url TEXT,
          source_record_id TEXT, evidence_text TEXT,
          raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
          seen_at TIMESTAMPTZ DEFAULT NOW()
        )"""))
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_hospitality_run_history(
          run_id BIGSERIAL PRIMARY KEY,
          category TEXT, query_text TEXT, provider TEXT,
          status TEXT NOT NULL DEFAULT 'RUNNING',
          fetched_count INT NOT NULL DEFAULT 0,
          inserted_or_updated INT NOT NULL DEFAULT 0,
          error_message TEXT,
          started_at TIMESTAMPTZ DEFAULT NOW(),
          completed_at TIMESTAMPTZ
        )"""))
    return {"status":"READY","created":True}

def upsert_hospitality(engine,payload,source):
    ensure_schema_safe(engine)
    name=_norm(payload.get("business_name") or payload.get("name"))
    if not name: raise ValueError("business_name is required")
    cat=str(payload.get("category") or "OTHER").upper()
    if cat not in CATEGORIES: cat="OTHER"
    phone=re.sub(r"\D","",str(payload.get("contact_phone") or ""))[-10:] or None
    key=_key(name,phone,payload.get("website"),payload.get("location"))
    with engine.begin() as c:
        hid=c.execute(text("""
          INSERT INTO ai_hospitality_entity(
            canonical_key,business_name,category,location,city,contact_name,contact_phone,
            whatsapp_phone,email,website,verification_status,outreach_status,assigned_to,notes,
            first_seen_at,last_seen_at,created_at,updated_at
          ) VALUES(
            :k,:n,:cat,:loc,:city,:cn,:cp,:wp,:email,:web,:vs,:os,:asgn,:notes,
            NOW(),NOW(),NOW(),NOW()
          )
          ON CONFLICT(canonical_key) DO UPDATE SET
            last_seen_at=NOW(),updated_at=NOW(),
            contact_name=COALESCE(EXCLUDED.contact_name,ai_hospitality_entity.contact_name),
            contact_phone=COALESCE(EXCLUDED.contact_phone,ai_hospitality_entity.contact_phone),
            email=COALESCE(EXCLUDED.email,ai_hospitality_entity.email),
            website=COALESCE(EXCLUDED.website,ai_hospitality_entity.website)
          RETURNING hospitality_id
        """),{"k":key,"n":name,"cat":cat,"loc":payload.get("location"),"city":payload.get("city"),
             "cn":payload.get("contact_name"),"cp":phone,"wp":payload.get("whatsapp_phone"),
             "email":payload.get("email"),"web":payload.get("website"),
             "vs":str(payload.get("verification_status") or "UNVERIFIED").upper(),
             "os":str(payload.get("outreach_status") or "NOT_CONTACTED").upper(),
             "asgn":payload.get("assigned_to"),"notes":payload.get("notes")}).scalar_one()
        c.execute(text("""
          INSERT INTO ai_hospitality_source_history(
            hospitality_id,source_type,source_name,source_url,source_record_id,evidence_text,raw_payload,seen_at
          ) VALUES(:hid,:st,:sn,:su,:sr,:ev,CAST(:raw AS jsonb),NOW())
        """),{"hid":int(hid),"st":str(source.get("source_type") or "BOT").upper(),
             "sn":source.get("source_name"),"su":source.get("source_url"),
             "sr":source.get("source_record_id"),"ev":source.get("evidence_text"),
             "raw":json.dumps(source.get("raw_payload") or {},default=str)})
    return int(hid)

def register_v31_hospitality_routes(core):
    app,engine=core.app,core.engine

    @app.get("/api/v3/hospitality/status")
    def status(req:Request):
        if hasattr(core,"need_login"): core.need_login(req)
        ready=_schema_ready(engine)
        if not ready:
            return {"version":MODULE_VERSION,"status":"OK","schema_ready":False,"startup_schema_ddl":False,
                    "persistent_storage":True,"next_step":"POST /api/v3/hospitality/setup"}
        with engine.connect() as c:
            e=int(c.execute(text("SELECT COUNT(*) FROM ai_hospitality_entity")).scalar() or 0)
            s=int(c.execute(text("SELECT COUNT(*) FROM ai_hospitality_source_history")).scalar() or 0)
        return {"version":MODULE_VERSION,"status":"OK","schema_ready":True,"entities":e,
                "source_history_rows":s,"startup_schema_ddl":False,"persistent_storage":True}

    @app.post("/api/v3/hospitality/setup")
    def setup(req:Request):
        if hasattr(core,"need_login"): core.need_login(req)
        try: return {"version":MODULE_VERSION,**ensure_schema_safe(engine)}
        except Exception as exc: return {"version":MODULE_VERSION,"status":"SCHEMA_BUSY","message":str(exc)}

    @app.post("/api/v3/hospitality/ingest")
    def ingest(req:Request,payload:dict=Body(...)):
        if hasattr(core,"need_login"): core.need_login(req)
        try:
            hid=upsert_hospitality(engine,payload,payload.get("source") or {"source_type":"MANUAL_OR_BOT"})
            return {"version":MODULE_VERSION,"status":"OK","hospitality_id":hid,"saved_permanently":True}
        except Exception as exc:
            return {"version":MODULE_VERSION,"status":"ERROR","message":str(exc)}

    @app.get("/api/v3/hospitality/entities")
    def entities(req:Request,limit:int=100):
        if hasattr(core,"need_login"): core.need_login(req)
        if not _schema_ready(engine):
            return {"version":MODULE_VERSION,"schema_ready":False,"count":0,"entities":[]}
        with engine.connect() as c:
            rows=c.execute(text("""
              SELECT hospitality_id,business_name,category,location,city,contact_name,contact_phone,
                     whatsapp_phone,email,website,verification_status,outreach_status,assigned_to,
                     first_seen_at,last_seen_at,last_verified_at
              FROM ai_hospitality_entity
              WHERE active=TRUE
              ORDER BY last_seen_at DESC,hospitality_id DESC
              LIMIT :lim
            """),{"lim":max(1,min(int(limit or 100),500))}).mappings().all()
        return {"version":MODULE_VERSION,"schema_ready":True,"count":len(rows),"entities":[dict(x) for x in rows]}

    @app.get("/v3/hospitality-intelligence",response_class=HTMLResponse)
    def dashboard(req:Request):
        if hasattr(core,"need_login"): core.need_login(req)
        return HTMLResponse("<h1>V3.1B Hospitality Persistent Intelligence</h1><p>Startup-safe. Persistent storage. No startup DDL.</p>")

    return app
