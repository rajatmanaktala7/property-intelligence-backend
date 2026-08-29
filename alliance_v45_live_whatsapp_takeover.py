from __future__ import annotations

import re
from datetime import datetime
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute
from sqlalchemy import text
import alliance_auto_updater as updater

VERSION = "4.5.2-DATA-QUALITY-AVAILABILITY"

CITY_ONLY = {
    "gurgaon","gurugram","delhi","new delhi","delhi ncr","ncr","noida","greater noida",
    "faridabad","ghaziabad","goa","north goa","south goa","mumbai","bombay","bengaluru","bangalore"
}

def _esc(v):
    s = str(v or "")
    return (s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
             .replace('"',"&quot;").replace("'","&#39;"))

def _norm(v):
    return re.sub(r"\s+"," ",str(v or "").strip().lower())

def _money_display(v, tx=None):
    if v in (None,""):
        return "—"
    try:
        n=float(v)
    except Exception:
        return str(v)
    if n>=10_000_000:
        x=n/10_000_000
        return f"₹{x:g} Cr"
    if n>=100_000:
        x=n/100_000
        return f"₹{x:g} L"
    return "₹{:,.0f}".format(n)

def _meaningful_location(value):
    n=_norm(value)
    return bool(n and n not in CITY_ONLY and n not in {"unknown","na","n/a","not specified","-"})

def _infer_city(text_value):
    t=_norm(text_value)
    if "gurgaon" in t or "gurugram" in t: return "Gurugram"
    if "greater noida" in t: return "Greater Noida"
    if "noida" in t: return "Noida"
    if "faridabad" in t: return "Faridabad"
    if "ghaziabad" in t: return "Ghaziabad"
    if "goa" in t: return "Goa"
    if "delhi" in t: return "New Delhi"
    return None

def _infer_type(blob):
    t=_norm(blob)
    rules=[
        ("Villa",["villa","independent house","bungalow"]),
        ("Residential",["bhk","apartment","flat","builder floor","residential"]),
        ("Land / Plot",["plot","land"]),
        ("Restaurant",["restaurant","restro"]),
        ("Cafe",["cafe","coffee shop"]),
        ("Banquet",["banquet","marriage hall","wedding venue"]),
        ("Hotel / Hospitality",["hotel","resort","guest house","guesthouse"]),
        ("Warehouse / Industrial",["warehouse","godown","industrial","factory"]),
        ("Office",["office","workspace"]),
        ("Commercial Shop",["shop","showroom","retail"]),
    ]
    for label,terms in rules:
        if any(x in t for x in terms):
            return label
    return "UNKNOWN"

def _infer_tx(blob):
    t=_norm(blob)
    sale=any(x in t for x in ["for sale","sale","selling","sell","resale","outright"])
    rent=any(x in t for x in ["for rent","on rent","rent","lease","leasing","to let"])
    if sale and not rent: return "SALE"
    if rent and not sale: return "RENT"
    return "UNKNOWN"

def _quality(row):
    tx=str(row.get("lead_type") or "").upper()
    loc=row.get("canonical_location")
    project=row.get("building_project")
    ptype=str(row.get("property_type") or "").upper()
    desc=str(row.get("description") or "")
    area=row.get("area")
    price=row.get("price_value")
    contact=row.get("contact_name_number")
    meaningful_loc=_meaningful_location(loc) or _meaningful_location(project)
    attrs=sum(bool(x) for x in [
        ptype and ptype!="UNKNOWN",
        area,
        price,
        re.search(r"\b\d+\s*bhk\b",desc,re.I),
        re.search(r"\bfloor\b",desc,re.I),
        contact,
    ])
    if len(_norm(desc))<8 and attrs<2:
        return "NOISE"
    if tx=="UNKNOWN":
        return "NEEDS_REVIEW"
    if not meaningful_loc:
        return "NEEDS_REVIEW"
    if attrs<2:
        return "NEEDS_REVIEW"
    return "READY"

def _latest_generation(engine):
    try:
        with engine.connect() as c:
            return c.execute(text("""
              SELECT generation_id FROM pi_whatsapp_property_master_generation
              WHERE status='COMPLETED'
              ORDER BY completed_at DESC NULLS LAST,id DESC LIMIT 1
            """)).scalar()
    except Exception:
        return None

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

def _canonical_available(engine):
    try:
        with engine.connect() as c:
            return bool(c.execute(text("SELECT to_regclass('public.alliance_canonical_properties') IS NOT NULL")).scalar())
    except Exception:
        return False

def _best_location(c, text_value):
    low=_norm(text_value)
    try:
        aliases=c.execute(text("""
            SELECT alias_text,canonical_location
            FROM alliance_location_aliases
            WHERE approved=TRUE
            ORDER BY length(alias_text) DESC
        """)).mappings().all()
    except Exception:
        aliases=[]
    for a in aliases:
        alias=_norm(a.get("alias_text"))
        canonical=str(a.get("canonical_location") or "").strip()
        if alias and alias in low and _meaningful_location(canonical):
            return canonical
    sec=re.search(r"\b(?:sector|sec)\s*[- ]?(\d{1,3}[a-z]?)\b",low,re.I)
    if sec:
        city=_infer_city(low)
        return ((city+" ") if city in {"Gurugram","Noida","Greater Noida"} else "")+"Sector "+sec.group(1).upper()
    return None

def _sync_latest_whatsapp_to_canonical(engine, limit=4000):
    if not _canonical_available(engine):
        return {"status":"NO_CANONICAL_DB","processed":0}
    try:
        import alliance_v383_database_foundation as canon
    except Exception as exc:
        return {"status":"CANONICAL_MODULE_UNAVAILABLE","processed":0,"error":str(exc)}
    gen=_latest_generation(engine)
    if not gen:
        return {"status":"NO_GENERATION","processed":0}
    try:
        with engine.connect() as c:
            rows=c.execute(text("""
              SELECT id,record_id,lead_type,description,area,configuration_details,price,
                     contact_name_number,source,captured_on,verification,source_count
              FROM pi_whatsapp_property_master
              WHERE generation_id=:g
              ORDER BY id DESC LIMIT :lim
            """),{"g":gen,"lim":limit}).mappings().all()

        processed=0
        with engine.begin() as c:
            canon._ensure_schema(engine)
            for r in rows:
                d=dict(r)
                desc=str(d.get("description") or "")
                config=str(d.get("configuration_details") or "")
                blob=" ".join([str(d.get("lead_type") or ""),desc,config])
                up=blob.upper()
                if any(x in up for x in ["REQUIREMENT","WANTED","LOOKING FOR","NEED PROPERTY","SPAM","NEWS","ADVERTISEMENT ONLY"]):
                    continue

                tx=_infer_tx(blob)
                ptype=_infer_type(blob)
                location=_best_location(c,blob)
                city=_infer_city(blob)
                # Never turn city-only text into a valid micro-location.
                canonical_location=location or "UNKNOWN"
                area=canon._num(d.get("area"))

                property_code=canon._upsert_property(c,{
                    "property_name":desc[:220] or d.get("record_id"),
                    "location":canonical_location,
                    "city":city or "UNKNOWN",
                    "building_project":config[:220] if _meaningful_location(config) else None,
                    "property_type":ptype,
                    "transaction_type":tx,
                    "area_sqft":area,
                    "floor":None,
                    "intended_use_tags":ptype if ptype!="UNKNOWN" else None,
                })

                listing_code=canon._upsert_listing(c,property_code,{
                    "source_type":"WHATSAPP",
                    "source_table":"pi_whatsapp_property_master",
                    "source_record_id":str(d.get("record_id") or d.get("id")),
                    "source_name":d.get("source") or "WhatsApp",
                    "raw_text":desc,
                    "rent_inr":d.get("price") if tx=="RENT" else None,
                    "sale_price_inr":d.get("price") if tx=="SALE" else None,
                    "availability_status":"UNKNOWN",
                    "verification_status":d.get("verification") or "UNVERIFIED",
                    "verification_confidence":100 if str(d.get("verification") or "").upper()=="VERIFIED" else 0,
                    "captured_at":d.get("captured_on"),
                })
                cn=str(d.get("contact_name_number") or "")
                canon._upsert_contact(c,listing_code,cn,cn,"BROKER",True)
                processed+=1
        return {"status":"OK","processed":processed,"generation":str(gen)}
    except Exception as exc:
        return {"status":"DEGRADED","processed":0,"error":f"{type(exc).__name__}: {exc}"}

def _canonical_rows(engine,q="",limit=1000):
    if not _canonical_available(engine):
        return []
    p={"lim":limit}
    where=["p.active=TRUE","l.active=TRUE","UPPER(l.source_type)='WHATSAPP'"]
    if q.strip():
        where.append("""(
          COALESCE(p.property_name,'') ILIKE :q OR
          COALESCE(p.canonical_location,'') ILIKE :q OR
          COALESCE(p.building_project,'') ILIKE :q OR
          COALESCE(p.property_type,'') ILIKE :q OR
          COALESCE(l.raw_text,'') ILIKE :q OR
          COALESCE(l.source_name,'') ILIKE :q OR
          COALESCE(c.display_name,'') ILIKE :q OR
          COALESCE(c.normalized_phone,'') ILIKE :q
        )""")
        p["q"]="%"+q.strip()+"%"
    with engine.connect() as c:
        rows=c.execute(text(f"""
          SELECT
            p.property_code AS record_id,
            p.transaction_type AS lead_type,
            COALESCE(NULLIF(p.property_name,''),p.building_project,p.canonical_location) AS description,
            p.city,
            p.canonical_location,
            p.building_project,
            p.property_type,
            p.area_sqft AS area,
            MAX(CASE WHEN p.transaction_type='RENT' THEN l.asking_rent_inr ELSE l.asking_sale_price_inr END) AS price_value,
            STRING_AGG(DISTINCT NULLIF(CONCAT_WS(' · ',c.display_name,c.normalized_phone),''),' | ') AS contact_name_number,
            STRING_AGG(DISTINCT NULLIF(l.source_name,''),' | ') AS source,
            STRING_AGG(DISTINCT NULLIF(l.source_record_id,''),' | ') AS source_record_ids,
            CASE WHEN BOOL_OR(UPPER(COALESCE(l.verification_status,''))='VERIFIED') THEN 'VERIFIED' ELSE 'UNVERIFIED' END AS verification,
            COUNT(DISTINCT l.listing_code) AS source_count,
            MAX(l.captured_at) AS captured_on
          FROM alliance_canonical_properties p
          JOIN alliance_property_listings l ON l.property_code=p.property_code
          LEFT JOIN alliance_listing_contacts lc ON lc.listing_code=l.listing_code
          LEFT JOIN alliance_contacts c ON c.contact_code=lc.contact_code
          WHERE {" AND ".join(where)}
          GROUP BY p.property_code,p.transaction_type,p.property_name,p.city,p.canonical_location,
                   p.building_project,p.property_type,p.area_sqft
          ORDER BY MAX(l.captured_at) DESC NULLS LAST,p.property_code DESC
          LIMIT :lim
        """),p).mappings().all()

    out=[]
    seen_source=set()
    seen_fp=set()
    for raw in rows:
        d=dict(raw)
        d["quality"]=_quality(d)
        if d["quality"]=="NOISE":
            continue
        source_ids=[x.strip() for x in str(d.get("source_record_ids") or "").split("|") if x.strip()]
        if source_ids and any(x in seen_source for x in source_ids):
            continue
        fp="|".join([
            _norm(d.get("lead_type")),
            _norm(d.get("canonical_location")),
            _norm(d.get("building_project")),
            _norm(d.get("property_type")),
            str(round(float(d.get("area") or 0),-2) if d.get("area") else 0),
            str(round(float(d.get("price_value") or 0),-4) if d.get("price_value") else 0),
            re.sub(r"\D","",str(d.get("contact_name_number") or ""))[-10:],
        ])
        if fp in seen_fp:
            continue
        seen_fp.add(fp)
        seen_source.update(source_ids)
        d["price_display"]=_money_display(d.get("price_value"),d.get("lead_type"))
        d["available_from"]="Not Mentioned"
        d["message_date"]=d.get("captured_on")
        d["edit_source_id"]=source_ids[0] if len(source_ids)==1 else None
        out.append(d)
    return out

def _stats(engine):
    rows=_canonical_rows(engine,"",1500) if _canonical_available(engine) else []
    return {
        "count":len(rows),
        "ready":sum(1 for x in rows if x.get("quality")=="READY"),
        "review":sum(1 for x in rows if x.get("quality")=="NEEDS_REVIEW"),
        "verified":sum(1 for x in rows if x.get("verification")=="VERIFIED"),
        "latest":max([x.get("captured_on") for x in rows if x.get("captured_on")], default=None)
    }

def _page(title,body):
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{_esc(title)}</title><style>
*{{box-sizing:border-box}}body{{margin:0;font-family:Arial;background:#efe4d2;color:#2d261f}}
header{{background:#5d4937;color:#fff;padding:18px 24px}}nav{{background:#fffdf9;padding:10px 18px;border-bottom:1px solid #dccdbb;display:flex;gap:8px;flex-wrap:wrap}}
nav a{{text-decoration:none;color:#4d3d30;padding:8px 10px;border-radius:7px;font-weight:700}}main{{max-width:1800px;margin:auto;padding:18px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}}.card{{background:#fffdf9;border:1px solid #dccdbb;border-radius:12px;padding:14px;margin-bottom:12px}}
table{{width:100%;border-collapse:collapse;background:#fff}}th,td{{padding:9px;border-bottom:1px solid #eee0ce;text-align:left;vertical-align:top;font-size:12px}}th{{background:#f7ecdf;position:sticky;top:0}}
input,select{{padding:10px;border:1px solid #d0c1af;border-radius:7px;width:100%}}button,.btn{{border:0;background:#6c543f;color:#fff;padding:9px 12px;border-radius:7px;text-decoration:none;cursor:pointer;font-weight:800}}
.green{{background:#377a4b}}.orange{{background:#a45d12}}.muted{{color:#7a6b5c}}.desc{{min-width:260px;max-width:500px;line-height:1.4}}.ok{{color:#176b3a;font-weight:800}}.warn{{color:#a45d12;font-weight:800}}
</style></head><body><header><h2 style='margin:0'>WhatsApp Live Property Intelligence</h2>
<small>Grouped property entities · city-only data blocked · readable price · availability dates</small></header>
<nav><a href='/team-dashboard-v376'>← Dashboard</a><a href='/workspace'>Working Space</a><a href='/whatsapp-live'>Live Dashboard</a>
<a href='/whatsapp-live/feed'>Clean Property Feed</a><a href='/whatsapp-live/feed?view=review'>Needs Review</a>
<a href='/whatsapp-property-master-v44'>Source Master</a><a href='/whatsapp-live/sources'>WhatsApp Sources</a>
<a href='/whatsapp-live/requirements'>Requirements</a><a href='/whatsapp-live/raw-feed-v45'>Raw Audit Feed</a></nav><main>{body}</main></body></html>"""

def register(core):
    app=core.app
    engine=core.engine
    router=APIRouter()

    kept=[]
    for route in app.router.routes:
        path=getattr(route,"path",None)
        methods=getattr(route,"methods",set()) or set()
        if isinstance(route,APIRoute) and path in {"/whatsapp-live","/whatsapp-live/feed","/api/v451/live/status","/api/v451/live/properties","/whatsapp-live/raw-feed-v45"} and "GET" in methods:
            continue
        kept.append(route)
    app.router.routes[:]=kept

    @router.get("/api/v451/live/status")
    def status():
        updater.request_refresh(force=False)
        sync=_sync_latest_whatsapp_to_canonical(engine,4000)
        return {"version":VERSION,"status":"OK","canonical":_stats(engine),"raw":_raw_stats(),"sync":sync,"auto_updater":updater.STATE}

    @router.get("/api/v451/live/properties")
    def properties(q:str="",limit:int=800,view:str="ready"):
        updater.request_refresh(force=False)
        sync=_sync_latest_whatsapp_to_canonical(engine,4000)
        rows=_canonical_rows(engine,q,min(max(limit,1),1500))
        if view.lower()=="ready":
            rows=[x for x in rows if x.get("quality")=="READY"]
        elif view.lower()=="review":
            rows=[x for x in rows if x.get("quality")=="NEEDS_REVIEW"]
        for d in rows:
            for k,v in list(d.items()):
                if hasattr(v,"isoformat"): d[k]=v.isoformat()
        return {"status":"OK","version":VERSION,"count":len(rows),"sync":sync,"rows":rows}

    @router.get("/whatsapp-live",response_class=HTMLResponse)
    def dashboard():
        updater.request_refresh(force=False)
        sync=_sync_latest_whatsapp_to_canonical(engine,4000)
        raw=_raw_stats(); st=_stats(engine)
        body=f"""<h2>Live Intake Command Centre</h2><div class=grid>
        <div class=card>Active Mobile Numbers<h2>{raw['accounts']}</h2></div>
        <div class=card>Active Groups<h2>{raw['groups']}</h2></div>
        <div class=card>Raw Messages Today<h2>{raw['today']}</h2></div>
        <div class=card>Clean Ready Properties<h2>{st['ready']}</h2></div>
        <div class=card>Needs Review<h2>{st['review']}</h2></div>
        <div class=card>Verified<h2>{st['verified']}</h2></div></div>
        <div class=card><b>Rule:</b> Gurugram/Goa/Delhi/Noida alone are city names, not usable micro-locations.
        Fragment/noise rows are hidden from the clean feed. Sale is never converted to Rent.</div>
        <div class=card><b>Latest raw WhatsApp:</b> {_esc(raw['latest'] or '—')}<br>
        <b>Latest canonical listing:</b> {_esc(st['latest'] or '—')}<br>
        <b>Canonical bridge:</b> <span class=ok>{_esc(sync.get('status'))}</span> · processed {_esc(sync.get('processed',0))}</div>
        <div class=card><a class='btn green' href='/whatsapp-live/feed'>Open Clean Ready Feed</a>
        <a class='btn orange' href='/whatsapp-live/feed?view=review'>Open Needs Review</a></div>"""
        return HTMLResponse(_page("WhatsApp Live Dashboard",body))

    @router.get("/whatsapp-live/feed",response_class=HTMLResponse)
    def feed(request:Request):
        updater.request_refresh(force=False)
        sync=_sync_latest_whatsapp_to_canonical(engine,4000)
        q=str(request.query_params.get("q") or "").strip()
        view=str(request.query_params.get("view") or "ready").lower()
        rows=_canonical_rows(engine,q,1200)
        if view=="review":
            rows=[x for x in rows if x.get("quality")=="NEEDS_REVIEW"]
            heading="WhatsApp Properties Needing Review"
        elif view=="all":
            heading="All Non-Noise WhatsApp Properties"
        else:
            rows=[x for x in rows if x.get("quality")=="READY"]
            heading="Clean WhatsApp Property Availability"

        trs=[]
        for r in rows:
            loc=r.get("canonical_location")
            if not _meaningful_location(loc):
                loc="Micro-location missing"
            project=r.get("building_project") or ""
            edit=(f"<a class='btn' href='/whatsapp-live/edit/{_esc(r['edit_source_id'])}'>Edit</a>" if r.get("edit_source_id") else "Review source")
            quality_cls="ok" if r.get("quality")=="READY" else "warn"
            trs.append(f"""<tr>
            <td class='{quality_cls}'>{_esc(r.get('quality'))}</td>
            <td><b>{_esc(r.get('lead_type') or 'UNKNOWN')}</b></td>
            <td class='desc'><b>{_esc(r.get('description'))}</b></td>
            <td>{_esc(r.get('city') or '—')}</td>
            <td>{_esc(loc)}{('<br>'+_esc(project)) if project else ''}</td>
            <td>{_esc(r.get('property_type') or '—')}</td>
            <td>{_esc(r.get('area') or '—')}</td>
            <td><b>{_esc(r.get('price_display'))}</b></td>
            <td>{_esc(r.get('available_from'))}</td>
            <td>{_esc(r.get('contact_name_number') or '—')}</td>
            <td>{_esc(r.get('source') or '—')}</td>
            <td>{_esc(r.get('message_date') or '—')}</td>
            <td>{_esc(r.get('verification'))}</td>
            <td>{edit}</td></tr>""")
        body=f"""<h2>{_esc(heading)}</h2>
        <div class=card><form method=get style='display:grid;grid-template-columns:1fr 180px auto;gap:8px'>
        <input name=q value='{_esc(q)}' placeholder='Search project, micro-location, contact, property or source group'>
        <select name=view><option value='ready' {'selected' if view=='ready' else ''}>Ready</option><option value='review' {'selected' if view=='review' else ''}>Needs Review</option><option value='all' {'selected' if view=='all' else ''}>All non-noise</option></select>
        <button>Apply</button></form>
        <p class=muted>{len(rows)} property entities shown · bridge {_esc(sync.get('status'))}. Message Date is not treated as Availability Date.</p></div>
        <div class=card style='overflow:auto'><table>
        <tr><th>Quality</th><th>Transaction</th><th>Property</th><th>City</th><th>Micro-location / Project</th><th>Type</th>
        <th>Area (normalized sqft)</th><th>Price / Rent</th><th>Available From</th><th>Internal Contact</th><th>Source Group</th><th>Message Date</th><th>Verification</th><th>Action</th></tr>
        {''.join(trs) or '<tr><td colspan=14>No properties in this view.</td></tr>'}</table></div>"""
        return HTMLResponse(_page("WhatsApp Property Availability",body))

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
          <p class=muted>Original messages remain here for audit. Use Clean Property Feed for team work.</p>
          <table><tr><th>Received</th><th>Group</th><th>Sender</th><th>Raw Message</th><th>Result</th></tr>{trs}</table></div>"""))

    app.include_router(router)
    return router
