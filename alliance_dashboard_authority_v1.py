from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Any, Dict

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

VERSION = "1.0.0-CANONICAL-ALLIANCE-DASHBOARD"
MARKER = "CANONICAL_ALLIANCE_DASHBOARD_V1"
STATE: Dict[str, Any] = {
    "status": "INIT",
    "version": VERSION,
    "authority": "CANONICAL_MASTER_DATA",
    "dashboard_marker": MARKER,
    "legacy_dashboard_replaced": True,
    "matcher_authority": "MASTER_ONLY",
    "property_authority": "pi_master_properties_v711",
    "requirement_authority": "pi_requirement_gate_v1191",
    "contacts_scope": "AUTHENTICATED_STAFF_ONLY",
    "last_render_at": None,
    "last_error": None,
}

def _app(core):
    return getattr(core, "app", core)

def _engine(core):
    try:
        import alliance_property_brain_foundation_v1 as foundation
        return foundation._engine_from_core(core)
    except Exception:
        return None

def _scalar(engine, sql: str):
    if engine is None:
        return None
    try:
        with engine.connect() as c:
            return c.execute(text(sql)).scalar()
    except Exception:
        return None

def _counts(engine):
    return {
        "master_properties": _scalar(engine, "SELECT COUNT(*) FROM pi_master_properties_v711"),
        "master_requirements": _scalar(engine, "SELECT COUNT(*) FROM pi_requirement_gate_v1191"),
        "manual_requirements": _scalar(engine, "SELECT COUNT(*) FROM pi_requirement_gate_v1191 WHERE UPPER(COALESCE(source_type,'')) LIKE '%MANUAL%'"),
        "whatsapp_requirements": _scalar(engine, "SELECT COUNT(*) FROM pi_requirement_gate_v1191 WHERE UPPER(COALESCE(source_type,'')) LIKE '%WHATSAPP%'"),
        "newspaper_requirements": _scalar(engine, "SELECT COUNT(*) FROM pi_requirement_gate_v1191 WHERE UPPER(COALESCE(source_type,'')) LIKE '%NEWSPAPER%' OR UPPER(COALESCE(source_type,'')) LIKE '%MAGAZINE%'"),
        "discovery_requirements": _scalar(engine, "SELECT COUNT(*) FROM pi_requirement_gate_v1191 WHERE UPPER(COALESCE(source_type,'')) LIKE '%DISCOVERY%'"),
        "matcher_eligible_requirements": _scalar(engine, "SELECT COUNT(*) FROM pi_requirement_gate_v1191 WHERE matcher_eligible IS TRUE"),
        "contact_master": _scalar(engine, "SELECT COUNT(*) FROM pi_alliance_contact_master_v1"),
    }

def _n(v):
    return "—" if v is None else f"{int(v):,}"

def _card(title, value, subtitle, href):
    return f'<a class="card" href="{escape(href)}"><div class="label">{escape(title)}</div><div class="value">{escape(str(value))}</div><div class="sub">{escape(subtitle)}</div><div class="open">Open →</div></a>'

def _link(title, desc, href):
    return f'<a class="workflow" href="{escape(href)}"><strong>{escape(title)}</strong><span>{escape(desc)}</span></a>'

def _render(counts):
    now = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    cards = "".join([
        _card("Master Properties", _n(counts["master_properties"]), "Canonical property authority", "/alliance/final/databases"),
        _card("Master Requirements", _n(counts["master_requirements"]), "Canonical requirement authority", "/alliance/final/requirements"),
        _card("Manual Requirements", _n(counts["manual_requirements"]), "Canonical manual-source view", "/alliance/final/requirements/manual"),
        _card("WhatsApp Requirements", _n(counts["whatsapp_requirements"]), "Canonical WhatsApp-source view", "/alliance/final/requirements/whatsapp"),
        _card("Newspaper Requirements", _n(counts["newspaper_requirements"]), "Canonical newspaper/magazine view", "/alliance/final/requirements/newspaper"),
        _card("Matcher Eligible", _n(counts["matcher_eligible_requirements"]), "Human-verified requirement gate", "/alliance/primary/matcher"),
        _card("Contact Master", _n(counts["contact_master"]), "Evidence-only, source-segregated contacts", "/alliance/primary/contact-master"),
        _card("Availability", "LIVE", "Master-property verification workflow", "/alliance/primary/availability"),
    ])
    workflow = "".join([
        _link("Master Properties", "Canonical master property database", "/alliance/final/databases"),
        _link("Availability", "Verify matched properties before client sharing", "/alliance/primary/availability"),
        _link("Add Property", "Manual property entry", "/property-manual"),
        _link("Master Requirements", "Canonical requirement database", "/alliance/final/requirements"),
        _link("Add Requirement", "Capture a new requirement", "/requirements-workbench"),
        _link("Manual Requirement DB", "Manual-source canonical requirements", "/alliance/final/requirements/manual"),
        _link("WhatsApp Requirement DB", "WhatsApp-source canonical requirements", "/alliance/final/requirements/whatsapp"),
        _link("Smart Matcher", "Match requirements only against Master Property DB", "/alliance/primary/matcher"),
        _link("Automated Deal Desk", "Exact, verification-needed and alternate options", "/alliance/primary/deal-desk"),
        _link("WhatsApp Live", "WhatsApp ingestion and review", "/whatsapp-live"),
        _link("Newspaper Capture", "Newspaper intelligence capture", "/capture-intelligence"),
        _link("Commercial Intelligence", "Commercial opportunity intelligence", "/commercial-intelligence"),
        _link("Hospitality Intelligence", "Hospitality intelligence", "/hospitality-intelligence"),
        _link("Retail Intelligence", "Retail expansion intelligence", "/retail-expansion"),
        _link("Requirement Discovery", "Requirement discovery evidence", "/requirement-discovery"),
        _link("Marketing / Contact Master", "Deduped evidence-backed internal contacts", "/alliance/primary/contact-master"),
        _link("Follow-ups", "Team action queue", "/alliance/primary/followups"),
        _link("Reports", "Operational reports", "/alliance/primary/reports"),
        _link("Data Health", "Production data-health workspace", "/alliance/primary/data-health"),
        _link("System Doctor", "Runtime diagnostics", "/alliance/system-doctor"),
    ])
    staff = "".join([
        _link("Daily Day Plan", "Yogesh · Priya · Zoya", "/alliance/primary/day-plan"),
        _link("Staff Review", "Review daily work by staff", "/alliance/primary/staff-review"),
        _link("Monthly Review", "Monthly staff performance", "/alliance/primary/monthly-review"),
    ])
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alliance · Master Command Centre</title>
<style>
:root{{--bg:#f6f7f9;--panel:#fff;--ink:#142033;--muted:#637083;--line:#e5e9ef}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 Arial,sans-serif}}
.wrap{{max-width:1240px;margin:auto;padding:28px 20px 60px}}.top{{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:24px}}
h1{{margin:0 0 6px;font-size:30px}}h2{{margin:34px 0 14px;font-size:20px}}.badge{{display:inline-block;padding:7px 10px;border:1px solid var(--line);border-radius:999px;background:#fff;font-weight:700}}
.muted{{color:var(--muted)}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}
.card,.workflow{{display:block;text-decoration:none;color:inherit;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px}}
.card:hover,.workflow:hover{{border-color:#9aa8ba}}.label{{font-weight:700}}.value{{font-size:27px;font-weight:800;margin:7px 0}}
.sub,.workflow span{{display:block;color:var(--muted);font-size:13px}}.open{{margin-top:12px;font-weight:700}}.workflow strong{{display:block;margin-bottom:4px}}
.notice{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px;margin-top:18px}}.footer{{margin-top:34px;color:var(--muted);font-size:12px}}
</style></head>
<body data-alliance-dashboard-authority="{MARKER}">
<div class="wrap">
<div class="top"><div><h1>Alliance · Master Command Centre</h1><div class="muted">Single canonical operational workspace. Legacy dashboard counters and duplicate command bars are retired.</div></div><div class="badge">MASTER-ONLY · FROZEN</div></div>
<div class="notice"><strong>Production authorities:</strong> Master Properties = <code>pi_master_properties_v711</code> · Master Requirements = <code>pi_requirement_gate_v1191</code> · Matcher = <strong>MASTER_ONLY</strong> · Contacts = authenticated staff only.</div>
<h2>Live Canonical Counts</h2><div class="grid">{cards}</div>
<h2>Alliance Workflow</h2><div class="grid">{workflow}</div>
<h2>Team Operations</h2><div class="grid">{staff}</div>
<div class="footer">{MARKER} · {VERSION} · rendered {now}. Counts come from canonical master authorities; the legacy “17 requirements” counter is not used.</div>
</div></body></html>"""

def register(core):
    app = _app(core)
    eng = _engine(core)

    @app.middleware("http")
    async def canonical_dashboard_authority(request: Request, call_next):
        response = await call_next(request)
        if request.url.path != "/alliance/primary":
            return response
        if int(getattr(response, "status_code", 500)) != 200:
            return response
        try:
            counts = _counts(eng)
            STATE["status"] = "PASS"
            STATE["last_render_at"] = datetime.now(timezone.utc).isoformat()
            STATE["last_error"] = None
            return HTMLResponse(_render(counts), status_code=200, headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "X-Alliance-Dashboard-Authority": MARKER,
            })
        except Exception as exc:
            STATE["status"] = "ERROR"
            STATE["last_error"] = f"{type(exc).__name__}: {exc}"
            return response

    existing = {getattr(r, "path", None) for r in app.router.routes}

    if "/api/alliance/dashboard-authority-v1/public-status" not in existing:
        @app.get("/api/alliance/dashboard-authority-v1/public-status")
        def dashboard_authority_public_status():
            return {
                "status": STATE.get("status"),
                "version": VERSION,
                "dashboard_marker": MARKER,
                "legacy_dashboard_replaced": True,
                "matcher_authority": "MASTER_ONLY",
                "property_authority": "pi_master_properties_v711",
                "requirement_authority": "pi_requirement_gate_v1191",
                "contacts_scope": "AUTHENTICATED_STAFF_ONLY",
                "data_exposed": False,
            }

    if "/api/alliance/dashboard-authority-v1/status" not in existing:
        @app.get("/api/alliance/dashboard-authority-v1/status")
        def dashboard_authority_status(request: Request):
            try:
                core.need_login(request)
            except Exception:
                return JSONResponse({"detail":"Login required"}, status_code=401)
            return dict(STATE)

    STATE["status"] = "PASS"
    return dict(STATE)
