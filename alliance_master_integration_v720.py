from __future__ import annotations
import html, json, math, re, threading, time
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

VERSION="7.2.0-ALLIANCE-UNIFIED-MASTER-DATABASE-INTEGRATION"
MODE="V711_AUTHORITATIVE_READ_MODEL_SMOOTH_NAV_SEARCH_MATCH_VERIFY_AREA_MONEY_PRIVACY_ROUTE_HEALTH_NO_SOURCE_MUTATION"
STATE={"status":"STARTING","started_at":datetime.now(timezone.utc).isoformat(),"last_audit":None,"result":None,"last_error":None}
_LOCK=threading.Lock()

DDL=[
"""CREATE TABLE IF NOT EXISTS pi_master_workflow_v720(
 canonical_id TEXT PRIMARY KEY,
 entity_type TEXT NOT NULL,
 verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
 verified_at TIMESTAMPTZ,
 verified_by TEXT,
 assigned_to TEXT,
 internal_notes TEXT,
 availability_status TEXT DEFAULT 'UNKNOWN',
 updated_at TIMESTAMPTZ DEFAULT NOW())""",
"""CREATE TABLE IF NOT EXISTS pi_master_matches_v720(
 id BIGSERIAL PRIMARY KEY,
 requirement_canonical_id TEXT NOT NULL,
 property_canonical_id TEXT NOT NULL,
 match_score NUMERIC(6,2) NOT NULL,
 match_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
 status TEXT NOT NULL DEFAULT 'READY_FOR_REVIEW',
 created_at TIMESTAMPTZ DEFAULT NOW(),
 updated_at TIMESTAMPTZ DEFAULT NOW(),
 UNIQUE(requirement_canonical_id,property_canonical_id))""",
"""CREATE TABLE IF NOT EXISTS pi_route_health_v720(
 route_path TEXT PRIMARY KEY,
 label TEXT NOT NULL,
 category TEXT NOT NULL,
 route_exists BOOLEAN NOT NULL DEFAULT FALSE,
 checked_at TIMESTAMPTZ DEFAULT NOW())"""
]

NAV=[
("Command Centre","/alliance","CORE"),
("Master Property Database","/alliance/properties","MASTER"),
("Master Requirement Database","/alliance/requirements","MASTER"),
("AI Property Matcher","/alliance/matcher","MASTER"),
("Add Property","/property-manual","OPERATIONS"),
("Legacy Workspace","/workspace","OPERATIONS"),
("Legacy Property Database","/database-page?table_name=properties","OPERATIONS"),
("Legacy Requirement Database","/database-page?table_name=requirements","OPERATIONS"),
("System Status","/status-page","SYSTEM"),
("7.1.1 Integrity","/property-brain/promotion-integrity-v711","SYSTEM"),
]

def _engine(core):return getattr(core,"engine",None)
def _app(core):return getattr(core,"app",None) or core
def _route_exists(app,path):
    base=path.split("?",1)[0]
    return any(getattr(r,"path",None)==base for r in getattr(app,"routes",[]))
def _role(core,req):
    fn=getattr(core,"need_login",None)
    return fn(req) if fn else "team"
def _actor(core,req):
    fn=getattr(core,"actor_name",None)
    return fn(req) if fn else "team"
def _safe(v):
    if v is None:return None
    if isinstance(v,Decimal):return float(v)
    if isinstance(v,(datetime,)):return v.isoformat()
    if isinstance(v,dict):return {str(k):_safe(x) for k,x in v.items()}
    if isinstance(v,(list,tuple,set)):return [_safe(x) for x in v]
    return v
def _rows(result):return [{k:_safe(v) for k,v in dict(r._mapping).items()} for r in result]
def _phones(v):
    if isinstance(v,list):return [str(x) for x in v]
    if isinstance(v,dict):return [str(x) for x in v.values()]
    return []
def _area_views(sqft):
    if sqft in (None,""):return {"sqft":None,"sqyd":None,"sqm":None,"acre":None}
    x=float(sqft)
    return {"sqft":round(x,2),"sqyd":round(x/9,2),"sqm":round(x/10.7639104167,2),"acre":round(x/43560,4)}
def _money(price_raw,kind,tx):
    sale=None;rent=None
    if price_raw:
        if tx=="SALE" or kind=="SALE_AMOUNT":sale=str(price_raw)
        elif tx=="RENT" or kind=="RENT_AMOUNT":rent=str(price_raw)
    return sale,rent
def _table_exists(engine,t):
    with engine.connect() as c:return bool(c.execute(text("SELECT to_regclass(:t) IS NOT NULL"),{"t":t}).scalar())
def _ensure_parent(engine):
    for t in ["pi_master_properties_v711","pi_master_requirements_v711","pi_master_source_links_v711"]:
        if not _table_exists(engine,t):raise RuntimeError("Required 7.1.1 table missing: "+t)

def _decorate_property(d,internal=True):
    d=dict(d); area=_area_views(d.get("area_sqft")); sale,rent=_money(d.get("price_raw"),d.get("price_kind"),d.get("transaction_type"))
    d.update({"area_sqft_display":area["sqft"],"area_sqyd":area["sqyd"],"area_sqm":area["sqm"],"area_acre":area["acre"],
              "sale_amount":sale,"rent_amount":rent})
    if not internal:d.pop("phones",None)
    return d
def _decorate_requirement(d,internal=True):
    d=dict(d); area=_area_views(d.get("area_sqft")); sale,rent=_money(d.get("budget_raw"),d.get("budget_kind"),d.get("transaction_type"))
    d.update({"area_sqft_display":area["sqft"],"area_sqyd":area["sqyd"],"area_sqm":area["sqm"],"area_acre":area["acre"],
              "sale_budget":sale,"rent_budget":rent})
    if not internal:d.pop("phones",None)
    return d

def _audit_routes(core):
    app=_app(core); engine=_engine(core); checks=[]
    for label,path,cat in NAV:
        ok=_route_exists(app,path)
        checks.append({"label":label,"path":path,"category":cat,"route_exists":ok})
    with engine.begin() as c:
        for x in checks:
            c.execute(text("""INSERT INTO pi_route_health_v720(route_path,label,category,route_exists,checked_at)
              VALUES(:p,:l,:c,:o,NOW()) ON CONFLICT(route_path) DO UPDATE SET
              label=EXCLUDED.label,category=EXCLUDED.category,route_exists=EXCLUDED.route_exists,checked_at=NOW()"""),
              {"p":x["path"],"l":x["label"],"c":x["category"],"o":x["route_exists"]})
    STATE["last_audit"]=datetime.now(timezone.utc).isoformat()
    return checks

def _counts(engine):
    with engine.connect() as c:
        p=c.execute(text("SELECT COUNT(*) FROM pi_master_properties_v711")).scalar_one()
        r=c.execute(text("SELECT COUNT(*) FROM pi_master_requirements_v711")).scalar_one()
        v=c.execute(text("SELECT COUNT(*) FROM pi_master_workflow_v720 WHERE verification_status='VERIFIED'")).scalar_one()
        m=c.execute(text("SELECT COUNT(*) FROM pi_master_matches_v720")).scalar_one()
    return {"properties":p,"requirements":r,"verified":v,"matches":m}

def _shell(core,req,title,body):
    role=_role(core,req)
    links="".join(f'<a href="{html.escape(p,quote=True)}">{html.escape(l)}</a>' for l,p,_ in NAV)
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>
*{{box-sizing:border-box}}body{{font-family:Arial;margin:0;background:#f5f7fb;color:#172033}}header{{background:#102235;color:white;padding:18px 24px}}
nav{{background:white;padding:10px 16px;border-bottom:1px solid #dde3ea;display:flex;gap:7px;flex-wrap:wrap}}nav a,.btn{{text-decoration:none;padding:9px 11px;border-radius:8px;background:#102235;color:white;border:0;cursor:pointer}}
.wrap{{padding:18px;max-width:1700px;margin:auto}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}}
.card{{background:white;border:1px solid #e1e7ee;border-radius:12px;padding:14px;margin-bottom:12px}}.num{{font-size:28px;font-weight:800}}
table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{padding:8px;border-bottom:1px solid #edf0f4;text-align:left;vertical-align:top}}.tablebox{{overflow:auto;max-height:70vh}}
input,select{{padding:9px;border:1px solid #ccd4dd;border-radius:7px}}.ok{{color:#08783e;font-weight:700}}.bad{{color:#b42318;font-weight:700}}
small,.muted{{color:#667085}}form.inline{{display:flex;gap:7px;flex-wrap:wrap;align-items:center;margin-bottom:12px}}
</style></head><body><header><b>Alliance Property Brain · 7.2</b><br><small style="color:#d7e0ea">Authoritative Master Database · {html.escape(str(role))}</small></header>
<nav>{links}</nav><div class="wrap"><h2>{html.escape(title)}</h2>{body}</div></body></html>"""

def _table(data,cols):
    if not data:return "<div class='card'>No records found.</div>"
    head="".join("<th>"+html.escape(lbl)+"</th>" for key,lbl in cols)
    out=[]
    for r in data:
        cells=[]
        for key,lbl in cols:
            v=r.get(key)
            if isinstance(v,(dict,list)):v=json.dumps(v,ensure_ascii=False)
            cells.append("<td>"+html.escape("" if v is None else str(v))+"</td>")
        out.append("<tr>"+"".join(cells)+"</tr>")
    return f"<div class='card tablebox'><table><thead><tr>{head}</tr></thead><tbody>{''.join(out)}</tbody></table></div>"

def _search_properties(engine,q="",tx="",limit=500):
    wh=["1=1"];params={"n":limit}
    if q:
        wh.append("(COALESCE(p.locality,'') ILIKE :q OR COALESCE(p.city,'') ILIKE :q OR COALESCE(p.clean_record::text,'') ILIKE :q)")
        params["q"]="%"+q+"%"
    if tx:
        wh.append("p.transaction_type=:tx");params["tx"]=tx.upper()
    sql=f"""SELECT p.*,COALESCE(w.verification_status,'UNVERIFIED') verification_status,w.availability_status,w.assigned_to
      FROM pi_master_properties_v711 p LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=p.canonical_id
      WHERE {' AND '.join(wh)} ORDER BY p.updated_at DESC LIMIT :n"""
    with engine.connect() as c:return [_decorate_property(x) for x in _rows(c.execute(text(sql),params))]
def _search_requirements(engine,q="",tx="",limit=500):
    wh=["1=1"];params={"n":limit}
    if q:
        wh.append("(COALESCE(r.locality,'') ILIKE :q OR COALESCE(r.city,'') ILIKE :q OR COALESCE(r.clean_record::text,'') ILIKE :q)")
        params["q"]="%"+q+"%"
    if tx:wh.append("r.transaction_type=:tx");params["tx"]=tx.upper()
    sql=f"""SELECT r.*,COALESCE(w.verification_status,'UNVERIFIED') verification_status,w.assigned_to
      FROM pi_master_requirements_v711 r LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=r.canonical_id
      WHERE {' AND '.join(wh)} ORDER BY r.updated_at DESC LIMIT :n"""
    with engine.connect() as c:return [_decorate_requirement(x) for x in _rows(c.execute(text(sql),params))]

def _score(req,p):
    score=0;reasons=[]
    if req.get("transaction_type")==p.get("transaction_type"):score+=30;reasons.append("transaction")
    rl=(req.get("locality") or "").lower();pl=(p.get("locality") or "").lower()
    rc=(req.get("city") or "").lower();pc=(p.get("city") or "").lower()
    if rl and pl and (rl in pl or pl in rl):score+=40;reasons.append("locality")
    elif rc and pc and rc==pc:score+=20;reasons.append("city")
    ra=req.get("area_sqft");pa=p.get("area_sqft")
    if ra and pa:
        diff=abs(float(pa)-float(ra))/max(float(ra),1)
        if diff<=.15:score+=25;reasons.append("area ±15%")
        elif diff<=.30:score+=15;reasons.append("area ±30%")
        elif diff<=.50:score+=7;reasons.append("area ±50%")
    if p.get("verification_status")=="VERIFIED":score+=5;reasons.append("verified")
    return min(score,100),reasons

def _run_match(engine,rid):
    with engine.connect() as c:
        rr=c.execute(text("SELECT * FROM pi_master_requirements_v711 WHERE canonical_id=:id"),{"id":rid}).mappings().first()
    if not rr:raise HTTPException(404,"Requirement not found")
    req=_decorate_requirement(_safe(dict(rr)))
    props=_search_properties(engine,tx=req.get("transaction_type") or "",limit=3507)
    scored=[]
    for p in props:
        s,reasons=_score(req,p)
        if s>=35:scored.append((s,reasons,p))
    scored.sort(key=lambda x:x[0],reverse=True)
    with engine.begin() as c:
        for s,reasons,p in scored[:50]:
            c.execute(text("""INSERT INTO pi_master_matches_v720(requirement_canonical_id,property_canonical_id,match_score,match_reasons,status,updated_at)
              VALUES(:r,:p,:s,CAST(:why AS JSONB),'READY_FOR_REVIEW',NOW())
              ON CONFLICT(requirement_canonical_id,property_canonical_id) DO UPDATE SET
              match_score=EXCLUDED.match_score,match_reasons=EXCLUDED.match_reasons,updated_at=NOW()"""),
              {"r":rid,"p":p["canonical_id"],"s":s,"why":json.dumps(reasons)})
    return [{"score":s,"reasons":reasons,"property":p} for s,reasons,p in scored[:25]]

def _client_message(req,matches):
    lines=["Property options matching your requirement:"]
    for i,m in enumerate(matches[:3],1):
        p=m["property"];bits=[f"{i}. {p.get('locality') or 'Property'}"]
        if p.get("area_sqft_display"):bits.append(f"{p['area_sqft_display']} sq ft")
        if p.get("transaction_type"):bits.append(str(p["transaction_type"]))
        if p.get("sale_amount"):bits.append("Sale: "+str(p["sale_amount"]))
        if p.get("rent_amount"):bits.append("Rent: "+str(p["rent_amount"]))
        lines.append(" | ".join(bits))
    lines.append("Please tell us which option you would like to inspect.")
    return "\n".join(lines)

def register(core):
    app=_app(core);engine=_engine(core)
    _ensure_parent(engine)
    with engine.begin() as c:
        for ddl in DDL:c.execute(text(ddl))

    @app.get("/api/v7.2/health")
    def api_health(req:Request):
        _role(core,req);checks=_audit_routes(core);counts=_counts(engine)
        return {"status":"ok","version":VERSION,"counts":counts,"routes":checks,
                "all_registered":all(x["route_exists"] for x in checks)}

    @app.get("/api/v7.2/properties")
    def api_properties(req:Request,q:str=Query(""),transaction:str=Query(""),limit:int=Query(500,ge=1,le=2000)):
        _role(core,req);rows=_search_properties(engine,q,transaction,limit)
        return {"status":"ok","count":len(rows),"rows":rows}

    @app.get("/api/v7.2/requirements")
    def api_requirements(req:Request,q:str=Query(""),transaction:str=Query(""),limit:int=Query(500,ge=1,le=2000)):
        _role(core,req);rows=_search_requirements(engine,q,transaction,limit)
        return {"status":"ok","count":len(rows),"rows":rows}

    @app.post("/api/v7.2/verify/{entity_type}/{canonical_id}")
    def api_verify(entity_type:str,canonical_id:str,req:Request):
        _role(core,req);et=entity_type.upper()
        if et not in {"PROPERTY","REQUIREMENT"}:raise HTTPException(400,"entity_type must be PROPERTY or REQUIREMENT")
        table="pi_master_properties_v711" if et=="PROPERTY" else "pi_master_requirements_v711"
        with engine.begin() as c:
            if not c.execute(text(f"SELECT 1 FROM {table} WHERE canonical_id=:id"),{"id":canonical_id}).first():raise HTTPException(404,"Entity not found")
            c.execute(text("""INSERT INTO pi_master_workflow_v720(canonical_id,entity_type,verification_status,verified_at,verified_by,availability_status)
              VALUES(:id,:et,'VERIFIED',NOW(),:by,'AVAILABLE')
              ON CONFLICT(canonical_id) DO UPDATE SET verification_status='VERIFIED',verified_at=NOW(),verified_by=EXCLUDED.verified_by,
              availability_status=CASE WHEN pi_master_workflow_v720.availability_status='UNKNOWN' THEN 'AVAILABLE' ELSE pi_master_workflow_v720.availability_status END,updated_at=NOW()"""),
              {"id":canonical_id,"et":et,"by":_actor(core,req)})
        return {"status":"VERIFIED","canonical_id":canonical_id}

    @app.post("/api/v7.2/match/{requirement_id}")
    def api_match(requirement_id:str,req:Request):
        _role(core,req);matches=_run_match(engine,requirement_id)
        return {"status":"ok","requirement_id":requirement_id,"matches":matches}

    @app.get("/api/v7.2/client-draft/{requirement_id}")
    def api_client_draft(requirement_id:str,req:Request):
        _role(core,req)
        with engine.connect() as c:
            rr=c.execute(text("SELECT * FROM pi_master_requirements_v711 WHERE canonical_id=:id"),{"id":requirement_id}).mappings().first()
        if not rr:raise HTTPException(404,"Requirement not found")
        matches=_run_match(engine,requirement_id)
        # Privacy hard gate: only verified properties in outbound draft and no contacts.
        verified=[m for m in matches if m["property"].get("verification_status")=="VERIFIED"]
        return {"status":"ok","message":_client_message(_safe(dict(rr)),verified),"verified_options":len(verified),
                "privacy":{"owner_broker_contacts_included":False}}

    @app.get("/alliance",response_class=HTMLResponse)
    def home(req:Request):
        _role(core,req);counts=_counts(engine);checks=_audit_routes(core)
        cards="".join(f"<div class='card'><div class='muted'>{html.escape(k.title())}</div><div class='num'>{v}</div></div>" for k,v in counts.items())
        routes="".join(f"<tr><td>{html.escape(x['label'])}</td><td>{html.escape(x['path'])}</td><td class='{'ok' if x['route_exists'] else 'bad'}'>{'READY' if x['route_exists'] else 'MISSING'}</td></tr>" for x in checks)
        body=f"<div class='grid'>{cards}</div><div class='card'><h3>System Link Health</h3><div class='tablebox'><table><tr><th>Link</th><th>Route</th><th>Status</th></tr>{routes}</table></div></div>"
        return HTMLResponse(_shell(core,req,"Command Centre",body))

    @app.get("/alliance/properties",response_class=HTMLResponse)
    def properties_page(req:Request,q:str=Query(""),transaction:str=Query("")):
        _role(core,req);rows=_search_properties(engine,q,transaction,1000)
        form=f"""<form class='inline'><input name='q' value='{html.escape(q,quote=True)}' placeholder='Search locality, city, property'>
        <select name='transaction'><option value=''>All</option><option {'selected' if transaction=='SALE' else ''}>SALE</option><option {'selected' if transaction=='RENT' else ''}>RENT</option></select><button class='btn'>Search</button></form>"""
        cols=[("canonical_id","ID"),("locality","Locality"),("city","City"),("transaction_type","Transaction"),("area_sqft_display","Sq Ft"),
              ("area_sqyd","Sq Yd"),("area_sqm","Sq M"),("area_acre","Acre"),("sale_amount","Sale Amount"),("rent_amount","Rent Amount"),
              ("phones","Internal Contact"),("verification_status","Verification"),("source_count","Sources")]
        return HTMLResponse(_shell(core,req,f"Master Property Database · {len(rows)} shown",form+_table(rows,cols)))

    @app.get("/alliance/requirements",response_class=HTMLResponse)
    def requirements_page(req:Request,q:str=Query(""),transaction:str=Query("")):
        _role(core,req);rows=_search_requirements(engine,q,transaction,500)
        form=f"""<form class='inline'><input name='q' value='{html.escape(q,quote=True)}' placeholder='Search requirement'>
        <select name='transaction'><option value=''>All</option><option {'selected' if transaction=='SALE' else ''}>SALE</option><option {'selected' if transaction=='RENT' else ''}>RENT</option></select><button class='btn'>Search</button></form>"""
        cols=[("canonical_id","ID"),("locality","Preferred Location"),("city","City"),("transaction_type","Transaction"),("area_sqft_display","Sq Ft"),
              ("area_sqyd","Sq Yd"),("area_sqm","Sq M"),("sale_budget","Sale Budget"),("rent_budget","Rent Budget"),("phones","Internal Contact"),
              ("verification_status","Verification"),("source_count","Sources")]
        return HTMLResponse(_shell(core,req,f"Master Requirement Database · {len(rows)} shown",form+_table(rows,cols)))

    @app.get("/alliance/matcher",response_class=HTMLResponse)
    def matcher_page(req:Request,requirement_id:str=Query("")):
        _role(core,req);reqs=_search_requirements(engine,limit=100)
        options="".join(f"<option value='{html.escape(x['canonical_id'],quote=True)}' {'selected' if requirement_id==x['canonical_id'] else ''}>{html.escape((x.get('locality') or 'Requirement')+' · '+x['canonical_id'])}</option>" for x in reqs)
        form=f"<form class='inline'><select name='requirement_id'><option value=''>Choose requirement</option>{options}</select><button class='btn'>Run Match</button></form>"
        if not requirement_id:return HTMLResponse(_shell(core,req,"AI Property Matcher",form+"<div class='card'>Choose a requirement. Matching uses transaction, locality/city, area fit and verification.</div>"))
        matches=_run_match(engine,requirement_id)
        data=[]
        for m in matches:
            p=dict(m["property"]);p["match_score"]=m["score"];p["match_reasons"]=", ".join(m["reasons"]);data.append(p)
        cols=[("match_score","Score"),("locality","Property"),("transaction_type","Transaction"),("area_sqft_display","Sq Ft"),("sale_amount","Sale"),
              ("rent_amount","Rent"),("verification_status","Verified"),("phones","Internal Contact"),("match_reasons","Why")]
        return HTMLResponse(_shell(core,req,"AI Property Matcher",form+_table(data,cols)))

    STATE.update(status="READY",result={"version":VERSION,"counts":_counts(engine),"routes":_audit_routes(core)})
    return STATE

def start(core):
    try:return register(core)
    except Exception as exc:
        STATE.update(status="ERROR",last_error=f"{type(exc).__name__}: {exc}");return STATE
