from __future__ import annotations
import html,json,re
from fastapi import Query,Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text
import alliance_final_5x5_databases_v910 as v

VERSION="9.3.0-ORGANIZED-MAIN-DASHBOARD"
SOURCES=("MASTER","NEWSPAPER","WHATSAPP","MAGAZINE","MANUAL")

def _e(x): return html.escape("" if x is None else str(x))
def _app(c): return getattr(c,"app",None) or c
def _engine(c): return getattr(c,"engine",None)
def _login(c,r):
    f=getattr(c,"need_login",None)
    return f(r) if f else "team"
def _remove_get(app,path):
    app.router.routes[:]=[r for r in app.router.routes if not(getattr(r,"path",None)==path and "GET" in set(getattr(r,"methods",set()) or set()))]
def _d(x):
    if isinstance(x,dict): return x
    if isinstance(x,str):
        try:
            y=json.loads(x); return y if isinstance(y,dict) else {}
        except: return {}
    return {}
def _first(d,*ks):
    for k in ks:
        x=d.get(k)
        if x not in (None,"",[],{}): return x
    return None
def _src_tokens(e,row,etype):
    vals=[]; cid=row.get("canonical_id")
    try:
        with e.connect() as c:
            rr=c.execute(text("""SELECT source_type,source_table FROM pi_master_source_links_v711
              WHERE canonical_id=:id AND master_entity_type=:et ORDER BY created_at DESC,id DESC LIMIT 6"""),{"id":cid,"et":etype}).mappings().all()
        for r in rr:
            for k in ("source_type","source_table"):
                x=(r.get(k) or "").strip()
                if x and x not in vals: vals.append(x)
    except: pass
    cr=_d(row.get("clean_record"))
    for k in ("source_type","source_name"):
        x=row.get(k)
        if x and str(x) not in vals: vals.append(str(x))
    for k in ("source","source_type","source_name","channel","import_source","origin"):
        x=cr.get(k)
        if x and str(x) not in vals: vals.append(str(x))
    return vals
def _src_kind(e,row,etype):
    cr=_d(row.get("clean_record")); cid=str(row.get("canonical_id") or "")
    s=(" ".join(_src_tokens(e,row,etype))+" "+cid+" "+str(_first(cr,"original_message","original_description","source_text") or "")[:160]).upper()
    if "WHATSAPP" in s or cid.upper().startswith(("WA-","WH-","WAPP-")): return "WHATSAPP"
    if "NEWSPAPER" in s or "NEWS" in s or cid.upper().startswith(("NP-","NEWS-")): return "NEWSPAPER"
    if "MAGAZINE" in s or cid.upper().startswith(("MAG-","MAGNEW-")): return "MAGAZINE"
    if "MANUAL" in s or cid.upper().startswith(("MAN-","REQ-MAN")): return "MANUAL"
    return "MASTER"
def _phones(row,cr,desc):
    vals=[]
    for k in ("contact_number","contact_phone","phone","mobile","mobile_no","contact_no"):
        if cr.get(k): vals.append(str(cr[k]))
    p=row.get("phones")
    if isinstance(p,list): vals += [str(x) for x in p if x]
    elif isinstance(p,str) and p.strip():
        try:
            j=json.loads(p); vals += [str(x) for x in j] if isinstance(j,list) else [p]
        except: vals.append(p)
    if not vals and desc:
        vals += re.findall(r'(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)',str(desc))
        if not vals: vals += re.findall(r'(?<!\d)0?11[\s-]?\d{7,8}(?!\d)',str(desc))
    out=[]
    for x in vals:
        x=re.sub(r"\s+"," ",x).strip()
        if x and x not in out: out.append(x)
    return ", ".join(out[:3])
def _req_rows(e,source,q,location,category,transaction,status,assigned,limit):
    rows=v._requirement_rows(e,"MASTER",q,transaction,1500)
    if source!="MASTER":
        rows=[r for r in rows if _src_kind(e,r,"REQUIREMENT")==source or source in " ".join(_src_tokens(e,r,"REQUIREMENT")).upper()]
    if location: rows=[r for r in rows if location.lower() in (str(r.get("locality") or "")+" "+str(r.get("city") or "")+" "+str(r.get("clean_record") or "")).lower()]
    if assigned: rows=[r for r in rows if assigned.lower() in str(r.get("assigned_to") or "").lower()]
    if status: rows=[r for r in rows if status.upper() in str(r.get("verification_status") or "").upper()]
    if category:
        rows=[r for r in rows if category.lower() in str(_first(_d(r.get("clean_record")),"property_category","required_property_category","category") or "").lower()]
    return rows[:limit]
def _stats_from(rows,entity):
    from datetime import datetime
    t=datetime.now().date(); today=ver=avail=ma=mc=0
    for r in rows:
        cr=_d(r.get("clean_record"))
        try:
            if r.get("created_at") and r["created_at"].date()==t: today+=1
        except: pass
        ver += str(r.get("verification_status") or "").upper()=="VERIFIED"
        avail += str(r.get("availability_status") or "").upper()=="AVAILABLE"
        if entity=="PROPERTY" and not (r.get("locality") or _first(cr,"location","locality","address","exact_address","property_address")): ma+=1
        desc=_first(cr,"requirement_text","original_message","original_description","description","source_text") or ""
        if not _phones(r,cr,desc): mc+=1
    return {"total":len(rows),"today":today,"verified":ver,"unverified":len(rows)-ver,"available":avail,"missing_address":ma,"missing_contact":mc,"needs_review":ma+mc if entity=="PROPERTY" else mc}
def _stat_html(s,entity):
    a=[("Total",s["total"]),("Today",s["today"]),("Verified",s["verified"]),("Unverified",s["unverified"])]
    if entity=="PROPERTY": a += [("Available",s["available"]),("Missing Address",s["missing_address"])]
    a += [("Missing Contact",s["missing_contact"]),("Needs Review",s["needs_review"])]
    return '<div class="stats">'+''.join(f'<div class="stat"><b>{x}</b><span>{_e(k)}</span></div>' for k,x in a)+'</div>'
def _shell(title,body):
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(title)}</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f5f7fa;font-family:Arial;color:#172033;font-size:10px}}header{{background:#0d2238;color:white;padding:8px 12px}}header b{{font-size:14px}}
nav{{background:white;border-bottom:1px solid #98a2b3;padding:4px 6px;display:flex;gap:3px;flex-wrap:wrap;position:sticky;top:0;z-index:20}}nav a,.btn,button{{background:#0d2238;color:white;text-decoration:none;border:1px solid #0d2238;padding:3px 5px;font-size:9px;cursor:pointer;white-space:nowrap}}.good{{background:#067647!important}}.light{{background:#475467!important}}.danger{{background:#b42318!important}}
.wrap{{width:100%;padding:5px}}h2{{font-size:14px;margin:4px 0 5px}}.stats{{display:grid;grid-template-columns:repeat(8,minmax(70px,1fr));gap:2px;margin-bottom:4px}}.stat{{background:white;border:1px solid #98a2b3;padding:3px 5px}}.stat b{{font-size:14px;display:block;line-height:15px}}.stat span{{font-size:8px;color:#475467}}
.card{{background:white;border:1px solid #98a2b3;padding:4px;margin-bottom:4px}}.searchgrid,.inline{{display:grid;grid-template-columns:2.2fr 1fr 1.2fr .75fr .85fr .9fr 55px 48px;gap:2px}}input,select{{width:100%;padding:2px;border:1px solid #98a2b3;height:21px;font-size:9px}}
.tablebox{{overflow:auto;max-height:81vh;border:1px solid #667085;background:white}}table{{border-collapse:collapse;width:100%;min-width:1380px;table-layout:fixed;font-size:8px}}th,td{{border:1px solid #98a2b3;padding:1px 2px;vertical-align:top;line-height:10px;overflow-wrap:anywhere}}th{{background:#e9eef5;position:sticky;top:0;z-index:5;font-size:8px}}tbody tr:nth-child(even) td{{background:#f8fafc}}
.desc{{width:190px!important;min-width:190px!important;max-width:190px!important}}.loc{{width:82px!important;min-width:82px!important}}.nowrap{{white-space:normal!important}}
.grid5{{display:grid;grid-template-columns:repeat(5,1fr);gap:4px}}.dbcard,.section{{background:white;border:1px solid #98a2b3;padding:6px}}.dbcard .n{{font-size:18px;font-weight:800}}.workflow{{display:grid;grid-template-columns:repeat(8,1fr);gap:3px}}.wf{{border:1px solid #98a2b3;background:#f8fafc;padding:4px;min-height:58px}}
</style></head><body><header><b>Alliance CRE Operating System</b><br><small>PROPERTY → VERIFY → REQUIREMENT → MATCH → CLIENT → FOLLOW-UP → DEAL</small></header>
<nav><a href="/alliance/primary">Main Dashboard</a><a href="/alliance/primary/databases">Property Databases</a><a href="/alliance/primary/requirements-hub">Requirement Databases</a><a href="/property-manual">Add Property</a><a href="/requirements-workbench">Add Requirement</a><a href="/alliance/primary/availability">Verification</a><a href="/alliance/primary/matcher">Matcher</a><a href="/alliance/primary/followups">Follow-ups</a><a href="/alliance/primary/reports">Reports</a></nav><div class="wrap"><h2>{_e(title)}</h2>{body}</div></body></html>"""
def _req_table(e,source,q,location,category,transaction,status,assigned,limit):
    rows=_req_rows(e,source,q,location,category,transaction,status,assigned,limit); trs=[]
    for r in rows:
        cr=_d(r.get("clean_record")); cid=str(r["canonical_id"]); desc=_first(cr,"requirement_text","original_message","original_description","additional_points","description","source_text") or ""
        vals=[cid,desc,_first(cr,"company_name","brand_name","client_company","company") or "",_first(cr,"contact_name","client_name","sender_name","name") or "",_phones(r,cr,desc),
              r.get("locality") or _first(cr,"location","locality") or "",_first(cr,"property_category","required_property_category","category") or "",_first(cr,"property_type","required_property_type","asset_type") or "",
              _first(cr,"required_area","required_area_sqft","minimum_area_sqft","maximum_area_sqft") or r.get("area_sqft") or "",r.get("transaction_type") or _first(cr,"transaction_type","rent_or_sale") or "",
              _first(cr,"budget","rent_budget","sale_budget","budget_raw") or "",v._fmt_dt(r.get("created_at")),r.get("verification_status") or "UNVERIFIED",r.get("assigned_to") or "",
              " · ".join(_src_tokens(e,r,"REQUIREMENT")[:2]) or _src_kind(e,r,"REQUIREMENT"),f'<a class="btn light" href="/alliance/primary/requirement/{_e(cid)}">Open</a>']
        trs.append("<tr>"+"".join(f"<td>{x if i==15 else _e(x)}</td>" for i,x in enumerate(vals))+"</tr>")
    h=["Requirement ID","Requirement / Original Message","Client / Company","Contact Name","Contact No.","Location","Property Category","Property Type","Area","Rent/Sale","Budget","Date & Time","Status","Assigned To","Source","Open"]
    f=v._filter_form(q,location,category,transaction,status,assigned,limit)
    return _stat_html(_stats_from(rows,"REQUIREMENT"),"REQUIREMENT")+f+f'<div class="tablebox"><table><thead><tr>{"".join("<th>"+x+"</th>" for x in h)}</tr></thead><tbody>{"".join(trs) if trs else "<tr><td colspan=16>No requirements found</td></tr>"}</tbody></table></div>'
def _prop_table(e,source,q,location,category,transaction,status,assigned,limit):
    rows=v._property_rows(e,source,q,location,category,transaction,status,assigned,limit)
    return _stat_html(_stats_from(rows,"PROPERTY"),"PROPERTY")+v._property_table(None,e,None,source,q,location,category,transaction,status,assigned,limit)
def _hub(e,entity):
    cards=[]
    for s in SOURCES:
        rows=v._property_rows(e,s,"","","","","",1500) if entity=="PROPERTY" else _req_rows(e,s,"","","","","","",1500)
        st=_stats_from(rows,entity); label=f"{s.title()} Database" if entity=="PROPERTY" else f"{s.title()} Requirements"
        url=f"/alliance/final/database/{s.lower()}" if entity=="PROPERTY" else f"/alliance/final/requirements/{s.lower()}"
        cards.append(f'<div class="dbcard"><b>{_e(label)}</b><div class="n">{st["total"]}</div><small>Verified {st["verified"]} · Review {st["needs_review"]}</small><br><a class="btn good" href="{url}">Open</a></div>')
    return "".join(cards)
def _dashboard(e):
    wf="".join(f'<div class="wf"><b>{a}</b><br><small>{b}</small></div>' for a,b in [("1 Capture","Property"),("2 Master","Canonical DB"),("3 Verify","Availability"),("4 Requirement","Demand"),("5 Match","Master only"),("6 Review","Client-safe"),("7 Follow-up","Team"),("8 Deal","Reports")])
    return f'<div class="section"><h3>Property Databases</h3><div class="grid5">{_hub(e,"PROPERTY")}</div></div><br><div class="section"><h3>Requirement Databases</h3><div class="grid5">{_hub(e,"REQUIREMENT")}</div></div><br><div class="section"><h3>Operating Workflow</h3><div class="workflow">{wf}</div></div>'
def register(core):
    app=_app(core); e=_engine(core)
    if app is None or e is None: raise RuntimeError("9.3 requires app + engine")
    for p in ("/alliance/primary","/alliance/final/database/{source}","/alliance/final/requirements/{source}","/alliance/final/databases","/alliance/final/requirements"): _remove_get(app,p)
    @app.get("/alliance/primary",response_class=HTMLResponse)
    def main(req:Request): _login(core,req); return HTMLResponse(_shell("Main Dashboard",_dashboard(e)))
    @app.get("/alliance/final/databases",response_class=HTMLResponse)
    def ph(req:Request): _login(core,req); return HTMLResponse(_shell("5 Property Databases",f'<div class="grid5">{_hub(e,"PROPERTY")}</div>'))
    @app.get("/alliance/final/requirements",response_class=HTMLResponse)
    def rh(req:Request): _login(core,req); return HTMLResponse(_shell("5 Requirement Databases",f'<div class="grid5">{_hub(e,"REQUIREMENT")}</div>'))
    @app.get("/alliance/final/database/{source}",response_class=HTMLResponse)
    def pd(req:Request,source:str,q:str=Query(""),location:str=Query(""),category:str=Query(""),transaction:str=Query(""),status:str=Query(""),assigned:str=Query(""),limit:int=Query(500,ge=1,le=1500)):
        _login(core,req); s=source.upper()
        if s not in SOURCES:return HTMLResponse("Unknown database",404)
        return HTMLResponse(_shell(f"{s.title()} Property Database",_prop_table(e,s,q,location,category,transaction,status,assigned,limit)))
    @app.get("/alliance/final/requirements/{source}",response_class=HTMLResponse)
    def rd(req:Request,source:str,q:str=Query(""),location:str=Query(""),category:str=Query(""),transaction:str=Query(""),status:str=Query(""),assigned:str=Query(""),limit:int=Query(500,ge=1,le=1500)):
        _login(core,req); s=source.upper()
        if s not in SOURCES:return HTMLResponse("Unknown requirements",404)
        return HTMLResponse(_shell(f"{s.title()} Requirements",_req_table(e,s,q,location,category,transaction,status,assigned,limit)))
    return {"status":"REGISTERED","version":VERSION}
