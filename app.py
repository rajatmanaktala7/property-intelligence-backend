import os
from decimal import Decimal
from datetime import date, datetime

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import create_engine, text


# =========================================================
# DATABASE CONFIGURATION
# =========================================================

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./property_intelligence.db"
)

# Railway PostgreSQL compatibility
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgres://",
        "postgresql+psycopg://",
        1
    )
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgresql://",
        "postgresql+psycopg://",
        1
    )

connect_args = (
    {"check_same_thread": False}
    if DATABASE_URL.startswith("sqlite")
    else {}
)

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True
)


# =========================================================
# FASTAPI APPLICATION
# =========================================================

app = FastAPI(
    title="Property Intelligence Agent",
    version="2.0.0"
)


# =========================================================
# TABLE CONFIGURATION
# =========================================================

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


# =========================================================
# UTILITIES
# =========================================================

def normalize(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, Decimal):
        return float(value)

    return value


def fetch_rows(table_key: str, limit: int = 100):
    if table_key not in VISIBLE_TABLES:
        raise HTTPException(
            status_code=404,
            detail="Unknown database table"
        )

    table_name = VISIBLE_TABLES[table_key]

    with engine.connect() as conn:
        result = conn.execute(
            text(
                f"""
                SELECT *
                FROM {table_name}
                ORDER BY id DESC
                LIMIT :limit
                """
            ),
            {"limit": limit},
        )

        rows = []

        for row in result:
            row_dict = dict(row._mapping)

            normalized_row = {
                key: normalize(value)
                for key, value in row_dict.items()
            }

            rows.append(normalized_row)

        return rows


# =========================================================
# HEALTH CHECK
# =========================================================

@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))

    return {
        "status": "ok",
        "service": "property-intelligence-agent",
        "version": "2.0.0",
        "database": "connected",
        "database_view": "/database"
    }


# =========================================================
# ROOT
# =========================================================

@app.get("/")
def root():
    return {
        "service": "Property Intelligence Agent",
        "version": "2.0.0",
        "status": "online",
        "health": "/health",
        "database": "/database",
        "api": {
            "properties": "/api/database/properties",
            "requirements": "/api/database/requirements",
            "contacts": "/api/database/contacts",
            "sources": "/api/database/sources",
            "media": "/api/database/media",
            "matches": "/api/database/matches",
            "verification": "/api/database/verification"
        }
    }


# =========================================================
# DATABASE API
# =========================================================

@app.get("/api/database/{table_key}")
def database_rows(
    table_key: str,
    limit: int = Query(
        100,
        ge=1,
        le=1000
    ),
    internal: bool = False,
):
    rows = fetch_rows(
        table_key,
        limit
    )

    # Hide private fields from normal Property API
    if table_key == "properties" and not internal:
        cleaned_rows = []

        for row in rows:
            cleaned_row = {
                key: value
                for key, value in row.items()
                if key not in PRIVATE_PROPERTY_COLUMNS
            }

            cleaned_rows.append(cleaned_row)

        rows = cleaned_rows

    return {
        "table": table_key,
        "count": len(rows),
        "rows": rows
    }


# =========================================================
# ORGANIZED DATABASE DASHBOARD
# =========================================================

@app.get(
    "/database",
    response_class=HTMLResponse
)
def database_view():

    tab_buttons = []

    for key in VISIBLE_TABLES:
        button = (
            '<button '
            'id="tab-{key}" '
            'onclick="loadTable(\'{key}\')">'
            '{label}'
            '</button>'
        ).format(
            key=key,
            label=key.title()
        )

        tab_buttons.append(button)

    tabs_html = "".join(tab_buttons)

    html = """
<!DOCTYPE html>

<html lang="en">

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1"
/>

<title>
Property Intelligence Database
</title>

<style>

* {
    box-sizing: border-box;
}

body {
    font-family:
        Arial,
        Helvetica,
        sans-serif;

    margin: 0;

    background:
        #f4f6f8;

    color:
        #172033;
}

header {
    background:
        #111827;

    color:
        white;

    padding:
        24px 30px;
}

header h1 {
    margin: 0;

    font-size:
        24px;
}

header p {
    margin:
        7px 0 0;

    color:
        #9ca3af;

    font-size:
        14px;
}

.container {
    padding:
        24px;
}

.stats {
    display:
        grid;

    grid-template-columns:
        repeat(
            auto-fit,
            minmax(
                180px,
                1fr
            )
        );

    gap:
        14px;

    margin-bottom:
        20px;
}

.stat-card {
    background:
        white;

    border:
        1px solid #e5e7eb;

    border-radius:
        12px;

    padding:
        16px;
}

.stat-label {
    font-size:
        12px;

    color:
        #6b7280;

    text-transform:
        uppercase;

    font-weight:
        bold;
}

.stat-value {
    font-size:
        24px;

    font-weight:
        bold;

    margin-top:
        6px;
}

.tabs {
    display:
        flex;

    gap:
        8px;

    flex-wrap:
        wrap;

    margin-bottom:
        16px;
}

button {
    border:
        1px solid #d1d5db;

    background:
        white;

    padding:
        10px 14px;

    border-radius:
        8px;

    cursor:
        pointer;

    font-weight:
        600;
}

button:hover {
    background:
        #f3f4f6;
}

button.active {
    background:
        #111827;

    color:
        white;

    border-color:
        #111827;
}

.toolbar {
    display:
        flex;

    justify-content:
        space-between;

    align-items:
        center;

    flex-wrap:
        wrap;

    gap:
        12px;

    margin-bottom:
        12px;
}

.meta {
    color:
        #6b7280;

    font-size:
        14px;
}

input {
    padding:
        9px 12px;

    border:
        1px solid #d1d5db;

    border-radius:
        8px;

    min-width:
        260px;
}

.table-card {
    background:
        white;

    border:
        1px solid #e5e7eb;

    border-radius:
        12px;

    overflow:
        auto;

    max-height:
        70vh;
}

table {
    border-collapse:
        collapse;

    width:
        100%;

    font-size:
        13px;
}

thead {
    background:
        #f9fafb;
}

th {
    text-align:
        left;

    padding:
        11px;

    border-bottom:
        1px solid #e5e7eb;

    position:
        sticky;

    top:
        0;

    background:
        #f9fafb;

    white-space:
        nowrap;

    z-index:
        1;
}

td {
    padding:
        10px;

    border-bottom:
        1px solid #f0f1f3;

    max-width:
        280px;

    overflow:
        hidden;

    text-overflow:
        ellipsis;

    white-space:
        nowrap;
}

tbody tr:hover {
    background:
        #f9fafb;
}

.error-box {
    padding:
        20px;

    color:
        #b91c1c;

    background:
        #fef2f2;

    border:
        1px solid #fecaca;
}

.empty-box {
    padding:
        24px;

    color:
        #6b7280;
}

</style>

</head>

<body>

<header>

<h1>
Property Intelligence Database
</h1>

<p>
Central backend database for property inventory,
requirements, sources, contacts, media,
AI matches and verification history.
</p>

</header>


<div class="container">


<div class="stats">

<div class="stat-card">

<div class="stat-label">
Current Table
</div>

<div
    class="stat-value"
    id="currentTable"
>
Properties
</div>

</div>


<div class="stat-card">

<div class="stat-label">
Records Loaded
</div>

<div
    class="stat-value"
    id="recordCount"
>
0
</div>

</div>


<div class="stat-card">

<div class="stat-label">
Backend Status
</div>

<div
    class="stat-value"
    style="color:#15803d"
>
Online
</div>

</div>


</div>


<div class="tabs">

__TABS__

</div>


<div class="toolbar">

<div
    class="meta"
    id="meta"
>
Loading database...
</div>


<input
    type="text"
    id="searchBox"
    placeholder="Search loaded records..."
    oninput="filterRows()"
/>

</div>


<div class="table-card">

<table id="grid"></table>

</div>


</div>


<script>

let currentRows = [];

let currentColumns = [];


function escapeHtml(value) {

    if (
        value === null ||
        value === undefined
    ) {
        return "";
    }

    return String(value)

        .replaceAll(
            "&",
            "&amp;"
        )

        .replaceAll(
            "<",
            "&lt;"
        )

        .replaceAll(
            ">",
            "&gt;"
        )

        .replaceAll(
            '"',
            "&quot;"
        )

        .replaceAll(
            "'",
            "&#039;"
        );
}


function renderTable(rows) {

    const grid =
        document.getElementById(
            "grid"
        );

    if (
        !rows ||
        rows.length === 0
    ) {

        grid.innerHTML =
            "<tr>" +
            "<td class='empty-box'>" +
            "No records found." +
            "</td>" +
            "</tr>";

        return;
    }


    let html =
        "<thead><tr>";


    currentColumns.forEach(
        function(column) {

            html +=
                "<th>" +
                escapeHtml(column) +
                "</th>";
        }
    );


    html +=
        "</tr></thead><tbody>";


    rows.forEach(
        function(row) {

            html +=
                "<tr>";


            currentColumns.forEach(
                function(column) {

                    const value =
                        row[column];


                    html +=

                        "<td title='" +

                        escapeHtml(
                            value
                        ) +

                        "'>" +

                        escapeHtml(
                            value
                        ) +

                        "</td>";
                }
            );


            html +=
                "</tr>";
        }
    );


    html +=
        "</tbody>";


    grid.innerHTML =
        html;
}


async function loadTable(name) {

    document

        .querySelectorAll(
            ".tabs button"
        )

        .forEach(
            function(button) {

                button
                    .classList
                    .remove(
                        "active"
                    );
            }
        );


    const activeButton =
        document.getElementById(
            "tab-" + name
        );


    if (activeButton) {

        activeButton
            .classList
            .add(
                "active"
            );
    }


    document.getElementById(
        "currentTable"
    ).innerText =
        name.charAt(0).toUpperCase() +
        name.slice(1);


    document.getElementById(
        "meta"
    ).innerText =
        "Loading " +
        name +
        "...";


    try {

        const response =
            await fetch(
                "/api/database/" +
                name +
                "?limit=500"
            );


        const data =
            await response.json();


        if (!response.ok) {

            throw new Error(
                data.detail ||
                data.error ||
                "Unable to load database"
            );
        }


        currentRows =
            data.rows || [];


        document.getElementById(
            "recordCount"
        ).innerText =
            data.count || 0;


        document.getElementById(
            "meta"
        ).innerText =
            (data.count || 0) +
            " records in " +
            name;


        if (
            currentRows.length === 0
        ) {

            currentColumns = [];

            renderTable([]);

            return;
        }


        currentColumns =
            Object.keys(
                currentRows[0]
            );


        document.getElementById(
            "searchBox"
        ).value =
            "";


        renderTable(
            currentRows
        );


    } catch (error) {

        const grid =
            document.getElementById(
                "grid"
            );


        document.getElementById(
            "meta"
        ).innerText =
            "Database error";


        grid.innerHTML =

            "<tr>" +

            "<td class='error-box'>" +

            escapeHtml(
                error.message
            ) +

            "</td>" +

            "</tr>";
    }
}


function filterRows() {

    const term =
        document
            .getElementById(
                "searchBox"
            )
            .value
            .toLowerCase();


    if (!term) {

        renderTable(
            currentRows
        );

        return;
    }


    const filtered =
        currentRows.filter(
            function(row) {

                return Object
                    .values(row)
                    .some(
                        function(value) {

                            return String(
                                value ?? ""
                            )
                            .toLowerCase()
                            .includes(
                                term
                            );
                        }
                    );
            }
        );


    renderTable(
        filtered
    );
}


loadTable(
    "properties"
);

</script>


</body>

</html>
"""

    html = html.replace(
        "__TABS__",
        tabs_html
    )

    return HTMLResponse(
        content=html
    )
