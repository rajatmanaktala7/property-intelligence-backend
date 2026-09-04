from __future__ import annotations
import html, json, re
from fastapi import Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION="9.1.0-FINAL-5X5-ORGANIZED-DATABASES"
SOURCES=("MASTER","NEWSPAPER","WHATSAPP","MAGAZINE","MANUAL")
CATEGORY_OPTIONS=("Residential Sale","Residential Rent","Commercial Sale","Commercial Rent","Industrial Sale","Industrial Rent","Farmhouse Sale","Farmhouse Rent")

def _app(core): return getattr(core,"app",None) or core
def _engine(core): return getattr(core,"engine",None)
def _login(core,req):
    fn=getattr(core,"need_login",None)
    return fn(req) if fn else "team"
def _e(v): return html.escape("" if v is None else str(v))
def _dict(v):
    if isinstance(v,dict): return v
    if isinstance(v,str):
        try:
            x=json.loads(v); return x if isinstance(x,dict) else {}
        except Exception:return {}
    return {}
def _first(d,*keys):
    for k in keys:
        v=d.get(k)
        if v not in (None,"",[],{}): return v
    return None
def _fmt_dt(v):
    if not v:return ""
    try:return v.strftime("%d-%m-%Y %I:%M %p")
    except Exception:return str(v)
def _src_pat(source):
    return {"NEWSPAPER":"%NEWSPAPER%","WHATSAPP":"%WHATSAPP%","MAGAZINE":"%MAGAZINE%","MANUAL":"%MANUAL%"}.get(source)
def _source_name(e,cid,etype):
    try:
        with e.connect() as c:
            r=c.execute(text("""SELECT source_type,source_table FROM pi_master_source_links_v711
            WHERE canonical_id=:id AND master_entity_type=:et ORDER BY created_at DESC,id DESC LIMIT 1"""),{"id":cid,"et":etype}).mappings().first()
        if not r:return ""
        a=(r.get("source_type") or "").strip(); b=(r.get("source_table") or "").strip()
        return a if not b or b==a else f"{a} · {b}"
    except Exception:return ""
def _contacts(cr):
    name=_first(cr,"contact_name","owner_name","broker_name","client_name","sender_name","name") or ""
    phone=_first(cr,"contact_number","contact_phone","owner_contact","owner_phone","broker_contact","broker_phone","phone","mobile") or ""
    if not phone:
        p=cr.get("phones")
        if isinstance(p,list): phone=", ".join(str(x) for x in p if x)
    return name,phone
def _property_category(cr,tx):
    explicit=_first(cr,"property_category","category")
    if explicit and str(explicit).strip() in CATEGORY_OPTIONS:return str(explicit).strip()
    asset=_first(cr,"asset_class","property_class","use_type")
    if asset and tx:
        a=str(asset).strip().title(); t="Rent" if str(tx).upper() in ("RENT","LEASE") else "Sale" if str(tx).upper()=="SALE" else ""
        cand=f"{a} {t}".strip()
        if cand in CATEGORY_OPTIONS:return cand
    return explicit or ""
def _property_rows(e,source,q,location,category,transaction,status,assigned,limit):
    pat=_src_pat(source)
    sc=""
    if pat:
        sc="""AND EXISTS(SELECT 1 FROM pi_master_source_links_v711 l WHERE l.canonical_id=p.canonical_id
        AND l.master_entity_type='PROPERTY' AND (UPPER(COALESCE(l.source_type,'')) LIKE :pat OR UPPER(COALESCE(l.source_table,'')) LIKE :pat))"""
    sql=f"""SELECT p.*,COALESCE(w.verification_status,'UNVERIFIED') verification_status,
    COALESCE(w.availability_status,'UNKNOWN') availability_status,COALESCE(w.assigned_to,a.assigned_to) assigned_to
    FROM pi_master_properties_v711 p
    LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=p.canonical_id
    LEFT JOIN pi_master_action_state_v730 a ON a.canonical_id=p.canonical_id
    WHERE NOT EXISTS(SELECT 1 FROM pi_property_archive_v801 ar WHERE ar.canonical_id=p.canonical_id AND ar.restored_at IS NULL)
    {sc}
    AND (:q='%%' OR p.canonical_id ILIKE :q OR COALESCE(p.locality,'') ILIKE :q OR COALESCE(p.city,'') ILIKE :q OR COALESCE(p.clean_record::text,'') ILIKE :q)
    AND (:loc='%%' OR COALESCE(p.locality,'') ILIKE :loc OR COALESCE(p.city,'') ILIKE :loc OR COALESCE(p.clean_record::text,'') ILIKE :loc)
    AND (:tx='' OR UPPER(COALESCE(p.transaction_type,''))=:tx)
    AND (:st='' OR UPPER(COALESCE(w.availability_status,w.verification_status,'UNVERIFIED'))=:st)
    AND (:asgn='%%' OR COALESCE(w.assigned_to,a.assigned_to,'') ILIKE :asgn)
    ORDER BY p.updated_at DESC NULLS LAST,p.created_at DESC NULLS LAST LIMIT :n"""
    with e.connect() as c:
        rows=[dict(x) for x in c.execute(text(sql),{"q":f"%{q.strip()}%","loc":f"%{location.strip()}%","tx":transaction.upper().strip(),
        "st":status.upper().strip(),"asgn":f"%{assigned.strip()}%","n":limit,"pat":pat or ""}).mappings().all()]
    if category.strip():
        want=category.strip().lower()
        rows=[r for r in rows if want in str(_property_category(_dict(r.get("clean_record")),r.get("transaction_type"))).lower()]
    return rows
def _requirement_rows(e,source,q,location,category,transaction,status,assigned,limit):
    pat=_src_pat(source)
    sc=""
    if pat:
        sc="""AND EXISTS(SELECT 1 FROM pi_master_source_links_v711 l WHERE l.canonical_id=r.canonical_id
        AND l.master_entity_type='REQUIREMENT' AND (UPPER(COALESCE(l.source_type,'')) LIKE :pat OR UPPER(COALESCE(l.source_table,'')) LIKE :pat))"""
    sql=f"""SELECT r.*,COALESCE(w.verification_status,'UNVERIFIED') verification_status,
    COALESCE(w.assigned_to,a.assigned_to) assigned_to
    FROM pi_master_requirements_v711 r
    LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=r.canonical_id
    LEFT JOIN pi_master_action_state_v730 a ON a.canonical_id=r.canonical_id
    WHERE 1=1 {sc}
    AND (:q='%%' OR r.canonical_id ILIKE :q OR COALESCE(r.locality,'') ILIKE :q OR COALESCE(r.city,'') ILIKE :q OR COALESCE(r.clean_record::text,'') ILIKE :q)
    AND (:loc='%%' OR COALESCE(r.locality,'') ILIKE :loc OR COALESCE(r.city,'') ILIKE :loc OR COALESCE(r.clean_record::text,'') ILIKE :loc)
    AND (:tx='' OR UPPER(COALESCE(r.transaction_type,''))=:tx)
    AND (:st='' OR UPPER(COALESCE(w.verification_status,'UNVERIFIED'))=:st)
    AND (:asgn='%%' OR COALESCE(w.assigned_to,a.assigned_to,'') ILIKE :asgn)
    ORDER BY r.updated_at DESC NULLS LAST,r.created_at DESC NULLS LAST LIMIT :n"""
    with e.connect() as c:
        rows=[dict(x) for x in c.execute(text(sql),{"q":f"%{q.strip()}%","loc":f"%{location.strip()}%","tx":transaction.upper().strip(),
        "st":status.upper().strip(),"asgn":f"%{assigned.strip()}%","n":limit,"pat":pat or ""}).mappings().all()]
    if category.strip():
        want=category.strip().lower()
        rows=[r for r in rows if want in str(_first(_dict(r.get("clean_record")),"property_category","category","required_property_category") or "").lower()]
    return rows
def _shell(title,body):
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(title)}</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f4f7fb;font-family:Arial;color:#172033}}header{{background:#0d2238;color:white;padding:16px 20px}}
nav{{background:white;border-bottom:1px solid #667085;padding:8px;display:flex;gap:6px;flex-wrap:wrap;position:sticky;top:0;z-index:10}}
nav a,.btn,button,.summarybtn{{background:#0d2238;color:white;text-decoration:none;border:1px solid #0d2238;padding:7px 9px;cursor:pointer;font-size:12px;border-radius:2px}}
.good{{background:#067647!important;border-color:#067647!important}}.light{{background:#475467!important}}.danger{{background:#b42318!important}}
.wrap{{max-width:2100px;margin:auto;padding:14px}}.card{{background:white;border:1px solid #98a2b3;padding:10px;margin-bottom:10px}}
.searchgrid{{display:grid;grid-template-columns:2fr repeat(6,minmax(130px,1fr));gap:6px}}input,select{{width:100%;padding:7px;border:1px solid #98a2b3;border-radius:0}}
.tablebox{{overflow:auto;max-height:76vh;border:1px solid #667085;background:white}}table{{border-collapse:collapse;width:max-content;min-width:100%;font-size:11px}}
th,td{{border:1px solid #98a2b3;padding:6px 7px;text-align:left;vertical-align:top;white-space:normal}}th{{background:#e9eef5;position:sticky;top:0;z-index:4;white-space:nowrap}}
tbody tr:nth-child(even) td{{background:#f8fafc}}tbody tr:hover td{{background:#eef4ff}}.desc{{min-width:300px;max-width:430px}}.loc{{min-width:130px}}.nowrap{{white-space:nowrap}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:8px}}.dbcard{{border:1px solid #98a2b3;background:white;padding:12px}}.dbcard h3{{margin:0 0 5px}}
details.pop{{position:relative}}details.pop>div{{position:absolute;z-index:20;background:white;border:1px solid #667085;padding:9px;min-width:430px}}details.pop summary{{list-style:none}}
@media(max-width:1000px){{.searchgrid{{grid-template-columns:1fr 1fr}}}}
</style></head><body><header><b>Alliance CRE Operating System</b><br><small>5 Property Databases + 5 Requirement Databases · Master-only Matcher</small></header>
<nav><a href="/alliance/primary">Command Centre</a><a href="/alliance/primary/databases">Property Databases</a><a href="/alliance/primary/requirements-hub">Requirement Databases</a><a href="/alliance/primary/matcher">Matcher</a><a href="/alliance/primary/availability">Verification</a><a href="/alliance/primary/followups">Follow-ups</a><a href="/alliance/primary/reports">Reports</a></nav>
<div class="wrap"><h2>{_e(title)}</h2>{body}</div></body></html>"""
def _filter_form(q,location,category,transaction,status,assigned,limit):
    cats="<option value=''>All Categories</option>"+"".join(f"<option {'selected' if category==x else ''}>{_e(x)}</option>" for x in CATEGORY_OPTIONS)
    return f"""<div class="card"><form class="searchgrid">
    <input name="q" value="{_e(q)}" placeholder="Search ID, description, contact, source">
    <input name="location" value="{_e(location)}" placeholder="Location">
    <select name="category">{cats}</select>
    <select name="transaction"><option value="">Rent / Sale</option><option value="RENT" {'selected' if transaction.upper()=='RENT' else ''}>Rent</option><option value="SALE" {'selected' if transaction.upper()=='SALE' else ''}>Sale</option><option value="LEASE" {'selected' if transaction.upper()=='LEASE' else ''}>Lease</option></select>
    <select name="status"><option value="">All Status</option><option {'selected' if status.upper()=='AVAILABLE' else ''}>AVAILABLE</option><option {'selected' if status.upper()=='UNVERIFIED' else ''}>UNVERIFIED</option><option {'selected' if status.upper()=='VERIFIED' else ''}>VERIFIED</option></select>
    <input name="assigned" value="{_e(assigned)}" placeholder="Assigned To">
    <input type="number" name="limit" min="1" max="1500" value="{limit}">
    <button>Search</button></form></div>"""
def _property_table(core,e,req,source,q,location,category,transaction,status,assigned,limit):
    rows=_property_rows(e,source,q,location,category,transaction,status,assigned,limit)
    trs=[]
    for r in rows:
        cr=_dict(r.get("clean_record")); cid=str(r["canonical_id"])
        locality=r.get("locality") or _first(cr,"location","locality") or ""
        address=_first(cr,"address","exact_address","property_address") or ""
        desc=_first(cr,"team_description","description_edit","description","original_description","original_message","raw_line","source_text") or ""
        if address and address.lower() not in str(desc).lower(): desc=(address+" · "+desc).strip(" ·")
        tx=r.get("transaction_type") or _first(cr,"transaction_type","rent_or_sale") or ""
        pcat=_property_category(cr,tx)
        ptype=_first(cr,"property_type","asset_type","subtype") or ""
        area=_first(cr,"area_display","area","available_area")
        if not area:
            av=_first(cr,"area_value") or r.get("area_value") or r.get("area_sqft") or ""
            au=_first(cr,"area_unit") or r.get("area_unit") or ("SQFT" if r.get("area_sqft") else "")
            area=f"{av} {au}".strip()
        floor=_first(cr,"floor","floors","floor_codes") or ""
        if isinstance(floor,list):floor=", ".join(map(str,floor))
        amount=_first(cr,"rent","monthly_rent","rent_amount","rent_in_figures") if str(tx).upper() in ("RENT","LEASE") else _first(cr,"sale_price","sale_amount","price","asking_price")
        amount=amount or _first(cr,"amount","price_raw") or r.get("price_raw") or ""
        cname,cphone=_contacts(cr)
        stat=r.get("availability_status")
        if not stat or stat=="UNKNOWN":stat=r.get("verification_status") or "UNVERIFIED"
        source_name=_source_name(e,cid,"PROPERTY")
        verify=f"""<details class="pop"><summary class="summarybtn good">Verify</summary><div><form method="post" action="/alliance/primary/property/{_e(cid)}/verify">
        <select name="status" required><option>AVAILABLE</option><option>NOT_AVAILABLE</option><option>CALL_BACK</option><option>SOLD</option><option>RENTED</option><option>HOLD</option><option>WRONG_NUMBER</option></select>
        <select name="verified_with" required><option>OWNER</option><option>BROKER</option><option>OTHER</option></select>
        <input name="verified_by" required placeholder="Verified By team member"><input name="remarks" placeholder="Remarks"><input type="datetime-local" name="next_verification_at"><button class="good">Save</button></form></div></details>"""
        delete=f"""<form method="post" action="/alliance/primary/property/{_e(cid)}/delete" onsubmit="return confirm('Archive this property? Original source evidence remains preserved.');"><button class="danger">Delete</button></form>"""
        vals=[cid,locality,desc,pcat,ptype,area,floor,amount,cname,cphone,_fmt_dt(r.get("created_at")),stat,verify,
              f'<a class="btn light" href="/alliance/primary/property/{_e(cid)}">History</a>',r.get("assigned_to") or "",source_name,
              f'<a class="btn light" href="/alliance/primary/property/{_e(cid)}/edit">Edit</a>',delete]
        cls=["nowrap","loc","desc","","","","","","","","nowrap","nowrap","","","","","",""]
        trs.append("<tr>"+"".join(f'<td class="{cls[i]}">{x if i in (12,13,16,17) else _e(x)}</td>' for i,x in enumerate(vals))+"</tr>")
    H=["Property ID","Location","Description / Address","Property Category","Property Type","Area","Floor","Amount","Contact Name","Contact No.","Date & Time","Status","Verify","History","Assigned To","Source","Edit","Delete"]
    return _filter_form(q,location,category,transaction,status,assigned,limit)+f'<div class="tablebox"><table><thead><tr>{"".join("<th>"+x+"</th>" for x in H)}</tr></thead><tbody>{"".join(trs) if trs else "<tr><td colspan=18>No records found</td></tr>"}</tbody></table></div>'
def _requirement_table(e,source,q,location,category,transaction,status,assigned,limit):
    rows=_requirement_rows(e,source,q,location,category,transaction,status,assigned,limit)
    trs=[]
    for r in rows:
        cr=_dict(r.get("clean_record")); cid=str(r["canonical_id"])
        desc=_first(cr,"requirement_text","original_message","original_description","additional_points","description") or ""
        company=_first(cr,"company_name","brand_name","client_company","company") or ""
        cname=_first(cr,"contact_name","client_name","name") or ""
        phone=_first(cr,"contact_phone","contact_number","phone","mobile") or ""
        loc=r.get("locality") or _first(cr,"location","locality") or ""
        pcat=_first(cr,"property_category","required_property_category","category") or ""
        ptype=_first(cr,"property_type","required_property_type","asset_type") or ""
        area=_first(cr,"required_area","required_area_sqft","minimum_area_sqft","maximum_area_sqft") or r.get("area_sqft") or ""
        tx=r.get("transaction_type") or _first(cr,"transaction_type","rent_or_sale") or ""
        budget=_first(cr,"budget","rent_budget","sale_budget","budget_raw") or r.get("budget_raw") or ""
        st=r.get("verification_status") or "UNVERIFIED"
        src=_source_name(e,cid,"REQUIREMENT")
        vals=[cid,desc,company,cname,phone,loc,pcat,ptype,area,tx,budget,_fmt_dt(r.get("created_at")),st,r.get("assigned_to") or "",src,
              f'<a class="btn light" href="/alliance/primary/requirement/{_e(cid)}">Open</a>']
        trs.append("<tr>"+"".join(f'<td class="{"desc" if i==1 else ""}">{x if i==15 else _e(x)}</td>' for i,x in enumerate(vals))+"</tr>")
    H=["Requirement ID","Requirement / Original Message","Client / Company","Contact Name","Contact No.","Location","Property Category","Property Type","Area","Rent/Sale","Budget","Date & Time","Status","Assigned To","Source","Open"]
    return _filter_form(q,location,category,transaction,status,assigned,limit)+f'<div class="tablebox"><table><thead><tr>{"".join("<th>"+x+"</th>" for x in H)}</tr></thead><tbody>{"".join(trs) if trs else "<tr><td colspan=16>No requirements found</td></tr>"}</tbody></table></div>'
def register(core):
    app=_app(core);e=_engine(core)
    if app is None or e is None:raise RuntimeError("9.1 requires app + engine")
    @app.get("/alliance/final/databases",response_class=HTMLResponse)
    def dbhub(req:Request):
        _login(core,req)
        cards="".join(f'<div class="dbcard"><h3>{s.title()} Database</h3><a class="btn good" href="/alliance/final/database/{s.lower()}">Open</a></div>' for s in SOURCES)
        return HTMLResponse(_shell("5 Property Databases",f'<div class="grid">{cards}</div><div class="card"><b>Matcher rule:</b> Matcher searches Master Property Database only. Source databases remain separate evidence views.</div>'))
    @app.get("/alliance/final/database/{source}",response_class=HTMLResponse)
    def db(req:Request,source:str,q:str=Query(""),location:str=Query(""),category:str=Query(""),transaction:str=Query(""),status:str=Query(""),assigned:str=Query(""),limit:int=Query(500,ge=1,le=1500)):
        _login(core,req);src=source.upper()
        if src not in SOURCES:return HTMLResponse("Unknown property database",404)
        return HTMLResponse(_shell(f"{src.title()} Property Database",_property_table(core,e,req,src,q,location,category,transaction,status,assigned,limit)))
    @app.get("/alliance/final/requirements",response_class=HTMLResponse)
    def rhub(req:Request):
        _login(core,req)
        cards="".join(f'<div class="dbcard"><h3>{s.title()} Requirements</h3><a class="btn good" href="/alliance/final/requirements/{s.lower()}">Open</a></div>' for s in SOURCES)
        return HTMLResponse(_shell("5 Requirement Databases",f'<div class="grid">{cards}</div>'))
    @app.get("/alliance/final/requirements/{source}",response_class=HTMLResponse)
    def rdb(req:Request,source:str,q:str=Query(""),location:str=Query(""),category:str=Query(""),transaction:str=Query(""),status:str=Query(""),assigned:str=Query(""),limit:int=Query(500,ge=1,le=1500)):
        _login(core,req);src=source.upper()
        if src not in SOURCES:return HTMLResponse("Unknown requirement database",404)
        return HTMLResponse(_shell(f"{src.title()} Requirements",_requirement_table(e,src,q,location,category,transaction,status,assigned,limit)))
    return {"status":"REGISTERED","version":VERSION}
