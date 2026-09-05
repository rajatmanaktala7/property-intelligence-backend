from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

import alliance_final_5x5_databases_v910 as v910

VERSION = "10.0.0-FINAL-TEAM-OS"
SOURCES = ("MASTER", "NEWSPAPER", "WHATSAPP", "MAGAZINE", "MANUAL")
CATEGORY_OPTIONS = (
    "Residential Sale", "Residential Rent", "Commercial Sale", "Commercial Rent",
    "Industrial Sale", "Industrial Rent", "Farmhouse Sale", "Farmhouse Rent",
)
CORE_TABLES = (
    "pi_master_properties_v711",
    "pi_master_requirements_v711",
    "pi_master_source_links_v711",
    "pi_master_workflow_v720",
    "pi_master_action_state_v730",
    "pi_master_matches_v720",
    "ai_source_history",
    "pi_newspaper_sources",
)

def _app(core):
    return getattr(core, "app", None) or core

def _engine(core):
    return getattr(core, "engine", None)

def _login(core, req):
    fn = getattr(core, "need_login", None)
    return fn(req) if fn else "team"

def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))

def _dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            out = json.loads(value)
            return out if isinstance(out, dict) else {}
        except Exception:
            return {}
    return {}

def _first(data: dict, *keys: str):
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None

def _fmt_dt(value: Any) -> str:
    if not value:
        return ""
    try:
        return value.strftime("%d-%m-%Y %I:%M %p")
    except Exception:
        return str(value)

def _scalar(engine, sql: str, params: dict | None = None, default=0):
    try:
        with engine.connect() as conn:
            value = conn.execute(text(sql), params or {}).scalar()
            return default if value is None else value
    except Exception:
        return default

def _remove_get(app, path: str):
    app.router.routes[:] = [
        route for route in list(app.router.routes)
        if not (
            getattr(route, "path", None) == path
            and "GET" in set(getattr(route, "methods", set()) or set())
        )
    ]

def _move_front(app, path: str):
    found = [
        route for route in list(app.router.routes)
        if getattr(route, "path", None) == path
    ]
    for route in found:
        try:
            app.router.routes.remove(route)
        except ValueError:
            pass
    for route in reversed(found):
        app.router.routes.insert(0, route)

def _route_exists(app, path: str) -> bool:
    return any(getattr(r, "path", None) == path for r in app.router.routes)

def _source_count(engine, entity: str, source: str) -> int:
    table = "pi_master_properties_v711" if entity == "PROPERTY" else "pi_master_requirements_v711"
    alias = "p" if entity == "PROPERTY" else "r"
    if source == "MASTER":
        return int(_scalar(engine, f"SELECT COUNT(*) FROM {table}", default=0) or 0)

    pat = f"%{source}%"
    sql = f"""
    SELECT COUNT(DISTINCT {alias}.canonical_id)
    FROM {table} {alias}
    WHERE EXISTS (
      SELECT 1
      FROM pi_master_source_links_v711 l
      WHERE l.canonical_id={alias}.canonical_id
        AND l.master_entity_type=:entity
        AND (
          UPPER(COALESCE(l.source_type,'')) LIKE :pat OR
          UPPER(COALESCE(l.source_table,'')) LIKE :pat
        )
    )
    OR UPPER(COALESCE({alias}.clean_record->>'source','')) LIKE :pat
    OR UPPER(COALESCE({alias}.clean_record->>'source_type','')) LIKE :pat
    OR UPPER(COALESCE({alias}.clean_record->>'source_name','')) LIKE :pat
    OR UPPER(COALESCE({alias}.clean_record->>'channel','')) LIKE :pat
    OR UPPER(COALESCE({alias}.clean_record->>'import_source','')) LIKE :pat
    """
    return int(_scalar(engine, sql, {"entity": entity, "pat": pat}, 0) or 0)

def _source_label(engine, canonical_id: str, entity: str) -> str:
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT source_type, source_table
                FROM pi_master_source_links_v711
                WHERE canonical_id=:cid AND master_entity_type=:entity
                ORDER BY created_at DESC NULLS LAST, id DESC
                LIMIT 4
            """), {"cid": canonical_id, "entity": entity}).mappings().all()
        vals = []
        for row in rows:
            for key in ("source_type", "source_table"):
                val = str(row.get(key) or "").strip()
                if val and val not in vals:
                    vals.append(val)
        return " · ".join(vals[:3])
    except Exception:
        return ""

def _storage_guard(engine) -> dict:
    """
    Non-destructive future-growth protection.
    Live source rows and existing history rows are untouched.
    """
    try:
        with engine.begin() as conn:
            exists = conn.execute(text("SELECT to_regclass('public.ai_source_history')")).scalar()
            if not exists:
                return {"status": "SKIPPED", "reason": "ai_source_history missing"}

            conn.execute(text("""
                CREATE OR REPLACE FUNCTION alliance_sanitize_history_blob_v1000()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                  IF NEW.old_record IS NOT NULL THEN
                    NEW.old_record = NEW.old_record
                      - 'image_content'
                      - 'file_content'
                      - 'pdf_content'
                      - 'binary_content'
                      - 'raw_pdf'
                      - 'raw_image'
                      - 'media_content';
                  END IF;
                  RETURN NEW;
                END;
                $$;
            """))
            conn.execute(text(
                "DROP TRIGGER IF EXISTS tr_ai_source_history_sanitize_v1000 ON ai_source_history"
            ))
            conn.execute(text("""
                CREATE TRIGGER tr_ai_source_history_sanitize_v1000
                BEFORE INSERT OR UPDATE ON ai_source_history
                FOR EACH ROW
                EXECUTE FUNCTION alliance_sanitize_history_blob_v1000()
            """))
        return {"status": "ACTIVE", "trigger": "tr_ai_source_history_sanitize_v1000"}
    except Exception as exc:
        return {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}

def _nav() -> str:
    links = (
        ("Command Centre", "/alliance/primary"),
        ("Add Property", "/property-manual"),
        ("Property Databases", "/alliance/final/databases"),
        ("Verification", "/alliance/primary/availability"),
        ("Add Requirement", "/requirements-workbench"),
        ("Requirement Databases", "/alliance/final/requirements"),
        ("Smart Matcher", "/alliance/primary/matcher"),
        ("Follow-ups", "/alliance/primary/followups"),
        ("Deals & Reports", "/alliance/primary/reports"),
        ("Contacts", "/alliance/primary/contacts"),
        ("AI Control", "/alliance/primary/ai-control"),
        ("Data Health", "/alliance/primary/data-health"),
    )
    return "".join(f'<a href="{p}">{_e(label)}</a>' for label, p in links)

def _shell(title: str, body: str, subtitle: str = "PROPERTY → VERIFY → REQUIREMENT → MATCH → CLIENT → FOLLOW-UP → DEAL") -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)} · Alliance CRE</title>
<style>
*{{box-sizing:border-box}}html,body{{margin:0;background:#f4f7fb;color:#172033;font-family:Arial,sans-serif;font-size:12px}}
header{{background:#102a43;color:white;padding:14px 18px}}header b{{font-size:18px}}header small{{opacity:.88}}
nav{{position:sticky;top:0;z-index:40;background:white;border-bottom:1px solid #98a2b3;padding:6px;display:flex;gap:5px;flex-wrap:wrap}}
nav a,.btn,button{{display:inline-block;background:#102a43;color:white;text-decoration:none;border:1px solid #102a43;padding:6px 8px;border-radius:2px;font-size:11px;cursor:pointer}}
.btn.good,.good{{background:#067647;border-color:#067647}}.btn.light,.light{{background:#475467;border-color:#475467}}.danger{{background:#b42318!important;border-color:#b42318!important}}
.wrap{{width:100%;max-width:2200px;margin:auto;padding:10px}}h2{{font-size:19px;margin:4px 0 10px}}h3{{margin:8px 0}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:6px;margin-bottom:8px}}.card{{background:white;border:1px solid #98a2b3;padding:9px}}
.card .num{{font-size:25px;font-weight:800;line-height:1.1}}.card small{{color:#667085}}.section{{background:white;border:1px solid #98a2b3;padding:9px;margin-bottom:8px}}
.dbgrid{{display:grid;grid-template-columns:repeat(5,minmax(160px,1fr));gap:6px}}.db{{border:1px solid #98a2b3;background:#fff;padding:9px}}.db b{{font-size:13px}}.db .num{{font-size:22px;font-weight:800;margin:5px 0}}
.flow{{display:grid;grid-template-columns:repeat(7,1fr);gap:5px}}.step{{background:white;border:1px solid #98a2b3;padding:8px;min-height:92px}}.step b{{display:block;margin-bottom:4px}}.step small{{color:#667085}}
.searchgrid{{display:grid;grid-template-columns:2fr 1fr 1.2fr .8fr .9fr 1fr 70px 64px;gap:4px;margin-bottom:7px}}
input,select{{width:100%;height:30px;border:1px solid #98a2b3;padding:4px;font-size:11px;background:white}}
.tablebox{{overflow:auto;max-height:74vh;border:1px solid #667085;background:white}}
table{{border-collapse:collapse;width:max-content;min-width:100%;font-size:10px}}
th,td{{border:1px solid #98a2b3;padding:5px 6px;text-align:left;vertical-align:top;white-space:normal;overflow-wrap:anywhere}}
th{{background:#e9eef5;position:sticky;top:0;z-index:5;white-space:nowrap}}
tbody tr:nth-child(even) td{{background:#f8fafc}}tbody tr:hover td{{background:#eef4ff}}
.desc{{min-width:310px;max-width:440px}}.loc{{min-width:120px}}.nowrap{{white-space:nowrap}}
.status-ok{{color:#067647;font-weight:700}}.status-bad{{color:#b42318;font-weight:700}}
.notice{{background:#fff7e6;border:1px solid #f79009;padding:8px;margin-bottom:8px}}
@media(max-width:1100px){{.dbgrid,.flow{{grid-template-columns:1fr 1fr}}.searchgrid{{grid-template-columns:1fr 1fr}}}}
</style></head>
<body><header><b>Alliance CRE Intelligence OS 10.0</b><br><small>{_e(subtitle)}</small></header>
<nav>{_nav()}</nav><div class="wrap"><h2>{_e(title)}</h2>{body}</div></body></html>"""

def _filter_form(q, location, category, transaction, status, assigned, limit):
    cats = '<option value="">All Categories</option>' + "".join(
        f'<option value="{_e(c)}" {"selected" if category == c else ""}>{_e(c)}</option>'
        for c in CATEGORY_OPTIONS
    )
    return f"""<form class="searchgrid" method="get">
      <input name="q" value="{_e(q)}" placeholder="Search ID, description, contact, source">
      <input name="location" value="{_e(location)}" placeholder="Location">
      <select name="category">{cats}</select>
      <select name="transaction"><option value="">Rent / Sale</option>
        <option value="RENT" {"selected" if transaction.upper()=="RENT" else ""}>Rent</option>
        <option value="SALE" {"selected" if transaction.upper()=="SALE" else ""}>Sale</option>
        <option value="LEASE" {"selected" if transaction.upper()=="LEASE" else ""}>Lease</option>
      </select>
      <select name="status"><option value="">All Status</option>
        <option {"selected" if status.upper()=="AVAILABLE" else ""}>AVAILABLE</option>
        <option {"selected" if status.upper()=="UNVERIFIED" else ""}>UNVERIFIED</option>
        <option {"selected" if status.upper()=="VERIFIED" else ""}>VERIFIED</option>
        <option {"selected" if status.upper()=="NOT_AVAILABLE" else ""}>NOT_AVAILABLE</option>
      </select>
      <input name="assigned" value="{_e(assigned)}" placeholder="Assigned To">
      <input type="number" name="limit" min="1" max="1500" value="{int(limit)}">
      <button>Search</button>
    </form>"""

def _dashboard(engine) -> str:
    metrics = {
        "properties": _scalar(engine, "SELECT COUNT(*) FROM pi_master_properties_v711"),
        "requirements": _scalar(engine, "SELECT COUNT(*) FROM pi_master_requirements_v711"),
        "available": _scalar(engine, "SELECT COUNT(*) FROM pi_master_workflow_v720 WHERE availability_status='AVAILABLE'"),
        "unverified": _scalar(engine, "SELECT COUNT(*) FROM pi_master_workflow_v720 WHERE COALESCE(verification_status,'UNVERIFIED')='UNVERIFIED'"),
        "matches": _scalar(engine, "SELECT COUNT(*) FROM pi_master_matches_v720"),
        "followups": _scalar(engine, "SELECT COUNT(*) FROM pi_master_action_state_v730 WHERE followup_status='SCHEDULED'"),
    }
    top = "".join(
        f'<div class="card"><div class="num">{int(value or 0):,}</div><small>{_e(label)}</small></div>'
        for label, value in (
            ("Master Properties", metrics["properties"]),
            ("Master Requirements", metrics["requirements"]),
            ("Available", metrics["available"]),
            ("Unverified", metrics["unverified"]),
            ("Matches", metrics["matches"]),
            ("Follow-ups", metrics["followups"]),
        )
    )
    pcards = "".join(
        f'<div class="db"><b>{s.title()} Database</b><div class="num">{_source_count(engine,"PROPERTY",s):,}</div><a class="btn good" href="/alliance/final/database/{s.lower()}">Open</a></div>'
        for s in SOURCES
    )
    rcards = "".join(
        f'<div class="db"><b>{s.title()} Requirements</b><div class="num">{_source_count(engine,"REQUIREMENT",s):,}</div><a class="btn good" href="/alliance/final/requirements/{s.lower()}">Open</a></div>'
        for s in SOURCES
    )
    flow = "".join(
        f'<div class="step"><b>{_e(title)}</b><small>{_e(desc)}</small><br><br><a class="btn" href="{path}">Open</a></div>'
        for title, desc, path in (
            ("1. CAPTURE", "Add property from manual, newspaper, WhatsApp or magazine source.", "/property-manual"),
            ("2. VERIFY", "Call owner/broker and confirm availability before client use.", "/alliance/primary/availability"),
            ("3. REQUIREMENT", "Capture tenant/buyer demand into Master Requirements.", "/requirements-workbench"),
            ("4. MATCH", "Search Master Property Database only and rank suitable inventory.", "/alliance/primary/matcher"),
            ("5. CLIENT", "Review client-safe options without internal contact intelligence.", "/alliance/final/database/master"),
            ("6. FOLLOW-UP", "Track call-backs, re-verification and requirement follow-up.", "/alliance/primary/followups"),
            ("7. DEAL", "Track movement to visit, negotiation and closure.", "/alliance/primary/reports"),
        )
    )
    return f"""
    <div class="cards">{top}</div>
    <div class="notice"><b>Operating rule:</b> source databases remain source-specific views. Master Property and Master Requirement databases are canonical. Matcher searches Master Property Database only.</div>
    <div class="section"><h3>Property Databases</h3><div class="dbgrid">{pcards}</div></div>
    <div class="section"><h3>Requirement Databases</h3><div class="dbgrid">{rcards}</div></div>
    <div class="section"><h3>Team Workflow</h3><div class="flow">{flow}</div></div>
    """

def _property_page(engine, source, q, location, category, transaction, status, assigned, limit):
    rows = v910._property_rows(engine, source, q, location, category, transaction, status, assigned, limit)
    total = _source_count(engine, "PROPERTY", source)
    body = f'<div class="cards"><div class="card"><div class="num">{total:,}</div><small>Total {source.title()} Properties</small></div></div>'
    body += '<div class="section">' + _filter_form(q, location, category, transaction, status, assigned, limit) + '</div>'
    trs = []
    for row in rows:
        cr = _dict(row.get("clean_record"))
        cid = str(row.get("canonical_id") or "")
        locality = row.get("locality") or _first(cr, "location", "locality") or ""
        address = _first(cr, "address", "exact_address", "property_address") or ""
        desc = _first(cr, "team_description", "description_edit", "description", "original_description", "original_message", "raw_line", "source_text") or ""
        if address and address.lower() not in str(desc).lower():
            desc = f"{address} · {desc}".strip(" ·")
        tx = row.get("transaction_type") or _first(cr, "transaction_type", "rent_or_sale") or ""
        pcat = v910._property_category(cr, tx)
        ptype = _first(cr, "property_type", "asset_type", "subtype") or ""
        area = _first(cr, "area_display", "area", "available_area") or ""
        if not area:
            av = _first(cr, "area_value") or row.get("area_value") or row.get("area_sqft") or ""
            au = _first(cr, "area_unit") or row.get("area_unit") or ("SQFT" if row.get("area_sqft") else "")
            area = f"{av} {au}".strip()
        floor = _first(cr, "floor", "floors", "floor_codes") or ""
        if isinstance(floor, list):
            floor = ", ".join(map(str, floor))
        amount = (
            _first(cr, "rent", "monthly_rent", "rent_amount", "rent_in_figures")
            if str(tx).upper() in ("RENT", "LEASE")
            else _first(cr, "sale_price", "sale_amount", "price", "asking_price")
        )
        amount = amount or _first(cr, "amount", "price_raw") or row.get("price_raw") or ""
        cname, cphone = v910._contacts(cr)
        stat = row.get("availability_status")
        if not stat or stat == "UNKNOWN":
            stat = row.get("verification_status") or "UNVERIFIED"
        src = _source_label(engine, cid, "PROPERTY") or source

        open_url = f"/alliance/primary/property/{_e(cid)}"
        verify = f'<a class="btn good" href="{open_url}">Verify / Open</a>'
        history = f'<a class="btn light" href="{open_url}">History</a>'
        edit = f'<a class="btn light" href="{open_url}">Edit</a>'
        delete = (
            f'<form method="post" action="/alliance/primary/property/{_e(cid)}/delete" style="margin:0" '
            f'onsubmit="return confirm(\'Archive this property? Source evidence remains preserved.\')">'
            f'<button class="danger">Delete</button></form>'
        )
        vals = [
            cid, locality, desc, pcat, ptype, area, floor, amount, cname, cphone,
            _fmt_dt(row.get("created_at")), stat, verify, history,
            row.get("assigned_to") or "", src, edit, delete,
        ]
        raw_cols = {12, 13, 16, 17}
        tds = []
        for i, val in enumerate(vals):
            cls = ' class="desc"' if i == 2 else (' class="loc"' if i == 1 else "")
            tds.append(f"<td{cls}>{val if i in raw_cols else _e(val)}</td>")
        trs.append("<tr>" + "".join(tds) + "</tr>")

    headers = (
        "Property ID", "Location", "Description / Address", "Property Category",
        "Property Type", "Area", "Floor", "Amount", "Contact Name", "Contact No.",
        "Entry Date & Time", "Status", "Verify", "History", "Assigned To",
        "Source", "Edit", "Delete",
    )
    body += (
        '<div class="tablebox"><table><thead><tr>'
        + "".join(f"<th>{_e(h)}</th>" for h in headers)
        + "</tr></thead><tbody>"
        + ("".join(trs) if trs else f'<tr><td colspan="{len(headers)}">No properties found.</td></tr>')
        + "</tbody></table></div>"
    )
    return body

def _requirement_rows(engine, source, q, location, category, transaction, status, assigned, limit):
    pat = None if source == "MASTER" else f"%{source}%"
    source_clause = ""
    if pat:
        source_clause = """AND (
          EXISTS (
            SELECT 1 FROM pi_master_source_links_v711 l
            WHERE l.canonical_id=r.canonical_id
              AND l.master_entity_type='REQUIREMENT'
              AND (
                UPPER(COALESCE(l.source_type,'')) LIKE :pat OR
                UPPER(COALESCE(l.source_table,'')) LIKE :pat
              )
          )
          OR UPPER(COALESCE(r.clean_record->>'source','')) LIKE :pat
          OR UPPER(COALESCE(r.clean_record->>'source_type','')) LIKE :pat
          OR UPPER(COALESCE(r.clean_record->>'source_name','')) LIKE :pat
          OR UPPER(COALESCE(r.clean_record->>'channel','')) LIKE :pat
        )"""

    sql = f"""
    SELECT r.*,
           COALESCE(w.verification_status,'UNVERIFIED') AS verification_status,
           COALESCE(w.assigned_to,a.assigned_to) AS assigned_to
    FROM pi_master_requirements_v711 r
    LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=r.canonical_id
    LEFT JOIN pi_master_action_state_v730 a ON a.canonical_id=r.canonical_id
    WHERE 1=1
      {source_clause}
      AND (:q='%%' OR r.canonical_id ILIKE :q OR COALESCE(r.locality,'') ILIKE :q OR COALESCE(r.city,'') ILIKE :q OR COALESCE(r.clean_record::text,'') ILIKE :q)
      AND (:loc='%%' OR COALESCE(r.locality,'') ILIKE :loc OR COALESCE(r.city,'') ILIKE :loc OR COALESCE(r.clean_record::text,'') ILIKE :loc)
      AND (:tx='' OR UPPER(COALESCE(r.transaction_type,''))=:tx)
      AND (:st='' OR UPPER(COALESCE(w.verification_status,'UNVERIFIED'))=:st)
      AND (:asgn='%%' OR COALESCE(w.assigned_to,a.assigned_to,'') ILIKE :asgn)
    ORDER BY r.updated_at DESC NULLS LAST, r.created_at DESC NULLS LAST
    LIMIT :limit
    """
    params = {
        "pat": pat or "",
        "q": f"%{q.strip()}%",
        "loc": f"%{location.strip()}%",
        "tx": transaction.upper().strip(),
        "st": status.upper().strip(),
        "asgn": f"%{assigned.strip()}%",
        "limit": int(limit),
    }
    with engine.connect() as conn:
        rows = [dict(x) for x in conn.execute(text(sql), params).mappings().all()]

    if category.strip():
        want = category.lower().strip()
        rows = [
            r for r in rows
            if want in str(_first(_dict(r.get("clean_record")), "property_category", "required_property_category", "category") or "").lower()
        ]
    return rows

def _requirement_page(engine, source, q, location, category, transaction, status, assigned, limit):
    rows = _requirement_rows(engine, source, q, location, category, transaction, status, assigned, limit)
    total = _source_count(engine, "REQUIREMENT", source)
    body = f'<div class="cards"><div class="card"><div class="num">{total:,}</div><small>Total {source.title()} Requirements</small></div></div>'
    body += '<div class="section">' + _filter_form(q, location, category, transaction, status, assigned, limit) + '</div>'

    trs = []
    for row in rows:
        cr = _dict(row.get("clean_record"))
        cid = str(row.get("canonical_id") or "")
        desc = _first(cr, "requirement_text", "original_message", "original_description", "additional_points", "description", "source_text") or ""
        company = _first(cr, "company_name", "brand_name", "client_company", "company") or ""
        cname = _first(cr, "contact_name", "client_name", "sender_name", "name") or ""
        cphone = _first(cr, "contact_number", "contact_phone", "phone", "mobile", "mobile_no", "contact_no") or ""
        loc = row.get("locality") or _first(cr, "location", "locality") or ""
        pcat = _first(cr, "property_category", "required_property_category", "category") or ""
        ptype = _first(cr, "property_type", "required_property_type", "asset_type") or ""
        area = _first(cr, "required_area", "required_area_sqft", "minimum_area_sqft", "maximum_area_sqft") or row.get("area_sqft") or ""
        tx = row.get("transaction_type") or _first(cr, "transaction_type", "rent_or_sale") or ""
        budget = _first(cr, "budget", "rent_budget", "sale_budget", "budget_raw") or ""
        stat = row.get("verification_status") or "UNVERIFIED"
        src = _source_label(engine, cid, "REQUIREMENT") or source
        openbtn = f'<a class="btn good" href="/alliance/primary/requirement/{_e(cid)}">Open / Match</a>'
        vals = (
            cid, desc, company, cname, cphone, loc, pcat, ptype, area, tx, budget,
            _fmt_dt(row.get("created_at")), stat, row.get("assigned_to") or "", src, openbtn,
        )
        cells = []
        for i, val in enumerate(vals):
            cls = ' class="desc"' if i == 1 else ""
            cells.append(f"<td{cls}>{val if i == 15 else _e(val)}</td>")
        trs.append("<tr>" + "".join(cells) + "</tr>")

    headers = (
        "Requirement ID", "Requirement / Original Message", "Client / Company",
        "Contact Name", "Contact No.", "Location", "Property Category",
        "Property Type", "Area", "Rent/Sale", "Budget", "Entry Date & Time",
        "Status", "Assigned To", "Source", "Open / Match",
    )
    body += (
        '<div class="tablebox"><table><thead><tr>'
        + "".join(f"<th>{_e(h)}</th>" for h in headers)
        + "</tr></thead><tbody>"
        + ("".join(trs) if trs else f'<tr><td colspan="{len(headers)}">No requirements found.</td></tr>')
        + "</tbody></table></div>"
    )
    return body

def _hub(engine, entity: str) -> str:
    cards = []
    for source in SOURCES:
        count = _source_count(engine, entity, source)
        if entity == "PROPERTY":
            title = f"{source.title()} Database"
            path = f"/alliance/final/database/{source.lower()}"
        else:
            title = f"{source.title()} Requirements"
            path = f"/alliance/final/requirements/{source.lower()}"
        note = "Canonical inventory" if source == "MASTER" else "Source-specific canonical view"
        cards.append(
            f'<div class="db"><b>{_e(title)}</b><div class="num">{count:,}</div>'
            f'<small>{note}</small><br><br><a class="btn good" href="{path}">Open</a></div>'
        )
    return '<div class="dbgrid">' + "".join(cards) + "</div>"

def _data_health(engine, app, storage_guard: dict) -> str:
    rows = []
    for table in CORE_TABLES:
        exists = bool(_scalar(
            engine,
            "SELECT to_regclass(:name) IS NOT NULL",
            {"name": f"public.{table}"},
            False,
        ))
        count = "—"
        if exists:
            count = f"{int(_scalar(engine, f'SELECT COUNT(*) FROM {table}', default=0) or 0):,}"
        rows.append(
            f'<tr><td>{_e(table)}</td>'
            f'<td class="{"status-ok" if exists else "status-bad"}">{"READY" if exists else "MISSING"}</td>'
            f'<td>{count}</td></tr>'
        )

    history_size = _scalar(
        engine,
        "SELECT pg_size_pretty(pg_total_relation_size('ai_source_history'))",
        default="unknown",
    )

    routes = (
        "/alliance/primary",
        "/alliance/final/databases",
        "/alliance/final/requirements",
        "/alliance/final/database/{source}",
        "/alliance/final/requirements/{source}",
        "/alliance/primary/availability",
        "/alliance/primary/matcher",
        "/alliance/primary/followups",
        "/alliance/primary/reports",
    )
    route_rows = "".join(
        f'<tr><td>{_e(p)}</td><td class="{"status-ok" if _route_exists(app,p) else "status-bad"}">'
        f'{"READY" if _route_exists(app,p) else "MISSING"}</td></tr>'
        for p in routes
    )

    return f"""
      <div class="cards">
        <div class="card"><div class="num">{_e(history_size)}</div><small>ai_source_history physical size</small></div>
        <div class="card"><div class="num">{_e(storage_guard.get("status"))}</div><small>Large-history storage guard</small></div>
      </div>
      <div class="notice"><b>Safe recovery policy:</b> live property, requirement and source data is never deleted by OS 10.0 startup. The storage guard only prevents future history snapshots from copying large binary payload fields. Existing history remains preserved until a verified backup and explicit maintenance action.</div>
      <div class="section"><h3>Core Database Recovery Status</h3>
        <table><thead><tr><th>Table</th><th>Status</th><th>Rows</th></tr></thead>
        <tbody>{"".join(rows)}</tbody></table>
      </div>
      <div class="section"><h3>Critical Route Acceptance</h3>
        <table><thead><tr><th>Route</th><th>Status</th></tr></thead>
        <tbody>{route_rows}</tbody></table>
      </div>
    """

def _simple_page(intro: str, items):
    cards = "".join(
        f'<div class="db"><b>{_e(label)}</b><p><small>{_e(desc)}</small></p>'
        f'<a class="btn" href="{path}">Open</a></div>'
        for label, desc, path in items
    )
    return f'<div class="notice">{_e(intro)}</div><div class="dbgrid">{cards}</div>'

def register(core):
    app = _app(core)
    engine = _engine(core)
    if app is None or engine is None:
        raise RuntimeError("Alliance CRE OS 10.0 requires core app + database engine")

    guard = _storage_guard(engine)

    takeover = (
        "/alliance/primary",
        "/alliance/final/databases",
        "/alliance/final/requirements",
        "/alliance/final/database/{source}",
        "/alliance/final/requirements/{source}",
        "/alliance/primary/contacts",
        "/alliance/primary/ai-control",
        "/alliance/primary/data-health",
    )
    for path in takeover:
        _remove_get(app, path)

    @app.get("/alliance/primary", response_class=HTMLResponse)
    def dashboard(req: Request):
        _login(core, req)
        return HTMLResponse(_shell("Command Centre", _dashboard(engine)))

    @app.get("/alliance/final/databases", response_class=HTMLResponse)
    def property_hub(req: Request):
        _login(core, req)
        return HTMLResponse(_shell("5 Property Databases", _hub(engine, "PROPERTY")))

    @app.get("/alliance/final/requirements", response_class=HTMLResponse)
    def requirement_hub(req: Request):
        _login(core, req)
        return HTMLResponse(_shell("5 Requirement Databases", _hub(engine, "REQUIREMENT")))

    @app.get("/alliance/final/database/{source}", response_class=HTMLResponse)
    def property_database(
        req: Request,
        source: str,
        q: str = Query(""),
        location: str = Query(""),
        category: str = Query(""),
        transaction: str = Query(""),
        status: str = Query(""),
        assigned: str = Query(""),
        limit: int = Query(500, ge=1, le=1500),
    ):
        _login(core, req)
        src = source.upper()
        if src not in SOURCES:
            return HTMLResponse(
                _shell("Unknown Database", "<div class='notice'>Unknown property database.</div>"),
                status_code=404,
            )
        body = _property_page(
            engine, src, q, location, category, transaction, status, assigned, limit
        )
        return HTMLResponse(_shell(f"{src.title()} Property Database", body))

    @app.get("/alliance/final/requirements/{source}", response_class=HTMLResponse)
    def requirement_database(
        req: Request,
        source: str,
        q: str = Query(""),
        location: str = Query(""),
        category: str = Query(""),
        transaction: str = Query(""),
        status: str = Query(""),
        assigned: str = Query(""),
        limit: int = Query(500, ge=1, le=1500),
    ):
        _login(core, req)
        src = source.upper()
        if src not in SOURCES:
            return HTMLResponse(
                _shell("Unknown Requirements", "<div class='notice'>Unknown requirement database.</div>"),
                status_code=404,
            )
        body = _requirement_page(
            engine, src, q, location, category, transaction, status, assigned, limit
        )
        return HTMLResponse(_shell(f"{src.title()} Requirements", body))

    @app.get("/alliance/primary/contacts", response_class=HTMLResponse)
    def contacts(req: Request):
        _login(core, req)
        body = _simple_page(
            "Internal contact intelligence stays inside Alliance and is never included in client-safe output.",
            (
                ("Property Contacts", "Owner and broker contacts linked to canonical properties.", "/alliance/final/database/master"),
                ("Requirement Contacts", "Client, brand and demand-side contacts linked to requirements.", "/alliance/final/requirements/master"),
                ("Verification Queue", "Call and update current availability before matching.", "/alliance/primary/availability"),
                ("Follow-ups", "Team call-backs and scheduled actions.", "/alliance/primary/followups"),
                ("Reports", "Pipeline and activity visibility.", "/alliance/primary/reports"),
            ),
        )
        return HTMLResponse(_shell("Contacts", body))

    @app.get("/alliance/primary/ai-control", response_class=HTMLResponse)
    def ai_control(req: Request):
        _login(core, req)
        body = _simple_page(
            "AI assists capture, organization, verification, matching and discovery. Canonical data changes remain reviewable by the team.",
            (
                ("Property Capture", "Manual and source ingestion into canonical property intelligence.", "/property-manual"),
                ("Requirement Capture", "Capture demand and preserve original source evidence.", "/requirements-workbench"),
                ("Smart Matcher", "Master-only property matching.", "/alliance/primary/matcher"),
                ("Verification", "Human confirmation before client use.", "/alliance/primary/availability"),
                ("Data Health", "Database, storage guard and route acceptance.", "/alliance/primary/data-health"),
            ),
        )
        return HTMLResponse(_shell("AI Control Centre", body))

    @app.get("/alliance/primary/data-health", response_class=HTMLResponse)
    def data_health(req: Request):
        _login(core, req)
        return HTMLResponse(
            _shell(
                "Data Health & Recovery",
                _data_health(engine, app, guard),
                "Non-destructive database recovery, route acceptance and storage protection",
            )
        )

    aliases = (
        ("/alliance/primary/databases", "/alliance/final/databases"),
        ("/alliance/primary/properties", "/alliance/final/database/master"),
        ("/alliance/primary/requirements-hub", "/alliance/final/requirements"),
        ("/alliance/primary/requirements", "/alliance/final/requirements/master"),
    )
    for old, target in aliases:
        _remove_get(app, old)

        async def _redirect(request: Request, _target=target):
            qs = request.url.query
            return RedirectResponse(
                _target + (("?" + qs) if qs else ""),
                status_code=307,
            )

        app.add_api_route(old, _redirect, methods=["GET"], include_in_schema=False)

    dynamic_aliases = (
        ("/alliance/primary/database/{source}", "/alliance/final/database/{source}"),
        ("/alliance/primary/requirements/source/{source}", "/alliance/final/requirements/{source}"),
    )
    for old, target in dynamic_aliases:
        _remove_get(app, old)

        async def _redirect_source(request: Request, source: str, _target=target):
            qs = request.url.query
            url = _target.replace("{source}", source)
            return RedirectResponse(
                url + (("?" + qs) if qs else ""),
                status_code=307,
            )

        app.add_api_route(old, _redirect_source, methods=["GET"], include_in_schema=False)

    priority = (
        "/alliance/primary",
        "/alliance/final/databases",
        "/alliance/final/requirements",
        "/alliance/final/database/{source}",
        "/alliance/final/requirements/{source}",
        "/alliance/primary/databases",
        "/alliance/primary/database/{source}",
        "/alliance/primary/properties",
        "/alliance/primary/requirements-hub",
        "/alliance/primary/requirements/source/{source}",
        "/alliance/primary/requirements",
        "/alliance/primary/contacts",
        "/alliance/primary/ai-control",
        "/alliance/primary/data-health",
    )
    for path in reversed(priority):
        _move_front(app, path)

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "storage_guard": guard,
        "route_count": len(app.router.routes),
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "architecture": "CAPTURE->DATABASE->VERIFY->REQUIREMENT->MATCH->CLIENT->FOLLOW-UP->DEAL",
        "data_policy": "NON_DESTRUCTIVE",
    }
