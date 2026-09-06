
from __future__ import annotations

import html
from datetime import datetime, timezone
from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

VERSION = "12.2.0-TEAM-OPERATING-DASHBOARD"
ROUTE = "/alliance/primary"

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

def _exists(e, name):
    try:
        return bool(_scalar(e, "SELECT to_regclass(:n) IS NOT NULL", {"n": name}, False))
    except Exception:
        return False

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
    if _exists(e, "pi_magazine_workable_v12009"):
        out["mag_workable"] = int(_scalar(e, "SELECT COUNT(*) FROM pi_magazine_workable_v12009"))
    else:
        out["mag_workable"] = 0
    if _exists(e, "pi_magazine_golden_master_v12009"):
        out["mag_gold"] = int(_scalar(e, "SELECT COUNT(*) FROM pi_magazine_golden_master_v12009"))
    else:
        out["mag_gold"] = 0
    if _exists(e, "pi_magazine_review_v12009"):
        out["mag_review"] = int(_scalar(e, "SELECT COUNT(*) FROM pi_magazine_review_v12009"))
    else:
        out["mag_review"] = 0
    if _exists(e, "pi_master_properties_matcher_v1210"):
        out["matcher_magazine"] = int(_scalar(e, """
            SELECT COUNT(*) FROM pi_master_properties_matcher_v1210
            WHERE source_version LIKE '12.1.%PRODUCTION%'
               OR source_type='MAGAZINE'
        """))
    else:
        out["matcher_magazine"] = 0
    return out

def _health(e, c):
    checks = {
        "Golden Magazine DB": _exists(e, "pi_magazine_golden_master_v12009") and c["mag_gold"] > 0,
        "Workable Magazine DB": _exists(e, "pi_magazine_workable_v12009") and c["mag_workable"] > 0,
        "Master Property DB": _exists(e, "pi_master_properties_v711"),
        "Master Requirement DB": _exists(e, "pi_master_requirements_v711"),
        "Matcher Read Model": _exists(e, "pi_master_properties_matcher_v1210") and c["matcher_magazine"] > 0,
        "Workflow DB": _exists(e, "pi_master_workflow_v720"),
        "Match Review DB": _exists(e, "pi_match_reviews_v730"),
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

def _dashboard(core, req):
    _role(core, req)
    e = _engine(core)
    if e is None:
        return HTMLResponse("<h2>Alliance database engine unavailable.</h2>", status_code=503)
    c = _counts(e)
    checks, healthy = _health(e, c)
    health_html = "".join(
        f"<div class='healthrow'><span>{html.escape(k)}</span><b class='{'good' if v else 'bad'}'>{'READY' if v else 'CHECK'}</b></div>"
        for k,v in checks.items()
    )
    status = "WORKABLE" if healthy else "ATTENTION NEEDED"
    now = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

    workflow = (
        _step(1, "Capture", "Add a property or requirement. Keep original evidence and contact details.", "/alliance/property-add/manual", "Add Property") +
        _step(2, "Verify Requirement", "Only human-verified requirements are allowed to run the matcher.", "/alliance/primary/requirements", "Open Requirements") +
        _step(3, "Run Smart Matcher", "12.1.1 uses transaction, location, area range, use, floor and availability.", "/alliance/primary/requirements", "Run From Requirement") +
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
    <a class='quick' href='/requirements-workbench'><b>Add Requirement</b><span>Capture new demand</span></a>
    <a class='quick' href='/alliance/property-add/manual'><b>Add Property</b><span>Manual property entry</span></a>
    """

    html_doc = f"""<!doctype html>
<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Alliance Team Command Centre</title>
<style>
*{{box-sizing:border-box}} body{{margin:0;background:#f5f7fb;color:#132238;font-family:Inter,Arial,sans-serif}}
.top{{background:#102a43;color:white;padding:22px 26px;display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap}}
.top h1{{margin:0 0 5px;font-size:25px}} .top p{{margin:0;color:#d8e5ef}} .top .badge{{align-self:center;background:{'#067647' if healthy else '#b54708'};padding:10px 14px;border-radius:10px;font-weight:800}}
.wrap{{max-width:1500px;margin:auto;padding:22px}} .section{{margin:0 0 24px}}
.section h2{{font-size:19px;margin:0 0 12px}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(185px,1fr));gap:12px}}
.metric{{background:white;border:1px solid #e1e7ef;border-radius:13px;padding:15px;min-height:120px;box-shadow:0 2px 7px rgba(16,42,67,.04)}}
.metric-label{{font-size:13px;color:#64748b;font-weight:700}} .metric-value{{font-size:30px;font-weight:900;margin:6px 0}}
.metric-sub{{font-size:12px;color:#667085;min-height:28px}} .metric-link{{display:inline-block;margin-top:8px;text-decoration:none;font-weight:700;color:#175cd3}}
.metric.warn{{border-color:#f3c589}} .metric.goodbox{{border-color:#9edbb9}}
.two{{display:grid;grid-template-columns:2fr 1fr;gap:16px}} @media(max-width:950px){{.two{{grid-template-columns:1fr}}}}
.panel{{background:white;border:1px solid #e1e7ef;border-radius:13px;padding:17px}}
.step{{display:grid;grid-template-columns:42px 1fr auto;gap:12px;align-items:center;padding:13px 0;border-bottom:1px solid #edf1f5}}
.step:last-child{{border-bottom:0}} .stepno{{width:34px;height:34px;border-radius:50%;background:#e9f2ff;color:#175cd3;font-weight:900;display:grid;place-items:center}}
.step p{{margin:4px 0 0;color:#667085;font-size:13px}} .btn{{background:#102a43;color:white;text-decoration:none;border-radius:8px;padding:9px 11px;font-size:13px;white-space:nowrap}}
.quickgrid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}}
.quick{{background:white;border:1px solid #e1e7ef;border-radius:11px;padding:14px;text-decoration:none;color:#132238}}
.quick b{{display:block;margin-bottom:4px}} .quick span{{font-size:12px;color:#667085}}
.healthrow{{display:flex;justify-content:space-between;border-bottom:1px solid #edf1f5;padding:9px 0;font-size:13px}} .healthrow:last-child{{border:0}}
.good{{color:#067647}} .bad{{color:#b42318}}
.rule{{background:#fff7e6;border:1px solid #f1d49b;border-radius:11px;padding:13px;margin-top:12px;font-size:13px}}
.footer{{font-size:12px;color:#667085;margin-top:18px}}
</style></head>
<body>
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

 <div class='section two'>
  <div class='panel'><h2>Team workflow</h2>{workflow}
   <div class='rule'><b>Client-safety rule:</b> Never send owner/broker contact details to the client. Verify property availability first, approve the match, then prepare the client-safe option.</div>
  </div>
  <div class='panel'><h2>Database health</h2>{health_html}
   <div class='rule'><b>Magazine governance:</b><br>
   Raw: {c["mag_workable"] + c["mag_review"] if c["mag_workable"] or c["mag_review"] else "-"}<br>
   Workable: {c["mag_workable"]}<br>
   Gold / AI-safe: {c["mag_gold"]}<br>
   Matcher Magazine: {c["matcher_magazine"]}<br>
   Review queue: {c["mag_review"]}</div>
  </div>
 </div>

 <div class='section'><h2>Quick access</h2><div class='quickgrid'>{quick}</div></div>

 <div class='footer'>Dashboard version {VERSION} · Generated {now}. This page does not modify raw evidence or bypass verification controls.</div>
</div></body></html>"""
    return HTMLResponse(html_doc, headers={"Cache-Control":"no-store"})

def register(core):
    app = _app(core)
    e = _engine(core)
    if app is None or e is None:
        raise RuntimeError("12.2.0 requires app + engine")

    # Remove only GET handlers for the command-centre root; preserve all other routes.
    keep=[]
    for r in list(app.router.routes):
        if getattr(r,"path",None)==ROUTE and "GET" in set(getattr(r,"methods",set()) or set()):
            continue
        keep.append(r)
    app.router.routes[:] = keep

    @app.get(ROUTE, response_class=HTMLResponse, include_in_schema=False)
    def team_dashboard(req: Request):
        return _dashboard(core, req)

    @app.get("/alliance/team", response_class=HTMLResponse, include_in_schema=False)
    def team_dashboard_alias(req: Request):
        return _dashboard(core, req)

    @app.get("/api/alliance/admin/team-dashboard-1220/status")
    def status():
        c=_counts(e)
        checks,healthy=_health(e,c)
        return {
            "status":"PASS" if healthy else "ATTENTION",
            "version":VERSION,
            "team_dashboard":ROUTE,
            "alias":"/alliance/team",
            "workflow":"Requirement -> Smart Match -> Availability Verification -> Approval -> Assignment -> Follow-up",
            "counts":c,
            "health":checks,
            "safety":{
                "requirement_verification_gate":"ON",
                "matcher_transaction_gate":"HARD via 12.1.1",
                "assignment_requires_approved_match":"ON",
                "availability_verification_before_client_send":"REQUIRED",
                "raw_magazine_mutation":"NONE"
            }
        }
    return {"status":"REGISTERED","version":VERSION,"route":ROUTE}
