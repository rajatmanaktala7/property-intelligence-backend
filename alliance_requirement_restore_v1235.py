from __future__ import annotations

import hashlib
import html
import json
import re
from typing import Any

from fastapi import Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy import text

VERSION = "13.5.0-ASTRA-LIVE-HANDLER-PROOF"
SOURCES = ("MASTER", "NEWSPAPER", "MANUAL", "MAGAZINE", "WHATSAPP", "SOCIAL")

EXCLUDE_TOKENS = (
    "match", "workflow", "action", "audit", "review", "repair", "task", "journal",
    "score", "acceptance", "metric", "test", "exam", "certification", "work_no",
    "mapping", "map_", "source_link", "archive", "history", "log"
)

def _app(core):
    return getattr(core, "app", None) or core

def _engine(core):
    return getattr(core, "engine", None)

# ALLIANCE_REQUIREMENT_CANONICAL_APP_AUTH_V25
def _login(core, req):
    """Authenticate through app.py, the confirmed owner of POST /login."""
    import importlib

    canonical_auth = importlib.import_module("app")
    get_role = getattr(canonical_auth, "get_role", None)

    if callable(get_role):
        role = get_role(req)

        if role in {"admin", "team"}:
            return role

    raise HTTPException(
        status_code=401,
        detail="Login required",
    )

def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v))

def _shown(v: Any) -> str:
    if v in (None, "", [], {}):
        return "Not captured"
    return str(v)

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

def _walk(obj, wanted):
    wanted = {str(x).lower() for x in wanted}
    out = []
    def rec(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if str(k).lower() in wanted and v not in (None, "", [], {}):
                    out.append(v)
                rec(v)
        elif isinstance(x, list):
            for y in x:
                rec(y)
    rec(obj)
    return out

def _first(obj, keys, default=""):
    vals = _walk(obj, keys)
    if not vals:
        return default
    v = vals[0]
    if isinstance(v, list):
        return ", ".join(str(x) for x in v if x not in (None, ""))
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False, default=str)
    return v

def _contact(obj):
    values = _walk(obj, [
        "phone", "phones", "contact_no", "contact_number", "contact_phone",
        "mobile", "mobile_no", "sender_phone", "sender_mobile", "whatsapp_phone",
        "sender_jid", "remote_jid", "from"
    ])
    found = []
    for value in values:
        text_value = str(value or "").replace("@s.whatsapp.net", "")
        for match in re.finditer(r"(?<!\d)(?:\+?91[\s.-]?)?([6-9](?:[\s.-]?\d){9})(?!\d)", text_value):
            digits = re.sub(r"\D", "", match.group(1))
            if len(digits) == 10 and digits not in found:
                found.append(digits)
    if found:
        return ", ".join(found)
    return _first(obj, [
        "phone", "phones", "contact_no", "contact_number", "contact_phone",
        "mobile", "mobile_no", "sender_phone", "sender_mobile"
    ])

def _message(obj):
    vals = _walk(obj, [
        "original_message", "requirement_message", "message", "raw_message", "raw_text",
        "source_text", "description", "requirement_text", "requirement",
        "additional_points", "remarks", "notes", "content"
    ])
    candidates = []
    for v in vals:
        if isinstance(v, (dict, list)):
            continue
        s = re.sub(r"\s+", " ", str(v or "")).strip()
        if s:
            candidates.append(s)
    return max(candidates, key=len) if candidates else ""

def _qident(name):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(name or "")):
        raise ValueError("Unsafe SQL identifier")
    return '"' + str(name) + '"'

def _table_exists(e, name):
    try:
        with e.connect() as c:
            return bool(c.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": name}).scalar())
    except Exception:
        return False

def _classify(value):
    s = str(value or "").upper()
    if "NEWSPAPER" in s or "NEWS_PAPER" in s:
        return "NEWSPAPER"
    if "WHATSAPP" in s or "WAI_" in s or s.startswith("WA_") or "_WA_" in s:
        return "WHATSAPP"
    if "MAGAZINE" in s:
        return "MAGAZINE"
    if any(token in s for token in ("SOCIAL", "LINKEDIN", "FACEBOOK", "INSTAGRAM")):
        return "SOCIAL"
    if "MANUAL" in s:
        return "MANUAL"
    return "OTHER"

def _discover_tables(e):
    try:
        with e.connect() as c:
            names = c.execute(text("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema=current_schema()
                  AND table_type='BASE TABLE'
                  AND table_name ILIKE '%requirement%'
                ORDER BY table_name
            """)).scalars().all()
    except Exception:
        return []
    exact_skip = {"pi_master_requirements_v711", "pi_requirement_work_no_v1230"}
    out = []
    for name in names:
        s = str(name)
        lo = s.lower()
        if s in exact_skip or any(tok in lo for tok in EXCLUDE_TOKENS):
            continue
        out.append(s)
    return out[:60]

def _source_hint(obj):
    keys = [
        "source", "source_type", "source_name", "channel", "import_source", "origin",
        "database_source", "ingestion_source"
    ]
    return " ".join(str(_first(obj, [k], "")) for k in keys)

def _safe_budget(value, message=""):
    """Never present a phone/JID as money just because a source column was mis-mapped."""
    raw = str(value or "").strip()
    digits = re.sub(r"\D", "", raw)
    phone_like = bool(re.fullmatch(r"(?:91)?[6-9]\d{9}", digits))
    # A mobile-shaped field is never a budget.  Message context is deliberately
    # ignored here: a rental request often contains the word "rent" beside its
    # sender number, which was the original false-positive path.
    has_money_context = bool(re.search(r"(?:â‚¹|rs\.?|inr|lakh|lac|crore|cr)", raw, re.I))
    return "" if phone_like and not has_money_context else raw

def _transaction_from_evidence(value, message=""):
    blob = (str(value or "") + " " + str(message or "")).upper()
    if re.search(r"\b(OUTRIGHT|PURCHASE|BUY|SALE)\b", blob):
        return "SALE"
    if re.search(r"\b(RENT|LEASE|LETTING)\b", blob):
        return "RENT"
    return ""

def _normalize_source_row(table, obj, idx):
    source = _classify(table)
    if source == "OTHER":
        source = _classify(_source_hint(obj))
    canonical_id = _first(obj, ["canonical_id", "master_requirement_id", "master_id"])
    source_pk = _first(obj, ["id", "record_id", "requirement_id", "source_id", "pk"], idx)
    amin = _first(obj, ["area_min", "area_min_sqft", "minimum_area", "minimum_area_sqft", "min_area", "min_area_sqft"])
    amax = _first(obj, ["area_max", "area_max_sqft", "maximum_area", "maximum_area_sqft", "max_area", "max_area_sqft"])
    area = f"{amin}-{amax}" if amin or amax else _first(obj, ["area_sqft", "requirement_sqft", "required_area", "area"])
    message = _message(obj)
    raw_tx = _first(obj, ["transaction_type", "transaction", "rent_sale", "rent_or_sale", "deal_type"])
    raw_budget = _first(obj, ["budget", "sale_budget", "rent_budget", "max_budget", "budget_raw"])
    return {
        "canonical_id": str(canonical_id or ""),
        "source_pk": str(source_pk or idx),
        "source_table": table,
        "source": source,
        "message": message,
        "company": _first(obj, ["company_name", "brand_name", "client_company", "company", "retailer_name"]),
        "contact_name": _first(obj, ["contact_name", "client_name", "sender_name", "name"]),
        "contact": _contact(obj),
        "location": _first(obj, [
            "preferred_locations", "preferred_location", "location", "locality",
            "city", "area_name", "micro_market"
        ]),
        "transaction": _transaction_from_evidence(raw_tx, message),
        "category": _first(obj, [
            "property_category", "required_property_category", "category",
            "intended_use", "use", "use_case", "business_category"
        ]),
        "property_type": _first(obj, ["property_type", "required_property_type", "asset_type"]),
        "area": area,
        "budget": _safe_budget(raw_budget, message),
        "created_at": _first(obj, ["created_at", "timestamp", "message_timestamp", "date", "captured_at"]),
        "verification": "SOURCE / NEEDS MASTER VERIFICATION",
        "assigned_to": "",
        "is_master": False,
    }

def _source_rows(e, source, per_table=5000):
    rows = []
    tables = _discover_tables(e)
    for table in tables:
        table_class = _classify(table)
        if table_class != "OTHER" and table_class != source:
            continue
        try:
            sql = f"SELECT to_jsonb(t) AS d FROM {_qident(table)} t LIMIT :n"
            with e.connect() as c:
                raw_rows = c.execute(text(sql), {"n": int(per_table)}).scalars().all()
        except Exception:
            continue
        for idx, raw in enumerate(raw_rows, 1):
            obj = _dict(raw) if not isinstance(raw, dict) else dict(raw)
            if not obj:
                continue
            row = _normalize_source_row(table, obj, idx)
            if row["source"] != source:
                continue
            if not any((row["message"], row["contact"], row["location"], row["company"], row["canonical_id"])):
                continue
            rows.append(row)
    return rows, tables

def _master_rows(e, source, limit=10000):
    if not _table_exists(e, "pi_master_requirements_v711"):
        return []
    params = {"n": int(limit)}
    source_clause = ""
    if source != "MASTER":
        params["pat"] = f"%{source}%"
        source_clause = """
          AND (
            EXISTS (
              SELECT 1 FROM pi_master_source_links_v711 l
              WHERE l.canonical_id=r.canonical_id
                AND l.master_entity_type='REQUIREMENT'
                AND (
                  UPPER(COALESCE(l.source_type,'')) LIKE :pat OR
                  UPPER(COALESCE(l.source_table,'')) LIKE :pat
                )
            )
            OR UPPER(COALESCE(r.clean_record->>'source','')) LIKE :pat
            OR UPPER(COALESCE(r.clean_record->>'source_type','')) LIKE :pat
            OR UPPER(COALESCE(r.clean_record->>'source_name','')) LIKE :pat
            OR UPPER(COALESCE(r.clean_record->>'channel','')) LIKE :pat
            OR UPPER(COALESCE(r.clean_record->>'import_source','')) LIKE :pat
          )
        """
    sql = f"""
        SELECT r.canonical_id,r.locality,r.city,r.transaction_type,r.area_sqft,
               r.sale_budget,r.rent_budget,r.phones,r.clean_record,r.created_at,
               COALESCE(w.verification_status,'UNVERIFIED') AS verification_status,
               COALESCE(a.assigned_to,'') AS assigned_to
        FROM pi_master_requirements_v711 r
        LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=r.canonical_id
        LEFT JOIN pi_master_action_state_v730 a ON a.canonical_id=r.canonical_id
        WHERE 1=1 {source_clause}
        ORDER BY r.created_at DESC NULLS LAST
        LIMIT :n
    """
    try:
        with e.connect() as c:
            data = c.execute(text(sql), params).mappings().all()
    except Exception:
        return []
    out = []
    for x in data:
        r = dict(x)
        cr = _dict(r.get("clean_record"))
        out.append({
            "canonical_id": str(r.get("canonical_id") or ""),
            "source_pk": "",
            "source_table": "pi_master_requirements_v711",
            "source": "MASTER" if source == "MASTER" else source,
            "message": _message(cr),
            "company": _first(cr, ["company_name", "brand_name", "client_company", "company", "retailer_name"]),
            "contact_name": _first(cr, ["contact_name", "client_name", "name", "sender_name"]),
            "contact": r.get("phones") or _first(cr, ["contact_phone", "contact_number", "phone", "mobile"]),
            "location": r.get("locality") or r.get("city") or _first(cr, ["location", "preferred_location"]),
            "transaction": r.get("transaction_type") or _first(cr, ["transaction_type", "rent_or_sale"]),
            "category": _first(cr, ["property_category", "required_property_category", "category", "intended_use", "use"]),
            "property_type": _first(cr, ["property_type", "required_property_type", "asset_type"]),
            "area": r.get("area_sqft") or _first(cr, ["required_area", "required_area_sqft", "minimum_area_sqft", "maximum_area_sqft"]),
            "budget": r.get("sale_budget") or r.get("rent_budget") or _first(cr, ["budget", "budget_raw"]),
            "created_at": r.get("created_at"),
            "verification": r.get("verification_status") or "UNVERIFIED",
            "assigned_to": r.get("assigned_to") or "",
            "is_master": True,
        })
    return out

def _gate_rows(e, limit=10000):
    """Load the all-source evidence inventory used by the Master Requirements view."""
    if not _table_exists(e, "pi_requirement_gate_v1191"):
        return []
    try:
        with e.connect() as c:
            raw_rows = c.execute(text("""
                SELECT to_jsonb(g) AS d
                FROM pi_requirement_gate_v1191 g
                WHERE COALESCE(classification,'') NOT IN ('REJECTED','NOISE','REJECTED/EXPIRED')
                ORDER BY created_at DESC NULLS LAST, id DESC
                LIMIT :n
            """), {"n": int(limit)}).scalars().all()
    except Exception:
        return []
    out = []
    for idx, raw in enumerate(raw_rows, 1):
        obj = _dict(raw) if not isinstance(raw, dict) else dict(raw)
        extracted = _dict(obj.get("extracted_fields"))
        merged = dict(extracted)
        merged.update({k: v for k, v in obj.items() if v not in (None, "", [], {})})
        source_table = str(obj.get("source_table") or "pi_requirement_gate_v1191")
        source = _classify(obj.get("source_type") or source_table)
        if source == "OTHER":
            source = "SOCIAL" if _classify(_source_hint(merged)) == "SOCIAL" else "OTHER"
        classification = str(obj.get("classification") or "").upper()
        canonical_id = _first(merged, ["canonical_id", "master_requirement_id", "master_id"], "")
        row = _normalize_source_row(source_table, merged, idx)
        row.update({
            "gate_id": obj.get("id"),
            "canonical_id": str(canonical_id or ""),
            "source_pk": str(obj.get("source_pk") or obj.get("id") or row.get("source_pk") or idx),
            "source": source,
            "message": str(obj.get("original_message") or row.get("message") or ""),
            "contact": _contact(merged) or _first(merged, ["contact_numbers"], ""),
            "created_at": obj.get("created_at") or row.get("created_at"),
            "verification": "VERIFIED" if "VERIFIED" in classification and "NEEDS" not in classification else "SOURCE / NEEDS VERIFICATION",
            "assigned_to": _first(merged, ["assigned_to", "owner", "team_member"], ""),
            "is_master": bool(canonical_id),
        })
        out.append(row)
    return out


def _fingerprint(row):
    cid = str(row.get("canonical_id") or "").strip().lower()
    if cid:
        return "CID:" + cid
    phone = re.sub(r"\D", "", str(row.get("contact") or ""))
    msg = re.sub(r"\s+", " ", str(row.get("message") or "").strip().lower())
    loc = re.sub(r"\s+", " ", str(row.get("location") or "").strip().lower())
    company = re.sub(r"\s+", " ", str(row.get("company") or "").strip().lower())
    seed = "|".join((phone, msg, loc, company))
    return "FP:" + hashlib.sha1(seed.encode("utf-8", "ignore")).hexdigest()

def _manual_operational_rows(e, limit=1000):
    """New-architecture Manual Requirement adapter.

    Authority stays pi_operational_requirements. The adapter only maps the
    settled operational schema into the common Requirement UI row contract.
    It does not promote, duplicate, delete or mutate source records.
    """
    lim = max(1, min(int(limit or 1000), 5000))

    with e.connect() as conn:
        raw_rows = conn.execute(text(f"""
            SELECT *
            FROM pi_operational_requirements
            WHERE UPPER(COALESCE(entry_source, 'MANUAL')) = 'MANUAL'
            ORDER BY id DESC
            LIMIT {lim}
        """)).mappings().all()

    out = []
    for raw in raw_rows:
        d = dict(raw)
        row = _normalize_source_row("pi_operational_requirements", d, int(d.get("id") or 0))

        # Preserve the exact Manual source identity. This is what Run Matcher
        # uses to locate the source row before Gate/canonical verification.
        row["source"] = "MANUAL"
        row["source_table"] = "pi_operational_requirements"
        row["source_pk"] = str(d.get("id") or row.get("source_pk") or "")
        row["source_id"] = row["source_pk"]

        # Map settled operational columns into the common new Requirement UI.
        row["message"] = (
            str(d.get("original_message") or "").strip()
            or str(d.get("requirement") or "").strip()
            or str(d.get("requirement_text") or "").strip()
            or str(d.get("description") or "").strip()
            or str(d.get("additional_points") or "").strip()
            or str(row.get("message") or "").strip()
        )
        row["company"] = (
            d.get("company_name")
            or d.get("client_name")
            or row.get("company")
            or ""
        )
        row["contact_name"] = (
            d.get("contact_name")
            or d.get("client_name")
            or row.get("contact_name")
            or ""
        )
        row["contact"] = (
            d.get("contact_number")
            or d.get("contact_no")
            or d.get("phone")
            or d.get("mobile")
            or row.get("contact")
            or ""
        )
        row["location"] = (
            d.get("preferred_locations")
            or d.get("preferred_location")
            or d.get("city")
            or d.get("location")
            or row.get("location")
            or ""
        )
        row["transaction"] = (
            _transaction_from_evidence(
                d.get("transaction_type") or d.get("transaction") or "",
                row.get("message") or "",
            )
            or str(d.get("transaction_type") or d.get("transaction") or "").strip().upper()
        )
        row["category"] = (
            d.get("property_category")
            or d.get("category")
            or d.get("intended_use")
            or row.get("category")
            or ""
        )
        row["property_type"] = (
            d.get("property_type")
            or row.get("property_type")
            or ""
        )

        amin = d.get("area_min_sqft") or d.get("area_min") or d.get("minimum_area")
        amax = d.get("area_max_sqft") or d.get("area_max") or d.get("maximum_area")
        if amin not in (None, "") or amax not in (None, ""):
            row["area"] = f"{amin or ''}-{amax or ''}".strip("-")
        else:
            row["area"] = (
                d.get("area_sqft")
                or d.get("required_area")
                or d.get("area")
                or row.get("area")
                or ""
            )

        row["budget"] = _safe_budget(
            d.get("budget")
            or d.get("budget_max")
            or d.get("rent_budget")
            or d.get("sale_budget")
            or row.get("budget")
            or "",
            row.get("message") or "",
        )
        row["created_at"] = (
            d.get("created_at")
            or d.get("timestamp")
            or row.get("created_at")
        )
        row["verification"] = (
            d.get("verification_status")
            or d.get("verification")
            or "RAW"
        )
        row["assigned_to"] = (
            d.get("assigned_to")
            or d.get("team_member")
            or ""
        )
        row["canonical_id"] = str(
            d.get("canonical_id")
            or d.get("master_requirement_id")
            or ""
        )
        row["is_master"] = bool(row["canonical_id"])

        out.append(row)

    return out


def _combined(e, source):
    # Manual form authority is pi_operational_requirements. Do this before any
    # Gate/historical work so the Manual page is both complete and fast.
    if source == "MANUAL":
        manual=_manual_operational_rows(e,1000)
        return manual, {"master":0,"source_only":len(manual),"tables":["pi_operational_requirements"]}
    masters = _master_rows(e, source)
    if source == "MASTER":
        gate_rows = _gate_rows(e)
        seen = {_fingerprint(r) for r in gate_rows}
        canonical_only = []
        for row in masters:
            fp = _fingerprint(row)
            if fp not in seen:
                seen.add(fp)
                canonical_only.append(row)
        rows = gate_rows + canonical_only
        rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        return rows, {
            "master": sum(1 for r in rows if r.get("is_master")),
            "source_only": sum(1 for r in rows if not r.get("is_master")),
            "tables": ["pi_requirement_gate_v1191", "pi_master_requirements_v711"],
        }
    # The Requirement Gate is the settled all-source evidence authority. Read it
    # directly first instead of scanning every historical requirement table on
    # every page load. This also restores MANUAL rows created through the current
    # manual-entry flow, whose source evidence lives in the Gate.
    gate_all = _gate_rows(e, limit=20000)
    gate_source = [r for r in gate_all if str(r.get("source") or "").upper() == source]
    source_rows = gate_source
    tables = ["pi_requirement_gate_v1191"]
    # Compatibility fallback only when the Gate has no rows for this source.
    if not source_rows:
        source_rows, tables = _source_rows(e, source, per_table=1500)
    master_ids = {str(r.get("canonical_id") or "") for r in masters if r.get("canonical_id")}
    seen = {_fingerprint(r) for r in masters}
    restored = []
    for row in source_rows:
        cid = str(row.get("canonical_id") or "")
        if cid and cid in master_ids:
            continue
        fp = _fingerprint(row)
        if fp in seen:
            continue
        seen.add(fp)
        restored.append(row)
    return masters + restored, {"master": len(masters), "source_only": len(restored), "tables": tables}

def _filtered(rows, q, location, transaction, status, assigned):
    ql = q.strip().lower()
    ll = location.strip().lower()
    tx = transaction.strip().upper()
    st = status.strip().upper()
    aa = assigned.strip().lower()
    out = []
    for row in rows:
        blob = " ".join(str(row.get(k) or "") for k in (
            "canonical_id","source_pk","source_table","message","company","contact_name",
            "contact","location","transaction","category","property_type","area","budget"
        )).lower()
        if ql and ql not in blob:
            continue
        if ll and ll not in str(row.get("location") or "").lower() and ll not in blob:
            continue
        if tx and tx != str(row.get("transaction") or "").upper():
            continue
        if st and st not in str(row.get("verification") or "").upper():
            continue
        if aa and aa not in str(row.get("assigned_to") or "").lower():
            continue
        out.append(row)
    return out

def _remove_get(app, path):
    app.router.routes[:] = [
        r for r in list(app.router.routes)
        if not (
            getattr(r, "path", None) == path
            and "GET" in set(getattr(r, "methods", set()) or set())
        )
    ]

def _move_front(app, path):
    found = [
        r for r in list(app.router.routes)
        if getattr(r, "path", None) == path
        and "GET" in set(getattr(r, "methods", set()) or set())
    ]
    for r in found:
        try:
            app.router.routes.remove(r)
        except ValueError:
            pass
    for r in reversed(found):
        app.router.routes.insert(0, r)

def _nav():
    return """
    <nav>
      <a href="#" onclick="history.back();return false">â† Back to Previous Page</a>
      <a href="/team-dashboard-v376">Back to Dashboard</a>
    </nav>
    """

def _shell(title, body):
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)}</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f4f7fb;color:#172033;font-family:Arial,sans-serif}}
header{{background:#10223f;color:white;padding:18px 22px}}header b{{font-size:22px}}header small{{display:block;margin-top:4px}}
nav{{background:white;border-bottom:1px solid #d6dde6;padding:9px;display:flex;gap:6px;flex-wrap:wrap;position:sticky;top:0;z-index:20}}
nav a,.btn,button{{background:#10223f;color:white;text-decoration:none;border:0;border-radius:7px;padding:8px 10px;font-size:12px;cursor:pointer}}
.wrap{{max-width:1900px;margin:auto;padding:16px}}.card{{background:white;border:1px solid #dfe5ec;border-radius:12px;padding:14px;margin-bottom:12px}}
.grid{{display:grid;grid-template-columns:repeat(5,minmax(190px,1fr));gap:10px}}
@media(max-width:1000px){{.grid{{grid-template-columns:1fr 1fr}}}}@media(max-width:620px){{.grid{{grid-template-columns:1fr}}}}
.dbcard{{background:white;border:1px solid #ccd7e4;border-radius:12px;padding:14px;text-decoration:none;color:#172033}}
.dbcard b{{display:block;color:#16315a}}.num{{font-size:30px;font-weight:900;margin:6px 0}}.sub{{font-size:11px;color:#667085;min-height:30px}}.open{{font-size:11px;color:#175cd3;font-weight:800;margin-top:8px}}
.notice{{background:#fff9ec;border:1px solid #f2c86b;border-radius:10px;padding:11px;margin-bottom:12px}}
.filters{{display:grid;grid-template-columns:2fr 1fr 1fr 1fr 1fr auto;gap:6px}}@media(max-width:900px){{.filters{{grid-template-columns:1fr 1fr}}}}
input,select{{width:100%;padding:8px;border:1px solid #98a2b3;border-radius:6px}}
.tablebox{{overflow:auto;max-height:72vh;background:white;border:1px solid #dfe5ec}}table{{border-collapse:collapse;width:max-content;min-width:100%;font-size:11px}}
th,td{{border:1px solid #d0d5dd;padding:7px;text-align:left;vertical-align:top;white-space:normal}}th{{position:sticky;top:0;background:#e9eef5;z-index:4}}
.desc{{min-width:240px;max-width:390px}}.sourceonly{{background:#fff8e8}}.masterrow{{background:#f8fff9}}
.reqtools{{display:flex;align-items:center;gap:5px;margin-bottom:7px;background:white;border:1px solid #d0d5dd;padding:5px;width:max-content;position:sticky;left:0;z-index:6}}
.reqtools button{{padding:4px 7px}}body.compact table{{font-size:10px}}body.compact th,body.compact td{{padding:4px 5px}}body.compact .desc{{min-width:200px;max-width:320px}}
</style><script>
function reqApplyZoom(){{var z=Number(localStorage.getItem('allianceReqZoom')||100);document.querySelectorAll('.tablebox table').forEach(function(t){{t.style.setProperty('font-size',(11*z/100)+'px','important')}});document.querySelectorAll('.tablebox th,.tablebox td').forEach(function(x){{x.style.setProperty('padding',(7*z/100)+'px','important')}})}}
function reqZoom(d){{var z=Number(localStorage.getItem('allianceReqZoom')||100);z=Math.max(70,Math.min(160,z+d*10));localStorage.setItem('allianceReqZoom',z);reqApplyZoom()}}
function reqZoomReset(){{localStorage.setItem('allianceReqZoom',100);reqApplyZoom()}}
function reqCompact(){{document.body.classList.toggle('compact');localStorage.setItem('allianceReqCompact',document.body.classList.contains('compact')?'1':'0')}}
document.addEventListener('DOMContentLoaded',function(){{if(localStorage.getItem('allianceReqCompact')==='1')document.body.classList.add('compact');reqApplyZoom()}})
</script></head><body>
<header><b>Alliance CRE Intelligence OS 11</b><small>PROPERTY â†’ VERIFY â†’ REQUIREMENT â†’ MATCH â†’ CLIENT â†’ FOLLOW-UP â†’ DEAL</small></header>
{_nav()}<div class="wrap"><h2>{_e(title)}</h2>{body}</div>
</body></html>"""

def _fast_requirement_counts(e):
    """Fast counters from settled authorities. Each source is isolated so one bad
    legacy query can never zero a healthy Manual counter."""
    counts = {source: 0 for source in SOURCES}
    errors = {}

    # Manual has its own settled operational authority. Count it independently
    # before touching Gate/Master queries.
    try:
        with e.connect() as c:
            counts["MANUAL"] = int(c.execute(text("""
                SELECT COUNT(*) FROM pi_operational_requirements
                WHERE UPPER(COALESCE(entry_source,'MANUAL'))='MANUAL'
            """)).scalar() or 0)
    except Exception as exc:
        errors["MANUAL"] = f"{type(exc).__name__}: {str(exc)[:160]}"

    try:
        if _table_exists(e, "pi_requirement_gate_v1191"):
            with e.connect() as c:
                counts["MASTER"] = int(c.execute(text("""
                    SELECT COUNT(*) FROM pi_requirement_gate_v1191
                    WHERE COALESCE(classification,'') NOT IN ('REJECTED','NOISE','REJECTED/EXPIRED')
                """)).scalar() or 0)
                for source in ("NEWSPAPER","MAGAZINE","WHATSAPP","SOCIAL"):
                    pats = {
                        "NEWSPAPER":["%NEWSPAPER%"],
                        "MAGAZINE":["%MAGAZINE%"],
                        "WHATSAPP":["%WHATSAPP%","WA_%","WAI_%"],
                        "SOCIAL":["%SOCIAL%","%LINKEDIN%","%FACEBOOK%","%INSTAGRAM%"],
                    }[source]
                    clauses=[]; params={}
                    for i,p in enumerate(pats):
                        clauses.append(f"(UPPER(COALESCE(source_type,'')) LIKE :p{i} OR UPPER(COALESCE(source_table,'')) LIKE :p{i})")
                        params[f"p{i}"]=p
                    try:
                        counts[source]=int(c.execute(text("SELECT COUNT(*) FROM pi_requirement_gate_v1191 WHERE COALESCE(classification,'') NOT IN ('REJECTED','NOISE','REJECTED/EXPIRED') AND ("+" OR ".join(clauses)+")"),params).scalar() or 0)
                    except Exception as exc:
                        errors[source] = f"{type(exc).__name__}: {str(exc)[:160]}"
        elif _table_exists(e, "pi_master_requirements_v711"):
            with e.connect() as c:
                counts["MASTER"] = int(c.execute(text("SELECT COUNT(*) FROM pi_master_requirements_v711")).scalar() or 0)
    except Exception as exc:
        errors["MASTER"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    return counts, errors

def _hub(e):
    # ALLIANCE_REQUIREMENT_FAST_COUNTS_V1
    counts, count_errors = _fast_requirement_counts(e)
    cards = []
    details = {}

    for source in SOURCES:
        count = counts.get(source, 0)
        label = "Social Media" if source == "SOCIAL" else source.title()

        if source == "MASTER":
            note = "Canonical matcher requirement inventory"
        elif source in {"NEWSPAPER", "MAGAZINE"} and count == 0:
            note = (
                "No genuine demand records detected. "
                "Property advertisements remain in the Property Database."
            )
        else:
            note = "Source requirement records awaiting Master verification"

        details[source] = {
            "count": count,
            "count_method": "SQL_COUNT_ONLY",
            "full_record_scan": False,
        }

        cards.append(f"""<a class="dbcard" href="/alliance/final/requirements/{source.lower()}">
          <b>{_e(label)} Requirements</b>
          <div class="num">{count:,}</div>
          <div class="sub">{_e(note)}</div>
          <div class="open">View Database</div>
        </a>""")

    warning = ""
    if count_errors:
        warning = (
            '<div class="notice">Some source counters could not be read. '
            'The underlying databases remain available.</div>'
        )

    actions = """<div class="card"><b>Add or open requirement sources</b><br><br>
    <a class="btn" href="/alliance/final/requirements/add-manual">+ Add Manual Requirement</a>
    <a class="btn" href="/alliance/final/requirements/newspaper">Newspaper</a>
    <a class="btn" href="/alliance/final/requirements/manual">Manual</a>
    <a class="btn" href="/alliance/final/requirements/magazine">Magazine</a>
    <a class="btn" href="/alliance/final/requirements/whatsapp">WhatsApp</a>
    <a class="btn" href="/alliance/final/requirements/social">Social Media / Other</a>
    </div>"""
    body = f"""{actions}<div class="notice"><b>Master Requirements is the complete all-source evidence inventory.</b>
    Manual, Newspaper, Magazine, WhatsApp and Social Media records remain traceable
    to their original source. Only verified canonical requirements are eligible for
    Smart Matcher; source evidence is never silently promoted or duplicated.</div>
    {warning}
    <div class="grid">{''.join(cards)}</div>"""

    return _shell("6 Requirement Databases", body), details


def _match_action(row, source):
    is_master = bool(row.get("is_master"))
    cid = str(row.get("canonical_id") or "")
    src = str(row.get("source") or source).upper()
    spk = str(row.get("source_pk") or "")
    if is_master and cid:
        verification = str(row.get("verification") or "").upper()
        if verification == "VERIFIED":
            return (
                f'<a class="btn" href="/alliance/primary/requirement/{_e(cid)}">Open</a> '
                f'<a class="btn" href="/alliance/primary/matcher?requirement_id={_e(cid)}">Run Match</a>'
            )
        return f'<a class="btn" href="/alliance/primary/requirement/{_e(cid)}">Verify &amp; Run Match</a>'
    if row.get("gate_id"):
        return (
            f'<a class="btn" href="/alliance/final/requirements/run-match?gate_id='
            f'{_e(row.get("gate_id"))}">Verify &amp; Run Match</a>'
        )
    if src in SOURCES and src != "MASTER" and spk:
        return (
            f'<a class="btn" href="/alliance/final/requirements/run-match?source={_e(src)}'
            f'&source_pk={_e(spk)}">Verify &amp; Run Match</a>'
        )
    return '<span>Review source evidence</span>'


def _table(e, source, q, location, transaction, status, assigned, limit):
    rows, meta = _combined(e, source)
    rows = _filtered(rows, q, location, transaction, status, assigned)[:limit]
    filters = f"""<div class="card"><form class="filters">
      <input name="q" value="{_e(q)}" placeholder="Search any field, contact, source or ID">
      <input name="location" value="{_e(location)}" placeholder="Location">
      <select name="transaction"><option value="">Rent / Sale</option>
        <option value="RENT" {'selected' if transaction.upper()=='RENT' else ''}>RENT</option>
        <option value="LEASE" {'selected' if transaction.upper()=='LEASE' else ''}>LEASE</option>
        <option value="SALE" {'selected' if transaction.upper()=='SALE' else ''}>SALE</option></select>
      <select name="status"><option value="">All Verification</option>
        <option value="VERIFIED" {'selected' if status.upper()=='VERIFIED' else ''}>VERIFIED</option>
        <option value="UNVERIFIED" {'selected' if status.upper()=='UNVERIFIED' else ''}>UNVERIFIED</option>
        <option value="SOURCE" {'selected' if status.upper()=='SOURCE' else ''}>SOURCE / NEEDS VERIFICATION</option></select>
      <input name="assigned" value="{_e(assigned)}" placeholder="Assigned To">
      <button>Search</button>
    </form></div>"""
    trs = []
    for row in rows:
        is_master = bool(row.get("is_master"))
        cid = str(row.get("canonical_id") or "")
        src = str(row.get("source") or source).upper()
        spk = str(row.get("source_pk") or "")
        action = _match_action(row, source)
        cls = "masterrow" if is_master else "sourceonly"
        vals = [
            row.get("created_at"), row.get("message"), row.get("company"),
            row.get("contact_name"), row.get("contact"), row.get("location"),
            row.get("category"), row.get("property_type"), row.get("area"),
            row.get("transaction"), row.get("budget"), action,
            row.get("verification"), row.get("assigned_to") or "UNASSIGNED",
            row.get("source_table"), spk, cid,
        ]
        cells = []
        for i, value in enumerate(vals):
            if i == 11:
                cells.append(f"<td>{value}</td>")
            else:
                css = " class='desc'" if i == 1 else ""
                cells.append(f"<td{css}>{_e(_shown(value))}</td>")
        trs.append(f"<tr class='{cls}'>{''.join(cells)}</tr>")
    note = (
        f"<b>{len(rows)}</b> rows shown. Verified/canonical linked: <b>{meta['master']}</b> Â· "
        f"source evidence awaiting verification: <b>{meta['source_only']}</b>. "
        "Master is the all-source inventory; Smart Matcher continues to use verified canonical requirements only."
    )
    headers = [
        "Date / Time","Original Requirement","Client / Company","Contact Name",
        "Contact No.","Location","Category / Use","Property Type","Area",
        "Rent / Sale","Budget","Action","Verification","Assigned To",
        "Source","Source ID","Requirement ID"
    ]
    body = f"""<div class="notice">{note}</div>{filters}
    <div class="reqtools"><b>Table</b><button type="button" onclick="reqCompact()">Compact</button><button type="button" onclick="reqZoom(-1)">âˆ’</button><button type="button" onclick="reqZoom(1)">+</button><button type="button" onclick="reqZoomReset()">Reset</button></div>
    <div class="tablebox"><table><thead><tr>{''.join('<th>'+h+'</th>' for h in headers)}</tr></thead>
    <tbody>{''.join(trs) if trs else '<tr><td colspan="17">No requirements found.</td></tr>'}</tbody></table></div>"""
    return _shell(f"{source.title()} Requirements", body)

def _gate_tx(v):
    s = str(v or "").strip().upper()
    if s in {"RENT", "LEASE", "LEASING"}:
        return "LEASE"
    if s in {"SALE", "PURCHASE", "BUY"}:
        return "PURCHASE"
    return ""

def _json_list_text(v):
    if isinstance(v, list):
        return ", ".join(str(x) for x in v if str(x).strip())
    if v is None:
        return ""
    if isinstance(v, str):
        try:
            x = json.loads(v)
            if isinstance(x, list):
                return ", ".join(str(y) for y in x if str(y).strip())
        except Exception:
            return v
    return str(v)

def _ensure_gate_row(e, selected):
    import alliance_requirement_gate_v1191 as gate
    import alliance_requirement_master_bridge_v1199 as bridge

    with e.begin() as c:
        for ddl in getattr(gate, "DDL", []):
            c.execute(text(ddl))
    bridge.ensure_schema(e)

    message = str(selected.get("message") or "").strip()

    # MANUAL_MATCHER_STRUCTURED_FALLBACK_V1
    # Historical/manual requirements can legitimately have structured fields
    # without an original free-text message. Build matcher input from those
    # existing fields only. Never invent requirement data.
    if not message:
        parts = []

        company = str(selected.get("company") or "").strip()
        contact_name = str(selected.get("contact_name") or "").strip()
        category = str(
            selected.get("category")
            or selected.get("intended_use")
            or selected.get("property_type")
            or ""
        ).strip()
        location = str(selected.get("location") or "").strip()
        transaction = str(
            selected.get("transaction")
            or selected.get("transaction_type")
            or ""
        ).strip()
        area = str(selected.get("area") or "").strip()
        budget = str(
            selected.get("budget")
            or selected.get("budget_max")
            or selected.get("rent_budget")
            or selected.get("sale_budget")
            or ""
        ).strip()

        if company:
            parts.append(f"Company/Brand: {company}")
        elif contact_name:
            parts.append(f"Client: {contact_name}")

        if category:
            parts.append(f"Requirement: {category}")
        if location:
            parts.append(f"Location: {location}")
        if transaction:
            parts.append(f"Transaction: {transaction}")
        if area:
            parts.append(f"Area: {area}")
        if budget:
            parts.append(f"Budget: {budget}")

        message = " | ".join(parts).strip()

    if not message:
        raise HTTPException(
            400,
            "Source requirement has insufficient requirement details for matching"
        )

    source_table = str(selected.get("source_table") or "").strip()
    source_pk = str(selected.get("source_pk") or "").strip()
    source_type = str(selected.get("source") or "SOURCE").strip().upper()
    message_hash = gate._hash(message)

    with e.connect() as c:
        existing = c.execute(text("""
            SELECT * FROM pi_requirement_gate_v1191
            WHERE (source_table=:tb AND source_pk=:pk) OR message_hash=:mh
            ORDER BY CASE WHEN source_table=:tb AND source_pk=:pk THEN 0 ELSE 1 END, id
            LIMIT 1
        """), {"tb": source_table, "pk": source_pk, "mh": message_hash}).mappings().first()
    if existing:
        return dict(existing)

    ex = gate.extract(message)
    if not ex.get("transaction_type"):
        ex["transaction_type"] = _gate_tx(selected.get("transaction"))
    if not ex.get("locations") and selected.get("location"):
        ex["locations"] = [str(selected.get("location")).strip()]
    if not ex.get("contact_numbers") and selected.get("contact"):
        ex["contact_numbers"] = gate._phone_list_from_form(str(selected.get("contact")))
    if not ex.get("intended_use") and selected.get("category"):
        ex["intended_use"] = str(selected.get("category")).strip().upper()
    if not ex.get("property_category") and selected.get("property_type"):
        ex["property_category"] = str(selected.get("property_type")).strip().upper()
    if not ex.get("company_brand_person"):
        ex["company_brand_person"] = str(selected.get("company") or selected.get("contact_name") or "").strip() or None

    evidence_key = f"RESTORED:{source_type}:{source_table}:{source_pk or message_hash[:20]}"
    with e.begin() as c:
        gid = c.execute(text("""
            INSERT INTO pi_requirement_gate_v1191(
                evidence_key,source_type,source_table,source_pk,original_message,message_hash,
                classification,genuine_confidence,rejection_reason,transaction_type,
                property_category,intended_use,locations,alternate_locations,
                area_min_sqft,area_max_sqft,budget_min,budget_max,floor_requirement,
                frontage_requirement,parking_requirement,company_brand_person,
                contact_numbers,extracted_fields,evidence_quality,matcher_eligible,
                created_at,updated_at
            )
            VALUES(
                :ek,:st,:tb,:pk,:msg,:mh,:cls,:conf,:rej,:tx,:pc,:use,
                CAST(:loc AS JSONB),CAST(:alt AS JSONB),:amin,:amax,:bmin,:bmax,
                :floor,:frontage,:parking,:company,CAST(:phones AS JSONB),
                CAST(:fields AS JSONB),:eq,FALSE,NOW(),NOW()
            )
            ON CONFLICT(evidence_key) DO UPDATE SET updated_at=NOW()
            RETURNING id
        """), {
            "ek": evidence_key, "st": source_type, "tb": source_table, "pk": source_pk,
            "msg": message, "mh": message_hash,
            "cls": ex.get("classification") or "NEEDS VERIFICATION",
            "conf": ex.get("genuine_confidence") or 0,
            "rej": ex.get("rejection_reason"),
            "tx": ex.get("transaction_type"),
            "pc": ex.get("property_category"),
            "use": ex.get("intended_use"),
            "loc": json.dumps(ex.get("locations") or []),
            "alt": json.dumps(ex.get("alternate_locations") or []),
            "amin": ex.get("area_min_sqft"), "amax": ex.get("area_max_sqft"),
            "bmin": ex.get("budget_min"), "bmax": ex.get("budget_max"),
            "floor": ex.get("floor_requirement"),
            "frontage": ex.get("frontage_requirement"),
            "parking": ex.get("parking_requirement"),
            "company": ex.get("company_brand_person"),
            "phones": json.dumps(ex.get("contact_numbers") or []),
            "fields": json.dumps(ex, ensure_ascii=False, default=str),
            "eq": ex.get("evidence_quality") or "UNKNOWN",
        }).scalar_one()
        row = c.execute(text("SELECT * FROM pi_requirement_gate_v1191 WHERE id=:id"), {"id": gid}).mappings().first()
    return dict(row)

def _gate_selection(row):
    obj = dict(row or {})
    extracted = _dict(obj.get("extracted_fields"))
    merged = dict(extracted)
    merged.update({k: v for k, v in obj.items() if v not in (None, "", [], {})})
    source_table = str(obj.get("source_table") or "pi_requirement_gate_v1191")
    selected = _normalize_source_row(source_table, merged, int(obj.get("id") or 0))
    selected.update({
        "source": _classify(obj.get("source_type") or source_table),
        "source_table": source_table,
        "source_pk": str(obj.get("source_pk") or obj.get("id") or ""),
        "message": str(obj.get("original_message") or selected.get("message") or ""),
        "contact": _contact(merged) or _json_list_text(obj.get("contact_numbers")),
        "location": _json_list_text(obj.get("locations")) or selected.get("location"),
    })
    return selected


def register(core, served_app=None):
    app = served_app or _app(core)
    e = _engine(core)
    if app is None or e is None:
        raise RuntimeError("Requirement restore requires FastAPI app + SQLAlchemy engine")

    # REQUIREMENT_AUTH_PROBE_V1
    probe_path = "/api/alliance/requirement-auth-probe"
    for route in list(getattr(app.router, "routes", [])):
        if getattr(route, "path", None) == probe_path:
            app.router.routes.remove(route)

    @app.get(probe_path, include_in_schema=False)
    def requirement_auth_probe(req: Request):
        result = {
            "status": "OK",
            "module": __name__,
            "version": VERSION,
            "cookie_present": bool(req.cookies.get("pi_session")),
            "cookie_length": len(req.cookies.get("pi_session") or ""),
            "page_role": None,
            "get_role": None,
            "need_login_role": None,
            "errors": {},
        }

        for label, function_name in (
            ("page_role", "page_role_or_redirect"),
            ("get_role", "get_role"),
            ("need_login_role", "need_login"),
        ):
            function = getattr(core, function_name, None)
            if not callable(function):
                result["errors"][label] = "NOT_CALLABLE"
                continue

            try:
                value = function(req)
                result[label] = (
                    value if isinstance(value, str)
                    else type(value).__name__ if value is not None
                    else None
                )
            except Exception as exc:
                result["errors"][label] = (
                    f"{type(exc).__name__}: {str(exc)[:160]}"
                )

        return result

    for path in ("/alliance/final/requirements", "/alliance/final/requirements/{source}"):
        _remove_get(app, path)

    @app.get("/alliance/final/requirements", response_class=HTMLResponse, include_in_schema=False)
    def requirement_hub(req: Request):
        # ALLIANCE_REQUIREMENT_EXACT_HANDLER_DIAGNOSTIC_V2
        if req.query_params.get("__auth_diagnostic") == "1":
            import importlib
            from fastapi.responses import JSONResponse

            canonical_auth = importlib.import_module("app")
            token = req.cookies.get("pi_session")
            role = canonical_auth.get_role(req)

            return JSONResponse({
                "diagnostic": "ALLIANCE_REQUIREMENT_EXACT_HANDLER_V2",
                "request_path": req.url.path,
                "cookie_header_present": bool(req.headers.get("cookie")),
                "pi_session_present": bool(token),
                "pi_session_length": len(token or ""),
                "canonical_module": canonical_auth.__name__,
                "canonical_role": role,
                "handler_module": __name__,
                "auth_mode": "CANONICAL_APP_AUTH_V25",
                "database_changed": False,
            })
        _login(core, req)
        page, _ = _hub(e)
        return HTMLResponse(page, headers={"Cache-Control":"no-store","X-Alliance-Requirement-Restore":VERSION})

    @app.get("/alliance/final/requirements/add-manual", response_class=HTMLResponse, include_in_schema=False)
    def add_manual_requirement_page(req: Request):
        _login(core, req)
        body = """<div class="notice"><b>Manual Requirement Entry</b><br>
        Saving creates one source-evidence row visible immediately in Manual and Master.
        Smart Matcher remains locked until the requirement is verified.</div>
        <div class="card"><form method="post" action="/alliance/final/requirements/add-manual">
        <label>Date / Time</label><input type="datetime-local" name="created_at"><br><br>
        <label>Original Requirement *</label><input name="message" required placeholder="Exact client requirement"><br><br>
        <label>Client / Company</label><input name="company"><br><br>
        <label>Contact Name</label><input name="contact_name"><br><br>
        <label>Contact No.</label><input name="contact"><br><br>
        <label>Location</label><input name="location"><br><br>
        <label>Category / Use</label><input name="category"><br><br>
        <label>Property Type</label><input name="property_type"><br><br>
        <label>Area</label><input name="area"><br><br>
        <label>Rent / Sale</label><select name="transaction"><option value="">Select</option><option>RENT</option><option>LEASE</option><option>SALE</option><option>PURCHASE</option></select><br><br>
        <label>Budget</label><input name="budget"><br><br>
        <label>Assigned To</label><input name="assigned_to"><br><br>
        <button type="submit">Save to Manual + Master</button>
        </form></div>"""
        return HTMLResponse(_shell("Add Manual Requirement", body), headers={"Cache-Control":"no-store"})

    @app.post("/alliance/final/requirements/add-manual", include_in_schema=False)
    def add_manual_requirement(
        req: Request,
        message: str = Form(...),
        company: str = Form(""),
        contact_name: str = Form(""),
        contact: str = Form(""),
        location: str = Form(""),
        category: str = Form(""),
        property_type: str = Form(""),
        area: str = Form(""),
        transaction: str = Form(""),
        budget: str = Form(""),
        assigned_to: str = Form(""),
        created_at: str = Form(""),
    ):
        _login(core, req)
        clean_message = re.sub(r"\s+", " ", str(message or "")).strip()
        if not clean_message:
            raise HTTPException(400, "Original requirement is required")
        source_pk = hashlib.sha1(
            "|".join((clean_message, str(contact or ""), str(created_at or ""))).encode("utf-8", "ignore")
        ).hexdigest()[:24]
        selected = {
            "source": "MANUAL",
            "source_table": "alliance_manual_requirement_entry",
            "source_pk": source_pk,
            "message": clean_message,
            "company": str(company or "").strip(),
            "contact_name": str(contact_name or "").strip(),
            "contact": str(contact or "").strip(),
            "location": str(location or "").strip(),
            "category": str(category or "").strip(),
            "property_type": str(property_type or "").strip(),
            "area": str(area or "").strip(),
            "transaction": str(transaction or "").strip(),
            "budget": str(budget or "").strip(),
            "assigned_to": str(assigned_to or "").strip(),
            "created_at": str(created_at or "").strip(),
        }
        _ensure_gate_row(e, selected)
        return RedirectResponse("/alliance/final/requirements/manual", status_code=303)

    @app.get("/api/alliance/manual-requirements-source-truth", include_in_schema=False)
    def manual_requirements_source_truth(req: Request):
        _login(core, req)
        with e.connect() as conn:
            count = conn.execute(text("""
                SELECT COUNT(*) FROM pi_operational_requirements
                WHERE UPPER(COALESCE(entry_source,'MANUAL'))='MANUAL'
            """)).scalar()
            sample = conn.execute(text("""
                SELECT id, requirement_code, client_name, contact_number,
                       preferred_locations, transaction_type, verification_status
                FROM pi_operational_requirements
                WHERE UPPER(COALESCE(entry_source,'MANUAL'))='MANUAL'
                ORDER BY id DESC LIMIT 3
            """)).mappings().all()
        return JSONResponse({
            "status":"READY",
            "version":VERSION,
            "authority":"pi_operational_requirements",
            "manual_count":int(count or 0),
            "sample":[dict(x) for x in sample],
            "database_writes":False,
        })

    @app.get("/alliance/final/requirements/{source}", response_class=HTMLResponse, include_in_schema=False)
    def requirement_db(
        req: Request,
        source: str,
        q: str = Query(""),
        location: str = Query(""),
        transaction: str = Query(""),
        status: str = Query(""),
        assigned: str = Query(""),
        limit: int = Query(250, ge=1, le=1000),
        page: int = Query(1, ge=1, le=10000),
    ):
        _login(core, req)
        src = source.upper()
        if src not in SOURCES:
            return HTMLResponse("Unknown requirement database", status_code=404)
        # ASTRA_LIVE_MANUAL_TRUTH_PROBE_V1
        # Read-only diagnostic on the exact authenticated live handler. This proves
        # which runtime, engine and table result the user's browser is actually using.
        if src == "MANUAL" and req.query_params.get("__astra_truth") == "1":
            from fastapi.responses import JSONResponse
            from fastapi.encoders import jsonable_encoder
            result = {
                "diagnostic": "ASTRA_LIVE_MANUAL_TRUTH_PROBE_V1",
                "version": VERSION,
                "source": "pi_operational_requirements",
                "database_changed": False,
            }
            try:
                with e.connect() as conn:
                    result["total_rows"] = int(conn.execute(text("SELECT COUNT(*) FROM pi_operational_requirements")).scalar() or 0)
                    result["manual_rows"] = int(conn.execute(text("""
                        SELECT COUNT(*) FROM pi_operational_requirements
                        WHERE UPPER(COALESCE(entry_source,'MANUAL'))='MANUAL'
                    """)).scalar() or 0)
                    result["latest"] = [dict(r) for r in conn.execute(text("""
                        SELECT id, entry_source, requirement_code, client_name, company_name,
                               contact_number, city, transaction_type, created_at
                        FROM pi_operational_requirements
                        ORDER BY id DESC LIMIT 3
                    """)).mappings().all()]
                result["mapped_rows"] = len(_manual_operational_rows(e, 1000))
                result["status"] = "OK"
            except Exception as exc:
                result["status"] = "ERROR"
                result["error"] = f"{type(exc).__name__}: {exc}"
            return JSONResponse(jsonable_encoder(result), headers={"Cache-Control":"no-store","X-Alliance-Requirement-Restore":VERSION})
        # Keep the first page intentionally small. Requirement pages are operational
        # workspaces, not full-table exports. Search narrows before rendering.
        page_rows = _table(e, src, q, location, transaction, status, assigned, limit)
        return HTMLResponse(
            page_rows,
            headers={"Cache-Control":"no-store","X-Alliance-Requirement-Restore":VERSION},
        )


    @app.get("/alliance/final/requirements/run-match", response_class=HTMLResponse, include_in_schema=False)
    def source_run_match(
        req: Request,
        source: str = Query(""),
        source_pk: str = Query(""),
        gate_id: int = Query(0, ge=0),
    ):
        _login(core, req)
        if gate_id:
            with e.connect() as c:
                direct = c.execute(
                    text("SELECT * FROM pi_requirement_gate_v1191 WHERE id=:id"),
                    {"id": gate_id},
                ).mappings().first()
            if not direct:
                return HTMLResponse(
                    _shell("Run Match", '<div class="notice"><b>Requirement not found.</b></div>'),
                    status_code=404,
                )
            gate_row = dict(direct)
            selected = _gate_selection(gate_row)
            src = str(selected.get("source") or "OTHER").upper()
            if src not in SOURCES or src == "MASTER":
                src = "SOCIAL"
        else:
            src = source.upper().strip()
            if src not in SOURCES or src == "MASTER":
                return HTMLResponse(_shell("Run Match", '<div class="notice">Invalid source requirement.</div>'), status_code=400)
            if src == "MANUAL":
                rows = _manual_operational_rows(e, 1000)
            else:
                rows, _ = _source_rows(e, src)
            selected = next((row for row in rows if str(row.get("source_pk") or "") == str(source_pk or "")), None)
            if not selected:
                return HTMLResponse(_shell("Run Match", '<div class="notice"><b>Requirement not found.</b></div>'), status_code=404)
            gate_row = _ensure_gate_row(e, selected)
        gid = int(gate_row["id"])
        locations = _json_list_text(gate_row.get("locations"))
        phones = _json_list_text(gate_row.get("contact_numbers"))
        tx = str(gate_row.get("transaction_type") or "")

        body = (
            '<div class="notice"><b>Review this exact requirement before matching.</b><br>'
            'Nothing is promoted until you press <b>Verify & Run Match</b>. '
            'The existing Requirement Gate and Master Bridge will deduplicate, preserve source evidence, '
            'mark the canonical requirement VERIFIED and then open Smart Matcher.</div>'
            '<div class="card">'
            f'<p><b>Source:</b> {_e(selected.get("source_table"))} Â· ID {_e(selected.get("source_pk"))}</p>'
            f'<p><b>Original Requirement:</b><br>{_e(selected.get("message"))}</p>'
            '<form method="post" action="/alliance/final/requirements/verify-and-match">'
            f'<input type="hidden" name="gate_id" value="{gid}">'
            f'<input type="hidden" name="return_source" value="{_e(src)}">'
            '<label>Transaction</label><select name="transaction_type" required>'
            '<option value="">Select</option>'
            f'<option value="LEASE" {"selected" if tx=="LEASE" else ""}>LEASE / RENT</option>'
            f'<option value="PURCHASE" {"selected" if tx=="PURCHASE" else ""}>PURCHASE / SALE</option>'
            '</select><br><br>'
            f'<label>Location</label><input name="location" value="{_e(locations)}" required><br><br>'
            f'<label>Contact number(s)</label><input name="contact_numbers" value="{_e(phones)}" required><br><br>'
            f'<label>Use / Category</label><input name="intended_use" value="{_e(gate_row.get("intended_use"))}"><br><br>'
            f'<label>Area Min Sqft</label><input name="area_min_sqft" value="{_e(gate_row.get("area_min_sqft"))}">'
            f'<label>Area Max Sqft</label><input name="area_max_sqft" value="{_e(gate_row.get("area_max_sqft"))}"><br><br>'
            f'<label>Budget Max â‚¹</label><input name="budget_max" value="{_e(gate_row.get("budget_max"))}"><br><br>'
            f'<label>Notes</label><input name="notes" value="{_e(gate_row.get("verification_notes"))}"><br><br>'
            '<button type="submit">Verify & Run Match</button></form><br>'
            '<form method="post" action="/alliance/final/requirements/reject-source">'
            f'<input type="hidden" name="gate_id" value="{gid}">'
            f'<input type="hidden" name="return_source" value="{_e(src)}">'
            '<button type="submit">Reject</button></form></div>'
        )
        return HTMLResponse(_shell("Verify & Run Match", body), headers={"Cache-Control":"no-store"})

    @app.post("/alliance/final/requirements/verify-and-match", include_in_schema=False)
    def verify_and_match(
        req: Request,
        gate_id: int = Form(...),
        return_source: str = Form("WHATSAPP"),
        transaction_type: str = Form(...),
        location: str = Form(...),
        contact_numbers: str = Form(...),
        intended_use: str = Form(""),
        area_min_sqft: str = Form(""),
        area_max_sqft: str = Form(""),
        budget_max: str = Form(""),
        notes: str = Form(""),
    ):
        _login(core, req)
        import alliance_requirement_gate_v1191 as gate
        import alliance_requirement_master_bridge_v1199 as bridge

        actor_fn = getattr(core, "actor_name", None)
        actor = actor_fn(req) if actor_fn else "team"

        tx = str(transaction_type or "").strip().upper()
        if tx not in {"LEASE", "PURCHASE"}:
            raise HTTPException(400, "Transaction must be LEASE or PURCHASE")

        locations = [x.strip() for x in re.split(r"[,;/\n]+", str(location or "")) if x.strip()]
        phones = gate._phone_list_from_form(contact_numbers)
        if not locations:
            raise HTTPException(400, "At least one verified location is required")
        if not phones:
            raise HTTPException(400, "At least one verified contact number is required")

        def num(v):
            try:
                return float(str(v).replace(",", "").strip()) if str(v or "").strip() else None
            except Exception:
                return None

        amin, amax = num(area_min_sqft), num(area_max_sqft)
        if amin is not None and amax is not None and amin > amax:
            amin, amax = amax, amin
        bmax = num(budget_max)

        with e.begin() as c:
            row = c.execute(text("SELECT id FROM pi_requirement_gate_v1191 WHERE id=:id FOR UPDATE"), {"id": gate_id}).mappings().first()
            if not row:
                raise HTTPException(404, "Requirement Gate row not found")

            c.execute(text("""
                UPDATE pi_requirement_gate_v1191
                SET classification='VERIFIED ACTIVE',
                    matcher_eligible=TRUE,
                    transaction_type=:tx,
                    locations=CAST(:loc AS JSONB),
                    contact_numbers=CAST(:phones AS JSONB),
                    intended_use=:use,
                    area_min_sqft=:amin,
                    area_max_sqft=:amax,
                    budget_max=:bmax,
                    verification_notes=:notes,
                    verified_by=:actor,
                    verified_at=NOW(),
                    updated_at=NOW()
                WHERE id=:id
            """), {
                "id": gate_id, "tx": tx, "loc": json.dumps(locations), "phones": json.dumps(phones),
                "use": str(intended_use or "").strip().upper() or None,
                "amin": amin, "amax": amax, "bmax": bmax,
                "notes": str(notes or "").strip() or None, "actor": actor,
            })
            result = bridge.sync(c, gate_id, actor, "VERIFIED ACTIVE")

        cid = str(result.get("canonical_id") or "")
        if not cid:
            raise HTTPException(500, "No canonical requirement ID returned")
        return RedirectResponse(f"/alliance/primary/matcher?requirement_id={cid}", status_code=303)

    @app.post("/alliance/final/requirements/reject-source", include_in_schema=False)
    def reject_source(req: Request, gate_id: int = Form(...), return_source: str = Form("WHATSAPP")):
        _login(core, req)
        import alliance_requirement_master_bridge_v1199 as bridge
        actor_fn = getattr(core, "actor_name", None)
        actor = actor_fn(req) if actor_fn else "team"
        with e.begin() as c:
            row = c.execute(text("SELECT classification FROM pi_requirement_gate_v1191 WHERE id=:id FOR UPDATE"), {"id": gate_id}).mappings().first()
            if not row:
                raise HTTPException(404, "Requirement Gate row not found")
            c.execute(text("""
                UPDATE pi_requirement_gate_v1191
                SET classification='REJECTED/EXPIRED',matcher_eligible=FALSE,
                    verified_by=NULL,verified_at=NULL,updated_at=NOW()
                WHERE id=:id
            """), {"id": gate_id})
            bridge.sync(c, gate_id, actor, "REJECTED/EXPIRED")
        src = str(return_source or "WHATSAPP").lower()
        return RedirectResponse(f"/alliance/final/requirements/{src}", status_code=303)


    @app.get("/api/alliance/requirement-restore/status", include_in_schema=False)
    def restore_status(req: Request):
        _login(core, req)
        found = _discover_tables(e)
        counts = {}
        for src in SOURCES:
            if src == "MANUAL":
                rows = _manual_operational_rows(e, 1000)
                meta = {
                    "master": 0,
                    "source_only": len(rows),
                    "tables": ["pi_operational_requirements"],
                }
            else:
                rows, meta = _combined(e, src)
            counts[src] = {
                "visible": len(rows),
                "master_linked": meta["master"],
                "source_only": meta["source_only"],
            }
        return {
            "status":"OK",
            "version":VERSION,
            "source_tables_found":found,
            "counts":counts,
            "matcher_master_only":True,
        }

    _move_front(app, "/alliance/final/requirements")
    _move_front(app, "/alliance/final/requirements/{source}")
    _move_front(app, "/alliance/final/requirements/run-match")
    _move_front(app, "/api/alliance/requirement-restore/status")

    return {
        "status":"REGISTERED",
        "version":VERSION,
        "hub":"/alliance/final/requirements",
        "status_api":"/api/alliance/requirement-restore/status",
        "matcher_master_only":True,
        "master_mutation":False,
    }

# ALLIANCE_APP_RECTIFIER_V402
