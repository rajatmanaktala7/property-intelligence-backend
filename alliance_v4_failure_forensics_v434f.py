from __future__ import annotations

import html
import json
from fastapi.responses import HTMLResponse

import alliance_property_brain_foundation_v1 as foundation
import alliance_cre_academy_v402 as v402
import alliance_autonomous_student_v430 as v430
import alliance_autonomous_student_v431 as v431
import alliance_autonomous_student_v432 as v432
import alliance_autonomous_student_v433 as v433
import alliance_autonomous_student_v434 as v434

VERSION = "4.3.4F-ALLIANCE-V4-FAILURE-FORENSICS"
MODE = "READ_ONLY_EXACT_FAILURE_FORENSICS_NO_STUDENT_CHANGES_NO_V5_FREEZE"

def _engine(core):
    return foundation._engine_from_core(core)

def _app(core):
    return getattr(core, "app", None) or core

def _safe(v):
    return foundation._json_safe(v)

def _pred(fn, raw):
    try:
        p = fn(raw)
        return {
            "class": p.get("class"),
            "transaction": p.get("transaction"),
            "ownership": p.get("ownership"),
            "confidence": p.get("confidence"),
            "rule": p.get("rule"),
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

def _failure_family(field, truth, student):
    if field == "class" and truth == "PROPERTY_AVAILABILITY" and student == "REQUIREMENT":
        return "DEMAND_OVERCLAIM_ON_PROPERTY_OFFER"
    if field == "class" and truth == "REQUIREMENT" and student == "PROPERTY_AVAILABILITY":
        return "OFFER_LEAKAGE_INTO_DEMAND"
    if field == "transaction" and truth == "UNKNOWN" and student == "RENT":
        return "RENT_RELATION_LEAKAGE"
    if field == "transaction" and truth == "RENT" and student == "UNKNOWN":
        return "MISSED_RENT_RELATION"
    if field == "transaction" and truth == "SALE" and student == "UNKNOWN":
        return "MISSED_SALE_RELATION"
    return "OTHER"

def report(engine):
    rows = v430._v4_truth_rows(engine)
    failures = []
    for item in rows:
        x = item["row"]
        raw = x["raw_text"]
        truth = {
            "class": item["truth"][0],
            "transaction": item["truth"][1],
            "ownership": item["truth"][2],
        }
        student = v434.predict_message(raw)
        mismatches = []
        for field in ("class", "transaction", "ownership"):
            if student[field] != truth[field]:
                mismatches.append({
                    "field": field,
                    "truth": truth[field],
                    "student": student[field],
                    "family": _failure_family(field, truth[field], student[field]),
                })
        if not mismatches:
            continue

        first_clause = v434._first_clause(raw)
        demand_first = v434._demand_first(raw)
        direct_rent = v434._direct_rent_requirement(raw)
        offer = v434._explicit_offer_anchors(raw)
        req433, offer433 = v433._requirement_grammar(raw)
        sr433, sp433, clauses433 = v433._strict_requirement_tx(raw)
        rf431 = v431._repair_features(raw)
        mixed, sale_headers, rent_headers = v432._mixed_parent(raw)

        failures.append({
            "ordinal": x["ordinal"],
            "audit_id": str(x["audit_id"]),
            "blind_id": str(x["blind_id"]),
            "truth_source": item.get("truth_source"),
            "raw_text": raw,
            "truth": truth,
            "student_v434": {
                "class": student["class"],
                "transaction": student["transaction"],
                "ownership": student["ownership"],
                "confidence": student.get("confidence"),
                "rule": student.get("rule"),
            },
            "mismatches": mismatches,
            "layers": {
                "v402": _pred(v402.predict_message, raw),
                "v430": _pred(v430.predict_message, raw),
                "v431": _pred(v431.predict_message, raw),
                "v432": _pred(v432.predict_message, raw),
                "v433": _pred(v433.predict_message, raw),
                "v434": _pred(v434.predict_message, raw),
            },
            "forensic_features": {
                "first_clause": first_clause,
                "v434_demand_first": demand_first,
                "v434_direct_rent_requirement": direct_rent,
                "v434_explicit_offer_anchors_anywhere": offer,
                "v433_requirement_grammar_anywhere": req433,
                "v433_offer_grammar_anywhere": offer433,
                "v433_strong_rent_same_clause": sr433,
                "v433_strong_purchase_same_clause": sp433,
                "v433_clauses": clauses433,
                "v431_repair_features": rf431,
                "v432_mixed_parent": {
                    "mixed": mixed,
                    "sale_headers": sale_headers,
                    "rent_headers": rent_headers,
                },
            },
        })

    return {
        "version": VERSION,
        "mode": MODE,
        "total_v4_truth_cases": len(rows),
        "failure_cases": len(failures),
        "failures": failures,
        "diagnostic_policy": (
            "Read-only. Exact frozen V4 truth is compared with each student layer. "
            "No student rule is changed, no V5 case is selected/frozen, and no Gold/production/WhatsApp write occurs."
        ),
        "next_step": (
            "Use this packet to build one generic relation/ownership repair. "
            "Do not patch from aggregate scores alone."
        ),
        "safety": {
            "student_tuning": 0,
            "v5_freeze": 0,
            "production_writes": 0,
            "whatsapp_writes": 0,
            "gold_mutations": 0,
        },
    }

def _dashboard(engine):
    s = report(engine)
    return f"""<!doctype html><html><head><meta charset='utf-8'>
    <title>Alliance V4 Failure Forensics 4.3.4F</title>
    <style>
      body{{font-family:Arial;margin:30px;background:#f6f7fb;color:#172033;max-width:1250px}}
      .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}
      .card{{background:#fff;padding:18px;border-radius:14px;box-shadow:0 2px 10px #0001}}
      strong{{display:block;font-size:25px;margin-top:8px}}
      .ok{{background:#e8f8ee;padding:16px;border-radius:10px;font-weight:700;margin:18px 0}}
      pre{{white-space:pre-wrap;background:#101624;color:#eef3ff;padding:16px;border-radius:10px;line-height:1.4}}
    </style></head><body>
    <h1>Alliance V4 Failure Forensics 4.3.4F</h1>
    <p>Exact frozen V4 failures with raw text, truth, every student layer, clause splits and relation signals. Read-only diagnostic only.</p>
    <div class='grid'>
      <div class='card'>V4 Truth Cases<strong>{s['total_v4_truth_cases']}</strong></div>
      <div class='card'>Exact Failures<strong>{s['failure_cases']}</strong></div>
      <div class='card'>Student Changes<strong>0</strong></div>
      <div class='card'>V5 Frozen<strong>0</strong></div>
    </div>
    <div class='ok'>No new student patch is being guessed from aggregate scores. V5 remains untouched.</div>
    <h2>Machine Forensic Packet</h2>
    <pre>{html.escape(json.dumps(_safe(s), ensure_ascii=False, indent=2))}</pre>
    </body></html>"""

def register(core):
    engine = _engine(core)
    app = _app(core)

    if not foundation._route_exists(app, "/api/property-brain/autonomous-v434-forensics/status"):
        @app.get("/api/property-brain/autonomous-v434-forensics/status")
        def status_v434_forensics():
            return report(engine)

    if not foundation._route_exists(app, "/property-brain/autonomous-v434-forensics"):
        @app.get("/property-brain/autonomous-v434-forensics", response_class=HTMLResponse)
        def page_v434_forensics():
            return HTMLResponse(_dashboard(engine))

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": "/property-brain/autonomous-v434-forensics",
        "student_tuning": 0,
        "v5_freeze": 0,
        "production_writes": 0,
        "whatsapp_writes": 0,
        "gold_mutations": 0,
    }
