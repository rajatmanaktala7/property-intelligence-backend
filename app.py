
import re
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

VERSION="7.8.0-V6-ORGANIZED-DATA-RETAIL"
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
    top_properties=[p for p in top_properties if _norm(p.get("verification_status"))=="verified"]
    name=requirement.get("client_name") or requirement.get("company_name") or "there"
    if not top_properties:
        return "No WhatsApp draft created because no shortlisted property has been verified for current availability."
    lines=[f"Hi {name}, we found a few verified property options matching your requirement:"]
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
    for p in [x for x in top_properties if _norm(x.get('verification_status'))=='verified'][:5]:
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
    return robust_match_requirement(rid, create_whatsapp=True)


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
<p>The manual property form has moved to one master page so the fields stay consistent.</p>
<a href="/property-manual" style="display:inline-block;padding:10px 14px;border-radius:8px;background:#111827;color:white;text-decoration:none;font-weight:700">Open Add Property Form</a>
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
    try:
        result=robust_match_requirement(requirement_id, create_whatsapp=True)
        message=f"Matcher complete. {len(result.get('matches',[]))} ranked matches. " + result.get("diagnostic",{}).get("message","")
    except HTTPException as exc:
        message=f"Matcher error: {exc.detail}"
    except Exception as exc:
        message=f"Matcher error: {exc}"
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
      <p>Use the new master property form for Owner, Broker, contact numbers, rent in figures,
      assigned team member, verification status and direct image/video upload.</p>
      <a class="btn" href="/property-manual">Open Add Property Form</a>
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
# AI DEAL INTELLIGENCE OS V4
# Organized data + Property + Hospitality + Retail + Demand Discovery
# ============================================================================

import re as _re
import math as _math
import socket as _socket
import ipaddress as _ipaddress
from urllib.parse import urlparse as _urlparse
try:
    import httpx as _httpx
except Exception:
    _httpx=None

DEAL_OS_VERSION="4.0.0"
SERPER_API_KEY=os.getenv("SERPER_API_KEY","").strip()
GOOGLE_PLACES_API_KEY=os.getenv("GOOGLE_PLACES_API_KEY","").strip()
APOLLO_API_KEY=os.getenv("APOLLO_API_KEY","").strip()
FLOWCONNECT_WEBHOOK_URL=os.getenv("FLOWCONNECT_WEBHOOK_URL","").strip()
FLOWCONNECT_API_URL=os.getenv("FLOWCONNECT_API_URL","").strip()
FLOWCONNECT_API_KEY=os.getenv("FLOWCONNECT_API_KEY","").strip()
MAX_VIDEO_MB=int(os.getenv("MAX_VIDEO_MB","80"))

V4_SCHEMA = """
CREATE TABLE IF NOT EXISTS pi_owners(
 id BIGSERIAL PRIMARY KEY,
 owner_id VARCHAR(50) UNIQUE NOT NULL,
 owner_name TEXT NOT NULL,
 contact_number TEXT,
 email TEXT,
 city TEXT,
 notes TEXT,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pi_brokers(
 id BIGSERIAL PRIMARY KEY,
 broker_id VARCHAR(50) UNIQUE NOT NULL,
 broker_name TEXT NOT NULL,
 contact_number TEXT,
 email TEXT,
 company_name TEXT,
 city TEXT,
 notes TEXT,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ai_companies(
 id BIGSERIAL PRIMARY KEY,
 company_id VARCHAR(50) UNIQUE NOT NULL,
 division VARCHAR(30) NOT NULL,
 company_name TEXT NOT NULL,
 category TEXT,
 primary_contact_name TEXT,
 primary_contact_phone TEXT,
 primary_contact_email TEXT,
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

CREATE TABLE IF NOT EXISTS ai_marketing_contacts(
 id BIGSERIAL PRIMARY KEY,
 contact_id VARCHAR(50) UNIQUE NOT NULL,
 fingerprint VARCHAR(64) UNIQUE,
 business_type TEXT,
 brand_name TEXT,
 contact_name TEXT,
 phone TEXT,
 email TEXT,
 website TEXT,
 location TEXT,
 city TEXT,
 source_name TEXT,
 source_url TEXT,
 consent_status VARCHAR(40) DEFAULT 'UNKNOWN',
 verification_status VARCHAR(40) DEFAULT 'UNVERIFIED',
 assigned_to TEXT,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ai_requirement_campaigns(
 id BIGSERIAL PRIMARY KEY,
 campaign_id VARCHAR(50) UNIQUE NOT NULL,
 property_id VARCHAR(50),
 campaign_name TEXT,
 property_type TEXT,
 city TEXT,
 location TEXT,
 area_sqft NUMERIC(14,2),
 monthly_rent NUMERIC(14,2),
 rent_or_sale TEXT,
 suitable_category TEXT,
 nearby_brands TEXT,
 additional_points TEXT,
 post_draft TEXT,
 assigned_to TEXT,
 status VARCHAR(40) DEFAULT 'DRAFT',
 created_at TIMESTAMPTZ DEFAULT NOW(),
 updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ai_demand_signals(
 id BIGSERIAL PRIMARY KEY,
 signal_id VARCHAR(50) UNIQUE NOT NULL,
 campaign_id VARCHAR(50),
 source_type VARCHAR(60),
 source_name TEXT,
 source_url TEXT,
 title TEXT,
 excerpt TEXT,
 contact_name TEXT,
 contact_phone TEXT,
 contact_email TEXT,
 company_name TEXT,
 location TEXT,
 intent_score NUMERIC(5,2) DEFAULT 0,
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
CREATE INDEX IF NOT EXISTS idx_ai_marketing_phone ON ai_marketing_contacts(phone);
CREATE INDEX IF NOT EXISTS idx_ai_demand_campaign ON ai_demand_signals(campaign_id);
CREATE INDEX IF NOT EXISTS idx_ai_activity_created ON ai_activity_ledger(created_at DESC);
"""

V4_MIGRATIONS = [
    "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS monthly_rent NUMERIC(14,2)",
    "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS assigned_to TEXT",
    "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS owner_id VARCHAR(50)",
    "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS broker_id VARCHAR(50)",
    "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS contact_number TEXT",
    "ALTER TABLE ai_companies ADD COLUMN IF NOT EXISTS primary_contact_name TEXT",
    "ALTER TABLE ai_companies ADD COLUMN IF NOT EXISTS primary_contact_phone TEXT",
    "ALTER TABLE ai_companies ADD COLUMN IF NOT EXISTS primary_contact_email TEXT",
    "ALTER TABLE ai_demand_signals ADD COLUMN IF NOT EXISTS match_breakdown JSONB",
    "ALTER TABLE ai_demand_signals ADD COLUMN IF NOT EXISTS contact_verification_status VARCHAR(40) DEFAULT 'UNVERIFIED'",
    "ALTER TABLE ai_demand_signals ADD COLUMN IF NOT EXISTS source_contact_text TEXT"
]

@app.on_event("startup")
def v4_startup():
    with engine.begin() as c:
        for stmt in [x.strip() for x in V4_SCHEMA.split(";") if x.strip()]:
            c.execute(text(stmt))
        for stmt in V4_MIGRATIONS:
            try: c.execute(text(stmt))
            except Exception: pass

def _new_code(prefix):
    return f"{prefix}-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"

def _norm(v):
    return _re.sub(r"\s+"," ",str(v or "").strip().lower())

def _tokens(v):
    stop={"the","and","or","in","at","of","for","road","rd","sector","sec","phase","near"}
    return {x for x in _re.findall(r"[a-z0-9]+",_norm(v)) if len(x)>1 and x not in stop}

def _float(v):
    try:
        if v is None or v=="": return None
        return float(str(v).replace(",","").replace("₹","").strip())
    except Exception:
        return None

def _json_rows(rows):
    return [{k:serialize_db_value(v) for k,v in dict(r._mapping).items()} for r in rows]

def _log_activity(actor_name,division,action,entity_type=None,entity_id=None,summary=None,status="SUCCESS"):
    eid=_new_code("EVT")
    with engine.begin() as c:
        c.execute(text("""INSERT INTO ai_activity_ledger
          (event_id,actor_type,actor_name,division,action,entity_type,entity_id,summary,status)
          VALUES(:e,'AI',:a,:d,:ac,:et,:ei,:s,:st)"""),
          {"e":eid,"a":actor_name,"d":division,"ac":action,"et":entity_type,"ei":entity_id,"s":summary,"st":status})
    return eid

def _upsert_owner(c,name,phone=None,email=None,city=None,notes=None):
    if not (name or phone): return None
    row=None
    if phone:
        row=c.execute(text("SELECT owner_id FROM pi_owners WHERE contact_number=:p LIMIT 1"),{"p":phone}).first()
    if not row and name:
        row=c.execute(text("SELECT owner_id FROM pi_owners WHERE LOWER(owner_name)=LOWER(:n) LIMIT 1"),{"n":name}).first()
    if row:
        oid=row[0]
        c.execute(text("""UPDATE pi_owners SET owner_name=COALESCE(NULLIF(:n,''),owner_name),
                         contact_number=COALESCE(NULLIF(:p,''),contact_number),
                         email=COALESCE(NULLIF(:e,''),email),city=COALESCE(NULLIF(:city,''),city),
                         notes=COALESCE(NULLIF(:notes,''),notes),updated_at=NOW() WHERE owner_id=:id"""),
                  {"n":name or "","p":phone or "","e":email or "","city":city or "","notes":notes or "","id":oid})
        return oid
    oid=_new_code("OWN")
    c.execute(text("""INSERT INTO pi_owners(owner_id,owner_name,contact_number,email,city,notes)
                     VALUES(:id,:n,:p,:e,:city,:notes)"""),
              {"id":oid,"n":name or "Unknown Owner","p":phone,"e":email,"city":city,"notes":notes})
    return oid

def _upsert_broker(c,name,phone=None,email=None,company=None,city=None,notes=None):
    if not (name or phone): return None
    row=None
    if phone:
        row=c.execute(text("SELECT broker_id FROM pi_brokers WHERE contact_number=:p LIMIT 1"),{"p":phone}).first()
    if not row and name:
        row=c.execute(text("SELECT broker_id FROM pi_brokers WHERE LOWER(broker_name)=LOWER(:n) LIMIT 1"),{"n":name}).first()
    if row:
        bid=row[0]
        c.execute(text("""UPDATE pi_brokers SET broker_name=COALESCE(NULLIF(:n,''),broker_name),
                         contact_number=COALESCE(NULLIF(:p,''),contact_number),
                         email=COALESCE(NULLIF(:e,''),email),company_name=COALESCE(NULLIF(:co,''),company_name),
                         city=COALESCE(NULLIF(:city,''),city),notes=COALESCE(NULLIF(:notes,''),notes),
                         updated_at=NOW() WHERE broker_id=:id"""),
                  {"n":name or "","p":phone or "","e":email or "","co":company or "","city":city or "","notes":notes or "","id":bid})
        return bid
    bid=_new_code("BRK")
    c.execute(text("""INSERT INTO pi_brokers(broker_id,broker_name,contact_number,email,company_name,city,notes)
                     VALUES(:id,:n,:p,:e,:co,:city,:notes)"""),
              {"id":bid,"n":name or "Unknown Broker","p":phone,"e":email,"co":company,"city":city,"notes":notes})
    return bid

async def _store_media_files(property_id,files):
    saved=[]
    for file in files or []:
        if not file or not file.filename: continue
        mime=(file.content_type or "").lower()
        is_img=mime in {"image/jpeg","image/png","image/webp","image/gif"}
        is_vid=mime in {"video/mp4","video/webm","video/quicktime","video/x-m4v"}
        if not (is_img or is_vid): continue
        data=await file.read()
        maxb=(MAX_IMAGE_MB if is_img else MAX_VIDEO_MB)*1024*1024
        if len(data)>maxb:
            raise HTTPException(413,f"{file.filename} is too large.")
        mid=str(uuid.uuid4())
        with engine.begin() as c:
            c.execute(text("""INSERT INTO pi_property_media(media_id,property_id,media_type,filename,mime_type,file_size,content)
                VALUES(CAST(:mid AS UUID),:pid,:mt,:fn,:mime,:sz,:data)"""),
                {"mid":mid,"pid":property_id,"mt":"IMAGE" if is_img else "VIDEO","fn":file.filename,
                 "mime":mime,"sz":len(data),"data":data})
        saved.append({"media_id":mid,"type":"IMAGE" if is_img else "VIDEO","filename":file.filename})
    return saved

@app.post("/api/v4/properties/manual")
async def v4_add_property_manual(
    req:Request,
    property_name:str=Form(""),
    property_type:str=Form("NA"),
    city:str=Form("NA"),
    location:str=Form("NA"),
    available_area_sqft:Optional[str]=Form(None),
    minimum_area_sqft:Optional[str]=Form(None),
    maximum_area_sqft:Optional[str]=Form(None),
    floor:Optional[str]=Form(None),
    rent_or_sale:Optional[str]=Form(None),
    monthly_rent:Optional[str]=Form(None),
    nearby_brands:Optional[str]=Form(None),
    suitable_category:Optional[str]=Form(None),
    parking:Optional[str]=Form(None),
    verification_status:str=Form("UNVERIFIED"),
    assigned_to:Optional[str]=Form(None),
    contact_number:Optional[str]=Form(None),
    owner_name:Optional[str]=Form(None),
    owner_contact:Optional[str]=Form(None),
    owner_email:Optional[str]=Form(None),
    broker_name:Optional[str]=Form(None),
    broker_contact:Optional[str]=Form(None),
    broker_email:Optional[str]=Form(None),
    broker_company:Optional[str]=Form(None),
    remarks:Optional[str]=Form(None),
    media:list[UploadFile]=File(default=[])
):
    need_login(req)
    verification_status="VERIFIED" if verification_status.upper()=="VERIFIED" else "UNVERIFIED"
    with engine.begin() as c:
        oid=_upsert_owner(c,owner_name,owner_contact,owner_email,city,remarks)
        bid=_upsert_broker(c,broker_name,broker_contact,broker_email,broker_company,city,remarks)
        pid=make_id("PROP",c)
        fp=fingerprint([property_name,city,location,property_type,available_area_sqft,floor,rent_or_sale,owner_contact,broker_contact])
        c.execute(text("""INSERT INTO pi_properties(
            property_id,fingerprint,property_name,property_type,city,location,available_area_sqft,
            minimum_area_sqft,maximum_area_sqft,floor,rent_or_sale,monthly_rent,nearby_brands,
            suitable_category,parking,owner_id,broker_id,owner_name,owner_contact,broker_name,broker_contact,
            remarks,source,verification_status,verified_date,verified_by,assigned_to,contact_number)
            VALUES(:pid,:fp,:pn,:pt,:city,:loc,:aa,:mn,:mx,:floor,:rs,:rent,:nb,:cat,:park,:oid,:bid,
            :on,:oc,:bn,:bc,:rem,'Manual V4',:vs,
            CASE WHEN :vs='VERIFIED' THEN CURRENT_DATE ELSE NULL END,
            CASE WHEN :vs='VERIFIED' THEN :actor ELSE NULL END,:assigned,:contact)"""),
            {"pid":pid,"fp":fp,"pn":property_name,"pt":property_type,"city":city,"loc":location,
             "aa":_float(available_area_sqft),"mn":_float(minimum_area_sqft),"mx":_float(maximum_area_sqft),
             "floor":floor,"rs":rent_or_sale,"rent":_float(monthly_rent),"nb":nearby_brands,"cat":suitable_category,
             "park":parking,"oid":oid,"bid":bid,"on":owner_name,"oc":owner_contact,"bn":broker_name,"bc":broker_contact,
             "rem":remarks,"vs":verification_status,"actor":actor_name(req),"assigned":assigned_to,
             "contact":contact_number or owner_contact or broker_contact})
        c.execute(text("""INSERT INTO pi_verification_log(property_id,action,performed_by,notes)
                         VALUES(:pid,'CREATED_V4',:actor,:notes)"""),
                  {"pid":pid,"actor":actor_name(req),"notes":f"Manual property; verification={verification_status}"})
    saved=await _store_media_files(pid,media)
    _log_activity("Dashboard","PROPERTY","PROPERTY_CREATED","property",pid,
                  f"{property_name or pid}; assigned to {assigned_to or 'Unassigned'}")
    return {"status":"created","property_id":pid,"owner_id":oid,"broker_id":bid,"media":saved}

@app.get("/api/v4/owners")
def v4_owners(req:Request,limit:int=Query(300,ge=1,le=1000)):
    need_login(req)
    with engine.connect() as c:
        rows=c.execute(text("SELECT * FROM pi_owners ORDER BY updated_at DESC LIMIT :n"),{"n":limit}).fetchall()
    return {"status":"ok","rows":_json_rows(rows)}

@app.get("/api/v4/brokers")
def v4_brokers(req:Request,limit:int=Query(300,ge=1,le=1000)):
    need_login(req)
    with engine.connect() as c:
        rows=c.execute(text("SELECT * FROM pi_brokers ORDER BY updated_at DESC LIMIT :n"),{"n":limit}).fetchall()
    return {"status":"ok","rows":_json_rows(rows)}

class V4CompanyInput(BaseModel):
    division: Literal["HOSPITALITY","RETAIL"]
    company_name:str
    category:Optional[str]=None
    primary_contact_name:Optional[str]=None
    primary_contact_phone:Optional[str]=None
    primary_contact_email:Optional[str]=None
    website:Optional[str]=None
    linkedin_url:Optional[str]=None
    city:Optional[str]=None
    target_markets:Optional[str]=None
    expansion_status:Optional[str]="DISCOVERED"
    expansion_score:Optional[float]=0
    source_name:Optional[str]="Manual"
    source_url:Optional[str]=None
    source_excerpt:Optional[str]=None
    assigned_to:Optional[str]=None

@app.post("/api/v4/companies")
def v4_add_company(p:V4CompanyInput,req:Request):
    need_login(req)
    cid=_new_code("CMP")
    with engine.begin() as c:
        c.execute(text("""INSERT INTO ai_companies(company_id,division,company_name,category,primary_contact_name,
            primary_contact_phone,primary_contact_email,website,linkedin_url,city,target_markets,expansion_status,
            expansion_score,source_name,source_url,source_excerpt,assigned_to)
            VALUES(:id,:division,:company_name,:category,:primary_contact_name,:primary_contact_phone,
            :primary_contact_email,:website,:linkedin_url,:city,:target_markets,:expansion_status,:expansion_score,
            :source_name,:source_url,:source_excerpt,:assigned_to)"""),{"id":cid,**p.model_dump()})
        if p.primary_contact_name or p.primary_contact_phone or p.primary_contact_email:
            c.execute(text("""INSERT INTO ai_contacts(contact_id,company_id,full_name,business_email,business_phone,
                linkedin_url,verification_status,source_url)
                VALUES(:id,:cid,:n,:e,:p,:li,'UNVERIFIED',:src)"""),
                {"id":_new_code("CON"),"cid":cid,"n":p.primary_contact_name,"e":p.primary_contact_email,
                 "p":p.primary_contact_phone,"li":p.linkedin_url,"src":p.source_url})
    _log_activity("Dashboard",p.division,"PROSPECT_CREATED","company",cid,p.company_name)
    return {"status":"created","company_id":cid}

@app.get("/api/v4/companies")
def v4_companies(req:Request,division:str=Query("RETAIL"),limit:int=Query(200,ge=1,le=500)):
    need_login(req)
    with engine.connect() as c:
        rows=c.execute(text("SELECT * FROM ai_companies WHERE division=:d ORDER BY expansion_score DESC,created_at DESC LIMIT :n"),
                       {"d":division.upper(),"n":limit}).fetchall()
    return {"status":"ok","rows":_json_rows(rows)}

def _marketing_fp(brand,phone,email,location):
    return hashlib.sha256("|".join([_norm(brand),_norm(phone),_norm(email),_norm(location)]).encode()).hexdigest()

def _save_marketing_contact(data):
    fp=_marketing_fp(data.get("brand_name"),data.get("phone"),data.get("email"),data.get("location"))
    with engine.begin() as c:
        old=c.execute(text("SELECT contact_id FROM ai_marketing_contacts WHERE fingerprint=:f"),{"f":fp}).first()
        if old: return {"status":"duplicate","contact_id":old[0]}
        cid=_new_code("MKT")
        c.execute(text("""INSERT INTO ai_marketing_contacts(contact_id,fingerprint,business_type,brand_name,contact_name,
            phone,email,website,location,city,source_name,source_url,consent_status,verification_status,assigned_to)
            VALUES(:id,:fp,:bt,:brand,:name,:phone,:email,:web,:loc,:city,:source,:url,:consent,:verify,:assigned)"""),
            {"id":cid,"fp":fp,"bt":data.get("business_type"),"brand":data.get("brand_name"),
             "name":data.get("contact_name"),"phone":data.get("phone"),"email":data.get("email"),
             "web":data.get("website"),"loc":data.get("location"),"city":data.get("city"),
             "source":data.get("source_name"),"url":data.get("source_url"),
             "consent":data.get("consent_status") or "UNKNOWN","verify":data.get("verification_status") or "UNVERIFIED",
             "assigned":data.get("assigned_to")})
    return {"status":"created","contact_id":cid}

@app.get("/api/v4/marketing-contacts")
def v4_marketing_contacts(req:Request,limit:int=Query(500,ge=1,le=2000)):
    need_login(req)
    with engine.connect() as c:
        rows=c.execute(text("SELECT * FROM ai_marketing_contacts ORDER BY created_at DESC LIMIT :n"),{"n":limit}).fetchall()
    return {"status":"ok","rows":_json_rows(rows)}

@app.post("/api/v4/marketing-contacts/upload")
async def v4_upload_contacts(req:Request,file:UploadFile=File(...)):
    need_login(req)
    raw=await file.read()
    textdata=raw.decode("utf-8-sig",errors="replace")
    reader=csv.DictReader(io.StringIO(textdata))
    created=duplicates=0
    for row in reader:
        data={
            "business_type":row.get("business_type") or row.get("Business Type") or row.get("category") or row.get("Category"),
            "brand_name":row.get("brand_name") or row.get("Brand Name") or row.get("company") or row.get("Company"),
            "contact_name":row.get("contact_name") or row.get("Contact Name") or row.get("name") or row.get("Name"),
            "phone":row.get("phone") or row.get("Phone") or row.get("contact_no") or row.get("Contact No"),
            "email":row.get("email") or row.get("Email"),
            "website":row.get("website") or row.get("Website"),
            "location":row.get("location") or row.get("Location"),
            "city":row.get("city") or row.get("City") or "Delhi NCR",
            "source_name":"Uploaded CSV: "+(file.filename or "contacts.csv"),
            "consent_status":row.get("consent_status") or row.get("Consent Status") or "UNKNOWN",
            "verification_status":row.get("verification_status") or row.get("Verification Status") or "UNVERIFIED",
            "assigned_to":row.get("assigned_to") or row.get("Team Member")
        }
        r=_save_marketing_contact(data)
        if r["status"]=="created": created+=1
        else: duplicates+=1
    _log_activity("Contact Upload","HOSPITALITY","CONTACT_DATABASE_UPLOADED","contact_database",None,
                  f"{created} new; {duplicates} duplicates")
    return {"status":"processed","created":created,"duplicates":duplicates}

@app.get("/api/v4/marketing-contacts/export.csv")
def v4_export_contacts(req:Request):
    need_login(req)
    with engine.connect() as c:
        rows=c.execute(text("""SELECT business_type,brand_name,contact_name,phone,email,website,location,city,
                              consent_status,verification_status,assigned_to,source_name,source_url
                              FROM ai_marketing_contacts ORDER BY created_at DESC""")).fetchall()
    out=io.StringIO()
    w=csv.writer(out)
    w.writerow(["business_type","brand_name","contact_name","phone","email","website","location","city",
                "consent_status","verification_status","assigned_to","source_name","source_url"])
    for r in rows: w.writerow(list(r))
    return Response(content=out.getvalue(),media_type="text/csv",
                    headers={"Content-Disposition":"attachment; filename=hospitality_marketing_contacts.csv"})

def _safe_public_url(url):
    try:
        u=_urlparse(url)
        if u.scheme not in {"http","https"} or not u.hostname: return False
        ip=_socket.gethostbyname(u.hostname)
        obj=_ipaddress.ip_address(ip)
        return not (obj.is_private or obj.is_loopback or obj.is_link_local or obj.is_reserved)
    except Exception:
        return False

def _public_email_from_website(url):
    if not _httpx or not url or not _safe_public_url(url): return None
    try:
        r=_httpx.get(url,timeout=8.0,follow_redirects=True,headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code>=400: return None
        emails=_re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",r.text,_re.I)
        bad=("example.com","wixpress.com","sentry.io")
        emails=[e for e in emails if not any(b in e.lower() for b in bad)]
        return emails[0][:255] if emails else None
    except Exception:
        return None

def _google_places_search(query):
    if not GOOGLE_PLACES_API_KEY:
        raise RuntimeError("GOOGLE_PLACES_API_KEY is not configured.")
    if not _httpx: raise RuntimeError("httpx dependency missing.")
    fields="places.id,places.displayName,places.formattedAddress,places.nationalPhoneNumber,places.internationalPhoneNumber,places.websiteUri,places.googleMapsUri,places.types"
    r=_httpx.post("https://places.googleapis.com/v1/places:searchText",
        headers={"X-Goog-Api-Key":GOOGLE_PLACES_API_KEY,"X-Goog-FieldMask":fields,"Content-Type":"application/json"},
        json={"textQuery":query,"maxResultCount":20,"languageCode":"en"},timeout=30.0)
    r.raise_for_status()
    return r.json().get("places",[])

def _start_bot(name,division,summary):
    rid=_new_code("RUN")
    with engine.begin() as c:
        c.execute(text("""INSERT INTO ai_bot_runs(run_id,bot_name,division,status,summary)
                         VALUES(:id,:n,:d,'RUNNING',:s)"""),{"id":rid,"n":name,"d":division,"s":summary})
    return rid

def _finish_bot(run_id,status,found,created,summary,error=None):
    with engine.begin() as c:
        c.execute(text("""UPDATE ai_bot_runs SET status=:st,records_found=:f,records_created=:cr,
            summary=:s,error_message=:e,completed_at=NOW() WHERE run_id=:id"""),
            {"st":status,"f":found,"cr":created,"s":summary,"e":error,"id":run_id})

def _hospitality_worker(run_id):
    categories=["restaurant","cafe","lounge","club","nightclub","banquet hall","hotel","guest house","guesthouse","wedding venue","commercial farmhouse"]
    zones=["South Delhi","West Delhi","Central Delhi","Gurgaon","Noida","Faridabad","Ghaziabad"]
    found=created=0; errors=[]
    try:
        for category in categories:
            for zone in zones:
                try:
                    for p in _google_places_search(f"{category} in {zone}"):
                        found+=1
                        name=((p.get("displayName") or {}).get("text") or "").strip()
                        if not name: continue
                        phone=p.get("nationalPhoneNumber") or p.get("internationalPhoneNumber")
                        website=p.get("websiteUri")
                        address=p.get("formattedAddress")
                        email=_public_email_from_website(website) if website else None
                        r=_save_marketing_contact({
                            "business_type":category.title(),"brand_name":name,"contact_name":None,
                            "phone":phone,"email":email,"website":website,"location":address,"city":"Delhi NCR",
                            "source_name":"Google Places","source_url":p.get("googleMapsUri"),
                            "consent_status":"UNKNOWN","verification_status":"PUBLIC_SOURCE"
                        })
                        if r["status"]=="created":
                            created+=1
                            _log_activity("Hospitality Data Bot","HOSPITALITY","CONTACT_FOUND","marketing_contact",
                                          r["contact_id"],f"{name} | {phone or 'no phone'}")
                except Exception as ex:
                    errors.append(f"{category}/{zone}: {ex}")
        _finish_bot(run_id,"COMPLETED" if created or not errors else "FAILED",found,created,
                    f"Hospitality scan: {found} places reviewed; {created} new contacts", " | ".join(errors[:5]) or None)
    except Exception as ex:
        _finish_bot(run_id,"FAILED",found,created,"Hospitality scan failed",str(ex))

@app.post("/api/v4/hospitality-bot/start")
def v4_hospitality_bot(bg:BackgroundTasks,req:Request):
    need_login(req)
    run=_start_bot("Delhi NCR Hospitality Data Bot","HOSPITALITY","Fetching public business contact data")
    bg.add_task(_hospitality_worker,run)
    return {"status":"ACCEPTED","run_id":run}

def _serper_search(query,num=10):
    if not SERPER_API_KEY: raise RuntimeError("SERPER_API_KEY is not configured.")
    if not _httpx: raise RuntimeError("httpx dependency missing.")
    r=_httpx.post("https://google.serper.dev/search",
        headers={"X-API-KEY":SERPER_API_KEY,"Content-Type":"application/json"},
        json={"q":query,"num":num},timeout=30.0)
    r.raise_for_status()
    return r.json()

def _retail_score(title,snippet):
    s=_norm((title or "")+" "+(snippet or ""))
    score=10
    groups=[
        (["delhi ncr","delhi","gurgaon","gurugram","noida","faridabad","ghaziabad"],25),
        (["expansion","expand","rollout","growth plan"],20),
        (["new store","new stores","outlet","flagship","store opening"],20),
        (["lease","leasing","retail space","mall","high street"],15),
        (["india entry","north india"],10)
    ]
    for terms,pts in groups:
        if any(t in s for t in terms): score+=pts
    return min(100,score)

def _company_guess(title):
    parts=_re.split(r"\s+(?:plans|plan|to|opens|open|launches|launch|expands|expand|eyes|targets|set to)\s+",
                    str(title or "").strip(),1,flags=_re.I)
    return (parts[0] if parts else str(title or "")).strip(" -:|")[:180] or "Unknown Retail Company"

def _retail_worker(run_id):
    queries=[
        '"Delhi NCR" retail expansion new stores brand',
        'Gurugram retail brand expansion store opening',
        'Noida retail brand expansion new store',
        'India retail expansion plans stores 2026',
        'site:linkedin.com/posts retail expansion Delhi NCR',
        'retail news India brand opening stores Delhi Gurgaon Noida'
    ]
    found=created=0; errors=[]
    for q in queries:
        try:
            for item in _serper_search(q,10).get("organic",[]):
                found+=1
                title=item.get("title") or ""; link=item.get("link") or ""; snippet=item.get("snippet") or ""
                score=_retail_score(title,snippet); company=_company_guess(title)
                with engine.begin() as c:
                    exists=c.execute(text("SELECT 1 FROM ai_expansion_signals WHERE source_url=:u LIMIT 1"),{"u":link}).first()
                    if exists: continue
                    row=c.execute(text("""SELECT company_id FROM ai_companies
                        WHERE division='RETAIL' AND LOWER(company_name)=LOWER(:n) LIMIT 1"""),{"n":company}).first()
                    if row: cid=row[0]
                    else:
                        cid=_new_code("CMP")
                        c.execute(text("""INSERT INTO ai_companies(company_id,division,company_name,category,target_markets,
                            expansion_status,expansion_score,source_name,source_url,source_excerpt)
                            VALUES(:id,'RETAIL',:n,'Retail','Delhi NCR','SIGNAL_DETECTED',:s,'Public Web / Retail News',:u,:x)"""),
                            {"id":cid,"n":company,"s":score,"u":link,"x":snippet})
                    sid=_new_code("SIG")
                    c.execute(text("""INSERT INTO ai_expansion_signals(signal_id,company_id,division,title,signal_type,market,
                        source_name,source_url,excerpt,signal_score)
                        VALUES(:sid,:cid,'RETAIL',:t,'EXPANSION_SIGNAL','Delhi NCR','Public Web / Retail News',:u,:x,:s)"""),
                        {"sid":sid,"cid":cid,"t":title,"u":link,"x":snippet,"s":score})
                    c.execute(text("UPDATE ai_companies SET expansion_score=GREATEST(expansion_score,:s),updated_at=NOW() WHERE company_id=:id"),
                              {"s":score,"id":cid})
                    created+=1
                _log_activity("Retail Expansion Bot","RETAIL","EXPANSION_SIGNAL_FOUND","company",cid,f"{company} | {score}")
        except Exception as ex: errors.append(str(ex))
    _finish_bot(run_id,"COMPLETED" if created or not errors else "FAILED",found,created,
                f"Retail scan: {found} results; {created} new signals"," | ".join(errors[:5]) or None)

@app.post("/api/v4/retail-bot/start")
def v4_retail_bot(bg:BackgroundTasks,req:Request):
    need_login(req)
    run=_start_bot("Retail Expansion Bot","RETAIL","Scanning public retail expansion signals")
    bg.add_task(_retail_worker,run)
    req_run=_start_bot("Retail LinkedIn Requirement Bot","DEMAND","Scanning LinkedIn/public-indexed retail leasing requirements")
    bg.add_task(_retail_linkedin_requirement_worker,req_run)
    return {"status":"ACCEPTED","run_id":run,"requirement_run_id":req_run}

class CampaignInput(BaseModel):
    campaign_name:str
    property_id:Optional[str]=None
    property_type:Optional[str]=None
    city:Optional[str]="Delhi NCR"
    location:Optional[str]=None
    area_sqft:Optional[float]=None
    monthly_rent:Optional[float]=None
    rent_or_sale:Optional[str]="Rent"
    suitable_category:Optional[str]=None
    nearby_brands:Optional[str]=None
    additional_points:Optional[str]=None
    assigned_to:Optional[str]=None

def _post_draft(p):
    bits=[p.property_type or "commercial property",p.location or p.city or "Delhi NCR"]
    if p.area_sqft: bits.append(f"{p.area_sqft:,.0f} sqft")
    if p.monthly_rent: bits.append(f"₹{p.monthly_rent:,.0f}/month")
    if p.suitable_category: bits.append(f"suitable for {p.suitable_category}")
    return "Available: "+", ".join(bits)+". Genuine business requirements may connect with our leasing team."

@app.post("/api/v4/requirement-campaigns")
def v4_create_campaign(p:CampaignInput,req:Request):
    need_login(req)
    cid=_new_code("CAM")
    draft=_post_draft(p)
    with engine.begin() as c:
        c.execute(text("""INSERT INTO ai_requirement_campaigns(campaign_id,property_id,campaign_name,property_type,city,
            location,area_sqft,monthly_rent,rent_or_sale,suitable_category,nearby_brands,additional_points,post_draft,assigned_to)
            VALUES(:id,:property_id,:campaign_name,:property_type,:city,:location,:area_sqft,:monthly_rent,:rent_or_sale,
            :suitable_category,:nearby_brands,:additional_points,:draft,:assigned_to)"""),
            {"id":cid,"draft":draft,**p.model_dump()})
    _log_activity("Dashboard","DEMAND","DEMAND_CAMPAIGN_CREATED","campaign",cid,p.campaign_name)
    return {"status":"created","campaign_id":cid,"post_draft":draft}

def _extract_public_contacts(text_value):
    raw=str(text_value or "")
    phones=[]
    for m in _re.findall(r'(?:(?:\+?91[\s-]?)?[6-9]\d{9})', raw):
        digits=_re.sub(r'\D','',m)
        if len(digits)==12 and digits.startswith("91"):
            digits=digits[2:]
        if len(digits)==10 and digits[0] in "6789" and digits not in phones:
            phones.append(digits)
    emails=[]
    for e in _re.findall(r'[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}', raw, _re.I):
        e=e.lower()
        if e not in emails:
            emails.append(e)
    return phones[:5], emails[:5]

def _campaign_match_score(title,snippet,camp):
    textv=_norm((title or "")+" "+(snippet or ""))
    score=0
    breakdown={}

    intent_terms=[
        "looking for","requirement","required","seeking","need space","needs space",
        "want to lease","wants to lease","looking to lease","looking to rent",
        "expansion requirement","space requirement","require commercial space",
        "looking for property","looking for shop","looking for showroom",
        "looking for restaurant space","looking for retail space"
    ]
    hits=[x for x in intent_terms if x in textv]
    s=30 if hits else 0
    breakdown["intent"]={"score":s,"max":30,"hits":hits[:4]}
    score+=s

    target_loc=camp.get("location") or camp.get("city") or ""
    loc_tokens=_tokens(target_loc); text_tokens=_tokens(textv); overlap=loc_tokens & text_tokens
    s=0
    if loc_tokens:
        ratio=len(overlap)/max(1,len(loc_tokens))
        if ratio>=0.75:s=25
        elif ratio>=0.5:s=20
        elif ratio>=0.25:s=12
        elif any(x in textv for x in ["delhi ncr","delhi","gurgaon","gurugram","noida"]) and any(x in _norm(target_loc) for x in ["delhi ncr","delhi","gurgaon","gurugram","noida"]):
            s=10
    else:s=10
    breakdown["location"]={"score":s,"max":25,"hits":sorted(overlap)[:6]}
    score+=s

    target_cat=camp.get("suitable_category") or camp.get("property_type") or ""
    cat_tokens=_type_family(target_cat); cat_overlap=cat_tokens & text_tokens
    s=0
    if cat_tokens:
        ratio=len(cat_overlap)/max(1,min(len(cat_tokens),4))
        if ratio>=0.75:s=20
        elif ratio>=0.5:s=16
        elif cat_overlap:s=10
    else:s=8
    breakdown["category"]={"score":s,"max":20,"hits":sorted(cat_overlap)[:6]}
    score+=s

    tx=_transaction_family(camp.get("rent_or_sale"))
    s=0
    if tx=="rent" and any(x in textv for x in ["lease","rent","rental","to let"]):s=10
    elif tx=="sale" and any(x in textv for x in ["buy","purchase","sale","acquire"]):s=10
    elif not tx:s=5
    breakdown["transaction"]={"score":s,"max":10,"target":tx}
    score+=s

    area_target=_float(camp.get("area_sqft")); s=0; area_hits=[]
    if area_target:
        vals=[]
        for m in _re.findall(r'(\d[\d,]{2,})\s*(?:sq\s*ft|sqft|square\s*feet|sft)', textv, _re.I):
            try: vals.append(float(m.replace(",","")))
            except: pass
        if vals:
            best=min(abs(x-area_target)/max(area_target,1) for x in vals)
            if best<=0.10:s=10
            elif best<=0.20:s=8
            elif best<=0.35:s=5
            area_hits=[int(x) for x in vals[:4]]
    else:s=5
    breakdown["area"]={"score":s,"max":10,"hits":area_hits}
    score+=s

    context_tokens=_tokens((camp.get("nearby_brands") or "")+" "+(camp.get("additional_points") or ""))
    context_overlap=context_tokens & text_tokens
    s=5 if context_overlap else 0
    breakdown["context"]={"score":s,"max":5,"hits":sorted(context_overlap)[:5]}
    score+=s

    return min(100,score),breakdown

def _fetch_public_source_text(url):
    if not _httpx or not url or not _safe_public_url(url):
        return ""
    try:
        r=_httpx.get(url,timeout=8.0,follow_redirects=True,headers={"User-Agent":"Mozilla/5.0 (compatible; DealIntelligenceBot/1.0)"})
        if r.status_code>=400:return ""
        ctype=(r.headers.get("content-type") or "").lower()
        if "text/html" not in ctype and "text/plain" not in ctype:return ""
        return (r.text or "")[:250000]
    except Exception:
        return ""

def _requirement_worker(run_id,campaign_id):
    found=created=rejected=0
    errors=[]
    with engine.connect() as c:
        row=c.execute(text("SELECT * FROM ai_requirement_campaigns WHERE campaign_id=:id"),{"id":campaign_id}).first()
    if not row:
        _finish_bot(run_id,"FAILED",0,0,"Campaign not found","Campaign not found")
        return

    camp=dict(row._mapping)
    loc=camp.get("location") or camp.get("city") or "Delhi NCR"
    cat=camp.get("suitable_category") or camp.get("property_type") or "commercial"
    tx=camp.get("rent_or_sale") or "Rent"

    queries=[
        f'"looking for" "{cat}" "{loc}" {tx}',
        f'"space requirement" "{cat}" "{loc}"',
        f'"requirement" "{cat}" "{loc}" lease',
        f'"seeking" "{cat}" space "{loc}"',
        f'site:linkedin.com/posts "looking for" "{cat}" "{loc}"',
        f'site:linkedin.com/posts "requirement" "{cat}" "{loc}"',
        f'site:facebook.com "looking for" "{cat}" "{loc}"',
        f'site:instagram.com "looking for" "{cat}" "{loc}"',
        f'site:99acres.com "{cat}" "{loc}" requirement',
        f'site:magicbricks.com "{cat}" "{loc}" requirement'
    ]

    for q in queries:
        try:
            for item in _serper_search(q,10).get("organic",[]):
                found+=1
                link=item.get("link") or ""
                title=item.get("title") or ""
                snippet=item.get("snippet") or ""
                score,breakdown=_campaign_match_score(title,snippet,camp)

                if score<90:
                    rejected+=1
                    continue

                with engine.connect() as c:
                    if c.execute(text("SELECT 1 FROM ai_demand_signals WHERE source_url=:u AND campaign_id=:cid LIMIT 1"),
                                 {"u":link,"cid":campaign_id}).first():
                        continue

                source="Public Web"
                if "linkedin.com" in link:source="LinkedIn public/indexed"
                elif "facebook.com" in link:source="Facebook public/indexed"
                elif "instagram.com" in link:source="Instagram public/indexed"
                elif "99acres.com" in link:source="99acres public/indexed"
                elif "magicbricks.com" in link:source="Magicbricks public/indexed"

                phones,emails=_extract_public_contacts(title+" "+snippet)
                if not phones or not emails:
                    page=_fetch_public_source_text(link)
                    p2,e2=_extract_public_contacts(page)
                    for p in p2:
                        if p not in phones:phones.append(p)
                    for e in e2:
                        if e not in emails:emails.append(e)

                phone=phones[0] if phones else None
                email=emails[0] if emails else None
                verification="PUBLIC_SOURCE" if (phone or email) else "NOT_FOUND"
                sid=_new_code("DEM")

                with engine.begin() as c:
                    c.execute(text("""INSERT INTO ai_demand_signals(
                        signal_id,campaign_id,source_type,source_name,source_url,title,excerpt,
                        contact_phone,contact_email,location,intent_score,status,match_breakdown,
                        contact_verification_status,source_contact_text)
                        VALUES(:sid,:cid,'WEB_DEMAND',:source,:url,:title,:excerpt,:phone,:email,:loc,:score,
                        '90_PLUS_MATCH',CAST(:breakdown AS JSONB),:verification,:contact_text)"""),
                        {"sid":sid,"cid":campaign_id,"source":source,"url":link,"title":title,"excerpt":snippet,
                         "phone":phone,"email":email,"loc":loc,"score":score,
                         "breakdown":json.dumps(breakdown),"verification":verification,
                         "contact_text":(title+" | "+snippet)[:2500]})
                    created+=1

                _log_activity("Requirement Discovery Bot","DEMAND","90_PLUS_DEMAND_MATCH","demand_signal",sid,
                              f"{source} | score {score} | contact {'yes' if phone else 'no'}")
        except Exception as ex:
            errors.append(str(ex))

    with engine.begin() as c:
        c.execute(text("UPDATE ai_requirement_campaigns SET status='SCANNED_90_PLUS',updated_at=NOW() WHERE campaign_id=:id"),
                  {"id":campaign_id})

    _finish_bot(run_id,"COMPLETED" if created or not errors else "FAILED",found,created,
                f"Strict demand scan: {found} reviewed; {created} saved at 90%+; {rejected} filtered out",
                " | ".join(errors[:5]) or None)

@app.post("/api/v4/requirement-campaigns/{campaign_id}/start")
def v4_start_requirement_campaign(campaign_id:str,bg:BackgroundTasks,req:Request):
    need_login(req)
    run=_start_bot("Requirement Discovery Bot","DEMAND",f"Scanning demand for {campaign_id}")
    bg.add_task(_requirement_worker,run,campaign_id)
    return {"status":"ACCEPTED","run_id":run}

@app.get("/api/v4/requirement-campaigns")
def v4_campaigns(req:Request):
    need_login(req)
    with engine.connect() as c:
        rows=c.execute(text("SELECT * FROM ai_requirement_campaigns ORDER BY created_at DESC LIMIT 200")).fetchall()
    return {"status":"ok","rows":_json_rows(rows)}

@app.get("/api/v4/demand-signals")
def v4_demand_signals(req:Request,campaign_id:Optional[str]=Query(None)):
    need_login(req)
    with engine.connect() as c:
        if campaign_id:
            rows=c.execute(text("SELECT * FROM ai_demand_signals WHERE campaign_id=:id ORDER BY intent_score DESC,created_at DESC"),
                           {"id":campaign_id}).fetchall()
        else:
            rows=c.execute(text("SELECT * FROM ai_demand_signals ORDER BY intent_score DESC,created_at DESC LIMIT 300")).fetchall()
    return {"status":"ok","rows":_json_rows(rows)}

def _city_family(value):
    v=_norm(value)
    if not v: return set()
    families=set(_tokens(v))
    if any(x in v for x in ["gurgaon","gurugram"]):
        families.update({"gurgaon","gurugram","delhi","ncr"})
    if "new delhi" in v or v=="delhi" or " delhi" in (" "+v):
        families.update({"delhi","newdelhi","ncr"})
    if "noida" in v:
        families.update({"noida","delhi","ncr"})
        if "greater" in v: families.add("greaternoida")
    if "faridabad" in v:
        families.update({"faridabad","delhi","ncr"})
    if "ghaziabad" in v:
        families.update({"ghaziabad","delhi","ncr"})
    if "delhi ncr" in v or v=="ncr":
        families.update({"delhi","ncr","gurgaon","gurugram","noida","faridabad","ghaziabad"})
    return families

def _transaction_family(value):
    v=_norm(value)
    if any(x in v for x in ["rent","lease","leasing","rental","to let"]): return "rent"
    if any(x in v for x in ["sale","sell","purchase","buy"]): return "sale"
    return v

def _type_family(value):
    toks=_tokens(value)
    v=_norm(value)
    if any(x in v for x in ["retail","shop","showroom","store"]):
        toks.update({"retail","shop","showroom","store","commercial"})
    if any(x in v for x in ["restaurant","cafe","café","qsr","food","f&b","fnb","lounge","club"]):
        toks.update({"restaurant","cafe","qsr","food","f&b","fnb","lounge","club","commercial","retail"})
    if any(x in v for x in ["banquet","wedding","farmhouse","hotel","hospitality"]):
        toks.update({"banquet","wedding","farmhouse","hotel","hospitality","commercial"})
    if any(x in v for x in ["office","commercial","business centre","cowork"]):
        toks.update({"office","commercial"})
    if any(x in v for x in ["warehouse","industrial","factory","logistics"]):
        toks.update({"warehouse","industrial","commercial"})
    if any(x in v for x in ["residential","apartment","flat","villa","house","builder floor","residence"]):
        toks.update({"residential"})
    return toks


def _property_class(*values):
    v=_norm(" ".join(str(x or "") for x in values))
    commercial=[
        "commercial","retail","shop","showroom","office","restaurant","cafe","café",
        "qsr","food","f&b","fnb","lounge","club","banquet","hotel","hospitality",
        "warehouse","industrial","factory","business centre","cowork"
    ]
    residential=[
        "residential","apartment","flat","villa","house","builder floor","residence",
        "residential floor","independent floor"
    ]
    if any(x in v for x in commercial):
        return "COMMERCIAL"
    if any(x in v for x in residential):
        return "RESIDENTIAL"
    return "UNKNOWN"


def _canonical_city(value):
    v=_norm(value)
    if not v:
        return None
    if "delhi ncr" in v or v=="ncr" or "national capital region" in v:
        return "NCR"
    if "gurgaon" in v or "gurugram" in v:
        return "GURUGRAM"
    if "greater noida" in v:
        return "GREATER_NOIDA"
    if "noida" in v:
        return "NOIDA"
    if "faridabad" in v:
        return "FARIDABAD"
    if "ghaziabad" in v:
        return "GHAZIABAD"
    if "new delhi" in v or v=="delhi" or v.endswith(" delhi") or v.startswith("delhi "):
        return "DELHI"
    return v.upper().replace(" ","_")


_SOUTH_DELHI_ALIASES={
    "south delhi","greater kailash","gk","gk 1","gk1","gk 2","gk2",
    "defence colony","defense colony","south extension","south ex",
    "hauz khas","green park","saket","vasant kunj","lajpat nagar",
    "new friends colony","nfc","kalkaji","nehru place","malviya nagar",
    "cr park","chittaranjan park","panchsheel","panchsheel park",
    "safdarjung","safdarjung enclave","vasant vihar","east of kailash",
    "greater kailash 1","greater kailash 2"
}


def _south_delhi_match(requirement_location, property_location):
    rq=_norm(requirement_location)
    pl=_norm(property_location)
    if "south delhi" not in rq:
        return False
    return any(alias in pl for alias in _SOUTH_DELHI_ALIASES if alias!="south delhi") or "south delhi" in pl


def _location_similarity(a,b):
    ta=_tokens(a); tb=_tokens(b)
    if not ta or not tb:
        return 0.0,[]
    overlap=ta & tb
    union=ta | tb
    score=len(overlap)/max(1,len(union))
    na=_norm(a); nb=_norm(b)
    if na and nb and (na in nb or nb in na):
        score=max(score,0.90)
    if _south_delhi_match(a,b):
        score=max(score,0.92)
    return score,sorted(overlap)


def _phone_key(value):
    digits="".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[-10:] if len(digits)>=10 else digits


def _is_restaurant_requirement(q):
    text_value=" ".join(str(q.get(k) or "") for k in [
        "property_type","suitable_category","additional_points","requirement_type"
    ])
    v=_norm(text_value)
    return any(x in v for x in [
        "restaurant","food","f&b","fnb","qsr","cafe","café","lounge","club"
    ])


def _restaurant_compatible(p):
    text_value=" ".join(str(p.get(k) or "") for k in [
        "property_type","suitable_category","remarks"
    ])
    v=_norm(text_value)
    if _property_class(text_value)=="RESIDENTIAL":
        return False
    return any(x in v for x in [
        "restaurant","food","f&b","fnb","qsr","cafe","café","lounge","club",
        "retail","shop","showroom","commercial"
    ])


def _requirement_area_window(q):
    mn=_float(q.get("minimum_area_sqft"))
    mx=_float(q.get("maximum_area_sqft"))
    if mn is None and mx is None:
        return None,None,None
    if mn is not None and mx is not None:
        base_lo=min(mn,mx); base_hi=max(mn,mx); target=(base_lo+base_hi)/2.0
    else:
        target=mn if mn is not None else mx
        base_lo=target; base_hi=target
    return max(0.0,base_lo*0.80),base_hi*1.20,target


def _property_area(p):
    for key in ["available_area_sqft","area_sqft","maximum_area_sqft","minimum_area_sqft"]:
        value=_float(p.get(key))
        if value is not None and value>0:
            return value
    return None


def _budget_max(q):
    for key in ["budget_max","monthly_rent","maximum_rent","rent_budget"]:
        value=_float(q.get(key))
        if value is not None and value>0:
            return value
    return None


def _property_monthly_rent(p):
    value=_float(p.get("monthly_rent"))
    if value is not None and value>0:
        return value
    psf=_float(p.get("asking_rent_per_sqft"))
    area=_property_area(p)
    if psf is not None and area is not None and psf>0 and area>0:
        return psf*area
    return None


def _match_band(score):
    if score>=90: return "EXCELLENT"
    if score>=80: return "STRONG"
    if score>=70: return "GOOD"
    return "POSSIBLE"


def _area_match_score(target,area):
    if target is None or area is None or target<=0:
        return 10
    ratio=abs(area-target)/target
    if ratio<=0.05: return 25
    if ratio<=0.10: return 23
    if ratio<=0.15: return 21
    if ratio<=0.20: return 18
    return 0


def _location_match_score(q,p):
    q_city=_canonical_city(q.get("city"))
    p_city=_canonical_city(p.get("city"))
    q_loc=q.get("preferred_locations") or ""
    p_loc=" ".join(str(x or "") for x in [p.get("location"),p.get("micro_market")])
    city_points=0
    location_points=0
    if not q_city:
        city_points=5
    elif q_city=="NCR":
        if p_city in {"DELHI","GURUGRAM","NOIDA","GREATER_NOIDA","FARIDABAD","GHAZIABAD","NCR"}:
            city_points=10
    elif p_city==q_city:
        city_points=10
    if not q_loc:
        location_points=10
    elif _south_delhi_match(q_loc,p_loc):
        location_points=20
    else:
        sim,_overlap=_location_similarity(q_loc,p_loc)
        if sim>=0.85: location_points=20
        elif sim>=0.60: location_points=17
        elif sim>=0.30: location_points=12
        elif sim>0: location_points=6
    return min(30,city_points+location_points)


def _property_type_score(q,p):
    q_text=" ".join(str(q.get(k) or "") for k in ["property_type","suitable_category","additional_points"])
    p_text=" ".join(str(p.get(k) or "") for k in ["property_type","suitable_category","remarks"])
    q_class=_property_class(q_text)
    p_class=_property_class(p_text)
    qt=_type_family(q_text)
    pt=_type_family(p_text)
    if q_class!="UNKNOWN" and q_class==p_class:
        if qt and pt and qt & pt: return 15
        return 12
    if qt and pt and qt & pt: return 12
    if q_class=="UNKNOWN" or p_class=="UNKNOWN": return 6
    return 0


def _suitable_use_score(q,p):
    if _is_restaurant_requirement(q):
        return 15 if _restaurant_compatible(p) else 0
    q_cat=_type_family(q.get("suitable_category"))
    p_cat=_type_family(" ".join(str(x or "") for x in [
        p.get("suitable_category"),p.get("property_type"),p.get("remarks")
    ]))
    if not q_cat: return 8
    if q_cat and p_cat and q_cat & p_cat: return 15
    return 5 if not p_cat else 0


def _budget_score(q,p):
    maximum=_budget_max(q)
    rent=_property_monthly_rent(p)
    if maximum is None: return 5
    if rent is None: return 3
    ratio=rent/maximum if maximum else 999
    if ratio<=1.00: return 10
    if ratio<=1.05: return 9
    if ratio<=1.10: return 7
    if ratio<=1.20: return 4
    return 0


def _verification_score(p):
    return 5 if _norm(p.get("verification_status"))=="verified" else 1


def _hard_filter_property(q,p):
    exclusions=[]

    quality=_organize_property_v4(p)
    if not quality["match_eligible"]:
        exclusions.append("MATCH_DATA_INCOMPLETE")
    request_phones=_contact_number_set(q.get("contact_phone"))
    property_phones=_contact_number_set(
        p.get("owner_contact"),p.get("broker_contact"),p.get("contact_number"),
        p.get("owner_contact_normalized"),p.get("broker_contact_normalized"),p.get("general_contact_normalized")
    )
    if request_phones and property_phones and (request_phones & property_phones):
        exclusions.append("SELF_INVENTORY")

    availability=_norm(p.get("availability_status") or "available")
    if any(x in availability for x in [
        "unavailable","sold","leased","not available","inactive","removed","closed"
    ]):
        exclusions.append("NOT_AVAILABLE")

    q_tx=_transaction_family(q.get("rent_or_sale"))
    p_tx=_transaction_family(p.get("rent_or_sale"))
    p_tx_raw=_norm(p.get("rent_or_sale"))
    if q_tx and p_tx:
        sale_or_rent=any(x in p_tx_raw for x in ["sale or rent","rent or sale","sale/rent","rent/sale"])
        if q_tx!=p_tx and not sale_or_rent:
            exclusions.append("TRANSACTION_MISMATCH")

    q_class=_property_class(q.get("property_type"),q.get("suitable_category"),q.get("additional_points"),q.get("requirement_type"))
    p_class=_property_class(p.get("property_type"),p.get("suitable_category"),p.get("remarks"))
    if q_class!="UNKNOWN" and p_class!="UNKNOWN" and q_class!=p_class:
        exclusions.append("PROPERTY_CLASS_MISMATCH")

    if _is_restaurant_requirement(q) and not _restaurant_compatible(p):
        exclusions.append("USE_MISMATCH")

    q_city=_canonical_city(q.get("city"))
    p_city=_canonical_city(p.get("city"))
    if q_city and q_city!="NCR" and p_city and p_city!="NCR" and q_city!=p_city:
        exclusions.append("CITY_MISMATCH")

    q_loc=q.get("preferred_locations") or ""
    p_loc=" ".join(str(x or "") for x in [p.get("location"),p.get("micro_market")])
    if "south delhi" in _norm(q_loc) and p_loc and not _south_delhi_match(q_loc,p_loc):
        exclusions.append("LOCATION_MISMATCH")

    hard_lo,hard_hi,_target=_requirement_area_window(q)
    area=_property_area(p)
    if hard_lo is not None and hard_hi is not None and area is not None:
        if area<hard_lo or area>hard_hi:
            exclusions.append("AREA_OUTSIDE_20_PERCENT_RANGE")

    return exclusions


def robust_match_requirement(rid,create_whatsapp=False):
    with engine.begin() as c:
        qrow=c.execute(text("SELECT * FROM pi_requirements WHERE requirement_id=:id"),{"id":rid}).first()
        if not qrow:
            raise HTTPException(404,"Requirement not found")
        q=dict(qrow._mapping)
        all_rows=c.execute(text("SELECT * FROM pi_properties ORDER BY created_at DESC")).fetchall()
        all_count=len(all_rows)
        c.execute(text("DELETE FROM pi_matches WHERE requirement_id=:id"),{"id":rid})

        eligible=[]
        excluded=[]
        exclusion_counts={}
        _hard_lo,_hard_hi,target_area=_requirement_area_window(q)

        for row in all_rows:
            p=dict(row._mapping)
            exclusion_reasons=_hard_filter_property(q,p)
            if exclusion_reasons:
                for reason in exclusion_reasons:
                    exclusion_counts[reason]=exclusion_counts.get(reason,0)+1
                excluded.append({
                    "property_id":p.get("property_id"),
                    "property_name":p.get("property_name"),
                    "city":p.get("city"),
                    "location":p.get("location"),
                    "property_type":p.get("property_type"),
                    "available_area_sqft":_property_area(p),
                    "source":p.get("source"),
                    "reasons":exclusion_reasons
                })
                continue

            area=_property_area(p)
            breakdown={
                "location":_location_match_score(q,p),
                "area":_area_match_score(target_area,area),
                "property_type":_property_type_score(q,p),
                "suitable_use":_suitable_use_score(q,p),
                "budget":_budget_score(q,p),
                "verification":_verification_score(p)
            }
            score=round(sum(breakdown.values()),2)
            reasons=[]
            if breakdown["location"]>=22: reasons.append("Strong location")
            elif breakdown["location"]>=15: reasons.append("Location compatible")
            if breakdown["area"]>=21: reasons.append("Strong area fit")
            elif breakdown["area"]>=18: reasons.append("Area within 20% tolerance")
            if breakdown["property_type"]>=12: reasons.append("Property type compatible")
            if breakdown["suitable_use"]>=12: reasons.append("Suitable use compatible")
            if breakdown["budget"]>=7: reasons.append("Budget/rent compatible")
            if breakdown["verification"]==5: reasons.append("Verified")

            gaps=[]
            if not p.get("location"): gaps.append("Property location missing")
            if area is None: gaps.append("Property area missing")
            if not p.get("rent_or_sale"): gaps.append("Property Rent/Sale missing")
            if _property_class(p.get("property_type"),p.get("suitable_category"),p.get("remarks"))=="UNKNOWN":
                gaps.append("Property class unclear")
            if _norm(p.get("verification_status"))!="verified":
                gaps.append("Availability not verified")

            eligible.append({
                "property_id":p.get("property_id"),
                "property_name":p.get("property_name"),
                "city":p.get("city"),
                "location":p.get("location"),
                "micro_market":p.get("micro_market"),
                "available_area_sqft":area,
                "monthly_rent":_property_monthly_rent(p),
                "rent_or_sale":p.get("rent_or_sale"),
                "property_type":p.get("property_type"),
                "suitable_category":p.get("suitable_category"),
                "verification_status":p.get("verification_status"),
                "owner_name":p.get("owner_name"),
                "owner_contact":p.get("owner_contact_normalized") or p.get("owner_contact"),
                "broker_name":p.get("broker_name"),
                "broker_contact":p.get("broker_contact_normalized") or p.get("broker_contact"),
                "contact_number":p.get("general_contact_normalized") or p.get("contact_number"),
                "score":score,
                "match_band":_match_band(score),
                "score_breakdown":breakdown,
                "reasons":reasons,
                "gaps":gaps
            })

        eligible.sort(key=lambda x:(x["score"],1 if _norm(x.get("verification_status"))=="verified" else 0),reverse=True)
        matches=eligible[:50]

        for rank,x in enumerate(matches,1):
            sql_insert="INSERT INTO pi_matches(requirement_id,property_id,match_score,rank,match_reasons,status) VALUES(:r,:p,:s,:rank,CAST(:reason AS JSONB),:status)"
            c.execute(
                text(sql_insert),
                {
                    "r":rid,
                    "p":x["property_id"],
                    "s":x["score"],
                    "rank":rank,
                    "reason":json.dumps({
                        "reasons":x["reasons"],
                        "score_breakdown":x["score_breakdown"],
                        "match_band":x["match_band"]
                    }),
                    "status":"READY_FOR_REVIEW"
                }
            )

    if all_count==0:
        msg="No properties exist in the database. Add/upload inventory first."
    elif not matches:
        msg="No eligible property passed the mandatory hard filters. Review Excluded Inventory below."
    else:
        msg=f"{len(matches)} eligible properties found after hard filtering. Highest scores are shown first."

    diagnostic={
        "engine":"MATCHING_V2",
        "database_properties":int(all_count),
        "active_properties_checked":int(all_count),
        "eligible_count":len(eligible),
        "excluded_count":len(excluded),
        "matches_returned":len(matches),
        "fallback_low_confidence":False,
        "area_rule":"Default hard tolerance is 80%-120% of the requirement",
        "weights":{"location":30,"area":25,"property_type":15,"suitable_use":15,"budget":10,"verification":5},
        "exclusion_counts":exclusion_counts,
        "requirement":{
            "requirement_id":rid,
            "city":q.get("city"),
            "preferred_locations":q.get("preferred_locations"),
            "minimum_area_sqft":_float(q.get("minimum_area_sqft")),
            "maximum_area_sqft":_float(q.get("maximum_area_sqft")),
            "rent_or_sale":q.get("rent_or_sale"),
            "property_type":q.get("property_type"),
            "suitable_category":q.get("suitable_category")
        },
        "message":msg
    }

    draft=None
    if create_whatsapp and matches:
        with engine.connect() as c:
            property_map={}
            for x in matches[:10]:
                if _norm(x.get("verification_status"))!="verified":
                    continue
                rr=c.execute(text("SELECT * FROM pi_properties WHERE property_id=:id"),{"id":x["property_id"]}).first()
                if rr:
                    property_map[x["property_id"]]=dict(rr._mapping)
        tops=[property_map[x["property_id"]] for x in matches[:10] if x["property_id"] in property_map][:5]
        if tops:
            message,provider=generate_whatsapp_message(q,tops)
            did=store_whatsapp_draft(q,message,provider)
            draft={"id":did,"status":"READY_FOR_REVIEW","message":message,"generated_by":provider}

    return {
        "status":"READY_FOR_REVIEW",
        "engine":"MATCHING_V2",
        "matches":matches,
        "excluded":excluded[:200],
        "diagnostic":diagnostic,
        "whatsapp_draft":draft
    }

@app.post("/api/v4/properties/{property_id}/availability-verification")
def set_property_availability_verification(property_id:str, req:Request, status:str=Form(...)):
    need_login(req)
    status=str(status or "").strip().upper()
    if status not in {"VERIFIED","UNVERIFIED"}: raise HTTPException(400,"Invalid verification status")
    with engine.begin() as c:
        if not c.execute(text("SELECT 1 FROM pi_properties WHERE property_id=:id"),{"id":property_id}).first():
            raise HTTPException(404,"Property not found")
        c.execute(text("UPDATE pi_properties SET verification_status=:s, updated_at=NOW() WHERE property_id=:id"),
                  {"s":status,"id":property_id})
    return {"status":"OK","property_id":property_id,"verification_status":status}

def _property_db_html_value(value):
    if value is None:
        return ""
    if isinstance(value,(dict,list)):
        try:
            return json.dumps(value,ensure_ascii=False,default=str)
        except Exception:
            return str(value)
    return str(value)


@app.get("/property-database",response_class=HTMLResponse)
def property_database_page(req:Request):
    role=page_role_or_redirect(req)
    if not role:
        return RedirectResponse("/login",status_code=303)

    with engine.connect() as c:
        total=int(c.execute(text("SELECT COUNT(*) FROM pi_properties")).scalar_one())
        verified=int(c.execute(text("SELECT COUNT(*) FROM pi_properties WHERE UPPER(COALESCE(verification_status,''))='VERIFIED'")).scalar_one())
        available=int(c.execute(text("""SELECT COUNT(*) FROM pi_properties
            WHERE UPPER(TRIM(COALESCE(availability_status,'AVAILABLE')))
            NOT IN ('UNAVAILABLE','LEASED','SOLD','INACTIVE','REMOVED','NOT AVAILABLE')""")).scalar_one())
        rows=c.execute(text("""SELECT *
            FROM pi_properties
            ORDER BY created_at DESC
            LIMIT 5000""")).fetchall()

    records=[dict(r._mapping) for r in rows]
    row_html=[]
    for p in records:
        pid=_property_db_html_value(p.get("property_id"))
        pname=_property_db_html_value(p.get("property_name")) or pid
        city=_property_db_html_value(p.get("city"))
        loc=_property_db_html_value(p.get("location"))
        ptype=_property_db_html_value(p.get("property_type"))
        area=_property_db_html_value(p.get("available_area_sqft"))
        rent=_property_db_html_value(p.get("monthly_rent") or p.get("asking_rent_per_sqft"))
        availability=_property_db_html_value(p.get("availability_status"))
        verification=_property_db_html_value(p.get("verification_status"))
        owner=_property_db_html_value(p.get("owner_name"))
        owner_phone=_property_db_html_value(p.get("owner_contact"))
        broker=_property_db_html_value(p.get("broker_name"))
        broker_phone=_property_db_html_value(p.get("broker_contact"))
        source=_property_db_html_value(p.get("source"))
        created=_property_db_html_value(p.get("created_at"))
        search_blob=" ".join([pid,pname,city,loc,ptype,availability,verification,owner,owner_phone,broker,broker_phone,source]).lower()
        row_html.append(
            f"""<tr data-search="{escape(search_blob)}">
            <td><a class="recordlink" href="/property-record/{quote_plus(pid)}"><b>{escape(pname)}</b><br><small>{escape(pid)}</small></a></td>
            <td>{escape(city)}</td><td>{escape(loc)}</td><td>{escape(ptype)}</td>
            <td>{escape(area)}</td><td>{escape(rent)}</td>
            <td>{escape(availability)}</td><td>{escape(verification)}</td>
            <td>{escape(owner)}<br><small>{escape(owner_phone)}</small></td>
            <td>{escape(broker)}<br><small>{escape(broker_phone)}</small></td>
            <td>{escape(source)}</td><td>{escape(created)}</td>
            <td><a class="btn" href="/property-record/{quote_plus(pid)}">View Full Property</a></td>
            </tr>"""
        )

    return HTMLResponse(f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Full Property Database</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f4f7fb;font-family:Arial,sans-serif;color:#142033}}
header{{background:#0d1d2d;color:white;padding:16px 22px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}}
.wrap{{padding:20px;max-width:1800px;margin:auto}}.kpis{{display:grid;grid-template-columns:repeat(3,minmax(160px,1fr));gap:12px;margin:16px 0}}
.kpi,.card{{background:white;border:1px solid #e4eaf1;border-radius:12px;padding:15px}}.kpi b{{font-size:28px;display:block;margin-top:6px}}
.toolbar{{display:flex;gap:9px;flex-wrap:wrap;align-items:center;margin:12px 0}}input{{padding:11px;border:1px solid #ccd7e4;border-radius:8px;min-width:300px;flex:1}}
.btn{{display:inline-block;background:#1677ff;color:white;padding:8px 11px;border-radius:7px;text-decoration:none;font-weight:700;font-size:12px}}
.btn.gray{{background:#edf2f7;color:#24364b}}.tablewrap{{overflow:auto;max-height:70vh;border:1px solid #e4eaf1;border-radius:10px}}
table{{width:100%;border-collapse:collapse;background:white;font-size:12px}}th,td{{padding:9px;border-bottom:1px solid #edf1f5;text-align:left;white-space:nowrap;vertical-align:top}}
th{{position:sticky;top:0;background:#f8fafc;z-index:1}}.recordlink{{color:#135fb8;text-decoration:none}}small{{color:#6d7b90}}
@media(max-width:760px){{.kpis{{grid-template-columns:1fr}}input{{min-width:100%}}}}
</style></head>
<body>
<header><div><b>Full Property Database</b><br><small style="color:#b9c8d8">Master inventory saved in Property Intelligence</small></div>
<div><a class="btn gray" href="/workspace">Back to Workspace</a> <a class="btn gray" href="/data-quality">Data Quality</a> <a class="btn" href="/property-manual">Add Property</a></div></header>
<div class="wrap">
<div class="kpis">
<div class="kpi"><span>TOTAL PROPERTIES</span><b id="totalCount">{total}</b></div>
<div class="kpi"><span>AVAILABLE</span><b>{available}</b></div>
<div class="kpi"><span>VERIFIED</span><b>{verified}</b></div>
</div>
<div class="card">
<div class="toolbar"><input id="propertySearch" placeholder="Search Property ID, name, city, location, type, owner, broker, phone or source">
<span id="visibleCount"><b>{len(records)}</b> records shown</span></div>
<div class="tablewrap"><table>
<thead><tr><th>Property / ID</th><th>City</th><th>Location</th><th>Type</th><th>Area</th><th>Rent</th><th>Availability</th><th>Verification</th><th>Owner</th><th>Broker</th><th>Source</th><th>Saved</th><th>Action</th></tr></thead>
<tbody id="propertyRows">{''.join(row_html)}</tbody>
</table></div></div></div>
<script>
const s=document.getElementById('propertySearch'),rows=[...document.querySelectorAll('#propertyRows tr')],count=document.getElementById('visibleCount');
function filterRows(){{const q=(s.value||'').toLowerCase().trim();let n=0;rows.forEach(r=>{{let show=!q||(r.dataset.search||'').includes(q);r.style.display=show?'':'none';if(show)n++}});count.innerHTML='<b>'+n+'</b> records shown'}}
s.addEventListener('input',filterRows);
</script></body></html>""")


@app.get("/property-record/{property_id}",response_class=HTMLResponse)
def property_record_page(property_id:str,req:Request):
    role=page_role_or_redirect(req)
    if not role:
        return RedirectResponse("/login",status_code=303)

    with engine.connect() as c:
        row=c.execute(text("SELECT * FROM pi_properties WHERE property_id=:id"),{"id":property_id}).first()
        if not row:
            raise HTTPException(404,"Property not found")
        p=dict(row._mapping)

        media=[]
        for table_name in ["pi_property_media","pi_media"]:
            try:
                rr=c.execute(text(f"SELECT * FROM {table_name} WHERE property_id=:id ORDER BY created_at DESC"),{"id":property_id}).fetchall()
                media.extend([dict(x._mapping) for x in rr])
            except Exception:
                pass

        try:
            verification=[dict(x._mapping) for x in c.execute(
                text("SELECT * FROM pi_verification_log WHERE property_id=:id ORDER BY created_at DESC LIMIT 100"),
                {"id":property_id}
            ).fetchall()]
        except Exception:
            verification=[]

        try:
            matches=[dict(x._mapping) for x in c.execute(
                text("""SELECT m.*,r.client_name,r.company_name,r.city AS requirement_city,
                    r.preferred_locations,r.minimum_area_sqft,r.maximum_area_sqft
                    FROM pi_matches m
                    LEFT JOIN pi_requirements r ON r.requirement_id=m.requirement_id
                    WHERE m.property_id=:id ORDER BY m.created_at DESC LIMIT 100"""),
                {"id":property_id}
            ).fetchall()]
        except Exception:
            matches=[]

    preferred_order=[
        "property_id","property_name","property_type","entry_status","availability_status",
        "verification_status","city","location","micro_market","address","google_maps_pin",
        "area_sqft","available_area_sqft","minimum_area_sqft","maximum_area_sqft","floor",
        "rent_or_sale","monthly_rent","asking_rent_per_sqft","asking_sale_price","possession",
        "nearby_brands","suitable_category","parking","ceiling_height","power_load",
        "cam_per_sqft","security_deposit","frontage","assigned_to","verified_date","verified_by",
        "remarks","source","source_id","extraction_confidence","created_at","updated_at"
    ]
    private_order=[
        "owner_name","owner_contact","owner_email","broker_name","broker_contact",
        "broker_email","broker_company","contact_number"
    ]

    def field_rows(keys):
        out=[]
        for key in keys:
            if key in p:
                val=_property_db_html_value(p.get(key))
                out.append(f"<tr><th>{escape(key.replace('_',' ').title())}</th><td>{escape(val)}</td></tr>")
        return "".join(out)

    used=set(preferred_order+private_order)
    other_keys=[k for k in p.keys() if k not in used and k not in {"fingerprint"}]
    general_html=field_rows(preferred_order+other_keys)
    private_html=field_rows(private_order)

    media_html=[]
    for m in media:
        mid=_property_db_html_value(m.get("media_id") or m.get("id"))
        filename=_property_db_html_value(m.get("filename") or m.get("title"))
        mtype=_property_db_html_value(m.get("media_type"))
        url=_property_db_html_value(m.get("url"))
        if url:
            media_html.append(f'<div class="media"><b>{escape(mtype)}</b><br>{escape(filename)}<br><a target="_blank" href="{escape(url)}">Open Media</a></div>')
        else:
            media_html.append(f'<div class="media"><b>{escape(mtype)}</b><br>{escape(filename)}<br><small>Stored media ID: {escape(mid)}</small></div>')

    verification_html="".join(
        f"<tr><td>{escape(_property_db_html_value(v.get('created_at')))}</td><td>{escape(_property_db_html_value(v.get('action')))}</td><td>{escape(_property_db_html_value(v.get('performed_by')))}</td><td>{escape(_property_db_html_value(v.get('notes')))}</td></tr>"
        for v in verification
    ) or '<tr><td colspan="4">No verification history saved.</td></tr>'

    matches_html="".join(
        f"<tr><td>{escape(_property_db_html_value(m.get('requirement_id')))}</td><td>{escape(_property_db_html_value(m.get('company_name') or m.get('client_name')))}</td><td>{escape(_property_db_html_value(m.get('requirement_city')))}</td><td>{escape(_property_db_html_value(m.get('preferred_locations')))}</td><td>{escape(_property_db_html_value(m.get('match_score')))}</td><td>{escape(_property_db_html_value(m.get('status')))}</td></tr>"
        for m in matches
    ) or '<tr><td colspan="6">This property has no saved match history.</td></tr>'

    title=_property_db_html_value(p.get("property_name")) or property_id
    return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)} - Property Record</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f4f7fb;font-family:Arial,sans-serif;color:#142033}}header{{background:#0d1d2d;color:white;padding:16px 22px;display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}}
.wrap{{max-width:1500px;margin:auto;padding:20px}}.card{{background:white;border:1px solid #e4eaf1;border-radius:12px;padding:16px;margin:14px 0}}.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:9px;border-bottom:1px solid #edf1f5;text-align:left;vertical-align:top}}th{{width:240px;background:#fafbfd}}
.btn{{display:inline-block;background:#1677ff;color:white;padding:9px 12px;border-radius:7px;text-decoration:none;font-weight:700}}.btn.gray{{background:#edf2f7;color:#24364b}}.private{{border-left:5px solid #df8b13}}.mediaGrid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px}}.media{{border:1px solid #e4eaf1;border-radius:9px;padding:10px}}
.tablewrap{{overflow:auto}}.badge{{display:inline-block;background:#eaf8f2;color:#086d49;padding:5px 8px;border-radius:20px;font-weight:700}}
@media(max-width:900px){{.grid2{{grid-template-columns:1fr}}}}
</style></head><body>
<header><div><b>{escape(title)}</b><br><small>{escape(property_id)}</small></div>
<div><a class="btn gray" href="/workspace">Workspace</a> <a class="btn" href="/property-database">Full Property Database</a></div></header>
<div class="wrap">
<div class="card"><h2>Property Record</h2><span class="badge">{escape(_property_db_html_value(p.get("verification_status") or "UNVERIFIED"))}</span>
<div class="tablewrap"><table>{general_html}</table></div></div>
<div class="card private"><h2>Private / Internal Contact Details</h2><p>For team verification and follow-up. Do not automatically include these contacts in client-facing WhatsApp drafts.</p>
<div class="tablewrap"><table>{private_html or '<tr><td>No owner/broker contact details saved.</td></tr>'}</table></div></div>
<div class="card"><h2>Property Media</h2><div class="mediaGrid">{''.join(media_html) or 'No media saved for this property.'}</div></div>
<div class="grid2">
<div class="card"><h2>Verification History</h2><div class="tablewrap"><table><thead><tr><th>Date</th><th>Action</th><th>By</th><th>Notes</th></tr></thead><tbody>{verification_html}</tbody></table></div></div>
<div class="card"><h2>Match History</h2><div class="tablewrap"><table><thead><tr><th>Requirement</th><th>Client</th><th>City</th><th>Location</th><th>Score</th><th>Status</th></tr></thead><tbody>{matches_html}</tbody></table></div></div>
</div></div></body></html>""")


# ============================================================
# DATA ORGANIZER V4
# Deterministic cleanup only. Never invents missing phone digits.
# ============================================================

_PHONE_INVALID_MARKERS={"","na","n/a","none","null","unknown","not available","not specified","-"}

def _dq_text(value):
    return str(value or "").strip()

def _dq_unknown(value):
    return _dq_text(value).lower() in _PHONE_INVALID_MARKERS

def _extract_contact_numbers(raw):
    s=_dq_text(raw)
    if not s or _dq_unknown(s):
        return {"mobiles":[],"landlines":[],"invalid_raw":s}

    mobiles=[]
    landlines=[]

    mobile_pat=re.compile(r'(?<!\d)(?:\+?91[\s\-]*)?([6-9](?:[\s\-]*\d){9})(?!\d)')
    for m in mobile_pat.finditer(s):
        digits=re.sub(r'\D','',m.group(1))
        if len(digits)==10 and digits[0] in "6789" and digits not in mobiles:
            mobiles.append(digits)

    land_pat=re.compile(r'(?<!\d)(0\d(?:[\s\-]*\d){8,9})(?!\d)')
    for m in land_pat.finditer(s):
        digits=re.sub(r'\D','',m.group(1))
        if len(digits) in (10,11) and digits.startswith("0") and digits not in landlines:
            landlines.append(digits)

    return {"mobiles":mobiles,"landlines":landlines,"invalid_raw":s}

def _contact_display(info):
    values=list(info.get("mobiles") or [])+list(info.get("landlines") or [])
    return ", ".join(values) if values else None

def _contact_number_set(*values):
    out=set()
    for value in values:
        info=_extract_contact_numbers(value)
        out.update(info["mobiles"])
        out.update(info["landlines"])
    return out

def _canonical_city_v4(value):
    v=_norm(value)
    if not v or v in {"na","n a","unknown","not specified","none"}:
        return "UNKNOWN"
    if "new delhi" in v or v=="delhi" or v.endswith(" delhi") or v.startswith("delhi "):
        return "DELHI"
    if "gurgaon" in v or "gurugram" in v:
        return "GURUGRAM"
    if "greater noida" in v:
        return "GREATER NOIDA"
    if "noida" in v:
        return "NOIDA"
    if "faridabad" in v:
        return "FARIDABAD"
    if "ghaziabad" in v:
        return "GHAZIABAD"
    return _dq_text(value).upper()

def _canonical_property_type_v4(value, suitable=None, remarks=None):
    raw=" ".join(_dq_text(x) for x in [value,suitable,remarks]).lower()
    raw=re.sub(r'\s+',' ',raw).strip()
    if not raw or raw in {"na","n/a","unknown","not specified"}:
        return "UNKNOWN"
    if any(x in raw for x in ["residential","3bhk","4bhk","2bhk","1bhk","apartment","flat","villa","builder floor","house"]):
        return "RESIDENTIAL"
    if any(x in raw for x in ["industrial","factory","warehouse","mohan co-operative","industrial area"]):
        return "INDUSTRIAL"
    if any(x in raw for x in ["office","cowork","business centre"]):
        return "OFFICE"
    if any(x in raw for x in ["shop","retail","showroom","commercial center","commercial centre"]):
        return "RETAIL/COMMERCIAL"
    if any(x in raw for x in ["restaurant","cafe","café","qsr","food","f&b","fnb","lounge","club"]):
        return "F&B/COMMERCIAL"
    if any(x in raw for x in ["banquet","hotel","guest house","hospitality","farmhouse"]):
        return "HOSPITALITY"
    if any(x in raw for x in ["mix land","mixland","mixed use","commercial/residential","commercial residential"]):
        return "MIXED USE"
    if "commercial" in raw or "comercial" in raw:
        return "COMMERCIAL"
    if re.fullmatch(r'[\s+/&\-]*(bmt|basement|gf|ff|sf|tf|terr|floor|unit)[\s+/&\-\w]*',raw,re.I):
        return "UNKNOWN"
    return _dq_text(value).upper()

def _canonical_transaction_v4(value):
    v=_norm(value)
    if not v or v in {"na","n a","unknown","not specified"}:
        return "UNKNOWN"
    has_rent=any(x in v for x in ["rent","lease","leasing"])
    has_sale=any(x in v for x in ["sale","sell","selling"])
    if has_rent and has_sale:
        return "RENT/SALE"
    if has_rent:
        return "RENT"
    if has_sale:
        return "SALE"
    return "UNKNOWN"

def _organize_property_v4(p):
    owner_info=_extract_contact_numbers(p.get("owner_contact"))
    broker_info=_extract_contact_numbers(p.get("broker_contact"))
    general_info=_extract_contact_numbers(p.get("contact_number"))

    valid_contacts=[]
    for info in [owner_info,broker_info,general_info]:
        valid_contacts.extend(info["mobiles"])
        valid_contacts.extend(info["landlines"])
    valid_contacts=list(dict.fromkeys(valid_contacts))

    city=_canonical_city_v4(p.get("city"))
    ptype=_canonical_property_type_v4(p.get("property_type"),p.get("suitable_category"),p.get("remarks"))
    transaction=_canonical_transaction_v4(p.get("rent_or_sale"))
    location=_dq_text(p.get("location") or p.get("micro_market"))
    area=_property_area(p)

    issues=[]
    if city=="UNKNOWN":
        issues.append("MISSING_OR_UNKNOWN_CITY")
    if not location or _dq_unknown(location):
        issues.append("MISSING_OR_UNKNOWN_LOCATION")
    if ptype=="UNKNOWN":
        issues.append("MISSING_OR_AMBIGUOUS_PROPERTY_TYPE")
    if area is None or area<=0:
        issues.append("MISSING_AREA")
    if transaction=="UNKNOWN":
        issues.append("MISSING_TRANSACTION")

    raw_contacts=[
        _dq_text(p.get("owner_contact")),
        _dq_text(p.get("broker_contact")),
        _dq_text(p.get("contact_number"))
    ]
    raw_contacts=[x for x in raw_contacts if x and not _dq_unknown(x)]
    if raw_contacts and not valid_contacts:
        issues.append("INVALID_OR_TRUNCATED_CONTACT")
    elif not raw_contacts:
        issues.append("MISSING_CONTACT")

    for raw in raw_contacts:
        digits=re.sub(r'\D','',raw)
        parsed=_extract_contact_numbers(raw)
        if digits and not parsed["mobiles"] and not parsed["landlines"]:
            if "INVALID_OR_TRUNCATED_CONTACT" not in issues:
                issues.append("INVALID_OR_TRUNCATED_CONTACT")

    match_eligible=not any(x in issues for x in [
        "MISSING_OR_UNKNOWN_CITY","MISSING_OR_UNKNOWN_LOCATION",
        "MISSING_OR_AMBIGUOUS_PROPERTY_TYPE","MISSING_AREA","MISSING_TRANSACTION"
    ])
    contact_ready=bool(valid_contacts)

    if match_eligible and contact_ready:
        status="READY"
    elif match_eligible:
        status="MATCH_READY_CONTACT_REVIEW"
    else:
        status="NEEDS_REVIEW"

    return {
        "canonical_city":city,
        "canonical_property_type":ptype,
        "canonical_transaction":transaction,
        "owner_contact_normalized":_contact_display(owner_info),
        "broker_contact_normalized":_contact_display(broker_info),
        "general_contact_normalized":_contact_display(general_info),
        "normalized_contacts":valid_contacts,
        "quality_issues":issues,
        "data_quality_status":status,
        "match_eligible":match_eligible,
        "contact_ready":contact_ready
    }

def _ensure_data_quality_columns():
    statements=[
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS raw_owner_contact TEXT",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS raw_broker_contact TEXT",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS raw_contact_number TEXT",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS owner_contact_normalized TEXT",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS broker_contact_normalized TEXT",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS general_contact_normalized TEXT",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS normalized_contacts JSONB DEFAULT '[]'::jsonb",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS canonical_city TEXT",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS canonical_property_type TEXT",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS canonical_transaction TEXT",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS data_quality_status TEXT",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS match_eligible BOOLEAN",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS contact_ready BOOLEAN",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS quality_issues JSONB DEFAULT '[]'::jsonb"
    ]
    with engine.begin() as c:
        for stmt in statements:
            c.execute(text(stmt))

def _audit_property_database_v4(apply_changes=False):
    _ensure_data_quality_columns()
    summary={
        "total":0,"ready":0,"match_ready_contact_review":0,"needs_review":0,
        "match_eligible":0,"contact_ready":0,"invalid_or_truncated_contact":0,
        "missing_contact":0,"missing_location":0,"missing_area":0,
        "missing_type":0,"missing_city":0,"missing_transaction":0
    }
    reviewed=[]
    with engine.begin() as c:
        rows=c.execute(text("SELECT * FROM pi_properties ORDER BY created_at DESC")).fetchall()
        for row in rows:
            p=dict(row._mapping)
            q=_organize_property_v4(p)
            summary["total"]+=1
            if q["data_quality_status"]=="READY": summary["ready"]+=1
            elif q["data_quality_status"]=="MATCH_READY_CONTACT_REVIEW": summary["match_ready_contact_review"]+=1
            else: summary["needs_review"]+=1
            if q["match_eligible"]: summary["match_eligible"]+=1
            if q["contact_ready"]: summary["contact_ready"]+=1
            issues=q["quality_issues"]
            if "INVALID_OR_TRUNCATED_CONTACT" in issues: summary["invalid_or_truncated_contact"]+=1
            if "MISSING_CONTACT" in issues: summary["missing_contact"]+=1
            if "MISSING_OR_UNKNOWN_LOCATION" in issues: summary["missing_location"]+=1
            if "MISSING_AREA" in issues: summary["missing_area"]+=1
            if "MISSING_OR_AMBIGUOUS_PROPERTY_TYPE" in issues: summary["missing_type"]+=1
            if "MISSING_OR_UNKNOWN_CITY" in issues: summary["missing_city"]+=1
            if "MISSING_TRANSACTION" in issues: summary["missing_transaction"]+=1

            if len(reviewed)<1000 and issues:
                reviewed.append({
                    "property_id":p.get("property_id"),
                    "property_name":p.get("property_name"),
                    "city":p.get("city"),
                    "location":p.get("location"),
                    "property_type":p.get("property_type"),
                    "area":_property_area(p),
                    "owner_name":p.get("owner_name"),
                    "owner_contact_raw":p.get("owner_contact"),
                    "broker_name":p.get("broker_name"),
                    "broker_contact_raw":p.get("broker_contact"),
                    "normalized_contacts":q["normalized_contacts"],
                    "status":q["data_quality_status"],
                    "issues":issues
                })

            if apply_changes:
                c.execute(text("""UPDATE pi_properties SET
                    raw_owner_contact=COALESCE(raw_owner_contact,owner_contact),
                    raw_broker_contact=COALESCE(raw_broker_contact,broker_contact),
                    raw_contact_number=COALESCE(raw_contact_number,contact_number),
                    owner_contact_normalized=:ocn,
                    broker_contact_normalized=:bcn,
                    general_contact_normalized=:gcn,
                    normalized_contacts=CAST(:contacts AS JSONB),
                    canonical_city=:city,
                    canonical_property_type=:ptype,
                    canonical_transaction=:tx,
                    data_quality_status=:status,
                    match_eligible=:me,
                    contact_ready=:cr,
                    quality_issues=CAST(:issues AS JSONB),
                    updated_at=NOW()
                    WHERE property_id=:id"""),{
                        "ocn":q["owner_contact_normalized"],
                        "bcn":q["broker_contact_normalized"],
                        "gcn":q["general_contact_normalized"],
                        "contacts":json.dumps(q["normalized_contacts"]),
                        "city":q["canonical_city"],
                        "ptype":q["canonical_property_type"],
                        "tx":q["canonical_transaction"],
                        "status":q["data_quality_status"],
                        "me":q["match_eligible"],
                        "cr":q["contact_ready"],
                        "issues":json.dumps(q["quality_issues"]),
                        "id":p.get("property_id")
                    })
    return {"summary":summary,"reviewed":reviewed}

@app.get("/api/v4/data-quality/summary")
def data_quality_summary_v4(req:Request):
    need_login(req)
    return {"status":"ok",**_audit_property_database_v4(False)}

@app.post("/api/v4/data-quality/organize")
def data_quality_organize_v4(req:Request):
    need_login(req)
    result=_audit_property_database_v4(True)
    return {"status":"ok","message":"Database organized safely. Original contacts preserved; no missing digits were guessed.",**result}

@app.get("/data-quality",response_class=HTMLResponse)
def data_quality_page_v4(req:Request):
    role=page_role_or_redirect(req)
    if not role:
        return RedirectResponse("/login",status_code=303)
    return HTMLResponse("""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Property Data Quality</title><style>
*{box-sizing:border-box}body{margin:0;background:#f4f7fb;font-family:Arial,sans-serif;color:#142033}
header{background:#0d1d2d;color:#fff;padding:16px 22px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.wrap{max-width:1700px;margin:auto;padding:20px}.kpis{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:10px}.kpi,.card{background:#fff;border:1px solid #e4eaf1;border-radius:12px;padding:14px;margin-bottom:12px}.kpi b{font-size:25px;display:block;margin-top:5px}
.btn{border:0;border-radius:8px;padding:10px 13px;background:#1677ff;color:white;font-weight:700;cursor:pointer;text-decoration:none;display:inline-block}.btn.orange{background:#df8b13}.btn.gray{background:#edf2f7;color:#24364b}
.tablewrap{overflow:auto;max-height:65vh;border:1px solid #e4eaf1;border-radius:10px}table{width:100%;border-collapse:collapse;background:#fff;font-size:12px}th,td{padding:9px;border-bottom:1px solid #edf1f5;text-align:left;white-space:nowrap;vertical-align:top}th{position:sticky;top:0;background:#f8fafc}.warn{color:#a35c00;font-weight:700}.good{color:#08734b;font-weight:700}
@media(max-width:900px){.kpis{grid-template-columns:repeat(2,1fr)}}
</style></head><body><header><div><b>Property Data Organizer V4</b><br><small>Safe cleanup · multiple contacts · no guessed phone digits</small></div><div><a class="btn gray" href="/workspace">Workspace</a> <a class="btn" href="/property-database">Property Database</a></div></header>
<div class="wrap"><div class="card"><b>Rule:</b> 10-digit Indian mobiles are normalized. Multiple valid numbers are preserved separately. Full landlines are preserved. Truncated numbers such as 98105 remain in the RAW field and are marked for review. We never invent missing digits.</div>
<div class="card"><button class="btn orange" onclick="organize()">Organize Entire Database</button> <button class="btn" onclick="load()">Refresh Audit</button><span id="msg"></span></div>
<div class="kpis" id="kpis"></div>
<div class="card"><h3>Records Needing Review</h3><div class="tablewrap"><table><thead><tr><th>Property</th><th>City</th><th>Location</th><th>Type</th><th>Area</th><th>Owner / Raw Contact</th><th>Broker / Raw Contact</th><th>Valid Contacts Found</th><th>Status</th><th>Issues</th><th>Open</th></tr></thead><tbody id="rows"></tbody></table></div></div></div>
<script>
const esc=x=>String(x??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
async function call(u,o={}){let r=await fetch(u,o),d=await r.json();if(!r.ok)throw new Error(d.detail||d.message||'Request failed');return d}
async function load(){let d=await call('/api/v4/data-quality/summary'),s=d.summary;document.querySelector('#kpis').innerHTML=[
['TOTAL',s.total],['READY',s.ready],['MATCH READY / CONTACT REVIEW',s.match_ready_contact_review],['NEEDS REVIEW',s.needs_review],
['MATCH ELIGIBLE',s.match_eligible],['CONTACT READY',s.contact_ready],['INVALID / TRUNCATED CONTACT',s.invalid_or_truncated_contact],['MISSING CONTACT',s.missing_contact],
['MISSING LOCATION',s.missing_location],['MISSING AREA',s.missing_area],['MISSING TYPE',s.missing_type],['MISSING TRANSACTION',s.missing_transaction]
].map(x=>`<div class="kpi"><span>${x[0]}</span><b>${Number(x[1]||0).toLocaleString()}</b></div>`).join('');
document.querySelector('#rows').innerHTML=(d.reviewed||[]).map(x=>`<tr><td><b>${esc(x.property_name||x.property_id)}</b><br>${esc(x.property_id)}</td><td>${esc(x.city||'')}</td><td>${esc(x.location||'')}</td><td>${esc(x.property_type||'')}</td><td>${esc(x.area||'')}</td><td>${esc(x.owner_name||'')}<br>${esc(x.owner_contact_raw||'')}</td><td>${esc(x.broker_name||'')}<br>${esc(x.broker_contact_raw||'')}</td><td>${esc((x.normalized_contacts||[]).join(', '))}</td><td class="${x.status==='READY'?'good':'warn'}">${esc(x.status)}</td><td>${esc((x.issues||[]).join(', '))}</td><td><a href="/property-record/${encodeURIComponent(x.property_id)}" target="_blank">View</a></td></tr>`).join('')}
async function organize(){if(!confirm('Organize all properties now? Original contacts will be preserved. Missing digits will NOT be guessed.'))return;document.querySelector('#msg').textContent=' Organizing...';try{let d=await call('/api/v4/data-quality/organize',{method:'POST'});document.querySelector('#msg').textContent=' '+d.message;await load()}catch(e){document.querySelector('#msg').textContent=' '+e.message}}
load();
</script></body></html>""")


# ============================================================
# DATA ORGANIZER V5 - INTELLIGENT RECOVERY + RETAIL LINKEDIN DEMAND
# Conservative recovery. Never invents phone digits or unsupported facts.
# ============================================================

_V5_LOCALITY_MAP = {
    "greater kailash":"Greater Kailash","gk 1":"Greater Kailash 1","gk1":"Greater Kailash 1",
    "gk 2":"Greater Kailash 2","gk2":"Greater Kailash 2","kailash colony":"Kailash Colony",
    "east of kailash":"East of Kailash","new friends colony":"New Friends Colony",
    "nfc":"New Friends Colony","safdarjung enclave":"Safdarjung Enclave",
    "safdurjung enclave":"Safdarjung Enclave","green park":"Green Park",
    "vasant vihar":"Vasant Vihar","vasant kunj":"Vasant Kunj","hauz khas":"Hauz Khas",
    "panchsheel park":"Panchsheel Park","panchsheel enclave":"Panchsheel Enclave",
    "defence colony":"Defence Colony","defense colony":"Defence Colony",
    "lajpat nagar":"Lajpat Nagar","jangpura":"Jangpura","nizamuddin":"Nizamuddin",
    "saket":"Saket","malviya nagar":"Malviya Nagar","chittaranjan park":"Chittaranjan Park",
    "cr park":"Chittaranjan Park","nehru place":"Nehru Place","okhla":"Okhla",
    "mathura road":"Mathura Road","mohan co-operative":"Mohan Co-operative",
    "mayapuri":"Mayapuri","pitampura":"Pitampura","sainik farm":"Sainik Farm",
    "gurgaon":"Gurugram","gurugram":"Gurugram","noida":"Noida","faridabad":"Faridabad","ghaziabad":"Ghaziabad"
}

def _v5_source_text(p):
    vals=[
        p.get("property_name"),p.get("property_type"),p.get("city"),p.get("location"),
        p.get("micro_market"),p.get("address"),p.get("remarks"),p.get("suitable_category"),
        p.get("nearby_brands"),p.get("source"),p.get("raw_owner_contact"),
        p.get("raw_broker_contact"),p.get("raw_contact_number")
    ]
    return " | ".join(str(x or "") for x in vals if str(x or "").strip())

def _v5_infer_location(text_value):
    v=_norm(text_value)
    hits=[]
    for key,label in _V5_LOCALITY_MAP.items():
        if key in v:
            hits.append((len(key),label))
    if not hits:
        return None,0
    hits.sort(reverse=True)
    return hits[0][1],95

def _v5_infer_city(text_value, location=None):
    v=_norm((text_value or "")+" "+(location or ""))
    if any(x in v for x in ["gurgaon","gurugram"]): return "Gurugram",98
    if "noida" in v: return "Noida",98
    if "faridabad" in v: return "Faridabad",98
    if "ghaziabad" in v: return "Ghaziabad",98
    if "delhi" in v: return "New Delhi",98
    known_delhi_localities={"Greater Kailash","Greater Kailash 1","Greater Kailash 2","Kailash Colony","East of Kailash","New Friends Colony","Safdarjung Enclave","Green Park","Vasant Vihar","Vasant Kunj","Hauz Khas","Panchsheel Park","Panchsheel Enclave","Defence Colony","Lajpat Nagar","Jangpura","Nizamuddin","Saket","Malviya Nagar","Chittaranjan Park","Nehru Place","Okhla","Mathura Road","Mohan Co-operative","Mayapuri","Pitampura","Sainik Farm"}
    if location in known_delhi_localities: return "New Delhi",96
    return None,0

def _v5_infer_type(text_value):
    v=_norm(text_value)
    rules=[
        (["restaurant","cafe","café","qsr","f&b","fnb","food outlet"],"F&B/COMMERCIAL",98),
        (["shop","showroom","retail","dda mkt","market shop"],"RETAIL/COMMERCIAL",96),
        (["office","business centre","cowork"],"OFFICE",96),
        (["industrial","factory","warehouse"],"INDUSTRIAL",96),
        (["banquet","hotel","guest house","hospitality","farmhouse"],"HOSPITALITY",94),
        (["1bhk","2bhk","3bhk","4bhk","5bhk","apartment","flat","villa","builder floor"],"RESIDENTIAL",96),
        (["commercial"],"COMMERCIAL",92),
    ]
    for terms,label,conf in rules:
        if any(t in v for t in terms):
            return label,conf
    return None,0

def _v5_infer_transaction(text_value):
    v=_norm(text_value)
    rent_terms=["for rent","on rent","to let","lease","leasing","rented","rental","rent @","rent rs","rent ₹"]
    sale_terms=["for sale","sale @","sale rs","asking sale","sell","selling","free hold sale","freehold sale"]
    rent=any(x in v for x in rent_terms)
    sale=any(x in v for x in sale_terms)
    if rent and sale:return "RENT/SALE",95
    if rent:return "RENT",96
    if sale:return "SALE",96
    return None,0

def _v5_infer_area(text_value):
    raw=str(text_value or "")
    patterns=[
        (r'(?i)(\d[\d,]{1,6})\s*(?:sq\.?\s*ft|sqft|sft|square\s*feet|ft2)\b',1.0,"SQFT"),
        (r'(?i)(\d[\d,]{1,5})\s*(?:sq\.?\s*yd|sqyd|sq\.?\s*yard|yards?|yds?)\b',9.0,"SQYD"),
        (r'(?i)\b(\d[\d,]{2,6})\s*ft\b',1.0,"FT")
    ]
    for pat,mult,unit in patterns:
        m=_re.search(pat,raw)
        if m:
            try:
                val=float(m.group(1).replace(",",""))*mult
                if 50 <= val <= 500000:
                    return round(val,2),95,unit
            except Exception:
                pass
    return None,0,None

def _v5_recovery_suggestion(p):
    base=_organize_property_v4(p)
    txt=_v5_source_text(p)
    suggestions={}
    reasons=[]

    current_location=_dq_text(p.get("location") or p.get("micro_market"))
    if not current_location or _dq_unknown(current_location):
        loc,conf=_v5_infer_location(txt)
        if loc:
            suggestions["location"]={"value":loc,"confidence":conf,"reason":"Locality found in stored property/source text"}
            reasons.append("LOCATION_RECOVERABLE")

    current_city=_canonical_city_v4(p.get("city"))
    if current_city=="UNKNOWN":
        loc=(suggestions.get("location") or {}).get("value") or current_location
        city,conf=_v5_infer_city(txt,loc)
        if city:
            suggestions["city"]={"value":city,"confidence":conf,"reason":"City inferred from explicit locality/city text"}
            reasons.append("CITY_RECOVERABLE")

    current_type=_canonical_property_type_v4(p.get("property_type"),p.get("suitable_category"),p.get("remarks"))
    if current_type=="UNKNOWN":
        pt,conf=_v5_infer_type(txt)
        if pt:
            suggestions["property_type"]={"value":pt,"confidence":conf,"reason":"Explicit use/type keyword in stored text"}
            reasons.append("TYPE_RECOVERABLE")

    if _property_area(p) is None:
        area,conf,unit=_v5_infer_area(txt)
        if area:
            suggestions["available_area_sqft"]={"value":area,"confidence":conf,"reason":f"Explicit {unit} area found in stored text"}
            reasons.append("AREA_RECOVERABLE")

    if _canonical_transaction_v4(p.get("rent_or_sale"))=="UNKNOWN":
        tx,conf=_v5_infer_transaction(txt)
        if tx:
            suggestions["rent_or_sale"]={"value":tx,"confidence":conf,"reason":"Explicit rent/lease/sale wording in stored text"}
            reasons.append("TRANSACTION_RECOVERABLE")

    high_conf={k:v for k,v in suggestions.items() if int(v.get("confidence") or 0)>=95}
    return {
        "property_id":p.get("property_id"),
        "property_name":p.get("property_name"),
        "source":p.get("source"),
        "before_status":base["data_quality_status"],
        "issues":base["quality_issues"],
        "suggestions":suggestions,
        "high_confidence_suggestions":high_conf,
        "recoverable":bool(high_conf),
        "reasons":reasons
    }

def _v5_duplicate_key(p):
    loc=_norm(p.get("location") or p.get("micro_market"))
    name=_norm(p.get("property_name"))
    area=_property_area(p)
    contacts=sorted(_contact_number_set(
        p.get("owner_contact"),p.get("broker_contact"),p.get("contact_number"),
        p.get("owner_contact_normalized"),p.get("broker_contact_normalized")
    ))
    contact=contacts[0] if contacts else ""
    if not (loc or name or contact): return None
    return hashlib.sha256(f"{loc}|{name}|{round(area or 0,1)}|{contact}".encode()).hexdigest()

def _v5_audit_recovery(limit=1000):
    rows=[]
    duplicate_groups={}
    counts={"total":0,"recoverable":0,"high_confidence_fields":0,"possible_duplicates":0}
    with engine.connect() as c:
        dbrows=c.execute(text("SELECT * FROM pi_properties ORDER BY created_at DESC")).fetchall()
    for row in dbrows:
        p=dict(row._mapping); counts["total"]+=1
        suggestion=_v5_recovery_suggestion(p)
        if suggestion["recoverable"]:
            counts["recoverable"]+=1
            counts["high_confidence_fields"]+=len(suggestion["high_confidence_suggestions"])
            if len(rows)<limit: rows.append(suggestion)
        k=_v5_duplicate_key(p)
        if k: duplicate_groups.setdefault(k,[]).append(p.get("property_id"))
    dups=[ids for ids in duplicate_groups.values() if len(ids)>1]
    counts["possible_duplicates"]=sum(len(x) for x in dups)
    return {"summary":counts,"recoverable":rows,"duplicate_groups":dups[:300]}

def _v5_apply_recovery():
    audit=_v5_audit_recovery(100000)
    updated_records=updated_fields=0
    with engine.begin() as c:
        for rec in audit["recoverable"]:
            sug=rec["high_confidence_suggestions"]
            if not sug: continue
            sets=[]; params={"id":rec["property_id"]}
            for field in ["city","location","property_type","available_area_sqft","rent_or_sale"]:
                if field in sug:
                    sets.append(f"{field}=:{field}")
                    params[field]=sug[field]["value"]
            if sets:
                sets.append("updated_at=NOW()")
                c.execute(text("UPDATE pi_properties SET "+",".join(sets)+" WHERE property_id=:id"),params)
                updated_records+=1; updated_fields+=len(sets)-1
    normalized=_audit_property_database_v4(True)
    return {"updated_records":updated_records,"updated_fields":updated_fields,"post_normalization":normalized["summary"]}

@app.get("/api/v5/data-recovery/audit")
def v5_data_recovery_audit(req:Request):
    need_login(req)
    return {"status":"ok",**_v5_audit_recovery()}

@app.post("/api/v5/data-recovery/apply")
def v5_data_recovery_apply(req:Request):
    need_login(req)
    result=_v5_apply_recovery()
    return {"status":"ok","message":"High-confidence recoveries applied. No phone digits or unsupported facts were invented.",**result}

@app.get("/data-recovery",response_class=HTMLResponse)
def v5_data_recovery_page(req:Request):
    role=page_role_or_redirect(req)
    if not role:return RedirectResponse("/login",status_code=303)
    return HTMLResponse("""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Data Recovery V5</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f4f7fb;font-family:Arial;color:#142033}header{background:#0d1d2d;color:#fff;padding:16px 22px}.wrap{max-width:1700px;margin:auto;padding:20px}.card,.kpi{background:#fff;border:1px solid #e4eaf1;border-radius:12px;padding:14px;margin-bottom:12px}.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.kpi b{font-size:26px;display:block}.btn{padding:10px 13px;border:0;border-radius:8px;background:#1677ff;color:white;font-weight:700;text-decoration:none;cursor:pointer}.orange{background:#df8b13}.tablewrap{overflow:auto;max-height:65vh}table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:8px;border-bottom:1px solid #edf1f5;text-align:left;vertical-align:top;white-space:nowrap}th{position:sticky;top:0;background:#f8fafc}</style></head>
<body><header><b>Data Organizer V5 · Intelligent Recovery</b></header><div class="wrap">
<div class="card"><a class="btn" href="/data-quality">Data Quality V4</a> <a class="btn" href="/property-database">Property Database</a> <a class="btn" href="/workspace">Workspace</a></div>
<div class="card"><b>Safety rule:</b> V5 applies only high-confidence facts explicitly recoverable from stored property/source text. It never completes truncated phone numbers and never defaults unknown transaction/location.</div>
<div class="card"><button class="btn" onclick="audit()">Run Recovery Audit</button> <button class="btn orange" onclick="apply()">Apply High-Confidence Recovery</button><span id="msg"></span></div>
<div class="kpis" id="kpis"></div>
<div class="card"><h3>Recoverable Records</h3><div class="tablewrap"><table><thead><tr><th>Property</th><th>Source</th><th>Current Issues</th><th>Suggested Recovery</th><th>Open</th></tr></thead><tbody id="rows"></tbody></table></div></div>
</div><script>
const esc=x=>String(x??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
async function api(u,o={}){let r=await fetch(u,o),d=await r.json();if(!r.ok)throw new Error(d.detail||d.message||'Error');return d}
async function audit(){let d=await api('/api/v5/data-recovery/audit'),s=d.summary;document.querySelector('#kpis').innerHTML=[['TOTAL',s.total],['RECOVERABLE',s.recoverable],['FIELDS RECOVERABLE',s.high_confidence_fields],['POSSIBLE DUPLICATE RECORDS',s.possible_duplicates]].map(x=>`<div class="kpi"><span>${x[0]}</span><b>${Number(x[1]||0).toLocaleString()}</b></div>`).join('');document.querySelector('#rows').innerHTML=(d.recoverable||[]).map(x=>`<tr><td><b>${esc(x.property_name||x.property_id)}</b><br>${esc(x.property_id)}</td><td>${esc(x.source||'')}</td><td>${esc((x.issues||[]).join(', '))}</td><td>${Object.entries(x.high_confidence_suggestions||{}).map(([k,v])=>`<b>${esc(k)}</b>: ${esc(v.value)} (${v.confidence}%)`).join('<br>')}</td><td><a target="_blank" href="/property-record/${encodeURIComponent(x.property_id)}">View</a></td></tr>`).join('')}
async function apply(){if(!confirm('Apply only high-confidence recoveries now?'))return;document.querySelector('#msg').textContent=' Working...';let d=await api('/api/v5/data-recovery/apply',{method:'POST'});document.querySelector('#msg').textContent=' '+d.message;await audit()}audit();
</script></body></html>""")

def _ensure_retail_linkedin_campaign():
    name="Retail LinkedIn Requirement Watch"
    with engine.begin() as c:
        row=c.execute(text("SELECT campaign_id FROM ai_requirement_campaigns WHERE campaign_name=:n ORDER BY created_at DESC LIMIT 1"),{"n":name}).first()
        if row:return row[0]
        cid=_new_code("CAM")
        c.execute(text("""INSERT INTO ai_requirement_campaigns(
            campaign_id,campaign_name,property_type,city,location,rent_or_sale,suitable_category,additional_points,status
        ) VALUES(:id,:n,'Retail / Commercial','Delhi NCR','Delhi NCR','Rent','Retail',
        'Auto-created for public/indexed LinkedIn retail leasing requirement signals','ACTIVE')"""),
        {"id":cid,"n":name})
        return cid

def _retail_requirement_score(title,snippet):
    v=_norm((title or "")+" "+(snippet or ""))
    score=0
    intent=["looking for","requirement","required","seeking","need space","needs space","space required","space requirement","looking to lease","want to lease","on lease","for lease"]
    retail=["retail","store","shop","showroom","outlet","brand","qsr","cafe","restaurant"]
    geo=["delhi","delhi ncr","gurgaon","gurugram","noida","faridabad","ghaziabad"]
    role=["business development","bd","expansion","leasing","real estate","property acquisition","projects","store development"]
    if any(x in v for x in intent):score+=40
    if any(x in v for x in retail):score+=20
    if any(x in v for x in geo):score+=20
    if any(x in v for x in role):score+=10
    if any(x in v for x in ["lease","rent"]):score+=10
    return min(100,score)

def _save_retail_linkedin_requirement(item,campaign_id):
    title=item.get("title") or ""; link=item.get("link") or ""; snippet=item.get("snippet") or ""
    if "linkedin.com" not in link.lower():return None
    score=_retail_requirement_score(title,snippet)
    if score<70:return None
    with engine.connect() as c:
        if c.execute(text("SELECT 1 FROM ai_demand_signals WHERE source_url=:u LIMIT 1"),{"u":link}).first():
            return None
    phones,emails=_extract_public_contacts(title+" "+snippet)
    sid=_new_code("DEM")
    with engine.begin() as c:
        c.execute(text("""INSERT INTO ai_demand_signals(
            signal_id,campaign_id,source_type,source_name,source_url,title,excerpt,
            contact_phone,contact_email,location,intent_score,status,match_breakdown,
            contact_verification_status,source_contact_text
        ) VALUES(:sid,:cid,'RETAIL_LINKEDIN_REQUIREMENT','LinkedIn public/indexed',:u,:t,:x,:p,:e,
        'Delhi NCR',:s,'MANUAL_FOLLOWUP_REQUIRED',CAST(:b AS JSONB),:v,:ct)"""),{
            "sid":sid,"cid":campaign_id,"u":link,"t":title,"x":snippet,
            "p":phones[0] if phones else None,"e":emails[0] if emails else None,"s":score,
            "b":json.dumps({"intent_score":score,"signal":"Retail leasing requirement from LinkedIn/public index"}),
            "v":"PUBLIC_SOURCE" if (phones or emails) else "NOT_FOUND","ct":(title+" | "+snippet)[:2500]
        })
    _v6_enrich_retail_signal(sid,title,snippet,link)
    _log_activity("Retail Requirement Bot","DEMAND","LINKEDIN_RETAIL_REQUIREMENT","demand_signal",sid,f"LinkedIn | score {score} | manual follow-up")
    return sid

def _retail_linkedin_requirement_worker(run_id):
    campaign_id=_ensure_retail_linkedin_campaign()
    queries=[
        'site:linkedin.com/posts "looking for" retail space lease Delhi NCR',
        'site:linkedin.com/posts "space requirement" retail lease Delhi NCR',
        'site:linkedin.com/posts "requirement" showroom lease Delhi Gurgaon Noida',
        'site:linkedin.com/posts "looking to lease" store Gurgaon Noida Delhi',
        'site:linkedin.com/posts "business development" "space requirement" retail',
        'site:linkedin.com/posts "expansion" "looking for space" retail Delhi',
        'site:linkedin.com/posts "property acquisition" retail lease Delhi NCR',
        'site:linkedin.com/posts "store development" "requirement" Delhi NCR'
    ]
    found=created=0;errors=[]
    for q in queries:
        try:
            for item in _serper_search(q,10).get("organic",[]):
                found+=1
                sid=_save_retail_linkedin_requirement(item,campaign_id)
                if sid:created+=1
        except Exception as ex:errors.append(str(ex))
    _finish_bot(run_id,"COMPLETED" if created or not errors else "FAILED",found,created,
                f"LinkedIn retail requirement scan: {found} reviewed; {created} manual-follow-up signals saved",
                " | ".join(errors[:5]) or None)

@app.post("/api/v5/retail-linkedin-requirements/start")
def v5_retail_linkedin_requirements(bg:BackgroundTasks,req:Request):
    need_login(req)
    run=_start_bot("Retail LinkedIn Requirement Bot","DEMAND","Scanning public/indexed LinkedIn retail leasing requirements")
    bg.add_task(_retail_linkedin_requirement_worker,run)
    return {"status":"ACCEPTED","run_id":run}


# ============================================================
# V6 ORGANIZED RETAIL REQUIREMENTS
# ============================================================

def _ensure_v6_retail_requirement_columns():
    statements=[
        "ALTER TABLE ai_demand_signals ADD COLUMN IF NOT EXISTS company_name TEXT",
        "ALTER TABLE ai_demand_signals ADD COLUMN IF NOT EXISTS contact_name TEXT",
        "ALTER TABLE ai_demand_signals ADD COLUMN IF NOT EXISTS designation TEXT",
        "ALTER TABLE ai_demand_signals ADD COLUMN IF NOT EXISTS linkedin_profile_url TEXT",
        "ALTER TABLE ai_demand_signals ADD COLUMN IF NOT EXISTS required_area_sqft DOUBLE PRECISION",
        "ALTER TABLE ai_demand_signals ADD COLUMN IF NOT EXISTS required_property_type TEXT",
        "ALTER TABLE ai_demand_signals ADD COLUMN IF NOT EXISTS required_transaction TEXT",
        "ALTER TABLE ai_demand_signals ADD COLUMN IF NOT EXISTS assigned_to TEXT",
        "ALTER TABLE ai_demand_signals ADD COLUMN IF NOT EXISTS followup_status TEXT",
        "ALTER TABLE ai_demand_signals ADD COLUMN IF NOT EXISTS crm_status TEXT",
        "ALTER TABLE ai_demand_signals ADD COLUMN IF NOT EXISTS crm_notes TEXT",
        "ALTER TABLE ai_demand_signals ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW()"
    ]
    with engine.begin() as c:
        for stmt in statements:
            c.execute(text(stmt))

def _v6_extract_retail_requirement_fields(title,snippet):
    raw=(title or "")+" | "+(snippet or "")
    norm=_norm(raw)
    company=_company_guess(title)
    contact_name=None
    designation=None

    m=_re.match(r"^\s*([A-Z][A-Za-z.' -]{2,60})\s*[-|–]\s*(.+)$",(title or "").strip())
    if m:
        tail=_norm(m.group(2))
        if any(role in tail for role in ["business development","expansion","leasing","real estate","property acquisition","store development","projects"]):
            contact_name=m.group(1).strip()

    for key,label in [
        ("business development","Business Development"),
        ("property acquisition","Property Acquisition"),
        ("store development","Store Development"),
        ("expansion","Expansion"),
        ("leasing","Leasing"),
        ("real estate","Real Estate"),
        ("projects","Projects")
    ]:
        if key in norm:
            designation=label
            break

    area=None
    for pat,mult in [
        (r"(?i)(\d[\d,]{2,6})\s*(?:sq\.?\s*ft|sqft|sft|square\s*feet)\b",1.0),
        (r"(?i)(\d[\d,]{2,5})\s*(?:sq\.?\s*yd|sqyd|sq\.?\s*yard|yards?)\b",9.0)
    ]:
        m2=_re.search(pat,raw)
        if m2:
            try:
                area=float(m2.group(1).replace(",",""))*mult
                break
            except Exception:
                pass

    ptype=None
    for terms,label in [
        (["restaurant","cafe","qsr","f&b","food outlet"],"F&B / Restaurant"),
        (["showroom"],"Showroom"),
        (["shop","retail","store","outlet"],"Retail / Store"),
        (["office"],"Office"),
        (["banquet"],"Banquet"),
        (["warehouse"],"Warehouse")
    ]:
        if any(t in norm for t in terms):
            ptype=label
            break

    tx="LEASE" if any(x in norm for x in ["lease","leasing","rent","rental"]) else None
    if company and company.lower().startswith(("linkedin","post by","view")):
        company=None

    return {
        "company_name":company,
        "contact_name":contact_name,
        "designation":designation,
        "required_area_sqft":area,
        "required_property_type":ptype,
        "required_transaction":tx
    }

def _v6_enrich_retail_signal(signal_id,title,snippet,source_url):
    _ensure_v6_retail_requirement_columns()
    f=_v6_extract_retail_requirement_fields(title,snippet)
    profile=None
    u=(source_url or "").lower()
    if "linkedin.com/in/" in u or "linkedin.com/company/" in u:
        profile=source_url

    with engine.begin() as c:
        c.execute(text("""UPDATE ai_demand_signals SET
            company_name=COALESCE(company_name,:company),
            contact_name=COALESCE(contact_name,:contact),
            designation=COALESCE(designation,:designation),
            linkedin_profile_url=COALESCE(linkedin_profile_url,:profile),
            required_area_sqft=COALESCE(required_area_sqft,:area),
            required_property_type=COALESCE(required_property_type,:ptype),
            required_transaction=COALESCE(required_transaction,:tx),
            followup_status=COALESCE(followup_status,'NEW'),
            crm_status=COALESCE(crm_status,'NOT_SENT'),
            updated_at=NOW()
            WHERE signal_id=:id"""),{
            "company":f["company_name"],"contact":f["contact_name"],"designation":f["designation"],
            "profile":profile,"area":f["required_area_sqft"],"ptype":f["required_property_type"],
            "tx":f["required_transaction"],"id":signal_id
        })

def _v6_backfill_retail_signals():
    _ensure_v6_retail_requirement_columns()
    with engine.connect() as c:
        rows=c.execute(text("""SELECT signal_id,title,excerpt,source_url
            FROM ai_demand_signals
            WHERE source_type='RETAIL_LINKEDIN_REQUIREMENT'
               OR source_name ILIKE '%LinkedIn%'""")).fetchall()
    done=0
    for r in rows:
        d=dict(r._mapping)
        _v6_enrich_retail_signal(d.get("signal_id"),d.get("title"),d.get("excerpt"),d.get("source_url"))
        done+=1
    return done

@app.get("/api/v6/retail-requirements")
def v6_retail_requirements(req:Request,limit:int=Query(1000,ge=1,le=3000)):
    need_login(req)
    _ensure_v6_retail_requirement_columns()
    with engine.connect() as c:
        rows=c.execute(text("""SELECT
            signal_id,campaign_id,company_name,contact_name,designation,
            contact_phone,contact_email,linkedin_profile_url,source_url,title,excerpt,
            location,required_area_sqft,required_property_type,required_transaction,
            intent_score,status,contact_verification_status,assigned_to,
            followup_status,crm_status,crm_notes,created_at,updated_at
            FROM ai_demand_signals
            WHERE source_type='RETAIL_LINKEDIN_REQUIREMENT'
               OR source_name ILIKE '%LinkedIn%'
            ORDER BY intent_score DESC,created_at DESC
            LIMIT :n"""),{"n":limit}).fetchall()
    return {"status":"ok","rows":_json_rows(rows)}

@app.post("/api/v6/retail-requirements/backfill")
def v6_retail_requirement_backfill(req:Request):
    need_login(req)
    return {"status":"ok","updated":_v6_backfill_retail_signals()}

@app.post("/api/v6/retail-requirements/{signal_id}/update")
async def v6_update_retail_requirement(signal_id:str,req:Request):
    need_login(req)
    body=await req.json()
    allowed={
        "company_name","contact_name","designation","contact_phone","contact_email",
        "linkedin_profile_url","required_area_sqft","required_property_type",
        "required_transaction","assigned_to","followup_status","crm_status","crm_notes"
    }
    updates={k:v for k,v in body.items() if k in allowed}
    if not updates:
        return {"status":"ok","message":"Nothing to update"}
    sets=[];params={"id":signal_id}
    for k,v in updates.items():
        sets.append(f"{k}=:{k}")
        params[k]=v
    sets.append("updated_at=NOW()")
    with engine.begin() as c:
        c.execute(text("UPDATE ai_demand_signals SET "+",".join(sets)+" WHERE signal_id=:id"),params)
    return {"status":"ok","signal_id":signal_id}

@app.get("/retail-requirements",response_class=HTMLResponse)
def v6_retail_requirements_page(req:Request):
    role=page_role_or_redirect(req)
    if not role:
        return RedirectResponse("/login",status_code=303)
    return HTMLResponse(r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Retail Requirement Leads</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f4f7fb;font-family:Arial;color:#142033}header{background:#0d1d2d;color:#fff;padding:16px 22px}.wrap{padding:18px}.card{background:#fff;border:1px solid #e4eaf1;border-radius:12px;padding:14px;margin-bottom:12px}.toolbar{display:flex;gap:8px;flex-wrap:wrap}.btn{padding:8px 10px;border:0;border-radius:7px;background:#1677ff;color:#fff;font-weight:700;text-decoration:none;cursor:pointer}.gray{background:#edf2f7;color:#24364b}input,select{padding:7px;border:1px solid #ccd7e4;border-radius:6px}.tablewrap{overflow:auto;max-height:72vh}table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:8px;border-bottom:1px solid #edf1f5;text-align:left;vertical-align:top;white-space:nowrap}th{position:sticky;top:0;background:#f8fafc;z-index:1}.small{font-size:11px;color:#697589}
</style></head><body>
<header><b>Retail Requirement Leads</b><br><small>LinkedIn/public-indexed leasing requirements for manual verification and follow-up</small></header>
<div class="wrap">
<div class="card toolbar">
<a class="btn gray" href="/workspace">Workspace</a><a class="btn gray" href="/data-recovery">Data Recovery</a>
<input id="search" placeholder="Search company, person, phone, email, location, requirement">
<select id="follow"><option value="">All Follow-up</option><option>NEW</option><option>CONTACTED</option><option>QUALIFIED</option><option>NOT_RELEVANT</option></select>
<button class="btn" onclick="load()">Refresh</button><button class="btn gray" onclick="backfill()">Organize Existing Signals</button>
</div>
<div class="card"><div class="tablewrap"><table><thead><tr>
<th>Company / Brand</th><th>Contact Person</th><th>Designation</th><th>Mobile</th><th>Email</th><th>LinkedIn Profile</th><th>Requirement Post</th><th>Requirement</th><th>Location</th><th>Area</th><th>Property Type</th><th>Transaction</th><th>Intent</th><th>Assigned</th><th>Follow-up</th><th>CRM</th><th>Save</th>
</tr></thead><tbody id="rows"></tbody></table></div></div></div>
<script>
const E=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let D=[];
async function A(u,o={}){let r=await fetch(u,o),d=await r.json();if(!r.ok)throw new Error(d.detail||d.message||'Error');return d}
function I(id,k,v){return `<input id="${id}_${k}" value="${E(v||'')}">`}
function S(id,k,v,opts){return `<select id="${id}_${k}">${opts.map(o=>`<option ${o===v?'selected':''}>${o}</option>`).join('')}</select>`}
function R(){let q=(document.querySelector('#search').value||'').toLowerCase(),f=document.querySelector('#follow').value;let rows=D.filter(x=>(!q||JSON.stringify(x).toLowerCase().includes(q))&&(!f||x.followup_status===f));document.querySelector('#rows').innerHTML=rows.map(x=>`<tr>
<td>${I(x.signal_id,'company_name',x.company_name)}</td><td>${I(x.signal_id,'contact_name',x.contact_name)}</td><td>${I(x.signal_id,'designation',x.designation)}</td>
<td>${I(x.signal_id,'contact_phone',x.contact_phone)}</td><td>${I(x.signal_id,'contact_email',x.contact_email)}</td>
<td>${x.linkedin_profile_url?`<a target="_blank" href="${E(x.linkedin_profile_url)}">Profile</a>`:'Not Found'}</td>
<td>${x.source_url?`<a target="_blank" href="${E(x.source_url)}">Open Post</a>`:'Not Found'}</td>
<td style="max-width:320px;white-space:normal"><b>${E(x.title||'')}</b><br><span class="small">${E(x.excerpt||'')}</span></td>
<td>${E(x.location||'')}</td><td>${I(x.signal_id,'required_area_sqft',x.required_area_sqft)}</td><td>${I(x.signal_id,'required_property_type',x.required_property_type)}</td><td>${I(x.signal_id,'required_transaction',x.required_transaction)}</td>
<td>${E(x.intent_score||0)}</td><td>${I(x.signal_id,'assigned_to',x.assigned_to)}</td>
<td>${S(x.signal_id,'followup_status',x.followup_status||'NEW',['NEW','CONTACTED','QUALIFIED','NOT_RELEVANT'])}</td>
<td>${S(x.signal_id,'crm_status',x.crm_status||'NOT_SENT',['NOT_SENT','CRM_READY','ADDED_TO_CRM'])}</td>
<td><button class="btn" onclick="save('${x.signal_id}')">Save</button></td></tr>`).join('')}
async function load(){let d=await A('/api/v6/retail-requirements');D=d.rows||[];R()}
async function backfill(){let d=await A('/api/v6/retail-requirements/backfill',{method:'POST'});alert('Organized '+d.updated+' existing LinkedIn signals');await load()}
async function save(id){let ks=['company_name','contact_name','designation','contact_phone','contact_email','required_area_sqft','required_property_type','required_transaction','assigned_to','followup_status','crm_status'];let b={};ks.forEach(k=>{let e=document.getElementById(id+'_'+k);if(e)b[k]=e.value});await A('/api/v6/retail-requirements/'+encodeURIComponent(id)+'/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});alert('Saved')}
document.querySelector('#search').addEventListener('input',R);document.querySelector('#follow').addEventListener('change',R);load();
</script></body></html>""")


@app.get("/api/v4/requirements")
def v4_requirements(req:Request,limit:int=Query(300,ge=1,le=1000)):
    need_login(req)
    with engine.connect() as c:
        rows=c.execute(text("""SELECT requirement_id,client_name,company_name,contact_phone,requirement_type,property_type,
            city,preferred_locations,minimum_area_sqft,maximum_area_sqft,rent_or_sale,status,created_at
            FROM pi_requirements ORDER BY created_at DESC LIMIT :n"""),{"n":limit}).fetchall()
    return {"status":"ok","rows":_json_rows(rows)}

@app.post("/api/v4/match/{rid}")
def v4_match(rid:str,req:Request):
    need_login(req)
    return robust_match_requirement(rid,create_whatsapp=True)

@app.get("/api/v4/bot-runs")
def v4_bot_runs(req:Request,limit:int=Query(100,ge=1,le=300)):
    need_login(req)
    with engine.connect() as c:
        rows=c.execute(text("SELECT * FROM ai_bot_runs ORDER BY started_at DESC LIMIT :n"),{"n":limit}).fetchall()
    return {"status":"ok","rows":_json_rows(rows)}

@app.get("/api/v4/activity")
def v4_activity(req:Request,limit:int=Query(100,ge=1,le=500)):
    need_login(req)
    with engine.connect() as c:
        rows=c.execute(text("SELECT * FROM ai_activity_ledger ORDER BY created_at DESC LIMIT :n"),{"n":limit}).fetchall()
    return {"status":"ok","rows":_json_rows(rows)}

@app.get("/api/v4/overview")
def v4_overview(req:Request):
    need_login(req)
    with engine.connect() as c:
        def one(q,p=None): return c.execute(text(q),p or {}).scalar_one()
        return {"status":"ok","data":{
            "properties":one("SELECT COUNT(*) FROM pi_properties"),
            "requirements":one("SELECT COUNT(*) FROM pi_requirements"),
            "matches":one("SELECT COUNT(*) FROM pi_matches"),
            "owners":one("SELECT COUNT(*) FROM pi_owners"),
            "brokers":one("SELECT COUNT(*) FROM pi_brokers"),
            "hospitality_contacts":one("SELECT COUNT(*) FROM ai_marketing_contacts"),
            "hospitality_companies":one("SELECT COUNT(*) FROM ai_companies WHERE division='HOSPITALITY'"),
            "retail_companies":one("SELECT COUNT(*) FROM ai_companies WHERE division='RETAIL'"),
            "demand_signals":one("SELECT COUNT(*) FROM ai_demand_signals"),
            "bot_runs":one("SELECT COUNT(*) FROM ai_bot_runs")
        }}

_V4_CSS = """
:root{--bg:#f4f7fb;--card:#fff;--ink:#142033;--muted:#6d7b90;--line:#e4eaf1;--nav:#0d1d2d;--blue:#1677ff;--green:#0a9b63;--orange:#df8b13;--red:#d94848}
*{box-sizing:border-box}body{margin:0;background:var(--bg);font-family:Arial,sans-serif;color:var(--ink)}
.shell{display:grid;grid-template-columns:260px 1fr;min-height:100vh}.side{background:var(--nav);color:#fff;padding:18px 13px;position:sticky;top:0;height:100vh;overflow:auto}.brand{font-size:18px;font-weight:800;padding:5px 9px 16px}.brand small{display:block;font-size:11px;color:#90a3b7;margin-top:5px}.group{font-size:10px;letter-spacing:1.2px;color:#7f93a9;margin:17px 9px 6px}.nav{display:block;width:100%;text-align:left;border:0;background:transparent;color:#d9e3ed;padding:10px;border-radius:8px;cursor:pointer;text-decoration:none;font-size:13px}.nav:hover,.nav.active{background:#193653;color:white}
.main{min-width:0}.top{height:66px;background:#fff;border-bottom:1px solid var(--line);padding:0 22px;display:flex;align-items:center;justify-content:space-between}.content{padding:22px;max-width:1650px;margin:auto}.page{display:none}.page.active{display:block}.title{font-size:25px;margin:0;font-weight:800}.sub{font-size:13px;color:var(--muted);margin-top:4px}.kpis{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:11px;margin:18px 0}.kpi,.card{background:#fff;border:1px solid var(--line);border-radius:12px;box-shadow:0 2px 9px rgba(0,0,0,.03)}.kpi{padding:14px}.kpi span{font-size:10px;color:var(--muted)}.kpi b{display:block;font-size:24px;margin-top:7px}.card{padding:16px;margin:14px 0}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
input,select,textarea{width:100%;padding:10px;border:1px solid #d7e0ea;border-radius:8px;background:#fff;font-size:14px}textarea{min-height:76px}.formgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.full{grid-column:1/-1}.btn{border:0;border-radius:8px;padding:10px 13px;background:var(--blue);color:white;font-weight:700;cursor:pointer}.btn.green{background:var(--green)}.btn.gray{background:#edf2f7;color:#24364b}.btn.orange{background:var(--orange)}.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}
.drop{border:2px dashed #b9c8d8;padding:18px;border-radius:10px;text-align:center;background:#f8fbff}.drop.drag{border-color:var(--blue);background:#eef5ff}.drop input{border:0}
.tablewrap{overflow:auto;max-height:65vh;border:1px solid var(--line);border-radius:10px}table{border-collapse:collapse;width:100%;font-size:12px;background:#fff}th,td{padding:9px;border-bottom:1px solid #edf1f5;text-align:left;white-space:nowrap}th{background:#f8fafc;position:sticky;top:0}.badge{display:inline-block;padding:4px 7px;border-radius:20px;background:#eef3f7;font-size:10px;font-weight:700}.hot{color:var(--red);font-weight:800}.msg{padding:10px;border-radius:8px;margin:8px 0;background:#eef6ff}.good{background:#eaf8f2;color:#086d49}.warn{background:#fff5e6;color:#8a5600}.activity{padding:9px 0;border-bottom:1px solid #edf1f5}.activity small{display:block;color:var(--muted);margin-top:3px}
@media(max-width:1100px){.kpis{grid-template-columns:repeat(3,1fr)}.grid2,.grid3{grid-template-columns:1fr}}@media(max-width:760px){.shell{grid-template-columns:1fr}.side{height:auto;position:relative}.kpis{grid-template-columns:repeat(2,1fr)}.formgrid{grid-template-columns:1fr}.full{grid-column:auto}.content{padding:12px}}
"""

_V4_JS = r"""
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
async function api(u,o={}){let r=await fetch(u,o),t=await r.text(),d;try{d=JSON.parse(t)}catch(e){d={message:t}}if(!r.ok)throw new Error(d.detail||d.message||t||('HTTP '+r.status));return d}
function esc(x){return String(x??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function fmt(x){return x?new Date(x).toLocaleString():'-'} function n(x){return Number(x||0).toLocaleString()}
function nav(id){$$('.page').forEach(x=>x.classList.remove('active'));$$('.nav[data-page]').forEach(x=>x.classList.remove('active'));$('#'+id).classList.add('active');document.querySelector(`[data-page="${id}"]`)?.classList.add('active');loadPage(id)}
$$('.nav[data-page]').forEach(b=>b.onclick=()=>nav(b.dataset.page));
async function loadOverview(){let d=(await api('/api/v4/overview')).data;Object.entries(d).forEach(([k,v])=>$$(`[data-k="${k}"]`).forEach(e=>e.textContent=n(v)));await loadActivity(8)}
async function loadPage(id){if(id==='property')await loadRequirements();if(id==='owners')await loadOwners();if(id==='brokers')await loadBrokers();if(id==='hospitality')await loadHospitality();if(id==='retail')await loadCompanies('RETAIL','retailRows');if(id==='contacts')await loadContacts();if(id==='demand')await loadCampaigns();if(id==='bots')await loadBots();if(id==='activity')await loadActivity(150)}
async function submitProperty(ev){ev.preventDefault();let fd=new FormData(ev.target);let inp=$('#propertyMedia');[...inp.files].forEach(f=>fd.append('media',f));let b=$('#saveProperty');b.disabled=true;try{let d=await api('/api/v4/properties/manual',{method:'POST',body:fd});$('#propertyMsg').innerHTML=`<div class="msg good">Saved ${esc(d.property_id)} · ${d.media.length} media files</div>`;ev.target.reset();await loadOverview()}catch(e){$('#propertyMsg').innerHTML=`<div class="msg warn">${esc(e.message)}</div>`}finally{b.disabled=false}}
function setupDrop(){let d=$('#dropZone'),i=$('#propertyMedia');if(!d||!i)return;['dragenter','dragover'].forEach(x=>d.addEventListener(x,e=>{e.preventDefault();d.classList.add('drag')}));['dragleave','drop'].forEach(x=>d.addEventListener(x,e=>{e.preventDefault();d.classList.remove('drag')}));d.addEventListener('drop',e=>{i.files=e.dataTransfer.files;$('#fileCount').textContent=i.files.length+' file(s) selected'});i.onchange=()=>$('#fileCount').textContent=i.files.length+' file(s) selected'}
async function loadOwners(){let d=await api('/api/v4/owners');$('#ownerRows').innerHTML=d.rows.map(x=>`<tr><td>${esc(x.owner_id)}</td><td><b>${esc(x.owner_name)}</b></td><td>${esc(x.contact_number||'')}</td><td>${esc(x.email||'')}</td><td>${esc(x.city||'')}</td><td>${esc(x.notes||'')}</td></tr>`).join('')}
async function loadBrokers(){let d=await api('/api/v4/brokers');$('#brokerRows').innerHTML=d.rows.map(x=>`<tr><td>${esc(x.broker_id)}</td><td><b>${esc(x.broker_name)}</b></td><td>${esc(x.company_name||'')}</td><td>${esc(x.contact_number||'')}</td><td>${esc(x.email||'')}</td><td>${esc(x.city||'')}</td></tr>`).join('')}
async function addCompany(ev,div){ev.preventDefault();let body=Object.fromEntries(new FormData(ev.target).entries());body.division=div;body.expansion_score=Number(body.expansion_score||0);try{await api('/api/v4/companies',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});ev.target.reset();await loadCompanies(div,div==='RETAIL'?'retailRows':'hospitalityRows');await loadOverview();alert('Saved')}catch(e){alert(e.message)}}
async function loadCompanies(div,target){let d=await api('/api/v4/companies?division='+div);$('#'+target).innerHTML=d.rows.map(x=>`<tr><td><b>${esc(x.company_name)}</b></td><td>${esc(x.category||'')}</td><td>${esc(x.primary_contact_name||'')}</td><td>${esc(x.primary_contact_phone||'')}</td><td>${esc(x.primary_contact_email||'')}</td><td>${esc(x.target_markets||x.city||'')}</td><td class="${Number(x.expansion_score)>=80?'hot':''}">${Number(x.expansion_score||0).toFixed(0)}</td><td>${esc(x.assigned_to||'')}</td></tr>`).join('')||'<tr><td colspan="8">No records yet.</td></tr>'}
async function loadHospitality(){await loadCompanies('HOSPITALITY','hospitalityRows');await loadContacts()}
async function runHospitality(){let d=await api('/api/v4/hospitality-bot/start',{method:'POST'});alert('Hospitality bot started in background. Run ID: '+d.run_id);setTimeout(loadBots,1500)}
async function runRetail(){let d=await api('/api/v4/retail-bot/start',{method:'POST'});alert('Retail bot started in background. Run ID: '+d.run_id);setTimeout(loadBots,1500)}
async function uploadContacts(ev){ev.preventDefault();let fd=new FormData(ev.target);try{let d=await api('/api/v4/marketing-contacts/upload',{method:'POST',body:fd});alert(`Uploaded: ${d.created} new, ${d.duplicates} duplicates`);await loadContacts();await loadOverview()}catch(e){alert(e.message)}}
async function loadContacts(){let d=await api('/api/v4/marketing-contacts');if($('#contactRows'))$('#contactRows').innerHTML=d.rows.map(x=>`<tr><td>${esc(x.business_type||'')}</td><td><b>${esc(x.brand_name||'')}</b></td><td>${esc(x.contact_name||'')}</td><td>${esc(x.phone||'')}</td><td>${esc(x.email||'')}</td><td>${esc(x.website||'')}</td><td>${esc(x.location||'')}</td><td>${esc(x.consent_status||'UNKNOWN')}</td><td>${esc(x.verification_status||'')}</td></tr>`).join('')}
async function createCampaign(ev){ev.preventDefault();let body=Object.fromEntries(new FormData(ev.target).entries());['area_sqft','monthly_rent'].forEach(k=>body[k]=body[k]?Number(body[k]):null);try{let d=await api('/api/v4/requirement-campaigns',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});$('#campaignMsg').innerHTML=`<div class="msg good">Campaign ${esc(d.campaign_id)} created.<br><b>Post draft:</b> ${esc(d.post_draft)}</div>`;ev.target.reset();await loadCampaigns()}catch(e){alert(e.message)}}
async function loadCampaigns(){let d=await api('/api/v4/requirement-campaigns');$('#campaignRows').innerHTML=d.rows.map(x=>`<tr><td>${esc(x.campaign_id)}</td><td><b>${esc(x.campaign_name)}</b></td><td>${esc(x.property_type||'')}</td><td>${esc(x.location||x.city||'')}</td><td>${x.area_sqft||''}</td><td>${x.monthly_rent||''}</td><td>${esc(x.status||'')}</td><td><button class="btn green" onclick="startCampaign('${esc(x.campaign_id)}')">Start Requirement Bot</button></td></tr>`).join('');await loadDemandSignals()}
async function startCampaign(id){let d=await api('/api/v4/requirement-campaigns/'+encodeURIComponent(id)+'/start',{method:'POST'});alert('Requirement Discovery Bot started: '+d.run_id)}
async function loadDemandSignals(){let d=await api('/api/v4/demand-signals');$('#demandRows').innerHTML=d.rows.map(x=>`<tr><td>${esc(x.source_name||'')}</td><td><b>${esc(x.title||'')}</b><br><small>${esc(x.excerpt||'')}</small></td><td class="hot">${Number(x.intent_score||0).toFixed(0)}%</td><td><b>${esc(x.contact_phone||'Not found')}</b></td><td>${esc(x.contact_email||'')}</td><td>${esc(x.contact_verification_status||'')}</td><td>${esc(x.location||'')}</td><td>${x.source_url?`<a target="_blank" href="${esc(x.source_url)}">Open source</a>`:''}</td><td>${fmt(x.created_at)}</td></tr>`).join('')||'<tr><td colspan="9">No 90%+ matches found yet.</td></tr>'}
async function loadRequirements(){let d=await api('/api/v4/requirements');let sel=$('#reqSelect');sel.innerHTML='<option value="">Select requirement</option>'+d.rows.map(x=>`<option value="${esc(x.requirement_id)}">${esc(x.requirement_id)} · ${esc(x.company_name||x.client_name||'')} · ${esc(x.city||'')} · ${esc(x.preferred_locations||'')}</option>`).join('');$('#reqRows').innerHTML=d.rows.map(x=>`<tr><td>${esc(x.requirement_id)}</td><td>${esc(x.company_name||x.client_name||'')}</td><td>${esc(x.city||'')}</td><td>${esc(x.preferred_locations||'')}</td><td>${x.minimum_area_sqft||''}-${x.maximum_area_sqft||''}</td><td>${esc(x.rent_or_sale||'')}</td><td><button class="btn" onclick="matchReq('${esc(x.requirement_id)}')">Match</button></td></tr>`).join('')}
async function matchSelected(){let id=$('#reqSelect').value;if(!id)return alert('Select a requirement');await matchReq(id)}
async function verifyProperty(pid){if(!confirm('Confirm: your team called the owner/broker and the property is currently available?'))return;let fd=new FormData();fd.append('status','VERIFIED');let r=await fetch('/api/v4/properties/'+encodeURIComponent(pid)+'/availability-verification',{method:'POST',body:fd});let d=await r.json();if(!r.ok)throw new Error(d.detail||'Verification failed');alert('VERIFIED. Contact numbers stay internal and will NOT be included in client WhatsApp.');await matchSelected()}
async function matchReq(id){try{let d=await api('/api/v4/match/'+encodeURIComponent(id),{method:'POST'});let diag=d.diagnostic||{};let ec=diag.exclusion_counts||{};let exclusions=Object.entries(ec).map(([k,v])=>`${esc(k)}: ${v}`).join(' · ');$('#matchDiag').innerHTML=`<div class="msg ${d.matches.length?'good':'warn'}"><b>${esc(diag.message||'Matching complete')}</b><br><b>Engine:</b> ${esc(d.engine||diag.engine||'MATCHING_V2')} · Database: ${diag.database_properties||0} · Eligible: ${diag.eligible_count||0} · Excluded: ${diag.excluded_count||0} · Returned: ${diag.matches_returned||0}<br><b>Hard-filter exclusions:</b> ${exclusions||'None'}<br><b>Team flow:</b> Call internal contact → verify availability → Mark Verified → then share. Contacts remain internal.</div>`;$('#matchRows').innerHTML=(d.matches||[]).map((x,i)=>{let contacts=[x.owner_contact?('Owner: '+(x.owner_name||'')+' '+x.owner_contact):'',x.broker_contact?('Broker: '+(x.broker_name||'')+' '+x.broker_contact):'',(!x.owner_contact&&!x.broker_contact&&x.contact_number)?('Contact: '+x.contact_number):''].filter(Boolean).join('<br>');let verified=String(x.verification_status||'').toUpperCase()==='VERIFIED';let b=x.score_breakdown||{};let breakdown=`Location ${b.location||0}/30 · Area ${b.area||0}/25 · Type ${b.property_type||0}/15 · Use ${b.suitable_use||0}/15 · Budget ${b.budget||0}/10 · Verification ${b.verification||0}/5`;return `<tr><td>${i+1}</td><td><a href="/property-record/${encodeURIComponent(x.property_id)}" target="_blank"><b>${esc(x.property_name||x.property_id)}</b><br>${esc(x.property_id)}</a><br><a class="btn gray" target="_blank" href="/property-record/${encodeURIComponent(x.property_id)}">View Full Property</a></td><td>${esc(x.city||'')}</td><td>${esc(x.location||'')}</td><td>${x.available_area_sqft||''}</td><td>${x.monthly_rent?Number(x.monthly_rent).toLocaleString():''}</td><td class="${x.score>=80?'hot':''}"><b>${Number(x.score||0).toFixed(0)}%</b></td><td><b>${esc(x.match_band||'')}</b></td><td><b>Internal only</b><br>${contacts||'No contact saved'}</td><td>${verified?'<b>✓ VERIFIED</b>':`<button class="btn green" onclick="verifyProperty('${x.property_id}')">Mark Verified</button>`}</td><td>${esc((x.reasons||[]).join(', '))}</td><td>${esc(breakdown)}</td><td>${esc((x.gaps||[]).join(', '))}</td></tr>`}).join('')||'<tr><td colspan="13">No eligible property passed the hard filters.</td></tr>';$('#excludedRows').innerHTML=(d.excluded||[]).map((x,i)=>`<tr><td>${i+1}</td><td><a href="/property-record/${encodeURIComponent(x.property_id)}" target="_blank"><b>${esc(x.property_name||x.property_id||'')}</b><br>${esc(x.property_id||'')}</a></td><td>${esc(x.city||'')}</td><td>${esc(x.location||'')}</td><td>${esc(x.property_type||'')}</td><td>${x.available_area_sqft||''}</td><td>${esc(x.source||'')}</td><td><b>${esc((x.reasons||[]).join(', '))}</b></td></tr>`).join('')||'<tr><td colspan="8">No inventory excluded.</td></tr>';await loadOverview()}catch(e){alert(e.message)}}
async function loadBots(){let d=await api('/api/v4/bot-runs');$('#botRows').innerHTML=d.rows.map(x=>`<tr><td>${esc(x.bot_name)}</td><td>${esc(x.division||'')}</td><td>${esc(x.status)}</td><td>${x.records_found||0}</td><td>${x.records_created||0}</td><td>${fmt(x.started_at)}</td><td>${esc(x.summary||x.error_message||'')}</td></tr>`).join('')}
async function loadActivity(limit=100){let d=await api('/api/v4/activity?limit='+limit);let h=d.rows.map(x=>`<div class="activity"><b>${esc(x.actor_name)} · ${esc(x.action)}</b><small>${esc(x.division||'')} · ${esc(x.summary||'')} · ${fmt(x.created_at)}</small></div>`).join('')||'No activity yet.';if($('#activityFeed'))$('#activityFeed').innerHTML=h;if($('#activityRows'))$('#activityRows').innerHTML=d.rows.map(x=>`<tr><td>${fmt(x.created_at)}</td><td>${esc(x.actor_name)}</td><td>${esc(x.division||'')}</td><td>${esc(x.action)}</td><td>${esc(x.summary||'')}</td><td>${esc(x.status||'')}</td></tr>`).join('')}
setupDrop();loadOverview();
"""

def _v4_page(role):
    badge="ADMIN" if role=="admin" else "TEAM"
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Deal Intelligence OS V4</title><style>{_V4_CSS}</style></head><body><div class="shell">
<aside class="side"><div class="brand">AI Deal Intelligence OS<small>V4 · Property · Hospitality · Retail · Demand</small></div>
<div class="group">COMMAND</div><button class="nav active" data-page="command">▣ Command Centre</button><button class="nav" data-page="activity">◎ AI Activity</button><button class="nav" data-page="bots">⚡ Bot Control Room</button>
<div class="group">PROPERTY</div><button class="nav" data-page="property">⌂ Add Property + Matcher</button><a class="nav" href="/property-database">▦ Full Property Database</a><a class="nav" href="/data-quality">✓ Data Quality / Organizer</a><a class="nav" href="/data-recovery">↻ Intelligent Recovery V5</a><a class="nav" href="/retail-requirements">▤ Retail Requirement Leads</a><button class="nav" data-page="owners">● Owners Database</button><button class="nav" data-page="brokers">● Brokers Database</button><a class="nav" href="/legacy-workspace">Original Upload Workspace</a><a class="nav" href="/database-page">Original Database</a>
<div class="group">LEAD INTELLIGENCE</div><button class="nav" data-page="hospitality">◆ Hospitality</button><button class="nav" data-page="retail">◈ Retail Expansion</button><button class="nav" data-page="contacts">✉ Marketing Contacts</button><button class="nav" data-page="demand">⌕ Requirement Discovery</button>
</aside><main class="main"><header class="top"><div><b>Unified Delhi NCR Deal Intelligence</b><div class="sub">Organized database + AI bots + matching</div></div><div>{badge} · <a href="/logout">Logout</a></div></header><div class="content">

<section class="page active" id="command"><h1 class="title">Command Centre</h1><div class="sub">One view across supply, demand, owners, brokers, hospitality and retail.</div>
<div class="kpis"><div class="kpi"><span>PROPERTIES</span><b data-k="properties">0</b></div><div class="kpi"><span>REQUIREMENTS</span><b data-k="requirements">0</b></div><div class="kpi"><span>MATCHES</span><b data-k="matches">0</b></div><div class="kpi"><span>HOSPITALITY CONTACTS</span><b data-k="hospitality_contacts">0</b></div><div class="kpi"><span>DEMAND SIGNALS</span><b data-k="demand_signals">0</b></div></div>
<div class="grid3"><div class="card"><h3>Property Database</h3><p>Owners: <b data-k="owners">0</b><br>Brokers: <b data-k="brokers">0</b></p><button class="btn" onclick="nav('property')">Open Property</button></div>
<div class="card"><h3>Automated Discovery</h3><p>Hospitality and Retail bots run in the background. Requirement Discovery works from a property/campaign brief.</p><button class="btn green" onclick="nav('bots')">Bot Control Room</button></div>
<div class="card"><h3>Important</h3><p>Public web signals are leads to qualify, not confirmed requirements. Verify before outreach or sharing inventory.</p></div></div>
<div class="card"><h3>Latest AI Activity</h3><div id="activityFeed"></div></div></section>

<section class="page" id="property"><h1 class="title">Property Intelligence</h1><div class="sub">Improved manual property form + drag/drop media + repaired matcher.</div>
<div class="grid2"><div class="card"><h3>Add Property Manually</h3>
<div class="msg good"><b>One master form is now used everywhere.</b><br>
Owner Name · Owner Contact · Broker Name · Broker Contact · Main Contact Number · Monthly Rent in figures · Team Member · Verified/Unverified · Direct Images/Videos.</div>
<a class="btn green" href="/property-manual">Open Correct Add Property Form</a>
</div>
<div class="card"><h3>Property Matching Centre · V2</h3><div class="toolbar"><a class="btn orange" href="/property-database">View Full Property Database</a><span class="badge">Master saved inventory</span></div><select id="reqSelect"><option>Loading requirements...</option></select><div class="toolbar"><button class="btn green" onclick="matchSelected()">Run Smart Match V2</button><a class="btn gray" href="/legacy-workspace">Add Requirement / Upload Source</a></div><div id="matchDiag"></div>
<div class="msg good"><b>Matching order:</b> Self Inventory → Availability → Rent/Sale → Commercial/Residential → Suitable Use → City/Location → 80%-120% Area → 100-point ranking.</div>
<h4>Ranked Eligible Matches</h4>
<div class="tablewrap"><table><thead><tr><th>#</th><th>Property</th><th>City</th><th>Location</th><th>Area</th><th>Rent</th><th>Score</th><th>Band</th><th>Availability Contact (INTERNAL)</th><th>Verification</th><th>Why Matched</th><th>Score Breakdown</th><th>Data Gaps</th></tr></thead><tbody id="matchRows"></tbody></table></div>
<h4 style="margin-top:16px">Excluded Inventory</h4>
<div class="msg warn">These properties failed one or more mandatory rules and are NOT eligible for client sharing.</div>
<div class="tablewrap"><table><thead><tr><th>#</th><th>Property</th><th>City</th><th>Location</th><th>Type</th><th>Area</th><th>Source</th><th>Exclusion Reason</th></tr></thead><tbody id="excludedRows"></tbody></table></div></div>
</div></div>
<div class="card"><h3>Requirements</h3><div class="tablewrap"><table><thead><tr><th>ID</th><th>Client/Company</th><th>City</th><th>Location</th><th>Area</th><th>Rent/Sale</th><th>Action</th></tr></thead><tbody id="reqRows"></tbody></table></div></div></section>

<section class="page" id="owners"><h1 class="title">Owners Database</h1><div class="sub">Owner data is now normalized separately from properties.</div><div class="card"><div class="tablewrap"><table><thead><tr><th>Owner ID</th><th>Name</th><th>Contact</th><th>Email</th><th>City</th><th>Notes</th></tr></thead><tbody id="ownerRows"></tbody></table></div></div></section>
<section class="page" id="brokers"><h1 class="title">Brokers Database</h1><div class="sub">Broker data is now normalized separately from properties.</div><div class="card"><div class="tablewrap"><table><thead><tr><th>Broker ID</th><th>Name</th><th>Company</th><th>Contact</th><th>Email</th><th>City</th></tr></thead><tbody id="brokerRows"></tbody></table></div></div></section>

<section class="page" id="hospitality"><h1 class="title">Hospitality Intelligence</h1><div class="sub">Automatic bot + manual prospect entry. Restaurants, cafes, banquets, hotels, wedding venues and commercial farmhouses.</div>
<div class="toolbar"><button class="btn green" onclick="runHospitality()">Run Delhi NCR Hospitality Data Bot</button><span class="badge">Requires GOOGLE_PLACES_API_KEY</span></div>
<div class="grid2"><div class="card"><h3>Add Hospitality Prospect Manually</h3><form class="formgrid" onsubmit="addCompany(event,'HOSPITALITY')">
<input name="company_name" placeholder="Brand / venue / operator" required><input name="category" placeholder="Restaurant / Cafe / Banquet / Hotel">
<input name="primary_contact_name" placeholder="Contact person name"><input name="primary_contact_phone" placeholder="Contact number">
<input name="primary_contact_email" placeholder="Email ID"><input name="website" placeholder="Website">
<input name="linkedin_url" placeholder="LinkedIn URL"><input name="target_markets" value="Delhi NCR" placeholder="Target market">
<input name="assigned_to" placeholder="Team member"><input name="expansion_score" type="number" min="0" max="100" placeholder="Opportunity score">
<textarea class="full" name="source_excerpt" placeholder="Requirement / availability / notes"></textarea><button class="btn full">Save Hospitality Prospect</button></form></div>
<div class="card"><h3>How automatic mode works</h3><p>The Hospitality Data Bot uses Google Places to discover public business records across Delhi NCR. It stores brand, phone, location and website when available, and attempts to find a public business email on the listed website.</p><p><b>Contact-person names are stored when known; Google Places itself does not normally supply a named decision-maker.</b></p></div></div>
<div class="card"><h3>Hospitality Prospects</h3><div class="tablewrap"><table><thead><tr><th>Brand</th><th>Category</th><th>Contact Name</th><th>Phone</th><th>Email</th><th>Market</th><th>Score</th><th>Team</th></tr></thead><tbody id="hospitalityRows"></tbody></table></div></div></section>

<section class="page" id="retail"><h1 class="title">Retail Expansion Intelligence</h1><div class="sub">Automatic Retail Expansion Bot + manual prospect entry, now with name and contact number.</div>
<div class="toolbar"><button class="btn green" onclick="runRetail()">Run Retail + LinkedIn Requirement Bots</button><span class="badge">Requires SERPER_API_KEY</span></div>
<div class="card"><h3>Add Retail Expansion Prospect Manually</h3><form class="formgrid" onsubmit="addCompany(event,'RETAIL')">
<input name="company_name" placeholder="Retail brand/company" required><input name="category" placeholder="Fashion / QSR / Beauty / Electronics">
<input name="primary_contact_name" placeholder="Contact person name"><input name="primary_contact_phone" placeholder="Contact number">
<input name="primary_contact_email" placeholder="Email ID"><input name="website" placeholder="Website">
<input name="linkedin_url" placeholder="LinkedIn URL"><input name="target_markets" value="Delhi NCR" placeholder="Target market">
<input name="assigned_to" placeholder="Team member"><input name="expansion_score" type="number" min="0" max="100" placeholder="Expansion score">
<textarea class="full" name="source_excerpt" placeholder="Expansion signal / notes"></textarea><button class="btn full">Save Retail Prospect</button></form></div>
<div class="card"><h3>Retail Prospects</h3><div class="tablewrap"><table><thead><tr><th>Brand</th><th>Category</th><th>Contact Name</th><th>Phone</th><th>Email</th><th>Market</th><th>Score</th><th>Team</th></tr></thead><tbody id="retailRows"></tbody></table></div></div></section>

<section class="page" id="contacts"><h1 class="title">WhatsApp / Marketing Contact Database</h1><div class="sub">Hospitality contact database plus your own uploaded contacts. Use only where your outreach is permitted and appropriate.</div>
<div class="grid2"><div class="card"><h3>Upload Existing Contact Database</h3><form onsubmit="uploadContacts(event)"><input name="file" type="file" accept=".csv" required><button class="btn">Upload CSV</button></form><p>Suggested CSV columns: Business Type, Brand Name, Contact Name, Phone, Email, Website, Location, City, Consent Status, Verification Status, Team Member.</p></div>
<div class="card"><h3>Export</h3><p>Export the organized database for approved WhatsApp/CRM workflows.</p><a class="btn green" href="/api/v4/marketing-contacts/export.csv">Export CSV</a></div></div>
<div class="card"><h3>Marketing Contacts</h3><div class="tablewrap"><table><thead><tr><th>Type</th><th>Brand</th><th>Contact Name</th><th>Phone</th><th>Email</th><th>Website</th><th>Location</th><th>Consent</th><th>Verification</th></tr></thead><tbody id="contactRows"></tbody></table></div></div></section>

<section class="page" id="demand"><h1 class="title">Requirement Discovery Bot</h1><div class="sub">Strict mode: only 90%+ campaign matches are saved. Public contact numbers/emails are shown when found on indexed/source pages.</div>
<div class="grid2"><div class="card"><h3>Create Property Demand-Hunt Campaign</h3><form class="formgrid" onsubmit="createCampaign(event)">
<input name="campaign_name" placeholder="Campaign name" required><input name="property_id" placeholder="Existing Property ID (optional)">
<input name="property_type" placeholder="Property type"><input name="city" value="Delhi NCR" placeholder="City">
<input name="location" placeholder="Location"><input name="area_sqft" type="number" placeholder="Area sqft">
<input name="monthly_rent" type="number" placeholder="Monthly rent"><select name="rent_or_sale"><option>Rent</option><option>Sale</option></select>
<input name="suitable_category" placeholder="Suitable for Restaurant / Retail / Banquet"><input name="nearby_brands" placeholder="Nearby brands">
<input name="assigned_to" placeholder="Team member"><textarea class="full" name="additional_points" placeholder="Frontage, floor, parking, special points"></textarea>
<button class="btn full">Create Campaign</button></form><div id="campaignMsg"></div></div>
<div class="card"><h3>Important source rule</h3><p>The current bot searches <b>public/indexed</b> web results, including search-visible LinkedIn, Facebook, Instagram, 99acres and Magicbricks pages. It cannot reliably read arbitrary private/logged-in comments without official authorized API access.</p><p>The form also generates a property post draft. Automatic posting to social accounts should only be activated after the relevant account APIs are connected.</p></div></div>
<div class="card"><h3>Campaigns</h3><div class="tablewrap"><table><thead><tr><th>ID</th><th>Campaign</th><th>Type</th><th>Location</th><th>Area</th><th>Rent</th><th>Status</th><th>Action</th></tr></thead><tbody id="campaignRows"></tbody></table></div></div>
<div class="card"><h3>90%+ Demand Matches Found</h3><div class="msg good"><b>Strict filter:</b> Results below 90% are rejected. Contact details are shown only when found on a public source; missing data is never guessed.</div><div class="tablewrap"><table><thead><tr><th>Source</th><th>Signal</th><th>Match %</th><th>Contact No.</th><th>Email</th><th>Contact Status</th><th>Location</th><th>Source Link</th><th>Found</th></tr></thead><tbody id="demandRows"></tbody></table></div></div></section>

<section class="page" id="bots"><h1 class="title">Bot Control Room</h1><div class="sub">Bots run in the background so dashboard requests do not time out.</div>
<div class="toolbar"><button class="btn green" onclick="runHospitality()">Run Hospitality Bot</button><button class="btn green" onclick="runRetail()">Run Retail Bot</button><button class="btn gray" onclick="loadBots()">Refresh Runs</button></div>
<div class="card"><div class="tablewrap"><table><thead><tr><th>Bot</th><th>Division</th><th>Status</th><th>Found</th><th>Created</th><th>Started</th><th>Summary</th></tr></thead><tbody id="botRows"></tbody></table></div></div></section>

<section class="page" id="activity"><h1 class="title">AI Activity Ledger</h1><div class="sub">Every important bot action recorded for management review.</div><div class="card"><div class="tablewrap"><table><thead><tr><th>Time</th><th>Actor</th><th>Division</th><th>Action</th><th>Summary</th><th>Status</th></tr></thead><tbody id="activityRows"></tbody></table></div></div></section>

</div></main></div><script>{_V4_JS}</script></body></html>"""


def _property_manual_page(role):
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Add Property Manually</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:#f4f7fb;font-family:Arial,sans-serif;color:#142033}}
header{{background:#0d1d2d;color:white;padding:18px 24px;display:flex;justify-content:space-between;align-items:center}}
header a{{color:white}}
.wrap{{max-width:1180px;margin:0 auto;padding:22px}}
.card{{background:white;border:1px solid #e4eaf1;border-radius:14px;padding:20px;box-shadow:0 2px 10px rgba(0,0,0,.04);margin-bottom:16px}}
h1{{margin:0 0 5px;font-size:27px}}h2{{font-size:16px;margin:0 0 14px}}.muted{{color:#6d7b90;font-size:13px}}
.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}
label{{font-size:12px;font-weight:700;display:block;margin-bottom:5px;color:#42536a}}
input,select,textarea{{width:100%;padding:11px;border:1px solid #d7e0ea;border-radius:8px;background:#fff;font-size:14px}}
textarea{{min-height:90px}}.full{{grid-column:1/-1}}
.section-title{{font-size:12px;font-weight:800;letter-spacing:.8px;color:#1677ff;margin:4px 0 10px;text-transform:uppercase}}
.drop{{border:2px dashed #aebed0;border-radius:12px;padding:24px;text-align:center;background:#f8fbff;cursor:pointer}}
.drop.drag{{border-color:#1677ff;background:#edf5ff}}
.drop input{{margin-top:12px;border:0}}
.btn{{display:inline-block;border:0;border-radius:8px;padding:11px 15px;background:#1677ff;color:white;font-weight:800;cursor:pointer;text-decoration:none}}
.btn.green{{background:#0a9b63}}.btn.gray{{background:#edf2f7;color:#24364b}}
.actions{{display:flex;gap:9px;flex-wrap:wrap}}
.note{{padding:11px;border-radius:8px;background:#eef6ff;margin:10px 0;font-size:13px}}
.good{{background:#eaf8f2;color:#086d49}}.warn{{background:#fff4e6;color:#8a5600}}
#preview{{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:9px;margin-top:12px}}
.previewItem{{border:1px solid #e2e8f0;border-radius:8px;padding:6px;background:white;overflow:hidden}}
.previewItem img,.previewItem video{{width:100%;height:95px;object-fit:cover;border-radius:6px}}
@media(max-width:760px){{.grid{{grid-template-columns:1fr}}.full{{grid-column:auto}}}}
</style>
</head>
<body>
<header>
<div><b>AI Deal Intelligence OS</b><br><small>Add Property Manually</small></div>
<div>{role.upper()} · <a href="/workspace">Dashboard</a> · <a href="/logout">Logout</a></div>
</header>
<div class="wrap">
<h1>Add Property Manually</h1>
<div class="muted">This is the only manual property-entry form. All dashboard links should open this page.</div>
<div class="note"><b>No image/video URLs are required.</b> Upload or drag files directly below.</div>

<form id="masterPropertyForm">

<div class="card">
<div class="section-title">A. Property Details</div>
<div class="grid">
<div><label>Property Name / Building</label><input name="property_name" placeholder="Example: ABC Tower"></div>
<div><label>Property Type *</label><input name="property_type" placeholder="Retail / Restaurant / Office / Banquet" required></div>
<div><label>City *</label><input name="city" value="Delhi NCR" required></div>
<div><label>Location / Micro-market *</label><input name="location" placeholder="Golf Course Road / Saket / Noida Sector 18" required></div>
<div><label>Available Area (sqft)</label><input name="available_area_sqft" type="number" step="0.01" placeholder="5000"></div>
<div><label>Minimum Area (sqft)</label><input name="minimum_area_sqft" type="number" step="0.01"></div>
<div><label>Maximum Area (sqft)</label><input name="maximum_area_sqft" type="number" step="0.01"></div>
<div><label>Floor</label><input name="floor" placeholder="Ground / First / Basement"></div>
<div><label>Rent / Sale</label><select name="rent_or_sale"><option value="Rent">Rent</option><option value="Sale">Sale</option></select></div>
<div><label>Monthly Rent - Figures</label><input name="monthly_rent" type="number" step="0.01" placeholder="800000"></div>
<div><label>Nearby Brands</label><input name="nearby_brands" placeholder="Starbucks, Zara, Nike"></div>
<div><label>Suitable Category</label><input name="suitable_category" placeholder="Restaurant / Fashion / Jewellery"></div>
<div><label>Parking</label><input name="parking" placeholder="Available / 10 cars / Valet"></div>
<div><label>Main Contact Number</label><input name="contact_number" placeholder="+91..."></div>
</div>
</div>

<div class="card">
<div class="section-title">B. Follow-up & Verification</div>
<div class="grid">
<div><label>Team Member for Follow-ups *</label><input name="assigned_to" placeholder="Team member name" required></div>
<div><label>Verification Status *</label>
<select name="verification_status" required>
<option value="UNVERIFIED">Unverified</option>
<option value="VERIFIED">Verified</option>
</select></div>
</div>
</div>

<div class="card">
<div class="section-title">C. Owner Details</div>
<div class="grid">
<div><label>Owner Name</label><input name="owner_name" placeholder="Owner full name"></div>
<div><label>Owner Contact Number</label><input name="owner_contact" placeholder="+91..."></div>
<div class="full"><label>Owner Email</label><input name="owner_email" type="email" placeholder="owner@example.com"></div>
</div>
<div class="muted" style="margin-top:8px">Owner information is also stored in the separate Owners Database.</div>
</div>

<div class="card">
<div class="section-title">D. Broker Details</div>
<div class="grid">
<div><label>Broker Name</label><input name="broker_name" placeholder="Broker full name"></div>
<div><label>Broker Contact Number</label><input name="broker_contact" placeholder="+91..."></div>
<div><label>Broker Email</label><input name="broker_email" type="email"></div>
<div><label>Broker Company</label><input name="broker_company" placeholder="Brokerage company"></div>
</div>
<div class="muted" style="margin-top:8px">Broker information is also stored in the separate Brokers Database.</div>
</div>

<div class="card">
<div class="section-title">E. Images & Videos</div>
<div id="dropZoneMaster" class="drop">
<b>Drag & Drop Property Images or Videos Here</b><br>
<span class="muted">or click Choose Files below. Multiple files are allowed.</span>
<input id="masterMedia" name="media" type="file" multiple
accept="image/jpeg,image/png,image/webp,image/gif,video/mp4,video/webm,video/quicktime,video/x-m4v">
<div id="masterFileCount" class="muted" style="margin-top:7px">0 files selected</div>
</div>
<div id="preview"></div>
</div>

<div class="card">
<div class="section-title">F. Remarks</div>
<textarea name="remarks" placeholder="Property details, owner/broker instructions, frontage, power, ceiling height, possession, restrictions, etc."></textarea>
</div>

<div id="saveMessage"></div>
<div class="actions">
<button id="masterSaveBtn" class="btn green" type="submit">Save Property</button>
<a class="btn gray" href="/workspace">Back to Dashboard</a>
</div>
</form>
</div>

<script>
const form=document.getElementById("masterPropertyForm");
const media=document.getElementById("masterMedia");
const drop=document.getElementById("dropZoneMaster");
const count=document.getElementById("masterFileCount");
const preview=document.getElementById("preview");
const msg=document.getElementById("saveMessage");
const btn=document.getElementById("masterSaveBtn");

function renderFiles(files){{
  count.textContent=files.length+" file(s) selected";
  preview.innerHTML="";
  [...files].forEach(file=>{{
    const box=document.createElement("div");
    box.className="previewItem";
    const title=document.createElement("div");
    title.style.fontSize="11px";
    title.style.marginBottom="5px";
    title.textContent=file.name;
    box.appendChild(title);
    const url=URL.createObjectURL(file);
    if(file.type.startsWith("image/")){{
      const im=document.createElement("img");im.src=url;box.appendChild(im);
    }}else if(file.type.startsWith("video/")){{
      const v=document.createElement("video");v.src=url;v.controls=true;box.appendChild(v);
    }}
    preview.appendChild(box);
  }});
}}
media.addEventListener("change",()=>renderFiles(media.files));
["dragenter","dragover"].forEach(ev=>drop.addEventListener(ev,e=>{{e.preventDefault();drop.classList.add("drag")}}));
["dragleave","drop"].forEach(ev=>drop.addEventListener(ev,e=>{{e.preventDefault();drop.classList.remove("drag")}}));
drop.addEventListener("drop",e=>{{media.files=e.dataTransfer.files;renderFiles(media.files)}});

form.addEventListener("submit",async e=>{{
  e.preventDefault();
  btn.disabled=true;
  msg.innerHTML='<div class="note">Saving property and media...</div>';
  try{{
    const fd=new FormData(form);
    const r=await fetch("/api/v4/properties/manual",{{method:"POST",body:fd}});
    const t=await r.text();
    let d;
    try{{d=JSON.parse(t)}}catch(err){{d={{message:t}}}}
    if(!r.ok)throw new Error(d.detail||d.message||t);
    msg.innerHTML='<div class="note good"><b>Property saved successfully.</b><br>Property ID: '+d.property_id+
      '<br>Owner ID: '+(d.owner_id||'-')+' · Broker ID: '+(d.broker_id||'-')+
      '<br>Media uploaded: '+(d.media||[]).length+'</div>';
    form.reset();preview.innerHTML="";count.textContent="0 files selected";
    window.scrollTo({{top:0,behavior:"smooth"}});
  }}catch(err){{
    msg.innerHTML='<div class="note warn"><b>Could not save property:</b> '+String(err.message||err)+'</div>';
  }}finally{{btn.disabled=false}}
}});
</script>
</body>
</html>"""

@app.get("/property-manual",response_class=HTMLResponse)
def property_manual_page(req:Request):
    role=page_role_or_redirect(req)
    if not role:
        return RedirectResponse("/login",status_code=303)
    return HTMLResponse(_property_manual_page(role))

@app.get("/workspace",response_class=HTMLResponse)
def v4_workspace(req:Request):
    role=page_role_or_redirect(req)
    if not role: return RedirectResponse("/login",status_code=303)
    return HTMLResponse(_v4_page(role))
