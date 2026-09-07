from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from fastapi import Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION = "12.3.3-STAFF-REVIEW-LINK-AUDIT-DASHBOARD"
ROUTE = "/alliance/primary"

STAFF = [
    ("yogesh-mehra", "Yogesh Mehra"),
    ("priya", "Priya"),
    ("zoya-saifi", "Zoya Saifi"),
]
STAFF_MAP = dict(STAFF)
STAFF_NAME_TO_SLUG = {name: slug for slug, name in STAFF}

def _app(core):
    return getattr(core, "app", None) or core

def _engine(core):
    return getattr(core, "engine", None)

def _role(core, req):
    fn = getattr(core, "need_login", None)
    return fn(req) if fn else "team"

def _scalar(e, sql, params=None, default=0):
    try:
        with e.connect() as c:
            v = c.execute(text(sql), params or {}).scalar()
        return default if v is None else v
    except Exception:
        return default

def _rows(e, sql, params=None):
    try:
        with e.connect() as c:
            return [dict(x) for x in c.execute(text(sql), params or {}).mappings().all()]
    except Exception:
        return []

def _exists(e, name):
    return bool(_scalar(e, "SELECT to_regclass(:n) IS NOT NULL", {"n": name}, False))

def _ensure_task_tables(e):
    ddls = [
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
    with e.begin() as c:
        for ddl in ddls:
            c.execute(text(ddl))

def _route_exists(app, path, method="GET"):
    base = str(path or "").split("?",1)[0]
    for r in getattr(app.router, "routes", []):
        if getattr(r, "path", None) != base:
            continue
        methods = set(getattr(r, "methods", set()) or set())
        if method.upper() in methods:
            return True
    return False

def _best_route(app, candidates, method="GET"):
    for p in candidates:
        if _route_exists(app, p, method):
            return p
    return candidates[-1]

def _audit_links(app):
    add_requirement = _best_route(app, ["/requirements-workbench", "/alliance/primary/requirements"])
    add_property = _best_route(app, ["/property-manual", "/alliance/property-add/manual", "/alliance/primary/properties"])
    links = [
        ("Command Centre", "/alliance/primary"),
        ("Requirements", "/alliance/primary/requirements"),
        ("Matcher", "/alliance/primary/matcher"),
        ("Availability", "/alliance/primary/availability"),
        ("Properties", "/alliance/primary/properties"),
        ("Follow-ups", "/alliance/primary/followups"),
        ("My Day", "/alliance/primary/tasks"),
        ("Monthly Review", "/alliance/primary/tasks/monthly"),
        ("Team Manual", "/alliance/primary/manual"),
        ("Add Requirement", add_requirement),
        ("Add Property", add_property),
        ("Staff Review", "/alliance/primary/team-review"),
    ]
    return [(label, path, _route_exists(app, path)) for label, path in links]

def _counts(e):
    out = {}
    out["properties"] = int(_scalar(e, "SELECT COUNT(*) FROM pi_master_properties_v711"))
    out["requirements"] = int(_scalar(e, "SELECT COUNT(*) FROM pi_master_requirements_v711"))
    out["verified_requirements"] = int(_scalar(e, """
        SELECT COUNT(*) FROM pi_master_requirements_v711 r
        JOIN pi_master_workflow_v720 w ON w.canonical_id=r.canonical_id
        WHERE w.entity_type='REQUIREMENT' AND w.verification_status='VERIFIED'
    """))
    out["unverified_requirements"] = max(0, out["requirements"] - out["verified_requirements"])
    out["verified_available"] = int(_scalar(e, """
        SELECT COUNT(*) FROM pi_master_properties_v711 p
        JOIN pi_master_workflow_v720 w ON w.canonical_id=p.canonical_id
        WHERE w.entity_type='PROPERTY'
          AND w.verification_status='VERIFIED'
          AND w.availability_status='AVAILABLE'
    """))
    out["availability_unknown"] = int(_scalar(e, """
        SELECT COUNT(*) FROM pi_master_properties_v711 p
        LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=p.canonical_id
        WHERE COALESCE(w.availability_status,'UNKNOWN')='UNKNOWN'
    """))
    out["matches_ready"] = int(_scalar(e, """
        SELECT COUNT(*) FROM pi_master_matches_v720
        WHERE COALESCE(status,'READY_FOR_REVIEW')='READY_FOR_REVIEW'
    """))
    out["matches_approved"] = int(_scalar(e, """
        SELECT COUNT(*) FROM pi_match_reviews_v730 WHERE review_status='APPROVED'
    """))
    out["followups"] = int(_scalar(e, """
        SELECT COUNT(*) FROM pi_master_action_state_v730 WHERE followup_status='SCHEDULED'
    """))
    out["assigned"] = int(_scalar(e, """
        SELECT COUNT(*) FROM pi_master_action_state_v730
        WHERE assigned_to IS NOT NULL AND assigned_to<>''
    """))
    return out

def _staff_today(e, name):
    return {
        "tasks": int(_scalar(e, """SELECT COUNT(*) FROM pi_team_daily_tasks_v1230
                    WHERE task_date=CURRENT_DATE AND team_member=:m""", {"m":name})),
        "done": int(_scalar(e, """SELECT COUNT(*) FROM pi_team_daily_tasks_v1230
                    WHERE task_date=CURRENT_DATE AND team_member=:m AND status='DONE'""", {"m":name})),
        "morning": bool(_scalar(e, """SELECT COUNT(*) FROM pi_team_daily_journal_v1230
                    WHERE task_date=CURRENT_DATE AND team_member=:m
                      AND COALESCE(morning_plan,'')<>''""", {"m":name})),
        "evening": bool(_scalar(e, """SELECT COUNT(*) FROM pi_team_daily_journal_v1230
                    WHERE task_date=CURRENT_DATE AND team_member=:m
                      AND COALESCE(evening_summary,'')<>''""", {"m":name})),
    }

def _staff_month(e, name):
    total = int(_scalar(e, """SELECT COUNT(*) FROM pi_team_daily_tasks_v1230
              WHERE to_char(task_date,'YYYY-MM')=to_char(CURRENT_DATE,'YYYY-MM')
                AND team_member=:m""", {"m":name}))
    done = int(_scalar(e, """SELECT COUNT(*) FROM pi_team_daily_tasks_v1230
              WHERE to_char(task_date,'YYYY-MM')=to_char(CURRENT_DATE,'YYYY-MM')
                AND team_member=:m AND status='DONE'""", {"m":name}))
    days = int(_scalar(e, """SELECT COUNT(*) FROM pi_team_daily_journal_v1230
              WHERE to_char(task_date,'YYYY-MM')=to_char(CURRENT_DATE,'YYYY-MM')
                AND team_member=:m""", {"m":name}))
    return {"total":total, "done":done, "pct":round(100*done/total,1) if total else 0, "days":days}

def _staff_card(e, slug, name):
    s = _staff_today(e, name)
    open_n = max(0, s["tasks"] - s["done"])
    return f"""
    <div class='staffcard'>
      <div class='staffhead'><div><b>{html.escape(name)}</b><br><span>Today's activity</span></div>
      <a class='mini' href='/alliance/primary/team-review?staff={html.escape(slug,quote=True)}'>History</a></div>
      <div class='staffmetrics'>
        <div><strong>{s["tasks"]}</strong><span>Tasks</span></div>
        <div><strong>{s["done"]}</strong><span>Done</span></div>
        <div><strong>{open_n}</strong><span>Pending</span></div>
      </div>
      <div class='checks'>
        <span class='{"okpill" if s["morning"] else "warnpill"}'>Morning plan: {"DONE" if s["morning"] else "PENDING"}</span>
        <span class='{"okpill" if s["evening"] else "warnpill"}'>Day report: {"DONE" if s["evening"] else "PENDING"}</span>
      </div>
      <details><summary>Enter today's work</summary>
        <form method='post' action='/alliance/primary/staff/{slug}/morning'>
          <textarea name='morning_plan' rows='3' placeholder='Morning to-do / priorities'></textarea>
          <button class='mini'>Save Morning Plan</button>
        </form>
        <form method='post' action='/alliance/primary/staff/{slug}/task'>
          <textarea name='task_text' rows='2' placeholder='Add one task' required></textarea>
          <button class='mini goodbtn'>Add Task</button>
        </form>
        <form method='post' action='/alliance/primary/staff/{slug}/evening'>
          <textarea name='evening_summary' rows='3' placeholder='What was completed today? What remains?'></textarea>
          <button class='mini'>Save End-of-Day Report</button>
        </form>
      </details>
    </div>"""

def _recent_staff_tasks(e):
    return _rows(e, """
        SELECT id,task_date,team_member,task_text,status,outcome,completed_at
        FROM pi_team_daily_tasks_v1230
        WHERE team_member IN ('Yogesh Mehra','Priya','Zoya Saifi')
        ORDER BY task_date DESC,id DESC
        LIMIT 30
    """)

def _card(label, value, sub="", href=None, cls=""):
    b = f"<div class='metric {cls}'><div class='metric-label'>{html.escape(label)}</div><div class='metric-value'>{html.escape(str(value))}</div>"
    if sub:
        b += f"<div class='metric-sub'>{html.escape(sub)}</div>"
    if href:
        b += f"<a class='metric-link' href='{html.escape(href,quote=True)}'>Open →</a>"
    return b + "</div>"

def _dashboard(core, req):
    _role(core, req)
    e = _engine(core)
    app = _app(core)
    _ensure_task_tables(e)
    c = _counts(e)

    links = _audit_links(app)
    broken = [(l,p) for l,p,ok in links if not ok]
    working = [(l,p) for l,p,ok in links if ok]

    audit_rows = "".join(
        f"<tr><td>{html.escape(label)}</td><td><code>{html.escape(path)}</code></td>"
        f"<td><span class='{'okpill' if ok else 'badpill'}'>{'WORKING' if ok else 'NOT REGISTERED'}</span></td></tr>"
        for label,path,ok in links
    )

    staff_cards = "".join(_staff_card(e, slug, name) for slug,name in STAFF)

    recent_rows = []
    for t in _recent_staff_tasks(e):
        status = str(t.get("status") or "")
        complete = ""
        if status != "DONE":
            complete = f"""<form class='inline' method='post' action='/alliance/primary/staff-task/{t["id"]}/done'>
              <input name='outcome' placeholder='Outcome / work done' required>
              <button class='mini goodbtn'>Mark Done</button></form>"""
        else:
            complete = "<span class='okpill'>DONE</span>"
        recent_rows.append(
            f"<tr><td>{html.escape(str(t.get('task_date') or ''))}</td><td><b>{html.escape(str(t.get('team_member') or ''))}</b></td>"
            f"<td>{html.escape(str(t.get('task_text') or ''))}</td><td>{html.escape(status)}</td>"
            f"<td>{html.escape(str(t.get('outcome') or ''))}</td><td>{complete}</td></tr>"
        )

    add_req = _best_route(app, ["/requirements-workbench", "/alliance/primary/requirements"])
    add_prop = _best_route(app, ["/property-manual", "/alliance/property-add/manual", "/alliance/primary/properties"])

    quick = [
        ("Unified Requirements","/alliance/primary/requirements","All requirement records and verification queue"),
        ("Smart Matcher","/alliance/primary/matcher","Run/review verified requirement matches"),
        ("Availability","/alliance/primary/availability","Verify shortlisted property availability"),
        ("Properties","/alliance/primary/properties","Master property inventory"),
        ("Follow-ups","/alliance/primary/followups","Assigned team actions"),
        ("Staff Review","/alliance/primary/team-review","Daily and monthly staff history"),
        ("Team Manual","/alliance/primary/manual","Operating instructions"),
        ("Add Requirement",add_req,"Capture requirement safely"),
        ("Add Property",add_prop,"Working property-entry route"),
    ]
    quick_html = "".join(
        f"<a class='quick' href='{html.escape(path,quote=True)}'><b>{html.escape(label)}</b><span>{html.escape(desc)}</span></a>"
        for label,path,desc in quick
    )

    status = "WORKABLE" if not broken else f"{len(broken)} LINK CHECK(S)"
    now = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>Alliance Team Command Centre</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f5f7fb;color:#132238;font-family:Arial,sans-serif}}
.top{{background:#102a43;color:white;padding:22px 26px;display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap}}
.top h1{{margin:0 0 5px;font-size:26px}}.top p{{margin:0;color:#d8e5ef}}.badge{{align-self:center;background:#067647;padding:10px 14px;border-radius:10px;font-weight:800}}
.wrap{{max-width:1750px;margin:auto;padding:22px}}.section{{margin-bottom:24px}}h2{{font-size:20px;margin:0 0 12px}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}}
.metric,.panel,.staffcard{{background:white;border:1px solid #e1e7ef;border-radius:13px;padding:15px;box-shadow:0 2px 7px rgba(16,42,67,.04)}}
.metric-label{{font-size:13px;color:#64748b;font-weight:700}}.metric-value{{font-size:30px;font-weight:900;margin:6px 0}}
.metric-sub{{font-size:12px;color:#667085;min-height:28px}}.metric-link{{display:inline-block;margin-top:8px;text-decoration:none;font-weight:700;color:#175cd3}}
.staffgrid{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}}@media(max-width:1000px){{.staffgrid{{grid-template-columns:1fr}}}}
.staffhead{{display:flex;justify-content:space-between;gap:12px}}.staffhead span{{font-size:12px;color:#667085}}
.staffmetrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:14px 0}}
.staffmetrics div{{background:#f8fafc;border-radius:9px;padding:10px;text-align:center}}.staffmetrics strong{{display:block;font-size:22px}}.staffmetrics span{{font-size:11px;color:#667085}}
.checks{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}}details{{border-top:1px solid #edf1f5;padding-top:10px}}
textarea,input{{width:100%;padding:8px;border:1px solid #cfd8e3;border-radius:7px;margin:5px 0}}button{{border:0;cursor:pointer}}
.mini{{background:#102a43;color:white;text-decoration:none;border-radius:8px;padding:8px 10px;font-size:12px;display:inline-block}}.goodbtn{{background:#067647}}
.okpill,.warnpill,.badpill{{display:inline-block;border-radius:999px;padding:4px 8px;font-size:11px;font-weight:800}}
.okpill{{background:#ecfdf3;color:#067647}}.warnpill{{background:#fff6ed;color:#b54708}}.badpill{{background:#fef3f2;color:#b42318}}
.two{{display:grid;grid-template-columns:1.5fr 1fr;gap:16px}}@media(max-width:950px){{.two{{grid-template-columns:1fr}}}}
.tablebox{{overflow:auto;max-height:420px}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{padding:8px;border-bottom:1px solid #edf1f5;text-align:left;vertical-align:top}}th{{background:#f8fafc;position:sticky;top:0}}
.quickgrid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}}.quick{{background:white;border:1px solid #e1e7ef;border-radius:11px;padding:14px;text-decoration:none;color:#132238}}.quick b{{display:block;margin-bottom:4px}}.quick span{{font-size:12px;color:#667085}}
.inline{{display:flex;gap:6px;align-items:center}}.inline input{{min-width:180px}}code{{font-size:11px}}.note{{background:#fff7e6;border:1px solid #f1d49b;border-radius:11px;padding:12px}}.footer{{font-size:12px;color:#667085;margin-top:18px}}
</style></head><body>
<div class='top'><div><h1>Alliance CRE · Team Command Centre</h1><p>Daily staff accountability + CRE workflow + live dashboard link audit</p></div><div class='badge'>{html.escape(status)}</div></div>
<div class='wrap'>

<div class='section'><h2>Team Performance Today</h2><div class='staffgrid'>{staff_cards}</div></div>

<div class='section'><h2>Alliance Deal Work Queue</h2><div class='metrics'>
{_card("Master Requirements",c["requirements"],"Canonical requirement database","/alliance/primary/requirements")}
{_card("Verified Requirements",c["verified_requirements"],"Ready to match","/alliance/primary/requirements?verification=VERIFIED","good")}
{_card("Verify First",c["unverified_requirements"],"Must be human verified","/alliance/primary/requirements?verification=NEEDS_VERIFICATION")}
{_card("Verified Available",c["verified_available"],"Current confirmed stock","/alliance/primary/availability")}
{_card("Availability Unknown",c["availability_unknown"],"Verify matched candidates only","/alliance/primary/availability")}
{_card("Matches Ready",c["matches_ready"],"Waiting for review","/alliance/primary/matcher")}
{_card("Approved Matches",c["matches_approved"],"Ready for assignment","/alliance/primary/matcher")}
{_card("Follow-ups",c["followups"],"Scheduled actions","/alliance/primary/followups")}
{_card("Assigned",c["assigned"],"Team-owned opportunities","/alliance/primary/followups")}
</div></div>

<div class='section two'>
<div class='panel'><h2>Latest Staff Tasks</h2><div class='tablebox'><table><tr><th>Date</th><th>Staff</th><th>Task</th><th>Status</th><th>Outcome</th><th>Action</th></tr>{''.join(recent_rows) or "<tr><td colspan='6'>No staff tasks saved yet.</td></tr>"}</table></div><p><a class='mini' href='/alliance/primary/team-review'>Open Full Staff History</a></p></div>
<div class='panel'><h2>Dashboard Link Audit</h2><div class='tablebox'><table><tr><th>Link</th><th>Route</th><th>Status</th></tr>{audit_rows}</table></div>
<div class='note'><b>Automatic fallback:</b> Add Property now prefers <code>/property-manual</code>. Add Requirement uses the workbench only if that route is actually registered; otherwise it safely opens Requirements.</div></div>
</div>

<div class='section'><h2>Everything the Team Needs</h2><div class='quickgrid'>{quick_html}</div></div>
<div class='note'><b>Management rule:</b> staff work is stored permanently in PostgreSQL under the exact names Yogesh Mehra, Priya and Zoya Saifi. You can review each person's daily or monthly history from Staff Review.</div>
<div class='footer'>Dashboard {VERSION} · {now}. This dashboard does not alter Master Requirement, Gold, or matcher logic.</div>
</div></body></html>""", headers={"Cache-Control":"no-store"})

def _staff_name(slug):
    if slug not in STAFF_MAP:
        raise ValueError("Unknown staff member")
    return STAFF_MAP[slug]

def register(core):
    app = _app(core)
    e = _engine(core)
    if app is None or e is None:
        raise RuntimeError("12.3.3 requires app + engine")
    _ensure_task_tables(e)

    # Replace command centre GET only.
    keep=[]
    for r in list(app.router.routes):
        methods=set(getattr(r,"methods",set()) or set())
        if getattr(r,"path",None)==ROUTE and "GET" in methods:
            continue
        keep.append(r)
    app.router.routes[:] = keep

    @app.get(ROUTE, response_class=HTMLResponse)
    def command_centre(req: Request):
        return _dashboard(core, req)

    @app.post("/alliance/primary/staff/{slug}/morning")
    def staff_morning(slug: str, req: Request, morning_plan: str = Form("")):
        _role(core, req)
        name = _staff_name(slug)
        with e.begin() as c:
            c.execute(text("""INSERT INTO pi_team_daily_journal_v1230(task_date,team_member,morning_plan,updated_at)
                VALUES(CURRENT_DATE,:m,:p,NOW())
                ON CONFLICT(task_date,team_member) DO UPDATE SET morning_plan=EXCLUDED.morning_plan,updated_at=NOW()"""),
                {"m":name,"p":morning_plan.strip()})
        return RedirectResponse(ROUTE, status_code=303)

    @app.post("/alliance/primary/staff/{slug}/evening")
    def staff_evening(slug: str, req: Request, evening_summary: str = Form("")):
        _role(core, req)
        name = _staff_name(slug)
        with e.begin() as c:
            c.execute(text("""INSERT INTO pi_team_daily_journal_v1230(task_date,team_member,evening_summary,updated_at)
                VALUES(CURRENT_DATE,:m,:p,NOW())
                ON CONFLICT(task_date,team_member) DO UPDATE SET evening_summary=EXCLUDED.evening_summary,updated_at=NOW()"""),
                {"m":name,"p":evening_summary.strip()})
        return RedirectResponse(ROUTE, status_code=303)

    @app.post("/alliance/primary/staff/{slug}/task")
    def staff_task(slug: str, req: Request, task_text: str = Form(...)):
        _role(core, req)
        name = _staff_name(slug)
        with e.begin() as c:
            c.execute(text("""INSERT INTO pi_team_daily_tasks_v1230(task_date,team_member,task_text,status)
                VALUES(CURRENT_DATE,:m,:t,'OPEN')"""), {"m":name,"t":task_text.strip()})
        return RedirectResponse(ROUTE, status_code=303)

    @app.post("/alliance/primary/staff-task/{task_id}/done")
    def staff_task_done(task_id: int, req: Request, outcome: str = Form("")):
        _role(core, req)
        with e.begin() as c:
            c.execute(text("""UPDATE pi_team_daily_tasks_v1230
                SET status='DONE',outcome=:o,completed_at=NOW()
                WHERE id=:id AND team_member IN ('Yogesh Mehra','Priya','Zoya Saifi')"""),
                {"o":outcome.strip(),"id":task_id})
        return RedirectResponse(ROUTE, status_code=303)

    @app.get("/alliance/primary/team-review", response_class=HTMLResponse)
    def team_review(req: Request, staff: str = Query(""), month: str = Query(""), day: str = Query("")):
        _role(core, req)
        chosen_month = month if re.fullmatch(r"\d{4}-\d{2}", month or "") else datetime.now().strftime("%Y-%m")
        chosen_day = day if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or "") else ""
        chosen_name = STAFF_MAP.get(staff, "")
        wh = ["team_member IN ('Yogesh Mehra','Priya','Zoya Saifi')"]
        params = {}
        if chosen_name:
            wh.append("team_member=:staff"); params["staff"]=chosen_name
        if chosen_day:
            wh.append("task_date=:day"); params["day"]=chosen_day
        else:
            wh.append("to_char(task_date,'YYYY-MM')=:month"); params["month"]=chosen_month
        tasks = _rows(e, f"""SELECT task_date,team_member,task_text,status,outcome,created_at,completed_at
            FROM pi_team_daily_tasks_v1230 WHERE {' AND '.join(wh)}
            ORDER BY task_date DESC,team_member,id DESC""", params)

        jwh = ["team_member IN ('Yogesh Mehra','Priya','Zoya Saifi')"]
        jparams = {}
        if chosen_name:
            jwh.append("team_member=:staff"); jparams["staff"]=chosen_name
        if chosen_day:
            jwh.append("task_date=:day"); jparams["day"]=chosen_day
        else:
            jwh.append("to_char(task_date,'YYYY-MM')=:month"); jparams["month"]=chosen_month
        journals = _rows(e, f"""SELECT task_date,team_member,morning_plan,evening_summary,updated_at
            FROM pi_team_daily_journal_v1230 WHERE {' AND '.join(jwh)}
            ORDER BY task_date DESC,team_member""", jparams)

        summaries = []
        for slug,name in STAFF:
            m = _staff_month(e,name)
            summaries.append(f"<div class='metric'><b>{html.escape(name)}</b><div style='font-size:28px;font-weight:900'>{m['pct']}%</div><small>{m['done']}/{m['total']} tasks done · {m['days']} reported days this month</small></div>")

        task_trs = "".join(f"<tr><td>{html.escape(str(x.get('task_date') or ''))}</td><td>{html.escape(str(x.get('team_member') or ''))}</td><td>{html.escape(str(x.get('task_text') or ''))}</td><td>{html.escape(str(x.get('status') or ''))}</td><td>{html.escape(str(x.get('outcome') or ''))}</td></tr>" for x in tasks)
        journal_trs = "".join(f"<tr><td>{html.escape(str(x.get('task_date') or ''))}</td><td>{html.escape(str(x.get('team_member') or ''))}</td><td>{html.escape(str(x.get('morning_plan') or ''))}</td><td>{html.escape(str(x.get('evening_summary') or ''))}</td></tr>" for x in journals)
        opts = "<option value=''>All Staff</option>" + "".join(f"<option value='{slug}' {'selected' if staff==slug else ''}>{html.escape(name)}</option>" for slug,name in STAFF)

        return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Alliance Staff Review</title>
<style>body{{font-family:Arial;background:#f5f7fb;color:#132238;margin:0}}.top{{background:#102a43;color:white;padding:18px}}.wrap{{max-width:1500px;margin:auto;padding:20px}}a{{color:#175cd3}}.metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}.metric,.panel{{background:white;border:1px solid #e1e7ef;border-radius:12px;padding:14px;margin-bottom:14px}}table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{padding:8px;border-bottom:1px solid #edf1f5;text-align:left;vertical-align:top}}input,select,button{{padding:8px;border:1px solid #cfd8e3;border-radius:7px}}button{{background:#102a43;color:white}}.filters{{display:flex;gap:8px;flex-wrap:wrap}}</style></head>
<body><div class='top'><b>Alliance CRE · Staff Activity Review</b></div><div class='wrap'><p><a href='/alliance/primary'>← Back to Dashboard</a></p>
<div class='metrics'>{''.join(summaries)}</div>
<div class='panel'><form class='filters'><select name='staff'>{opts}</select><input type='month' name='month' value='{html.escape(chosen_month,quote=True)}'><input type='date' name='day' value='{html.escape(chosen_day,quote=True)}'><button>Review</button></form></div>
<div class='panel'><h3>Tasks & Outcomes</h3><table><tr><th>Date</th><th>Staff</th><th>Task</th><th>Status</th><th>Outcome</th></tr>{task_trs or "<tr><td colspan='5'>No tasks found for this filter.</td></tr>"}</table></div>
<div class='panel'><h3>Morning Plan & End-of-Day Report</h3><table><tr><th>Date</th><th>Staff</th><th>Morning Plan</th><th>Evening Report</th></tr>{journal_trs or "<tr><td colspan='4'>No reports found for this filter.</td></tr>"}</table></div>
</div></body></html>""")

    # Prioritize dashboard route after all existing routes currently present.
    matches, rest = [], []
    for r in list(app.router.routes):
        methods=set(getattr(r,"methods",set()) or set())
        if getattr(r,"path",None)==ROUTE and "GET" in methods:
            matches.append(r)
        else:
            rest.append(r)
    app.router.routes[:] = matches + rest

    return {"status":"REGISTERED","version":VERSION,"staff":list(STAFF_MAP.values()),"link_audit":True,"master_mutation":False,"matcher_logic_changed":False}
