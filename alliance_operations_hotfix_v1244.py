from __future__ import annotations
import html,re
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import Form,Query,Request
from fastapi.responses import HTMLResponse,RedirectResponse
from sqlalchemy import text

VERSION="12.4.4-TEAM-OPERATIONS-CONSOLIDATED-FIX"
STAFF=("Yogesh Mehra","Priya","Zoya Saifi")
INDIA_TZ=ZoneInfo("Asia/Kolkata")
REGISTRATION={}

def _app(core): return getattr(core,"app",None) or core
def _engine(core): return getattr(core,"engine",None)
def _login(core,req):
    fn=getattr(core,"need_login",None); return fn(req) if fn else "team"
def _e(v): return html.escape("" if v is None else str(v),quote=True)
def _today(): return datetime.now(INDIA_TZ).date()
def _staff(v):
    s=str(v or "").strip(); return s if s in STAFF else STAFF[0]
def _remove_routes(app,specs):
    specs={(p,m.upper()) for p,m in specs}; kept=[]; removed=[]
    for r in list(app.router.routes):
        p=getattr(r,"path",None); methods=set(getattr(r,"methods",set()) or set())
        if any(p==sp and sm in methods for sp,sm in specs): removed.append((p,sorted(methods)))
        else: kept.append(r)
    app.router.routes[:]=kept; return removed
def _route_exists(app,path,method="GET"):
    return any(getattr(r,"path",None)==path and method.upper() in set(getattr(r,"methods",set()) or set()) for r in app.router.routes)
def _table_exists(engine,name):
    try:
        with engine.connect() as c:return bool(c.execute(text("SELECT to_regclass(:n) IS NOT NULL"),{"n":name}).scalar())
    except Exception:return False

def _install_staff_day_plan(core):
    app,engine=_app(core),_engine(core)
    import alliance_team_dashboard_v1220 as td
    def staff_today_india(e,name):
        d=_today()
        def scalar(sql):
            try:
                with e.connect() as c:return int(c.execute(text(sql),{"m":name,"d":d}).scalar() or 0)
            except Exception:return 0
        total=scalar("SELECT COUNT(*) FROM pi_team_daily_tasks_v1230 WHERE task_date=:d AND team_member=:m")
        done=scalar("SELECT COUNT(*) FROM pi_team_daily_tasks_v1230 WHERE task_date=:d AND team_member=:m AND status='DONE'")
        morning=bool(scalar("SELECT COUNT(*) FROM pi_team_daily_journal_v1230 WHERE task_date=:d AND team_member=:m AND COALESCE(morning_plan,'')<>''"))
        evening=bool(scalar("SELECT COUNT(*) FROM pi_team_daily_journal_v1230 WHERE task_date=:d AND team_member=:m AND COALESCE(evening_summary,'')<>''"))
        return {"total":total,"done":done,"open":max(0,total-done),"morning":morning,"evening":evening}
    td._staff_today=staff_today_india
    removed=_remove_routes(app,[
        ("/alliance/primary/day-plan","GET"),
        ("/alliance/primary/day-plan/save-morning","POST"),
        ("/alliance/primary/day-plan/add-task","POST"),
        ("/alliance/primary/day-plan/task/{task_id}/done","POST"),
        ("/alliance/primary/day-plan/save-evening","POST"),
    ])
    def shell(title,body):
        return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(title)}</title>
<style>*{{box-sizing:border-box}}body{{font-family:Arial;margin:0;background:#f5f7fb;color:#172033}}header{{background:#102a43;color:white;padding:18px 22px;display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}}header a,.btn{{background:#102a43;color:white;text-decoration:none;border:0;border-radius:8px;padding:9px 11px;cursor:pointer;display:inline-block}}header a{{background:white;color:#102a43;font-weight:800}}.wrap{{max-width:1400px;margin:auto;padding:18px}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}@media(max-width:850px){{.grid{{grid-template-columns:1fr}}}}.card{{background:white;border:1px solid #dfe6ee;border-radius:12px;padding:14px;margin-bottom:14px}}textarea,input,select{{width:100%;padding:9px;border:1px solid #cfd8e3;border-radius:7px;margin:5px 0}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{padding:8px;border-bottom:1px solid #edf1f5;text-align:left;vertical-align:top}}th{{background:#f8fafc}}.ok{{background:#ecfdf3;border:1px solid #abefc6;color:#067647;padding:10px;border-radius:8px;margin-bottom:12px}}.muted{{color:#667085}}.pill{{padding:4px 8px;border-radius:999px;background:#eef2f6;font-weight:800;font-size:11px}}</style></head>
<body><header><div><b>Alliance CRE · {_e(title)}</b><br><small>India business date · staff-specific permanent storage</small></div><a href="/alliance/primary">← Back to Main Dashboard</a></header><div class="wrap">{body}<p><a class="btn" href="/alliance/primary">← Back to Main Dashboard</a></p></div></body></html>"""
    @app.get("/alliance/primary/day-plan",response_class=HTMLResponse)
    def day_plan(req:Request,staff:str=Query("Yogesh Mehra"),saved:str=Query("")):
        _login(core,req); name=_staff(staff); d=_today()
        with engine.connect() as c:
            j=c.execute(text("SELECT morning_plan,evening_summary,updated_at FROM pi_team_daily_journal_v1230 WHERE task_date=:d AND team_member=:m"),{"d":d,"m":name}).mappings().first()
            tasks=[dict(x) for x in c.execute(text("SELECT id,task_text,status,outcome,created_at,completed_at FROM pi_team_daily_tasks_v1230 WHERE task_date=:d AND team_member=:m ORDER BY id"),{"d":d,"m":name}).mappings().all()]
        j=dict(j or {})
        options="".join(f"<option value='{_e(x)}' {'selected' if x==name else ''}>{_e(x)}</option>" for x in STAFF)
        trs=[]
        for t in tasks:
            if str(t.get("status") or "")=="DONE": action="<span class='pill'>DONE</span>"
            else: action=f"""<form method="post" action="/alliance/primary/day-plan/task/{int(t['id'])}/done"><input type="hidden" name="staff" value="{_e(name)}"><input name="outcome" placeholder="What was completed?" required><button class="btn">Mark Done</button></form>"""
            trs.append(f"<tr><td><b>{_e(t.get('task_text'))}</b></td><td>{_e(t.get('status'))}</td><td>{_e(t.get('outcome'))}</td><td>{action}</td></tr>")
        notice=f"<div class='ok'>Saved successfully: {_e(saved.replace('_',' ').title())}</div>" if saved else ""
        body=f"""{notice}<div class="card"><form method="get"><b>Staff Member</b><select name="staff">{options}</select><button class="btn">Open Day Plan</button></form><p class="muted">Working date: {d.strftime('%d-%m-%Y')}</p></div>
<div class="grid"><div class="card"><h2>Morning Plan · {_e(name)}</h2><form method="post" action="/alliance/primary/day-plan/save-morning"><input type="hidden" name="staff" value="{_e(name)}"><textarea name="morning_plan" rows="8" required>{_e(j.get('morning_plan'))}</textarea><button class="btn">Save Morning Plan</button></form><p><b>Currently saved:</b><br>{_e(j.get('morning_plan') or 'Nothing saved yet.')}</p></div>
<div class="card"><h2>Add Task / Message To Share · {_e(name)}</h2><form method="post" action="/alliance/primary/day-plan/add-task"><input type="hidden" name="staff" value="{_e(name)}"><textarea name="task_text" rows="8" required placeholder="Enter exact task/message/call/follow-up"></textarea><button class="btn">Save Task</button></form><p class="muted">Saved text appears immediately below and in Staff Review.</p></div></div>
<div class="card"><h2>Today's Saved Tasks · {_e(name)}</h2><table><tr><th>Task / Message</th><th>Status</th><th>Outcome</th><th>Action</th></tr>{''.join(trs) or "<tr><td colspan='4'>No tasks saved today.</td></tr>"}</table></div>
<div class="card"><h2>End-of-Day Report · {_e(name)}</h2><form method="post" action="/alliance/primary/day-plan/save-evening"><input type="hidden" name="staff" value="{_e(name)}"><textarea name="evening_summary" rows="8" required>{_e(j.get('evening_summary'))}</textarea><button class="btn">Save End Task Report</button></form><p><b>Currently saved report:</b><br>{_e(j.get('evening_summary') or 'Nothing saved yet.')}</p></div>"""
        return HTMLResponse(shell("Daily Day Plan",body),headers={"Cache-Control":"no-store"})
    @app.post("/alliance/primary/day-plan/save-morning")
    def save_morning(req:Request,staff:str=Form(...),morning_plan:str=Form(...)):
        _login(core,req);name=_staff(staff);d=_today();v=morning_plan.strip()
        if not v:return HTMLResponse("Morning plan cannot be blank.",400)
        with engine.begin() as c:c.execute(text("INSERT INTO pi_team_daily_journal_v1230(task_date,team_member,morning_plan,updated_at) VALUES(:d,:m,:v,NOW()) ON CONFLICT(task_date,team_member) DO UPDATE SET morning_plan=EXCLUDED.morning_plan,updated_at=NOW()"),{"d":d,"m":name,"v":v})
        return RedirectResponse(f"/alliance/primary/day-plan?staff={name.replace(' ','%20')}&saved=morning_plan",303)
    @app.post("/alliance/primary/day-plan/add-task")
    def add_task(req:Request,staff:str=Form(...),task_text:str=Form(...)):
        _login(core,req);name=_staff(staff);d=_today();v=task_text.strip()
        if not v:return HTMLResponse("Task cannot be blank.",400)
        with engine.begin() as c:c.execute(text("INSERT INTO pi_team_daily_tasks_v1230(task_date,team_member,task_text,status) VALUES(:d,:m,:v,'OPEN')"),{"d":d,"m":name,"v":v})
        return RedirectResponse(f"/alliance/primary/day-plan?staff={name.replace(' ','%20')}&saved=task",303)
    @app.post("/alliance/primary/day-plan/task/{task_id}/done")
    def task_done(task_id:int,req:Request,staff:str=Form(...),outcome:str=Form(...)):
        _login(core,req);name=_staff(staff);d=_today();v=outcome.strip()
        if not v:return HTMLResponse("Outcome cannot be blank.",400)
        with engine.begin() as c:c.execute(text("UPDATE pi_team_daily_tasks_v1230 SET status='DONE',outcome=:v,completed_at=NOW() WHERE id=:id AND team_member=:m AND task_date=:d"),{"v":v,"id":task_id,"m":name,"d":d})
        return RedirectResponse(f"/alliance/primary/day-plan?staff={name.replace(' ','%20')}&saved=task_done",303)
    @app.post("/alliance/primary/day-plan/save-evening")
    def save_evening(req:Request,staff:str=Form(...),evening_summary:str=Form(...)):
        _login(core,req);name=_staff(staff);d=_today();v=evening_summary.strip()
        if not v:return HTMLResponse("End Task Report cannot be blank.",400)
        with engine.begin() as c:c.execute(text("INSERT INTO pi_team_daily_journal_v1230(task_date,team_member,evening_summary,updated_at) VALUES(:d,:m,:v,NOW()) ON CONFLICT(task_date,team_member) DO UPDATE SET evening_summary=EXCLUDED.evening_summary,updated_at=NOW()"),{"d":d,"m":name,"v":v})
        return RedirectResponse(f"/alliance/primary/day-plan?staff={name.replace(' ','%20')}&saved=end_task_report",303)
    REGISTRATION["staff_day_plan"]={"status":"AUTHORITATIVE","india_business_date":str(_today()),"old_routes_removed":len(removed)}


def _install_availability(core):
    app,engine=_app(core),_engine(core)
    import alliance_primary_workspace_v730 as ws
    import alliance_master_integration_v720 as v720
    import alliance_smart_matcher_v1211 as sm
    _remove_routes(app,[("/alliance/primary/availability","GET")])
    def page(body,title="Availability Verification"):
        return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(title)}</title>
<style>body{{font-family:Arial;margin:0;background:#f5f7fb;color:#172033}}header{{background:#102a43;color:white;padding:18px 22px}}nav{{background:white;padding:10px;border-bottom:1px solid #dfe6ee}}nav a,.btn,button{{display:inline-block;background:#102a43;color:white;text-decoration:none;border:0;border-radius:7px;padding:8px 10px;margin:2px;cursor:pointer}}.wrap{{max-width:1900px;margin:auto;padding:18px}}.card{{background:white;border:1px solid #dfe6ee;border-radius:12px;padding:14px;margin-bottom:12px}}.warn{{background:#fff7e6;border-color:#f3cf8c}}.ok{{color:#067647;font-weight:800}}.muted{{color:#667085}}select,input{{padding:8px;border:1px solid #cfd8e3;border-radius:7px}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{padding:8px;border-bottom:1px solid #edf1f5;text-align:left;vertical-align:top}}th{{background:#f8fafc;position:sticky;top:0}}.tablebox{{overflow:auto;max-height:72vh}}</style></head>
<body><header><b>Alliance CRE · {title}</b><br><small>Matcher candidates → availability verification → client-safe stock</small></header><nav><a href="/alliance/primary">← Main Dashboard</a><a href="/alliance/primary/requirements">Requirements</a><a href="/alliance/primary/matcher">Smart Matcher</a></nav><div class="wrap">{body}</div></body></html>"""
    @app.get("/alliance/primary/availability",response_class=HTMLResponse)
    def availability(req:Request,requirement_id:str=Query(""),tier:str=Query(""),verified_only:str=Query("")):
        _login(core,req)
        reqs=v720._search_requirements(engine,limit=300)
        opts="".join(f"<option value='{_e(x['canonical_id'])}' {'selected' if requirement_id==x['canonical_id'] else ''}>{_e((x.get('locality') or 'Requirement')+' · '+x['canonical_id'])}</option>" for x in reqs)
        tiers=("BEST_MATCH","POSSIBLE_VERIFY","ALTERNATIVE")
        form=f"""<div class="card"><form><select name="requirement_id"><option value="">Choose requirement</option>{opts}</select><select name="tier"><option value="">All match tiers</option>{''.join(f"<option value='{x}' {'selected' if tier==x else ''}>{x.replace('_',' ')}</option>" for x in tiers)}</select><label><input type="checkbox" name="verified_only" value="1" {'checked' if verified_only else ''}> Confirmed available first</label><button>Load Availability</button></form></div>"""
        if not requirement_id:
            return HTMLResponse(page(form+"<div class='card'><h3>Select a requirement</h3><p>UNKNOWN/UNVERIFIED candidates remain visible for calling. Only VERIFIED + AVAILABLE is confirmed stock.</p></div>"),headers={"Cache-Control":"no-store"})
        rr=ws._requirement(engine,requirement_id)
        if not rr:
            return HTMLResponse(page(form+"<div class='card warn'><h3>Requirement must be human VERIFIED first</h3><p>The Requirement Gate remains mandatory.</p><a class='btn' href='/alliance/primary/requirements'>Open Requirements</a></div>"))
        try: matches=sm._smart_match_full(engine,requirement_id,120)
        except Exception as exc:
            return HTMLResponse(page(form+f"<div class='card warn'><b>Matcher could not load:</b> {_e(type(exc).__name__+': '+str(exc))}</div>"))
        use=tier if tier in tiers else ""
        filtered=[m for m in matches if not use or m.get("tier")==use]
        confirmed=[]; needs=[]
        for m in filtered:
            p=m.get("property") or {}
            if str(p.get("availability_status") or "").upper()=="UNAVAILABLE": continue
            if str(p.get("verification_status") or "").upper()=="VERIFIED" and str(p.get("availability_status") or "").upper()=="AVAILABLE": confirmed.append(m)
            else: needs.append(m)
        display=confirmed if (verified_only and confirmed) else confirmed+needs
        note=""
        if verified_only and not confirmed:
            note=f"<div class='card warn'><b>No VERIFIED + AVAILABLE property exists yet.</b><br>{len(needs)} matched candidate(s) are shown for availability verification instead of an empty/error page.</div>"
        trs=[]
        for m in display:
            p=m.get("property") or {};cid=str(p.get("canonical_id") or "")
            ver=str(p.get("verification_status") or "UNVERIFIED");av=str(p.get("availability_status") or "UNKNOWN")
            ok=ver.upper()=="VERIFIED" and av.upper()=="AVAILABLE"
            status="<span class='ok'>VERIFIED · AVAILABLE</span>" if ok else f"<b>{_e(ver)} · {_e(av)}</b><br><span class='muted'>Call/verify before sharing</span>"
            phones=", ".join(str(x) for x in (p.get("phones") or []));why=", ".join(str(x) for x in (m.get("reasons") or []))
            amount=p.get("sale_amount") if str(p.get("transaction_type") or "").upper()=="SALE" else p.get("rent_amount")
            verify="" if ok else f"<form method='post' action='/alliance/primary/property/{_e(cid)}/verify' style='display:inline'><button>Verify Available</button></form>"
            trs.append(f"<tr><td><b>{_e(m.get('score'))}%</b><br>{_e(m.get('tier'))}</td><td><a class='btn' href='/alliance/primary/property/{_e(cid)}'>Open</a> {verify}</td><td><b>{_e(p.get('locality') or p.get('city'))}</b><br>{_e(cid)}</td><td>{_e(p.get('transaction_type'))}</td><td>{_e(p.get('area_sqft_display') or p.get('area_sqft'))}</td><td>{_e(amount)}</td><td>{status}</td><td>{_e(phones)}</td><td>{_e(why)}</td></tr>")
        summary=f"<div class='card'><h3>Verified Requirement</h3><b>{_e(requirement_id)}</b> · {_e(rr.get('locality') or rr.get('city'))} · {_e(rr.get('transaction_type'))} · Area {_e(rr.get('area_sqft_display') or rr.get('area_sqft'))}</div>"
        table=f"<div class='card tablebox'><table><tr><th>Score/Tier</th><th>Action</th><th>Property</th><th>Transaction</th><th>Area</th><th>Amount</th><th>Availability</th><th>Internal Contact</th><th>Why Matched</th></tr>{''.join(trs) or '<tr><td colspan=9>No matching candidates for this filter.</td></tr>'}</table></div>"
        return HTMLResponse(page(form+summary+note+table),headers={"Cache-Control":"no-store"})
    REGISTRATION["availability"]={"status":"AUTHORITATIVE","smart_matcher":sm.VERSION}

def _install_master_search():
    import alliance_final_5x5_databases_v910 as db
    if getattr(db,"_v1244_search_patched",False): return
    original_rows=db._property_rows;original_table=db._property_table
    def rows(e,source,q,location,category,transaction,status,assigned,limit):
        exact=original_rows(e,source,q,location,category,transaction,status,assigned,limit)
        if exact:return exact
        if str(status or "").upper()=="AVAILABLE":
            fallback=original_rows(e,source,q,location,category,transaction,"",assigned,limit)
            if not fallback and str(q or "").strip().lower() in {"commercial","property","rent","rental","sale"}:
                fallback=original_rows(e,source,"",location,category,transaction,"",assigned,limit)
            for r in fallback:r["_v1244_needs_availability_verification"]=True
            return fallback
        return exact
    def table(core,e,req,source,q,location,category,transaction,status,assigned,limit):
        exact=original_rows(e,source,q,location,category,transaction,status,assigned,limit)
        body=original_table(core,e,req,source,q,location,category,transaction,status,assigned,limit)
        if not exact and str(status or "").upper()=="AVAILABLE":
            body="""<div class="card" style="border:1px solid #f3c589;background:#fff7e6"><b>No property is currently VERIFIED + AVAILABLE for these filters.</b><br>Relevant Master candidates with UNKNOWN / UNVERIFIED availability are shown below for team verification. They are <b>not client-ready</b> until confirmed.</div>"""+body
        return body
    db._property_rows=rows;db._property_table=table;db._v1244_search_patched=True
    REGISTRATION["master_search"]={"status":"PATCHED","unknown_fallback_truthful":True}

def _install_property_entry(core):
    app=_app(core)
    import fast_manual_forms as fm
    _remove_routes(app,[("/fast-property-entry","GET")])
    @app.get("/fast-property-entry",response_class=HTMLResponse)
    def property_entry(req:Request,division:str=Query("DELHI_NCR")):
        _login(core,req);d="GOA" if str(division).upper()=="GOA" else "DELHI_NCR";city="Goa" if d=="GOA" else "Delhi NCR"
        checks="".join(f"<label><input type=checkbox name=ptype value='{_e(x)}'> {_e(x)}</label>" for x in fm.PROPERTY_TYPES)
        page=fm._property_page(d).replace("{city}",city).replace("{d}",d).replace("{checks}",checks)
        page=page.replace('href="/workspace">← Back to Dashboard</a>','href="/alliance/primary">← Back to Main Dashboard</a>')
        switch="/fast-property-entry?division=DELHI_NCR" if d=="GOA" else "/fast-property-entry?division=GOA"
        nav=f"""<div class="card"><a class="btn gray" href="/alliance/primary">← Main Dashboard</a> <a class="btn" href="/alliance/final/databases">Property Databases</a> <a class="btn green" href="/alliance/goa-properties">Goa Property Listing</a> <a class="btn" href="{switch}">{'Delhi NCR Entry' if d=='GOA' else '+ Add Goa Property'}</a> <a class="btn" href="/commercial-intelligence">Commercial Intelligence</a></div>"""
        page=page.replace("<div class=w><p>","<div class=w>"+nav+"<p>",1)
        return HTMLResponse(page,headers={"Cache-Control":"no-store"})
    REGISTRATION["property_entry"]={"status":"AUTHORITATIVE","brochure_ui":True,"goa_listing_link":True,"media_backend":"fast_manual_forms V20.1"}

def _install_goa(core):
    app,engine=_app(core),_engine(core)
    import alliance_final_dashboard_v1241 as fd
    _remove_routes(app,[("/alliance/goa-properties","GET")])
    @app.get("/alliance/goa-properties",response_class=HTMLResponse)
    def goa(req:Request):
        _login(core,req);rows=fd._goa_rows(engine,1000);page=fd._goa_page(rows)
        if "+ Add Goa Property" not in page:
            button='<a href="/fast-property-entry?division=GOA">+ Add Goa Property</a>'
            page=page.replace("<nav>","<nav>"+button,1) if "<nav>" in page else page.replace("</header>","</header><p>"+button+"</p>",1)
        return HTMLResponse(page,headers={"Cache-Control":"no-store"})
    REGISTRATION["goa"]={"status":"AUTHORITATIVE","add_manual_goa":True}


def _install_government(core):
    engine=_engine(core)
    import alliance_government_commercial_sources_v1 as gov
    if getattr(gov,"_v1244_authority_patched",False): return
    original=gov.render_commercial
    authorities=("DMRC","DDA","NDMC","MCD","RLDA","AAI","NOIDA","GNIDA","YEIDA")
    def toolbar():
        return " ".join(f'<a class="btn" href="/commercial-intelligence?view=GOV&city={a}">{a}</a>' for a in authorities)
    def phones_html(v):
        vals=[]
        for x in re.split(r"[,;/ ]+",str(v or "")):
            d=re.sub(r"\D","",x)
            if len(d)==10: vals.append(f'<a href="tel:{d}">{d}</a>')
        return ", ".join(vals) if vals else _e(v)
    def authority_page(authority,message=""):
        pat="%"+authority+"%"
        with engine.connect() as c:
            assets=[dict(x) for x in c.execute(text("""SELECT a.* FROM aci_intel_assets a
                WHERE a.asset_class='GOVERNMENT_PREMISES' AND COALESCE(a.visibility_status,'ACTIVE')='ACTIVE'
                AND (UPPER(COALESCE(a.source_provider,'')) LIKE :p OR EXISTS(
                    SELECT 1 FROM aci_intel_evidence e WHERE e.asset_code=a.asset_code
                    AND UPPER(COALESCE(e.source_provider,'')) LIKE :p))
                ORDER BY confidence DESC,asset_name LIMIT 500"""),{"p":pat}).mappings().all()]
            devs=[dict(x) for x in c.execute(text("""SELECT authority,developer_name,COUNT(*) property_count,
                STRING_AGG(DISTINCT COALESCE(phone,''),', ') phones FROM aci_gov_developer_portfolio
                WHERE UPPER(authority)=:a GROUP BY authority,developer_name
                ORDER BY COUNT(*) DESC,developer_name LIMIT 250"""),{"a":authority}).mappings().all()]
            docs=[dict(x) for x in c.execute(text("""SELECT authority,source_type,source_url,document_status,
                fetch_status,fetched_at,notes FROM aci_gov_source_documents WHERE UPPER(authority)=:a
                ORDER BY fetched_at DESC LIMIT 100"""),{"a":authority}).mappings().all()]
        asset_rows="".join(
            f"""<tr><td><b>{_e(x.get('asset_name'))}</b><br>{_e(x.get('location'))}</td>
            <td>{_e(x.get('city'))}</td><td>{_e(x.get('lifecycle_status'))}</td>
            <td>{_e(x.get('developer_or_authority'))}</td><td>{_e(x.get('confidence'))}</td>
            <td><form method="post" action="/commercial-intelligence/research/{_e(x.get('asset_code'))}"><button>Research</button></form>
            {'<a class="btn" target="_blank" href="'+_e(x.get("source_url"))+'">Official Source</a>' if x.get("source_url") else ''}</td></tr>"""
            for x in assets) or "<tr><td colspan='6'>No authority assets indexed yet. Use Sync Government Sources.</td></tr>"
        dev_rows="".join(f"<tr><td>{_e(x.get('developer_name'))}</td><td>{x.get('property_count')}</td><td>{phones_html(x.get('phones'))}</td></tr>" for x in devs) or "<tr><td colspan='3'>No developer/lessee portfolio records yet.</td></tr>"
        doc_rows="".join(f"""<tr><td>{_e(x.get('source_type'))}</td><td>{_e(x.get('document_status'))}</td>
            <td>{_e(x.get('fetch_status'))}</td><td>{_e(x.get('fetched_at'))}</td>
            <td><a class="btn" target="_blank" href="{_e(x.get('source_url'))}">Open Official Source</a></td></tr>""" for x in docs) or "<tr><td colspan='5'>No synced official documents yet.</td></tr>"
        return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{authority} Commercial Intelligence</title><style>body{{font-family:Arial;margin:0;background:#f4f7fb;color:#172437}}header{{background:#102235;color:white;padding:18px}}.wrap{{max-width:1700px;margin:auto;padding:18px}}.bar{{display:flex;gap:7px;flex-wrap:wrap;margin:10px 0}}.btn,button{{padding:9px 12px;border:0;border-radius:8px;background:#1677ff;color:white;text-decoration:none;font-weight:bold;cursor:pointer;display:inline-block}}.back{{background:white;color:#102235}}.card{{background:white;border:1px solid #e1e7ee;border-radius:12px;padding:14px;margin:12px 0}}table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{padding:8px;border-bottom:1px solid #e7edf3;text-align:left;vertical-align:top}}</style></head>
<body><header><a class="btn back" style="float:right" href="/alliance/primary">← Back to Main Dashboard</a><h2>{authority} Commercial Intelligence</h2><div>Government commercial properties · official documents · developer/lessee contacts</div></header>
<div class="wrap"><div class="bar"><a class="btn" href="/commercial-intelligence?view=ALL">All</a><a class="btn" href="/commercial-intelligence?view=MALLS">Malls</a><a class="btn" href="/commercial-intelligence?view=GOV">All Government</a>{toolbar()}<form method="post" action="/commercial-intelligence/government-sync"><button>Sync Government Sources</button></form></div>
<div class="card"><b>{len(assets)} {authority} government commercial asset(s) indexed.</b></div>
<div class="card"><h3>{authority} Properties / Opportunities</h3><table><tr><th>Property</th><th>City</th><th>Status</th><th>Developer/Authority</th><th>Confidence</th><th>Action</th></tr>{asset_rows}</table></div>
<div class="card"><h3>{authority} Developer / Lessee Portfolio</h3><table><tr><th>Developer / Lessee</th><th>Properties</th><th>Public Phones</th></tr>{dev_rows}</table></div>
<div class="card"><h3>{authority} Official Source Documents</h3><table><tr><th>Source Type</th><th>Document Status</th><th>Fetch</th><th>Last Sync</th><th>Action</th></tr>{doc_rows}</table></div></div></body></html>"""
    def render(engine_arg,view,city,message=""):
        key=str(city or "").strip().upper()
        if key in authorities:return authority_page(key,message)
        if str(city or "").strip().lower() in {"government","gov"}:view,city="GOV",""
        page=original(engine_arg,view,city,message)
        page=page.replace('href="/team-dashboard-v376">Back to Main Dashboard</a>','href="/alliance/primary">← Back to Main Dashboard</a>')
        if "/commercial-intelligence?view=GOV&city=DMRC" not in page:
            marker='<a class="btn" href="/commercial-intelligence?view=GOV">Government</a>'
            page=page.replace(marker,marker+toolbar(),1)
        return page
    gov.render_commercial=render;gov._v1244_authority_patched=True
    REGISTRATION["government"]={"status":"PATCHED","authorities":list(authorities),"dmrc_operable":True,"back_to_main_dashboard":True}

def _install_status(core):
    app,engine=_app(core),_engine(core)
    _remove_routes(app,[("/api/alliance/operations-hotfix/status","GET"),("/alliance/operations-check","GET")])
    def snapshot():
        paths=["/alliance/primary/day-plan","/alliance/primary/availability","/fast-property-entry","/alliance/goa-properties","/commercial-intelligence"]
        return {"status":"PASS" if all(_route_exists(app,p) for p in paths) else "FAIL","version":VERSION,
                "india_business_date":str(_today()),"registrations":REGISTRATION,
                "routes":{p:_route_exists(app,p) for p in paths},
                "tables":{"staff_tasks":_table_exists(engine,"pi_team_daily_tasks_v1230"),
                          "staff_journal":_table_exists(engine,"pi_team_daily_journal_v1230"),
                          "master_properties":_table_exists(engine,"pi_master_properties_v711"),
                          "master_requirements":_table_exists(engine,"pi_master_requirements_v711"),
                          "government_assets":_table_exists(engine,"aci_intel_assets")}}
    @app.get("/api/alliance/operations-hotfix/status")
    def api_status(req:Request):
        _login(core,req);return snapshot()
    @app.get("/alliance/operations-check",response_class=HTMLResponse)
    def check(req:Request):
        _login(core,req);s=snapshot()
        rows="".join(f"<tr><td>{_e(k)}</td><td><b>{'PASS' if v else 'FAIL'}</b></td></tr>" for k,v in s["routes"].items())
        regs="".join(f"<tr><td>{_e(k)}</td><td>{_e(v.get('status'))}</td><td>{_e(v)}</td></tr>" for k,v in REGISTRATION.items())
        body=f"""<!doctype html><html><head><meta charset="utf-8"><title>Alliance Operations Check</title><style>body{{font-family:Arial;background:#f5f7fb;padding:20px}}.card{{background:white;padding:15px;margin:10px;border:1px solid #ddd}}table{{width:100%;border-collapse:collapse}}td,th{{padding:8px;border-bottom:1px solid #eee;text-align:left}}a{{padding:8px;background:#102a43;color:white;text-decoration:none}}</style></head><body><a href="/alliance/primary">← Main Dashboard</a><h2>Alliance Operations Consolidated Check</h2><div class="card"><b>Version:</b> {VERSION}<br><b>Status:</b> {s['status']}<br><b>India Date:</b> {s['india_business_date']}</div><div class="card"><h3>Routes</h3><table>{rows}</table></div><div class="card"><h3>Fix Registrations</h3><table>{regs}</table></div></body></html>"""
        return HTMLResponse(body,headers={"Cache-Control":"no-store"})

def register(core):
    if _engine(core) is None:raise RuntimeError("12.4.4 requires core.engine")
    _install_staff_day_plan(core)
    _install_availability(core)
    _install_master_search()
    _install_property_entry(core)
    _install_goa(core)
    _install_government(core)
    _install_status(core)
    return {"status":"REGISTERED","version":VERSION,"fixes":REGISTRATION}
