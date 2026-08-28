from __future__ import annotations

from fastapi import Request, Query, HTTPException
from sqlalchemy import text

VERSION = "PRODUCTION-SURFACE-1.1-NO-WHATSAPP-UI"
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
        return {
            "status": "OK",
            "version": v451.VERSION,
            "count": len(out),
            "sync": sync,
            "rows": out,
        }

    # IMPORTANT:
    # Production surface owns API/health/intelligence routes only.
    # WhatsApp presentation routes are intentionally NOT registered here.
    # Sole UI owner is alliance_live_feed_purity V5.1, loaded afterwards by
    # production_entrypoint.py.
    routes = [
        ("/api/v383/status", v383_status, ["GET"]),
        ("/api/v383/sync", v383_sync, ["POST"]),
        ("/api/v46/status", v46_status, ["GET"]),
        ("/api/v46/semantic-search", v46_semantic_search, ["GET"]),
        ("/api/v451/live/status", v451_status, ["GET"]),
        ("/api/v451/live/properties", v451_properties, ["GET"]),
    ]

    owned = {p for p, _, _ in routes}
    _remove_owned(app, owned)

    for path, endpoint, methods in routes:
        app.add_api_route(path, endpoint, methods=methods)

    present = {
        p: any(getattr(r, "path", None) == p for r in app.router.routes)
        for p in owned
    }
    STATE["registered"] = all(present.values())
    STATE["routes"] = sorted([p for p, ok in present.items() if ok])
    STATE["error"] = None
    STATE["whatsapp_ui_owner"] = "alliance_live_feed_purity V5.1"
    STATE["whatsapp_ui_registered_here"] = False
    return dict(STATE)
