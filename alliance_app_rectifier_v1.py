from __future__ import annotations
import html, json, re, threading, time
from datetime import datetime, timezone
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy import inspect, text

VERSION="1.0.3-TEAM-WORKFLOW-RECTIFIER"
INVALID_LOCATIONS={"","missing","tara","unknown","none","null","na","n/a","-","—"}
_STATE={
    "status":"STARTING","version":VERSION,
    "matcher_source_contract":"MASTER_ONLY","automatic_send":False,
    "requirements_compact":False,"master_compact":False,
    "invalid_location_display_blocked":False,
    "newspaper_upload_target":"/newspaper-v83",
    "commercial_research_action":"RECTIFIER_WRAPPER",
    "whatsapp_location_worker":False,
    "whatsapp_locations_backfilled":0,
    "last_whatsapp_run":None,"last_error":None
}

def _esc(v): return html.escape("" if v is None else str(v),quote=True)

def _txt(v):
    if v is None:return ""
    if isinstance(v,dict):
        return " · ".join(f"{k}: {x}" for k,x in v.items() if x not in (None,"",[],{}))
    if isinstance(v,(list,tuple)):
        return " · ".join(str(x) for x in v if x not in (None,""))
    s=str(v)
    if s[:1] in ("{","["):
        try:return _txt(json.loads(s))
        except Exception:pass
    return re.sub(r"\s+"," ",s).strip()

def _clean_location(v):
    s=_txt(v).strip()
    return "Verify" if s.lower() in INVALID_LOCATIONS else s

def _cols(engine,table):
    try:return [x["name"] for x in inspect(engine).get_columns(table)]
    except Exception:return []

def _rv(r,*names):
    for n in names:
        if r.get(n) not in (None,"",[],{}):return r.get(n)
    cr=r.get("clean_record")
    if isinstance(cr,dict):
        for n in names:
            if cr.get(n) not in (None,"",[],{}):return cr.get(n)
    return ""

def _pick(cols,*names): return next((n for n in names if n in cols),None)

def _need(core,req):
    f=getattr(core,"need_login",None)
    if f:return f(req)

def _remove_exact(app,path,method=None):
    kept=[]
    for r in app.router.routes:
        if getattr(r,"path",None)!=path:
            kept.append(r);continue
        methods=set(getattr(r,"methods",[]) or [])
        if method and method not in methods:
            kept.append(r)
    app.router.routes[:]=kept

def _page(title,body):
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f5f7fb;color:#17233b;font-family:Arial,sans-serif}}
header{{background:#102a43;color:white;padding:12px 18px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}}
header h1{{margin:0}}.wrap{{padding:12px}}.bar{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}}
.btn{{display:inline-block;padding:7px 10px;border:1px solid #c9d3df;border-radius:7px;background:white;text-decoration:none;color:#17324d}}
.tablebox{{overflow:auto;background:white;border:1px solid #dce4ec;border-radius:9px}}
table{{border-collapse:collapse;width:100%;white-space:nowrap}}th,td{{padding:4px 6px;border-bottom:1px solid #edf1f5;text-align:left;vertical-align:top;line-height:1.15}}
th{{position:sticky;top:0;background:#eef3f8;z-index:1}}td.message{{white-space:normal;min-width:300px;max-width:520px}}.muted{{color:#637083}}
</style></head><body><header><h1>{_esc(title)}</h1><a class="btn" href="/alliance/primary">Dashboard</a></header><div class="wrap">{body}</div></body></html>"""

def _requirements(engine,source=None):
    table="pi_requirement_gate_v1191"; cols=_cols(engine,table)
    if not cols:return _page("Requirement Database","<p>Requirement authority unavailable.</p>")
    order=_pick(cols,"created_at","updated_at","id","requirement_id")
    params={"lim":500}; where=""
    if source and "source_type" in cols:
        where=" WHERE UPPER(COALESCE(source_type,''))=:src";params["src"]=source.upper()
    q=f'SELECT * FROM "{table}"{where}'+(f' ORDER BY "{order}" DESC' if order else "")+" LIMIT :lim"
    with engine.connect() as c:rows=[dict(x) for x in c.execute(text(q),params).mappings()]
    trs=[]
    for r in rows:
        rid=_rv(r,"requirement_id","id","source_pk")
        link=f"/alliance/master-requirement-matcher?category=ALL&requirement_id={html.escape(str(rid),quote=True)}#results"
        trs.append("<tr>"+
          f"<td>{_esc(rid)}</td><td>{_esc(_rv(r,'source_type','source_table'))}</td>"+
          f"<td class='message'>{_esc(_txt(_rv(r,'requirement_text','original_message','raw_text')))}</td>"+
          f"<td>{_esc(_clean_location(_rv(r,'locations','location','alternate_locations')))}</td>"+
          f"<td>{_esc(_txt(_rv(r,'property_category','property_type','asset_type','primary_asset')))}</td>"+
          f"<td>{_esc(_rv(r,'transaction','transaction_type'))}</td>"+
          f"<td>{_esc(_txt(_rv(r,'area','area_text','area_min','area_max')))}</td>"+
          f"<td>{_esc(_txt(_rv(r,'budget','budget_text','budget_min','budget_max')))}</td>"+
          f"<td>{_esc(_txt(_rv(r,'verification_status','status','classification')))}</td>"+
          f"<td><a href='{link}'>Match</a></td></tr>")
    body="""<div class="bar"><a class="btn" href="/alliance/final/requirements">All</a>
<a class="btn" href="/alliance/final/requirements/whatsapp">WhatsApp</a>
<a class="btn" href="/alliance/final/requirements/manual">Manual</a>
<a class="btn" href="/alliance/final/requirements/newspaper">Newspaper</a></div>"""
    body+=f"<div class='muted'>{len(rows)} rows shown · canonical authority: {table}</div><div class='tablebox'><table><tr><th>ID</th><th>Source</th><th>Requirement</th><th>Location</th><th>Asset</th><th>Transaction</th><th>Area</th><th>Budget</th><th>Status</th><th>Action</th></tr>{''.join(trs)}</table></div>"
    return _page("Requirement Database",body)

def _master(engine):
    table="pi_master_properties_v711"; cols=_cols(engine,table)
    if not cols:return _page("Master Property Database","<p>Master authority unavailable.</p>")
    order=_pick(cols,"created_at","updated_at","id","master_property_id","canonical_id")
    q=f'SELECT * FROM "{table}"'+(f' ORDER BY "{order}" DESC' if order else "")+" LIMIT 600"
    with engine.connect() as c:rows=[dict(x) for x in c.execute(text(q)).mappings()]
    trs=[]
    for r in rows:
        pid=_rv(r,"master_property_id","canonical_id","record_id","property_id","id")
        loc=_clean_location(_rv(r,"locality","location","micro_location","city"))
        trs.append("<tr>"+
          f"<td><a href='/alliance/primary/property/{html.escape(str(pid),quote=True)}'>{_esc(pid)}</a></td>"+
          f"<td>{_esc(_txt(_rv(r,'property_name','project_name','title','name')))}</td><td>{_esc(loc)}</td>"+
          f"<td>{_esc(_txt(_rv(r,'property_type','asset_type','asset_family','category')))}</td>"+
          f"<td>{_esc(_txt(_rv(r,'transaction','transaction_type','deal_type')))}</td>"+
          f"<td>{_esc(_txt(_rv(r,'area','area_text','area_sqft','builtup_area','plot_area')))}</td>"+
          f"<td>{_esc(_txt(_rv(r,'price','price_text','asking_price','rent','sale_price')))}</td>"+
          f"<td>{_esc(_txt(_rv(r,'verification_status','record_status','status')))}</td>"+
          f"<td>{_esc(_txt(_rv(r,'source_type','source','source_table')))}</td></tr>")
    body=f"<div class='muted'>{len(rows)} rows shown · invalid location tokens missing/tara are suppressed; records remain intact.</div><div class='tablebox'><table><tr><th>ID</th><th>Property</th><th>Location</th><th>Type</th><th>Transaction</th><th>Area</th><th>Price</th><th>Status</th><th>Source</th></tr>{''.join(trs)}</table></div>"
    return _page("Master Property Database",body)

def _commercial_html(engine,view,city,message):
    # Reuse the existing commercial intelligence renderer; inject the missing UI
    # only when the active renderer omits it. Existing POST research routes remain authoritative.
    import alliance_commercial_intelligence_ai as ci
    rendered=ci._render(engine,view,city,message)
    if "Research this asset" in rendered:
        return rendered
    rows=ci._load(engine,view,city)
    parts=rendered.split("</article>")
    if len(parts)<=1 or not rows:
        return rendered
    upto=min(len(rows),len(parts)-1)
    for i in range(upto):
        code=_esc(rows[i].get("asset_code"))
        button=f'<div class="actions"><form method="post" action="/commercial-intelligence/research/{code}"><button type="submit">Research this asset</button></form></div>'
        parts[i]=parts[i]+button
    return "</article>".join(parts)

def _wa_engine():
    try:
        import alliance_whatsapp_source_reconciliation_v2 as w
        f=getattr(w,"_wa_engine",None)
        return f() if f else None
    except Exception:return None

def _recover_location(raw):
    raw=_txt(raw)
    for modname in ("alliance_v3_property_data_quality","alliance_phase5_canonical_matcher"):
        try:
            mod=__import__(modname)
            for fnname in ("canonical_location","parse_location"):
                f=getattr(mod,fnname,None)
                if f:
                    v=f(raw)
                    if isinstance(v,(list,tuple)):v=v[0] if v else ""
                    if v and _clean_location(v)!="Verify":return _txt(v)
        except Exception:pass
    m=re.search(r"(?i)\b(?:location|locality|area)\s*[:\-]\s*([^|;\n]{2,80})",raw)
    if not m:return ""
    v=re.split(r"(?i)\b(?:budget|price|rent|sale|contact|call|dm|size|sq\s*ft|sqft|sqm|sqmt)\b",m.group(1))[0]
    v=re.sub(r"\s+"," ",v).strip(" ,.-")
    return v if 2<=len(v)<=60 and _clean_location(v)!="Verify" else ""

def _wa_backfill():
    eng=_wa_engine()
    if eng is None:return 0
    cols=_cols(eng,"wa_properties")
    if not {"wa_property_id","location"}.issubset(cols):return 0
    evidence=[x for x in ("raw_text","description","original_message","property_text","notes") if x in cols]
    if not evidence:return 0
    sel=", ".join(['"wa_property_id"']+[f'"{x}"' for x in evidence])
    filters=["COALESCE(BTRIM(location),'')=''"]
    if "record_status" in cols:filters.append("COALESCE(record_status,'ACTIVE')='ACTIVE'")
    order=" ORDER BY id DESC" if "id" in cols else ""
    q=f"SELECT {sel} FROM wa_properties WHERE {' AND '.join(filters)}{order} LIMIT 5000"
    changed=0
    with eng.begin() as c:
        for r in c.execute(text(q)).mappings():
            loc=_recover_location(" ".join(_txt(r.get(x)) for x in evidence if r.get(x)))
            if loc:
                res=c.execute(text("UPDATE wa_properties SET location=:loc WHERE wa_property_id=:pid AND COALESCE(BTRIM(location),'')=''"),
                              {"loc":loc,"pid":r["wa_property_id"]})
                changed+=int(res.rowcount or 0)
    _STATE["whatsapp_locations_backfilled"]+=changed
    _STATE["last_whatsapp_run"]=datetime.now(timezone.utc).isoformat()
    return changed

def _worker():
    _STATE["whatsapp_location_worker"]=True
    while True:
        try:_wa_backfill();_STATE["last_error"]=None
        except Exception as e:_STATE["last_error"]=f"{type(e).__name__}: {e}"
        time.sleep(600)

def register(core,served_app=None):
    app=getattr(core,"app",core);engine=getattr(core,"engine",None)
    if engine is None:raise RuntimeError("core engine unavailable")
    for p in ["/alliance/final/requirements","/alliance/final/requirements/master","/alliance/final/requirements/whatsapp",
              "/alliance/final/requirements/manual","/alliance/final/requirements/newspaper","/alliance/final/databases",
              "/alliance/newspaper-capture","/api/alliance/app-rectifier-v1/status"]:
        _remove_exact(app,p)
    # Replace only GET commercial UI; preserve existing POST research endpoints.
    _remove_exact(app,"/commercial-intelligence","GET")

    @app.get("/alliance/final/requirements",response_class=HTMLResponse)
    def req_all(req:Request):_need(core,req);return HTMLResponse(_requirements(engine))
    @app.get("/alliance/final/requirements/master",response_class=HTMLResponse)
    def req_master(req:Request):_need(core,req);return HTMLResponse(_requirements(engine))
    @app.get("/alliance/final/requirements/whatsapp",response_class=HTMLResponse)
    def req_wa(req:Request):_need(core,req);return HTMLResponse(_requirements(engine,"WHATSAPP"))
    @app.get("/alliance/final/requirements/manual",response_class=HTMLResponse)
    def req_manual(req:Request):_need(core,req);return HTMLResponse(_requirements(engine,"MANUAL"))
    @app.get("/alliance/final/requirements/newspaper",response_class=HTMLResponse)
    def req_news(req:Request):_need(core,req);return HTMLResponse(_requirements(engine,"NEWSPAPER"))
    @app.get("/alliance/final/databases",response_class=HTMLResponse)
    def master_db(req:Request):_need(core,req);return HTMLResponse(_master(engine))
    @app.get("/alliance/newspaper-capture")
    def newspaper(req:Request):_need(core,req);return RedirectResponse("/newspaper-v83",303)

    @app.get("/commercial-intelligence",response_class=HTMLResponse)
    def commercial(req:Request,view:str="ALL",city:str="",message:str=""):
        _need(core,req)
        return HTMLResponse(_commercial_html(engine,view,city,message))

    @app.get("/api/alliance/app-rectifier-v1/status")
    def status():
        routes=[(getattr(r,"path",None),set(getattr(r,"methods",[]) or [])) for r in app.router.routes]
        checks={
          "requirements":any(p=="/alliance/final/requirements" for p,_ in routes),
          "master_database":any(p=="/alliance/final/databases" for p,_ in routes),
          "newspaper_capture":any(p=="/alliance/newspaper-capture" for p,_ in routes),
          "commercial_get":any(p=="/commercial-intelligence" and "GET" in m for p,m in routes),
          "commercial_research_post":any(p=="/commercial-intelligence/research/{asset_code}" and "POST" in m for p,m in routes),
          "whatsapp_live":any(p=="/whatsapp-live" for p,_ in routes),
        }
        out=dict(_STATE);out["route_checks"]=checks;out["status"]="PASS" if all(checks.values()) else "FAIL"
        return JSONResponse(out)

    _STATE["requirements_compact"]=True;_STATE["master_compact"]=True;_STATE["invalid_location_display_blocked"]=True
    try:_wa_backfill()
    except Exception as e:_STATE["last_error"]=f"{type(e).__name__}: {e}"
    threading.Thread(target=_worker,daemon=True,name="alliance-wa-location-rectifier").start()
    _STATE["status"]="PASS"
    return dict(_STATE)
