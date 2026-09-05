from __future__ import annotations
import html
from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

VERSION="9.2.0-FINAL-WORKFLOW-COCKPIT"

def _app(core): return getattr(core,"app",None) or core
def _engine(core): return getattr(core,"engine",None)
def _login(core,req):
    fn=getattr(core,"need_login",None)
    return fn(req) if fn else "team"
def _e(v): return html.escape("" if v is None else str(v))
def _scalar(e,sql,params=None,default=0):
    try:
        with e.connect() as c:
            return c.execute(text(sql),params or {}).scalar_one()
    except Exception:
        return default
def _route_exists(app,path):
    return any(getattr(r,"path",None)==path for r in getattr(app.router,"routes",[]))
def _page(core,req):
    _login(core,req); app=_app(core); e=_engine(core)
    metrics={
        "properties":_scalar(e,"SELECT COUNT(*) FROM pi_master_properties_v711"),
        "requirements":_scalar(e,"SELECT COUNT(*) FROM pi_master_requirements_v711"),
        "verified":_scalar(e,"SELECT COUNT(*) FROM pi_master_workflow_v720 WHERE verification_status='VERIFIED'"),
        "available":_scalar(e,"SELECT COUNT(*) FROM pi_master_workflow_v720 WHERE availability_status='AVAILABLE'"),
        "matches":_scalar(e,"SELECT COUNT(*) FROM pi_master_matches_v720"),
        "followups":_scalar(e,"SELECT COUNT(*) FROM pi_master_action_state_v730 WHERE followup_status='SCHEDULED'"),
    }
    routes=[
      ("Property Databases","/alliance/primary/databases"),
      ("Requirement Databases","/alliance/primary/requirements-hub"),
      ("Add Property","/property-manual"),
      ("Add / Manage Requirement","/requirements-workbench"),
      ("Verification Queue","/alliance/primary/availability"),
      ("Matcher","/alliance/primary/matcher"),
      ("Follow-ups","/alliance/primary/followups"),
      ("Reports","/alliance/primary/reports"),
    ]
    cards="".join(
        f'<div class="stat"><div class="n">{metrics[k]}</div><small>{label}</small></div>'
        for k,label in [("properties","Master Properties"),("requirements","Master Requirements"),("verified","Verified"),("available","Available"),("matches","Matches"),("followups","Scheduled Follow-ups")]
    )
    steps=[
      ("1","CAPTURE PROPERTY","Enter manually or ingest from Newspaper, WhatsApp or Magazine.","/alliance/primary/databases"),
      ("2","MASTER DATABASE","All source records remain traceable. The canonical working inventory is Master.","/alliance/final/database/master"),
      ("3","VERIFY","Call owner/broker and update availability before sending property options.","/alliance/primary/availability"),
      ("4","CAPTURE REQUIREMENT","Store demand in its source requirement database and Master Requirements.","/alliance/primary/requirements-hub"),
      ("5","MATCH","Matcher searches Master Property Database only.","/alliance/primary/matcher"),
      ("6","REVIEW","Team reviews results. Owner/broker contact details stay internal.","/alliance/final/database/master"),
      ("7","FOLLOW-UP","Schedule call-backs, re-verification and requirement follow-up.","/alliance/primary/followups"),
      ("8","DEAL","Track activity and conversion in Reports.","/alliance/primary/reports"),
    ]
    stephtml=""
    for n,t,d,p in steps:
        ok=_route_exists(app,p) or p.startswith("/alliance/final/")
        action=f'<a href="{_e(p)}">Open</a>' if ok else '<span class="missing">Route unavailable</span>'
        stephtml+=f'<div class="step"><div class="numstep">{n}</div><div><b>{t}</b><p>{_e(d)}</p>{action}</div></div>'
    health=""
    for label,p in routes:
        ok=_route_exists(app,p)
        health+=f'<tr><td>{_e(label)}</td><td>{_e(p)}</td><td class="{"ok" if ok else "bad"}">{"READY" if ok else "MISSING"}</td></tr>'
    return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alliance CRE Workflow</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f4f7fb;font-family:Arial;color:#172033}}header{{background:#0d2238;color:white;padding:18px 22px}}
nav{{background:white;border-bottom:1px solid #98a2b3;padding:8px;display:flex;gap:6px;flex-wrap:wrap;position:sticky;top:0;z-index:10}}
nav a,.step a{{background:#0d2238;color:white;text-decoration:none;border:1px solid #0d2238;padding:7px 9px;font-size:12px}}
.wrap{{max-width:1900px;margin:auto;padding:14px}}.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:8px}}
.stat{{background:white;border:1px solid #98a2b3;padding:12px}}.stat .n{{font-size:26px;font-weight:800}}.flow{{background:white;border:1px solid #667085;margin-top:12px}}
.step{{display:grid;grid-template-columns:54px 1fr;gap:8px;padding:11px;border-bottom:1px solid #98a2b3}}.step:last-child{{border-bottom:0}}.numstep{{border:1px solid #667085;width:36px;height:36px;display:flex;align-items:center;justify-content:center;font-weight:800;background:#e9eef5}}
.step p{{margin:4px 0 8px;color:#475467;font-size:12px}}table{{border-collapse:collapse;width:100%;font-size:12px;margin-top:12px}}th,td{{border:1px solid #98a2b3;padding:7px;text-align:left}}th{{background:#e9eef5}}.ok{{color:#067647;font-weight:800}}.bad,.missing{{color:#b42318;font-weight:800}}
.rule{{background:#fff7e6;border:1px solid #f79009;padding:10px;margin-top:12px;font-size:12px}}
</style></head><body><header><b>Alliance CRE Operating System · Workflow Cockpit</b><br><small>PROPERTY → VERIFY → REQUIREMENT → MATCH → CLIENT → FOLLOW-UP → DEAL</small></header>
<nav><a href="/alliance/primary">Command Centre</a><a href="/alliance/primary/databases">5 Property Databases</a><a href="/alliance/primary/requirements-hub">5 Requirement Databases</a><a href="/alliance/primary/matcher">Matcher</a><a href="/alliance/primary/availability">Verification</a><a href="/alliance/primary/followups">Follow-ups</a><a href="/alliance/primary/reports">Reports</a></nav>
<div class="wrap"><div class="stats">{cards}</div><div class="flow">{stephtml}</div>
<div class="rule"><b>Frozen operating rule:</b> Newspaper, WhatsApp, Magazine and Manual remain separate source databases. Master is the canonical consolidated database. Matching searches Master Property Database only. No client-safe output should expose internal owner/broker contact details.</div>
<h3>Workflow Health</h3><table><thead><tr><th>Module</th><th>Route</th><th>Status</th></tr></thead><tbody>{health}</tbody></table></div></body></html>""")
def register(core):
    app=_app(core)
    if app is None or _engine(core) is None: raise RuntimeError("9.2 requires app + engine")
    @app.get("/alliance/workflow",response_class=HTMLResponse)
    def workflow(req:Request):
        return _page(core,req)
    return {"status":"REGISTERED","version":VERSION,"route":"/alliance/workflow"}
