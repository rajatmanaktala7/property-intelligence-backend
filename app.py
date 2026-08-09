import os
from decimal import Decimal
from datetime import date, datetime

from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import create_engine, text

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
    title="Property Intelligence Agent",
    version="2.1.0",
)

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
    "address",
    "owner_name",
    "owner_contact",
    "broker_name",
    "broker_contact",
    "verified_date",
    "verified_by",
    "remarks",
    "source",
}

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

def initialize_database():
    statements = [part.strip() for part in SCHEMA_SQL.split(";") if part.strip()]
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))

@app.on_event("startup")
def startup_event():
    initialize_database()

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "message": str(exc),
            "path": request.url.path,
        },
    )

def normalize(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value

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
            {
                key: normalize(value)
                for key, value in dict(row._mapping).items()
            }
            for row in result
        ]

@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))

    return {
        "status": "ok",
        "service": "property-intelligence-agent",
        "version": "2.1.0",
        "database": "connected",
        "database_initialized": True,
        "database_view": "/database",
    }

@app.get("/api/database/status")
def database_status():
    counts = {}

    with engine.connect() as conn:
        for key, table_name in VISIBLE_TABLES.items():
            counts[key] = conn.execute(
                text(f"SELECT COUNT(*) FROM {table_name}")
            ).scalar_one()

    return {
        "status": "ok",
        "tables": counts,
    }

@app.get("/api/database/{table_key}")
def database_rows(
    table_key: str,
    limit: int = Query(100, ge=1, le=1000),
    internal: bool = False,
):
    rows = fetch_rows(table_key, limit)

    if table_key == "properties" and not internal:
        rows = [
            {
                key: value
                for key, value in row.items()
                if key not in PRIVATE_PROPERTY_COLUMNS
            }
            for row in rows
        ]

    return {
        "status": "ok",
        "table": table_key,
        "count": len(rows),
        "rows": rows,
    }

@app.get("/database", response_class=HTMLResponse)
def database_view():
    tab_buttons = []

    for key in VISIBLE_TABLES:
        button = (
            "<button id=\"tab-{key}\" onclick=\"loadTable('{key}')\">"
            "{label}</button>"
        ).format(
            key=key,
            label=key.title(),
        )
        tab_buttons.append(button)

    tabs_html = "".join(tab_buttons)

    html = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Property Intelligence Database</title>

<style>
body {
    font-family: Arial, sans-serif;
    margin: 0;
    background: #f4f6f8;
    color: #172033;
}

header {
    background: #111827;
    color: white;
    padding: 24px 30px;
}

.container {
    padding: 24px;
}

.tabs {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin: 16px 0;
}

button {
    padding: 10px 14px;
    border: 1px solid #d1d5db;
    background: white;
    border-radius: 8px;
    cursor: pointer;
}

button.active {
    background: #111827;
    color: white;
}

.meta {
    margin: 12px 0;
    color: #6b7280;
}

.card {
    background: white;
    border: 1px solid #e5e7eb;
    border-radius: 12px;
    overflow: auto;
}

table {
    border-collapse: collapse;
    width: 100%;
    font-size: 13px;
}

th,
td {
    padding: 10px;
    border-bottom: 1px solid #eeeeee;
    text-align: left;
    white-space: nowrap;
}

th {
    background: #f9fafb;
    position: sticky;
    top: 0;
}

.error {
    padding: 20px;
    color: #b91c1c;
    background: #fef2f2;
}
</style>
</head>

<body>

<header>
<h1>Property Intelligence Database</h1>
<p>
Central backend database for property inventory, requirements,
sources, contacts, media, AI matches and verification history.
</p>
</header>

<div class="container">

<div class="tabs">
__TABS__
</div>

<div class="meta" id="meta">
Loading...
</div>

<div class="card">
<table id="grid"></table>
</div>

</div>

<script>
let currentRows = [];
let currentColumns = [];

function escapeHtml(value) {
    if (value === null || value === undefined) {
        return "";
    }

    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
}

function renderTable(rows) {
    const grid = document.getElementById("grid");

    if (!rows || rows.length === 0) {
        grid.innerHTML =
            "<tr><td style='padding:20px'>" +
            "No records yet. Database tables are ready." +
            "</td></tr>";
        return;
    }

    currentColumns = Object.keys(rows[0]);

    let html = "<thead><tr>";

    currentColumns.forEach(function(column) {
        html += "<th>" + escapeHtml(column) + "</th>";
    });

    html += "</tr></thead><tbody>";

    rows.forEach(function(row) {
        html += "<tr>";

        currentColumns.forEach(function(column) {
            html +=
                "<td>" +
                escapeHtml(row[column]) +
                "</td>";
        });

        html += "</tr>";
    });

    html += "</tbody>";

    grid.innerHTML = html;
}

async function loadTable(name) {
    document.querySelectorAll("button").forEach(function(button) {
        button.classList.remove("active");
    });

    const activeButton = document.getElementById("tab-" + name);

    if (activeButton) {
        activeButton.classList.add("active");
    }

    const meta = document.getElementById("meta");
    const grid = document.getElementById("grid");

    meta.innerText = "Loading " + name + "...";

    try {
        const response = await fetch(
            "/api/database/" + name + "?limit=500"
        );

        const raw = await response.text();

        let data;

        try {
            data = JSON.parse(raw);
        } catch (parseError) {
            throw new Error(
                "Backend returned non-JSON response: " +
                raw.substring(0, 150)
            );
        }

        if (!response.ok || data.status === "error") {
            throw new Error(
                data.message ||
                data.detail ||
                data.error ||
                "Unable to load database"
            );
        }

        currentRows = data.rows || [];

        meta.innerText =
            (data.count || 0) +
            " records in " +
            name;

        renderTable(currentRows);

    } catch (error) {
        meta.innerText = "Database error";

        grid.innerHTML =
            "<tr><td class='error'>" +
            escapeHtml(error.message) +
            "</td></tr>";
    }
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
        "service": "Property Intelligence Agent",
        "version": "2.1.0",
        "status": "online",
        "database": "/database",
        "health": "/health",
        "database_status": "/api/database/status",
    }
