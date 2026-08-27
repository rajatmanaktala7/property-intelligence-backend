from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

VERSION="LIVE-FEED-PROVEN-RESTORE-3.0"

def _esc(v):
    return (str(v or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
            .replace('"',"&quot;").replace("'","&#39;"))

def _source_engine(core):
    # Reuse the exact source-engine logic already used by the working WhatsApp V2 system.
    import alliance_v2_whatsapp_adapter as wa_adapter
    return wa_adapter._source_engine(core.engine)

def _exists(c,table):
    return bool(c.execute(text("SELECT to_regclass(:t)"),{"t":f"public.{table}"}).scalar())

def status(core):
    engine,dispose=_source_engine(core)
    out={
        "status":"OK",
        "version":VERSION,
        "mode":"READ_EXISTING_PROVEN_LEADS",
        "using_separate_whatsapp_database":bool(dispose),
        "wa_properties_exists":False,
        "wa_properties_count":0,
        "wa_requirements_count":0,
        "wai_listings_count":0,
    }
    try:
        with engine.connect() as c:
            if _exists(c,"wa_properties"):
                out["wa_properties_exists"]=True
                out["wa_properties_count"]=int(c.execute(text(
                    "SELECT COUNT(*) FROM wa_properties WHERE COALESCE(record_status,'ACTIVE')='ACTIVE'"
                )).scalar() or 0)
            if _exists(c,"wa_requirements"):
                out["wa_requirements_count"]=int(c.execute(text(
                    "SELECT COUNT(*) FROM wa_requirements WHERE COALESCE(status,'ACTIVE')='ACTIVE'"
                )).scalar() or 0)
            if _exists(c,"wai_listings"):
                out["wai_listings_count"]=int(c.execute(text("SELECT COUNT(*) FROM wai_listings")).scalar() or 0)
    finally:
        if dispose:
            engine.dispose()
    return out

def _rows_from_wa_properties(c,q,limit):
    params={"lim":limit}
    where=["COALESCE(record_status,'ACTIVE')='ACTIVE'"]
    if q:
        where.append("""(
          COALESCE(location,'') ILIKE :q OR
          COALESCE(locality,'') ILIKE :q OR
          COALESCE(property_type,'') ILIKE :q OR
          COALESCE(raw_text,'') ILIKE :q OR
          COALESCE(broker_name,'') ILIKE :q OR
          COALESCE(broker_phone,'') ILIKE :q OR
          COALESCE(owner_name,'') ILIKE :q OR
          COALESCE(owner_phone,'') ILIKE :q OR
          COALESCE(sender_name,'') ILIKE :q OR
          COALESCE(sender_phone,'') ILIKE :q
        )""")
        params["q"]="%"+q+"%"

    sql=f"""
      SELECT
        wa_property_id,
        source_item_no,
        first_seen,
        last_seen,
        property_type,
        transaction_type,
        city,
        location,
        locality,
        address,
        landmark,
        area_sqft,
        available_area_sqft,
        floor,
        frontage,
        rent_inr,
        sale_price_inr,
        cam_inr,
        possession,
        parking,
        suitable_for,
        nearby_brands,
        availability,
        broker_name,
        broker_phone,
        owner_name,
        owner_phone,
        sender_name,
        sender_phone,
        duplicate_status,
        confidence,
        raw_text
      FROM wa_properties
      WHERE {" AND ".join(where)}
      ORDER BY COALESCE(last_seen,first_seen) DESC NULLS LAST, id DESC
      LIMIT :lim
    """
    return c.execute(text(sql),params).mappings().all()

def _rows_from_wai_listings(c,q,limit):
    params={"lim":limit}
    where=["COALESCE(l.status,'') NOT IN ('REJECTED','AUTO_REJECT','AUTO_REJECTED')"]
    if q:
        where.append("""(
          COALESCE(l.location,'') ILIKE :q OR
          COALESCE(l.property_type,'') ILIKE :q OR
          COALESCE(l.raw_listing_text,'') ILIKE :q OR
          COALESCE(l.summary,'') ILIKE :q OR
          COALESCE(l.source_group_name,'') ILIKE :q OR
          COALESCE(ct.phone,'') ILIKE :q
        )""")
        params["q"]="%"+q+"%"

    sql=f"""
      SELECT
        l.id::text AS wa_property_id,
        1 AS source_item_no,
        l.created_at AS first_seen,
        l.created_at AS last_seen,
        l.property_type,
        l.transaction AS transaction_type,
        NULL::text AS city,
        l.location,
        NULL::text AS locality,
        NULL::text AS address,
        NULL::text AS landmark,
        l.area_sqft_numeric AS area_sqft,
        l.area_sqft_numeric AS available_area_sqft,
        NULL::text AS floor,
        NULL::text AS frontage,
        CASE WHEN lower(COALESCE(l.transaction,'')) IN ('rent','lease','leasing') THEN l.budget_numeric ELSE NULL END AS rent_inr,
        CASE WHEN lower(COALESCE(l.transaction,'')) IN ('sale','sell','selling') THEN l.budget_numeric ELSE NULL END AS sale_price_inr,
        NULL::numeric AS cam_inr,
        NULL::text AS possession,
        NULL::text AS parking,
        NULL::text AS suitable_for,
        NULL::text AS nearby_brands,
        'UNKNOWN'::text AS availability,
        COALESCE(ct.display_name,l.poster_name) AS broker_name,
        ct.phone AS broker_phone,
        NULL::text AS owner_name,
        NULL::text AS owner_phone,
        l.poster_name AS sender_name,
        ct.phone AS sender_phone,
        CASE WHEN l.duplicate_of IS NULL THEN 'UNIQUE' ELSE 'POSSIBLE_DUPLICATE' END AS duplicate_status,
        l.confidence_score AS confidence,
        COALESCE(NULLIF(l.raw_listing_text,''),l.summary,'') AS raw_text
      FROM wai_listings l
      LEFT JOIN wai_contacts ct ON ct.id=l.contact_id
      WHERE {" AND ".join(where)}
      ORDER BY l.created_at DESC NULLS LAST
      LIMIT :lim
    """
    return c.execute(text(sql),params).mappings().all()

def get_rows(core,q="",limit=500):
    engine,dispose=_source_engine(core)
    try:
        with engine.connect() as c:
            # PROVEN path first: this table was created by the earlier live bridge after split_inventory().
            if _exists(c,"wa_properties"):
                n=int(c.execute(text(
                    "SELECT COUNT(*) FROM wa_properties WHERE COALESCE(record_status,'ACTIVE')='ACTIVE'"
                )).scalar() or 0)
                if n>0:
                    return "wa_properties",_rows_from_wa_properties(c,q,limit)

            # Safe fallback if old table is unavailable.
            if _exists(c,"wai_listings"):
                return "wai_listings",_rows_from_wai_listings(c,q,limit)
    finally:
        if dispose:
            engine.dispose()
    return "none",[]

def register(wrapped):
    app=wrapped.app
    core=wrapped.core

    owned={"/whatsapp-live/feed","/api/live-feed-purity/status","/api/live-feed-purity/sample"}
    app.router.routes[:]=[
        r for r in app.router.routes
        if not (getattr(r,"path",None) in owned and "GET" in (getattr(r,"methods",set()) or set()))
    ]

    def api_status():
        return status(core)

    def sample():
        source,rows=get_rows(core,"",25)
        return {
            "status":"OK",
            "version":VERSION,
            "source":source,
            "sample_count":len(rows),
            "one_property_per_row":source=="wa_properties",
        }

    def feed(request:Request):
        q=str(request.query_params.get("q") or "").strip()
        try:
            limit=max(25,min(int(request.query_params.get("limit") or 500),1000))
        except Exception:
            limit=500

        source,rows=get_rows(core,q,limit)

        trs=[]
        for r in rows:
            price=[]
            if r.get("rent_inr") not in (None,""):
                price.append("Rent: "+str(r.get("rent_inr")))
            if r.get("sale_price_inr") not in (None,""):
                price.append("Sale: "+str(r.get("sale_price_inr")))

            contact=" | ".join([
                x for x in [
                    r.get("owner_name"),r.get("owner_phone"),
                    r.get("broker_name"),r.get("broker_phone"),
                    r.get("sender_name"),r.get("sender_phone")
                ] if x
            ])

            location=" · ".join([x for x in [r.get("location"),r.get("locality")] if x])
            area=r.get("available_area_sqft") or r.get("area_sqft") or "—"

            trs.append(f"""<tr>
              <td>{_esc(r.get('last_seen') or r.get('first_seen') or '—')}</td>
              <td><b>{_esc(r.get('transaction_type') or '—')}</b></td>
              <td>{_esc(r.get('property_type') or '—')}</td>
              <td>{_esc(location or '—')}</td>
              <td>{_esc(area)}</td>
              <td>{_esc(' | '.join(price) or '—')}</td>
              <td style='min-width:440px;white-space:pre-wrap'>{_esc(r.get('raw_text') or '—')}</td>
              <td>{_esc(contact or '—')}</td>
              <td>{_esc(r.get('duplicate_status') or '—')}</td>
              <td>{_esc(r.get('confidence') or '—')}</td>
            </tr>""")

        body="".join(trs) or "<tr><td colspan='10'>No property leads found.</td></tr>"

        return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'>
        <meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>WhatsApp Property Leads</title>
        <style>
        body{{font-family:Arial;margin:0;background:#f5f1eb;color:#29231e}}
        header{{background:#4e4034;color:white;padding:18px 22px}}
        main{{padding:18px;max-width:1900px;margin:auto}}
        .card{{background:white;border:1px solid #ddd0c2;border-radius:10px;padding:12px;margin-bottom:12px}}
        table{{width:100%;border-collapse:collapse;background:white}}
        th,td{{padding:8px;border-bottom:1px solid #e8e1da;text-align:left;vertical-align:top;font-size:12px}}
        th{{background:#eee4da;position:sticky;top:0}}
        input{{width:75%;padding:10px;border:1px solid #baa896;border-radius:6px}}
        button{{padding:10px 14px;background:#5a4635;color:white;border:0;border-radius:6px}}
        .ok{{color:#176b3a;font-weight:700}}
        </style></head><body>
        <header><h2 style='margin:0'>WhatsApp Property Leads</h2>
        <small>Restored from the proven earlier lead pipeline · one stored property per row</small></header>
        <main>
        <div class='card'>
          <form>
            <input name='q' value='{_esc(q)}' placeholder='Search location, property type, broker, owner, phone or details'>
            <input type='hidden' name='limit' value='{limit}'>
            <button>Search</button>
          </form>
          <p><span class='ok'>{len(rows)} property leads</span> · source table: <b>{_esc(source)}</b></p>
        </div>
        <div class='card' style='overflow:auto'>
          <table>
          <tr><th>Latest</th><th>Transaction</th><th>Type</th><th>Location</th><th>Area</th><th>Price/Rent</th><th>Property Details</th><th>Contact</th><th>Duplicate</th><th>Confidence</th></tr>
          {body}
          </table>
        </div>
        </main></body></html>""")

    app.add_api_route("/api/live-feed-purity/status",api_status,methods=["GET"])
    app.add_api_route("/api/live-feed-purity/sample",sample,methods=["GET"])
    app.add_api_route("/whatsapp-live/feed",feed,methods=["GET"])
    return {"status":"REGISTERED","version":VERSION}
