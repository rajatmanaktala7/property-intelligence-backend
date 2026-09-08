from __future__ import annotations

import html
from urllib.parse import quote_plus
from fastapi import Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION = "12.4.14-LIVE-BOT-DATABASE-PAGES"

def _app(core):
    return getattr(core, "app", None) or core

def _remove_get(app, paths):
    paths=set(paths)
    kept=[]
    removed=[]
    for r in list(app.router.routes):
        methods=set(getattr(r,"methods",set()) or set())
        if getattr(r,"path",None) in paths and "GET" in methods:
            removed.append(getattr(r,"path",None))
        else:
            kept.append(r)
    app.router.routes[:]=kept
    return removed

def _e(v):
    return html.escape("" if v is None else str(v), quote=True)

def _shell(title, body):
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)}</title>
<style>
*{{box-sizing:border-box}}body{{font-family:Arial;margin:0;background:#f5f7fb;color:#172033}}
.wrap{{max-width:1800px;margin:auto;padding:18px}}.card{{background:#fff;border:1px solid #dfe6ee;border-radius:12px;padding:14px;margin-bottom:14px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}}
.metric{{background:#f8fafc;border:1px solid #e4e7ec;border-radius:10px;padding:12px}}.metric strong{{font-size:26px;display:block}}
table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{padding:8px;border-bottom:1px solid #edf1f5;text-align:left;vertical-align:top}}
th{{background:#f8fafc;position:sticky;top:0}}.tablebox{{overflow:auto;max-height:68vh}}
input,select{{padding:8px;border:1px solid #cfd8e3;border-radius:7px}}button,.btn{{background:#102a43;color:#fff;border:0;border-radius:7px;padding:8px 10px;text-decoration:none;cursor:pointer}}
.good{{background:#ecfdf3;border:1px solid #abefc6;color:#067647;padding:10px;border-radius:8px}}
.warn{{background:#fff7e6;border:1px solid #f3cf8c;color:#7a2e0e;padding:10px;border-radius:8px}}
.pager{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:10px}}
.muted{{color:#667085}}h1{{margin-top:0}}
</style></head><body><div class="wrap">{body}</div></body></html>"""

def _login(core, req):
    fn=getattr(core,"need_login",None)
    return fn(req) if fn else "team"

def _hospitality_page(core, req, page=1, per_page=100, category="", search="", msg=""):
    _login(core, req)
    import alliance_v31_hospitality as h
    e=core.engine
    ready=h._schema_ready(e)
    page=max(1,int(page or 1))
    per_page=max(25,min(int(per_page or 100),200))
    offset=(page-1)*per_page

    if not ready:
        body=f"""<div class="card"><h1>Hospitality Intelligence</h1>
        <div class="warn">Hospitality persistent schema is not ready.</div>
        <form method="post" action="/hospitality-intelligence/setup"><button>Initialize Safe Hospitality Storage</button></form></div>"""
        return HTMLResponse(_shell("Hospitality Intelligence",body),headers={"Cache-Control":"no-store"})

    clauses=["active=TRUE"]
    params={"lim":per_page,"off":offset}
    if category:
        clauses.append("category=:category"); params["category"]=category.upper()
    if search:
        clauses.append("""(
            LOWER(COALESCE(business_name,'')) LIKE :q OR
            LOWER(COALESCE(location,'')) LIKE :q OR
            LOWER(COALESCE(city,'')) LIKE :q OR
            LOWER(COALESCE(contact_name,'')) LIKE :q OR
            LOWER(COALESCE(contact_phone,'')) LIKE :q OR
            LOWER(COALESCE(email,'')) LIKE :q
        )""")
        params["q"]=f"%{search.lower()}%"
    where=" AND ".join(clauses)

    with e.connect() as c:
        total=int(c.execute(text(f"SELECT COUNT(*) FROM ai_hospitality_entity WHERE {where}"),params).scalar() or 0)
        all_total=int(c.execute(text("SELECT COUNT(*) FROM ai_hospitality_entity WHERE active=TRUE")).scalar() or 0)
        source_total=int(c.execute(text("SELECT COUNT(*) FROM ai_hospitality_source_history")).scalar() or 0)
        run_total=int(c.execute(text("SELECT COUNT(*) FROM ai_hospitality_run_history")).scalar() or 0)
        rows=[dict(x) for x in c.execute(text(f"""
          SELECT hospitality_id,business_name,category,location,city,contact_name,
                 contact_phone,whatsapp_phone,email,website,verification_status,
                 outreach_status,assigned_to,first_seen_at,last_seen_at,last_verified_at
          FROM ai_hospitality_entity
          WHERE {where}
          ORDER BY last_seen_at DESC,hospitality_id DESC
          LIMIT :lim OFFSET :off
        """),params).mappings().all()]
        runs=[dict(x) for x in c.execute(text("""
          SELECT run_id,category,query_text,provider,status,fetched_count,
                 inserted_or_updated,error_message,started_at,completed_at
          FROM ai_hospitality_run_history
          ORDER BY run_id DESC LIMIT 20
        """)).mappings().all()]

    cats=["","RESTAURANT","CAFE","LOUNGE","CLUB","BANQUET","GUEST_HOUSE","HOTEL","BAR","CLOUD_KITCHEN","OTHER"]
    opts="".join(f"<option value='{_e(x)}' {'selected' if x==category else ''}>{_e(x or 'ALL CATEGORIES')}</option>" for x in cats)
    trs="".join(
        "<tr>"
        f"<td><b>{_e(x.get('business_name'))}</b><br><span class='muted'>ID {_e(x.get('hospitality_id'))}</span></td>"
        f"<td>{_e(x.get('category'))}</td><td>{_e(x.get('location'))}<br>{_e(x.get('city'))}</td>"
        f"<td>{_e(x.get('contact_name'))}</td><td>{_e(x.get('contact_phone'))}<br>{_e(x.get('whatsapp_phone'))}</td>"
        f"<td>{_e(x.get('email'))}</td><td>{_e(x.get('website'))}</td>"
        f"<td>{_e(x.get('verification_status'))}</td><td>{_e(x.get('outreach_status'))}</td>"
        f"<td>{_e(x.get('assigned_to'))}</td><td>{_e(x.get('last_seen_at'))}</td>"
        f"<td><a class='btn' href='/api/v3/hospitality/sources/{int(x.get('hospitality_id'))}'>Source History</a></td>"
        "</tr>" for x in rows
    ) or "<tr><td colspan='12'>No hospitality records found for this filter.</td></tr>"

    run_trs="".join(
        f"<tr><td>{_e(x.get('run_id'))}</td><td>{_e(x.get('category'))}</td><td>{_e(x.get('query_text'))}</td>"
        f"<td>{_e(x.get('provider'))}</td><td>{_e(x.get('status'))}</td><td>{_e(x.get('fetched_count'))}</td>"
        f"<td>{_e(x.get('inserted_or_updated'))}</td><td>{_e(x.get('started_at'))}</td><td>{_e(x.get('error_message'))}</td></tr>"
        for x in runs
    ) or "<tr><td colspan='9'>No discovery runs stored yet.</td></tr>"

    last=max(1,(total+per_page-1)//per_page)
    base=f"/hospitality-intelligence?per_page={per_page}&category={quote_plus(category)}&search={quote_plus(search)}"
    prev=f"{base}&page={max(1,page-1)}"
    nxt=f"{base}&page={min(last,page+1)}"
    notice=f"<div class='good'>{_e(msg)}</div>" if msg else ""

    body=f"""{notice}
    <div class="card"><h1>Hospitality Intelligence · Complete Persistent Database</h1>
    <p class="muted">Previous fetched records, adopted legacy records and new bot discoveries are shown from the permanent hospitality database. Nothing is recreated from scratch on this page.</p>
    <div class="grid">
      <div class="metric"><span>Active Hospitality Records</span><strong>{all_total:,}</strong></div>
      <div class="metric"><span>Current Filter</span><strong>{total:,}</strong></div>
      <div class="metric"><span>Source History Rows</span><strong>{source_total:,}</strong></div>
      <div class="metric"><span>Bot Runs</span><strong>{run_total:,}</strong></div>
    </div></div>

    <div class="card"><h2>Search Full Previous Database</h2>
    <form method="get">
      <select name="category">{opts}</select>
      <input name="search" value="{_e(search)}" placeholder="Business / location / contact / email" size="38">
      <select name="per_page"><option { 'selected' if per_page==50 else ''}>50</option><option { 'selected' if per_page==100 else ''}>100</option><option { 'selected' if per_page==200 else ''}>200</option></select>
      <button>Search Database</button>
    </form></div>

    <div class="card"><h2>Bot / Legacy Actions</h2>
    <form method="post" action="/hospitality-intelligence/discover" style="display:inline-block;margin-right:8px">
      <select name="category">{"".join(f"<option>{_e(x)}</option>" for x in cats if x)}</select>
      <input name="location" value="Delhi NCR" placeholder="Location">
      <button>Run Hospitality Discovery</button>
    </form>
    <form method="post" action="/hospitality-intelligence/adopt-legacy" style="display:inline-block">
      <button>Re-scan Previous Legacy Hospitality Tables</button>
    </form></div>

    <div class="card"><h2>Stored Hospitality Records</h2>
    <div class="tablebox"><table><tr><th>Business</th><th>Category</th><th>Location</th><th>Contact</th><th>Phone / WhatsApp</th><th>Email</th><th>Website</th><th>Verification</th><th>Outreach</th><th>Assigned</th><th>Last Seen</th><th>Evidence</th></tr>{trs}</table></div>
    <div class="pager"><a class="btn" href="{prev}">Previous</a><b>Page {page} of {last}</b><a class="btn" href="{nxt}">Next</a><span>{total:,} matching records</span></div></div>

    <div class="card"><h2>Recent Bot Run History</h2><div class="tablebox"><table>
    <tr><th>Run</th><th>Category</th><th>Query</th><th>Provider</th><th>Status</th><th>Fetched</th><th>Saved</th><th>Started</th><th>Error</th></tr>{run_trs}</table></div></div>
    """
    return HTMLResponse(_shell("Hospitality Intelligence",body),headers={"Cache-Control":"no-store"})

def _retail_page(core, req, page=1, per_page=100, msg=""):
    _login(core, req)
    import alliance_v32_retail_expansion as r
    e=core.engine
    ready=r._schema_ready(e)
    page=max(1,int(page or 1)); per_page=max(25,min(int(per_page or 100),200)); off=(page-1)*per_page
    if not ready:
        body="""<div class="card"><h1>Retail Expansion Intelligence</h1><div class="warn">Retail persistent schema is not ready.</div>
        <form method="post" action="/retail-expansion/setup"><button>Initialize Safe Retail Storage</button></form></div>"""
        return HTMLResponse(_shell("Retail Expansion",body),headers={"Cache-Control":"no-store"})
    with e.connect() as c:
        contacts=int(c.execute(text("SELECT COUNT(*) FROM ai_retail_contact WHERE active=TRUE")).scalar() or 0)
        signals=int(c.execute(text("SELECT COUNT(*) FROM ai_retail_expansion_signal")).scalar() or 0)
        candidates=int(c.execute(text("SELECT COUNT(*) FROM ai_retail_requirement_candidate")).scalar() or 0)
        rows=[dict(x) for x in c.execute(text("""
          SELECT retail_contact_id,person_name,designation,company_name,category,
                 linkedin_profile_url,city,verification_status,last_seen_at
          FROM ai_retail_contact WHERE active=TRUE
          ORDER BY last_seen_at DESC,retail_contact_id DESC
          LIMIT :lim OFFSET :off
        """),{"lim":per_page,"off":off}).mappings().all()]
        sigs=[dict(x) for x in c.execute(text("""
          SELECT signal_id,company_name,category,headline,source_name,source_url,
                 intent_score,intent_status,location_signal,last_seen_at
          FROM ai_retail_expansion_signal
          ORDER BY intent_score DESC,signal_id DESC LIMIT 100
        """)).mappings().all()]
    trs="".join(f"<tr><td>{_e(x.get('person_name'))}</td><td>{_e(x.get('designation'))}</td><td>{_e(x.get('company_name'))}</td><td>{_e(x.get('category'))}</td><td>{_e(x.get('city'))}</td><td>{_e(x.get('verification_status'))}</td><td><a href='{_e(x.get('linkedin_profile_url'))}'>Profile</a></td><td>{_e(x.get('last_seen_at'))}</td></tr>" for x in rows) or "<tr><td colspan='8'>No stored retail contacts.</td></tr>"
    sigtrs="".join(f"<tr><td>{_e(x.get('company_name'))}</td><td>{_e(x.get('category'))}</td><td>{_e(x.get('headline'))}</td><td>{_e(x.get('intent_score'))}</td><td>{_e(x.get('intent_status'))}</td><td>{_e(x.get('location_signal'))}</td><td><a href='{_e(x.get('source_url'))}'>Source</a></td></tr>" for x in sigs) or "<tr><td colspan='7'>No stored expansion signals.</td></tr>"
    body=f"""<div class="card"><h1>Retail Expansion Intelligence · Persistent Database</h1>
    <div class="grid"><div class="metric"><span>Contacts</span><strong>{contacts:,}</strong></div><div class="metric"><span>Expansion Signals</span><strong>{signals:,}</strong></div><div class="metric"><span>Requirement Candidates</span><strong>{candidates:,}</strong></div></div></div>
    <div class="card"><h2>Discovery Actions</h2>
    <form method="post" action="/retail-expansion/discover-linkedin" style="display:inline-block"><input name="category" value="OTHER"><input name="location" value="India"><button>Discover Public Profiles</button></form>
    <form method="post" action="/retail-expansion/discover-news" style="display:inline-block"><input name="category" value="ALL"><button>Discover Expansion News</button></form></div>
    <div class="card"><h2>Stored Retail Contacts</h2><div class="tablebox"><table><tr><th>Person</th><th>Designation</th><th>Company</th><th>Category</th><th>City</th><th>Verification</th><th>Profile</th><th>Last Seen</th></tr>{trs}</table></div></div>
    <div class="card"><h2>Latest Expansion Signals</h2><div class="tablebox"><table><tr><th>Company</th><th>Category</th><th>Headline</th><th>Score</th><th>Status</th><th>Location</th><th>Source</th></tr>{sigtrs}</table></div></div>"""
    return HTMLResponse(_shell("Retail Expansion Intelligence",body),headers={"Cache-Control":"no-store"})

def register(core):
    app=_app(core)
    removed=_remove_get(app,{
        "/hospitality-intelligence","/v3/hospitality-intelligence",
        "/retail-expansion","/v3/retail-expansion-intelligence",
    })

    @app.get("/hospitality-intelligence",response_class=HTMLResponse)
    @app.get("/v3/hospitality-intelligence",response_class=HTMLResponse)
    def hospitality(req:Request,page:int=Query(1),per_page:int=Query(100),category:str=Query(""),search:str=Query(""),msg:str=Query("")):
        return _hospitality_page(core,req,page,per_page,category,search,msg)

    @app.post("/hospitality-intelligence/setup")
    def hospitality_setup(req:Request):
        _login(core,req)
        import alliance_v31_hospitality as h
        h.ensure_schema_safe(core.engine)
        return RedirectResponse("/hospitality-intelligence?msg=Hospitality+storage+ready",303)

    @app.post("/hospitality-intelligence/adopt-legacy")
    def hospitality_adopt(req:Request):
        _login(core,req)
        import alliance_v31_hospitality as h
        out=h.adopt_legacy_tables(core.engine)
        msg=f"Legacy scan complete: {out.get('rows_adopted_or_merged',0)} rows adopted or merged"
        return RedirectResponse("/hospitality-intelligence?msg="+quote_plus(msg),303)

    @app.post("/hospitality-intelligence/discover")
    def hospitality_discover(req:Request,category:str=Form("RESTAURANT"),location:str=Form("Delhi NCR")):
        _login(core,req)
        import alliance_v31_hospitality as h
        out=h.run_discovery(core.engine,category,location,8)
        msg=f"Discovery complete: fetched {out.get('fetched_count',0)}, saved {out.get('saved_permanently',0)}"
        return RedirectResponse("/hospitality-intelligence?msg="+quote_plus(msg),303)

    @app.get("/retail-expansion",response_class=HTMLResponse)
    @app.get("/v3/retail-expansion-intelligence",response_class=HTMLResponse)
    def retail(req:Request,page:int=Query(1),per_page:int=Query(100),msg:str=Query("")):
        return _retail_page(core,req,page,per_page,msg)

    @app.post("/retail-expansion/setup")
    def retail_setup(req:Request):
        _login(core,req)
        import alliance_v32_retail_expansion as r
        r.ensure_schema_safe(core.engine)
        return RedirectResponse("/retail-expansion?msg=Retail+storage+ready",303)

    @app.post("/retail-expansion/discover-linkedin")
    def retail_linkedin(req:Request,category:str=Form("OTHER"),location:str=Form("India")):
        _login(core,req)
        import alliance_v32_retail_expansion as r
        out=r.discover_linkedin_people(core.engine,category,location,8)
        msg=f"Profile discovery finished: {out.get('saved_permanently',out.get('saved',0))} saved"
        return RedirectResponse("/retail-expansion?msg="+quote_plus(msg),303)

    @app.post("/retail-expansion/discover-news")
    def retail_news(req:Request,category:str=Form("ALL")):
        _login(core,req)
        import alliance_v32_retail_expansion as r
        out=r.discover_indiaretailing_signals(core.engine,category,8)
        msg=f"News discovery finished: {out.get('saved_permanently',out.get('saved',0))} saved"
        return RedirectResponse("/retail-expansion?msg="+quote_plus(msg),303)

    @app.get("/api/alliance/bot-pages-status")
    def status():
        import alliance_v31_hospitality as h
        import alliance_v32_retail_expansion as r
        return {
            "status":"PASS",
            "version":VERSION,
            "hospitality_schema_ready":h._schema_ready(core.engine),
            "retail_schema_ready":r._schema_ready(core.engine),
            "hospitality_page":"/hospitality-intelligence",
            "retail_page":"/retail-expansion",
            "previous_data_preserved":True,
            "database_mutation_on_page_load":False,
        }

    return {
        "status":"AUTHORITATIVE",
        "version":VERSION,
        "removed_old_bot_get_routes":removed,
        "hospitality_full_database_visible":True,
        "retail_persistent_database_visible":True,
    }
