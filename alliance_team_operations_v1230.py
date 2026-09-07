from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import date
from typing import Any, Dict, List

from fastapi import Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION = "12.3.1-UNIFIED-REQUIREMENTS-TEAM-OPERATIONS"

DDL = [
    """CREATE TABLE IF NOT EXISTS pi_requirement_work_no_v1230(
        id BIGSERIAL PRIMARY KEY,
        canonical_id TEXT NOT NULL UNIQUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS pi_team_daily_tasks_v1230(
        id BIGSERIAL PRIMARY KEY,
        task_date DATE NOT NULL DEFAULT CURRENT_DATE,
        team_member TEXT NOT NULL,
        task_text TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'OPEN',
        outcome TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        completed_at TIMESTAMPTZ
    )""",
    """CREATE INDEX IF NOT EXISTS idx_team_tasks_v1230_member_date
       ON pi_team_daily_tasks_v1230(team_member,task_date DESC)""",
    """CREATE TABLE IF NOT EXISTS pi_team_daily_journal_v1230(
        task_date DATE NOT NULL,
        team_member TEXT NOT NULL,
        morning_plan TEXT,
        evening_summary TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY(task_date,team_member)
    )""",
]

def _app(core):
    return getattr(core, "app", None) or core

def _engine(core):
    return getattr(core, "engine", None)

def _role(core, req):
    fn = getattr(core, "need_login", None)
    return fn(req) if fn else "team"

def _actor(core, req):
    fn = getattr(core, "actor_name", None)
    return str(fn(req) if fn else "team")

def _safe(v):
    if v is None:
        return ""
    if isinstance(v, (dict, list, tuple)):
        try:
            return json.dumps(v, ensure_ascii=False, default=str)
        except Exception:
            return str(v)
    return str(v)

def _esc(v):
    return html.escape(_safe(v))

def _qident(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(name or "")):
        raise ValueError("unsafe SQL identifier")
    return '"' + str(name) + '"'

def _remove_get(app, path):
    kept = []
    for r in list(app.router.routes):
        methods = set(getattr(r, "methods", set()) or set())
        if getattr(r, "path", None) == path and "GET" in methods:
            continue
        kept.append(r)
    app.router.routes[:] = kept

def _prioritize_get(app, path):
    matches, rest = [], []
    for r in list(app.router.routes):
        methods = set(getattr(r, "methods", set()) or set())
        if getattr(r, "path", None) == path and "GET" in methods:
            matches.append(r)
        else:
            rest.append(r)
    app.router.routes[:] = matches + rest

def _table_exists(engine, name: str) -> bool:
    with engine.connect() as c:
        return bool(c.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": name}).scalar())

def _ensure(engine):
    master_exists = _table_exists(engine, "pi_master_requirements_v711")
    with engine.begin() as c:
        for ddl in DDL:
            c.execute(text(ddl))
        if master_exists:
            c.execute(text("""
                INSERT INTO pi_requirement_work_no_v1230(canonical_id)
                SELECT r.canonical_id
                FROM pi_master_requirements_v711 r
                LEFT JOIN pi_requirement_work_no_v1230 n ON n.canonical_id=r.canonical_id
                WHERE n.canonical_id IS NULL
                ORDER BY r.canonical_id
                ON CONFLICT(canonical_id) DO NOTHING
            """))

def _shell(core, req, title, body):
    role = _role(core, req)
    nav = """
    <a href="/alliance/primary">← Back to Dashboard</a>
    <a href="/alliance/primary/requirements">Requirements</a>
    <a href="/alliance/primary/matcher">Matcher</a>
    <a href="/alliance/primary/availability">Availability</a>
    <a href="/alliance/primary/properties">Properties</a>
    <a href="/alliance/primary/followups">Follow-ups</a>
    <a href="/alliance/primary/tasks">My Day</a>
    <a href="/alliance/primary/tasks/monthly">Monthly Review</a>
    <a href="/alliance/primary/manual">Team Manual</a>
    """
    return f"""<!doctype html><html><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{html.escape(title)}</title>
    <style>
    *{{box-sizing:border-box}}body{{margin:0;background:#f5f7fb;color:#172033;font-family:Arial,sans-serif}}
    header{{background:#102a43;color:white;padding:18px 22px;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}}
    nav{{background:white;border-bottom:1px solid #dfe6ee;padding:10px 14px;display:flex;gap:7px;flex-wrap:wrap;position:sticky;top:0;z-index:10}}
    nav a,.btn,.mini{{background:#102a43;color:white;text-decoration:none;border:0;border-radius:8px;padding:9px 11px;cursor:pointer;display:inline-block}}
    .btn.good,.mini.good{{background:#067647}}.btn.warn,.mini.warn{{background:#b54708}}.btn.alt,.mini.alt{{background:#475467}}
    .wrap{{max-width:1900px;margin:auto;padding:18px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}}
    .card{{background:white;border:1px solid #e1e7ee;border-radius:12px;padding:14px;margin-bottom:12px}}
    .metric{{font-size:30px;font-weight:900}}.muted{{color:#667085}}.ok{{color:#067647;font-weight:700}}.warntext{{color:#b54708;font-weight:700}}
    .tablebox{{overflow:auto;max-height:72vh}}table{{border-collapse:collapse;width:100%;font-size:12px}}
    th,td{{padding:8px;border-bottom:1px solid #edf0f4;text-align:left;vertical-align:top}}th{{position:sticky;top:0;background:#f8fafc}}
    input,select,textarea{{padding:8px;border:1px solid #cfd8e3;border-radius:7px;max-width:100%}}textarea{{width:100%}}
    form.inline{{display:flex;gap:7px;flex-wrap:wrap;align-items:center}}.actions{{display:flex;gap:5px;flex-wrap:wrap}}
    .pill{{padding:4px 8px;border-radius:999px;background:#eef2f6;white-space:nowrap}}.source{{font-weight:800;color:#175cd3}}
    .mono{{font-family:Consolas,monospace;font-size:11px}}.message{{min-width:320px;max-width:520px;white-space:normal}}
    </style></head><body>
    <header><div><b>Alliance CRE · Team Operations 12.3.1</b><br><small>{html.escape(title)}</small></div>
    <div>{html.escape(str(role))} · <a href="/logout" style="color:white">Logout</a></div></header>
    <nav>{nav}</nav><div class="wrap">{body}</div></body></html>"""

def _dict(v: Any) -> Dict[str, Any]:
    if isinstance(v, dict):
        return dict(v)
    if isinstance(v, str):
        try:
            x = json.loads(v)
            return x if isinstance(x, dict) else {}
        except Exception:
            return {}
    return {}

def _deep_values(obj: Any, wanted) -> List[Any]:
    wanted = {str(x).lower() for x in wanted}
    out = []
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if str(k).lower() in wanted and v not in (None, "", [], {}):
                    out.append(v)
                walk(v)
        elif isinstance(x, list):
            for y in x:
                walk(y)
    walk(obj)
    return out

def _first(obj: Any, keys, default=""):
    vals = _deep_values(obj, keys)
    if not vals:
        return default
    v = vals[0]
    if isinstance(v, list):
        return ", ".join(str(x) for x in v if x not in (None, ""))
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False, default=str)
    return v

def _original_message(obj: Any) -> str:
    vals = _deep_values(obj, [
        "original_message","requirement_message","message","raw_message","raw_text",
        "source_text","description","requirement_text","requirement","remarks","notes","content"
    ])
    candidates = []
    for v in vals:
        if isinstance(v, (dict, list)):
            continue
        s = re.sub(r"\s+", " ", str(v or "")).strip()
        if s:
            candidates.append(s)
    if not candidates:
        return ""
    candidates.sort(key=len, reverse=True)
    return candidates[0]

def _source_label(table: str, source_type: str = "") -> str:
    s = (str(table or "") + " " + str(source_type or "")).upper()
    if "WHATSAPP" in s or "WAI_" in s or "WA_" in s:
        return "WHATSAPP"
    if "MANUAL" in s:
        return "MANUAL"
    if "MAGAZINE" in s:
        return "MAGAZINE"
    if "NEWSPAPER" in s:
        return "NEWSPAPER"
    if "MASTER" in s:
        return "MASTER"
    return "OTHER"

def _fingerprint(row: Dict[str, Any]) -> str:
    seed = "|".join([
        str(row.get("canonical_id") or "").strip().lower(),
        re.sub(r"\D", "", str(row.get("contact") or "")),
        re.sub(r"\s+", " ", str(row.get("message") or "").strip().lower()),
        str(row.get("location") or "").strip().lower(),
    ])
    return hashlib.sha1(seed.encode("utf-8", "ignore")).hexdigest()

def _master_rows(engine) -> List[Dict[str, Any]]:
    if not _table_exists(engine, "pi_master_requirements_v711"):
        return []
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT n.id AS work_no,
                   r.canonical_id,r.locality,r.city,r.transaction_type,r.area_sqft,
                   r.sale_budget,r.rent_budget,r.phones,r.clean_record,
                   COALESCE(w.verification_status,'UNVERIFIED') AS verification_status,
                   COALESCE(a.assigned_to,'') AS assigned_to,
                   COALESCE(a.stage,'NEW') AS stage
            FROM pi_master_requirements_v711 r
            LEFT JOIN pi_requirement_work_no_v1230 n ON n.canonical_id=r.canonical_id
            LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=r.canonical_id
            LEFT JOIN pi_master_action_state_v730 a ON a.canonical_id=r.canonical_id
            ORDER BY n.id DESC NULLS LAST
            LIMIT 10000
        """)).mappings().all()
    out = []
    for x in rows:
        r = dict(x)
        clean = _dict(r.get("clean_record"))
        message = _original_message(clean)
        contact = _safe(r.get("phones") or _first(clean, ["phone","phones","contact_no","contact_number","mobile"]))
        name = _first(clean, ["brand_name","company_name","client_name","contact_name","name"])
        use = _first(clean, ["intended_use","use","use_case","business_category","property_type","category"])
        area_min = _first(clean, ["area_min_sqft","minimum_area_sqft","min_area_sqft"])
        area_max = _first(clean, ["area_max_sqft","maximum_area_sqft","max_area_sqft"])
        area = f"{area_min}-{area_max}" if area_min or area_max else _safe(r.get("area_sqft"))
        out.append({
            "work_no":r.get("work_no"),"canonical_id":r.get("canonical_id"),"source_table":"pi_master_requirements_v711",
            "source":"MASTER","name":name,"contact":contact,"message":message,
            "location":r.get("locality") or r.get("city") or "","transaction":r.get("transaction_type") or "",
            "use":use,"area":area,"budget":r.get("sale_budget") or r.get("rent_budget") or "",
            "verification":r.get("verification_status") or "UNVERIFIED","assigned_to":r.get("assigned_to") or "",
            "stage":r.get("stage") or "NEW","is_master":True,
        })
    return out

def _discover_source_tables(engine) -> List[str]:
    with engine.connect() as c:
        names = c.execute(text("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema=current_schema()
              AND table_type='BASE TABLE'
              AND table_name ILIKE '%requirement%'
            ORDER BY table_name
        """)).scalars().all()
    exclude_exact = {"pi_master_requirements_v711","pi_requirement_work_no_v1230"}
    exclude_tokens = (
        "match","workflow","action","audit","review","repair","task","journal",
        "score","acceptance","metric","test","exam","certification"
    )
    out = []
    for n in names:
        s = str(n)
        lo = s.lower()
        if s in exclude_exact or any(tok in lo for tok in exclude_tokens):
            continue
        out.append(s)
    return out[:40]

def _source_rows(engine, table: str, limit=500) -> List[Dict[str, Any]]:
    try:
        sql = f"SELECT to_jsonb(t) AS row_data FROM {_qident(table)} t LIMIT :n"
        with engine.connect() as c:
            rows = c.execute(text(sql), {"n":int(limit)}).scalars().all()
    except Exception:
        return []
    out = []
    for i, raw in enumerate(rows, 1):
        obj = _dict(raw) if not isinstance(raw, dict) else dict(raw)
        if not obj:
            continue
        message = _original_message(obj)
        contact = _first(obj, ["phone","phones","contact_no","contact_number","mobile","mobile_no","sender_phone"])
        location = _first(obj, ["preferred_locations","preferred_location","location","locality","city","area_name"])
        name = _first(obj, ["brand_name","company_name","client_name","contact_name","name","sender_name"])
        transaction = _first(obj, ["transaction_type","transaction","rent_sale","deal_type"])
        use = _first(obj, ["intended_use","use","use_case","business_category","property_type","category"])
        area_min = _first(obj, ["area_min","area_min_sqft","minimum_area","minimum_area_sqft","min_area","min_area_sqft"])
        area_max = _first(obj, ["area_max","area_max_sqft","maximum_area","maximum_area_sqft","max_area","max_area_sqft"])
        area = f"{area_min}-{area_max}" if area_min or area_max else _first(obj, ["area_sqft","requirement_sqft","area"])
        budget = _first(obj, ["budget","sale_budget","rent_budget","max_budget","rent","sale_amount"])
        canonical_id = _first(obj, ["canonical_id","master_requirement_id"])
        source_type = _first(obj, ["source_type","source"])
        pk = _first(obj, ["id","record_id","requirement_id","source_id","pk"])
        if not any([message,contact,location,name,canonical_id]):
            continue
        out.append({
            "work_no":"","canonical_id":canonical_id,"source_table":table,"source_pk":pk or i,
            "source":_source_label(table,source_type),"name":name,"contact":contact,"message":message,
            "location":location,"transaction":transaction,"use":use,"area":area,"budget":budget,
            "verification":"SOURCE / NEEDS MASTER VERIFICATION","assigned_to":"","stage":"CAPTURED","is_master":False,
        })
    return out

def _all_requirement_rows(engine) -> List[Dict[str, Any]]:
    masters = _master_rows(engine)
    source_rows = []
    for table in _discover_source_tables(engine):
        source_rows.extend(_source_rows(engine, table, 500))
    master_ids = {str(x.get("canonical_id") or "") for x in masters if x.get("canonical_id")}
    seen = set()
    out = []
    for r in masters:
        seen.add(_fingerprint(r))
        out.append(r)
    for r in source_rows:
        cid = str(r.get("canonical_id") or "")
        if cid and cid in master_ids:
            continue
        fp = _fingerprint(r)
        if fp in seen:
            continue
        seen.add(fp)
        out.append(r)
    return out

def register(core):
    app = _app(core)
    engine = _engine(core)
    if app is None or engine is None:
        raise RuntimeError("Alliance Team Operations requires app + engine")

    _ensure(engine)
    _remove_get(app, "/alliance/primary/requirements")

    @app.get("/alliance/primary/requirements", response_class=HTMLResponse)
    def requirements_work_queue(
        req: Request,
        q: str = Query(""),
        source: str = Query(""),
        verification: str = Query(""),
        assignment: str = Query("")
    ):
        _role(core, req)
        _ensure(engine)
        rows = _all_requirement_rows(engine)
        counts = {"ALL":len(rows),"MASTER":0,"WHATSAPP":0,"MANUAL":0,"MAGAZINE":0,"NEWSPAPER":0,"OTHER":0}
        for r in rows:
            lab = str(r.get("source") or "OTHER").upper()
            counts[lab if lab in counts else "OTHER"] += 1

        ql = q.strip().lower()
        filtered = []
        for r in rows:
            lab = str(r.get("source") or "OTHER").upper()
            ver = str(r.get("verification") or "").upper()
            if source and lab != source.upper():
                continue
            if verification == "VERIFIED" and ver != "VERIFIED":
                continue
            if verification == "NEEDS_VERIFICATION" and ver == "VERIFIED":
                continue
            if assignment == "ASSIGNED" and not r.get("assigned_to"):
                continue
            if assignment == "UNASSIGNED" and r.get("assigned_to"):
                continue
            blob = " ".join(_safe(r.get(k)) for k in ["canonical_id","source_table","source","name","contact","message","location","transaction","use","area","budget"]).lower()
            if ql and ql not in blob:
                continue
            filtered.append(r)

        cards = "".join(
            f"<a class='card' style='text-decoration:none;color:inherit' href='/alliance/primary/requirements?source={k if k!='ALL' else ''}'><div class='muted'>{k.title()}</div><div class='metric'>{v}</div></a>"
            for k,v in counts.items()
        )
        form = f"""<div class='card'><form class='inline'>
        <input name='q' value='{html.escape(q,quote=True)}' placeholder='Search message, brand, contact, location or source'>
        <select name='source'><option value=''>All sources</option>{''.join(f"<option value='{x}' {'selected' if source.upper()==x else ''}>{x.title()}</option>" for x in ['MASTER','WHATSAPP','MANUAL','MAGAZINE','NEWSPAPER','OTHER'])}</select>
        <select name='verification'><option value=''>All verification</option><option value='VERIFIED' {'selected' if verification=='VERIFIED' else ''}>VERIFIED</option><option value='NEEDS_VERIFICATION' {'selected' if verification=='NEEDS_VERIFICATION' else ''}>NEEDS VERIFICATION</option></select>
        <select name='assignment'><option value=''>All assignments</option><option value='ASSIGNED' {'selected' if assignment=='ASSIGNED' else ''}>ASSIGNED</option><option value='UNASSIGNED' {'selected' if assignment=='UNASSIGNED' else ''}>UNASSIGNED</option></select>
        <button class='btn'>Filter</button></form></div>"""

        trs = []
        for idx,r in enumerate(filtered,1):
            cid = str(r.get("canonical_id") or "")
            is_master = bool(r.get("is_master"))
            verified = str(r.get("verification") or "").upper() == "VERIFIED"
            work = f"REQ-{int(r['work_no']):05d}" if r.get("work_no") else f"SRC-{idx:05d}"
            actions = "<div class='actions'>"
            if is_master and cid:
                actions += f"<a class='mini' href='/alliance/primary/requirement/{html.escape(cid,quote=True)}'>Open</a>"
                if verified:
                    actions += f"<a class='mini good' href='/alliance/primary/matcher?requirement_id={html.escape(cid,quote=True)}'>Run Matcher</a>"
                else:
                    actions += "<span class='pill'>Verify first</span>"
            else:
                actions += "<a class='mini warn' href='/alliance/final/requirements'>Open Verification Queue</a>"
            actions += "</div>"
            trs.append(f"""<tr>
                <td><b>{_esc(work)}</b><br><span class='mono'>{_esc(cid or r.get('source_pk'))}</span></td>
                <td>{actions}</td><td><span class='source'>{_esc(r.get('source'))}</span><br><span class='mono'>{_esc(r.get('source_table'))}</span></td>
                <td>{_esc(r.get('name'))}</td><td>{_esc(r.get('contact'))}</td><td class='message'>{_esc(r.get('message'))}</td>
                <td>{_esc(r.get('location'))}</td><td>{_esc(r.get('transaction'))}</td><td>{_esc(r.get('use'))}</td>
                <td>{_esc(r.get('area'))}</td><td>{_esc(r.get('budget'))}</td><td>{_esc(r.get('verification'))}</td>
                <td>{_esc(r.get('assigned_to') or 'UNASSIGNED')}</td><td>{_esc(r.get('stage'))}</td>
            </tr>""")

        source_table_count = len(_discover_source_tables(engine))
        body = f"""<h1>Unified Requirement Work Queue</h1>
        <div class='card'><b>All captured requirement databases on one working page.</b> Canonical Master Requirements stay the matching authority. WhatsApp, manual, magazine, newspaper and other requirement source/staging tables are read-only here until verified/promoted. This page does not create duplicate master requirements.</div>
        <div class='grid'>{cards}</div>{form}
        <div class='card'><b>{len(filtered)}</b> requirements shown from <b>{source_table_count}</b> source/staging requirement tables + Master Requirements. Only human VERIFIED master requirements can run Smart Matcher.</div>
        <div class='card tablebox'><table><tr>
        <th>Work No.</th><th>Action</th><th>Source / Database</th><th>Client / Brand</th><th>Contact</th><th>Original Requirement</th>
        <th>Location</th><th>Transaction</th><th>Use</th><th>Area</th><th>Budget</th><th>Verification</th><th>Assigned</th><th>Stage</th>
        </tr>{''.join(trs)}</table></div>"""
        return HTMLResponse(_shell(core, req, "Unified Requirement Work Queue", body))

    _prioritize_get(app, "/alliance/primary/requirements")

    @app.get("/alliance/primary/tasks", response_class=HTMLResponse)
    def my_day(req: Request):
        _role(core, req)
        actor = _actor(core, req)
        with engine.connect() as c:
            journal = c.execute(text("SELECT * FROM pi_team_daily_journal_v1230 WHERE task_date=CURRENT_DATE AND team_member=:m"), {"m":actor}).mappings().first()
            tasks = c.execute(text("SELECT * FROM pi_team_daily_tasks_v1230 WHERE task_date=CURRENT_DATE AND team_member=:m ORDER BY id"), {"m":actor}).mappings().all()
        j = dict(journal) if journal else {}
        task_rows = []
        for t in tasks:
            done = str(t.get("status") or "") == "DONE"
            action = "<span class='ok'>DONE</span>" if done else f"""<form method='post' action='/alliance/primary/tasks/{t['id']}/done'><input name='outcome' placeholder='What was done?' required><button class='mini good'>Complete</button></form>"""
            task_rows.append(f"<tr><td>{_esc(t.get('task_text'))}</td><td>{_esc(t.get('status'))}</td><td>{_esc(t.get('outcome'))}</td><td>{action}</td></tr>")
        body = f"""<h1>My Day · {date.today().isoformat()}</h1>
        <div class='grid'><div class='card'><h3>Morning Plan</h3><form method='post' action='/alliance/primary/tasks/journal'><textarea name='morning_plan' rows='6' placeholder='What must I complete today?'>{_esc(j.get('morning_plan'))}</textarea><input type='hidden' name='evening_summary' value='{html.escape(_safe(j.get('evening_summary')),quote=True)}'><button class='btn'>Save Morning Plan</button></form></div>
        <div class='card'><h3>Add Task</h3><form method='post' action='/alliance/primary/tasks/add'><textarea name='task_text' rows='4' required placeholder='One clear task'></textarea><button class='btn good'>Add Task</button></form></div></div>
        <div class='card tablebox'><table><tr><th>Task</th><th>Status</th><th>Outcome</th><th>Action</th></tr>{''.join(task_rows)}</table></div>
        <div class='card'><h3>End-of-Day Report</h3><form method='post' action='/alliance/primary/tasks/journal'><input type='hidden' name='morning_plan' value='{html.escape(_safe(j.get('morning_plan')),quote=True)}'><textarea name='evening_summary' rows='7' placeholder='What did I complete today? What remains? Important follow-ups?'>{_esc(j.get('evening_summary'))}</textarea><button class='btn alt'>Save Day Report</button></form></div>"""
        return HTMLResponse(_shell(core, req, "My Day", body))

    @app.post("/alliance/primary/tasks/add")
    def add_task(req: Request, task_text: str = Form(...)):
        _role(core, req)
        actor = _actor(core, req)
        with engine.begin() as c:
            c.execute(text("INSERT INTO pi_team_daily_tasks_v1230(task_date,team_member,task_text) VALUES(CURRENT_DATE,:m,:t)"), {"m":actor,"t":task_text.strip()})
        return RedirectResponse("/alliance/primary/tasks", status_code=303)

    @app.post("/alliance/primary/tasks/{task_id}/done")
    def done_task(task_id: int, req: Request, outcome: str = Form("")):
        _role(core, req)
        actor = _actor(core, req)
        with engine.begin() as c:
            c.execute(text("UPDATE pi_team_daily_tasks_v1230 SET status='DONE',outcome=:o,completed_at=NOW() WHERE id=:id AND team_member=:m"), {"o":outcome.strip(),"id":task_id,"m":actor})
        return RedirectResponse("/alliance/primary/tasks", status_code=303)

    @app.post("/alliance/primary/tasks/journal")
    def save_journal(req: Request, morning_plan: str = Form(""), evening_summary: str = Form("")):
        _role(core, req)
        actor = _actor(core, req)
        with engine.begin() as c:
            c.execute(text("""INSERT INTO pi_team_daily_journal_v1230(task_date,team_member,morning_plan,evening_summary,updated_at)
                VALUES(CURRENT_DATE,:m,:p,:e,NOW())
                ON CONFLICT(task_date,team_member) DO UPDATE SET morning_plan=EXCLUDED.morning_plan,evening_summary=EXCLUDED.evening_summary,updated_at=NOW()"""),
                {"m":actor,"p":morning_plan,"e":evening_summary})
        return RedirectResponse("/alliance/primary/tasks", status_code=303)

    @app.get("/alliance/primary/tasks/monthly", response_class=HTMLResponse)
    def monthly(req: Request, month: str = Query("")):
        _role(core, req)
        chosen = month if re.fullmatch(r"\d{4}-\d{2}", month or "") else date.today().strftime("%Y-%m")
        with engine.connect() as c:
            stats = c.execute(text("""SELECT team_member,COUNT(*) total,COUNT(*) FILTER(WHERE status='DONE') done
                FROM pi_team_daily_tasks_v1230 WHERE to_char(task_date,'YYYY-MM')=:m
                GROUP BY team_member ORDER BY team_member"""), {"m":chosen}).mappings().all()
            journals = c.execute(text("""SELECT team_member,COUNT(*) days_reported,
                COUNT(*) FILTER(WHERE COALESCE(evening_summary,'')<>'') evening_reports
                FROM pi_team_daily_journal_v1230 WHERE to_char(task_date,'YYYY-MM')=:m
                GROUP BY team_member ORDER BY team_member"""), {"m":chosen}).mappings().all()
        jmap = {x["team_member"]:dict(x) for x in journals}
        trs = []
        for s in stats:
            d = dict(s)
            j = jmap.get(d["team_member"],{})
            pct = round(100*d["done"]/d["total"],1) if d["total"] else 0
            trs.append(f"<tr><td>{_esc(d['team_member'])}</td><td>{d['total']}</td><td>{d['done']}</td><td>{pct}%</td><td>{j.get('days_reported',0)}</td><td>{j.get('evening_reports',0)}</td></tr>")
        body = f"""<h1>Monthly Team Review</h1><div class='card'><form class='inline'><input type='month' name='month' value='{chosen}'><button class='btn'>Load Month</button></form></div>
        <div class='card tablebox'><table><tr><th>Team Member</th><th>Tasks</th><th>Done</th><th>Completion</th><th>Days Reported</th><th>Evening Reports</th></tr>{''.join(trs)}</table></div>"""
        return HTMLResponse(_shell(core, req, "Monthly Review", body))

    @app.get("/alliance/primary/manual", response_class=HTMLResponse)
    def manual(req: Request):
        _role(core, req)
        body = """<h1>Alliance Team Manual</h1>
        <div class='card'><h3>Daily sequence</h3><p><b>1.</b> Open My Day and write today's plan. <b>2.</b> Open Requirements and work the unified queue. <b>3.</b> Verify the requirement before matching. <b>4.</b> Run Matcher only from a VERIFIED master requirement. <b>5.</b> Verify availability of shortlisted properties. <b>6.</b> Approve suitable matches. <b>7.</b> Assign and follow up. <b>8.</b> End the day with your completed-work report.</p></div>
        <div class='grid'><div class='card'><h3>Requirements</h3><p>Shows Master + WhatsApp + Manual + Magazine + Newspaper + other requirement source tables. Source-only rows must be verified/promoted before matching.</p></div><div class='card'><h3>Matcher</h3><p>Uses the Master Property Database. Never bypass human requirement verification.</p></div><div class='card'><h3>Availability</h3><p>Call/check shortlisted property contacts. VERIFIED + AVAILABLE is client-ready inventory.</p></div><div class='card'><h3>Client Safety</h3><p>Never send owner, broker or source contact details in the client-safe property option.</p></div></div>"""
        return HTMLResponse(_shell(core, req, "Team Manual", body))

    return {
        "status":"REGISTERED",
        "version":VERSION,
        "requirements_route":"AUTHORITATIVE_PRIORITY",
        "requirements_sources":"MASTER_PLUS_DISCOVERED_REQUIREMENT_TABLES_READ_ONLY",
        "master_requirement_mutation":False,
        "matcher_logic_changed":False,
        "gold_mutation":False,
    }
