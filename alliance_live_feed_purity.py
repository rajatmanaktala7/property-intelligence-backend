from __future__ import annotations

import hashlib
import re

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

VERSION = "LIVE-FEED-PROVEN-RESTORE-3.2-VISIBLE-ENTITY-FIX"


def _esc(v):
    return (
        str(v or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _source_engine(core):
    import alliance_v2_whatsapp_adapter as wa_adapter
    return wa_adapter._source_engine(core.engine)


def _exists(conn, table):
    return bool(
        conn.execute(
            text("SELECT to_regclass(:t)"),
            {"t": f"public.{table}"},
        ).scalar()
    )


def _count(conn, table, where_sql=""):
    sql = f"SELECT COUNT(*) FROM {table}"
    if where_sql:
        sql += f" WHERE {where_sql}"
    return int(conn.execute(text(sql)).scalar() or 0)


def _detect_source(conn):
    wa_properties = 0
    wai_listings = 0
    wa_requirements = 0

    if _exists(conn, "wa_properties"):
        wa_properties = _count(
            conn,
            "wa_properties",
            "COALESCE(record_status,'ACTIVE')='ACTIVE'",
        )

    if _exists(conn, "wa_requirements"):
        wa_requirements = _count(
            conn,
            "wa_requirements",
            "COALESCE(status,'ACTIVE')='ACTIVE'",
        )

    if _exists(conn, "wai_listings"):
        wai_listings = _count(conn, "wai_listings")

    selected = (
        "wa_properties"
        if wa_properties > 0
        else "wai_listings"
        if wai_listings > 0
        else "none"
    )

    return {
        "selected_source": selected,
        "wa_properties_count": wa_properties,
        "wa_requirements_count": wa_requirements,
        "wai_listings_count": wai_listings,
    }


def status(core):
    engine, dispose = _source_engine(core)
    out = {
        "status": "OK",
        "version": VERSION,
        "mode": "READ_EXISTING_PROVEN_LEADS_WITH_VISIBLE_ENTITY_SPLIT",
        "using_separate_whatsapp_database": bool(dispose),
        "selected_source": "none",
        "wa_properties_count": 0,
        "wa_requirements_count": 0,
        "wai_listings_count": 0,
    }
    try:
        with engine.connect() as conn:
            out.update(_detect_source(conn))
    except Exception as exc:
        out["status"] = "ERROR"
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if dispose:
            engine.dispose()
    return out


def _rows_from_wa_properties(conn, q, limit):
    params = {"lim": limit}
    where = ["COALESCE(record_status,'ACTIVE')='ACTIVE'"]

    if q:
        where.append(
            """(
              COALESCE(location,'') ILIKE :q OR
              COALESCE(locality,'') ILIKE :q OR
              COALESCE(property_type,'') ILIKE :q OR
              COALESCE(raw_text,'') ILIKE :q OR
              COALESCE(parent_message_text,'') ILIKE :q OR
              COALESCE(broker_name,'') ILIKE :q OR
              COALESCE(broker_phone,'') ILIKE :q OR
              COALESCE(owner_name,'') ILIKE :q OR
              COALESCE(owner_phone,'') ILIKE :q OR
              COALESCE(sender_name,'') ILIKE :q OR
              COALESCE(sender_phone,'') ILIKE :q
            )"""
        )
        params["q"] = "%" + q + "%"

    sql = f"""
      SELECT
        id,
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
        raw_text,
        parent_message_text
      FROM wa_properties
      WHERE {" AND ".join(where)}
      ORDER BY COALESCE(last_seen,first_seen) DESC NULLS LAST, id DESC
      LIMIT :lim
    """
    return conn.execute(text(sql), params).mappings().all()


def _rows_from_wai_listings(conn, q, limit):
    params = {"lim": limit}
    where = [
        "COALESCE(l.status,'') NOT IN ('REJECTED','AUTO_REJECT','AUTO_REJECTED')",
        "COALESCE(l.raw_listing_text,l.summary,'') <> ''",
    ]

    if q:
        where.append(
            """(
              COALESCE(l.location,'') ILIKE :q OR
              COALESCE(l.property_type,'') ILIKE :q OR
              COALESCE(l.raw_listing_text,'') ILIKE :q OR
              COALESCE(l.summary,'') ILIKE :q OR
              COALESCE(l.source_group_name,'') ILIKE :q OR
              COALESCE(l.poster_name,'') ILIKE :q OR
              COALESCE(ct.phone,'') ILIKE :q OR
              COALESCE(ct.display_name,'') ILIKE :q
            )"""
        )
        params["q"] = "%" + q + "%"

    sql = f"""
      SELECT
        l.id,
        l.id::text AS wa_property_id,
        1 AS source_item_no,
        COALESCE(rm.sent_at,l.created_at)::text AS first_seen,
        COALESCE(l.verified_at,l.created_at,rm.sent_at)::text AS last_seen,
        l.property_type,
        CASE
          WHEN lower(COALESCE(l.transaction,'')) IN ('rent','lease','leasing') THEN 'LEASE'
          WHEN lower(COALESCE(l.transaction,'')) IN ('sale','sell','selling') THEN 'SALE'
          ELSE COALESCE(l.transaction,'UNKNOWN')
        END AS transaction_type,
        NULL::text AS city,
        l.location,
        NULL::text AS locality,
        NULL::text AS address,
        NULL::text AS landmark,
        l.area_sqft_numeric AS area_sqft,
        l.area_sqft_numeric AS available_area_sqft,
        NULL::text AS floor,
        NULL::text AS frontage,
        CASE
          WHEN lower(COALESCE(l.transaction,'')) IN ('rent','lease','leasing') THEN l.budget_numeric
          ELSE NULL
        END AS rent_inr,
        CASE
          WHEN lower(COALESCE(l.transaction,'')) IN ('sale','sell','selling') THEN l.budget_numeric
          ELSE NULL
        END AS sale_price_inr,
        NULL::numeric AS cam_inr,
        NULL::text AS possession,
        NULL::text AS parking,
        NULL::text AS suitable_for,
        NULL::text AS nearby_brands,
        'UNKNOWN'::text AS availability,
        COALESCE(ct.display_name,l.poster_name) AS broker_name,
        COALESCE(ct.phone,rm.sender_phone) AS broker_phone,
        NULL::text AS owner_name,
        NULL::text AS owner_phone,
        COALESCE(rm.sender_display_name,l.poster_name) AS sender_name,
        COALESCE(rm.sender_phone,ct.phone) AS sender_phone,
        CASE WHEN l.duplicate_of IS NULL THEN 'UNIQUE' ELSE 'POSSIBLE_DUPLICATE' END AS duplicate_status,
        l.confidence_score AS confidence,
        COALESCE(NULLIF(l.raw_listing_text,''),l.summary,'') AS raw_text,
        COALESCE(NULLIF(l.raw_listing_text,''),l.summary,'') AS parent_message_text
      FROM wai_listings l
      LEFT JOIN wai_contacts ct ON ct.id=l.contact_id
      LEFT JOIN wai_raw_messages rm ON rm.id=l.source_message_id
      WHERE {" AND ".join(where)}
      ORDER BY l.created_at DESC NULLS LAST, l.id DESC
      LIMIT :lim
    """
    return conn.execute(text(sql), params).mappings().all()


def get_rows(core, q="", limit=500):
    engine, dispose = _source_engine(core)
    try:
        with engine.connect() as conn:
            detected = _detect_source(conn)
            source = detected["selected_source"]

            if source == "wa_properties":
                return source, _rows_from_wa_properties(conn, q, limit), detected

            if source == "wai_listings":
                return source, _rows_from_wai_listings(conn, q, limit), detected

            return "none", [], detected
    finally:
        if dispose:
            engine.dispose()


def _norm_entity(v):
    return re.sub(r"\s+", " ", str(v or "")).strip().lower()


def _visible_entities(rows):
    """
    Historical wa_properties may contain old multi-property raw_text values.
    For display only, use the proven split_inventory() again and flatten rows.
    No database writes occur.
    """
    from whatsapp_intelligence import split_inventory

    out = []
    seen = set()

    for row in rows:
        d = dict(row)
        raw = str(d.get("raw_text") or "").strip()
        parent = str(d.get("parent_message_text") or "").strip()

        # Use raw_text first. If it is suspiciously identical to a long parent message,
        # split the parent. This handles historical rows created before splitter fixes.
        candidate = raw or parent

        try:
            parts = split_inventory(candidate) if candidate else []
        except Exception:
            parts = [candidate] if candidate else []

        if not parts:
            parts = [candidate] if candidate else [""]

        # If raw_text is one clean item but parent contains many items, split parent
        # only when raw_text == parent or raw_text is empty.
        if parent and (not raw or _norm_entity(raw) == _norm_entity(parent)):
            try:
                parent_parts = split_inventory(parent)
                if len(parent_parts) > len(parts):
                    parts = parent_parts
            except Exception:
                pass

        for idx, part in enumerate(parts, start=1):
            part = str(part or "").strip()
            if not part:
                continue

            fp = hashlib.sha1(
                (
                    str(d.get("wa_property_id") or d.get("id") or "")
                    + "|"
                    + _norm_entity(part)
                ).encode("utf-8")
            ).hexdigest()

            if fp in seen:
                continue
            seen.add(fp)

            x = dict(d)
            x["visible_entity_no"] = idx
            x["visible_entity_text"] = part
            x["visible_entity_id"] = (
                f"{d.get('wa_property_id') or d.get('id')}-E{idx}"
            )
            out.append(x)

    return out


def register(wrapped):
    app = wrapped.app
    core = wrapped.core

    owned = {
        "/whatsapp-live/feed",
        "/api/live-feed-purity/status",
        "/api/live-feed-purity/sample",
        "/api/live-feed-purity/debug",
    }

    app.router.routes[:] = [
        r
        for r in app.router.routes
        if not (
            getattr(r, "path", None) in owned
            and "GET" in (getattr(r, "methods", set()) or set())
        )
    ]

    def api_status():
        return status(core)

    def sample():
        try:
            source, stored_rows, detected = get_rows(core, "", 50)
            visible = _visible_entities(stored_rows)
            return {
                "status": "OK",
                "version": VERSION,
                "source": source,
                "stored_rows_sampled": len(stored_rows),
                "visible_entities_after_split": len(visible),
                "split_gain": len(visible) - len(stored_rows),
                "counts": detected,
            }
        except Exception as exc:
            return {
                "status": "ERROR",
                "version": VERSION,
                "error": f"{type(exc).__name__}: {exc}",
            }

    def debug():
        result = status(core)
        try:
            source, stored_rows, detected = get_rows(core, "", 100)
            visible = _visible_entities(stored_rows)
            multi = {}
            for row in visible:
                base = str(row.get("wa_property_id") or row.get("id"))
                multi[base] = multi.get(base, 0) + 1

            result.update(
                {
                    "selected_source": source,
                    "stored_rows_sampled": len(stored_rows),
                    "visible_entities_after_split": len(visible),
                    "split_gain": len(visible) - len(stored_rows),
                    "multi_entity_stored_rows": sum(1 for n in multi.values() if n > 1),
                    "sample_visible_ids": [
                        x.get("visible_entity_id") for x in visible[:10]
                    ],
                    "counts": detected,
                }
            )
        except Exception as exc:
            result["sample_error"] = f"{type(exc).__name__}: {exc}"
        return result

    def feed(request: Request):
        q = str(request.query_params.get("q") or "").strip()
        try:
            limit = max(25, min(int(request.query_params.get("limit") or 500), 1000))
        except Exception:
            limit = 500

        try:
            # Fetch fewer stored rows than the absolute maximum because a row
            # may expand into several visible entities.
            source, stored_rows, detected = get_rows(core, q, limit)
            rows = _visible_entities(stored_rows)
        except Exception as exc:
            return HTMLResponse(
                f"""<!doctype html><html><body style='font-family:Arial;padding:30px'>
                <h2>WhatsApp Property Leads</h2>
                <p><b>Feed error:</b> {_esc(type(exc).__name__)}: {_esc(exc)}</p>
                <p><a href='/api/live-feed-purity/debug'>Open live-feed diagnostics</a></p>
                </body></html>""",
                status_code=500,
            )

        trs = []
        for row in rows[:1500]:
            price = []
            if row.get("rent_inr") not in (None, ""):
                price.append("Rent: " + str(row.get("rent_inr")))
            if row.get("sale_price_inr") not in (None, ""):
                price.append("Sale: " + str(row.get("sale_price_inr")))

            contact = " | ".join(
                str(x)
                for x in [
                    row.get("owner_name"),
                    row.get("owner_phone"),
                    row.get("broker_name"),
                    row.get("broker_phone"),
                    row.get("sender_name"),
                    row.get("sender_phone"),
                ]
                if x
            )

            location = " · ".join(
                str(x)
                for x in [row.get("location"), row.get("locality")]
                if x
            )
            area = row.get("available_area_sqft") or row.get("area_sqft") or "—"

            trs.append(
                f"""<tr>
                <td>{_esc(row.get('last_seen') or row.get('first_seen') or '—')}</td>
                <td><b>{_esc(row.get('transaction_type') or '—')}</b></td>
                <td>{_esc(row.get('property_type') or '—')}</td>
                <td>{_esc(location or '—')}</td>
                <td>{_esc(area)}</td>
                <td>{_esc(' | '.join(price) or '—')}</td>
                <td style='min-width:440px;white-space:pre-wrap'>{_esc(row.get('visible_entity_text') or '—')}</td>
                <td>{_esc(contact or '—')}</td>
                <td>{_esc(row.get('duplicate_status') or '—')}</td>
                <td>{_esc(row.get('confidence') or '—')}</td>
                </tr>"""
            )

        body = "".join(trs) or "<tr><td colspan='10'>No property leads found.</td></tr>"

        return HTMLResponse(
            f"""<!doctype html><html><head><meta charset='utf-8'>
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
            <small>One visible property entity per row · historical multi-property rows re-split safely in memory</small></header>
            <main>
            <div class='card'>
              <form>
                <input name='q' value='{_esc(q)}' placeholder='Search location, property type, broker, owner, phone or details'>
                <input type='hidden' name='limit' value='{limit}'>
                <button>Search</button>
              </form>
              <p><span class='ok'>{len(rows)} visible property entities</span> from <b>{len(stored_rows)}</b> stored rows · source: <b>{_esc(source)}</b></p>
              <p>wa_properties: <b>{detected['wa_properties_count']}</b> · requirements kept separate: <b>{detected['wa_requirements_count']}</b></p>
              <p><a href='/api/live-feed-purity/debug'>Diagnostics</a></p>
            </div>
            <div class='card' style='overflow:auto'>
              <table>
              <tr><th>Latest</th><th>Transaction</th><th>Type</th><th>Location</th><th>Area</th><th>Price/Rent</th><th>Property Entity</th><th>Contact</th><th>Duplicate</th><th>Confidence</th></tr>
              {body}
              </table>
            </div>
            </main></body></html>"""
        )

    app.add_api_route("/api/live-feed-purity/status", api_status, methods=["GET"])
    app.add_api_route("/api/live-feed-purity/sample", sample, methods=["GET"])
    app.add_api_route("/api/live-feed-purity/debug", debug, methods=["GET"])
    app.add_api_route("/whatsapp-live/feed", feed, methods=["GET"])

    return {"status": "REGISTERED", "version": VERSION}
