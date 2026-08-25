
import json
from sqlalchemy import text
from fastapi import Request
from fastapi.responses import HTMLResponse

MODULE_VERSION = "2.5.9-ULTRA-LIGHT-POST-PROCESSOR"

def _decision(score, hard_rule_pass, legacy_status):
    try:
        s = float(score or 0)
    except Exception:
        s = 0.0

    if not hard_rule_pass or legacy_status == "REJECTED":
        return "REJECT", "DO_NOT_SEND"
    if s >= 90:
        return "STRONG_MATCH", "VERIFY_CONTACT"
    if s >= 80:
        return "GOOD_MATCH", "REVIEW"
    if s >= 70:
        return "POSSIBLE_MATCH", "REVIEW"
    return "WEAK", "DO_NOT_SEND"

def _team_priority(decision, verification_status, confidence):
    score = 0
    if decision == "STRONG_MATCH":
        score += 50
    elif decision == "GOOD_MATCH":
        score += 35
    elif decision == "POSSIBLE_MATCH":
        score += 20
    elif decision == "REJECT":
        return 0, "NO_ACTION"

    if str(verification_status or "").upper() == "VERIFIED":
        score += 20

    try:
        c = float(confidence or 0)
    except Exception:
        c = 0

    if c >= 90:
        score += 20
    elif c >= 75:
        score += 10

    score = max(0, min(100, score))
    if score >= 80:
        return score, "CALL_FIRST"
    if score >= 60:
        return score, "VERIFY_AND_CALL"
    if score >= 40:
        return score, "REVIEW"
    return score, "LOW_PRIORITY"

def post_process_matches(engine, code, minimum_score=0, limit=100):
    limit = max(1, min(int(limit or 100), 100))
    minimum_score = float(minimum_score or 0)

    # Stage 1: requirement only.
    with engine.connect() as c:
        req = c.execute(text("""
          SELECT
            requirement_index_id,
            requirement_code,
            company_name,
            preferred_locations_raw,
            transaction_type,
            minimum_area_sqft,
            maximum_area_sqft,
            minimum_frontage_ft,
            required_floor,
            suitable_for
          FROM ai_requirement_index
          WHERE requirement_code=:x OR source_record_id=:x
          ORDER BY requirement_index_id DESC
          LIMIT 1
        """), {"x": code}).mappings().first()

    if not req:
        return {
            "version": MODULE_VERSION,
            "status": "NOT_FOUND",
            "detail": "Requirement not indexed",
        }

    rid = req["requirement_index_id"]

    # Stage 2: read only stored match rows for this one requirement.
    with engine.connect() as c:
        match_rows = c.execute(text("""
          SELECT
            match_index_id,
            match_score,
            status,
            action,
            hard_rule_pass,
            rejection_reasons,
            positive_reasons,
            location_score,
            area_score,
            rent_score,
            type_score,
            floor_score,
            frontage_score,
            suitability_score,
            matcher_version
          FROM ai_match_v2
          WHERE requirement_index_id=:rid
            AND match_score>=:minimum_score
          ORDER BY match_score DESC
          LIMIT :lim
        """), {
            "rid": rid,
            "minimum_score": minimum_score,
            "lim": limit,
        }).mappings().all()

    if not match_rows:
        return {
            "version": MODULE_VERSION,
            "requirement_code": req["requirement_code"],
            "company_name": req["company_name"],
            "execution_mode": "ULTRA_LIGHT_STORED_RESULTS",
            "core_matcher_untouched": True,
            "summary": {
                "stored_match_rows": 0,
                "rows_returned": 0,
            },
            "matches": [],
            "inventory_gap": {
                "status": "OPEN",
                "reason": "No stored ai_match_v2 rows found for this requirement."
            }
        }

    # Stage 3: fetch only the properties referenced by those match rows.
    mids = [int(r["match_index_id"]) for r in match_rows]
    bind = []
    params = {}
    for i, mid in enumerate(mids):
        k = f"m{i}"
        bind.append(f":{k}")
        params[k] = mid

    with engine.connect() as c:
        prop_rows = c.execute(text(f"""
          SELECT
            match_index_id,
            property_name,
            location_raw,
            area_min_sqft,
            area_max_sqft,
            rent_psf_month,
            monthly_rent,
            transaction_type,
            canonical_property_type,
            floor_raw,
            frontage_ft,
            source_type,
            source_name,
            verification_status,
            data_confidence_score,
            source_record_id
          FROM ai_property_match_index
          WHERE match_index_id IN ({",".join(bind)})
        """), params).mappings().all()

    prop_map = {int(p["match_index_id"]): dict(p) for p in prop_rows}

    processed = []

    for m0 in match_rows:
        m = dict(m0)
        p = prop_map.get(int(m["match_index_id"]), {})

        decision, v25_action = _decision(
            m.get("match_score"),
            bool(m.get("hard_rule_pass")),
            m.get("status"),
        )

        priority_score, team_action = _team_priority(
            decision,
            p.get("verification_status"),
            p.get("data_confidence_score"),
        )

        processed.append({
            "match_score": m.get("match_score"),
            "v25_decision": decision,
            "v25_action": v25_action,
            "team_priority_score": priority_score,
            "team_action": team_action,

            "legacy_status": m.get("status"),
            "legacy_action": m.get("action"),
            "hard_rule_pass": m.get("hard_rule_pass"),

            "property_name": p.get("property_name"),
            "location": p.get("location_raw"),
            "area_min_sqft": p.get("area_min_sqft"),
            "area_max_sqft": p.get("area_max_sqft"),
            "rent_psf_month": p.get("rent_psf_month"),
            "monthly_rent": p.get("monthly_rent"),
            "transaction_type": p.get("transaction_type"),
            "canonical_property_type": p.get("canonical_property_type"),
            "floor": p.get("floor_raw"),
            "frontage_ft": p.get("frontage_ft"),

            "verification_status": p.get("verification_status"),
            "data_confidence": p.get("data_confidence_score"),

            "source_type": p.get("source_type"),
            "source": p.get("source_name"),
            "source_record_id": p.get("source_record_id"),

            "reasons": m.get("rejection_reasons") or [],
            "positive_reasons": m.get("positive_reasons") or [],

            "component_scores": {
                "location": m.get("location_score"),
                "area": m.get("area_score"),
                "rent": m.get("rent_score"),
                "type": m.get("type_score"),
                "floor": m.get("floor_score"),
                "frontage": m.get("frontage_score"),
                "suitability": m.get("suitability_score"),
            },

            "core_matcher_version": m.get("matcher_version"),
        })

    processed.sort(
        key=lambda x: (
            x["team_priority_score"],
            float(x["match_score"] or 0)
        ),
        reverse=True
    )

    counts = {
        "STRONG_MATCH": 0,
        "GOOD_MATCH": 0,
        "POSSIBLE_MATCH": 0,
        "WEAK": 0,
        "REJECT": 0,
    }
    for x in processed:
        counts[x["v25_decision"]] = counts.get(x["v25_decision"], 0) + 1

    actionable = [
        x for x in processed
        if x["v25_decision"] in {"STRONG_MATCH", "GOOD_MATCH", "POSSIBLE_MATCH"}
    ]

    inventory_gap = None
    if not actionable:
        inventory_gap = {
            "status": "OPEN",
            "requirement_code": req["requirement_code"],
            "company_name": req["company_name"],
            "locations": req["preferred_locations_raw"],
            "transaction_type": req["transaction_type"],
            "minimum_area_sqft": req["minimum_area_sqft"],
            "maximum_area_sqft": req["maximum_area_sqft"],
            "minimum_frontage_ft": req["minimum_frontage_ft"],
            "required_floor": req["required_floor"],
            "suitable_for": req["suitable_for"],
            "reason": "No actionable V2.5 post-processed match in stored stable matcher results."
        }

    return {
        "version": MODULE_VERSION,
        "requirement_code": req["requirement_code"],
        "company_name": req["company_name"],
        "execution_mode": "ULTRA_LIGHT_STORED_RESULTS",
        "core_matcher_untouched": True,
        "duplicate_lookup_in_request": False,
        "summary": {
            "stored_match_rows": len(match_rows),
            "properties_fetched": len(prop_rows),
            "rows_returned": len(processed),
            **counts,
        },
        "matches": processed,
        "inventory_gap": inventory_gap,
    }

def register_v25i_routes(core):
    app, engine = core.app, core.engine

    @app.get("/api/v2/intelligence/v25i/matches/{code}")
    def results(
        code: str,
        req: Request,
        minimum_score: float = 0,
        limit: int = 100,
    ):
        if hasattr(core, "need_login"):
            core.need_login(req)

        try:
            return post_process_matches(
                engine,
                code,
                minimum_score,
                limit,
            )
        except Exception as exc:
            return {
                "version": MODULE_VERSION,
                "status": "ERROR",
                "message": str(exc),
            }

    @app.get("/v2/match-intelligence-lite", response_class=HTMLResponse)
    def dashboard(req: Request):
        if hasattr(core, "need_login"):
            core.need_login(req)

        return HTMLResponse("""<!doctype html>
<html>
<head><meta charset="utf-8"><title>V2.5I Match Intelligence</title></head>
<body style="font-family:Arial;background:#f5f7fa">
<div style="max-width:1000px;margin:30px auto;background:white;padding:28px;border-radius:14px">
<h1>V2.5I Ultra-Light Match Intelligence</h1>
<p>Reads only stored ai_match_v2 rows for one requirement.</p>
<p>No core matcher execution. No duplicate-resolution scan. No large join.</p>
</div>
</body>
</html>""")

    return app
