from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute
from sqlalchemy import text
import alliance_auto_updater as updater

VERSION = "4.5.1-CANONICAL-LIVE-FEED-FIX"

def _esc(v):
    s = str(v or "")
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

def _sync_latest_whatsapp_to_canonical(engine, limit=4000):
    """
    Best-effort bridge from the latest V4.4 WhatsApp master generation into V3.8.3.
    It never alters the source/master tables. Failures do not break the live page.
    """
    if not _canonical_available(engine):
        return {"status":"NO_CANONICAL_DB","processed":0}

    try:
        import alliance_v383_database_foundation as canon
    except Exception as exc:
        return {"status":"CANONICAL_MODULE_UNAVAILABLE","processed":0,"error":str(exc)}

    gen = _latest_generation(engine)
    if not gen:
        return {"status":"NO_GENERATION","processed":0}

    try:
        with engine.connect() as c:
            rows = c.execute(text("""
              SELECT id,record_id,lead_type,description,area,configuration_details,price,
                     contact_name_number,source,captured_on,verification,source_count
              FROM pi_whatsapp_property_master
              WHERE generation_id=:g
              ORDER BY id DESC
              LIMIT :lim
            """), {"g":gen,"lim":limit}).mappings().all()

        processed = 0
        with engine.begin() as c:
            canon._ensure_schema(engine)
            for r in rows:
                d = dict(r)
                lead = str(d.get("lead_type") or "").upper()
                desc = str(d.get("description") or "")
                config = str(d.get("configuration_details") or "")

                # Do not promote requirement/noise/rejected records into property inventory.
                blob = (lead + " " + desc + " " + config).upper()
                if any(x in blob for x in ["REQUIREMENT", "WANTED", "LOOKING FOR", "NEED PROPERTY"]):
                    continue
                if any(x in blob for x in ["REJECTED", "SPAM", "NEWS", "ADVERTISEMENT ONLY"]):
                    continue

                tx = "SALE" if "SALE" in lead or "SALE" in blob else ("RENT" if any(x in blob for x in ["RENT","LEASE","TO LET"]) else "UNKNOWN")
                ptype = "Restaurant" if "RESTAURANT" in blob else (
                    "Cafe" if "CAFE" in blob else (
                    "Banquet" if "BANQUET" in blob else (
                    "Warehouse / Industrial" if any(x in blob for x in ["WAREHOUSE","GODOWN","INDUSTRIAL"]) else (
                    "Office" if "OFFICE" in blob else (
                    "Commercial Shop" if any(x in blob for x in ["SHOP","SHOWROOM","RETAIL"]) else "Commercial Space"
                )))))

                # Location is deliberately conservative: V3.8.3 alias resolver canonicalizes known names.
                location = ""
                low = desc.lower()
                aliases = c.execute(text("""
                    SELECT alias_text,canonical_location
                    FROM alliance_location_aliases
                    WHERE approved=TRUE
                    ORDER BY length(alias_text) DESC
                """)).mappings().all()
                for a in aliases:
                    if a["alias_text"] and a["alias_text"].lower() in low:
                        location = a["canonical_location"]
                        break

                # Fallback to description to avoid fake location guesses.
                if not location:
                    location = desc[:180] or "UNKNOWN"

                area = canon._num(d.get("area"))
                property_code = canon._upsert_property(c,{
                    "property_name":desc[:220] or d.get("record_id"),
                    "location":location,
                    "city":"Delhi NCR",
                    "building_project":config[:220],
                    "property_type":ptype,
                    "transaction_type":tx,
                    "area_sqft":area,
                    "floor":None,
                    "intended_use_tags":ptype,
                })

                listing_code = canon._upsert_listing(c,property_code,{
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

                cn = str(d.get("contact_name_number") or "")
                canon._upsert_contact(c,listing_code,cn,cn,"BROKER",True)
                processed += 1

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
            p.area_sqft AS area,
            CONCAT_WS(' · ',p.property_type,p.building_project,p.canonical_location) AS configuration_details,
            CASE
              WHEN MAX(l.asking_rent_inr) IS NOT NULL THEN '₹' || TO_CHAR(MAX(l.asking_rent_inr),'FM999,999,999,999')
              WHEN MAX(l.asking_sale_price_inr) IS NOT NULL THEN '₹' || TO_CHAR(MAX(l.asking_sale_price_inr),'FM999,999,999,999')
              ELSE NULL
            END AS price,
            STRING_AGG(DISTINCT NULLIF(CONCAT_WS(' · ',c.display_name,c.normalized_phone),''),' | ') AS contact_name_number,
            STRING_AGG(DISTINCT NULLIF(l.source_name,''),' | ') AS source,
            CASE
              WHEN BOOL_OR(UPPER(COALESCE(l.verification_status,''))='VERIFIED') THEN 'VERIFIED'
              ELSE 'UNVERIFIED'
            END AS verification,
            COUNT(DISTINCT l.listing_code) AS source_count,
            MAX(l.captured_at) AS captured_on
          FROM alliance_canonical_properties p
          JOIN alliance_property_listings l ON l.property_code=p.property_code
          LEFT JOIN alliance_listing_contacts lc ON lc.listing_code=l.listing_code
          LEFT JOIN alliance_contacts c ON c.contact_code=lc.contact_code
          WHERE {" AND ".join(where)}
          GROUP BY p.property_code,p.transaction_type,p.property_name,p.area_sqft,
                   p.property_type,p.building_project,p.canonical_location
          ORDER BY MAX(l.captured_at) DESC NULLS LAST,p.property_code DESC
          LIMIT :lim
        """),p).mappings().all()
    return rows

def _stats(engine):
    if not _canonical_available(engine):
        return {"count":0,"latest":None,"verified":0}
    with engine.connect() as c:
        r=c.execute(text("""
          SELECT COUNT(DISTINCT p.property_code) property_count,
                 MAX(l.captured_at) latest,
                 COUNT(DISTINCT p.property_code) FILTER (
                    WHERE UPPER(COALESCE(l.verification_status,''))='VERIFIED'
                 ) verified_count
          FROM alliance_canonical_properties p
          JOIN alliance_property_listings l ON l.property_code=p.property_code
          WHERE p.active=TRUE AND l.active=TRUE AND UPPER(l.source_type)='WHATSAPP'
        """)).mappings().first()
    return {"count":int((r or {}).get("property_count") or 0),
            "latest":(r or {}).get("latest"),
            "verified":int((r or {}).get("verified_count") or 0)}

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
    .green{{background:#377a4b}}.muted{{color:#7a6b5c}}.desc{{min-width:380px;max-width:620px;line-height:1.4}}
    .ok{{color:#176b3a;font-weight:800}}
    </style></head><body><header><h2 style='margin:0'>WhatsApp Live Property Intelligence</h2>
    <small>Live intake → canonical Property → Listings → Contacts → clean searchable feed</small></header>
    <nav><a href='/team-dashboard-v376'>← Dashboard</a><a href='/workspace'>Working Space</a>
    <a href='/whatsapp-live'>Live Dashboard</a><a href='/whatsapp-live/feed'>Live Property Feed</a>
    <a href='/whatsapp-property-master-v44'>Source Master</a><a href='/whatsapp-live/sources'>WhatsApp Sources</a>
    <a href='/whatsapp-live/requirements'>Requirements</a><a href='/whatsapp-live/raw-feed-v45'>Raw Audit Feed</a></nav>
    <main>{body}</main></body></html>"""

def register(core):
    app=core.app
    engine=core.engine
    router=APIRouter()

    # Take over only visible GET routes. Ingestion, sources and requirement write routes are untouched.
    kept=[]
    for route in app.router.routes:
        path=getattr(route,"path",None)
        methods=getattr(route,"methods",set()) or set()
        if isinstance(route,APIRoute) and path in {"/whatsapp-live","/whatsapp-live/feed"} and "GET" in methods:
            continue
        kept.append(route)
    app.router.routes[:] = kept

    @router.get("/api/v451/live/status")
    def status():
        updater.request_refresh(force=False)
        sync=_sync_latest_whatsapp_to_canonical(engine,4000)
        return {"version":VERSION,"status":"OK","canonical":_stats(engine),"raw":_raw_stats(),"sync":sync,"auto_updater":updater.STATE}

    @router.get("/api/v451/live/properties")
    def properties(q:str="",limit:int=800):
        updater.request_refresh(force=False)
        sync=_sync_latest_whatsapp_to_canonical(engine,4000)
        rows=_canonical_rows(engine,q,min(max(limit,1),1500))
        out=[]
        for r in rows:
            d=dict(r)
            for k,v in list(d.items()):
                if hasattr(v,"isoformat"): d[k]=v.isoformat()
            out.append(d)
        return {"status":"OK","version":VERSION,"count":len(out),"sync":sync,"rows":out}

    @router.get("/whatsapp-live",response_class=HTMLResponse)
    def dashboard():
        updater.request_refresh(force=False)
        sync=_sync_latest_whatsapp_to_canonical(engine,4000)
        raw=_raw_stats()
        st=_stats(engine)
        body=f"""<h2>Live Intake Command Centre</h2><div class=grid>
        <div class=card>Active Mobile Numbers<h2>{raw['accounts']}</h2></div>
        <div class=card>Active Groups<h2>{raw['groups']}</h2></div>
        <div class=card>Raw Messages Today<h2>{raw['today']}</h2></div>
        <div class=card>Canonical WhatsApp Properties<h2>{st['count']}</h2></div>
        <div class=card>Verified Canonical Properties<h2>{st['verified']}</h2></div>
        </div>
        <div class=card><b>Latest raw WhatsApp:</b> {_esc(raw['latest'] or '—')}<br>
        <b>Latest canonical WhatsApp listing:</b> {_esc(st['latest'] or '—')}<br>
        <b>Canonical bridge:</b> <span class=ok>{_esc(sync.get('status'))}</span> · processed {_esc(sync.get('processed',0))}</div>
        <div class=card><a class='btn green' href='/whatsapp-live/feed'>Open Clean Canonical Feed</a>
        <a class=btn href='/whatsapp-property-master-v44'>Open Source Master</a>
        <a class=btn href='/whatsapp-live/sources'>Add Number / Group</a>
        <a class=btn href='/whatsapp-live/requirements'>Requirements</a></div>"""
        return HTMLResponse(_page("WhatsApp Live Dashboard",body))

    @router.get("/whatsapp-live/feed",response_class=HTMLResponse)
    def feed(request:Request):
        updater.request_refresh(force=False)
        sync=_sync_latest_whatsapp_to_canonical(engine,4000)
        q=str(request.query_params.get("q") or "").strip()
        rows=_canonical_rows(engine,q,1000)
        trs="".join(f"""<tr>
        <td>{_esc(r['captured_on'] or '—')}</td><td><b>{_esc(r['lead_type'] or '—')}</b></td>
        <td class='desc'><b>{_esc(r['description'])}</b></td><td>{_esc(r['area'] or '—')}</td>
        <td>{_esc(r['configuration_details'])}</td><td><b>{_esc(r['price'] or '—')}</b></td>
        <td>{_esc(r['contact_name_number'] or '—')}</td><td>{_esc(r['source'] or '—')}</td>
        <td>{_esc(r['verification'])}</td><td>{_esc(r['source_count'])}</td></tr>""" for r in rows)
        body=f"""<h2>Live WhatsApp Canonical Property Feed</h2>
        <div class=card><form method=get style='display:grid;grid-template-columns:1fr auto;gap:8px'>
        <input name=q value='{_esc(q)}' placeholder='Search property, location, project, area, contact or source group'>
        <button>Search</button></form>
        <p class=muted>{len(rows)} canonical properties shown · bridge {_esc(sync.get('status'))}.
        One physical property is shown once; its WhatsApp listings/sources and contacts are merged underneath.</p></div>
        <div class=card style='overflow:auto'><table>
        <tr><th>Latest Capture</th><th>Transaction</th><th>Property</th><th>Area</th><th>Type / Project / Location</th>
        <th>Price / Rent</th><th>Internal Contact(s)</th><th>Source Group(s)</th><th>Verification</th><th>Listings Merged</th></tr>
        {trs or '<tr><td colspan=10>No canonical WhatsApp properties found.</td></tr>'}</table></div>
        <script>setTimeout(()=>{{ if(!document.querySelector("input").value) location.reload(); }},30000);</script>"""
        return HTMLResponse(_page("Live WhatsApp Canonical Feed",body))

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
          <p class=muted>Original incoming messages are preserved for audit. Use Live Property Feed for clean team search.</p>
          <table><tr><th>Received</th><th>Group</th><th>Sender</th><th>Raw Message</th><th>Result</th></tr>{trs}</table></div>"""))

    app.include_router(router)
    return router
