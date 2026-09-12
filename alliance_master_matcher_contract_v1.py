from __future__ import annotations
import json
from typing import Any, Dict, List, Tuple
from sqlalchemy import text
import alliance_phase5_canonical_matcher as base
from alliance_phase5_canonical_matcher import *

VERSION = "1.0.0-MASTER-DATABASE-AUTHORITY"
MASTER_TABLE = "pi_master_properties_v711"
WORKFLOW_TABLE = "pi_master_workflow_v720"
MATCHER_SOURCE_CONTRACT = "MASTER_ONLY"

def _json(v):
    if isinstance(v, dict):
        return v
    if not v:
        return {}
    try:
        return json.loads(v)
    except Exception:
        return {}

def _float(v):
    try:
        if v in (None, ""):
            return None
        return float(str(v).replace(",", "").strip())
    except Exception:
        return None

def _master_workflow(engine):
    out = {}
    try:
        if not base.table_exists(engine, WORKFLOW_TABLE):
            return out
        cols = base.table_columns(engine, WORKFLOW_TABLE)
        wanted = [x for x in ("canonical_id","verification_status","availability_status","updated_at") if x in cols]
        if "canonical_id" not in wanted:
            return out
        q = "SELECT " + ",".join(f'"{x}"' for x in wanted) + f" FROM {WORKFLOW_TABLE}"
        with engine.connect() as c:
            for r in c.execute(text(q)).mappings().all():
                out[str(r.get("canonical_id") or "")] = dict(r)
    except Exception:
        return {}
    return out

def load_master_properties(engine, limit: int = 50000) -> List[Dict[str, Any]]:
    if not base.table_exists(engine, MASTER_TABLE):
        raise RuntimeError(f"MASTER_SOURCE_MISSING:{MASTER_TABLE}")
    cols = base.table_columns(engine, MASTER_TABLE)
    wanted = [x for x in (
        "master_property_id","canonical_id","source_type","transaction_type",
        "locality","city","area_value","area_unit","area_sqft",
        "price_raw","price_kind","clean_record","source_count",
        "promotion_status","source_version","created_at","updated_at"
    ) if x in cols]
    if "canonical_id" not in wanted and "master_property_id" not in wanted:
        raise RuntimeError("MASTER_ID_COLUMN_MISSING")
    q = "SELECT " + ",".join(f'"{x}"' for x in wanted) + f" FROM {MASTER_TABLE}"
    if "promotion_status" in cols:
        q += " WHERE COALESCE(promotion_status,'') NOT IN ('REJECTED','DELETED','DUPLICATE','QUARANTINED')"
    if "updated_at" in cols:
        q += " ORDER BY updated_at DESC NULLS LAST"
    elif "created_at" in cols:
        q += " ORDER BY created_at DESC NULLS LAST"
    q += " LIMIT :lim"
    with engine.connect() as c:
        rows = [dict(r) for r in c.execute(text(q), {"lim": int(max(1,min(limit,100000)))}).mappings().all()]
    wf = _master_workflow(engine)
    out = []
    for d in rows:
        cid = str(d.get("canonical_id") or d.get("master_property_id") or "").strip()
        if not cid:
            continue
        flow = wf.get(cid, {})
        if str(flow.get("availability_status") or "").upper() in {"UNAVAILABLE","REMOVED","CLOSED","SOLD","LEASED"}:
            continue
        clean = _json(d.get("clean_record"))
        wa = clean.get("whatsapp_live_clean") if isinstance(clean.get("whatsapp_live_clean"), dict) else {}
        raw = str(clean.get("original_message") or clean.get("description") or clean.get("property_name") or "").strip()
        ptype = str(clean.get("property_type") or "").strip()
        loc_raw = str(d.get("locality") or d.get("city") or "").strip()
        loc = base.canonical_location(loc_raw) or base.candidate_location(loc_raw) or base.norm(loc_raw) or None
        tx = base.norm(d.get("transaction_type"))
        if tx not in {"SALE","RENT"} or not loc:
            continue
        fam, sub = base.family_subtype(ptype, raw)
        area = _float(d.get("area_sqft"))
        if area is None:
            area = base.area_to_sqft(d.get("area_value"), d.get("area_unit"))
        price = _float(d.get("price_raw"))
        if price is None:
            try:
                price = base.money_value(d.get("price_raw"))
            except Exception:
                price = None
        kind = base.norm(d.get("price_kind"))
        comparable = price is not None and (not kind or "AMOUNT" in kind or tx in kind)
        ver = str(flow.get("verification_status") or "UNVERIFIED").upper()
        promotion = str(d.get("promotion_status") or "").upper()
        strict_ready = promotion in {"PROMOTED_VALIDATED","ACTIVE","READY","VERIFIED"}
        captured = d.get("updated_at") or d.get("created_at") or flow.get("updated_at")
        desc = raw or " ".join(x for x in (ptype, loc_raw, tx) if x)
        completeness = sum(bool(x) for x in (loc, tx, fam, sub, area, price, desc))
        out.append({
            "record_id": cid,
            "source_bucket": "MASTER_DATABASE",
            "source_table": MASTER_TABLE,
            "description": base.sanitize_text(desc),
            "location": loc,
            "transaction": tx,
            "family": fam,
            "subtype": sub,
            "area_sqft": area,
            "area_unit_verified": bool(area is not None),
            "price": price if comparable else None,
            "price_text": base.sanitize_text(d.get("price_raw")),
            "price_comparable": comparable,
            "quality": "READY" if strict_ready else "REVIEW",
            "verification": ver,
            "captured_on": captured,
            "source_name": base.sanitize_text(d.get("source_type") or "Master Property Database"),
            "review_reasons": None,
            "detail_url": f"/alliance/primary/property/{cid}",
            "source_count": int(d.get("source_count") or 1),
            "data_completeness": completeness,
            "master_property_id": d.get("master_property_id"),
            "canonical_id": cid,
            "whatsapp_source_id": wa.get("source_id") if wa else None,
        })
    return out

def _count(engine, table: str) -> int:
    try:
        if not base.table_exists(engine, table):
            return 0
        with engine.connect() as c:
            return int(c.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
    except Exception:
        return 0

def load_candidates(engine, pi_limit: int = 50000, wa_limit: int = 0) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    rows = load_master_properties(engine, pi_limit)
    promoted_wa = 0
    try:
        with engine.connect() as c:
            promoted_wa = int(c.execute(text(
                "SELECT COUNT(*) FROM pi_master_properties_v711 WHERE UPPER(COALESCE(source_type,'')) LIKE 'WHATSAPP%'"
            )).scalar() or 0)
    except Exception:
        pass
    return rows, {
        "matcher_source_contract": MATCHER_SOURCE_CONTRACT,
        "primary_source": MASTER_TABLE,
        "master_rows_considered": len(rows),
        "master_rows_total": _count(engine, MASTER_TABLE),
        "master_whatsapp_promoted": promoted_wa,
        "parallel_whatsapp_match_candidates": 0,
        "total_before_dedupe": len(rows),
    }

def public_item(p: Dict[str, Any], match_score: float, match_class: str, why: List[str]) -> Dict[str, Any]:
    item = base.public_item(p, match_score, match_class, why)
    rid = str(p.get("record_id") or "")
    item["detail_url"] = p.get("detail_url") or (f"/alliance/primary/property/{rid}" if rid else None)
    item["source_count"] = int(p.get("source_count") or 1)
    item["data_completeness"] = int(p.get("data_completeness") or 0)
    item["captured_on"] = p.get("captured_on")
    return item

def _sort_key(x):
    return (
        1 if x.get("send_eligible") else 0,
        1 if x.get("availability_verification") == "VERIFIED" else 0,
        float(x.get("match_score") or 0),
        int(x.get("data_completeness") or 0),
        str(x.get("captured_on") or ""),
        str(x.get("record_id") or ""),
    )

def run_match(engine, requirement_text: str, min_score: float = 70.0, limit: int = 50) -> Dict[str, Any]:
    req = base.parse_requirement(requirement_text)
    raw, source_counts = load_candidates(engine)
    candidates = base.dedupe_candidates(raw)
    exact_verified, exact_verify, rejected = [], [], []
    for p in candidates:
        ok, code, gate = base.eligible(req, p, "EXACT")
        if not ok:
            if len(rejected) < 200:
                rejected.append({"record_id": p.get("record_id"), "reason": code})
            continue
        ms, why = base.score(req, p, "EXACT", gate)
        if ms < min_score:
            continue
        item = public_item(p, ms, "EXACT", why)
        (exact_verified if item["send_eligible"] else exact_verify).append(item)
    exact_verified.sort(key=_sort_key, reverse=True)
    exact_verify.sort(key=_sort_key, reverse=True)

    alternatives = []
    if not exact_verified:
        allowed = set(base.approved_alternatives(req))
        if allowed:
            for p in candidates:
                if p.get("location") not in allowed:
                    continue
                ok, code, gate = base.eligible(req, p, "ALTERNATIVE")
                if not ok:
                    continue
                ms, why = base.score(req, p, "ALTERNATIVE", gate)
                if ms >= max(60.0, min_score - 10.0):
                    alternatives.append(public_item(p, ms, "APPROVED_ALTERNATIVE", why))
            alternatives.sort(key=_sort_key, reverse=True)

    result = {
        "version": VERSION,
        "requirement": req,
        "summary": {
            **source_counts,
            "deduped_candidates": len(candidates),
            "exact_verified": len(exact_verified),
            "exact_needs_verification": len(exact_verify),
            "approved_alternatives": len(alternatives),
            "inventory_gap": not bool(exact_verified or exact_verify or alternatives),
            "matching_path": "MASTER_DATABASE_ONLY",
            "primary_source": MASTER_TABLE,
            "fallback_source": None,
            "fallback_used": False,
            "contacts_exposed": False,
            "clickable_property_contract": True,
            "deterministic_business_ordering": True,
        },
        "exact_verified": exact_verified[:limit],
        "exact_needs_verification": exact_verify[:limit],
        "alternatives": alternatives[:limit],
        "rejected_sample": rejected[:100],
    }
    if hasattr(base, "sanitize_public_payload"):
        result = base.sanitize_public_payload(result)
    payload = repr(result)
    if base.PHONE_RE.search(payload) or base.EMAIL_RE.search(payload):
        raise RuntimeError("CONTACT_LEAK_GUARD_TRIGGERED")
    return result
