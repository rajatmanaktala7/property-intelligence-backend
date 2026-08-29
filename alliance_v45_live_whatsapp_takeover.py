from __future__ import annotations

import re
from collections import Counter
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute
from sqlalchemy import text
import alliance_auto_updater as updater

VERSION = "4.5.3-SAFE-CLEAN-FEED"

CITY_ONLY = {
    "gurgaon","gurugram","delhi","new delhi","delhi ncr","ncr","noida","greater noida",
    "faridabad","ghaziabad","goa","north goa","south goa","mumbai","bengaluru","bangalore"
}

MICRO_LOCATIONS = [
    "dlf phase 1","dlf phase 2","dlf phase 3","dlf phase 4","dlf phase 5",
    "sushant lok 1","golf course road","golf course extension road","sohna road",
    "mg road","udyog vihar","nirvana country","south city 1","south city 2",
    "vasant kunj","vasant vihar","greater kailash 1","greater kailash 2","gk 1","gk 2",
    "kalkaji","cr park","chittaranjan park","defence colony","south extension",
    "connaught place","cp","panchsheel park","kailash colony","hauz khas","green park",
    "siolim","assagao","anjuna","vagator","morjim","candolim","calangute","porvorim",
    "panaji","panjim","dona paula","miramar","caranzalem","mapusa","margao","saligao","aldona"
]

PROPERTY_HINTS = [
    "bhk","villa","apartment","flat","floor","plot","land","office","shop","showroom",
    "commercial","restaurant","cafe","banquet","hotel","warehouse","godown","farmhouse",
    "sq ft","sqft","sq yd","sqyd","sqm","sq m","acre"
]

def _esc(v):
    s=str(v or "")
    return (s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
             .replace('"',"&quot;").replace("'","&#39;"))

def _norm(v):
    return re.sub(r"\s+"," ",re.sub(r"[^a-z0-9]+"," ",str(v or "").lower())).strip()

def _table_exists(engine,name):
    try:
        with engine.connect() as c:
            return bool(c.execute(text("""
                SELECT 1 FROM information_schema.tables
                WHERE table_schema='public' AND table_name=:n
            """),{"n":name}).first())
    except Exception:
        return False

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

def _money_display(v,tx):
    try:
        n=float(v)
    except Exception:
        return str(v or "—")
    if n <= 0:
        return "—"
    prefix = "Rent " if tx=="RENT" else "Price "
    if n >= 10_000_000:
        return prefix + "₹" + (f"{n/10_000_000:.2f}".rstrip("0").rstrip(".")) + " Cr"
    if n >= 100_000:
        return prefix + "₹" + (f"{n/100_000:.2f}".rstrip("0").rstrip(".")) + " L"
    return prefix + "₹" + f"{n:,.0f}" + ("/month" if tx=="RENT" else "")

def _location_from_blob(blob):
    n=_norm(blob)
    m=re.search(r"\bsector\s*[- ]?\s*(\d{1,3}[a-z]?)\b",n,re.I)
    if m:
        return "Sector " + m.group(1).upper()
    for loc in sorted(MICRO_LOCATIONS,key=len,reverse=True):
        if re.search(rf"(?<![a-z0-9]){re.escape(loc)}(?![a-z0-9])",n):
            return " ".join(x.capitalize() if x not in {"dlf","gk","cp","mg"} else x.upper()
                            for x in loc.split())
    return None

def _quality(row, duplicate_count):
    lead=_norm(row.get("lead_type"))
    desc=str(row.get("description") or "")
    cfg=str(row.get("configuration_details") or "")
    blob=f"{desc} {cfg}"
    n=_norm(blob)
    tx="SALE" if "sale" in lead else ("RENT" if any(x in lead for x in ["rent","lease"]) else "UNKNOWN")
    loc=_location_from_blob(blob)

    if any(x in n for x in ["requirement","wanted","looking for","need property","news","rejected","spam"]):
        return "NOISE","Requirement/noise message"
    if tx=="UNKNOWN":
        return "NEEDS_REVIEW","Transaction is unclear"
    if len(n) < 12 or not any(h in n for h in PROPERTY_HINTS):
        return "NOISE","Not enough property meaning"
    if duplicate_count > 1:
        return "NEEDS_REVIEW","Same WhatsApp source produced conflicting/fragmented property rows"
    if not loc:
        return "NEEDS_REVIEW","Micro-location/project is missing; city-only property is hidden"

    try:
        price=float(row.get("price")) if row.get("price") not in (None,"") else None
    except Exception:
        price=None
    if tx=="RENT" and price is not None and (price < 1000 or price > 5_000_000):
        return "NEEDS_REVIEW","Rent amount/unit is ambiguous"
    if tx=="SALE" and price is not None and 0 < price < 1_000_000:
        return "NEEDS_REVIEW","Sale price/unit is ambiguous"

    try:
        area=float(row.get("area")) if row.get("area") not in (None,"") else None
    except Exception:
        area=None
    if area is not None and area <= 0:
        return "NEEDS_REVIEW","Area is zero/invalid"

    return "READY",""

def _load_master(engine,q="",limit=3000):
    if not _table_exists(engine,"pi_whatsapp_property_master"):
        return []
    gen=_latest_generation(engine)
    if not gen:
        return []
    with engine.connect() as c:
        rows=c.execute(text("""
            SELECT id,record_id,lead_type,description,area,configuration_details,price,
                   contact_name_number,source,captured_on,verification,source_count
            FROM pi_whatsapp_property_master
            WHERE generation_id=:g
            ORDER BY id DESC
            LIMIT :lim
        """),{"g":gen,"lim":limit}).mappings().all()
    items=[dict(r) for r in rows]
    counts=Counter(str(r.get("record_id") or "") for r in items if r.get("record_id"))
    ready=[]
    for r in items:
        rid=str(r.get("record_id") or "")
        quality,reason=_quality(r,counts.get(rid,1))
        r["data_quality"]=quality
        r["quality_reason"]=reason
        r["micro_location"]=_location_from_blob(f"{r.get('description') or ''} {r.get('configuration_details') or ''}")
        lead=_norm(r.get("lead_type"))
        r["transaction"]="SALE" if "sale" in lead else ("RENT" if any(x in lead for x in ["rent","lease"]) else "UNKNOWN")
        r["price_display"]=_money_display(r.get("price"),r["transaction"])
        r["availability_from"]="Not Mentioned"
        if quality=="READY":
            if q:
                hay=_norm(" ".join(str(r.get(k) or "") for k in [
                    "description","configuration_details","contact_name_number","source","micro_location"
                ]))
                if _norm(q) not in hay:
                    continue
            ready.append(r)
    return ready[:1000]

def _stats(engine):
    rows=_load_master(engine,"",3000)
    verified=sum(1 for r in rows if "VERIFIED" in str(r.get("verification") or "").upper())
    latest=max((r.get("captured_on") for r in rows if r.get("captured_on")),default=None)
    return {"count":len(rows),"verified":verified,"latest":latest}

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
    .green{{background:#377a4b}}.muted{{color:#7a6b5c}}.desc{{min-width:360px;max-width:620px;line-height:1.4}}
    .ok{{color:#176b3a;font-weight:800}}
    </style></head><body><header><h2 style='margin:0'>WhatsApp Live Property Intelligence</h2>
    <small>READY properties only. Review/noise stays hidden from team availability.</small></header>
    <nav><a href='/team-dashboard-v376'>← Dashboard</a><a href='/workspace'>Working Space</a>
    <a href='/whatsapp-live'>Live Dashboard</a><a href='/whatsapp-live/feed'>Live Property Feed</a>
    <a href='/whatsapp-property-master-v44'>Source Master</a><a href='/whatsapp-live/raw-feed-v45'>Raw Audit Feed</a></nav>
    <main>{body}</main></body></html>"""

def register(core):
    app=core.app
    engine=core.engine
    router=APIRouter()

    kept=[]
    for route in app.router.routes:
        path=getattr(route,"path",None)
        methods=getattr(route,"methods",set()) or set()
        if isinstance(route,APIRoute) and path in {
            "/whatsapp-live","/whatsapp-live/feed","/api/v451/live/status","/api/v451/live/properties"
        } and "GET" in methods:
            continue
        kept.append(route)
    app.router.routes[:]=kept

    @router.get("/api/v451/live/status")
    def status():
        updater.request_refresh(force=False)
        return {"version":VERSION,"status":"OK","clean_feed":_stats(engine),
                "policy":"READY_ONLY_REVIEW_HIDDEN","source_data_mutation":False}

    @router.get("/api/v451/live/properties")
    def properties(q:str="",limit:int=800):
        updater.request_refresh(force=False)
        rows=_load_master(engine,q,min(max(limit,1),1500))
        out=[]
        for r in rows:
            d=dict(r)
            for k,v in list(d.items()):
                if hasattr(v,"isoformat"): d[k]=v.isoformat()
            out.append(d)
        return {"status":"OK","version":VERSION,"count":len(out),"rows":out}

    @router.get("/whatsapp-live",response_class=HTMLResponse)
    def dashboard():
        updater.request_refresh(force=False)
        raw=_raw_stats(); st=_stats(engine)
        body=f"""<h2>Live Intake Command Centre</h2><div class=grid>
        <div class=card>Active Mobile Numbers<h2>{raw['accounts']}</h2></div>
        <div class=card>Active Groups<h2>{raw['groups']}</h2></div>
        <div class=card>Raw Messages Today<h2>{raw['today']}</h2></div>
        <div class=card>Clean READY Properties<h2>{st['count']}</h2></div>
        <div class=card>Verified READY Properties<h2>{st['verified']}</h2></div></div>
        <div class=card><b>Rule:</b> NEEDS_REVIEW and NOISE are intentionally hidden from Property Availability.
        They remain in source/raw storage for AI matching and audit.</div>
        <div class=card><a class='btn green' href='/whatsapp-live/feed'>Open Clean READY Feed</a>
        <a class=btn href='/whatsapp-property-master-v44'>Open Source Master</a></div>"""
        return HTMLResponse(_page("WhatsApp Live Dashboard",body))

    @router.get("/whatsapp-live/feed",response_class=HTMLResponse)
    def feed(request:Request):
        updater.request_refresh(force=False)
        q=str(request.query_params.get("q") or "").strip()
        rows=_load_master(engine,q,3000)
        trs="".join(f"""<tr>
        <td>{_esc(r.get('transaction'))}</td>
        <td class='desc'><b>{_esc(r.get('description'))}</b></td>
        <td><b>{_esc(r.get('micro_location'))}</b></td>
        <td>{_esc(r.get('area') or '—')}</td>
        <td><b>{_esc(r.get('price_display'))}</b></td>
        <td>{_esc(r.get('availability_from'))}</td>
        <td>{_esc(r.get('contact_name_number') or '—')}</td>
        <td>{_esc(r.get('source') or '—')}</td>
        <td>{_esc(r.get('captured_on') or '—')}</td>
        <td>{_esc(r.get('verification') or 'UNVERIFIED')}</td></tr>""" for r in rows)
        body=f"""<h2>WhatsApp Group Availability</h2>
        <div class=card><form method=get style='display:grid;grid-template-columns:1fr auto;gap:8px'>
        <input name=q value='{_esc(q)}' placeholder='Search location, project, property, contact or source'>
        <button>Search</button></form>
        <p class=muted><b>{len(rows)}</b> READY properties shown. NEEDS_REVIEW, city-only,
        conflicting fragments and NOISE are hidden from this page.</p></div>
        <div class=card style='overflow:auto'><table>
        <tr><th>Sale/Rent</th><th>Property</th><th>Micro-location / Project</th><th>Area</th>
        <th>Price / Rent</th><th>Available From</th><th>Internal Contact</th><th>Source Group</th>
        <th>Message Date</th><th>Verification</th></tr>
        {trs or '<tr><td colspan=10>No READY WhatsApp properties found.</td></tr>'}</table></div>"""
        return HTMLResponse(_page("WhatsApp Group Availability",body))

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
        trs="".join(f"""<tr><td>{_esc(r.get('created_at'))}</td><td>{_esc(r.get('group_name'))}</td>
        <td>{_esc(r.get('sender_name') or r.get('sender_phone'))}</td>
        <td style='max-width:650px;white-space:pre-wrap'>{_esc(r.get('raw_text'))}</td>
        <td>{_esc(r.get('classification') or r.get('status'))}</td></tr>""" for r in rows)
        return HTMLResponse(_page("Raw WhatsApp Audit Feed",
          f"""<h2>Raw WhatsApp Audit Feed</h2><div class=card>
          <p class=muted>Raw/source messages remain available for audit and second-preference AI matching.</p>
          <table><tr><th>Received</th><th>Group</th><th>Sender</th><th>Raw Message</th><th>Result</th></tr>{trs}</table></div>"""))

    app.include_router(router)
    return router
