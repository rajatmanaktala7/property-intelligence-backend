from __future__ import annotations

import re, json, uuid
from typing import Any, Dict, List, Tuple
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.routing import APIRoute
from sqlalchemy import text

import alliance_live_feed_purity_legacy36 as _legacy

VERSION="5.1-WHATSAPP-WORKSPACE-LOCATION-MATCHER"

LOCATION_ALIASES={
    "KALKAJI":["KALKAJI"],
    "SAKET":["SAKET","SAKET DISTRICT CENTRE","DISTRICT CENTRE SAKET","DLF AVENUE SAKET","SELECT CITYWALK","SELECT CITY WALK"],
    "MALVIYA NAGAR":["MALVIYA NAGAR"],
    "HAUZ KHAS":["HAUZ KHAS","HAUZ KHAS ENCLAVE"],
    "GREEN PARK":["GREEN PARK","GREEN PARK EXTENSION"],
    "GREATER KAILASH 1":["GK 1","GK-1","GREATER KAILASH 1","GREATER KAILASH-I"],
    "GREATER KAILASH 2":["GK 2","GK-2","GREATER KAILASH 2","GREATER KAILASH-II"],
    "CR PARK":["CR PARK","C R PARK","CHITTARANJAN PARK"],
    "DEFENCE COLONY":["DEFENCE COLONY"],
    "SOUTH EXTENSION":["SOUTH EXTENSION","SOUTH EX","SOUTH EX 1","SOUTH EX 2"],
    "NEHRU PLACE":["NEHRU PLACE"],
    "VASANT KUNJ":["VASANT KUNJ"],
    "VASANT VIHAR":["VASANT VIHAR"],
    "PANCHSHEEL PARK":["PANCHSHEEL PARK"],
    "PANCHSHEEL ENCLAVE":["PANCHSHEEL ENCLAVE"],
    "EAST OF KAILASH":["EAST OF KAILASH"],
    "KAILASH COLONY":["KAILASH COLONY"],
    "OKHLA":["OKHLA","OKHLA INDUSTRIAL AREA"],
    "JASOLA":["JASOLA"],
    "CHHATARPUR":["CHHATARPUR","CHATTARPUR"],
    "MEHRAULI":["MEHRAULI"],
    "GURUGRAM":["GURUGRAM","GURGAON"],
    "DLF PHASE 1":["DLF PHASE 1","DLF PHASE-I"],
    "DLF PHASE 2":["DLF PHASE 2","DLF PHASE-II"],
    "DLF PHASE 3":["DLF PHASE 3","DLF PHASE-III"],
    "DLF PHASE 4":["DLF PHASE 4","DLF PHASE-IV"],
    "SUSHANT LOK 1":["SUSHANT LOK 1","SUSHANT LOK-I"],
    "SIOLIM":["SIOLIM"],
    "ASSAGAO":["ASSAGAO"],
    "SALIGAO":["SALIGAO"],
    "PAREL":["PAREL"],
    "LOWER PAREL":["LOWER PAREL"],
    "MAHALAXMI":["MAHALAXMI"],
    "BREACH CANDY":["BREACH CANDY"],
    "NEPEAN SEA ROAD":["NEPEAN SEA ROAD","NEAPEANSEA ROAD","NEPEANSEA ROAD"],
}
NEARBY={
    "SAKET":{"MALVIYA NAGAR","HAUZ KHAS","PANCHSHEEL PARK","MEHRAULI"},
    "KALKAJI":{"NEHRU PLACE","CR PARK","EAST OF KAILASH","GREATER KAILASH 1"},
    "NEHRU PLACE":{"KALKAJI","CR PARK","EAST OF KAILASH"},
    "GREATER KAILASH 1":{"GREATER KAILASH 2","KAILASH COLONY","CR PARK"},
}
COMMERCIAL={"COMMERCIAL","OFFICE","SHOP","SHOWROOM","RETAIL","WAREHOUSE","GODOWN","BANQUET","RESTAURANT","CAFE","LOUNGE","COMMERCIAL SPACE"}
RESIDENTIAL={"RESIDENTIAL","APARTMENT","FLAT","VILLA","KOTHI","BHK","BUILDER FLOOR","INDEPENDENT HOUSE"}
RENT={"RENT","RENTAL","LEASE","LEASING","TO LET"}
SALE={"SALE","SELL","OUTRIGHT","PURCHASE"}

def esc(v):
    s=str(v or "")
    return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;").replace("'","&#39;")

def norm(v):
    return re.sub(r"\s+"," ",re.sub(r"[^A-Z0-9]+"," ",str(v or "").upper())).strip()

def canonical_location(*texts):
    blob=norm(" ".join(str(x or "") for x in texts))
    for canon, aliases in LOCATION_ALIASES.items():
        for alias in sorted(aliases,key=len,reverse=True):
            a=norm(alias)
            if a and re.search(r"(?<![A-Z0-9])"+re.escape(a)+r"(?![A-Z0-9])",blob):
                return canon.title() if canon not in {"CR PARK","DLF PHASE 1","DLF PHASE 2","DLF PHASE 3","DLF PHASE 4"} else canon
    m=re.search(r"\bLOCATION\s+(?:IS\s+)?([A-Z][A-Z0-9 ]{2,45})",blob)
    if m:
        x=re.split(r"\b(?:AREA|PRICE|RENT|SALE|BUDGET|CONTACT|FLOOR|ROAD|NEAR)\b",m.group(1))[0].strip()
        if x:return x.title()
    m=re.search(r"\b(?:IN|AT)\s+([A-Z][A-Z0-9 ]{2,35})",blob)
    if m:
        x=re.split(r"\b(?:AREA|PRICE|RENT|SALE|BUDGET|CONTACT|FLOOR|ROAD|NEAR|WITH)\b",m.group(1))[0].strip()
        if x:return x.title()
    return "Unknown"

def property_type(textv):
    up=norm(textv)
    if any(x in up for x in COMMERCIAL): return "Commercial"
    if any(x in up for x in RESIDENTIAL): return "Residential"
    return "Property"

def transaction(textv):
    up=norm(textv)
    if any(x in up for x in RENT): return "RENT"
    if any(x in up for x in SALE): return "SALE"
    return "UNKNOWN"

def _latest_generation(engine):
    try:
        with engine.connect() as c:
            return c.execute(text("""SELECT generation_id FROM pi_whatsapp_property_master_generation
              WHERE status='COMPLETED' ORDER BY completed_at DESC NULLS LAST,id DESC LIMIT 1""")).scalar()
    except Exception:return None

def _ensure_overrides(engine):
    # Lazy creation only when WhatsApp workspace is opened. No startup DDL.
    with engine.begin() as c:
        c.execute(text("""
          CREATE TABLE IF NOT EXISTS alliance_whatsapp_property_overrides(
            record_id TEXT PRIMARY KEY,
            description_override TEXT,
            location_override TEXT,
            type_override TEXT,
            transaction_override TEXT,
            price_override TEXT,
            area_override TEXT,
            deleted BOOLEAN DEFAULT FALSE,
            updated_at TIMESTAMPTZ DEFAULT NOW()
          )
        """))

def clean_properties(engine,q="",include_deleted=False,limit=1500):
    _ensure_overrides(engine)
    gen=_latest_generation(engine)
    if not gen:return []
    with engine.connect() as c:
        rows=[dict(x) for x in c.execute(text("""
          SELECT record_id,lead_type,description,area,configuration_details,price,
                 contact_name_number,source,captured_on,verification,source_count
          FROM pi_whatsapp_property_master
          WHERE generation_id=:g
          ORDER BY captured_on DESC NULLS LAST,id DESC
          LIMIT :lim
        """),{"g":gen,"lim":limit}).mappings().all()]
        ovs={r["record_id"]:dict(r) for r in c.execute(text("""
          SELECT * FROM alliance_whatsapp_property_overrides
        """)).mappings().all()}

    out=[]
    qn=norm(q)
    for d in rows:
        ov=ovs.get(d.get("record_id"),{})
        if ov.get("deleted") and not include_deleted:continue

        raw=" ".join(str(d.get(k) or "") for k in ["lead_type","description","configuration_details"])
        blob=norm(raw)
        if any(x in blob for x in ["REJECTED","NEEDS REVIEW","GREETING","PROPERTY REQUIREMENT","LOOKING FOR","WANTED FOR","URGENT REQUIREMENT"]):
            continue

        loc=ov.get("location_override") or canonical_location(d.get("description"),d.get("configuration_details"))
        ptype=ov.get("type_override") or property_type(raw)
        tx=ov.get("transaction_override") or transaction(raw)
        desc=ov.get("description_override") or d.get("description") or "Property Availability"
        area=ov.get("area_override") or d.get("area")
        price=ov.get("price_override") or d.get("price")

        row={
          **d,
          "description":desc,
          "location":loc,
          "property_type":ptype,
          "lead_type":tx if tx!="UNKNOWN" else (d.get("lead_type") or "UNKNOWN"),
          "area":area,
          "price":price,
          "deleted":bool(ov.get("deleted")),
        }
        if qn:
            search=norm(" ".join(str(row.get(k) or "") for k in [
                "record_id","lead_type","description","location","property_type","area","price","contact_name_number","source","verification"
            ]))
            if qn not in search and not all(t in search for t in qn.split()):
                continue
        out.append(row)
    return out

def load_requirements():
    try:
        import whatsapp_live_bridge as live
        if live.wa_engine is None:return []
        with live.wa_engine.connect() as c:
            return [dict(x) for x in c.execute(text("""
              SELECT r.*,COALESCE(s.group_name,'WhatsApp Group') source_group
              FROM wa_requirements r
              LEFT JOIN wa_sources s ON s.source_id=r.source_id
              WHERE COALESCE(r.status,'ACTIVE')='ACTIVE'
              ORDER BY r.id DESC LIMIT 1500
            """)).mappings().all()]
    except Exception:return []

def req_location(r):
    return canonical_location(r.get("preferred_locations"),r.get("raw_text"))

def req_transaction(r):
    v=norm(r.get("transaction_type"))
    if "RENT" in v:return "RENT"
    if "SALE" in v:return "SALE"
    return transaction(r.get("raw_text"))

def req_type(r):
    v=norm(r.get("property_type"))
    if any(x in v for x in COMMERCIAL):return "Commercial"
    if any(x in v for x in RESIDENTIAL):return "Residential"
    return property_type(r.get("raw_text"))

def _num(v):
    if v in (None,""):return None
    try:return float(v)
    except:
        m=re.search(r"[\d,.]+",str(v))
        if not m:return None
        try:return float(m.group(0).replace(",",""))
        except:return None

def match_one(req,prop,allow_nearby=False):
    target=req_location(req)
    ploc=prop.get("location") or "Unknown"

    if target!="Unknown":
        if norm(ploc)==norm(target):
            loc_score=40; loc_reason=f"Exact location: {target}"
        elif allow_nearby and norm(ploc) in {norm(x) for x in NEARBY.get(norm(target),set())}:
            loc_score=25; loc_reason=f"Nearby alternative to {target}: {ploc}"
        else:
            return None
    else:
        loc_score=20; loc_reason="Requirement location not structured"

    rtx=req_transaction(req); ptx=norm(prop.get("lead_type"))
    if rtx!="UNKNOWN" and rtx not in ptx:
        return None

    rt=req_type(req); pt=prop.get("property_type")
    if rt in {"Commercial","Residential"} and pt!=rt:
        return None

    score=loc_score+20+15
    reasons=[loc_reason,f"Transaction: {rtx}",f"Property type: {rt}"]

    amin=_num(req.get("minimum_area_sqft")); amax=_num(req.get("maximum_area_sqft")); pa=_num(prop.get("area"))
    if pa and (amin or amax):
        low=amin or amax; high=amax or amin
        if low<=pa<=high:
            score+=15; reasons.append("Area within requirement")
        else:
            mid=(low+high)/2
            if mid and abs(pa-mid)/mid<=0.25:
                score+=8; reasons.append("Area near requirement")

    if norm(prop.get("verification"))=="VERIFIED":
        score+=5; reasons.append("Verified")

    if prop.get("contact_name_number"):
        score+=5; reasons.append("Contact available")

    return min(score,100),reasons

def match_requirement(engine,req):
    props=clean_properties(engine,"",False,2000)
    exact=[]; nearby=[]
    for p in props:
        m=match_one(req,p,False)
        if m:
            exact.append((m[0],m[1],p));continue
        m2=match_one(req,p,True)
        if m2: nearby.append((m2[0],m2[1],p))
    exact.sort(key=lambda x:x[0],reverse=True)
    nearby.sort(key=lambda x:x[0],reverse=True)
    return exact[:100],nearby[:50]

def page(body,active="availability"):
    tabs=[
      ("availability","1. Availability"),
      ("requirements","2. Requirements"),
      ("match","3. Match Properties"),
    ]
    links="".join(
      f"<a class='tab {'on' if active==k else ''}' href='/whatsapp-live?section={k}'>{label}</a>"
      for k,label in tabs
    )
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>WhatsApp Group Workspace</title><style>
    *{{box-sizing:border-box}}body{{margin:0;font-family:Arial;background:#efe4d2;color:#2d261f}}
    header{{background:#5d4937;color:#fff;padding:18px 24px}}main{{max-width:1850px;margin:auto;padding:18px}}
    .top{{background:#fffdf9;padding:10px 18px;display:flex;gap:8px;flex-wrap:wrap;border-bottom:1px solid #dccdbb}}
    .top a,.tab,button,.btn{{text-decoration:none;border:0;background:#6c543f;color:#fff;padding:9px 12px;border-radius:7px;font-weight:800;cursor:pointer}}
    .tabs{{display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap}}.tab{{background:#b7a18b}}.tab.on{{background:#5d4937}}
    .card{{background:#fffdf9;border:1px solid #dccdbb;border-radius:12px;padding:14px;margin-bottom:14px}}
    .scroll{{overflow:auto;max-height:72vh}}table{{width:100%;min-width:1600px;border-collapse:collapse;background:#fff}}
    th,td{{padding:9px;border-bottom:1px solid #eee0ce;text-align:left;vertical-align:top;font-size:12px}}th{{background:#f7ecdf;position:sticky;top:0}}
    input,textarea,select{{width:100%;padding:9px;border:1px solid #d0c1af;border-radius:7px}}.desc{{min-width:420px;max-width:650px;line-height:1.4}}
    .loc{{font-weight:800;min-width:120px}}.good{{color:#176b3a;font-weight:800}}.muted{{color:#7a6b5c}}
    .danger{{background:#9a3f36}}.edit{{background:#377a4b}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}}
    </style></head><body><header><h2 style='margin:0'>WhatsApp Group Property Workspace</h2><small>Availability · Requirements · Match Properties</small></header>
    <div class=top><a href='/team-dashboard-v376'>← Dashboard</a><a href='/workspace'>Working Space</a><a href='/whatsapp-live/sources'>WhatsApp Sources</a><a href='/whatsapp-live/raw-feed-v45'>Raw Audit</a></div>
    <main><div class=tabs>{links}</div>{body}</main></body></html>"""

def property_rows(rows):
    trs=[]
    for r in rows:
        trs.append(f"""<tr>
        <td>{esc(r.get('record_id'))}</td><td>{esc(r.get('lead_type'))}</td>
        <td class=desc>{esc(r.get('description'))}</td>
        <td class=loc>{esc(r.get('location'))}</td>
        <td>{esc(r.get('property_type'))}</td><td>{esc(r.get('area'))}</td><td>{esc(r.get('price'))}</td>
        <td>{esc(r.get('contact_name_number'))}</td><td>{esc(r.get('source'))}</td>
        <td>{esc(r.get('captured_on'))}</td><td>{esc(r.get('verification'))}</td>
        <td><a class='btn edit' href='/whatsapp-live/edit/{esc(r.get("record_id"))}'>Edit</a>
        <form method=post action='/whatsapp-live/delete/{esc(r.get("record_id"))}' style='display:inline'>
        <button class=danger onclick="return confirm('Hide this property from working database? Source WhatsApp data will remain preserved.')">Delete</button></form></td>
        </tr>""")
    return "".join(trs)

def match_rows(rows):
    trs=[]
    for sc,reasons,r in rows:
        trs.append(f"""<tr><td><b>{sc:.0f}%</b></td><td class=loc>{esc(r.get('location'))}</td>
        <td class=desc>{esc(r.get('description'))}</td><td>{esc(r.get('lead_type'))}</td><td>{esc(r.get('property_type'))}</td>
        <td>{esc(r.get('area'))}</td><td>{esc(r.get('price'))}</td><td>{esc(r.get('contact_name_number'))}</td>
        <td>{esc(r.get('source'))}</td><td>{esc('; '.join(reasons))}</td></tr>""")
    return "".join(trs)

def register(wrapped):
    legacy_result=_legacy.register(wrapped)
    app=wrapped.app; core=wrapped.core; engine=core.engine

    owned={"/whatsapp-live","/whatsapp-live/feed","/whatsapp-live/requirements"}
    kept=[]
    for route in app.router.routes:
        path=getattr(route,"path",None); methods=getattr(route,"methods",set()) or set()
        if isinstance(route,APIRoute) and "GET" in methods and path in owned:
            continue
        kept.append(route)
    app.router.routes[:]=kept

    router=APIRouter()

    @router.get("/api/v51/status")
    def status():
        return {"status":"OK","version":VERSION,"workspace":True,"sections":["Availability","Requirements","Match Properties"],
                "location_column":"canonical only","edit":True,"soft_delete":True,"source_data_preserved":True,
                "matcher":"location-first hard gates"}

    @router.get("/whatsapp-live",response_class=HTMLResponse)
    def workspace(request:Request):
        section=str(request.query_params.get("section") or "availability")
        if section=="requirements":
            reqs=load_requirements()
            trs=""
            for r in reqs:
                rid=r.get("wa_requirement_id")
                trs+=f"""<tr><td>{esc(rid)}</td><td class=loc>{esc(req_location(r))}</td><td>{esc(req_transaction(r))}</td>
                <td>{esc(req_type(r))}</td><td class=desc>{esc(r.get('raw_text'))}</td>
                <td>{esc(r.get('minimum_area_sqft'))} - {esc(r.get('maximum_area_sqft'))}</td>
                <td>{esc(r.get('budget_max_inr'))}</td><td>{esc(r.get('contact_name'))} · {esc(r.get('contact_phone'))}</td>
                <td>{esc(r.get('source_group'))}</td>
                <td><a class=btn href='/whatsapp-live?section=match&requirement_id={esc(rid)}'>Match Now</a></td></tr>"""
            body=f"""<div class=card><h2>2. Requirements</h2><p class=muted>Only active requirements. Click Match Now to run location-first matching.</p></div>
            <div class=scroll><table><tr><th>Requirement ID</th><th>Location</th><th>Transaction</th><th>Property Type</th><th>Description</th><th>Area</th><th>Budget</th><th>Contact</th><th>Source</th><th>Action</th></tr>
            {trs or '<tr><td colspan=10>No active requirements.</td></tr>'}</table></div>"""
            return HTMLResponse(page(body,"requirements"))

        if section=="match":
            rid=str(request.query_params.get("requirement_id") or "")
            reqs=load_requirements()
            req=next((x for x in reqs if str(x.get("wa_requirement_id"))==rid),None)
            opts="".join(f"<option value='{esc(r.get('wa_requirement_id'))}' {'selected' if str(r.get('wa_requirement_id'))==rid else ''}>{esc(req_location(r))} · {esc(req_transaction(r))} · {esc((r.get('raw_text') or '')[:90])}</option>" for r in reqs)
            if req:
                exact,near=match_requirement(engine,req)
                body=f"""<div class=card><h2>3. Match Properties</h2>
                <form method=get><input type=hidden name=section value=match><div class=grid><div><label>Requirement</label><select name=requirement_id>{opts}</select></div><div style='align-self:end'><button>Run Match</button></div></div></form>
                <p><b>Location:</b> {esc(req_location(req))} · <b>Transaction:</b> {esc(req_transaction(req))} · <b>Type:</b> {esc(req_type(req))}</p>
                <p class=good>Wrong-location, wrong-transaction and wrong-property-type inventory is excluded before scoring.</p></div>
                <div class=card><h3>A. Exact Location Matches ({len(exact)})</h3><div class=scroll><table><tr><th>Match</th><th>Location</th><th>Description</th><th>Transaction</th><th>Type</th><th>Area</th><th>Price/Rent</th><th>Contact</th><th>Source</th><th>Why</th></tr>{match_rows(exact) or '<tr><td colspan=10>No exact matches.</td></tr>'}</table></div></div>
                <div class=card><h3>B. Smart Nearby Alternatives ({len(near)})</h3><p class=muted>Shown separately, never mixed with exact-location results.</p><div class=scroll><table><tr><th>Match</th><th>Location</th><th>Description</th><th>Transaction</th><th>Type</th><th>Area</th><th>Price/Rent</th><th>Contact</th><th>Source</th><th>Why</th></tr>{match_rows(near) or '<tr><td colspan=10>No nearby alternatives.</td></tr>'}</table></div></div>"""
            else:
                body=f"""<div class=card><h2>3. Match Properties</h2><form method=get><input type=hidden name=section value=match><label>Select Requirement</label><select name=requirement_id><option value=''>Choose requirement</option>{opts}</select><p><button>Run Match</button></p></form></div>"""
            return HTMLResponse(page(body,"match"))

        q=str(request.query_params.get("q") or "")
        rows=clean_properties(engine,q,False,1500)
        body=f"""<div class=card><h2>1. Availability</h2><form method=get><input type=hidden name=section value=availability>
        <div class=grid><div><label>Search</label><input name=q value='{esc(q)}' placeholder='Kalkaji, Saket, office, rent, contact...'></div><div style='align-self:end'><button>Search</button></div></div></form>
        <p class=muted>Description and Location are separate. Location contains only the canonical location.</p></div>
        <div class=scroll><table><tr><th>Record</th><th>Rent/Sale</th><th>Description</th><th>Location</th><th>Property Type</th><th>Area</th><th>Price/Rent</th><th>Contact</th><th>Source</th><th>Captured</th><th>Verification</th><th>Action</th></tr>
        {property_rows(rows) or '<tr><td colspan=12>No properties.</td></tr>'}</table></div>"""
        return HTMLResponse(page(body,"availability"))

    @router.get("/whatsapp-live/feed")
    def feed_redirect():
        return RedirectResponse("/whatsapp-live?section=availability",303)

    @router.get("/whatsapp-live/requirements")
    def req_redirect():
        return RedirectResponse("/whatsapp-live?section=requirements",303)

    @router.get("/whatsapp-live/edit/{record_id}",response_class=HTMLResponse)
    def edit_form(record_id:str):
        rows=clean_properties(engine,"",True,2500)
        r=next((x for x in rows if str(x.get("record_id"))==record_id),None)
        if not r:raise HTTPException(404,"Property not found")
        body=f"""<div class=card><h2>Edit Property</h2><p class=muted>Edits affect the working database only. Original WhatsApp source remains unchanged.</p>
        <form method=post><div class=grid><div><label>Location</label><input name=location value='{esc(r.get("location"))}'></div>
        <div><label>Transaction</label><select name=transaction><option>{esc(r.get("lead_type"))}</option><option>RENT</option><option>SALE</option></select></div>
        <div><label>Property Type</label><select name=property_type><option>{esc(r.get("property_type"))}</option><option>Commercial</option><option>Residential</option><option>Property</option></select></div>
        <div><label>Area</label><input name=area value='{esc(r.get("area"))}'></div><div><label>Price/Rent</label><input name=price value='{esc(r.get("price"))}'></div>
        <div class='card' style='grid-column:1/-1'><label>Description</label><textarea name=description rows=5>{esc(r.get("description"))}</textarea></div></div>
        <button class=edit>Save Changes</button></form></div>"""
        return HTMLResponse(page(body,"availability"))

    @router.post("/whatsapp-live/edit/{record_id}")
    def edit_save(record_id:str,description:str=Form(""),location:str=Form(""),property_type:str=Form(""),
                  transaction:str=Form(""),area:str=Form(""),price:str=Form("")):
        _ensure_overrides(engine)
        with engine.begin() as c:
            c.execute(text("""INSERT INTO alliance_whatsapp_property_overrides
              (record_id,description_override,location_override,type_override,transaction_override,price_override,area_override,deleted,updated_at)
              VALUES(:r,:d,:l,:t,:x,:p,:a,FALSE,NOW())
              ON CONFLICT(record_id) DO UPDATE SET description_override=EXCLUDED.description_override,
              location_override=EXCLUDED.location_override,type_override=EXCLUDED.type_override,
              transaction_override=EXCLUDED.transaction_override,price_override=EXCLUDED.price_override,
              area_override=EXCLUDED.area_override,deleted=FALSE,updated_at=NOW()"""),
              {"r":record_id,"d":description,"l":location,"t":property_type,"x":transaction,"p":price,"a":area})
        return RedirectResponse("/whatsapp-live?section=availability",303)

    @router.post("/whatsapp-live/delete/{record_id}")
    def soft_delete(record_id:str):
        _ensure_overrides(engine)
        with engine.begin() as c:
            c.execute(text("""INSERT INTO alliance_whatsapp_property_overrides(record_id,deleted,updated_at)
              VALUES(:r,TRUE,NOW()) ON CONFLICT(record_id) DO UPDATE SET deleted=TRUE,updated_at=NOW()"""),{"r":record_id})
        return RedirectResponse("/whatsapp-live?section=availability",303)

    app.include_router(router)
    return {"status":"REGISTERED","version":VERSION,"legacy":legacy_result,
            "workspace_sections":["Availability","Requirements","Match Properties"]}
