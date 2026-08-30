from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timezone
from urllib.parse import quote_plus

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from starlette.routing import request_response

VERSION = "1.0.0-ALLIANCE-COMMERCIAL-INTELLIGENCE"

SCHEMA = r'''
CREATE TABLE IF NOT EXISTS aci_sources(
    id BIGSERIAL PRIMARY KEY,
    source_code TEXT UNIQUE NOT NULL,
    source_name TEXT NOT NULL,
    source_category TEXT NOT NULL,
    organization_name TEXT,
    state_coverage TEXT,
    base_url TEXT,
    access_mode TEXT DEFAULT 'MANUAL_OR_PUBLIC_SEARCH',
    automation_status TEXT DEFAULT 'MANUAL_FIRST',
    reliability_band TEXT DEFAULT 'MEDIUM',
    volume_band TEXT DEFAULT 'UNKNOWN',
    legal_access_note TEXT,
    active BOOLEAN DEFAULT TRUE,
    last_checked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS aci_discoveries(
    id BIGSERIAL PRIMARY KEY,
    discovery_code TEXT UNIQUE NOT NULL,
    discovery_fingerprint TEXT UNIQUE NOT NULL,
    asset_name TEXT,
    title TEXT,
    asset_type TEXT DEFAULT 'COMMERCIAL',
    lifecycle_status TEXT DEFAULT 'UNKNOWN',
    city TEXT,
    state TEXT,
    location TEXT,
    developer_company TEXT,
    related_company TEXT,
    contact_name TEXT,
    contact_phone TEXT,
    contact_email TEXT,
    source_code TEXT,
    source_name TEXT,
    source_url TEXT,
    source_provider TEXT,
    raw_text TEXT,
    source_confidence TEXT DEFAULT 'REPORTED',
    research_status TEXT DEFAULT 'NEW',
    assigned_to TEXT,
    linked_property_code TEXT,
    discovered_at TIMESTAMPTZ DEFAULT NOW(),
    last_researched_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS aci_companies(
    id BIGSERIAL PRIMARY KEY,
    company_code TEXT UNIQUE NOT NULL,
    company_name TEXT NOT NULL,
    company_type TEXT DEFAULT 'UNKNOWN',
    website TEXT,
    city TEXT,
    state TEXT,
    source_url TEXT,
    verification_status TEXT DEFAULT 'REPORTED',
    strategic_relevance TEXT DEFAULT 'REVIEW',
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS aci_contacts(
    id BIGSERIAL PRIMARY KEY,
    contact_code TEXT UNIQUE NOT NULL,
    company_code TEXT,
    discovery_code TEXT,
    contact_name TEXT,
    designation TEXT,
    phone TEXT,
    email TEXT,
    contact_type TEXT DEFAULT 'PUBLIC_BUSINESS',
    provenance_status TEXT DEFAULT 'PUBLIC_BUSINESS',
    source_url TEXT,
    verification_status TEXT DEFAULT 'UNVERIFIED',
    last_verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS aci_tasks(
    id BIGSERIAL PRIMARY KEY,
    task_code TEXT UNIQUE NOT NULL,
    discovery_code TEXT,
    task_type TEXT NOT NULL,
    title TEXT NOT NULL,
    assigned_to TEXT,
    status TEXT DEFAULT 'OPEN',
    priority TEXT DEFAULT 'NORMAL',
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS aci_coverage_checks(
    id BIGSERIAL PRIMARY KEY,
    city TEXT NOT NULL,
    source_category TEXT NOT NULL,
    source_code TEXT,
    checked_by TEXT,
    checked_at TIMESTAMPTZ DEFAULT NOW(),
    result_note TEXT,
    UNIQUE(city, source_category, source_code)
);

CREATE TABLE IF NOT EXISTS aci_evidence(
    id BIGSERIAL PRIMARY KEY,
    discovery_code TEXT NOT NULL,
    field_name TEXT NOT NULL,
    field_value TEXT,
    source_url TEXT,
    provenance_status TEXT DEFAULT 'REPORTED',
    captured_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_aci_disc_status ON aci_discoveries(research_status, discovered_at DESC);
CREATE INDEX IF NOT EXISTS idx_aci_disc_city ON aci_discoveries(city, asset_type);
CREATE INDEX IF NOT EXISTS idx_aci_task_status ON aci_tasks(status, assigned_to);
CREATE INDEX IF NOT EXISTS idx_aci_contact_phone ON aci_contacts(phone);
'''

SOURCE_SEEDS = [
    ("DMRC", "DMRC Property / Tender Intelligence", "TRANSIT", "Delhi Metro Rail Corporation", "Delhi NCR", "https://delhimetrorail.com", "MANUAL_FIRST", "HIGH", "Transit commercial spaces, tenders, licensees and awards."),
    ("DDA", "DDA Commercial Properties", "DEVELOPMENT_AUTHORITY", "Delhi Development Authority", "Delhi", "https://dda.gov.in/commercial-properties", "MANUAL_FIRST", "HIGH", "Commercial plots, shops, auctions and allotments."),
    ("HRERA-GRG", "HRERA Gurugram", "RERA", "Haryana RERA Gurugram", "Haryana", "https://haryanarera.gov.in", "SEMI_AUTOMATABLE", "HIGH", "Upcoming and registered projects; verify commercial relevance."),
    ("HRERA-PKL", "HRERA Panchkula", "RERA", "Haryana RERA Panchkula", "Haryana", "https://haryanarera.gov.in", "SEMI_AUTOMATABLE", "HIGH", "Separate Haryana bench; use as independent source."),
    ("UPRERA", "UP RERA", "RERA", "Uttar Pradesh RERA", "Uttar Pradesh", "https://www.up-rera.in", "SEMI_AUTOMATABLE", "HIGH", "Registered/upcoming commercial and mixed-use projects."),
    ("NOIDA", "Noida Authority", "DEVELOPMENT_AUTHORITY", "Noida Authority", "Uttar Pradesh", "https://noidaauthorityonline.in", "MANUAL_FIRST", "HIGH", "Commercial allotments, tenders and development signals."),
    ("GNIDA", "Greater Noida Authority", "DEVELOPMENT_AUTHORITY", "Greater Noida Authority", "Uttar Pradesh", "https://www.greaternoidaauthority.in", "MANUAL_FIRST", "HIGH", "Commercial land, schemes, auctions and tenders."),
    ("YEIDA", "YEIDA", "DEVELOPMENT_AUTHORITY", "Yamuna Expressway Industrial Development Authority", "Uttar Pradesh", "https://www.yamunaexpresswayauthority.com", "MANUAL_FIRST", "HIGH", "Commercial plots, institutional and airport-corridor development."),
    ("RLDA", "Rail Land Development Authority", "TRANSIT", "RLDA", "North India", "https://rlda.indianrailways.gov.in", "MANUAL_FIRST", "HIGH", "Railway land, station redevelopment and commercial leasing."),
    ("IREPS", "Indian Railways E-Procurement", "TRANSIT", "Indian Railways", "North India", "https://www.ireps.gov.in", "MANUAL_FIRST", "MEDIUM", "Railway tenders and commercial opportunities."),
    ("AAI", "Airports Authority of India", "AIRPORT", "AAI", "North India", "https://www.aai.aero", "MANUAL_FIRST", "HIGH", "Airport retail/F&B/commercial concessions."),
    ("CPPP", "Central Public Procurement Portal", "GOV_TENDER", "Government of India", "India", "https://eprocure.gov.in", "MANUAL_FIRST", "MEDIUM", "Public tenders and awards; filter aggressively for commercial relevance."),
    ("MSTC", "MSTC E-Auction", "GOV_AUCTION", "MSTC", "India", "https://www.mstcecommerce.com", "MANUAL_FIRST", "MEDIUM", "Government e-auction and monetisation signals."),
    ("RAJRERA", "Rajasthan RERA", "RERA", "Rajasthan RERA", "Rajasthan", "https://rera.rajasthan.gov.in", "SEMI_AUTOMATABLE", "HIGH", "Registered commercial and mixed-use projects."),
    ("RIICO", "RIICO", "INDUSTRIAL_AUTHORITY", "RIICO", "Rajasthan", "https://riico.rajasthan.gov.in", "MANUAL_FIRST", "MEDIUM", "Industrial/commercial plots and corridor development."),
    ("PUBLIC-WEB", "Public Web / Search Providers", "PUBLIC_WEB", "Alliance Search Waterfall", "North India", "", "AUTOMATABLE", "MEDIUM", "Jina/LangSearch/Tavily/Brave/Google CSE when configured; every result remains REPORTED until verified."),
    ("TEAM", "Alliance Team / Offline Signal", "OFFLINE", "Alliance Infrastructure", "North India", "", "MANUAL", "HIGH", "Site boards, broker calls, site visits and team intelligence."),
]

MODE_QUERIES = {
    "EXISTING": [
        'shopping mall commercial complex {city}',
        'high street retail commercial building {city}',
        'SCO retail development {city}',
    ],
    "UPCOMING": [
        'upcoming mall commercial project {city}',
        'pre leasing commercial retail project {city}',
        'under construction shopping centre mixed use {city}',
    ],
    "GOVERNMENT": [
        'commercial property tender auction allotment {city}',
        'metro railway commercial space tender {city}',
        'development authority commercial plot auction {city}',
    ],
    "LEASING": [
        'leasing mandate mall commercial project {city}',
        'appointed leasing partner retail project {city}',
        'pre leasing retail space {city}',
    ],
    "HIRING": [
        'leasing head mall hiring {city}',
        'mall general manager hiring {city}',
        'retail leasing manager hiring {city}',
    ],
}


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _slug(value):
    return re.sub(r"[^a-z0-9]+", " ", _clean(value).lower()).strip()


def _code(prefix, seed):
    return prefix + "-" + hashlib.sha1(seed.encode("utf-8", "ignore")).hexdigest()[:12].upper()


def _phone_from_text(value):
    text_value = str(value or "")
    found = []
    for match in re.finditer(r"(?<!\d)(?:\+?91[\s-]?)?([6-9](?:[\s-]?\d){9})(?!\d)", text_value):
        digits = re.sub(r"\D", "", match.group(1))
        if len(digits) == 10 and digits not in found:
            found.append(digits)
    return ", ".join(found[:3]) or None


def _email_from_text(value):
    m = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", str(value or ""), re.I)
    return m.group(0) if m else None


def _source_category_from_url(url):
    low = str(url or "").lower()
    if "rera" in low: return "RERA"
    if "delhimetrorail" in low or "dmrc" in low: return "TRANSIT"
    if "indianrailways" in low or "ireps" in low or "rlda" in low: return "TRANSIT"
    if "dda.gov" in low or "authority" in low or ".gov.in" in low: return "GOVERNMENT"
    if "linkedin.com" in low: return "PUBLIC_LINKEDIN"
    return "PUBLIC_WEB"


def _fingerprint(asset_name, city, source_url, raw_text):
    key = "|".join([_slug(asset_name), _slug(city), _clean(source_url).lower(), _slug(raw_text)[:180]])
    return hashlib.sha256(key.encode("utf-8", "ignore")).hexdigest()


def _page_role(core, req):
    fn = getattr(core, "page_role_or_redirect", None)
    if callable(fn):
        return fn(req)
    get_role = getattr(core, "get_role", None)
    if callable(get_role):
        return get_role(req)
    try:
        core.need_login(req)
        return "team"
    except Exception:
        return None


def _actor(core, req):
    fn = getattr(core, "actor_name", None)
    if callable(fn):
        return fn(req)
    return req.headers.get("x-user-name") or _page_role(core, req) or "team"


def ensure_schema(engine):
    with engine.begin() as c:
        for stmt in [x.strip() for x in SCHEMA.split(";") if x.strip()]:
            c.execute(text(stmt))
        for code, name, category, org, coverage, url, auto, reliability, note in SOURCE_SEEDS:
            c.execute(text('''
                INSERT INTO aci_sources(source_code,source_name,source_category,organization_name,state_coverage,base_url,automation_status,reliability_band,legal_access_note)
                VALUES(:code,:name,:cat,:org,:coverage,:url,:auto,:rel,:note)
                ON CONFLICT(source_code) DO UPDATE SET
                    source_name=EXCLUDED.source_name,
                    source_category=EXCLUDED.source_category,
                    organization_name=EXCLUDED.organization_name,
                    state_coverage=EXCLUDED.state_coverage,
                    base_url=EXCLUDED.base_url,
                    automation_status=EXCLUDED.automation_status,
                    reliability_band=EXCLUDED.reliability_band,
                    legal_access_note=EXCLUDED.legal_access_note,
                    updated_at=NOW()
            '''), {"code":code,"name":name,"cat":category,"org":org,"coverage":coverage,"url":url,"auto":auto,"rel":reliability,"note":note})


def _create_task_if_missing(c, discovery_code, task_type, title, priority="NORMAL"):
    existing = c.execute(text('''SELECT id FROM aci_tasks WHERE discovery_code=:d AND task_type=:t AND status='OPEN' LIMIT 1'''), {"d":discovery_code,"t":task_type}).scalar()
    if existing:
        return
    task_code = _code("TASK", f"{discovery_code}|{task_type}|{title}")
    c.execute(text('''INSERT INTO aci_tasks(task_code,discovery_code,task_type,title,priority) VALUES(:tc,:dc,:tt,:title,:p) ON CONFLICT(task_code) DO NOTHING'''), {"tc":task_code,"dc":discovery_code,"tt":task_type,"title":title,"p":priority})


def upsert_discovery(engine, *, asset_name=None, title=None, asset_type="COMMERCIAL", lifecycle_status="UNKNOWN", city=None, state=None, location=None, developer_company=None, related_company=None, contact_name=None, contact_phone=None, contact_email=None, source_code=None, source_name=None, source_url=None, source_provider=None, raw_text=None, source_confidence="REPORTED"):
    asset_name = _clean(asset_name or title)[:500]
    title = _clean(title or asset_name)[:1000]
    raw_text = _clean(raw_text)[:12000]
    source_url = _clean(source_url)[:3000]
    fp = _fingerprint(asset_name, city, source_url, raw_text)
    dc = _code("DISC", fp)
    contact_phone = _clean(contact_phone or _phone_from_text(" ".join([title, raw_text]))) or None
    contact_email = _clean(contact_email or _email_from_text(" ".join([title, raw_text]))) or None
    with engine.begin() as c:
        c.execute(text('''
            INSERT INTO aci_discoveries(
              discovery_code,discovery_fingerprint,asset_name,title,asset_type,lifecycle_status,city,state,location,
              developer_company,related_company,contact_name,contact_phone,contact_email,source_code,source_name,
              source_url,source_provider,raw_text,source_confidence,research_status)
            VALUES(:dc,:fp,:asset,:title,:atype,:life,:city,:state,:loc,:dev,:company,:cname,:phone,:email,:scode,:sname,:url,:provider,:raw,:conf,'NEW')
            ON CONFLICT(discovery_fingerprint) DO UPDATE SET
              contact_phone=COALESCE(aci_discoveries.contact_phone,EXCLUDED.contact_phone),
              contact_email=COALESCE(aci_discoveries.contact_email,EXCLUDED.contact_email),
              developer_company=COALESCE(NULLIF(aci_discoveries.developer_company,''),EXCLUDED.developer_company),
              source_provider=COALESCE(NULLIF(aci_discoveries.source_provider,''),EXCLUDED.source_provider),
              updated_at=NOW()
        '''), {"dc":dc,"fp":fp,"asset":asset_name,"title":title,"atype":_clean(asset_type) or "COMMERCIAL","life":_clean(lifecycle_status) or "UNKNOWN","city":_clean(city),"state":_clean(state),"loc":_clean(location),"dev":_clean(developer_company),"company":_clean(related_company),"cname":_clean(contact_name),"phone":contact_phone,"email":contact_email,"scode":_clean(source_code),"sname":_clean(source_name),"url":source_url,"provider":_clean(source_provider),"raw":raw_text,"conf":_clean(source_confidence) or "REPORTED"})
        actual = c.execute(text("SELECT discovery_code FROM aci_discoveries WHERE discovery_fingerprint=:fp"), {"fp":fp}).scalar() or dc
        if source_url:
            c.execute(text('''INSERT INTO aci_evidence(discovery_code,field_name,field_value,source_url,provenance_status) VALUES(:d,'DISCOVERY_SOURCE',:v,:u,:p)'''), {"d":actual,"v":title or asset_name,"u":source_url,"p":source_confidence})
        if not contact_phone and not contact_email:
            _create_task_if_missing(c, actual, "CONTACT_RESEARCH", f"Find leasing/business contact for {asset_name or title}", "HIGH")
        if not developer_company and not related_company:
            _create_task_if_missing(c, actual, "COMPANY_RESEARCH", f"Identify developer/owner/operator for {asset_name or title}")
    return actual


def _run_search(engine, city, mode, custom_query=""):
    try:
        import property_discovery
    except Exception as exc:
        return {"created":0,"provider_log":[{"provider":"SYSTEM","status":"IMPORT_ERROR","error":str(exc)}]}
    queries = []
    if _clean(custom_query):
        queries.append(_clean(custom_query))
    else:
        for template in MODE_QUERIES.get(mode, MODE_QUERIES["UPCOMING"]):
            queries.append(template.format(city=_clean(city) or "North India"))
    try:
        rows, provider_log = property_discovery.search_waterfall(queries[:3], deep=False)
    except Exception as exc:
        return {"created":0,"provider_log":[{"provider":"SYSTEM","status":"SEARCH_ERROR","error":str(exc)}]}
    created = 0
    for row in rows[:80]:
        title = _clean(row.get("title") or row.get("name"))
        url = _clean(row.get("url"))
        snippet = _clean(row.get("snippet") or row.get("description") or row.get("content"))
        if not title and not snippet:
            continue
        category = _source_category_from_url(url)
        source_code = "PUBLIC-WEB"
        for code, _, _, _, _, seed_url, _, _, _ in SOURCE_SEEDS:
            if seed_url and seed_url.lower().replace("https://","").replace("http://","").split("/")[0] in url.lower():
                source_code = code
                break
        lifecycle = "UNKNOWN"
        low = (title + " " + snippet).lower()
        if any(x in low for x in ["upcoming","under construction","pre-leasing","pre leasing","launch"]): lifecycle = "UPCOMING"
        elif any(x in low for x in ["operational","open now","shopping mall","commercial centre","commercial center"]): lifecycle = "EXISTING_OR_OPERATIONAL"
        before = None
        fp = _fingerprint(title, city, url, snippet)
        with engine.connect() as c:
            before = c.execute(text("SELECT id FROM aci_discoveries WHERE discovery_fingerprint=:f"), {"f":fp}).scalar()
        upsert_discovery(engine, asset_name=title, title=title, asset_type=category, lifecycle_status=lifecycle, city=city, source_code=source_code, source_name=category, source_url=url, source_provider=row.get("source_provider"), raw_text=snippet, source_confidence="REPORTED")
        if before is None:
            created += 1
    return {"created":created,"results_seen":len(rows),"provider_log":provider_log}


def _promote(engine, discovery_code, actor):
    with engine.begin() as c:
        d = c.execute(text("SELECT * FROM aci_discoveries WHERE discovery_code=:d"), {"d":discovery_code}).mappings().first()
        if not d:
            raise HTTPException(404, "Discovery not found")
        if d.get("linked_property_code"):
            return d.get("linked_property_code")
        exists = c.execute(text("SELECT to_regclass('public.alliance_canonical_properties') IS NOT NULL")).scalar()
        if not exists:
            raise HTTPException(409, "Canonical property database is not ready yet")
        seed = "|".join([_slug(d.get("asset_name")),_slug(d.get("city")),_slug(d.get("location")),_slug(d.get("developer_company") or d.get("related_company"))])
        fp = hashlib.sha256(seed.encode()).hexdigest()
        pc = _code("PROP", fp)
        c.execute(text('''
            INSERT INTO alliance_canonical_properties(property_code,canonical_fingerprint,property_name,canonical_location,city,building_project,property_type,transaction_type,active)
            VALUES(:pc,:fp,:name,:loc,:city,:building,:ptype,'LEASE',TRUE)
            ON CONFLICT(canonical_fingerprint) DO UPDATE SET updated_at=NOW(),active=TRUE
        '''), {"pc":pc,"fp":fp,"name":d.get("asset_name") or d.get("title") or discovery_code,"loc":d.get("location") or d.get("city") or "UNKNOWN","city":d.get("city") or "UNKNOWN","building":d.get("asset_name") or d.get("title"),"ptype":d.get("asset_type") or "COMMERCIAL"})
        pc = c.execute(text("SELECT property_code FROM alliance_canonical_properties WHERE canonical_fingerprint=:f"), {"f":fp}).scalar() or pc
        if c.execute(text("SELECT to_regclass('public.alliance_property_listings') IS NOT NULL")).scalar():
            lc = _code("LIST", discovery_code)
            c.execute(text('''
                INSERT INTO alliance_property_listings(listing_code,property_code,source_type,source_table,source_record_id,source_name,raw_text,availability_status,verification_status,verification_confidence,captured_at,active)
                VALUES(:lc,:pc,'COMMERCIAL_INTELLIGENCE','aci_discoveries',:rid,:sn,:raw,'UNKNOWN','REPORTED',60,NOW(),TRUE)
                ON CONFLICT(source_type,source_table,source_record_id) DO UPDATE SET property_code=EXCLUDED.property_code,updated_at=NOW(),active=TRUE
            '''), {"lc":lc,"pc":pc,"rid":discovery_code,"sn":d.get("source_name") or d.get("source_code") or "Commercial Intelligence","raw":d.get("raw_text") or ""})
        c.execute(text("UPDATE aci_discoveries SET linked_property_code=:pc,research_status='VERIFIED',last_researched_at=NOW(),updated_at=NOW() WHERE discovery_code=:d"), {"pc":pc,"d":discovery_code})
        c.execute(text("UPDATE aci_tasks SET status='DONE',completed_at=NOW(),updated_at=NOW() WHERE discovery_code=:d AND task_type IN ('PROPERTY_VERIFY','PROPERTY_RESEARCH')"), {"d":discovery_code})
        return pc


def _esc(value):
    return html.escape(str(value or ""))


def _dashboard_link_patch(app):
    marker = "commercial-intelligence-dashboard-link-v1"
    if getattr(app.state, marker.replace("-", "_"), False):
        return "ALREADY_PATCHED"
    workspace_route = None
    for route in app.router.routes:
        if getattr(route, "path", None) == "/workspace" and hasattr(route, "dependant"):
            workspace_route = route
            break
    if workspace_route is None:
        return "WORKSPACE_ROUTE_NOT_FOUND"
    original = workspace_route.dependant.call

    def wrapped_workspace(*args, **kwargs):
        response = original(*args, **kwargs)
        if isinstance(response, HTMLResponse):
            try:
                body = response.body.decode("utf-8")
                if "/commercial-intelligence" not in body:
                    link = '<a class="nav" href="/commercial-intelligence">Commercial Intelligence<small>Discover properties, companies, public contacts, sources & research tasks</small></a>'
                    needle = '<div class="wrap"><div class="navs">'
                    if needle in body:
                        body = body.replace(needle, needle + link, 1)
                    else:
                        body = body.replace('</header>', '</header><div style="padding:12px 18px"><a href="/commercial-intelligence">Commercial Intelligence</a></div>', 1)
                    return HTMLResponse(body, status_code=response.status_code, headers={k:v for k,v in response.headers.items() if k.lower() not in {"content-length","content-type"}})
            except Exception:
                return response
        return response

    workspace_route.endpoint = wrapped_workspace
    workspace_route.dependant.call = wrapped_workspace
    workspace_route.app = request_response(workspace_route.get_route_handler())
    setattr(app.state, marker.replace("-", "_"), True)
    return "PATCHED"


def _render_dashboard(engine, role, message=""):
    with engine.connect() as c:
        counts = c.execute(text('''SELECT
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE research_status='NEW') AS new_count,
          COUNT(*) FILTER (WHERE lifecycle_status ILIKE '%UPCOMING%') AS upcoming,
          COUNT(*) FILTER (WHERE COALESCE(contact_phone,'')<>'' OR COALESCE(contact_email,'')<>'') AS contactable,
          COUNT(*) FILTER (WHERE linked_property_code IS NOT NULL) AS promoted
          FROM aci_discoveries''')).mappings().first()
        tasks_open = c.execute(text("SELECT COUNT(*) FROM aci_tasks WHERE status='OPEN'")).scalar() or 0
        sources_due = c.execute(text("SELECT COUNT(*) FROM aci_sources WHERE active=TRUE AND (last_checked_at IS NULL OR last_checked_at < NOW()-INTERVAL '30 days')")).scalar() or 0
        discoveries = c.execute(text("SELECT * FROM aci_discoveries ORDER BY discovered_at DESC LIMIT 120")).mappings().all()
        tasks = c.execute(text("SELECT * FROM aci_tasks WHERE status='OPEN' ORDER BY CASE priority WHEN 'HIGH' THEN 1 ELSE 2 END, created_at LIMIT 80")).mappings().all()
        sources = c.execute(text("SELECT * FROM aci_sources WHERE active=TRUE ORDER BY source_category,source_name")).mappings().all()
        coverage = c.execute(text("SELECT city,source_category,source_code,checked_by,checked_at,result_note FROM aci_coverage_checks ORDER BY checked_at DESC LIMIT 80")).mappings().all()

    rows = []
    for d in discoveries:
        action = ''
        if not d.get('linked_property_code'):
            action += f'<form method="post" action="/commercial-intelligence/discovery/{_esc(d.get("discovery_code"))}/promote" style="display:inline"><button class="mini good" type="submit">Promote</button></form> '
        action += f'<form method="post" action="/commercial-intelligence/discovery/{_esc(d.get("discovery_code"))}/status" style="display:inline"><input type="hidden" name="status" value="RESEARCH"><button class="mini" type="submit">Research</button></form>'
        rows.append(f'''<tr><td><b>{_esc(d.get('asset_name') or d.get('title'))}</b><br><small>{_esc(d.get('discovery_code'))}</small></td><td>{_esc(d.get('city'))}</td><td>{_esc(d.get('asset_type'))}</td><td>{_esc(d.get('lifecycle_status'))}</td><td>{_esc(d.get('developer_company') or d.get('related_company'))}</td><td>{_esc(d.get('contact_name'))}<br>{_esc(d.get('contact_phone'))}<br>{_esc(d.get('contact_email'))}</td><td>{_esc(d.get('source_code') or d.get('source_name'))}<br><a href="{_esc(d.get('source_url'))}" target="_blank">source</a></td><td>{_esc(d.get('research_status'))}</td><td>{action}</td></tr>''')

    task_rows = ''.join(f'''<tr><td>{_esc(t.get('priority'))}</td><td><b>{_esc(t.get('title'))}</b><br><small>{_esc(t.get('discovery_code'))}</small></td><td>{_esc(t.get('assigned_to') or 'Unassigned')}</td><td><form method="post" action="/commercial-intelligence/task/{t.get('id')}/done"><button class="mini good" type="submit">Done</button></form></td></tr>''' for t in tasks)
    source_cards = ''.join(f'''<div class="source"><b>{_esc(s.get('source_name'))}</b><small>{_esc(s.get('source_category'))} · {_esc(s.get('state_coverage'))}</small><p>{_esc(s.get('legal_access_note'))}</p><div><span class="badge">{_esc(s.get('automation_status'))}</span> <span class="badge">{_esc(s.get('reliability_band'))}</span></div><p><a href="{_esc(s.get('base_url'))}" target="_blank">Open source</a></p><form method="post" action="/commercial-intelligence/source/{s.get('id')}/checked"><input name="city" placeholder="City checked" required><input name="note" placeholder="What was checked / found"><button class="mini" type="submit">Mark checked</button></form><small>Last checked: {_esc(s.get('last_checked_at') or 'Never')}</small></div>''' for s in sources)
    coverage_rows = ''.join(f'''<tr><td>{_esc(x.get('city'))}</td><td>{_esc(x.get('source_category'))}</td><td>{_esc(x.get('source_code'))}</td><td>{_esc(x.get('checked_by'))}</td><td>{_esc(x.get('checked_at'))}</td><td>{_esc(x.get('result_note'))}</td></tr>''' for x in coverage)
    notice = f'<div class="notice">{_esc(message)}</div>' if message else ''
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Alliance Commercial Intelligence</title>
<style>*{{box-sizing:border-box}}body{{margin:0;background:#f4f7fb;font-family:Arial,sans-serif;color:#142033}}header{{background:#0d1d2d;color:white;padding:16px 22px;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}}.wrap{{max-width:1850px;margin:auto;padding:18px}}.tabs{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}}.tabs a,.btn,.mini{{background:#1677ff;color:white;text-decoration:none;border:0;border-radius:8px;padding:9px 12px;font-weight:700;cursor:pointer}}.mini{{padding:6px 8px;font-size:11px}}.gray{{background:#edf2f7!important;color:#24364b!important}}.good{{background:#08734b!important}}.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:12px 0}}.kpi,.card,.source{{background:white;border:1px solid #e4eaf1;border-radius:12px;padding:13px}}.kpi b{{font-size:25px;display:block;margin-top:4px}}.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}.sourcegrid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}}.source small{{display:block;color:#68788c;margin:4px 0}}.badge{{display:inline-block;background:#edf2f7;padding:4px 7px;border-radius:20px;font-size:11px}}input,select,textarea{{width:100%;padding:9px;border:1px solid #ccd7e4;border-radius:7px;margin:5px 0 9px}}.tablewrap{{overflow:auto;max-height:68vh}}table{{width:100%;border-collapse:collapse;font-size:12px;background:white}}th,td{{padding:8px;border-bottom:1px solid #edf1f5;text-align:left;vertical-align:top;white-space:nowrap}}th{{position:sticky;top:0;background:#f8fafc}}.notice{{background:#eaf8f2;color:#075b3c;border:1px solid #bde5d3;border-radius:9px;padding:10px;margin-bottom:10px}}small{{color:#68788c}}@media(max-width:900px){{.grid2{{grid-template-columns:1fr}}}}</style></head><body>
<header><div><b>Alliance Commercial Intelligence Network</b><br><small style="color:#c8d5e3">Discover broadly · capture immediately · verify humans where needed</small></div><div>{_esc(role).upper()} · <a style="color:white" href="/workspace">Main Dashboard</a></div></header>
<div class="wrap">{notice}<div class="tabs"><a href="#inbox">Discovery Inbox</a><a href="#tasks">My Tasks</a><a href="#discover">Run Discovery</a><a href="#sources">Source Master</a><a href="#coverage">Coverage</a><a class="gray" href="/property-database">Property Database</a></div>
<div class="kpis"><div class="kpi"><span>TOTAL DISCOVERIES</span><b>{counts.get('total') or 0}</b></div><div class="kpi"><span>NEW</span><b>{counts.get('new_count') or 0}</b></div><div class="kpi"><span>UPCOMING SIGNALS</span><b>{counts.get('upcoming') or 0}</b></div><div class="kpi"><span>CONTACTABLE</span><b>{counts.get('contactable') or 0}</b></div><div class="kpi"><span>PROMOTED TO MASTER</span><b>{counts.get('promoted') or 0}</b></div><div class="kpi"><span>OPEN RESEARCH TASKS</span><b>{tasks_open}</b></div><div class="kpi"><span>SOURCES DUE</span><b>{sources_due}</b></div></div>
<div id="discover" class="grid2"><div class="card"><h3>Automatic Public Discovery</h3><p>Uses the existing Alliance search-provider waterfall. Results enter the Inbox as <b>REPORTED</b>, never automatically VERIFIED.</p><form method="post" action="/commercial-intelligence/discover"><label>City / Market</label><input name="city" placeholder="Gurugram / Noida / Lucknow / North India" required><label>Signal Mode</label><select name="mode"><option>UPCOMING</option><option>EXISTING</option><option>GOVERNMENT</option><option>LEASING</option><option>HIRING</option></select><label>Optional exact search instead of preset</label><input name="custom_query" placeholder='e.g. "pre leasing" mall Jaipur'><button class="btn" type="submit">Run Public Discovery</button></form></div>
<div class="card"><h3>Manual / Offline Discovery</h3><p>Use this for DMRC/DDA documents, site boards, broker intelligence, public LinkedIn research, calls or any other source.</p><form method="post" action="/commercial-intelligence/discovery/add"><input name="asset_name" placeholder="Property / project name" required><div class="grid2"><input name="city" placeholder="City"><input name="location" placeholder="Location / micro-market"></div><div class="grid2"><input name="developer_company" placeholder="Developer / owner / operator"><input name="contact_phone" placeholder="Public business contact"></div><input name="source_url" placeholder="Source URL"><textarea name="raw_text" placeholder="What you found, company details, tender text, public contact evidence"></textarea><button class="btn good" type="submit">Add to Discovery Inbox</button></form></div></div>
<div id="inbox" class="card"><h3>Discovery Inbox</h3><p>Incomplete records are useful. Promote only after a human checks that the asset is real and correctly identified.</p><div class="tablewrap"><table><thead><tr><th>Asset</th><th>City</th><th>Type</th><th>Status</th><th>Company</th><th>Contact</th><th>Source</th><th>Research</th><th>Action</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="9">No discoveries yet. Run a public search or add one manually.</td></tr>'}</tbody></table></div></div>
<div id="tasks" class="card"><h3>Research Tasks</h3><div class="tablewrap"><table><thead><tr><th>Priority</th><th>Task</th><th>Assigned</th><th>Action</th></tr></thead><tbody>{task_rows or '<tr><td colspan="4">No open tasks.</td></tr>'}</tbody></table></div></div>
<div id="sources" class="card"><h3>Alliance Source Master</h3><p>Automation is not a prerequisite. Low-volume sources can be checked manually until their business value justifies automation.</p><div class="sourcegrid">{source_cards}</div></div>
<div id="coverage" class="card"><h3>Coverage Checklist</h3><p>No fake 100% score. This shows exactly what source was checked, in which city, by whom and when.</p><div class="tablewrap"><table><thead><tr><th>City</th><th>Source Category</th><th>Source</th><th>Checked By</th><th>Checked At</th><th>Result</th></tr></thead><tbody>{coverage_rows or '<tr><td colspan="6">No source checks recorded yet.</td></tr>'}</tbody></table></div></div>
</div></body></html>'''


def register(core):
    app, engine = core.app, core.engine
    ensure_schema(engine)
    router = APIRouter(tags=["Alliance Commercial Intelligence Network"])

    @router.get("/api/commercial-intelligence/status")
    def api_status(req: Request):
        core.need_login(req)
        with engine.connect() as c:
            counts = c.execute(text("SELECT (SELECT COUNT(*) FROM aci_discoveries) discoveries,(SELECT COUNT(*) FROM aci_tasks WHERE status='OPEN') tasks,(SELECT COUNT(*) FROM aci_sources WHERE active=TRUE) sources")).mappings().first()
        return {"status":"ok","version":VERSION,**dict(counts or {})}

    @router.get("/commercial-intelligence", response_class=HTMLResponse)
    def dashboard(req: Request, message: str = Query("")):
        role = _page_role(core, req)
        if not role:
            return RedirectResponse("/login", status_code=303)
        return HTMLResponse(_render_dashboard(engine, role, message))

    @router.post("/commercial-intelligence/discover")
    def discover(req: Request, city: str = Form(...), mode: str = Form("UPCOMING"), custom_query: str = Form("")):
        core.need_login(req)
        result = _run_search(engine, city, mode.upper(), custom_query)
        msg = f"Discovery complete: {result.get('created',0)} new signals from {result.get('results_seen',0)} search results."
        if result.get('provider_log'):
            msg += " Provider log saved in response; use configured Jina/LangSearch/Tavily/Brave/Google CSE keys."
        return RedirectResponse("/commercial-intelligence?message=" + quote_plus(msg) + "#inbox", status_code=303)

    @router.post("/commercial-intelligence/discovery/add")
    def add_discovery(req: Request, asset_name: str = Form(...), city: str = Form(""), location: str = Form(""), developer_company: str = Form(""), contact_phone: str = Form(""), source_url: str = Form(""), raw_text: str = Form("")):
        core.need_login(req)
        dc = upsert_discovery(engine, asset_name=asset_name, title=asset_name, city=city, location=location, developer_company=developer_company, contact_phone=contact_phone, source_code="TEAM", source_name="Alliance Team / Public Research", source_url=source_url, raw_text=raw_text, source_confidence="REPORTED")
        return RedirectResponse("/commercial-intelligence?message=" + quote_plus(f"{dc} added to Discovery Inbox") + "#inbox", status_code=303)

    @router.post("/commercial-intelligence/discovery/{discovery_code}/status")
    def set_status(discovery_code: str, req: Request, status: str = Form(...)):
        core.need_login(req)
        allowed = {"NEW","RESEARCH","VERIFIED","REJECTED"}
        status = status.upper()
        if status not in allowed:
            raise HTTPException(400, "Invalid status")
        with engine.begin() as c:
            c.execute(text("UPDATE aci_discoveries SET research_status=:s,last_researched_at=NOW(),updated_at=NOW() WHERE discovery_code=:d"), {"s":status,"d":discovery_code})
            if status == "RESEARCH":
                _create_task_if_missing(c, discovery_code, "PROPERTY_RESEARCH", f"Research and verify {discovery_code}", "HIGH")
        return RedirectResponse("/commercial-intelligence#inbox", status_code=303)

    @router.post("/commercial-intelligence/discovery/{discovery_code}/promote")
    def promote(discovery_code: str, req: Request):
        core.need_login(req)
        pc = _promote(engine, discovery_code, _actor(core, req))
        return RedirectResponse("/commercial-intelligence?message=" + quote_plus(f"Promoted to canonical Property Master: {pc}") + "#inbox", status_code=303)

    @router.post("/commercial-intelligence/task/{task_id}/done")
    def task_done(task_id: int, req: Request):
        core.need_login(req)
        with engine.begin() as c:
            c.execute(text("UPDATE aci_tasks SET status='DONE',completed_at=NOW(),updated_at=NOW() WHERE id=:id"), {"id":task_id})
        return RedirectResponse("/commercial-intelligence#tasks", status_code=303)

    @router.post("/commercial-intelligence/source/{source_id}/checked")
    def source_checked(source_id: int, req: Request, city: str = Form(...), note: str = Form("")):
        core.need_login(req)
        actor = _actor(core, req)
        with engine.begin() as c:
            source = c.execute(text("SELECT source_code,source_category FROM aci_sources WHERE id=:id"), {"id":source_id}).mappings().first()
            if not source:
                raise HTTPException(404, "Source not found")
            c.execute(text("UPDATE aci_sources SET last_checked_at=NOW(),updated_at=NOW() WHERE id=:id"), {"id":source_id})
            c.execute(text('''
                INSERT INTO aci_coverage_checks(city,source_category,source_code,checked_by,checked_at,result_note)
                VALUES(:city,:cat,:code,:actor,NOW(),:note)
                ON CONFLICT(city,source_category,source_code) DO UPDATE SET checked_by=EXCLUDED.checked_by,checked_at=NOW(),result_note=EXCLUDED.result_note
            '''), {"city":_clean(city),"cat":source.get("source_category"),"code":source.get("source_code"),"actor":actor,"note":_clean(note)})
        return RedirectResponse("/commercial-intelligence#coverage", status_code=303)

    app.include_router(router)
    patch_status = _dashboard_link_patch(app)
    return {"status":"REGISTERED","version":VERSION,"dashboard_link_patch":patch_status,"route":"/commercial-intelligence"}
