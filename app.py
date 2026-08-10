
import os, io, csv, json, uuid, hmac, hashlib, base64, tempfile
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, Literal

from fastapi import FastAPI, Request, UploadFile, File, Query, BackgroundTasks, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from google import genai
from google.genai import types

VERSION="5.0.0"
DATABASE_URL=os.getenv("DATABASE_URL","")
GEMINI_API_KEY=os.getenv("GEMINI_API_KEY","")
GEMINI_MODEL=os.getenv("GEMINI_MODEL","gemini-3.1-flash-lite")
MAX_UPLOAD_MB=int(os.getenv("MAX_UPLOAD_MB","25"))
ADMIN_CODE=os.getenv("ADMIN_CODE","admin-change-me")
TEAM_CODE=os.getenv("TEAM_CODE","team-change-me")
SESSION_SECRET=os.getenv("SESSION_SECRET","change-this-secret")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL=DATABASE_URL.replace("postgres://","postgresql+psycopg://",1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL=DATABASE_URL.replace("postgresql://","postgresql+psycopg://",1)

engine=create_engine(DATABASE_URL,pool_pre_ping=True,pool_recycle=300)
client=genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
app=FastAPI(title="Property Intelligence Unified Workspace",version=VERSION)

SCHEMA='''
CREATE TABLE IF NOT EXISTS pi_properties(
 id BIGSERIAL PRIMARY KEY, property_id VARCHAR(50) UNIQUE NOT NULL,
 fingerprint VARCHAR(64), property_name VARCHAR(255), entry_status VARCHAR(50) DEFAULT 'Active',
 availability_status VARCHAR(50) DEFAULT 'Available', property_type VARCHAR(100) DEFAULT 'NA',
 city VARCHAR(100) DEFAULT 'NA', location VARCHAR(255) DEFAULT 'NA',
 available_area_sqft NUMERIC(14,2), minimum_area_sqft NUMERIC(14,2),
 maximum_area_sqft NUMERIC(14,2), floor VARCHAR(100), rent_or_sale VARCHAR(30),
 possession VARCHAR(100), nearby_brands TEXT, suitable_category TEXT, parking TEXT,
 google_maps_pin TEXT, owner_name VARCHAR(255), owner_contact VARCHAR(100),
 broker_name VARCHAR(255), broker_contact VARCHAR(100), remarks TEXT,
 verification_status VARCHAR(50) DEFAULT 'UNVERIFIED', verified_by VARCHAR(255),
 verified_date DATE, source VARCHAR(255), source_id BIGINT, extraction_confidence NUMERIC(5,2),
 created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS pi_requirements(
 id BIGSERIAL PRIMARY KEY, requirement_id VARCHAR(50) UNIQUE NOT NULL,
 fingerprint VARCHAR(64), client_name VARCHAR(255), company_name VARCHAR(255),
 contact_phone VARCHAR(100), contact_email VARCHAR(255), requirement_type VARCHAR(100),
 property_type VARCHAR(100), city VARCHAR(100), preferred_locations TEXT,
 minimum_area_sqft NUMERIC(14,2), maximum_area_sqft NUMERIC(14,2), rent_or_sale VARCHAR(30),
 nearby_brands TEXT, suitable_category TEXT, additional_points TEXT, source VARCHAR(255),
 source_id BIGINT, status VARCHAR(50) DEFAULT 'New', extraction_confidence NUMERIC(5,2),
 created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS pi_sources(
 id BIGSERIAL PRIMARY KEY, source_type VARCHAR(50) NOT NULL, source_name VARCHAR(255),
 source_reference TEXT, original_filename VARCHAR(500), mime_type VARCHAR(150),
 ingestion_status VARCHAR(50) DEFAULT 'RECEIVED', extracted_record_type VARCHAR(50),
 processed_records INTEGER DEFAULT 0, duplicate_records INTEGER DEFAULT 0,
 error_message TEXT, ai_provider VARCHAR(50), ai_model VARCHAR(100),
 uploaded_at TIMESTAMPTZ DEFAULT NOW(), processed_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS pi_matches(
 id BIGSERIAL PRIMARY KEY, requirement_id VARCHAR(50) NOT NULL, property_id VARCHAR(50) NOT NULL,
 match_score NUMERIC(5,2) DEFAULT 0, rank INTEGER, match_reasons JSONB DEFAULT '[]'::jsonb,
 status VARCHAR(50) DEFAULT 'READY_FOR_REVIEW', created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS pi_verification_log(
 id BIGSERIAL PRIMARY KEY, property_id VARCHAR(50), requirement_id VARCHAR(50),
 action VARCHAR(100) NOT NULL, performed_by VARCHAR(255), notes TEXT,
 created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS pi_ai_jobs(
 id BIGSERIAL PRIMARY KEY, source_id BIGINT, job_type VARCHAR(50) NOT NULL,
 status VARCHAR(50) DEFAULT 'PENDING', provider VARCHAR(50), model VARCHAR(100),
 input_summary TEXT, output_summary TEXT, error_message TEXT,
 started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT NOW()
);
'''

MIGRATIONS=[
"ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS fingerprint VARCHAR(64)",
"ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS source_id BIGINT",
"ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS extraction_confidence NUMERIC(5,2)",
"ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS verification_status VARCHAR(50) DEFAULT 'UNVERIFIED'",
"ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW()",
"ALTER TABLE pi_requirements ADD COLUMN IF NOT EXISTS fingerprint VARCHAR(64)",
"ALTER TABLE pi_requirements ADD COLUMN IF NOT EXISTS source_id BIGINT",
"ALTER TABLE pi_requirements ADD COLUMN IF NOT EXISTS extraction_confidence NUMERIC(5,2)",
"ALTER TABLE pi_requirements ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW()",
"ALTER TABLE pi_sources ADD COLUMN IF NOT EXISTS mime_type VARCHAR(150)",
"ALTER TABLE pi_sources ADD COLUMN IF NOT EXISTS extracted_record_type VARCHAR(50)",
"ALTER TABLE pi_sources ADD COLUMN IF NOT EXISTS duplicate_records INTEGER DEFAULT 0",
"ALTER TABLE pi_sources ADD COLUMN IF NOT EXISTS ai_provider VARCHAR(50)"
]

@app.on_event("startup")
def startup():
    with engine.begin() as c:
        for stmt in [x.strip() for x in SCHEMA.split(";") if x.strip()]:
            c.execute(text(stmt))
        for stmt in MIGRATIONS:
            c.execute(text(stmt))

def signed(role):
    sig=hmac.new(SESSION_SECRET.encode(),role.encode(),hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode((role+"|"+sig).encode()).decode()

def get_role(req):
    token=req.cookies.get("pi_session")
    if not token:return None
    try:
        raw=base64.urlsafe_b64decode(token).decode()
        role,sig=raw.rsplit("|",1)
        expected=hmac.new(SESSION_SECRET.encode(),role.encode(),hashlib.sha256).hexdigest()
        return role if role in {"team","admin"} and hmac.compare_digest(sig,expected) else None
    except Exception:
        return None

def need_login(req):
    role=get_role(req)
    if not role:raise HTTPException(401,"Login required")
    return role

@app.exception_handler(Exception)
async def all_errors(req,exc):
    return JSONResponse(status_code=500,content={"status":"error","message":str(exc),"path":req.url.path})

class Property(BaseModel):
    property_name:Optional[str]=None
    property_type:str="NA"
    city:str="NA"
    location:str="NA"
    available_area_sqft:Optional[float]=None
    minimum_area_sqft:Optional[float]=None
    maximum_area_sqft:Optional[float]=None
    floor:Optional[str]=None
    rent_or_sale:Optional[str]=None
    nearby_brands:Optional[str]=None
    suitable_category:Optional[str]=None
    parking:Optional[str]=None
    owner_name:Optional[str]=None
    owner_contact:Optional[str]=None
    broker_name:Optional[str]=None
    broker_contact:Optional[str]=None
    remarks:Optional[str]=None
    source:Optional[str]="Manual"
    extraction_confidence:Optional[float]=None

class Requirement(BaseModel):
    client_name:Optional[str]=None
    company_name:Optional[str]=None
    contact_phone:Optional[str]=None
    contact_email:Optional[str]=None
    requirement_type:Optional[str]="Store Opening"
    property_type:Optional[str]="Retail"
    city:Optional[str]=None
    preferred_locations:Optional[str]=None
    minimum_area_sqft:Optional[float]=None
    maximum_area_sqft:Optional[float]=None
    rent_or_sale:Optional[str]=None
    nearby_brands:Optional[str]=None
    suitable_category:Optional[str]=None
    additional_points:Optional[str]=None
    source:Optional[str]="Manual"
    extraction_confidence:Optional[float]=None

class EP(Property):
    record_type:Literal["property"]="property"

class ER(Requirement):
    record_type:Literal["requirement"]="requirement"

class Envelope(BaseModel):
    properties:list[EP]=Field(default_factory=list)
    requirements:list[ER]=Field(default_factory=list)

class TextInput(BaseModel):
    source_type:str="WHATSAPP"
    source_name:Optional[str]=None
    text_content:str=Field(min_length=1)

def make_id(prefix):
    return prefix+"-"+datetime.utcnow().strftime("%Y%m%d")+"-"+uuid.uuid4().hex[:6].upper()

def fingerprint(values):
    return hashlib.sha256("|".join(str(v or "").lower().strip() for v in values).encode()).hexdigest()

def source_row(source_type,name=None,filename=None,mime=None,reference=None):
    sql='INSERT INTO pi_sources(source_type,source_name,original_filename,mime_type,source_reference,ingestion_status) VALUES(:t,:n,:f,:m,:r,\'RECEIVED\') RETURNING id'
    with engine.begin() as c:
        return c.execute(text(sql),{"t":source_type,"n":name,"f":filename,"m":mime,"r":reference}).scalar_one()

def save_property(data,sid=None):
    d=dict(data)
    fp=fingerprint([d.get("city"),d.get("location"),d.get("property_type"),d.get("available_area_sqft"),d.get("floor"),d.get("rent_or_sale")])
    with engine.begin() as c:
        old=c.execute(text("SELECT property_id FROM pi_properties WHERE fingerprint=:f LIMIT 1"),{"f":fp}).first()
        if old:return {"status":"duplicate","property_id":old[0]}
        pid=make_id("PROP")
        p={"pid":pid,"fp":fp,"sid":sid,**{k:d.get(k) for k in Property.model_fields}}
        sql='''INSERT INTO pi_properties(property_id,fingerprint,property_name,property_type,city,location,available_area_sqft,minimum_area_sqft,maximum_area_sqft,floor,rent_or_sale,nearby_brands,suitable_category,parking,owner_name,owner_contact,broker_name,broker_contact,remarks,source,source_id,extraction_confidence)
        VALUES(:pid,:fp,:property_name,:property_type,:city,:location,:available_area_sqft,:minimum_area_sqft,:maximum_area_sqft,:floor,:rent_or_sale,:nearby_brands,:suitable_category,:parking,:owner_name,:owner_contact,:broker_name,:broker_contact,:remarks,:source,:sid,:extraction_confidence)'''
        c.execute(text(sql),p)
        c.execute(text("INSERT INTO pi_verification_log(property_id,action,performed_by,notes) VALUES(:p,'CREATED','SYSTEM','Queued for review')"),{"p":pid})
    return {"status":"created","property_id":pid}

def save_requirement(data,sid=None):
    d=dict(data)
    fp=fingerprint([d.get("company_name") or d.get("client_name"),d.get("city"),d.get("preferred_locations"),d.get("minimum_area_sqft"),d.get("maximum_area_sqft")])
    with engine.begin() as c:
        old=c.execute(text("SELECT requirement_id FROM pi_requirements WHERE fingerprint=:f LIMIT 1"),{"f":fp}).first()
        if old:return {"status":"duplicate","requirement_id":old[0]}
        rid=make_id("REQ")
        p={"rid":rid,"fp":fp,"sid":sid,**{k:d.get(k) for k in Requirement.model_fields}}
        sql='''INSERT INTO pi_requirements(requirement_id,fingerprint,client_name,company_name,contact_phone,contact_email,requirement_type,property_type,city,preferred_locations,minimum_area_sqft,maximum_area_sqft,rent_or_sale,nearby_brands,suitable_category,additional_points,source,source_id,extraction_confidence)
        VALUES(:rid,:fp,:client_name,:company_name,:contact_phone,:contact_email,:requirement_type,:property_type,:city,:preferred_locations,:minimum_area_sqft,:maximum_area_sqft,:rent_or_sale,:nearby_brands,:suitable_category,:additional_points,:source,:sid,:extraction_confidence)'''
        c.execute(text(sql),p)
    return {"status":"created","requirement_id":rid}

PROMPT="Extract all commercial real-estate property inventory and client/retailer requirements. Do not invent facts. Use null for unknown fields. Return all distinct records with extraction_confidence 0-100."

def complete_source(sid,envelope):
    props=[save_property({**p.model_dump(exclude={"record_type"}),"source":"AI_SOURCE_"+str(sid)},sid) for p in envelope.properties]
    reqs=[save_requirement({**r.model_dump(exclude={"record_type"}),"source":"AI_SOURCE_"+str(sid)},sid) for r in envelope.requirements]
    with engine.begin() as c:
        c.execute(text("UPDATE pi_sources SET ingestion_status='PROCESSED',processed_records=:n,duplicate_records=:d,ai_provider='gemini',ai_model=:m,processed_at=NOW() WHERE id=:id"),
                  {"n":len(props)+len(reqs),"d":sum(x["status"]=="duplicate" for x in props+reqs),"m":GEMINI_MODEL,"id":sid})
    return props,reqs

def run_text_job(sid,jid,content):
    try:
        if not client:raise RuntimeError("GEMINI_API_KEY missing")
        resp=client.models.generate_content(model=GEMINI_MODEL,contents=[PROMPT,"\nSOURCE:\n",content],
            config=types.GenerateContentConfig(response_mime_type="application/json",response_schema=Envelope,temperature=0.1))
        env=Envelope.model_validate(resp.parsed) if getattr(resp,"parsed",None) is not None else Envelope.model_validate_json(resp.text)
        p,r=complete_source(sid,env)
        with engine.begin() as c:c.execute(text("UPDATE pi_ai_jobs SET status='COMPLETED',output_summary=:o,completed_at=NOW() WHERE id=:id"),{"o":f"{len(p)} properties, {len(r)} requirements","id":jid})
    except Exception as ex:
        with engine.begin() as c:
            c.execute(text("UPDATE pi_sources SET ingestion_status='FAILED',error_message=:e WHERE id=:id"),{"e":str(ex),"id":sid})
            c.execute(text("UPDATE pi_ai_jobs SET status='FAILED',error_message=:e,completed_at=NOW() WHERE id=:id"),{"e":str(ex),"id":jid})

def run_file_job(sid,jid,path,mime):
    try:
        if not client:raise RuntimeError("GEMINI_API_KEY missing")
        uploaded=client.files.upload(file=path,config={"mime_type":mime})
        resp=client.models.generate_content(model=GEMINI_MODEL,contents=[PROMPT,uploaded],
            config=types.GenerateContentConfig(response_mime_type="application/json",response_schema=Envelope,temperature=0.1))
        env=Envelope.model_validate(resp.parsed) if getattr(resp,"parsed",None) is not None else Envelope.model_validate_json(resp.text)
        p,r=complete_source(sid,env)
        with engine.begin() as c:c.execute(text("UPDATE pi_ai_jobs SET status='COMPLETED',output_summary=:o,completed_at=NOW() WHERE id=:id"),{"o":f"{len(p)} properties, {len(r)} requirements","id":jid})
    except Exception as ex:
        with engine.begin() as c:
            c.execute(text("UPDATE pi_sources SET ingestion_status='FAILED',error_message=:e WHERE id=:id"),{"e":str(ex),"id":sid})
            c.execute(text("UPDATE pi_ai_jobs SET status='FAILED',error_message=:e,completed_at=NOW() WHERE id=:id"),{"e":str(ex),"id":jid})
    finally:
        try:os.unlink(path)
        except:pass

def create_job(sid,kind,summary):
    sql="INSERT INTO pi_ai_jobs(source_id,job_type,status,provider,model,input_summary,started_at) VALUES(:s,:k,'RUNNING','gemini',:m,:x,NOW()) RETURNING id"
    with engine.begin() as c:
        return c.execute(text(sql),{"s":sid,"k":kind,"m":GEMINI_MODEL,"x":summary}).scalar_one()

@app.get("/login",response_class=HTMLResponse)
def login_page():
    html="<html><body style='font-family:Arial;background:#f4f6f8'><form method='post' action='/login' style='max-width:420px;margin:12vh auto;background:white;padding:25px;border-radius:12px'><h2>Property Intelligence</h2><select name='role' style='width:100%;padding:10px;margin:6px 0'><option value='team'>Team</option><option value='admin'>Admin</option></select><input name='code' type='password' placeholder='Access code' style='width:100%;padding:10px;margin:6px 0'><button style='width:100%;padding:10px'>Login</button></form></body></html>"
    return HTMLResponse(html)

@app.post("/login")
def login_post(role:str=Form(...),code:str=Form(...)):
    ok=(role=="team" and hmac.compare_digest(code,TEAM_CODE)) or (role=="admin" and hmac.compare_digest(code,ADMIN_CODE))
    if not ok:return HTMLResponse("Invalid code",401)
    resp=RedirectResponse("/workspace",303)
    resp.set_cookie("pi_session",signed(role),httponly=True,secure=True,samesite="lax",max_age=43200)
    return resp

@app.get("/logout")
def logout():
    resp=RedirectResponse("/login",303);resp.delete_cookie("pi_session");return resp

@app.get("/health")
def health():
    with engine.connect() as c:c.execute(text("SELECT 1"))
    return {"status":"ok","service":"property-intelligence-unified","version":VERSION,"database":"connected","gemini_configured":bool(GEMINI_API_KEY)}

@app.get("/api/status")
def status(req:Request):
    need_login(req)
    with engine.connect() as c:
        tables={"properties":"pi_properties","requirements":"pi_requirements","sources":"pi_sources","matches":"pi_matches","ai_jobs":"pi_ai_jobs"}
        counts={k:c.execute(text("SELECT COUNT(*) FROM "+v)).scalar_one() for k,v in tables.items()}
    return {"status":"ok","role":get_role(req),"records":counts,"gemini_configured":bool(GEMINI_API_KEY)}

@app.post("/api/properties")
def add_property(p:Property,req:Request):
    need_login(req);return save_property(p.model_dump())

@app.post("/api/requirements")
def add_requirement(r:Requirement,req:Request):
    need_login(req);return save_requirement(r.model_dump())

@app.post("/api/ingest/text")
def ingest_text(p:TextInput,bg:BackgroundTasks,req:Request):
    need_login(req)
    sid=source_row(p.source_type,p.source_name,reference=p.text_content)
    jid=create_job(sid,"TEXT_EXTRACTION",p.source_name or p.source_type)
    bg.add_task(run_text_job,sid,jid,p.text_content)
    return {"status":"ACCEPTED","source_id":sid,"job_id":jid}

@app.post("/api/ingest/file")
async def ingest_file(bg:BackgroundTasks,req:Request,file:UploadFile=File(...),source_type:str=Query("DOCUMENT"),source_name:Optional[str]=Query(None)):
    need_login(req)
    data=await file.read()
    if len(data)>MAX_UPLOAD_MB*1024*1024:raise HTTPException(413,"File too large")
    sid=source_row(source_type.upper(),source_name or file.filename,file.filename,file.content_type)
    if (file.filename or "").lower().endswith(".csv"):
        reader=csv.DictReader(io.StringIO(data.decode("utf-8-sig",errors="replace")));n=0
        for row in reader:
            item={"property_name":row.get("Property name") or row.get("Property Name"),"property_type":row.get("Property type") or "NA","city":row.get("City") or "NA","location":row.get("Location") or "NA","available_area_sqft":row.get("Available area") or None,"floor":row.get("Floor"),"rent_or_sale":row.get("Rent/Sale"),"nearby_brands":row.get("Nearby brand"),"suitable_category":row.get("Suitable category"),"parking":row.get("Parking"),"source":"CSV:"+str(file.filename)}
            try:item["available_area_sqft"]=float(str(item["available_area_sqft"]).replace(",","")) if item["available_area_sqft"] else None
            except:item["available_area_sqft"]=None
            if save_property(item,sid)["status"]=="created":n+=1
        with engine.begin() as c:c.execute(text("UPDATE pi_sources SET ingestion_status='PROCESSED',processed_records=:n,processed_at=NOW() WHERE id=:id"),{"n":n,"id":sid})
        return {"status":"PROCESSED","source_id":sid,"inserted":n}
    fd,path=tempfile.mkstemp(suffix=os.path.splitext(file.filename or "")[1] or ".bin");os.close(fd)
    with open(path,"wb") as f:f.write(data)
    jid=create_job(sid,"FILE_EXTRACTION",file.filename or source_type)
    bg.add_task(run_file_job,sid,jid,path,file.content_type or "application/octet-stream")
    return {"status":"ACCEPTED","source_id":sid,"job_id":jid,"message":"Upload received; AI processing continues in background"}

TABLES={"properties":"pi_properties","requirements":"pi_requirements","matches":"pi_matches","sources":"pi_sources","ai_jobs":"pi_ai_jobs","verification":"pi_verification_log"}
PRIVATE={"fingerprint","owner_name","owner_contact","broker_name","broker_contact","remarks","verified_by","verified_date","source"}

@app.get("/api/database/{name}")
def database(name:str,req:Request,limit:int=Query(500,ge=1,le=2000)):
    role=need_login(req)
    if name not in TABLES:raise HTTPException(404,"Unknown table")
    if role!="admin" and name in {"sources","ai_jobs","verification"}:raise HTTPException(403,"Admin only")
    with engine.connect() as c:
        result=c.execute(text("SELECT * FROM "+TABLES[name]+" ORDER BY id DESC LIMIT :n"),{"n":limit})
        rows=[]
        for row in result:
            d={}
            for k,v in dict(row._mapping).items():
                d[k]=v.isoformat() if isinstance(v,(date,datetime)) else float(v) if isinstance(v,Decimal) else v
            rows.append(d)
    if name=="properties" and role!="admin":
        rows=[{k:v for k,v in x.items() if k not in PRIVATE} for x in rows]
    return {"status":"ok","table":name,"count":len(rows),"rows":rows}

@app.post("/api/match/{rid}")
def match(rid:str,req:Request):
    need_login(req)
    with engine.begin() as c:
        q=c.execute(text("SELECT * FROM pi_requirements WHERE requirement_id=:id"),{"id":rid}).first()
        if not q:raise HTTPException(404,"Requirement not found")
        q=dict(q._mapping);props=c.execute(text("SELECT * FROM pi_properties WHERE availability_status='Available'")).fetchall()
        c.execute(text("DELETE FROM pi_matches WHERE requirement_id=:id"),{"id":rid})
        out=[]
        for row in props:
            p=dict(row._mapping);score=0;reasons=[]
            if q.get("city") and str(q["city"]).lower()==str(p.get("city") or "").lower():score+=30;reasons.append("City")
            if q.get("preferred_locations") and str(p.get("location") or "").lower() in str(q["preferred_locations"]).lower():score+=30;reasons.append("Location")
            a=p.get("available_area_sqft");mn=q.get("minimum_area_sqft");mx=q.get("maximum_area_sqft")
            if a is not None and (mn is None or a>=mn) and (mx is None or a<=mx):score+=30;reasons.append("Area")
            if q.get("rent_or_sale") and str(q["rent_or_sale"]).lower()==str(p.get("rent_or_sale") or "").lower():score+=10;reasons.append("Rent/Sale")
            out.append({"property_id":p["property_id"],"score":score,"reasons":reasons})
        out.sort(key=lambda x:x["score"],reverse=True)
        for i,x in enumerate(out,1):
            sql="INSERT INTO pi_matches(requirement_id,property_id,match_score,rank,match_reasons,status) VALUES(:r,:p,:s,:i,CAST(:m AS JSONB),'READY_FOR_REVIEW')"
            c.execute(text(sql),{"r":rid,"p":x["property_id"],"s":x["score"],"i":i,"m":json.dumps(x["reasons"])})
    return {"status":"READY_FOR_REVIEW","matches":out[:50]}

WORKSPACE='''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Property Intelligence</title>
<style>*{box-sizing:border-box}body{font-family:Arial;margin:0;background:#f5f7fb}header{background:#111827;color:white;padding:18px 24px;display:flex;justify-content:space-between}nav{background:white;padding:10px 20px;border-bottom:1px solid #ddd}button{padding:9px 12px;margin:3px;border:0;border-radius:7px;background:#111827;color:white}.wrap{padding:20px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:15px}.card{background:white;padding:16px;border:1px solid #ddd;border-radius:10px}input,select,textarea{width:100%;padding:9px;margin:5px 0}.hidden{display:none}pre{background:#f8fafc;padding:9px;max-height:250px;overflow:auto}table{border-collapse:collapse;width:100%;font-size:12px}th,td{padding:8px;border-bottom:1px solid #eee;text-align:left;white-space:nowrap}.tablebox{overflow:auto;max-height:65vh}</style></head>
<body><header><div><b>Property Intelligence Unified Workspace</b><br><small>Team + Admin on one domain</small></div><div>__ROLE__ | <a href="/logout" style="color:white">Logout</a></div></header>
<nav><button onclick="sec('ops')">Operations</button><button onclick="sec('db')">Database</button><button onclick="sec('status')">Status</button>__ADMINBTN__</nav>
<div class="wrap"><section id="ops"><div class="grid">
<div class="card"><h3>Upload Photo / Magazine / PDF / CSV</h3><input id="sn" placeholder="Source name"><select id="st"><option>MAGAZINE</option><option>NEWSPAPER</option><option>PHOTO</option><option>PDF</option><option>CSV</option></select><input id="fu" type="file"><button onclick="up()">Upload</button><pre id="uo"></pre></div>
<div class="card"><h3>Paste WhatsApp / Email</h3><input id="tn" placeholder="Source name"><textarea id="tc" rows="7"></textarea><button onclick="txt()">Process</button><pre id="to"></pre></div>
<div class="card"><h3>Add Property</h3><input id="pc" placeholder="City"><input id="pl" placeholder="Location"><input id="pa" type="number" placeholder="Available sqft"><select id="px"><option>Rent</option><option>Sale</option></select><button onclick="prop()">Save</button><pre id="po"></pre></div>
<div class="card"><h3>Add Requirement</h3><input id="rc" placeholder="Client"><input id="rci" placeholder="City"><input id="rl" placeholder="Preferred locations"><input id="rmin" type="number" placeholder="Min sqft"><input id="rmax" type="number" placeholder="Max sqft"><select id="rx"><option>Rent</option><option>Sale</option></select><button onclick="req()">Save</button><pre id="ro"></pre></div>
<div class="card"><h3>Run Matcher</h3><input id="rid" placeholder="Requirement ID"><button onclick="mt()">Match</button><pre id="mo"></pre></div>
</div></section>
<section id="db" class="hidden"><div class="card"><h3>Database</h3><div id="tabs"></div><p id="meta"></p><div class="tablebox"><table id="grid"></table></div></div></section>
<section id="status" class="hidden"><div class="card"><button onclick="stat()">Refresh</button><pre id="so"></pre></div></section>
<section id="admin" class="hidden"><div class="card"><h3>Admin</h3><div id="atabs"></div><p id="ameta"></p><div class="tablebox"><table id="agrid"></table></div></div></section></div>
<script>
const ROLE="__ROLELOW__";const e=i=>document.getElementById(i),v=i=>e(i).value,s=(i,d)=>e(i).textContent=JSON.stringify(d,null,2);
async function jf(u,o={}){const r=await fetch(u,o),t=await r.text();let d;try{d=JSON.parse(t)}catch{x={};d={message:t}}if(!r.ok)throw d;return d}
function sec(i){["ops","db","status","admin"].forEach(x=>e(x).classList.add("hidden"));e(i).classList.remove("hidden");if(i=="db")load("properties","grid","meta");if(i=="status")stat()}
async function up(){try{const f=e("fu").files[0];if(!f)return s("uo",{error:"Choose file"});const fd=new FormData();fd.append("file",f);const q=new URLSearchParams({source_type:v("st"),source_name:v("sn")});s("uo",{status:"uploading"});s("uo",await jf("/api/ingest/file?"+q,{method:"POST",body:fd}))}catch(x){s("uo",x)}}
async function txt(){try{s("to",await jf("/api/ingest/text",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({source_type:"WHATSAPP",source_name:v("tn"),text_content:v("tc")})}))}catch(x){s("to",x)}}
async function prop(){try{s("po",await jf("/api/properties",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({city:v("pc"),location:v("pl"),available_area_sqft:Number(v("pa"))||null,rent_or_sale:v("px")})}))}catch(x){s("po",x)}}
async function req(){try{const d=await jf("/api/requirements",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({client_name:v("rc"),city:v("rci"),preferred_locations:v("rl"),minimum_area_sqft:Number(v("rmin"))||null,maximum_area_sqft:Number(v("rmax"))||null,rent_or_sale:v("rx")})});s("ro",d);if(d.requirement_id)e("rid").value=d.requirement_id}catch(x){s("ro",x)}}
async function mt(){try{s("mo",await jf("/api/match/"+encodeURIComponent(v("rid")),{method:"POST"}))}catch(x){s("mo",x)}}
async function stat(){try{s("so",await jf("/api/status"))}catch(x){s("so",x)}}
function render(rows,target){const g=e(target);if(!rows.length){g.innerHTML="<tr><td>No records yet</td></tr>";return}const c=Object.keys(rows[0]);g.innerHTML="<thead><tr>"+c.map(x=>"<th>"+x+"</th>").join("")+"</tr></thead><tbody>"+rows.map(r=>"<tr>"+c.map(x=>"<td>"+String(r[x]??"")+"</td>").join("")+"</tr>").join("")+"</tbody>"}
async function load(n,target="grid",meta="meta"){try{const d=await jf("/api/database/"+n);e(meta).innerText=d.count+" records";render(d.rows,target)}catch(x){e(meta).innerText=x.detail||x.message||"Error"}}
e("tabs").innerHTML=["properties","requirements","matches"].map(x=>'<button onclick="load(\\''+x+'\\')">'+x+"</button>").join("");
if(ROLE=="admin")e("atabs").innerHTML=["sources","ai_jobs","verification"].map(x=>'<button onclick="load(\\''+x+'\\',\\'agrid\\',\\'ameta\\')">'+x+"</button>").join("");
</script></body></html>'''

@app.get("/workspace",response_class=HTMLResponse)
def workspace(req:Request):
    role=need_login(req)
    admin_btn="<button onclick=\"sec('admin')\">Admin</button>" if role=="admin" else ""
    return HTMLResponse(WORKSPACE.replace("__ROLE__",role.upper()).replace("__ROLELOW__",role).replace("__ADMINBTN__",admin_btn))

@app.get("/")
def root(req:Request):
    return RedirectResponse("/workspace" if get_role(req) else "/login")
