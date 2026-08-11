
import os, io, csv, json, uuid, hmac, hashlib, base64, tempfile
from html import escape
from urllib.parse import quote_plus
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, Literal

from fastapi import FastAPI, Request, UploadFile, File, Query, BackgroundTasks, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from google import genai
from google.genai import types
from PIL import Image
import fitz
from pypdf import PdfReader, PdfWriter

VERSION="7.3.0"
DATABASE_URL=os.getenv("DATABASE_URL","")
GEMINI_API_KEY=os.getenv("GEMINI_API_KEY","")
GEMINI_MODEL=os.getenv("GEMINI_MODEL","gemini-3.1-flash-lite")
MAX_UPLOAD_MB=int(os.getenv("MAX_UPLOAD_MB","100"))
PDF_PAGES_PER_BATCH=int(os.getenv("PDF_PAGES_PER_BATCH","2"))
MAX_PROPERTY_IMAGES=int(os.getenv("MAX_PROPERTY_IMAGES","12"))
MAX_IMAGE_MB=int(os.getenv("MAX_IMAGE_MB","10"))
VERIFICATION_DUE_DAYS=int(os.getenv("VERIFICATION_DUE_DAYS","30"))
SCAN_TILE_COLS=int(os.getenv("SCAN_TILE_COLS","3"))
SCAN_TILE_ROWS=int(os.getenv("SCAN_TILE_ROWS","3"))
SCAN_TILE_OVERLAP=float(os.getenv("SCAN_TILE_OVERLAP","0.12"))
PDF_RENDER_DPI=int(os.getenv("PDF_RENDER_DPI","220"))
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
 image_urls TEXT, video_urls TEXT, brochure_url TEXT,
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
 old_value JSONB, new_value JSONB,
 created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS pi_ai_jobs(
 id BIGSERIAL PRIMARY KEY, source_id BIGINT, job_type VARCHAR(50) NOT NULL,
 status VARCHAR(50) DEFAULT 'PENDING', provider VARCHAR(50), model VARCHAR(100),
 input_summary TEXT, output_summary TEXT, error_message TEXT,
 started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT NOW()

);

CREATE SEQUENCE IF NOT EXISTS pi_property_code_seq START 1;
CREATE SEQUENCE IF NOT EXISTS pi_requirement_code_seq START 1;

CREATE TABLE IF NOT EXISTS pi_extraction_batches(
 id BIGSERIAL PRIMARY KEY,
 source_id BIGINT NOT NULL,
 start_page INTEGER NOT NULL,
 end_page INTEGER NOT NULL,
 status VARCHAR(50) DEFAULT 'PENDING',
 attempts INTEGER DEFAULT 0,
 records_created INTEGER DEFAULT 0,
 duplicates INTEGER DEFAULT 0,
 error_message TEXT,
 updated_at TIMESTAMPTZ DEFAULT NOW(),
 UNIQUE(source_id,start_page,end_page)
);

CREATE TABLE IF NOT EXISTS pi_message_drafts(
 id BIGSERIAL PRIMARY KEY,
 requirement_id TEXT NOT NULL,
 recipient_name TEXT,
 recipient_phone TEXT,
 channel VARCHAR(30) DEFAULT 'WHATSAPP',
 message_text TEXT NOT NULL,
 status VARCHAR(50) DEFAULT 'READY_FOR_REVIEW',
 ai_provider VARCHAR(50),
 ai_model VARCHAR(100),
 created_at TIMESTAMPTZ DEFAULT NOW()
);


CREATE TABLE IF NOT EXISTS pi_property_media(
 id BIGSERIAL PRIMARY KEY,
 media_id UUID UNIQUE NOT NULL,
 property_id TEXT NOT NULL,
 media_type VARCHAR(30) DEFAULT 'IMAGE',
 filename TEXT,
 mime_type TEXT,
 file_size BIGINT,
 content BYTEA NOT NULL,
 created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pi_property_media_property_id ON pi_property_media(property_id);


CREATE TABLE IF NOT EXISTS pi_scan_tiles(
 id BIGSERIAL PRIMARY KEY,
 source_id BIGINT NOT NULL,
 page_number INTEGER,
 tile_label TEXT NOT NULL,
 status VARCHAR(50) DEFAULT 'PENDING',
 attempts INTEGER DEFAULT 0,
 records_created INTEGER DEFAULT 0,
 duplicates INTEGER DEFAULT 0,
 error_message TEXT,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 updated_at TIMESTAMPTZ DEFAULT NOW(),
 UNIQUE(source_id,page_number,tile_label)
);
CREATE INDEX IF NOT EXISTS idx_pi_scan_tiles_source ON pi_scan_tiles(source_id);

'''

MIGRATIONS=[
"ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS fingerprint VARCHAR(64)",
"ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS source_id BIGINT",
"ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS extraction_confidence NUMERIC(5,2)",
"ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS verification_status VARCHAR(50) DEFAULT 'UNVERIFIED'",
"ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW()",
"ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS image_urls TEXT",
"ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS video_urls TEXT",
"ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS brochure_url TEXT",
"ALTER TABLE pi_verification_log ADD COLUMN IF NOT EXISTS old_value JSONB",
"ALTER TABLE pi_verification_log ADD COLUMN IF NOT EXISTS new_value JSONB",
"ALTER TABLE pi_requirements ADD COLUMN IF NOT EXISTS fingerprint VARCHAR(64)",
"ALTER TABLE pi_requirements ADD COLUMN IF NOT EXISTS source_id BIGINT",
"ALTER TABLE pi_requirements ADD COLUMN IF NOT EXISTS extraction_confidence NUMERIC(5,2)",
"ALTER TABLE pi_requirements ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW()",
"ALTER TABLE pi_sources ADD COLUMN IF NOT EXISTS mime_type VARCHAR(150)",
"ALTER TABLE pi_sources ADD COLUMN IF NOT EXISTS extracted_record_type VARCHAR(50)",
"ALTER TABLE pi_sources ADD COLUMN IF NOT EXISTS duplicate_records INTEGER DEFAULT 0",
"ALTER TABLE pi_sources ADD COLUMN IF NOT EXISTS ai_provider VARCHAR(50)",
"ALTER TABLE pi_properties ALTER COLUMN property_name TYPE TEXT",
"ALTER TABLE pi_properties ALTER COLUMN property_type TYPE TEXT",
"ALTER TABLE pi_properties ALTER COLUMN city TYPE TEXT",
"ALTER TABLE pi_properties ALTER COLUMN location TYPE TEXT",
"ALTER TABLE pi_properties ALTER COLUMN floor TYPE TEXT",
"ALTER TABLE pi_properties ALTER COLUMN rent_or_sale TYPE TEXT",
"ALTER TABLE pi_properties ALTER COLUMN possession TYPE TEXT",
"ALTER TABLE pi_properties ALTER COLUMN owner_name TYPE TEXT",
"ALTER TABLE pi_properties ALTER COLUMN owner_contact TYPE TEXT",
"ALTER TABLE pi_properties ALTER COLUMN broker_name TYPE TEXT",
"ALTER TABLE pi_properties ALTER COLUMN broker_contact TYPE TEXT",
"ALTER TABLE pi_properties ALTER COLUMN source TYPE TEXT",
"ALTER TABLE pi_requirements ALTER COLUMN client_name TYPE TEXT",
"ALTER TABLE pi_requirements ALTER COLUMN company_name TYPE TEXT",
"ALTER TABLE pi_requirements ALTER COLUMN contact_phone TYPE TEXT",
"ALTER TABLE pi_requirements ALTER COLUMN contact_email TYPE TEXT",
"ALTER TABLE pi_requirements ALTER COLUMN requirement_type TYPE TEXT",
"ALTER TABLE pi_requirements ALTER COLUMN property_type TYPE TEXT",
"ALTER TABLE pi_requirements ALTER COLUMN city TYPE TEXT",
"ALTER TABLE pi_requirements ALTER COLUMN rent_or_sale TYPE TEXT",
"ALTER TABLE pi_requirements ALTER COLUMN source TYPE TEXT"
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
    if not role:
        raise HTTPException(401,"Login required")
    return role

def actor_name(req):
    return (req.headers.get("x-user-name") or get_role(req) or "unknown").strip()[:255]

def page_role_or_redirect(req):
    role=get_role(req)
    if role:
        return role
    return None

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
    image_urls:Optional[str]=None
    video_urls:Optional[str]=None
    brochure_url:Optional[str]=None
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


class WhatsAppDraftResult(BaseModel):
    message: str


def make_id(prefix,conn=None):
    sequence="pi_property_code_seq" if prefix=="PROP" else "pi_requirement_code_seq"
    def get_value(c):
        return c.execute(text("SELECT nextval(:seq::regclass)"),{"seq":sequence}).scalar_one()
    # PostgreSQL does not allow a bind parameter directly in nextval(regclass) on all drivers,
    # so use a fixed allow-listed sequence name.
    sql=f"SELECT nextval('{sequence}')"
    if conn is not None:
        number=conn.execute(text(sql)).scalar_one()
    else:
        with engine.begin() as c:
            number=c.execute(text(sql)).scalar_one()
    return f"{prefix}-{datetime.utcnow().strftime('%Y%m%d')}-{int(number):010d}"

def fingerprint(values, minimum_identity_fields=3):
    cleaned=[str(v or "").lower().strip() for v in values]
    meaningful=[v for v in cleaned if v and v not in {"na","n/a","none","null","unknown","0"}]
    if len(meaningful) < minimum_identity_fields:
        return hashlib.sha256(("sparse|"+uuid.uuid4().hex).encode()).hexdigest()
    return hashlib.sha256("|".join(cleaned).encode()).hexdigest()

def source_row(source_type,name=None,filename=None,mime=None,reference=None):
    sql='INSERT INTO pi_sources(source_type,source_name,original_filename,mime_type,source_reference,ingestion_status) VALUES(:t,:n,:f,:m,:r,\'RECEIVED\') RETURNING id'
    with engine.begin() as c:
        return c.execute(text(sql),{"t":source_type,"n":name,"f":filename,"m":mime,"r":reference}).scalar_one()

def save_property(data,sid=None):
    d=dict(data)
    fp=fingerprint([d.get("property_name"),d.get("city"),d.get("location"),d.get("property_type"),d.get("available_area_sqft"),d.get("floor"),d.get("rent_or_sale"),d.get("owner_contact"),d.get("broker_contact")])
    with engine.begin() as c:
        old=c.execute(text("SELECT property_id FROM pi_properties WHERE fingerprint=:f LIMIT 1"),{"f":fp}).first()
        if old:return {"status":"duplicate","property_id":old[0]}
        pid=make_id("PROP",c)
        p={"pid":pid,"fp":fp,"sid":sid,**{k:d.get(k) for k in Property.model_fields}}
        sql='''INSERT INTO pi_properties(property_id,fingerprint,property_name,property_type,city,location,available_area_sqft,minimum_area_sqft,maximum_area_sqft,floor,rent_or_sale,nearby_brands,suitable_category,parking,owner_name,owner_contact,broker_name,broker_contact,remarks,image_urls,video_urls,brochure_url,source,source_id,extraction_confidence)
        VALUES(:pid,:fp,:property_name,:property_type,:city,:location,:available_area_sqft,:minimum_area_sqft,:maximum_area_sqft,:floor,:rent_or_sale,:nearby_brands,:suitable_category,:parking,:owner_name,:owner_contact,:broker_name,:broker_contact,:remarks,:image_urls,:video_urls,:brochure_url,:source,:sid,:extraction_confidence)'''
        c.execute(text(sql),p)
        c.execute(text("INSERT INTO pi_verification_log(property_id,action,performed_by,notes) VALUES(:p,'CREATED','SYSTEM','Queued for review')"),{"p":pid})
    return {"status":"created","property_id":pid}

def save_requirement(data,sid=None):
    d=dict(data)
    fp=fingerprint([d.get("company_name") or d.get("client_name"),d.get("contact_phone"),d.get("contact_email"),d.get("city"),d.get("preferred_locations"),d.get("minimum_area_sqft"),d.get("maximum_area_sqft")])
    with engine.begin() as c:
        old=c.execute(text("SELECT requirement_id FROM pi_requirements WHERE fingerprint=:f LIMIT 1"),{"f":fp}).first()
        if old:return {"status":"duplicate","requirement_id":old[0]}
        rid=make_id("REQ",c)
        p={"rid":rid,"fp":fp,"sid":sid,**{k:d.get(k) for k in Requirement.model_fields}}
        sql='''INSERT INTO pi_requirements(requirement_id,fingerprint,client_name,company_name,contact_phone,contact_email,requirement_type,property_type,city,preferred_locations,minimum_area_sqft,maximum_area_sqft,rent_or_sale,nearby_brands,suitable_category,additional_points,source,source_id,extraction_confidence)
        VALUES(:rid,:fp,:client_name,:company_name,:contact_phone,:contact_email,:requirement_type,:property_type,:city,:preferred_locations,:minimum_area_sqft,:maximum_area_sqft,:rent_or_sale,:nearby_brands,:suitable_category,:additional_points,:source,:sid,:extraction_confidence)'''
        c.execute(text(sql),p)
    return {"status":"created","requirement_id":rid}

PROMPT="""You are a high-recall real-estate magazine data extractor.
Extract EVERY distinct property listing and EVERY client/retailer requirement visible in the supplied pages.
Treat each classified advertisement/listing as a separate record.
Never summarize, sample, merge, or return only the best listings.
Do not invent facts. Use null for unknown fields. Preserve visible phone/contact details.
If a page contains 40 listings, return approximately 40 separate records.
Return all records in the required schema with extraction_confidence from 0 to 100.
"""

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


def parse_envelope_response(resp):
    if getattr(resp,"parsed",None) is not None:
        return Envelope.model_validate(resp.parsed)

    raw=(resp.text or "").strip()

    try:
        return Envelope.model_validate_json(raw)
    except Exception as first_error:
        # Gemini may wrap JSON in markdown fences.
        cleaned=raw
        if cleaned.startswith("```json"):
            cleaned=cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned=cleaned[3:]
        if cleaned.endswith("```"):
            cleaned=cleaned[:-3]
        cleaned=cleaned.strip()

        try:
            return Envelope.model_validate_json(cleaned)
        except Exception:
            raise RuntimeError(
                "GEMINI_JSON_TRUNCATED_OR_INVALID: "
                + str(first_error)
                + f" | response_chars={len(raw)}"
            )

def extract_gemini_batch(path,mime,label):
    if not client:
        raise RuntimeError("GEMINI_API_KEY missing")

    uploaded=client.files.upload(
        file=path,
        config={"mime_type":mime}
    )

    resp=client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            PROMPT
            + "\n"
            + label
            + "\nReturn compact JSON. Do not repeat source text. Do not add explanations."
            ,
            uploaded
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=Envelope,
            temperature=0.0,
            max_output_tokens=16384
        )
    )

    return parse_envelope_response(resp)

def split_pdf(path):
    reader=PdfReader(path)
    total=len(reader.pages)
    batches=[]
    for start in range(0,total,PDF_PAGES_PER_BATCH):
        end=min(start+PDF_PAGES_PER_BATCH,total)
        writer=PdfWriter()
        for i in range(start,end): writer.add_page(reader.pages[i])
        fd,bp=tempfile.mkstemp(suffix=".pdf"); os.close(fd)
        with open(bp,"wb") as f: writer.write(f)
        batches.append((bp,start+1,end,total))
    return batches


def write_pdf_range(reader,start_idx,end_idx):
    writer=PdfWriter()
    for i in range(start_idx,end_idx):
        writer.add_page(reader.pages[i])
    fd,temp_path=tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    with open(temp_path,"wb") as f:
        writer.write(f)
    return temp_path


def batch_state(source_id,start_page,end_page):
    with engine.connect() as c:
        row=c.execute(
            text("""SELECT status,attempts,records_created,duplicates,error_message
                    FROM pi_extraction_batches
                    WHERE source_id=:sid AND start_page=:sp AND end_page=:ep"""),
            {"sid":source_id,"sp":start_page,"ep":end_page}
        ).first()
    return dict(row._mapping) if row else None

def mark_batch(source_id,start_page,end_page,status,created=0,duplicates=0,error=None):
    with engine.begin() as c:
        c.execute(
            text("""INSERT INTO pi_extraction_batches(
                        source_id,start_page,end_page,status,attempts,records_created,duplicates,error_message,updated_at
                    ) VALUES(:sid,:sp,:ep,:status,1,:created,:dup,:err,NOW())
                    ON CONFLICT(source_id,start_page,end_page)
                    DO UPDATE SET
                        status=EXCLUDED.status,
                        attempts=pi_extraction_batches.attempts+1,
                        records_created=EXCLUDED.records_created,
                        duplicates=EXCLUDED.duplicates,
                        error_message=EXCLUDED.error_message,
                        updated_at=NOW()"""),
            {"sid":source_id,"sp":start_page,"ep":end_page,"status":status,
             "created":created,"dup":duplicates,"err":error}
        )

def extract_pdf_range_recursive(reader,start_idx,end_idx,total_pages,sid,jid,progress):
    """
    Extract a page range. If Gemini returns truncated/invalid JSON,
    split the range in half and retry automatically.
    """
    start_page=start_idx+1
    end_page=end_idx
    previous=batch_state(sid,start_page,end_page)
    if previous and previous.get("status")=="COMPLETED":
        return {
            "created":int(previous.get("records_created") or 0),
            "duplicates":int(previous.get("duplicates") or 0),
            "property_outputs":0,
            "requirement_outputs":0
        }

    mark_batch(sid,start_page,end_page,"RUNNING")
    temp_path=write_pdf_range(reader,start_idx,end_idx)
    try:
        label=f"PDF pages {start_page}-{end_page} of {total_pages}. Extract every distinct listing."

        try:
            env=extract_gemini_batch(
                temp_path,
                "application/pdf",
                label
            )
        except Exception as exc:
            msg=str(exc)

            # If a multi-page range is too large, split and retry.
            if "GEMINI_JSON_TRUNCATED_OR_INVALID" in msg and (end_idx-start_idx)>1:
                mid=start_idx + (end_idx-start_idx)//2

                left=extract_pdf_range_recursive(
                    reader,start_idx,mid,total_pages,sid,jid,progress
                )
                right=extract_pdf_range_recursive(
                    reader,mid,end_idx,total_pages,sid,jid,progress
                )
                combined_created=left["created"]+right["created"]
                combined_duplicates=left["duplicates"]+right["duplicates"]
                mark_batch(sid,start_page,end_page,"COMPLETED",combined_created,combined_duplicates,None)
                return {
                    "created":combined_created,
                    "duplicates":combined_duplicates,
                    "property_outputs":left["property_outputs"]+right["property_outputs"],
                    "requirement_outputs":left["requirement_outputs"]+right["requirement_outputs"]
                }

            # A single page can still contain a huge classifieds grid.
            # Retry once with an even stricter compact-output instruction.
            if "GEMINI_JSON_TRUNCATED_OR_INVALID" in msg and (end_idx-start_idx)==1:
                strict_label=(
                    label
                    + "\nThis single page contains many ads. "
                    + "Return ONLY the schema fields. "
                    + "Use null for unknowns. "
                    + "Keep remarks concise. "
                    + "Do not copy long advertisement text."
                )
                env=extract_gemini_batch(
                    temp_path,
                    "application/pdf",
                    strict_label
                )
            else:
                raise

        props=[
            save_property(
                {
                    **x.model_dump(exclude={"record_type"}),
                    "source":f"AI_SOURCE_{sid}_PAGES_{start_idx+1}_{end_idx}"
                },
                sid
            )
            for x in env.properties
        ]

        reqs=[
            save_requirement(
                {
                    **x.model_dump(exclude={"record_type"}),
                    "source":f"AI_SOURCE_{sid}_PAGES_{start_idx+1}_{end_idx}"
                },
                sid
            )
            for x in env.requirements
        ]

        created=sum(x["status"]=="created" for x in props+reqs)
        duplicates=sum(x["status"]=="duplicate" for x in props+reqs)

        progress["created"]+=created
        progress["duplicates"]+=duplicates
        progress["ranges_done"]+=1

        with engine.begin() as c:
            c.execute(
                text("""UPDATE pi_sources
                        SET ingestion_status='PROCESSING',
                            processed_records=:n,
                            duplicate_records=:d
                        WHERE id=:id"""),
                {
                    "n":progress["created"],
                    "d":progress["duplicates"],
                    "id":sid
                }
            )

            c.execute(
                text("""UPDATE pi_ai_jobs
                        SET output_summary=:o
                        WHERE id=:id"""),
                {
                    "o":(
                        f"{progress['created']} records stored; "
                        f"processed through page {end_idx} of {total_pages}"
                    ),
                    "id":jid
                }
            )

        mark_batch(sid,start_page,end_page,"COMPLETED",created,duplicates,None)
        return {
            "created":created,
            "duplicates":duplicates,
            "property_outputs":len(props),
            "requirement_outputs":len(reqs)
        }

    except Exception as exc:
        mark_batch(sid,start_page,end_page,"FAILED",0,0,str(exc))
        raise

    finally:
        try:
            os.unlink(temp_path)
        except Exception:
            pass

def scan_tile_state(source_id,page_number,tile_label):
    with engine.connect() as c:
        row=c.execute(text("""SELECT status,attempts,records_created,duplicates,error_message
                              FROM pi_scan_tiles
                              WHERE source_id=:sid AND page_number IS NOT DISTINCT FROM :pg AND tile_label=:tile"""),
                      {"sid":source_id,"pg":page_number,"tile":tile_label}).first()
    return dict(row._mapping) if row else None

def mark_scan_tile(source_id,page_number,tile_label,status,created=0,duplicates=0,error=None):
    with engine.begin() as c:
        c.execute(text("""INSERT INTO pi_scan_tiles(source_id,page_number,tile_label,status,attempts,records_created,duplicates,error_message,updated_at)
                          VALUES(:sid,:pg,:tile,:status,1,:created,:dup,:err,NOW())
                          ON CONFLICT(source_id,page_number,tile_label)
                          DO UPDATE SET status=EXCLUDED.status,attempts=pi_scan_tiles.attempts+1,
                                        records_created=EXCLUDED.records_created,duplicates=EXCLUDED.duplicates,
                                        error_message=EXCLUDED.error_message,updated_at=NOW()"""),
                  {"sid":source_id,"pg":page_number,"tile":tile_label,"status":status,
                   "created":created,"dup":duplicates,"err":error})

def save_scanned_envelope(env,sid,suffix):
    props=[save_property({**x.model_dump(exclude={"record_type"}),"source":f"AI_SOURCE_{sid}_{suffix}"},sid) for x in env.properties]
    reqs=[save_requirement({**x.model_dump(exclude={"record_type"}),"source":f"AI_SOURCE_{sid}_{suffix}"},sid) for x in env.requirements]
    return (sum(x["status"]=="created" for x in props+reqs),
            sum(x["status"]=="duplicate" for x in props+reqs),len(props),len(reqs))

def crop_overlapping_tiles(image_path):
    image=Image.open(image_path).convert("RGB")
    w,h=image.size
    cols=max(1,SCAN_TILE_COLS); rows=max(1,SCAN_TILE_ROWS)
    ov=max(0.0,min(SCAN_TILE_OVERLAP,0.35))
    cw=w/cols; ch=h/rows; out=[]
    for r in range(rows):
        for c in range(cols):
            x0=max(0,int(c*cw-cw*ov)); y0=max(0,int(r*ch-ch*ov))
            x1=min(w,int((c+1)*cw+cw*ov)); y1=min(h,int((r+1)*ch+ch*ov))
            tile=image.crop((x0,y0,x1,y1))
            fd,tp=tempfile.mkstemp(suffix=".jpg"); os.close(fd)
            tile.save(tp,"JPEG",quality=95)
            out.append((f"R{r+1}C{c+1}",tp))
    return out

def scan_image_exhaustive(image_path,sid,jid,page_number=1):
    total={"created":0,"duplicates":0,"property_outputs":0,"requirement_outputs":0,"failed":0}
    units=[("FULL_PAGE",image_path,False)]+[(label,tp,True) for label,tp in crop_overlapping_tiles(image_path)]
    for label,path,is_temp in units:
        try:
            state=scan_tile_state(sid,page_number,label)
            if state and state.get("status")=="COMPLETED":
                continue
            mark_scan_tile(sid,page_number,label,"RUNNING")
            prompt=(PROMPT+"\nHigh-recall classified scanner. Extract EVERY distinct property advertisement visible in this image region. "
                    "Do not summarize, sample or merge neighboring ads. Preserve all legible phone numbers and names. "
                    "Overlapping crops are intentional; duplicates will be handled later. Region: "+label)
            env=extract_gemini_batch(path,"image/jpeg",prompt)
            cr,du,po,ro=save_scanned_envelope(env,sid,f"PAGE_{page_number}_{label}")
            mark_scan_tile(sid,page_number,label,"COMPLETED",cr,du,None)
            total["created"]+=cr; total["duplicates"]+=du; total["property_outputs"]+=po; total["requirement_outputs"]+=ro
            with engine.begin() as c:
                c.execute(text("UPDATE pi_ai_jobs SET output_summary=:o WHERE id=:id"),
                          {"o":f"Page {page_number} {label}: {total['created']} new records in this page scan","id":jid})
        except Exception as exc:
            mark_scan_tile(sid,page_number,label,"FAILED",0,0,str(exc)); total["failed"]+=1
        finally:
            if is_temp:
                try: os.unlink(path)
                except Exception: pass
    return total

def render_pdf_page(doc,page_index):
    page=doc.load_page(page_index)
    pix=page.get_pixmap(matrix=fitz.Matrix(PDF_RENDER_DPI/72.0,PDF_RENDER_DPI/72.0),alpha=False)
    fd,p=tempfile.mkstemp(suffix=".jpg"); os.close(fd); pix.save(p); return p

def run_file_job(sid,jid,path,mime):
    try:
        created=duplicates=property_outputs=requirement_outputs=failed=0
        is_pdf=(mime=="application/pdf" or path.lower().endswith(".pdf"))
        is_image=(mime or "").startswith("image/") or path.lower().endswith((".jpg",".jpeg",".png",".webp"))
        if is_pdf:
            doc=fitz.open(path)
            with engine.begin() as c:
                c.execute(text("UPDATE pi_ai_jobs SET input_summary=:x WHERE id=:id"),
                          {"x":f"Exhaustive scanner: {doc.page_count} PDF pages, full-page plus overlapping tiles","id":jid})
            for i in range(doc.page_count):
                page_img=None
                try:
                    page_img=render_pdf_page(doc,i)
                    r=scan_image_exhaustive(page_img,sid,jid,i+1)
                    created+=r["created"]; duplicates+=r["duplicates"]; property_outputs+=r["property_outputs"]; requirement_outputs+=r["requirement_outputs"]; failed+=r["failed"]
                except Exception as exc:
                    failed+=1
                    with engine.begin() as c:
                        c.execute(text("UPDATE pi_ai_jobs SET output_summary=:o WHERE id=:id"),{"o":f"Continuing after page {i+1} error: {exc}","id":jid})
                finally:
                    if page_img:
                        try: os.unlink(page_img)
                        except Exception: pass
            doc.close()
        elif is_image:
            fd,jpg=tempfile.mkstemp(suffix=".jpg"); os.close(fd)
            try:
                Image.open(path).convert("RGB").save(jpg,"JPEG",quality=95)
                r=scan_image_exhaustive(jpg,sid,jid,1)
                created=r["created"]; duplicates=r["duplicates"]; property_outputs=r["property_outputs"]; requirement_outputs=r["requirement_outputs"]; failed=r["failed"]
            finally:
                try: os.unlink(jpg)
                except Exception: pass
        else:
            env=extract_gemini_batch(path,mime,"Extract every distinct property and requirement from this source.")
            created,duplicates,property_outputs,requirement_outputs=save_scanned_envelope(env,sid,"SOURCE")
        status="PROCESSED_WITH_ERRORS" if failed else "PROCESSED"
        with engine.begin() as c:
            c.execute(text("""UPDATE pi_sources SET ingestion_status=:st,processed_records=:n,duplicate_records=:d,
                              ai_provider='gemini',ai_model=:m,processed_at=NOW() WHERE id=:id"""),
                      {"st":status,"n":created,"d":duplicates,"m":GEMINI_MODEL,"id":sid})
            c.execute(text("""UPDATE pi_ai_jobs SET status='COMPLETED',output_summary=:o,completed_at=NOW() WHERE id=:id"""),
                      {"o":f"Exhaustive scan complete: {created} new records, {duplicates} duplicates/overlap, {failed} failed units","id":jid})
    except Exception as ex:
        with engine.begin() as c:
            c.execute(text("UPDATE pi_sources SET ingestion_status='FAILED',error_message=:e WHERE id=:id"),{"e":str(ex),"id":sid})
            c.execute(text("UPDATE pi_ai_jobs SET status='FAILED',error_message=:e,completed_at=NOW() WHERE id=:id"),{"e":str(ex),"id":jid})
    finally:
        try: os.unlink(path)
        except Exception: pass

def create_job(sid,kind,summary):
    sql="INSERT INTO pi_ai_jobs(source_id,job_type,status,provider,model,input_summary,started_at) VALUES(:s,:k,'RUNNING','gemini',:m,:x,NOW()) RETURNING id"
    with engine.begin() as c:
        return c.execute(text(sql),{"s":sid,"k":kind,"m":GEMINI_MODEL,"x":summary}).scalar_one()

@app.get("/login",response_class=HTMLResponse)
def login_page(req:Request):
    existing=get_role(req)
    if existing:
        return RedirectResponse("/workspace",status_code=303)

    html="""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Property Intelligence Login</title>
<style>
*{box-sizing:border-box}
body{font-family:Arial,sans-serif;background:#f4f6f8;margin:0;min-height:100vh;display:grid;place-items:center;padding:18px}
.card{width:min(430px,100%);background:white;padding:26px;border-radius:14px;border:1px solid #e5e7eb;box-shadow:0 10px 30px rgba(0,0,0,.06)}
h2{margin:0 0 6px}.muted{color:#6b7280;margin:0 0 18px}
label{display:block;font-size:13px;font-weight:700;margin-top:10px}
select,input,button{width:100%;padding:13px;margin:6px 0;border-radius:9px;border:1px solid #d1d5db;font-size:16px}
button{background:#111827;color:white;border:0;font-weight:700;cursor:pointer;margin-top:14px}
.note{font-size:12px;color:#6b7280;margin-top:14px;line-height:1.5}
</style>
</head>
<body>
<form class="card" method="post" action="/login">
<h2>Property Intelligence</h2>
<p class="muted">Unified Team & Admin Workspace</p>
<label>Login as</label>
<select name="role">
<option value="team">Team</option>
<option value="admin">Admin</option>
</select>
<label>Access code</label>
<input name="code" type="password" placeholder="Enter access code" autocomplete="current-password" required>
<button type="submit">Open Workspace</button>
<p class="note">Use this same website on Android, iPhone, Windows or Mac. Each device logs in separately.</p>
</form>
</body>
</html>"""
    return HTMLResponse(html)

@app.post("/login")
def login_post(role:str=Form(...),code:str=Form(...)):
    ok=(role=="team" and hmac.compare_digest(code,TEAM_CODE)) or (role=="admin" and hmac.compare_digest(code,ADMIN_CODE))
    if not ok:return HTMLResponse("Invalid code",401)
    resp=RedirectResponse("/workspace",303)
    resp.set_cookie(
        "pi_session",
        signed(role),
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=60*60*24*7,
        path="/"
    )
    return resp

@app.get("/logout")
def logout():
    resp=RedirectResponse("/login",303);resp.delete_cookie("pi_session",path="/");return resp

@app.get("/health")
def health():
    with engine.connect() as c:c.execute(text("SELECT 1"))
    return {"status":"ok","service":"property-intelligence-unified","version":VERSION,"database":"connected","gemini_configured":bool(GEMINI_API_KEY)}


@app.get("/api/upload-status/{job_id}")
def upload_status(job_id:int,req:Request):
    need_login(req)
    with engine.connect() as c:
        row=c.execute(
            text("""SELECT j.id,j.status,j.output_summary,j.error_message,
                           j.created_at,j.completed_at,
                           s.id AS source_id,s.ingestion_status,s.processed_records,
                           s.duplicate_records,s.original_filename
                    FROM pi_ai_jobs j
                    LEFT JOIN pi_sources s ON s.id=j.source_id
                    WHERE j.id=:id"""),
            {"id":job_id}
        ).first()
    if not row:
        raise HTTPException(404,"Upload job not found")
    d={}
    for k,v in dict(row._mapping).items():
        if isinstance(v,(date,datetime)):
            d[k]=v.isoformat()
        elif isinstance(v,Decimal):
            d[k]=float(v)
        else:
            d[k]=v
    return {"status":"ok","job":d}

@app.get("/api/status")
def status(req:Request):
    need_login(req)
    with engine.connect() as c:
        tables={"properties":"pi_properties","requirements":"pi_requirements","sources":"pi_sources","matches":"pi_matches","ai_jobs":"pi_ai_jobs"}
        counts={k:c.execute(text("SELECT COUNT(*) FROM "+v)).scalar_one() for k,v in tables.items()}
    return {"status":"ok","role":get_role(req),"records":counts,"gemini_configured":bool(GEMINI_API_KEY)}


def serialize_db_value(value):
    if isinstance(value,(date,datetime)):
        return value.isoformat()
    if isinstance(value,Decimal):
        return float(value)
    return value

def property_with_verification_status(row):
    d={k:serialize_db_value(v) for k,v in dict(row._mapping).items()}
    verified=d.get("verified_date")
    if not verified:
        d["last_verified_label"]="Never Verified"
        d["verification_due"]=True
        d["verification_age_days"]=None
        return d

    verified_date=date.fromisoformat(verified) if isinstance(verified,str) else verified
    age=(date.today()-verified_date).days
    d["verification_age_days"]=age
    d["verification_due"]=age>=VERIFICATION_DUE_DAYS
    d["last_verified_label"]=(
        f"Verification Due · Last verified {verified}"
        if d["verification_due"]
        else f"Last verified {verified}"
    )
    return d

@app.post("/api/properties")
def add_property(p:Property,req:Request):
    need_login(req);return save_property(p.model_dump())


@app.get("/api/properties/{property_id}")
def get_property(property_id:str,req:Request):
    need_login(req)
    with engine.connect() as c:
        row=c.execute(
            text("SELECT * FROM pi_properties WHERE property_id=:pid"),
            {"pid":property_id}
        ).first()
    if not row:
        raise HTTPException(404,"Property not found")
    return {"status":"ok","property":property_with_verification_status(row)}

@app.put("/api/properties/{property_id}")
def update_property(property_id:str,p:Property,req:Request):
    need_login(req)
    actor=actor_name(req)
    data=p.model_dump()

    with engine.begin() as c:
        row=c.execute(
            text("SELECT * FROM pi_properties WHERE property_id=:pid FOR UPDATE"),
            {"pid":property_id}
        ).first()
        if not row:
            raise HTTPException(404,"Property not found")

        old={k:serialize_db_value(v) for k,v in dict(row._mapping).items()}

        allowed=[
            "property_name","property_type","city","location","available_area_sqft",
            "minimum_area_sqft","maximum_area_sqft","floor","rent_or_sale",
            "nearby_brands","suitable_category","parking","owner_name","owner_contact",
            "broker_name","broker_contact","remarks","image_urls","video_urls","brochure_url"
        ]

        params={k:data.get(k) for k in allowed}
        params["pid"]=property_id

        sets=",".join(f"{k}=:{k}" for k in allowed)
        c.execute(
            text(f"UPDATE pi_properties SET {sets},updated_at=NOW() WHERE property_id=:pid"),
            params
        )

        newrow=c.execute(
            text("SELECT * FROM pi_properties WHERE property_id=:pid"),
            {"pid":property_id}
        ).first()
        newd={k:serialize_db_value(v) for k,v in dict(newrow._mapping).items()}

        c.execute(
            text("""INSERT INTO pi_verification_log(
                        property_id,action,performed_by,notes,old_value,new_value
                    ) VALUES(
                        :pid,'PROPERTY_EDIT',:actor,'Property details edited',
                        CAST(:old AS JSONB),CAST(:new AS JSONB)
                    )"""),
            {
                "pid":property_id,
                "actor":actor,
                "old":json.dumps(old,default=str),
                "new":json.dumps(newd,default=str)
            }
        )

    return {"status":"updated","property_id":property_id,"property":property_with_verification_status(newrow)}

@app.post("/api/properties/{property_id}/verify")
def verify_property(property_id:str,req:Request):
    need_login(req)
    actor=actor_name(req)

    with engine.begin() as c:
        row=c.execute(
            text("SELECT verified_date,verified_by FROM pi_properties WHERE property_id=:pid FOR UPDATE"),
            {"pid":property_id}
        ).first()
        if not row:
            raise HTTPException(404,"Property not found")

        old={
            "verified_date":serialize_db_value(row._mapping["verified_date"]),
            "verified_by":row._mapping["verified_by"]
        }

        c.execute(
            text("""UPDATE pi_properties
                    SET verified_date=CURRENT_DATE,
                        verified_by=:actor,
                        verification_status='VERIFIED',
                        updated_at=NOW()
                    WHERE property_id=:pid"""),
            {"actor":actor,"pid":property_id}
        )

        c.execute(
            text("""INSERT INTO pi_verification_log(
                        property_id,action,performed_by,notes,old_value,new_value
                    ) VALUES(
                        :pid,'VERIFIED',:actor,'Property verified',
                        CAST(:old AS JSONB),CAST(:new AS JSONB)
                    )"""),
            {
                "pid":property_id,
                "actor":actor,
                "old":json.dumps(old,default=str),
                "new":json.dumps({
                    "verified_date":date.today().isoformat(),
                    "verified_by":actor,
                    "verification_status":"VERIFIED"
                })
            }
        )

    return {
        "status":"verified",
        "property_id":property_id,
        "verified_date":date.today().isoformat(),
        "verified_by":actor,
        "next_verification_due_days":VERIFICATION_DUE_DAYS
    }


@app.post("/api/properties/{property_id}/media")
async def upload_property_media(property_id:str,req:Request,file:UploadFile=File(...)):
    need_login(req)

    filename=file.filename or "property-image.jpg"
    mime=(file.content_type or "").lower()
    allowed={"image/jpeg","image/png","image/webp","image/gif"}

    if mime not in allowed:
        ext=os.path.splitext(filename)[1].lower()
        extmap={".jpg":"image/jpeg",".jpeg":"image/jpeg",".png":"image/png",".webp":"image/webp",".gif":"image/gif"}
        mime=extmap.get(ext,mime)

    if mime not in allowed:
        raise HTTPException(400,"Only JPG, JPEG, PNG, WEBP and GIF images are allowed.")

    data=await file.read()
    if not data:
        raise HTTPException(400,"Image file is empty.")

    if len(data) > MAX_IMAGE_MB*1024*1024:
        raise HTTPException(413,f"Each image must be {MAX_IMAGE_MB} MB or smaller.")

    with engine.begin() as c:
        exists=c.execute(
            text("SELECT 1 FROM pi_properties WHERE property_id=:pid"),
            {"pid":property_id}
        ).first()
        if not exists:
            raise HTTPException(404,"Property not found.")

        count=c.execute(
            text("SELECT COUNT(*) FROM pi_property_media WHERE property_id=:pid"),
            {"pid":property_id}
        ).scalar_one()

        if count >= MAX_PROPERTY_IMAGES:
            raise HTTPException(413,f"Maximum {MAX_PROPERTY_IMAGES} images per property.")

        media_id=str(uuid.uuid4())
        c.execute(
            text("""INSERT INTO pi_property_media(
                        media_id,property_id,media_type,filename,mime_type,file_size,content
                    ) VALUES(
                        CAST(:mid AS UUID),:pid,'IMAGE',:fn,:mime,:size,:content
                    )"""),
            {
                "mid":media_id,
                "pid":property_id,
                "fn":filename,
                "mime":mime,
                "size":len(data),
                "content":data
            }
        )

    base=str(req.base_url).rstrip("/")
    public_url=f"{base}/media/{media_id}"

    with engine.begin() as c:
        old=c.execute(
            text("SELECT image_urls FROM pi_properties WHERE property_id=:pid"),
            {"pid":property_id}
        ).scalar_one_or_none()

        urls=[u.strip() for u in str(old or "").splitlines() if u.strip()]
        if public_url not in urls:
            urls.append(public_url)

        c.execute(
            text("UPDATE pi_properties SET image_urls=:urls,updated_at=NOW() WHERE property_id=:pid"),
            {"urls":"\n".join(urls),"pid":property_id}
        )

    return {
        "status":"uploaded",
        "property_id":property_id,
        "media_id":media_id,
        "url":public_url,
        "filename":filename
    }

@app.get("/media/{media_id}")
def public_property_media(media_id:str):
    try:
        uuid.UUID(media_id)
    except Exception:
        raise HTTPException(404,"Media not found.")

    with engine.connect() as c:
        row=c.execute(
            text("""SELECT mime_type,content,filename
                    FROM pi_property_media
                    WHERE media_id=CAST(:mid AS UUID)"""),
            {"mid":media_id}
        ).first()

    if not row:
        raise HTTPException(404,"Media not found.")

    return Response(
        content=bytes(row._mapping["content"]),
        media_type=row._mapping["mime_type"] or "application/octet-stream",
        headers={
            "Cache-Control":"public, max-age=31536000, immutable",
            "Content-Disposition":f'inline; filename="{row._mapping["filename"] or "property-image"}"'
        }
    )

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
async def ingest_file(
    bg:BackgroundTasks,
    req:Request,
    file:UploadFile=File(...),
    source_type:str=Query("DOCUMENT"),
    source_name:Optional[str]=Query(None)
):
    need_login(req)

    filename=file.filename or "upload.bin"
    ext=os.path.splitext(filename)[1].lower()
    mime=file.content_type or "application/octet-stream"

    # Browser/device MIME types can be missing or inconsistent.
    mime_map={
        ".jpg":"image/jpeg",
        ".jpeg":"image/jpeg",
        ".png":"image/png",
        ".webp":"image/webp",
        ".pdf":"application/pdf",
        ".csv":"text/csv",
        ".txt":"text/plain"
    }
    if mime in {"application/octet-stream",""} or not mime:
        mime=mime_map.get(ext,"application/octet-stream")

    suffix=ext or ".bin"

    # Gemini PDF processing limit is 50 MB.
    pdf_limit=50*1024*1024
    app_limit=MAX_UPLOAD_MB*1024*1024

    fd,path=tempfile.mkstemp(suffix=suffix)
    os.close(fd)

    total=0
    try:
        with open(path,"wb") as out:
            while True:
                chunk=await file.read(1024*1024)  # 1 MB chunks
                if not chunk:
                    break
                total += len(chunk)

                if total > app_limit:
                    raise HTTPException(
                        413,
                        f"File too large. Maximum workspace upload is {MAX_UPLOAD_MB} MB."
                    )

                if (mime=="application/pdf" or filename.lower().endswith(".pdf")) and total > pdf_limit:
                    raise HTTPException(
                        413,
                        "PDF too large for Gemini processing. Maximum PDF size is 50 MB. Please split/compress the PDF."
                    )

                out.write(chunk)

        sid=source_row(
            source_type.upper(),
            source_name or filename,
            filename,
            mime
        )

        # CSV is processed directly after streamed upload.
        if filename.lower().endswith(".csv"):
            inserted=0
            duplicates=0
            errors=[]

            with open(path,"r",encoding="utf-8-sig",errors="replace",newline="") as csvfile:
                reader=csv.DictReader(csvfile)
                for line,row in enumerate(reader,start=2):
                    item={
                        "property_name":row.get("Property name") or row.get("Property Name"),
                        "property_type":row.get("Property type") or row.get("Property Type") or "NA",
                        "city":row.get("City") or "NA",
                        "location":row.get("Location") or "NA",
                        "available_area_sqft":row.get("Available area") or row.get("Available Area") or None,
                        "minimum_area_sqft":row.get("Minimum area") or row.get("Minimum Area") or None,
                        "maximum_area_sqft":row.get("Maximum area") or row.get("Maximum Area") or None,
                        "floor":row.get("Floor"),
                        "rent_or_sale":row.get("Rent/Sale"),
                        "nearby_brands":row.get("Nearby brand") or row.get("Nearby brands"),
                        "suitable_category":row.get("Suitable category"),
                        "parking":row.get("Parking"),
                        "owner_name":row.get("Owner name"),
                        "owner_contact":row.get("Owner contact"),
                        "broker_name":row.get("Broker name"),
                        "broker_contact":row.get("Broker contact"),
                        "remarks":row.get("Remarks"),
                        "source":"CSV:"+filename
                    }

                    for numkey in ["available_area_sqft","minimum_area_sqft","maximum_area_sqft"]:
                        try:
                            item[numkey]=float(str(item[numkey]).replace(",","")) if item[numkey] not in (None,"") else None
                        except Exception:
                            item[numkey]=None

                    try:
                        result=save_property(item,sid)
                        if result["status"]=="created":
                            inserted+=1
                        else:
                            duplicates+=1
                    except Exception as exc:
                        errors.append({"row":line,"error":str(exc)})

            with engine.begin() as c:
                c.execute(
                    text("""UPDATE pi_sources
                            SET ingestion_status=:status,
                                processed_records=:n,
                                duplicate_records=:d,
                                error_message=:e,
                                processed_at=NOW()
                            WHERE id=:id"""),
                    {
                        "status":"PROCESSED" if not errors else "PROCESSED_WITH_ERRORS",
                        "n":inserted,
                        "d":duplicates,
                        "e":json.dumps(errors[:20]) if errors else None,
                        "id":sid
                    }
                )

            try: os.unlink(path)
            except: pass

            return {
                "status":"PROCESSED" if not errors else "PROCESSED_WITH_ERRORS",
                "source_id":sid,
                "inserted":inserted,
                "duplicates":duplicates,
                "errors":errors[:20],
                "file_size_mb":round(total/1024/1024,2)
            }

        jid=create_job(sid,"FILE_EXTRACTION",filename)
        bg.add_task(run_file_job,sid,jid,path,mime)

        return {
            "status":"ACCEPTED",
            "source_id":sid,
            "job_id":jid,
            "file_size_mb":round(total/1024/1024,2),
            "message":"Upload received. AI processing continues in background."
        }

    except Exception:
        try: os.unlink(path)
        except: pass
        raise

TABLES={"properties":"pi_properties","requirements":"pi_requirements","matches":"pi_matches","whatsapp_drafts":"pi_message_drafts","sources":"pi_sources","ai_jobs":"pi_ai_jobs","verification":"pi_verification_log","batches":"pi_extraction_batches","media":"pi_property_media","scan_tiles":"pi_scan_tiles"}
PRIVATE={"fingerprint","owner_name","owner_contact","broker_name","broker_contact","remarks","verified_by","verified_date","source"}

@app.get("/api/database/{name}")
def database(name:str,req:Request,limit:int=Query(500,ge=1,le=2000)):
    role=need_login(req)
    if name not in TABLES:raise HTTPException(404,"Unknown table")
    if role!="admin" and name in {"sources","ai_jobs","verification","batches","media"}:raise HTTPException(403,"Admin only")
    with engine.connect() as c:
        if name=="media":
            result=c.execute(text("""SELECT id,media_id,property_id,media_type,filename,mime_type,file_size,created_at
                                     FROM pi_property_media ORDER BY id DESC LIMIT :n"""),{"n":limit})
        else:
            result=c.execute(text("SELECT * FROM "+TABLES[name]+" ORDER BY id DESC LIMIT :n"),{"n":limit})
        rows=[]
        for row in result:
            d={}
            for k,v in dict(row._mapping).items():
                d[k]=v.isoformat() if isinstance(v,(date,datetime)) else float(v) if isinstance(v,Decimal) else v
            rows.append(d)
    if name=="properties":
        enhanced=[]
        for x in rows:
            verified=x.get("verified_date")
            if not verified:
                x["last_verified"]="Never Verified"
                x["verification_due"]=True
            else:
                vd=date.fromisoformat(verified) if isinstance(verified,str) else verified
                age=(date.today()-vd).days
                x["last_verified"]=f"{verified} ({age} days ago)"
                x["verification_due"]=age>=VERIFICATION_DUE_DAYS
            enhanced.append(x)
        rows=enhanced

    if name=="properties" and role!="admin":
        # Team can see verification date/status but not private owner/broker/internal fields.
        team_private=PRIVATE-{"verified_date","verified_by"}
        rows=[{k:v for k,v in x.items() if k not in team_private} for x in rows]

    return {"status":"ok","table":name,"count":len(rows),"rows":rows}


def fallback_whatsapp_message(requirement,top_properties):
    name=requirement.get("client_name") or requirement.get("company_name") or "there"
    lines=[f"Hi {name}, we found a few property options matching your requirement:"]
    for i,p in enumerate(top_properties[:3],1):
        bits=[f"{i}. {p.get('property_name') or p.get('property_id')}"]
        if p.get("location"): bits.append(str(p["location"]))
        if p.get("available_area_sqft") is not None: bits.append(f"{p['available_area_sqft']} sq ft")
        if p.get("rent_or_sale"): bits.append(str(p["rent_or_sale"]))
        lines.append(" | ".join(bits))
        if p.get("image_urls"): lines.append("Photos: "+str(p["image_urls"]))
        if p.get("video_urls"): lines.append("Video: "+str(p["video_urls"]))
        if p.get("brochure_url"): lines.append("Brochure: "+str(p["brochure_url"]))
    lines.append("Please let me know which option you would like to review in detail or schedule a site visit for.")
    return "\\n".join(lines)

def generate_whatsapp_message(requirement,top_properties):
    fallback=fallback_whatsapp_message(requirement,top_properties)
    if not client:
        return fallback,"fallback"

    safe_properties=[]
    for p in top_properties[:5]:
        safe_properties.append({
            "property_id":p.get("property_id"),
            "property_name":p.get("property_name"),
            "city":p.get("city"),
            "location":p.get("location"),
            "available_area_sqft":float(p["available_area_sqft"]) if p.get("available_area_sqft") is not None else None,
            "floor":p.get("floor"),
            "rent_or_sale":p.get("rent_or_sale"),
            "nearby_brands":p.get("nearby_brands"),
            "suitable_category":p.get("suitable_category"),
            "image_urls":p.get("image_urls"),
            "video_urls":p.get("video_urls"),
            "brochure_url":p.get("brochure_url"),
        })

    prompt=f"""Write one concise professional WhatsApp message for a real-estate client.
Use ONLY the supplied requirement and matched property facts.
Do not reveal owner names, broker names, owner contacts, broker contacts, internal remarks or source data.
Do not invent rent, price, amenities or availability details.
Mention no more than the best 3 options.
If image_urls, video_urls or brochure_url are available for a selected property, include the relevant links directly under that property.
Never invent, rewrite or shorten a media URL. Copy supplied URLs exactly.
End with a simple call to action for details or a site visit.

Requirement:
{json.dumps({k:v for k,v in requirement.items() if k not in {'fingerprint','source_id'}},default=str)}

Matched properties:
{json.dumps(safe_properties,default=str)}
"""

    try:
        response=client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=WhatsAppDraftResult,
                temperature=0.3
            )
        )
        parsed=WhatsAppDraftResult.model_validate(response.parsed) if getattr(response,"parsed",None) is not None else WhatsAppDraftResult.model_validate_json(response.text)
        message=(parsed.message or "").strip()
        return (message if message else fallback),"gemini"
    except Exception:
        return fallback,"fallback"

def store_whatsapp_draft(requirement,message,provider):
    with engine.begin() as c:
        did=c.execute(
            text("""INSERT INTO pi_message_drafts(
                        requirement_id,recipient_name,recipient_phone,channel,message_text,status,ai_provider,ai_model
                    ) VALUES(
                        :rid,:name,:phone,'WHATSAPP',:msg,'READY_FOR_REVIEW',:provider,:model
                    ) RETURNING id"""),
            {
                "rid":requirement.get("requirement_id"),
                "name":requirement.get("client_name") or requirement.get("company_name"),
                "phone":requirement.get("contact_phone"),
                "msg":message,
                "provider":provider,
                "model":GEMINI_MODEL if provider=="gemini" else None
            }
        ).scalar_one()
    return did

@app.post("/api/match/{rid}")
def match(rid:str,req:Request):
    need_login(req)

    with engine.begin() as c:
        qrow=c.execute(
            text("SELECT * FROM pi_requirements WHERE requirement_id=:id"),
            {"id":rid}
        ).first()
        if not qrow:
            raise HTTPException(404,"Requirement not found")

        requirement=dict(qrow._mapping)

        props=c.execute(
            text("SELECT * FROM pi_properties WHERE availability_status='Available'")
        ).fetchall()

        c.execute(
            text("DELETE FROM pi_matches WHERE requirement_id=:id"),
            {"id":rid}
        )

        out=[]
        property_map={}

        for row in props:
            p=dict(row._mapping)
            property_map[p["property_id"]]=p
            score=0
            reasons=[]

            if requirement.get("city") and str(requirement["city"]).lower()==str(p.get("city") or "").lower():
                score+=30;reasons.append("City")

            if requirement.get("preferred_locations") and str(p.get("location") or "").lower() in str(requirement["preferred_locations"]).lower():
                score+=30;reasons.append("Location")

            a=p.get("available_area_sqft")
            mn=requirement.get("minimum_area_sqft")
            mx=requirement.get("maximum_area_sqft")
            if a is not None and (mn is None or a>=mn) and (mx is None or a<=mx):
                score+=30;reasons.append("Area")

            if requirement.get("rent_or_sale") and str(requirement["rent_or_sale"]).lower()==str(p.get("rent_or_sale") or "").lower():
                score+=10;reasons.append("Rent/Sale")

            out.append({
                "property_id":p["property_id"],
                "property_name":p.get("property_name"),
                "city":p.get("city"),
                "location":p.get("location"),
                "available_area_sqft":float(a) if a is not None else None,
                "rent_or_sale":p.get("rent_or_sale"),
                "score":score,
                "reasons":reasons
            })

        out.sort(key=lambda x:x["score"],reverse=True)

        for i,x in enumerate(out,1):
            c.execute(
                text("""INSERT INTO pi_matches(
                            requirement_id,property_id,match_score,rank,match_reasons,status
                        ) VALUES(
                            :r,:p,:s,:i,CAST(:m AS JSONB),'READY_FOR_REVIEW'
                        )"""),
                {
                    "r":rid,
                    "p":x["property_id"],
                    "s":x["score"],
                    "i":i,
                    "m":json.dumps(x["reasons"])
                }
            )

    top_property_rows=[]
    for x in out[:5]:
        p=property_map.get(x["property_id"])
        if p:
            top_property_rows.append(p)

    whatsapp_message,provider=generate_whatsapp_message(
        requirement,
        top_property_rows
    )

    draft_id=store_whatsapp_draft(
        requirement,
        whatsapp_message,
        provider
    )

    return {
        "status":"READY_FOR_REVIEW",
        "matches":out[:50],
        "whatsapp_draft":{
            "id":draft_id,
            "status":"READY_FOR_REVIEW",
            "recipient_name":requirement.get("client_name") or requirement.get("company_name"),
            "recipient_phone":requirement.get("contact_phone"),
            "message":whatsapp_message,
            "generated_by":provider
        }
    }

WORKSPACE='''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Property Intelligence</title>
<style>*{box-sizing:border-box}body{font-family:Arial;margin:0;background:#f5f7fb}header{background:#111827;color:white;padding:18px 24px;display:flex;justify-content:space-between}nav{background:white;padding:10px 20px;border-bottom:1px solid #ddd}button{padding:9px 12px;margin:3px;border:0;border-radius:7px;background:#111827;color:white}.wrap{padding:20px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:15px}.card{background:white;padding:16px;border:1px solid #ddd;border-radius:10px}input,select,textarea{width:100%;padding:9px;margin:5px 0}.hidden{display:none}pre{background:#f8fafc;padding:9px;max-height:250px;overflow:auto}table{border-collapse:collapse;width:100%;font-size:12px}th,td{padding:8px;border-bottom:1px solid #eee;text-align:left;white-space:nowrap}.tablebox{overflow:auto;max-height:65vh}</style></head>
<body><header><div><b>Property Intelligence Unified Workspace</b><br><small>Team + Admin on one domain</small></div><div>__ROLE__ | <a href="/logout" style="color:white">Logout</a></div></header>
<nav>
<button type="button" onclick="sec('ops')">Operations</button>
<button type="button" onclick="sec('db')">Database</button>
<button type="button" onclick="sec('status')">Status</button>
__ADMINBTN__
</nav>
<div id="appErrorBanner" style="display:none;margin:12px 20px 0;padding:10px;border-radius:8px;background:#fef2f2;color:#991b1b;border:1px solid #fecaca"></div>
<div class="wrap"><section id="ops"><div class="grid">
<div class="card">
<h3>Upload Photo / Magazine / PDF / CSV</h3>
<input id="sn" placeholder="Source name">
<select id="st">
<option>MAGAZINE</option><option>NEWSPAPER</option><option>PHOTO</option>
<option>PDF</option><option>CSV</option>
</select>
<input id="fu" type="file" accept=".jpg,.jpeg,.png,.webp,.pdf,.csv,.txt">
<button id="uploadBtn" onclick="up()">Upload</button>
<div id="progressWrap" style="display:none;margin-top:14px">
  <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:6px">
    <span id="progressText">Preparing upload...</span>
    <b id="progressPct">0%</b>
  </div>
  <div style="height:14px;background:#e5e7eb;border-radius:999px;overflow:hidden">
    <div id="progressBar" style="height:100%;width:0%;background:#111827;transition:width .15s"></div>
  </div>
</div>
<div id="uploadResult" style="margin-top:12px;font-size:14px"></div>
</div>
<div class="card"><h3>Paste WhatsApp / Email</h3><input id="tn" placeholder="Source name"><textarea id="tc" rows="7"></textarea><button onclick="txt()">Process</button><pre id="to"></pre></div>
<div class="card"><h3>Add Property Manually</h3>
<input id="pn" placeholder="Property name / building">
<input id="pt" placeholder="Property type">
<input id="pc" placeholder="City">
<input id="pl" placeholder="Location">
<input id="pa" type="number" placeholder="Available sqft">
<input id="pf" placeholder="Floor">
<select id="px"><option>Rent</option><option>Sale</option></select>
<input id="pnb" placeholder="Nearby brands">
<input id="psc" placeholder="Suitable category">
<input id="ppk" placeholder="Parking">

<div id="dropZone" style="border:2px dashed #9ca3af;border-radius:10px;padding:20px;text-align:center;margin:10px 0;background:#f9fafb;cursor:pointer">
  <b>Drag & Drop Property Photos Here</b><br>
  <small>or click to choose multiple photos</small>
  <input id="pfiles" type="file" accept="image/jpeg,image/png,image/webp,image/gif" multiple style="display:none">
</div>
<div id="previewGrid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(90px,1fr));gap:8px;margin-bottom:10px"></div>
<div id="photoCount" style="font-size:12px;color:#6b7280;margin-bottom:8px">0 photos selected</div>

<input id="pimg" placeholder="Optional external photo links">
<input id="pvid" placeholder="Video links - YouTube / Drive / other public links">
<input id="pbro" placeholder="Brochure / presentation link">
<textarea id="prem" rows="3" placeholder="Remarks"></textarea>
<small>You can drag up to __MAX_PROPERTY_IMAGES__ photos. Each photo can be up to __MAX_IMAGE_MB__ MB. Photos will be attached to the Property ID and used in WhatsApp drafts.</small><br>
<button id="savePropertyBtn" onclick="prop()">Save Property + Photos</button>
<div id="propertySaveStatus" style="margin-top:10px"></div>
<pre id="po"></pre>
</div>
<div class="card"><h3>Edit / Verify Existing Property</h3>
<input id="epid" placeholder="Property ID e.g. PROP-20260810-0000000123">
<button onclick="loadEditProperty()">Load Property</button>
<div id="verifyBadge" style="display:none;padding:10px;border-radius:8px;margin:10px 0;font-weight:700"></div>
<div id="editFields" style="display:none">
<input id="epn" placeholder="Property name">
<input id="ept" placeholder="Property type">
<input id="epc" placeholder="City">
<input id="epl" placeholder="Location">
<input id="epa" type="number" placeholder="Available sqft">
<input id="epf" placeholder="Floor">
<select id="epx"><option>Rent</option><option>Sale</option></select>
<input id="epnb" placeholder="Nearby brands">
<input id="epsc" placeholder="Suitable category">
<input id="eppk" placeholder="Parking">
<input id="epimg" placeholder="Photo links">
<input id="epvid" placeholder="Video links">
<input id="epbro" placeholder="Brochure link">
<textarea id="eprem" rows="3" placeholder="Remarks"></textarea>
<button onclick="saveEditProperty()">Save Changes</button>
<button onclick="verifyNow()">Verify Now</button>
</div>
<pre id="epo"></pre>
</div>
<div class="card"><h3>Add Requirement</h3><input id="rc" placeholder="Client"><input id="rci" placeholder="City"><input id="rl" placeholder="Preferred locations"><input id="rmin" type="number" placeholder="Min sqft"><input id="rmax" type="number" placeholder="Max sqft"><select id="rx"><option>Rent</option><option>Sale</option></select><button onclick="req()">Save</button><pre id="ro"></pre></div>
<div class="card"><h3>Run Matcher + WhatsApp Draft</h3><input id="rid" placeholder="Requirement ID"><button onclick="mt()">Match + Create WhatsApp</button><pre id="mo"></pre><div id="waBox" style="display:none;margin-top:10px"><b>WhatsApp Draft</b><textarea id="waText" rows="9" readonly></textarea><small>READY_FOR_REVIEW. Nothing is sent automatically.</small></div></div>
</div></section>
<section id="db" class="hidden"><div class="card"><h3>Database</h3><div id="tabs"></div><p id="meta"></p><div class="tablebox"><table id="grid"></table></div></div></section>
<section id="status" class="hidden"><div class="card"><button onclick="stat()">Refresh</button><pre id="so"></pre></div></section>
<section id="admin" class="hidden"><div class="card"><h3>Admin</h3><div id="atabs"></div><p id="ameta"></p><div class="tablebox"><table id="agrid"></table></div></div></section></div>
<script>
window.addEventListener("error",function(ev){
  const banner=document.getElementById("appErrorBanner");
  if(banner){
    banner.style.display="block";
    banner.textContent="Workspace error: "+(ev.message||"Unknown browser error");
  }
});
const ROLE="__ROLELOW__";const e=i=>document.getElementById(i),v=i=>e(i).value,s=(i,d)=>e(i).textContent=JSON.stringify(d,null,2);
async function jf(u,o={}){
  const r=await fetch(u,o);
  const t=await r.text();
  let d;
  try{ d=JSON.parse(t); }
  catch(err){ d={message:t||("HTTP "+r.status)}; }
  if(!r.ok)throw d;
  return d;
}
function sec(i){
  ["ops","db","status","admin"].forEach(x=>{
    const el=e(x);
    if(el)el.classList.add("hidden");
  });
  const target=e(i);
  if(!target)return;
  target.classList.remove("hidden");
  if(i==="db")load("properties","grid","meta");
  if(i==="status")stat();
}
function setProgress(pct,text){
  e("progressWrap").style.display="block";
  e("progressPct").innerText=pct+"%";
  e("progressBar").style.width=pct+"%";
  if(text)e("progressText").innerText=text;
}

function showUploadMessage(text,ok=true){
  e("uploadResult").innerHTML='<div style="padding:10px;border-radius:8px;background:'+
    (ok?'#ecfdf5;color:#065f46':'#fef2f2;color:#991b1b')+'">'+text+'</div>';
}

function watchJob(jobId){
  let tries=0;
  const timer=setInterval(async()=>{
    tries++;
    try{
      const d=await jf("/api/upload-status/"+jobId);
      const j=d.job||{};
      if(j.status==="COMPLETED"){
        clearInterval(timer);
        setProgress(100,"AI processing completed");
        showUploadMessage("✓ Upload and AI extraction completed successfully. Processed records: "+
          (j.processed_records??0)+(j.duplicate_records?(" | Duplicates: "+j.duplicate_records):""));
      }else if(j.status==="FAILED"){
        clearInterval(timer);
        setProgress(100,"Upload completed, AI processing failed");
        showUploadMessage("Upload succeeded, but AI extraction failed: "+(j.error_message||"Unknown error"),false);
      }else{
        e("progressText").innerText="AI is processing magazine batches... "+(j.processed_records??0)+" records stored so far";
      }
    }catch(err){}
    if(tries>120){
      clearInterval(timer);
      showUploadMessage("Upload completed. AI processing is continuing in the background.");
    }
  },2500);
}

function up(){
  const file=e("fu").files[0];
  if(!file){
    showUploadMessage("Please choose a file first.",false);
    return;
  }

  const fd=new FormData();
  fd.append("file",file);

  const q=new URLSearchParams({
    source_type:v("st"),
    source_name:v("sn")
  });

  e("uploadBtn").disabled=true;
  e("uploadResult").innerHTML="";
  setProgress(0,"Starting upload...");

  const xhr=new XMLHttpRequest();
  xhr.open("POST","/api/ingest/file?"+q.toString(),true);

  xhr.upload.onprogress=function(ev){
    if(ev.lengthComputable){
      const pct=Math.min(99,Math.round((ev.loaded/ev.total)*100));
      setProgress(pct,"Uploading "+file.name);
    }
  };

  xhr.onerror=function(){
    e("uploadBtn").disabled=false;
    showUploadMessage("Upload failed because the network connection was interrupted. Please try again.",false);
  };

  xhr.onload=function(){
    e("uploadBtn").disabled=false;

    let data={};
    try{data=JSON.parse(xhr.responseText||"{}")}catch(err){
      data={detail:xhr.responseText||"Unknown response"};
    }

    if(xhr.status<200 || xhr.status>=300){
      setProgress(0,"Upload failed");
      showUploadMessage(data.detail||data.message||"Upload failed.",false);
      return;
    }

    setProgress(100,"Upload completed");

    if(data.status==="PROCESSED"){
      showUploadMessage("✓ File uploaded and imported successfully. Records added: "+(data.inserted??0));
      return;
    }

    if(data.status==="ACCEPTED"){
      showUploadMessage("✓ File uploaded successfully. AI is now reading and organizing it.");
      if(data.job_id)watchJob(data.job_id);
      return;
    }

    showUploadMessage("✓ Upload completed successfully.");
  };

  xhr.send(fd);
}

async function txt(){try{s("to",await jf("/api/ingest/text",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({source_type:"WHATSAPP",source_name:v("tn"),text_content:v("tc")})}))}catch(x){s("to",x)}}

let selectedPropertyFiles=[];

function renderPropertyPreviews(){
  const grid=e("previewGrid");
  grid.innerHTML="";
  selectedPropertyFiles.forEach((file,index)=>{
    const box=document.createElement("div");
    box.style.position="relative";
    box.style.border="1px solid #e5e7eb";
    box.style.borderRadius="8px";
    box.style.overflow="hidden";
    box.style.background="#fff";

    const img=document.createElement("img");
    img.style.width="100%";
    img.style.height="90px";
    img.style.objectFit="cover";
    img.src=URL.createObjectURL(file);

    const btn=document.createElement("button");
    btn.type="button";
    btn.innerText="×";
    btn.style.position="absolute";
    btn.style.top="3px";
    btn.style.right="3px";
    btn.style.width="26px";
    btn.style.height="26px";
    btn.style.padding="0";
    btn.style.borderRadius="50%";
    btn.onclick=()=>{
      selectedPropertyFiles.splice(index,1);
      renderPropertyPreviews();
    };

    box.appendChild(img);
    box.appendChild(btn);
    grid.appendChild(box);
  });
  e("photoCount").innerText=selectedPropertyFiles.length+" photos selected";
}

function addPropertyFiles(fileList){
  const incoming=Array.from(fileList||[]).filter(f=>f.type.startsWith("image/"));
  for(const f of incoming){
    if(selectedPropertyFiles.length>=12) break;
    const duplicate=selectedPropertyFiles.some(x=>x.name===f.name && x.size===f.size);
    if(!duplicate) selectedPropertyFiles.push(f);
  }
  renderPropertyPreviews();
}

window.addEventListener("DOMContentLoaded",()=>{
  const dz=e("dropZone");
  const input=e("pfiles");

  dz.onclick=()=>input.click();
  input.onchange=()=>addPropertyFiles(input.files);

  ["dragenter","dragover"].forEach(evt=>{
    dz.addEventListener(evt,ev=>{
      ev.preventDefault();
      dz.style.background="#eef2ff";
      dz.style.borderColor="#4f46e5";
    });
  });

  ["dragleave","drop"].forEach(evt=>{
    dz.addEventListener(evt,ev=>{
      ev.preventDefault();
      dz.style.background="#f9fafb";
      dz.style.borderColor="#9ca3af";
    });
  });

  dz.addEventListener("drop",ev=>{
    addPropertyFiles(ev.dataTransfer.files);
  });
});

async function uploadPropertyImages(propertyId){
  const uploaded=[];
  for(let i=0;i<selectedPropertyFiles.length;i++){
    const file=selectedPropertyFiles[i];
    e("propertySaveStatus").innerText="Uploading photo "+(i+1)+" of "+selectedPropertyFiles.length+"...";
    const fd=new FormData();
    fd.append("file",file);
    const d=await jf("/api/properties/"+encodeURIComponent(propertyId)+"/media",{
      method:"POST",
      body:fd
    });
    uploaded.push(d);
  }
  return uploaded;
}

async function prop(){
  try{
    e("savePropertyBtn").disabled=true;
    e("propertySaveStatus").innerText="Saving property...";

    const d=await jf("/api/properties",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({
        property_name:v("pn"),
        property_type:v("pt")||"NA",
        city:v("pc")||"NA",
        location:v("pl")||"NA",
        available_area_sqft:Number(v("pa"))||null,
        floor:v("pf")||null,
        rent_or_sale:v("px"),
        nearby_brands:v("pnb")||null,
        suitable_category:v("psc")||null,
        parking:v("ppk")||null,
        image_urls:v("pimg")||null,
        video_urls:v("pvid")||null,
        brochure_url:v("pbro")||null,
        remarks:v("prem")||null,
        source:"Manual"
      })
    });

    if(d.status==="duplicate"){
      s("po",d);
      e("propertySaveStatus").innerText="This property appears to already exist. Photos were not uploaded.";
      return;
    }

    let uploaded=[];
    if(d.property_id && selectedPropertyFiles.length){
      uploaded=await uploadPropertyImages(d.property_id);
    }

    s("po",{
      status:"saved",
      property_id:d.property_id,
      photos_uploaded:uploaded.length,
      photo_urls:uploaded.map(x=>x.url)
    });

    e("propertySaveStatus").innerText="✓ Property saved successfully with "+uploaded.length+" photos.";
    selectedPropertyFiles=[];
    renderPropertyPreviews();

  }catch(x){
    s("po",x);
    e("propertySaveStatus").innerText="Property/photo upload failed: "+(x.detail||x.message||"Unknown error");
  }finally{
    e("savePropertyBtn").disabled=false;
  }
}


async function loadEditProperty(){
  try{
    const d=await jf("/api/properties/"+encodeURIComponent(v("epid")));
    const p=d.property||{};
    e("editFields").style.display="block";
    e("epn").value=p.property_name||"";
    e("ept").value=p.property_type||"";
    e("epc").value=p.city||"";
    e("epl").value=p.location||"";
    e("epa").value=p.available_area_sqft||"";
    e("epf").value=p.floor||"";
    e("epx").value=p.rent_or_sale==="Sale"?"Sale":"Rent";
    e("epnb").value=p.nearby_brands||"";
    e("epsc").value=p.suitable_category||"";
    e("eppk").value=p.parking||"";
    e("epimg").value=p.image_urls||"";
    e("epvid").value=p.video_urls||"";
    e("epbro").value=p.brochure_url||"";
    e("eprem").value=p.remarks||"";

    const badge=e("verifyBadge");
    badge.style.display="block";
    badge.innerText=p.last_verified_label||"Never Verified";
    if(p.verification_due){
      badge.style.background="#fef2f2";
      badge.style.color="#991b1b";
      badge.style.border="1px solid #fecaca";
    }else{
      badge.style.background="#ecfdf5";
      badge.style.color="#065f46";
      badge.style.border="1px solid #a7f3d0";
    }
    s("epo",{status:"loaded",property_id:p.property_id});
  }catch(x){s("epo",x)}
}

async function saveEditProperty(){
  try{
    const pid=v("epid");
    const d=await jf("/api/properties/"+encodeURIComponent(pid),{
      method:"PUT",
      headers:{"Content-Type":"application/json","x-user-name":ROLE},
      body:JSON.stringify({
        property_name:v("epn"),property_type:v("ept")||"NA",city:v("epc")||"NA",location:v("epl")||"NA",
        available_area_sqft:Number(v("epa"))||null,floor:v("epf")||null,rent_or_sale:v("epx"),
        nearby_brands:v("epnb")||null,suitable_category:v("epsc")||null,parking:v("eppk")||null,
        image_urls:v("epimg")||null,video_urls:v("epvid")||null,brochure_url:v("epbro")||null,
        remarks:v("eprem")||null,source:"Manual"
      })
    });
    s("epo",d);
    await loadEditProperty();
  }catch(x){s("epo",x)}
}

async function verifyNow(){
  try{
    const d=await jf("/api/properties/"+encodeURIComponent(v("epid"))+"/verify",{
      method:"POST",
      headers:{"x-user-name":ROLE}
    });
    s("epo",d);
    await loadEditProperty();
  }catch(x){s("epo",x)}
}

async function req(){try{const d=await jf("/api/requirements",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({client_name:v("rc"),city:v("rci"),preferred_locations:v("rl"),minimum_area_sqft:Number(v("rmin"))||null,maximum_area_sqft:Number(v("rmax"))||null,rent_or_sale:v("rx")})});s("ro",d);if(d.requirement_id)e("rid").value=d.requirement_id}catch(x){s("ro",x)}}
async function mt(){try{const d=await jf("/api/match/"+encodeURIComponent(v("rid")),{method:"POST"});s("mo",{status:d.status,matches:d.matches});if(d.whatsapp_draft){e("waBox").style.display="block";e("waText").value=d.whatsapp_draft.message||""}}catch(x){s("mo",x)}}
async function stat(){try{s("so",await jf("/api/status"))}catch(x){s("so",x)}}
function render(rows,target){const g=e(target);if(!rows.length){g.innerHTML="<tr><td>No records yet</td></tr>";return}const c=Object.keys(rows[0]);g.innerHTML="<thead><tr>"+c.map(x=>"<th>"+x+"</th>").join("")+"</tr></thead><tbody>"+rows.map(r=>"<tr>"+c.map(x=>{let val=String(r[x]??"");if(x==="last_verified"){const due=!!r.verification_due;return "<td><span style=\"padding:5px 8px;border-radius:999px;font-weight:700;background:"+(due?"#fef2f2;color:#991b1b":"#ecfdf5;color:#065f46")+"\">"+val+"</span></td>"}return "<td>"+val+"</td>"}).join("")+"</tr>").join("")+"</tbody>"}
async function load(n,target="grid",meta="meta"){try{const d=await jf("/api/database/"+n);e(meta).innerText=d.count+" records";render(d.rows,target)}catch(x){e(meta).innerText=x.detail||x.message||"Error"}}
e("tabs").innerHTML=["properties","requirements","matches","whatsapp_drafts"]
  .map(name=>`<button type="button" data-table="${name}" class="dbTab">${name}</button>`)
  .join("");

document.querySelectorAll(".dbTab").forEach(btn=>{
  btn.addEventListener("click",()=>load(btn.dataset.table,"grid","meta"));
});
if(ROLE=="admin"){
  e("atabs").innerHTML=["sources","ai_jobs","verification","batches","media"]
    .map(name=>`<button type="button" data-admin-table="${name}" class="adminDbTab">${name}</button>`)
    .join("");

  document.querySelectorAll(".adminDbTab").forEach(btn=>{
    btn.addEventListener("click",()=>load(btn.dataset.adminTable,"agrid","ameta"));
  });
}
</script></body></html>'''


def ui_shell(role,title,body):
    admin_link='<a class="navbtn" href="/admin-page">Admin</a>' if role=="admin" else ""
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title>
<style>
*{{box-sizing:border-box}}
body{{font-family:Arial,sans-serif;margin:0;background:#f5f7fb;color:#172033}}
header{{background:#111827;color:#fff;padding:18px 22px;display:flex;justify-content:space-between;gap:15px;align-items:center}}
nav{{background:#fff;border-bottom:1px solid #ddd;padding:10px 16px;display:flex;gap:8px;flex-wrap:wrap}}
.navbtn,.btn{{display:inline-block;padding:10px 14px;border-radius:8px;background:#111827;color:#fff;text-decoration:none;border:0;cursor:pointer;font-size:14px}}
.navbtn.secondary{{background:#374151}}
.wrap{{padding:18px;max-width:1500px;margin:auto}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:15px}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:16px;margin-bottom:15px}}
input,select,textarea{{width:100%;padding:10px;border:1px solid #d1d5db;border-radius:8px;margin:5px 0 10px;font-size:16px}}
table{{border-collapse:collapse;width:100%;font-size:12px}}
th,td{{border-bottom:1px solid #eee;padding:8px;text-align:left;white-space:nowrap}}
.tablebox{{overflow:auto;max-height:70vh}}
.good{{background:#ecfdf5;color:#065f46;padding:8px;border-radius:8px}}
.warn{{background:#fef2f2;color:#991b1b;padding:8px;border-radius:8px}}
.info{{background:#eff6ff;color:#1e40af;padding:8px;border-radius:8px}}
small{{color:#6b7280}}
</style>
</head>
<body>
<header>
<div><b>Property Intelligence</b><br><small style="color:#d1d5db">Reliable Team + Admin Workspace</small></div>
<div>{escape(role.upper())} · <a href="/logout" style="color:white">Logout</a></div>
</header>
<nav>
<a class="navbtn" href="/workspace">Operations</a>
<a class="navbtn" href="/database-page">Database</a>
<a class="navbtn" href="/status-page">Status</a>
{admin_link}
</nav>
<div class="wrap">
<h2>{escape(title)}</h2>
{body}
</div>
</body>
</html>"""

def ui_message(value,kind="info"):
    return f'<div class="{kind}">{escape(str(value))}</div>'

@app.get("/database-page",response_class=HTMLResponse)
def database_page(req:Request,table_name:str=Query("properties"),q:str=Query("")):
    role=need_login(req)
    allowed=["properties","requirements","matches","whatsapp_drafts"]
    if table_name not in allowed:
        table_name="properties"

    table=TABLES[table_name]
    search_term=(q or "").strip()

    with engine.connect() as c:
        if table_name=="properties" and search_term:
            like="%"+search_term+"%"
            result=c.execute(
                text("""SELECT * FROM pi_properties
                        WHERE
                            property_id ILIKE :q OR
                            COALESCE(property_name,'') ILIKE :q OR
                            COALESCE(property_type,'') ILIKE :q OR
                            COALESCE(city,'') ILIKE :q OR
                            COALESCE(location,'') ILIKE :q OR
                            COALESCE(floor,'') ILIKE :q OR
                            COALESCE(rent_or_sale,'') ILIKE :q OR
                            COALESCE(nearby_brands,'') ILIKE :q OR
                            COALESCE(suitable_category,'') ILIKE :q OR
                            COALESCE(owner_name,'') ILIKE :q OR
                            COALESCE(owner_contact,'') ILIKE :q OR
                            COALESCE(broker_name,'') ILIKE :q OR
                            COALESCE(broker_contact,'') ILIKE :q OR
                            COALESCE(remarks,'') ILIKE :q
                        ORDER BY id DESC
                        LIMIT 500"""),
                {"q":like}
            )
        elif table_name=="requirements" and search_term:
            like="%"+search_term+"%"
            result=c.execute(
                text("""SELECT * FROM pi_requirements
                        WHERE
                            requirement_id ILIKE :q OR
                            COALESCE(client_name,'') ILIKE :q OR
                            COALESCE(company_name,'') ILIKE :q OR
                            COALESCE(contact_phone,'') ILIKE :q OR
                            COALESCE(contact_email,'') ILIKE :q OR
                            COALESCE(city,'') ILIKE :q OR
                            COALESCE(preferred_locations,'') ILIKE :q OR
                            COALESCE(rent_or_sale,'') ILIKE :q OR
                            COALESCE(status,'') ILIKE :q
                        ORDER BY id DESC
                        LIMIT 500"""),
                {"q":like}
            )
        else:
            result=c.execute(text("SELECT * FROM "+table+" ORDER BY id DESC LIMIT 500"))

        rows=[]
        for row in result:
            d={}
            for k,v in dict(row._mapping).items():
                if isinstance(v,(date,datetime)):
                    d[k]=v.isoformat()
                elif isinstance(v,Decimal):
                    d[k]=float(v)
                else:
                    d[k]=v
            rows.append(d)

    if table_name=="properties":
        for row in rows:
            verified=row.get("verified_date")
            if not verified:
                row["last_verified"]="Never Verified"
                row["verification_due"]=True
            else:
                vd=date.fromisoformat(verified) if isinstance(verified,str) else verified
                age=(date.today()-vd).days
                row["last_verified"]=f"{verified} ({age} days ago)"
                row["verification_due"]=age>=VERIFICATION_DUE_DAYS

        if role!="admin":
            team_private=PRIVATE-{"verified_date","verified_by"}
            rows=[{k:v for k,v in row.items() if k not in team_private} for row in rows]

    if table_name=="properties":
        for row in rows:
            row_id=row.get("id")
            row["action"]=f'<a class="btn" href="/property/edit-row/{int(row_id)}">Edit</a>' if row_id is not None else ""

    search_form=f"""
    <div class="card">
      <form method="get" action="/database-page">
        <input type="hidden" name="table_name" value="{escape(table_name,quote=True)}">
        <label><b>Search Database</b></label>
        <input name="q" value="{escape(search_term,quote=True)}"
               placeholder="Property ID, name, location, city, broker, phone, nearby brand...">
        <button class="btn" type="submit">Search</button>
        <a class="navbtn secondary" href="/database-page?table_name={escape(table_name)}">Clear</a>
      </form>
      <small>Search is not case-sensitive. Enter any part of a Property ID, name, phone number, location or broker.</small>
    </div>
    """

    tabs=" ".join(
        f'<a class="navbtn secondary" href="/database-page?table_name={escape(name)}">{escape(name.replace("_"," ").title())}</a>'
        for name in allowed
    )

    if not rows:
        table_html="<p>No records yet.</p>"
    else:
        cols=list(rows[0].keys())
        header="".join(f"<th>{escape(str(col))}</th>" for col in cols)
        rendered=[]
        for row in rows:
            cells=[]
            for col in cols:
                val=row.get(col,"")
                if col=="last_verified":
                    cls="warn" if row.get("verification_due") else "good"
                    cells.append(f'<td><span class="{cls}">{escape(str(val))}</span></td>')
                elif col=="action":
                    cells.append(f"<td>{val}</td>")
                else:
                    cells.append(f"<td>{escape(str(val if val is not None else ''))}</td>")
            rendered.append("<tr>"+"".join(cells)+"</tr>")
        table_html=f'<div class="tablebox"><table><thead><tr>{header}</tr></thead><tbody>{"".join(rendered)}</tbody></table></div>'

    result_label=(
        f'<p><b>{len(rows)} results</b> for “{escape(search_term)}”</p>'
        if search_term else
        f'<p><b>{len(rows)} records shown</b></p>'
    )
    return HTMLResponse(ui_shell(role,"Database",search_form+tabs+result_label+table_html))


@app.get("/property/edit-row/{row_id}",response_class=HTMLResponse)
def edit_property_by_row(req:Request,row_id:int):
    role=need_login(req)
    with engine.connect() as c:
        row=c.execute(text("SELECT property_id FROM pi_properties WHERE id=:id"),{"id":row_id}).first()
    if not row:
        return HTMLResponse(ui_shell(role,"Edit Property",ui_message("Property database row not found.","warn")),status_code=404)
    property_id=row._mapping["property_id"]
    if not property_id:
        with engine.begin() as c:
            property_id=make_id("PROP",c)
            c.execute(text("UPDATE pi_properties SET property_id=:pid,updated_at=NOW() WHERE id=:id"),{"pid":property_id,"id":row_id})
    return RedirectResponse("/property/edit/"+quote_plus(str(property_id)),status_code=303)

@app.get("/property/edit/{property_id}",response_class=HTMLResponse)
def edit_property_page(req:Request,property_id:str,message:str=Query("")):
    role=need_login(req)
    with engine.connect() as c:
        row=c.execute(
            text("SELECT * FROM pi_properties WHERE property_id=:pid"),
            {"pid":property_id}
        ).first()

    if not row:
        return HTMLResponse(
            ui_shell(role,"Edit Property",ui_message("Property not found.","warn")),
            status_code=404
        )

    d=dict(row._mapping)

    def fv(name):
        value=d.get(name)
        return escape(str(value if value is not None else ""), quote=True)

    notice=ui_message(message,"good") if message else ""
    body=notice+f"""
    <div class="card">
      <div class="info">
        <b>Property ID:</b> {escape(property_id)}
        <br><small>The Property ID never changes when details are edited.</small>
      </div>

      <form action="/property/edit/{quote_plus(property_id)}" method="post">
        <label>Property Name</label>
        <input name="property_name" value="{fv('property_name')}">

        <label>Property Type</label>
        <input name="property_type" value="{fv('property_type')}">

        <label>City</label>
        <input name="city" value="{fv('city')}">

        <label>Location</label>
        <input name="location" value="{fv('location')}">

        <label>Available Area (sqft)</label>
        <input name="available_area_sqft" type="number" step="any" value="{fv('available_area_sqft')}">

        <label>Minimum Area (sqft)</label>
        <input name="minimum_area_sqft" type="number" step="any" value="{fv('minimum_area_sqft')}">

        <label>Maximum Area (sqft)</label>
        <input name="maximum_area_sqft" type="number" step="any" value="{fv('maximum_area_sqft')}">

        <label>Floor</label>
        <input name="floor" value="{fv('floor')}">

        <label>Rent / Sale</label>
        <input name="rent_or_sale" value="{fv('rent_or_sale')}">

        <label>Nearby Brands</label>
        <textarea name="nearby_brands" rows="2">{escape(str(d.get('nearby_brands') or ''))}</textarea>

        <label>Suitable Category</label>
        <input name="suitable_category" value="{fv('suitable_category')}">

        <label>Parking</label>
        <input name="parking" value="{fv('parking')}">

        <label>Image URLs</label>
        <textarea name="image_urls" rows="3" placeholder="One or more image links">{escape(str(d.get('image_urls') or ''))}</textarea>

        <label>Video URLs</label>
        <textarea name="video_urls" rows="3" placeholder="One or more video links">{escape(str(d.get('video_urls') or ''))}</textarea>

        <label>Brochure URL</label>
        <input name="brochure_url" value="{fv('brochure_url')}">

        <label>Remarks</label>
        <textarea name="remarks" rows="4">{escape(str(d.get('remarks') or ''))}</textarea>

        <button class="btn" type="submit">Save Changes</button>
        <a class="navbtn secondary" href="/database-page?table_name=properties">Back to Database</a>
      </form>
    </div>

    <div class="card">
      <h3>Verification</h3>
      <p><b>Last Verified:</b> {escape(str(d.get('verified_date') or 'Never Verified'))}</p>
      <p><b>Verified By:</b> {escape(str(d.get('verified_by') or ''))}</p>
      <form action="/ui/verify" method="post">
        <input type="hidden" name="property_id" value="{escape(property_id, quote=True)}">
        <button class="btn" type="submit">Verify Today</button>
      </form>
      <small>Saving an edit does not change Last Verified. Use Verify Today only after actual confirmation.</small>
    </div>
    """
    return HTMLResponse(ui_shell(role,"Edit Property",body))


@app.post("/property/edit/{property_id}")
def save_property_edit(
    req:Request,
    property_id:str,
    property_name:str=Form(""),
    property_type:str=Form(""),
    city:str=Form(""),
    location:str=Form(""),
    available_area_sqft:Optional[float]=Form(None),
    minimum_area_sqft:Optional[float]=Form(None),
    maximum_area_sqft:Optional[float]=Form(None),
    floor:str=Form(""),
    rent_or_sale:str=Form(""),
    nearby_brands:str=Form(""),
    suitable_category:str=Form(""),
    parking:str=Form(""),
    image_urls:str=Form(""),
    video_urls:str=Form(""),
    brochure_url:str=Form(""),
    remarks:str=Form("")
):
    role=need_login(req)
    actor=actor_name(req)

    editable={
        "property_name":property_name or None,
        "property_type":property_type or None,
        "city":city or None,
        "location":location or None,
        "available_area_sqft":available_area_sqft,
        "minimum_area_sqft":minimum_area_sqft,
        "maximum_area_sqft":maximum_area_sqft,
        "floor":floor or None,
        "rent_or_sale":rent_or_sale or None,
        "nearby_brands":nearby_brands or None,
        "suitable_category":suitable_category or None,
        "parking":parking or None,
        "image_urls":image_urls or None,
        "video_urls":video_urls or None,
        "brochure_url":brochure_url or None,
        "remarks":remarks or None
    }

    with engine.begin() as c:
        existing=c.execute(
            text("SELECT * FROM pi_properties WHERE property_id=:pid"),
            {"pid":property_id}
        ).first()

        if not existing:
            return HTMLResponse(
                ui_shell(role,"Edit Property",ui_message("Property not found.","warn")),
                status_code=404
            )

        old=dict(existing._mapping)
        changes={}
        for key,new_value in editable.items():
            old_value=old.get(key)
            if isinstance(old_value,Decimal):
                old_value=float(old_value)
            if old_value != new_value:
                changes[key]={"old":old_value,"new":new_value}

        c.execute(
            text("""UPDATE pi_properties SET
                property_name=:property_name,
                property_type=:property_type,
                city=:city,
                location=:location,
                available_area_sqft=:available_area_sqft,
                minimum_area_sqft=:minimum_area_sqft,
                maximum_area_sqft=:maximum_area_sqft,
                floor=:floor,
                rent_or_sale=:rent_or_sale,
                nearby_brands=:nearby_brands,
                suitable_category=:suitable_category,
                parking=:parking,
                image_urls=:image_urls,
                video_urls=:video_urls,
                brochure_url=:brochure_url,
                remarks=:remarks,
                updated_at=NOW()
                WHERE property_id=:property_id"""),
            {**editable,"property_id":property_id}
        )

        if changes:
            c.execute(
                text("""INSERT INTO pi_verification_log
                        (property_id,action,performed_by,notes,old_values,new_values)
                        VALUES(:pid,'EDITED',:actor,:notes,CAST(:oldv AS JSONB),CAST(:newv AS JSONB))"""),
                {
                    "pid":property_id,
                    "actor":actor,
                    "notes":"Property edited directly from database",
                    "oldv":json.dumps({k:v["old"] for k,v in changes.items()},default=str),
                    "newv":json.dumps({k:v["new"] for k,v in changes.items()},default=str)
                }
            )

    result_message="Changes saved successfully." if changes else "No changes were made."
    return RedirectResponse(
        "/property/edit/"+quote_plus(property_id)+"?message="+quote_plus(result_message),
        status_code=303
    )

@app.get("/status-page",response_class=HTMLResponse)
def status_page(req:Request):
    role=need_login(req)
    with engine.connect() as c:
        counts={}
        for label,table in {
            "Properties":"pi_properties",
            "Requirements":"pi_requirements",
            "Sources":"pi_sources",
            "Matches":"pi_matches",
            "AI Jobs":"pi_ai_jobs"
        }.items():
            counts[label]=c.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()

    cards="".join(
        f'<div class="card"><h3>{escape(label)}</h3><div style="font-size:28px;font-weight:700">{count}</div></div>'
        for label,count in counts.items()
    )
    body=f'<div class="grid">{cards}</div><div class="card"><b>Version:</b> {VERSION}<br><b>Gemini configured:</b> {bool(GEMINI_API_KEY)}<br><b>Verification due:</b> {VERIFICATION_DUE_DAYS} days</div>'
    return HTMLResponse(ui_shell(role,"System Status",body))

@app.get("/admin-page",response_class=HTMLResponse)
def admin_page(req:Request,table_name:str=Query("sources")):
    role=need_login(req)
    if role!="admin":
        return HTMLResponse(ui_shell(role,"Admin",ui_message("Admin access required.","warn")),status_code=403)

    allowed=["sources","ai_jobs","verification","batches","media","scan_tiles"]
    if table_name not in allowed:
        table_name="sources"

    with engine.connect() as c:
        if table_name=="media":
            result=c.execute(text("""SELECT id,media_id,property_id,media_type,filename,mime_type,file_size,created_at
                                     FROM pi_property_media ORDER BY id DESC LIMIT 500"""))
        else:
            result=c.execute(text("SELECT * FROM "+TABLES[table_name]+" ORDER BY id DESC LIMIT 500"))
        rows=[]
        for row in result:
            d={}
            for k,v in dict(row._mapping).items():
                if isinstance(v,(date,datetime)):
                    d[k]=v.isoformat()
                elif isinstance(v,Decimal):
                    d[k]=float(v)
                else:
                    d[k]=v
            rows.append(d)

    tabs=" ".join(
        f'<a class="navbtn secondary" href="/admin-page?table_name={escape(name)}">{escape(name.replace("_"," ").title())}</a>'
        for name in allowed
    )

    if not rows:
        table_html="<p>No records yet.</p>"
    else:
        cols=list(rows[0].keys())
        header="".join(f"<th>{escape(str(col))}</th>" for col in cols)
        body="".join(
            "<tr>"+"".join(f"<td>{escape(str(row.get(col,'') if row.get(col,'') is not None else ''))}</td>" for col in cols)+"</tr>"
            for row in rows
        )
        table_html=f'<div class="tablebox"><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>'

    return HTMLResponse(ui_shell(role,"Admin",tabs+f"<p>{len(rows)} records</p>"+table_html))

@app.post("/ui/upload")
async def ui_upload(
    req:Request,
    bg:BackgroundTasks,
    source_type:str=Form("DOCUMENT"),
    source_name:str=Form(""),
    file:UploadFile=File(...)
):
    need_login(req)
    filename=file.filename or "upload.bin"
    ext=os.path.splitext(filename)[1].lower()
    mime=file.content_type or "application/octet-stream"

    mime_map={
        ".jpg":"image/jpeg",".jpeg":"image/jpeg",".png":"image/png",".webp":"image/webp",
        ".pdf":"application/pdf",".csv":"text/csv",".txt":"text/plain"
    }
    if mime in {"application/octet-stream",""} or not mime:
        mime=mime_map.get(ext,"application/octet-stream")

    fd,path=tempfile.mkstemp(suffix=ext or ".bin")
    os.close(fd)
    total=0

    try:
        with open(path,"wb") as output:
            while True:
                chunk=await file.read(1024*1024)
                if not chunk:
                    break
                total+=len(chunk)
                if total>MAX_UPLOAD_MB*1024*1024:
                    raise HTTPException(413,f"File too large. Maximum {MAX_UPLOAD_MB} MB.")
                if (mime=="application/pdf" or ext==".pdf") and total>50*1024*1024:
                    raise HTTPException(413,"PDF too large. Maximum PDF size is 50 MB.")
                output.write(chunk)

        sid=source_row(source_type.upper(),source_name or filename,filename,mime)

        if ext==".csv":
            inserted=0
            with open(path,"r",encoding="utf-8-sig",errors="replace",newline="") as csvfile:
                reader=csv.DictReader(csvfile)
                for row in reader:
                    item={
                        "property_name":row.get("Property name") or row.get("Property Name"),
                        "property_type":row.get("Property type") or row.get("Property Type") or "NA",
                        "city":row.get("City") or "NA",
                        "location":row.get("Location") or "NA",
                        "available_area_sqft":row.get("Available area") or row.get("Available Area") or None,
                        "floor":row.get("Floor"),
                        "rent_or_sale":row.get("Rent/Sale"),
                        "nearby_brands":row.get("Nearby brand") or row.get("Nearby brands"),
                        "suitable_category":row.get("Suitable category"),
                        "parking":row.get("Parking"),
                        "source":"CSV:"+filename
                    }
                    try:
                        if item["available_area_sqft"]:
                            item["available_area_sqft"]=float(str(item["available_area_sqft"]).replace(",",""))
                    except Exception:
                        item["available_area_sqft"]=None

                    if save_property(item,sid)["status"]=="created":
                        inserted+=1

            with engine.begin() as c:
                c.execute(
                    text("UPDATE pi_sources SET ingestion_status='PROCESSED',processed_records=:n,processed_at=NOW() WHERE id=:id"),
                    {"n":inserted,"id":sid}
                )
            try: os.unlink(path)
            except Exception: pass
            message=f"CSV uploaded successfully. {inserted} properties inserted."
        else:
            jid=create_job(sid,"FILE_EXTRACTION",filename)
            bg.add_task(run_file_job,sid,jid,path,mime)
            message=f"Upload accepted. AI processing started in background. Job ID: {jid}"

        return RedirectResponse("/workspace?message="+quote_plus(message),status_code=303)

    except Exception:
        try: os.unlink(path)
        except Exception: pass
        raise

@app.post("/ui/add-property")
def ui_add_property(
    req:Request,
    property_name:str=Form(""),
    property_type:str=Form("NA"),
    city:str=Form("NA"),
    location:str=Form("NA"),
    available_area_sqft:Optional[float]=Form(None),
    floor:str=Form(""),
    rent_or_sale:str=Form("Rent"),
    nearby_brands:str=Form(""),
    suitable_category:str=Form(""),
    parking:str=Form(""),
    image_urls:str=Form(""),
    video_urls:str=Form(""),
    brochure_url:str=Form(""),
    remarks:str=Form("")
):
    need_login(req)
    result=save_property({
        "property_name":property_name or None,
        "property_type":property_type or "NA",
        "city":city or "NA",
        "location":location or "NA",
        "available_area_sqft":available_area_sqft,
        "floor":floor or None,
        "rent_or_sale":rent_or_sale or None,
        "nearby_brands":nearby_brands or None,
        "suitable_category":suitable_category or None,
        "parking":parking or None,
        "image_urls":image_urls or None,
        "video_urls":video_urls or None,
        "brochure_url":brochure_url or None,
        "remarks":remarks or None,
        "source":"Manual"
    })
    message=f"Property {result.get('status')}: {result.get('property_id','')}"
    return RedirectResponse("/workspace?message="+quote_plus(message),status_code=303)

@app.post("/ui/add-requirement")
def ui_add_requirement(
    req:Request,
    client_name:str=Form(""),
    company_name:str=Form(""),
    contact_phone:str=Form(""),
    city:str=Form(""),
    preferred_locations:str=Form(""),
    minimum_area_sqft:Optional[float]=Form(None),
    maximum_area_sqft:Optional[float]=Form(None),
    rent_or_sale:str=Form("Rent")
):
    need_login(req)
    result=save_requirement({
        "client_name":client_name or None,
        "company_name":company_name or None,
        "contact_phone":contact_phone or None,
        "city":city or None,
        "preferred_locations":preferred_locations or None,
        "minimum_area_sqft":minimum_area_sqft,
        "maximum_area_sqft":maximum_area_sqft,
        "rent_or_sale":rent_or_sale or None,
        "source":"Manual"
    })
    message=f"Requirement {result.get('status')}: {result.get('requirement_id','')}"
    return RedirectResponse("/workspace?message="+quote_plus(message),status_code=303)

@app.post("/ui/verify")
def ui_verify(req:Request,property_id:str=Form(...)):
    need_login(req)
    actor=actor_name(req)

    with engine.begin() as c:
        exists=c.execute(text("SELECT 1 FROM pi_properties WHERE property_id=:pid"),{"pid":property_id}).first()
        if not exists:
            return RedirectResponse("/workspace?message="+quote_plus("Property not found"),status_code=303)

        c.execute(
            text("""UPDATE pi_properties
                    SET verified_date=CURRENT_DATE,
                        verified_by=:actor,
                        verification_status='VERIFIED',
                        updated_at=NOW()
                    WHERE property_id=:pid"""),
            {"actor":actor,"pid":property_id}
        )
        c.execute(
            text("""INSERT INTO pi_verification_log(property_id,action,performed_by,notes)
                    VALUES(:pid,'VERIFIED',:actor,'Property verified from server UI')"""),
            {"pid":property_id,"actor":actor}
        )

    return RedirectResponse("/workspace?message="+quote_plus(f"{property_id} verified today"),status_code=303)

@app.post("/ui/match")
def ui_match(req:Request,requirement_id:str=Form(...)):
    need_login(req)

    with engine.begin() as c:
        qrow=c.execute(text("SELECT * FROM pi_requirements WHERE requirement_id=:id"),{"id":requirement_id}).first()
        if not qrow:
            return RedirectResponse("/workspace?message="+quote_plus("Requirement not found"),status_code=303)

        requirement=dict(qrow._mapping)
        props=c.execute(text("SELECT * FROM pi_properties WHERE availability_status='Available'")).fetchall()
        c.execute(text("DELETE FROM pi_matches WHERE requirement_id=:id"),{"id":requirement_id})

        results=[]
        property_map={}
        for row in props:
            prop=dict(row._mapping)
            property_map[prop["property_id"]]=prop
            score=0
            reasons=[]

            if requirement.get("city") and str(requirement["city"]).lower()==str(prop.get("city") or "").lower():
                score+=30
                reasons.append("City")

            if requirement.get("preferred_locations") and str(prop.get("location") or "").lower() in str(requirement["preferred_locations"]).lower():
                score+=30
                reasons.append("Location")

            area=prop.get("available_area_sqft")
            minimum=requirement.get("minimum_area_sqft")
            maximum=requirement.get("maximum_area_sqft")
            if area is not None and (minimum is None or area>=minimum) and (maximum is None or area<=maximum):
                score+=30
                reasons.append("Area")

            if requirement.get("rent_or_sale") and str(requirement["rent_or_sale"]).lower()==str(prop.get("rent_or_sale") or "").lower():
                score+=10
                reasons.append("Rent/Sale")

            results.append({"property_id":prop["property_id"],"score":score,"reasons":reasons})

        results.sort(key=lambda item:item["score"],reverse=True)

        for rank,item in enumerate(results,1):
            c.execute(
                text("""INSERT INTO pi_matches(requirement_id,property_id,match_score,rank,match_reasons,status)
                        VALUES(:rid,:pid,:score,:rank,CAST(:reasons AS JSONB),'READY_FOR_REVIEW')"""),
                {
                    "rid":requirement_id,
                    "pid":item["property_id"],
                    "score":item["score"],
                    "rank":rank,
                    "reasons":json.dumps(item["reasons"])
                }
            )

    top_properties=[property_map[item["property_id"]] for item in results[:5] if item["property_id"] in property_map]
    whatsapp_message,provider=generate_whatsapp_message(requirement,top_properties)
    draft_id=store_whatsapp_draft(requirement,whatsapp_message,provider)

    message=f"Matcher complete. {len(results)} matches. WhatsApp draft #{draft_id} created."
    return RedirectResponse("/workspace?message="+quote_plus(message),status_code=303)

@app.get("/legacy-workspace",response_class=HTMLResponse)
def legacy_workspace(req:Request,message:str=Query("")):
    role=page_role_or_redirect(req)
    if not role:
        return RedirectResponse("/login",status_code=303)

    notice=ui_message(message,"good") if message else ""

    upload_card="""
    <div class="card">
      <h3>Upload Photo / Magazine / PDF / CSV</h3>
      <form action="/ui/upload" method="post" enctype="multipart/form-data">
        <input name="source_name" placeholder="Source name">
        <select name="source_type">
          <option>MAGAZINE</option>
          <option>NEWSPAPER</option>
          <option>PHOTO</option>
          <option>PDF</option>
          <option>CSV</option>
        </select>
        <input name="file" type="file" required>
        <button class="btn" type="submit">Upload</button>
      </form>
      <small>High-recall scanner runs full-page + overlapping tiles in the background for photos and PDFs.</small>
    </div>"""

    property_card="""
    <div class="card">
      <h3>Add Property Manually</h3>
      <form action="/ui/add-property" method="post">
        <input name="property_name" placeholder="Property name / building">
        <input name="property_type" placeholder="Property type">
        <input name="city" placeholder="City">
        <input name="location" placeholder="Location">
        <input name="available_area_sqft" type="number" step="any" placeholder="Available sqft">
        <input name="floor" placeholder="Floor">
        <select name="rent_or_sale"><option>Rent</option><option>Sale</option></select>
        <input name="nearby_brands" placeholder="Nearby brands">
        <input name="suitable_category" placeholder="Suitable category">
        <input name="parking" placeholder="Parking">
        <textarea name="image_urls" rows="2" placeholder="Image URLs"></textarea>
        <textarea name="video_urls" rows="2" placeholder="Video URLs"></textarea>
        <input name="brochure_url" placeholder="Brochure link">
        <textarea name="remarks" rows="3" placeholder="Remarks"></textarea>
        <button class="btn" type="submit">Save Property</button>
      </form>
    </div>"""

    requirement_card="""
    <div class="card">
      <h3>Add Requirement</h3>
      <form action="/ui/add-requirement" method="post">
        <input name="client_name" placeholder="Client name">
        <input name="company_name" placeholder="Company">
        <input name="contact_phone" placeholder="Contact phone">
        <input name="city" placeholder="City">
        <input name="preferred_locations" placeholder="Preferred locations">
        <input name="minimum_area_sqft" type="number" step="any" placeholder="Min sqft">
        <input name="maximum_area_sqft" type="number" step="any" placeholder="Max sqft">
        <select name="rent_or_sale"><option>Rent</option><option>Sale</option></select>
        <button class="btn" type="submit">Save Requirement</button>
      </form>
    </div>"""

    verify_card="""
    <div class="card">
      <h3>Verify Existing Property</h3>
      <form action="/ui/verify" method="post">
        <input name="property_id" placeholder="Property ID" required>
        <button class="btn" type="submit">Verify Today</button>
      </form>
      <small>Verification date and verifier are recorded.</small>
    </div>"""

    match_card="""
    <div class="card">
      <h3>Run Matcher + WhatsApp Draft</h3>
      <form action="/ui/match" method="post">
        <input name="requirement_id" placeholder="Requirement ID" required>
        <button class="btn" type="submit">Match + Create WhatsApp</button>
      </form>
      <small>Draft remains READY_FOR_REVIEW. Nothing is sent automatically.</small>
    </div>"""

    quick_search_card="""
    <div class="card">
      <h3>Find a Property</h3>
      <form action="/database-page" method="get">
        <input type="hidden" name="table_name" value="properties">
        <input name="q" placeholder="Property ID / Location / Name / Phone / Broker">
        <button class="btn" type="submit">Search Property</button>
      </form>
    </div>"""

    body=notice+'<div class="grid">'+quick_search_card+upload_card+property_card+requirement_card+verify_card+match_card+'</div>'
    return HTMLResponse(ui_shell(role,"Operations",body))

@app.get("/")
def root(req:Request):
    return RedirectResponse("/workspace" if get_role(req) else "/login")

# ============================================================================
# AI DEAL INTELLIGENCE OS V3
# Unified Property + Hospitality + Retail Expansion dashboard
# Preserves all legacy Property Intelligence APIs and database tables above.
# ============================================================================

import re as _re
try:
    import httpx as _httpx
except Exception:
    _httpx = None

DEAL_OS_VERSION = "3.0.0"
SERPER_API_KEY = os.getenv("SERPER_API_KEY","").strip()
APOLLO_API_KEY = os.getenv("APOLLO_API_KEY","").strip()
FLOWCONNECT_WEBHOOK_URL = os.getenv("FLOWCONNECT_WEBHOOK_URL","").strip()
FLOWCONNECT_API_URL = os.getenv("FLOWCONNECT_API_URL","").strip()
FLOWCONNECT_API_KEY = os.getenv("FLOWCONNECT_API_KEY","").strip()

DEAL_OS_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_companies(
 id BIGSERIAL PRIMARY KEY,
 company_id VARCHAR(50) UNIQUE NOT NULL,
 division VARCHAR(30) NOT NULL,
 company_name TEXT NOT NULL,
 category TEXT,
 website TEXT,
 linkedin_url TEXT,
 city TEXT,
 target_markets TEXT,
 expansion_status VARCHAR(50) DEFAULT 'DISCOVERED',
 expansion_score NUMERIC(5,2) DEFAULT 0,
 source_name TEXT,
 source_url TEXT,
 source_excerpt TEXT,
 assigned_to TEXT,
 crm_status VARCHAR(50) DEFAULT 'NOT_SYNCED',
 created_at TIMESTAMPTZ DEFAULT NOW(),
 updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ai_contacts(
 id BIGSERIAL PRIMARY KEY,
 contact_id VARCHAR(50) UNIQUE NOT NULL,
 company_id VARCHAR(50),
 full_name TEXT,
 designation TEXT,
 business_email TEXT,
 business_phone TEXT,
 linkedin_url TEXT,
 verification_status VARCHAR(40) DEFAULT 'UNVERIFIED',
 source_url TEXT,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ai_expansion_signals(
 id BIGSERIAL PRIMARY KEY,
 signal_id VARCHAR(50) UNIQUE NOT NULL,
 company_id VARCHAR(50),
 division VARCHAR(30) DEFAULT 'RETAIL',
 title TEXT,
 signal_type VARCHAR(80),
 market TEXT,
 source_name TEXT,
 source_url TEXT,
 published_at TIMESTAMPTZ,
 excerpt TEXT,
 signal_score NUMERIC(5,2) DEFAULT 0,
 status VARCHAR(40) DEFAULT 'NEW',
 created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ai_followups(
 id BIGSERIAL PRIMARY KEY,
 followup_id VARCHAR(50) UNIQUE NOT NULL,
 entity_type VARCHAR(40),
 entity_id VARCHAR(50),
 division VARCHAR(30),
 channel VARCHAR(30),
 due_at TIMESTAMPTZ,
 status VARCHAR(40) DEFAULT 'DUE',
 assigned_to TEXT,
 notes TEXT,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS ai_meetings(
 id BIGSERIAL PRIMARY KEY,
 meeting_id VARCHAR(50) UNIQUE NOT NULL,
 entity_type VARCHAR(40),
 entity_id VARCHAR(50),
 division VARCHAR(30),
 meeting_at TIMESTAMPTZ,
 status VARCHAR(40) DEFAULT 'SCHEDULED',
 owner TEXT,
 notes TEXT,
 created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ai_bot_runs(
 id BIGSERIAL PRIMARY KEY,
 run_id VARCHAR(50) UNIQUE NOT NULL,
 bot_name TEXT NOT NULL,
 division VARCHAR(30),
 status VARCHAR(40) DEFAULT 'RUNNING',
 summary TEXT,
 records_found INTEGER DEFAULT 0,
 records_created INTEGER DEFAULT 0,
 error_message TEXT,
 started_at TIMESTAMPTZ DEFAULT NOW(),
 completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS ai_activity_ledger(
 id BIGSERIAL PRIMARY KEY,
 event_id VARCHAR(50) UNIQUE NOT NULL,
 actor_type VARCHAR(30) DEFAULT 'AI',
 actor_name TEXT,
 division VARCHAR(30),
 action TEXT NOT NULL,
 entity_type VARCHAR(40),
 entity_id VARCHAR(50),
 summary TEXT,
 status VARCHAR(40) DEFAULT 'SUCCESS',
 created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ai_crm_sync(
 id BIGSERIAL PRIMARY KEY,
 sync_id VARCHAR(50) UNIQUE NOT NULL,
 entity_type VARCHAR(40),
 entity_id VARCHAR(50),
 direction VARCHAR(30),
 target VARCHAR(50) DEFAULT 'FLOWCONNECT',
 status VARCHAR(40) DEFAULT 'PENDING',
 request_payload JSONB,
 response_payload JSONB,
 error_message TEXT,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ai_companies_division ON ai_companies(division);
CREATE INDEX IF NOT EXISTS idx_ai_companies_score ON ai_companies(expansion_score DESC);
CREATE INDEX IF NOT EXISTS idx_ai_signals_company ON ai_expansion_signals(company_id);
CREATE INDEX IF NOT EXISTS idx_ai_activity_created ON ai_activity_ledger(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_followups_due ON ai_followups(status,due_at);
"""

@app.on_event("startup")
def deal_os_startup():
    with engine.begin() as c:
        for stmt in [x.strip() for x in DEAL_OS_SCHEMA.split(";") if x.strip()]:
            c.execute(text(stmt))

def _new_code(prefix):
    return f"{prefix}-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"

def _log_activity(actor_name, division, action, entity_type=None, entity_id=None, summary=None, status="SUCCESS"):
    eid=_new_code("EVT")
    with engine.begin() as c:
        c.execute(text("""INSERT INTO ai_activity_ledger
            (event_id,actor_type,actor_name,division,action,entity_type,entity_id,summary,status)
            VALUES(:e,'AI',:a,:d,:ac,:et,:ei,:s,:st)"""),
            {"e":eid,"a":actor_name,"d":division,"ac":action,"et":entity_type,"ei":entity_id,"s":summary,"st":status})
    return eid

def _retail_signal_score(title, snippet):
    s=(str(title or "")+" "+str(snippet or "")).lower()
    score=15
    weights=[
        (["delhi ncr","delhi","gurgaon","gurugram","noida","faridabad","ghaziabad"],25),
        (["expansion","expand","growth plan","rollout"],20),
        (["new store","new stores","store opening","outlet","flagship"],20),
        (["lease","leasing","retail space","mall","high street"],10),
        (["india entry","enter india","north india"],10),
    ]
    for terms,pts in weights:
        if any(t in s for t in terms): score+=pts
    return min(100,score)

def _extract_company_guess(title):
    title=str(title or "").strip()
    # Conservative heuristic: company/brand text before common news verbs.
    parts=_re.split(r"\s+(?:plans|plan|to|opens|open|launches|launch|expands|expand|eyes|targets|set to)\s+",title,1,flags=_re.I)
    guess=(parts[0] if parts else title).strip(" -:|")
    return guess[:180] or "Unknown Retail Company"

class CompanyInput(BaseModel):
    division: Literal["PROPERTY","HOSPITALITY","RETAIL"]="RETAIL"
    company_name: str
    category: Optional[str]=None
    website: Optional[str]=None
    linkedin_url: Optional[str]=None
    city: Optional[str]=None
    target_markets: Optional[str]=None
    expansion_status: Optional[str]="DISCOVERED"
    expansion_score: Optional[float]=0
    source_name: Optional[str]="Manual"
    source_url: Optional[str]=None
    source_excerpt: Optional[str]=None
    assigned_to: Optional[str]=None

class ContactInput(BaseModel):
    company_id: Optional[str]=None
    full_name: Optional[str]=None
    designation: Optional[str]=None
    business_email: Optional[str]=None
    business_phone: Optional[str]=None
    linkedin_url: Optional[str]=None
    verification_status: Optional[str]="UNVERIFIED"
    source_url: Optional[str]=None

@app.post("/api/deal-os/companies")
def deal_add_company(p:CompanyInput,req:Request):
    need_login(req)
    cid=_new_code("CMP")
    with engine.begin() as c:
        c.execute(text("""INSERT INTO ai_companies(company_id,division,company_name,category,website,linkedin_url,city,
        target_markets,expansion_status,expansion_score,source_name,source_url,source_excerpt,assigned_to)
        VALUES(:id,:division,:company_name,:category,:website,:linkedin_url,:city,:target_markets,:expansion_status,
        :expansion_score,:source_name,:source_url,:source_excerpt,:assigned_to)"""),{"id":cid,**p.model_dump()})
    _log_activity("Dashboard",p.division,"COMPANY_CREATED","company",cid,p.company_name)
    return {"status":"created","company_id":cid}

@app.post("/api/deal-os/contacts")
def deal_add_contact(p:ContactInput,req:Request):
    need_login(req)
    cid=_new_code("CON")
    with engine.begin() as c:
        c.execute(text("""INSERT INTO ai_contacts(contact_id,company_id,full_name,designation,business_email,business_phone,
        linkedin_url,verification_status,source_url)
        VALUES(:id,:company_id,:full_name,:designation,:business_email,:business_phone,:linkedin_url,:verification_status,:source_url)"""),
        {"id":cid,**p.model_dump()})
    _log_activity("Contact Intelligence","RETAIL","CONTACT_CREATED","contact",cid,p.full_name or p.designation)
    return {"status":"created","contact_id":cid}

@app.get("/api/deal-os/overview")
def deal_overview(req:Request):
    need_login(req)
    with engine.connect() as c:
        def one(sql,params=None):
            return c.execute(text(sql),params or {}).scalar_one()
        data={
            "properties":one("SELECT COUNT(*) FROM pi_properties"),
            "requirements":one("SELECT COUNT(*) FROM pi_requirements"),
            "matches":one("SELECT COUNT(*) FROM pi_matches"),
            "hospitality_companies":one("SELECT COUNT(*) FROM ai_companies WHERE division='HOSPITALITY'"),
            "retail_companies":one("SELECT COUNT(*) FROM ai_companies WHERE division='RETAIL'"),
            "hot_retail":one("SELECT COUNT(*) FROM ai_companies WHERE division='RETAIL' AND expansion_score>=80"),
            "signals":one("SELECT COUNT(*) FROM ai_expansion_signals"),
            "contacts":one("SELECT COUNT(*) FROM ai_contacts"),
            "followups_due":one("SELECT COUNT(*) FROM ai_followups WHERE status='DUE' AND (due_at IS NULL OR due_at<=NOW())"),
            "crm_pending":one("SELECT COUNT(*) FROM ai_crm_sync WHERE status IN ('PENDING','FAILED')")
        }
        # Operational exceptions
        data["exceptions"] = (
            one("SELECT COUNT(*) FROM pi_requirements r WHERE NOT EXISTS (SELECT 1 FROM pi_matches m WHERE m.requirement_id=r.requirement_id)")
            + one("SELECT COUNT(*) FROM ai_companies WHERE expansion_score>=80 AND COALESCE(assigned_to,'')=''")
            + data["crm_pending"]
        )
    return {"status":"ok","data":data}

@app.get("/api/deal-os/companies")
def deal_companies(req:Request,division:str=Query("RETAIL"),limit:int=Query(100,ge=1,le=500)):
    need_login(req)
    division=division.upper()
    with engine.connect() as c:
        rows=c.execute(text("""SELECT * FROM ai_companies WHERE division=:d
                              ORDER BY expansion_score DESC,created_at DESC LIMIT :lim"""),
                       {"d":division,"lim":limit}).fetchall()
    return {"status":"ok","rows":[{k:serialize_db_value(v) for k,v in dict(r._mapping).items()} for r in rows]}

@app.get("/api/deal-os/signals")
def deal_signals(req:Request,limit:int=Query(100,ge=1,le=500)):
    need_login(req)
    with engine.connect() as c:
        rows=c.execute(text("""SELECT s.*,c.company_name,c.category
                              FROM ai_expansion_signals s LEFT JOIN ai_companies c ON c.company_id=s.company_id
                              ORDER BY s.created_at DESC LIMIT :lim"""),{"lim":limit}).fetchall()
    return {"status":"ok","rows":[{k:serialize_db_value(v) for k,v in dict(r._mapping).items()} for r in rows]}

@app.get("/api/deal-os/activity")
def deal_activity(req:Request,limit:int=Query(100,ge=1,le=500)):
    need_login(req)
    with engine.connect() as c:
        rows=c.execute(text("SELECT * FROM ai_activity_ledger ORDER BY created_at DESC LIMIT :lim"),{"lim":limit}).fetchall()
    return {"status":"ok","rows":[{k:serialize_db_value(v) for k,v in dict(r._mapping).items()} for r in rows]}

@app.get("/api/deal-os/bot-runs")
def deal_bot_runs(req:Request,limit:int=Query(50,ge=1,le=200)):
    need_login(req)
    with engine.connect() as c:
        rows=c.execute(text("SELECT * FROM ai_bot_runs ORDER BY started_at DESC LIMIT :lim"),{"lim":limit}).fetchall()
    return {"status":"ok","rows":[{k:serialize_db_value(v) for k,v in dict(r._mapping).items()} for r in rows]}

@app.get("/api/deal-os/exceptions")
def deal_exceptions(req:Request):
    need_login(req)
    rows=[]
    with engine.connect() as c:
        unmatched=c.execute(text("""SELECT requirement_id,company_name,client_name,city,preferred_locations,created_at
            FROM pi_requirements r WHERE NOT EXISTS
            (SELECT 1 FROM pi_matches m WHERE m.requirement_id=r.requirement_id)
            ORDER BY created_at DESC LIMIT 50""")).fetchall()
        for r in unmatched:
            d=dict(r._mapping); rows.append({"type":"REQUIREMENT_WITHOUT_MATCH","severity":"HIGH",
                "entity_id":d["requirement_id"],"summary":(d.get("company_name") or d.get("client_name") or "Requirement")+" has no generated match"})
        hot=c.execute(text("""SELECT company_id,company_name,expansion_score FROM ai_companies
            WHERE expansion_score>=80 AND COALESCE(assigned_to,'')='' ORDER BY expansion_score DESC LIMIT 50""")).fetchall()
        for r in hot:
            d=dict(r._mapping); rows.append({"type":"HOT_LEAD_UNASSIGNED","severity":"HIGH","entity_id":d["company_id"],
                                             "summary":f"{d['company_name']} is hot ({float(d['expansion_score'] or 0):.0f}) but unassigned"})
        failed=c.execute(text("""SELECT sync_id,entity_id,error_message FROM ai_crm_sync
            WHERE status='FAILED' ORDER BY created_at DESC LIMIT 50""")).fetchall()
        for r in failed:
            d=dict(r._mapping); rows.append({"type":"CRM_SYNC_FAILED","severity":"MEDIUM","entity_id":d["entity_id"],
                                             "summary":d.get("error_message") or "FlowConnect sync failed"})
    return {"status":"ok","rows":rows[:100]}

def _serper_search(query,num=10):
    if not SERPER_API_KEY:
        raise RuntimeError("SERPER_API_KEY is not configured in Railway Variables.")
    if _httpx is None:
        raise RuntimeError("httpx dependency missing.")
    r=_httpx.post("https://google.serper.dev/search",
        headers={"X-API-KEY":SERPER_API_KEY,"Content-Type":"application/json"},
        json={"q":query,"num":num},timeout=30.0)
    r.raise_for_status()
    return r.json()

@app.post("/api/deal-os/retail-scout/run")
def retail_scout(req:Request):
    need_login(req)
    run_id=_new_code("RUN")
    with engine.begin() as c:
        c.execute(text("""INSERT INTO ai_bot_runs(run_id,bot_name,division,status,summary)
                         VALUES(:id,'Retail Expansion Scout','RETAIL','RUNNING','Searching public expansion signals')"""),{"id":run_id})
    queries=[
        '"Delhi NCR" retail expansion new stores brand',
        'Gurugram OR Gurgaon retail brand expansion store opening',
        'Noida retail brand expansion new store',
        'India retail "expansion plans" stores 2026',
        'site:linkedin.com/company retail expansion Delhi NCR',
        'retail news India brand opening stores Delhi Gurgaon Noida'
    ]
    found=created=0
    errors=[]
    try:
        for q in queries:
            try:
                data=_serper_search(q,10)
                for item in data.get("organic",[]):
                    found+=1
                    title=item.get("title") or ""
                    link=item.get("link") or ""
                    snippet=item.get("snippet") or ""
                    score=_retail_signal_score(title,snippet)
                    company=_extract_company_guess(title)
                    # Do not duplicate exact source URLs.
                    with engine.begin() as c:
                        exists=c.execute(text("SELECT 1 FROM ai_expansion_signals WHERE source_url=:u LIMIT 1"),{"u":link}).first()
                        if exists: continue
                        row=c.execute(text("""SELECT company_id FROM ai_companies
                            WHERE division='RETAIL' AND LOWER(company_name)=LOWER(:n) LIMIT 1"""),{"n":company}).first()
                        if row:
                            company_id=row[0]
                            c.execute(text("""UPDATE ai_companies SET expansion_score=GREATEST(expansion_score,:s),
                                              source_url=COALESCE(source_url,:u),source_excerpt=COALESCE(source_excerpt,:x),
                                              updated_at=NOW() WHERE company_id=:id"""),
                                      {"s":score,"u":link,"x":snippet,"id":company_id})
                        else:
                            company_id=_new_code("CMP")
                            c.execute(text("""INSERT INTO ai_companies(company_id,division,company_name,category,target_markets,
                                expansion_status,expansion_score,source_name,source_url,source_excerpt)
                                VALUES(:id,'RETAIL',:n,'Retail','Delhi NCR','SIGNAL_DETECTED',:s,'Public Web / Retail News',:u,:x)"""),
                                      {"id":company_id,"n":company,"s":score,"u":link,"x":snippet})
                        signal_id=_new_code("SIG")
                        c.execute(text("""INSERT INTO ai_expansion_signals(signal_id,company_id,division,title,signal_type,
                            market,source_name,source_url,excerpt,signal_score)
                            VALUES(:sid,:cid,'RETAIL',:t,'EXPANSION_SIGNAL','Delhi NCR','Public Web / Retail News',:u,:x,:s)"""),
                                  {"sid":signal_id,"cid":company_id,"t":title,"u":link,"x":snippet,"s":score})
                        created+=1
                    _log_activity("Retail Expansion Scout","RETAIL","EXPANSION_SIGNAL_FOUND","company",company_id,
                                  f"{company} | score {score}")
            except Exception as ex:
                errors.append(f"{q}: {ex}")
        status="COMPLETED" if created or not errors else "FAILED"
        with engine.begin() as c:
            c.execute(text("""UPDATE ai_bot_runs SET status=:st,records_found=:f,records_created=:cr,
                summary=:s,error_message=:e,completed_at=NOW() WHERE run_id=:id"""),
                {"st":status,"f":found,"cr":created,"s":f"Found {found}; created {created} new expansion signals",
                 "e":" | ".join(errors[:4]) if errors else None,"id":run_id})
        return {"status":status,"run_id":run_id,"found":found,"created":created,"errors":errors[:4]}
    except Exception as ex:
        with engine.begin() as c:
            c.execute(text("UPDATE ai_bot_runs SET status='FAILED',error_message=:e,completed_at=NOW() WHERE run_id=:id"),
                      {"e":str(ex),"id":run_id})
        raise HTTPException(500,str(ex))

@app.post("/api/deal-os/flowconnect/sync/{entity_type}/{entity_id}")
def flowconnect_sync(entity_type:str,entity_id:str,req:Request):
    need_login(req)
    sync_id=_new_code("SYNC")
    payload={"entity_type":entity_type,"entity_id":entity_id,"source":"AI_DEAL_INTELLIGENCE_OS"}
    with engine.begin() as c:
        c.execute(text("""INSERT INTO ai_crm_sync(sync_id,entity_type,entity_id,direction,target,status,request_payload)
            VALUES(:s,:et,:ei,'OUTBOUND','FLOWCONNECT','PENDING',CAST(:p AS JSONB))"""),
                  {"s":sync_id,"et":entity_type,"ei":entity_id,"p":json.dumps(payload)})
    if not FLOWCONNECT_WEBHOOK_URL:
        _log_activity("FlowConnect Sync","SYSTEM","CRM_SYNC_QUEUED",entity_type,entity_id,
                      "Webhook URL not configured","PENDING")
        return {"status":"PENDING_CONFIGURATION","sync_id":sync_id,
                "message":"Add FLOWCONNECT_WEBHOOK_URL to Railway Variables to activate live sync."}
    if _httpx is None:
        raise HTTPException(500,"httpx dependency missing")
    try:
        headers={"Content-Type":"application/json"}
        if FLOWCONNECT_API_KEY: headers["Authorization"]="Bearer "+FLOWCONNECT_API_KEY
        r=_httpx.post(FLOWCONNECT_WEBHOOK_URL,json=payload,headers=headers,timeout=30.0)
        ok=200 <= r.status_code < 300
        with engine.begin() as c:
            c.execute(text("""UPDATE ai_crm_sync SET status=:st,response_payload=CAST(:rp AS JSONB),
                error_message=:er,completed_at=NOW() WHERE sync_id=:id"""),
                {"st":"COMPLETED" if ok else "FAILED","rp":json.dumps({"status_code":r.status_code,"text":r.text[:2000]}),
                 "er":None if ok else r.text[:1000],"id":sync_id})
        _log_activity("FlowConnect Sync","SYSTEM","CRM_SYNC",entity_type,entity_id,
                      f"HTTP {r.status_code}","SUCCESS" if ok else "FAILED")
        return {"status":"COMPLETED" if ok else "FAILED","sync_id":sync_id,"http_status":r.status_code}
    except Exception as ex:
        with engine.begin() as c:
            c.execute(text("UPDATE ai_crm_sync SET status='FAILED',error_message=:e,completed_at=NOW() WHERE sync_id=:id"),
                      {"e":str(ex),"id":sync_id})
        raise HTTPException(502,str(ex))

@app.post("/api/deal-os/flowconnect/inbound")
async def flowconnect_inbound(req:Request):
    # Can be called by FlowConnect/webhook provider. Protect with API key when configured.
    if FLOWCONNECT_API_KEY:
        supplied=(req.headers.get("authorization") or "").replace("Bearer ","").strip()
        if not hmac.compare_digest(supplied,FLOWCONNECT_API_KEY):
            raise HTTPException(401,"Invalid FlowConnect integration key")
    payload=await req.json()
    _log_activity("FlowConnect","SYSTEM","CRM_INBOUND_EVENT",
                  str(payload.get("entity_type") or "event"),str(payload.get("entity_id") or ""),
                  json.dumps(payload)[:1500])
    return {"status":"accepted"}

_DEAL_CSS = """
:root{--bg:#f4f7fb;--card:#fff;--ink:#152033;--muted:#6c7a90;--line:#e5eaf1;--nav:#0e1b2b;--accent:#1677ff;--good:#0c9b64;--warn:#e29315;--bad:#d94949}
*{box-sizing:border-box}body{margin:0;background:var(--bg);font-family:Inter,Arial,sans-serif;color:var(--ink)}
.shell{display:grid;grid-template-columns:250px 1fr;min-height:100vh}.side{background:var(--nav);color:white;padding:20px 14px;position:sticky;top:0;height:100vh;overflow:auto}
.brand{font-weight:800;font-size:19px;padding:4px 10px 18px}.brand small{display:block;color:#94a5b9;font-weight:500;font-size:11px;margin-top:5px}
.group{font-size:10px;letter-spacing:1.2px;color:#8193a9;margin:18px 10px 7px}.nav{width:100%;text-align:left;border:0;background:transparent;color:#d7e1ec;padding:10px;border-radius:8px;cursor:pointer;font-size:13px}.nav:hover,.nav.active{background:#19314c;color:#fff}
.main{min-width:0}.top{height:68px;background:#fff;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;padding:0 24px;position:sticky;top:0;z-index:5}.content{padding:24px;max-width:1600px;margin:auto}
.page{display:none}.page.active{display:block}.title{font-size:25px;font-weight:800;margin:0}.sub{color:var(--muted);font-size:13px;margin-top:4px}
.kpis{display:grid;grid-template-columns:repeat(6,minmax(130px,1fr));gap:12px;margin:20px 0}.kpi,.card{background:var(--card);border:1px solid var(--line);border-radius:12px;box-shadow:0 2px 9px rgba(18,37,63,.03)}.kpi{padding:15px}.kpi b{display:block;font-size:25px;margin-top:8px}.kpi span{font-size:11px;color:var(--muted)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.card{padding:17px;margin-bottom:16px}.card h3{margin:0 0 12px;font-size:15px}
.btn{background:var(--accent);color:#fff;border:0;border-radius:8px;padding:10px 13px;cursor:pointer;font-weight:700}.btn.secondary{background:#eef3f9;color:#24374f}.btn.good{background:var(--good)}.btn.warn{background:var(--warn)}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}.tablewrap{overflow:auto;border:1px solid var(--line);border-radius:10px;background:white}table{border-collapse:collapse;width:100%;font-size:12px}th,td{padding:10px;border-bottom:1px solid #eef1f5;text-align:left;white-space:nowrap}th{background:#f8fafc;color:#5c6c80;position:sticky;top:0}.score{font-weight:800}.hot{color:#d33}.goodtxt{color:var(--good)}.badge{display:inline-block;border-radius:100px;padding:4px 8px;background:#eef3f8;font-size:10px;font-weight:800}
form.gridform{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}input,select,textarea{width:100%;padding:10px;border:1px solid #dbe2ea;border-radius:8px;background:white}textarea{min-height:70px}.full{grid-column:1/-1}
.funnel{display:flex;gap:8px;align-items:stretch;overflow:auto}.step{min-width:130px;background:#f8fafc;border:1px solid var(--line);border-radius:9px;padding:12px}.step b{font-size:20px;display:block}
.exception{border-left:4px solid var(--bad);padding:10px 12px;background:#fff6f6;border-radius:6px;margin:8px 0}
.activity{padding:10px 0;border-bottom:1px solid #eef1f5}.activity b{font-size:12px}.activity small{display:block;color:var(--muted);margin-top:4px}
@media(max-width:1100px){.kpis{grid-template-columns:repeat(3,1fr)}.grid2,.grid3{grid-template-columns:1fr}}@media(max-width:760px){.shell{grid-template-columns:1fr}.side{position:relative;height:auto}.kpis{grid-template-columns:repeat(2,1fr)}.top{position:relative}.content{padding:14px}}
"""

_DEAL_JS = r"""
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
async function api(url,opt={}){const r=await fetch(url,opt);const t=await r.text();let d={};try{d=JSON.parse(t)}catch(e){d={message:t}}if(!r.ok)throw new Error(d.message||d.detail||t||('HTTP '+r.status));return d}
function nav(id){$$('.page').forEach(x=>x.classList.remove('active'));$$('.nav').forEach(x=>x.classList.remove('active'));$('#'+id).classList.add('active');document.querySelector('[data-page="'+id+'"]')?.classList.add('active');if(id==='retail')loadRetail();if(id==='hospitality')loadCompanies('HOSPITALITY','hospitalityRows');if(id==='activity')loadActivity();if(id==='exceptions')loadExceptions();if(id==='bots')loadBots()}
$$('.nav').forEach(b=>b.onclick=()=>nav(b.dataset.page));
function n(v){return Number(v||0).toLocaleString()}
async function overview(){let d=(await api('/api/deal-os/overview')).data;for(const k in d){let el=document.querySelector('[data-kpi="'+k+'"]');if(el)el.textContent=n(d[k])}
$('#funnel').innerHTML=[
['Properties',d.properties],['Requirements',d.requirements],['Matches',d.matches],['Retail Signals',d.signals],['Hot Retail',d.hot_retail],['Follow-ups',d.followups_due]
].map(x=>`<div class="step"><span>${x[0]}</span><b>${n(x[1])}</b></div>`).join('');
await loadActivity(8);await loadExceptions(6)}
function trCompany(x){return `<tr><td><b>${esc(x.company_name)}</b></td><td>${esc(x.category||'')}</td><td>${esc(x.target_markets||x.city||'')}</td><td class="score ${Number(x.expansion_score)>=80?'hot':''}">${Number(x.expansion_score||0).toFixed(0)}</td><td><span class="badge">${esc(x.expansion_status||'')}</span></td><td>${esc(x.assigned_to||'Unassigned')}</td><td>${x.source_url?`<a target="_blank" href="${escAttr(x.source_url)}">Source</a>`:''}</td><td><button class="btn secondary" onclick="syncCRM('company','${escAttr(x.company_id)}')">Sync CRM</button></td></tr>`}
async function loadCompanies(div,target){let d=await api('/api/deal-os/companies?division='+encodeURIComponent(div));$('#'+target).innerHTML=d.rows.map(trCompany).join('')||'<tr><td colspan="8">No records yet.</td></tr>'}
async function loadRetail(){await loadCompanies('RETAIL','retailRows');let d=await api('/api/deal-os/signals');$('#signalRows').innerHTML=d.rows.map(x=>`<tr><td>${esc(x.company_name||'')}</td><td>${esc(x.title||'')}</td><td class="score">${Number(x.signal_score||0).toFixed(0)}</td><td>${esc(x.market||'')}</td><td>${x.source_url?`<a target="_blank" href="${escAttr(x.source_url)}">Open</a>`:''}</td><td>${fmt(x.created_at)}</td></tr>`).join('')||'<tr><td colspan="6">Run Retail Scout to collect expansion signals.</td></tr>'}
async function runScout(){let b=$('#runScout');b.disabled=true;b.textContent='Running...';try{let d=await api('/api/deal-os/retail-scout/run',{method:'POST'});alert(`Retail Scout: ${d.status}\nFound: ${d.found}\nNew signals: ${d.created}`);await loadRetail();await overview()}catch(e){alert(e.message)}finally{b.disabled=false;b.textContent='Run Retail Expansion Scout'}}
async function syncCRM(t,id){try{let d=await api(`/api/deal-os/flowconnect/sync/${t}/${id}`,{method:'POST'});alert(d.message||('CRM sync: '+d.status));await overview()}catch(e){alert(e.message)}}
async function loadActivity(limit=100){let d=await api('/api/deal-os/activity?limit='+limit);let html=d.rows.map(x=>`<div class="activity"><b>${esc(x.actor_name||'System')} · ${esc(x.action)}</b><small>${esc(x.division||'SYSTEM')} · ${esc(x.summary||'')} · ${fmt(x.created_at)}</small></div>`).join('')||'<p>No activity yet.</p>';let a=$('#activityFeed');if(a)a.innerHTML=html;let b=$('#activityRows');if(b)b.innerHTML=d.rows.map(x=>`<tr><td>${fmt(x.created_at)}</td><td>${esc(x.actor_name||'')}</td><td>${esc(x.division||'')}</td><td>${esc(x.action)}</td><td>${esc(x.entity_id||'')}</td><td>${esc(x.summary||'')}</td><td>${esc(x.status||'')}</td></tr>`).join('')}
async function loadExceptions(limit=100){let d=await api('/api/deal-os/exceptions');let rows=d.rows.slice(0,limit);let html=rows.map(x=>`<div class="exception"><b>${esc(x.type)}</b><div>${esc(x.summary)}</div><small>${esc(x.entity_id||'')}</small></div>`).join('')||'<p class="goodtxt">No current exceptions.</p>';let a=$('#exceptionFeed');if(a)a.innerHTML=html;let b=$('#exceptionRows');if(b)b.innerHTML=d.rows.map(x=>`<tr><td>${esc(x.severity)}</td><td>${esc(x.type)}</td><td>${esc(x.entity_id||'')}</td><td>${esc(x.summary)}</td></tr>`).join('')}
async function loadBots(){let d=await api('/api/deal-os/bot-runs');$('#botRows').innerHTML=d.rows.map(x=>`<tr><td>${esc(x.bot_name)}</td><td>${esc(x.division||'')}</td><td>${esc(x.status)}</td><td>${n(x.records_found)}</td><td>${n(x.records_created)}</td><td>${fmt(x.started_at)}</td><td>${esc(x.summary||x.error_message||'')}</td></tr>`).join('')||'<tr><td colspan="7">No bot runs yet.</td></tr>'}
function esc(x){return String(x??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}function escAttr(x){return esc(x)}function fmt(x){return x?new Date(x).toLocaleString():'-'}
async function addCompany(ev,div){ev.preventDefault();let f=new FormData(ev.target);let body=Object.fromEntries(f.entries());body.division=div;body.expansion_score=Number(body.expansion_score||0);try{await api('/api/deal-os/companies',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});ev.target.reset();await (div==='RETAIL'?loadRetail():loadCompanies('HOSPITALITY','hospitalityRows'));await overview();alert('Saved')}catch(e){alert(e.message)}}
overview().catch(e=>console.error(e));
"""

def _deal_page(role):
    admin_badge = "ADMIN" if role=="admin" else "TEAM"
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Deal Intelligence OS</title><style>{_DEAL_CSS}</style></head><body>
<div class="shell">
<aside class="side"><div class="brand">AI Deal Intelligence OS<small>Property · Hospitality · Retail</small></div>
<div class="group">COMMAND</div><button class="nav active" data-page="command">▣ Command Centre</button><button class="nav" data-page="activity">◎ Live AI Activity</button><button class="nav" data-page="exceptions">⚠ Exception Centre</button>
<div class="group">DIVISIONS</div><button class="nav" data-page="property">⌂ Property Intelligence</button><button class="nav" data-page="hospitality">◆ Hospitality Intelligence</button><button class="nav" data-page="retail">◈ Retail Expansion</button>
<div class="group">AI & CONTROL</div><button class="nav" data-page="bots">⚡ Bot Control Room</button><button class="nav" data-page="crm">⇄ FlowConnect Sync</button>
<div class="group">LEGACY</div><a class="nav" style="display:block;text-decoration:none" href="/legacy-workspace">Original Operations Workspace</a><a class="nav" style="display:block;text-decoration:none" href="/database-page">Original Database</a>
</aside>
<main class="main"><header class="top"><div><b>Unified Delhi NCR Deal Intelligence</b><div class="sub">Management command centre</div></div><div>{admin_badge} · <a href="/logout">Logout</a></div></header>
<div class="content">
<section class="page active" id="command"><h1 class="title">Command Centre</h1><div class="sub">One dashboard for Property, Hospitality and Retail expansion intelligence.</div>
<div class="kpis">
<div class="kpi"><span>PROPERTIES</span><b data-kpi="properties">0</b></div><div class="kpi"><span>REQUIREMENTS</span><b data-kpi="requirements">0</b></div><div class="kpi"><span>MATCHES</span><b data-kpi="matches">0</b></div><div class="kpi"><span>RETAIL COMPANIES</span><b data-kpi="retail_companies">0</b></div><div class="kpi"><span>HOSPITALITY</span><b data-kpi="hospitality_companies">0</b></div><div class="kpi"><span>EXCEPTIONS</span><b data-kpi="exceptions">0</b></div>
</div>
<div class="card"><h3>Deal Intelligence Funnel</h3><div id="funnel" class="funnel"></div></div>
<div class="grid2"><div class="card"><h3>Priority Exceptions</h3><div id="exceptionFeed"></div><button class="btn secondary" onclick="nav('exceptions')">Open Exception Centre</button></div><div class="card"><h3>Live AI Activity</h3><div id="activityFeed"></div><button class="btn secondary" onclick="nav('activity')">Open Audit Ledger</button></div></div>
</section>

<section class="page" id="property"><h1 class="title">Property Intelligence</h1><div class="sub">Your existing working inventory, OCR/extraction, requirements and matcher remain intact.</div>
<div class="grid3"><div class="card"><h3>Existing Operations</h3><p>Upload magazine/PDF/photo/CSV, add inventory and requirements.</p><a class="btn" href="/legacy-workspace">Open Operations</a></div><div class="card"><h3>Database</h3><p>Search properties, requirements, matches and WhatsApp drafts.</p><a class="btn secondary" href="/database-page">Open Database</a></div><div class="card"><h3>Current Totals</h3><p>Properties: <b data-kpi="properties">0</b></p><p>Requirements: <b data-kpi="requirements">0</b></p><p>Matches: <b data-kpi="matches">0</b></p></div></div>
</section>

<section class="page" id="hospitality"><h1 class="title">Hospitality Intelligence</h1><div class="sub">Restaurants, hotels, banquets, marriage venues, commercial farmhouses and operators.</div>
<div class="grid2"><div class="card"><h3>Add Hospitality Prospect</h3><form class="gridform" onsubmit="addCompany(event,'HOSPITALITY')">
<input name="company_name" placeholder="Company / Venue / Operator" required><input name="category" placeholder="Restaurant / Hotel / Banquet / Farmhouse">
<input name="city" placeholder="City"><input name="target_markets" placeholder="Target market / locations">
<input name="website" placeholder="Website"><input name="linkedin_url" placeholder="LinkedIn URL">
<input name="assigned_to" placeholder="Assigned team member"><input name="expansion_score" type="number" min="0" max="100" placeholder="Opportunity score">
<textarea class="full" name="source_excerpt" placeholder="Requirement / availability / notes"></textarea><button class="btn full">Save Hospitality Prospect</button></form></div>
<div class="card"><h3>Hospitality Agent Objective</h3><p>Discover operators, lease/sale opportunities, requirements and expansion signals, then match them against the shared property inventory.</p></div></div>
<div class="card"><h3>Hospitality Companies & Operators</h3><div class="tablewrap"><table><thead><tr><th>Company</th><th>Category</th><th>Market</th><th>Score</th><th>Status</th><th>Owner</th><th>Source</th><th>CRM</th></tr></thead><tbody id="hospitalityRows"></tbody></table></div></div>
</section>

<section class="page" id="retail"><h1 class="title">Retail Expansion Intelligence</h1><div class="sub">Public expansion signals from retail news, company announcements and search-visible LinkedIn company signals.</div>
<div class="toolbar"><button id="runScout" class="btn good" onclick="runScout()">Run Retail Expansion Scout</button><span class="badge">Requires SERPER_API_KEY</span></div>
<div class="grid2"><div class="card"><h3>Add Retail Company</h3><form class="gridform" onsubmit="addCompany(event,'RETAIL')">
<input name="company_name" placeholder="Retail company / brand" required><input name="category" placeholder="Fashion / QSR / Beauty / Electronics">
<input name="target_markets" value="Delhi NCR" placeholder="Target markets"><input name="city" placeholder="City">
<input name="website" placeholder="Website"><input name="linkedin_url" placeholder="LinkedIn company URL">
<input name="assigned_to" placeholder="Assigned team member"><input name="expansion_score" type="number" min="0" max="100" placeholder="Expansion score">
<textarea class="full" name="source_excerpt" placeholder="Expansion signal / requirement"></textarea><button class="btn full">Save Retail Company</button></form></div>
<div class="card"><h3>Retail Scout Rules</h3><p>Prioritises Delhi NCR mentions, expansion plans, new-store announcements, flagship openings and retail-leasing language. It does not bypass LinkedIn login or collect private LinkedIn data.</p></div></div>
<div class="card"><h3>Retail Expansion Radar</h3><div class="tablewrap"><table><thead><tr><th>Company</th><th>Category</th><th>Target Market</th><th>AI Score</th><th>Status</th><th>Owner</th><th>Source</th><th>CRM</th></tr></thead><tbody id="retailRows"></tbody></table></div></div>
<div class="card"><h3>Latest Expansion Signals</h3><div class="tablewrap"><table><thead><tr><th>Company</th><th>Signal</th><th>Score</th><th>Market</th><th>Source</th><th>Found</th></tr></thead><tbody id="signalRows"></tbody></table></div></div>
</section>

<section class="page" id="activity"><h1 class="title">AI Activity Ledger</h1><div class="sub">Auditable record of bot and integration actions.</div><div class="card"><div class="tablewrap"><table><thead><tr><th>Time</th><th>Actor</th><th>Division</th><th>Action</th><th>Entity</th><th>Summary</th><th>Status</th></tr></thead><tbody id="activityRows"></tbody></table></div></div></section>
<section class="page" id="exceptions"><h1 class="title">Exception Centre</h1><div class="sub">Only items that need human attention.</div><div class="card"><div class="tablewrap"><table><thead><tr><th>Severity</th><th>Type</th><th>Entity</th><th>Problem</th></tr></thead><tbody id="exceptionRows"></tbody></table></div></div></section>
<section class="page" id="bots"><h1 class="title">Bot Control Room</h1><div class="sub">Runs, results and failures.</div><div class="toolbar"><button class="btn good" onclick="runScout()">Run Retail Scout</button></div><div class="card"><div class="tablewrap"><table><thead><tr><th>Bot</th><th>Division</th><th>Status</th><th>Found</th><th>Created</th><th>Started</th><th>Summary</th></tr></thead><tbody id="botRows"></tbody></table></div></div></section>
<section class="page" id="crm"><h1 class="title">FlowConnect Sync Centre</h1><div class="sub">FlowConnect remains the sales CRM; this dashboard is the AI intelligence layer.</div><div class="grid2"><div class="card"><h3>Outbound</h3><p>Retail/Hospitality company records can be queued or pushed to FlowConnect using the CRM button.</p><p><b>Required Railway variable:</b><br>FLOWCONNECT_WEBHOOK_URL</p></div><div class="card"><h3>Inbound Webhook</h3><p>Endpoint:</p><code>/api/deal-os/flowconnect/inbound</code><p>Set FLOWCONNECT_API_KEY to protect inbound events.</p></div></div></section>
</div></main></div><script>{_DEAL_JS}</script></body></html>"""

@app.get("/workspace",response_class=HTMLResponse)
def deal_os_workspace(req:Request):
    role=page_role_or_redirect(req)
    if not role:
        return RedirectResponse("/login",status_code=303)
    return HTMLResponse(_deal_page(role))
