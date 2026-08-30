from __future__ import annotations

import html
import os
import re
from difflib import SequenceMatcher
from urllib.parse import quote_plus, urlparse

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION = "2.0.0-COMMERCIAL-INTELLIGENCE-AI-FRONTEND"

# Frontend purity layer. Raw discovery data remains preserved in the database.
# This module decides what is useful enough to show to the team.
NOISE_DOMAINS = {
    "apna.co", "indeed.com", "glassdoor.com", "jobsora.com", "jooble.org", "jooble.co.in",
    "bebee.com", "jora.com", "jobted.in", "naukri.com", "monsterindia.com", "randstad.com",
    "glints.com", "taploker.com", "yatra.com", "wanderlog.com", "holidify.com", "tripgrab.com",
    "datagemba.com", "wikipedia.org", "grokipedia.com",
}

HARD_NOISE_TERMS = {
    "job vacancies", "job vacancy", "jobs in", "apply online", "career guide", "salary guide",
    "full time jobs", "part time jobs", "urgent hiring", "recruitment 2026", "vacancies in",
    "manager jobs", "executive jobs", "leadership jobs", "scope of pgdm", "how to write a letter",
    "how to negotiate", "travel guide", "shopping guide", "best malls to visit", "things to do",
    "reviews ratings tips", "holiday", "tourism",
}

FOREIGN_NOISE_TERMS = {
    "pasig", "malaysia", "wilayah persekutuan", "indonesia", "philippines", "singapore jobs",
    "lowongan kerja", "招聘", "招聘网", "kerja", "vacancy dubai",
}

ASSET_TERMS = {
    "mall": 24,
    "shopping mall": 26,
    "shopping centre": 24,
    "shopping center": 24,
    "high street": 24,
    "commercial project": 22,
    "commercial complex": 22,
    "commercial development": 22,
    "mixed use": 20,
    "mixed-use": 20,
    "retail project": 20,
    "retail space": 15,
    "retail destination": 18,
    "sco": 17,
    "shop cum office": 17,
    "business park": 16,
    "commercial building": 16,
    "food court": 14,
    "multiplex": 15,
    "showroom": 12,
}

OPPORTUNITY_TERMS = {
    "pre leasing": 16,
    "pre-leasing": 16,
    "leasing mandate": 18,
    "leasing partner": 17,
    "exclusive leasing": 18,
    "for lease": 11,
    "for rent": 8,
    "launch": 8,
    "launched": 8,
    "upcoming": 12,
    "under construction": 12,
    "construction": 7,
    "bhoomi poojan": 10,
    "groundbreaking": 10,
    "tender": 12,
    "auction": 12,
    "allotment": 10,
    "concession": 10,
    "rera": 10,
}

LOW_VALUE_TERMS = {
    "commercial properties for rent": -9,
    "shops for rent": -10,
    "shop for rent": -8,
    "commercial property for rent": -8,
    "properties for lease": -7,
    "price brochure floor plan reviews": -5,
    "best malls": -12,
    "top malls": -12,
}

TRUSTED_SOURCE_TERMS = {
    "rera": 14,
    ".gov.in": 14,
    "delhimetrorail": 14,
    "dmrc": 14,
    "rlda": 14,
    "ireps": 12,
    "aai.aero": 12,
    "dda.gov": 14,
    "noidaauthority": 14,
    "greaternoidaauthority": 14,
    "yamunaexpresswayauthority": 14,
}

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "in", "at", "for", "to", "on", "with", "by",
    "from", "new", "latest", "india", "delhi", "ncr", "faridabad", "gurugram", "gurgaon",
    "noida", "ghaziabad", "jaipur", "lucknow", "commercial", "project", "property", "properties",
    "retail", "space", "spaces", "mall", "shopping", "2026", "price", "details", "sector",
}


def _clean(v):
    return re.sub(r"\s+", " ", str(v or "")).strip()


def _esc(v):
    return html.escape(str(v or ""))


def _domain(url):
    try:
        host = (urlparse(str(url or "")).hostname or "").lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _contains_any(text_value, terms):
    low = text_value.lower()
    return any(term in low for term in terms)


def _source_trust(row):
    text_value = " ".join([
        _clean(row.get("source_code")), _clean(row.get("source_name")), _clean(row.get("source_url"))
    ]).lower()
    return max([score for term, score in TRUSTED_SOURCE_TERMS.items() if term in text_value] or [0])


def _score(row):
    title = _clean(row.get("asset_name") or row.get("title"))
    raw = _clean(row.get("raw_text"))
    url = _clean(row.get("source_url"))
    low = f"{title} {raw} {url}".lower()
    dom = _domain(url)

    reasons = []
    if dom in NOISE_DOMAINS or any(dom.endswith("." + d) for d in NOISE_DOMAINS):
        return 0, "Noise source"
    if _contains_any(low, HARD_NOISE_TERMS):
        return 0, "Job/generic content"
    if _contains_any(low, FOREIGN_NOISE_TERMS):
        return 0, "Outside target market"

    score = 0
    for term, points in ASSET_TERMS.items():
        if term in low:
            score += points
            if points >= 20:
                reasons.append(term.title())
    for term, points in OPPORTUNITY_TERMS.items():
        if term in low:
            score += points
            if points >= 12:
                reasons.append(term.title())
    for term, points in LOW_VALUE_TERMS.items():
        if term in low:
            score += points

    trust = _source_trust(row)
    score += trust
    if trust:
        reasons.append("High-trust source")

    if _clean(row.get("developer_company") or row.get("related_company")):
        score += 10
        reasons.append("Company identified")
    if _clean(row.get("contact_phone") or row.get("contact_email")):
        score += 8
        reasons.append("Contact available")
    if _clean(row.get("location")):
        score += 5
    if str(row.get("research_status") or "").upper() == "VERIFIED":
        score += 20
        reasons.append("Verified")

    # Generic listing pages are allowed only when they contain a named asset/project signal.
    generic_listing = _contains_any(low, ["commercial properties for rent", "shop for rent", "shops for rent", "property for rent"])
    named_signal = bool(re.search(r"\b(?:worldmark|dlf|omaxe|bhumika|puri|eldeco|m3m|raheja|kw|navraj|emaar|parsvnath|crown|galleria|aerocity|unity|metro|airport)\b", low))
    if generic_listing and not named_signal and _source_trust(row) == 0:
        score -= 18

    reason = ", ".join(dict.fromkeys(reasons[:4])) or "Commercial signal"
    return max(0, min(100, score)), reason


def _tokens(row):
    title = _clean(row.get("asset_name") or row.get("title")).lower()
    title = re.sub(r"\b20\d{2}\b", " ", title)
    words = re.findall(r"[a-z0-9]+", title)
    return [w for w in words if len(w) > 2 and w not in STOPWORDS]


def _similar(a, b):
    ta, tb = set(_tokens(a)), set(_tokens(b))
    if not ta or not tb:
        return False
    jaccard = len(ta & tb) / max(1, len(ta | tb))
    sa = " ".join(sorted(ta))
    sb = " ".join(sorted(tb))
    seq = SequenceMatcher(None, sa, sb).ratio()
    return jaccard >= 0.56 or seq >= 0.74


def _dedupe(rows):
    kept = []
    for row in sorted(rows, key=lambda r: (r["ai_score"], bool(r.get("contact_phone") or r.get("contact_email"))), reverse=True):
        duplicate = False
        for current in kept:
            same_city = _clean(row.get("city")).lower() == _clean(current.get("city")).lower()
            if same_city and _similar(row, current):
                duplicate = True
                break
        if not duplicate:
            kept.append(row)
    return kept


def _classify(row):
    life = _clean(row.get("lifecycle_status")).upper()
    text_value = " ".join([_clean(row.get("asset_name")), _clean(row.get("title")), _clean(row.get("raw_text"))]).lower()
    if "UPCOMING" in life or _contains_any(text_value, ["upcoming", "under construction", "pre leasing", "pre-leasing", "launch", "bhoomi poojan"]):
        return "UPCOMING"
    if "EXISTING" in life or _contains_any(text_value, ["operational", "open now", "existing mall"]):
        return "EXISTING"
    return "VERIFY"


def _load_useful(engine):
    with engine.connect() as c:
        raw_rows = c.execute(text('''
            SELECT * FROM aci_discoveries
            WHERE COALESCE(research_status,'NEW') <> 'REJECTED'
            ORDER BY discovered_at DESC
            LIMIT 2500
        ''')).mappings().all()

    useful = []
    for r in raw_rows:
        row = dict(r)
        score, reason = _score(row)
        row["ai_score"] = score
        row["ai_reason"] = reason
        row["ai_bucket"] = _classify(row)
        if score >= 28:
            useful.append(row)
    useful = _dedupe(useful)
    useful.sort(key=lambda x: (x["ai_score"], x.get("discovered_at") or 0), reverse=True)
    return useful, len(raw_rows)


def _page_role(core, req):
    fn = getattr(core, "page_role_or_redirect", None)
    if callable(fn):
        return fn(req)
    try:
        core.need_login(req)
        return "team"
    except Exception:
        return None


def _render(engine, role, view, city, message):
    useful, raw_count = _load_useful(engine)
    city_q = _clean(city).lower()
    if city_q:
        useful = [r for r in useful if city_q in _clean(r.get("city")).lower() or city_q in _clean(r.get("location")).lower()]

    counts = {
        "BEST": len([r for r in useful if r["ai_score"] >= 55]),
        "UPCOMING": len([r for r in useful if r["ai_bucket"] == "UPCOMING"]),
        "EXISTING": len([r for r in useful if r["ai_bucket"] == "EXISTING"]),
        "VERIFY": len([r for r in useful if r["ai_bucket"] == "VERIFY"]),
    }

    if view == "BEST":
        shown = [r for r in useful if r["ai_score"] >= 55]
    elif view in {"UPCOMING", "EXISTING", "VERIFY"}:
        shown = [r for r in useful if r["ai_bucket"] == view]
    else:
        shown = useful
    shown = shown[:150]

    cards = []
    for r in shown:
        contact = _clean(r.get("contact_phone") or r.get("contact_email")) or "Needs verification"
        company = _clean(r.get("developer_company") or r.get("related_company")) or "Not identified yet"
        location = " · ".join(x for x in [_clean(r.get("city")), _clean(r.get("location"))] if x) or "Location not confirmed"
        source_url = _clean(r.get("source_url"))
        source = f'<a href="{_esc(source_url)}" target="_blank" rel="noopener">Open source</a>' if source_url else "Source retained in backend"
        score = int(r["ai_score"])
        cls = "hot" if score >= 70 else ("good" if score >= 55 else "review")
        cards.append(f'''
        <article class="opportunity">
          <div class="top"><div><h3>{_esc(r.get('asset_name') or r.get('title'))}</h3><div class="muted">{_esc(location)}</div></div><div class="score {cls}">{score}<small>/100</small></div></div>
          <div class="chips"><span>{_esc(r.get('ai_bucket'))}</span><span>{_esc(r.get('asset_type') or 'COMMERCIAL')}</span><span>{_esc(r.get('research_status') or 'NEW')}</span></div>
          <div class="facts"><div><b>Developer / Owner</b><br>{_esc(company)}</div><div><b>Business Contact</b><br>{_esc(contact)}</div><div><b>Why useful</b><br>{_esc(r.get('ai_reason'))}</div><div><b>Evidence</b><br>{source}</div></div>
          <div class="actions">
            <form method="post" action="/commercial-intelligence-ai/{_esc(r.get('discovery_code'))}/status"><input type="hidden" name="status" value="VERIFIED"><button class="verify">Verify</button></form>
            <form method="post" action="/commercial-intelligence-ai/{_esc(r.get('discovery_code'))}/status"><input type="hidden" name="status" value="RESEARCH"><button>Research</button></form>
            <form method="post" action="/commercial-intelligence-ai/{_esc(r.get('discovery_code'))}/promote"><button class="promote">Promote</button></form>
            <form method="post" action="/commercial-intelligence-ai/{_esc(r.get('discovery_code'))}/status"><input type="hidden" name="status" value="REJECTED"><button class="reject">Hide</button></form>
          </div>
        </article>''')

    notice = f'<div class="notice">{_esc(message)}</div>' if message else ""
    city_param = f"&city={quote_plus(city)}" if city else ""
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Commercial Intelligence AI</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f5f7fb;font-family:Arial,sans-serif;color:#152238}}header{{background:#0d1d2d;color:#fff;padding:18px 24px;display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}}header small{{color:#bfd0df}}.wrap{{max-width:1500px;margin:auto;padding:20px}}.notice{{background:#e9f8f1;border:1px solid #b9e2ce;color:#075c3e;padding:10px 12px;border-radius:10px;margin-bottom:14px}}.hero{{background:#fff;border:1px solid #e3e9f0;border-radius:14px;padding:16px;margin-bottom:14px}}.hero h2{{margin:0 0 6px}}.muted{{color:#6c7c8d}}.tabs{{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}}.tabs a{{text-decoration:none;color:#19324d;background:#eaf0f6;padding:9px 12px;border-radius:9px;font-weight:700}}.tabs a.active{{background:#1677ff;color:#fff}}.filter{{display:flex;gap:8px;max-width:560px}}.filter input{{flex:1;padding:10px;border:1px solid #ccd7e4;border-radius:8px}}button{{border:0;background:#eaf0f6;color:#19324d;padding:8px 11px;border-radius:8px;font-weight:700;cursor:pointer}}.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}}.kpi{{background:#fff;border:1px solid #e3e9f0;border-radius:12px;padding:12px}}.kpi b{{font-size:26px;display:block;margin-top:5px}}.list{{display:grid;gap:12px}}.opportunity{{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:15px}}.top{{display:flex;justify-content:space-between;gap:12px}}h3{{margin:0 0 5px;font-size:18px}}.score{{min-width:68px;text-align:center;border-radius:12px;padding:9px;font-size:24px;font-weight:800;background:#eef3f7}}.score small{{font-size:10px;display:block}}.score.hot{{background:#daf5e8;color:#08734b}}.score.good{{background:#e8f1ff;color:#135db6}}.score.review{{background:#fff4dc;color:#865b00}}.chips{{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0}}.chips span{{background:#eef3f7;border-radius:20px;padding:4px 8px;font-size:11px;font-weight:700}}.facts{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;padding:10px 0;border-top:1px solid #edf1f5;border-bottom:1px solid #edf1f5;font-size:13px}}.actions{{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px}}.verify{{background:#1677ff;color:#fff}}.promote{{background:#08734b;color:#fff}}.reject{{background:#f7e8e8;color:#9d2f2f}}a{{color:#1666c0}}@media(max-width:850px){{.facts{{grid-template-columns:1fr 1fr}}}}@media(max-width:520px){{.facts{{grid-template-columns:1fr}}}}
</style></head><body>
<header><div><b>Commercial Intelligence AI</b><br><small>Useful property opportunities only · noise hidden automatically</small></div><div>{_esc(role).upper()} · <a style="color:white" href="/workspace">Main Dashboard</a></div></header>
<div class="wrap">{notice}
<section class="hero"><h2>Commercial Opportunities</h2><div class="muted">Raw discovery is preserved in the backend. This page shows only commercially relevant, de-duplicated opportunities. {raw_count} raw signals reviewed by the purity layer.</div>
<div class="tabs"><a class="{'active' if view=='BEST' else ''}" href="/commercial-intelligence?view=BEST{city_param}">Best Opportunities</a><a class="{'active' if view=='UPCOMING' else ''}" href="/commercial-intelligence?view=UPCOMING{city_param}">Upcoming Projects</a><a class="{'active' if view=='EXISTING' else ''}" href="/commercial-intelligence?view=EXISTING{city_param}">Existing Assets</a><a class="{'active' if view=='VERIFY' else ''}" href="/commercial-intelligence?view=VERIFY{city_param}">Needs Verification</a></div>
<form class="filter" method="get"><input type="hidden" name="view" value="{_esc(view)}"><input name="city" value="{_esc(city)}" placeholder="Filter city / market"><button>Filter</button></form></section>
<div class="kpis"><div class="kpi">BEST OPPORTUNITIES<b>{counts['BEST']}</b></div><div class="kpi">UPCOMING<b>{counts['UPCOMING']}</b></div><div class="kpi">EXISTING<b>{counts['EXISTING']}</b></div><div class="kpi">VERIFY<b>{counts['VERIFY']}</b></div></div>
<div class="list">{''.join(cards) or '<div class="hero"><b>No useful opportunities in this view.</b><br><span class="muted">The purity layer is correctly hiding weak/noisy records.</span></div>'}</div>
</div></body></html>'''


def register(core):
    app, engine = core.app, core.engine

    # Reuse the existing data model and canonical promotion logic without registering
    # the old raw-search dashboard.
    import alliance_commercial_intelligence_network as network
    network.ensure_schema(engine)

    router = APIRouter(tags=["Alliance Commercial Intelligence AI"])

    @router.get("/commercial-intelligence", response_class=HTMLResponse)
    def dashboard(req: Request, view: str = Query("BEST"), city: str = Query(""), message: str = Query("")):
        role = _page_role(core, req)
        if not role:
            return RedirectResponse("/login", status_code=303)
        view = str(view or "BEST").upper()
        if view not in {"BEST", "UPCOMING", "EXISTING", "VERIFY"}:
            view = "BEST"
        return HTMLResponse(_render(engine, role, view, city, message))

    @router.get("/api/commercial-intelligence/status")
    def status(req: Request):
        core.need_login(req)
        useful, raw_count = _load_useful(engine)
        return {
            "status": "ok",
            "version": VERSION,
            "raw_discoveries": raw_count,
            "useful_after_purity": len(useful),
            "best_opportunities": len([r for r in useful if r["ai_score"] >= 55]),
            "noise_hidden": max(0, raw_count - len(useful)),
            "raw_data_deleted": False,
        }

    @router.post("/commercial-intelligence-ai/{discovery_code}/status")
    def set_status(discovery_code: str, req: Request, status: str = Form(...)):
        core.need_login(req)
        status = str(status or "").upper()
        if status not in {"NEW", "RESEARCH", "VERIFIED", "REJECTED"}:
            raise HTTPException(400, "Invalid status")
        with engine.begin() as c:
            found = c.execute(text("SELECT id FROM aci_discoveries WHERE discovery_code=:d"), {"d": discovery_code}).scalar()
            if not found:
                raise HTTPException(404, "Discovery not found")
            c.execute(text("UPDATE aci_discoveries SET research_status=:s,last_researched_at=NOW(),updated_at=NOW() WHERE discovery_code=:d"), {"s": status, "d": discovery_code})
            if status == "RESEARCH":
                network._create_task_if_missing(c, discovery_code, "PROPERTY_RESEARCH", f"Research and verify {discovery_code}", "HIGH")
        return RedirectResponse("/commercial-intelligence", status_code=303)

    @router.post("/commercial-intelligence-ai/{discovery_code}/promote")
    def promote(discovery_code: str, req: Request):
        core.need_login(req)
        pc = network._promote(engine, discovery_code, network._actor(core, req))
        return RedirectResponse("/commercial-intelligence?message=" + quote_plus(f"Promoted to Property Master: {pc}"), status_code=303)

    app.include_router(router)
    try:
        patch_status = network._dashboard_link_patch(app)
    except Exception:
        patch_status = "SKIPPED"
    return {"status": "REGISTERED", "version": VERSION, "route": "/commercial-intelligence", "dashboard_link_patch": patch_status}
