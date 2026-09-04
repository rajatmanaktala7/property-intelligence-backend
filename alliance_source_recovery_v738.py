from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

import alliance_magazine_section_context_v680 as section_v680

VERSION = "7.3.8-ALLIANCE-SOURCE-RECOVERY-REEXTRACTION"
MODE = "AUDIT_FIRST_SOURCE_STORAGE_DISCOVERY_NO_MASTER_MUTATION"

DDL = [
    """CREATE TABLE IF NOT EXISTS pi_source_recovery_runs_v738(
        run_id BIGSERIAL PRIMARY KEY,
        mode TEXT NOT NULL,
        status TEXT NOT NULL,
        scanned_links INTEGER NOT NULL DEFAULT 0,
        source_tables INTEGER NOT NULL DEFAULT 0,
        resolved_rows INTEGER NOT NULL DEFAULT 0,
        raw_text_found INTEGER NOT NULL DEFAULT 0,
        image_refs_found INTEGER NOT NULL DEFAULT 0,
        recoverable INTEGER NOT NULL DEFAULT 0,
        needs_image_reprocess INTEGER NOT NULL DEFAULT 0,
        missing INTEGER NOT NULL DEFAULT 0,
        failed INTEGER NOT NULL DEFAULT 0,
        result JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS pi_source_recovery_candidates_v738(
        id BIGSERIAL PRIMARY KEY,
        run_id BIGINT NOT NULL,
        canonical_id TEXT NOT NULL,
        source_type TEXT,
        source_table TEXT,
        source_pk TEXT,
        source_row_hash TEXT,
        status TEXT NOT NULL,
        source_lookup TEXT,
        discovered_key TEXT,
        original_text TEXT,
        section_heading TEXT,
        locality TEXT,
        image_ref TEXT,
        recovered_record JSONB NOT NULL DEFAULT '{}'::jsonb,
        evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
        reason TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(run_id,canonical_id,source_table,source_pk,source_row_hash)
    )""",
    """CREATE TABLE IF NOT EXISTS pi_source_storage_inventory_v738(
        id BIGSERIAL PRIMARY KEY,
        run_id BIGINT NOT NULL,
        source_table TEXT NOT NULL,
        column_name TEXT NOT NULL,
        data_type TEXT,
        evidence_class TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(run_id,source_table,column_name)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_v738_candidates_run_status
       ON pi_source_recovery_candidates_v738(run_id,status)""",
    """CREATE INDEX IF NOT EXISTS idx_v738_candidates_canonical
       ON pi_source_recovery_candidates_v738(canonical_id,created_at DESC)""",
]

SOURCE_FILTER = ("MAGAZINE", "NEWSPAPER")

TEXT_KEY_HINTS = (
    "original_description", "original_message", "raw_line", "raw_text",
    "description", "message", "ad_text", "listing_text", "content",
    "body", "text", "ocr_text", "extracted_text", "property_text",
    "source_text", "raw_content"
)
SECTION_KEY_HINTS = (
    "section_heading", "category_heading", "transaction_heading",
    "page_section", "section", "heading", "category", "transaction_type"
)
LOCALITY_KEY_HINTS = ("locality", "location", "locality_clean", "area_name")
IMAGE_KEY_HINTS = (
    "image_url", "image_path", "source_image", "page_image", "image",
    "media_url", "file_path", "attachment_url", "attachment_path",
    "upload_path", "photo_url", "photo_path", "scan_url", "scan_path",
    "page_url", "page_path", "filename", "file_name"
)
ID_KEY_HINTS = (
    "id", "record_id", "property_id", "source_id", "pk", "row_id",
    "master_property_id", "canonical_id", "listing_id", "ad_id",
    "item_id", "entry_id"
)
PAGE_KEY_HINTS = (
    "page", "page_no", "page_number", "publication", "publication_name",
    "magazine", "newspaper", "issue", "issue_date", "batch_id", "upload_id",
    "document_id", "file_id"
)


def _engine(core):
    return getattr(core, "engine", None)


def _app(core):
    return getattr(core, "app", None) or core


def _route_exists(app, path):
    return any(getattr(r, "path", None) == path for r in getattr(app, "routes", []))


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


def _json(v):
    if isinstance(v, dict):
        return dict(v)
    if isinstance(v, str):
        try:
            x = json.loads(v)
            return x if isinstance(x, dict) else {}
        except Exception:
            return {}
    return {}


def _qident(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(name or "")):
        raise ValueError("Unsafe SQL identifier")
    return '"' + str(name) + '"'


def _table_exists(engine, table_name: str) -> bool:
    with engine.connect() as c:
        return bool(c.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": table_name}).scalar())


def _columns(engine, table_name: str) -> List[Dict[str, str]]:
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT column_name,data_type
            FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name=:t
            ORDER BY ordinal_position
        """), {"t": table_name}).mappings().all()
    return [{"column_name": str(x["column_name"]), "data_type": str(x["data_type"])} for x in rows]


def _ensure(engine):
    if engine is None:
        raise RuntimeError("Database engine not available")
    for t in ("pi_master_properties_v711", "pi_master_source_links_v711"):
        if not _table_exists(engine, t):
            raise RuntimeError("Required master table missing: " + t)
    with engine.begin() as c:
        for ddl in DDL:
            c.execute(text(ddl))


def _walk(obj: Any, key_hints: Iterable[str], path: str = "") -> List[Tuple[str, str]]:
    hints = {str(x).lower() for x in key_hints}
    found: List[Tuple[str, str]] = []

    def rec(x, p):
        if isinstance(x, dict):
            for k, v in x.items():
                kp = f"{p}.{k}" if p else str(k)
                kl = str(k).lower()
                if kl in hints and v not in (None, "", [], {}):
                    if isinstance(v, (dict, list)):
                        found.append((kp, json.dumps(_safe(v), ensure_ascii=False)))
                    else:
                        found.append((kp, str(v)))
                rec(v, kp)
        elif isinstance(x, list):
            for i, v in enumerate(x):
                rec(v, f"{p}[{i}]")
    rec(obj, path)
    return found


def _looks_like_property_text(s: str) -> bool:
    u = str(s or "").upper()
    score = 0
    if re.search(r"\b(?:SHOP\s*NO[-\s]*\d+|[A-Z]{0,3}-?\d+[A-Z]?)\b", u):
        score += 2
    if re.search(r"\b\d{2,7}\s*(?:SQ\.?\s*FT|SQFT|SFT|FT)\b", u):
        score += 2
    if re.search(r"\b(?:GF|FF|SF|TF|BMT|UGF|MEZZ|TERR)\b", u):
        score += 1
    if re.search(r"\b(?:RENT|LEASE|SALE|COMMERCIAL|RESIDENTIAL)\b", u):
        score += 1
    if re.search(r"(?:\+?91[-\s]?)?[6-9]\d{9}|0\d{10}", re.sub(r"[\s-]", "", u)):
        score += 1
    return score >= 2


def _best_text(*objects: Any) -> Tuple[str, str]:
    vals: List[Tuple[str, str]] = []
    for obj in objects:
        vals.extend(_walk(obj, TEXT_KEY_HINTS))
    cleaned = []
    for k, v in vals:
        s = re.sub(r"\s+", " ", str(v)).strip()
        if s:
            cleaned.append((k, s))
    if not cleaned:
        return "", ""
    cleaned.sort(key=lambda kv: (_looks_like_property_text(kv[1]), len(kv[1])), reverse=True)
    return cleaned[0]


def _best_value(objects: Iterable[Any], keys: Iterable[str]) -> Tuple[str, str]:
    vals: List[Tuple[str, str]] = []
    for obj in objects:
        vals.extend(_walk(obj, keys))
    for k, v in vals:
        s = re.sub(r"\s+", " ", str(v)).strip()
        if s:
            return k, s
    return "", ""


def _source_links(engine, limit: int) -> List[Dict[str, Any]]:
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT id,canonical_id,source_type,source_table,source_pk,source_row_hash,created_at
            FROM pi_master_source_links_v711
            WHERE master_entity_type='PROPERTY'
              AND UPPER(COALESCE(source_type,'')) IN ('MAGAZINE','NEWSPAPER')
            ORDER BY created_at,id
            LIMIT :n
        """), {"n": max(1, min(int(limit), 20000))}).mappings().all()
    return [_safe(dict(x)) for x in rows]


def _master(engine, cid: str) -> Dict[str, Any]:
    with engine.connect() as c:
        row = c.execute(text(
            "SELECT * FROM pi_master_properties_v711 WHERE canonical_id=:c LIMIT 1"
        ), {"c": cid}).mappings().first()
    return _safe(dict(row)) if row else {}


def _candidate_key_columns(cols: List[Dict[str, str]]) -> List[str]:
    names = [x["column_name"] for x in cols]
    exact = [k for k in ID_KEY_HINTS if k in names]
    fuzzy = [n for n in names if n.lower().endswith("_id") and n not in exact]
    return exact + fuzzy


def _fetch_source_row(engine, table_name: str, source_pk: str) -> Tuple[Dict[str, Any], str, str]:
    if not table_name:
        return {}, "SOURCE_TABLE_EMPTY", ""
    if not _table_exists(engine, table_name):
        return {}, "SOURCE_TABLE_MISSING", ""
    cols = _columns(engine, table_name)
    for col in _candidate_key_columns(cols):
        try:
            sql = f"""SELECT to_jsonb(t) FROM {_qident(table_name)} t
                      WHERE CAST({_qident(col)} AS TEXT)=:pk LIMIT 1"""
            with engine.connect() as c:
                row = c.execute(text(sql), {"pk": str(source_pk)}).scalar()
            if row:
                return _json(row) if not isinstance(row, dict) else dict(row), "FOUND_BY_SOURCE_PK", col
        except Exception:
            continue
    # Last safe recovery path: source_pk may have been an ordinal/ctid-like historical pointer.
    # We deliberately do not guess a row by position because that can attach the wrong evidence.
    return {}, "SOURCE_ROW_NOT_RESOLVED", ""


def _classify_column(name: str, dtype: str) -> str:
    n = name.lower()
    if n in {x.lower() for x in TEXT_KEY_HINTS}:
        return "TEXT_OR_JSON_EVIDENCE"
    if n in {x.lower() for x in IMAGE_KEY_HINTS}:
        return "IMAGE_OR_FILE_REFERENCE"
    if n in {x.lower() for x in SECTION_KEY_HINTS}:
        return "SECTION_CONTEXT"
    if n in {x.lower() for x in PAGE_KEY_HINTS}:
        return "PAGE_OR_DOCUMENT_CONTEXT"
    if n in {x.lower() for x in ID_KEY_HINTS} or n.endswith("_id"):
        return "IDENTIFIER"
    if dtype in ("json", "jsonb"):
        return "JSON_CONTAINER"
    return "OTHER"


def _inventory_tables(engine, run_id: int, links: List[Dict[str, Any]]) -> Dict[str, Any]:
    tables = sorted({str(x.get("source_table") or "").strip() for x in links if str(x.get("source_table") or "").strip()})
    result = {}
    with engine.begin() as c:
        for t in tables:
            if not _table_exists(engine, t):
                result[t] = {"exists": False, "columns": []}
                continue
            cols = _columns(engine, t)
            result[t] = {"exists": True, "columns": cols}
            for col in cols:
                klass = _classify_column(col["column_name"], col["data_type"])
                c.execute(text("""
                    INSERT INTO pi_source_storage_inventory_v738
                    (run_id,source_table,column_name,data_type,evidence_class)
                    VALUES (:r,:t,:c,:d,:e)
                    ON CONFLICT DO NOTHING
                """), {"r": run_id, "t": t, "c": col["column_name"], "d": col["data_type"], "e": klass})
    return result


def _recover_one(engine, link: Dict[str, Any]) -> Dict[str, Any]:
    cid = str(link.get("canonical_id") or "")
    master = _master(engine, cid)
    if not master:
        return {
            "canonical_id": cid, "status": "FAILED",
            "reason": "MASTER_RECORD_MISSING", "evidence": {}
        }

    source, lookup, lookup_key = _fetch_source_row(
        engine, str(link.get("source_table") or ""), str(link.get("source_pk") or "")
    )
    clean = _json(master.get("clean_record"))

    text_key, raw = _best_text(source, clean)
    section_key, section_heading = _best_value((source, clean), SECTION_KEY_HINTS)
    locality_key, locality = _best_value((source, clean), LOCALITY_KEY_HINTS)
    image_key, image_ref = _best_value((source, clean), IMAGE_KEY_HINTS)
    page_context = dict(_walk(source, PAGE_KEY_HINTS))

    if not locality:
        locality = str(master.get("locality") or "").strip()

    evidence = {
        "source_lookup": lookup,
        "lookup_key": lookup_key,
        "discovered_text_key": text_key,
        "discovered_section_key": section_key,
        "discovered_locality_key": locality_key,
        "discovered_image_key": image_key,
        "page_context": page_context,
        "source_row_present": bool(source),
        "source_row_keys": sorted(list(source.keys()))[:100] if source else [],
        "original_text": raw,
        "section_heading": section_heading,
        "image_ref": image_ref,
    }

    if raw:
        recovered = section_v680.enrich_record(
            {
                "ref": "",
                "section_heading": section_heading,
                "raw_line": raw,
                "original_description": raw,
                "property_category": clean.get("property_category") or clean.get("category") or "",
                "transaction_type": master.get("transaction_type") or clean.get("transaction_type") or "",
                "address": clean.get("address") or "",
                "locality": locality,
                "city": master.get("city") or clean.get("city") or "",
                "area_raw": clean.get("area_raw") or "",
                "area_sqft": master.get("area_sqft"),
                "floor_codes": clean.get("floor_codes") or "",
                "floors": clean.get("floors") or [],
                "contact_name": clean.get("contact_name") or clean.get("name") or "",
                "phones": master.get("phones") or clean.get("phones") or [],
            },
            inherited_locality=locality,
            inherited_section_heading=section_heading,
        )
        gate = str(recovered.get("quality_gate") or recovered.get("quality") or "")
        if recovered.get("needs_review"):
            status = "RECOVERED_NEEDS_REVIEW"
            reason = recovered.get("review_reason") or "Recovered text requires review"
        else:
            status = "RECOVERABLE_TEXT"
            reason = "Original text recovered and restructured with 7.3.6"
        return {
            "canonical_id": cid, "status": status, "reason": reason,
            "source_lookup": lookup, "discovered_key": text_key,
            "original_text": raw, "section_heading": section_heading,
            "locality": locality, "image_ref": image_ref,
            "recovered_record": recovered, "evidence": evidence,
        }

    if image_ref:
        return {
            "canonical_id": cid,
            "status": "IMAGE_REFERENCE_FOUND",
            "reason": "Source image/file reference recovered. Vision re-extraction must be explicitly run against the original page.",
            "source_lookup": lookup, "discovered_key": image_key,
            "original_text": "", "section_heading": section_heading,
            "locality": locality, "image_ref": image_ref,
            "recovered_record": {}, "evidence": evidence,
        }

    if source:
        return {
            "canonical_id": cid,
            "status": "SOURCE_ROW_FOUND_NO_EVIDENCE",
            "reason": "Historical source row resolved but no safe original text or image/file reference was found.",
            "source_lookup": lookup, "discovered_key": "",
            "original_text": "", "section_heading": section_heading,
            "locality": locality, "image_ref": "",
            "recovered_record": {}, "evidence": evidence,
        }

    return {
        "canonical_id": cid,
        "status": "SOURCE_MISSING",
        "reason": "Historical source row could not be resolved from source_table/source_pk.",
        "source_lookup": lookup, "discovered_key": "",
        "original_text": "", "section_heading": section_heading,
        "locality": locality, "image_ref": "",
        "recovered_record": {}, "evidence": evidence,
    }


def run_audit(engine, limit: int = 5000) -> Dict[str, Any]:
    _ensure(engine)
    links = _source_links(engine, limit)
    with engine.begin() as c:
        run_id = c.execute(text("""
            INSERT INTO pi_source_recovery_runs_v738(mode,status)
            VALUES (:m,'RUNNING') RETURNING run_id
        """), {"m": MODE}).scalar_one()

    inventory = _inventory_tables(engine, int(run_id), links)

    counts = {
        "scanned_links": 0, "source_tables": len(inventory), "resolved_rows": 0,
        "raw_text_found": 0, "image_refs_found": 0, "recoverable": 0,
        "needs_image_reprocess": 0, "missing": 0, "failed": 0,
    }

    with engine.begin() as c:
        for link in links:
            counts["scanned_links"] += 1
            try:
                rec = _recover_one(engine, link)
                status = rec["status"]
                if rec.get("evidence", {}).get("source_row_present"):
                    counts["resolved_rows"] += 1
                if rec.get("original_text"):
                    counts["raw_text_found"] += 1
                if rec.get("image_ref"):
                    counts["image_refs_found"] += 1
                if status in ("RECOVERABLE_TEXT", "RECOVERED_NEEDS_REVIEW"):
                    counts["recoverable"] += 1
                elif status == "IMAGE_REFERENCE_FOUND":
                    counts["needs_image_reprocess"] += 1
                elif status in ("SOURCE_MISSING", "SOURCE_ROW_FOUND_NO_EVIDENCE"):
                    counts["missing"] += 1
                elif status == "FAILED":
                    counts["failed"] += 1

                c.execute(text("""
                    INSERT INTO pi_source_recovery_candidates_v738
                    (run_id,canonical_id,source_type,source_table,source_pk,source_row_hash,
                     status,source_lookup,discovered_key,original_text,section_heading,
                     locality,image_ref,recovered_record,evidence,reason)
                    VALUES
                    (:run_id,:canonical_id,:source_type,:source_table,:source_pk,:source_row_hash,
                     :status,:source_lookup,:discovered_key,:original_text,:section_heading,
                     :locality,:image_ref,CAST(:recovered_record AS JSONB),CAST(:evidence AS JSONB),:reason)
                    ON CONFLICT DO NOTHING
                """), {
                    "run_id": run_id, "canonical_id": rec.get("canonical_id") or "",
                    "source_type": link.get("source_type"), "source_table": link.get("source_table"),
                    "source_pk": link.get("source_pk"), "source_row_hash": link.get("source_row_hash"),
                    "status": status, "source_lookup": rec.get("source_lookup"),
                    "discovered_key": rec.get("discovered_key"), "original_text": rec.get("original_text"),
                    "section_heading": rec.get("section_heading"), "locality": rec.get("locality"),
                    "image_ref": rec.get("image_ref"),
                    "recovered_record": json.dumps(_safe(rec.get("recovered_record") or {}), ensure_ascii=False),
                    "evidence": json.dumps(_safe(rec.get("evidence") or {}), ensure_ascii=False),
                    "reason": rec.get("reason"),
                })
            except Exception as exc:
                counts["failed"] += 1
                c.execute(text("""
                    INSERT INTO pi_source_recovery_candidates_v738
                    (run_id,canonical_id,source_type,source_table,source_pk,source_row_hash,status,reason)
                    VALUES (:r,:cid,:st,:tbl,:pk,:h,'FAILED',:why)
                    ON CONFLICT DO NOTHING
                """), {
                    "r": run_id, "cid": str(link.get("canonical_id") or ""),
                    "st": link.get("source_type"), "tbl": link.get("source_table"),
                    "pk": link.get("source_pk"), "h": link.get("source_row_hash"),
                    "why": f"{type(exc).__name__}: {exc}"[:2000],
                })

        c.execute(text("""
            UPDATE pi_source_recovery_runs_v738
            SET status='COMPLETE',
                scanned_links=:scanned_links,source_tables=:source_tables,
                resolved_rows=:resolved_rows,raw_text_found=:raw_text_found,
                image_refs_found=:image_refs_found,recoverable=:recoverable,
                needs_image_reprocess=:needs_image_reprocess,missing=:missing,failed=:failed,
                result=CAST(:result AS JSONB)
            WHERE run_id=:run_id
        """), {**counts, "run_id": run_id,
               "result": json.dumps({"inventory": inventory, "counts": counts}, ensure_ascii=False)})

    return {"run_id": int(run_id), "status": "COMPLETE", **counts, "inventory": inventory}


def latest(engine) -> Dict[str, Any]:
    _ensure(engine)
    with engine.connect() as c:
        run = c.execute(text("""
            SELECT * FROM pi_source_recovery_runs_v738 ORDER BY run_id DESC LIMIT 1
        """)).mappings().first()
        if not run:
            return {"run": None, "rows": [], "inventory": []}
        rows = c.execute(text("""
            SELECT canonical_id,source_type,source_table,source_pk,status,source_lookup,
                   discovered_key,original_text,section_heading,locality,image_ref,reason
            FROM pi_source_recovery_candidates_v738
            WHERE run_id=:r
            ORDER BY
              CASE status
                WHEN 'RECOVERABLE_TEXT' THEN 1
                WHEN 'RECOVERED_NEEDS_REVIEW' THEN 2
                WHEN 'IMAGE_REFERENCE_FOUND' THEN 3
                WHEN 'SOURCE_ROW_FOUND_NO_EVIDENCE' THEN 4
                WHEN 'SOURCE_MISSING' THEN 5
                ELSE 6
              END,id
            LIMIT 250
        """), {"r": run["run_id"]}).mappings().all()
        inv = c.execute(text("""
            SELECT source_table,column_name,data_type,evidence_class
            FROM pi_source_storage_inventory_v738
            WHERE run_id=:r
            ORDER BY source_table,column_name
        """), {"r": run["run_id"]}).mappings().all()
    return {
        "run": _safe(dict(run)),
        "rows": [_safe(dict(x)) for x in rows],
        "inventory": [_safe(dict(x)) for x in inv],
    }


def _role(core, req):
    fn = getattr(core, "need_login", None)
    return fn(req) if fn else "team"


def _card(label, value):
    return f"<div class='card'><div class='n'>{html.escape(str(value))}</div><div class='l'>{html.escape(label)}</div></div>"


def _short(s, n=220):
    x = str(s or "")
    return x if len(x) <= n else x[:n] + "…"


def dashboard(core, request: Request, message: str = ""):
    _role(core, request)
    engine = _engine(core)
    data = latest(engine)
    run = data["run"]
    rows = data["rows"]
    inv = data["inventory"]

    if run:
        cards = "".join([
            _card("Scanned Source Links", run["scanned_links"]),
            _card("Source Tables", run["source_tables"]),
            _card("Resolved Source Rows", run["resolved_rows"]),
            _card("Raw Text Found", run["raw_text_found"]),
            _card("Image Refs Found", run["image_refs_found"]),
            _card("Recoverable", run["recoverable"]),
            _card("Needs Image Reprocess", run["needs_image_reprocess"]),
            _card("Missing", run["missing"]),
            _card("Failed", run["failed"]),
        ])
        latest_text = f"Run {run['run_id']} · {run['status']}"
    else:
        cards = "".join(_card(x, 0) for x in [
            "Scanned Source Links","Source Tables","Resolved Source Rows","Raw Text Found",
            "Image Refs Found","Recoverable","Needs Image Reprocess","Missing","Failed"
        ])
        latest_text = "None"

    row_html = ""
    for r in rows:
        status = html.escape(str(r.get("status") or ""))
        row_html += f"""<tr>
        <td>{html.escape(str(r.get('canonical_id') or ''))}</td>
        <td>{html.escape(str(r.get('source_type') or ''))}</td>
        <td>{html.escape(str(r.get('source_table') or ''))}<br><small>{html.escape(str(r.get('source_pk') or ''))}</small></td>
        <td><b>{status}</b><br><small>{html.escape(str(r.get('source_lookup') or ''))}</small></td>
        <td>{html.escape(str(r.get('discovered_key') or ''))}</td>
        <td>{html.escape(_short(r.get('section_heading'),120))}</td>
        <td>{html.escape(_short(r.get('original_text'),260))}</td>
        <td>{html.escape(_short(r.get('image_ref'),220))}</td>
        <td>{html.escape(_short(r.get('reason'),220))}</td>
        </tr>"""
    if not row_html:
        row_html = "<tr><td colspan='9'>No source recovery audit yet.</td></tr>"

    inv_html = ""
    for x in inv[:300]:
        inv_html += f"<tr><td>{html.escape(str(x['source_table']))}</td><td>{html.escape(str(x['column_name']))}</td><td>{html.escape(str(x['data_type']))}</td><td>{html.escape(str(x['evidence_class']))}</td></tr>"
    if not inv_html:
        inv_html = "<tr><td colspan='4'>No storage inventory yet.</td></tr>"

    msg = f"<div class='msg'>{html.escape(message)}</div>" if message else ""
    return HTMLResponse(f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alliance 7.3.8 Source Recovery</title>
<style>
body{{font-family:Arial,sans-serif;background:#f3eee6;color:#25211c;margin:0}}
main{{max-width:1500px;margin:24px auto;padding:0 18px}}
a{{color:#5b3d1f}} .top{{font-size:13px;margin-bottom:18px}}
h1{{margin-bottom:6px}} .safety{{background:#fff7da;border:1px solid #e0c66f;padding:14px;border-radius:10px}}
.grid{{display:grid;grid-template-columns:repeat(5,minmax(150px,1fr));gap:10px;margin:18px 0}}
.card{{background:white;border-radius:12px;padding:15px;box-shadow:0 1px 3px #0001}}
.n{{font-size:28px;font-weight:700}} .l{{font-size:12px;color:#655}}
.panel{{background:white;border-radius:12px;padding:16px;margin:14px 0;overflow:auto}}
table{{border-collapse:collapse;width:100%;font-size:12px}} th,td{{border-bottom:1px solid #ddd;padding:8px;text-align:left;vertical-align:top}}
button{{padding:10px 16px;border:0;border-radius:8px;background:#33291f;color:white;cursor:pointer}}
input{{padding:9px;width:120px}} .msg{{background:#e9f7e9;padding:10px;border-radius:8px;margin:10px 0}}
small{{color:#786}}
</style></head><body><main>
<div class="top"><b>Alliance CRE Operating System · 7.3.8</b> · Source Recovery ·
<a href="/alliance/primary">Command Centre</a> ·
<a href="/alliance/primary/properties">Properties</a> ·
<a href="/alliance/primary/data-repair">7.3.7 Data Repair</a></div>
<h1>Historical Source Recovery · 7.3.8</h1>
<p>Source Link → historical source table → recover raw text / page context / image reference → 7.3.6 structure.</p>
<div class="safety"><b>AUDIT ONLY.</b> No master property mutation. No delete. No new canonical property. No source row mutation. An image reference is reported, not automatically trusted or guessed.</div>
{msg}
<div class="grid">{cards}</div>
<div class="panel">
<form method="post" action="/alliance/primary/source-recovery/run">
<label>Source links to inspect </label>
<input type="number" name="limit" value="5000" min="1" max="20000">
<button type="submit">Run Source Recovery Audit</button>
</form>
<p><b>Latest:</b> {html.escape(latest_text)}</p>
</div>
<div class="panel"><h2>Recovery Candidates</h2>
<table><thead><tr><th>Canonical ID</th><th>Source</th><th>Source Storage</th><th>Status</th><th>Evidence Key</th><th>Section</th><th>Original Text</th><th>Image/File Ref</th><th>Reason</th></tr></thead>
<tbody>{row_html}</tbody></table></div>
<div class="panel"><h2>Historical Source Storage Inventory</h2>
<p>This is the schema audit that tells us where the old evidence actually lives before any Vision reprocessing is enabled.</p>
<table><thead><tr><th>Source Table</th><th>Column</th><th>Type</th><th>Evidence Class</th></tr></thead>
<tbody>{inv_html}</tbody></table></div>
</main></body></html>""")


def register(core):
    app = _app(core)
    engine = _engine(core)
    if engine is None:
        raise RuntimeError("7.3.8 requires core.engine")
    _ensure(engine)

    if not _route_exists(app, "/api/alliance/v738/status"):
        @app.get("/api/alliance/v738/status")
        def _status():
            d = latest(engine)
            return {"version": VERSION, "mode": MODE, **d}

    if not _route_exists(app, "/alliance/primary/source-recovery"):
        @app.get("/alliance/primary/source-recovery", response_class=HTMLResponse)
        def _page(request: Request):
            return dashboard(core, request)

    if not _route_exists(app, "/alliance/primary/source-recovery/run"):
        @app.post("/alliance/primary/source-recovery/run")
        def _run(request: Request, limit: int = Form(5000)):
            _role(core, request)
            result = run_audit(engine, limit=limit)
            return RedirectResponse(
                url=f"/alliance/primary/source-recovery?run={result['run_id']}",
                status_code=303,
            )

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "mode": MODE,
        "route": "/alliance/primary/source-recovery",
        "api": "/api/alliance/v738/status",
        "master_mutation": False,
        "docker_change": False,
    }


def self_test():
    sample = {
        "payload": {
            "section_heading": "COMMERCIAL - RENT",
            "locality": "Connaught Place",
            "original_description": "A-7 INNER CIRCLE 7500FT FF+SF+TF (KAPIL 011 41550460)",
            "image_path": "/media/magazine/page-12.jpg",
        }
    }
    k, raw = _best_text(sample)
    _, sec = _best_value((sample,), SECTION_KEY_HINTS)
    _, loc = _best_value((sample,), LOCALITY_KEY_HINTS)
    _, img = _best_value((sample,), IMAGE_KEY_HINTS)
    rec = section_v680.enrich_record(
        {"raw_line": raw, "original_description": raw, "section_heading": sec, "locality": loc},
        inherited_locality=loc, inherited_section_heading=sec
    )
    assert raw == "A-7 INNER CIRCLE 7500FT FF+SF+TF (KAPIL 011 41550460)"
    assert sec == "COMMERCIAL - RENT"
    assert loc == "Connaught Place"
    assert img == "/media/magazine/page-12.jpg"
    assert rec.get("property_category") == "COMMERCIAL"
    assert rec.get("transaction_type") == "RENT"
    assert rec.get("address") == "A-7, Inner Circle"
    assert int(rec.get("area_sqft")) == 7500
    return {"status": "PASS", "version": VERSION, "text_key": k}


if __name__ == "__main__":
    print(json.dumps(self_test(), indent=2))
