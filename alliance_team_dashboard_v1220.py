from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

VERSION = "12.3.2-ALL-IN-ONE-TEAM-DASHBOARD"
ROUTE = "/alliance/primary"

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

def _table_names(e):
    try:
        with e.connect() as c:
            return [str(x) for x in c.execute(text("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema=current_schema() AND table_type='BASE TABLE'
                ORDER BY table_name
            """)).scalars().all()]
    except Exception:
        return []

def _requirement_source_counts(e):
    counts = {"MASTER":0,"WHATSAPP":0,"MANUAL":0,"MAGAZINE":0,"NEWSPAPER":0,"OTHER":0}
    counts["MASTER"] = int(_scalar(e, "SELECT COUNT(*) FROM pi_master_requirements_v711"))

    exclude_tokens = (
        "match","workflow","action","audit","review","repair","task","journal",
        "score","acceptance","test","exam","certification"
    )
    for table in _table_names(e):
        lo = table.lower()
        if "requirement" not in lo:
            continue
        if table in {"pi_master_requirements_v711","pi_requirement_work_no_v1230"}:
            continue
        if any(tok in lo for tok in exclude_tokens):
            continue
        n = int(_scalar(e, f'SELECT COUNT(*) FROM "{table}"'))
        upper = table.upper()
        if "WHATSAPP" in upper or "WAI_" in upper or "WA_" in upper:
            counts["WHATSAPP"] += n
        elif "MANUAL" in upper:
            counts["MANUAL"] += n
        elif "MAGAZINE" in upper:
            counts["MAGAZINE"] += n
        elif "NEWSPAPER" in upper:
            counts["NEWSPAPER"] += n
        else:
            counts["OTHER"] += n
    counts["ALL_CAPTURED"] = sum(counts.values())
    return counts

def _counts(e, actor):
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
        SELECT COUNT(*) FROM pi_match_reviews_v730
        WHERE review_status='APPROVED'
    """))
    out["followups"] = int(_scalar(e, """
        SELECT COUNT(*) FROM pi_master_action_state_v730
        WHERE followup_status='SCHEDULED'
    """))
    out["assigned"] = int(_scalar(e, """
        SELECT COUNT(*) FROM pi_master_action_state_v730
        WHERE assigned_to IS NOT NULL AND assigned_to<>''
    """))

    # Daily task system
    out["today_tasks"] = int(_scalar(e, """
        SELECT COUNT(*) FROM pi_team_daily_tasks_v1230
        WHERE task_date=CURRENT_DATE AND team_member=:m
    """, {"m":actor}))
    out["today_done"] = int(_scalar(e, """
        SELECT COUNT(*) FROM pi_team_daily_tasks_v1230
        WHERE task_date=CURRENT_DATE AND team_member=:m AND status='DONE'
    """, {"m":actor}))
    out["today_open"] = max(0, out["today_tasks"] - out["today_done"])
    out["morning_plan"] = int(_scalar(e, """
        SELECT COUNT(*) FROM pi_team_daily_journal_v1230
        WHERE task_date=CURRENT_DATE AND team_member=:m AND COALESCE(morning_plan,'')<>''
    """, {"m":actor}))
    out["evening_report"] = int(_scalar(e, """
        SELECT COUNT(*) FROM pi_team_daily_journal_v1230
        WHERE task_date=CURRENT_DATE AND team_member=:m AND COALESCE(evening_summary,'')<>''
    """, {"m":actor}))
    out["month_tasks"] = int(_scalar(e, """
        SELECT COUNT(*) FROM pi_team_daily_tasks_v1230
        WHERE to_char(task_date,'YYYY-MM')=to_char(CURRENT_DATE,'YYYY-MM') AND team_member=:m
    """, {"m":actor}))
    out["month_done"] = int(_scalar(e, """
        SELECT COUNT(*) FROM pi_team_daily_tasks_v1230
        WHERE to_char(task_date,'YYYY-MM')=to_char(CURRENT_DATE,'YYYY-MM')
          AND team_member=:m AND status='DONE'
    """, {"m":actor}))
    out["month_pct"] = round((100*out["month_done"]/out["month_tasks"]),1) if out["month_tasks"] else 0

    # Magazine governance
    out["mag_workable"] = int(_scalar(e, "SELECT COUNT(*) FROM pi_magazine_workable_v12009")) if _exists(e,"pi_magazine_workable_v12009") else 0
    out["mag_gold"] = int(_scalar(e, "SELECT COUNT(*) FROM pi_magazine_golden_master_v12009")) if _exists(e,"pi_magazine_golden_master_v12009") else 0
    out["mag_review"] = int(_scalar(e, "SELECT COUNT(*) FROM pi_magazine_review_v12009")) if _exists(e,"pi_magazine_review_v12009") else 0
    if _exists(e, "pi_master_properties_matcher_v1210"):
        out["matcher_magazine"] = int(_scalar(e, """
            SELECT COUNT(*) FROM pi_master_properties_matcher_v1210
            WHERE source_version LIKE '12.1.%PRODUCTION%' OR source_type='MAGAZINE'
        """))
    else:
        out["matcher_magazine"] = 0

    out["requirement_sources"] = _requirement_source_counts(e)
    return out

def _health(e, c):
    checks = {
        "Master Property DB": _exists(e, "pi_master_properties_v711"),
        "Master Requirement DB": _exists(e, "pi_master_requirements_v711"),
        "Workflow DB": _exists(e, "pi_master_workflow_v720"),
        "Match Review DB": _exists(e, "pi_match_reviews_v730"),
        "Daily Task DB": _exists(e, "pi_team_daily_tasks_v1230"),
        "Daily Journal DB": _exists(e, "pi_team_daily_journal_v1230"),
        "Requirement Work No. DB": _exists(e, "pi_requirement_work_no_v1230"),
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

def _recent_requirements(e, limit=8):
    return _rows(e, """
        SELECT r.canonical_id,r.locality,r.city,r.transaction_type,r.area_sqft,
               r.sale_budget,r.rent_budget,r.phones,
               COALESCE(w.verification_status,'UNVERIFIED') verification_status
        FROM pi_master_requirements_v711 r
        LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=r.canonical_id
        ORDER BY r.canonical_id DESC
        LIMIT :n
    """, {"n":limit})

def _today_tasks(e, actor):
    return _rows(e, """
        SELECT id,task_text,status,outcome
        FROM pi_team_daily_tasks_v1230
        WHERE task_date=CURRENT_DATE AND team_member=:m
        ORDER BY id
        LIMIT 12
    """, {"m":actor})

def _dashboard(core, req):
    _role(core, req)
    actor = _actor(core, req)
    e = _engine(core)
    if e is None:
        return HTMLResponse("<h2>Alliance database engine unavailable.</h2>", status_code=503)

    c = _counts(e, actor)
    rs = c["requirement_sources"]
    checks, healthy = _health(e, c)
    status = "WORKABLE" if healthy else "ATTENTION NEEDED"
    now = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

    health_html = "".join(
        f"<div class='healthrow'><span>{html.escape(k)}</span><b class='{'good' if v else 'bad'}'>{'READY' if v else 'CHECK'}</b></div>"
        for k,v in checks.items()
    )

    workflow = (
        _step(1, "Start My Day", "Write the morning plan and today's task list.", "/alliance/primary/tasks", "My Day") +
        _step(2, "Work Requirements", "Use one queue for Master, WhatsApp, Manual, Magazine, Newspaper and other captured requirements.", "/alliance/primary/requirements", "Requirements") +
        _step(3, "Verify Requirement", "Human verification is mandatory before Smart Matcher.", "/alliance/primary/requirements?verification=NEEDS_VERIFICATION", "Verify Queue") +
        _step(4, "Run Smart Matcher", "Run only from a VERIFIED Master Requirement.", "/alliance/primary/matcher", "Matcher") +
        _step(5, "Verify Property Availability", "Call owner/broker for shortlisted properties before client sharing.", "/alliance/primary/availability", "Availability") +
        _step(6, "Approve & Assign", "Approve suitable matches and assign ownership.", "/alliance/primary/followups", "Follow-ups") +
        _step(7, "Close My Day", "Write what was completed and what remains.", "/alliance/primary/tasks", "Day Report")
    )

    source_cards = "".join([
        _card("All Captured Requirements", rs["ALL_CAPTURED"], "Master + source/staging requirement records", "/alliance/primary/requirements"),
        _card("Master Requirements", rs["MASTER"], "Canonical matcher-authority records", "/alliance/primary/requirements?source=MASTER"),
        _card("WhatsApp Requirements", rs["WHATSAPP"], "Captured from requirement/WhatsApp tables", "/alliance/primary/requirements?source=WHATSAPP"),
        _card("Manual Requirements", rs["MANUAL"], "Team-entered requirement sources", "/alliance/primary/requirements?source=MANUAL"),
        _card("Magazine Requirements", rs["MAGAZINE"], "Requirement records from magazine sources", "/alliance/primary/requirements?source=MAGAZINE"),
        _card("Newspaper Requirements", rs["NEWSPAPER"], "Requirement records from newspaper sources", "/alliance/primary/requirements?source=NEWSPAPER"),
        _card("Other Requirement Sources", rs["OTHER"], "Other discovered requirement tables", "/alliance/primary/requirements?source=OTHER"),
    ])

    recent_req_rows = []
    for r in _recent_requirements(e):
        cid = str(r.get("canonical_id") or "")
        status_txt = str(r.get("verification_status") or "UNVERIFIED")
        action = (
            f"<a class='mini good' href='/alliance/primary/matcher?requirement_id={html.escape(cid,quote=True)}'>Run Matcher</a>"
            if status_txt == "VERIFIED"
            else "<a class='mini warn' href='/alliance/primary/requirements?verification=NEEDS_VERIFICATION'>Verify First</a>"
        )
        recent_req_rows.append(
            f"<tr><td>{html.escape(cid)}</td><td>{html.escape(str(r.get('locality') or r.get('city') or ''))}</td>"
            f"<td>{html.escape(str(r.get('transaction_type') or ''))}</td><td>{html.escape(str(r.get('area_sqft') or ''))}</td>"
            f"<td>{html.escape(str(r.get('phones') or ''))}</td><td>{html.escape(status_txt)}</td><td>{action}</td></tr>"
        )

    task_rows = []
    for t in _today_tasks(e, actor):
        task_rows.append(
            f"<tr><td>{html.escape(str(t.get('task_text') or ''))}</td><td>{html.escape(str(t.get('status') or ''))}</td>"
            f"<td>{html.escape(str(t.get('outcome') or ''))}</td></tr>"
        )
    if not task_rows:
        task_rows.append("<tr><td colspan='3' class='muted'>No tasks added yet. Start My Day.</td></tr>")

    quick = """
    <a class='quick' href='/alliance/primary/requirements'><b>Unified Requirements</b><span>All requirement databases in one work queue</span></a>
    <a class='quick' href='/alliance/primary/tasks'><b>My Day</b><span>Morning plan, tasks and end-of-day report</span></a>
    <a class='quick' href='/alliance/primary/tasks/monthly'><b>Monthly Review</b><span>Team completion and reporting review</span></a>
    <a class='quick' href='/alliance/primary/manual'><b>Team Manual</b><span>How to use Alliance correctly</span></a>
    <a class='quick' href='/alliance/primary/matcher'><b>Smart Matcher</b><span>Review ranked property options</span></a>
    <a class='quick' href='/alliance/primary/availability'><b>Availability</b><span>Verify shortlisted stock before sharing</span></a>
    <a class='quick' href='/alliance/primary/properties'><b>Properties</b><span>Master property inventory</span></a>
    <a class='quick' href='/alliance/primary/followups'><b>Follow-ups</b><span>Assigned opportunities and next actions</span></a>
    <a class='quick' href='/requirements-workbench'><b>Add Requirement</b><span>Capture new demand</span></a>
    <a class='quick' href='/alliance/property-add/manual'><b>Add Property</b><span>Manual property entry</span></a>
    """

    html_doc = f"""<!doctype html>
<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Alliance Team Command Centre</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f5f7fb;color:#132238;font-family:Inter,Arial,sans-serif}}
.top{{background:#102a43;color:white;padding:22px 26px;display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap}}
.top h1{{margin:0 0 5px;font-size:25px}}.top p{{margin:0;color:#d8e5ef}}.top .badge{{align-self:center;background:{'#067647' if healthy else '#b54708'};padding:10px 14px;border-radius:10px;font-weight:800}}
.wrap{{max-width:1700px;margin:auto;padding:22px}}.section{{margin:0 0 24px}}.section h2{{font-size:19px;margin:0 0 12px}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}
.metric{{background:white;border:1px solid #e1e7ef;border-radius:13px;padding:15px;min-height:120px;box-shadow:0 2px 7px rgba(16,42,67,.04)}}
.metric-label{{font-size:13px;color:#64748b;font-weight:700}}.metric-value{{font-size:30px;font-weight:900;margin:6px 0}}
.metric-sub{{font-size:12px;color:#667085;min-height:28px}}.metric-link{{display:inline-block;margin-top:8px;text-decoration:none;font-weight:700;color:#175cd3}}
.metric.warn{{border-color:#f3c589}}.metric.goodbox{{border-color:#9edbb9}}
.two{{display:grid;grid-template-columns:2fr 1fr;gap:16px}}.half{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
@media(max-width:950px){{.two,.half{{grid-template-columns:1fr}}}}
.panel{{background:white;border:1px solid #e1e7ef;border-radius:13px;padding:17px}}
.step{{display:grid;grid-template-columns:42px 1fr auto;gap:12px;align-items:center;padding:13px 0;border-bottom:1px solid #edf1f5}}
.step:last-child{{border-bottom:0}}.stepno{{width:34px;height:34px;border-radius:50%;background:#e9f2ff;color:#175cd3;font-weight:900;display:grid;place-items:center}}
.step p{{margin:4px 0 0;color:#667085;font-size:13px}}.btn,.mini{{background:#102a43;color:white;text-decoration:none;border-radius:8px;padding:9px 11px;font-size:13px;white-space:nowrap;display:inline-block}}
.mini.good{{background:#067647}}.mini.warn{{background:#b54708}}
.quickgrid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}}
.quick{{background:white;border:1px solid #e1e7ef;border-radius:11px;padding:14px;text-decoration:none;color:#132238}}
.quick b{{display:block;margin-bottom:4px}}.quick span{{font-size:12px;color:#667085}}
.healthrow{{display:flex;justify-content:space-between;border-bottom:1px solid #edf1f5;padding:9px 0;font-size:13px}}.healthrow:last-child{{border:0}}
.good{{color:#067647}}.bad{{color:#b42318}}.rule{{background:#fff7e6;border:1px solid #f1d49b;border-radius:11px;padding:13px;margin-top:12px;font-size:13px}}
.tablebox{{overflow:auto;max-height:380px}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{padding:8px;border-bottom:1px solid #edf1f5;text-align:left;vertical-align:top}}th{{background:#f8fafc;position:sticky;top:0}}
.muted{{color:#667085}}.footer{{font-size:12px;color:#667085;margin-top:18px}}
</style></head>
<body>
<div class='top'>
 <div><h1>Alliance CRE · Team Command Centre</h1><p>One dashboard for Requirements · Matching · Availability · Tasks · Follow-up · Review</p></div>
 <div class='badge'>{status}</div>
</div>
<div class='wrap'>

 <div class='section'>
  <h2>My Day</h2>
  <div class='metrics'>
   {_card("Today's Tasks", c["today_tasks"], "Tasks assigned to you today", "/alliance/primary/tasks")}
   {_card("Completed Today", c["today_done"], "Tasks marked done", "/alliance/primary/tasks", "goodbox")}
   {_card("Open Today", c["today_open"], "Still to complete", "/alliance/primary/tasks", "warn")}
   {_card("Morning Plan", "DONE" if c["morning_plan"] else "PENDING", "Start the day with a written plan", "/alliance/primary/tasks", "goodbox" if c["morning_plan"] else "warn")}
   {_card("End-of-Day Report", "DONE" if c["evening_report"] else "PENDING", "Write what was completed", "/alliance/primary/tasks", "goodbox" if c["evening_report"] else "warn")}
   {_card("This Month", f'{c["month_pct"]}%', f'{c["month_done"]}/{c["month_tasks"]} tasks completed', "/alliance/primary/tasks/monthly")}
  </div>
 </div>

 <div class='section'>
  <h2>Requirement Intelligence · All Databases</h2>
  <div class='metrics'>{source_cards}</div>
 </div>

 <div class='section'>
  <h2>Deal Work Queue</h2>
  <div class='metrics'>
   {_card("Verified Requirements", c["verified_requirements"], "Ready for Smart Matcher", "/alliance/primary/requirements?verification=VERIFIED", "goodbox")}
   {_card("Verify First", c["unverified_requirements"], "Master requirements needing human verification", "/alliance/primary/requirements?verification=NEEDS_VERIFICATION", "warn")}
   {_card("Verified Available Properties", c["verified_available"], "Confirmed current stock", "/alliance/primary/availability", "goodbox")}
   {_card("Availability Unknown", c["availability_unknown"], "Verify only shortlisted properties", "/alliance/primary/availability", "warn")}
   {_card("Matches Ready", c["matches_ready"], "Matcher output waiting for review", "/alliance/primary/matcher")}
   {_card("Approved Matches", c["matches_approved"], "Eligible for assignment/client-safe drafting", "/alliance/primary/matcher", "goodbox")}
   {_card("Scheduled Follow-ups", c["followups"], "Active follow-up queue", "/alliance/primary/followups")}
   {_card("Assigned Opportunities", c["assigned"], "Team-owned active work", "/alliance/primary/followups")}
  </div>
 </div>

 <div class='section half'>
  <div class='panel'>
   <h2>Today's Task List</h2>
   <div class='tablebox'><table><tr><th>Task</th><th>Status</th><th>Outcome</th></tr>{''.join(task_rows)}</table></div>
   <p><a class='btn' href='/alliance/primary/tasks'>Open My Day</a> <a class='btn' href='/alliance/primary/tasks/monthly'>Monthly Review</a></p>
  </div>
  <div class='panel'>
   <h2>Recent Master Requirements</h2>
   <div class='tablebox'><table><tr><th>ID</th><th>Location</th><th>Deal</th><th>Sq Ft</th><th>Contact</th><th>Status</th><th>Action</th></tr>{''.join(recent_req_rows)}</table></div>
   <p><a class='btn' href='/alliance/primary/requirements'>Open Full Unified Requirements</a></p>
  </div>
 </div>

 <div class='section two'>
  <div class='panel'><h2>Team Operating Flow</h2>{workflow}
   <div class='rule'><b>Client-safety rule:</b> Never send owner/broker contacts to the client. Requirement must be verified, property availability checked, match approved, then client-safe option prepared.</div>
  </div>
  <div class='panel'><h2>System & Database Health</h2>{health_html}
   <div class='rule'><b>Magazine governance</b><br>
   Workable: {c["mag_workable"]}<br>
   Gold / AI-safe: {c["mag_gold"]}<br>
   Matcher Magazine: {c["matcher_magazine"]}<br>
   Review queue: {c["mag_review"]}</div>
  </div>
 </div>

 <div class='section'><h2>Everything the Team Needs</h2><div class='quickgrid'>{quick}</div></div>

 <div class='footer'>Dashboard version {VERSION} · Generated {now}. Dashboard reads operational counts and does not mutate Master, Gold or matcher logic.</div>
</div></body></html>"""
    return HTMLResponse(html_doc, headers={"Cache-Control":"no-store"})

def register(core):
    app = _app(core)
    e = _engine(core)
    if app is None or e is None:
        raise RuntimeError("12.3.2 dashboard requires app + engine")

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

    # Put this route first so later duplicate command-centre routes cannot win.
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
        "all_in_one_dashboard":True,
        "master_mutation":False,
        "matcher_logic_changed":False,
    }
