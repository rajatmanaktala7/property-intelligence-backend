from __future__ import annotations

from fastapi import Request, Form, Query, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute
from sqlalchemy import text

VERSION = "PRODUCTION-SURFACE-1.0"
STATE = {"registered": False, "error": None, "routes": []}

def _remove_owned(app, owned_paths):
    kept = []
    for route in app.router.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", set()) or set()
        if path in owned_paths and methods.intersection({"GET", "POST"}):
            continue
        kept.append(route)
    app.router.routes[:] = kept

def register(wrapped):
    app = wrapped.app
    core = wrapped.core

    import alliance_v383_database_foundation as v383
    import alliance_v46_unified_intelligence as v46
    import alliance_v45_live_whatsapp_takeover as v451

    def v383_status(req: Request):
        core.need_login(req)
        v383._ensure_schema(core.engine)
        with core.engine.connect() as c:
            counts = {
                "properties": c.execute(text("SELECT COUNT(*) FROM alliance_canonical_properties WHERE active=TRUE")).scalar(),
                "listings": c.execute(text("SELECT COUNT(*) FROM alliance_property_listings WHERE active=TRUE")).scalar(),
                "contacts": c.execute(text("SELECT COUNT(*) FROM alliance_contacts WHERE active=TRUE")).scalar(),
                "listing_contacts": c.execute(text("SELECT COUNT(*) FROM alliance_listing_contacts")).scalar(),
                "location_aliases": c.execute(text("SELECT COUNT(*) FROM alliance_location_aliases WHERE approved=TRUE")).scalar(),
            }
        return {
            "version": v383.VERSION,
            "status": "READY",
            "startup_error": None,
            "authoritative_foundation": True,
            "legacy_tables_preserved": True,
            "startup_mode": "DIRECT_PRODUCTION_SURFACE",
            "counts": counts,
        }

    def v383_sync(req: Request, limit: int = Query(10000, ge=1, le=50000)):
        core.need_login(req)
        try:
            return {"status": "OK", "version": v383.VERSION, **v383._migrate(core.engine, limit)}
        except Exception as exc:
            raise HTTPException(500, f"V383_SYNC_FAILED: {type(exc).__name__}: {exc}")

    def v46_status():
        gen = v46.latest_wa_generation(core.engine)
        _, mt = v46.master_results(core.engine, v46.parse_requirement("property"), 1)
        return {
            "version": v46.VERSION,
            "status": "OK",
            "whatsapp_generation": str(gen) if gen else None,
            "master_table": mt,
            "rejected_visible": False,
            "semantic_search": True,
            "four_source_matcher": True,
            "registration_mode": "DIRECT_PRODUCTION_SURFACE",
        }

    def v46_semantic_search(q: str, limit: int = 50):
        req = v46.parse_requirement(q)
        wa = v46.whatsapp_availability(core.engine, req, limit)
        news = v46.newspaper_results(core.engine, req, limit)
        master, mt = v46.master_results(core.engine, req, limit)
        return {
            "query": q,
            "parsed": req,
            "count": len(wa) + len(news) + len(master),
            "whatsapp": wa,
            "newspaper": news,
            "master": master,
            "master_table": mt,
        }

    def v451_status():
        v451.updater.request_refresh(force=False)
        sync = v451._sync_latest_whatsapp_to_canonical(core.engine, 4000)
        return {
            "version": v451.VERSION,
            "status": "OK",
            "canonical": v451._stats(core.engine),
            "raw": v451._raw_stats(),
            "sync": sync,
            "auto_updater": v451.updater.STATE,
            "registration_mode": "DIRECT_PRODUCTION_SURFACE",
        }

    def v451_properties(q: str = "", limit: int = 800):
        v451.updater.request_refresh(force=False)
        sync = v451._sync_latest_whatsapp_to_canonical(core.engine, 4000)
        rows = v451._canonical_rows(core.engine, q, min(max(limit, 1), 1500))
        out = []
        for r in rows:
            d = dict(r)
            for k, val in list(d.items()):
                if hasattr(val, "isoformat"):
                    d[k] = val.isoformat()
            out.append(d)
        return {"status": "OK", "version": v451.VERSION, "count": len(out), "sync": sync, "rows": out}

    def live_dashboard():
        v451.updater.request_refresh(force=False)
        sync = v451._sync_latest_whatsapp_to_canonical(core.engine, 4000)
        raw = v451._raw_stats()
        st = v451._stats(core.engine)
        body = f"""<h2>Live Intake Command Centre</h2><div class=grid>
        <div class=card>Active Mobile Numbers<h2>{raw['accounts']}</h2></div>
        <div class=card>Active Groups<h2>{raw['groups']}</h2></div>
        <div class=card>Raw Messages Today<h2>{raw['today']}</h2></div>
        <div class=card>Canonical WhatsApp Properties<h2>{st['count']}</h2></div>
        <div class=card>Verified Canonical Properties<h2>{st['verified']}</h2></div></div>
        <div class=card><b>Canonical bridge:</b> <span class=ok>{v451._esc(sync.get('status'))}</span> · processed {v451._esc(sync.get('processed',0))}</div>
        <div class=card><a class='btn green' href='/whatsapp-live/feed'>Open Clean Canonical Feed</a>
        <a class=btn href='/property-match-ai-v46'>AI Property Match</a></div>"""
        return HTMLResponse(v451._page("WhatsApp Live Dashboard", body))

    def live_feed(request: Request):
        v451.updater.request_refresh(force=False)
        sync = v451._sync_latest_whatsapp_to_canonical(core.engine, 4000)
        q = str(request.query_params.get("q") or "").strip()
        rows = v451._canonical_rows(core.engine, q, 1000)
        trs = "".join(
            f"""<tr>
            <td>{v451._esc(r['captured_on'] or '—')}</td><td><b>{v451._esc(r['lead_type'] or '—')}</b></td>
            <td class='desc'><b>{v451._esc(r['description'])}</b></td><td>{v451._esc(r['area'] or '—')}</td>
            <td>{v451._esc(r['configuration_details'])}</td><td><b>{v451._esc(r['price'] or '—')}</b></td>
            <td>{v451._esc(r['contact_name_number'] or '—')}</td><td>{v451._esc(r['source'] or '—')}</td>
            <td>{v451._esc(r['verification'])}</td><td>{v451._esc(r['source_count'])}</td></tr>"""
            for r in rows
        )
        body = f"""<h2>Live WhatsApp Canonical Property Feed</h2>
        <div class=card><form method=get style='display:grid;grid-template-columns:1fr auto;gap:8px'>
        <input name=q value='{v451._esc(q)}' placeholder='Search property, location, project, area, contact or source group'>
        <button>Search</button></form>
        <p class=muted>{len(rows)} canonical properties shown · bridge {v451._esc(sync.get('status'))}.</p></div>
        <div class=card style='overflow:auto'><table>
        <tr><th>Latest Capture</th><th>Transaction</th><th>Property</th><th>Area</th><th>Type / Project / Location</th>
        <th>Price / Rent</th><th>Internal Contact(s)</th><th>Source Group(s)</th><th>Verification</th><th>Listings Merged</th></tr>
        {trs or '<tr><td colspan=10>No canonical WhatsApp properties found.</td></tr>'}</table></div>"""
        return HTMLResponse(v451._page("Live WhatsApp Canonical Feed", body))

    routes = [
        ("/api/v383/status", v383_status, ["GET"]),
        ("/api/v383/sync", v383_sync, ["POST"]),
        ("/api/v46/status", v46_status, ["GET"]),
        ("/api/v46/semantic-search", v46_semantic_search, ["GET"]),
        ("/api/v451/live/status", v451_status, ["GET"]),
        ("/api/v451/live/properties", v451_properties, ["GET"]),
        ("/whatsapp-live", live_dashboard, ["GET"]),
        ("/whatsapp-live/feed", live_feed, ["GET"]),
    ]

    owned = {p for p, _, _ in routes}
    _remove_owned(app, owned)
    for path, endpoint, methods in routes:
        app.add_api_route(path, endpoint, methods=methods)

    present = {p: any(getattr(r, "path", None) == p for r in app.router.routes) for p in owned}
    STATE["registered"] = all(present.values())
    STATE["routes"] = sorted([p for p, ok in present.items() if ok])
    STATE["error"] = None
    return dict(STATE)
