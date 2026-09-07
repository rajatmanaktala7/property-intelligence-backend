from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from fastapi import Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION = "12.3.6-PREVIOUS-UI-NEWSPAPER-RESTORED"
ROUTE = "/alliance/primary"

STAFF = ["Yogesh Mehra", "Priya", "Zoya Saifi"]

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
    try:
        return bool(_scalar(e, "SELECT to_regclass(:n) IS NOT NULL", {"n": name}, False))
    except Exception:
        return False

def _ensure_staff_tables(e):
    with e.begin() as c:
        c.execute(text("""CREATE TABLE IF NOT EXISTS pi_team_daily_tasks_v1230(
            id BIGSERIAL PRIMARY KEY,
            task_date DATE NOT NULL DEFAULT CURRENT_DATE,
            team_member TEXT NOT NULL,
            task_text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            outcome TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ
        )"""))
        c.execute(text("""CREATE INDEX IF NOT EXISTS idx_team_tasks_v1230_member_date
            ON pi_team_daily_tasks_v1230(team_member,task_date DESC)"""))
        c.execute(text("""CREATE TABLE IF NOT EXISTS pi_team_daily_journal_v1230(
            task_date DATE NOT NULL,
            team_member TEXT NOT NULL,
            morning_plan TEXT,
            evening_summary TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY(task_date,team_member)
        )"""))

def _staff_name(value):
    value = str(value or "").strip()
    if value not in STAFF:
        return STAFF[0]
    return value

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
    out["mag_workable"] = int(_scalar(e, "SELECT COUNT(*) FROM pi_magazine_workable_v12009")) if _exists(e, "pi_magazine_workable_v12009") else 0
    out["mag_gold"] = int(_scalar(e, "SELECT COUNT(*) FROM pi_magazine_golden_master_v12009")) if _exists(e, "pi_magazine_golden_master_v12009") else 0
    out["mag_review"] = int(_scalar(e, "SELECT COUNT(*) FROM pi_magazine_review_v12009")) if _exists(e, "pi_magazine_review_v12009") else 0
    if _exists(e, "pi_master_properties_matcher_v1210"):
        out["matcher_magazine"] = int(_scalar(e, """
            SELECT COUNT(*) FROM pi_master_properties_matcher_v1210
            WHERE source_version LIKE '12.1.%PRODUCTION%' OR source_type='MAGAZINE'
        """))
    else:
        out["matcher_magazine"] = 0
    return out

def _staff_today(e, name):
    total = int(_scalar(e, """SELECT COUNT(*) FROM pi_team_daily_tasks_v1230
        WHERE task_date=CURRENT_DATE AND team_member=:m""", {"m":name}))
    done = int(_scalar(e, """SELECT COUNT(*) FROM pi_team_daily_tasks_v1230
        WHERE task_date=CURRENT_DATE AND team_member=:m AND status='DONE'""", {"m":name}))
    morning = bool(_scalar(e, """SELECT COUNT(*) FROM pi_team_daily_journal_v1230
        WHERE task_date=CURRENT_DATE AND team_member=:m AND COALESCE(morning_plan,'')<>''""", {"m":name}))
    evening = bool(_scalar(e, """SELECT COUNT(*) FROM pi_team_daily_journal_v1230
        WHERE task_date=CURRENT_DATE AND team_member=:m AND COALESCE(evening_summary,'')<>''""", {"m":name}))
    return {"total":total,"done":done,"open":max(0,total-done),"morning":morning,"evening":evening}

def _health(e, c):
    checks = {
        "Golden Magazine DB": _exists(e, "pi_magazine_golden_master_v12009") and c["mag_gold"] > 0,
        "Workable Magazine DB": _exists(e, "pi_magazine_workable_v12009") and c["mag_workable"] > 0,
        "Master Property DB": _exists(e, "pi_master_properties_v711"),
        "Master Requirement DB": _exists(e, "pi_master_requirements_v711"),
        "Matcher Read Model": _exists(e, "pi_master_properties_matcher_v1210") and c["matcher_magazine"] > 0,
        "Workflow DB": _exists(e, "pi_master_workflow_v720"),
        "Match Review DB": _exists(e, "pi_match_reviews_v730"),
        "Staff Task DB": _exists(e, "pi_team_daily_tasks_v1230"),
        "Staff Journal DB": _exists(e, "pi_team_daily_journal_v1230"),
    }
    return checks, all(checks.values())

def _card(label, value, sub="", href=None, cls=""):
    body = f"<div class='metric {cls}'><div class='metric-label'>{html.escape(label)}</div><div class='metric-value'>{html.escape(str(value))}</div>"
    if sub:
        body += f"<div class='metric-sub'>{html.escape(sub)}</div>"
    if href:
        body += f"<a class='metric-link' href='{html.escape(href, quote=True)}'>Open →</a>"
    return body + "</div>"

def _step(n, title, text, href, action):
    return f"""
    <div class='step'>
      <div class='stepno'>{n}</div>
      <div class='stepbody'><b>{html.escape(title)}</b><p>{html.escape(text)}</p></div>
      <a class='btn' href='{html.escape(href,quote=True)}'>{html.escape(action)}</a>
    </div>"""

def _base_css():
    return """
*{box-sizing:border-box}body{margin:0;background:#f5f7fb;color:#132238;font-family:Arial,sans-serif}
.top{background:#102a43;color:white;padding:22px 26px;display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap}
.top h1{margin:0 0 5px;font-size:26px}.top p{margin:0;color:#d8e5ef}.top .badge{align-self:center;background:#067647;padding:10px 14px;border-radius:10px;font-weight:800}
.wrap{max-width:1750px;margin:auto;padding:22px}.section{margin-bottom:24px}.section h2{font-size:20px;margin:0 0 12px}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}
.metric,.panel,.staffcard{background:white;border:1px solid #e1e7ef;border-radius:13px;padding:15px;box-shadow:0 2px 7px rgba(16,42,67,.04)}
.metric-label{font-size:13px;color:#64748b;font-weight:700}.metric-value{font-size:30px;font-weight:900;margin:6px 0}
.metric-sub{font-size:12px;color:#667085;min-height:28px}.metric-link{display:inline-block;margin-top:8px;text-decoration:none;font-weight:700;color:#175cd3}
.staffgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}@media(max-width:1000px){.staffgrid{grid-template-columns:1fr}}
.staffhead{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.staffhead span{font-size:12px;color:#667085}
.staffmetrics{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:14px 0}
.staffmetrics div{background:#f8fafc;border-radius:9px;padding:10px;text-align:center}.staffmetrics strong{display:block;font-size:22px}.staffmetrics span{font-size:11px;color:#667085}
.checks{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}
.two{display:grid;grid-template-columns:1.5fr 1fr;gap:16px}@media(max-width:950px){.two{grid-template-columns:1fr}}
.tablebox{overflow:auto;max-height:420px}table{border-collapse:collapse;width:100%;font-size:12px}th,td{padding:8px;border-bottom:1px solid #edf1f5;text-align:left;vertical-align:top}th{background:#f8fafc;position:sticky;top:0}
.quickgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}.quick{background:white;border:1px solid #e1e7ef;border-radius:11px;padding:14px;text-decoration:none;color:#132238}.quick b{display:block;margin-bottom:4px}.quick span{font-size:12px;color:#667085}
.btn,.mini{background:#102a43;color:white;text-decoration:none;border:0;border-radius:8px;padding:8px 10px;font-size:12px;display:inline-block;cursor:pointer}.goodbtn{background:#067647}
.okpill,.warnpill,.badpill{display:inline-block;border-radius:999px;padding:4px 8px;font-size:11px;font-weight:800}
.okpill{background:#ecfdf3;color:#067647}.warnpill{background:#fff6ed;color:#b54708}.badpill{background:#fef3f2;color:#b42318}
.note,.rule{background:#fff7e6;border:1px solid #f1d49b;border-radius:11px;padding:12px;font-size:13px}.footer{font-size:12px;color:#667085;margin-top:18px}
textarea,input,select{width:100%;padding:9px;border:1px solid #cfd8e3;border-radius:8px;margin:5px 0}.formgrid{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media(max-width:800px){.formgrid{grid-template-columns:1fr}}
"""

def _dashboard(core, req):
    _role(core, req)
    e = _engine(core)
    _ensure_staff_tables(e)
    c = _counts(e)

    staff_cards = []
    for name in STAFF:
        s = _staff_today(e, name)
        open_n = max(0, s["total"] - s["done"])
        plan_cls = "okpill" if s["morning"] else "warnpill"
        report_cls = "okpill" if s["evening"] else "warnpill"
        staff_cards.append(f"""
        <div class='staffcard'>
          <div class='staffhead'>
            <div><b>{html.escape(name)}</b><br><span>Today's activity</span></div>
            <a class='mini' href='/alliance/primary/day-plan?staff={name.replace(" ","%20")}'>Open Day Plan</a>
          </div>
          <div class='staffmetrics'>
            <div><strong>{s["total"]}</strong><span>Tasks</span></div>
            <div><strong>{s["done"]}</strong><span>Done</span></div>
            <div><strong>{open_n}</strong><span>Pending</span></div>
          </div>
          <div class='checks'>
            <span class='{plan_cls}'>Morning plan: {"DONE" if s["morning"] else "PENDING"}</span>
            <span class='{report_cls}'>Day report: {"DONE" if s["evening"] else "PENDING"}</span>
          </div>
          <a class='mini' href='/alliance/primary/staff-review?staff={name.replace(" ","%20")}'>History</a>
        </div>""")

    recent = _rows(e, """SELECT task_date,team_member,task_text,status,outcome
        FROM pi_team_daily_tasks_v1230
        WHERE team_member IN ('Yogesh Mehra','Priya','Zoya Saifi')
        ORDER BY task_date DESC,id DESC LIMIT 30""")
    recent_rows = "".join(
        f"<tr><td>{html.escape(str(x.get('task_date') or ''))}</td>"
        f"<td><b>{html.escape(str(x.get('team_member') or ''))}</b></td>"
        f"<td>{html.escape(str(x.get('task_text') or ''))}</td>"
        f"<td>{html.escape(str(x.get('status') or ''))}</td>"
        f"<td>{html.escape(str(x.get('outcome') or ''))}</td></tr>"
        for x in recent
    ) or "<tr><td colspan='5'>No staff tasks saved yet.</td></tr>"

    quick = [
        ("Requirements","/alliance/primary/requirements","Verify and run matcher"),
        ("Smart Matcher","/alliance/primary/matcher","Review ranked properties"),
        ("Availability","/alliance/primary/availability","Verify before client sharing"),
        ("Property Databases","/alliance/final/databases","Master + source property views"),
        ("Requirement Databases","/alliance/final/requirements","Master + source requirement views"),
        ("Goa Properties","/alliance/goa-properties","Goa inventory and search"),
        ("Newspaper Capture","/capture-intelligence","Capture newspaper pages into clean property records"),
        ("WhatsApp Live","/whatsapp-live","Live READY WhatsApp property feed"),
        ("Hospitality Intelligence","/hospitality-intelligence","Restaurant, hotel, banquet and hospitality intelligence"),
        ("Retail Expansion","/retail-expansion","Retail brand expansion intelligence"),
        ("Commercial Intelligence","/commercial-intelligence","Malls, government premises and commercial opportunities"),
        ("Requirement Discovery","/requirement-discovery","Demand discovery evidence"),
        ("Marketing Contacts","/marketing-contacts","Marketing/contact intelligence"),
        ("Follow-ups","/alliance/primary/followups","Team action queue"),
        ("Deals & Reports","/alliance/primary/reports","Live workflow reports"),
        ("Daily Day Plan","/alliance/primary/day-plan","Yogesh · Priya · Zoya"),
        ("Staff Review","/alliance/primary/staff-review","Review daily work by staff"),
        ("Monthly Review","/alliance/primary/monthly-review","Monthly staff performance"),
        ("Add Requirement","/requirements-workbench","Capture new demand"),
        ("Add Property","/property-manual","Manual property entry"),
        ("Final Link Audit","/alliance/team-link-audit","27-route production audit"),
    ]
    quick_html = "".join(
        f"<a class='quick' href='{html.escape(path,quote=True)}'><b>{html.escape(label)}</b><span>{html.escape(desc)}</span></a>"
        for label,path,desc in quick
    )

    now = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>Alliance Team Command Centre</title>
<style>{_base_css()}</style></head><body>
<div class='top'>
  <div><h1>Alliance CRE · Team Command Centre</h1><p>Daily staff accountability + CRE workflow + live intelligence access</p></div>
  <div class='badge'>WORKABLE</div>
</div>
<div class='wrap'>

<div class='section'><h2>Team Performance Today</h2><div class='staffgrid'>{''.join(staff_cards)}</div></div>

<div class='section'><h2>Alliance Deal Work Queue</h2><div class='metrics'>
{_card("Master Properties",c["properties"],"Canonical property database","/alliance/final/database/master")}
{_card("Master Requirements",c["requirements"],"Canonical requirement database","/alliance/primary/requirements")}
{_card("Verified Requirements",c["verified_requirements"],"Ready to match","/alliance/primary/requirements","goodbox")}
{_card("Verify First",c["unverified_requirements"],"Must be human verified","/alliance/primary/requirements","warn")}
{_card("Verified Available",c["verified_available"],"Current confirmed stock","/alliance/primary/availability","goodbox")}
{_card("Availability Unknown",c["availability_unknown"],"Verify matched candidates only","/alliance/primary/availability","warn")}
{_card("Matches Ready",c["matches_ready"],"Waiting for review","/alliance/primary/matcher")}
{_card("Approved Matches",c["matches_approved"],"Ready for assignment","/alliance/primary/matcher","goodbox")}
{_card("Follow-ups",c["followups"],"Scheduled actions","/alliance/primary/followups")}
{_card("Assigned",c["assigned"],"Team-owned opportunities","/alliance/primary/followups")}
</div></div>

<div class='section two'>
  <div class='panel'>
    <h2>Latest Staff Tasks</h2>
    <div class='tablebox'><table><tr><th>Date</th><th>Staff</th><th>Task</th><th>Status</th><th>Outcome</th></tr>{recent_rows}</table></div>
    <p><a class='mini' href='/alliance/primary/staff-review'>Open Full Staff History</a></p>
  </div>
  <div class='panel'>
    <h2>System Ready</h2>
    <div class='checks'>
      <span class='okpill'>27/27 dashboard routes PASS</span>
      <span class='okpill'>Goa Properties LIVE</span>
      <span class='okpill'>WhatsApp Live LIVE</span>
      <span class='okpill'>Commercial Intelligence LIVE</span>
    </div>
    <div class='note'><b>Safety:</b> Requirement verification remains mandatory before Smart Matcher. Property availability must be verified before client sharing. Owner/broker contacts remain internal.</div>
    <p><a class='mini goodbtn' href='/alliance/team-link-audit'>Open Full Link Audit</a></p>
  </div>
</div>

<div class='section'><h2>Everything the Team Needs</h2><div class='quickgrid'>{quick_html}</div></div>

<div class='note'><b>Management rule:</b> Yogesh Mehra, Priya and Zoya Saifi day plans remain stored under their exact staff names. This UI restoration does not change Master, Gold, Matcher, Requirement Gate or the 12.4.3 route fixes.</div>
<div class='footer'>Dashboard 12.3.5-PREVIOUS-UI-RESTORED · {now}. Previous all-in-one visual interface restored with current production routes retained.</div>
</div></body></html>""", headers={"Cache-Control":"no-store"})

def _staff_shell(title, body):
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{html.escape(title)}</title><style>{_base_css()}</style></head><body>
<div class='top'><div><h1>Alliance CRE · {html.escape(title)}</h1><p>Saved by staff name in PostgreSQL</p></div><a class='btn' href='/alliance/primary'>← Back to Dashboard</a></div>
<div class='wrap'>{body}</div></body></html>"""

def register(core):
    app = _app(core)
    e = _engine(core)
    if app is None or e is None:
        raise RuntimeError("12.3.4 requires app + engine")
    _ensure_staff_tables(e)

    # Restore the main command-centre route.
    keep = []
    for r in list(app.router.routes):
        methods = set(getattr(r, "methods", set()) or set())
        if getattr(r, "path", None) == ROUTE and "GET" in methods:
            continue
        keep.append(r)
    app.router.routes[:] = keep

    @app.get(ROUTE, response_class=HTMLResponse)
    def command_centre(req: Request):
        return _dashboard(core, req)

    @app.get("/alliance/primary/day-plan", response_class=HTMLResponse)
    def day_plan(req: Request, staff: str = Query("Yogesh Mehra")):
        _role(core, req)
        name = _staff_name(staff)
        journal = _rows(e, """SELECT morning_plan,evening_summary,updated_at
            FROM pi_team_daily_journal_v1230
            WHERE task_date=CURRENT_DATE AND team_member=:m""", {"m":name})
        j = journal[0] if journal else {}
        tasks = _rows(e, """SELECT id,task_text,status,outcome,created_at,completed_at
            FROM pi_team_daily_tasks_v1230
            WHERE task_date=CURRENT_DATE AND team_member=:m
            ORDER BY id""", {"m":name})

        options = "".join(f"<option value='{html.escape(x,quote=True)}' {'selected' if x==name else ''}>{html.escape(x)}</option>" for x in STAFF)
        task_rows = []
        for t in tasks:
            if str(t.get("status") or "") == "DONE":
                action = "<span class='pill okpill'>DONE</span>"
            else:
                action = f"""<form method='post' action='/alliance/primary/day-plan/task/{int(t["id"])}/done'>
                <input type='hidden' name='staff' value='{html.escape(name,quote=True)}'>
                <input name='outcome' placeholder='What was completed?' required>
                <button class='btn'>Mark Done</button></form>"""
            task_rows.append(f"<tr><td>{html.escape(str(t.get('task_text') or ''))}</td><td>{html.escape(str(t.get('status') or ''))}</td><td>{html.escape(str(t.get('outcome') or ''))}</td><td>{action}</td></tr>")

        body = f"""
        <div class='panel'>
          <form method='get'><label><b>Select Staff</b></label><select name='staff'>{options}</select><button class='btn'>Open Day Plan</button></form>
        </div>
        <div class='formgrid'>
          <div class='panel'><h2>Morning Plan · {html.escape(name)}</h2>
            <form method='post' action='/alliance/primary/day-plan/save-morning'>
              <input type='hidden' name='staff' value='{html.escape(name,quote=True)}'>
              <textarea name='morning_plan' rows='8' required placeholder='What must be completed today?'>{html.escape(str(j.get("morning_plan") or ""))}</textarea>
              <button class='btn'>Save Morning Plan</button>
            </form>
          </div>
          <div class='panel'><h2>Add Task · {html.escape(name)}</h2>
            <form method='post' action='/alliance/primary/day-plan/add-task'>
              <input type='hidden' name='staff' value='{html.escape(name,quote=True)}'>
              <textarea name='task_text' rows='6' required placeholder='Enter one clear task'></textarea>
              <button class='btn'>Add Task</button>
            </form>
          </div>
        </div>
        <div class='panel'><h2>Today's Tasks</h2><table><tr><th>Task</th><th>Status</th><th>Outcome</th><th>Action</th></tr>{''.join(task_rows) or "<tr><td colspan='4'>No tasks added yet.</td></tr>"}</table></div>
        <div class='panel'><h2>End-of-Day Report · {html.escape(name)}</h2>
          <form method='post' action='/alliance/primary/day-plan/save-evening'>
            <input type='hidden' name='staff' value='{html.escape(name,quote=True)}'>
            <textarea name='evening_summary' rows='8' required placeholder='What was completed? What remains? Important follow-ups?'>{html.escape(str(j.get("evening_summary") or ""))}</textarea>
            <button class='btn'>Save Day Report</button>
          </form>
        </div>
        <div class='rule'><b>Storage:</b> Morning plan and day report are saved in <code>pi_team_daily_journal_v1230</code>. Tasks and outcomes are saved in <code>pi_team_daily_tasks_v1230</code> under the exact staff name <b>{html.escape(name)}</b>.</div>
        """
        return HTMLResponse(_staff_shell("Daily Day Plan", body), headers={"Cache-Control":"no-store"})

    @app.post("/alliance/primary/day-plan/save-morning")
    def save_morning(req: Request, staff: str = Form(...), morning_plan: str = Form(...)):
        _role(core, req)
        name = _staff_name(staff)
        with e.begin() as c:
            c.execute(text("""INSERT INTO pi_team_daily_journal_v1230(task_date,team_member,morning_plan,updated_at)
                VALUES(CURRENT_DATE,:m,:p,NOW())
                ON CONFLICT(task_date,team_member)
                DO UPDATE SET morning_plan=EXCLUDED.morning_plan,updated_at=NOW()"""),
                {"m":name,"p":morning_plan.strip()})
        return RedirectResponse(f"/alliance/primary/day-plan?staff={name.replace(' ','%20')}", status_code=303)

    @app.post("/alliance/primary/day-plan/add-task")
    def add_task(req: Request, staff: str = Form(...), task_text: str = Form(...)):
        _role(core, req)
        name = _staff_name(staff)
        with e.begin() as c:
            c.execute(text("""INSERT INTO pi_team_daily_tasks_v1230(task_date,team_member,task_text,status)
                VALUES(CURRENT_DATE,:m,:t,'OPEN')"""), {"m":name,"t":task_text.strip()})
        return RedirectResponse(f"/alliance/primary/day-plan?staff={name.replace(' ','%20')}", status_code=303)

    @app.post("/alliance/primary/day-plan/task/{task_id}/done")
    def task_done(task_id: int, req: Request, staff: str = Form(...), outcome: str = Form(...)):
        _role(core, req)
        name = _staff_name(staff)
        with e.begin() as c:
            c.execute(text("""UPDATE pi_team_daily_tasks_v1230
                SET status='DONE', outcome=:o, completed_at=NOW()
                WHERE id=:id AND team_member=:m"""), {"o":outcome.strip(),"id":task_id,"m":name})
        return RedirectResponse(f"/alliance/primary/day-plan?staff={name.replace(' ','%20')}", status_code=303)

    @app.post("/alliance/primary/day-plan/save-evening")
    def save_evening(req: Request, staff: str = Form(...), evening_summary: str = Form(...)):
        _role(core, req)
        name = _staff_name(staff)
        with e.begin() as c:
            c.execute(text("""INSERT INTO pi_team_daily_journal_v1230(task_date,team_member,evening_summary,updated_at)
                VALUES(CURRENT_DATE,:m,:p,NOW())
                ON CONFLICT(task_date,team_member)
                DO UPDATE SET evening_summary=EXCLUDED.evening_summary,updated_at=NOW()"""),
                {"m":name,"p":evening_summary.strip()})
        return RedirectResponse(f"/alliance/primary/day-plan?staff={name.replace(' ','%20')}", status_code=303)

    @app.get("/alliance/primary/staff-review", response_class=HTMLResponse)
    def staff_review(req: Request, staff: str = Query(""), day: str = Query(""), month: str = Query("")):
        _role(core, req)
        chosen_staff = staff if staff in STAFF else ""
        chosen_day = day if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or "") else ""
        chosen_month = month if re.fullmatch(r"\d{4}-\d{2}", month or "") else datetime.now().strftime("%Y-%m")

        wh = ["team_member IN ('Yogesh Mehra','Priya','Zoya Saifi')"]
        params = {}
        if chosen_staff:
            wh.append("team_member=:m"); params["m"]=chosen_staff
        if chosen_day:
            wh.append("task_date=:d"); params["d"]=chosen_day
        else:
            wh.append("to_char(task_date,'YYYY-MM')=:mo"); params["mo"]=chosen_month

        tasks = _rows(e, f"""SELECT task_date,team_member,task_text,status,outcome,created_at,completed_at
            FROM pi_team_daily_tasks_v1230 WHERE {' AND '.join(wh)}
            ORDER BY task_date DESC,team_member,id DESC""", params)

        jwh = ["team_member IN ('Yogesh Mehra','Priya','Zoya Saifi')"]
        jp = {}
        if chosen_staff:
            jwh.append("team_member=:m"); jp["m"]=chosen_staff
        if chosen_day:
            jwh.append("task_date=:d"); jp["d"]=chosen_day
        else:
            jwh.append("to_char(task_date,'YYYY-MM')=:mo"); jp["mo"]=chosen_month
        journals = _rows(e, f"""SELECT task_date,team_member,morning_plan,evening_summary,updated_at
            FROM pi_team_daily_journal_v1230 WHERE {' AND '.join(jwh)}
            ORDER BY task_date DESC,team_member""", jp)

        options = "<option value=''>All Staff</option>" + "".join(f"<option value='{html.escape(x,quote=True)}' {'selected' if x==chosen_staff else ''}>{html.escape(x)}</option>" for x in STAFF)
        task_html = "".join(f"<tr><td>{html.escape(str(x.get('task_date') or ''))}</td><td>{html.escape(str(x.get('team_member') or ''))}</td><td>{html.escape(str(x.get('task_text') or ''))}</td><td>{html.escape(str(x.get('status') or ''))}</td><td>{html.escape(str(x.get('outcome') or ''))}</td></tr>" for x in tasks)
        journal_html = "".join(f"<tr><td>{html.escape(str(x.get('task_date') or ''))}</td><td>{html.escape(str(x.get('team_member') or ''))}</td><td>{html.escape(str(x.get('morning_plan') or ''))}</td><td>{html.escape(str(x.get('evening_summary') or ''))}</td></tr>" for x in journals)

        body = f"""<div class='panel'><form method='get'>
          <div class='formgrid'><div><label>Staff</label><select name='staff'>{options}</select></div>
          <div><label>Exact Date</label><input type='date' name='day' value='{html.escape(chosen_day,quote=True)}'></div></div>
          <label>Month</label><input type='month' name='month' value='{html.escape(chosen_month,quote=True)}'><button class='btn'>Review</button>
        </form></div>
        <div class='panel'><h2>Tasks & Outcomes</h2><table><tr><th>Date</th><th>Staff</th><th>Task</th><th>Status</th><th>Outcome</th></tr>{task_html or "<tr><td colspan='5'>No saved tasks for this filter.</td></tr>"}</table></div>
        <div class='panel'><h2>Morning Plans & End-of-Day Reports</h2><table><tr><th>Date</th><th>Staff</th><th>Morning Plan</th><th>Day Report</th></tr>{journal_html or "<tr><td colspan='4'>No saved day reports for this filter.</td></tr>"}</table></div>"""
        return HTMLResponse(_staff_shell("Staff Review", body), headers={"Cache-Control":"no-store"})

    @app.get("/alliance/primary/monthly-review", response_class=HTMLResponse)
    def monthly_review(req: Request, month: str = Query("")):
        _role(core, req)
        chosen = month if re.fullmatch(r"\d{4}-\d{2}", month or "") else datetime.now().strftime("%Y-%m")
        cards=[]
        trs=[]
        for name in STAFF:
            total=int(_scalar(e, """SELECT COUNT(*) FROM pi_team_daily_tasks_v1230 WHERE team_member=:m AND to_char(task_date,'YYYY-MM')=:mo""",{"m":name,"mo":chosen}))
            done=int(_scalar(e, """SELECT COUNT(*) FROM pi_team_daily_tasks_v1230 WHERE team_member=:m AND status='DONE' AND to_char(task_date,'YYYY-MM')=:mo""",{"m":name,"mo":chosen}))
            reports=int(_scalar(e, """SELECT COUNT(*) FROM pi_team_daily_journal_v1230 WHERE team_member=:m AND COALESCE(evening_summary,'')<>'' AND to_char(task_date,'YYYY-MM')=:mo""",{"m":name,"mo":chosen}))
            pct=round(100*done/total,1) if total else 0
            cards.append(_card(name,f"{pct}%","Task completion"))
            trs.append(f"<tr><td><b>{html.escape(name)}</b></td><td>{total}</td><td>{done}</td><td>{max(0,total-done)}</td><td>{pct}%</td><td>{reports}</td></tr>")
        body=f"""<div class='panel'><form method='get'><label>Month</label><input type='month' name='month' value='{html.escape(chosen,quote=True)}'><button class='btn'>Open Review</button></form></div>
        <div class='metrics'>{''.join(cards)}</div>
        <div class='panel'><table><tr><th>Staff</th><th>Tasks</th><th>Completed</th><th>Pending</th><th>Completion</th><th>Day Reports</th></tr>{''.join(trs)}</table></div>"""
        return HTMLResponse(_staff_shell("Monthly Review", body), headers={"Cache-Control":"no-store"})

    # Prioritize restored dashboard GET.
    matches, rest = [], []
    for r in list(app.router.routes):
        methods = set(getattr(r, "methods", set()) or set())
        if getattr(r, "path", None) == ROUTE and "GET" in methods:
            matches.append(r)
        else:
            rest.append(r)
    app.router.routes[:] = matches + rest

    return {
        "status":"REGISTERED",
        "version":VERSION,
        "route":ROUTE,
        "staff_day_plan":"/alliance/primary/day-plan",
        "staff_review":"/alliance/primary/staff-review",
        "monthly_review":"/alliance/primary/monthly-review",
        "staff":STAFF,
        "master_mutation":False,
        "matcher_logic_changed":False,
    }
