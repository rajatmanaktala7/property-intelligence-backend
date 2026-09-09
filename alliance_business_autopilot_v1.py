from __future__ import annotations
import asyncio, html, json, re
from datetime import datetime, timezone
from sqlalchemy import text
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

VERSION="1.0.0-ALLIANCE-BUSINESS-AUTOPILOT"
STATE={"running":False,"last_run":None,"last_error":None,"report":None,"task":None}
INTERVAL_SECONDS=900
REQUIRED_LINKS=[
"/alliance/primary","/alliance/final/database/master","/alliance/final/requirements/master",
"/alliance/primary/availability","/alliance/primary/matcher","/alliance/primary/followups",
"/property-manual","/requirement-manual","/alliance/final/databases","/alliance/final/requirements",
"/alliance/goa-properties","/whatsapp-live","/capture-intelligence","/property-discovery",
"/commercial-intelligence","/hospitality-intelligence","/retail-expansion",
"/requirement-discovery","/marketing-contacts","/alliance/primary/ai-control",
"/alliance/primary/data-health","/alliance/system-doctor","/alliance/deep-audit"
]
MASTER_REQ_TABLE="pi_master_requirements_v711"
MASTER_PROP_TABLE="pi_master_properties_v711"

def _app(core): return getattr(core,"app",None) or core
def _engine(core): return getattr(core,"engine",None)
def _login(core,req):
    fn=getattr(core,"need_login",None)
    if fn: fn(req)
def _route_methods(app):
    out={}
    for r in app.router.routes:
        p=getattr(r,"path",None)
        if p: out.setdefault(p,set()).update(getattr(r,"methods",set()) or set())
    return out
def _table_exists(c,t):
    return bool(c.execute(text("SELECT to_regclass(:t) IS NOT NULL"),{"t":t}).scalar())
def _cols(c,t):
    return {x[0] for x in c.execute(text("""SELECT column_name FROM information_schema.columns
      WHERE table_schema=current_schema() AND table_name=:t"""),{"t":t}).all()}
def _safe_count(c,sql):
    try:return int(c.execute(text(sql)).scalar() or 0)
    except Exception:return None

def audit(core):
    app=_app(core); eng=_engine(core); checks=[]; facts={}
    routes=_route_methods(app)
    missing=[p for p in REQUIRED_LINKS if "GET" not in routes.get(p,set())]
    checks.append({"name":"Canonical internal links registered","ok":not missing,"detail":"PASS" if not missing else "Missing: "+", ".join(missing)})

    import alliance_primary_workspace_v730 as ws
    src=open(ws.__file__,encoding="utf-8").read()
    master_req=(MASTER_REQ_TABLE in src and "PROMOTED_VALIDATED" in src and "verification_status" in src)
    master_prop=(MASTER_PROP_TABLE in src and "_search_properties" in src)
    alternatives=all(x in src for x in ("EXACT_LOCALITY","SAME_CITY_ALTERNATIVE","TRANSACTION_AREA_ALTERNATIVE"))
    run_contract=("def _match_full" in src and "return req,results[:limit]" in src)
    checks += [
      {"name":"Requirements match from Master","ok":master_req,"detail":MASTER_REQ_TABLE+" + promoted/verified gate"},
      {"name":"Properties match from Master","ok":master_prop,"detail":MASTER_PROP_TABLE+" via master integration"},
      {"name":"Run Matcher contract","ok":run_contract,"detail":"_match_full returns requirement + ranked results"},
      {"name":"Automatic alternative suggestions","ok":alternatives,"detail":"Exact locality → same-city → broader transaction/area alternatives"},
    ]

    db={}
    if eng:
      with eng.connect() as c:
        for t in (MASTER_REQ_TABLE,MASTER_PROP_TABLE,"pi_master_workflow_v720","pi_master_source_links_v711"):
          db[t]={"exists":_table_exists(c,t)}
        facts["master_requirements"]=_safe_count(c,"SELECT COUNT(*) FROM pi_master_requirements_v711")
        facts["master_properties"]=_safe_count(c,"SELECT COUNT(*) FROM pi_master_properties_v711")
        facts["verified_requirements"]=_safe_count(c,"""SELECT COUNT(*) FROM pi_master_requirements_v711 r
          JOIN pi_master_workflow_v720 w ON w.canonical_id=r.canonical_id
          WHERE r.promotion_status='PROMOTED_VALIDATED' AND w.verification_status='VERIFIED'""")
        facts["source_links"]=_safe_count(c,"SELECT COUNT(*) FROM pi_master_source_links_v711")
    checks.append({"name":"Master database foundation","ok":all(x["exists"] for x in db.values()),"detail":json.dumps(db)})

    try:
      import alliance_final_5x5_databases_v910 as grid
      gs=open(grid.__file__,encoding="utf-8").read()
      table_ok=("Date" in gs and "Location" in gs and "Transaction" in gs and "Source" in gs and "<table" in gs)
      checks.append({"name":"Approved table-grid contract","ok":table_ok,"detail":"Final 5x5 database renderer contains standard table fields"})
    except Exception as e:
      checks.append({"name":"Approved table-grid contract","ok":False,"detail":f"{type(e).__name__}: {e}"})

    deep=getattr(__import__("alliance_deep_runtime_auditor"),"STATE",{})
    deep_report={"running":bool(deep.get("running")),"tested":len(deep.get("results",{})),
                 "red":sum(1 for x in deep.get("results",{}).values() if not x.get("ok"))}
    deep_ok=(not deep_report["running"] and deep_report["tested"]>0 and deep_report["red"]==0)
    checks.append({"name":"Deep runtime link audit","ok":deep_ok,"detail":json.dumps(deep_report)})

    overall=all(x["ok"] for x in checks)
    return {"version":VERSION,"generated_at":datetime.now(timezone.utc).isoformat(),"status":"GREEN" if overall else "ATTENTION",
            "checks":checks,"facts":facts,"deep_runtime":deep_report,
            "policy":{"auto_safe_checks":True,"auto_business_data_mutation":False,"historical_backfill":False,
                      "verification_auto_promotion":False,"scoring_auto_change":False}}

async def run_once(core):
    if STATE["running"]: return STATE["report"]
    STATE["running"]=True
    try:
      STATE["report"]=audit(core); STATE["last_run"]=datetime.now(timezone.utc).isoformat(); STATE["last_error"]=None
    except Exception as e:
      STATE["last_error"]=f"{type(e).__name__}: {e}"
      STATE["report"]={"version":VERSION,"status":"ERROR","checks":[],"error":STATE["last_error"]}
    finally: STATE["running"]=False
    return STATE["report"]

async def _loop(core):
    while True:
      await run_once(core)
      await asyncio.sleep(INTERVAL_SECONDS)

def _html(r):
    rows="".join(f"<tr><td>{'🟢' if c['ok'] else '🔴'}</td><td>{html.escape(c['name'])}</td><td>{html.escape(c['detail'])}</td></tr>" for c in r.get("checks",[]))
    facts="".join(f"<li><b>{html.escape(str(k))}</b>: {html.escape(str(v))}</li>" for k,v in r.get("facts",{}).items())
    return f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Alliance Automation Control</title>
<style>body{{font-family:Arial;background:#f4f7fb;margin:0;padding:20px}}.card{{background:#fff;border:1px solid #ddd;border-radius:10px;padding:14px;margin:10px 0}}table{{border-collapse:collapse;width:100%;background:#fff}}th,td{{border:1px solid #aaa;padding:8px;text-align:left}}th{{background:#e9eef5}}a{{display:inline-block;padding:9px 12px;background:#172033;color:#fff;text-decoration:none;border-radius:7px}}</style></head>
<body><h2>Alliance Business Automation Control</h2><div class=card><b>Status: {html.escape(r.get('status','NOT RUN'))}</b><br>Version {VERSION}<br>Last run: {html.escape(str(STATE.get('last_run') or 'pending'))}<br>Automatic audit interval: 15 minutes</div>
<a href='/alliance/automation-control/run'>Run Audit Now</a><div class=card><b>Automation safety:</b> audits automatically; never auto-verifies, mass-promotes, changes matcher scoring, deletes records, exposes contacts, or performs historical backfill.</div>
<table><tr><th></th><th>Business Contract</th><th>Evidence</th></tr>{rows}</table><div class=card><h3>Master Facts</h3><ul>{facts}</ul></div></body></html>"""

def register(core):
    app=_app(core)
    owned={"/alliance/automation-control","/alliance/automation-control/run","/api/alliance/automation-control"}
    app.router.routes[:]=[r for r in app.router.routes if getattr(r,"path",None) not in owned]
    @app.get("/alliance/automation-control",response_class=HTMLResponse)
    async def page(req:Request):
      _login(core,req)
      if STATE["report"] is None: await run_once(core)
      return HTMLResponse(_html(STATE["report"] or {}))
    @app.get("/alliance/automation-control/run",response_class=HTMLResponse)
    async def run(req:Request):
      _login(core,req); r=await run_once(core); return HTMLResponse(_html(r or {}))
    @app.get("/api/alliance/automation-control")
    async def api(req:Request):
      _login(core,req); return JSONResponse(STATE["report"] or await run_once(core))
    try:
      loop=asyncio.get_running_loop()
      if STATE["task"] is None or STATE["task"].done(): STATE["task"]=loop.create_task(_loop(core))
    except RuntimeError: pass
    return {"status":"REGISTERED","version":VERSION,"interval_seconds":INTERVAL_SECONDS}
