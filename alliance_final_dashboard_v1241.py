from __future__ import annotations

import html
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

VERSION = "12.4.1-FINAL-TEAM-DASHBOARD-AUDIT"

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

def _app(core):
    return getattr(core, "app", None) or core

def _engine(core):
    return getattr(core, "engine", None)

def _login(core, req):
    fn = getattr(core, "need_login", None)
    return fn(req) if fn else "team"

def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)

def _route_exists(app, path):
    base = str(path).split("?", 1)[0]
    return any(getattr(r, "path", None) == base for r in getattr(app.router, "routes", []))

def _methods(app, path):
    base = str(path).split("?", 1)[0]
    methods = set()
    for r in getattr(app.router, "routes", []):
        if getattr(r, "path", None) == base:
            methods.update(set(getattr(r, "methods", set()) or set()))
    return sorted(methods)

def _table_exists(engine, name):
    try:
        with engine.connect() as c:
            return bool(c.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": name}).scalar())
    except Exception:
        return False

def _register_commercial_if_needed(core):
    app = _app(core)
    if _route_exists(app, "/commercial-intelligence"):
        return {"status": "ALREADY_REGISTERED"}
    try:
        import alliance_commercial_intelligence_ai as commercial
        result = commercial.register(core)
        return {
            "status": "REGISTERED" if _route_exists(app, "/commercial-intelligence") else "NO_ROUTE",
            "result": result,
        }
    except Exception as exc:
        return {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}

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
                    "source": "MASTER",
                })
        except Exception:
            rows = []

    if rows:
        return rows

    if _table_exists(engine, "pi_properties"):
        try:
            with engine.connect() as c:
                data = c.execute(text("""
                    SELECT to_jsonb(p) AS d
                    FROM pi_properties p
                    WHERE LOWER(COALESCE(to_jsonb(p)::text,'')) ~ :pat
                    ORDER BY COALESCE(to_jsonb(p)->>'updated_at',to_jsonb(p)->>'created_at','') DESC
                    LIMIT :n
                """), {"pat": pattern, "n": int(limit)}).scalars().all()
            for d in data:
                d = d if isinstance(d, dict) else {}
                rows.append({
                    "canonical_id": d.get("property_id", ""),
                    "property_name": d.get("property_name") or "",
                    "city": d.get("city") or "",
                    "location": d.get("location") or "",
                    "property_type": d.get("property_type") or "",
                    "transaction": d.get("rent_or_sale") or "",
                    "area": d.get("available_area_sqft") or "",
                    "verification": d.get("verification_status") or "UNVERIFIED",
                    "availability": d.get("availability_status") or "UNKNOWN",
                    "source": "LEGACY",
                })
        except Exception:
            pass
    return rows

def _goa_page(rows):
    tr = []
    for r in rows:
        cid = str(r.get("canonical_id") or "")
        if cid and r.get("source") == "MASTER":
            action = f'<a class="btn" href="/alliance/primary/property/{_e(cid)}">Open</a>'
        else:
            action = '<a class="btn" href="/property-discovery?q=Goa">Search</a>'
        tr.append(
            "<tr>"
            f"<td>{_e(cid)}</td>"
            f"<td>{_e(r.get('property_name'))}</td>"
            f"<td>{_e(r.get('city'))}</td>"
            f"<td>{_e(r.get('location'))}</td>"
            f"<td>{_e(r.get('property_type'))}</td>"
            f"<td>{_e(r.get('transaction'))}</td>"
            f"<td>{_e(r.get('area'))}</td>"
            f"<td>{_e(r.get('verification'))}</td>"
            f"<td>{_e(r.get('availability'))}</td>"
            f"<td>{action}</td>"
            "</tr>"
        )
    rows_html = "".join(tr) or "<tr><td colspan='10'>No Goa-tagged properties found.</td></tr>"
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alliance Goa Properties</title><style>
body{{font-family:Arial;margin:0;background:#f4f7fb;color:#172033}}header{{background:#10223f;color:white;padding:18px 22px}}
.wrap{{padding:18px;max-width:1800px;margin:auto}}nav{{background:white;padding:10px;border-bottom:1px solid #dfe6ee}}
nav a,.btn{{background:#10223f;color:white;text-decoration:none;padding:8px 10px;border-radius:7px;margin:2px;display:inline-block}}
.card{{background:white;border:1px solid #dfe6ee;border-radius:12px;padding:14px;margin-bottom:12px}}
.tablebox{{overflow:auto;max-height:72vh}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{padding:8px;border-bottom:1px solid #edf0f4;text-align:left;vertical-align:top}}th{{background:#f8fafc;position:sticky;top:0}}
</style></head><body>
<header><b>Alliance Goa Property Intelligence</b><br><small>Master-first Goa inventory for team use</small></header>
<nav><a href="/alliance/primary">Command Centre</a><a href="/team-dashboard-v376">Dashboard</a><a href="/alliance/final/database/master">Master Property Database</a><a href="/property-discovery?q=Goa">Search Goa</a><a href="/commercial-intelligence">Commercial Intelligence</a><a href="/alliance/team-link-audit">Link Audit</a></nav>
<div class="wrap"><div class="card"><b>{len(rows)} Goa-tagged properties visible.</b><br>Master is preferred. Legacy rows appear only when Master has no Goa rows.</div>
<div class="card tablebox"><table><thead><tr><th>ID</th><th>Property</th><th>City</th><th>Location</th><th>Type</th><th>Transaction</th><th>Area</th><th>Verified</th><th>Availability</th><th>Action</th></tr></thead>
<tbody>{rows_html}</tbody></table></div></div></body></html>"""

def audit_snapshot(core):
    app = _app(core)
    engine = _engine(core)
    links = []
    missing = []
    for label, path, category in TEAM_LINKS:
        ok = _route_exists(app, path)
        links.append({
            "label": label,
            "path": path,
            "category": category,
            "route_registered": ok,
            "methods": _methods(app, path),
        })
        if not ok:
            missing.append(path)

    tables = {}
    for t in [
        "pi_master_properties_v711",
        "pi_master_requirements_v711",
        "pi_master_workflow_v720",
        "pi_master_matches_v720",
        "pi_master_action_state_v730",
        "pi_master_source_links_v711",
        "pi_requirement_gate_v1191",
        "aci_intel_assets",
    ]:
        tables[t] = _table_exists(engine, t)

    return {
        "status": "PASS" if not missing else "FAIL",
        "version": VERSION,
        "registered_routes_checked": len(TEAM_LINKS),
        "missing_routes": missing,
        "links": links,
        "database_dependencies": tables,
        "goa_properties_visible": len(_goa_rows(engine, 1000)),
        "commercial_intelligence_registered": _route_exists(app, "/commercial-intelligence"),
    }

def _audit_page(snapshot):
    link_rows = []
    for x in snapshot["links"]:
        status = "PASS" if x["route_registered"] else "MISSING"
        css = "ok" if x["route_registered"] else "bad"
        link_rows.append(
            f"<tr><td>{_e(x['category'])}</td><td>{_e(x['label'])}</td>"
            f"<td><a href='{_e(x['path'])}'>{_e(x['path'])}</a></td>"
            f"<td class='{css}'>{status}</td><td>{_e(', '.join(x['methods']))}</td></tr>"
        )
    db_rows = "".join(
        f"<tr><td>{_e(k)}</td><td class='{'ok' if v else 'bad'}'>{'PASS' if v else 'MISSING'}</td></tr>"
        for k, v in snapshot["database_dependencies"].items()
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alliance Final Dashboard Audit</title><style>
body{{font-family:Arial;margin:0;background:#f4f7fb;color:#172033}}header{{background:#10223f;color:white;padding:18px 22px}}
.wrap{{max-width:1700px;margin:auto;padding:18px}}.card{{background:white;border:1px solid #dfe6ee;border-radius:12px;padding:14px;margin-bottom:12px}}
table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{padding:8px;border-bottom:1px solid #edf0f4;text-align:left}}th{{background:#f8fafc}}
.ok{{color:#08783e;font-weight:800}}.bad{{color:#b42318;font-weight:800}}a{{color:#1849a9}}
</style></head><body><header><b>Alliance Final Team Dashboard Audit</b><br><small>{_e(VERSION)}</small></header>
<div class="wrap"><div class="card"><b>Overall: <span class="{'ok' if snapshot['status']=='PASS' else 'bad'}">{_e(snapshot['status'])}</span></b><br>
Routes checked: {snapshot['registered_routes_checked']} Â· Goa properties visible: {snapshot['goa_properties_visible']} Â· Commercial Intelligence: {'PASS' if snapshot['commercial_intelligence_registered'] else 'MISSING'}</div>
<div class="card"><h3>Team Links</h3><table><thead><tr><th>Category</th><th>Link</th><th>Path</th><th>Status</th><th>Methods</th></tr></thead><tbody>{''.join(link_rows)}</tbody></table></div>
<div class="card"><h3>Database Dependencies</h3><table><thead><tr><th>Table</th><th>Status</th></tr></thead><tbody>{db_rows}</tbody></table></div>
</div></body></html>"""

def register(core):
    app = _app(core)
    engine = _engine(core)
    if engine is None:
        raise RuntimeError("Final dashboard audit requires core.engine")

    commercial_result = _register_commercial_if_needed(core)

    if not _route_exists(app, "/alliance/goa-properties"):
        @app.get("/alliance/goa-properties", response_class=HTMLResponse, include_in_schema=False)
        def goa_properties(req: Request):
            _login(core, req)
            return HTMLResponse(_goa_page(_goa_rows(engine)), headers={"Cache-Control": "no-store"})

    if not _route_exists(app, "/api/alliance/final-dashboard-audit"):
        @app.get("/api/alliance/final-dashboard-audit")
        def final_dashboard_audit(req: Request):
            _login(core, req)
            return audit_snapshot(core)

    if not _route_exists(app, "/alliance/team-link-audit"):
        @app.get("/alliance/team-link-audit", response_class=HTMLResponse, include_in_schema=False)
        def team_link_audit(req: Request):
            _login(core, req)
            return HTMLResponse(_audit_page(audit_snapshot(core)), headers={"Cache-Control": "no-store"})

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "commercial_registration": commercial_result,
        "goa_route": _route_exists(app, "/alliance/goa-properties"),
        "audit_route": _route_exists(app, "/alliance/team-link-audit"),
    }
