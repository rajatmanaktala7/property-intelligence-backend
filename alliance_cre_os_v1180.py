from __future__ import annotations
import html
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
import alliance_cre_os_v1171 as v1171
import alliance_cre_os_v1100 as dashboard_old

VERSION="11.8.0-ONE-TIME-UI-AUTHORITY"
SOURCE_KEYS=("manual","newspaper","magazine","whatsapp")

def _methods(r): return set(getattr(r,"methods",set()) or set())
def _remove(app,path,methods=("GET",)):
    methods=set(methods)
    app.router.routes[:]=[r for r in list(app.router.routes) if not (getattr(r,"path",None)==path and (_methods(r)&methods))]
def _front(app,path):
    rs=[r for r in list(app.router.routes) if getattr(r,"path",None)==path]
    for r in rs:
        try: app.router.routes.remove(r)
        except ValueError: pass
    for r in reversed(rs): app.router.routes.insert(0,r)
    return len(rs)
def _count(engine,table):
    try:
        with engine.connect() as c: return int(c.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0)
    except Exception: return 0

def _hub(engine):
    counts={"master":_count(engine,"pi_master_properties_v711"),"newspaper":_count(engine,"pi_newspaper_properties"),
    "whatsapp":_count(engine,"pi_whatsapp_property_master"),"magazine":_count(engine,"pi_magazine_master"),
    "manual":_count(engine,"pi_operational_properties")}
    cards=[("Master Database",counts["master"],"/alliance/final/database/master","Canonical unique properties"),
    ("Newspaper Database",counts["newspaper"],"/alliance/source/newspaper","FULL source-table records"),
    ("WhatsApp Database",counts["whatsapp"],"/alliance/source/whatsapp","FULL source-table records"),
    ("Magazine Database",counts["magazine"],"/alliance/source/magazine","FULL source-table records"),
    ("Manual Database",counts["manual"],"/alliance/source/manual","FULL source-table records")]
    body="".join('<a class="db" href="%s"><b>%s</b><strong>%s</strong><span>%s</span><em>Open Database</em></a>' %
        (u,html.escape(n),format(c,","),html.escape(note)) for n,c,u,note in cards)
    return v1171.shell('<h2>Property Databases</h2><p>Source cards use exact live source-table counts. Master is canonical and is not the sum of source rows because source records can overlap.</p><div style="display:grid;grid-template-columns:repeat(5,minmax(180px,1fr));gap:8px">'+body+'</div><style>.db{display:flex;flex-direction:column;min-height:145px;padding:14px;background:#fff;border:1px solid #667085;color:#172033;text-decoration:none}.db strong{font-size:30px;margin:8px 0}.db span{color:#667085;flex:1}.db em{font-style:normal;color:#174ea6;font-weight:bold;margin-top:10px}</style>')

def _dashboard(engine):
    page=dashboard_old._dashboard(engine)
    replacements={'href="/property-manual"':'href="/alliance/property-add/manual"',
    '"/property-manual"':'"/alliance/property-add/manual"',
    'href="/alliance/final/databases"':'href="/alliance/property-databases"',
    '"/alliance/final/databases"':'"/alliance/property-databases"',
    '"/alliance/final/database/newspaper"':'"/alliance/source/newspaper"',
    '"/alliance/final/database/whatsapp"':'"/alliance/source/whatsapp"',
    '"/alliance/final/database/magazine"':'"/alliance/source/magazine"',
    '"/alliance/final/database/manual"':'"/alliance/source/manual"'}
    for a,b in replacements.items(): page=page.replace(a,b)
    badge='<div style="position:fixed;left:12px;bottom:12px;z-index:99999;background:#102a43;color:#fff;padding:7px 10px;border-radius:4px;font:700 11px Arial">CRE 11.8 LIVE</div>'
    return page.replace("</body>",badge+"</body>")

def register(wrapped):
    app=wrapped.app; core=wrapped.core; engine=core.engine
    result={"status":"REGISTERED","version":VERSION}
    result["v1171"]=v1171.register(wrapped)

    manual_paths=("/alliance/property-add/manual","/property-manual","/manual-property-v18","/manual-property","/fast-property-entry")
    for path in manual_paths:
        _remove(app,path,("GET","POST"))
        def add_get(req:Request):
            core.need_login(req)
            return HTMLResponse(v1171.add_form(),headers={"Cache-Control":"no-store","X-Alliance-CRE-Version":VERSION})
        async def add_post(req:Request):
            core.need_login(req)
            rid=await v1171.save_manual(engine,req)
            return RedirectResponse(f"/alliance/property-edit/manual/{rid}",status_code=303)
        app.add_api_route(path,add_get,methods=["GET"],include_in_schema=False)
        app.add_api_route(path,add_post,methods=["POST"],include_in_schema=False)

    for key in SOURCE_KEYS:
        alias=f"/alliance/final/database/{key}"
        _remove(app,alias,("GET",))
        def source_alias(req:Request,_key=key):
            core.need_login(req)
            qs=req.url.query
            url=f"/alliance/source/{_key}"+(("?"+qs) if qs else "")
            return RedirectResponse(url,status_code=307)
        app.add_api_route(alias,source_alias,methods=["GET"],include_in_schema=False)

    for path in ("/alliance/property-databases","/alliance/final/databases"):
        _remove(app,path,("GET",))
        def hub(req:Request):
            core.need_login(req)
            return HTMLResponse(_hub(engine),headers={"Cache-Control":"no-store","X-Alliance-CRE-Version":VERSION})
        app.add_api_route(path,hub,methods=["GET"],include_in_schema=False)

    for path in ("/alliance/primary","/team-dashboard-v376","/team-dashboard-live"):
        _remove(app,path,("GET",))
        def dash(req:Request):
            core.need_login(req)
            return HTMLResponse(_dashboard(engine),headers={"Cache-Control":"no-store, no-cache, must-revalidate, max-age=0","Pragma":"no-cache","Expires":"0","X-Alliance-CRE-Version":VERSION})
        app.add_api_route(path,dash,methods=["GET"],include_in_schema=False)

    _remove(app,"/admin/db-schema-probe-117",("GET",))

    order=["/alliance/primary","/team-dashboard-v376","/team-dashboard-live",
    "/alliance/property-add/manual","/property-manual","/manual-property-v18","/manual-property","/fast-property-entry",
    "/alliance/property-databases","/alliance/final/databases",
    "/alliance/final/database/manual","/alliance/final/database/newspaper","/alliance/final/database/whatsapp","/alliance/final/database/magazine",
    "/alliance/source/manual","/alliance/source/newspaper","/alliance/source/whatsapp","/alliance/source/magazine",
    "/alliance/property-edit/{source}/{record_id}","/alliance/property-archive/{source}/{record_id}",
    "/alliance/property-media/{source}/{record_id}","/alliance/media-file/{store}/{media_id}"]
    for path in reversed(order): _front(app,path)

    result["exact_counts"]={"master":_count(engine,"pi_master_properties_v711"),"newspaper":_count(engine,"pi_newspaper_properties"),
    "whatsapp":_count(engine,"pi_whatsapp_property_master"),"magazine":_count(engine,"pi_magazine_master"),
    "manual":_count(engine,"pi_operational_properties")}
    result["schema_probe_removed"]=not any(getattr(r,"path",None)=="/admin/db-schema-probe-117" for r in app.router.routes)
    result["route_count"]=len(app.router.routes)
    return result
