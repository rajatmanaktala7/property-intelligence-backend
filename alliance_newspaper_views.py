from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Tuple

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_live_feed_purity_legacy36 as legacy

VERSION = "WHATSAPP-NEWSPAPER-ENTITY-FIRST-4.0"
MAX_PAGE_ROWS = 350
DB_TIMEOUT_MS = 4500


def _esc(v: Any) -> str:
    return html.escape(str(v if v is not None else ""), quote=True)


def _clean_text(v: Any) -> str:
    s = str(v or "")
    s = re.sub(r"[*_`#]+", "", s)
    s = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", s)
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def _plain(v: Any) -> str:
    s = _clean_text(v)
    s = re.sub(r"^[\W_]+", "", s)
    return s.strip()


def _fmt_money(v: Any) -> str:
    if v in (None, "", 0, "0"):
        return "—"
    try:
        n = float(v)
    except Exception:
        return _clean_text(v) or "—"
    if n >= 10_000_000:
        return f"₹{n/10_000_000:.2f} Cr".replace(".00", "")
    if n >= 100_000:
        return f"₹{n/100_000:.2f} Lakh".replace(".00", "")
    if n >= 1_000:
        return f"₹{n:,.0f}"
    return f"₹{n:,.2f}".replace(".00", "")


def _fmt_area(v: Any) -> str:
    if v in (None, "", 0, "0"):
        return "—"
    try:
        n = float(v)
        return f"{n:,.0f} sq ft"
    except Exception:
        return _clean_text(v) or "—"


def _fmt_date(v: Any) -> str:
    if not v:
        return "—"
    s = str(v)
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d.strftime("%d %b %Y, %H:%M")
    except Exception:
        return s[:19].replace("T", " ")


def _first_phone(*values: Any) -> str:
    for value in values:
        if not value:
            continue
        m = re.search(r"(?:\+?91[\s-]?)?[6-9](?:[\s-]?\d){9}", str(value))
        if m:
            return re.sub(r"\D", "", m.group(0))[-10:]
    return "—"


def _extract(rx: str, raw: str, flags=re.I) -> str:
    m = re.search(rx, raw or "", flags)
    return _plain(m.group(1)) if m else ""


def _extract_configuration(raw: str) -> str:
    m = re.search(r"\b(\d+(?:\.\d+)?)\s*BHK\b", raw or "", re.I)
    if m:
        return f"{m.group(1)} BHK"
    m = re.search(r"\b(\d+)\s*(?:bed|bedroom)s?\b", raw or "", re.I)
    return f"{m.group(1)} Bedroom" if m else "—"


def _extract_furnishing(raw: str) -> str:
    s = (raw or "").lower()
    if "fully furnished" in s:
        return "Fully Furnished"
    if "semi furnished" in s or "semi-furnished" in s:
        return "Semi Furnished"
    if "unfurnished" in s:
        return "Unfurnished"
    if "bare shell" in s or "bareshell" in s:
        return "Bare Shell"
    return "—"


def _extract_parking(raw: str, current: Any = None) -> str:
    if current:
        return _clean_text(current)
    m = re.search(r"\b(\d+)\s*(?:car\s*)?parking\b", raw or "", re.I)
    if m:
        return f"{m.group(1)} Car Parking"
    if re.search(r"\bparking\b", raw or "", re.I):
        return "Parking Available"
    return "—"


def _extract_frontage(raw: str, current: Any = None) -> str:
    if current:
        return _clean_text(current)
    v = _extract(r"(?:frontage|front)\s*[:\-]?\s*([0-9.]+\s*(?:ft|feet|mtr|meter|metre)s?)", raw)
    return v or "—"


def _extract_tenant(raw: str) -> str:
    v = _extract(r"\btenant\s*[:\-]\s*([^\n|]{2,80})", raw)
    return v or "—"


def _extract_possession(raw: str, current: Any = None) -> str:
    if current:
        return _clean_text(current)
    if re.search(r"\bready\s*to\s*move\b", raw or "", re.I):
        return "Ready to Move"
    if re.search(r"\bimmediate\s+possession\b", raw or "", re.I):
        return "Immediate"
    v = _extract(r"\bpossession\s*[:\-]\s*([^\n|]{2,60})", raw)
    return v or "—"


def _description_from_raw(raw: str, row: Dict[str, Any], requirement: bool = False) -> str:
    lines = [_plain(x) for x in (raw or "").splitlines()]
    lines = [x for x in lines if x and not re.fullmatch(r"[-━=•▪◾◼️\s]+", x)]

    # Prefer a human-readable title / intent line.
    preferred = []
    for line in lines[:12]:
        low = line.lower()
        if any(k in low for k in (
            "for sale", "for rent", "available", "requirement", "looking for",
            "wanted", "need ", "plot", "villa", "apartment", "flat", "office",
            "shop", "showroom", "restaurant", "banquet", "warehouse", "bhk",
        )):
            preferred.append(line)
    if preferred:
        d = preferred[0]
    elif lines:
        d = lines[0]
    else:
        d = ""

    if len(d) < 8:
        typ = _clean_text(row.get("clean_property_type") or row.get("property_type") or "Property")
        tx = _clean_text(row.get("clean_transaction") or row.get("transaction_type") or "")
        loc = _clean_text(row.get("clean_location") or row.get("location") or row.get("preferred_locations") or "")
        if requirement:
            d = f"Requirement: {typ} {tx} {loc}".strip()
        else:
            d = f"{typ} {tx} {loc}".strip()

    return d[:180] or ("Property Requirement" if requirement else "Property Availability")


PROPERTY_SIGNAL_RE = re.compile(
    r"\b(?:bhk|sq\.?\s*ft|sqft|sq\.?\s*yd|yard|gaz|gaj|sqm|sq\.?\s*m|"
    r"cr\b|crore|lakh|lac\b|rent|sale|sell|lease|plot|villa|apartment|flat|"
    r"floor|office|shop|showroom|warehouse|hotel|restaurant|banquet|pg\b|"
    r"guest\s*house|parking|tenant|sector|road|location|demand|price)\b",
    re.I,
)


def _chunk_property_score(chunk: str) -> int:
    c = chunk or ""
    score = 0
    score += min(4, len(PROPERTY_SIGNAL_RE.findall(c)))
    if re.search(r"(?:₹|rs\.?|inr)\s*[\d,.]+|\b\d+(?:\.\d+)?\s*(?:cr|crore|lakh|lac|k)\b", c, re.I):
        score += 2
    if re.search(r"\b\d[\d,.]*\s*(?:sq\.?\s*ft|sqft|sq\.?\s*yd|yard|gaz|gaj|sqm|sq\.?\s*m|mtr)\b", c, re.I):
        score += 2
    if re.search(r"\b(?:for sale|for rent|available|tenant|demand|price|rent)\b", c, re.I):
        score += 1
    return score


def _secondary_split_text(raw: str) -> List[str]:
    """
    Conservative second-pass splitter for historical WhatsApp portfolio messages.

    It only splits when two or more chunks independently look like property entities.
    This avoids turning title/details/contact paragraphs of one property into fake rows.
    """
    raw = (raw or "").strip()
    if not raw:
        return []

    candidates: List[str] = []

    # Strong numbered boundaries: 1. / 2) / 3.
    numbered = re.split(r"(?m)(?=^\s*\d{1,2}[\.\)]\s+)", raw)
    numbered = [x.strip() for x in numbered if x.strip()]
    if sum(_chunk_property_score(x) >= 4 for x in numbered) >= 2:
        candidates = [x for x in numbered if _chunk_property_score(x) >= 4]

    # Strong decorative property headings common in broker portfolio posts.
    if not candidates:
        decorative = re.split(
            r"(?m)(?=^\s*(?:✨|🏡|🏢|🏰|🌴|🌅|🔥)\s*[A-Z0-9][^\n]{1,80}$)",
            raw,
        )
        decorative = [x.strip() for x in decorative if x.strip()]
        if sum(_chunk_property_score(x) >= 4 for x in decorative) >= 2:
            candidates = [x for x in decorative if _chunk_property_score(x) >= 4]

    # Blank-line portfolio groups. Use only when each retained paragraph can
    # stand on its own as a property.
    if not candidates:
        paragraphs = [x.strip() for x in re.split(r"\n\s*\n+", raw) if x.strip()]
        good = [x for x in paragraphs if _chunk_property_score(x) >= 5]
        if len(good) >= 2:
            candidates = good

    return candidates or [raw]


def _secondary_split_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        d = dict(row)
        raw = str(d.get("visible_entity_text") or d.get("raw_text") or "").strip()
        parts = _secondary_split_text(raw)
        if len(parts) == 1:
            out.append(d)
            continue
        for idx, part in enumerate(parts, 1):
            fp = re.sub(r"\W+", "", part.lower())[:240]
            if not fp or fp in seen:
                continue
            seen.add(fp)
            x = dict(d)
            x["visible_entity_text"] = part
            x["visible_entity_no"] = idx
            x["visible_entity_id"] = f"{d.get('visible_entity_id') or d.get('wa_property_id') or d.get('id')}-N{idx}"
            out.append(x)
    return out


def _load_inventory(core, q: str = "", limit: int = 300) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    source, stored, detected = legacy.get_rows(core, q, limit)
    visible = legacy._visible_entities(stored)
    visible = _secondary_split_rows(visible)
    clean, stats = legacy._clean_visible_rows(visible, 60)
    # Keep proven priority data but no longer make priority the newspaper's main visual.
    rows = legacy._prioritize_rows(clean)
    return source, rows, detected, stats


def _load_requirements(core, q: str = "", limit: int = 300) -> List[Dict[str, Any]]:
    engine, dispose = legacy._source_engine(core)
    params: Dict[str, Any] = {"lim": min(max(limit, 25), 1000)}
    where = ["COALESCE(r.status,'ACTIVE')='ACTIVE'"]
    if q:
        params["q"] = f"%{q}%"
        where.append("""(
            COALESCE(r.raw_text,'') ILIKE :q OR
            COALESCE(r.preferred_locations,'') ILIKE :q OR
            COALESCE(r.property_type,'') ILIKE :q OR
            COALESCE(r.client_name,'') ILIKE :q OR
            COALESCE(r.company_name,'') ILIKE :q OR
            COALESCE(r.contact_name,'') ILIKE :q OR
            COALESCE(r.contact_phone,'') ILIKE :q
        )""")
    sql = f"""
        SELECT
            r.*,
            s.group_name,
            s.source_name,
            m.message_timestamp,
            m.sender_name,
            m.sender_phone
        FROM wa_requirements r
        LEFT JOIN wa_sources s ON s.source_id=r.source_id
        LEFT JOIN wa_messages m ON m.message_id=r.message_id
        WHERE {" AND ".join(where)}
        ORDER BY r.created_at DESC NULLS LAST, r.id DESC
        LIMIT :lim
    """
    try:
        with engine.connect() as conn:
            try:
                conn.execute(text(f"SET statement_timeout = {DB_TIMEOUT_MS}"))
            except Exception:
                pass
            rows = [dict(x) for x in conn.execute(text(sql), params).mappings().all()]
    finally:
        if dispose:
            engine.dispose()

    out: List[Dict[str, Any]] = []
    for row in rows:
        raw = str(row.get("raw_text") or "")
        if _requirement_is_clean(row, raw):
            out.append(row)
    return out


def _requirement_is_clean(row: Dict[str, Any], raw: str) -> bool:
    # Reject obvious greetings, links and motivational/non-property content.
    words = re.findall(r"[A-Za-z0-9]+", raw)
    property_hits = len(PROPERTY_SIGNAL_RE.findall(raw))
    intent = bool(re.search(
        r"\b(?:requirement|required|looking\s+for|wanted|need(?:ed)?|"
        r"client\s+(?:is\s+)?looking|direct\s+client|preferred\s+location|"
        r"budget\s*(?:up\s*to|:)|kindly\s+share\s+suitable)\b",
        raw,
        re.I,
    ))
    structured = sum(bool(row.get(k)) for k in (
        "preferred_locations", "property_type", "budget_max_inr",
        "minimum_area_sqft", "contact_phone",
    ))

    # If the existing purity classifier confidently says supply, do not show it as demand.
    try:
        from alliance_v2_whatsapp_purity import detect_transaction
        role, _, _, _ = detect_transaction(row.get("transaction_type"), raw)
        if role == "SUPPLY" and not intent:
            return False
    except Exception:
        pass

    return len(words) >= 4 and property_hits >= 1 and (intent or structured >= 3)


def _value(label: str, value: Any, wide: bool = False, cls: str = "") -> str:
    v = _clean_text(value) or "—"
    return (
        f"<div class='field {'wide' if wide else ''} {cls}'>"
        f"<div class='label'>{_esc(label)}</div>"
        f"<div class='value'>{_esc(v)}</div>"
        f"</div>"
    )


def _raw_details(raw: str) -> str:
    return (
        "<details class='raw'><summary>View Original WhatsApp Message</summary>"
        f"<pre>{_esc(raw)}</pre></details>"
    )


def _property_card(row: Dict[str, Any]) -> str:
    raw = str(row.get("visible_entity_text") or row.get("raw_text") or "")
    desc = _description_from_raw(raw, row, False)
    tx = _clean_text(row.get("clean_transaction") or row.get("transaction_type") or "—")
    loc = _clean_text(row.get("clean_location") or row.get("location") or row.get("locality") or "—")
    typ = _clean_text(row.get("clean_property_type") or row.get("property_type") or "—")
    area = row.get("clean_area_min_sqft") or row.get("available_area_sqft") or row.get("area_sqft")
    amount = row.get("clean_budget")
    if not amount:
        amount = row.get("rent_inr") if tx.upper() == "LEASE" else row.get("sale_price_inr")
    name = row.get("owner_name") or row.get("broker_name") or row.get("sender_name") or "—"
    phone = _first_phone(row.get("owner_phone"), row.get("broker_phone"), row.get("sender_phone"), raw)
    company = row.get("broker_name") or row.get("sender_name") or "—"
    pid = row.get("visible_entity_id") or row.get("wa_property_id") or row.get("id") or "—"

    special = []
    for phrase in ("park facing", "corner", "main road", "roadside", "sea view", "garden", "lift", "ready to move", "negotiable"):
        if phrase in raw.lower():
            special.append(phrase.title())
    special_text = " | ".join(dict.fromkeys(special)) or "—"

    return f"""
    <article class='listing'>
      <div class='kicker'>AVAILABILITY</div>
      <h2>{_esc(desc)}</h2>
      <div class='fields'>
        {_value("Location", loc)}
        {_value("Property Type", typ)}
        {_value("Transaction", tx)}
        {_value("Area / Size", _fmt_area(area))}
        {_value("Floor", row.get("floor") or "—")}
        {_value("Price / Rent", _fmt_money(amount), cls="money")}
        {_value("Configuration", _extract_configuration(raw))}
        {_value("Furnishing", _extract_furnishing(raw))}
        {_value("Parking", _extract_parking(raw, row.get("parking")))}
        {_value("Frontage", _extract_frontage(raw, row.get("frontage")))}
        {_value("Possession", _extract_possession(raw, row.get("possession")))}
        {_value("Tenant / Pre-leased", _extract_tenant(raw))}
        {_value("Suitable For", row.get("suitable_for") or "—", wide=True)}
        {_value("Special Features", special_text, wide=True)}
        {_value("Availability", row.get("availability") or "To Verify")}
        {_value("Verification", "Verified" if str(row.get("availability") or "").upper()=="VERIFIED" else "Unverified")}
        {_value("Contact Name", name)}
        {_value("Contact Number", phone)}
        {_value("Broker / Company", company)}
        {_value("Received", _fmt_date(row.get("last_seen") or row.get("first_seen")))}
        {_value("Property ID", pid)}
        {_value("Priority", f"{row.get('priority_band') or '—'} {row.get('priority_score') or ''}".strip())}
      </div>
      {_raw_details(raw)}
    </article>
    """


def _requirement_card(row: Dict[str, Any]) -> str:
    raw = str(row.get("raw_text") or "")
    desc = _description_from_raw(raw, row, True)
    rid = row.get("wa_requirement_id") or row.get("id") or "—"
    phone = _first_phone(row.get("contact_phone"), row.get("sender_phone"), raw)
    name = row.get("contact_name") or row.get("client_name") or row.get("sender_name") or "—"
    locations = row.get("preferred_locations") or row.get("city") or "—"
    budget = row.get("budget_max_inr") or row.get("budget_min_inr")
    urgency = "Immediate / Ready to Close" if re.search(r"\b(?:immediate|urgent|ready\s+to\s+close|same\s+day)\b", raw, re.I) else "—"
    possession = _extract(r"\bpossession\s*[:\-]\s*([^\n]{2,60})", raw) or "—"
    purpose = _extract(r"\b(?:purpose|ideal\s+for)\s*[:\-]\s*([^\n]{2,100})", raw) or row.get("suitable_category") or "—"

    return f"""
    <article class='listing requirement'>
      <div class='kicker'>REQUIREMENT</div>
      <h2>{_esc(desc)}</h2>
      <div class='fields'>
        {_value("Requirement Type", row.get("transaction_type") or "—")}
        {_value("Preferred Locations", locations, wide=True)}
        {_value("Property Type", row.get("property_type") or "—")}
        {_value("Configuration", _extract_configuration(raw))}
        {_value("Minimum Area", _fmt_area(row.get("minimum_area_sqft")))}
        {_value("Maximum Area", _fmt_area(row.get("maximum_area_sqft")))}
        {_value("Budget", _fmt_money(budget), cls="money")}
        {_value("Floor Preference", row.get("floor_preference") or "—")}
        {_value("Frontage Requirement", row.get("frontage_requirement") or "—")}
        {_value("Purpose / Suitable Category", purpose, wide=True)}
        {_value("Possession", possession)}
        {_value("Urgency", urgency)}
        {_value("Contact Name", name)}
        {_value("Contact Number", phone)}
        {_value("Company", row.get("company_name") or "—")}
        {_value("WhatsApp Group", row.get("group_name") or row.get("source_name") or "—")}
        {_value("Received", _fmt_date(row.get("message_timestamp") or row.get("created_at")))}
        {_value("Requirement ID", rid)}
      </div>
      <div class='actions'><a class='action' href='/whatsapp-intelligence/requirement/{_esc(rid)}/matches'>Find Matching Properties</a></div>
      {_raw_details(raw)}
    </article>
    """


def _page(title: str, subtitle: str, body: str, q: str = "", counters: str = "") -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html>
<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{_esc(title)}</title>
<style>
:root{{--paper:#f3efe6;--ink:#1f1c18;--muted:#6e665d;--line:#c9bda9;--accent:#593e2a;--card:#fffdf8;}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font-family:Georgia,'Times New Roman',serif}}
.top{{border-bottom:4px double var(--ink);padding:16px 22px 12px;background:#fffaf0}}
.brand{{font-size:13px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted)}}
h1{{font-size:34px;line-height:1;margin:5px 0 7px;text-transform:uppercase;letter-spacing:-.02em}}
.subtitle{{font:14px/1.4 Arial,sans-serif;color:#4e4943}}
.nav{{display:flex;gap:8px;flex-wrap:wrap;margin-top:13px}}
.nav a{{font:700 13px Arial,sans-serif;text-decoration:none;color:var(--ink);border:1px solid var(--ink);padding:8px 11px;background:#fff}}
.nav a:hover{{background:var(--ink);color:#fff}}
.wrap{{max-width:1500px;margin:auto;padding:18px}}
.tools{{background:#fffaf0;border:1px solid var(--line);padding:12px;margin-bottom:16px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}}
.tools form{{display:flex;gap:8px;flex:1;min-width:280px}} input{{width:100%;padding:10px;border:1px solid #9f9484;background:white}}
button,.action{{font:700 12px Arial,sans-serif;background:var(--accent);color:white;border:0;padding:10px 13px;text-decoration:none}}
.counters{{font:12px Arial,sans-serif;color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:15px;align-items:start}}
.listing{{background:var(--card);border:1px solid var(--line);border-top:5px solid var(--ink);padding:16px;box-shadow:0 2px 8px rgba(0,0,0,.04);break-inside:avoid}}
.listing.requirement{{border-top-color:#6f2f2f}}
.kicker{{font:700 10px Arial,sans-serif;letter-spacing:.16em;color:var(--muted);margin-bottom:5px}}
.listing h2{{font-size:22px;line-height:1.15;margin:0 0 13px;border-bottom:1px solid var(--line);padding-bottom:10px}}
.fields{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0;border-top:1px solid #ded5c6;border-left:1px solid #ded5c6}}
.field{{min-height:61px;border-right:1px solid #ded5c6;border-bottom:1px solid #ded5c6;padding:8px}}
.field.wide{{grid-column:span 2}} .label{{font:700 10px Arial,sans-serif;text-transform:uppercase;letter-spacing:.06em;color:#766d63;margin-bottom:4px}}
.value{{font:15px/1.3 Georgia,serif;word-break:break-word}} .money .value{{font-weight:700;font-size:17px}}
.raw{{margin-top:12px;border-top:1px dashed var(--line);padding-top:9px}} summary{{cursor:pointer;font:700 11px Arial,sans-serif;color:#6a5748}}
pre{{white-space:pre-wrap;word-wrap:break-word;background:#f7f2e8;border:1px solid #ddd1bf;padding:10px;font:12px/1.4 Consolas,monospace;max-height:300px;overflow:auto}}
.actions{{margin-top:12px}} .empty{{background:white;border:1px solid var(--line);padding:30px;text-align:center}}
.sectiontitle{{font-size:25px;text-transform:uppercase;border-bottom:3px double var(--ink);margin:22px 0 12px;padding-bottom:5px}}
@media(max-width:650px){{h1{{font-size:27px}}.grid{{grid-template-columns:1fr}}.fields{{grid-template-columns:1fr}}.field.wide{{grid-column:span 1}}}}
</style></head>
<body>
<header class='top'>
<div class='brand'>Alliance Infrastructure · WhatsApp Property Intelligence</div>
<h1>{_esc(title)}</h1><div class='subtitle'>{_esc(subtitle)}</div>
<nav class='nav'>
<a href='/whatsapp-live'>Main Newspaper</a>
<a href='/whatsapp-live/requirements'>Requirements</a>
<a href='/whatsapp-live/feed'>Availability</a>
<a href='/team-dashboard-v376'>Dashboard</a>
</nav>
</header>
<main class='wrap'>
<div class='tools'>
<form method='get'><input name='q' value='{_esc(q)}' placeholder='Search location, property type, company, contact, details'><button>Search</button></form>
<div class='counters'>{counters}</div>
</div>
{body}
</main></body></html>""")


def availability_page(request: Request, core) -> HTMLResponse:
    q = str(request.query_params.get("q") or "").strip()
    try:
        limit = min(max(int(request.query_params.get("limit") or 250), 25), MAX_PAGE_ROWS)
    except Exception:
        limit = 250
    try:
        source, rows, detected, stats = _load_inventory(core, q, limit)
        body = "<div class='grid'>" + "".join(_property_card(x) for x in rows[:MAX_PAGE_ROWS]) + "</div>"
        if not rows:
            body = "<div class='empty'>No clean availability found for this search.</div>"
        counters = (
            f"<b>{len(rows)}</b> clean property entities · source <b>{_esc(source)}</b> · "
            f"requirements removed <b>{stats.get('requirements_removed',0)}</b> · "
            f"duplicates removed <b>{stats.get('duplicate_removed',0)}</b>"
        )
        return _page(
            "Availability Newspaper",
            "One physical property = one entity. Description first, structured fields below, original WhatsApp message only on demand.",
            body, q, counters
        )
    except Exception as exc:
        return _page("Availability Newspaper", "Database-safe newspaper view", f"<div class='empty'><b>Feed error:</b> {_esc(type(exc).__name__)}: {_esc(exc)}</div>", q)


def requirements_page(request: Request, core) -> HTMLResponse:
    q = str(request.query_params.get("q") or "").strip()
    try:
        limit = min(max(int(request.query_params.get("limit") or 250), 25), MAX_PAGE_ROWS)
    except Exception:
        limit = 250
    try:
        rows = _load_requirements(core, q, limit)
        body = "<div class='grid'>" + "".join(_requirement_card(x) for x in rows[:MAX_PAGE_ROWS]) + "</div>"
        if not rows:
            body = "<div class='empty'>No clean requirements found for this search.</div>"
        counters = f"<b>{len(rows)}</b> genuine active requirements · non-property/noise filtered"
        return _page(
            "Requirements Newspaper",
            "Only genuine buyer / tenant demand. Description first, requirement fields underneath, original message kept for audit.",
            body, q, counters
        )
    except Exception as exc:
        return _page("Requirements Newspaper", "Database-safe newspaper view", f"<div class='empty'><b>Requirements error:</b> {_esc(type(exc).__name__)}: {_esc(exc)}</div>", q)


def main_page(request: Request, core) -> HTMLResponse:
    q = str(request.query_params.get("q") or "").strip()
    try:
        source, inventory, detected, stats = _load_inventory(core, q, 80)
    except Exception:
        source, inventory, detected, stats = "unavailable", [], {}, {}
    try:
        requirements = _load_requirements(core, q, 80)
    except Exception:
        requirements = []

    inv_html = "".join(_property_card(x) for x in inventory[:12]) or "<div class='empty'>No clean availability found.</div>"
    req_html = "".join(_requirement_card(x) for x in requirements[:12]) or "<div class='empty'>No clean requirements found.</div>"
    body = (
        "<h2 class='sectiontitle'>Latest Requirements</h2><div class='grid'>" + req_html + "</div>"
        "<h2 class='sectiontitle'>Latest Availability</h2><div class='grid'>" + inv_html + "</div>"
    )
    counters = (
        f"<b>{len(requirements)}</b> requirements preview · "
        f"<b>{len(inventory)}</b> availability preview · "
        f"WhatsApp source <b>{_esc(source)}</b>"
    )
    return _page(
        "Property Newspaper",
        "Clean WhatsApp property intelligence: Requirements and Availability separated, each property shown as one structured entity.",
        body, q, counters
    )


def api_status(core):
    result = {
        "status": "OK",
        "version": VERSION,
        "routes": ["/whatsapp-live", "/whatsapp-live/requirements", "/whatsapp-live/feed"],
        "entity_first": True,
        "raw_message_hidden_by_default": True,
        "production_stability_changed": False,
    }
    try:
        st = legacy.status(core)
        result["source"] = st.get("selected_source")
        result["wa_properties_count"] = st.get("wa_properties_count")
        result["wa_requirements_count"] = st.get("wa_requirements_count")
    except Exception as exc:
        result["source_status_error"] = f"{type(exc).__name__}: {exc}"
    return result


def register(wrapped):
    app = wrapped.app
    core = wrapped.core

    owned = {
        "/whatsapp-live",
        "/whatsapp-live/feed",
        "/whatsapp-live/requirements",
        "/api/whatsapp-newspaper/status",
    }

    app.router.routes[:] = [
        r for r in app.router.routes
        if not (
            getattr(r, "path", None) in owned
            and "GET" in (getattr(r, "methods", set()) or set())
        )
    ]

    def main(request: Request):
        return main_page(request, core)

    def availability(request: Request):
        return availability_page(request, core)

    def requirements(request: Request):
        return requirements_page(request, core)

    def status():
        return api_status(core)

    app.add_api_route("/whatsapp-live", main, methods=["GET"])
    app.add_api_route("/whatsapp-live/feed", availability, methods=["GET"])
    app.add_api_route("/whatsapp-live/requirements", requirements, methods=["GET"])
    app.add_api_route("/api/whatsapp-newspaper/status", status, methods=["GET"])

    return {"status": "REGISTERED", "version": VERSION, "owned_routes": sorted(owned)}
