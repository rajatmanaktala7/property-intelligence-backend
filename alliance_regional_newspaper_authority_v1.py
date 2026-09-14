from __future__ import annotations
import inspect,re,threading,time
from datetime import datetime,timezone
from html import escape
from fastapi import Query,Request
from fastapi.responses import HTMLResponse,JSONResponse,RedirectResponse
from sqlalchemy import text

VERSION="1.0.0-REGIONAL-NEWSPAPER-AUTHORITY"
MARKER="ALLIANCE_REGIONAL_NEWSPAPER_AUTHORITY_V1"
PROPERTY_AUTHORITY="pi_master_properties_v711"
REQUIREMENT_AUTHORITY="pi_requirement_gate_v1191"
REGION_AUTHORITY="pi_alliance_region_authority_v1"
MATCHER_SOURCE_CONTRACT="MASTER_ONLY"
_STATE={"status":"STARTING","last_audit_at":None,"last_error":None,"property_counts":{},"requirement_counts":{},"routes":{},"worker_started":False}
_SERVED_APP=None
GOA=("goa","panjim","panaji","mapusa","thivim","tivim","colvale","comvale","siolim","assagao","anjuna","vagator","morjim","parra","sangolda","candolim","calangute","porvorim","dona paula","margao","madgaon","colva","benaulim","vasco","verna","salcete","bardez","pernem","north goa","south goa")
DELHI=("delhi ncr","new delhi","delhi","gurugram","gurgaon","noida","greater noida","ghaziabad","faridabad","dwarka","aerocity","connaught place","saket","vasant kunj","nehru place","janakpuri","rohini","rajouri garden","south delhi","north delhi","west delhi","east delhi")
OTHER=("mumbai","bombay","pune","bengaluru","bangalore","hyderabad","chennai","kolkata","jaipur","ahmedabad","chandigarh")
TEXT_FIELDS=("city","location","locality","address","project_name","project","title","description","details","raw_text","original_message","requirement_text","source_name","source_group","clean_record")
PID=("master_property_id","canonical_id","record_id","property_id","id","master_id")
RID=("requirement_id","record_id","id","master_id")

def _app(core): return getattr(core,"app",None) or core
def _engine(core):
    e=getattr(core,"engine",None)
    if e is None: raise RuntimeError("Alliance engine unavailable")
    return e
def _need_login(core,req):
    fn=getattr(core,"need_login",None)
    if not callable(fn): raise RuntimeError("Alliance login authority unavailable")
    return fn(req)
def _route_exists(app,path,method="GET"):
    m=method.upper()
    return any(getattr(r,"path",None)==path and m in set(getattr(r,"methods",set()) or set()) for r in list(app.router.routes))
def _route_exists_any(primary,path,method="GET"):
    if _route_exists(primary,path,method):
        return True
    other=_SERVED_APP
    if other is not None and other is not primary:
        try:
            return _route_exists(other,path,method)
        except Exception:
            return False
    return False
def _remove(app,path,method="GET"):
    m=method.upper(); kept=[]; n=0
    for r in list(app.router.routes):
        if getattr(r,"path",None)==path and m in set(getattr(r,"methods",set()) or set()): n+=1
        else: kept.append(r)
    app.router.routes[:]=kept
    return n
def _norm(v):
    s=re.sub(r"[^a-z0-9]+"," ",str(v or "").lower())
    return re.sub(r"\s+"," ",s).strip()
def _has(n,p):
    p=_norm(p)
    return re.search(r"(?<![a-z0-9])"+re.escape(p)+r"(?![a-z0-9])",n) is not None
def classify_region(row_or_text):
    if isinstance(row_or_text,dict):
        vals=[]; fields=[]
        for k in TEXT_FIELDS:
            if row_or_text.get(k) not in (None,""):
                vals.append(str(row_or_text[k])); fields.append(k)
        raw=" | ".join(vals)
    else: raw=str(row_or_text or ""); fields=["text"]
    n=_norm(raw); gh=sorted({x for x in GOA if _has(n,x)}); dh=sorted({x for x in DELHI if _has(n,x)}); oh=sorted({x for x in OTHER if _has(n,x)})
    if gh and dh: region,conf,reason="REVIEW_REQUIRED",0.0,"CONFLICTING_TARGET_REGIONS"
    elif gh: region,conf,reason="GOA",1.0,"DETERMINISTIC_GOA_EVIDENCE"
    elif dh: region,conf,reason="DELHI_NCR",1.0,"DETERMINISTIC_DELHI_NCR_EVIDENCE"
    elif oh: region,conf,reason="OTHER",1.0,"DETERMINISTIC_NON_TARGET_REGION"
    else: region,conf,reason="REVIEW_REQUIRED",0.0,"NO_DETERMINISTIC_REGION_EVIDENCE"
    return {"market_region":region,"confidence":conf,"reason":reason,"evidence":{"goa":gh,"delhi_ncr":dh,"other":oh},"basis_fields":fields}
def _cols(engine,table):
    with engine.connect() as c:
        rs=c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=:t ORDER BY ordinal_position"),{"t":table}).fetchall()
    return [str(r[0]) for r in rs]
def _table_exists(engine,table):
    with engine.connect() as c:
        return bool(c.execute(text("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name=:t)"),{"t":table}).scalar())
def _rows(engine,table,limit=20000):
    if table not in {PROPERTY_AUTHORITY,REQUIREMENT_AUTHORITY}: raise ValueError("unapproved table")
    allowed=set(TEXT_FIELDS)|set(PID)|set(RID)|{"source_type","transaction","property_type","asset_type","verification_status","verified","availability_status","status","created_at","updated_at"}
    wanted=[c for c in _cols(engine,table) if c in allowed]
    if not wanted:return []
    q=", ".join('"'+c.replace('"','""')+'"' for c in wanted)
    with engine.connect() as c:
        rs=c.execute(text(f'SELECT {q} FROM "{table}" LIMIT :lim'),{"lim":int(limit)}).mappings().all()
    return [dict(r) for r in rs]
def _rid(row,kind):
    for k in (PID if kind=="PROPERTY" else RID):
        if row.get(k) not in (None,""): return str(row[k])
    return None
def _ensure(engine):
    with engine.begin() as c:
        c.execute(text(f"""CREATE TABLE IF NOT EXISTS {REGION_AUTHORITY}(
record_type VARCHAR(20) NOT NULL,record_id TEXT NOT NULL,market_region VARCHAR(32) NOT NULL,
confidence DOUBLE PRECISION NOT NULL DEFAULT 0,reason TEXT,evidence TEXT,basis_fields TEXT,
first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
PRIMARY KEY(record_type,record_id))"""))
        c.execute(text(f"CREATE INDEX IF NOT EXISTS idx_alliance_region_authority_v1_region ON {REGION_AUTHORITY}(record_type,market_region)"))
def _refresh(engine,table,kind):
    counts={x:0 for x in ("GOA","DELHI_NCR","OTHER","REVIEW_REQUIRED")}; payload=[]
    for row in _rows(engine,table):
        rid=_rid(row,kind)
        if not rid: continue
        d=classify_region(row); counts[d["market_region"]]+=1
        payload.append({"record_type":kind,"record_id":rid,"market_region":d["market_region"],"confidence":d["confidence"],"reason":d["reason"],"evidence":repr(d["evidence"])[:4000],"basis_fields":",".join(d["basis_fields"])[:1000]})
    if payload:
        with engine.begin() as c:
            c.execute(text(f"""INSERT INTO {REGION_AUTHORITY}
(record_type,record_id,market_region,confidence,reason,evidence,basis_fields,last_seen_at)
VALUES(:record_type,:record_id,:market_region,:confidence,:reason,:evidence,:basis_fields,NOW())
ON CONFLICT(record_type,record_id) DO UPDATE SET market_region=EXCLUDED.market_region,
confidence=EXCLUDED.confidence,reason=EXCLUDED.reason,evidence=EXCLUDED.evidence,
basis_fields=EXCLUDED.basis_fields,last_seen_at=NOW()"""),payload)
    return counts
def audit(core):
    app=_app(core); eng=_engine(core)
    try:
        _ensure(eng); pc=_refresh(eng,PROPERTY_AUTHORITY,"PROPERTY"); rc=_refresh(eng,REQUIREMENT_AUTHORITY,"REQUIREMENT")
        routes={"newspaper_workspace":_route_exists_any(app,"/newspaper-v83"),"newspaper_process":_route_exists_any(app,"/api/newspaper-v83/process","POST"),"newspaper_health":_route_exists_any(app,"/api/newspaper-v83/health"),"canonical_newspaper_capture":_route_exists(app,"/alliance/newspaper-capture"),"goa_properties":_route_exists(app,"/alliance/properties/goa"),"delhi_properties":_route_exists(app,"/alliance/properties/delhi-ncr"),"goa_requirements":_route_exists(app,"/alliance/requirements/goa"),"delhi_requirements":_route_exists(app,"/alliance/requirements/delhi-ncr"),"home":_route_exists(app,"/")}
        ok=all(routes.values()) and _table_exists(eng,PROPERTY_AUTHORITY) and _table_exists(eng,REQUIREMENT_AUTHORITY) and _table_exists(eng,REGION_AUTHORITY)
        _STATE.update(status="PASS" if ok else "FAIL",last_audit_at=datetime.now(timezone.utc).isoformat(),last_error=None,property_counts=pc,requirement_counts=rc,routes=routes)
    except Exception as exc: _STATE.update(status="FAIL",last_audit_at=datetime.now(timezone.utc).isoformat(),last_error=f"{type(exc).__name__}: {exc}")
    return dict(_STATE)
def _worker(core):
    while True: audit(core); time.sleep(600)
def _e(v): return escape(str(v if v is not None else ""))
def _region_page(core,req,kind,region,q=""):
    _need_login(core,req); table=PROPERTY_AUTHORITY if kind=="PROPERTY" else REQUIREMENT_AUTHORITY; qn=_norm(q); chosen=[]
    for row in _rows(_engine(core),table):
        if classify_region(row)["market_region"]!=region: continue
        if qn and qn not in _norm(" ".join(str(v or "") for v in row.values())): continue
        chosen.append(row)
    total=len(chosen); shown=chosen[:300]
    cols=["record_id","property_id","requirement_id","source_type","city","location","project_name","property_type","asset_type","transaction","availability_status","verification_status","status"]
    headers=[c for c in cols if any(c in r for r in shown)]
    trs="".join("<tr>"+"".join(f"<td>{_e(r.get(c,''))}</td>" for c in headers)+"</tr>" for r in shown)
    pretty="Goa" if region=="GOA" else "Delhi NCR"
    return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8"><title>{pretty} {kind.title()} Database</title>
<style>body{{font-family:Arial;background:#f5f7fb;margin:0;color:#172033}}header{{background:#102a43;color:white;padding:22px}}main{{padding:20px;max-width:1500px;margin:auto}}a,.btn{{display:inline-block;padding:9px 12px;margin:3px;background:#163d63;color:white;text-decoration:none;border-radius:8px}}.card{{background:white;padding:16px;margin:14px 0;border:1px solid #ddd;border-radius:14px}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:8px;border-bottom:1px solid #eee;text-align:left}}</style></head><body><header><b>Alliance {pretty} {kind.title()} Database</b><div>Canonical master authority; operational regional view only.</div></header><main><a href="/alliance/primary">Dashboard</a><a href="/alliance/regions">Regional Hub</a><a href="/alliance/newspaper-capture">Newspaper Capture</a><a href="/logout">Logout</a><div class="card"><form><input name="q" value="{_e(q)}" placeholder="Search this region"><button class="btn">Search</button></form><p><b>{total}</b> record(s); showing up to 300.</p><p>Unknown or conflicting geography stays REVIEW_REQUIRED and is never guessed.</p></div><div class="card" style="overflow:auto"><table><thead><tr>{''.join(f'<th>{_e(h)}</th>' for h in headers)}</tr></thead><tbody>{trs}</tbody></table></div></main></body></html>""")
def _access():
    return HTMLResponse("""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Alliance Infrastructure Intelligence</title><style>body{font-family:Arial;background:#eef3f8;min-height:100vh;display:grid;place-items:center;margin:0;padding:20px;color:#14213d}.wrap{width:min(900px,100%)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px}.card{background:white;border:1px solid #dce5ee;border-radius:18px;padding:22px}input,button,a{box-sizing:border-box;width:100%;padding:13px;margin-top:10px;border-radius:9px}.staff{background:#102a43;color:white;border:0}.admin{background:#7c2d12;color:white;border:0}a{display:block;text-align:center;background:#e2e8f0;color:#172033;text-decoration:none}</style></head><body><div class="wrap"><h1>Alliance Infrastructure Intelligence</h1><p>Secure Staff and Admin access</p><div class="grid"><form class="card" method="post" action="/login"><h2>Staff Login</h2><input type="hidden" name="role" value="team"><input name="code" type="password" autocomplete="current-password" placeholder="Staff access code" required><button class="staff">Login as Staff</button></form><form class="card" method="post" action="/login"><h2>Admin Login</h2><input type="hidden" name="role" value="admin"><input name="code" type="password" autocomplete="current-password" placeholder="Admin access code" required><button class="admin">Login as Admin</button></form></div><div class="card"><a href="/alliance/primary">Open workspace if already logged in</a><p>Existing Alliance authentication is reused. No new credential store is created.</p></div></div></body></html>""")
def _inject(resp):
    if not isinstance(resp,HTMLResponse): return resp
    body=bytes(resp.body).decode("utf-8","replace")
    if "ALLIANCE_REGIONAL_NEWSPAPER_PANEL_V1" in body:return resp
    panel="<section id=\"ALLIANCE_REGIONAL_NEWSPAPER_PANEL_V1\" style=\"margin:18px 0;padding:16px;border:1px solid #dbe5ef;border-radius:14px;background:#fff\"><h2>Regional Operations</h2><p><a href=\"/alliance/properties/goa\">Goa Property Database</a> | <a href=\"/alliance/properties/delhi-ncr\">Delhi NCR Property Database</a> | <a href=\"/alliance/requirements/goa\">Goa Requirements</a> | <a href=\"/alliance/requirements/delhi-ncr\">Delhi NCR Requirements</a> | <a href=\"/alliance/newspaper-capture\">Newspaper Capture</a> | <a href=\"/logout\">Logout</a></p></section>"
    low=body.lower(); pos=low.rfind("</main>")
    if pos<0: pos=low.rfind("</body>")
    body=body+panel if pos<0 else body[:pos]+panel+body[pos:]
    return HTMLResponse(body,status_code=resp.status_code)
def _wrap_dashboard(app):
    target=None
    for r in list(app.router.routes):
        if getattr(r,"path",None)=="/alliance/primary" and "GET" in set(getattr(r,"methods",set()) or set()): target=r
    if target is None:return False
    original=target.endpoint; _remove(app,"/alliance/primary")
    @app.get("/alliance/primary",response_class=HTMLResponse,include_in_schema=False)
    async def regional_dashboard(req:Request):
        sig=inspect.signature(original)
        required=[p for p in sig.parameters.values() if p.default is inspect._empty and p.kind in (p.POSITIONAL_ONLY,p.POSITIONAL_OR_KEYWORD,p.KEYWORD_ONLY)]
        if len(required)>1:return HTMLResponse("Dashboard wrapper contract mismatch",500)
        result=original(req) if required else original()
        if inspect.isawaitable(result): result=await result
        return _inject(result)
    return True
def register(core,served_app=None):
    global _SERVED_APP
    _SERVED_APP=served_app
    app=_app(core); eng=_engine(core); _remove(app,"/")
    @app.get("/",response_class=HTMLResponse,include_in_schema=False)
    def home(): return _access()
    @app.get("/alliance/newspaper-capture",include_in_schema=False)
    def newspaper_capture(req:Request):
        _need_login(core,req)
        if not (_route_exists_any(app,"/newspaper-v83") and _route_exists_any(app,"/api/newspaper-v83/process","POST")): return HTMLResponse("<h1>Newspaper Capture unavailable</h1><p>Pipeline audit failed. No upload attempted.</p>",503)
        return RedirectResponse("/newspaper-v83",307)
    @app.get("/alliance/regions",response_class=HTMLResponse,include_in_schema=False)
    def regions(req:Request):
        _need_login(core,req)
        return HTMLResponse("""<html><body style="font-family:Arial;padding:24px"><h1>Alliance Regional Hub</h1><p>One master authority; separate operational views.</p><h2>Goa</h2><a href="/alliance/properties/goa">Properties</a> | <a href="/alliance/requirements/goa">Requirements</a><h2>Delhi NCR</h2><a href="/alliance/properties/delhi-ncr">Properties</a> | <a href="/alliance/requirements/delhi-ncr">Requirements</a><h2>Newspaper</h2><a href="/alliance/newspaper-capture">Capture Newspaper</a><p><a href="/alliance/primary">Dashboard</a> | <a href="/logout">Logout</a></p></body></html>""")
    @app.get("/alliance/properties/goa",response_class=HTMLResponse,include_in_schema=False)
    def gp(req:Request,q:str=Query(default="",max_length=250)): return _region_page(core,req,"PROPERTY","GOA",q)
    @app.get("/alliance/properties/delhi-ncr",response_class=HTMLResponse,include_in_schema=False)
    def dp(req:Request,q:str=Query(default="",max_length=250)): return _region_page(core,req,"PROPERTY","DELHI_NCR",q)
    @app.get("/alliance/requirements/goa",response_class=HTMLResponse,include_in_schema=False)
    def gr(req:Request,q:str=Query(default="",max_length=250)): return _region_page(core,req,"REQUIREMENT","GOA",q)
    @app.get("/alliance/requirements/delhi-ncr",response_class=HTMLResponse,include_in_schema=False)
    def dr(req:Request,q:str=Query(default="",max_length=250)): return _region_page(core,req,"REQUIREMENT","DELHI_NCR",q)
    @app.get("/api/alliance/regional-newspaper-authority-v1/status")
    def status():
        s=dict(_STATE)
        return JSONResponse({"status":s.get("status"),"version":VERSION,"marker":MARKER,"matcher_source_contract":MATCHER_SOURCE_CONTRACT,"property_authority":PROPERTY_AUTHORITY,"requirement_authority":REQUIREMENT_AUTHORITY,"region_authority":REGION_AUTHORITY,"regions":["GOA","DELHI_NCR","OTHER","REVIEW_REQUIRED"],"newspaper":{"canonical_capture":"/alliance/newspaper-capture","workspace":"/newspaper-v83","process":"/api/newspaper-v83/process","health":"/api/newspaper-v83/health","route_checks":s.get("routes",{})},"login":{"staff_admin_home":"/","existing_login_post_reused":True,"new_credentials_store_created":False},"safety":{"master_property_rows_mutated":False,"master_requirement_rows_mutated":False,"region_map_additive_only":True,"ambiguous_region_policy":"REVIEW_REQUIRED","contacts_exposed":False,"automatic_send":False},"property_region_counts":s.get("property_counts",{}),"requirement_region_counts":s.get("requirement_counts",{}),"last_audit_at":s.get("last_audit_at"),"last_error":s.get("last_error"),"worker_started":s.get("worker_started",False)})
    wrapped=_wrap_dashboard(app); _ensure(eng); audit(core)
    if not _STATE["worker_started"]:
        _STATE["worker_started"]=True
        threading.Thread(target=_worker,args=(core,),daemon=True,name="alliance-region-newspaper-auditor").start()
    return {"status":"REGISTERED","version":VERSION,"matcher_source_contract":"MASTER_ONLY","property_authority":PROPERTY_AUTHORITY,"requirement_authority":REQUIREMENT_AUTHORITY,"region_authority":REGION_AUTHORITY,"dashboard_wrapped":wrapped,"served_app_audit":served_app is not None,"master_business_mutations":0}
