from __future__ import annotations
import html, json
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION="11.6.0-UNIFIED-PROPERTY-DATABASES"
SOURCES={"manual":("pi_operational_properties","Manual"),"newspaper":("pi_newspaper_properties","Newspaper"),"magazine":("pi_magazine_master","Magazine"),"whatsapp":("pi_whatsapp_property_master","WhatsApp")}
ALIASES=("/property-manual","/manual-property-v18","/manual-property")

def esc(v): return html.escape("" if v is None else str(v))
def pick(d,*ks):
    low={str(k).lower():v for k,v in d.items()}
    for k in ks:
        v=low.get(k.lower())
        if v not in (None,"",[],{}): return v
    return ""
def remove_get(app,path):
    app.router.routes[:]=[r for r in list(app.router.routes) if not (getattr(r,"path",None)==path and "GET" in set(getattr(r,"methods",set()) or set()))]

def render_source(engine,key,q="",limit=200):
    table,label=SOURCES[key]
    with engine.connect() as c:
        if not c.execute(text("SELECT to_regclass(:n)"),{"n":"public."+table}).scalar():
            return f"<h2>{label} Database</h2><p>Source table unavailable.</p>"
        total=int(c.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0)
        rows=c.execute(text(f'''SELECT to_jsonb(t) FROM "{table}" t WHERE (:q='' OR to_jsonb(t)::text ILIKE :pat) LIMIT :lim'''),
                       {"q":q,"pat":f"%{q}%","lim":limit}).scalars().all()
    heads=["ID","Location","Description / Address","Category","Type","Area","Floor","Amount","Contact Name","Contact No.","Entry Date & Time","Status","Assigned To","Source","Media / Evidence"]
    trs=[]
    for raw in rows:
        d=raw if isinstance(raw,dict) else json.loads(raw)
        vals=[pick(d,"id","property_id","canonical_id"),pick(d,"location","locality","area_name","city"),
        pick(d,"description","address","property_description","original_description","original_message"),pick(d,"property_category","category"),
        pick(d,"property_type","type"),pick(d,"area","area_sqft","size","built_up_area"),pick(d,"floor"),pick(d,"amount","rent","sale_amount","price"),
        pick(d,"owner_name","broker_name","contact_name","name"),pick(d,"contact_number","contact_no","phone","mobile"),pick(d,"created_at","entry_date","created_on"),
        pick(d,"verification_status","status","availability_status"),pick(d,"assigned_to","team_member"),pick(d,"source","source_name") or label.upper(),
        pick(d,"media","images","image","image_url","image_path","video","video_url","media_url","attachment","attachments")]
        trs.append("<tr>"+"".join(f"<td>{esc(v)}</td>" for v in vals)+"</tr>")
    return f'''<div class="kpi"><b>{total:,}</b><span>Exact {label} source records</span></div>
<form><input name="q" value="{esc(q)}" placeholder="Search {label} database"><input type="hidden" name="limit" value="{limit}"><button>Search</button></form>
<div class="tablebox"><table><thead><tr>{''.join(f"<th>{esc(h)}</th>" for h in heads)}</tr></thead><tbody>{''.join(trs) if trs else '<tr><td colspan="15">No records</td></tr>'}</tbody></table></div>'''

def shell(body,active=""):
    tabs=''.join(f'<a href="/alliance/source/{k}" class="{("on" if active==k else "")}">{v[1]}</a>' for k,v in SOURCES.items())
    css='''*{box-sizing:border-box}body{margin:0;background:#f5f7fa;color:#172033;font:12px Arial}header{background:#102a43;color:white;padding:14px 18px}nav,.tabs{background:white;border-bottom:1px solid #98a2b3;padding:7px;white-space:nowrap;overflow:auto}a{text-decoration:none}nav a,.tabs a,button{display:inline-block;background:#102a43;color:white;padding:7px 9px;margin-right:5px;border:0}.tabs a.on{background:#486581}.wrap{padding:10px}.kpi{display:inline-flex;flex-direction:column;background:white;border:1px solid #667085;padding:10px 16px;margin:8px 0}.kpi b{font-size:25px}form{display:flex;gap:5px;margin:8px 0}input{padding:7px;border:1px solid #98a2b3;min-width:320px}.tablebox{overflow:auto;max-height:72vh;border:1px solid #667085}table{border-collapse:collapse;width:max-content;min-width:100%;background:white}th,td{border:1px solid #98a2b3;padding:5px 6px;vertical-align:top;overflow-wrap:anywhere}th{background:#e9eef5;position:sticky;top:0;z-index:2}td:nth-child(3){min-width:320px;max-width:480px}tbody tr:nth-child(even) td{background:#f8fafc}'''
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Alliance Property Databases</title><style>{css}</style></head>
<body><header><b>Alliance CRE Intelligence OS 11.6</b><br>Unified Property Source Databases · original evidence preserved</header>
<nav><a href="/alliance/primary">Command Centre</a><a href="/fast-property-entry?division=DELHI_NCR">Add Property</a><a href="/alliance/source/manual">Manual Database</a><a href="/commercial-intelligence">Commercial Intelligence</a></nav>
<div class="tabs">{tabs}</div><div class="wrap">{body}</div></body></html>'''

def register(wrapped):
    app=wrapped.app; core=wrapped.core; engine=core.engine
    for p in ALIASES:
        remove_get(app,p)
        async def go(req:Request):
            core.need_login(req); return RedirectResponse("/fast-property-entry?division=DELHI_NCR",status_code=307)
        app.add_api_route(p,go,methods=["GET"],include_in_schema=False)
    for key in SOURCES:
        path=f"/alliance/source/{key}"; remove_get(app,path)
        def page(req:Request,q:str="",limit:int=200,_key=key):
            core.need_login(req); limit=max(1,min(int(limit),500))
            return HTMLResponse(shell(render_source(engine,_key,q.strip(),limit),_key),headers={"Cache-Control":"no-store","X-Alliance-CRE-Version":"11.6.0"})
        app.add_api_route(path,page,methods=["GET"],response_class=HTMLResponse)
    remove_get(app,"/alliance/final/database/manual")
    def manual(req:Request,q:str="",limit:int=200):
        core.need_login(req); limit=max(1,min(int(limit),500))
        return HTMLResponse(shell(render_source(engine,"manual",q.strip(),limit),"manual"),headers={"Cache-Control":"no-store","X-Alliance-CRE-Version":"11.6.0"})
    app.add_api_route("/alliance/final/database/manual",manual,methods=["GET"],response_class=HTMLResponse)
    preferred=set(ALIASES)|{f"/alliance/source/{k}" for k in SOURCES}|{"/alliance/final/database/manual"}
    chosen=[r for r in list(app.router.routes) if getattr(r,"path",None) in preferred]
    for r in chosen:
        try: app.router.routes.remove(r)
        except ValueError: pass
    for r in reversed(chosen): app.router.routes.insert(0,r)
    return {"status":"REGISTERED","version":VERSION,"sources":list(SOURCES)}
