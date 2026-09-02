from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Body, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_property_brain_gold_v2 as gold_v2

VERSION = "2.1.0-GOLD-BENCHMARK-INTELLIGENCE-REPAIR"
MODE = "GOLD_V1_EXACT_SNAPSHOT_CONTEXT_PROVENANCE_REPAIR"
SNAPSHOT_VERSION = "GOLD_V1_100"
BENCHMARK_VERSION = "PROPERTY_BRAIN_GOLD_BENCHMARK_V21"

DDL = [
    """
    CREATE TABLE IF NOT EXISTS alliance_gold_v21_error_catalog (
        error_id UUID PRIMARY KEY,
        run_id UUID NOT NULL,
        span_id UUID NOT NULL,
        category TEXT NOT NULL,
        gold_value JSONB,
        predicted_value JSONB,
        evidence_excerpt TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """
]

REQ_RE = re.compile(
    r"\b(?:URGENT\s+REQUIREMENT|REQUIREMENT|REQUIRED|LOOKING\s+FOR|WANTED|"
    r"NEED(?:ED)?|CLIENT\s+WANTS?|BUYER\s+REQUIREMENT|RENTAL\s+REQUIREMENT|"
    r"PURCHASE\s+REQUIREMENT)\b", re.I
)
PROPERTY_FACT_RE = re.compile(
    r"\b(?:BHK|FLAT|APARTMENT|VILLA|KOTHI|HOUSE|BUNGALOW|PLOT|LAND|OFFICE|"
    r"SHOP|SHOWROOM|WAREHOUSE|HOTEL|BANQUET|RESTAURANT|CAFE|CLUB|LOUNGE|"
    r"GUEST\s+HOUSE|SQ\.?\s*(?:FT|YD|MTR|M)|SQFT|SQYD|SQM|ACRE|FLOOR|PARKING)\b",
    re.I,
)
NOISE_RE = re.compile(
    r"\b(?:MARKET\s+UPDATE|NEWS|ARTICLE|BLOG|BEST\s+DEALS|PRIME\s+LOCATIONS)\b",
    re.I,
)
SALE_RE = re.compile(
    r"\b(?:FOR\s+SALE|SALE|SELL|SELLING|ASKING|DEMAND|MANDATE\s+SALE|"
    r"DESPERATE\s+SALE|STAKE|PARTNERSHIP)\b", re.I
)
RENT_RE = re.compile(r"\b(?:FOR\s+RENT|RENTAL|RENT|LEASE|LEASING|TO\s+LET)\b", re.I)
RENTED_RE = re.compile(r"\b(?:RENTED|PRE[\s\-]?RENTED|LEASED)\b", re.I)
BUY_REQ_RE = re.compile(r"\b(?:BUY|BUYING|PURCHASE|PURCHASING)\b", re.I)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s\-]?)?[6-9](?:[\s\-]?\d){9}(?!\d)")

CITY_ALIASES = {
    "GGN": "Gurgaon",
    "GURGAON": "Gurgaon",
    "GURUGRAM": "Gurgaon",
    "NOIDA": "Noida",
    "DELHI": "Delhi",
    "NEW DELHI": "Delhi",
    "MUMBAI": "Mumbai",
}

def _engine(core):
    return foundation._engine_from_core(core)

def _app(core):
    return getattr(core, "app", None) or core

def _loads(v, default):
    return foundation._loads(v, default)

def _safe(v):
    return foundation._json_safe(v)

def _install(engine):
    with engine.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))

def _norm(v):
    if v is None:
        return None
    s = re.sub(r"\s+", " ", str(v)).strip()
    return s.casefold() if s else None

def _phone_digits(value):
    d = re.sub(r"\D", "", str(value or ""))
    return d[-10:] if len(d) >= 10 else None

def _phones_from_text(value, provenance):
    seen = set()
    out = []
    for m in PHONE_RE.finditer(str(value or "")):
        p = _phone_digits(m.group(0))
        if p and p not in seen:
            seen.add(p)
            out.append({"phone": p, "provenance": provenance})
    return out

def _phone_set(items):
    out = set()
    for item in items or []:
        value = item.get("phone") if isinstance(item, dict) else item
        p = _phone_digits(value)
        if p:
            out.add(p)
    return sorted(out)

def _literal_supported(value, span, source):
    if not value:
        return True
    val = re.sub(r"\s+", " ", str(value)).strip()
    hay = re.sub(r"\s+", " ", str(span or "") + "\n" + str(source or ""))
    if val.casefold() in hay.casefold():
        return True
    if val.casefold() == "gurgaon":
        return bool(re.search(r"\b(?:GGN|GURGAON|GURUGRAM)\b", hay, re.I))
    return False

def _explicit_city(span, source):
    # Child evidence first. Whole-source evidence is allowed only for explicit city tokens.
    for raw in (str(span or ""), str(source or "")):
        for alias, normalized in CITY_ALIASES.items():
            if re.search(r"(?<![A-Za-z])" + re.escape(alias) + r"(?![A-Za-z])", raw, re.I):
                return normalized
    return None

def _sector_locality(span):
    raw = str(span or "")
    m = re.search(
        r"\bSector\s+(\d+[A-Za-z]?)(?:\s*,\s*(?:Noida|Delhi|Gurgaon|Gurugram|GGN))?"
        r"(?:\s*[-–—]\s*([A-Za-z0-9 ]+Block))?",
        raw, re.I
    )
    if m:
        base = "Sector " + m.group(1)
        block = re.sub(r"\s+", " ", m.group(2) or "").strip()
        return base + (", " + block if block else "")
    m = re.search(r"\bSec(?:tor)?\.?\s*(\d+[A-Za-z]?)\b", raw, re.I)
    return "Sector " + m.group(1) if m else None

def _project(span, source, proposal):
    candidate = proposal.get("project_name_hint") or proposal.get("project_hint")
    if candidate and _literal_supported(candidate, span, source):
        return candidate

    cleaned = re.sub(r"[*_`]", "", str(span or ""))
    lines = [re.sub(r"\s+", " ", x).strip() for x in cleaned.splitlines() if x.strip()]
    for line in lines[:3]:
        if "|" in line:
            left = line.split("|", 1)[0].strip(" -–—🏠🏡✨📍")
            if 3 <= len(left) <= 80 and PROPERTY_FACT_RE.search(cleaned):
                if not re.search(r"\b(?:SALE|RENT|AVAILABLE|REQUIREMENT)\b", left, re.I):
                    return left
        m = re.search(r"\b(?:PROJECT|BUILDING\s+NAME)\s*[:\-]\s*([^,\n|]{3,80})", line, re.I)
        if m:
            return m.group(1).strip()
    return None

def _content_type(span):
    raw = str(span or "")
    if REQ_RE.search(raw):
        return "REQUIREMENT"
    if re.search(r"\b(?:PLOTS|PROPERTIES|OPTIONS)\s+AVAILABLE\b", raw, re.I):
        if re.search(r"\b(?:Sector\s+\d+\s*/|multiple|various)\b", raw, re.I):
            return "INVENTORY_GROUP"
    if PROPERTY_FACT_RE.search(raw):
        return "PROPERTY_AVAILABILITY"
    if _phones_from_text(raw, "EXPLICIT_MESSAGE_CONTACT") and len(raw.split()) <= 18:
        return "CONTACT_ONLY"
    if NOISE_RE.search(raw):
        return "NOISE"
    if len(raw.strip()) < 80:
        return "FRAGMENT"
    return "NOISE"

def _transaction(span, source, content_type):
    raw = str(span or "")
    src = str(source or "")
    if content_type == "REQUIREMENT":
        if RENT_RE.search(raw):
            return "RENT"
        if BUY_REQ_RE.search(raw) or SALE_RE.search(raw):
            return "SALE"
        if RENT_RE.search(src) and not BUY_REQ_RE.search(src):
            return "RENT"
        return "UNKNOWN"

    sale = bool(SALE_RE.search(raw))
    rented = bool(RENTED_RE.search(raw))
    rent_offer = bool(re.search(r"\bFOR\s+RENT\b|\bAVAILABLE\s+FOR\s+RENT\b", raw, re.I))
    if sale and rented:
        return "BOTH"
    if sale:
        return "SALE"
    if rent_offer:
        return "RENT"

    # Strong source header inheritance only.
    pos = src.find(raw) if raw and raw in src else -1
    prefix = src[:pos] if pos >= 0 else src[:300]
    if re.search(r"\b(?:PREMIUM\s+PROPERTIES\s+FOR\s+SALE|FOR\s+SALE|SALE\s+INVENTORY)\b", prefix, re.I):
        return "SALE"
    if re.search(r"\b(?:RENTAL\s+AVAILABLE|NEW\s+RENTAL\s+INVENTORY|FOR\s+RENT)\b", prefix, re.I):
        return "RENT"

    try:
        p = foundation._v16_enrich_proposal(raw)
        hint = str(p.get("transaction_type_hint") or "UNKNOWN").upper()
        if hint in {"SALE", "RENT", "BOTH"}:
            return hint
    except Exception:
        pass
    return "UNKNOWN"

def _contact_prediction(engine, row, base):
    span = str(row.get("span_text") or "")
    source_text = str(row.get("source_raw_text") or "")

    explicit = _phones_from_text(span, "EXPLICIT_MESSAGE_CONTACT")
    if explicit:
        return explicit, {"stage": "EXPLICIT_MESSAGE_CONTACT"}

    source_contacts = _phones_from_text(source_text, "SOURCE_SHARED_CONTACT")
    unique = {c["phone"]: c for c in source_contacts}
    if 1 <= len(unique) <= 4:
        return list(unique.values()), {
            "stage": "SOURCE_SHARED_CONTACT",
            "source_phone_count": len(unique),
        }

    source_payload = {
        "source_message_id": row.get("source_message_id"),
        "source_table": row.get("source_table"),
        "source_row_ref": row.get("source_row_ref"),
        "source_raw_text": source_text,
        "raw_text": source_text,
        "source_metadata": row.get("source_metadata") or {},
    }
    proposal = dict(base)
    proposal["contacts"] = []
    try:
        recovered = foundation._v19g_live_upstream_sender_contact(engine, proposal, source_payload)
        return recovered.get("contacts") or [], {
            "stage": recovered.get("sender_lineage_resolution_stage")
                     or recovered.get("sender_lineage_status")
                     or "WHATSAPP_SENDER",
            "sender_lineage_status": recovered.get("sender_lineage_status"),
        }
    except Exception as exc:
        return [], {"stage": "SENDER_RECOVERY_ERROR", "error": f"{type(exc).__name__}: {exc}"[:300]}

def _predict(engine, row):
    span = str(row.get("span_text") or "")
    source = str(row.get("source_raw_text") or "")
    try:
        p = foundation._v16_enrich_proposal(span)
    except Exception:
        p = {}

    content = _content_type(span)
    tx = _transaction(span, source, content)
    city = _explicit_city(span, source)
    locality = _sector_locality(span)
    if not locality:
        candidate = p.get("locality_hint")
        if candidate and _literal_supported(candidate, span, source):
            locality = candidate
    project = _project(span, source, p)

    # Zero-tolerance grounding guard.
    if city and not _literal_supported(city, span, source):
        city = None
    if project and not _literal_supported(project, span, source):
        project = None

    base = {
        "content_type": content,
        "transaction_type": tx,
        "city": city,
        "locality": locality,
        "project_name": project,
        "areas": p.get("areas") or [],
        "money_mentions": p.get("money_mentions") or [],
        "contacts": [],
    }
    contacts, resolution = _contact_prediction(engine, row, base)
    base["contacts"] = contacts
    base["contact_resolution"] = resolution
    return base

def _gold(row):
    return {
        "content_type": row.get("content_type"),
        "transaction_type": row.get("transaction_type"),
        "city": row.get("city"),
        "locality": row.get("locality"),
        "project_name": row.get("project_name"),
        "areas": _loads(row.get("areas"), []),
        "money_mentions": _loads(row.get("money_mentions"), []),
        "contacts": _loads(row.get("contacts"), []),
    }

def _compare(engine, row):
    gold = _gold(row)
    pred = _predict(engine, row)
    checks = {
        k: _norm(gold.get(k)) == _norm(pred.get(k))
        for k in ("content_type", "transaction_type", "city", "locality", "project_name")
    }
    gp = _phone_set(gold.get("contacts"))
    pp = _phone_set(pred.get("contacts"))
    checks["contacts"] = gp == pp if gp else not pp
    checks["areas_presence"] = bool(gold.get("areas")) == bool(pred.get("areas"))
    checks["money_presence"] = bool(gold.get("money_mentions")) == bool(pred.get("money_mentions"))

    unsupported = []
    for key in ("city", "locality", "project_name"):
        if pred.get(key) and not gold.get(key):
            unsupported.append({"field": key, "predicted": pred.get(key)})

    mismatches = [k for k, ok in checks.items() if not ok]
    return gold, pred, {
        "checks": checks,
        "mismatches": mismatches,
        "unsupported_inference": unsupported,
        "gold_phones": gp,
        "predicted_phones": pp,
        "case_score": round(sum(bool(v) for v in checks.values()) / len(checks), 4),
    }

def _snapshot_rows(engine):
    with engine.connect() as conn:
        snap = conn.execute(
            text("SELECT snapshot_payload FROM alliance_gold_dataset_snapshots WHERE snapshot_version=:v"),
            {"v": SNAPSHOT_VERSION},
        ).first()
    rows = _loads(snap[0] if snap else None, [])
    if len(rows) != 100:
        raise HTTPException(409, "Frozen GOLD_V1_100 snapshot missing or not exactly 100 cases.")
    return rows

def _augment_lineage(engine, rows):
    out = []
    with engine.connect() as conn:
        for row in rows:
            d = dict(row)
            rec = conn.execute(
                text(
                    "SELECT source_row_ref,source_metadata FROM alliance_gold_source_messages "
                    "WHERE source_message_id=:sid"
                ),
                {"sid": str(d.get("source_message_id"))},
            ).mappings().first()
            d["source_row_ref"] = rec.get("source_row_ref") if rec else None
            d["source_metadata"] = rec.get("source_metadata") if rec else {}
            out.append(d)
    return out

def run_benchmark(engine):
    _install(engine)
    gold_v2.freeze_gold_v1(engine)
    gold_v2._seed_normalization_rules(engine)
    rows = _augment_lineage(engine, _snapshot_rows(engine))

    run_id = str(uuid.uuid4())
    hits, totals, errors = {}, {}, {}
    unsupported_count = 0
    boundary_correct = 0
    weakest = []

    with engine.begin() as conn:
        for row in rows:
            gold, pred, comp = _compare(engine, row)
            conn.execute(
                text(
                    """
                    INSERT INTO alliance_gold_benchmark_cases
                    (case_id,run_id,snapshot_version,span_id,gold_label,brain_prediction,comparison)
                    VALUES (:cid,:rid,:sv,:sid,CAST(:g AS jsonb),CAST(:p AS jsonb),CAST(:c AS jsonb))
                    """
                ),
                {
                    "cid": str(uuid.uuid4()), "rid": run_id, "sv": SNAPSHOT_VERSION,
                    "sid": row["span_id"],
                    "g": json.dumps(_safe(gold), ensure_ascii=False),
                    "p": json.dumps(_safe(pred), ensure_ascii=False),
                    "c": json.dumps(_safe(comp), ensure_ascii=False),
                },
            )

            for k, ok in comp["checks"].items():
                totals[k] = totals.get(k, 0) + 1
                hits[k] = hits.get(k, 0) + int(bool(ok))
            unsupported_count += len(comp["unsupported_inference"])
            if str(row.get("boundary_action") or "").upper() == "CORRECT":
                boundary_correct += 1

            for cat in comp["mismatches"]:
                errors[cat] = errors.get(cat, 0) + 1
                conn.execute(
                    text(
                        """
                        INSERT INTO alliance_gold_v21_error_catalog
                        (error_id,run_id,span_id,category,gold_value,predicted_value,evidence_excerpt)
                        VALUES (:eid,:rid,:sid,:cat,CAST(:gv AS jsonb),CAST(:pv AS jsonb),:ev)
                        """
                    ),
                    {
                        "eid": str(uuid.uuid4()), "rid": run_id, "sid": row["span_id"],
                        "cat": cat,
                        "gv": json.dumps(_safe(gold.get(cat)), ensure_ascii=False),
                        "pv": json.dumps(_safe(pred.get(cat)), ensure_ascii=False),
                        "ev": str(row.get("span_text") or "")[:1000],
                    },
                )

            weakest.append({
                "span_id": row.get("span_id"),
                "score": comp["case_score"],
                "mismatches": comp["mismatches"],
                "unsupported_inference": comp["unsupported_inference"],
                "evidence": str(row.get("span_text") or "")[:350],
                "contact_resolution": pred.get("contact_resolution"),
            })

        metrics = {k: round(hits[k] / max(1, totals[k]), 4) for k in totals}
        metrics["boundary_acceptance_rate"] = round(boundary_correct / 100, 4)
        metrics["false_inference_events"] = unsupported_count
        metrics["false_inference_rate_per_case"] = round(unsupported_count / 100, 4)
        metrics["overall_field_score"] = round(sum(hits.values()) / max(1, sum(totals.values())), 4)

        thresholds = {
            "contacts": 0.95,
            "locality": 0.90,
            "content_type": 0.92,
            "project_name": 0.90,
            "transaction_type": 0.90,
            "city": 0.95,
            "areas_presence": 0.95,
            "money_presence": 0.97,
            "overall_field_score": 0.92,
        }
        threshold_failures = {
            k: {"actual": metrics.get(k), "required": required}
            for k, required in thresholds.items()
            if metrics.get(k, 0) < required
        }
        failures = []
        if unsupported_count:
            failures.append({
                "metric": "UNSUPPORTED_GEOGRAPHY_OR_PROJECT_INFERENCE",
                "count": unsupported_count,
            })
        promotion_ready = unsupported_count == 0 and not threshold_failures

        conn.execute(
            text(
                """
                INSERT INTO alliance_gold_evaluation_runs
                (run_id,engine_version,dataset_snapshot,metrics,zero_tolerance_failures,passed)
                VALUES (:rid,:ev,CAST(:ds AS jsonb),CAST(:m AS jsonb),CAST(:f AS jsonb),:passed)
                """
            ),
            {
                "rid": run_id, "ev": VERSION,
                "ds": json.dumps({
                    "snapshot_version": SNAPSHOT_VERSION,
                    "gold_count": 100,
                    "benchmark_version": BENCHMARK_VERSION,
                    "snapshot_immutable": True,
                }),
                "m": json.dumps(metrics),
                "f": json.dumps(failures),
                "passed": promotion_ready,
            },
        )

    weakest = sorted(weakest, key=lambda x: (x["score"], -len(x["mismatches"])))[:15]
    return {
        "status": "PASS",
        "version": VERSION,
        "run_id": run_id,
        "snapshot_version": SNAPSHOT_VERSION,
        "snapshot_unchanged": True,
        "gold_count": 100,
        "metrics": metrics,
        "error_counts": dict(sorted(errors.items(), key=lambda kv: kv[1], reverse=True)),
        "zero_tolerance_failures": failures,
        "threshold_failures": threshold_failures,
        "promotion_ready": promotion_ready,
        "weakest_cases": weakest,
        "production_write_permission": False,
        "production_writes": 0,
    }

def status(engine):
    _install(engine)
    with engine.connect() as conn:
        latest = conn.execute(
            text(
                """
                SELECT run_id,engine_version,metrics,zero_tolerance_failures,passed,created_at
                FROM alliance_gold_evaluation_runs
                WHERE engine_version=:ev
                ORDER BY created_at DESC LIMIT 1
                """
            ),
            {"ev": VERSION},
        ).mappings().first()
    return _safe({
        "status": "PASS",
        "version": VERSION,
        "mode": MODE,
        "snapshot_version": SNAPSHOT_VERSION,
        "snapshot_immutable": True,
        "latest_benchmark": dict(latest) if latest else None,
        "production_write_permission": False,
        "production_writes": 0,
    })

DASHBOARD = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alliance Property Brain - Foundation 2.1</title>
<style>
body{font-family:Arial,sans-serif;background:#f1ebe1;color:#29241f;margin:0}
main{max-width:1150px;margin:28px auto;padding:24px}
.card{background:white;border-radius:14px;padding:20px;margin:14px 0;box-shadow:0 2px 9px #00000012}
button{padding:12px 18px;border:0;border-radius:9px;cursor:pointer}
.primary{background:#29241f;color:#fff}
pre{white-space:pre-wrap;overflow:auto;background:#f8f4ee;padding:14px;border-radius:9px}
</style>
</head>
<body><main>
<h1>Alliance Property Brain - Foundation 2.1</h1>
<p>Frozen Gold V1 benchmark. Reuses shared contacts and WhatsApp sender lineage. Production writes remain disabled.</p>
<div class="card">
<button class="primary" onclick="runBench()">Run Foundation 2.1 Benchmark</button>
<button onclick="refreshStatus()">Refresh Status</button>
</div>
<div class="card"><h3>Status</h3><pre id="status">Loading...</pre></div>
<div class="card"><h3>Benchmark Result</h3><pre id="result">Not run yet.</pre></div>
<script>
async function api(path,method="GET"){
 const r=await fetch(path,{method,headers:{"Content-Type":"application/json"}});
 const t=await r.text(); let d={}; try{d=JSON.parse(t)}catch(e){d={raw:t}}
 if(!r.ok) throw new Error(d.detail||d.raw||("HTTP "+r.status)); return d;
}
async function refreshStatus(){
 try{document.getElementById("status").textContent=JSON.stringify(await api("/api/property-brain/gold-v21/status"),null,2)}
 catch(e){document.getElementById("status").textContent="ERROR: "+e.message}
}
async function runBench(){
 document.getElementById("result").textContent="Running exact frozen Gold V1 benchmark...";
 try{
   const d=await api("/api/property-brain/gold-v21/benchmark","POST");
   document.getElementById("result").textContent=JSON.stringify(d,null,2); await refreshStatus();
 }catch(e){document.getElementById("result").textContent="ERROR: "+e.message}
}
refreshStatus();
</script>
</main></body></html>
"""

def register(core):
    engine = _engine(core)
    app = _app(core)
    _install(engine)

    if not foundation._route_exists(app, "/api/property-brain/gold-v21/status"):
        @app.get("/api/property-brain/gold-v21/status")
        def gold_v21_status():
            return status(engine)

    if not foundation._route_exists(app, "/api/property-brain/gold-v21/benchmark"):
        @app.post("/api/property-brain/gold-v21/benchmark")
        def gold_v21_benchmark(payload: Dict[str, Any] = Body(default={})):
            return run_benchmark(engine)

    if not foundation._route_exists(app, "/property-brain/gold-v21"):
        @app.get("/property-brain/gold-v21", response_class=HTMLResponse)
        def gold_v21_dashboard():
            return HTMLResponse(DASHBOARD)

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "mode": MODE,
        "dashboard": "/property-brain/gold-v21",
        "benchmark_route": "/api/property-brain/gold-v21/benchmark",
        "snapshot_immutable": True,
        "production_write_permission": False,
        "production_writes": 0,
    }
