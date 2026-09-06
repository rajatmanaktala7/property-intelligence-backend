from __future__ import annotations
import html, json, re
from fastapi import HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION="11.9.11-SIMPLE-MATCH-MAGAZINE-HIERARCHY"
ADDRESS_RE=re.compile(r"^\s*((?:[A-Z]{1,3}\s*-\s*\d+[A-Z]?|\d+\s*/\s*\d+[A-Z]?|[A-Z]{1,3}\d+[A-Z]?))(?=\s|$)",re.I)
CATEGORY_RE=re.compile(r"\b(RESIDENTIAL|COMMERCIAL|INDUSTRIAL|FARMHOUSE)\b",re.I)
TX_RE=re.compile(r"\b(SALE|SELL|RENT|LEASE|LEASING|RENTING)\b",re.I)

def _app(core): return getattr(core,"app",None) or core
def _engine(core): return getattr(core,"engine",None)
def _txt(v): return "" if v is None else str(v)

def _json_obj(v):
    if isinstance(v,dict): return v
    if not v:return {}
    try:return json.loads(v)
    except:return {}

def _remove_route(app,path,method="GET"):
    method=method.upper()
    app.router.routes[:]=[r for r in list(app.router.routes)
        if not (getattr(r,"path",None)==path and method in set(getattr(r,"methods",set()) or set()))]

def _endpoint(app,path,method="GET"):
    method=method.upper()
    for r in list(app.router.routes):
        if getattr(r,"path",None)==path and method in set(getattr(r,"methods",set()) or set()):
            return getattr(r,"endpoint",None)
    return None

def _req_text(clean):
    for k in ("requirement","requirement_text","original_message","description"):
        if clean.get(k):return _txt(clean.get(k))
    g=clean.get("requirement_gate") or {}
    return _txt(g.get("original_message")) if isinstance(g,dict) else ""

def _req_use(clean):
    for k in ("intended_use","property_type","property_category"):
        if clean.get(k):return _txt(clean.get(k))
    return ""

def _req_contact(clean,row):
    for k in ("contact_no","contact_phone"):
        if clean.get(k):return _txt(clean.get(k))
    a=clean.get("contact_numbers")
    if isinstance(a,list) and a:return _txt(a[0])
    p=row.get("phones")
    if isinstance(p,list) and p:return _txt(p[0])
    return ""

def _area_label(row,clean):
    lo,hi=clean.get("area_min_sqft"),clean.get("area_max_sqft")
    if lo is not None and hi is not None:
        try:
            if float(lo)==float(hi):return f"{float(lo):g} sqft"
            return f"{float(lo):g}-{float(hi):g} sqft"
        except:return f"{lo}-{hi} sqft"
    if lo is not None:return f"{lo} sqft+"
    if hi is not None:return f"up to {hi} sqft"
    v=row.get("area_sqft")
    return f"{v} sqft" if v not in (None,"") else ""

def _simple_requirements_page(core,req):
    import alliance_primary_workspace_v730 as ws
    e=_engine(core); ws._role(core,req)
    with e.connect() as c:
        rows=c.execute(text("""
        SELECT r.canonical_id,r.transaction_type,r.locality,r.city,r.area_sqft,r.phones,r.clean_record,r.updated_at,
               COALESCE(w.verification_status,'UNVERIFIED') verification_status
        FROM pi_master_requirements_v711 r
        LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=r.canonical_id AND w.entity_type='REQUIREMENT'
        ORDER BY r.updated_at DESC,r.canonical_id DESC
        """)).mappings().all()
    trs=[];verified_count=0
    for rr in rows:
        r=dict(rr);clean=_json_obj(r.get("clean_record"));cid=_txt(r.get("canonical_id"))
        status=_txt(r.get("verification_status") or "UNVERIFIED").upper();verified=status=="VERIFIED"
        verified_count+=1 if verified else 0
        action=(f"<a class='mini good' href='/alliance/primary/matcher?requirement_id={html.escape(cid,quote=True)}'>Run Matcher</a>"
                if verified else "<span class='pill'>Verify First</span> <a class='mini alt' href='/alliance/final/requirements'>Open Requirements</a>")
        trs.append("<tr>"
          f"<td><b>{html.escape(_req_text(clean) or cid)}</b><br><span class='muted'>{html.escape(cid)}</span></td>"
          f"<td>{html.escape(_txt(r.get('locality') or r.get('city')))}</td>"
          f"<td>{html.escape(_area_label(r,clean))}</td>"
          f"<td>{html.escape(_req_use(clean))}</td>"
          f"<td>{html.escape(_txt(r.get('transaction_type')))}</td>"
          f"<td>{html.escape(_req_contact(clean,r))}</td>"
          f"<td class={'ok' if verified else 'warntext'}>{html.escape(status)}</td><td>{action}</td></tr>")
    body=("<div class='card'><b>Simple flow:</b> Requirement → Run Matcher → Approve Property → Assign Deal → Follow-up."
          "<br><span class='muted'>All requirements stay on this page. Only human VERIFIED requirements can run matching.</span>"
          f"<br><b>{len(rows)}</b> total · <b>{verified_count}</b> verified</div>"
          "<div class='card tablebox'><table><tr><th>Requirement</th><th>Location</th><th>Area</th><th>Type / Use</th>"
          "<th>Rent / Sale</th><th>Contact</th><th>Status</th><th>Action</th></tr>"+''.join(trs)+"</table></div>")
    return HTMLResponse(ws._shell(core,req,"Requirements · Run Matcher",body))

def _install_simple_match_flow(core):
    import alliance_primary_workspace_v730 as ws
    app=_app(core);old_matcher=_endpoint(app,"/alliance/primary/matcher","GET")
    if old_matcher is None:raise RuntimeError("Existing primary matcher route not found")
    if not getattr(ws,"_v11911_assignment_guard_installed",False):
        old_set=ws._set_action
        def guarded_set_action(engine,cid,etype,actor,**fields):
            if str(etype or "").upper()=="REQUIREMENT" and _txt(fields.get("assigned_to")).strip():
                with engine.connect() as c:
                    ok=c.execute(text("""SELECT EXISTS(
                    SELECT 1 FROM pi_match_reviews_v730
                    WHERE requirement_canonical_id=:r AND review_status='APPROVED')"""),{"r":cid}).scalar()
                if not ok:raise HTTPException(409,"Approve a property match first. Deal assignment starts only after matching.")
            return old_set(engine,cid,etype,actor,**fields)
        ws._set_action=guarded_set_action;ws._v11911_assignment_guard_installed=True
    _remove_route(app,"/alliance/primary/requirements","GET");_remove_route(app,"/alliance/primary/matcher","GET")
    @app.get("/alliance/primary/requirements",response_class=HTMLResponse)
    def requirements(req:Request):return _simple_requirements_page(core,req)
    @app.get("/alliance/primary/matcher",response_class=HTMLResponse)
    def matcher(req:Request,requirement_id:str=Query("")):
        if not requirement_id:return RedirectResponse("/alliance/primary/requirements",status_code=303)
        resp=old_matcher(req,requirement_id=requirement_id)
        if not isinstance(resp,HTMLResponse):return resp
        try:
            body=resp.body.decode("utf-8","replace")
            body=re.sub(r"<div class='card'><form class='inline'>\s*<select name='requirement_id'>.*?Run Master Match</button>\s*</form>\s*<p class='muted'>.*?</p></div>","",body,count=1,flags=re.S)
            body=body.replace("<div class=\"wrap\"><h2>","<div class=\"wrap\"><div class='card'><a class='mini alt' href='/alliance/primary/requirements'>← All Requirements</a> <b>Direct Matcher Results</b><br><span class='muted'>Approve a suitable property first. Assignment starts only after approval.</span></div><h2>",1)
            return HTMLResponse(body,headers={"Cache-Control":"no-store"})
        except:return resp
    return {"status":"PASS","requirements":"ALL_ON_ONE_PAGE","matcher":"DIRECT_RESULTS","assignment":"AFTER_APPROVED_MATCH"}

def _address_from_row(s):
    m=ADDRESS_RE.search(s or "")
    return re.sub(r"\s+","",m.group(1).upper()) if m else None

def _category_from_heading(s):
    m=CATEGORY_RE.search(s or "");return m.group(1).upper() if m else None

def _tx_from_heading(s):
    m=TX_RE.search(s or "")
    if not m:return None
    v=m.group(1).upper()
    return "SALE" if v in ("SALE","SELL") else ("LEASE" if v in ("LEASE","LEASING") else "RENT")

def _hierarchy_fastlane_extract(page):
    import alliance_magazine_fastlane_v840 as f
    import alliance_magazine_complete_v860 as comp
    lines=f._native_lines(page);chars=sum(len(x["text"]) for x in lines);width=float(page.rect.width)
    ctx={0:{"category":None,"locality":None},1:{"category":None,"locality":None},2:{"category":None,"locality":None}};out=[]
    for row in lines:
        raw=row["text"];col=f._column_id(row["x0"],width)
        if f._is_heading(raw):
            if _category_from_heading(raw) or _tx_from_heading(raw):
                ctx[col]["category"]=raw.strip();continue
            try:loc=comp._valid_heading(raw)
            except:loc=None
            if loc:ctx[col]["locality"]=loc;continue
        sig=f._signal(raw)
        if sig<3:continue
        phones=f.PHONE_RE.findall(raw)
        pe=bool(f.AREA_RE.search(raw) or f.FLOOR_RE.search(raw) or f.BHK_RE.search(raw) or f.PTYPE_RE.search(raw) or f.AMOUNT_RE.search(raw))
        if f.AGENCY_RE.search(raw) and (f.WEB_RE.search(raw) or f.EMAIL_RE.search(raw)) and phones and not pe:continue
        am=f.AREA_RE.search(raw);fl=f.FLOOR_RE.search(raw);amt=f.AMOUNT_RE.search(raw);pt=f.PTYPE_RE.search(raw)
        cat=ctx[col]["category"];loc=ctx[col]["locality"]
        out.append({"source_method":"NATIVE_PDF_TEXT_HIERARCHY","section_heading":cat,"original_description":raw,
        "transaction_type":f._tx(raw,cat),"property_type":pt.group(1).upper() if pt else None,
        "area_value":am.group(1) if am else None,"area_unit":f._unit(am.group(2)) if am else None,
        "floor":fl.group(1).upper() if fl else None,"amount_raw":amt.group(0).strip() if amt else None,
        "contact_numbers":list(dict.fromkeys(phones)),"signal_score":sig,"needs_review":sig<7 or not phones,"bbox":row["bbox"],
        "raw_json":{"column":col,"text_chars_on_page":chars,"category_heading":cat,"locality_heading":loc,
                    "exact_address":_address_from_row(raw),"hierarchy_version":VERSION}})
    return out,{"method":"NATIVE_PDF_TEXT_HIERARCHY","text_chars":chars,"line_count":len(lines),"hierarchy_version":VERSION}

def _repair_complete_magazine(e,upload_id=None):
    import alliance_magazine_complete_v860 as comp
    with e.begin() as c:c.execute(text("ALTER TABLE pi_magazine_complete_v860 ADD COLUMN IF NOT EXISTS address TEXT"))
    where="";params={}
    if upload_id:where="WHERE f.upload_id=CAST(:u AS UUID)";params["u"]=upload_id
    with e.connect() as c:
        rows=c.execute(text(f"""SELECT c.property_id,c.location,c.property_category,c.contact_name,c.contact_numbers,c.location_source,
        c.original_description,f.upload_id::text upload_id,f.page_number,f.section_heading,f.original_description fast_original,f.raw_json
        FROM pi_magazine_complete_v860 c JOIN pi_magazine_fastlane_records f ON f.record_id=c.source_record_id
        {where} ORDER BY f.upload_id,f.page_number,f.id"""),params).mappings().all()
    state={};counts={"location_repaired":0,"address_repaired":0,"contact_repaired":0,"category_repaired":0}
    with e.begin() as c:
        for rr in rows:
            r=dict(rr);rawj=_json_obj(r.get("raw_json"));col=str(rawj.get("column","0"));key=(r.get("upload_id"),r.get("page_number"),col)
            state.setdefault(key,{"locality":None,"category":None});heading=_txt(r.get("section_heading")).strip()
            if rawj.get("category_heading"):state[key]["category"]=_txt(rawj.get("category_heading"))
            elif _category_from_heading(heading):state[key]["category"]=heading
            if rawj.get("locality_heading"):state[key]["locality"]=_txt(rawj.get("locality_heading"))
            else:
                try:lh=comp._valid_heading(heading)
                except:lh=None
                if lh:state[key]["locality"]=lh
            original=_txt(r.get("fast_original") or r.get("original_description"))
            addr=_txt(rawj.get("exact_address")).strip() or _address_from_row(original)
            sets=[];p={"pid":r["property_id"]}
            if state[key]["locality"] and (not _txt(r.get("location")).strip() or _txt(r.get("location_source")).upper() in ("","UNKNOWN")):
                sets+=["location=:loc","location_source='HIERARCHY_CONTEXT_REPAIR'"];p["loc"]=state[key]["locality"];counts["location_repaired"]+=1
            if addr:sets+=["address=COALESCE(NULLIF(address,''),:addr)"];p["addr"]=addr;counts["address_repaired"]+=1
            cat=_category_from_heading(state[key]["category"] or "")
            if cat and not _txt(r.get("property_category")).strip():
                sets+=["property_category=:cat","category_source='HIERARCHY_SECTION_REPAIR'"];p["cat"]=cat;counts["category_repaired"]+=1
            try:cname,_=comp._contact(original)
            except:cname=None
            try:mob,land=comp._phones(original);phones=mob+land
            except:phones=[]
            if cname and not _txt(r.get("contact_name")).strip():
                sets+=["contact_name=:cname"];p["cname"]=cname;counts["contact_repaired"]+=1
            existing=r.get("contact_numbers")
            if phones and (not isinstance(existing,list) or not existing):
                sets+=["contact_numbers=CAST(:phones AS JSONB)"];p["phones"]=json.dumps(phones);counts["contact_repaired"]+=1
            if sets:
                sets.append("updated_at=NOW()");c.execute(text("UPDATE pi_magazine_complete_v860 SET "+",".join(sets)+" WHERE property_id=:pid"),p)
    return {"processed":len(rows),**counts,"duplicates_created":0}

def _install_magazine_hierarchy(core):
    import alliance_magazine_fastlane_v840 as fastlane
    import alliance_magazine_complete_v860 as comp
    e=_engine(core);fastlane._extract_candidates=_hierarchy_fastlane_extract
    if not getattr(comp,"_v11911_wrapped",False):
        old=comp._build
        def wrapped(engine,upload_id=None):
            result=old(engine,upload_id);out=dict(result or {});out["hierarchy_repair"]=_repair_complete_magazine(engine,upload_id);out["hierarchy_version"]=VERSION;return out
        comp._build=wrapped;comp._v11911_wrapped=True
    repair=_repair_complete_magazine(e,None)
    return {"status":"PASS","future_parser":"SECTION -> LOCALITY -> ADDRESS -> DETAILS -> CONTACT","existing_database_repair":repair,
            "raw_evidence_mutated":False,"duplicates_created":0}

def register(core):
    return {"status":"PASS","version":VERSION,"simple_match_flow":_install_simple_match_flow(core),"magazine_hierarchy":_install_magazine_hierarchy(core)}
