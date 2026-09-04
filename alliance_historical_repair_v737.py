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

VERSION = "7.3.7-ALLIANCE-HISTORICAL-EVIDENCE-REPAIR"
MODE = "DRY_RUN_FIRST_SOURCE_EVIDENCE_ONLY_CANONICAL_ID_PRESERVED_NO_NEW_MASTER_RECORDS"

DDL = [
    """CREATE TABLE IF NOT EXISTS pi_historical_repair_runs_v737(
        run_id BIGSERIAL PRIMARY KEY,
        mode TEXT NOT NULL,
        source_filter TEXT NOT NULL,
        status TEXT NOT NULL,
        scanned INTEGER NOT NULL DEFAULT 0,
        repairable INTEGER NOT NULL DEFAULT 0,
        already_correct INTEGER NOT NULL DEFAULT 0,
        needs_review INTEGER NOT NULL DEFAULT 0,
        source_missing INTEGER NOT NULL DEFAULT 0,
        duplicates_prevented INTEGER NOT NULL DEFAULT 0,
        applied INTEGER NOT NULL DEFAULT 0,
        failed INTEGER NOT NULL DEFAULT 0,
        result JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS pi_historical_repair_candidates_v737(
        id BIGSERIAL PRIMARY KEY,
        run_id BIGINT NOT NULL,
        canonical_id TEXT NOT NULL,
        source_type TEXT,
        source_table TEXT,
        source_pk TEXT,
        source_row_hash TEXT,
        status TEXT NOT NULL,
        current_record JSONB NOT NULL DEFAULT '{}'::jsonb,
        proposed_record JSONB NOT NULL DEFAULT '{}'::jsonb,
        change_set JSONB NOT NULL DEFAULT '{}'::jsonb,
        evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
        reason TEXT,
        applied_at TIMESTAMPTZ,
        applied_by TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(run_id, canonical_id, source_table, source_pk, source_row_hash)
    )""",
    """CREATE TABLE IF NOT EXISTS pi_historical_repair_audit_v737(
        id BIGSERIAL PRIMARY KEY,
        run_id BIGINT,
        canonical_id TEXT NOT NULL,
        action TEXT NOT NULL,
        actor TEXT,
        before_record JSONB,
        after_record JSONB,
        details JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )""",
    """CREATE INDEX IF NOT EXISTS idx_v737_candidates_run_status
       ON pi_historical_repair_candidates_v737(run_id,status)""",
    """CREATE INDEX IF NOT EXISTS idx_v737_candidates_canonical
       ON pi_historical_repair_candidates_v737(canonical_id,created_at DESC)""",
]

TEXT_KEYS = (
    "original_description", "original_message", "raw_line", "raw_text",
    "description", "message", "ad_text", "listing_text", "content", "body", "text"
)
SECTION_KEYS = (
    "section_heading", "category_heading", "transaction_heading", "page_section",
    "section", "heading"
)
LOCALITY_KEYS = ("locality", "location", "locality_clean", "area_name")
IMAGE_KEYS = (
    "image_url", "image_path", "source_image", "page_image", "image",
    "media_url", "file_path", "attachment_url"
)

SOURCE_FILTER = ("MAGAZINE", "NEWSPAPER")


def _engine(core):
    return getattr(core, "engine", None)


def _app(core):
    return getattr(core, "app", None) or core


def _route_exists(app, path):
    return any(getattr(r, "path", None) == path for r in getattr(app, "routes", []))


def _role(core, req):
    fn = getattr(core, "need_login", None)
    return fn(req) if fn else "team"


def _actor(core, req):
    fn = getattr(core, "actor_name", None)
    return fn(req) if fn else "team"


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


def _columns(engine, table_name: str) -> List[str]:
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name=:t
            ORDER BY ordinal_position
        """), {"t": table_name}).scalars().all()
    return [str(x) for x in rows]


def _ensure(engine):
    required = ["pi_master_properties_v711", "pi_master_source_links_v711"]
    for t in required:
        if not _table_exists(engine, t):
            raise RuntimeError("Required master table missing: " + t)
    with engine.begin() as c:
        for ddl in DDL:
            c.execute(text(ddl))


def _walk(obj: Any, keys: Iterable[str]) -> List[str]:
    wanted = {str(x).lower() for x in keys}
    out: List[str] = []
    def rec(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if str(k).lower() in wanted and v not in (None, "", [], {}):
                    if isinstance(v, (dict, list)):
                        out.append(json.dumps(_safe(v), ensure_ascii=False))
                    else:
                        out.append(str(v))
                rec(v)
        elif isinstance(x, list):
            for y in x:
                rec(y)
    rec(obj)
    return out


def _best_text(*objects: Any) -> str:
    candidates: List[str] = []
    for obj in objects:
        candidates.extend(_walk(obj, TEXT_KEYS))
    candidates = [re.sub(r"\s+", " ", x).strip() for x in candidates if str(x).strip()]
    if not candidates:
        return ""
    # Prefer a full property-like row, then length.
    candidates.sort(
        key=lambda s: (
            bool(re.search(r"\b(?:SHOP\s*NO[-\s]*\d+|[A-Z]-?\d+)\b", s, re.I)),
            bool(re.search(r"\b\d{2,7}\s*(?:SQFT|SFT|SF|FT|SQ\.?\s*FT)\b", s, re.I)),
            len(s),
        ),
        reverse=True,
    )
    return candidates[0]


def _best_value(objects: Iterable[Any], keys: Iterable[str]) -> str:
    vals: List[str] = []
    for obj in objects:
        vals.extend(_walk(obj, keys))
    vals = [re.sub(r"\s+", " ", str(x)).strip() for x in vals if str(x).strip()]
    return vals[0] if vals else ""


def _source_row(engine, table_name: str, source_pk: str) -> Tuple[Dict[str, Any], str]:
    """Best-effort source lookup. Never mutates source tables."""
    if not table_name or not _table_exists(engine, table_name):
        return {}, "SOURCE_TABLE_MISSING"
    cols = _columns(engine, table_name)
    preferred = [
        "id", "record_id", "property_id", "source_id", "pk",
        "master_property_id", "canonical_id", "row_id"
    ]
    try_cols = [x for x in preferred if x in cols]
    for col in try_cols:
        sql = f"SELECT to_jsonb(t) AS row_data FROM {_qident(table_name)} t WHERE CAST({_qident(col)} AS TEXT)=:pk LIMIT 1"
        try:
            with engine.connect() as c:
                row = c.execute(text(sql), {"pk": str(source_pk)}).scalar()
            if row:
                return _json(row) if not isinstance(row, dict) else dict(row), f"FOUND_BY_{col}"
        except Exception:
            continue
    return {}, "SOURCE_ROW_NOT_RESOLVED"


def _current_master(engine, canonical_id: str) -> Dict[str, Any]:
    with engine.connect() as c:
        r = c.execute(
            text("SELECT * FROM pi_master_properties_v711 WHERE canonical_id=:id"),
            {"id": canonical_id},
        ).mappings().first()
    return _safe(dict(r)) if r else {}


def _source_links(engine, limit: int, canonical_id: str = "") -> List[Dict[str, Any]]:
    wh = ["UPPER(COALESCE(source_type,'')) IN ('MAGAZINE','NEWSPAPER')", "master_entity_type='PROPERTY'"]
    params: Dict[str, Any] = {"n": max(1, min(int(limit), 20000))}
    if canonical_id:
        wh.append("canonical_id=:cid")
        params["cid"] = canonical_id
    with engine.connect() as c:
        rows = c.execute(text(f"""
            SELECT canonical_id,source_type,source_table,source_pk,source_row_hash,created_at
            FROM pi_master_source_links_v711
            WHERE {' AND '.join(wh)}
            ORDER BY created_at,id
            LIMIT :n
        """), params).mappings().all()
    return [_safe(dict(x)) for x in rows]


def _section_from_context(master: Dict[str, Any], source: Dict[str, Any]) -> str:
    clean = _json(master.get("clean_record"))
    return _best_value([source, clean], SECTION_KEYS)


def _locality_from_context(master: Dict[str, Any], source: Dict[str, Any]) -> str:
    clean = _json(master.get("clean_record"))
    return (
        str(master.get("locality") or "").strip()
        or _best_value([source, clean], LOCALITY_KEYS)
    )


def _image_ref(master: Dict[str, Any], source: Dict[str, Any]) -> str:
    clean = _json(master.get("clean_record"))
    return _best_value([source, clean], IMAGE_KEYS)


def _candidate_from_link(engine, link: Dict[str, Any]) -> Dict[str, Any]:
    cid = str(link["canonical_id"])
    master = _current_master(engine, cid)
    if not master:
        return {"canonical_id": cid, "status": "FAILED", "reason": "MASTER_RECORD_MISSING"}

    source, source_lookup = _source_row(engine, str(link.get("source_table") or ""), str(link.get("source_pk") or ""))
    clean = _json(master.get("clean_record"))
    raw = _best_text(source, clean)
    section_heading = _section_from_context(master, source)
    locality = _locality_from_context(master, source)
    image_ref = _image_ref(master, source)

    evidence = {
        "source_type": link.get("source_type"),
        "source_table": link.get("source_table"),
        "source_pk": link.get("source_pk"),
        "source_row_hash": link.get("source_row_hash"),
        "source_lookup": source_lookup,
        "source_image_ref": image_ref,
        "section_heading": section_heading,
        "original_text": raw,
    }

    if not raw:
        status = "SOURCE_MISSING" if not image_ref else "NEEDS_SOURCE_IMAGE_REPROCESS"
        return {
            "canonical_id": cid,
            "status": status,
            "reason": "No retained original property text available for safe deterministic repair",
            "current_record": master,
            "proposed_record": {},
            "change_set": {},
            "evidence": evidence,
        }

    proposed = section_v680.enrich_record(
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

    # Existing transaction is valid evidence; explicit section heading may refine it.
    ctx = section_v680.parse_section_heading(section_heading)
    if ctx.get("transaction_type"):
        proposed["transaction_type"] = ctx["transaction_type"]
    if ctx.get("property_category"):
        proposed["property_category"] = ctx["property_category"]

    merged_clean = dict(clean)
    for k in (
        "section_heading", "property_category", "transaction_type", "address",
        "locality", "city", "area_raw", "area_sqft", "floor_codes", "floors",
        "contact_name", "phones", "original_description", "raw_line",
        "quality_status", "needs_review", "review_reason"
    ):
        v = proposed.get(k)
        if v not in (None, "", [], {}):
            merged_clean[k] = _safe(v)
    merged_clean["repair_version"] = VERSION
    merged_clean["repair_source_evidence"] = {
        "source_type": link.get("source_type"),
        "source_table": link.get("source_table"),
        "source_pk": link.get("source_pk"),
        "source_row_hash": link.get("source_row_hash"),
    }

    proposed_master = {
        "transaction_type": proposed.get("transaction_type") or master.get("transaction_type"),
        "locality": proposed.get("locality") or master.get("locality"),
        "city": proposed.get("city") or master.get("city"),
        "area_sqft": proposed.get("area_sqft") if proposed.get("area_sqft") not in (None, "") else master.get("area_sqft"),
        "phones": proposed.get("phones") or master.get("phones") or [],
        "clean_record": merged_clean,
    }

    changes: Dict[str, Any] = {}
    for k, newv in proposed_master.items():
        oldv = master.get(k)
        if k == "clean_record":
            # Surface only meaningful fields, not every JSON metadata difference.
            oldc = clean
            fields = {}
            for fk in (
                "section_heading", "property_category", "transaction_type", "address",
                "locality", "area_raw", "area_sqft", "floor_codes", "floors",
                "contact_name", "phones", "original_description"
            ):
                if merged_clean.get(fk) not in (None, "", [], {}) and oldc.get(fk) != merged_clean.get(fk):
                    fields[fk] = {"before": _safe(oldc.get(fk)), "after": _safe(merged_clean.get(fk))}
            if fields:
                changes["clean_record"] = fields
        elif _safe(oldv) != _safe(newv) and newv not in (None, "", [], {}):
            changes[k] = {"before": _safe(oldv), "after": _safe(newv)}

    # Quality gates for auto-apply.
    reasons: List[str] = []
    if proposed.get("quality_status") != "PASS":
        reasons.append(str(proposed.get("review_reason") or "EXTRACTION_QUALITY_NOT_PASS"))
    if re.search(r"\b(?:SHOP\s*NO[-\s]*\d+|[A-Z]-?\d+)\b", raw, re.I) and not proposed.get("address"):
        reasons.append("VISIBLE_ADDRESS_NOT_CAPTURED")
    # If section heading is present and says category/transaction, both must survive.
    if section_heading:
        section_ctx = section_v680.parse_section_heading(section_heading)
        if section_ctx.get("property_category") and not proposed.get("property_category"):
            reasons.append("SECTION_CATEGORY_MISSING")
        if section_ctx.get("transaction_type") and not proposed.get("transaction_type"):
            reasons.append("SECTION_TRANSACTION_MISSING")

    if not changes:
        status = "ALREADY_CORRECT"
        reason = "Retained evidence produces no material repair"
    elif reasons:
        status = "NEEDS_REVIEW"
        reason = ";".join(dict.fromkeys(reasons))
    else:
        status = "VERIFIED_REPAIR"
        reason = "Evidence-backed deterministic repair; canonical ID preserved"

    return {
        "canonical_id": cid,
        "status": status,
        "reason": reason,
        "current_record": master,
        "proposed_record": proposed_master,
        "change_set": changes,
        "evidence": evidence,
    }


def _insert_candidate(engine, run_id: int, link: Dict[str, Any], cand: Dict[str, Any]):
    with engine.begin() as c:
        c.execute(text("""
            INSERT INTO pi_historical_repair_candidates_v737(
                run_id,canonical_id,source_type,source_table,source_pk,source_row_hash,status,
                current_record,proposed_record,change_set,evidence,reason
            ) VALUES(
                :run,:cid,:st,:tb,:pk,:rh,:status,
                CAST(:cur AS JSONB),CAST(:prop AS JSONB),CAST(:chg AS JSONB),CAST(:ev AS JSONB),:reason
            )
            ON CONFLICT(run_id,canonical_id,source_table,source_pk,source_row_hash) DO UPDATE SET
                status=EXCLUDED.status,current_record=EXCLUDED.current_record,
                proposed_record=EXCLUDED.proposed_record,change_set=EXCLUDED.change_set,
                evidence=EXCLUDED.evidence,reason=EXCLUDED.reason
        """), {
            "run": run_id, "cid": cand["canonical_id"], "st": link.get("source_type"),
            "tb": link.get("source_table"), "pk": str(link.get("source_pk") or ""),
            "rh": str(link.get("source_row_hash") or ""), "status": cand["status"],
            "cur": json.dumps(_safe(cand.get("current_record") or {}), ensure_ascii=False),
            "prop": json.dumps(_safe(cand.get("proposed_record") or {}), ensure_ascii=False),
            "chg": json.dumps(_safe(cand.get("change_set") or {}), ensure_ascii=False),
            "ev": json.dumps(_safe(cand.get("evidence") or {}), ensure_ascii=False),
            "reason": cand.get("reason") or "",
        })


def dry_run(engine, limit: int = 5000) -> Dict[str, Any]:
    _ensure(engine)
    with engine.begin() as c:
        run_id = c.execute(text("""
            INSERT INTO pi_historical_repair_runs_v737(mode,source_filter,status)
            VALUES('DRY_RUN','MAGAZINE,NEWSPAPER','RUNNING') RETURNING run_id
        """)).scalar_one()

    links = _source_links(engine, limit)
    seen_canonical = set()
    counts = {
        "scanned": 0, "repairable": 0, "already_correct": 0,
        "needs_review": 0, "source_missing": 0,
        "duplicates_prevented": 0, "failed": 0
    }
    for link in links:
        counts["scanned"] += 1
        cid = str(link["canonical_id"])
        if cid in seen_canonical:
            counts["duplicates_prevented"] += 1
        else:
            seen_canonical.add(cid)
        try:
            cand = _candidate_from_link(engine, link)
        except Exception as exc:
            cand = {
                "canonical_id": cid, "status": "FAILED",
                "reason": f"{type(exc).__name__}: {exc}",
                "current_record": {}, "proposed_record": {}, "change_set": {},
                "evidence": {
                    "source_type": link.get("source_type"), "source_table": link.get("source_table"),
                    "source_pk": link.get("source_pk"), "source_row_hash": link.get("source_row_hash"),
                }
            }
        _insert_candidate(engine, run_id, link, cand)
        status = cand["status"]
        if status == "VERIFIED_REPAIR":
            counts["repairable"] += 1
        elif status == "ALREADY_CORRECT":
            counts["already_correct"] += 1
        elif status in ("NEEDS_REVIEW", "NEEDS_SOURCE_IMAGE_REPROCESS"):
            counts["needs_review"] += 1
        elif status == "SOURCE_MISSING":
            counts["source_missing"] += 1
        else:
            counts["failed"] += 1

    result = {
        "version": VERSION,
        "mode": "DRY_RUN",
        "run_id": run_id,
        **counts,
        "canonical_entities_scanned": len(seen_canonical),
        "safety": {
            "master_updates": 0,
            "master_inserts": 0,
            "master_deletes": 0,
            "source_mutations": 0,
            "canonical_ids_changed": 0,
            "dry_run": True,
        },
    }
    with engine.begin() as c:
        c.execute(text("""
            UPDATE pi_historical_repair_runs_v737 SET
              status='COMPLETE',scanned=:sc,repairable=:rp,already_correct=:ac,
              needs_review=:nr,source_missing=:sm,duplicates_prevented=:dp,
              failed=:fl,result=CAST(:r AS JSONB)
            WHERE run_id=:id
        """), {
            "id": run_id, "sc": counts["scanned"], "rp": counts["repairable"],
            "ac": counts["already_correct"], "nr": counts["needs_review"],
            "sm": counts["source_missing"], "dp": counts["duplicates_prevented"],
            "fl": counts["failed"], "r": json.dumps(result, ensure_ascii=False),
        })
    return result


def apply_verified(engine, run_id: int, actor: str) -> Dict[str, Any]:
    _ensure(engine)
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT * FROM pi_historical_repair_candidates_v737
            WHERE run_id=:run AND status='VERIFIED_REPAIR' AND applied_at IS NULL
            ORDER BY id
        """), {"run": run_id}).mappings().all()

    applied = failed = 0
    for row in rows:
        d = dict(row)
        cid = str(d["canonical_id"])
        current = _current_master(engine, cid)
        proposed = _json(d.get("proposed_record"))
        if not current:
            failed += 1
            continue

        # Optimistic safety: refuse if master changed after dry-run in material top-level fields.
        dry_current = _json(d.get("current_record"))
        material = ("transaction_type", "locality", "city", "area_sqft", "phones")
        if any(_safe(current.get(k)) != _safe(dry_current.get(k)) for k in material):
            with engine.begin() as c:
                c.execute(text("""
                    UPDATE pi_historical_repair_candidates_v737
                    SET status='NEEDS_REVIEW',reason='MASTER_CHANGED_SINCE_DRY_RUN'
                    WHERE id=:id
                """), {"id": d["id"]})
            failed += 1
            continue

        before = _safe(current)
        tx = proposed.get("transaction_type") or current.get("transaction_type")
        locality = proposed.get("locality") or current.get("locality")
        city = proposed.get("city") or current.get("city")
        area_sqft = proposed.get("area_sqft")
        if area_sqft in (None, ""):
            area_sqft = current.get("area_sqft")
        phones = proposed.get("phones") or current.get("phones") or []
        clean_record = proposed.get("clean_record") or current.get("clean_record") or {}

        try:
            with engine.begin() as c:
                c.execute(text("""
                    UPDATE pi_master_properties_v711 SET
                        transaction_type=:tx,
                        locality=:loc,
                        city=:city,
                        area_sqft=:asq,
                        phones=CAST(:phones AS JSONB),
                        clean_record=CAST(:clean AS JSONB),
                        source_version=:sv,
                        updated_at=NOW()
                    WHERE canonical_id=:cid
                """), {
                    "cid": cid, "tx": tx, "loc": locality, "city": city, "asq": area_sqft,
                    "phones": json.dumps(_safe(phones), ensure_ascii=False),
                    "clean": json.dumps(_safe(clean_record), ensure_ascii=False),
                    "sv": VERSION,
                })
                after = c.execute(
                    text("SELECT * FROM pi_master_properties_v711 WHERE canonical_id=:id"),
                    {"id": cid},
                ).mappings().first()
                c.execute(text("""
                    INSERT INTO pi_historical_repair_audit_v737(
                      run_id,canonical_id,action,actor,before_record,after_record,details
                    ) VALUES(
                      :run,:cid,'APPLY_VERIFIED_REPAIR',:actor,
                      CAST(:before AS JSONB),CAST(:after AS JSONB),CAST(:details AS JSONB)
                    )
                """), {
                    "run": run_id, "cid": cid, "actor": actor,
                    "before": json.dumps(before, ensure_ascii=False),
                    "after": json.dumps(_safe(dict(after)) if after else {}, ensure_ascii=False),
                    "details": json.dumps({
                        "candidate_id": d["id"],
                        "source_type": d.get("source_type"),
                        "source_table": d.get("source_table"),
                        "source_pk": d.get("source_pk"),
                        "canonical_id_preserved": True,
                        "new_master_record_created": False,
                    }, ensure_ascii=False),
                })
                c.execute(text("""
                    UPDATE pi_historical_repair_candidates_v737
                    SET applied_at=NOW(),applied_by=:actor,status='APPLIED'
                    WHERE id=:id
                """), {"id": d["id"], "actor": actor})
            applied += 1
        except Exception:
            failed += 1

    with engine.begin() as c:
        c.execute(text("""
            UPDATE pi_historical_repair_runs_v737
            SET applied=COALESCE(applied,0)+:applied,failed=COALESCE(failed,0)+:failed
            WHERE run_id=:run
        """), {"applied": applied, "failed": failed, "run": run_id})

    return {
        "version": VERSION, "mode": "APPLY_VERIFIED_REPAIRS", "run_id": run_id,
        "applied": applied, "failed_or_changed_since_scan": failed,
        "canonical_ids_preserved": True, "new_master_records_created": 0,
        "source_rows_mutated": 0,
    }


def _latest_run(engine) -> Dict[str, Any]:
    with engine.connect() as c:
        r = c.execute(text("""
            SELECT * FROM pi_historical_repair_runs_v737 ORDER BY run_id DESC LIMIT 1
        """)).mappings().first()
    return _safe(dict(r)) if r else {}


def _candidate_rows(engine, run_id: int, limit: int = 250) -> List[Dict[str, Any]]:
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT id,canonical_id,source_type,source_table,source_pk,status,change_set,evidence,reason,applied_at
            FROM pi_historical_repair_candidates_v737
            WHERE run_id=:run
            ORDER BY
              CASE status WHEN 'VERIFIED_REPAIR' THEN 1 WHEN 'NEEDS_REVIEW' THEN 2
                          WHEN 'NEEDS_SOURCE_IMAGE_REPROCESS' THEN 3 WHEN 'SOURCE_MISSING' THEN 4
                          WHEN 'ALREADY_CORRECT' THEN 5 ELSE 6 END,
              id
            LIMIT :n
        """), {"run": run_id, "n": limit}).mappings().all()
    return [_safe(dict(x)) for x in rows]


def _esc(v):
    return html.escape("" if v is None else str(v))


def _dashboard(core, req, message=""):
    role = _role(core, req)
    engine = _engine(core)
    _ensure(engine)
    latest = _latest_run(engine)
    run_id = int(latest.get("run_id") or 0)
    rows = _candidate_rows(engine, run_id) if run_id else []

    cards = [
        ("Scanned", latest.get("scanned", 0)),
        ("Repairable", latest.get("repairable", 0)),
        ("Already Correct", latest.get("already_correct", 0)),
        ("Needs Review", latest.get("needs_review", 0)),
        ("Source Missing", latest.get("source_missing", 0)),
        ("Duplicates Prevented", latest.get("duplicates_prevented", 0)),
        ("Applied", latest.get("applied", 0)),
        ("Failed", latest.get("failed", 0)),
    ]
    card_html = "".join(
        f"<div class='card'><div class='num'>{_esc(v)}</div><b>{_esc(k)}</b></div>"
        for k, v in cards
    )

    trs = []
    for r in rows:
        ev = _json(r.get("evidence"))
        ch = _json(r.get("change_set"))
        original = str(ev.get("original_text") or "")
        if len(original) > 260:
            original = original[:257] + "..."
        trs.append(
            "<tr>"
            f"<td>{_esc(r.get('canonical_id'))}</td>"
            f"<td>{_esc(r.get('source_type'))}</td>"
            f"<td>{_esc(r.get('status'))}</td>"
            f"<td>{_esc(ev.get('section_heading'))}</td>"
            f"<td>{_esc(original)}</td>"
            f"<td><pre>{_esc(json.dumps(ch, ensure_ascii=False, indent=1))}</pre></td>"
            f"<td>{_esc(r.get('reason'))}</td>"
            "</tr>"
        )

    apply_button = ""
    if run_id and latest.get("repairable", 0):
        apply_button = f"""
        <form method="post" action="/alliance/primary/data-repair/apply" class="inline"
              onsubmit="return confirm('Apply only VERIFIED_REPAIR candidates from dry run {run_id}? Canonical IDs will be preserved.');">
          <input type="hidden" name="run_id" value="{run_id}">
          <button class="btn good" type="submit">Apply Verified Repairs</button>
        </form>"""

    msg = f"<div class='notice'>{_esc(message)}</div>" if message else ""
    body = f"""
    <div class="hero">
      <div><h1>Historical Database Repair · 7.3.7</h1>
      <p>Magazine/Newspaper evidence → 7.3.6 structure → dry-run diff → verified update of the SAME canonical property.</p></div>
      <div class="badge">NO DELETE · NO NEW MASTER PROPERTY · SOURCE EVIDENCE PRESERVED</div>
    </div>
    {msg}
    <div class="warning"><b>Safety:</b> Dry Run never changes the master database. Apply updates only evidence-backed VERIFIED_REPAIR rows. Missing source evidence is never invented.</div>
    <div class="grid">{card_html}</div>
    <div class="actions">
      <form method="post" action="/alliance/primary/data-repair/dry-run" class="inline">
        <label>Source links to scan <input name="limit" type="number" value="5000" min="1" max="20000"></label>
        <button class="btn" type="submit">Run Dry Scan</button>
      </form>
      {apply_button}
      <a class="btn alt" href="/alliance/primary/properties">Master Properties</a>
    </div>
    <div class="card">
      <b>Latest run:</b> {_esc(run_id or "None")} · {_esc(latest.get("status") or "")}
      <br><small>Rows below are evidence-level candidates. Multiple sources can belong to one canonical property without creating duplicates.</small>
    </div>
    <div class="tablebox"><table>
      <thead><tr>
        <th>Canonical ID</th><th>Source</th><th>Status</th><th>Section Heading</th>
        <th>Original Evidence</th><th>Proposed Changes</th><th>Reason</th>
      </tr></thead><tbody>{''.join(trs) if trs else '<tr><td colspan="7">No repair scan yet.</td></tr>'}</tbody>
    </table></div>
    """
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alliance Historical Database Repair 7.3.7</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;font-family:Arial,sans-serif;background:#f4f7fb;color:#172033}}
header{{background:#0d2238;color:#fff;padding:16px 22px}}header a{{color:#fff}}
.wrap{{max-width:1900px;margin:auto;padding:18px}}.hero{{display:flex;justify-content:space-between;gap:15px;align-items:center;flex-wrap:wrap}}
.badge{{background:#e8f7ef;color:#067647;padding:10px 12px;border-radius:9px;font-weight:700}}
.warning,.notice{{background:#fff4e5;border:1px solid #f5c16c;padding:12px;border-radius:9px;margin:10px 0}}
.notice{{background:#eaf2ff;border-color:#b7cdf8}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:9px;margin:12px 0}}
.card{{background:#fff;border:1px solid #dfe6ee;border-radius:11px;padding:12px}}.num{{font-size:25px;font-weight:800}}
.actions,.inline{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:10px 0}}
.btn{{border:0;background:#0d2238;color:#fff;padding:9px 12px;border-radius:8px;text-decoration:none;cursor:pointer}}
.btn.good{{background:#067647}}.btn.alt{{background:#475467}}
input{{padding:8px;border:1px solid #ccd5df;border-radius:7px}}
.tablebox{{overflow:auto;max-height:67vh;background:#fff;border:1px solid #dfe6ee;border-radius:11px}}
table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{padding:8px;border-bottom:1px solid #edf0f4;text-align:left;vertical-align:top}}
th{{position:sticky;top:0;background:#f8fafc}}pre{{white-space:pre-wrap;max-width:460px;margin:0}}
</style></head><body>
<header><b>Alliance CRE Operating System · 7.3.7</b> · Historical Repair · {_esc(role)}
&nbsp; <a href="/alliance/primary">Command Centre</a> · <a href="/alliance/primary/properties">Properties</a></header>
<div class="wrap">{body}</div></body></html>"""


def register(core):
    app = _app(core)
    engine = _engine(core)
    if engine is None:
        raise RuntimeError("Database engine unavailable")
    _ensure(engine)

    if not _route_exists(app, "/alliance/primary/data-repair"):
        @app.get("/alliance/primary/data-repair", response_class=HTMLResponse)
        def page(req: Request, message: str = ""):
            return _dashboard(core, req, message)

    if not _route_exists(app, "/alliance/primary/data-repair/dry-run"):
        @app.post("/alliance/primary/data-repair/dry-run")
        def run_dry(req: Request, limit: int = Form(5000)):
            _role(core, req)
            result = dry_run(engine, limit=limit)
            msg = (
                f"Dry run {result['run_id']} complete: "
                f"{result['repairable']} repairable, {result['already_correct']} already correct, "
                f"{result['needs_review']} need review, {result['source_missing']} source missing."
            )
            return RedirectResponse(
                "/alliance/primary/data-repair?message=" + msg.replace(" ", "%20"),
                status_code=303,
            )

    if not _route_exists(app, "/alliance/primary/data-repair/apply"):
        @app.post("/alliance/primary/data-repair/apply")
        def apply(req: Request, run_id: int = Form(...)):
            role = str(_role(core, req)).lower()
            if role not in ("admin", "super admin", "super_admin"):
                raise HTTPException(403, "Admin required to apply historical repairs")
            result = apply_verified(engine, run_id, _actor(core, req))
            msg = (
                f"Run {run_id}: applied {result['applied']} verified repairs; "
                f"{result['failed_or_changed_since_scan']} skipped/failed."
            )
            return RedirectResponse(
                "/alliance/primary/data-repair?message=" + msg.replace(" ", "%20"),
                status_code=303,
            )

    if not _route_exists(app, "/api/alliance/v737/status"):
        @app.get("/api/alliance/v737/status")
        def status(req: Request):
            _role(core, req)
            return {
                "status": "ok", "version": VERSION, "mode": MODE,
                "latest_run": _latest_run(engine),
                "routes": [
                    "/alliance/primary/data-repair",
                    "/alliance/primary/data-repair/dry-run",
                    "/alliance/primary/data-repair/apply",
                ],
            }

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": "/alliance/primary/data-repair",
        "safety": {
            "default": "DRY_RUN",
            "source_mutation": False,
            "new_master_records": False,
            "canonical_id_changes": False,
        },
    }


def self_test():
    ex = section_v680.enrich_record(
        {
            "section_heading": "COMMERCIAL - RENT",
            "raw_line": "A-7 INNER CIRCLE 7500FT FF+SF+TF (KAPIL 011 41550460)",
        },
        inherited_locality="Connaught Place",
        inherited_section_heading="COMMERCIAL - RENT",
    )
    checks = {
        "category": ex.get("property_category") == "COMMERCIAL",
        "transaction": ex.get("transaction_type") == "RENT",
        "address": ex.get("address") == "A-7, Inner Circle",
        "locality": ex.get("locality") == "Connaught Place",
        "area": ex.get("area_sqft") == 7500,
        "contact": ex.get("contact_name") == "Kapil",
        "phone": ex.get("phones") == ["01141550460"],
        "original": ex.get("original_description") == "A-7 INNER CIRCLE 7500FT FF+SF+TF (KAPIL 011 41550460)",
    }
    return {"version": VERSION, "status": "PASS" if all(checks.values()) else "FAIL", "checks": checks, "example": ex}


if __name__ == "__main__":
    print(json.dumps(self_test(), ensure_ascii=False, indent=2))
