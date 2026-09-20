from __future__ import annotations

from fastapi import Request, Query, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import text

VERSION = "PRODUCTION-SURFACE-2.0-CANONICAL-CLEAN"
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
    import alliance_master_matcher_contract_v1 as master_matcher
    import alliance_dashboard_cleanliness_v1 as cleanliness

    # Canonical Property Databases presentation must be installed on the actual
    # production boot path. This is display-only: no database mutation.
    import alliance_property_database_unified_patch_v1 as property_database_patch
    property_database_patch_state = property_database_patch.install()

    # Install the existing non-blocking cleanliness guard. It does not scan or
    # rewrite historical data at startup; it only hardens future explicit rebuilds.
    cleanliness_state = cleanliness.register(wrapped)

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

    def canonical_match(req: Request, q: str, limit: int = Query(50, ge=1, le=200)):
        core.need_login(req)
        q = (q or "").strip()
        if not q:
            raise HTTPException(422, "Requirement is required")
        try:
            result = master_matcher.run_match(core.engine, q, limit=limit)
            result["canonical_contract"] = "MASTER_PROPERTIES_ONLY"
            result["master_table"] = master_matcher.MASTER_TABLE
            return result
        except Exception as exc:
            raise HTTPException(500, f"CANONICAL_MATCH_FAILED: {type(exc).__name__}: {exc}")

    def clean_home(req: Request):
        core.need_login(req)
        html = """<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Alliance CRE Command Centre</title><style>
body{font-family:Arial,sans-serif;margin:0;background:#f5f2ec;color:#24211d}.wrap{max-width:1100px;margin:34px auto;padding:0 18px}
h1{margin-bottom:4px}.sub{color:#6d655c;margin-top:0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-top:24px}
a.card{display:block;background:#fff;border:1px solid #ded8cf;border-radius:12px;padding:18px;text-decoration:none;color:#24211d;box-shadow:0 2px 8px #0000000a}
a.card:hover{border-color:#8d8173}.card b{display:block;font-size:18px;margin-bottom:7px}.card span{font-size:14px;color:#6d655c}
.match{background:#fff;border:1px solid #ded8cf;border-radius:12px;padding:18px;margin-top:18px}input{width:70%;max-width:720px;padding:11px;border:1px solid #bbb;border-radius:8px}button{padding:11px 16px;border:0;border-radius:8px;background:#24211d;color:#fff;cursor:pointer}pre{white-space:pre-wrap;background:#f7f7f7;padding:12px;border-radius:8px;max-height:420px;overflow:auto}.foot{margin-top:22px;color:#756d64;font-size:13px}
</style></head><body><div class='wrap'><h1>Alliance CRE Command Centre</h1><p class='sub'>One Master Property Database. One Master Requirement Database. One canonical matcher.</p>
<div class='grid'>
<a class='card' href='/alliance/primary/properties'><b>Master Properties</b><span>All valid property inventory from Manual, WhatsApp, Newspaper and Magazine.</span></a>
<a class='card' href='/alliance/primary/requirements'><b>Master Requirements</b><span>All active requirements with Run Matcher workflow.</span></a>
<a class='card' href='/property-manual'><b>Add Property</b><span>Add a manual property into the operational pipeline.</span></a>
<a class='card' href='/whatsapp-live'><b>WhatsApp Live</b><span>Live property and requirement capture with source identity.</span></a>
<a class='card' href='/alliance/primary/matcher'><b>Matcher</b><span>Review canonical matching results and verification status.</span></a>
<a class='card' href='/alliance/primary/followups'><b>Follow-ups</b><span>Approved actions and team follow-up queue.</span></a>
</div>
<div class='match'><h2>Smart Match</h2><p>Write the requirement. Results are sourced only from Master Properties.</p><input id='q' placeholder='Example: restaurant space 2000 sqft in Saket for rent'><button onclick='runMatch()'>Run Matcher</button><pre id='out'>Ready.</pre></div>
<p class='foot'>Source databases remain available in their own sections for ingestion and audit. Team navigation is intentionally limited to operational actions.</p></div>
<script>async function runMatch(){const q=document.getElementById('q').value.trim(),o=document.getElementById('out');if(!q){o.textContent='Enter a requirement.';return;}o.textContent='Matching...';try{const r=await fetch('/api/alliance/canonical-match?q='+encodeURIComponent(q));const d=await r.json();o.textContent=JSON.stringify(d,null,2);}catch(e){o.textContent='Matcher error: '+e;}}</script></body></html>"""
        return HTMLResponse(html)

    routes = [
        ("/api/v383/status", v383_status, ["GET"]),
        ("/api/v383/sync", v383_sync, ["POST"]),
        ("/api/v46/status", v46_status, ["GET"]),
        ("/api/v46/semantic-search", v46_semantic_search, ["GET"]),
        ("/api/v451/live/status", v451_status, ["GET"]),
        ("/api/v451/live/properties", v451_properties, ["GET"]),
        ("/api/alliance/canonical-match", canonical_match, ["GET"]),
        ("/alliance/legacy/production-surface-home", clean_home, ["GET"]),
    ]

    owned = {p for p, _, _ in routes}
    _remove_owned(app, owned)

    for path, endpoint, methods in routes:
        app.add_api_route(path, endpoint, methods=methods)

    present = {p: any(getattr(r, "path", None) == p for r in app.router.routes) for p in owned}
    STATE["registered"] = all(present.values())
    STATE["routes"] = sorted([p for p, ok in present.items() if ok])
    STATE["error"] = None
    STATE["matcher_contract"] = "MASTER_PROPERTIES_ONLY"
    STATE["matcher_master_table"] = master_matcher.MASTER_TABLE
    STATE["dashboard_cleanliness"] = cleanliness_state
    STATE["unified_property_databases"] = property_database_patch_state
    STATE["whatsapp_ui_owner"] = "alliance_live_feed_purity V5.1"
    STATE["whatsapp_ui_registered_here"] = False
    return dict(STATE)
