from __future__ import annotations

import html
import json
import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Tuple

from fastapi import Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

VERSION = "8.2.1-ALLIANCE-LATE-ROUTE-TAKEOVER-HOTFIX"
MODE = "LATE_ROUTE_TAKEOVER_MAGAZINE_RECOVERY_UNIVERSAL_STATS"

SOURCE_NAMES = ("MASTER", "NEWSPAPER", "WHATSAPP", "MAGAZINE", "MANUAL")
SOURCE_PATTERNS = {
    "NEWSPAPER": "%NEWSPAPER%",
    "WHATSAPP": "%WHATSAPP%",
    "MAGAZINE": "%MAGAZINE%",
    "MANUAL": "%MANUAL%",
}

TEXT_KEYS = (
    "original_description", "original_message", "raw_line", "raw_text",
    "description", "message", "ad_text", "listing_text", "property_text",
    "source_text", "text", "content", "body", "ocr_text", "raw_content",
    "property_description", "details", "remarks",
)
ADDRESS_KEYS = (
    "address", "exact_address", "property_address", "address_line",
    "address_text", "unit_address", "building_address", "property_location",
)
CONTACT_NAME_KEYS = ("contact_name", "owner_name", "broker_name", "sender_name", "name")
PHONE_KEYS = (
    "contact_number", "contact_phone", "owner_contact", "owner_phone",
    "broker_contact", "broker_phone", "phone", "mobile", "contact_no", "phone_number",
)
LOCALITY_KEYS = ("locality", "location", "locality_clean", "area_name")
SECTION_KEYS = ("section_heading", "heading", "category_heading", "transaction_heading")
CATEGORY_KEYS = ("property_category", "property_type", "category")
TRANSACTION_KEYS = ("transaction_type", "rent_or_sale", "transaction")
FLOOR_KEYS = ("floor", "floors", "floor_codes")
AREA_KEYS = ("area_sqft", "available_area_sqft", "area", "area_value", "size_sqft")


def _app(core):
    return getattr(core, "app", None) or core


def _engine(core):
    return getattr(core, "engine", None)


def _role(core, req):
    fn = getattr(core, "need_login", None)
    return fn(req) if fn else "team"


def _e(v):
    return html.escape("" if v is None else str(v))


def _dict(v):
    if isinstance(v, dict):
        return dict(v)
    if isinstance(v, str):
        try:
            x = json.loads(v)
            return x if isinstance(x, dict) else {}
        except Exception:
            return {}
    return {}


def _safe(v):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): _safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_safe(x) for x in v]
    return str(v)


def _clean(v):
    return re.sub(r"\s+", " ", str(v or "")).strip()


def _first(d, keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def _fmt_dt(v):
    if not v:
        return ""
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except Exception:
            return str(v)
    if isinstance(v, datetime):
        return v.strftime("%d-%m-%Y %I:%M %p")
    return str(v)


def _qident(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(name or "")):
        raise ValueError("Unsafe SQL identifier")
    return '"' + str(name) + '"'


def _table_exists(e, table_name: str) -> bool:
    try:
        with e.connect() as c:
            return bool(c.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": table_name}).scalar())
    except Exception:
        return False


def _columns(e, table_name: str) -> List[str]:
    if not _table_exists(e, table_name):
        return []
    with e.connect() as c:
        rows = c.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name=:t
            ORDER BY ordinal_position
        """), {"t": table_name}).scalars().all()
    return [str(x) for x in rows]


def _candidate_key_columns(cols: List[str]) -> List[str]:
    preferred = [
        "id", "record_id", "property_id", "source_id", "pk", "row_id",
        "master_property_id", "canonical_id", "listing_id", "ad_id",
        "item_id", "entry_id",
    ]
    exact = [x for x in preferred if x in cols]
    fuzzy = [x for x in cols if x.lower().endswith("_id") and x not in exact]
    return exact + fuzzy


def _fetch_source_row(e, table_name: str, source_pk: str) -> Dict[str, Any]:
    if not table_name or not source_pk or not _table_exists(e, table_name):
        return {}
    cols = _columns(e, table_name)
    for col in _candidate_key_columns(cols):
        try:
            sql = f"""SELECT to_jsonb(t) FROM {_qident(table_name)} t
                      WHERE CAST({_qident(col)} AS TEXT)=:pk LIMIT 1"""
            with e.connect() as c:
                row = c.execute(text(sql), {"pk": str(source_pk)}).scalar()
            if isinstance(row, dict):
                return dict(row)
            if isinstance(row, str):
                return _dict(row)
        except Exception:
            continue
    return {}


def _walk_values(obj: Any, path: str = "") -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            if isinstance(v, (dict, list)):
                out.extend(_walk_values(v, p))
            elif v not in (None, ""):
                out.append((p, _clean(v)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(_walk_values(v, f"{path}[{i}]"))
    return out


def _property_text_score(s: str) -> int:
    u = _clean(s).upper()
    score = 0
    if re.search(r"\b(?:SHOP\s*NO[-\s]*\d+|[A-Z]{1,4}-?\d+[A-Z]?)\b", u):
        score += 4
    if re.search(r"\b\d{2,7}(?:\.\d+)?\s*(?:SQ\.?\s*FT|SQFT|SFT|FT)\b", u):
        score += 4
    if re.search(r"\b(?:GF|UGF|FF|SF|TF|BMT|MEZZ|TERR)\b", u):
        score += 2
    if re.search(r"\b(?:RENT|LEASE|SALE|COMMERCIAL|RESIDENTIAL|RETAIL|OFFICE)\b", u):
        score += 1
    compact = re.sub(r"[\s-]", "", u)
    if re.search(r"(?:\+?91)?[6-9]\d{9}|0\d{10}", compact):
        score += 2
    if "(" in u and ")" in u:
        score += 1
    return score


def _best_raw_text(source_row: Dict[str, Any], clean_record: Dict[str, Any]) -> str:
    for obj in (source_row, clean_record):
        for k in TEXT_KEYS:
            v = obj.get(k) if isinstance(obj, dict) else None
            if v not in (None, "", [], {}):
                s = _clean(v)
                if _property_text_score(s) >= 2:
                    return s

    candidates = []
    for path, value in _walk_values(source_row):
        if value:
            candidates.append((_property_text_score(value), len(value), path, value))
    candidates.sort(reverse=True)
    if candidates and candidates[0][0] >= 2:
        return candidates[0][3]

    candidates = []
    for path, value in _walk_values(clean_record):
        if value:
            candidates.append((_property_text_score(value), len(value), path, value))
    candidates.sort(reverse=True)
    if candidates and candidates[0][0] >= 2:
        return candidates[0][3]
    return ""


def _structured_value(source_row: Dict[str, Any], clean_record: Dict[str, Any], keys: Iterable[str]):
    for obj in (source_row, clean_record):
        if not isinstance(obj, dict):
            continue
        for k in keys:
            if obj.get(k) not in (None, "", [], {}):
                return obj.get(k)
    keyset = {str(k).lower() for k in keys}
    for path, value in _walk_values(source_row):
        leaf = path.split(".")[-1].lower()
        if leaf in keyset and value:
            return value
    return None


def _source_links(e, source: str) -> List[Dict[str, Any]]:
    pat = SOURCE_PATTERNS[source]
    with e.connect() as c:
        rows = c.execute(text("""
            SELECT DISTINCT ON (l.canonical_id)
                   l.canonical_id,l.source_type,l.source_table,l.source_pk,
                   l.source_row_hash,l.created_at
            FROM pi_master_source_links_v711 l
            WHERE l.master_entity_type='PROPERTY'
              AND (
                UPPER(COALESCE(l.source_type,'')) LIKE :pat OR
                UPPER(COALESCE(l.source_table,'')) LIKE :pat
              )
            ORDER BY l.canonical_id,l.created_at DESC,l.id DESC
        """), {"pat": pat}).mappings().all()
    return [dict(x) for x in rows]


def _current_master(e, cid: str) -> Dict[str, Any]:
    with e.connect() as c:
        row = c.execute(text("""
            SELECT * FROM pi_master_properties_v711
            WHERE canonical_id=:cid LIMIT 1
        """), {"cid": cid}).mappings().first()
    return dict(row) if row else {}


def _recover_magazine_record(e, link: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    cid = str(link.get("canonical_id") or "")
    master = _current_master(e, cid)
    if not master:
        return {}, {"status": "MASTER_MISSING"}

    clean_record = _dict(master.get("clean_record"))
    source_row = _fetch_source_row(
        e, str(link.get("source_table") or ""), str(link.get("source_pk") or "")
    )

    raw = _best_raw_text(source_row, clean_record)
    section = _structured_value(source_row, clean_record, SECTION_KEYS) or ""
    locality = master.get("locality") or _structured_value(source_row, clean_record, LOCALITY_KEYS) or ""

    parsed: Dict[str, Any] = {}
    if raw:
        try:
            import alliance_magazine_section_context_v680 as mag680
            parsed = mag680.enrich_record(
                {
                    "section_heading": section,
                    "raw_line": raw,
                    "original_description": raw,
                    "address": _structured_value(source_row, clean_record, ADDRESS_KEYS) or "",
                    "locality": locality,
                    "property_category": _structured_value(source_row, clean_record, CATEGORY_KEYS) or "",
                    "transaction_type": master.get("transaction_type") or _structured_value(source_row, clean_record, TRANSACTION_KEYS) or "",
                    "contact_name": _structured_value(source_row, clean_record, CONTACT_NAME_KEYS) or "",
                    "phones": [],
                },
                inherited_locality=str(locality or ""),
                inherited_transaction=str(master.get("transaction_type") or ""),
                inherited_section_heading=str(section or ""),
            )
        except Exception:
            parsed = {}

    recovered = {
        "address": parsed.get("address") or _structured_value(source_row, clean_record, ADDRESS_KEYS) or "",
        "locality": parsed.get("locality") or locality or "",
        "property_category": parsed.get("property_category") or _structured_value(source_row, clean_record, CATEGORY_KEYS) or "",
        "transaction_type": parsed.get("transaction_type") or master.get("transaction_type") or _structured_value(source_row, clean_record, TRANSACTION_KEYS) or "",
        "area_sqft": parsed.get("area_sqft") or _structured_value(source_row, clean_record, AREA_KEYS),
        "floor_codes": parsed.get("floor_codes") or "",
        "floors": parsed.get("floors") or _structured_value(source_row, clean_record, FLOOR_KEYS) or [],
        "contact_name": parsed.get("contact_name") or _structured_value(source_row, clean_record, CONTACT_NAME_KEYS) or "",
        "phones": parsed.get("phones") or [],
        "original_description": parsed.get("original_description") or raw or "",
        "raw_line": parsed.get("raw_line") or raw or "",
        "section_heading": parsed.get("section_heading") or section or "",
    }

    if not recovered["phones"]:
        phone = _structured_value(source_row, clean_record, PHONE_KEYS)
        if phone:
            recovered["phones"] = [str(phone)]

    evidence = {
        "source_table": link.get("source_table"),
        "source_pk": link.get("source_pk"),
        "source_row_found": bool(source_row),
        "source_row_keys": sorted(source_row.keys())[:100] if source_row else [],
        "raw_found": bool(raw),
    }
    return recovered, evidence


def _blank(cr: Dict[str, Any], *keys) -> bool:
    return all(cr.get(k) in (None, "", [], {}) for k in keys)


def _backfill_magazine(e) -> Dict[str, int]:
    scanned = updated = unresolved = 0
    if not _table_exists(e, "pi_master_properties_v711"):
        return {"scanned": 0, "updated": 0, "unresolved": 0}

    for link in _source_links(e, "MAGAZINE"):
        scanned += 1
        cid = str(link.get("canonical_id") or "")
        master = _current_master(e, cid)
        cr = _dict(master.get("clean_record"))
        rec, evidence = _recover_magazine_record(e, link)
        if not rec:
            unresolved += 1
            continue

        patch: Dict[str, Any] = {}
        if rec.get("address") and _blank(cr, "address", "exact_address", "property_address"):
            patch["address"] = rec["address"]
            patch["exact_address"] = rec["address"]
        if rec.get("locality") and not master.get("locality") and _blank(cr, "locality", "location"):
            patch["locality"] = rec["locality"]
        if rec.get("property_category") and _blank(cr, "property_category", "property_type", "category"):
            patch["property_category"] = rec["property_category"]
        if rec.get("transaction_type") and not master.get("transaction_type") and _blank(cr, "transaction_type", "rent_or_sale"):
            patch["transaction_type"] = rec["transaction_type"]
        if rec.get("floor_codes") and _blank(cr, "floor_codes", "floor", "floors"):
            patch["floor_codes"] = rec["floor_codes"]
            patch["floors"] = rec.get("floors") or []
        if rec.get("contact_name") and _blank(cr, "contact_name", "owner_name", "broker_name"):
            patch["contact_name"] = rec["contact_name"]
        if rec.get("phones") and _blank(cr, "contact_number", "contact_phone", "owner_contact", "broker_contact", "phone", "mobile", "phones"):
            patch["phones"] = rec["phones"]
            patch["contact_number"] = rec["phones"][0]
        if rec.get("original_description") and _blank(cr, "original_description", "original_message", "raw_line", "source_text"):
            patch["original_description"] = rec["original_description"]
            patch["raw_line"] = rec["original_description"]
        if rec.get("section_heading") and _blank(cr, "section_heading"):
            patch["section_heading"] = rec["section_heading"]

        top_tx = rec.get("transaction_type") if not master.get("transaction_type") else None
        top_area = rec.get("area_sqft") if not master.get("area_sqft") else None
        top_locality = rec.get("locality") if not master.get("locality") else None
        top_phones = rec.get("phones") if not master.get("phones") else None

        if not patch and not any((top_tx, top_area, top_locality, top_phones)):
            if not rec.get("address") or not rec.get("original_description"):
                unresolved += 1
            continue

        with e.begin() as c:
            c.execute(text("""
                UPDATE pi_master_properties_v711
                SET clean_record=COALESCE(clean_record,'{}'::jsonb) || CAST(:patch AS JSONB),
                    transaction_type=COALESCE(transaction_type,:tx),
                    locality=COALESCE(locality,:loc),
                    area_sqft=COALESCE(area_sqft,:area),
                    area_value=COALESCE(area_value,:area),
                    area_unit=COALESCE(area_unit,CASE WHEN CAST(:area AS DOUBLE PRECISION) IS NULL THEN NULL ELSE 'SQFT' END),
                    phones=CASE WHEN (phones IS NULL OR phones='[]'::jsonb) AND CAST(:phones AS JSONB) IS NOT NULL
                               THEN CAST(:phones AS JSONB) ELSE phones END,
                    updated_at=NOW()
                WHERE canonical_id=:cid
            """), {
                "cid": cid,
                "patch": json.dumps(_safe(patch), ensure_ascii=False),
                "tx": top_tx,
                "loc": top_locality,
                "area": top_area,
                "phones": json.dumps(_safe(top_phones), ensure_ascii=False) if top_phones else None,
            })
        updated += 1

    return {"scanned": scanned, "updated": updated, "unresolved": unresolved}


def _source_filter_sql(source: str):
    source = source.upper()
    if source == "MASTER":
        return "", {}
    pat = SOURCE_PATTERNS.get(source)
    if not pat:
        return " AND 1=0 ", {}
    return """
      AND EXISTS(
        SELECT 1 FROM pi_master_source_links_v711 l
        WHERE l.canonical_id=p.canonical_id
          AND l.master_entity_type='PROPERTY'
          AND (
            UPPER(COALESCE(l.source_type,'')) LIKE :pat OR
            UPPER(COALESCE(l.source_table,'')) LIKE :pat
          )
      )
    """, {"pat": pat}


def _stats(e, source: str) -> Dict[str, int]:
    clause, params = _source_filter_sql(source)
    sql = f"""
    SELECT
      COUNT(*) AS total,
      COUNT(*) FILTER (WHERE p.created_at::date=CURRENT_DATE) AS added_today,
      COUNT(*) FILTER (WHERE COALESCE(w.verification_status,'UNVERIFIED')='VERIFIED') AS verified,
      COUNT(*) FILTER (WHERE COALESCE(w.verification_status,'UNVERIFIED')<>'VERIFIED') AS unverified,
      COUNT(*) FILTER (WHERE COALESCE(w.availability_status,'UNKNOWN')='AVAILABLE') AS available,
      COUNT(*) FILTER (
        WHERE COALESCE(
          p.clean_record->>'address',
          p.clean_record->>'exact_address',
          p.clean_record->>'property_address',''
        )=''
      ) AS missing_address,
      COUNT(*) FILTER (
        WHERE COALESCE(
          p.clean_record->>'contact_number',
          p.clean_record->>'contact_phone',
          p.clean_record->>'owner_contact',
          p.clean_record->>'broker_contact',
          p.clean_record->>'phone',
          p.clean_record->>'mobile',''
        )=''
        AND (p.phones IS NULL OR p.phones='[]'::jsonb)
      ) AS missing_contact
    FROM pi_master_properties_v711 p
    LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=p.canonical_id
    WHERE NOT EXISTS(
      SELECT 1 FROM pi_property_archive_v801 ar
      WHERE ar.canonical_id=p.canonical_id AND ar.restored_at IS NULL
    )
    {clause}
    """
    with e.connect() as c:
        row = c.execute(text(sql), params).mappings().first() or {}
    d = {k: int(row.get(k) or 0) for k in (
        "total", "added_today", "verified", "unverified",
        "available", "missing_address", "missing_contact",
    )}
    d["needs_review"] = max(d["missing_address"], d["missing_contact"])
    return d


def _rows(e, source: str, q: str, transaction: str, limit: int):
    clause, params = _source_filter_sql(source)
    params.update({
        "q": f"%{q.strip()}%",
        "tx": transaction.upper().strip(),
        "n": max(1, min(int(limit), 1500)),
    })
    with e.connect() as c:
        rows = c.execute(text(f"""
            SELECT p.*,
                   COALESCE(w.verification_status,'UNVERIFIED') AS verification_status,
                   COALESCE(w.availability_status,'UNKNOWN') AS availability_status,
                   COALESCE(w.assigned_to,a.assigned_to) AS assigned_to
            FROM pi_master_properties_v711 p
            LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=p.canonical_id
            LEFT JOIN pi_master_action_state_v730 a ON a.canonical_id=p.canonical_id
            WHERE NOT EXISTS(
              SELECT 1 FROM pi_property_archive_v801 ar
              WHERE ar.canonical_id=p.canonical_id AND ar.restored_at IS NULL
            )
            {clause}
            AND (:tx='' OR UPPER(COALESCE(p.transaction_type,''))=:tx)
            AND (
              :q='%%' OR p.canonical_id ILIKE :q OR
              COALESCE(p.locality,'') ILIKE :q OR
              COALESCE(p.city,'') ILIKE :q OR
              COALESCE(p.clean_record::text,'') ILIKE :q
            )
            ORDER BY p.updated_at DESC NULLS LAST,p.created_at DESC NULLS LAST
            LIMIT :n
        """), params).mappings().all()
    return [dict(x) for x in rows]


def _source_label(e, cid: str):
    with e.connect() as c:
        row = c.execute(text("""
            SELECT source_type,source_table FROM pi_master_source_links_v711
            WHERE canonical_id=:cid AND master_entity_type='PROPERTY'
            ORDER BY created_at DESC,id DESC LIMIT 1
        """), {"cid": cid}).mappings().first()
    if not row:
        return "", ""
    return str(row.get("source_type") or ""), str(row.get("source_table") or "")


def _display_row(e, r: Dict[str, Any]) -> Dict[str, Any]:
    cr = _dict(r.get("clean_record"))
    address = _first(cr, ADDRESS_KEYS) or ""
    locality = r.get("locality") or _first(cr, LOCALITY_KEYS) or ""
    ptype = _first(cr, CATEGORY_KEYS) or ""
    tx = r.get("transaction_type") or _first(cr, TRANSACTION_KEYS) or ""
    sqft = _first(cr, ("area_sqft", "available_area_sqft")) or r.get("area_sqft") or r.get("area_value") or 0
    try:
        sqft = float(sqft or 0)
    except Exception:
        sqft = 0
    floors = _first(cr, FLOOR_KEYS) or ""
    if isinstance(floors, list):
        floors = ", ".join(str(x) for x in floors)
    cname = _first(cr, CONTACT_NAME_KEYS) or ""
    cphone = _first(cr, PHONE_KEYS) or ""
    if not cphone:
        phones = cr.get("phones") or r.get("phones")
        if isinstance(phones, str):
            try:
                phones = json.loads(phones)
            except Exception:
                phones = []
        if isinstance(phones, list) and phones:
            cphone = ", ".join(str(x) for x in phones if x)
    original = _first(cr, ("original_description", "original_message", "raw_line", "source_text")) or ""
    source, source_name = _source_label(e, r["canonical_id"])
    amount = _first(cr, ("rent", "monthly_rent", "rent_amount", "rent_in_figures"))
    if str(tx).upper() not in ("RENT", "LEASE"):
        amount = _first(cr, ("sale_price", "sale_amount", "price", "asking_price")) or amount
    if amount in (None, ""):
        amount = r.get("price_raw") or _first(cr, ("amount", "price_raw")) or ""
    status = r.get("availability_status") or r.get("verification_status") or "UNVERIFIED"
    return {
        "cid": r["canonical_id"], "address": address, "locality": locality,
        "ptype": ptype, "tx": tx, "sqft": sqft, "floors": floors,
        "amount": amount, "cname": cname, "cphone": cphone,
        "dt": _fmt_dt(r.get("created_at")), "status": status,
        "source": source, "source_name": source_name, "original": original,
        "assigned": r.get("assigned_to") or "",
    }


def _remove_get(app, path: str):
    keep = []
    for route in list(getattr(app, "routes", [])):
        methods = set(getattr(route, "methods", set()) or set())
        if getattr(route, "path", None) == path and "GET" in methods:
            continue
        keep.append(route)
    app.router.routes[:] = keep


def _shell(core, req, title: str, body: str) -> str:
    try:
        import alliance_business_os_v800 as v800
        page = v800._shell(core, req, title, body)
        return page.replace("Alliance CRE Operating System · 8.1", "Alliance CRE Operating System · 8.2")
    except Exception:
        return f"<!doctype html><html><body><h2>{_e(title)}</h2>{body}</body></html>"


def _stat_cards(s: Dict[str, int]) -> str:
    items = [
        ("Total Records", s["total"]), ("Added Today", s["added_today"]),
        ("Verified", s["verified"]), ("Unverified", s["unverified"]),
        ("Available", s["available"]), ("Missing Address", s["missing_address"]),
        ("Missing Contact", s["missing_contact"]), ("Needs Review", s["needs_review"]),
    ]
    return '<div class="grid">' + ''.join(
        f'<div class="card"><div class="muted">{_e(k)}</div><div class="num">{_e(v)}</div></div>'
        for k, v in items
    ) + '</div>'


def register(core):
    app = _app(core)
    e = _engine(core)
    if app is None or e is None:
        raise RuntimeError("Alliance CRE OS 8.2 requires core app + engine")

    try:
        recovery = _backfill_magazine(e)
    except Exception as exc:
        recovery = {
            "scanned": 0,
            "updated": 0,
            "unresolved": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }

    _remove_get(app, "/alliance/primary/database/{source}")
    _remove_get(app, "/alliance/primary/databases")

    @app.get("/alliance/primary/databases", response_class=HTMLResponse)
    def databases_hub(req: Request):
        _role(core, req)
        cards = []
        for src in SOURCE_NAMES:
            s = _stats(e, src)
            cards.append(
                f"""<div class="card">
                <div class="muted">{_e(src.title())} Database</div>
                <div class="num">{s["total"]}</div>
                <div class="tiny">Today: {s["added_today"]} · Available: {s["available"]} · Review: {s["needs_review"]}</div>
                <br><a class="btn good" href="/alliance/primary/database/{src.lower()}">Open</a>
                </div>"""
            )
        note = f"""<div class="card"><b>CRE OS 8.2 Magazine Repair:</b>
        scanned {recovery["scanned"]}, backfilled {recovery["updated"]}, unresolved {recovery["unresolved"]}.
        Only evidence-backed blank fields are filled. Existing source evidence is never overwritten.</div>"""
        body = '<div class="grid">' + ''.join(cards) + '</div>' + note
        return HTMLResponse(_shell(core, req, "Property Databases · CRE OS 8.2", body))

    @app.get("/alliance/primary/database/{source}", response_class=HTMLResponse)
    def source_database(
        req: Request,
        source: str,
        q: str = Query("", max_length=160),
        transaction: str = Query(""),
        limit: int = Query(500, ge=1, le=1500),
    ):
        _role(core, req)
        src = source.upper().strip()
        if src not in SOURCE_NAMES:
            return HTMLResponse("Unknown database", status_code=404)

        s = _stats(e, src)
        rows = _rows(e, src, q, transaction, limit)
        trs = []
        for r in rows:
            v = _display_row(e, r)
            address = v["address"] or "ADDRESS NOT CAPTURED"
            vals = [
                f'<a href="/alliance/primary/property/{_e(v["cid"])}">{_e(v["cid"])}</a>',
                _e(address), _e(v["locality"]), _e(v["ptype"]), _e(v["tx"]),
                _e(f'{v["sqft"]:.2f}' if v["sqft"] else ""), _e(v["floors"]),
                _e(v["amount"]), _e(v["cname"]), _e(v["cphone"]), _e(v["dt"]),
                _e(v["status"]), _e(v["assigned"]), _e(v["source"]), _e(v["original"]),
                f'<a class="btn light" href="/alliance/primary/property/{_e(v["cid"])}">Open / History</a>',
            ]
            trs.append("<tr>" + "".join("<td>" + x + "</td>" for x in vals) + "</tr>")

        headers = [
            "Property ID", "Exact Address", "Locality", "Property Type", "Rent/Sale",
            "Area Sq Ft", "Floor(s)", "Amount", "Contact Name", "Contact Number",
            "Date & Time", "Status", "Assigned To", "Source", "Original Description", "Open",
        ]
        form = f"""<div class="card"><form class="inline">
        <input name="q" value="{_e(q)}" placeholder="Search address, locality, contact, original description">
        <select name="transaction">
          <option value="">All Rent/Sale</option>
          <option value="RENT" {"selected" if transaction.upper()=="RENT" else ""}>Rent</option>
          <option value="SALE" {"selected" if transaction.upper()=="SALE" else ""}>Sale</option>
          <option value="LEASE" {"selected" if transaction.upper()=="LEASE" else ""}>Lease</option>
        </select>
        <input type="number" name="limit" value="{limit}">
        <button>Search</button>
        </form></div>"""

        magazine_note = ""
        if src == "MAGAZINE":
            magazine_note = f"""<div class="card">
            <b>Magazine Evidence Repair · 8.2</b><br>
            Address, locality, area, floors, category, rent/sale, contact name/number and original row
            are recovered from the actual linked magazine source row wherever retained evidence supports them.
            Nothing is invented.<br><br>
            Boot repair result: scanned {recovery["scanned"]}, updated {recovery["updated"]}, unresolved {recovery["unresolved"]}.
            </div>"""

        table = f"""<div class="card tablebox"><table>
        <thead><tr>{"".join("<th>"+h+"</th>" for h in headers)}</tr></thead>
        <tbody>{"".join(trs) if trs else '<tr><td colspan="16">No records found</td></tr>'}</tbody>
        </table></div>"""

        body = _stat_cards(s) + form + magazine_note + table
        return HTMLResponse(_shell(core, req, f"{src.title()} Property Database · CRE OS 8.2", body))

    @app.get("/api/cre-os-8-2/status")
    def status(req: Request):
        _role(core, req)
        return {
            "status": "OK",
            "version": VERSION,
            "mode": MODE,
            "magazine_recovery": recovery,
            "stats": {src: _stats(e, src) for src in SOURCE_NAMES},
        }

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "mode": MODE,
        "magazine_recovery": recovery,
        "routes": [
            "/alliance/primary/databases",
            "/alliance/primary/database/{source}",
            "/api/cre-os-8-2/status",
        ],
    }


def self_test():
    sample = {"payload": {"mystery_field": "A-7 INNER CIRCLE 7500FT FF+SF+TF (KAPIL 011 41550460)"}}
    raw = _best_raw_text(sample, {})
    ok = raw == "A-7 INNER CIRCLE 7500FT FF+SF+TF (KAPIL 011 41550460)"
    return {
        "version": VERSION,
        "status": "PASS" if ok else "FAIL",
        "unknown_source_field_recovered": ok,
        "sample": raw,
    }


if __name__ == "__main__":
    print(json.dumps(self_test(), indent=2))
