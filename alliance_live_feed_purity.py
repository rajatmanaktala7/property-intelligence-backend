from __future__ import annotations

import re
from datetime import datetime, date, timezone
from urllib.parse import quote_plus
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
import alliance_live_feed_purity_legacy36 as _legacy

VERSION="5.4-FIXED-FIELDS-SINGLE-MATCHER"
OWNER="ALLIANCE_V54_WHATSAPP_DATA_WORKSPACE"

LOCATION_ALIASES={
 "KALKAJI":["KALKAJI"],"SAKET":["SAKET","SAKET DISTRICT CENTRE","DISTRICT CENTRE SAKET","DLF AVENUE SAKET","SELECT CITYWALK","SELECT CITY WALK"],
 "MALVIYA NAGAR":["MALVIYA NAGAR"],"HAUZ KHAS":["HAUZ KHAS"],"GREEN PARK":["GREEN PARK"],
 "GREATER KAILASH 1":["GK 1","GK-1","GK1","GREATER KAILASH 1"],"GREATER KAILASH 2":["GK 2","GK-2","GK2","GREATER KAILASH 2"],
 "CR PARK":["CR PARK","C R PARK","CHITTARANJAN PARK"],"NEHRU PLACE":["NEHRU PLACE"],"EAST OF KAILASH":["EAST OF KAILASH"],
 "KAILASH COLONY":["KAILASH COLONY"],"DEFENCE COLONY":["DEFENCE COLONY"],"SOUTH EXTENSION":["SOUTH EXTENSION","SOUTH EX"],
 "VASANT KUNJ":["VASANT KUNJ"],"VASANT VIHAR":["VASANT VIHAR"],"PANCHSHEEL PARK":["PANCHSHEEL PARK"],"OKHLA":["OKHLA"],"JASOLA":["JASOLA"],
 "MEHRAULI":["MEHRAULI"],"CHHATARPUR":["CHHATARPUR","CHATTARPUR"],"CONNAUGHT PLACE":["CONNAUGHT PLACE","CP"],
 "DLF PHASE 1":["DLF PHASE 1","DLFPHASE1"],"DLF PHASE 2":["DLF PHASE 2","DLFPHASE2"],"DLF PHASE 4":["DLF PHASE 4","DLFPHASE4"],
 "SUSHANT LOK 1":["SUSHANT LOK 1","SUSHANTLOK1"],"GURUGRAM":["GURUGRAM","GURGAON"],"SIOLIM":["SIOLIM"],"ASSAGAO":["ASSAGAO"],"PANAJI":["PANAJI","PANJIM"]
}

def esc(v):
    return str(v or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;").replace("'","&#39;")

def norm(v):
    return re.sub(r"\s+"," ",re.sub(r"[^A-Z0-9]+"," ",str(v or "").upper())).strip()

def canonical_location(*vals):
    blob=norm(" ".join(str(v or "") for v in vals))
    found=[]
    for canon,aliases in LOCATION_ALIASES.items():
        for a in aliases:
            aa=norm(a)
            if re.search(r"(?<![A-Z0-9])"+re.escape(aa)+r"(?![A-Z0-9])",blob):
                found.append((len(aa),canon))
    if found:
        return sorted(found,reverse=True)[0][1].title()
    return "Unknown"

def tx(v):
    n=norm(v)
    if any(x in n for x in ("RENT","RENTAL","LEASE","LEASING","TO LET")):return "RENT"
    if any(x in n for x in ("SALE","SELL","OUTRIGHT","PURCHASE","RESALE")):return "SALE"
    return "UNKNOWN"

def ptype(v):
    n=norm(v)
    if any(x in n for x in ("COMMERCIAL","OFFICE","SHOP","SHOWROOM","RETAIL","WAREHOUSE","GODOWN","BANQUET","RESTAURANT","CAFE","LOUNGE","HOTEL","GUEST HOUSE")):return "Commercial"
    if any(x in n for x in ("RESIDENTIAL","APARTMENT","FLAT","VILLA","KOTHI","BHK","BUILDER FLOOR","INDEPENDENT HOUSE","PENTHOUSE")):return "Residential"
    if any(x in n for x in ("PLOT","LAND","FARMHOUSE")):return "Land"
    return "Property"


PHONE_RE=re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d(?:[\s-]?\d){8}(?!\d)")

def contact_number(v):
    raw=str(v or "")
    m=PHONE_RE.search(raw)
    if not m:return ""
    d=re.sub(r"\D","",m.group(0))
    if len(d)==12 and d.startswith("91"): d=d[2:]
    if len(d)==11 and d.startswith("0"): d=d[1:]
    return ("+91 "+d) if len(d)==10 else m.group(0).strip()

def contact_name(v):
    raw=str(v or "").strip()
    ph=PHONE_RE.search(raw)
    if ph:
        raw=(raw[:ph.start()]+" "+raw[ph.end():]).strip(" ·|-/,")
    raw=re.sub(r"\s*[·|/,-]\s*$","",raw).strip()
    return raw

def noise(v):
    n=norm(v)
    return any(x in n for x in ("GOOD MORNING","GOOD NIGHT","MOTIVATIONAL","HAPPY BIRTHDAY","CONGRATULATIONS","REJECTED"))

def requirement_like(v):
    n=norm(v)
    demand=("REQUIRE","REQUIREMENT","LOOKING FOR","WANTED","CLIENT NEED","URGENT REQUIREMENT")
    supply=("AVAILABLE","FOR RENT","FOR SALE","TO LET","EXCLUSIVE MANDATE","RESALE","READY TO MOVE")
    return any(x in n for x in demand) and not any(x in n for x in supply)

def ordinal(d):
    if not d:return "—"
    try:
        if isinstance(d,str):
            # supports YYYY-MM-DD and ISO
            dt=datetime.fromisoformat(d.replace("Z","+00:00"))
        elif isinstance(d,date) and not isinstance(d,datetime):
            dt=datetime(d.year,d.month,d.day)
        else:dt=d
        day=dt.day
        suf="th" if 11<=day%100<=13 else {1:"st",2:"nd",3:"rd"}.get(day%10,"th")
        return f"{day}{suf} {dt.strftime('%b %Y')}"
    except Exception:return str(d)

def _engine():
    import whatsapp_live_bridge as live
    return live.wa_engine

def _ensure(engine):
    if engine is None:return
    with engine.begin() as c:
        c.execute(text("""CREATE TABLE IF NOT EXISTS alliance_whatsapp_v52_overrides(
          record_id TEXT PRIMARY KEY,description_override TEXT,location_override TEXT,transaction_override TEXT,
          property_type_override TEXT,area_override TEXT,price_override TEXT,verification_override TEXT,
          deleted BOOLEAN DEFAULT FALSE,updated_at TIMESTAMPTZ DEFAULT NOW())"""))

def _latest_generation(engine):
    try:
        with engine.connect() as c:
            return c.execute(text("""SELECT generation_id FROM pi_whatsapp_property_master_generation
              WHERE status='COMPLETED' ORDER BY completed_at DESC NULLS LAST,id DESC LIMIT 1""")).scalar()
    except Exception:return None

def properties(q="",include_deleted=False,limit=2500):
    engine=_engine()
    if engine is None:return []
    _ensure(engine);rows=[];gen=_latest_generation(engine)
    if gen:
        try:
            with engine.connect() as c:
                rows=[dict(r) for r in c.execute(text("""SELECT record_id,lead_type,description,area,configuration_details,price,
                  contact_name_number,source,captured_on,verification,source_count FROM pi_whatsapp_property_master
                  WHERE generation_id=:g ORDER BY captured_on DESC NULLS LAST,id DESC LIMIT :lim"""),{"g":gen,"lim":limit}).mappings().all()]
        except Exception:rows=[]
    if not rows:
        try:
            with engine.connect() as c:
                rows=[dict(r) for r in c.execute(text("""SELECT p.wa_property_id record_id,p.transaction_type lead_type,p.raw_text description,
                  COALESCE(p.available_area_sqft,p.area_sqft)::text area,CONCAT_WS(' · ',p.location,p.locality,p.floor,p.property_type) configuration_details,
                  COALESCE(p.rent_inr,p.sale_price_inr)::text price,CONCAT_WS(' · ',COALESCE(p.owner_name,p.broker_name,p.sender_name),
                  COALESCE(p.owner_phone,p.broker_phone,p.sender_phone)) contact_name_number,COALESCE(s.group_name,s.source_name,'WhatsApp') source,
                  p.last_seen captured_on,COALESCE(p.verification_status,p.availability,'UNVERIFIED') verification,1 source_count
                  FROM wa_properties p LEFT JOIN wa_sources s ON s.source_id=p.source_id
                  WHERE COALESCE(p.record_status,'ACTIVE')='ACTIVE' ORDER BY p.id DESC LIMIT :lim"""),{"lim":limit}).mappings().all()]
        except Exception:rows=[]
    ovs={}
    try:
        with engine.connect() as c:
            ovs={str(r["record_id"]):dict(r) for r in c.execute(text("SELECT * FROM alliance_whatsapp_v52_overrides")).mappings().all()}
    except Exception:pass
    out=[];qn=norm(q)
    for r in rows:
        rid=str(r.get("record_id") or "");ov=ovs.get(rid,{})
        if ov.get("deleted") and not include_deleted:continue
        raw=" ".join(str(r.get(k) or "") for k in ("description","configuration_details","lead_type"))
        if noise(raw) or requirement_like(raw):continue
        item={**r,"record_id":rid,
          "description":ov.get("description_override") or r.get("description") or "Property availability",
          "location":ov.get("location_override") or canonical_location(r.get("configuration_details"),r.get("description")),
          "transaction":ov.get("transaction_override") or tx(raw),
          "property_type":ov.get("property_type_override") or ptype(raw),
          "area":ov.get("area_override") or r.get("area"),
          "price":ov.get("price_override") or r.get("price"),
          "verification":ov.get("verification_override") or r.get("verification") or "UNVERIFIED"}
        if qn:
            hay=norm(" ".join(str(v or "") for v in item.values()))
            if qn not in hay and not all(t in hay for t in qn.split()):continue
        out.append(item)
    return out

def _parse_message_ts(value):
    """Convert WhatsApp message_timestamp text to a SQL-comparable timestamp in Python."""
    if not value:return None
    s=str(value).strip()
    # ISO format is the primary live bridge format.
    try:
        return datetime.fromisoformat(s.replace("Z","+00:00"))
    except Exception:pass
    # Common WhatsApp export dates.
    for fmt in ("%d/%m/%Y, %H:%M","%d/%m/%Y %H:%M","%m/%d/%Y, %H:%M","%Y-%m-%d %H:%M:%S"):
        try:return datetime.strptime(s,fmt)
        except Exception:pass
    return None

def requirement_rows(selected_date=None,limit=2000):
    engine=_engine()
    if engine is None:return []
    try:
        with engine.connect() as c:
            rows=[dict(r) for r in c.execute(text("""SELECT r.*,COALESCE(s.group_name,s.source_name,'WhatsApp') source_group,
              m.message_timestamp original_message_timestamp,m.created_at message_ingested_at
              FROM wa_requirements r LEFT JOIN wa_sources s ON s.source_id=r.source_id
              LEFT JOIN wa_messages m ON m.message_id=r.message_id
              WHERE COALESCE(r.status,'ACTIVE')='ACTIVE' ORDER BY r.id DESC LIMIT :lim"""),{"lim":limit}).mappings().all()]
    except Exception:return []
    out=[]
    for r in rows:
        dt=_parse_message_ts(r.get("original_message_timestamp"))
        if dt is None:
            dt=r.get("created_at")
        # message timestamps with offset are converted to IST; naive WhatsApp timestamps are assumed local IST.
        local_date=None
        if isinstance(dt,datetime):
            if dt.tzinfo is not None:
                try:
                    from zoneinfo import ZoneInfo
                    dt=dt.astimezone(ZoneInfo("Asia/Kolkata"))
                except Exception:pass
            local_date=dt.date().isoformat()
        elif dt:
            local_date=str(dt)[:10]
        r["effective_date"]=local_date
        r["effective_date_label"]=ordinal(dt)
        if selected_date and local_date!=selected_date:continue
        out.append(r)
    return out

def rloc(r):return canonical_location(r.get("preferred_locations"),r.get("raw_text"))
def rtx(r):return tx(" ".join(str(r.get(k) or "") for k in ("transaction_type","raw_text")))
def rpt(r):return ptype(" ".join(str(r.get(k) or "") for k in ("property_type","raw_text")))

def page(body,active="availability"):
    tabs="".join(f"<a class='tab {'on' if active==k else ''}' href='/whatsapp-live?section={k}'>{label}</a>" for k,label in
      [("availability","1. Availability"),("requirements","2. Date-wise Requirements")]) + "<a class='tab matcher' href='/deal-match-ai-v60'>3. Alliance Deal Matcher</a>"
    return f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>WhatsApp Group Property Workspace</title><style>
    *{{box-sizing:border-box}}body{{margin:0;font-family:Arial;background:#efe4d2;color:#2c251e}}header{{background:#594634;color:#fff;padding:18px 24px}}
    nav,main{{max-width:1900px;margin:auto}}nav{{padding:12px 18px;background:#fffaf4;display:flex;gap:8px;flex-wrap:wrap}}a,.btn,button{{background:#6c543f;color:#fff;text-decoration:none;border:0;border-radius:7px;padding:9px 12px;font-weight:800;cursor:pointer}}
    main{{padding:18px}}.tabs{{display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap}}.tab{{background:#ad9882}}.tab.on{{background:#594634}}.tab.matcher{{background:#315f8d}}.card{{background:#fffdf9;border:1px solid #d9c9b7;border-radius:12px;padding:14px;margin-bottom:14px}}
    .scroll{{overflow:auto;max-height:72vh}}table{{width:100%;min-width:1550px;border-collapse:collapse;background:white}}th,td{{padding:9px;border-bottom:1px solid #eee1d1;text-align:left;vertical-align:top;font-size:12px}}
    th{{background:#f7ecdf;position:sticky;top:0}}.desc{{min-width:400px;max-width:620px;line-height:1.4}}.loc{{font-weight:800;min-width:130px}}input,textarea,select{{width:100%;padding:9px;border:1px solid #cdbba8;border-radius:7px}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}}.green{{background:#39734a}}.red{{background:#963d35}}.blue{{background:#315f8d}}.muted{{color:#756757}}.good{{color:#246b3d;font-weight:800}}
    </style></head><body><header><h2 style='margin:0'>WhatsApp Group Property Workspace</h2><small>Clean Availability → Accurate Date-wise Requirements → Alliance Deal Match AI</small></header>
    <nav><a href='/team-dashboard-v376'>← Dashboard</a><a href='/workspace'>Working Space</a><a href='/whatsapp-live/sources'>WhatsApp Sources</a><a href='/deal-match-ai-v60'>Alliance Deal Match AI</a><a href='/whatsapp-live/raw-audit'>Admin Raw Audit</a></nav>
    <main><div class=tabs>{tabs}</div>{body}</main></body></html>"""

def prop_table(rows):
    tr=[]
    for r in rows:
        rid=esc(r.get("record_id"))
        tr.append(f"""<tr><td>{rid}</td><td>{esc(r.get('transaction'))}</td><td class=desc>{esc(r.get('description'))}</td><td class=loc>{esc(r.get('location'))}</td>
        <td>{esc(r.get('property_type'))}</td><td>{esc(r.get('area'))}</td><td>{esc(r.get('price'))}</td><td>{esc(contact_name(r.get('contact_name_number')))}</td><td><b>{esc(contact_number(r.get('contact_name_number')))}</b></td><td>{esc(r.get('source'))}</td>
        <td>{esc(r.get('captured_on'))}</td><td>{esc(r.get('verification'))}</td><td><a class='btn green' href='/whatsapp-live/edit/{rid}'>Edit</a>
        <form style='display:inline' method=post action='/whatsapp-live/delete/{rid}'><button class=red onclick="return confirm('Hide from working database? Original source remains preserved.')">Delete</button></form></td></tr>""")
    return "".join(tr)

def render_workspace(request):
    sec=request.query_params.get("section","availability")
    if sec=="match":
        return RedirectResponse("/deal-match-ai-v60",303)
    if sec=="requirements":
        selected=request.query_params.get("date","").strip()
        rs=requirement_rows(selected or None)
        # If no date selected, show all active requirements, but date column is accurate.
        trs=""
        for r in rs:
            rid=esc(r.get("wa_requirement_id"));raw=str(r.get("raw_text") or "")
            trs+=f"""<tr><td><b>{esc(r.get('effective_date_label'))}</b></td><td>{rid}</td><td class=loc>{esc(rloc(r))}</td><td>{esc(rtx(r))}</td><td>{esc(rpt(r))}</td>
              <td class=desc>{esc(raw)}</td><td>{esc(r.get('minimum_area_sqft'))} - {esc(r.get('maximum_area_sqft'))}</td><td>{esc(r.get('budget_max_inr'))}</td>
              <td>{esc(r.get('contact_name'))}</td><td><b>{esc(r.get('contact_phone'))}</b></td><td>{esc(r.get('source_group'))}</td></tr>"""
        body=f"""<div class=card><h2>2. Date-wise Requirements</h2><p class=muted>Date uses the original WhatsApp message timestamp first, converted to India time. Server ingestion time is only a fallback.</p>
        <p><a class='btn blue' href='/deal-match-ai-v60'>Run Alliance Deal Matcher</a> <span class=muted>Single matcher for WhatsApp, Newspaper, Master and other property databases.</span></p>
        <form method=get><input type=hidden name=section value=requirements><div class=grid><div><label>Requirement Date</label><input type=date name=date value='{esc(selected)}'></div>
        <div style='align-self:end'><button>Show Date</button> <a class=btn href='/whatsapp-live?section=requirements'>Show All</a></div></div></form></div>
        <div class=scroll><table><tr><th>Date</th><th>ID</th><th>Location</th><th>Transaction</th><th>Type</th><th>Description</th><th>Area</th><th>Budget</th><th>Contact Name</th><th>Contact Number</th><th>Source</th></tr>
        {trs or '<tr><td colspan=11>No active requirements for this date.</td></tr>'}</table></div>"""
        return HTMLResponse(page(body,"requirements"))
    q=request.query_params.get("q","");rows=properties(q=q)
    body=f"""<div class=card><h2>1. Availability</h2><form><input type=hidden name=section value=availability><div class=grid><div><label>Search</label><input name=q value='{esc(q)}' placeholder='Kalkaji, Saket, rent, commercial...'></div>
    <div style='align-self:end'><button>Search</button></div></div></form><p class=muted>Availability is a clean property database. Matching is handled only by Alliance Deal Match AI to avoid overlapping engines.</p></div>
    <div class=scroll><table><tr><th>Record</th><th>Rent/Sale</th><th>Description</th><th>Location</th><th>Property Type</th><th>Area</th><th>Price/Rent</th><th>Contact Name</th><th>Contact Number</th><th>Source</th><th>Captured</th><th>Verification</th><th>Action</th></tr>
    {prop_table(rows) or '<tr><td colspan=13>No clean availability records.</td></tr>'}</table></div>"""
    return HTMLResponse(page(body,"availability"))

def render_edit(record_id):
    row=next((r for r in properties(include_deleted=True,limit=3000) if str(r.get("record_id"))==record_id),None)
    if not row:return HTMLResponse("Property not found",404)
    body=f"""<div class=card><h2>Edit Property</h2><p class=muted>Working-layer edit only. Original WhatsApp source remains unchanged.</p><form method=post><div class=grid>
    <div><label>Location</label><input name=location value='{esc(row.get("location"))}'></div><div><label>Transaction</label><select name=transaction><option>{esc(row.get("transaction"))}</option><option>RENT</option><option>SALE</option></select></div>
    <div><label>Property Type</label><select name=property_type><option>{esc(row.get("property_type"))}</option><option>Commercial</option><option>Residential</option><option>Land</option><option>Property</option></select></div>
    <div><label>Area</label><input name=area value='{esc(row.get("area"))}'></div><div><label>Price/Rent</label><input name=price value='{esc(row.get("price"))}'></div>
    <div><label>Verification</label><select name=verification><option>{esc(row.get("verification"))}</option><option>VERIFIED</option><option>UNVERIFIED</option><option>NOT AVAILABLE</option></select></div>
    <div style='grid-column:1/-1'><label>Description</label><textarea name=description rows=5>{esc(row.get("description"))}</textarea></div></div><p><button class=green>Save Changes</button></p></form></div>"""
    return HTMLResponse(page(body,"availability"))

async def save_edit(request,record_id):
    form=await request.form();engine=_engine()
    if engine is None:return HTMLResponse("WhatsApp DB unavailable",503)
    _ensure(engine);vals={k:str(form.get(k) or "") for k in ("description","location","transaction","property_type","area","price","verification")}
    with engine.begin() as c:
        c.execute(text("""INSERT INTO alliance_whatsapp_v52_overrides(record_id,description_override,location_override,transaction_override,property_type_override,area_override,price_override,verification_override,deleted,updated_at)
        VALUES(:r,:d,:l,:t,:pt,:a,:p,:v,FALSE,NOW()) ON CONFLICT(record_id) DO UPDATE SET description_override=EXCLUDED.description_override,location_override=EXCLUDED.location_override,
        transaction_override=EXCLUDED.transaction_override,property_type_override=EXCLUDED.property_type_override,area_override=EXCLUDED.area_override,price_override=EXCLUDED.price_override,
        verification_override=EXCLUDED.verification_override,deleted=FALSE,updated_at=NOW()"""),
        {"r":record_id,"d":vals["description"],"l":vals["location"],"t":vals["transaction"],"pt":vals["property_type"],"a":vals["area"],"p":vals["price"],"v":vals["verification"]})
    return RedirectResponse("/whatsapp-live?section=availability",303)

async def soft_delete(record_id):
    engine=_engine()
    if engine is None:return HTMLResponse("WhatsApp DB unavailable",503)
    _ensure(engine)
    with engine.begin() as c:
        c.execute(text("""INSERT INTO alliance_whatsapp_v52_overrides(record_id,deleted,updated_at) VALUES(:r,TRUE,NOW())
        ON CONFLICT(record_id) DO UPDATE SET deleted=TRUE,updated_at=NOW()"""),{"r":record_id})
    return RedirectResponse("/whatsapp-live?section=availability",303)

def raw_audit():
    engine=_engine()
    if engine is None:return HTMLResponse(page("<div class=card>WhatsApp DB unavailable.</div>"),503)
    try:
        with engine.connect() as c:
            rows=c.execute(text("""SELECT e.created_at,e.sender_name,e.sender_phone,e.raw_text,e.classification,e.entity_id,e.status,g.group_name,a.label account_label
              FROM wa_bridge_events e LEFT JOIN wa_bridge_groups g ON g.group_id=e.group_id LEFT JOIN wa_bridge_accounts a ON a.account_id=g.account_id
              ORDER BY e.id DESC LIMIT 500""")).mappings().all()
    except Exception as e:return HTMLResponse(page(f"<div class=card>Raw audit unavailable: {esc(e)}</div>"),500)
    trs="".join(f"<tr><td>{esc(r.get('created_at'))}</td><td>{esc(r.get('account_label'))}</td><td>{esc(r.get('group_name'))}</td><td>{esc(r.get('sender_name'))} {esc(r.get('sender_phone'))}</td><td class=desc>{esc(r.get('raw_text'))}</td><td>{esc(r.get('classification'))}</td><td>{esc(r.get('status'))}</td><td>{esc(r.get('entity_id'))}</td></tr>" for r in rows)
    return HTMLResponse(page(f"<div class=card><h2>Admin Raw WhatsApp Audit</h2></div><div class=scroll><table><tr><th>Received</th><th>Mobile</th><th>Group</th><th>Sender</th><th>Raw Message</th><th>Classification</th><th>Status</th><th>Entity</th></tr>{trs}</table></div>",""))

class V53AuthoritativeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self,request,call_next):
        p=request.url.path.rstrip("/") or "/";method=request.method.upper()
        if method=="GET" and p=="/whatsapp-live":return render_workspace(request)
        if method=="GET" and p=="/whatsapp-live/feed":return RedirectResponse("/whatsapp-live?section=availability",303)
        if method=="GET" and p=="/whatsapp-live/requirements":return RedirectResponse("/whatsapp-live?section=requirements",303)
        if method=="GET" and p=="/whatsapp-live/raw-audit":return raw_audit()
        if method=="GET" and p.startswith("/whatsapp-live/edit/"):return render_edit(p.split("/edit/",1)[1])
        if method=="POST" and p.startswith("/whatsapp-live/edit/"):return await save_edit(request,p.split("/edit/",1)[1])
        if method=="POST" and p.startswith("/whatsapp-live/delete/"):return await soft_delete(p.split("/delete/",1)[1])
        return await call_next(request)

def register(wrapped):
    try:legacy_result=_legacy.register(wrapped)
    except Exception as e:legacy_result={"status":"DEGRADED","error":f"{type(e).__name__}: {e}"}
    app=wrapped.app
    if not getattr(app.state,"alliance_v53_authoritative_middleware",False):
        app.add_middleware(V53AuthoritativeMiddleware);app.state.alliance_v53_authoritative_middleware=True

    @app.get("/api/v54/status")
    def status():
        return {"status":"OK","version":VERSION,"owner":OWNER,"sections":["Availability","Date-wise Requirements"],
          "single_matcher":"/deal-match-ai-v60","local_whatsapp_matcher_removed":True,"single_matcher_button_position":"TOP_ONLY",
          "requirement_date_source":"original wa_messages.message_timestamp, fallback wa_requirements.created_at",
          "requirement_timezone":"Asia/Kolkata","ordinal_date_format":"28th Aug 2026","source_preserved":True,"contact_name_column":True,"contact_number_column":True,"fixed_field_order":True}
    return {"status":"REGISTERED","version":VERSION,"owner":OWNER,"legacy":legacy_result}
