from __future__ import annotations

import html
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION = "12.4.2-ROUTE-RESCUE-FINAL-AUDIT"

TEAM_LINKS = [
    ("Dashboard", "/team-dashboard-v376", "TEAM"),
    ("Working Space", "/workspace", "TEAM"),
    ("Add Property", "/property-manual", "PROPERTY"),
    ("Property Databases", "/alliance/final/databases", "PROPERTY"),
    ("Goa Properties", "/alliance/goa-properties", "PROPERTY"),
    ("Newspaper Capture", "/capture-intelligence", "PROPERTY"),
    ("WhatsApp Live", "/whatsapp-live", "PROPERTY"),
    ("Property Search", "/property-discovery", "PROPERTY"),
    ("Add Requirement", "/requirements-workbench", "REQUIREMENT"),
    ("Requirement Databases", "/alliance/final/requirements", "REQUIREMENT"),
    ("Verification Centre", "/alliance/primary/availability", "WORKFLOW"),
    ("Smart Matcher", "/alliance/primary/matcher", "WORKFLOW"),
    ("Client Master Database", "/alliance/final/database/master", "WORKFLOW"),
    ("Follow-ups", "/alliance/primary/followups", "WORKFLOW"),
    ("Deals & Reports", "/alliance/primary/reports", "WORKFLOW"),
    ("Contacts", "/alliance/primary/contacts", "INTELLIGENCE"),
    ("Hospitality Intelligence", "/hospitality-intelligence", "INTELLIGENCE"),
    ("Retail Expansion", "/retail-expansion", "INTELLIGENCE"),
    ("Commercial Intelligence", "/commercial-intelligence", "INTELLIGENCE"),
    ("Requirement Discovery", "/requirement-discovery", "INTELLIGENCE"),
    ("Marketing Contacts", "/marketing-contacts", "INTELLIGENCE"),
    ("AI Control", "/alliance/primary/ai-control", "SYSTEM"),
    ("Data Health", "/alliance/primary/data-health", "SYSTEM"),
    ("System Status", "/status-page", "SYSTEM"),
    ("Source Recovery", "/alliance/primary/source-recovery", "SYSTEM"),
    ("Requirement Restore Status", "/api/alliance/requirement-restore/status", "SYSTEM"),
    ("Commercial Intelligence Status", "/api/commercial-intelligence/status", "SYSTEM"),
]

REGISTRATION = {}

def _app(core):
    return getattr(core, "app", None) or core

def _engine(core):
    return getattr(core, "engine", None)

def _login(core, req):
    fn = getattr(core, "need_login", None)
    return fn(req) if fn else "team"

def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)

def _route_matches_template(template: str, actual: str) -> bool:
    tp = [x for x in str(template).strip("/").split("/") if x]
    ap = [x for x in str(actual).strip("/").split("/") if x]
    if len(tp) != len(ap):
        return False
    for t, a in zip(tp, ap):
        if t.startswith("{") and t.endswith("}"):
            continue
        if t != a:
            return False
    return True

def _route_exists(app, path):
    base = str(path).split("?", 1)[0]
    for r in getattr(app.router, "routes", []):
        rp = getattr(r, "path", None)
        if not rp:
            continue
        if rp == base or _route_matches_template(rp, base):
            return True
    return False

def _methods(app, path):
    base = str(path).split("?", 1)[0]
    methods = set()
    for r in getattr(app.router, "routes", []):
        rp = getattr(r, "path", None)
        if rp and (rp == base or _route_matches_template(rp, base)):
            methods.update(set(getattr(r, "methods", set()) or set()))
    return sorted(methods)

def _table_exists(engine, name):
    try:
        with engine.connect() as c:
            return bool(c.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": name}).scalar())
    except Exception:
        return False

def _safe_count(engine, sql):
    try:
        with engine.connect() as c:
            return int(c.execute(text(sql)).scalar() or 0)
    except Exception:
        return 0

def _try_register(label, fn):
    try:
        result = fn()
        REGISTRATION[label] = {"status": "OK", "result": str(result)[:600]}
    except Exception as exc:
        REGISTRATION[label] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}"[:1000],
        }

def _register_whatsapp(core):
    app = _app(core)
    if _route_exists(app, "/whatsapp-live"):
        REGISTRATION["whatsapp"] = {"status": "ALREADY_REGISTERED"}
        return
    def go():
        import alliance_v45_live_whatsapp_takeover as mod
        return mod.register(core)
    _try_register("whatsapp", go)

def _register_hospitality(core):
    app = _app(core)
    if not _route_exists(app, "/v3/hospitality-intelligence"):
        def go():
            import alliance_v31_hospitality as mod
            return mod.register_v31_hospitality_routes(core)
        _try_register("hospitality_module", go)
    else:
        REGISTRATION["hospitality_module"] = {"status": "ALREADY_REGISTERED"}

    if not _route_exists(app, "/hospitality-intelligence"):
        @app.get("/hospitality-intelligence", include_in_schema=False)
        def hospitality_alias(req: Request):
            _login(core, req)
            return RedirectResponse("/v3/hospitality-intelligence", status_code=302)

def _commercial_fallback_page(engine):
    rows = []
    if _table_exists(engine, "aci_intel_assets"):
        try:
            with engine.connect() as c:
                rows = [dict(x) for x in c.execute(text("""
                    SELECT asset_code,asset_name,asset_class,city,location,
                           developer_or_authority,lifecycle_status,confidence,
                           last_researched_at,visibility_status,purity_score
                    FROM aci_intel_assets
                    WHERE COALESCE(visibility_status,'ACTIVE')='ACTIVE'
                    ORDER BY COALESCE(purity_score,0) DESC,
                             last_researched_at DESC NULLS LAST,
                             updated_at DESC
                    LIMIT 400
                """)).mappings().all()]
        except Exception:
            rows = []
    trs = []
    for r in rows:
        trs.append(
            "<tr>"
            f"<td>{_e(r.get('asset_name'))}</td>"
            f"<td>{_e(r.get('asset_class'))}</td>"
            f"<td>{_e(r.get('city'))}</td>"
            f"<td>{_e(r.get('location'))}</td>"
            f"<td>{_e(r.get('developer_or_authority'))}</td>"
            f"<td>{_e(r.get('lifecycle_status'))}</td>"
            f"<td>{_e(r.get('confidence'))}</td>"
            f"<td>{_e(r.get('last_researched_at'))}</td>"
            "</tr>"
        )
    body = "".join(trs) or "<tr><td colspan='8'>No active commercial intelligence assets found.</td></tr>"
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Commercial Intelligence</title><style>
body{{font-family:Arial;margin:0;background:#f4f7fb;color:#172033}}header{{background:#10223f;color:#fff;padding:18px 22px}}
nav{{background:white;padding:10px;border-bottom:1px solid #dfe6ee}}nav a{{display:inline-block;margin:2px;padding:8px 10px;background:#10223f;color:#fff;text-decoration:none;border-radius:7px}}
.wrap{{max-width:1800px;margin:auto;padding:18px}}.card{{background:#fff;border:1px solid #dfe6ee;border-radius:12px;padding:14px;margin-bottom:12px}}
.tablebox{{overflow:auto;max-height:72vh}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{padding:8px;border-bottom:1px solid #edf0f4;text-align:left;vertical-align:top}}th{{background:#f8fafc;position:sticky;top:0}}
</style></head><body><header><b>Alliance Commercial Intelligence</b><br><small>Malls, commercial premises, government opportunities and leasing intelligence</small></header>
<nav><a href="/alliance/primary">Command Centre</a><a href="/alliance/goa-properties">Goa Properties</a><a href="/retail-expansion">Retail Expansion</a><a href="/alliance/team-link-audit">Link Audit</a></nav>
<div class="wrap"><div class="card"><b>{len(rows)} active intelligence assets visible.</b><br>
This safe fallback is read-only and uses the existing ACI intelligence database. No commercial records are changed.</div>
<div class="card tablebox"><table><thead><tr><th>Asset</th><th>Class</th><th>City</th><th>Location</th><th>Developer / Authority</th><th>Status</th><th>Confidence</th><th>Last Researched</th></tr></thead><tbody>{body}</tbody></table></div></div></body></html>"""

def _register_commercial(core):
    app = _app(core)
    engine = _engine(core)
    if _route_exists(app, "/commercial-intelligence"):
        REGISTRATION["commercial_module"] = {"status": "ALREADY_REGISTERED"}
        return

    def go():
        import alliance_commercial_intelligence_ai as mod
        return mod.register(core)
    _try_register("commercial_module", go)

    # If the full module cannot register, keep the team surface usable and expose the error.
    if not _route_exists(app, "/commercial-intelligence"):
        @app.get("/commercial-intelligence", response_class=HTMLResponse, include_in_schema=False)
        def commercial_fallback(req: Request):
            _login(core, req)
            return HTMLResponse(_commercial_fallback_page(engine), headers={"Cache-Control": "no-store"})

    if not _route_exists(app, "/api/commercial-intelligence/status"):
        @app.get("/api/commercial-intelligence/status")
        def commercial_status_fallback(req: Request):
            _login(core, req)
            return {
                "status": "DEGRADED_FALLBACK" if REGISTRATION.get("commercial_module", {}).get("status") == "ERROR" else "OK",
                "version": VERSION,
                "full_module_registration": REGISTRATION.get("commercial_module", {}),
                "active_assets": _safe_count(engine, "SELECT COUNT(*) FROM aci_intel_assets WHERE COALESCE(visibility_status,'ACTIVE')='ACTIVE'"),
                "brands_observed": _safe_count(engine, "SELECT COUNT(*) FROM aci_intel_brands"),
                "public_contacts": _safe_count(engine, "SELECT COUNT(*) FROM aci_intel_contacts"),
                "vacancy_signals": _safe_count(engine, "SELECT COUNT(*) FROM aci_intel_vacancies"),
                "fallback_read_only": True,
            }

def _reports_page(engine):
    counts = {
        "properties": _safe_count(engine, "SELECT COUNT(*) FROM pi_master_properties_v711"),
        "requirements": _safe_count(engine, "SELECT COUNT(*) FROM pi_master_requirements_v711"),
        "matches": _safe_count(engine, "SELECT COUNT(*) FROM pi_master_matches_v720"),
        "approved_matches": _safe_count(engine, "SELECT COUNT(*) FROM pi_master_matches_v720 WHERE UPPER(COALESCE(status,''))='APPROVED'"),
        "followups": _safe_count(engine, "SELECT COUNT(*) FROM pi_master_action_state_v730 WHERE UPPER(COALESCE(followup_status,''))='SCHEDULED'"),
        "assigned": _safe_count(engine, "SELECT COUNT(*) FROM pi_master_action_state_v730 WHERE COALESCE(assigned_to,'')<>''"),
    }
    stages = []
    try:
        with engine.connect() as c:
            stages = c.execute(text("""
                SELECT COALESCE(stage,'NEW') stage,COUNT(*) n
                FROM pi_master_action_state_v730
                GROUP BY COALESCE(stage,'NEW')
                ORDER BY COUNT(*) DESC
            """)).all()
    except Exception:
        pass
    stage_html = "".join(f"<tr><td>{_e(s)}</td><td>{int(n)}</td></tr>" for s,n in stages) or "<tr><td>No workflow stage data</td><td>0</td></tr>"
    cards = "".join(f"<div class='k'><small>{_e(k.replace('_',' ').title())}</small><b>{v}</b></div>" for k,v in counts.items())
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Deals & Reports</title><style>
body{{font-family:Arial;margin:0;background:#f4f7fb;color:#172033}}header{{background:#10223f;color:white;padding:18px 22px}}
nav,.wrap{{padding:12px 18px}}nav{{background:white;border-bottom:1px solid #dfe6ee}}nav a{{background:#10223f;color:white;text-decoration:none;padding:8px 10px;border-radius:7px;margin:2px;display:inline-block}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}}.k,.card{{background:white;border:1px solid #dfe6ee;border-radius:12px;padding:14px}}.k b{{display:block;font-size:28px;margin-top:4px}}
table{{border-collapse:collapse;width:100%}}th,td{{padding:8px;border-bottom:1px solid #edf0f4;text-align:left}}
</style></head><body><header><b>Alliance Deals & Reports</b><br><small>Live canonical workflow summary</small></header>
<nav><a href="/alliance/primary">Command Centre</a><a href="/alliance/primary/matcher">Matcher</a><a href="/alliance/primary/followups">Follow-ups</a><a href="/alliance/team-link-audit">Link Audit</a></nav>
<div class="wrap"><div class="grid">{cards}</div><br><div class="card"><h3>Workflow Stages</h3><table><thead><tr><th>Stage</th><th>Records</th></tr></thead><tbody>{stage_html}</tbody></table></div></div></body></html>"""

def _register_reports(core):
    app = _app(core)
    engine = _engine(core)
    if _route_exists(app, "/alliance/primary/reports"):
        REGISTRATION["reports"] = {"status": "ALREADY_REGISTERED"}
        return
    @app.get("/alliance/primary/reports", response_class=HTMLResponse, include_in_schema=False)
    def reports(req: Request):
        _login(core, req)
        return HTMLResponse(_reports_page(engine), headers={"Cache-Control": "no-store"})
    REGISTRATION["reports"] = {"status": "FALLBACK_REGISTERED"}

def _requirement_discovery_page(engine):
    rows = []
    if _table_exists(engine, "ai_requirement_index"):
        try:
            with engine.connect() as c:
                rows = [dict(x) for x in c.execute(text("""
                    SELECT to_jsonb(r) d
                    FROM ai_requirement_index r
                    ORDER BY COALESCE(to_jsonb(r)->>'updated_at',to_jsonb(r)->>'created_at','') DESC
                    LIMIT 300
                """)).scalars().all()]
        except Exception:
            rows = []
    trs = []
    for d in rows:
        d = d if isinstance(d, dict) else {}
        req = d.get("requirement") or d.get("original_message") or d.get("requirement_text") or d.get("description") or ""
        loc = d.get("preferred_locations_raw") or d.get("location") or d.get("locations") or ""
        company = d.get("company_name") or d.get("brand_name") or d.get("client_name") or ""
        code = d.get("requirement_code") or d.get("requirement_id") or d.get("id") or ""
        trs.append(f"<tr><td>{_e(code)}</td><td>{_e(company)}</td><td>{_e(loc)}</td><td>{_e(req)}</td></tr>")
    rows_html = "".join(trs) or "<tr><td colspan='4'>No indexed requirement discovery rows found.</td></tr>"
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Requirement Discovery</title><style>
body{{font-family:Arial;margin:0;background:#f4f7fb;color:#172033}}header{{background:#10223f;color:white;padding:18px 22px}}
nav{{background:white;padding:10px;border-bottom:1px solid #dfe6ee}}nav a{{background:#10223f;color:white;text-decoration:none;padding:8px 10px;border-radius:7px;margin:2px;display:inline-block}}
.wrap{{max-width:1800px;margin:auto;padding:18px}}.card{{background:white;border:1px solid #dfe6ee;border-radius:12px;padding:14px}}.tablebox{{overflow:auto;max-height:72vh}}
table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{padding:8px;border-bottom:1px solid #edf0f4;text-align:left;vertical-align:top}}th{{background:#f8fafc;position:sticky;top:0}}
</style></head><body><header><b>Alliance Requirement Discovery</b><br><small>Indexed demand evidence requiring verification before Matcher</small></header>
<nav><a href="/alliance/primary">Command Centre</a><a href="/alliance/requirements-gate">Requirement Gate</a><a href="/alliance/final/requirements">Requirement Databases</a><a href="/alliance/team-link-audit">Link Audit</a></nav>
<div class="wrap"><div class="card"><b>{len(rows)} discovery/index rows shown.</b><br>These are evidence records. Human verification remains mandatory before Master Matcher use.</div><br>
<div class="card tablebox"><table><thead><tr><th>ID</th><th>Company / Brand</th><th>Location</th><th>Requirement / Evidence</th></tr></thead><tbody>{rows_html}</tbody></table></div></div></body></html>"""

def _register_requirement_discovery(core):
    app = _app(core)
    engine = _engine(core)
    # Register external-discovery APIs when absent.
    if not _route_exists(app, "/api/v2/intelligence/v28/discover/{action_id}"):
        def go():
            import alliance_v28_external_discovery as mod
            return mod.register_v28_routes(core)
        _try_register("requirement_discovery_api", go)
    else:
        REGISTRATION["requirement_discovery_api"] = {"status": "ALREADY_REGISTERED"}

    if not _route_exists(app, "/requirement-discovery"):
        @app.get("/requirement-discovery", response_class=HTMLResponse, include_in_schema=False)
        def requirement_discovery(req: Request):
            _login(core, req)
            return HTMLResponse(_requirement_discovery_page(engine), headers={"Cache-Control": "no-store"})

def _goa_rows(engine, limit=1000):
    rows = []
    pattern = "(goa|siolim|assagao|anjuna|vagator|morjim|mandrem|mapusa|porvorim|candolim|calangute|panjim|panaji|dona paula|bardez)"
    if _table_exists(engine, "pi_master_properties_v711"):
        try:
            with engine.connect() as c:
                data = c.execute(text("""
                    SELECT to_jsonb(p) AS d,
                           COALESCE(w.verification_status,'UNVERIFIED') verification_status,
                           COALESCE(w.availability_status,'UNKNOWN') availability_status
                    FROM pi_master_properties_v711 p
                    LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=p.canonical_id
                    WHERE LOWER(COALESCE(to_jsonb(p)::text,'')) ~ :pat
                    ORDER BY COALESCE(to_jsonb(p)->>'updated_at',to_jsonb(p)->>'created_at','') DESC
                    LIMIT :n
                """), {"pat": pattern, "n": int(limit)}).mappings().all()
            for x in data:
                d = x["d"] if isinstance(x["d"], dict) else {}
                rows.append({
                    "canonical_id": d.get("canonical_id", ""),
                    "property_name": d.get("property_name") or d.get("name") or "",
                    "city": d.get("city") or "",
                    "location": d.get("locality") or d.get("location") or "",
                    "property_type": d.get("property_type") or "",
                    "transaction": d.get("transaction_type") or d.get("rent_or_sale") or "",
                    "area": d.get("area_sqft") or d.get("available_area_sqft") or "",
                    "verification": x.get("verification_status") or "UNVERIFIED",
                    "availability": x.get("availability_status") or "UNKNOWN",
                })
        except Exception:
            pass
    return rows

def _goa_page(rows):
    trs = []
    for r in rows:
        cid = str(r.get("canonical_id") or "")
        action = f'<a class="btn" href="/alliance/primary/property/{_e(cid)}">Open</a>' if cid else ""
        trs.append("<tr>" + "".join([
            f"<td>{_e(cid)}</td>", f"<td>{_e(r.get('property_name'))}</td>",
            f"<td>{_e(r.get('city'))}</td>", f"<td>{_e(r.get('location'))}</td>",
            f"<td>{_e(r.get('property_type'))}</td>", f"<td>{_e(r.get('transaction'))}</td>",
            f"<td>{_e(r.get('area'))}</td>", f"<td>{_e(r.get('verification'))}</td>",
            f"<td>{_e(r.get('availability'))}</td>", f"<td>{action}</td>"
        ]) + "</tr>")
    rows_html = "".join(trs) or "<tr><td colspan='10'>No Goa-tagged Master properties found.</td></tr>"
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Goa Properties</title>
<style>body{{font-family:Arial;margin:0;background:#f4f7fb;color:#172033}}header{{background:#10223f;color:white;padding:18px}}nav{{background:white;padding:10px}}nav a,.btn{{background:#10223f;color:white;text-decoration:none;padding:8px 10px;border-radius:7px;margin:2px;display:inline-block}}.wrap{{padding:18px;max-width:1800px;margin:auto}}.card{{background:white;border:1px solid #ddd;padding:12px}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{padding:8px;border-bottom:1px solid #eee;text-align:left}}</style></head>
<body><header><b>Alliance Goa Property Intelligence</b></header><nav><a href="/alliance/primary">Command Centre</a><a href="/property-discovery?q=Goa">Search Goa</a><a href="/commercial-intelligence">Commercial Intelligence</a></nav>
<div class="wrap"><div class="card"><b>{len(rows)} Goa Master properties visible.</b></div><br><div class="card"><table><thead><tr><th>ID</th><th>Property</th><th>City</th><th>Location</th><th>Type</th><th>Transaction</th><th>Area</th><th>Verified</th><th>Availability</th><th>Action</th></tr></thead><tbody>{rows_html}</tbody></table></div></div></body></html>"""

def audit_snapshot(core):
    app = _app(core)
    engine = _engine(core)
    links, missing = [], []
    for label, path, category in TEAM_LINKS:
        ok = _route_exists(app, path)
        links.append({
            "label": label, "path": path, "category": category,
            "route_registered": ok, "methods": _methods(app, path),
        })
        if not ok:
            missing.append(path)

    tables = {}
    for t in [
        "pi_master_properties_v711", "pi_master_requirements_v711",
        "pi_master_workflow_v720", "pi_master_matches_v720",
        "pi_master_action_state_v730", "pi_master_source_links_v711",
        "pi_requirement_gate_v1191", "aci_intel_assets",
    ]:
        tables[t] = _table_exists(engine, t)

    db_missing = [k for k,v in tables.items() if not v]
    return {
        "status": "PASS" if not missing and not db_missing else "FAIL",
        "version": VERSION,
        "registered_routes_checked": len(TEAM_LINKS),
        "missing_routes": missing,
        "missing_database_dependencies": db_missing,
        "links": links,
        "database_dependencies": tables,
        "goa_properties_visible": len(_goa_rows(engine, 1000)),
        "commercial_intelligence_registered": _route_exists(app, "/commercial-intelligence"),
        "registration_diagnostics": REGISTRATION,
    }

def _audit_page(snapshot):
    link_rows = []
    for x in snapshot["links"]:
        ok = x["route_registered"]
        link_rows.append(
            f"<tr><td>{_e(x['category'])}</td><td>{_e(x['label'])}</td>"
            f"<td><a href='{_e(x['path'])}'>{_e(x['path'])}</a></td>"
            f"<td class='{'ok' if ok else 'bad'}'>{'PASS' if ok else 'MISSING'}</td>"
            f"<td>{_e(', '.join(x['methods']))}</td></tr>"
        )
    db_rows = "".join(
        f"<tr><td>{_e(k)}</td><td class='{'ok' if v else 'bad'}'>{'PASS' if v else 'MISSING'}</td></tr>"
        for k,v in snapshot["database_dependencies"].items()
    )
    reg_rows = "".join(
        f"<tr><td>{_e(k)}</td><td>{_e(v.get('status'))}</td><td>{_e(v.get('error') or v.get('result') or '')}</td></tr>"
        for k,v in snapshot["registration_diagnostics"].items()
    ) or "<tr><td colspan='3'>No registration diagnostics.</td></tr>"
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Final Dashboard Audit</title>
<style>body{{font-family:Arial;margin:0;background:#f4f7fb;color:#172033}}header{{background:#10223f;color:#fff;padding:18px}}.wrap{{padding:18px;max-width:1800px;margin:auto}}.card{{background:white;border:1px solid #ddd;padding:14px;margin-bottom:12px}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{padding:8px;border-bottom:1px solid #eee;text-align:left;vertical-align:top}}.ok{{color:#08783e;font-weight:800}}.bad{{color:#b42318;font-weight:800}}</style></head>
<body><header><b>Alliance Final Team Dashboard Audit</b><br><small>{_e(VERSION)}</small></header><div class="wrap">
<div class="card"><b>Overall: <span class="{'ok' if snapshot['status']=='PASS' else 'bad'}">{_e(snapshot['status'])}</span></b><br>Routes checked: {snapshot['registered_routes_checked']} · Goa properties visible: {snapshot['goa_properties_visible']} · Commercial Intelligence: {'PASS' if snapshot['commercial_intelligence_registered'] else 'MISSING'}</div>
<div class="card"><h3>Team Links</h3><table><thead><tr><th>Category</th><th>Link</th><th>Path</th><th>Status</th><th>Methods</th></tr></thead><tbody>{''.join(link_rows)}</tbody></table></div>
<div class="card"><h3>Database Dependencies</h3><table><tbody>{db_rows}</tbody></table></div>
<div class="card"><h3>Registration Diagnostics</h3><table><thead><tr><th>Module</th><th>Status</th><th>Detail</th></tr></thead><tbody>{reg_rows}</tbody></table></div>
</div></body></html>"""

def register(core):
    app = _app(core)
    engine = _engine(core)
    if engine is None:
        raise RuntimeError("Final dashboard audit requires core.engine")

    # Repair genuine missing registrations first.
    _register_whatsapp(core)
    _register_hospitality(core)
    _register_commercial(core)
    _register_reports(core)
    _register_requirement_discovery(core)

    if not _route_exists(app, "/alliance/goa-properties"):
        @app.get("/alliance/goa-properties", response_class=HTMLResponse, include_in_schema=False)
        def goa_properties(req: Request):
            _login(core, req)
            return HTMLResponse(_goa_page(_goa_rows(engine)), headers={"Cache-Control": "no-store"})

    # Replace old audit routes so this version owns the final result.
    app.router.routes[:] = [
        r for r in list(app.router.routes)
        if getattr(r, "path", None) not in {
            "/api/alliance/final-dashboard-audit",
            "/alliance/team-link-audit",
        }
    ]

    @app.get("/api/alliance/final-dashboard-audit")
    def final_dashboard_audit(req: Request):
        _login(core, req)
        return audit_snapshot(core)

    @app.get("/alliance/team-link-audit", response_class=HTMLResponse, include_in_schema=False)
    def team_link_audit(req: Request):
        _login(core, req)
        return HTMLResponse(_audit_page(audit_snapshot(core)), headers={"Cache-Control": "no-store"})

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "audit": audit_snapshot(core),
    }
