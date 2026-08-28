from __future__ import annotations

import hashlib
import re
import unicodedata

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

VERSION = "WHATSAPP-CLEAN-DATABASE-3.8-ENTITY-FIRST"


def _esc(v):
    return (
        str(v or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _wa_text_normalize(value):
    """Normalize WhatsApp decorative Unicode without changing meaning."""
    txt = unicodedata.normalize("NFKC", str(value or ""))
    txt = txt.replace("\u00a0", " ").replace("\u200b", "").replace("\u200e", "").replace("\u200f", "")
    txt = txt.replace("–", "-").replace("—", "-")
    txt = re.sub(r"[ \t]+", " ", txt)
    return txt.strip()


def _clean_classify_text(value):
    """
    Accuracy-first role classifier.
    Demand intent wins over generic words such as 'rent' or 'sale'.
    """
    txt = _wa_text_normalize(value)
    low = txt.lower()

    demand_patterns = [
        r"\brequire(?:d|ment|ments)?\b", r"\blooking\s+for\b", r"\bneed(?:ed|s)?\b",
        r"\bwanted\b", r"\bclient\s+(?:looking|requires?|needs?)\b",
        r"\bbuyer\s+requirement\b", r"\btenant\s+requirement\b",
        r"\bmandate\b", r"\burgent\s+requirement\b",
    ]
    supply_patterns = [
        r"\bavailable\b", r"\bavailability\b", r"\bfor\s+sale\b",
        r"\bfor\s+rent\b", r"\bfor\s+lease\b", r"\bready\s+to\s+move\b",
        r"\bvacant\b", r"\bdeal\s+available\b", r"\binventory\b",
    ]
    property_patterns = [
        r"\bbhk\b", r"\bflat\b", r"\bapartment\b", r"\bvilla\b", r"\bkothi\b",
        r"\bshop\b", r"\bshowroom\b", r"\boffice\b", r"\bwarehouse\b",
        r"\bplot\b", r"\bfarm\s*house\b", r"\brestaurant\b", r"\bbanquet\b",
        r"\bhotel\b", r"\bguest\s*house\b", r"\bsq\.?\s*ft\b", r"\bsqft\b",
        r"\byards?\b", r"\bgaj\b", r"\bsq\.?\s*m\b", r"\bsqm\b",
    ]

    demand = sum(bool(re.search(p, low, re.I)) for p in demand_patterns)
    supply = sum(bool(re.search(p, low, re.I)) for p in supply_patterns)
    prop = sum(bool(re.search(p, low, re.I)) for p in property_patterns)

    # Explicit demand phrases have priority, including "wanted for rent".
    if demand > 0:
        return "PROPERTY_REQUIREMENT", min(.99, .82 + .035*demand + .012*prop)

    if supply > 0 and prop > 0:
        return "PROPERTY_INVENTORY", min(.99, .80 + .035*supply + .012*prop)

    # Strong property description with price/contact but no explicit availability.
    if prop >= 2 and (re.search(r"(?:₹|rs\.?|inr|\b\d+(?:\.\d+)?\s*(?:k|lac|lakh|cr|crore)\b)", low, re.I)
                      or re.search(r"(?<!\d)[6-9]\d{9}(?!\d)", re.sub(r"\D", "", txt))):
        return "PROPERTY_INVENTORY", min(.95, .72 + .025*prop)

    # Contact/business-card only.
    digits = re.sub(r"\D", "", txt)
    if any(ch.isdigit() for ch in digits) and len(digits) >= 10:
        return "PROPERTY_CONTACT", .72

    return "NEEDS_REVIEW", .55


def _clean_is_noise_text(value):
    txt = _wa_text_normalize(value)
    low = txt.lower().strip()
    kind, _ = _clean_classify_text(txt)
    if kind in {"PROPERTY_REQUIREMENT", "PROPERTY_INVENTORY"}:
        return False, ""
    if re.match(r"^(good morning|gm|good evening|good night|thanks|thank you|ok|okay|noted|shared)\b", low):
        return True, "Greeting/non-transactional message"
    if "instagram.com/" in low or "youtube.com/" in low or "facebook.com/" in low:
        return True, "Link-only/non-property message"
    if len(re.sub(r"\W", "", low)) < 8:
        return True, "Too little actionable content"
    return kind == "NEEDS_REVIEW", "No reliable actionable property signal" if kind == "NEEDS_REVIEW" else ""


def _clean_split_inventory_text(value, fallback_splitter=None):
    """
    Entity-first splitter.
    Keeps one physical property per visible/database row where the message
    contains clear repeated blocks. Conservative fallback prevents over-split.
    """
    raw_original = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    raw = _wa_text_normalize(raw_original)
    if not raw:
        return []

    # First retain the proven legacy splitter when it already finds entities.
    if fallback_splitter:
        try:
            legacy = [str(x or "").strip() for x in fallback_splitter(raw) if str(x or "").strip()]
            if len(legacy) > 1:
                return legacy
        except Exception:
            pass

    # Emoji/Unicode numbered bullets: 1️⃣ / 2️⃣ etc.
    emoji_num = re.split(r"(?m)(?=^\s*[0-9]{1,2}\s*[️⃣\u20e3])", raw)
    emoji_num = [x.strip() for x in emoji_num if x.strip()]
    if len(emoji_num) >= 2:
        strong = sum(1 for x in emoji_num if re.search(r"\b(?:bhk|sqft|sq ft|yard|gaj|rent|price|cr|lakh|lac)\b", x, re.I))
        if strong >= 2:
            return emoji_num

    # Blank-line portfolio blocks. Common header/contact are carried into children.
    paras = [re.sub(r"\n+", "\n", x).strip() for x in re.split(r"\n\s*\n+", raw) if x.strip()]
    if len(paras) >= 3:
        def prop_block(x):
            low = x.lower()
            spec = bool(re.search(r"\b(?:\d+\s*bhk|sqft|sq ft|sq\.?\s*yd|yards?|gaj|sq\.?\s*m|sqm)\b", low))
            money = bool(re.search(r"(?:₹|rs\.?|inr|\b\d+(?:\.\d+)?\s*(?:k|lac|lakh|lakhs|cr|crore|crores)\b)", low))
            tx = bool(re.search(r"\b(?:rent|sale|lease|available|asking|demand|price)\b", low))
            return (spec and (money or tx)) or (money and tx)

        candidates = [i for i, x in enumerate(paras) if prop_block(x)]
        if len(candidates) >= 2:
            prefix = []
            suffix = []
            first_i, last_i = candidates[0], candidates[-1]
            for x in paras[:first_i]:
                if len(x) <= 220 and not re.search(r"(?<!\d)[6-9]\d{9}(?!\d)", re.sub(r"\D", "", x)):
                    prefix.append(x)
            for x in paras[last_i+1:]:
                if re.search(r"(?:contact|call|mob|phone|dm|whatsapp)", x, re.I) or re.search(r"\d{10}", re.sub(r"\D", "", x)):
                    suffix.append(x)

            out = []
            for i in candidates:
                child_parts = prefix[-1:] + [paras[i]] + suffix[:1]
                child = "\n\n".join(x for x in child_parts if x).strip()
                if child:
                    out.append(child)
            if len(out) >= 2:
                return out

    # Repeated short project/name headings followed by specs.
    lines = [x.strip() for x in raw.splitlines() if x.strip()]
    anchors = []
    generic = {"available for rent","available for sale","inventory for sale","deal available on sale",
               "properties available","contact","for rent","for sale"}
    for i, line in enumerate(lines):
        low = line.lower().strip("*_ -")
        if not low or low in generic or len(line) > 65:
            continue
        alpha = re.sub(r"[^A-Za-z]", "", line)
        upper_ratio = (sum(c.isupper() for c in alpha) / max(1, len(alpha))) if alpha else 0
        follow = " ".join(lines[i+1:i+4])
        if upper_ratio >= .65 and re.search(r"\b(?:bhk|sqft|sq ft|rent|price|cr|lakh|lac|yard|gaj)\b", follow, re.I):
            anchors.append(i)

    if len(anchors) >= 2:
        out = []
        common_tail = ""
        for j, a in enumerate(anchors):
            b = anchors[j+1] if j+1 < len(anchors) else len(lines)
            child_lines = lines[a:b]
            child = "\n".join(child_lines).strip()
            if re.search(r"\b(?:bhk|sqft|sq ft|rent|price|cr|lakh|lac|yard|gaj)\b", child, re.I):
                out.append(child)
        if len(out) >= 2:
            return out

    return [raw]


def _patch_runtime_whatsapp_engine():
    """Patch both modules in memory so new live intake and display share one policy."""
    try:
        import whatsapp_intelligence as wi
        old_split = getattr(wi, "split_inventory", None)

        def split_fixed(txt):
            return _clean_split_inventory_text(txt, old_split)

        wi.classify = _clean_classify_text
        wi.is_noise = _clean_is_noise_text
        wi.split_inventory = split_fixed

        # The bridge imported functions with `from whatsapp_intelligence import ...`.
        # Replace those bound references too if the bridge is already loaded.
        try:
            import whatsapp_live_bridge as bridge
            bridge.classify = _clean_classify_text
            bridge.is_noise = _clean_is_noise_text
            bridge.split_inventory = split_fixed
        except Exception:
            pass

        return {"patched": True, "unicode_normalization": True, "entity_splitter": True}
    except Exception as exc:
        return {"patched": False, "error": f"{type(exc).__name__}: {exc}"}


def _fmt_money(v):
    if v in (None, "", "UNKNOWN"):
        return "—"
    try:
        n = float(v)
        if n >= 10_000_000:
            return f"₹{n/10_000_000:.2f} Cr"
        if n >= 100_000:
            return f"₹{n/100_000:.2f} L"
        return f"₹{n:,.0f}"
    except Exception:
        return str(v)


def _configuration(raw):
    txt = _wa_text_normalize(raw)
    bits = []
    m = re.search(r"\b(\d+(?:\.\d+)?)\s*BHK\b", txt, re.I)
    if m:
        bits.append(f"{m.group(1)} BHK")
    for label, pat in [
        ("Furnished", r"\bfully\s+furnished\b"),
        ("Semi Furnished", r"\bsemi\s*furnished\b"),
        ("Unfurnished", r"\bunfurnished\b"),
    ]:
        if re.search(pat, txt, re.I):
            bits.append(label)
            break
    return " · ".join(bits) or "—"


def _database_shell(title, subtitle, body, active=""):
    nav = [
        ("Live Database", "/whatsapp-live/feed"),
        ("Requirements", "/whatsapp-live/requirements"),
        ("Availability", "/whatsapp-live/availability"),
        ("AI Property Finder", "/property-finder"),
        ("Sources", "/whatsapp-live/sources"),
        ("Dashboard", "/whatsapp-live"),
    ]
    links = "".join(
        f"<a class='{'active' if name==active else ''}' href='{url}'>{_esc(name)}</a>"
        for name, url in nav
    )
    return f"""<!doctype html><html><head><meta charset='utf-8'>
    <meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>{_esc(title)}</title>
    <style>
    *{{box-sizing:border-box}} body{{margin:0;font-family:Arial,Helvetica,sans-serif;background:#f6f3ee;color:#28231f}}
    header{{background:#3f352d;color:white;padding:20px 24px}}
    header h1{{margin:0 0 4px;font-size:24px}} header small{{color:#ded4cb}}
    nav{{display:flex;gap:7px;flex-wrap:wrap;background:white;border-bottom:1px solid #ddd3c9;padding:9px 18px;position:sticky;top:0;z-index:20}}
    nav a{{text-decoration:none;color:#493d34;padding:8px 10px;border-radius:7px;font-size:13px}}
    nav a.active,nav a:hover{{background:#4d4036;color:white}}
    main{{max-width:1900px;margin:18px auto;padding:0 14px}}
    .card{{background:white;border:1px solid #ded5cc;border-radius:10px;padding:13px;margin-bottom:12px}}
    .kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:9px;margin:10px 0}}
    .kpi{{background:#faf8f5;border:1px solid #e5ddd5;border-radius:8px;padding:10px}}
    .kpi b{{display:block;font-size:21px;margin-top:3px}}
    form.filters{{display:grid;grid-template-columns:minmax(240px,2fr) repeat(3,minmax(120px,1fr)) auto;gap:8px;align-items:end}}
    input,select{{width:100%;padding:9px;border:1px solid #cdbfb1;border-radius:6px;background:white}}
    button{{padding:10px 14px;border:0;border-radius:6px;background:#4d4036;color:white;font-weight:700;cursor:pointer}}
    .scroll{{overflow:auto;max-height:72vh;border:1px solid #e4dcd4;border-radius:8px}}
    table{{width:100%;border-collapse:collapse;background:white;font-size:12px;min-width:1450px}}
    th,td{{padding:8px 9px;border-bottom:1px solid #ece5df;text-align:left;vertical-align:top}}
    th{{background:#ede5dd;position:sticky;top:0;z-index:2;white-space:nowrap}}
    tr:hover td{{background:#fcfaf7}}
    .entity{{min-width:320px;white-space:pre-wrap;max-width:500px}}
    .phone{{white-space:nowrap;font-weight:700}}
    .badge{{display:inline-block;padding:3px 7px;border-radius:999px;font-size:11px;font-weight:700;background:#eee7df}}
    .supply{{background:#e8f5ec;color:#17643a}} .demand{{background:#fff1d6;color:#805800}}
    .muted{{color:#766b63}} .small{{font-size:11px}}
    @media(max-width:900px){{form.filters{{grid-template-columns:1fr 1fr}}}}
    </style></head><body>
    <header><h1>{_esc(title)}</h1><small>{_esc(subtitle)}</small></header>
    <nav>{links}</nav><main>{body}</main></body></html>"""


def _source_engine(core):
    import alliance_v2_whatsapp_adapter as wa_adapter
    return wa_adapter._source_engine(core.engine)


def _exists(conn, table):
    return bool(
        conn.execute(
            text("SELECT to_regclass(:t)"),
            {"t": f"public.{table}"},
        ).scalar()
    )


def _count(conn, table, where_sql=""):
    sql = f"SELECT COUNT(*) FROM {table}"
    if where_sql:
        sql += f" WHERE {where_sql}"
    return int(conn.execute(text(sql)).scalar() or 0)


def _detect_source(conn):
    wa_properties = 0
    wai_listings = 0
    wa_requirements = 0

    if _exists(conn, "wa_properties"):
        wa_properties = _count(
            conn,
            "wa_properties",
            "COALESCE(record_status,'ACTIVE')='ACTIVE'",
        )

    if _exists(conn, "wa_requirements"):
        wa_requirements = _count(
            conn,
            "wa_requirements",
            "COALESCE(status,'ACTIVE')='ACTIVE'",
        )

    if _exists(conn, "wai_listings"):
        wai_listings = _count(conn, "wai_listings")

    selected = (
        "wa_properties"
        if wa_properties > 0
        else "wai_listings"
        if wai_listings > 0
        else "none"
    )

    return {
        "selected_source": selected,
        "wa_properties_count": wa_properties,
        "wa_requirements_count": wa_requirements,
        "wai_listings_count": wai_listings,
    }


def status(core):
    engine, dispose = _source_engine(core)
    out = {
        "status": "OK",
        "version": VERSION,
        "mode": "READ_EXISTING_PROVEN_LEADS_WITH_VISIBLE_ENTITY_SPLIT",
        "using_separate_whatsapp_database": bool(dispose),
        "selected_source": "none",
        "wa_properties_count": 0,
        "wa_requirements_count": 0,
        "wai_listings_count": 0,
    }
    try:
        with engine.connect() as conn:
            out.update(_detect_source(conn))
    except Exception as exc:
        out["status"] = "ERROR"
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if dispose:
            engine.dispose()
    return out


def _rows_from_wa_properties(conn, q, limit):
    params = {"lim": limit}
    where = ["COALESCE(record_status,'ACTIVE')='ACTIVE'"]

    if q:
        where.append(
            """(
              COALESCE(location,'') ILIKE :q OR
              COALESCE(locality,'') ILIKE :q OR
              COALESCE(property_type,'') ILIKE :q OR
              COALESCE(raw_text,'') ILIKE :q OR
              COALESCE(parent_message_text,'') ILIKE :q OR
              COALESCE(broker_name,'') ILIKE :q OR
              COALESCE(broker_phone,'') ILIKE :q OR
              COALESCE(owner_name,'') ILIKE :q OR
              COALESCE(owner_phone,'') ILIKE :q OR
              COALESCE(sender_name,'') ILIKE :q OR
              COALESCE(sender_phone,'') ILIKE :q
            )"""
        )
        params["q"] = "%" + q + "%"

    sql = f"""
      SELECT
        id,
        wa_property_id,
        source_item_no,
        first_seen,
        last_seen,
        property_type,
        transaction_type,
        city,
        location,
        locality,
        address,
        landmark,
        area_sqft,
        available_area_sqft,
        floor,
        frontage,
        rent_inr,
        sale_price_inr,
        cam_inr,
        possession,
        parking,
        suitable_for,
        nearby_brands,
        availability,
        broker_name,
        broker_phone,
        owner_name,
        owner_phone,
        sender_name,
        sender_phone,
        duplicate_status,
        confidence,
        raw_text,
        parent_message_text
      FROM wa_properties
      WHERE {" AND ".join(where)}
      ORDER BY COALESCE(last_seen,first_seen) DESC NULLS LAST, id DESC
      LIMIT :lim
    """
    return conn.execute(text(sql), params).mappings().all()


def _rows_from_wai_listings(conn, q, limit):
    params = {"lim": limit}
    where = [
        "COALESCE(l.status,'') NOT IN ('REJECTED','AUTO_REJECT','AUTO_REJECTED')",
        "COALESCE(l.raw_listing_text,l.summary,'') <> ''",
    ]

    if q:
        where.append(
            """(
              COALESCE(l.location,'') ILIKE :q OR
              COALESCE(l.property_type,'') ILIKE :q OR
              COALESCE(l.raw_listing_text,'') ILIKE :q OR
              COALESCE(l.summary,'') ILIKE :q OR
              COALESCE(l.source_group_name,'') ILIKE :q OR
              COALESCE(l.poster_name,'') ILIKE :q OR
              COALESCE(ct.phone,'') ILIKE :q OR
              COALESCE(ct.display_name,'') ILIKE :q
            )"""
        )
        params["q"] = "%" + q + "%"

    sql = f"""
      SELECT
        l.id,
        l.id::text AS wa_property_id,
        1 AS source_item_no,
        COALESCE(rm.sent_at,l.created_at)::text AS first_seen,
        COALESCE(l.verified_at,l.created_at,rm.sent_at)::text AS last_seen,
        l.property_type,
        CASE
          WHEN lower(COALESCE(l.transaction,'')) IN ('rent','lease','leasing') THEN 'LEASE'
          WHEN lower(COALESCE(l.transaction,'')) IN ('sale','sell','selling') THEN 'SALE'
          ELSE COALESCE(l.transaction,'UNKNOWN')
        END AS transaction_type,
        NULL::text AS city,
        l.location,
        NULL::text AS locality,
        NULL::text AS address,
        NULL::text AS landmark,
        l.area_sqft_numeric AS area_sqft,
        l.area_sqft_numeric AS available_area_sqft,
        NULL::text AS floor,
        NULL::text AS frontage,
        CASE
          WHEN lower(COALESCE(l.transaction,'')) IN ('rent','lease','leasing') THEN l.budget_numeric
          ELSE NULL
        END AS rent_inr,
        CASE
          WHEN lower(COALESCE(l.transaction,'')) IN ('sale','sell','selling') THEN l.budget_numeric
          ELSE NULL
        END AS sale_price_inr,
        NULL::numeric AS cam_inr,
        NULL::text AS possession,
        NULL::text AS parking,
        NULL::text AS suitable_for,
        NULL::text AS nearby_brands,
        'UNKNOWN'::text AS availability,
        COALESCE(ct.display_name,l.poster_name) AS broker_name,
        COALESCE(ct.phone,rm.sender_phone) AS broker_phone,
        NULL::text AS owner_name,
        NULL::text AS owner_phone,
        COALESCE(rm.sender_display_name,l.poster_name) AS sender_name,
        COALESCE(rm.sender_phone,ct.phone) AS sender_phone,
        CASE WHEN l.duplicate_of IS NULL THEN 'UNIQUE' ELSE 'POSSIBLE_DUPLICATE' END AS duplicate_status,
        l.confidence_score AS confidence,
        COALESCE(NULLIF(l.raw_listing_text,''),l.summary,'') AS raw_text,
        COALESCE(NULLIF(l.raw_listing_text,''),l.summary,'') AS parent_message_text
      FROM wai_listings l
      LEFT JOIN wai_contacts ct ON ct.id=l.contact_id
      LEFT JOIN wai_raw_messages rm ON rm.id=l.source_message_id
      WHERE {" AND ".join(where)}
      ORDER BY l.created_at DESC NULLS LAST, l.id DESC
      LIMIT :lim
    """
    return conn.execute(text(sql), params).mappings().all()


def get_rows(core, q="", limit=500):
    engine, dispose = _source_engine(core)
    try:
        with engine.connect() as conn:
            detected = _detect_source(conn)
            source = detected["selected_source"]

            if source == "wa_properties":
                return source, _rows_from_wa_properties(conn, q, limit), detected

            if source == "wai_listings":
                return source, _rows_from_wai_listings(conn, q, limit), detected

            return "none", [], detected
    finally:
        if dispose:
            engine.dispose()


def _norm_entity(v):
    return re.sub(r"\s+", " ", str(v or "")).strip().lower()


def _visible_entities(rows):
    """
    Historical wa_properties may contain old multi-property raw_text values.
    For display only, use the proven split_inventory() again and flatten rows.
    No database writes occur.
    """
    from whatsapp_intelligence import split_inventory

    out = []
    seen = set()

    for row in rows:
        d = dict(row)
        raw = str(d.get("raw_text") or "").strip()
        parent = str(d.get("parent_message_text") or "").strip()

        # Use raw_text first. If it is suspiciously identical to a long parent message,
        # split the parent. This handles historical rows created before splitter fixes.
        candidate = raw or parent

        try:
            parts = split_inventory(candidate) if candidate else []
        except Exception:
            parts = [candidate] if candidate else []

        if not parts:
            parts = [candidate] if candidate else [""]

        # If raw_text is one clean item but parent contains many items, split parent
        # only when raw_text == parent or raw_text is empty.
        if parent and (not raw or _norm_entity(raw) == _norm_entity(parent)):
            try:
                parent_parts = split_inventory(parent)
                if len(parent_parts) > len(parts):
                    parts = parent_parts
            except Exception:
                pass

        for idx, part in enumerate(parts, start=1):
            part = str(part or "").strip()
            if not part:
                continue

            fp = hashlib.sha1(
                (
                    str(d.get("wa_property_id") or d.get("id") or "")
                    + "|"
                    + _norm_entity(part)
                ).encode("utf-8")
            ).hexdigest()

            if fp in seen:
                continue
            seen.add(fp)

            x = dict(d)
            x["visible_entity_no"] = idx
            x["visible_entity_text"] = part
            x["visible_entity_id"] = (
                f"{d.get('wa_property_id') or d.get('id')}-E{idx}"
            )
            out.append(x)

    return out



def _clean_visible_rows(rows, min_score=60):
    """
    Apply the existing Alliance WhatsApp purity engine to stored wa_properties
    rows without rewriting the database.

    Main feed policy:
      - supply only
      - not inactive/closed/deleted
      - no requirement-like text
      - recovered transaction/location/type where possible
      - safe area and price recovery
      - quality >= min_score
      - collapse exact duplicate fingerprints in the visible result
    """
    from alliance_v2_whatsapp_purity import (
        detect_transaction,
        canonical_location,
        detect_property_type,
        recover_area,
        recover_budget,
        quality_score,
    )

    out = []
    seen = set()
    stats = {
        "input_entities": 0,
        "kept": 0,
        "requirements_removed": 0,
        "inactive_removed": 0,
        "low_quality_removed": 0,
        "duplicate_removed": 0,
        "unknown_removed": 0,
    }

    for row in rows:
        stats["input_entities"] += 1
        d = dict(row)
        raw = _wa_text_normalize(d.get("visible_entity_text") or d.get("raw_text") or "")
        current_tx = d.get("transaction_type")
        current_type = d.get("property_type")
        current_loc = d.get("location") or d.get("locality")
        availability = str(d.get("availability") or "").strip().lower()

        if availability in {"inactive", "closed", "deleted", "unavailable"}:
            stats["inactive_removed"] += 1
            continue

        role, tx, tx_conf, tx_reason = detect_transaction(current_tx, raw)
        if role == "REQUIREMENT":
            stats["requirements_removed"] += 1
            continue

        if role != "SUPPLY" or tx == "UNKNOWN":
            stats["unknown_removed"] += 1
            continue

        loc = canonical_location(
            d.get("location"),
            d.get("locality"),
            d.get("address"),
            raw,
        )
        typ, type_conf, type_reason = detect_property_type(current_type, raw)

        area_text = (
            str(d.get("available_area_sqft") or "")
            if d.get("available_area_sqft") not in (None, "")
            else str(d.get("area_sqft") or "")
        )
        area_numeric = d.get("available_area_sqft") or d.get("area_sqft")
        amin, amax = recover_area(area_text, area_numeric, raw)

        if tx == "LEASE":
            budget_numeric = d.get("rent_inr")
        elif tx == "SALE":
            budget_numeric = d.get("sale_price_inr")
        else:
            budget_numeric = d.get("rent_inr") or d.get("sale_price_inr")

        budget = recover_budget(None, budget_numeric, raw)

        phone_present = bool(
            d.get("owner_phone")
            or d.get("broker_phone")
            or d.get("sender_phone")
        )

        raw_conf = d.get("confidence")
        verified = str(d.get("availability") or "").upper() == "VERIFIED"

        score = quality_score(
            tx,
            loc,
            typ,
            amin,
            phone_present,
            raw_conf,
            verified,
        )

        if score < min_score:
            stats["low_quality_removed"] += 1
            continue

        # Exact visible duplicate barrier.
        fp = "|".join(
            [
                str(loc or "").strip().lower(),
                str(typ or "").strip().lower(),
                str(tx or "").strip().lower(),
                str(round(float(amin or 0), -1) if amin else 0),
                str(round(float(budget or 0), -1) if budget else 0),
                str(d.get("broker_phone") or d.get("owner_phone") or d.get("sender_phone") or "").strip(),
                re.sub(r"\W+", "", raw.lower())[:180],
            ]
        )

        if fp in seen:
            stats["duplicate_removed"] += 1
            continue
        seen.add(fp)

        d["clean_role"] = role
        d["clean_transaction"] = tx
        d["clean_transaction_confidence"] = tx_conf
        d["clean_transaction_reason"] = tx_reason
        d["clean_location"] = loc
        d["clean_property_type"] = typ
        d["clean_property_type_confidence"] = type_conf
        d["clean_property_type_reason"] = type_reason
        d["clean_area_min_sqft"] = amin
        d["clean_area_max_sqft"] = amax
        d["clean_budget"] = budget
        d["clean_score"] = score
        out.append(d)
        stats["kept"] += 1

    return out, stats


def _priority_score(row):
    """
    Business-priority score for already-clean property entities.
    Keeps purity separate from prioritization.
    """
    import math
    from datetime import datetime, timezone

    score = 0.0
    reasons = []

    # Base purity contribution.
    purity = float(row.get("clean_score") or 0)
    score += min(35.0, purity * 0.35)
    if purity >= 75:
        reasons.append("high purity")

    # Contact usability.
    has_owner = bool(row.get("owner_phone"))
    has_broker = bool(row.get("broker_phone"))
    has_sender = bool(row.get("sender_phone"))
    if has_owner:
        score += 16
        reasons.append("owner contact")
    elif has_broker:
        score += 12
        reasons.append("broker contact")
    elif has_sender:
        score += 8
        reasons.append("sender contact")

    # Core commercial fields.
    if row.get("clean_location"):
        score += 8
        reasons.append("location clear")
    if row.get("clean_property_type") and str(row.get("clean_property_type")).upper() != "UNKNOWN":
        score += 6
        reasons.append("type clear")
    if row.get("clean_transaction") and str(row.get("clean_transaction")).upper() != "UNKNOWN":
        score += 6
        reasons.append("transaction clear")
    if row.get("clean_area_min_sqft"):
        score += 8
        reasons.append("area available")
    if row.get("clean_budget"):
        score += 8
        reasons.append("price/rent available")

    # Availability / freshness.
    availability = str(row.get("availability") or "").upper()
    if availability in {"VERIFIED", "AVAILABLE", "ACTIVE"}:
        score += 5
        reasons.append("availability positive")

    dt = row.get("last_seen") or row.get("first_seen")
    age_days = None
    if dt:
        try:
            if isinstance(dt, str):
                x = dt.replace("Z", "+00:00")
                parsed = datetime.fromisoformat(x)
            else:
                parsed = dt
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            age_days = max(0, (datetime.now(timezone.utc) - parsed).total_seconds() / 86400)
        except Exception:
            age_days = None

    if age_days is not None:
        if age_days <= 3:
            score += 8
            reasons.append("very fresh")
        elif age_days <= 7:
            score += 6
            reasons.append("fresh")
        elif age_days <= 30:
            score += 3

    # Duplicate risk penalty.
    dup = str(row.get("duplicate_status") or "").upper()
    if "DUPLICATE" in dup and "UNIQUE" not in dup:
        score -= 10
        reasons.append("duplicate risk")

    score = round(max(0.0, min(100.0, score)), 2)

    if score >= 80:
        band = "HOT"
    elif score >= 65:
        band = "STRONG"
    else:
        band = "REVIEW"

    row = dict(row)
    row["priority_score"] = score
    row["priority_band"] = band
    row["priority_reason"] = ", ".join(reasons[:6])
    return row


def _prioritize_rows(rows):
    ranked = [_priority_score(x) for x in rows]
    ranked.sort(
        key=lambda x: (
            0 if x["priority_band"] == "HOT" else 1 if x["priority_band"] == "STRONG" else 2,
            -float(x.get("priority_score") or 0),
            str(x.get("last_seen") or x.get("first_seen") or ""),
        )
    )
    return ranked


MARKET_EQUIVALENTS = {
    "Delhi NCR": {
        "restaurant": {
            "Saket": ["Malviya Nagar", "Hauz Khas", "Greater Kailash 1", "Greater Kailash 2", "Vasant Kunj"],
            "Greater Kailash 1": ["Greater Kailash 2", "Defence Colony", "Kailash Colony", "Nehru Place", "Saket"],
            "Greater Kailash 2": ["Greater Kailash 1", "Defence Colony", "Kailash Colony", "Saket", "CR Park"],
            "Defence Colony": ["Greater Kailash 1", "Greater Kailash 2", "Lajpat Nagar", "South Extension", "Kailash Colony"],
            "Connaught Place": ["Khan Market", "Lodhi Colony", "Defence Colony", "South Extension", "Aerocity"],
            "Rajouri Garden": ["Punjabi Bagh", "Janakpuri", "Kirti Nagar", "Pitampura", "Dwarka"],
            "Noida": ["Sector 18 Noida", "Sector 104 Noida", "Sector 75 Noida", "Sector 142 Noida", "Greater Noida"],
            "Gurgaon": ["Golf Course Road", "Cyber Hub", "Sector 29 Gurgaon", "Sector 56 Gurgaon", "Sohna Road"],
        },
        "retail": {
            "Saket": ["Malviya Nagar", "Hauz Khas", "Greater Kailash 1", "Nehru Place", "Vasant Kunj"],
            "Connaught Place": ["Khan Market", "Janpath", "Karol Bagh", "South Extension", "Lajpat Nagar"],
            "Rajouri Garden": ["Punjabi Bagh", "Janakpuri", "Tilak Nagar", "Pitampura", "Dwarka"],
            "Gurgaon": ["Golf Course Road", "MG Road Gurgaon", "Sohna Road", "Sector 56 Gurgaon", "Cyber Hub"],
            "Noida": ["Sector 18 Noida", "Sector 104 Noida", "Sector 75 Noida", "Sector 137 Noida", "Greater Noida"],
        },
        "residential_sale": {
            "Saket": ["Greater Kailash 1", "Greater Kailash 2", "Malviya Nagar", "Panchsheel Park", "Vasant Kunj"],
            "Greater Kailash 1": ["Greater Kailash 2", "Panchsheel Park", "Defence Colony", "Kailash Colony", "Saket"],
            "Greater Kailash 2": ["Greater Kailash 1", "Panchsheel Park", "CR Park", "Defence Colony", "Saket"],
            "Vasant Kunj": ["Vasant Vihar", "Saket", "Chattarpur", "Panchsheel Park", "Greater Kailash"],
            "Gurgaon": ["Golf Course Road", "Golf Course Extension", "DLF Phase 1", "DLF Phase 2", "Sector 56 Gurgaon"],
            "Noida": ["Sector 44 Noida", "Sector 93 Noida", "Sector 100 Noida", "Sector 104 Noida", "Sector 137 Noida"],
        },
        "commercial_sale": {
            "Saket": ["Nehru Place", "Okhla", "Jasola", "Greater Kailash", "Malviya Nagar"],
            "Connaught Place": ["Barakhamba Road", "ITO", "Karol Bagh", "Nehru Place", "Aerocity"],
            "Gurgaon": ["Golf Course Road", "MG Road Gurgaon", "Cyber City", "Sohna Road", "Sector 44 Gurgaon"],
            "Noida": ["Sector 18 Noida", "Sector 62 Noida", "Sector 125 Noida", "Sector 132 Noida", "Sector 142 Noida"],
        },
        "office_lease": {
            "Saket": ["Nehru Place", "Jasola", "Okhla", "Greater Kailash", "Malviya Nagar"],
            "Connaught Place": ["Barakhamba Road", "ITO", "Karol Bagh", "Nehru Place", "Aerocity"],
            "Gurgaon": ["Cyber City", "Golf Course Road", "MG Road Gurgaon", "Sector 44 Gurgaon", "Sohna Road"],
            "Noida": ["Sector 62 Noida", "Sector 125 Noida", "Sector 132 Noida", "Sector 142 Noida", "Sector 18 Noida"],
            "Rajouri Garden": ["Kirti Nagar", "Punjabi Bagh", "Janakpuri", "Pitampura", "Naraina"],
        },
        "residential_lease": {
            "Saket": ["Greater Kailash 1", "Greater Kailash 2", "Malviya Nagar", "Panchsheel Park", "Vasant Kunj"],
            "Greater Kailash 1": ["Greater Kailash 2", "Defence Colony", "Panchsheel Park", "Kailash Colony", "Saket"],
            "Greater Kailash 2": ["Greater Kailash 1", "CR Park", "Panchsheel Park", "Defence Colony", "Saket"],
            "Vasant Kunj": ["Vasant Vihar", "Saket", "Chattarpur", "Panchsheel Park", "Greater Kailash"],
            "Gurgaon": ["Golf Course Road", "DLF Phase 1", "DLF Phase 2", "Golf Course Extension", "Sector 56 Gurgaon"],
            "Noida": ["Sector 44 Noida", "Sector 93 Noida", "Sector 100 Noida", "Sector 104 Noida", "Sector 137 Noida"],
        },
        "warehouse_industrial": {
            "Delhi": ["Naraina", "Mayapuri", "Okhla", "Mundka", "Bawana"],
            "Gurgaon": ["Udyog Vihar", "Manesar", "Bilaspur", "Sohna Road", "Pataudi Road"],
            "Noida": ["Sector 63 Noida", "Sector 67 Noida", "Sector 80 Noida", "Sector 83 Noida", "Greater Noida"],
            "Faridabad": ["Mathura Road Faridabad", "Sector 24 Faridabad", "Sector 58 Faridabad", "Ballabgarh", "Prithla"],
            "Ghaziabad": ["Sahibabad", "Mohan Nagar", "Loni", "Meerut Road", "Dasna"],
        },
    },
    "Goa": {
        "restaurant": {
            "Siolim": ["Assagao", "Vagator", "Anjuna", "Morjim", "Mapusa"],
            "Assagao": ["Siolim", "Vagator", "Anjuna", "Parra", "Mapusa"],
            "Panaji": ["Porvorim", "Dona Paula", "Miramar", "Caranzalem", "Candolim"],
            "Calangute": ["Candolim", "Baga", "Anjuna", "Arpora", "Vagator"],
            "Margao": ["Colva", "Benaulim", "Navelim", "Fatorda", "Cavelossim"],
        },
        "residential_sale": {
            "Siolim": ["Assagao", "Vagator", "Morjim", "Anjuna", "Parra"],
            "Assagao": ["Siolim", "Vagator", "Anjuna", "Parra", "Mapusa"],
            "Panaji": ["Dona Paula", "Porvorim", "Miramar", "Caranzalem", "Taleigao"],
            "Calangute": ["Candolim", "Arpora", "Baga", "Nagoa", "Anjuna"],
            "Margao": ["Benaulim", "Colva", "Navelim", "Fatorda", "Cavelossim"],
        },
        "commercial_sale": {
            "Panaji": ["Porvorim", "Mapusa", "Margao", "Vasco da Gama", "Ponda"],
            "Mapusa": ["Porvorim", "Panaji", "Siolim", "Assagao", "Calangute"],
            "Margao": ["Panaji", "Vasco da Gama", "Ponda", "Colva", "Benaulim"],
        },
        "office_lease": {
            "Panaji": ["Porvorim", "Dona Paula", "Mapusa", "Margao", "Vasco da Gama"],
            "Porvorim": ["Panaji", "Mapusa", "Dona Paula", "Pilerne", "Candolim"],
            "Margao": ["Panaji", "Vasco da Gama", "Ponda", "Colva", "Benaulim"],
            "Mapusa": ["Porvorim", "Panaji", "Siolim", "Calangute", "Assagao"],
        },
        "residential_lease": {
            "Siolim": ["Assagao", "Vagator", "Morjim", "Anjuna", "Parra"],
            "Assagao": ["Siolim", "Vagator", "Anjuna", "Parra", "Mapusa"],
            "Panaji": ["Dona Paula", "Porvorim", "Miramar", "Caranzalem", "Taleigao"],
            "Porvorim": ["Panaji", "Mapusa", "Dona Paula", "Pilerne", "Candolim"],
            "Calangute": ["Candolim", "Arpora", "Baga", "Nagoa", "Anjuna"],
            "Margao": ["Benaulim", "Colva", "Navelim", "Fatorda", "Cavelossim"],
        },
        "warehouse_industrial": {
            "Panaji": ["Porvorim", "Pilerne", "Mapusa", "Vasco da Gama", "Verna"],
            "Margao": ["Verna", "Vasco da Gama", "Ponda", "Cuncolim", "Navelim"],
            "Mapusa": ["Porvorim", "Pilerne", "Panaji", "Thivim", "Colvale"],
            "Vasco da Gama": ["Verna", "Margao", "Panaji", "Ponda", "Cortalim"],
        },
    },
}


def _market_context(row):
    typ = str(row.get("clean_property_type") or row.get("property_type") or "").upper()
    tx = str(row.get("clean_transaction") or row.get("transaction_type") or "").upper()
    loc = str(row.get("clean_location") or row.get("location") or row.get("locality") or "").strip()

    hospitality = {"RESTAURANT","CAFE","BANQUET","HOTEL","GUEST_HOUSE","CLUB","LOUNGE"}
    retail = {"RETAIL_SHOP","COMMERCIAL_SHOP","COMMERCIAL_SHOWROOM","SHOWROOM","SHOP"}
    residential = {"RESIDENTIAL","VILLA","INDEPENDENT_HOUSE_VILLA","APARTMENT","FLAT","BUILDER_FLOOR"}
    office = {"OFFICE","COMMERCIAL_OFFICE","WORKSPACE"}
    industrial = {"WAREHOUSE","WAREHOUSE_INDUSTRIAL","INDUSTRIAL","FACTORY","GODOWN"}

    if typ in hospitality:
        context = "restaurant"
    elif typ in industrial:
        context = "warehouse_industrial"
    elif typ in office:
        context = "office_lease" if tx in {"LEASE","LEASE_OR_SALE"} else "commercial_sale"
    elif typ in residential:
        context = "residential_sale" if tx in {"SALE","LEASE_OR_SALE"} else "residential_lease"
    elif typ in retail:
        context = "retail" if tx in {"LEASE","LEASE_OR_SALE"} else "commercial_sale"
    elif tx in {"SALE","LEASE_OR_SALE"}:
        context = "commercial_sale"
    else:
        context = "office_lease"

    goa_locations = {
        "Siolim","Assagao","Vagator","Morjim","Anjuna","Panaji","Porvorim",
        "Calangute","Candolim","Margao","Mapusa","Benaulim","Colva","Vasco da Gama",
        "Dona Paula","Miramar","Caranzalem","Taleigao","Pilerne","Verna","Ponda"
    }
    region = "Goa" if loc in goa_locations else "Delhi NCR"
    return region, context, loc

def _alternative_markets(row, limit=5):
    region, context, loc = _market_context(row)
    context_map = MARKET_EQUIVALENTS.get(region, {}).get(context, {})

    # Exact mapped market first.
    if loc in context_map:
        return context_map[loc][:limit]

    # Broader city fallback for industrial/office use-cases.
    n = (loc or "").lower()
    aliases = [
        ("gurgaon", "Gurgaon"), ("gurugram", "Gurgaon"),
        ("noida", "Noida"), ("faridabad", "Faridabad"),
        ("ghaziabad", "Ghaziabad"), ("delhi", "Delhi"),
        ("panaji", "Panaji"), ("porvorim", "Porvorim"),
        ("margao", "Margao"), ("mapusa", "Mapusa"),
        ("vasco", "Vasco da Gama"),
    ]
    for token, canonical in aliases:
        if token in n and canonical in context_map:
            return context_map[canonical][:limit]

    # If location is missing, infer sensible city-level alternatives from region/context.
    if not loc:
        default_key = {
            ("Delhi NCR","warehouse_industrial"): "Gurgaon",
            ("Delhi NCR","office_lease"): "Gurgaon",
            ("Delhi NCR","retail"): "Saket",
            ("Delhi NCR","restaurant"): "Saket",
            ("Delhi NCR","residential_sale"): "Saket",
            ("Delhi NCR","residential_lease"): "Saket",
            ("Goa","warehouse_industrial"): "Panaji",
            ("Goa","office_lease"): "Panaji",
            ("Goa","restaurant"): "Siolim",
            ("Goa","residential_sale"): "Siolim",
            ("Goa","residential_lease"): "Siolim",
        }.get((region, context))
        if default_key and default_key in context_map:
            return context_map[default_key][:limit]

    return []

def _attach_market_intelligence(rows):
    out = []
    for row in rows:
        x = dict(row)
        region, context, loc = _market_context(x)
        x["market_region"] = region
        x["market_context"] = context
        x["alternative_markets"] = _alternative_markets(x)
        if x["alternative_markets"]:
            if loc:
                x["market_reason"] = f"Comparable {context.replace('_',' ')} markets to {loc}"
            else:
                x["market_reason"] = f"Suggested {context.replace('_',' ')} markets because location is missing"
        else:
            x["market_reason"] = "No mapped alternative found yet"
        out.append(x)
    return out


# ---------------------------------------------------------------------------
# PROPERTY FINDER AI 3.7
# Accuracy-first, explainable matching.
# Requirement -> hard gates -> exact-market scoring -> alternative-market
# scoring -> commercial fit -> data confidence -> ranked shortlist.
# ---------------------------------------------------------------------------

TYPE_FAMILIES = {
    "RESTAURANT": {"RESTAURANT", "CAFE", "LOUNGE", "CLUB", "BANQUET"},
    "CAFE": {"CAFE", "RESTAURANT", "LOUNGE"},
    "LOUNGE": {"LOUNGE", "CLUB", "RESTAURANT", "CAFE"},
    "CLUB": {"CLUB", "LOUNGE", "RESTAURANT"},
    "BANQUET": {"BANQUET", "HOTEL", "GUEST_HOUSE"},
    "HOTEL": {"HOTEL", "GUEST_HOUSE", "BANQUET"},
    "GUEST_HOUSE": {"GUEST_HOUSE", "HOTEL", "INDEPENDENT_HOUSE_VILLA"},
    "OFFICE": {"OFFICE", "COMMERCIAL_OFFICE", "WORKSPACE"},
    "COMMERCIAL_OFFICE": {"COMMERCIAL_OFFICE", "OFFICE", "WORKSPACE"},
    "COMMERCIAL_SHOP": {"COMMERCIAL_SHOP", "RETAIL_SHOP", "COMMERCIAL_SHOWROOM", "SHOWROOM", "SHOP"},
    "RETAIL_SHOP": {"RETAIL_SHOP", "COMMERCIAL_SHOP", "COMMERCIAL_SHOWROOM", "SHOWROOM", "SHOP"},
    "COMMERCIAL_SHOWROOM": {"COMMERCIAL_SHOWROOM", "SHOWROOM", "COMMERCIAL_SHOP", "RETAIL_SHOP"},
    "SHOWROOM": {"SHOWROOM", "COMMERCIAL_SHOWROOM", "COMMERCIAL_SHOP", "RETAIL_SHOP"},
    "WAREHOUSE_INDUSTRIAL": {"WAREHOUSE_INDUSTRIAL", "WAREHOUSE", "INDUSTRIAL", "FACTORY", "GODOWN"},
    "WAREHOUSE": {"WAREHOUSE", "WAREHOUSE_INDUSTRIAL", "INDUSTRIAL", "GODOWN"},
    "RESIDENTIAL": {"RESIDENTIAL", "APARTMENT", "FLAT", "BUILDER_FLOOR", "INDEPENDENT_HOUSE_VILLA", "VILLA"},
    "INDEPENDENT_HOUSE_VILLA": {"INDEPENDENT_HOUSE_VILLA", "VILLA", "RESIDENTIAL"},
    "VILLA": {"VILLA", "INDEPENDENT_HOUSE_VILLA", "RESIDENTIAL"},
}

LOCATION_ALIASES = {
    "gurugram": "gurgaon",
    "new delhi": "delhi",
    "greater kailash i": "greater kailash 1",
    "greater kailash ii": "greater kailash 2",
    "gk 1": "greater kailash 1",
    "gk1": "greater kailash 1",
    "gk 2": "greater kailash 2",
    "gk2": "greater kailash 2",
    "cp": "connaught place",
}


def _finder_norm(v):
    x = re.sub(r"[^a-z0-9]+", " ", str(v or "").lower()).strip()
    x = re.sub(r"\s+", " ", x)
    return LOCATION_ALIASES.get(x, x)


def _finder_float(v):
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return None


def _finder_tx(v):
    x = str(v or "").strip().upper()
    if x in {"RENT", "RENTAL", "LEASING"}:
        return "LEASE"
    if x in {"SELL", "SELLING"}:
        return "SALE"
    return x or "UNKNOWN"


def _finder_type(v):
    return str(v or "").strip().upper().replace(" ", "_") or "UNKNOWN"


def _type_fit(required, actual):
    req = _finder_type(required)
    act = _finder_type(actual)
    if req in {"", "UNKNOWN", "ANY"}:
        return 1.0, "type not restricted"
    if req == act:
        return 1.0, "exact property type"
    if act in TYPE_FAMILIES.get(req, set()):
        return 0.72, "compatible property type"
    return 0.0, "property type mismatch"


def _transaction_fit(required, actual):
    req = _finder_tx(required)
    act = _finder_tx(actual)
    if req in {"", "UNKNOWN", "ANY"}:
        return 1.0, "transaction not restricted"
    if req == act:
        return 1.0, "exact transaction"
    if act == "LEASE_OR_SALE" and req in {"LEASE", "SALE"}:
        return 0.92, "property supports requested transaction"
    if req == "LEASE_OR_SALE" and act in {"LEASE", "SALE"}:
        return 0.86, "one requested transaction available"
    return 0.0, "transaction mismatch"


def _location_fit(required, row):
    req = _finder_norm(required)
    actual_raw = row.get("clean_location") or row.get("location") or row.get("locality") or ""
    actual = _finder_norm(actual_raw)

    if not req:
        return 0.70, "location not restricted", "UNSPECIFIED"

    if req == actual:
        return 1.0, "exact location", "EXACT"

    if req and actual and (req in actual or actual in req):
        return 0.92, "same location / sub-market", "EXACT"

    # Compare against curated alternative markets generated from the requirement.
    probe = {
        "clean_location": required,
        "clean_property_type": row.get("_requirement_type"),
        "clean_transaction": row.get("_requirement_transaction"),
    }
    alternatives = [_finder_norm(x) for x in _alternative_markets(probe, limit=8)]
    if actual and actual in alternatives:
        rank = alternatives.index(actual)
        return max(0.62, 0.80 - rank * 0.025), "AI alternative market", "ALTERNATIVE"

    return 0.0, "location mismatch", "OUTSIDE"


def _area_fit(req_min, req_max, row):
    rmin = _finder_float(req_min)
    rmax = _finder_float(req_max)
    if rmin is None and rmax is None:
        return 1.0, "area not restricted"

    pmin = _finder_float(row.get("clean_area_min_sqft") or row.get("available_area_sqft") or row.get("area_sqft"))
    pmax = _finder_float(row.get("clean_area_max_sqft") or pmin)
    if pmin is None:
        return 0.45, "property area unknown"

    if rmin is None:
        rmin = 0.0
    if rmax is None:
        rmax = max(rmin * 1.35, rmin + 1)

    if pmax >= rmin and pmin <= rmax:
        # overlap
        midpoint_req = (rmin + rmax) / 2 if rmax else rmin
        midpoint_prop = (pmin + pmax) / 2
        delta = abs(midpoint_prop - midpoint_req) / max(midpoint_req, 1)
        return max(0.78, 1.0 - min(delta, 0.22)), "area within requirement"

    # Near miss tolerance, never equal to an exact fit.
    if pmax < rmin:
        gap = (rmin - pmax) / max(rmin, 1)
    else:
        gap = (pmin - rmax) / max(rmax, 1)

    if gap <= 0.10:
        return 0.70, "area within 10% tolerance"
    if gap <= 0.20:
        return 0.45, "area within 20% tolerance"
    return 0.0, "area outside acceptable range"


def _budget_fit(req_min, req_max, transaction, row):
    lo = _finder_float(req_min)
    hi = _finder_float(req_max)
    if lo is None and hi is None:
        return 1.0, "budget not restricted"

    tx = _finder_tx(transaction)
    price = (
        _finder_float(row.get("rent_inr") or row.get("clean_budget"))
        if tx == "LEASE"
        else _finder_float(row.get("sale_price_inr") or row.get("clean_budget"))
    )
    if price is None:
        return 0.48, "price/rent unknown"

    if lo is not None and price < lo:
        # Cheaper is normally commercially acceptable, but not a perfect signal.
        return 0.88, "within budget, below minimum indication"

    if hi is None or price <= hi:
        return 1.0, "within budget"

    over = (price - hi) / max(hi, 1)
    if over <= 0.05:
        return 0.78, "within 5% budget tolerance"
    if over <= 0.10:
        return 0.55, "within 10% budget tolerance"
    return 0.0, "over budget"


def _data_confidence(row):
    points = 0.0
    reasons = []
    if row.get("clean_score"):
        q = min(1.0, float(row.get("clean_score") or 0) / 100.0)
        points += q * 0.35
        if q >= .75:
            reasons.append("clean data")
    if row.get("owner_phone"):
        points += 0.22
        reasons.append("owner contact")
    elif row.get("broker_phone"):
        points += 0.18
        reasons.append("broker contact")
    elif row.get("sender_phone"):
        points += 0.12
        reasons.append("sender contact")
    if row.get("clean_area_min_sqft"):
        points += 0.12
    if row.get("rent_inr") or row.get("sale_price_inr") or row.get("clean_budget"):
        points += 0.12
    if str(row.get("availability") or "").upper() in {"VERIFIED", "AVAILABLE", "ACTIVE"}:
        points += 0.12
        reasons.append("availability positive")
    if row.get("last_seen") or row.get("first_seen"):
        points += 0.07
    return min(1.0, points), reasons


def _score_property_match(requirement, row):
    x = dict(row)
    x["_requirement_type"] = requirement.get("property_type")
    x["_requirement_transaction"] = requirement.get("transaction")

    tx_fit, tx_reason = _transaction_fit(requirement.get("transaction"), x.get("clean_transaction") or x.get("transaction_type"))
    type_fit, type_reason = _type_fit(requirement.get("property_type"), x.get("clean_property_type") or x.get("property_type"))
    loc_fit, loc_reason, loc_class = _location_fit(requirement.get("location"), x)
    area_fit, area_reason = _area_fit(requirement.get("min_area_sqft"), requirement.get("max_area_sqft"), x)
    budget_fit, budget_reason = _budget_fit(
        requirement.get("min_budget"),
        requirement.get("max_budget"),
        requirement.get("transaction"),
        x,
    )

    # Hard commercial gates. Wrong transaction/type/location cannot become a
    # "strong" match because of other fields.
    hard_reject = []
    if tx_fit == 0:
        hard_reject.append(tx_reason)
    if type_fit == 0 and requirement.get("property_type"):
        hard_reject.append(type_reason)
    if loc_fit == 0 and requirement.get("location"):
        hard_reject.append(loc_reason)
    if area_fit == 0:
        hard_reject.append(area_reason)
    if budget_fit == 0:
        hard_reject.append(budget_reason)

    confidence, confidence_reasons = _data_confidence(x)

    # Match score weights = 100.
    # Location and type are deliberately dominant for real-estate finding.
    score = (
        loc_fit * 30.0 +
        type_fit * 20.0 +
        tx_fit * 15.0 +
        area_fit * 15.0 +
        budget_fit * 10.0 +
        confidence * 10.0
    )
    if hard_reject:
        score = min(score, 49.0)

    score = round(max(0.0, min(100.0, score)), 2)

    if hard_reject:
        band = "REJECT"
    elif loc_class == "EXACT" and score >= 86:
        band = "EXACT"
    elif score >= 76:
        band = "STRONG"
    elif loc_class == "ALTERNATIVE" and score >= 66:
        band = "ALTERNATIVE"
    elif score >= 60:
        band = "REVIEW"
    else:
        band = "REJECT"

    reasons = [
        loc_reason,
        type_reason,
        tx_reason,
        area_reason,
        budget_reason,
    ] + confidence_reasons

    x.pop("_requirement_type", None)
    x.pop("_requirement_transaction", None)
    x["match_score"] = score
    x["match_band"] = band
    x["location_match_class"] = loc_class
    x["match_reasons"] = reasons[:8]
    x["hard_reject_reasons"] = hard_reject
    x["match_components"] = {
        "location": round(loc_fit * 30, 2),
        "property_type": round(type_fit * 20, 2),
        "transaction": round(tx_fit * 15, 2),
        "area": round(area_fit * 15, 2),
        "budget": round(budget_fit * 10, 2),
        "data_confidence": round(confidence * 10, 2),
    }
    return x


def _finder_candidate_queries(requirement):
    queries = []
    location = str(requirement.get("location") or "").strip()
    if location:
        queries.append(location)

    probe = {
        "clean_location": location,
        "clean_property_type": requirement.get("property_type"),
        "clean_transaction": requirement.get("transaction"),
    }
    for alt in _alternative_markets(probe, limit=8):
        if alt and alt not in queries:
            queries.append(alt)

    # Property type query is a secondary recovery path when location text is
    # absent from a row but the structured type is useful.
    ptype = str(requirement.get("property_type") or "").strip()
    if ptype and ptype not in queries:
        queries.append(ptype.replace("_", " "))

    return queries[:10]


def _find_properties(core, requirement, top_n=20):
    # Pull candidates from exact location first, then alternatives. This is
    # more accurate than only looking at the latest global 500/1000 rows.
    candidates = []
    seen = set()
    detected = None
    source = "none"

    queries = _finder_candidate_queries(requirement)
    for q in queries:
        src, stored, det = get_rows(core, q, 1200)
        source, detected = src, det
        for item in stored:
            key = str(item.get("wa_property_id") or item.get("id"))
            if key not in seen:
                seen.add(key)
                candidates.append(item)

    # If requirement has little structure, include a bounded recent recovery
    # pool rather than returning nothing.
    if len(candidates) < 200:
        src, stored, det = get_rows(core, "", 2000)
        source, detected = src, det
        for item in stored:
            key = str(item.get("wa_property_id") or item.get("id"))
            if key not in seen:
                seen.add(key)
                candidates.append(item)

    visible = _visible_entities(candidates)
    clean, purity_stats = _clean_visible_rows(visible, 60)

    scored = [_score_property_match(requirement, row) for row in clean]
    useful = [x for x in scored if x.get("match_band") != "REJECT"]
    useful.sort(
        key=lambda x: (
            0 if x["match_band"] == "EXACT" else
            1 if x["match_band"] == "STRONG" else
            2 if x["match_band"] == "ALTERNATIVE" else 3,
            -float(x.get("match_score") or 0),
            -float(x.get("priority_score") or 0),
        )
    )

    exact = [x for x in useful if x["match_band"] == "EXACT"]
    strong = [x for x in useful if x["match_band"] == "STRONG"]
    alternative = [x for x in useful if x["match_band"] == "ALTERNATIVE"]
    review = [x for x in useful if x["match_band"] == "REVIEW"]

    return {
        "status": "OK",
        "version": VERSION,
        "engine": "ACCURACY_FIRST_EXPLAINABLE_PROPERTY_FINDER",
        "source": source,
        "requirement": requirement,
        "candidate_queries": queries,
        "candidates_scanned": len(candidates),
        "clean_candidates": len(clean),
        "purity_stats": purity_stats,
        "summary": {
            "EXACT": len(exact),
            "STRONG": len(strong),
            "ALTERNATIVE": len(alternative),
            "REVIEW": len(review),
        },
        "results": useful[:max(1, min(int(top_n or 20), 100))],
        "counts": detected or {},
    }


def _finder_result_public(x):
    # Keep all matching intelligence but make the JSON concise enough for UI.
    return {
        "id": x.get("visible_entity_id") or x.get("wa_property_id") or x.get("id"),
        "match_band": x.get("match_band"),
        "match_score": x.get("match_score"),
        "location_match_class": x.get("location_match_class"),
        "location": x.get("clean_location") or x.get("location") or x.get("locality"),
        "property_type": x.get("clean_property_type") or x.get("property_type"),
        "transaction": x.get("clean_transaction") or x.get("transaction_type"),
        "area_min_sqft": x.get("clean_area_min_sqft") or x.get("available_area_sqft") or x.get("area_sqft"),
        "area_max_sqft": x.get("clean_area_max_sqft"),
        "rent_inr": x.get("rent_inr"),
        "sale_price_inr": x.get("sale_price_inr"),
        "availability": x.get("availability"),
        "owner_name": x.get("owner_name"),
        "owner_phone": x.get("owner_phone"),
        "broker_name": x.get("broker_name"),
        "broker_phone": x.get("broker_phone"),
        "sender_name": x.get("sender_name"),
        "sender_phone": x.get("sender_phone"),
        "last_seen": x.get("last_seen") or x.get("first_seen"),
        "match_reasons": x.get("match_reasons"),
        "hard_reject_reasons": x.get("hard_reject_reasons"),
        "match_components": x.get("match_components"),
        "raw_text": x.get("visible_entity_text") or x.get("raw_text"),
    }



def _get_requirement_rows(core, q="", limit=1000):
    engine, dispose = _source_engine(core)
    try:
        with engine.connect() as conn:
            if not _exists(conn, "wa_requirements"):
                return [], {"wa_requirements_count": 0}
            params = {"lim": max(25, min(int(limit or 1000), 3000))}
            where = ["COALESCE(r.status,'ACTIVE')='ACTIVE'"]
            if q:
                params["q"] = "%" + q + "%"
                where.append("""(
                    COALESCE(r.preferred_locations,'') ILIKE :q OR
                    COALESCE(r.property_type,'') ILIKE :q OR
                    COALESCE(r.transaction_type,'') ILIKE :q OR
                    COALESCE(r.contact_name,'') ILIKE :q OR
                    COALESCE(r.contact_phone,'') ILIKE :q OR
                    COALESCE(r.company_name,'') ILIKE :q OR
                    COALESCE(r.raw_text,'') ILIKE :q OR
                    COALESCE(s.group_name,s.source_name,'') ILIKE :q
                )""")
            sql = f"""
            SELECT r.*,
                   COALESCE(s.group_name,s.source_name) AS source_group,
                   COALESCE(m.message_timestamp,r.created_at::text) AS received_at,
                   COALESCE(m.sender_name,r.contact_name) AS source_sender,
                   COALESCE(m.sender_phone,r.contact_phone) AS source_phone
            FROM wa_requirements r
            LEFT JOIN wa_sources s ON s.source_id=r.source_id
            LEFT JOIN wa_messages m ON m.message_id=r.message_id
            WHERE {" AND ".join(where)}
            ORDER BY r.created_at DESC, r.id DESC
            LIMIT :lim
            """
            rows = [dict(x) for x in conn.execute(text(sql), params).mappings().all()]
            return rows, {"wa_requirements_count": _count(conn, "wa_requirements", "COALESCE(status,'ACTIVE')='ACTIVE'")}
    finally:
        if dispose:
            engine.dispose()


def _clean_requirement_rows(rows):
    out = []
    seen = set()
    for d0 in rows:
        d = dict(d0)
        raw = _wa_text_normalize(d.get("raw_text") or "")
        kind, inferred_conf = _clean_classify_text(raw)
        # A stored requirement remains authoritative unless text very clearly says supply.
        if kind == "PROPERTY_INVENTORY" and not re.search(
            r"\b(require(?:d|ment)?|looking\s+for|need(?:ed|s)?|wanted|client\s+(?:looking|requires?|needs?))\b",
            raw, re.I
        ):
            continue

        tx = _finder_tx(d.get("transaction_type"))
        if tx == "UNKNOWN":
            low = raw.lower()
            tx = "SALE" if re.search(r"\b(?:buy|purchase|for sale|sale)\b", low) else "LEASE"

        loc = d.get("preferred_locations") or d.get("city") or "—"
        typ = d.get("property_type") or "UNKNOWN"
        contact_phone = d.get("contact_phone") or d.get("source_phone")
        contact_name = d.get("contact_name") or d.get("source_sender")
        fp = "|".join([
            _finder_norm(loc), _finder_type(typ), tx,
            str(d.get("minimum_area_sqft") or ""), str(d.get("maximum_area_sqft") or ""),
            str(d.get("budget_max_inr") or ""), str(contact_phone or "")
        ])
        if fp in seen:
            continue
        seen.add(fp)

        d["clean_transaction"] = tx
        d["clean_location"] = loc
        d["clean_property_type"] = typ
        d["clean_contact_phone"] = contact_phone
        d["clean_contact_name"] = contact_name
        d["clean_confidence"] = max(float(d.get("confidence") or 0), inferred_conf*100)
        d["clean_raw"] = raw
        out.append(d)
    return out


def _recover_recent_false_negatives(core, wanted_kind, limit=300):
    """
    Read-only recovery for historical REJECTED/REVIEW rows.
    Does not write to the DB. New intake is fixed by runtime classifier patch.
    """
    engine, dispose = _source_engine(core)
    try:
        with engine.connect() as conn:
            if not _exists(conn, "wa_messages"):
                return []
            sql = """
            SELECT m.message_id, m.source_id, m.message_timestamp, m.sender_name,
                   m.sender_phone, m.raw_text, m.classification, m.confidence,
                   COALESCE(s.group_name,s.source_name) AS source_group
            FROM wa_messages m
            LEFT JOIN wa_sources s ON s.source_id=m.source_id
            WHERE COALESCE(m.classification,'') IN ('REJECTED','NEEDS_REVIEW','PROPERTY_CONTACT')
            ORDER BY m.id DESC
            LIMIT :lim
            """
            rows = conn.execute(text(sql), {"lim": max(50, min(int(limit), 1500))}).mappings().all()
            recovered = []
            for r0 in rows:
                r = dict(r0)
                raw = _wa_text_normalize(r.get("raw_text") or "")
                kind, conf = _clean_classify_text(raw)
                if kind != wanted_kind:
                    continue
                r["recovered_kind"] = kind
                r["recovered_confidence"] = round(conf*100, 2)
                recovered.append(r)
            return recovered
    finally:
        if dispose:
            engine.dispose()

def register(wrapped):
    app = wrapped.app
    core = wrapped.core
    engine_patch = _patch_runtime_whatsapp_engine()

    owned = {
        "/whatsapp-live/feed",
        "/whatsapp-live/requirements",
        "/whatsapp-live/availability",
        "/api/whatsapp-clean-database/status",
        "/api/live-feed-purity/status",
        "/api/live-feed-purity/sample",
        "/api/live-feed-purity/debug",
        "/api/live-feed-purity/market-suggestions",
        "/api/property-finder/search",
        "/property-finder",
    }

    app.router.routes[:] = [
        r
        for r in app.router.routes
        if not (
            getattr(r, "path", None) in owned
            and "GET" in (getattr(r, "methods", set()) or set())
        )
    ]

    def api_status():
        return status(core)

    def sample():
        try:
            source, stored_rows, detected = get_rows(core, "", 50)
            visible = _visible_entities(stored_rows)
            clean, purity_stats = _clean_visible_rows(visible, 60)
            ranked = _attach_market_intelligence(_prioritize_rows(clean))
            bands = {
                "HOT": sum(1 for x in ranked if x["priority_band"] == "HOT"),
                "STRONG": sum(1 for x in ranked if x["priority_band"] == "STRONG"),
                "REVIEW": sum(1 for x in ranked if x["priority_band"] == "REVIEW"),
            }
            return {
                "status": "OK",
                "version": VERSION,
                "source": source,
                "stored_rows_sampled": len(stored_rows),
                "visible_entities_after_split": len(visible),
                "clean_entities": len(clean),
                "priority_bands": bands,
                "purity_stats": purity_stats,
                "counts": detected,
            }
        except Exception as exc:
            return {
                "status": "ERROR",
                "version": VERSION,
                "error": f"{type(exc).__name__}: {exc}",
            }

    def debug():
        result = status(core)
        try:
            source, stored_rows, detected = get_rows(core, "", 100)
            visible = _visible_entities(stored_rows)
            clean, purity_stats = _clean_visible_rows(visible, 60)
            ranked = _attach_market_intelligence(_prioritize_rows(clean))
            multi = {}
            for row in visible:
                base = str(row.get("wa_property_id") or row.get("id"))
                multi[base] = multi.get(base, 0) + 1

            result.update(
                {
                    "selected_source": source,
                    "stored_rows_sampled": len(stored_rows),
                    "visible_entities_after_split": len(visible),
                    "clean_entities_after_purity": len(clean),
                    "priority_bands": {
                        "HOT": sum(1 for x in ranked if x["priority_band"] == "HOT"),
                        "STRONG": sum(1 for x in ranked if x["priority_band"] == "STRONG"),
                        "REVIEW": sum(1 for x in ranked if x["priority_band"] == "REVIEW"),
                    },
                    "market_coverage": {
                        "with_alternatives": sum(1 for x in ranked if x.get("alternative_markets")),
                        "without_alternatives": sum(1 for x in ranked if not x.get("alternative_markets")),
                    },
                    "top_priority_sample": [
                        {
                            "id": x.get("visible_entity_id"),
                            "band": x.get("priority_band"),
                            "score": x.get("priority_score"),
                            "location": x.get("clean_location"),
                            "type": x.get("clean_property_type"),
                            "transaction": x.get("clean_transaction"),
                            "alternative_markets": x.get("alternative_markets"),
                        }
                        for x in ranked[:10]
                    ],
                    "purity_stats": purity_stats,
                    "multi_entity_stored_rows": sum(1 for n in multi.values() if n > 1),
                    "sample_visible_ids": [
                        x.get("visible_entity_id") for x in clean[:10]
                    ],
                    "counts": detected,
                }
            )
        except Exception as exc:
            result["sample_error"] = f"{type(exc).__name__}: {exc}"
        return result


    def market_suggestions(request: Request):
        location = str(request.query_params.get("location") or "").strip()
        property_type = str(request.query_params.get("property_type") or "").strip()
        transaction = str(request.query_params.get("transaction") or "").strip()

        probe = {
            "clean_location": location,
            "clean_property_type": property_type,
            "clean_transaction": transaction,
        }
        region, context, loc = _market_context(probe)
        return {
            "status": "OK",
            "version": VERSION,
            "location": loc,
            "property_type": property_type,
            "transaction": transaction,
            "market_region": region,
            "market_context": context,
            "alternatives": _alternative_markets(probe),
        }

    def property_finder_search(request: Request):
        qp = request.query_params
        requirement = {
            "location": str(qp.get("location") or "").strip(),
            "property_type": str(qp.get("property_type") or "").strip().upper().replace(" ", "_"),
            "transaction": str(qp.get("transaction") or "").strip().upper(),
            "min_area_sqft": _finder_float(qp.get("min_area_sqft")),
            "max_area_sqft": _finder_float(qp.get("max_area_sqft")),
            "min_budget": _finder_float(qp.get("min_budget")),
            "max_budget": _finder_float(qp.get("max_budget")),
        }
        try:
            top_n = int(qp.get("top_n") or 20)
        except Exception:
            top_n = 20

        if not any([
            requirement["location"],
            requirement["property_type"],
            requirement["transaction"],
            requirement["min_area_sqft"],
            requirement["max_area_sqft"],
            requirement["min_budget"],
            requirement["max_budget"],
        ]):
            return {
                "status": "ERROR",
                "version": VERSION,
                "error": "Provide at least one requirement field.",
            }

        result = _find_properties(core, requirement, top_n)
        result["results"] = [_finder_result_public(x) for x in result["results"]]
        return result

    def property_finder_page(request: Request):
        qp = request.query_params
        has_search = any(qp.get(k) for k in [
            "location","property_type","transaction","min_area_sqft",
            "max_area_sqft","min_budget","max_budget"
        ])
        result = None
        if has_search:
            requirement = {
                "location": str(qp.get("location") or "").strip(),
                "property_type": str(qp.get("property_type") or "").strip().upper().replace(" ", "_"),
                "transaction": str(qp.get("transaction") or "").strip().upper(),
                "min_area_sqft": _finder_float(qp.get("min_area_sqft")),
                "max_area_sqft": _finder_float(qp.get("max_area_sqft")),
                "min_budget": _finder_float(qp.get("min_budget")),
                "max_budget": _finder_float(qp.get("max_budget")),
            }
            result = _find_properties(core, requirement, 30)

        rows_html = ""
        summary_html = ""
        if result:
            summary = result["summary"]
            summary_html = (
                f"<div class='summary'>"
                f"<b>EXACT {summary['EXACT']}</b> · "
                f"<b>STRONG {summary['STRONG']}</b> · "
                f"<b>ALTERNATIVE {summary['ALTERNATIVE']}</b> · "
                f"REVIEW {summary['REVIEW']} · "
                f"scanned {result['candidates_scanned']} candidates"
                f"</div>"
            )
            for x in result["results"]:
                contact = " | ".join(str(v) for v in [
                    x.get("owner_name"), x.get("owner_phone"),
                    x.get("broker_name"), x.get("broker_phone"),
                    x.get("sender_name"), x.get("sender_phone"),
                ] if v)
                price = x.get("rent_inr") if _finder_tx(result["requirement"].get("transaction")) == "LEASE" else x.get("sale_price_inr")
                rows_html += f"""
                <tr>
                  <td><b>{_esc(x.get('match_band'))}</b><br>{_esc(x.get('match_score'))}/100</td>
                  <td>{_esc(x.get('clean_location') or x.get('location') or x.get('locality') or '—')}</td>
                  <td>{_esc(x.get('clean_property_type') or x.get('property_type') or '—')}</td>
                  <td>{_esc(x.get('clean_transaction') or x.get('transaction_type') or '—')}</td>
                  <td>{_esc(x.get('clean_area_min_sqft') or x.get('available_area_sqft') or x.get('area_sqft') or '—')}</td>
                  <td>{_esc(price or '—')}</td>
                  <td>{_esc(' · '.join(x.get('match_reasons') or []))}</td>
                  <td>{_esc(contact or '—')}</td>
                  <td style='min-width:360px;white-space:pre-wrap'>{_esc(x.get('visible_entity_text') or x.get('raw_text') or '—')}</td>
                </tr>
                """

        if result and not rows_html:
            rows_html = "<tr><td colspan='9'>No safe match found. The engine refused to promote mismatched properties.</td></tr>"

        return HTMLResponse(f"""<!doctype html>
        <html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>AI Property Finder</title>
        <style>
        body{{font-family:Arial;margin:0;background:#f6f2ec;color:#2b241f}}
        header{{background:#40352d;color:#fff;padding:22px}}
        main{{max-width:1700px;margin:auto;padding:18px}}
        .card{{background:#fff;border:1px solid #ded3c7;border-radius:12px;padding:16px;margin-bottom:14px}}
        .grid{{display:grid;grid-template-columns:repeat(4,minmax(160px,1fr));gap:10px}}
        label{{font-size:12px;font-weight:700}}
        input,select{{width:100%;box-sizing:border-box;padding:10px;border:1px solid #c7b8aa;border-radius:7px;margin-top:4px}}
        button{{padding:11px 18px;background:#4e4034;color:#fff;border:0;border-radius:7px;font-weight:700}}
        table{{width:100%;border-collapse:collapse;background:#fff}}
        th,td{{padding:9px;border-bottom:1px solid #ece5df;text-align:left;vertical-align:top;font-size:12px}}
        th{{background:#eee5dc;position:sticky;top:0}}
        .summary{{padding:12px;background:#edf5ef;border-radius:8px;margin-top:12px}}
        .note{{font-size:12px;color:#655b53}}
        @media(max-width:900px){{.grid{{grid-template-columns:1fr 1fr}}}}
        </style></head>
        <body><header><h2 style='margin:0'>AI Property Finder</h2>
        <div>Accuracy-first matching · exact market before alternatives · explainable score</div></header>
        <main>
        <div class='card'>
        <form>
          <div class='grid'>
            <label>Location<input name='location' value='{_esc(qp.get("location") or "")}' placeholder='e.g. Saket'></label>
            <label>Property Type<input name='property_type' value='{_esc(qp.get("property_type") or "")}' placeholder='e.g. RESTAURANT'></label>
            <label>Transaction<select name='transaction'>
              <option value=''>Any</option>
              <option value='LEASE' {'selected' if qp.get("transaction")=="LEASE" else ''}>Lease</option>
              <option value='SALE' {'selected' if qp.get("transaction")=="SALE" else ''}>Sale</option>
            </select></label>
            <label>Min Area sqft<input name='min_area_sqft' value='{_esc(qp.get("min_area_sqft") or "")}' type='number'></label>
            <label>Max Area sqft<input name='max_area_sqft' value='{_esc(qp.get("max_area_sqft") or "")}' type='number'></label>
            <label>Min Budget / Rent<input name='min_budget' value='{_esc(qp.get("min_budget") or "")}' type='number'></label>
            <label>Max Budget / Rent<input name='max_budget' value='{_esc(qp.get("max_budget") or "")}' type='number'></label>
          </div>
          <p><button>Find Best Properties</button></p>
        </form>
        <div class='note'>Score = Location 30 + Type 20 + Transaction 15 + Area 15 + Budget 10 + Data Confidence 10. Hard commercial mismatches are rejected.</div>
        {summary_html}
        </div>
        <div class='card' style='overflow:auto'>
        <table><thead><tr>
        <th>AI Match</th><th>Location</th><th>Type</th><th>Transaction</th><th>Area</th><th>Price/Rent</th><th>Why matched</th><th>Contact</th><th>Property</th>
        </tr></thead><tbody>{rows_html}</tbody></table>
        </div>
        </main></body></html>""")

    def _query_filters(request):
        q = str(request.query_params.get("q") or "").strip()
        tx = str(request.query_params.get("transaction") or "").strip().upper()
        typ = str(request.query_params.get("property_type") or "").strip().upper()
        loc = str(request.query_params.get("location") or "").strip()
        try:
            limit = max(25, min(int(request.query_params.get("limit") or 750), 2000))
        except Exception:
            limit = 750
        return q, tx, typ, loc, limit

    def _filter_supply(rows, tx="", typ="", loc=""):
        out = []
        for x in rows:
            if tx and _finder_tx(x.get("clean_transaction") or x.get("transaction_type")) != tx:
                continue
            if typ and typ not in _finder_type(x.get("clean_property_type") or x.get("property_type")):
                continue
            if loc and _finder_norm(loc) not in _finder_norm(x.get("clean_location") or x.get("location") or x.get("locality")):
                continue
            out.append(x)
        return out

    def _filter_demand(rows, tx="", typ="", loc=""):
        out = []
        for x in rows:
            if tx and _finder_tx(x.get("clean_transaction") or x.get("transaction_type")) != tx:
                continue
            if typ and typ not in _finder_type(x.get("clean_property_type") or x.get("property_type")):
                continue
            if loc and _finder_norm(loc) not in _finder_norm(x.get("clean_location") or x.get("preferred_locations")):
                continue
            out.append(x)
        return out

    def clean_db_status():
        base = status(core)
        try:
            reqs, rc = _get_requirement_rows(core, "", 50)
            recovered_req = _recover_recent_false_negatives(core, "PROPERTY_REQUIREMENT", 500)
            recovered_inv = _recover_recent_false_negatives(core, "PROPERTY_INVENTORY", 500)
            base.update({
                "clean_database_version": VERSION,
                "runtime_engine_patch": engine_patch,
                "requirements_sample_clean": len(_clean_requirement_rows(reqs)),
                "historical_false_negative_candidates": {
                    "requirements": len(recovered_req),
                    "inventory": len(recovered_inv),
                },
                "entity_contract": "ONE_ROW_ONE_PROPERTY_OR_REQUIREMENT",
                "views": [
                    "/whatsapp-live/feed",
                    "/whatsapp-live/requirements",
                    "/whatsapp-live/availability",
                ],
            })
        except Exception as exc:
            base["clean_database_error"] = f"{type(exc).__name__}: {exc}"
        return base

    def availability_page(request: Request):
        q, tx, typ, loc, limit = _query_filters(request)
        try:
            source, stored_rows, detected = get_rows(core, q, limit)
            visible = _visible_entities(stored_rows)
            clean, purity_stats = _clean_visible_rows(visible, 55)
            rows = _attach_market_intelligence(_prioritize_rows(clean))
            rows = _filter_supply(rows, tx, typ, loc)
            recovered = _recover_recent_false_negatives(core, "PROPERTY_INVENTORY", 500)
        except Exception as exc:
            return HTMLResponse(_database_shell(
                "Availability Database", "One property per row",
                f"<div class='card'><b>Error:</b> {_esc(type(exc).__name__)}: {_esc(exc)}</div>",
                "Availability"
            ), status_code=500)

        trs = []
        for x in rows[:1500]:
            txv = _finder_tx(x.get("clean_transaction") or x.get("transaction_type"))
            price = x.get("rent_inr") if txv == "LEASE" else x.get("sale_price_inr")
            contact_name = x.get("owner_name") or x.get("broker_name") or x.get("sender_name")
            contact_phone = x.get("owner_phone") or x.get("broker_phone") or x.get("sender_phone")
            raw = x.get("visible_entity_text") or x.get("raw_text") or ""
            area_min = x.get("clean_area_min_sqft") or x.get("available_area_sqft") or x.get("area_sqft")
            area_max = x.get("clean_area_max_sqft")
            area = f"{area_min:,.0f}" if isinstance(area_min,(int,float)) else str(area_min or "—")
            if area_max and area_max != area_min:
                try: area = f"{float(area_min):,.0f} - {float(area_max):,.0f}"
                except Exception: pass
            trs.append(f"""<tr>
            <td>{_esc(x.get('last_seen') or x.get('first_seen') or '—')}</td>
            <td><b>{_esc(x.get('visible_entity_id') or x.get('wa_property_id'))}</b></td>
            <td>{_esc(x.get('clean_location') or x.get('location') or x.get('locality') or '—')}</td>
            <td>{_esc(x.get('clean_property_type') or x.get('property_type') or '—')}</td>
            <td>{_esc(_configuration(raw))}</td>
            <td>{_esc(txv)}</td>
            <td>{_esc(area)}</td>
            <td>{_esc(x.get('floor') or '—')}</td>
            <td>{_esc(_fmt_money(price))}</td>
            <td>{_esc(x.get('availability') or 'UNKNOWN')}</td>
            <td>{_esc(contact_name or '—')}</td>
            <td class='phone'>{_esc(contact_phone or '—')}</td>
            <td><span class='badge supply'>{_esc(x.get('priority_band') or 'REVIEW')} {_esc(x.get('priority_score') or '')}</span></td>
            <td>{_esc(x.get('clean_score') or x.get('confidence') or '—')}</td>
            <td class='entity'>{_esc(raw)}</td>
            </tr>""")

        body = "".join(trs) or "<tr><td colspan='15'>No clean availability found for these filters.</td></tr>"
        kpis = f"""<div class='kpis'>
          <div class='kpi'>Clean Availability<b>{len(rows)}</b></div>
          <div class='kpi'>Stored Rows Read<b>{len(stored_rows)}</b></div>
          <div class='kpi'>False-Negative Candidates<b>{len(recovered)}</b><span class='small muted'>recent rejected/review, read-only recovery</span></div>
          <div class='kpi'>DB Inventory<b>{detected.get('wa_properties_count',0)}</b></div>
        </div>"""
        filters = f"""<div class='card'><form class='filters'>
          <label>Search<input name='q' value='{_esc(q)}' placeholder='location, contact, type, text'></label>
          <label>Location<input name='location' value='{_esc(loc)}'></label>
          <label>Type<input name='property_type' value='{_esc(typ)}'></label>
          <label>Transaction<select name='transaction'><option value=''>All</option>
          <option value='LEASE' {'selected' if tx=='LEASE' else ''}>LEASE</option>
          <option value='SALE' {'selected' if tx=='SALE' else ''}>SALE</option></select></label>
          <button>Filter</button></form>{kpis}</div>"""
        table = f"""<div class='card'><div class='scroll'><table>
        <thead><tr><th>Date</th><th>Property ID</th><th>Location</th><th>Property Type</th>
        <th>Configuration</th><th>Transaction</th><th>Area Sqft</th><th>Floor</th>
        <th>Rent / Sale Price</th><th>Availability</th><th>Contact Name</th><th>Contact No.</th>
        <th>AI Priority</th><th>Purity</th><th>Original Property Text</th></tr></thead>
        <tbody>{body}</tbody></table></div></div>"""
        return HTMLResponse(_database_shell(
            "WhatsApp Availability Database",
            "Newspaper-style clean inventory · one physical property per row · raw message retained only for audit",
            filters + table, "Availability"
        ))

    def requirements_page(request: Request):
        q, tx, typ, loc, limit = _query_filters(request)
        try:
            stored, counts = _get_requirement_rows(core, q, limit)
            rows = _filter_demand(_clean_requirement_rows(stored), tx, typ, loc)
            recovered = _recover_recent_false_negatives(core, "PROPERTY_REQUIREMENT", 500)
        except Exception as exc:
            return HTMLResponse(_database_shell(
                "Requirements Database", "One requirement per row",
                f"<div class='card'><b>Error:</b> {_esc(type(exc).__name__)}: {_esc(exc)}</div>",
                "Requirements"
            ), status_code=500)

        trs = []
        for x in rows[:1500]:
            amin = x.get("minimum_area_sqft")
            amax = x.get("maximum_area_sqft")
            if amin and amax and amin != amax:
                area = f"{float(amin):,.0f} - {float(amax):,.0f}"
            elif amin or amax:
                area = f"{float(amin or amax):,.0f}"
            else:
                area = "—"
            bmin, bmax = x.get("budget_min_inr"), x.get("budget_max_inr")
            budget = _fmt_money(bmax or bmin)
            if bmin and bmax and bmin != bmax:
                budget = f"{_fmt_money(bmin)} - {_fmt_money(bmax)}"
            raw = x.get("clean_raw") or x.get("raw_text") or ""
            trs.append(f"""<tr>
            <td>{_esc(x.get('received_at') or x.get('created_at') or '—')}</td>
            <td><b>{_esc(x.get('wa_requirement_id'))}</b></td>
            <td>{_esc(x.get('clean_location') or '—')}</td>
            <td>{_esc(x.get('clean_property_type') or '—')}</td>
            <td>{_esc(_finder_tx(x.get('clean_transaction')))}</td>
            <td>{_esc(area)}</td>
            <td>{_esc(budget)}</td>
            <td>{_esc(x.get('floor_preference') or '—')}</td>
            <td>{_esc(x.get('suitable_category') or '—')}</td>
            <td>{_esc(x.get('company_name') or x.get('client_name') or '—')}</td>
            <td>{_esc(x.get('clean_contact_name') or '—')}</td>
            <td class='phone'>{_esc(x.get('clean_contact_phone') or '—')}</td>
            <td>{_esc(x.get('source_group') or '—')}</td>
            <td>{_esc(round(float(x.get('clean_confidence') or 0),1))}</td>
            <td class='entity'>{_esc(raw)}</td>
            </tr>""")
        body = "".join(trs) or "<tr><td colspan='15'>No clean requirement found for these filters.</td></tr>"
        kpis = f"""<div class='kpis'>
          <div class='kpi'>Clean Requirements<b>{len(rows)}</b></div>
          <div class='kpi'>Stored Requirements Read<b>{len(stored)}</b></div>
          <div class='kpi'>False-Negative Candidates<b>{len(recovered)}</b><span class='small muted'>recent rejected/review, read-only recovery</span></div>
          <div class='kpi'>Active Requirement DB<b>{counts.get('wa_requirements_count',0)}</b></div>
        </div>"""
        filters = f"""<div class='card'><form class='filters'>
          <label>Search<input name='q' value='{_esc(q)}' placeholder='location, brand, contact, requirement'></label>
          <label>Location<input name='location' value='{_esc(loc)}'></label>
          <label>Type<input name='property_type' value='{_esc(typ)}'></label>
          <label>Transaction<select name='transaction'><option value=''>All</option>
          <option value='LEASE' {'selected' if tx=='LEASE' else ''}>LEASE</option>
          <option value='SALE' {'selected' if tx=='SALE' else ''}>SALE</option></select></label>
          <button>Filter</button></form>{kpis}</div>"""
        table = f"""<div class='card'><div class='scroll'><table>
        <thead><tr><th>Date</th><th>Requirement ID</th><th>Preferred Location</th><th>Property Type</th>
        <th>Transaction</th><th>Area Sqft</th><th>Budget</th><th>Floor</th><th>Suitable For</th>
        <th>Client / Company</th><th>Contact Name</th><th>Contact No.</th><th>Source Group</th>
        <th>AI Confidence</th><th>Original Requirement Text</th></tr></thead>
        <tbody>{body}</tbody></table></div></div>"""
        return HTMLResponse(_database_shell(
            "WhatsApp Requirement Database",
            "Newspaper-style demand database · one actionable requirement per row · searchable and matcher-ready",
            filters + table, "Requirements"
        ))

    def feed(request: Request):
        q, tx, typ, loc, limit = _query_filters(request)
        try:
            source, stored_rows, detected = get_rows(core, q, limit)
            supply = _filter_supply(
                _attach_market_intelligence(_prioritize_rows(
                    _clean_visible_rows(_visible_entities(stored_rows), 55)[0]
                )), tx, typ, loc
            )
            req_stored, req_counts = _get_requirement_rows(core, q, limit)
            demand = _filter_demand(_clean_requirement_rows(req_stored), tx, typ, loc)
        except Exception as exc:
            return HTMLResponse(_database_shell(
                "Live Property Database", "Clean entity feed",
                f"<div class='card'><b>Error:</b> {_esc(type(exc).__name__)}: {_esc(exc)}</div>",
                "Live Database"
            ), status_code=500)

        unified = []
        for x in supply:
            dt = str(x.get("last_seen") or x.get("first_seen") or "")
            unified.append((dt, "AVAILABILITY", x))
        for x in demand:
            dt = str(x.get("received_at") or x.get("created_at") or "")
            unified.append((dt, "REQUIREMENT", x))
        unified.sort(key=lambda t: t[0], reverse=True)

        trs = []
        for _, role, x in unified[:1500]:
            if role == "AVAILABILITY":
                txv = _finder_tx(x.get("clean_transaction") or x.get("transaction_type"))
                price = x.get("rent_inr") if txv == "LEASE" else x.get("sale_price_inr")
                contact_name = x.get("owner_name") or x.get("broker_name") or x.get("sender_name")
                contact_phone = x.get("owner_phone") or x.get("broker_phone") or x.get("sender_phone")
                raw = x.get("visible_entity_text") or x.get("raw_text") or ""
                area = x.get("clean_area_min_sqft") or x.get("available_area_sqft") or x.get("area_sqft") or "—"
                eid = x.get("visible_entity_id") or x.get("wa_property_id")
                locv = x.get("clean_location") or x.get("location") or x.get("locality") or "—"
                typv = x.get("clean_property_type") or x.get("property_type") or "—"
                statusv = x.get("priority_band") or "REVIEW"
                value = _fmt_money(price)
            else:
                txv = _finder_tx(x.get("clean_transaction"))
                contact_name = x.get("clean_contact_name")
                contact_phone = x.get("clean_contact_phone")
                raw = x.get("clean_raw") or x.get("raw_text") or ""
                area = x.get("minimum_area_sqft") or x.get("maximum_area_sqft") or "—"
                eid = x.get("wa_requirement_id")
                locv = x.get("clean_location") or "—"
                typv = x.get("clean_property_type") or "—"
                statusv = "ACTIVE"
                value = _fmt_money(x.get("budget_max_inr") or x.get("budget_min_inr"))
            trs.append(f"""<tr>
            <td>{_esc(_)}</td><td><span class='badge {'supply' if role=='AVAILABILITY' else 'demand'}'>{role}</span></td>
            <td><b>{_esc(eid)}</b></td><td>{_esc(locv)}</td><td>{_esc(typv)}</td><td>{_esc(txv)}</td>
            <td>{_esc(area)}</td><td>{_esc(value)}</td><td>{_esc(contact_name or '—')}</td>
            <td class='phone'>{_esc(contact_phone or '—')}</td><td>{_esc(statusv)}</td>
            <td class='entity'>{_esc(raw)}</td></tr>""")
        body = "".join(trs) or "<tr><td colspan='12'>No clean entities found.</td></tr>"
        kpis = f"""<div class='kpis'>
        <div class='kpi'>Availability<b>{len(supply)}</b></div>
        <div class='kpi'>Requirements<b>{len(demand)}</b></div>
        <div class='kpi'>Total Clean Entities<b>{len(unified)}</b></div>
        <div class='kpi'>Inventory DB<b>{detected.get('wa_properties_count',0)}</b></div>
        <div class='kpi'>Requirement DB<b>{req_counts.get('wa_requirements_count',0)}</b></div>
        </div>"""
        filters = f"""<div class='card'><form class='filters'>
          <label>Search<input name='q' value='{_esc(q)}' placeholder='search clean database'></label>
          <label>Location<input name='location' value='{_esc(loc)}'></label>
          <label>Type<input name='property_type' value='{_esc(typ)}'></label>
          <label>Transaction<select name='transaction'><option value=''>All</option>
          <option value='LEASE' {'selected' if tx=='LEASE' else ''}>LEASE</option>
          <option value='SALE' {'selected' if tx=='SALE' else ''}>SALE</option></select></label>
          <button>Filter</button></form>{kpis}
          <div class='small muted'>Raw WhatsApp text is retained only as audit evidence. Main working view is structured entities.</div>
        </div>"""
        table = f"""<div class='card'><div class='scroll'><table>
        <thead><tr><th>Date</th><th>Entity</th><th>ID</th><th>Location</th><th>Property Type</th>
        <th>Transaction</th><th>Area Sqft</th><th>Rent / Price / Budget</th><th>Contact Name</th>
        <th>Contact No.</th><th>Status</th><th>Original Text</th></tr></thead>
        <tbody>{body}</tbody></table></div></div>"""
        return HTMLResponse(_database_shell(
            "WhatsApp Live Clean Database",
            "Property Capture OS format · clean requirements + clean availability · one entity per row",
            filters + table, "Live Database"
        ))

    app.add_api_route("/api/live-feed-purity/status", api_status, methods=["GET"])
    app.add_api_route("/api/live-feed-purity/sample", sample, methods=["GET"])
    app.add_api_route("/api/live-feed-purity/debug", debug, methods=["GET"])
    app.add_api_route("/api/live-feed-purity/market-suggestions", market_suggestions, methods=["GET"])
    app.add_api_route("/api/property-finder/search", property_finder_search, methods=["GET"])
    app.add_api_route("/property-finder", property_finder_page, methods=["GET"])
    app.add_api_route("/api/whatsapp-clean-database/status", clean_db_status, methods=["GET"])
    app.add_api_route("/whatsapp-live/feed", feed, methods=["GET"])
    app.add_api_route("/whatsapp-live/requirements", requirements_page, methods=["GET"])
    app.add_api_route("/whatsapp-live/availability", availability_page, methods=["GET"])

    return {"status": "REGISTERED", "version": VERSION, "engine_patch": engine_patch}
