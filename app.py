
import os
import csv
import io
import json
import uuid
import tempfile
import hashlib
from decimal import Decimal
from datetime import date, datetime
from typing import Optional, Literal

from fastapi import FastAPI, Query, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from google import genai
from google.genai import types

APP_VERSION = "4.0.1"
DATABASE_URL = os.getenv("DATABASE_URL", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "25"))

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required.")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
app = FastAPI(title="Property Intelligence Agent - All Layers", version=APP_VERSION)
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

SCHEMA_SQL = '''
CREATE TABLE IF NOT EXISTS pi_properties (
    id BIGSERIAL PRIMARY KEY,
    property_id VARCHAR(50) UNIQUE NOT NULL,
    fingerprint VARCHAR(64),
    property_name VARCHAR(255),
    entry_status VARCHAR(50) NOT NULL DEFAULT 'Active',
    availability_status VARCHAR(50) NOT NULL DEFAULT 'Available',
    property_type VARCHAR(100) NOT NULL DEFAULT 'NA',
    city VARCHAR(100) NOT NULL DEFAULT 'NA',
    location VARCHAR(255) NOT NULL DEFAULT 'NA',
    micro_market VARCHAR(255),
    address TEXT,
    google_maps_pin TEXT,
    area_sqft NUMERIC(14,2),
    available_area_sqft NUMERIC(14,2),
    minimum_area_sqft NUMERIC(14,2),
    maximum_area_sqft NUMERIC(14,2),
    floor VARCHAR(100),
    rent_or_sale VARCHAR(30),
    asking_rent_per_sqft NUMERIC(14,2),
    asking_sale_price NUMERIC(18,2),
    possession VARCHAR(100),
    nearby_brands TEXT,
    suitable_category TEXT,
    parking TEXT,
    ceiling_height VARCHAR(100),
    power_load VARCHAR(100),
    cam_per_sqft NUMERIC(14,2),
    security_deposit VARCHAR(100),
    frontage VARCHAR(100),
    owner_name VARCHAR(255),
    owner_contact VARCHAR(100),
    broker_name VARCHAR(255),
    broker_contact VARCHAR(100),
    verified_date DATE,
    verified_by VARCHAR(255),
    verification_status VARCHAR(50) DEFAULT 'UNVERIFIED',
    remarks TEXT,
    source VARCHAR(255),
    source_id BIGINT,
    extraction_confidence NUMERIC(5,2),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pi_requirements (
    id BIGSERIAL PRIMARY KEY,
    requirement_id VARCHAR(50) UNIQUE NOT NULL,
    fingerprint VARCHAR(64),
    client_name VARCHAR(255),
    company_name VARCHAR(255),
    contact_phone VARCHAR(100),
    contact_email VARCHAR(255),
    requirement_type VARCHAR(100),
    property_type VARCHAR(100),
    city VARCHAR(100),
    preferred_locations TEXT,
    minimum_area_sqft NUMERIC(14,2),
    maximum_area_sqft NUMERIC(14,2),
    budget_min NUMERIC(18,2),
    budget_max NUMERIC(18,2),
    rent_or_sale VARCHAR(30),
    floor_preference VARCHAR(100),
    nearby_brands TEXT,
    suitable_category TEXT,
    parking_requirement TEXT,
    possession_timeline VARCHAR(100),
    additional_points TEXT,
    source VARCHAR(255),
    source_id BIGINT,
    status VARCHAR(50) DEFAULT 'New',
    assigned_to VARCHAR(255),
    extraction_confidence NUMERIC(5,2),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pi_contacts (
    id BIGSERIAL PRIMARY KEY,
    contact_type VARCHAR(50) NOT NULL,
    name VARCHAR(255),
    company VARCHAR(255),
    phone VARCHAR(100),
    email VARCHAR(255),
    city VARCHAR(100),
    notes TEXT,
    source_id BIGINT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pi_sources (
    id BIGSERIAL PRIMARY KEY,
    source_type VARCHAR(50) NOT NULL,
    source_name VARCHAR(255),
    source_reference TEXT,
    original_filename VARCHAR(500),
    mime_type VARCHAR(150),
    ingestion_status VARCHAR(50) DEFAULT 'RECEIVED',
    extracted_record_type VARCHAR(50),
    processed_records INTEGER DEFAULT 0,
    duplicate_records INTEGER DEFAULT 0,
    error_message TEXT,
    ai_provider VARCHAR(50),
    ai_model VARCHAR(100),
    uploaded_at TIMESTAMPTZ DEFAULT NOW(),
    processed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS pi_media (
    id BIGSERIAL PRIMARY KEY,
    property_id VARCHAR(50) NOT NULL,
    media_type VARCHAR(30) NOT NULL,
    url TEXT NOT NULL,
    title VARCHAR(255),
    is_primary BOOLEAN DEFAULT FALSE,
    source_id BIGINT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pi_matches (
    id BIGSERIAL PRIMARY KEY,
    requirement_id VARCHAR(50) NOT NULL,
    property_id VARCHAR(50) NOT NULL,
    match_score NUMERIC(5,2) DEFAULT 0,
    rank INTEGER,
    match_reasons JSONB DEFAULT '[]'::jsonb,
    exclusions JSONB DEFAULT '[]'::jsonb,
    status VARCHAR(50) DEFAULT 'READY_FOR_REVIEW',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pi_verification_log (
    id BIGSERIAL PRIMARY KEY,
    property_id VARCHAR(50),
    requirement_id VARCHAR(50),
    action VARCHAR(100) NOT NULL,
    performed_by VARCHAR(255),
    old_value JSONB,
    new_value JSONB,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pi_ai_jobs (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT,
    job_type VARCHAR(50) NOT NULL,
    status VARCHAR(50) DEFAULT 'PENDING',
    provider VARCHAR(50),
    model VARCHAR(100),
    input_summary TEXT,
    output_summary TEXT,
    error_message TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pi_properties_fingerprint ON pi_properties(fingerprint);
CREATE INDEX IF NOT EXISTS idx_pi_properties_location ON pi_properties(city, location);
CREATE INDEX IF NOT EXISTS idx_pi_properties_type ON pi_properties(property_type);
CREATE INDEX IF NOT EXISTS idx_pi_requirements_fingerprint ON pi_requirements(fingerprint);
CREATE INDEX IF NOT EXISTS idx_pi_sources_status ON pi_sources(ingestion_status);
CREATE INDEX IF NOT EXISTS idx_pi_matches_req_score ON pi_matches(requirement_id, match_score DESC);
'''

MIGRATION_SQL = [
    "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS fingerprint VARCHAR(64)",
    "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS source_id BIGINT",
    "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS extraction_confidence NUMERIC(5,2)",
    "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS verification_status VARCHAR(50) DEFAULT 'UNVERIFIED'",
    "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS micro_market VARCHAR(255)",
    "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS address TEXT",
    "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS google_maps_pin TEXT",
    "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS area_sqft NUMERIC(14,2)",
    "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS asking_rent_per_sqft NUMERIC(14,2)",
    "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS asking_sale_price NUMERIC(18,2)",
    "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS ceiling_height VARCHAR(100)",
    "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS power_load VARCHAR(100)",
    "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS cam_per_sqft NUMERIC(14,2)",
    "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS security_deposit VARCHAR(100)",
    "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS frontage VARCHAR(100)",
    "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW()",

    "ALTER TABLE pi_requirements ADD COLUMN IF NOT EXISTS fingerprint VARCHAR(64)",
    "ALTER TABLE pi_requirements ADD COLUMN IF NOT EXISTS source_id BIGINT",
    "ALTER TABLE pi_requirements ADD COLUMN IF NOT EXISTS extraction_confidence NUMERIC(5,2)",
    "ALTER TABLE pi_requirements ADD COLUMN IF NOT EXISTS requirement_type VARCHAR(100)",
    "ALTER TABLE pi_requirements ADD COLUMN IF NOT EXISTS property_type VARCHAR(100)",
    "ALTER TABLE pi_requirements ADD COLUMN IF NOT EXISTS budget_min NUMERIC(18,2)",
    "ALTER TABLE pi_requirements ADD COLUMN IF NOT EXISTS budget_max NUMERIC(18,2)",
    "ALTER TABLE pi_requirements ADD COLUMN IF NOT EXISTS floor_preference VARCHAR(100)",
    "ALTER TABLE pi_requirements ADD COLUMN IF NOT EXISTS parking_requirement TEXT",
    "ALTER TABLE pi_requirements ADD COLUMN IF NOT EXISTS possession_timeline VARCHAR(100)",
    "ALTER TABLE pi_requirements ADD COLUMN IF NOT EXISTS assigned_to VARCHAR(255)",
    "ALTER TABLE pi_requirements ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW()",

    "ALTER TABLE pi_sources ADD COLUMN IF NOT EXISTS mime_type VARCHAR(150)",
    "ALTER TABLE pi_sources ADD COLUMN IF NOT EXISTS extracted_record_type VARCHAR(50)",
    "ALTER TABLE pi_sources ADD COLUMN IF NOT EXISTS duplicate_records INTEGER DEFAULT 0",
    "ALTER TABLE pi_sources ADD COLUMN IF NOT EXISTS ai_provider VARCHAR(50)",

    "ALTER TABLE pi_contacts ADD COLUMN IF NOT EXISTS source_id BIGINT",
    "ALTER TABLE pi_media ADD COLUMN IF NOT EXISTS source_id BIGINT"
]

INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_pi_properties_fingerprint ON pi_properties(fingerprint)",
    "CREATE INDEX IF NOT EXISTS idx_pi_properties_location ON pi_properties(city, location)",
    "CREATE INDEX IF NOT EXISTS idx_pi_properties_type ON pi_properties(property_type)",
    "CREATE INDEX IF NOT EXISTS idx_pi_requirements_fingerprint ON pi_requirements(fingerprint)",
    "CREATE INDEX IF NOT EXISTS idx_pi_sources_status ON pi_sources(ingestion_status)",
    "CREATE INDEX IF NOT EXISTS idx_pi_matches_req_score ON pi_matches(requirement_id, match_score DESC)"
]

def initialize_database():
    create_statements = [
        s.strip()
        for s in SCHEMA_SQL.split(";")
        if s.strip() and not s.strip().upper().startswith("CREATE INDEX")
    ]

    with engine.begin() as conn:
        for statement in create_statements:
            conn.execute(text(statement))

        for statement in MIGRATION_SQL:
            conn.execute(text(statement))

        for statement in INDEX_SQL:
            conn.execute(text(statement))

@app.on_event("startup")
def startup_event():
    initialize_database()

@app.exception_handler(Exception)
async def error_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": str(exc), "path": request.url.path},
    )

class PropertyPayload(BaseModel):
    property_name: Optional[str] = None
    property_type: str = "NA"
    city: str = "NA"
    location: str = "NA"
    micro_market: Optional[str] = None
    address: Optional[str] = None
    google_maps_pin: Optional[str] = None
    area_sqft: Optional[float] = None
    available_area_sqft: Optional[float] = None
    minimum_area_sqft: Optional[float] = None
    maximum_area_sqft: Optional[float] = None
    floor: Optional[str] = None
    rent_or_sale: Optional[str] = None
    asking_rent_per_sqft: Optional[float] = None
    asking_sale_price: Optional[float] = None
    possession: Optional[str] = None
    nearby_brands: Optional[str] = None
    suitable_category: Optional[str] = None
    parking: Optional[str] = None
    ceiling_height: Optional[str] = None
    power_load: Optional[str] = None
    cam_per_sqft: Optional[float] = None
    security_deposit: Optional[str] = None
    frontage: Optional[str] = None
    owner_name: Optional[str] = None
    owner_contact: Optional[str] = None
    broker_name: Optional[str] = None
    broker_contact: Optional[str] = None
    remarks: Optional[str] = None
    source: Optional[str] = "Manual"
    extraction_confidence: Optional[float] = None

class RequirementPayload(BaseModel):
    client_name: Optional[str] = None
    company_name: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    requirement_type: Optional[str] = "Store Opening"
    property_type: Optional[str] = "Retail"
    city: Optional[str] = None
    preferred_locations: Optional[str] = None
    minimum_area_sqft: Optional[float] = None
    maximum_area_sqft: Optional[float] = None
    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    rent_or_sale: Optional[str] = None
    floor_preference: Optional[str] = None
    nearby_brands: Optional[str] = None
    suitable_category: Optional[str] = None
    parking_requirement: Optional[str] = None
    possession_timeline: Optional[str] = None
    additional_points: Optional[str] = None
    source: Optional[str] = "Manual"
    extraction_confidence: Optional[float] = None

class ExtractedProperty(PropertyPayload):
    record_type: Literal["property"] = "property"

class ExtractedRequirement(RequirementPayload):
    record_type: Literal["requirement"] = "requirement"

class ExtractionEnvelope(BaseModel):
    properties: list[ExtractedProperty] = Field(default_factory=list)
    requirements: list[ExtractedRequirement] = Field(default_factory=list)
    extraction_notes: Optional[str] = None

class TextIngestion(BaseModel):
    source_type: str = "WHATSAPP"
    source_name: Optional[str] = None
    text_content: str = Field(min_length=1)
    auto_extract: bool = True

class VerifyPayload(BaseModel):
    status: Literal["VERIFIED", "REJECTED", "NEEDS_REVIEW"]
    verified_by: str
    notes: Optional[str] = None

class GenericWebhook(BaseModel):
    record_type: Literal["property", "requirement"]
    source_name: Optional[str] = "Webhook"
    data: dict

def normalize(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v

def safe_float(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if not s or s.lower() in {"na", "n/a", "none", "null", "-"}:
        return None
    try:
        return float(s)
    except Exception:
        return None

def clean_text(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None

def make_id(prefix):
    return f"{prefix}-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

def fingerprint_property(data):
    raw = "|".join([
        (data.get("city") or "").strip().lower(),
        (data.get("location") or "").strip().lower(),
        (data.get("property_type") or "").strip().lower(),
        str(data.get("available_area_sqft") or ""),
        (data.get("floor") or "").strip().lower(),
        (data.get("rent_or_sale") or "").strip().lower(),
        (data.get("owner_contact") or data.get("broker_contact") or "").strip().lower(),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()

def fingerprint_requirement(data):
    raw = "|".join([
        (data.get("company_name") or data.get("client_name") or "").strip().lower(),
        (data.get("city") or "").strip().lower(),
        (data.get("preferred_locations") or "").strip().lower(),
        str(data.get("minimum_area_sqft") or ""),
        str(data.get("maximum_area_sqft") or ""),
        (data.get("rent_or_sale") or "").strip().lower(),
        (data.get("contact_phone") or data.get("contact_email") or "").strip().lower(),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()

def keyword_set(v):
    if not v:
        return set()
    return {x.strip().lower() for x in str(v).replace("|", ",").replace(";", ",").split(",") if x.strip()}

def insert_source(source_type, source_name=None, reference=None, filename=None, mime_type=None, status="RECEIVED"):
    with engine.begin() as conn:
        return conn.execute(
            text('''
                INSERT INTO pi_sources (
                    source_type, source_name, source_reference,
                    original_filename, mime_type, ingestion_status
                ) VALUES (
                    :source_type, :source_name, :source_reference,
                    :original_filename, :mime_type, :ingestion_status
                ) RETURNING id
            '''),
            {
                "source_type": source_type,
                "source_name": source_name,
                "source_reference": reference,
                "original_filename": filename,
                "mime_type": mime_type,
                "ingestion_status": status,
            },
        ).scalar_one()

def update_source(source_id, **fields):
    allowed = {
        "ingestion_status","extracted_record_type","processed_records",
        "duplicate_records","error_message","ai_provider","ai_model","processed_at"
    }
    payload = {k:v for k,v in fields.items() if k in allowed}
    if not payload:
        return
    payload["source_id"] = source_id
    sets = ", ".join(f"{k}=:{k}" for k in payload if k != "source_id")
    with engine.begin() as conn:
        conn.execute(text(f"UPDATE pi_sources SET {sets} WHERE id=:source_id"), payload)

def create_ai_job(source_id, job_type, summary):
    with engine.begin() as conn:
        return conn.execute(
            text('''
                INSERT INTO pi_ai_jobs (
                    source_id, job_type, status, provider, model, input_summary, started_at
                ) VALUES (
                    :source_id, :job_type, 'RUNNING', 'gemini', :model, :summary, NOW()
                ) RETURNING id
            '''),
            {"source_id":source_id,"job_type":job_type,"model":GEMINI_MODEL,"summary":summary},
        ).scalar_one()

def finish_ai_job(job_id, status, output=None, error=None):
    with engine.begin() as conn:
        conn.execute(
            text('''
                UPDATE pi_ai_jobs
                SET status=:status, output_summary=:output,
                    error_message=:error, completed_at=NOW()
                WHERE id=:job_id
            '''),
            {"status":status,"output":output,"error":error,"job_id":job_id},
        )

def insert_property(data, source_id=None):
    data = dict(data)
    data["property_type"] = clean_text(data.get("property_type")) or "NA"
    data["city"] = clean_text(data.get("city")) or "NA"
    data["location"] = clean_text(data.get("location")) or "NA"

    numeric_fields = [
        "area_sqft","available_area_sqft","minimum_area_sqft","maximum_area_sqft",
        "asking_rent_per_sqft","asking_sale_price","cam_per_sqft","extraction_confidence"
    ]
    for field in numeric_fields:
        data[field] = safe_float(data.get(field))

    fp = fingerprint_property(data)

    with engine.begin() as conn:
        duplicate = conn.execute(
            text("SELECT property_id FROM pi_properties WHERE fingerprint=:fp LIMIT 1"),
            {"fp":fp},
        ).first()
        if duplicate:
            return {"status":"duplicate","property_id":duplicate[0]}

        property_id = make_id("PROP")
        params = {
            "property_id":property_id,
            "fingerprint":fp,
            "source_id":source_id,
            **{k:data.get(k) for k in PropertyPayload.model_fields},
        }

        conn.execute(
            text('''
                INSERT INTO pi_properties (
                    property_id,fingerprint,property_name,property_type,city,location,
                    micro_market,address,google_maps_pin,area_sqft,available_area_sqft,
                    minimum_area_sqft,maximum_area_sqft,floor,rent_or_sale,
                    asking_rent_per_sqft,asking_sale_price,possession,nearby_brands,
                    suitable_category,parking,ceiling_height,power_load,cam_per_sqft,
                    security_deposit,frontage,owner_name,owner_contact,broker_name,
                    broker_contact,remarks,source,source_id,extraction_confidence
                ) VALUES (
                    :property_id,:fingerprint,:property_name,:property_type,:city,:location,
                    :micro_market,:address,:google_maps_pin,:area_sqft,:available_area_sqft,
                    :minimum_area_sqft,:maximum_area_sqft,:floor,:rent_or_sale,
                    :asking_rent_per_sqft,:asking_sale_price,:possession,:nearby_brands,
                    :suitable_category,:parking,:ceiling_height,:power_load,:cam_per_sqft,
                    :security_deposit,:frontage,:owner_name,:owner_contact,:broker_name,
                    :broker_contact,:remarks,:source,:source_id,:extraction_confidence
                )
            '''),
            params,
        )

        conn.execute(
            text('''
                INSERT INTO pi_verification_log(property_id,action,performed_by,notes)
                VALUES(:property_id,'CREATED','SYSTEM','Queued for verification.')
            '''),
            {"property_id":property_id},
        )

    return {"status":"created","property_id":property_id}

def insert_requirement(data, source_id=None):
    data = dict(data)
    for field in ["minimum_area_sqft","maximum_area_sqft","budget_min","budget_max","extraction_confidence"]:
        data[field] = safe_float(data.get(field))
    fp = fingerprint_requirement(data)

    with engine.begin() as conn:
        duplicate = conn.execute(
            text("SELECT requirement_id FROM pi_requirements WHERE fingerprint=:fp LIMIT 1"),
            {"fp":fp},
        ).first()
        if duplicate:
            return {"status":"duplicate","requirement_id":duplicate[0]}

        requirement_id = make_id("REQ")
        params = {
            "requirement_id":requirement_id,
            "fingerprint":fp,
            "source_id":source_id,
            **{k:data.get(k) for k in RequirementPayload.model_fields},
        }

        conn.execute(
            text('''
                INSERT INTO pi_requirements (
                    requirement_id,fingerprint,client_name,company_name,contact_phone,
                    contact_email,requirement_type,property_type,city,preferred_locations,
                    minimum_area_sqft,maximum_area_sqft,budget_min,budget_max,rent_or_sale,
                    floor_preference,nearby_brands,suitable_category,parking_requirement,
                    possession_timeline,additional_points,source,source_id,extraction_confidence
                ) VALUES (
                    :requirement_id,:fingerprint,:client_name,:company_name,:contact_phone,
                    :contact_email,:requirement_type,:property_type,:city,:preferred_locations,
                    :minimum_area_sqft,:maximum_area_sqft,:budget_min,:budget_max,:rent_or_sale,
                    :floor_preference,:nearby_brands,:suitable_category,:parking_requirement,
                    :possession_timeline,:additional_points,:source,:source_id,:extraction_confidence
                )
            '''),
            params,
        )
    return {"status":"created","requirement_id":requirement_id}

EXTRACTION_PROMPT = '''
You are a commercial real-estate Property Intelligence Extraction Agent.
Extract every distinct property inventory record and every distinct client/retailer requirement.
Do not invent facts. Use null for unknown optional fields.
Preserve city and location. Keep phone numbers as text.
A property is inventory being offered. A requirement is demand/wanted/required.
If the source contains multiple listings, return all separately.
Set extraction_confidence from 0 to 100.
Extract owner/broker contacts only if explicitly present.
'''

def gemini_extract_text(source_text):
    if not gemini_client:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[EXTRACTION_PROMPT, "\nSOURCE:\n", source_text],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ExtractionEnvelope,
            temperature=0.1,
        ),
    )
    if getattr(response, "parsed", None) is not None:
        return ExtractionEnvelope.model_validate(response.parsed)
    return ExtractionEnvelope.model_validate_json(response.text)

def gemini_extract_file(file_bytes, mime_type, filename):
    if not gemini_client:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    suffix = os.path.splitext(filename or "")[1] or ".bin"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    try:
        uploaded = gemini_client.files.upload(file=tmp_path, config={"mime_type":mime_type})
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[EXTRACTION_PROMPT, uploaded],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ExtractionEnvelope,
                temperature=0.1,
            ),
        )
        if getattr(response, "parsed", None) is not None:
            return ExtractionEnvelope.model_validate(response.parsed)
        return ExtractionEnvelope.model_validate_json(response.text)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

def persist_extraction(envelope, source_id):
    props, reqs, duplicates = [], [], 0
    for prop in envelope.properties:
        data = prop.model_dump(exclude={"record_type"})
        data["source"] = data.get("source") or f"AI_SOURCE_{source_id}"
        result = insert_property(data, source_id)
        duplicates += 1 if result["status"] == "duplicate" else 0
        props.append(result)
    for req in envelope.requirements:
        data = req.model_dump(exclude={"record_type"})
        data["source"] = data.get("source") or f"AI_SOURCE_{source_id}"
        result = insert_requirement(data, source_id)
        duplicates += 1 if result["status"] == "duplicate" else 0
        reqs.append(result)

    if props and reqs:
        record_type = "MIXED"
    elif props:
        record_type = "PROPERTY"
    elif reqs:
        record_type = "REQUIREMENT"
    else:
        record_type = "NONE"

    update_source(
        source_id,
        ingestion_status="PROCESSED",
        extracted_record_type=record_type,
        processed_records=len(props)+len(reqs),
        duplicate_records=duplicates,
        ai_provider="gemini",
        ai_model=GEMINI_MODEL,
        processed_at=datetime.utcnow(),
    )
    return {"properties":props,"requirements":reqs,"duplicates":duplicates,"notes":envelope.extraction_notes}

def calculate_match(req, prop):
    score, reasons, exclusions = 0, [], []
    req_city=(req.get("city") or "").lower().strip()
    prop_city=(prop.get("city") or "").lower().strip()
    if req_city:
        if req_city == prop_city:
            score += 20; reasons.append("City match")
        else:
            exclusions.append("City mismatch")
    else:
        score += 5

    req_locations=keyword_set(req.get("preferred_locations"))
    prop_location=(prop.get("location") or "").lower().strip()
    if req_locations:
        if any(x in prop_location or prop_location in x for x in req_locations):
            score += 25; reasons.append("Location match")
        else:
            exclusions.append("Location mismatch")
    else:
        score += 5

    if req.get("property_type"):
        if (req.get("property_type") or "").lower().strip() == (prop.get("property_type") or "").lower().strip():
            score += 15; reasons.append("Property type match")
    else:
        score += 5

    if req.get("rent_or_sale"):
        if (req.get("rent_or_sale") or "").lower().strip() == (prop.get("rent_or_sale") or "").lower().strip():
            score += 10; reasons.append("Rent/Sale match")
    else:
        score += 5

    avail=safe_float(prop.get("available_area_sqft"))
    rmin=safe_float(req.get("minimum_area_sqft"))
    rmax=safe_float(req.get("maximum_area_sqft"))
    if avail is not None:
        if (rmin is None or avail >= rmin) and (rmax is None or avail <= rmax):
            score += 20; reasons.append("Area within requirement")
        else:
            exclusions.append("Area outside requirement")

    if keyword_set(req.get("suitable_category")) & keyword_set(prop.get("suitable_category")):
        score += 5; reasons.append("Category overlap")
    if keyword_set(req.get("nearby_brands")) & keyword_set(prop.get("nearby_brands")):
        score += 5; reasons.append("Nearby brand overlap")
    if (prop.get("availability_status") or "").lower() == "available":
        score += 5; reasons.append("Available")

    return min(score,100), reasons, exclusions

@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {
        "status":"ok","service":"property-intelligence-all-layers",
        "version":APP_VERSION,"database":"connected",
        "gemini_configured":bool(GEMINI_API_KEY),
        "ai_provider":"gemini","ai_model":GEMINI_MODEL,
        "workspace":"/workspace","database_view":"/database"
    }

@app.get("/api/status")
def status():
    tables={"properties":"pi_properties","requirements":"pi_requirements","sources":"pi_sources","matches":"pi_matches","verification":"pi_verification_log","ai_jobs":"pi_ai_jobs"}
    with engine.connect() as conn:
        counts={k:conn.execute(text(f"SELECT COUNT(*) FROM {v}")).scalar_one() for k,v in tables.items()}
    return {"status":"ok","version":APP_VERSION,"gemini_configured":bool(GEMINI_API_KEY),"gemini_model":GEMINI_MODEL,"records":counts}

@app.post("/api/properties")
def create_property(payload: PropertyPayload):
    return insert_property(payload.model_dump())

@app.post("/api/requirements")
def create_requirement(payload: RequirementPayload):
    return insert_requirement(payload.model_dump())

@app.post("/api/ingest/text")
def ingest_text(payload: TextIngestion):
    source_id=insert_source(payload.source_type,payload.source_name,payload.text_content,status="RECEIVED")
    if not payload.auto_extract:
        update_source(source_id,ingestion_status="READY_FOR_AI_EXTRACTION")
        return {"status":"stored","source_id":source_id}
    job_id=create_ai_job(source_id,"TEXT_EXTRACTION",payload.source_name or payload.source_type)
    try:
        envelope=gemini_extract_text(payload.text_content)
        result=persist_extraction(envelope,source_id)
        finish_ai_job(job_id,"COMPLETED",f"{len(envelope.properties)} properties, {len(envelope.requirements)} requirements")
        return {"status":"PROCESSED","source_id":source_id,"job_id":job_id,**result}
    except Exception as exc:
        update_source(source_id,ingestion_status="FAILED",error_message=str(exc),ai_provider="gemini",ai_model=GEMINI_MODEL,processed_at=datetime.utcnow())
        finish_ai_job(job_id,"FAILED",error=str(exc))
        raise

def import_csv_bytes(file_bytes, filename, source_id):
    reader=csv.DictReader(io.StringIO(file_bytes.decode("utf-8-sig",errors="replace")))
    aliases={
        "property_name":["Property name","Property Name","property_name"],
        "property_type":["Property type","Property Type","property_type"],
        "city":["City","city"],"location":["Location","location"],
        "micro_market":["Micro market","Micro Market","micro_market"],
        "address":["Address","address"],
        "google_maps_pin":["Google Maps Pin","Google pin","google_maps_pin"],
        "available_area_sqft":["Available area","Available Area","available_area_sqft"],
        "minimum_area_sqft":["Minimum area","Minimum Area","minimum_area_sqft"],
        "maximum_area_sqft":["Maximum area","Maximum Area","maximum_area_sqft"],
        "floor":["Floor","floor"],"rent_or_sale":["Rent/Sale","Rent or Sale","rent_or_sale"],
        "possession":["Possession","possession"],
        "nearby_brands":["Nearby brand","Nearby brands","nearby_brands"],
        "suitable_category":["Suitable category","Suitable Category","suitable_category"],
        "parking":["Parking","parking"],"owner_name":["Owner name","Owner Name","owner_name"],
        "owner_contact":["Owner contact","Owner Contact","owner_contact"],
        "broker_name":["Broker name","Broker Name","broker_name"],
        "broker_contact":["Broker contact","Broker Contact","broker_contact"],
        "remarks":["Remarks","remarks"],
    }
    def pick(row,names):
        for name in names:
            if row.get(name) not in (None,""):
                return row.get(name)
        return None
    inserted=duplicates=0; errors=[]
    for line,row in enumerate(reader,start=2):
        try:
            data={field:pick(row,names) for field,names in aliases.items()}
            for n in ["available_area_sqft","minimum_area_sqft","maximum_area_sqft"]:
                data[n]=safe_float(data.get(n))
            data["source"]=f"CSV:{filename}"
            result=insert_property(data,source_id)
            if result["status"]=="duplicate":duplicates+=1
            else:inserted+=1
        except Exception as exc:
            errors.append({"row":line,"error":str(exc)})
    update_source(
        source_id,
        ingestion_status="PROCESSED" if not errors else "PROCESSED_WITH_ERRORS",
        extracted_record_type="PROPERTY",
        processed_records=inserted,
        duplicate_records=duplicates,
        error_message=json.dumps(errors[:20]) if errors else None,
        processed_at=datetime.utcnow(),
    )
    return {"status":"PROCESSED" if not errors else "PROCESSED_WITH_ERRORS","source_id":source_id,"inserted":inserted,"duplicates":duplicates,"errors":errors[:20]}

@app.post("/api/ingest/file")
async def ingest_file(file: UploadFile=File(...),source_type:str=Query("DOCUMENT"),source_name:Optional[str]=Query(None)):
    file_bytes=await file.read()
    if len(file_bytes) > MAX_UPLOAD_MB*1024*1024:
        raise HTTPException(status_code=413,detail=f"File exceeds {MAX_UPLOAD_MB} MB.")
    mime=file.content_type or "application/octet-stream"
    source_id=insert_source(source_type.upper(),source_name or file.filename,filename=file.filename,mime_type=mime,status="RECEIVED")
    if mime in {"text/csv","application/csv"} or (file.filename or "").lower().endswith(".csv"):
        return import_csv_bytes(file_bytes,file.filename or "upload.csv",source_id)
    job_id=create_ai_job(source_id,"FILE_EXTRACTION",f"{file.filename} ({mime})")
    try:
        envelope=gemini_extract_file(file_bytes,mime,file.filename or "upload.bin")
        result=persist_extraction(envelope,source_id)
        finish_ai_job(job_id,"COMPLETED",f"{len(envelope.properties)} properties, {len(envelope.requirements)} requirements")
        return {"status":"PROCESSED","source_id":source_id,"job_id":job_id,**result}
    except Exception as exc:
        update_source(source_id,ingestion_status="FAILED",error_message=str(exc),ai_provider="gemini",ai_model=GEMINI_MODEL,processed_at=datetime.utcnow())
        finish_ai_job(job_id,"FAILED",error=str(exc))
        raise

@app.post("/api/webhooks/ingest")
def webhook(payload: GenericWebhook):
    source_id=insert_source("WEBHOOK",payload.source_name,json.dumps(payload.data),status="PROCESSING")
    result=insert_property(payload.data,source_id) if payload.record_type=="property" else insert_requirement(payload.data,source_id)
    update_source(source_id,ingestion_status="PROCESSED",extracted_record_type=payload.record_type.upper(),processed_records=1 if result["status"]=="created" else 0,duplicate_records=1 if result["status"]=="duplicate" else 0,processed_at=datetime.utcnow())
    return {"status":"ok","source_id":source_id,"result":result}

@app.post("/api/properties/{property_id}/verify")
def verify(property_id:str,payload:VerifyPayload):
    with engine.begin() as conn:
        old=conn.execute(text("SELECT verification_status,verified_by,verified_date FROM pi_properties WHERE property_id=:id"),{"id":property_id}).first()
        if not old: raise HTTPException(status_code=404,detail="Property not found.")
        conn.execute(text("UPDATE pi_properties SET verification_status=:status,verified_by=:by,verified_date=CURRENT_DATE,updated_at=NOW() WHERE property_id=:id"),{"status":payload.status,"by":payload.verified_by,"id":property_id})
        conn.execute(text('''
            INSERT INTO pi_verification_log(property_id,action,performed_by,old_value,new_value,notes)
            VALUES(:id,'VERIFICATION_UPDATE',:by,CAST(:old AS JSONB),CAST(:new AS JSONB),:notes)
        '''),{"id":property_id,"by":payload.verified_by,"old":json.dumps(dict(old._mapping),default=str),"new":json.dumps({"verification_status":payload.status}),"notes":payload.notes})
    return {"status":"updated","property_id":property_id,"verification_status":payload.status}

@app.post("/api/match/{requirement_id}")
def match(requirement_id:str):
    with engine.begin() as conn:
        rr=conn.execute(text("SELECT * FROM pi_requirements WHERE requirement_id=:id"),{"id":requirement_id}).first()
        if not rr: raise HTTPException(status_code=404,detail="Requirement not found.")
        req={k:normalize(v) for k,v in dict(rr._mapping).items()}
        props=conn.execute(text("SELECT * FROM pi_properties WHERE availability_status='Available' AND entry_status='Active' ORDER BY id DESC")).fetchall()
        conn.execute(text("DELETE FROM pi_matches WHERE requirement_id=:id"),{"id":requirement_id})
        matches=[]
        for row in props:
            prop={k:normalize(v) for k,v in dict(row._mapping).items()}
            score,reasons,exclusions=calculate_match(req,prop)
            matches.append({"property_id":prop["property_id"],"property_name":prop.get("property_name"),"city":prop.get("city"),"location":prop.get("location"),"available_area_sqft":prop.get("available_area_sqft"),"score":score,"reasons":reasons,"exclusions":exclusions})
        matches.sort(key=lambda x:x["score"],reverse=True)
        for rank,item in enumerate(matches,start=1):
            conn.execute(text('''
                INSERT INTO pi_matches(requirement_id,property_id,match_score,rank,match_reasons,exclusions,status)
                VALUES(:rid,:pid,:score,:rank,CAST(:reasons AS JSONB),CAST(:exclusions AS JSONB),'READY_FOR_REVIEW')
            '''),{"rid":requirement_id,"pid":item["property_id"],"score":item["score"],"rank":rank,"reasons":json.dumps(item["reasons"]),"exclusions":json.dumps(item["exclusions"])})
    return {"status":"READY_FOR_REVIEW","requirement_id":requirement_id,"total_properties":len(matches),"matches":matches[:50]}

TABLES={
    "properties":"pi_properties","requirements":"pi_requirements","contacts":"pi_contacts",
    "sources":"pi_sources","media":"pi_media","matches":"pi_matches",
    "verification":"pi_verification_log","ai_jobs":"pi_ai_jobs"
}
PRIVATE={"address","owner_name","owner_contact","broker_name","broker_contact","verified_date","verified_by","remarks","source","fingerprint"}

@app.get("/api/database/{table_key}")
def database(table_key:str,limit:int=Query(200,ge=1,le=2000),internal:bool=False):
    if table_key not in TABLES: raise HTTPException(status_code=404,detail="Unknown table.")
    with engine.connect() as conn:
        rows=[{k:normalize(v) for k,v in dict(r._mapping).items()} for r in conn.execute(text(f"SELECT * FROM {TABLES[table_key]} ORDER BY id DESC LIMIT :limit"),{"limit":limit})]
    if table_key=="properties" and not internal:
        rows=[{k:v for k,v in row.items() if k not in PRIVATE} for row in rows]
    return {"status":"ok","table":table_key,"count":len(rows),"rows":rows}

@app.get("/api/export/properties.csv")
def export_csv(internal:bool=False):
    with engine.connect() as conn:
        rows=[dict(r._mapping) for r in conn.execute(text("SELECT * FROM pi_properties ORDER BY id DESC"))]
    if not internal:
        rows=[{k:v for k,v in row.items() if k not in PRIVATE} for row in rows]
    headers=list(rows[0].keys()) if rows else ["property_id","property_name","property_type","city","location"]
    out=io.StringIO(); w=csv.DictWriter(out,fieldnames=headers); w.writeheader()
    for row in rows:w.writerow({k:normalize(v) for k,v in row.items()})
    return StreamingResponse(iter([out.getvalue()]),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=property-intelligence-properties.csv"})

WORKSPACE_HTML = '''
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Property Intelligence All Layers</title>
<style>
*{box-sizing:border-box}body{font-family:Arial;margin:0;background:#f5f7fb;color:#172033}
header{background:#111827;color:white;padding:22px 28px}header h1{margin:0}.sub{color:#9ca3af;margin-top:6px}
nav{padding:12px 22px;background:white;border-bottom:1px solid #e5e7eb}nav a{margin-right:16px;text-decoration:none;color:#2563eb;font-weight:600}
.wrap{padding:22px;display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:18px}
.card{background:white;border:1px solid #e5e7eb;border-radius:12px;padding:18px}.card h3{margin-top:0}
input,textarea,select{width:100%;padding:10px;margin:6px 0 10px;border:1px solid #d1d5db;border-radius:8px}
button{background:#111827;color:white;border:0;border-radius:8px;padding:11px 15px;cursor:pointer}
pre{white-space:pre-wrap;background:#f8fafc;padding:12px;border-radius:8px;max-height:280px;overflow:auto;font-size:12px}
</style></head><body>
<header><h1>Property Intelligence Agent - All Layers</h1><div class="sub">Ingest → Gemini Extract → Normalize → Deduplicate → Verify → Match → Review → Export</div></header>
<nav><a href="/workspace">Workspace</a><a href="/database">Database</a><a href="/api/status">Status</a><a href="/api/export/properties.csv">Export CSV</a></nav>
<div class="wrap">
<div class="card"><h3>Upload Photo / Magazine / PDF / CSV</h3><input id="fsn" placeholder="Source name"><select id="fst"><option>MAGAZINE</option><option>NEWSPAPER</option><option>PHOTO</option><option>PDF</option><option>CSV</option><option>OTHER</option></select><input id="fu" type="file"><button onclick="uploadFile()">Upload + Extract</button><pre id="fo"></pre></div>
<div class="card"><h3>Paste WhatsApp / Email / Text</h3><input id="tsn" placeholder="Source name"><select id="tst"><option>WHATSAPP</option><option>EMAIL</option><option>MANUAL_TEXT</option></select><textarea id="tc" rows="8" placeholder="Paste content"></textarea><button onclick="ingestText()">Gemini Extract</button><pre id="to"></pre></div>
<div class="card"><h3>Manual Property</h3><input id="pn" placeholder="Property name"><input id="pt" value="Retail" placeholder="Property type"><input id="pc" placeholder="City"><input id="pl" placeholder="Location"><input id="pa" type="number" placeholder="Available area sqft"><input id="pf" placeholder="Floor"><select id="px"><option>Rent</option><option>Sale</option></select><input id="pca" placeholder="Suitable category"><input id="pb" placeholder="Nearby brands"><input id="pg" placeholder="Google Maps link"><button onclick="addProperty()">Save Property</button><pre id="po"></pre></div>
<div class="card"><h3>Manual Requirement</h3><input id="rc" placeholder="Client"><input id="rco" placeholder="Company"><input id="rci" placeholder="City"><input id="rl" placeholder="Preferred locations"><input id="rmin" type="number" placeholder="Min sqft"><input id="rmax" type="number" placeholder="Max sqft"><select id="rx"><option>Rent</option><option>Sale</option></select><input id="rca" placeholder="Category"><input id="rb" placeholder="Nearby brands"><button onclick="addRequirement()">Save Requirement</button><pre id="ro"></pre></div>
<div class="card"><h3>Run Matcher</h3><input id="rid" placeholder="Requirement ID"><button onclick="runMatch()">Run Matcher</button><pre id="mo"></pre></div>
<div class="card"><h3>Verify Property</h3><input id="vpid" placeholder="Property ID"><input id="vby" placeholder="Verified by"><select id="vst"><option>VERIFIED</option><option>NEEDS_REVIEW</option><option>REJECTED</option></select><textarea id="vn" placeholder="Notes"></textarea><button onclick="verify()">Update</button><pre id="vo"></pre></div>
<div class="card"><h3>System Status</h3><button onclick="loadStatus()">Refresh</button><pre id="so"></pre></div>
</div>
<script>
const e=id=>document.getElementById(id),v=id=>e(id).value,s=(id,d)=>e(id).textContent=JSON.stringify(d,null,2);
async function jf(url,opt={}){const r=await fetch(url,opt),t=await r.text();let d;try{d=JSON.parse(t)}catch(x){d={status:"error",message:t}}if(!r.ok)throw d;return d}
async function uploadFile(){try{const f=e("fu").files[0];if(!f)return s("fo",{error:"Choose file"});const fd=new FormData();fd.append("file",f);const q=new URLSearchParams({source_type:v("fst"),source_name:v("fsn")});s("fo",{status:"processing"});s("fo",await jf("/api/ingest/file?"+q,{method:"POST",body:fd}));loadStatus()}catch(x){s("fo",x)}}
async function ingestText(){try{s("to",await jf("/api/ingest/text",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({source_type:v("tst"),source_name:v("tsn"),text_content:v("tc"),auto_extract:true})}));loadStatus()}catch(x){s("to",x)}}
async function addProperty(){try{s("po",await jf("/api/properties",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({property_name:v("pn"),property_type:v("pt"),city:v("pc"),location:v("pl"),available_area_sqft:Number(v("pa"))||null,floor:v("pf"),rent_or_sale:v("px"),suitable_category:v("pca"),nearby_brands:v("pb"),google_maps_pin:v("pg"),source:"Manual"})}));loadStatus()}catch(x){s("po",x)}}
async function addRequirement(){try{const d=await jf("/api/requirements",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({client_name:v("rc"),company_name:v("rco"),city:v("rci"),preferred_locations:v("rl"),minimum_area_sqft:Number(v("rmin"))||null,maximum_area_sqft:Number(v("rmax"))||null,rent_or_sale:v("rx"),suitable_category:v("rca"),nearby_brands:v("rb"),source:"Manual"})});s("ro",d);if(d.requirement_id)e("rid").value=d.requirement_id;loadStatus()}catch(x){s("ro",x)}}
async function runMatch(){try{s("mo",await jf("/api/match/"+encodeURIComponent(v("rid")),{method:"POST"}));loadStatus()}catch(x){s("mo",x)}}
async function verify(){try{s("vo",await jf("/api/properties/"+encodeURIComponent(v("vpid"))+"/verify",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({status:v("vst"),verified_by:v("vby"),notes:v("vn")})}));loadStatus()}catch(x){s("vo",x)}}
async function loadStatus(){try{s("so",await jf("/api/status"))}catch(x){s("so",x)}}loadStatus();
</script></body></html>
'''

DATABASE_HTML = '''
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Property Intelligence Database</title>
<style>body{font-family:Arial;margin:0;background:#f4f6f8;color:#172033}header{background:#111827;color:white;padding:24px 30px}nav{padding:12px 24px;background:white;border-bottom:1px solid #ddd}nav a{margin-right:16px;color:#2563eb;text-decoration:none;font-weight:600}.wrap{padding:24px}.tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}button{padding:9px 12px;border:1px solid #d1d5db;background:white;border-radius:8px;cursor:pointer}button.active{background:#111827;color:white}.toolbar{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}input{padding:9px 12px;border:1px solid #d1d5db;border-radius:8px;min-width:260px}.card{background:white;border:1px solid #e5e7eb;border-radius:12px;overflow:auto;max-height:72vh}table{border-collapse:collapse;width:100%;font-size:12px}th,td{padding:9px;border-bottom:1px solid #eee;text-align:left;white-space:nowrap}th{position:sticky;top:0;background:#f9fafb}.meta{margin:12px 0;color:#6b7280}.error{padding:20px;color:#b91c1c;background:#fef2f2}</style></head>
<body><header><h1>Property Intelligence Database</h1><p>Live organized PostgreSQL records</p></header><nav><a href="/workspace">Workspace</a><a href="/database">Database</a><a href="/api/export/properties.csv">Export CSV</a></nav><div class="wrap"><div class="tabs">__TABS__</div><div class="toolbar"><div class="meta" id="meta">Loading...</div><input id="search" placeholder="Search..." oninput="filterRows()"></div><div class="card"><table id="grid"></table></div></div>
<script>let rows=[],cols=[];const esc=v=>v==null?"":String(v).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");function render(x){const g=document.getElementById("grid");if(!x.length){g.innerHTML="<tr><td style='padding:20px'>No records yet.</td></tr>";return}cols=Object.keys(x[0]);let h="<thead><tr>"+cols.map(c=>"<th>"+esc(c)+"</th>").join("")+"</tr></thead><tbody>";h+=x.map(r=>"<tr>"+cols.map(c=>"<td>"+esc(typeof r[c]==="object"?JSON.stringify(r[c]):r[c])+"</td>").join("")+"</tr>").join("");g.innerHTML=h+"</tbody>"}async function loadTable(n){document.querySelectorAll("button").forEach(b=>b.classList.remove("active"));const b=document.getElementById("tab-"+n);if(b)b.classList.add("active");const r=await fetch("/api/database/"+n+"?limit=1000"),t=await r.text();let d;try{d=JSON.parse(t)}catch(e){d={status:"error",message:t}}if(!r.ok||d.status==="error"){document.getElementById("meta").innerText="Error";document.getElementById("grid").innerHTML="<tr><td class='error'>"+esc(d.message||d.detail)+"</td></tr>";return}rows=d.rows||[];document.getElementById("meta").innerText=(d.count||0)+" records in "+n;document.getElementById("search").value="";render(rows)}function filterRows(){const q=document.getElementById("search").value.toLowerCase();render(!q?rows:rows.filter(r=>Object.values(r).some(v=>String(v??"").toLowerCase().includes(q))))}loadTable("properties");</script></body></html>
'''

@app.get("/workspace",response_class=HTMLResponse)
def workspace():
    return HTMLResponse(WORKSPACE_HTML)

@app.get("/database",response_class=HTMLResponse)
def database_view():
    tabs="".join(f'<button id="tab-{k}" onclick="loadTable(\'{k}\')">{k.title()}</button>' for k in TABLES)
    return HTMLResponse(DATABASE_HTML.replace("__TABS__",tabs))

@app.get("/")
def root():
    return {"service":"Property Intelligence Agent - All Layers","version":APP_VERSION,"workspace":"/workspace","database":"/database","health":"/health","status":"/api/status","docs":"/docs"}
