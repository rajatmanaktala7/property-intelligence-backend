from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from fastapi import Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION = "12.3.4-RESTORED-DASHBOARD-STAFF-DAY-PLAN"
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
*{box-sizing:border-box}body{margin:0;background:#f5f7fb;color:#132238;font-family:Inter,Arial,sans-serif}
.top{background:#102a43;color:white;padding:22px 26px;display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap}
.top h1{margin:0 0 5px;font-size:25px}.top p{margin:0;color:#d8e5ef}.top .badge{align-self:center;background:#067647;padding:10px 14px;border-radius:10px;font-weight:800}
.wrap{max-width:1500px;margin:auto;padding:22px}.section{margin:0 0 24px}.section h2{font-size:19px;margin:0 0 12px}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(185px,1fr));gap:12px}
.metric{background:white;border:1px solid #e1e7ef;border-radius:13px;padding:15px;min-height:120px;box-shadow:0 2px 7px rgba(16,42,67,.04)}
.metric-label{font-size:13px;color:#64748b;font-weight:700}.metric-value{font-size:30px;font-weight:900;margin:6px 0}
.metric-sub{font-size:12px;color:#667085;min-height:28px}.metric-link{display:inline-block;margin-top:8px;text-decoration:none;font-weight:700;color:#175cd3}
.metric.warn{border-color:#f3c589}.metric.goodbox{border-color:#9edbb9}
.two{display:grid;grid-template-columns:2fr 1fr;gap:16px}@media(max-width:950px){.two{grid-template-columns:1fr}}
.panel{background:white;border:1px solid #e1e7ef;border-radius:13px;padding:17px}
.step{display:grid;grid-template-columns:42px 1fr auto;gap:12px;align-items:center;padding:13px 0;border-bottom:1px solid #edf1f5}
.step:last-child{border-bottom:0}.stepno{width:34px;height:34px;border-radius:50%;background:#e9f2ff;color:#175cd3;font-weight:900;display:grid;place-items:center}
.step p{margin:4px 0 0;color:#667085;font-size:13px}.btn{background:#102a43;color:white;text-decoration:none;border:0;border-radius:8px;padding:9px 11px;font-size:13px;white-space:nowrap;cursor:pointer}
.quickgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}
.quick{background:white;border:1px solid #e1e7ef;border-radius:11px;padding:14px;text-decoration:none;color:#132238}
.quick b{display:block;margin-bottom:4px}.quick span{font-size:12px;color:#667085}
.healthrow{display:flex;justify-content:space-between;border-bottom:1px solid #edf1f5;padding:9px 0;font-size:13px}.healthrow:last-child{border:0}
.good{color:#067647}.bad{color:#b42318}.rule{background:#fff7e6;border:1px solid #f1d49b;border-radius:11px;padding:13px;margin-top:12px;font-size:13px}
.footer{font-size:12px;color:#667085;margin-top:18px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:9px;border-bottom:1px solid #edf1f5;text-align:left;vertical-align:top}
th{background:#f8fafc}textarea,input,select{width:100%;padding:9px;border:1px solid #cfd8e3;border-radius:8px;margin:5px 0}
.formgrid{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media(max-width:800px){.formgrid{grid-template-columns:1fr}}
.pill{display:inline-block;padding:4px 8px;border-radius:999px;font-size:11px;font-weight:800}.okpill{background:#ecfdf3;color:#067647}.warnpill{background:#fff6ed;color:#b54708}
"""

def _dashboard(core, req):
    _role(core, req)
    e = _engine(core)
    _ensure_staff_tables(e)
    c = _counts(e)
    checks, healthy = _health(e, c)
    health_html = "".join(
        f"<div class='healthrow'><span>{html.escape(k)}</span><b class='{'good' if v else 'bad'}'>{'READY' if v else 'CHECK'}</b></div>"
        for k,v in checks.items()
    )
    status = "WORKABLE" if healthy else "ATTENTION NEEDED"
    now = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

    workflow = (
        _step(1, "Capture", "Add a property or requirement. Keep original evidence and contact details.", "/property-manual", "Add Property") +
        _step(2, "Verify Requirement", "Only human-verified requirements are allowed to run the matcher.", "/alliance/primary/requirements", "Open Requirements") +
        _step(3, "Run Smart Matcher", "Use transaction, location, area range, use, floor and availability.", "/alliance/primary/requirements", "Run From Requirement") +
        _step(4, "Verify Property Availability", "Call the owner/broker before client sharing. Gold extraction is not live availability.", "/alliance/primary/availability", "Verify Availability") +
        _step(5, "Approve Match", "Approve only suitable properties. Assignment starts after an approved match.", "/alliance/primary/matcher", "Open Matcher") +
        _step(6, "Assign & Follow Up", "Assign the approved opportunity to a team member and schedule follow-up.", "/alliance/primary/followups", "Follow-ups")
    )

    quick = """
    <a class='quick' href='/alliance/primary/requirements'><b>Requirements</b><span>Verify and run matcher</span></a>
    <a class='quick' href='/alliance/primary/matcher'><b>Smart Matcher</b><span>Review ranked properties</span></a>
    <a class='quick' href='/alliance/primary/availability'><b>Availability</b><span>Verify before sending</span></a>
    <a class='quick' href='/alliance/source/magazine'><b>Magazine Inventory</b><span>Workable governed records</span></a>
    <a class='quick' href='/alliance/primary/properties'><b>Properties</b><span>Master property inventory</span></a>
    <a class='quick' href='/alliance/primary/followups'><b>Follow-ups</b><span>Team action queue</span></a>
    <a class='quick' href='/alliance/primary/day-plan'><b>Daily Day Plan</b><span>Yogesh · Priya · Zoya</span></a>
    <a class='quick' href='/alliance/primary/staff-review'><b>Staff Review</b><span>Review daily work by staff name</span></a>
    <a class='quick' href='/alliance/primary/monthly-review'><b>Monthly Review</b><span>Monthly staff performance</span></a>
    <a class='quick' href='/property-manual'><b>Add Property</b><span>Manual property entry</span></a>
    """

    staff_metrics = []
    for name in STAFF:
        s = _staff_today(e, name)
        state = f"{s['done']}/{s['total']} done · {s['open']} pending"
        report = ("Plan ✓" if s["morning"] else "Plan pending") + " · " + ("Report ✓" if s["evening"] else "Report pending")
        staff_metrics.append(_card(name, state, report, f"/alliance/primary/day-plan?staff={name.replace(' ','%20')}", "goodbox" if s["morning"] and s["evening"] else "warn"))

    return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Alliance Team Command Centre</title><style>{_base_css()}</style></head><body>
<div class='top'>
 <div><h1>Alliance CRE · Team Command Centre</h1><p>Requirement → Smart Match → Availability Verification → Approval → Assignment → Follow-up</p></div>
 <div class='badge'>{status}</div>
</div>
<div class='wrap'>
 <div class='section'>
  <h2>Today at a glance</h2>
  <div class='metrics'>
   {_card("Verified Requirements", c["verified_requirements"], "Ready to run Smart Matcher", "/alliance/primary/requirements", "goodbox")}
   {_card("Verify First", c["unverified_requirements"], "Requirements still needing human verification", "/alliance/primary/requirements", "warn")}
   {_card("Verified Available Properties", c["verified_available"], "Safe candidates after current availability check", "/alliance/primary/availability", "goodbox")}
   {_card("Availability Unknown", c["availability_unknown"], "Must be checked before client sharing", "/alliance/primary/availability", "warn")}
   {_card("Matches Ready", c["matches_ready"], "Matcher output waiting for review", "/alliance/primary/matcher")}
   {_card("Approved Matches", c["matches_approved"], "Eligible for assignment", "/alliance/primary/matcher", "goodbox")}
   {_card("Scheduled Follow-ups", c["followups"], "Active team follow-up queue", "/alliance/primary/followups")}
   {_card("Assigned Opportunities", c["assigned"], "Current team-owned work", "/alliance/primary/followups")}
  </div>
 </div>

 <div class='section'>
  <h2>Team Day Plan</h2>
  <div class='metrics'>{''.join(staff_metrics)}</div>
 </div>

 <div class='section two'>
  <div class='panel'><h2>Team workflow</h2>{workflow}
   <div class='rule'><b>Client-safety rule:</b> Never send owner/broker contact details to the client. Verify property availability first, approve the match, then prepare the client-safe option.</div>
  </div>
  <div class='panel'><h2>Database health</h2>{health_html}
   <div class='rule'><b>Magazine governance:</b><br>
   Workable: {c["mag_workable"]}<br>
   Gold / AI-safe: {c["mag_gold"]}<br>
   Matcher Magazine: {c["matcher_magazine"]}<br>
   Review queue: {c["mag_review"]}</div>
  </div>
 </div>

 <div class='section'><h2>Quick access</h2><div class='quickgrid'>{quick}</div></div>
 <div class='footer'>Dashboard version {VERSION} · Generated {now}. Previous Command Centre interface restored; staff day-plan data is stored separately without changing Master/Gold/matcher logic.</div>
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
