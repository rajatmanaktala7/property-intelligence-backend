
import os
import re
import json
import hashlib
import urllib.parse
import urllib.request
from sqlalchemy import text
from fastapi import Request, Body
from fastapi.responses import HTMLResponse

MODULE_VERSION = "3.1.0-HOSPITALITY-PERSISTENT-INTELLIGENCE"

CATEGORIES = {
    "RESTAURANT","CAFE","LOUNGE","CLUB","BANQUET",
    "GUEST_HOUSE","HOTEL","BAR","CLOUD_KITCHEN","OTHER"
}

def _norm(v):
    return re.sub(r"\s+", " ", str(v or "").strip())

def _key(name="", phone="", website="", location=""):
    base = "|".join([
        _norm(name).lower(),
        re.sub(r"\D","",str(phone or ""))[-10:],
        _norm(website).lower(),
        _norm(location).lower(),
    ])
    return hashlib.sha256(base.encode("utf-8")).hexdigest()

def _ensure_schema(engine):
    with engine.begin() as c:
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_hospitality_entity(
          hospitality_id BIGSERIAL PRIMARY KEY,
          canonical_key TEXT NOT NULL UNIQUE,
          business_name TEXT NOT NULL,
          category TEXT NOT NULL DEFAULT 'OTHER',
          location TEXT,
          city TEXT,
          contact_name TEXT,
          contact_phone TEXT,
          whatsapp_phone TEXT,
          email TEXT,
          website TEXT,
          verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
          outreach_status TEXT NOT NULL DEFAULT 'NOT_CONTACTED',
          assigned_to TEXT,
          notes TEXT,
          first_seen_at TIMESTAMPTZ DEFAULT NOW(),
          last_seen_at TIMESTAMPTZ DEFAULT NOW(),
          last_verified_at TIMESTAMPTZ,
          active BOOLEAN NOT NULL DEFAULT TRUE,
          created_at TIMESTAMPTZ DEFAULT NOW(),
          updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """))
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_hospitality_source_history(
          source_history_id BIGSERIAL PRIMARY KEY,
          hospitality_id BIGINT REFERENCES ai_hospitality_entity(hospitality_id),
          source_type TEXT NOT NULL,
          source_name TEXT,
          source_url TEXT,
          source_record_id TEXT,
          evidence_text TEXT,
          raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
          seen_at TIMESTAMPTZ DEFAULT NOW()
        )
        """))
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_hospitality_run_history(
          run_id BIGSERIAL PRIMARY KEY,
          category TEXT,
          query_text TEXT,
          provider TEXT,
          status TEXT NOT NULL DEFAULT 'RUNNING',
          fetched_count INT NOT NULL DEFAULT 0,
          inserted_or_updated INT NOT NULL DEFAULT 0,
          error_message TEXT,
          started_at TIMESTAMPTZ DEFAULT NOW(),
          completed_at TIMESTAMPTZ
        )
        """))
        c.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_ai_hospitality_category
        ON ai_hospitality_entity(category,active)
        """))
        c.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_ai_hospitality_location
        ON ai_hospitality_entity(location)
        """))

def upsert_hospitality(engine, payload, source):
    _ensure_schema(engine)
    name = _norm(payload.get("business_name") or payload.get("name"))
    if not name:
        raise ValueError("business_name is required")

    category = str(payload.get("category") or "OTHER").upper()
    if category not in CATEGORIES:
        category = "OTHER"

    phone = re.sub(r"\D","",str(payload.get("contact_phone") or ""))
    if phone:
        phone = phone[-10:]

    key = _key(
        name,
        phone,
        payload.get("website"),
        payload.get("location"),
    )

    with engine.begin() as c:
        row = c.execute(text("""
          INSERT INTO ai_hospitality_entity(
            canonical_key,business_name,category,location,city,
            contact_name,contact_phone,whatsapp_phone,email,website,
            verification_status,outreach_status,assigned_to,notes,
            first_seen_at,last_seen_at,created_at,updated_at
          )
          VALUES(
            :canonical_key,:business_name,:category,:location,:city,
            :contact_name,:contact_phone,:whatsapp_phone,:email,:website,
            :verification_status,:outreach_status,:assigned_to,:notes,
            NOW(),NOW(),NOW(),NOW()
          )
          ON CONFLICT(canonical_key) DO UPDATE SET
            business_name=COALESCE(NULLIF(EXCLUDED.business_name,''),ai_hospitality_entity.business_name),
            category=CASE WHEN EXCLUDED.category='OTHER' THEN ai_hospitality_entity.category ELSE EXCLUDED.category END,
            location=COALESCE(NULLIF(EXCLUDED.location,''),ai_hospitality_entity.location),
            city=COALESCE(NULLIF(EXCLUDED.city,''),ai_hospitality_entity.city),
            contact_name=COALESCE(NULLIF(EXCLUDED.contact_name,''),ai_hospitality_entity.contact_name),
            contact_phone=COALESCE(NULLIF(EXCLUDED.contact_phone,''),ai_hospitality_entity.contact_phone),
            whatsapp_phone=COALESCE(NULLIF(EXCLUDED.whatsapp_phone,''),ai_hospitality_entity.whatsapp_phone),
            email=COALESCE(NULLIF(EXCLUDED.email,''),ai_hospitality_entity.email),
            website=COALESCE(NULLIF(EXCLUDED.website,''),ai_hospitality_entity.website),
            verification_status=CASE
              WHEN ai_hospitality_entity.verification_status='VERIFIED'
              THEN ai_hospitality_entity.verification_status
              ELSE EXCLUDED.verification_status
            END,
            assigned_to=COALESCE(NULLIF(EXCLUDED.assigned_to,''),ai_hospitality_entity.assigned_to),
            notes=COALESCE(NULLIF(EXCLUDED.notes,''),ai_hospitality_entity.notes),
            last_seen_at=NOW(),
            updated_at=NOW()
          RETURNING hospitality_id
        """), {
            "canonical_key":key,
            "business_name":name,
            "category":category,
            "location":payload.get("location"),
            "city":payload.get("city"),
            "contact_name":payload.get("contact_name"),
            "contact_phone":phone or None,
            "whatsapp_phone":payload.get("whatsapp_phone"),
            "email":payload.get("email"),
            "website":payload.get("website"),
            "verification_status":str(payload.get("verification_status") or "UNVERIFIED").upper(),
            "outreach_status":str(payload.get("outreach_status") or "NOT_CONTACTED").upper(),
            "assigned_to":payload.get("assigned_to"),
            "notes":payload.get("notes"),
        }).mappings().one()

        hid = int(row["hospitality_id"])

        c.execute(text("""
          INSERT INTO ai_hospitality_source_history(
            hospitality_id,source_type,source_name,source_url,
            source_record_id,evidence_text,raw_payload,seen_at
          )
          VALUES(
            :hospitality_id,:source_type,:source_name,:source_url,
            :source_record_id,:evidence_text,CAST(:raw_payload AS jsonb),NOW()
          )
        """), {
            "hospitality_id":hid,
            "source_type":str(source.get("source_type") or "BOT").upper(),
            "source_name":source.get("source_name"),
            "source_url":source.get("source_url"),
            "source_record_id":source.get("source_record_id"),
            "evidence_text":source.get("evidence_text"),
            "raw_payload":json.dumps(source.get("raw_payload") or {}, default=str),
        })

    return hid

def _search_langsearch(query, count=8):
    key = os.getenv("LANGSEARCH_API_KEY","").strip()
    if not key:
        return {"status":"NO_KEY","provider":"LANGSEARCH","results":[]}

    body = json.dumps({
        "query":query,
        "freshness":"noLimit",
        "summary":True,
        "count":max(1,min(int(count),8)),
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.langsearch.com/v1/web-search",
        data=body,
        method="POST",
        headers={
            "Authorization":f"Bearer {key}",
            "Content-Type":"application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=4) as r:
            data=json.loads(r.read().decode("utf-8","replace"))
        vals=data.get("data",{}).get("webPages",{}).get("value",[])
        return {"status":"OK","provider":"LANGSEARCH","results":vals}
    except Exception as exc:
        return {"status":"ERROR","provider":"LANGSEARCH","message":str(exc),"results":[]}

def run_discovery(engine, category, location="Delhi NCR", count=8):
    _ensure_schema(engine)
    category = str(category or "RESTAURANT").upper()
    if category not in CATEGORIES:
        category = "OTHER"

    query = f"{category.replace('_',' ')} {location} contact phone website"
    with engine.begin() as c:
        run_id = c.execute(text("""
          INSERT INTO ai_hospitality_run_history(category,query_text,provider,status)
          VALUES(:category,:query_text,'LANGSEARCH','RUNNING')
          RETURNING run_id
        """),{"category":category,"query_text":query}).scalar_one()

    result=_search_langsearch(query,count)
    saved=0

    if result["status"]=="OK":
        for item in result["results"]:
            title=_norm(item.get("name"))
            url=item.get("url")
            snippet=_norm(item.get("summary") or item.get("snippet"))
            if not title:
                continue

            # Search results are leads, not verified contact records.
            payload={
                "business_name":title,
                "category":category,
                "location":location,
                "verification_status":"UNVERIFIED",
            }
            source={
                "source_type":"WEB_DISCOVERY",
                "source_name":"LANGSEARCH",
                "source_url":url,
                "evidence_text":snippet,
                "raw_payload":item,
            }
            upsert_hospitality(engine,payload,source)
            saved+=1

    with engine.begin() as c:
        c.execute(text("""
          UPDATE ai_hospitality_run_history
          SET status=:status,fetched_count=:fetched,
              inserted_or_updated=:saved,error_message=:error,
              completed_at=NOW()
          WHERE run_id=:run_id
        """),{
            "status":result["status"],
            "fetched":len(result["results"]),
            "saved":saved,
            "error":result.get("message"),
            "run_id":run_id,
        })

    return {
        "version":MODULE_VERSION,
        "run_id":int(run_id),
        "category":category,
        "query":query,
        "provider":result["provider"],
        "provider_status":result["status"],
        "fetched_count":len(result["results"]),
        "saved_permanently":saved,
        "next_step":"VERIFY_CONTACTS" if saved else "NO_RESULTS",
    }

def adopt_legacy_tables(engine):
    """
    Non-destructive adoption:
    finds likely hospitality tables and stores each row as source history.
    Does not delete, update, truncate, or rename legacy tables.
    """
    _ensure_schema(engine)
    with engine.connect() as c:
        tables=[x[0] for x in c.execute(text("""
          SELECT table_name
          FROM information_schema.tables
          WHERE table_schema='public'
            AND table_type='BASE TABLE'
        """)).all()]

    candidates=[
        t for t in tables
        if any(k in t.lower() for k in ["hospital","restaurant","banquet","cafe","lounge","club","guest","hotel"])
        and not t.startswith("ai_hospitality_")
    ]

    adopted=0
    details=[]

    for table in candidates:
        try:
            with engine.connect() as c:
                rows=c.execute(text(f'SELECT * FROM "{table}" LIMIT 5000')).mappings().all()
            table_count=0
            for idx,row in enumerate(rows,1):
                d=dict(row)
                name = next((d.get(k) for k in [
                    "business_name","brand_name","restaurant_name","hotel_name","name","company_name"
                ] if d.get(k)), None)
                if not name:
                    continue
                phone = next((d.get(k) for k in [
                    "contact_phone","phone","contact_no","contact_number","mobile","whatsapp"
                ] if d.get(k)), None)
                location = next((d.get(k) for k in [
                    "location","address","area","city"
                ] if d.get(k)), None)
                category="OTHER"
                low=table.lower()+" "+str(name).lower()
                for cat,kw in [
                    ("RESTAURANT","restaurant"),("CAFE","cafe"),("LOUNGE","lounge"),
                    ("CLUB","club"),("BANQUET","banquet"),("GUEST_HOUSE","guest"),
                    ("HOTEL","hotel")
                ]:
                    if kw in low:
                        category=cat; break

                upsert_hospitality(engine,{
                    "business_name":str(name),
                    "category":category,
                    "location":location,
                    "contact_phone":phone,
                    "email":d.get("email"),
                    "website":d.get("website"),
                    "verification_status":"UNVERIFIED",
                },{
                    "source_type":"LEGACY_TABLE",
                    "source_name":table,
                    "source_record_id":str(d.get("id") or idx),
                    "raw_payload":d,
                })
                adopted+=1
                table_count+=1
            details.append({"table":table,"rows_adopted":table_count})
        except Exception as exc:
            details.append({"table":table,"error":str(exc)})

    return {
        "version":MODULE_VERSION,
        "legacy_tables_found":len(candidates),
        "rows_adopted_or_merged":adopted,
        "details":details,
        "legacy_data_modified":False,
    }

def register_v31_hospitality_routes(core):
    app,engine=core.app,core.engine

    @app.get("/api/v3/hospitality/status")
    def status(req:Request):
        if hasattr(core,"need_login"):
            core.need_login(req)
        _ensure_schema(engine)
        with engine.connect() as c:
            entity_count=c.execute(text("SELECT COUNT(*) FROM ai_hospitality_entity")).scalar() or 0
            source_count=c.execute(text("SELECT COUNT(*) FROM ai_hospitality_source_history")).scalar() or 0
            verified=c.execute(text("SELECT COUNT(*) FROM ai_hospitality_entity WHERE verification_status='VERIFIED'")).scalar() or 0
        return {
            "version":MODULE_VERSION,
            "status":"OK",
            "entities":int(entity_count),
            "source_history_rows":int(source_count),
            "verified":int(verified),
            "persistent_storage":True,
            "destructive_operations":False,
        }

    @app.post("/api/v3/hospitality/ingest")
    def ingest(req:Request,payload:dict=Body(...)):
        if hasattr(core,"need_login"):
            core.need_login(req)
        try:
            hid=upsert_hospitality(
                engine,
                payload,
                payload.get("source") or {"source_type":"MANUAL_OR_BOT"},
            )
            return {"version":MODULE_VERSION,"status":"OK","hospitality_id":hid,"saved_permanently":True}
        except Exception as exc:
            return {"version":MODULE_VERSION,"status":"ERROR","message":str(exc)}

    @app.post("/api/v3/hospitality/discover/{category}")
    def discover(category:str,req:Request,location:str="Delhi NCR",count:int=8):
        if hasattr(core,"need_login"):
            core.need_login(req)
        return run_discovery(engine,category,location,count)

    @app.post("/api/v3/hospitality/adopt-legacy")
    def adopt(req:Request):
        if hasattr(core,"need_login"):
            core.need_login(req)
        return adopt_legacy_tables(engine)

    @app.get("/api/v3/hospitality/entities")
    def entities(req:Request,category:str="",search:str="",limit:int=100):
        if hasattr(core,"need_login"):
            core.need_login(req)
        _ensure_schema(engine)
        clauses=["active=TRUE"]
        params={"lim":max(1,min(int(limit or 100),500))}
        if category:
            clauses.append("category=:category")
            params["category"]=category.upper()
        if search:
            clauses.append("(LOWER(business_name) LIKE :q OR LOWER(COALESCE(location,'')) LIKE :q)")
            params["q"]=f"%{search.lower()}%"
        with engine.connect() as c:
            rows=c.execute(text(f"""
              SELECT hospitality_id,business_name,category,location,city,
                     contact_name,contact_phone,whatsapp_phone,email,website,
                     verification_status,outreach_status,assigned_to,
                     first_seen_at,last_seen_at,last_verified_at
              FROM ai_hospitality_entity
              WHERE {" AND ".join(clauses)}
              ORDER BY last_seen_at DESC,hospitality_id DESC
              LIMIT :lim
            """),params).mappings().all()
        return {"version":MODULE_VERSION,"count":len(rows),"entities":[dict(x) for x in rows]}

    @app.post("/api/v3/hospitality/entities/{hospitality_id}/verify")
    def verify(hospitality_id:int,req:Request,payload:dict=Body(default={})):
        if hasattr(core,"need_login"):
            core.need_login(req)
        status=str(payload.get("verification_status") or "VERIFIED").upper()
        with engine.begin() as c:
            row=c.execute(text("""
              UPDATE ai_hospitality_entity
              SET verification_status=:status,
                  contact_name=COALESCE(:contact_name,contact_name),
                  contact_phone=COALESCE(:contact_phone,contact_phone),
                  email=COALESCE(:email,email),
                  website=COALESCE(:website,website),
                  last_verified_at=CASE WHEN :status='VERIFIED' THEN NOW() ELSE last_verified_at END,
                  updated_at=NOW()
              WHERE hospitality_id=:id
              RETURNING hospitality_id,business_name,verification_status,last_verified_at
            """),{
                "status":status,
                "contact_name":payload.get("contact_name"),
                "contact_phone":payload.get("contact_phone"),
                "email":payload.get("email"),
                "website":payload.get("website"),
                "id":hospitality_id,
            }).mappings().first()
        return {"version":MODULE_VERSION,"entity":dict(row) if row else None}

    @app.get("/api/v3/hospitality/runs")
    def runs(req:Request,limit:int=50):
        if hasattr(core,"need_login"):
            core.need_login(req)
        _ensure_schema(engine)
        with engine.connect() as c:
            rows=c.execute(text("""
              SELECT *
              FROM ai_hospitality_run_history
              ORDER BY run_id DESC
              LIMIT :lim
            """),{"lim":max(1,min(int(limit or 50),100))}).mappings().all()
        return {"version":MODULE_VERSION,"count":len(rows),"runs":[dict(x) for x in rows]}

    @app.get("/v3/hospitality-intelligence",response_class=HTMLResponse)
    def dashboard(req:Request):
        if hasattr(core,"need_login"):
            core.need_login(req)
        return HTMLResponse("""<!doctype html>
<html><head><meta charset="utf-8"><title>Hospitality Intelligence</title>
<style>
body{font-family:Arial;background:#f5f7fa;margin:0}.wrap{max-width:1100px;margin:32px auto;padding:0 20px}
.card{background:#fff;border-radius:16px;padding:24px;margin-bottom:18px;box-shadow:0 1px 8px rgba(0,0,0,.06)}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.k{background:#f8fafc;border-radius:12px;padding:16px}
</style></head>
<body><div class="wrap">
<div class="card"><h1>V3.1 Hospitality Persistent Intelligence</h1>
<p>Restaurants · Cafes · Lounges · Clubs · Banquets · Guest Houses · Hotels</p>
<p><b>All fetched data is stored permanently with source history.</b></p></div>
<div class="grid">
<div class="k"><b>Discover</b><br>Bot/web discovery</div>
<div class="k"><b>Persist</b><br>Permanent database</div>
<div class="k"><b>Verify</b><br>Contact verification</div>
<div class="k"><b>History</b><br>Sources retained</div>
</div>
<div class="card">
<p>GET <code>/api/v3/hospitality/status</code></p>
<p>POST <code>/api/v3/hospitality/adopt-legacy</code></p>
<p>POST <code>/api/v3/hospitality/discover/RESTAURANT</code></p>
<p>GET <code>/api/v3/hospitality/entities</code></p>
</div></div></body></html>""")

    return app
