from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Any, Dict

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

VERSION = "2.3.0-CLEAN-SOURCE-NAVIGATION"
MARKER = "CANONICAL_ALLIANCE_DASHBOARD_V1"
STATE: Dict[str, Any] = {
    "status": "INIT",
    "version": VERSION,
    "authority": "CANONICAL_MASTER_DATA",
    "dashboard_marker": MARKER,
    "legacy_dashboard_replaced": True,
    "navigation_model": "8-AREA-CLEAN-SOURCE-HUB",
    "matcher_authority": "MASTER_ONLY",
    "property_authority": "pi_master_properties_v711",
    "requirement_authority": "pi_requirement_gate_v1191",
    "contacts_scope": "AUTHENTICATED_STAFF_ONLY",
    "feature_freeze": "ACTIVE",
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
        "matcher_eligible": _scalar(engine, "SELECT COUNT(*) FROM pi_requirement_gate_v1191 WHERE matcher_eligible IS TRUE"),
        "manual_requirements": _scalar(engine, "SELECT COUNT(*) FROM pi_requirement_gate_v1191 WHERE UPPER(COALESCE(source_type,'')) LIKE '%MANUAL%'"),
        "whatsapp_requirements": _scalar(engine, "SELECT COUNT(*) FROM pi_requirement_gate_v1191 WHERE UPPER(COALESCE(source_type,'')) LIKE '%WHATSAPP%'"),
        "newspaper_requirements": _scalar(engine, "SELECT COUNT(*) FROM pi_requirement_gate_v1191 WHERE UPPER(COALESCE(source_type,'')) LIKE '%NEWSPAPER%' OR UPPER(COALESCE(source_type,'')) LIKE '%MAGAZINE%'"),
        "contact_master": _scalar(engine, "SELECT COUNT(*) FROM pi_alliance_contact_master_v1"),
    }

def _n(v):
    return "—" if v is None else f"{int(v):,}"

def _area(title, icon, stat, desc, href, action, tone):
    return f"""
    <a class="area {tone}" href="{escape(href)}">
      <div class="area-top"><div class="icon">{icon}</div><div class="stat">{escape(str(stat))}</div></div>
      <div class="area-title">{escape(title)}</div>
      <div class="area-desc">{escape(desc)}</div>
      <div class="area-action">{escape(action)} →</div>
    </a>"""

def _mini(title, href, desc):
    return f"""
    <a class="mini" href="{escape(href)}">
      <strong>{escape(title)}</strong>
      <span>{escape(desc)}</span>
    </a>"""

def _render(counts):
    now = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

    primary = "".join([
        _area("Properties", "🏢", _n(counts["master_properties"]), "Master properties, availability, Goa inventory and new property entry.", "/alliance/final/databases", "Open Properties", "blue"),
        _area("Requirements", "📋", _n(counts["master_requirements"]), "One canonical demand workspace for manual, WhatsApp and newspaper requirements.", "/alliance/final/requirements", "Open Requirements", "purple"),
        _area("Match & Deal Desk", "🎯", _n(counts["matcher_eligible"]), "Run Smart Match, review exact / verify / alternate options and prepare the client draft.", "/alliance/primary/smart-match", "Open Smart Match", "green"),
        _area("Intelligence", chr(0x2728), "3 sections", "Commercial, Hospitality and Retail intelligence with bot controls.", "#intelligence", "View Intelligence", "orange"),
        _area("WhatsApp Live", chr(0x1F4AC), "Live", "Live WhatsApp source, contacts, availability and requirement capture.", "/whatsapp-live", "Open WhatsApp Live", "green"),
        _area("Contacts", "☎", _n(counts["contact_master"]), "Evidence-backed internal contact master with source segregation.", "/alliance/primary/contact-master", "Open Contacts", "cyan"),
        _area("Team", "👥", "Today", "Follow-ups, day plans, staff review, monthly review and reports.", "#team", "View Team", "pink"),
        _area("System", "🛡", "PASS", "Guardian, data health, diagnostics and production link audit.", "#system", "View Health", "slate"),
    ])

    intelligence = """<a class="mini" href="/commercial-intelligence"><strong>Commercial Intelligence</strong><span>Research assets and review the commercial intelligence database</span></a><div class="mini"><strong>Hospitality Intelligence</strong><span>Review saved hospitality intelligence</span><a class="subbtn" href="/hospitality-intelligence#bot-controls">Run Hospitality Bot</a><a class="subbtn lightbtn" href="/hospitality-intelligence#intelligence-database">Open Intelligence</a></div><div class="mini"><strong>Retail Intelligence</strong><span>Review retail contacts, expansion signals and requirements</span><a class="subbtn" href="/retail-expansion#bot-controls">Run Retail Bot</a><a class="subbtn lightbtn" href="/retail-expansion#intelligence-database">Open Intelligence</a></div>"""

    team = "".join([
        _mini("Team Tasks", "/alliance/primary/followups", "Sorted work queue: overdue, due today, upcoming, then completed"),
        _mini("Day Plan", "/alliance/primary/day-plan", "Staff priorities and assignments for today"),
        _mini("Daily Staff Performance", "/alliance/primary/staff-review", "Tasks assigned, completed, overdue, follow-ups and last activity"),
        _mini("Monthly Staff Performance", "/alliance/primary/monthly-review", "Task completion and follow-up performance by staff member"),
    ])

    system = "".join([
        _mini("Data Health", "/alliance/primary/data-health", "Production data-health workspace"),
        _mini("System Doctor", "/alliance/system-doctor", "Runtime diagnostics"),
        _mini("Link Audit", "/alliance/team-link-audit", "Production route audit"),
    ])

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alliance · Command Centre</title>
<style>
:root{{
 --bg:#f5f7fb;--panel:#fff;--ink:#182235;--muted:#6b7586;--line:#e6eaf0;
 --shadow:0 10px 30px rgba(30,44,70,.07)
}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}
body{{margin:0;background:linear-gradient(180deg,#f8faff 0,#f5f7fb 240px);color:var(--ink);font:15px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
.wrap{{max-width:1240px;margin:auto;padding:28px 20px 70px}}
.header{{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;margin-bottom:22px}}
.eyebrow{{font-size:12px;letter-spacing:.09em;text-transform:uppercase;font-weight:800;color:#6f7d92;margin-bottom:7px}}
h1{{font-size:34px;line-height:1.15;margin:0 0 8px}}h2{{font-size:21px;margin:34px 0 13px}}.muted{{color:var(--muted)}}
.badges{{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}}.badge{{background:#fff;border:1px solid var(--line);padding:8px 11px;border-radius:999px;font-size:12px;font-weight:800;white-space:nowrap}}
.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}}
.area{{text-decoration:none;color:inherit;background:var(--panel);border:1px solid var(--line);border-radius:20px;padding:18px;min-height:190px;box-shadow:var(--shadow);transition:.18s ease}}
.area:hover{{transform:translateY(-3px);box-shadow:0 16px 38px rgba(30,44,70,.11)}}
.area-top{{display:flex;align-items:center;justify-content:space-between;gap:10px}}.icon{{font-size:27px}}.stat{{font-size:13px;font-weight:850;padding:6px 9px;border-radius:999px;background:#f4f6fa}}
.area-title{{font-size:20px;font-weight:850;margin-top:18px}}.area-desc{{color:var(--muted);font-size:13px;margin-top:6px;min-height:58px}}.area-action{{font-weight:800;margin-top:12px}}
.blue{{border-top:4px solid #3b82f6}}.purple{{border-top:4px solid #8b5cf6}}.green{{border-top:4px solid #10b981}}.orange{{border-top:4px solid #f59e0b}}
.cyan{{border-top:4px solid #06b6d4}}.pink{{border-top:4px solid #ec4899}}.slate{{border-top:4px solid #64748b}}.gold{{border-top:4px solid #eab308}}
.section{{background:#fff;border:1px solid var(--line);border-radius:20px;padding:18px;box-shadow:var(--shadow)}}
.mini-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}}
.mini{{display:block;text-decoration:none;color:inherit;border:1px solid var(--line);border-radius:14px;padding:14px;background:#fbfcfe}}
.mini:hover{{background:#fff;border-color:#bac4d2}}.mini strong{{display:block;margin-bottom:3px}}.mini span{{display:block;color:var(--muted);font-size:12.5px}}
.subbtn{{display:inline-block;margin:10px 6px 0 0;padding:8px 10px;border-radius:8px;background:#3157d5;color:white!important;text-decoration:none;font-size:12px;font-weight:800}}.lightbtn{{background:#e9eefb;color:#2445b5!important}}
.flow{{display:flex;align-items:center;justify-content:center;gap:8px;flex-wrap:wrap;background:#10223f;color:white;border-radius:16px;padding:14px;margin-top:26px;font-size:12px;font-weight:850}}.flow b{{background:rgba(255,255,255,.1);padding:7px 9px;border-radius:8px}}.flow i{{font-style:normal;color:#8fb4ff}}
.quick{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.quick a{{text-decoration:none;color:#fff;border-radius:16px;padding:17px 18px;font-weight:850;background:#182235}}
.quick a:last-child{{background:#3157d5}}
.rule{{margin-top:20px;padding:13px 15px;border-radius:14px;background:#eef4ff;color:#32415b;font-size:13px}}
.footer{{margin-top:32px;color:var(--muted);font-size:12px}}
@media(max-width:950px){{.grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}.mini-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
@media(max-width:620px){{.header{{display:block}}.badges{{justify-content:flex-start;margin-top:12px}}.grid,.mini-grid,.quick{{grid-template-columns:1fr}}h1{{font-size:28px}}}}
</style>
</head>
<body data-alliance-dashboard-authority="{MARKER}">
<div class="wrap">
  <div class="header">
    <div>
      <div class="eyebrow">Alliance Infrastructure</div>
      <h1>Command Centre</h1>
      <div class="muted">Everything the team needs, grouped into simple workflows.</div>
    </div>
    <div class="badges">
      <div class="badge">MASTER-ONLY MATCHER</div>
      <div class="badge">FEATURE FREEZE ACTIVE</div>
      <div class="badge">STAFF ONLY</div>
    </div>
  </div>

  <div class="grid">{primary}</div>

  <div class="flow"><b>PROPERTY</b><i>&rarr;</i><b>VERIFY</b><i>&rarr;</i><b>REQUIREMENT</b><i>&rarr;</i><b>MATCH</b><i>&rarr;</i><b>CLIENT</b><i>&rarr;</i><b>FOLLOW-UP</b><i>&rarr;</i><b>DEAL</b></div>
  <h2 id="quick-add">Quick Add</h2>
  <div class="quick">
    <a href="/property-manual">＋ Add Inventory</a>
    <a href="/requirements-workbench">＋ Add Requirement</a>
  </div>

  <h2 id="intelligence">Intelligence Sources</h2>
  <div class="section"><div class="mini-grid">{intelligence}</div></div>

  <h2 id="team">Team</h2>
  <div class="section"><div class="mini-grid">{team}</div></div>

  <h2 id="system">System Health</h2>
  <div class="section"><div class="mini-grid">{system}</div></div>

  <div class="rule"><strong>Simple navigation rule:</strong> legacy URLs remain available for compatibility, but the dashboard exposes only the grouped workflows above. Availability stays a Master Property workflow; Smart Match stays MASTER_ONLY; contacts stay internal.</div>
  <div class="footer">{MARKER} · {VERSION} · rendered {now}</div>
</div>
</body>
</html>"""

def register(core):
    app = _app(core)
    eng = _engine(core)

    # Canonical dashboard owns /alliance/primary as a real route, not as
    # response-replacing middleware. Remove every pre-existing GET owner first.
    kept=[]
    for route in app.router.routes:
        methods=set(getattr(route,"methods",set()) or set())
        if getattr(route,"path",None)=="/alliance/primary" and "GET" in methods:
            continue
        kept.append(route)
    app.router.routes[:] = kept

    @app.get("/alliance/primary", response_class=HTMLResponse)
    def canonical_dashboard_home(request: Request):
        core.need_login(request)
        counts = _counts(eng)
        STATE["status"] = "PASS"
        STATE["last_render_at"] = datetime.now(timezone.utc).isoformat()
        STATE["last_error"] = None
        return HTMLResponse(_render(counts), status_code=200, headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-Alliance-Dashboard-Authority": MARKER,
            "X-Alliance-Navigation-Model": "8-AREA-CLEAN-SOURCE-HUB",
        })

    existing = {getattr(r, "path", None) for r in app.router.routes}

    if "/api/alliance/dashboard-authority-v1/public-status" not in existing:
        @app.get("/api/alliance/dashboard-authority-v1/public-status")
        def dashboard_authority_public_status():
            matches=[]
            for r in app.router.routes:
                methods=set(getattr(r,"methods",set()) or set())
                if getattr(r,"path",None)=="/alliance/primary" and "GET" in methods:
                    ep=getattr(r,"endpoint",None)
                    matches.append({
                        "module":getattr(ep,"__module__",""),
                        "name":getattr(ep,"__name__",""),
                    })
            active=matches[0] if matches else {}
            expected=(active.get("module")=="alliance_dashboard_authority_v1" and active.get("name")=="canonical_dashboard_home")
            return {
                "status":"PASS" if expected else "FAIL",
                "version": VERSION,
                "dashboard_marker": MARKER,
                "actual_active_owner":active,
                "matching_route_count":len(matches),
                "render_title":"Alliance · Command Centre",
                "render_sections":["Properties","Requirements","Match & Deal","Intelligence","Contacts","Team","System","Quick Add"],
                "legacy_dashboard_replaced": expected,
                "navigation_model": "8-AREA-CLEAN-SOURCE-HUB",
                "matcher_authority": "MASTER_ONLY",
                "property_authority": "pi_master_properties_v711",
                "requirement_authority": "pi_requirement_gate_v1191",
                "contacts_scope": "AUTHENTICATED_STAFF_ONLY",
                "feature_freeze": "ACTIVE",
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
