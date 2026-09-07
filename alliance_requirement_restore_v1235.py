from __future__ import annotations

import hashlib
import html
import json
import re
from typing import Any

from fastapi import Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

VERSION = "12.3.8-ROBUST-MASTER-REQUIREMENTS"
SOURCES = ("MASTER", "NEWSPAPER", "WHATSAPP", "MAGAZINE", "MANUAL")

EXCLUDE_TOKENS = (
    "match", "workflow", "action", "audit", "review", "repair", "task", "journal",
    "score", "acceptance", "metric", "test", "exam", "certification", "work_no",
    "mapping", "map_", "source_link", "archive", "history", "log"
)

def _app(core):
    return getattr(core, "app", None) or core

def _engine(core):
    return getattr(core, "engine", None)

def _login(core, req):
    fn = getattr(core, "need_login", None)
    return fn(req) if fn else "team"

def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v))

def _dict(v):
    if isinstance(v, dict):
        return dict(v)
    if isinstance(v, str):
        try:
            x = json.loads(v)
            return x if isinstance(x, dict) else {}
        except Exception:
            return {}
    return {}

def _walk(obj, wanted):
    wanted = {str(x).lower() for x in wanted}
    out = []
    def rec(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if str(k).lower() in wanted and v not in (None, "", [], {}):
                    out.append(v)
                rec(v)
        elif isinstance(x, list):
            for y in x:
                rec(y)
    rec(obj)
    return out

def _first(obj, keys, default=""):
    vals = _walk(obj, keys)
    if not vals:
        return default
    v = vals[0]
    if isinstance(v, list):
        return ", ".join(str(x) for x in v if x not in (None, ""))
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False, default=str)
    return v

def _message(obj):
    vals = _walk(obj, [
        "original_message", "requirement_message", "message", "raw_message", "raw_text",
        "source_text", "description", "requirement_text", "requirement",
        "additional_points", "remarks", "notes", "content"
    ])
    candidates = []
    for v in vals:
        if isinstance(v, (dict, list)):
            continue
        s = re.sub(r"\s+", " ", str(v or "")).strip()
        if s:
            candidates.append(s)
    return max(candidates, key=len) if candidates else ""

def _qident(name):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(name or "")):
        raise ValueError("Unsafe SQL identifier")
    return '"' + str(name) + '"'

def _table_exists(e, name):
    try:
        with e.connect() as c:
            return bool(c.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": name}).scalar())
    except Exception:
        return False

def _classify(value):
    s = str(value or "").upper()
    if "NEWSPAPER" in s or "NEWS_PAPER" in s:
        return "NEWSPAPER"
    if "WHATSAPP" in s or "WAI_" in s or s.startswith("WA_") or "_WA_" in s:
        return "WHATSAPP"
    if "MAGAZINE" in s:
        return "MAGAZINE"
    if "MANUAL" in s:
        return "MANUAL"
    return "OTHER"

def _discover_tables(e):
    try:
        with e.connect() as c:
            names = c.execute(text("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema=current_schema()
                  AND table_type='BASE TABLE'
                  AND table_name ILIKE '%requirement%'
                ORDER BY table_name
            """)).scalars().all()
    except Exception:
        return []
    exact_skip = {"pi_master_requirements_v711", "pi_requirement_work_no_v1230"}
    out = []
    for name in names:
        s = str(name)
        lo = s.lower()
        if s in exact_skip or any(tok in lo for tok in EXCLUDE_TOKENS):
            continue
        out.append(s)
    return out[:60]

def _source_hint(obj):
    keys = [
        "source", "source_type", "source_name", "channel", "import_source", "origin",
        "database_source", "ingestion_source"
    ]
    return " ".join(str(_first(obj, [k], "")) for k in keys)

def _normalize_source_row(table, obj, idx):
    source = _classify(table)
    if source == "OTHER":
        source = _classify(_source_hint(obj))
    canonical_id = _first(obj, ["canonical_id", "master_requirement_id", "master_id"])
    source_pk = _first(obj, ["id", "record_id", "requirement_id", "source_id", "pk"], idx)
    amin = _first(obj, ["area_min", "area_min_sqft", "minimum_area", "minimum_area_sqft", "min_area", "min_area_sqft"])
    amax = _first(obj, ["area_max", "area_max_sqft", "maximum_area", "maximum_area_sqft", "max_area", "max_area_sqft"])
    area = f"{amin}-{amax}" if amin or amax else _first(obj, ["area_sqft", "requirement_sqft", "required_area", "area"])
    return {
        "canonical_id": str(canonical_id or ""),
        "source_pk": str(source_pk or idx),
        "source_table": table,
        "source": source,
        "message": _message(obj),
        "company": _first(obj, ["company_name", "brand_name", "client_company", "company", "retailer_name"]),
        "contact_name": _first(obj, ["contact_name", "client_name", "sender_name", "name"]),
        "contact": _first(obj, [
            "phone", "phones", "contact_no", "contact_number", "contact_phone",
            "mobile", "mobile_no", "sender_phone", "sender_mobile"
        ]),
        "location": _first(obj, [
            "preferred_locations", "preferred_location", "location", "locality",
            "city", "area_name", "micro_market"
        ]),
        "transaction": _first(obj, ["transaction_type", "transaction", "rent_sale", "rent_or_sale", "deal_type"]),
        "category": _first(obj, [
            "property_category", "required_property_category", "category",
            "intended_use", "use", "use_case", "business_category"
        ]),
        "property_type": _first(obj, ["property_type", "required_property_type", "asset_type"]),
        "area": area,
        "budget": _first(obj, ["budget", "sale_budget", "rent_budget", "max_budget", "budget_raw"]),
        "created_at": _first(obj, ["created_at", "timestamp", "message_timestamp", "date", "captured_at"]),
        "verification": "SOURCE / NEEDS MASTER VERIFICATION",
        "assigned_to": "",
        "is_master": False,
    }

def _source_rows(e, source, per_table=5000):
    rows = []
    tables = _discover_tables(e)
    for table in tables:
        table_class = _classify(table)
        if table_class != "OTHER" and table_class != source:
            continue
        try:
            sql = f"SELECT to_jsonb(t) AS d FROM {_qident(table)} t LIMIT :n"
            with e.connect() as c:
                raw_rows = c.execute(text(sql), {"n": int(per_table)}).scalars().all()
        except Exception:
            continue
        for idx, raw in enumerate(raw_rows, 1):
            obj = _dict(raw) if not isinstance(raw, dict) else dict(raw)
            if not obj:
                continue
            row = _normalize_source_row(table, obj, idx)
            if row["source"] != source:
                continue
            if not any((row["message"], row["contact"], row["location"], row["company"], row["canonical_id"])):
                continue
            rows.append(row)
    return rows, tables

def _master_rows(e, source, limit=10000):
    if not _table_exists(e, "pi_master_requirements_v711"):
        return []

    params = {"n": int(limit)}
    source_clause = ""
    if source != "MASTER":
        params["pat"] = f"%{source}%"
        source_clause = """
          AND (
            EXISTS (
              SELECT 1 FROM pi_master_source_links_v711 l
              WHERE l.canonical_id=(to_jsonb(r)->>'canonical_id')
                AND l.master_entity_type='REQUIREMENT'
                AND (
                  UPPER(COALESCE(l.source_type,'')) LIKE :pat OR
                  UPPER(COALESCE(l.source_table,'')) LIKE :pat
                )
            )
            OR UPPER(COALESCE(to_jsonb(r)->>'source','')) LIKE :pat
            OR UPPER(COALESCE(to_jsonb(r)->>'source_type','')) LIKE :pat
            OR UPPER(COALESCE(to_jsonb(r)->>'source_name','')) LIKE :pat
            OR UPPER(COALESCE(to_jsonb(r)->>'channel','')) LIKE :pat
            OR UPPER(COALESCE(to_jsonb(r)->>'import_source','')) LIKE :pat
          )
        """

    sql = f"""
        SELECT
          to_jsonb(r) AS d,
          COALESCE(w.verification_status,'UNVERIFIED') AS verification_status,
          COALESCE(a.assigned_to,'') AS assigned_to
        FROM pi_master_requirements_v711 r
        LEFT JOIN pi_master_workflow_v720 w
          ON w.canonical_id=(to_jsonb(r)->>'canonical_id')
        LEFT JOIN pi_master_action_state_v730 a
          ON a.canonical_id=(to_jsonb(r)->>'canonical_id')
        WHERE 1=1 {source_clause}
        ORDER BY
          NULLIF(to_jsonb(r)->>'updated_at','') DESC NULLS LAST,
          NULLIF(to_jsonb(r)->>'created_at','') DESC NULLS LAST
        LIMIT :n
    """
    try:
        with e.connect() as c:
            data = c.execute(text(sql), params).mappings().all()
    except Exception:
        return []

    out = []
    for x in data:
        meta = dict(x)
        obj = _dict(meta.get("d"))
        if not obj:
            continue
        cr = _dict(obj.get("clean_record"))

        cid = _first(obj, ["canonical_id", "id", "requirement_id"])
        locality = _first(obj, ["locality", "location"])
        city = _first(obj, ["city"])
        location = locality or city or _first(cr, ["location", "preferred_location", "preferred_locations"])

        contact = _first(obj, ["phones", "phone", "contact_phone", "contact_number", "mobile"])
        if not contact:
            contact = _first(cr, ["contact_phone", "contact_number", "phone", "mobile", "phones"])

        area = _first(obj, ["area_sqft", "required_area_sqft", "requirement_sqft"])
        if not area:
            amin = _first(cr, ["area_min_sqft", "minimum_area_sqft", "min_area_sqft"])
            amax = _first(cr, ["area_max_sqft", "maximum_area_sqft", "max_area_sqft"])
            area = f"{amin}-{amax}" if amin or amax else _first(cr, ["required_area", "area"])

        budget = _first(obj, ["sale_budget", "rent_budget", "budget", "budget_raw"])
        if not budget:
            budget = _first(cr, ["sale_budget", "rent_budget", "budget", "budget_raw", "max_budget"])

        transaction = _first(obj, ["transaction_type", "transaction", "rent_or_sale"])
        if not transaction:
            transaction = _first(cr, ["transaction_type", "transaction", "rent_or_sale"])

        out.append({
            "canonical_id": str(cid or ""),
            "source_pk": "",
            "source_table": "pi_master_requirements_v711",
            "source": "MASTER" if source == "MASTER" else source,
            "message": _message(cr) or _message(obj),
            "company": _first(cr, ["company_name", "brand_name", "client_company", "company", "retailer_name"]),
            "contact_name": _first(cr, ["contact_name", "client_name", "name", "sender_name"]),
            "contact": contact,
            "location": location,
            "transaction": transaction,
            "category": _first(cr, ["property_category", "required_property_category", "category", "intended_use", "use"]),
            "property_type": _first(cr, ["property_type", "required_property_type", "asset_type"]),
            "area": area,
            "budget": budget,
            "created_at": _first(obj, ["created_at", "updated_at"]),
            "verification": meta.get("verification_status") or "UNVERIFIED",
            "assigned_to": meta.get("assigned_to") or "",
            "is_master": True,
        })
    return out

def _fingerprint(row):
    cid = str(row.get("canonical_id") or "").strip().lower()
    if cid:
        return "CID:" + cid
    phone = re.sub(r"\D", "", str(row.get("contact") or ""))
    msg = re.sub(r"\s+", " ", str(row.get("message") or "").strip().lower())
    loc = re.sub(r"\s+", " ", str(row.get("location") or "").strip().lower())
    company = re.sub(r"\s+", " ", str(row.get("company") or "").strip().lower())
    seed = "|".join((phone, msg, loc, company))
    return "FP:" + hashlib.sha1(seed.encode("utf-8", "ignore")).hexdigest()

def _combined(e, source):
    masters = _master_rows(e, source)
    if source == "MASTER":
        return masters, {"master": len(masters), "source_only": 0, "tables": []}
    source_rows, tables = _source_rows(e, source)
    master_ids = {str(r.get("canonical_id") or "") for r in masters if r.get("canonical_id")}
    seen = {_fingerprint(r) for r in masters}
    restored = []
    for row in source_rows:
        cid = str(row.get("canonical_id") or "")
        if cid and cid in master_ids:
            continue
        fp = _fingerprint(row)
        if fp in seen:
            continue
        seen.add(fp)
        restored.append(row)
    return masters + restored, {"master": len(masters), "source_only": len(restored), "tables": tables}

def _filtered(rows, q, location, transaction, status, assigned):
    ql = q.strip().lower()
    ll = location.strip().lower()
    tx = transaction.strip().upper()
    st = status.strip().upper()
    aa = assigned.strip().lower()
    out = []
    for row in rows:
        blob = " ".join(str(row.get(k) or "") for k in (
            "canonical_id","source_pk","source_table","message","company","contact_name",
            "contact","location","transaction","category","property_type","area","budget"
        )).lower()
        if ql and ql not in blob:
            continue
        if ll and ll not in str(row.get("location") or "").lower() and ll not in blob:
            continue
        if tx and tx != str(row.get("transaction") or "").upper():
            continue
        if st and st not in str(row.get("verification") or "").upper():
            continue
        if aa and aa not in str(row.get("assigned_to") or "").lower():
            continue
        out.append(row)
    return out

def _remove_get(app, path):
    app.router.routes[:] = [
        r for r in list(app.router.routes)
        if not (
            getattr(r, "path", None) == path
            and "GET" in set(getattr(r, "methods", set()) or set())
        )
    ]

def _move_front(app, path):
    found = [
        r for r in list(app.router.routes)
        if getattr(r, "path", None) == path
        and "GET" in set(getattr(r, "methods", set()) or set())
    ]
    for r in found:
        try:
            app.router.routes.remove(r)
        except ValueError:
            pass
    for r in reversed(found):
        app.router.routes.insert(0, r)

def _nav():
    return """
    <nav>
      <a href="/team-dashboard-v376">Dashboard</a>
      <a href="/alliance/primary">Command Centre</a>
      <a href="/property-manual">Add Property</a>
      <a href="/alliance/final/databases">Property Databases</a>
      <a href="/alliance/primary/availability">Verification</a>
      <a href="/requirements-workbench">Add Requirement</a>
      <a href="/alliance/final/requirements">Requirement Databases</a>
      <a href="/alliance/primary/matcher">Smart Matcher</a>
      <a href="/alliance/primary/followups">Follow-ups</a>
      <a href="/alliance/primary/reports">Deals & Reports</a>
      <a href="/alliance/primary/contacts">Contacts</a>
      <a href="/alliance/primary/ai-control">AI Control</a>
      <a href="/alliance/primary/data-health">Data Health</a>
    </nav>
    """

def _shell(title, body):
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)}</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f4f7fb;color:#172033;font-family:Arial,sans-serif}}
header{{background:#10223f;color:white;padding:18px 22px}}header b{{font-size:22px}}header small{{display:block;margin-top:4px}}
nav{{background:white;border-bottom:1px solid #d6dde6;padding:9px;display:flex;gap:6px;flex-wrap:wrap;position:sticky;top:0;z-index:20}}
nav a,.btn,button{{background:#10223f;color:white;text-decoration:none;border:0;border-radius:7px;padding:8px 10px;font-size:12px;cursor:pointer}}
.wrap{{max-width:1900px;margin:auto;padding:16px}}.card{{background:white;border:1px solid #dfe5ec;border-radius:12px;padding:14px;margin-bottom:12px}}
.grid{{display:grid;grid-template-columns:repeat(5,minmax(190px,1fr));gap:10px}}
@media(max-width:1000px){{.grid{{grid-template-columns:1fr 1fr}}}}@media(max-width:620px){{.grid{{grid-template-columns:1fr}}}}
.dbcard{{background:white;border:1px solid #ccd7e4;border-radius:12px;padding:14px;text-decoration:none;color:#172033}}
.dbcard b{{display:block;color:#16315a}}.num{{font-size:30px;font-weight:900;margin:6px 0}}.sub{{font-size:11px;color:#667085;min-height:30px}}.open{{font-size:11px;color:#175cd3;font-weight:800;margin-top:8px}}
.notice{{background:#fff9ec;border:1px solid #f2c86b;border-radius:10px;padding:11px;margin-bottom:12px}}
.filters{{display:grid;grid-template-columns:2fr 1fr 1fr 1fr 1fr auto;gap:6px}}@media(max-width:900px){{.filters{{grid-template-columns:1fr 1fr}}}}
input,select{{width:100%;padding:8px;border:1px solid #98a2b3;border-radius:6px}}
.tablebox{{overflow:auto;max-height:72vh;background:white;border:1px solid #dfe5ec}}table{{border-collapse:collapse;width:max-content;min-width:100%;font-size:11px}}
th,td{{border:1px solid #d0d5dd;padding:7px;text-align:left;vertical-align:top;white-space:normal}}th{{position:sticky;top:0;background:#e9eef5;z-index:4}}
.desc{{min-width:300px;max-width:500px}}.sourceonly{{background:#fff8e8}}.masterrow{{background:#f8fff9}}
</style></head><body>
<header><b>Alliance CRE Intelligence OS 11</b><small>PROPERTY â†’ VERIFY â†’ REQUIREMENT â†’ MATCH â†’ CLIENT â†’ FOLLOW-UP â†’ DEAL</small></header>
{_nav()}<div class="wrap"><h2>{_e(title)}</h2>{body}<p><a class="btn" href="/team-dashboard-v376">â† Back to Dashboard</a></p></div>
</body></html>"""

def _hub(e):
    cards = []
    details = {}
    for source in SOURCES:
        rows, meta = _combined(e, source)
        details[source] = meta
        note = "Canonical inventory Â· matcher authority" if source == "MASTER" else (
            f"Master linked: {meta['master']} Â· Restored source-only: {meta['source_only']}"
        )
        cards.append(f"""<a class="dbcard" href="/alliance/final/requirements/{source.lower()}">
          <b>{_e(source.title())} Requirements</b><div class="num">{len(rows)}</div>
          <div class="sub">{_e(note)}</div><div class="open">Open â†’</div></a>""")
    body = f"""<div class="notice"><b>All requirements restored for visibility.</b>
    Master remains canonical. Source-only records are read-only here and must be human verified/promoted before Smart Matcher can use them.
    Nothing is auto-copied into Master and no duplicate Master records are created.</div>
    <div class="grid">{''.join(cards)}</div>"""
    return _shell("5 Requirement Databases", body), details

def _table(e, source, q, location, transaction, status, assigned, limit):
    rows, meta = _combined(e, source)
    rows = _filtered(rows, q, location, transaction, status, assigned)[:limit]
    filters = f"""<div class="card"><form class="filters">
      <input name="q" value="{_e(q)}" placeholder="Search message, contact, brand, ID or source">
      <input name="location" value="{_e(location)}" placeholder="Location">
      <select name="transaction"><option value="">Rent / Sale</option>
        <option value="RENT" {'selected' if transaction.upper()=='RENT' else ''}>RENT</option>
        <option value="LEASE" {'selected' if transaction.upper()=='LEASE' else ''}>LEASE</option>
        <option value="SALE" {'selected' if transaction.upper()=='SALE' else ''}>SALE</option></select>
      <select name="status"><option value="">All Status</option>
        <option value="VERIFIED" {'selected' if status.upper()=='VERIFIED' else ''}>VERIFIED</option>
        <option value="UNVERIFIED" {'selected' if status.upper()=='UNVERIFIED' else ''}>UNVERIFIED</option>
        <option value="SOURCE" {'selected' if status.upper()=='SOURCE' else ''}>SOURCE / NEEDS VERIFICATION</option></select>
      <input name="assigned" value="{_e(assigned)}" placeholder="Assigned To">
      <button>Search</button>
    </form></div>"""
    trs = []
    for row in rows:
        is_master = bool(row.get("is_master"))
        rid = row.get("canonical_id") or f"{row.get('source_table')}:{row.get('source_pk')}"
        if is_master and row.get("canonical_id"):
            cid = _e(row["canonical_id"])
            action = (
                f'<a class="btn" href="/alliance/primary/requirement/{cid}">Open</a> '
                f'<a class="btn" href="/alliance/primary/matcher?requirement_id={cid}">Run Match</a>'
            )
        else:
            src = _e(row.get("source") or source)
            spk = _e(row.get("source_pk") or "")
            action = (
                f'<a class="btn" href="/alliance/final/requirements/run-match?source={src}&source_pk={spk}">Run Match</a>'
            )
        cls = "masterrow" if is_master else "sourceonly"
        vals = [
            rid, row.get("message"), row.get("company"), row.get("contact_name"), row.get("contact"),
            row.get("location"), row.get("category"), row.get("property_type"), row.get("area"),
            row.get("transaction"), row.get("budget"), row.get("created_at"), row.get("verification"),
            row.get("assigned_to") or "UNASSIGNED", row.get("source_table"), action
        ]
        cells = []
        for i, value in enumerate(vals):
            if i == 15:
                cells.append(f"<td>{value}</td>")
            else:
                css = " class='desc'" if i == 1 else ""
                cells.append(f"<td{css}>{_e(value)}</td>")
        trs.append(f"<tr class='{cls}'>{''.join(cells)}</tr>")
    note = (
        f"<b>{len(rows)}</b> rows shown. Canonical linked: <b>{meta['master']}</b> Â· "
        f"Restored source-only: <b>{meta['source_only']}</b>. "
        "Green rows are Master. Verified Master requirements show Run Match. Yellow source-only rows must be verified/promoted first."
    )
    headers = [
        "Requirement ID / Source ID","Original Requirement","Client / Company","Contact Name","Contact No.",
        "Location","Category / Use","Property Type","Area","Rent/Sale","Budget","Date / Time",
        "Verification","Assigned To","Source Database","Action"
    ]
    body = f"""<div class="notice">{note}</div>{filters}
    <div class="tablebox"><table><thead><tr>{''.join('<th>'+h+'</th>' for h in headers)}</tr></thead>
    <tbody>{''.join(trs) if trs else '<tr><td colspan="16">No requirements found.</td></tr>'}</tbody></table></div>"""
    return _shell(f"{source.title()} Requirements", body)

def register(core):
    app = _app(core)
    e = _engine(core)
    if app is None or e is None:
        raise RuntimeError("Requirement restore requires FastAPI app + SQLAlchemy engine")

    for path in ("/alliance/final/requirements", "/alliance/final/requirements/{source}"):
        _remove_get(app, path)

    @app.get("/alliance/final/requirements", response_class=HTMLResponse, include_in_schema=False)
    def requirement_hub(req: Request):
        _login(core, req)
        page, _ = _hub(e)
        return HTMLResponse(page, headers={"Cache-Control":"no-store","X-Alliance-Requirement-Restore":VERSION})

    @app.get("/alliance/final/requirements/{source}", response_class=HTMLResponse, include_in_schema=False)
    def requirement_db(
        req: Request,
        source: str,
        q: str = Query(""),
        location: str = Query(""),
        transaction: str = Query(""),
        status: str = Query(""),
        assigned: str = Query(""),
        limit: int = Query(1500, ge=1, le=5000),
    ):
        _login(core, req)
        src = source.upper()
        if src not in SOURCES:
            return HTMLResponse("Unknown requirement database", status_code=404)
        return HTMLResponse(
            _table(e, src, q, location, transaction, status, assigned, limit),
            headers={"Cache-Control":"no-store","X-Alliance-Requirement-Restore":VERSION},
        )

    @app.get("/alliance/final/requirements/run-match", response_class=HTMLResponse, include_in_schema=False)
    def source_run_match(req: Request, source: str = Query(""), source_pk: str = Query("")):
        _login(core, req)
        src = source.upper().strip()
        if src not in SOURCES or src == "MASTER":
            return HTMLResponse(_shell("Run Match", '<div class="notice">Invalid source requirement.</div>'), status_code=400)

        rows, _ = _source_rows(e, src)
        selected = None
        for row in rows:
            if str(row.get("source_pk") or "") == str(source_pk or ""):
                selected = row
                break

        if not selected:
            return HTMLResponse(
                _shell("Run Match", '<div class="notice"><b>Requirement not found in source database.</b></div>'),
                status_code=404,
            )

        msg = _e(selected.get("message") or "")
        loc = _e(selected.get("location") or "")
        contact = _e(selected.get("contact") or "")
        body = f"""
        <div class="notice"><b>Run Match requested.</b><br>
        This record is still a source-only requirement. Smart Matcher accepts only a human-verified Master Requirement.
        Verify/promote this exact requirement first; after verification, use Run Match from the Master Requirement row.</div>
        <div class="card">
          <b>Original Requirement</b><p>{msg}</p>
          <b>Location</b><p>{loc}</p>
          <b>Contact</b><p>{contact}</p>
          <p><a class="btn" href="/alliance/primary/requirements?q={source_pk}">Verify / Promote This Requirement</a></p>
        </div>
        """
        return HTMLResponse(_shell("Run Match", body), headers={"Cache-Control":"no-store"})

    @app.get("/api/alliance/requirement-restore/status", include_in_schema=False)
    def restore_status(req: Request):
        _login(core, req)
        found = _discover_tables(e)
        counts = {}
        for src in SOURCES:
            rows, meta = _combined(e, src)
            counts[src] = {
                "visible": len(rows),
                "master_linked": meta["master"],
                "source_only": meta["source_only"],
            }
        return {
            "status":"OK",
            "version":VERSION,
            "source_tables_found":found,
            "counts":counts,
            "matcher_master_only":True,
        }

    _move_front(app, "/alliance/final/requirements")
    _move_front(app, "/alliance/final/requirements/{source}")
    _move_front(app, "/alliance/final/requirements/run-match")
    _move_front(app, "/api/alliance/requirement-restore/status")

    return {
        "status":"REGISTERED",
        "version":VERSION,
        "hub":"/alliance/final/requirements",
        "status_api":"/api/alliance/requirement-restore/status",
        "matcher_master_only":True,
        "master_mutation":False,
    }
