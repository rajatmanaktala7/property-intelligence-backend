import os
from decimal import Decimal
from datetime import date, datetime

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse
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
    title="Property Intelligence Backend",
    version="1.0.1",
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


def normalize(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def fetch_rows(table_key: str, limit: int = 100):
    if table_key not in VISIBLE_TABLES:
        raise HTTPException(status_code=404, detail="Unknown table")

    table_name = VISIBLE_TABLES[table_key]

    with engine.connect() as conn:
        result = conn.execute(
            text(f"SELECT * FROM {table_name} ORDER BY id DESC LIMIT :limit"),
            {"limit": limit},
        )
        return [
            {key: normalize(value) for key, value in dict(row._mapping).items()}
            for row in result
        ]


@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))

    return {
        "status": "ok",
        "service": "property-intelligence-backend",
        "version": "1.0.1",
        "database": "connected",
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
        "table": table_key,
        "count": len(rows),
        "rows": rows,
    }


@app.get("/database", response_class=HTMLResponse)
def database_view():
    tab_buttons = []

    for key in VISIBLE_TABLES:
        button = (
            '<button id="tab-{key}" onclick="loadTable(\'{key}\')">'
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
    padding: 20px 28px;
}
header h1 {
    margin: 0;
    font-size: 22px;
}
header p {
    margin: 6px 0 0;
    color: #9ca3af;
}
.wrap {
    padding: 22px;
}
.tabs {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 16px;
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
th {
    background: #f9fafb;
    position: sticky;
    top: 0;
    text-align: left;
    padding: 10px;
    border-bottom: 1px solid #e5e7eb;
    white-space: nowrap;
}
td {
    padding: 10px;
    border-bottom: 1px solid #f0f1f3;
    max-width: 280px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.meta {
    margin: 0 0 12px;
    color: #6b7280;
}
</style>
</head>
<body>
<header>
    <h1>Property Intelligence Database</h1>
    <p>Organized backend view for properties, requirements, sources and AI matches</p>
</header>

<div class="wrap">
    <div class="tabs">
        __TABS__
    </div>

    <p class="meta" id="meta">Loading...</p>

    <div class="card">
        <table id="grid"></table>
    </div>
</div>

<script>
function escapeHtml(value) {
    if (value === null || value === undefined) return "";
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

async function loadTable(name) {
    document.querySelectorAll("button").forEach(function(button) {
        button.classList.remove("active");
    });

    const activeButton = document.getElementById("tab-" + name);
    if (activeButton) activeButton.classList.add("active");

    const response = await fetch("/api/database/" + name + "?limit=200");
    const data = await response.json();

    const meta = document.getElementById("meta");
    const grid = document.getElementById("grid");

    if (!response.ok) {
        meta.innerText = "Unable to load " + name;
        grid.innerHTML = "<tr><td style='padding:20px'>" +
            escapeHtml(data.detail || data.error || "Unknown error") +
            "</td></tr>";
        return;
    }

    meta.innerText = (data.count || 0) + " records in " + name;

    if (!data.rows || data.rows.length === 0) {
        grid.innerHTML =
            "<tr><td style='padding:20px'>No records yet.</td></tr>";
        return;
    }

    const columns = Object.keys(data.rows[0]);

    let html = "<thead><tr>";
    columns.forEach(function(column) {
        html += "<th>" + escapeHtml(column) + "</th>";
    });
    html += "</tr></thead><tbody>";

    data.rows.forEach(function(row) {
        html += "<tr>";
        columns.forEach(function(column) {
            const value = row[column];
            html += "<td title='" + escapeHtml(value) + "'>" +
                escapeHtml(value) +
                "</td>";
        });
        html += "</tr>";
    });

    html += "</tbody>";
    grid.innerHTML = html;
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
        "service": "Property Intelligence Backend",
        "version": "1.0.1",
        "database_view": "/database",
        "health": "/health",
        "tables": list(VISIBLE_TABLES.keys()),
    }
