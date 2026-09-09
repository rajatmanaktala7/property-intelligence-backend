from __future__ import annotations
from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text
import alliance_core_contract as contract

VERSION = "1.0.0-ALLIANCE-SYSTEM-DOCTOR"

def _app(core): return getattr(core, "app", None) or core
def _engine(core): return getattr(core, "engine", None)
def _login(core, req):
    fn = getattr(core, "need_login", None)
    if fn: fn(req)
def _paths(app): return {getattr(r, "path", None) for r in app.router.routes}
def _table(engine, name):
    try:
        with engine.connect() as c:
            return bool(c.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": name}).scalar())
    except Exception:
        return False

def snapshot(core):
    app = _app(core)
    engine = _engine(core)
    paths = _paths(app)
    route_checks = {k: (v["path"] in paths) for k,v in contract.ROUTE_REGISTRY.items() if v["lifecycle"] == "ACTIVE"}
    tables = {t: _table(engine, t) for t in [
        "pi_master_properties_v711", "pi_master_requirements_v711", "pi_master_source_links_v711",
        "pi_master_workflow_v720", "pi_master_matches_v720", "pi_operational_properties", "pi_operational_requirements"
    ]}
    try:
        import alliance_whatsapp_first_match_v1 as matcher
        matcher_version = getattr(matcher, "VERSION", "unknown")
    except Exception as exc:
        matcher_version = f"ERROR:{type(exc).__name__}"
    blockers = []
    for k,ok in route_checks.items():
        if not ok: blockers.append(f"MISSING_ROUTE:{k}")
    for k,ok in tables.items():
        if not ok: blockers.append(f"MISSING_TABLE:{k}")
    if "CANONICAL-MASTER-AUTHORITY" not in matcher_version:
        blockers.append("MATCHER_AUTHORITY_NOT_CANONICAL_MASTER")
    return {
        "status": "PASS" if not blockers else "FAIL",
        "version": VERSION,
        "contract_version": contract.VERSION,
        "protected_invariants": contract.PROTECTED_INVARIANTS,
        "routes": route_checks,
        "tables": tables,
        "matcher_version": matcher_version,
        "blockers": blockers,
    }

def _html(title, data):
    rows = ''.join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k,v in data.items())
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>body{{font-family:Arial;margin:0;background:#f4f7fb}}main{{padding:20px}}table{{border-collapse:collapse;width:100%;background:white}}td{{padding:9px;border-bottom:1px solid #ddd}}</style></head>
<body><main><h2>{title}</h2><table>{rows}</table></main></body></html>'''

def register(core):
    app = _app(core)
    owned = {"/alliance/system-doctor","/alliance/primary/data-health","/alliance/primary/ai-control","/api/alliance/system-doctor"}
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) not in owned]

    @app.get("/api/alliance/system-doctor")
    def api(req: Request):
        _login(core, req)
        return snapshot(core)

    @app.get("/alliance/system-doctor", response_class=HTMLResponse)
    def doctor(req: Request):
        _login(core, req)
        s = snapshot(core)
        return HTMLResponse(_html("Alliance System Doctor", {
            "Status": s["status"], "Matcher": s["matcher_version"],
            "Blockers": ", ".join(s["blockers"]) or "None",
            "Routes": str(s["routes"]), "Tables": str(s["tables"])
        }))

    @app.get("/alliance/primary/data-health", response_class=HTMLResponse)
    def data_health(req: Request):
        _login(core, req)
        s = snapshot(core)
        return HTMLResponse(_html("Alliance Data Health", {
            "Status": s["status"], "Canonical tables": str(s["tables"]),
            "Blockers": ", ".join(s["blockers"]) or "None"
        }))

    @app.get("/alliance/primary/ai-control", response_class=HTMLResponse)
    def ai_control(req: Request):
        _login(core, req)
        s = snapshot(core)
        return HTMLResponse(_html("Alliance AI Control", {
            "Matcher authority": s["matcher_version"], "System Doctor": s["status"],
            "Historical backfill": contract.PROTECTED_INVARIANTS["historical_backfill"],
            "Contacts exposed": contract.PROTECTED_INVARIANTS["client_contact_exposure"]
        }))

    return {"status": "REGISTERED", "version": VERSION}
