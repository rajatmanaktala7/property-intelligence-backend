from __future__ import annotations

from fastapi import Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text

VERSION = "12.4.11-ROOT-AND-MATCHER-AUTHORITY"
REGISTRATION = {}

def _app(core):
    return getattr(core, "app", None) or core

def _remove(app, specs):
    specs = {(p, m.upper()) for p, m in specs}
    kept, removed = [], []
    for r in list(app.router.routes):
        path = getattr(r, "path", None)
        methods = set(getattr(r, "methods", set()) or set())
        if any(path == p and m in methods for p, m in specs):
            removed.append((path, sorted(methods), getattr(getattr(r, "endpoint", None), "__module__", "")))
        else:
            kept.append(r)
    app.router.routes[:] = kept
    return removed

def _owners(app, path):
    return [
        {
            "module": getattr(getattr(r, "endpoint", None), "__module__", ""),
            "name": getattr(getattr(r, "endpoint", None), "__name__", ""),
            "methods": sorted(set(getattr(r, "methods", set()) or set())),
        }
        for r in app.router.routes
        if getattr(r, "path", None) == path
    ]

def register(core):
    app = _app(core)

    removed_root = _remove(app, [("/", "GET")])

    @app.get("/", include_in_schema=False)
    def alliance_home():
        return RedirectResponse("/alliance/primary", status_code=307)

    removed_matcher = _remove(app, [
        ("/deal-match-ai-v60", "GET"),
        ("/api/v60/deal-match", "GET"),
        ("/api/v60/status", "GET"),
        ("/api/v60/feedback", "POST"),
    ])

    import alliance_deal_match_ai_v60 as v60
    matcher_registration = v60.register(core)

    @app.get("/api/alliance/root-matcher-audit")
    def root_matcher_audit(
        q: str = Query(
            "Need 1-2 BHK Apartment / Villa in North Goa for rent around 1 lakh",
            max_length=5000,
        )
    ):
        import alliance_whatsapp_first_match_v1 as wa
        import alliance_phase5_canonical_matcher as phase5

        total = 0
        generations = None
        current_generation_rows = None
        if phase5.table_exists(core.engine, "pi_whatsapp_property_master"):
            with core.engine.connect() as c:
                total = int(c.execute(text("SELECT COUNT(*) FROM pi_whatsapp_property_master")).scalar() or 0)
                cols = phase5.table_columns(core.engine, "pi_whatsapp_property_master")
                if "generation_id" in cols:
                    generations = int(c.execute(text(
                        "SELECT COUNT(DISTINCT generation_id) FROM pi_whatsapp_property_master"
                    )).scalar() or 0)
                    try:
                        g = phase5._live_wa_generation(core.engine)
                        current_generation_rows = int(c.execute(text(
                            "SELECT COUNT(*) FROM pi_whatsapp_property_master WHERE generation_id=:g"
                        ), {"g": g}).scalar() or 0)
                    except Exception:
                        current_generation_rows = None

        result = wa.run_match(core.engine, q, min_score=70.0, limit=20)
        req = result.get("requirement") or {}
        summary = result.get("summary") or {}

        return JSONResponse({
            "status": "PASS",
            "version": VERSION,
            "root": {
                "redirect_target": "/alliance/primary",
                "owners": _owners(app, "/"),
            },
            "dashboard": {
                "owners": _owners(app, "/alliance/primary"),
            },
            "matcher": {
                "page_owners": _owners(app, "/deal-match-ai-v60"),
                "api_owners": _owners(app, "/api/v60/deal-match"),
                "status_owners": _owners(app, "/api/v60/status"),
                "route_version": getattr(v60, "VERSION", "unknown"),
                "engine_version": getattr(wa, "VERSION", "unknown"),
            },
            "whatsapp_inventory": {
                "table": "pi_whatsapp_property_master",
                "total_rows": total,
                "generation_count": generations,
                "current_generation_rows": current_generation_rows,
                "search_scope": "ALL_STORED_GENERATIONS_REQUIREMENT_FILTERED",
                "rows_loaded_for_requirement": summary.get("pi_whatsapp_property_master"),
                "deduped_candidates": summary.get("whatsapp_deduped_candidates"),
            },
            "sample_requirement": {
                "parsed": req,
                "exact_verified": len(result.get("exact_verified") or []),
                "exact_needs_verification": len(result.get("exact_needs_verification") or []),
                "alternatives": len(result.get("alternatives") or []),
                "fallback_used": summary.get("fallback_used"),
                "matching_path": summary.get("matching_path"),
            },
            "contacts_exposed": False,
            "source_data_mutation": False,
        })

    REGISTRATION.update({
        "status": "AUTHORITATIVE",
        "version": VERSION,
        "root_removed": len(removed_root),
        "matcher_routes_removed": len(removed_matcher),
        "matcher_registration": matcher_registration,
        "root_target": "/alliance/primary",
        "matcher_owner": "alliance_deal_match_ai_v60",
    })
    return dict(REGISTRATION)
