import os
import csv
import io
import json
import uuid
from decimal import Decimal
from datetime import date, datetime

from fastapi import FastAPI, Query, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text


# =========================================================
# DATABASE
# =========================================================

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./property_intelligence.db")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
)

app = FastAPI(
    title="Property Intelligence Connected V1",
    version="3.0.0",
)


# =========================================================
# SCHEMA
# =========================================================

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pi_properties (
    id BIGSERIAL PRIMARY KEY,
    property_id VARCHAR(40) UNIQUE NOT NULL,
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
    remarks TEXT,
    source VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pi_requirements (
    id BIGSERIAL PRIMARY KEY,
    requirement_id VARCHAR(40) UNIQUE NOT NULL,
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
    status VARCHAR(50) DEFAULT 'New',
    assigned_to VARCHAR(255),
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
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pi_sources (
    id BIGSERIAL PRIMARY KEY,
    source_type VARCHAR(50) NOT NULL,
    source_name VARCHAR(255),
    source_reference TEXT,
    original_filename VARCHAR(500),
    ingestion_status VARCHAR(50) DEFAULT 'Pending',
    processed_records INTEGER DEFAULT 0,
    error_message TEXT,
    ai_model VARCHAR(100),
    uploaded_at TIMESTAMPTZ DEFAULT NOW(),
    processed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS pi_media (
    id BIGSERIAL PRIMARY KEY,
    property_id VARCHAR(40) NOT NULL,
    media_type VARCHAR(30) NOT NULL,
    url TEXT NOT NULL,
    title VARCHAR(255),
    is_primary BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pi_matches (
    id BIGSERIAL PRIMARY KEY,
    requirement_id VARCHAR(40) NOT NULL,
    property_id VARCHAR(40) NOT NULL,
    match_score NUMERIC(5,2) DEFAULT 0,
    rank INTEGER,
    match_reasons JSONB DEFAULT '[]'::jsonb,
    exclusions JSONB DEFAULT '[]'::jsonb,
    status VARCHAR(50) DEFAULT 'READY_FOR_REVIEW',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pi_verification_log (
    id BIGSERIAL PRIMARY KEY,
    property_id VARCHAR(40),
    requirement_id VARCHAR(40),
    action VARCHAR(100) NOT NULL,
    performed_by VARCHAR(255),
    old_value JSONB,
    new_value JSONB,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""

VISIBLE_TABLES = {
    "properties": "pi_properties",
    "requirements": "pi_requirements",
    "contacts": "pi_contacts",
    "sources": "pi_sources",
    "media": "pi_media",
    "matches": "pi_matches",
    "verification": "pi_verification_log",
}

PRIVATE_PROPERTY_COLUMNS = {
    "address","owner_name","owner_contact","broker_name","broker_contact",
    "verified_date","verified_by","remarks","source"
}


def initialize_database():
    statements = [p.strip() for p in SCHEMA_SQL.split(";") if p.strip()]
    with engine.begin() as conn:
        for statement in statements:
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


# =========================================================
# MODELS
# =========================================================

class PropertyCreate(BaseModel):
    property_name: str | None = None
    property_type: str = "NA"
    city: str = "NA"
    location: str = "NA"
    micro_market: str | None = None
    available_area_sqft: float | None = None
    minimum_area_sqft: float | None = None
    maximum_area_sqft: float | None = None
    floor: str | None = None
    rent_or_sale: str | None = None
    asking_rent_per_sqft: float | None = None
    asking_sale_price: float | None = None
    possession: str | None = None
    nearby_brands: str | None = None
    suitable_category: str | None = None
    parking: str | None = None
    google_maps_pin: str | None = None
    source: str | None = "Manual"
    owner_name: str | None = None
    owner_contact: str | None = None
    broker_name: str | None = None
    broker_contact: str | None = None
    remarks: str | None = None


class RequirementCreate(BaseModel):
    client_name: str | None = None
    company_name: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    requirement_type: str | None = "Store Opening"
    property_type: str | None = "Retail"
    city: str | None = None
    preferred_locations: str | None = None
    minimum_area_sqft: float | None = None
    maximum_area_sqft: float | None = None
    budget_min: float | None = None
    budget_max: float | None = None
    rent_or_sale: str | None = None
    floor_preference: str | None = None
    nearby_brands: str | None = None
    suitable_category: str | None = None
    parking_requirement: str | None = None
    possession_timeline: str | None = None
    additional_points: str | None = None
    source: str | None = "Manual"


class TextImport(BaseModel):
    source_type: str = "WHATSAPP"
    source_name: str | None = None
    text_content: str = Field(min_length=1)


# =========================================================
# HELPERS
# =========================================================

def normalize(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


def make_id(prefix: str):
    stamp = datetime.utcnow().strftime("%Y%m%d")
    short = uuid.uuid4().hex[:6].upper()
    return f"{prefix}-{stamp}-{short}"


def safe_float(v):
    if v in (None, "", "NA", "N/A"):
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return None


def fetch_rows(table_key: str, limit: int = 100):
    if table_key not in VISIBLE_TABLES:
        raise HTTPException(status_code=404, detail="Unknown database table")

    table_name = VISIBLE_TABLES[table_key]

    with engine.connect() as conn:
        result = conn.execute(
            text(f"SELECT * FROM {table_name} ORDER BY id DESC LIMIT :limit"),
            {"limit": limit},
        )
        return [
            {k: normalize(v) for k, v in dict(row._mapping).items()}
            for row in result
        ]


def keyword_set(value):
    if not value:
        return set()
    return {
        x.strip().lower()
        for x in str(value).replace("|", ",").split(",")
        if x.strip()
    }


def calculate_match(requirement: dict, prop: dict):
    score = 0
    reasons = []
    exclusions = []

    req_city = (requirement.get("city") or "").strip().lower()
    prop_city = (prop.get("city") or "").strip().lower()
    req_locations = keyword_set(requirement.get("preferred_locations"))
    prop_location = (prop.get("location") or "").strip().lower()
    req_type = (requirement.get("property_type") or "").strip().lower()
    prop_type = (prop.get("property_type") or "").strip().lower()
    req_txn = (requirement.get("rent_or_sale") or "").strip().lower()
    prop_txn = (prop.get("rent_or_sale") or "").strip().lower()

    if req_city and prop_city == req_city:
        score += 20
        reasons.append("City match")
    elif req_city:
        exclusions.append("City mismatch")

    if not req_locations:
        score += 5
    elif any(loc in prop_location or prop_location in loc for loc in req_locations):
        score += 25
        reasons.append("Preferred location match")
    else:
        exclusions.append("Location mismatch")

    if req_type and prop_type == req_type:
        score += 15
        reasons.append("Property type match")

    if req_txn and prop_txn == req_txn:
        score += 10
        reasons.append("Rent/Sale match")

    req_min = requirement.get("minimum_area_sqft")
    req_max = requirement.get("maximum_area_sqft")
    avail = prop.get("available_area_sqft")

    if avail is not None:
        if req_min is None and req_max is None:
            score += 10
        elif (req_min is None or avail >= req_min) and (req_max is None or avail <= req_max):
            score += 20
            reasons.append("Area within requirement")
        else:
            exclusions.append("Area outside requirement")

    req_cat = keyword_set(requirement.get("suitable_category"))
    prop_cat = keyword_set(prop.get("suitable_category"))
    if req_cat and prop_cat and req_cat.intersection(prop_cat):
        score += 5
        reasons.append("Suitable category overlap")

    req_brands = keyword_set(requirement.get("nearby_brands"))
    prop_brands = keyword_set(prop.get("nearby_brands"))
    if req_brands and prop_brands and req_brands.intersection(prop_brands):
        score += 5
        reasons.append("Nearby brand overlap")

    return min(score, 100), reasons, exclusions


# =========================================================
# HEALTH / STATUS
# =========================================================

@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))

    return {
        "status": "ok",
        "service": "property-intelligence-connected-v1",
        "version": "3.0.0",
        "database": "connected",
        "database_initialized": True,
        "database_view": "/database",
        "workspace": "/workspace",
    }


@app.get("/api/database/status")
def database_status():
    counts = {}
    with engine.connect() as conn:
        for key, table_name in VISIBLE_TABLES.items():
            counts[key] = conn.execute(
                text(f"SELECT COUNT(*) FROM {table_name}")
            ).scalar_one()
    return {"status": "ok", "tables": counts}


# =========================================================
# DATABASE READ API
# =========================================================

@app.get("/api/database/{table_key}")
def database_rows(
    table_key: str,
    limit: int = Query(100, ge=1, le=1000),
    internal: bool = False,
):
    rows = fetch_rows(table_key, limit)

    if table_key == "properties" and not internal:
        rows = [
            {k: v for k, v in row.items() if k not in PRIVATE_PROPERTY_COLUMNS}
            for row in rows
        ]

    return {
        "status": "ok",
        "table": table_key,
        "count": len(rows),
        "rows": rows,
    }


# =========================================================
# CREATE PROPERTY
# =========================================================

@app.post("/api/properties")
def create_property(payload: PropertyCreate):
    property_id = make_id("PROP")
    data = payload.model_dump()

    sql = """
    INSERT INTO pi_properties (
        property_id, property_name, property_type, city, location,
        micro_market, available_area_sqft, minimum_area_sqft,
        maximum_area_sqft, floor, rent_or_sale, asking_rent_per_sqft,
        asking_sale_price, possession, nearby_brands, suitable_category,
        parking, google_maps_pin, source, owner_name, owner_contact,
        broker_name, broker_contact, remarks
    )
    VALUES (
        :property_id, :property_name, :property_type, :city, :location,
        :micro_market, :available_area_sqft, :minimum_area_sqft,
        :maximum_area_sqft, :floor, :rent_or_sale, :asking_rent_per_sqft,
        :asking_sale_price, :possession, :nearby_brands, :suitable_category,
        :parking, :google_maps_pin, :source, :owner_name, :owner_contact,
        :broker_name, :broker_contact, :remarks
    )
    """

    with engine.begin() as conn:
        conn.execute(text(sql), {"property_id": property_id, **data})

    return {"status": "created", "property_id": property_id}


# =========================================================
# CREATE REQUIREMENT
# =========================================================

@app.post("/api/requirements")
def create_requirement(payload: RequirementCreate):
    requirement_id = make_id("REQ")
    data = payload.model_dump()

    sql = """
    INSERT INTO pi_requirements (
        requirement_id, client_name, company_name, contact_phone,
        contact_email, requirement_type, property_type, city,
        preferred_locations, minimum_area_sqft, maximum_area_sqft,
        budget_min, budget_max, rent_or_sale, floor_preference,
        nearby_brands, suitable_category, parking_requirement,
        possession_timeline, additional_points, source
    )
    VALUES (
        :requirement_id, :client_name, :company_name, :contact_phone,
        :contact_email, :requirement_type, :property_type, :city,
        :preferred_locations, :minimum_area_sqft, :maximum_area_sqft,
        :budget_min, :budget_max, :rent_or_sale, :floor_preference,
        :nearby_brands, :suitable_category, :parking_requirement,
        :possession_timeline, :additional_points, :source
    )
    """

    with engine.begin() as conn:
        conn.execute(text(sql), {"requirement_id": requirement_id, **data})

    return {"status": "created", "requirement_id": requirement_id}


# =========================================================
# TEXT / WHATSAPP INGESTION
# =========================================================

@app.post("/api/import/text")
def import_text(payload: TextImport):
    with engine.begin() as conn:
        result = conn.execute(
            text("""
            INSERT INTO pi_sources (
                source_type, source_name, source_reference,
                ingestion_status, processed_records
            )
            VALUES (
                :source_type, :source_name, :text_content,
                'READY_FOR_AI_EXTRACTION', 0
            )
            RETURNING id
            """),
            payload.model_dump(),
        )
        source_id = result.scalar_one()

    return {
        "status": "stored",
        "source_id": source_id,
        "next_action": "AI extraction hook",
    }


# =========================================================
# CSV IMPORT
# =========================================================

@app.post("/api/import/csv")
async def import_csv(file: UploadFile = File(...)):
    raw = await file.read()
    decoded = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(decoded))

    inserted = 0
    errors = []

    with engine.begin() as conn:
        source_result = conn.execute(
            text("""
            INSERT INTO pi_sources (
                source_type, source_name, original_filename,
                ingestion_status, processed_records
            )
            VALUES (
                'CSV', :source_name, :filename, 'PROCESSING', 0
            )
            RETURNING id
            """),
            {
                "source_name": file.filename,
                "filename": file.filename,
            },
        )
        source_id = source_result.scalar_one()

        for index, row in enumerate(reader, start=2):
            try:
                property_id = make_id("PROP")
                payload = {
                    "property_id": property_id,
                    "property_name": row.get("Property name") or row.get("property_name"),
                    "property_type": row.get("Property type") or row.get("property_type") or "NA",
                    "city": row.get("City") or row.get("city") or "NA",
                    "location": row.get("Location") or row.get("location") or "NA",
                    "available_area_sqft": safe_float(
                        row.get("Available area") or row.get("available_area_sqft")
                    ),
                    "minimum_area_sqft": safe_float(
                        row.get("Minimum area") or row.get("minimum_area_sqft")
                    ),
                    "maximum_area_sqft": safe_float(
                        row.get("Maximum area") or row.get("maximum_area_sqft")
                    ),
                    "floor": row.get("Floor") or row.get("floor"),
                    "rent_or_sale": row.get("Rent/Sale") or row.get("rent_or_sale"),
                    "possession": row.get("Possession") or row.get("possession"),
                    "nearby_brands": row.get("Nearby brand") or row.get("nearby_brands"),
                    "suitable_category": row.get("Suitable category") or row.get("suitable_category"),
                    "parking": row.get("Parking") or row.get("parking"),
                    "google_maps_pin": row.get("Google Maps Pin") or row.get("google_maps_pin"),
                    "owner_name": row.get("Owner name") or row.get("owner_name"),
                    "owner_contact": row.get("Owner contact") or row.get("owner_contact"),
                    "broker_name": row.get("Broker name") or row.get("broker_name"),
                    "broker_contact": row.get("Broker contact") or row.get("broker_contact"),
                    "remarks": row.get("Remarks") or row.get("remarks"),
                    "source": f"CSV:{file.filename}",
                }

                conn.execute(
                    text("""
                    INSERT INTO pi_properties (
                        property_id, property_name, property_type, city, location,
                        available_area_sqft, minimum_area_sqft, maximum_area_sqft,
                        floor, rent_or_sale, possession, nearby_brands,
                        suitable_category, parking, google_maps_pin,
                        owner_name, owner_contact, broker_name, broker_contact,
                        remarks, source
                    )
                    VALUES (
                        :property_id, :property_name, :property_type, :city, :location,
                        :available_area_sqft, :minimum_area_sqft, :maximum_area_sqft,
                        :floor, :rent_or_sale, :possession, :nearby_brands,
                        :suitable_category, :parking, :google_maps_pin,
                        :owner_name, :owner_contact, :broker_name, :broker_contact,
                        :remarks, :source
                    )
                    """),
                    payload,
                )

                inserted += 1

            except Exception as exc:
                errors.append({"row": index, "error": str(exc)})

        conn.execute(
            text("""
            UPDATE pi_sources
            SET ingestion_status = :status,
                processed_records = :processed_records,
                error_message = :error_message,
                processed_at = NOW()
            WHERE id = :source_id
            """),
            {
                "status": "PROCESSED" if not errors else "PROCESSED_WITH_ERRORS",
                "processed_records": inserted,
                "error_message": json.dumps(errors[:20]) if errors else None,
                "source_id": source_id,
            },
        )

    return {
        "status": "completed",
        "source_id": source_id,
        "inserted": inserted,
        "errors": errors[:20],
    }


# =========================================================
# MATCHER
# =========================================================

@app.post("/api/match/{requirement_id}")
def run_matcher(requirement_id: str):
    with engine.begin() as conn:
        requirement_row = conn.execute(
            text("SELECT * FROM pi_requirements WHERE requirement_id = :rid"),
            {"rid": requirement_id},
        ).first()

        if not requirement_row:
            raise HTTPException(status_code=404, detail="Requirement not found")

        requirement = {
            k: normalize(v)
            for k, v in dict(requirement_row._mapping).items()
        }

        property_rows = conn.execute(
            text("""
            SELECT *
            FROM pi_properties
            WHERE availability_status = 'Available'
            ORDER BY id DESC
            """)
        ).fetchall()

        conn.execute(
            text("DELETE FROM pi_matches WHERE requirement_id = :rid"),
            {"rid": requirement_id},
        )

        matches = []

        for row in property_rows:
            prop = {
                k: normalize(v)
                for k, v in dict(row._mapping).items()
            }

            score, reasons, exclusions = calculate_match(requirement, prop)

            matches.append({
                "property_id": prop["property_id"],
                "score": score,
                "reasons": reasons,
                "exclusions": exclusions,
            })

        matches.sort(key=lambda x: x["score"], reverse=True)

        for rank, item in enumerate(matches, start=1):
            conn.execute(
                text("""
                INSERT INTO pi_matches (
                    requirement_id, property_id, match_score,
                    rank, match_reasons, exclusions, status
                )
                VALUES (
                    :requirement_id, :property_id, :match_score,
                    :rank, CAST(:match_reasons AS JSONB),
                    CAST(:exclusions AS JSONB), 'READY_FOR_REVIEW'
                )
                """),
                {
                    "requirement_id": requirement_id,
                    "property_id": item["property_id"],
                    "match_score": item["score"],
                    "rank": rank,
                    "match_reasons": json.dumps(item["reasons"]),
                    "exclusions": json.dumps(item["exclusions"]),
                },
            )

    return {
        "status": "READY_FOR_REVIEW",
        "requirement_id": requirement_id,
        "total_properties": len(matches),
        "matches": matches[:20],
    }


# =========================================================
# WORKSPACE UI
# =========================================================

@app.get("/workspace", response_class=HTMLResponse)
def workspace():
    return HTMLResponse("""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Property Intelligence Workspace</title>
<style>
body{font-family:Arial;margin:0;background:#f5f7fb;color:#172033}
header{background:#111827;color:#fff;padding:22px 28px}
.wrap{padding:22px;display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:18px}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:18px}
input,textarea,select{width:100%;padding:10px;margin:6px 0 10px;border:1px solid #d1d5db;border-radius:8px}
button{background:#111827;color:#fff;border:0;border-radius:8px;padding:11px 15px;cursor:pointer}
pre{white-space:pre-wrap;background:#f8fafc;padding:12px;border-radius:8px;max-height:240px;overflow:auto}
a{color:#2563eb}
</style>
</head>
<body>
<header>
<h1>Property Intelligence Connected V1</h1>
<p>Enter inventory, requirements, imports and run matching from one place.</p>
</header>
<div class="wrap">

<div class="card">
<h3>Add Property</h3>
<input id="pname" placeholder="Property name">
<input id="ptype" placeholder="Property type" value="Retail">
<input id="pcity" placeholder="City">
<input id="plocation" placeholder="Location">
<input id="parea" type="number" placeholder="Available area sqft">
<input id="pfloor" placeholder="Floor">
<select id="ptxn"><option>Rent</option><option>Sale</option></select>
<input id="pcat" placeholder="Suitable category">
<input id="pbrands" placeholder="Nearby brands">
<button onclick="addProperty()">Save Property</button>
<pre id="pout"></pre>
</div>

<div class="card">
<h3>Add Requirement</h3>
<input id="rclient" placeholder="Client / Retailer">
<input id="rcompany" placeholder="Company">
<input id="rcity" placeholder="City">
<input id="rlocations" placeholder="Preferred locations, comma separated">
<input id="rmin" type="number" placeholder="Minimum area sqft">
<input id="rmax" type="number" placeholder="Maximum area sqft">
<select id="rtxn"><option>Rent</option><option>Sale</option></select>
<input id="rcat" placeholder="Suitable category">
<input id="rbrands" placeholder="Nearby brands">
<button onclick="addRequirement()">Save Requirement</button>
<pre id="rout"></pre>
</div>

<div class="card">
<h3>Paste WhatsApp / Source Text</h3>
<input id="sname" placeholder="Source name / WhatsApp group">
<textarea id="stext" rows="8" placeholder="Paste source text here..."></textarea>
<button onclick="saveText()">Store Source</button>
<pre id="sout"></pre>
</div>

<div class="card">
<h3>Import Property CSV</h3>
<input id="csvfile" type="file" accept=".csv">
<button onclick="uploadCsv()">Upload CSV</button>
<pre id="csvout"></pre>
</div>

<div class="card">
<h3>Run Matcher</h3>
<input id="rid" placeholder="Requirement ID e.g. REQ-...">
<button onclick="runMatch()">Run Matcher</button>
<pre id="mout"></pre>
</div>

<div class="card">
<h3>Quick Links</h3>
<p><a href="/database">Open Organized Database</a></p>
<p><a href="/api/database/status">Database Status</a></p>
<p><a href="/health">Health Check</a></p>
</div>

</div>
<script>
const val=id=>document.getElementById(id).value;
const show=(id,data)=>document.getElementById(id).textContent=JSON.stringify(data,null,2);

async function addProperty(){
 const body={
   property_name:val("pname"), property_type:val("ptype"),
   city:val("pcity"), location:val("plocation"),
   available_area_sqft:Number(val("parea"))||null,
   floor:val("pfloor"), rent_or_sale:val("ptxn"),
   suitable_category:val("pcat"), nearby_brands:val("pbrands")
 };
 const r=await fetch("/api/properties",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
 show("pout",await r.json());
}

async function addRequirement(){
 const body={
   client_name:val("rclient"), company_name:val("rcompany"),
   city:val("rcity"), preferred_locations:val("rlocations"),
   minimum_area_sqft:Number(val("rmin"))||null,
   maximum_area_sqft:Number(val("rmax"))||null,
   rent_or_sale:val("rtxn"), suitable_category:val("rcat"),
   nearby_brands:val("rbrands")
 };
 const r=await fetch("/api/requirements",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
 const data=await r.json(); show("rout",data); if(data.requirement_id)document.getElementById("rid").value=data.requirement_id;
}

async function saveText(){
 const body={source_type:"WHATSAPP",source_name:val("sname"),text_content:val("stext")};
 const r=await fetch("/api/import/text",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
 show("sout",await r.json());
}

async function uploadCsv(){
 const f=document.getElementById("csvfile").files[0];
 if(!f){show("csvout",{error:"Choose a CSV first"});return;}
 const fd=new FormData(); fd.append("file",f);
 const r=await fetch("/api/import/csv",{method:"POST",body:fd});
 show("csvout",await r.json());
}

async function runMatch(){
 const r=await fetch("/api/match/"+encodeURIComponent(val("rid")),{method:"POST"});
 show("mout",await r.json());
}
</script>
</body>
</html>
""")


# =========================================================
# DATABASE UI
# =========================================================

@app.get("/database", response_class=HTMLResponse)
def database_view():
    buttons = []
    for key in VISIBLE_TABLES:
        buttons.append(
            "<button id=\"tab-{0}\" onclick=\"loadTable('{0}')\">{1}</button>".format(
                key, key.title()
            )
        )
    tabs_html = "".join(buttons)

    html = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Property Intelligence Database</title>
<style>
body{font-family:Arial;margin:0;background:#f4f6f8;color:#172033}
header{background:#111827;color:white;padding:24px 30px}
.wrap{padding:24px}
.tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
button{padding:10px 14px;border:1px solid #d1d5db;background:white;border-radius:8px;cursor:pointer}
button.active{background:#111827;color:#fff}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:12px;overflow:auto}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:10px;border-bottom:1px solid #eee;text-align:left;white-space:nowrap}
th{position:sticky;top:0;background:#f9fafb}
.meta{margin:12px 0;color:#6b7280}
</style>
</head>
<body>
<header><h1>Property Intelligence Database</h1><p>Organized PostgreSQL backend</p></header>
<div class="wrap">
<div class="tabs">__TABS__</div>
<div class="meta" id="meta">Loading...</div>
<div class="card"><table id="grid"></table></div>
</div>
<script>
function esc(v){
 if(v===null||v===undefined)return "";
 return String(v).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");
}
async function loadTable(name){
 document.querySelectorAll("button").forEach(b=>b.classList.remove("active"));
 const b=document.getElementById("tab-"+name); if(b)b.classList.add("active");
 const res=await fetch("/api/database/"+name+"?limit=500");
 const data=await res.json();
 const meta=document.getElementById("meta");
 const grid=document.getElementById("grid");
 if(!res.ok){meta.innerText="Error";grid.innerHTML="<tr><td>"+esc(data.message||data.detail)+"</td></tr>";return;}
 meta.innerText=(data.count||0)+" records in "+name;
 if(!data.rows.length){grid.innerHTML="<tr><td style='padding:20px'>No records yet.</td></tr>";return;}
 const cols=Object.keys(data.rows[0]);
 let h="<thead><tr>"+cols.map(c=>"<th>"+esc(c)+"</th>").join("")+"</tr></thead><tbody>";
 h+=data.rows.map(r=>"<tr>"+cols.map(c=>"<td>"+esc(r[c])+"</td>").join("")+"</tr>").join("");
 h+="</tbody>";grid.innerHTML=h;
}
loadTable("properties");
</script>
</body>
</html>
""".replace("__TABS__", tabs_html)

    return HTMLResponse(content=html)


@app.get("/")
def root():
    return {
        "service": "Property Intelligence Connected V1",
        "version": "3.0.0",
        "workspace": "/workspace",
        "database": "/database",
        "health": "/health",
    }
