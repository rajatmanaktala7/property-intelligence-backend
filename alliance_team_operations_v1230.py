from __future__ import annotations

import html
from datetime import date
from fastapi import Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION = "12.3.0-TEAM-OPERATIONS"

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
)"""
]

def _app(core): return getattr(core, "app", None) or core
def _engine(core): return getattr(core, "engine", None)

def _role(core, req):
    fn = getattr(core, "need_login", None)
    return fn(req) if fn else "team"

def _actor(core, req):
    fn = getattr(core, "actor_name", None)
    return str(fn(req) if fn else "team")

def _safe(v):
    return "" if v is None else str(v)

def _remove_get(app, path):
    keep = []
    for r in list(app.router.routes):
        methods = set(getattr(r, "methods", set()) or set())
        if getattr(r, "path", None) == path and "GET" in methods:
            continue
        keep.append(r)
    app.router.routes[:] = keep

def _ensure(engine):
    with engine.begin() as c:
        for ddl in DDL:
            c.execute(text(ddl))
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
    .wrap{{max-width:1800px;margin:auto;padding:18px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}}
    .card{{background:white;border:1px solid #e1e7ee;border-radius:12px;padding:14px;margin-bottom:12px}}
    .metric{{font-size:30px;font-weight:900}}.muted{{color:#667085}}.ok{{color:#067647;font-weight:700}}.warntext{{color:#b54708;font-weight:700}}
    .tablebox{{overflow:auto;max-height:72vh}}table{{border-collapse:collapse;width:100%;font-size:12px}}
    th,td{{padding:8px;border-bottom:1px solid #edf0f4;text-align:left;vertical-align:top}}th{{position:sticky;top:0;background:#f8fafc}}
    input,select,textarea{{padding:8px;border:1px solid #cfd8e3;border-radius:7px;max-width:100%}}textarea{{width:100%}}
    form.inline{{display:flex;gap:7px;flex-wrap:wrap;align-items:center}}.actions{{display:flex;gap:5px;flex-wrap:wrap}}
    .pill{{padding:4px 8px;border-radius:999px;background:#eef2f6;white-space:nowrap}}.source{{font-weight:800;color:#175cd3}}
    </style></head><body>
    <header><div><b>Alliance CRE · Team Operations 12.3</b><br><small>{html.escape(title)}</small></div>
    <div>{html.escape(str(role))} · <a href="/logout" style="color:white">Logout</a></div></header>
    <nav>{nav}</nav><div class="wrap">{body}</div></body></html>"""

def _requirement_rows(engine, limit=5000):
    sql = """
    SELECT
      n.id AS work_no,
      r.canonical_id,
      r.locality,
      r.city,
      r.transaction_type,
      r.area_sqft,
      r.sale_budget,
      r.rent_budget,
      r.phones,
      r.clean_record,
      r.created_at,
      COALESCE(w.verification_status,'UNVERIFIED') AS verification_status,
      COALESCE(a.assigned_to,'') AS assigned_to,
      COALESCE(a.stage,'NEW') AS stage,
      sl.source_type,
      sl.source_table,
      sl.source_pk
    FROM pi_master_requirements_v711 r
    JOIN pi_requirement_work_no_v1230 n ON n.canonical_id=r.canonical_id
    LEFT JOIN pi_master_workflow_v720 w
      ON w.canonical_id=r.canonical_id AND w.entity_type='REQUIREMENT'
    LEFT JOIN pi_master_action_state_v730 a
      ON a.canonical_id=r.canonical_id AND a.entity_type='REQUIREMENT'
    LEFT JOIN LATERAL (
      SELECT source_type,source_table,source_pk
      FROM pi_master_source_links_v711 s
      WHERE s.canonical_id=r.canonical_id
      ORDER BY s.id
      LIMIT 1
    ) sl ON TRUE
    ORDER BY n.id DESC
    LIMIT :lim
    """
    with engine.connect() as c:
        return [dict(x) for x in c.execute(text(sql), {"lim": limit}).mappings().all()]

def _source_label(row):
    s = (_safe(row.get("source_type")) + " " + _safe(row.get("source_table"))).upper()
    if "WHATSAPP" in s or "WAI_" in s:
        return "WHATSAPP"
    if "MANUAL" in s:
        return "MANUAL"
    if "MAGAZINE" in s:
        return "MAGAZINE"
    if "NEWSPAPER" in s:
        return "NEWSPAPER"
    return _safe(row.get("source_type") or "OTHER").upper()

def _clean(row):
    d = row.get("clean_record")
    return d if isinstance(d, dict) else {}

def _pick(row, *names):
    d = _clean(row)
    for src in (row, d):
        for n in names:
            if n in src and src.get(n) not in (None, "", [], {}):
                return src.get(n)
    return ""

def _esc(v): return html.escape(_safe(v))

def register(core):
    app = _app(core)
    engine = _engine(core)
    if app is None or engine is None:
        raise RuntimeError("Alliance Team Operations requires app + engine")

    _ensure(engine)

    # Replace only the Requirements GET page. Existing detail/verify/matcher POST routes remain untouched.
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
        rows = _requirement_rows(engine)

        counts = {"ALL": len(rows), "WHATSAPP":0, "MANUAL":0, "MAGAZINE":0, "NEWSPAPER":0, "OTHER":0}
        for r in rows:
            lab = _source_label(r)
            counts[lab if lab in counts else "OTHER"] += 1

        ql = q.strip().lower()
        filtered = []
        for r in rows:
            lab = _source_label(r)
            if source and lab != source.upper():
                continue
            if verification and _safe(r.get("verification_status")).upper() != verification.upper():
                continue
            if assignment == "ASSIGNED" and not r.get("assigned_to"):
                continue
            if assignment == "UNASSIGNED" and r.get("assigned_to"):
                continue
            blob = " ".join([
                _safe(r.get("canonical_id")), lab, _safe(r.get("locality")), _safe(r.get("city")),
                _safe(r.get("transaction_type")), _safe(r.get("phones")),
                _safe(_pick(r,"client_name","contact_name","company_name","brand_name","name")),
                _safe(_pick(r,"original_message","message","description","requirement_text","remarks","notes"))
            ]).lower()
            if ql and ql not in blob:
                continue
            filtered.append(r)

        cards = "".join(
            f"<a class='card' style='text-decoration:none;color:inherit' href='/alliance/primary/requirements?source={k if k!='ALL' else ''}'><div class='muted'>{k.title()}</div><div class='metric'>{v}</div></a>"
            for k,v in counts.items()
        )

        form = f"""<div class='card'><form class='inline'>
        <input name='q' value='{html.escape(q,quote=True)}' placeholder='Search brand, contact, location, message'>
        <select name='source'><option value=''>All sources</option>
        {''.join(f"<option value='{x}' {'selected' if source.upper()==x else ''}>{x.title()}</option>" for x in ['WHATSAPP','MANUAL','MAGAZINE','NEWSPAPER','OTHER'])}
        </select>
        <select name='verification'><option value=''>All verification</option>
        <option {'selected' if verification.upper()=='VERIFIED' else ''}>VERIFIED</option>
        <option {'selected' if verification.upper()=='UNVERIFIED' else ''}>UNVERIFIED</option></select>
        <select name='assignment'><option value=''>All assignments</option>
        <option value='ASSIGNED' {'selected' if assignment=='ASSIGNED' else ''}>ASSIGNED</option>
        <option value='UNASSIGNED' {'selected' if assignment=='UNASSIGNED' else ''}>UNASSIGNED</option></select>
        <button class='btn'>Filter</button></form></div>"""

        trs = []
        for r in filtered:
            cid = _safe(r.get("canonical_id"))
            verified = _safe(r.get("verification_status")).upper() == "VERIFIED"
            source_lab = _source_label(r)
            name = _pick(r,"company_name","brand_name","client_name","contact_name","name")
            use = _pick(r,"intended_use","suitable_category","use_case","business_category","property_type")
            area_min = _pick(r,"area_min_sqft","minimum_area_sqft","min_area_sqft")
            area_max = _pick(r,"area_max_sqft","maximum_area_sqft","max_area_sqft")
            area = f"{area_min}-{area_max}" if area_min or area_max else _safe(r.get("area_sqft"))
            actions = f"<div class='actions'><a class='mini' href='/alliance/primary/requirement/{html.escape(cid,quote=True)}'>Open</a>"
            if verified:
                actions += f"<a class='mini good' href='/alliance/primary/requirement/{html.escape(cid,quote=True)}'>Run Match</a>"
            else:
                actions += "<span class='pill'>Verify before match</span>"
            actions += "</div>"
            trs.append(f"""<tr>
              <td><b>REQ-{int(r['work_no']):05d}</b></td>
              <td>{actions}</td>
              <td><span class='source'>{_esc(source_lab)}</span></td>
              <td>{_esc(name)}</td><td>{_esc(r.get('phones'))}</td>
              <td>{_esc(r.get('locality') or r.get('city'))}</td>
              <td>{_esc(r.get('transaction_type'))}</td><td>{_esc(use)}</td><td>{_esc(area)}</td>
              <td>{_esc(r.get('sale_budget') or r.get('rent_budget'))}</td>
              <td>{_esc(r.get('verification_status'))}</td>
              <td>{_esc(r.get('assigned_to') or 'UNASSIGNED')}</td>
              <td>{_esc(r.get('stage'))}</td>
            </tr>""")

        body = f"""<h1>Requirement Work Queue</h1>
        <div class='card'><b>One master queue:</b> WhatsApp-group, manual and other requirement sources are shown from the canonical Master Requirement database. No duplicate shadow requirement database is created.</div>
        <div class='grid'>{cards}</div>{form}
        <div class='card'><b>{len(filtered)}</b> requirements shown. A requirement can run Smart Matcher only after human verification.</div>
        <div class='card tablebox'><table><tr>
        <th>Work No.</th><th>Actions</th><th>Source</th><th>Client / Brand</th><th>Contact</th>
        <th>Location</th><th>Transaction</th><th>Use</th><th>Area Sq Ft</th><th>Budget</th>
        <th>Verification</th><th>Assigned</th><th>Stage</th></tr>{''.join(trs)}</table></div>"""
        return HTMLResponse(_shell(core, req, "Requirement Work Queue", body))

    @app.get("/alliance/primary/tasks", response_class=HTMLResponse)
    def my_day(req: Request):
        _role(core, req)
        actor = _actor(core, req)
        today = date.today()
        with engine.connect() as c:
            journal = c.execute(text("""SELECT * FROM pi_team_daily_journal_v1230
              WHERE task_date=CURRENT_DATE AND team_member=:m"""), {"m":actor}).mappings().first()
            tasks = c.execute(text("""SELECT * FROM pi_team_daily_tasks_v1230
              WHERE task_date=CURRENT_DATE AND team_member=:m ORDER BY id"""), {"m":actor}).mappings().all()
        j = dict(journal) if journal else {}
        rows = "".join(f"""<tr><td>{x['id']}</td><td>{_esc(x['task_text'])}</td><td>{_esc(x['status'])}</td>
        <td>{_esc(x.get('outcome'))}</td><td>{f"<form method='post' action='/alliance/primary/tasks/{x['id']}/done'><input name='outcome' placeholder='Result / outcome'><button class='mini good'>Done</button></form>" if x['status']!='DONE' else '✓ Completed'}</td></tr>""" for x in tasks)
        body = f"""<h1>My Day · {html.escape(actor)}</h1>
        <div class='grid'>
        <div class='card'><h3>1. Start of Day</h3><p class='muted'>Before starting calls or matching, write what you plan to achieve today.</p>
        <form method='post' action='/alliance/primary/tasks/journal'>
        <textarea name='morning_plan' rows='6' placeholder='Today I will...'>{_esc(j.get('morning_plan'))}</textarea>
        <input type='hidden' name='evening_summary' value='{html.escape(_safe(j.get("evening_summary")),quote=True)}'>
        <button class='btn'>Save Morning Plan</button></form></div>
        <div class='card'><h3>2. Add Today's Tasks</h3>
        <form method='post' action='/alliance/primary/tasks/add'>
        <textarea name='task_text' rows='4' placeholder='Example: Verify top 10 matched properties for Saket restaurant requirement'></textarea>
        <button class='btn good'>Add Task</button></form></div></div>
        <div class='card'><h3>Today's Task List</h3><div class='tablebox'><table>
        <tr><th>#</th><th>Task</th><th>Status</th><th>Outcome</th><th>Action</th></tr>{rows}</table></div></div>
        <div class='card'><h3>3. End of Day</h3><p class='muted'>Before leaving, record what was actually completed, important calls, matches, site visits, problems and what carries forward.</p>
        <form method='post' action='/alliance/primary/tasks/journal'>
        <input type='hidden' name='morning_plan' value='{html.escape(_safe(j.get("morning_plan")),quote=True)}'>
        <textarea name='evening_summary' rows='8' placeholder='Today I completed...'>{_esc(j.get('evening_summary'))}</textarea>
        <button class='btn good'>Save End-of-Day Report</button></form></div>"""
        return HTMLResponse(_shell(core, req, "My Day", body))

    @app.post("/alliance/primary/tasks/add")
    def add_task(req: Request, task_text: str = Form(...)):
        _role(core, req); actor = _actor(core, req)
        if task_text.strip():
            with engine.begin() as c:
                c.execute(text("""INSERT INTO pi_team_daily_tasks_v1230(task_date,team_member,task_text)
                  VALUES(CURRENT_DATE,:m,:t)"""), {"m":actor,"t":task_text.strip()})
        return RedirectResponse("/alliance/primary/tasks", status_code=303)

    @app.post("/alliance/primary/tasks/{task_id}/done")
    def done_task(task_id: int, req: Request, outcome: str = Form("")):
        _role(core, req); actor = _actor(core, req)
        with engine.begin() as c:
            c.execute(text("""UPDATE pi_team_daily_tasks_v1230 SET status='DONE',outcome=:o,completed_at=NOW()
              WHERE id=:id AND team_member=:m"""), {"id":task_id,"m":actor,"o":outcome.strip() or None})
        return RedirectResponse("/alliance/primary/tasks", status_code=303)

    @app.post("/alliance/primary/tasks/journal")
    def save_journal(req: Request, morning_plan: str = Form(""), evening_summary: str = Form("")):
        _role(core, req); actor = _actor(core, req)
        with engine.begin() as c:
            c.execute(text("""INSERT INTO pi_team_daily_journal_v1230(task_date,team_member,morning_plan,evening_summary,updated_at)
              VALUES(CURRENT_DATE,:m,:p,:s,NOW())
              ON CONFLICT(task_date,team_member) DO UPDATE SET
              morning_plan=EXCLUDED.morning_plan,evening_summary=EXCLUDED.evening_summary,updated_at=NOW()"""),
              {"m":actor,"p":morning_plan.strip() or None,"s":evening_summary.strip() or None})
        return RedirectResponse("/alliance/primary/tasks", status_code=303)

    @app.get("/alliance/primary/tasks/monthly", response_class=HTMLResponse)
    def monthly_review(req: Request, month: str = Query("")):
        _role(core, req)
        ym = month.strip() or date.today().strftime("%Y-%m")
        with engine.connect() as c:
            stats = c.execute(text("""
              SELECT team_member,
                COUNT(*) AS tasks,
                COUNT(*) FILTER(WHERE status='DONE') AS done,
                COUNT(*) FILTER(WHERE status<>'DONE') AS open
              FROM pi_team_daily_tasks_v1230
              WHERE TO_CHAR(task_date,'YYYY-MM')=:ym
              GROUP BY team_member ORDER BY team_member
            """), {"ym":ym}).mappings().all()
            journals = c.execute(text("""
              SELECT team_member,COUNT(*) AS days_reported,
                COUNT(*) FILTER(WHERE COALESCE(morning_plan,'')<>'') AS morning_days,
                COUNT(*) FILTER(WHERE COALESCE(evening_summary,'')<>'') AS evening_days
              FROM pi_team_daily_journal_v1230
              WHERE TO_CHAR(task_date,'YYYY-MM')=:ym
              GROUP BY team_member
            """), {"ym":ym}).mappings().all()
        jm = {x["team_member"]:dict(x) for x in journals}
        rows = ""
        for x in stats:
            j = jm.get(x["team_member"],{})
            completion = round((x["done"] or 0)*100/max(x["tasks"] or 1,1))
            rows += f"""<tr><td>{_esc(x['team_member'])}</td><td>{x['tasks']}</td><td>{x['done']}</td><td>{x['open']}</td>
            <td>{completion}%</td><td>{j.get('days_reported',0)}</td><td>{j.get('morning_days',0)}</td><td>{j.get('evening_days',0)}</td></tr>"""
        body = f"""<h1>Monthly Team Review</h1>
        <div class='card'><form class='inline'><label>Month</label><input type='month' name='month' value='{html.escape(ym,quote=True)}'><button class='btn'>Open Review</button></form></div>
        <div class='card'><p>Use this page at month end to review execution discipline, not only activity volume. Check task completion plus whether each member consistently starts with a plan and closes the day with a written report.</p></div>
        <div class='card tablebox'><table><tr><th>Team Member</th><th>Tasks</th><th>Done</th><th>Open</th><th>Completion</th><th>Days Reported</th><th>Morning Plans</th><th>Evening Reports</th></tr>{rows}</table></div>"""
        return HTMLResponse(_shell(core, req, "Monthly Team Review", body))

    @app.get("/alliance/primary/manual", response_class=HTMLResponse)
    def team_manual(req: Request):
        _role(core, req)
        body = """<h1>Alliance App · Simple Team Manual</h1>
        <div class='card'><h3>Your daily sequence</h3><p><b>1. My Day:</b> write your morning plan and today's tasks.<br>
        <b>2. Requirements:</b> open new requirements from WhatsApp/manual/master sources. Verify genuine demand first.<br>
        <b>3. Run Match:</b> only verified requirements can run Smart Matcher.<br>
        <b>4. Availability:</b> call owner/broker for the best-ranked matched properties. Do not waste time verifying the full database randomly.<br>
        <b>5. Matcher Review:</b> approve only suitable properties whose availability has been checked.<br>
        <b>6. Follow-ups:</b> assign responsibility, next call, inspection or site visit.<br>
        <b>7. End My Day:</b> write what you actually completed and what remains.</p></div>
        <div class='grid'>
        <div class='card'><h3>Dashboard</h3><p>Starting point. Shows operational counts and shortcuts.</p></div>
        <div class='card'><h3>Requirements</h3><p>One work queue for all canonical requirements. Work No. helps the team refer to a requirement quickly.</p></div>
        <div class='card'><h3>Properties</h3><p>Master inventory. Property evidence/contact is internal. Availability must be verified before client sharing.</p></div>
        <div class='card'><h3>Smart Matcher</h3><p>Ranks properties against a verified requirement using transaction, location, area, use, floor and availability signals.</p></div>
        <div class='card'><h3>Availability</h3><p>Human confirmation layer. Mark Available or Unavailable after a real check.</p></div>
        <div class='card'><h3>Follow-ups</h3><p>Assigned work and scheduled next actions.</p></div>
        <div class='card'><h3>My Day</h3><p>Every member's daily plan, tasks, results and evening report.</p></div>
        <div class='card'><h3>Monthly Review</h3><p>Management review of task completion and daily reporting discipline.</p></div>
        </div>
        <div class='card'><h3>Non-negotiable client safety</h3><p>Never send owner/broker contact numbers to clients. Verify availability first, approve the match, then use the client-safe draft.</p></div>"""
        return HTMLResponse(_shell(core, req, "Team Manual", body))

    return {
        "status":"REGISTERED",
        "version":VERSION,
        "requirements":"/alliance/primary/requirements",
        "tasks":"/alliance/primary/tasks",
        "monthly":"/alliance/primary/tasks/monthly",
        "manual":"/alliance/primary/manual",
        "master_requirement_mutation":False,
        "gold_mutation":False,
        "matcher_logic_changed":False
    }
