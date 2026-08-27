
from __future__ import annotations
from fastapi import APIRouter,Request
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute
from sqlalchemy import text
import alliance_auto_updater as updater

VERSION="4.5.0-LIVE-WHATSAPP-TAKEOVER"

def _esc(v):
    s=str(v or "")
    return (s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
             .replace('"',"&quot;").replace("'","&#39;"))

def _latest_generation(engine):
    try:
        with engine.connect() as c:
            return c.execute(text("""
              SELECT generation_id
              FROM pi_whatsapp_property_master_generation
              WHERE status='COMPLETED'
              ORDER BY completed_at DESC NULLS LAST,id DESC
              LIMIT 1
            """)).scalar()
    except Exception:
        return None

def _master_rows(engine,q="",limit=800):
    gen=_latest_generation(engine)
    if not gen:
        return gen,[]
    p={"g":gen,"lim":limit}
    where=""
    if q.strip():
        where="""AND (
          COALESCE(description,'') ILIKE :q OR
          COALESCE(area,'') ILIKE :q OR
          COALESCE(configuration_details,'') ILIKE :q OR
          COALESCE(price,'') ILIKE :q OR
          COALESCE(contact_name_number,'') ILIKE :q OR
          COALESCE(source,'') ILIKE :q
        )"""
        p["q"]="%"+q.strip()+"%"
    with engine.connect() as c:
        rows=c.execute(text(f"""
          SELECT record_id,lead_type,description,area,configuration_details,price,
                 contact_name_number,source,captured_on,verification,source_count
          FROM pi_whatsapp_property_master
          WHERE generation_id=:g {where}
          ORDER BY captured_on DESC NULLS LAST,id DESC
          LIMIT :lim
        """),p).mappings().all()
    return gen,rows

def _stats(engine):
    gen=_latest_generation(engine)
    if not gen:
        return {"generation":None,"count":0,"completed_at":None}
    with engine.connect() as c:
        r=c.execute(text("""
          SELECT g.completed_at,COUNT(p.id) property_count
          FROM pi_whatsapp_property_master_generation g
          LEFT JOIN pi_whatsapp_property_master p ON p.generation_id=g.generation_id
          WHERE g.generation_id=:g
          GROUP BY g.completed_at
        """),{"g":gen}).mappings().first()
    return {
        "generation":str(gen),
        "count":int((r or {}).get("property_count") or 0),
        "completed_at":(r or {}).get("completed_at"),
    }

def _raw_stats():
    try:
        import whatsapp_live_bridge as legacy
        if legacy.wa_engine is None:
            return {"accounts":0,"groups":0,"today":0,"latest":None}
        with legacy.wa_engine.connect() as c:
            return {
                "accounts":int(c.execute(text("SELECT COUNT(*) FROM wa_bridge_accounts WHERE active=TRUE")).scalar() or 0),
                "groups":int(c.execute(text("SELECT COUNT(*) FROM wa_bridge_groups WHERE active=TRUE")).scalar() or 0),
                "today":int(c.execute(text("SELECT COUNT(*) FROM wa_bridge_events WHERE created_at>=CURRENT_DATE")).scalar() or 0),
                "latest":c.execute(text("SELECT MAX(created_at) FROM wa_bridge_events")).scalar(),
            }
    except Exception:
        return {"accounts":0,"groups":0,"today":0,"latest":None}

def _page(title,body):
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>{_esc(title)}</title><style>
    *{{box-sizing:border-box}}body{{margin:0;font-family:Arial;background:#efe4d2;color:#2d261f}}
    header{{background:#5d4937;color:#fff;padding:18px 24px}}
    nav{{background:#fffdf9;padding:10px 18px;border-bottom:1px solid #dccdbb;display:flex;gap:8px;flex-wrap:wrap}}
    nav a{{text-decoration:none;color:#4d3d30;padding:8px 10px;border-radius:7px;font-weight:700}}
    main{{max-width:1650px;margin:auto;padding:18px}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}}
    .card{{background:#fffdf9;border:1px solid #dccdbb;border-radius:12px;padding:14px;margin-bottom:12px}}
    table{{width:100%;border-collapse:collapse;background:#fff}}th,td{{padding:9px;border-bottom:1px solid #eee0ce;text-align:left;vertical-align:top;font-size:12px}}
    th{{background:#f7ecdf;position:sticky;top:0}}input{{padding:10px;border:1px solid #d0c1af;border-radius:7px;width:100%}}
    button,.btn{{border:0;background:#6c543f;color:#fff;padding:9px 12px;border-radius:7px;text-decoration:none;cursor:pointer;font-weight:800}}
    .green{{background:#377a4b}}.muted{{color:#7a6b5c}}.desc{{min-width:420px;max-width:620px;line-height:1.4}}
    </style></head><body><header><h2 style='margin:0'>WhatsApp Live Property Intelligence</h2>
    <small>Live intake → canonical clean property master → dedupe → search → requirement matching</small></header>
    <nav><a href='/team-dashboard-v376'>← Dashboard</a><a href='/workspace'>Working Space</a>
    <a href='/whatsapp-live'>Live Dashboard</a><a href='/whatsapp-live/feed'>Live Property Feed</a>
    <a href='/whatsapp-property-master-v44'>Live Database</a><a href='/whatsapp-live/sources'>WhatsApp Sources</a>
    <a href='/whatsapp-live/requirements'>Requirements</a><a href='/whatsapp-live/raw-feed-v45'>Raw Audit Feed</a></nav>
    <main>{body}</main></body></html>"""

def register(core):
    app=core.app
    engine=core.engine
    router=APIRouter()

    # Remove ONLY old visible GET routes. Ingest/source/requirements routes remain untouched.
    kept=[]
    for route in app.router.routes:
        path=getattr(route,"path",None)
        methods=getattr(route,"methods",set()) or set()
        if isinstance(route,APIRoute) and path in {"/whatsapp-live","/whatsapp-live/feed"} and "GET" in methods:
            continue
        kept.append(route)
    app.router.routes[:] = kept

    @router.get("/api/v45/live/status")
    def status():
        updater.request_refresh(force=False)
        return {
            "version":VERSION,
            "status":"OK",
            "master":_stats(engine),
            "raw":_raw_stats(),
            "auto_updater":updater.STATE,
        }

    @router.get("/api/v45/live/properties")
    def properties(q:str="",limit:int=800):
        # Check for new source messages in background. Never block page response.
        updater.request_refresh(force=False)
        gen,rows=_master_rows(engine,q,min(max(limit,1),1500))
        out=[]
        for r in rows:
            d=dict(r)
            for k,v in list(d.items()):
                if hasattr(v,"isoformat"): d[k]=v.isoformat()
            out.append(d)
        return {"status":"OK","generation_id":str(gen) if gen else None,"count":len(out),"rows":out}

    @router.get("/whatsapp-live",response_class=HTMLResponse)
    def dashboard():
        updater.request_refresh(force=False)
        raw=_raw_stats()
        master=_stats(engine)
        body=f"""<h2>Live Intake Command Centre</h2><div class=grid>
        <div class=card>Active Mobile Numbers<h2>{raw['accounts']}</h2></div>
        <div class=card>Active Groups<h2>{raw['groups']}</h2></div>
        <div class=card>Raw Messages Today<h2>{raw['today']}</h2></div>
        <div class=card>Canonical Unique Properties<h2>{master['count']}</h2></div>
        </div>
        <div class=card><b>Latest raw WhatsApp:</b> {_esc(raw['latest'] or '—')}<br>
        <b>Clean master updated:</b> {_esc(master['completed_at'] or '—')}<br>
        <b>Generation:</b> {_esc(master['generation'] or 'Not ready')}</div>
        <div class=card><a class='btn green' href='/whatsapp-live/feed'>Search Live Property Feed</a>
        <a class=btn href='/whatsapp-property-master-v44'>Open Full Live Database</a>
        <a class=btn href='/whatsapp-live/sources'>Add Number / Group</a>
        <a class=btn href='/whatsapp-live/requirements'>Requirements</a></div>"""
        return HTMLResponse(_page("WhatsApp Live Dashboard",body))

    @router.get("/whatsapp-live/feed",response_class=HTMLResponse)
    def feed(request:Request):
        updater.request_refresh(force=False)
        q=str(request.query_params.get("q") or "").strip()
        gen,rows=_master_rows(engine,q,1000)
        trs="".join(f"""<tr>
        <td>{_esc(r['captured_on'] or '—')}</td><td><b>{_esc(r['lead_type'])}</b></td>
        <td class='desc'><b>{_esc(r['description'])}</b></td><td>{_esc(r['area'])}</td>
        <td>{_esc(r['configuration_details'])}</td><td><b>{_esc(r['price'])}</b></td>
        <td>{_esc(r['contact_name_number'])}</td><td>{_esc(r['source'])}</td>
        <td>{_esc(r['verification'])}</td><td>{_esc(r['source_count'])}</td></tr>""" for r in rows)
        body=f"""<h2>Live WhatsApp Property Feed</h2>
        <div class=card><form method=get style='display:grid;grid-template-columns:1fr auto;gap:8px'>
        <input name=q value='{_esc(q)}' placeholder='Search description, project, area, price, contact or WhatsApp group'>
        <button>Search</button></form>
        <p class=muted>{len(rows)} unique canonical properties · generation {_esc(gen or 'not ready')}</p></div>
        <div class=card style='overflow:auto'><table>
        <tr><th>Captured</th><th>Type</th><th>Description</th><th>Area</th><th>Configuration</th>
        <th>Price / Rent</th><th>Contact Name / Number</th><th>Source Group</th><th>Verification</th><th>Sources Merged</th></tr>
        {trs or '<tr><td colspan=10>No canonical properties yet.</td></tr>'}</table></div>
        <script>setTimeout(()=>{{ if(!document.querySelector("input").value) location.reload(); }},30000);</script>"""
        return HTMLResponse(_page("Live WhatsApp Property Feed",body))

    @router.get("/whatsapp-live/raw-feed-v45",response_class=HTMLResponse)
    def raw_feed():
        try:
            import whatsapp_live_bridge as legacy
            with legacy.wa_engine.connect() as c:
                rows=c.execute(text("""SELECT e.*,g.group_name,a.label account_label
                  FROM wa_bridge_events e
                  JOIN wa_bridge_groups g ON g.group_id=e.group_id
                  JOIN wa_bridge_accounts a ON a.account_id=g.account_id
                  ORDER BY e.id DESC LIMIT 300""")).mappings().all()
        except Exception:
            rows=[]
        trs="".join(f"""<tr><td>{_esc(r['created_at'])}</td><td>{_esc(r['group_name'])}</td>
        <td>{_esc(r['sender_name'] or r['sender_phone'])}</td><td style='max-width:650px;white-space:pre-wrap'>{_esc(r['raw_text'])}</td>
        <td>{_esc(r['classification'] or r['status'])}</td></tr>""" for r in rows)
        return HTMLResponse(_page("Raw WhatsApp Audit Feed",
          f"""<h2>Raw WhatsApp Audit Feed</h2><div class=card>
          <p class=muted>This keeps original incoming messages for audit. Use Live Property Feed for team search.</p>
          <table><tr><th>Received</th><th>Group</th><th>Sender</th><th>Raw Message</th><th>Result</th></tr>{trs}</table></div>"""))

    app.include_router(router)
    return router
