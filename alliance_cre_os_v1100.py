from __future__ import annotations

import html
from typing import Any
from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

VERSION = "11.0.0-RESTORED-TEAM-DASHBOARD-INTEGRATED-5X5"
SOURCES = ("MASTER","NEWSPAPER","WHATSAPP","MAGAZINE","MANUAL")

def _app(core):
    return getattr(core, "app", None) or core

def _engine(core):
    return getattr(core, "engine", None)

def _login(core, req):
    fn = getattr(core, "need_login", None)
    return fn(req) if fn else "team"

def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v))

def _scalar(engine, sql, params=None, default=0):
    try:
        with engine.connect() as conn:
            v = conn.execute(text(sql), params or {}).scalar()
            return default if v is None else v
    except Exception:
        return default

def _source_count(engine, entity, source):
    table = "pi_master_properties_v711" if entity == "PROPERTY" else "pi_master_requirements_v711"
    alias = "p" if entity == "PROPERTY" else "r"
    if source == "MASTER":
        return int(_scalar(engine, f"SELECT COUNT(*) FROM {table}", default=0) or 0)
    pat = f"%{source}%"
    sql = f"""
    SELECT COUNT(DISTINCT {alias}.canonical_id)
    FROM {table} {alias}
    WHERE EXISTS (
      SELECT 1
      FROM pi_master_source_links_v711 l
      WHERE l.canonical_id={alias}.canonical_id
        AND l.master_entity_type=:entity
        AND (
          UPPER(COALESCE(l.source_type,'')) LIKE :pat OR
          UPPER(COALESCE(l.source_table,'')) LIKE :pat
        )
    )
    OR UPPER(COALESCE({alias}.clean_record->>'source','')) LIKE :pat
    OR UPPER(COALESCE({alias}.clean_record->>'source_type','')) LIKE :pat
    OR UPPER(COALESCE({alias}.clean_record->>'source_name','')) LIKE :pat
    OR UPPER(COALESCE({alias}.clean_record->>'channel','')) LIKE :pat
    OR UPPER(COALESCE({alias}.clean_record->>'import_source','')) LIKE :pat
    """
    return int(_scalar(engine, sql, {"entity":entity,"pat":pat}, 0) or 0)

def _metrics(engine):
    return {
        "properties": _source_count(engine,"PROPERTY","MASTER"),
        "requirements": _source_count(engine,"REQUIREMENT","MASTER"),
        "available": int(_scalar(engine, """
            SELECT COUNT(DISTINCT canonical_id)
            FROM pi_master_workflow_v720
            WHERE UPPER(COALESCE(availability_status,''))='AVAILABLE'
        """, default=0) or 0),
        "verified": int(_scalar(engine, """
            SELECT COUNT(DISTINCT canonical_id)
            FROM pi_master_workflow_v720
            WHERE UPPER(COALESCE(verification_status,''))='VERIFIED'
        """, default=0) or 0),
        "matches": int(_scalar(engine, "SELECT COUNT(*) FROM pi_master_matches_v720", default=0) or 0),
        "followups": int(_scalar(engine, """
            SELECT COUNT(*) FROM pi_master_action_state_v730
            WHERE UPPER(COALESCE(followup_status,''))='SCHEDULED'
        """, default=0) or 0),
    }

def _remove_get(app,path):
    app.router.routes[:] = [
        r for r in list(app.router.routes)
        if not (getattr(r,"path",None)==path and "GET" in set(getattr(r,"methods",set()) or set()))
    ]

def _move_front(app,path):
    found=[r for r in list(app.router.routes)
           if getattr(r,"path",None)==path and "GET" in set(getattr(r,"methods",set()) or set())]
    for r in found:
        try: app.router.routes.remove(r)
        except ValueError: pass
    for r in reversed(found):
        app.router.routes.insert(0,r)

def _tile(title, subtitle, url):
    return f"""<a class="tile" href="{url}">
      <div class="tiletitle">{_e(title)}</div>
      <div class="tilesub">{_e(subtitle)}</div>
      <div class="open">OPEN →</div>
    </a>"""

def _db_card(label, count, url, note=""):
    return f"""<a class="dbcard" href="{url}">
      <div class="dbtop">{_e(label)}</div>
      <div class="dbnum">{int(count)}</div>
      <div class="dbnote">{_e(note)}</div>
      <div class="dbopen">Open Database →</div>
    </a>"""

def _dashboard(engine):
    m=_metrics(engine)
    pc={s:_source_count(engine,"PROPERTY",s) for s in SOURCES}
    rc={s:_source_count(engine,"REQUIREMENT",s) for s in SOURCES}

    property_cards = "".join([
        _db_card("Master Database",pc["MASTER"],"/alliance/final/database/master","Canonical property database"),
        _db_card("Newspaper Database",pc["NEWSPAPER"],"/alliance/final/database/newspaper","Newspaper source view"),
        _db_card("WhatsApp Database",pc["WHATSAPP"],"/alliance/final/database/whatsapp","WhatsApp source view"),
        _db_card("Magazine Database",pc["MAGAZINE"],"/alliance/final/database/magazine","Magazine source view"),
        _db_card("Manual Database",pc["MANUAL"],"/alliance/final/database/manual","Manual source view"),
    ])
    requirement_cards = "".join([
        _db_card("Master Requirements",rc["MASTER"],"/alliance/final/requirements/master","Canonical demand database"),
        _db_card("Newspaper Requirements",rc["NEWSPAPER"],"/alliance/final/requirements/newspaper","Newspaper demand"),
        _db_card("WhatsApp Requirements",rc["WHATSAPP"],"/alliance/final/requirements/whatsapp","WhatsApp demand"),
        _db_card("Magazine Requirements",rc["MAGAZINE"],"/alliance/final/requirements/magazine","Magazine demand"),
        _db_card("Manual Requirements",rc["MANUAL"],"/alliance/final/requirements/manual","Manual demand"),
    ])

    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alliance CRE Intelligence OS 11</title>
<style>
:root{{--nav:#10223f;--nav2:#17345d;--bg:#efe4d2;--card:#fff;--line:#e1e7ee;--text:#1d2a3a;--muted:#6c7a8a;--blue:#2f6fed;}}
*{{box-sizing:border-box}}body{{margin:0;background:linear-gradient(180deg,#f7efe3 0%,#eadbc5 100%);font-family:Inter,Segoe UI,Arial,sans-serif;color:var(--text)}}
header{{background:linear-gradient(135deg,var(--nav),var(--nav2));color:#fff;padding:18px 24px;display:flex;justify-content:space-between;align-items:center;gap:20px;flex-wrap:wrap;box-shadow:0 6px 20px rgba(16,34,63,.18)}}
.brand b{{font-size:23px}}.brand small{{display:block;margin-top:4px;opacity:.88}}
.kpis{{display:grid;grid-template-columns:repeat(6,minmax(100px,1fr));gap:8px;flex:1;max-width:850px}}
.kpi{{background:#fff;color:var(--text);border-radius:12px;padding:9px 11px;min-width:96px;box-shadow:0 5px 16px rgba(0,0,0,.08)}}
.kpi span{{display:block;font-size:9px;color:var(--muted);text-transform:uppercase;font-weight:800;letter-spacing:.5px}}.kpi strong{{display:block;font-size:21px;color:#16315a;margin-top:2px}}
.wrap{{max-width:1700px;margin:auto;padding:18px}}
.tabs{{display:flex;gap:7px;flex-wrap:wrap;background:#fff;padding:10px;border:1px solid var(--line);border-radius:14px;box-shadow:0 8px 24px rgba(31,47,70,.08);margin-bottom:15px;position:sticky;top:0;z-index:30}}
.tabs a{{text-decoration:none;background:#f7f9fc;color:#1d2a3a;padding:9px 11px;border-radius:9px;font-size:12px;font-weight:800;border:1px solid #edf0f4}}
.tabs a:hover{{background:#eaf1ff;color:#1c4fb7}}
.card{{background:#fff;border:1px solid var(--line);border-radius:16px;padding:16px;margin-bottom:14px;box-shadow:0 8px 24px rgba(31,47,70,.08)}}
.card h2{{margin:0 0 4px;color:#16315a}}.card p{{margin:0 0 12px;color:var(--muted)}}
.quickgrid{{display:grid;grid-template-columns:repeat(6,minmax(150px,1fr));gap:9px}}
.tile{{text-decoration:none;color:var(--text);background:#fff;border:1px solid #dbe3ed;border-radius:13px;padding:13px;min-height:112px;display:flex;flex-direction:column;box-shadow:0 5px 16px rgba(31,47,70,.06)}}
.tile:hover{{transform:translateY(-1px);box-shadow:0 8px 20px rgba(31,47,70,.10)}}.tiletitle{{font-size:14px;font-weight:850;color:#16315a}}.tilesub{{font-size:11px;color:var(--muted);margin-top:5px;flex:1}}.open{{font-size:10px;font-weight:900;color:var(--blue);margin-top:8px}}
.dbgrid{{display:grid;grid-template-columns:repeat(5,minmax(175px,1fr));gap:8px}}.dbcard{{text-decoration:none;color:var(--text);background:#fbfcfe;border:1px solid #ccd7e4;border-radius:12px;padding:12px;min-height:132px}}
.dbtop{{font-weight:850;color:#16315a;font-size:13px}}.dbnum{{font-size:28px;font-weight:900;margin:5px 0}}.dbnote{{font-size:10px;color:var(--muted)}}.dbopen{{font-size:10px;font-weight:850;color:var(--blue);margin-top:10px}}
.flow{{display:grid;grid-template-columns:repeat(7,1fr);gap:7px}}.flow a{{text-decoration:none;color:var(--text);border:1px solid #dbe3ed;background:#fbfcfe;border-radius:11px;padding:11px;min-height:90px}}.flow b{{display:block;color:#16315a;margin-bottom:5px}}.flow small{{color:var(--muted)}}
.notice{{background:#fff9ec;border:1px solid #f2c86b;border-radius:11px;padding:10px;margin-top:10px;color:#6e5215}}
.sectiontitle{{display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap}}.sectiontitle a{{text-decoration:none;background:#17345d;color:white;padding:8px 10px;border-radius:8px;font-size:11px;font-weight:800}}
@media(max-width:1250px){{.quickgrid{{grid-template-columns:repeat(3,1fr)}}.dbgrid{{grid-template-columns:repeat(2,1fr)}}.flow{{grid-template-columns:repeat(2,1fr)}}.kpis{{grid-template-columns:repeat(3,1fr)}}}}
@media(max-width:700px){{.quickgrid,.dbgrid,.flow,.kpis{{grid-template-columns:1fr}}}}
</style></head><body>
<header>
  <div class="brand"><b>AI Deal Intelligence OS · CRE 11</b><small>Alliance Infrastructure · PROPERTY → VERIFY → REQUIREMENT → MATCH → CLIENT → FOLLOW-UP → DEAL</small></div>
  <div class="kpis">
    <div class="kpi"><span>Properties</span><strong>{m["properties"]}</strong></div>
    <div class="kpi"><span>Requirements</span><strong>{m["requirements"]}</strong></div>
    <div class="kpi"><span>Verified</span><strong>{m["verified"]}</strong></div>
    <div class="kpi"><span>Available</span><strong>{m["available"]}</strong></div>
    <div class="kpi"><span>Matches</span><strong>{m["matches"]}</strong></div>
    <div class="kpi"><span>Follow-ups</span><strong>{m["followups"]}</strong></div>
  </div>
</header>
<div class="wrap">
<nav class="tabs">
<a href="/team-dashboard-v376">Dashboard</a>
<a href="/workspace">Working Space</a>
<a href="/property-manual">Add Property</a>
<a href="/alliance/final/databases">Property Databases</a>
<a href="/requirements-workbench">Add Requirement</a>
<a href="/alliance/final/requirements">Requirement Databases</a>
<a href="/alliance/primary/availability">Verification</a>
<a href="/alliance/primary/matcher">Smart Matcher</a>
<a href="/alliance/primary/followups">Follow-ups</a>
<a href="/alliance/primary/reports">Deals & Reports</a>
<a href="/alliance/primary/contacts">Contacts</a>
<a href="/alliance/primary/ai-control">AI Control</a>
<a href="/alliance/primary/data-health">Data Health</a>
</nav>

<section class="card">
<div class="sectiontitle"><div><h2>Command Centre</h2><p>Previous team dashboard restored with the CRE canonical database and workflow integrated.</p></div></div>
<div class="quickgrid">
{_tile("Add Property","Manual property capture with owner/broker/contact and verification fields","/property-manual")}
{_tile("Newspaper Capture","Capture newspaper properties into the intelligence workflow","/capture-intelligence?source_type=NEWSPAPER")}
{_tile("WhatsApp Live","Live WhatsApp property intelligence and source management","/whatsapp-live")}
{_tile("Add Requirement","Create buyer, tenant or operator requirement","/requirements-workbench")}
{_tile("Verification Centre","Verify owner/broker availability before client use","/alliance/primary/availability")}
{_tile("Smart Matcher","Match requirements against Master Property Database only","/alliance/primary/matcher")}
{_tile("Client Shortlists","Review canonical master inventory for client options","/alliance/final/database/master")}
{_tile("Follow-ups","Callbacks, re-verification and requirement follow-up","/alliance/primary/followups")}
{_tile("Deals & Reports","Track visit, negotiation and closure","/alliance/primary/reports")}
{_tile("Hospitality Intelligence","Restaurants, cafes, clubs, lounges, banquets, hotels and guest houses","/hospitality-intelligence")}
{_tile("Retail Expansion","Retail expansion prospects and signals","/retail-expansion")}
{_tile("Requirement Discovery","Web-discovered property demand and requirements","/requirement-discovery")}
{_tile("Marketing Contacts","Owner, broker and business contact intelligence","/marketing-contacts")}
{_tile("AI Control","Bot and AI operating controls","/alliance/primary/ai-control")}
{_tile("Data Health","Canonical database and source health","/alliance/primary/data-health")}
{_tile("System Status","Runtime and deployment status","/status-page")}
{_tile("Source Recovery","Admin source recovery tools","/alliance/primary/source-recovery")}
{_tile("Property Search","Property discovery and search tools","/property-discovery")}
</div>
</section>

<section class="card">
<div class="sectiontitle"><div><h2>Property Databases</h2><p>Five operating views. Master is canonical; source databases preserve lineage.</p></div><a href="/alliance/final/databases">Open Property Database Hub</a></div>
<div class="dbgrid">{property_cards}</div>
</section>

<section class="card">
<div class="sectiontitle"><div><h2>Requirement Databases</h2><p>Five demand views feeding one canonical Master Requirements database.</p></div><a href="/alliance/final/requirements">Open Requirement Database Hub</a></div>
<div class="dbgrid">{requirement_cards}</div>
</section>

<section class="card">
<h2>Team Workflow</h2><p>One operating sequence for the Alliance CRE team.</p>
<div class="flow">
<a href="/property-manual"><b>1. CAPTURE</b><small>Add property from manual, newspaper, WhatsApp or magazine.</small></a>
<a href="/alliance/primary/availability"><b>2. VERIFY</b><small>Confirm availability with owner/broker before client use.</small></a>
<a href="/requirements-workbench"><b>3. REQUIREMENT</b><small>Capture tenant, buyer, retail or hospitality demand.</small></a>
<a href="/alliance/primary/matcher"><b>4. MATCH</b><small>Search Master Property Database only.</small></a>
<a href="/alliance/final/database/master"><b>5. CLIENT</b><small>Prepare client-safe options without internal contacts.</small></a>
<a href="/alliance/primary/followups"><b>6. FOLLOW-UP</b><small>Track callbacks, re-verification and responses.</small></a>
<a href="/alliance/primary/reports"><b>7. DEAL</b><small>Move to visit, negotiation, closure and reporting.</small></a>
</div>
<div class="notice"><b>Data protection:</b> CRE 11 does not delete, truncate, restore or overwrite PostgreSQL data. It restores the previous dashboard experience and reconnects it to current canonical databases and routes.</div>
</section>
</div></body></html>"""

def register(core):
    app=_app(core)
    engine=_engine(core)
    if app is None or engine is None:
        raise RuntimeError("CRE 11 requires FastAPI app and SQLAlchemy engine")

    for path in ("/team-dashboard-v376","/alliance/primary","/team-dashboard-live"):
        _remove_get(app,path)

    @app.get("/team-dashboard-v376",response_class=HTMLResponse,include_in_schema=False)
    def team_dashboard(req:Request):
        _login(core,req)
        return HTMLResponse(_dashboard(engine))

    @app.get("/alliance/primary",response_class=HTMLResponse,include_in_schema=False)
    def alliance_primary(req:Request):
        _login(core,req)
        return HTMLResponse(_dashboard(engine))

    @app.get("/team-dashboard-live",response_class=HTMLResponse,include_in_schema=False)
    def team_dashboard_live(req:Request):
        _login(core,req)
        return HTMLResponse(_dashboard(engine))

    for p in ("/team-dashboard-v376","/alliance/primary","/team-dashboard-live"):
        _move_front(app,p)

    return {
        "status":"REGISTERED",
        "version":VERSION,
        "dashboard":"/team-dashboard-v376",
        "command_centre":"/alliance/primary",
        "architecture":"PREVIOUS_TEAM_DASHBOARD + CRE10_CANONICAL_5X5 + VERIFY->MATCH->CLIENT->FOLLOWUP->DEAL",
        "database_policy":"NON_DESTRUCTIVE",
        "dummy_panels_removed":True,
        "route_count":len(app.router.routes),
    }
