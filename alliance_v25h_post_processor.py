
import json
from sqlalchemy import text
from fastapi import Request
from fastapi.responses import HTMLResponse

MODULE_VERSION = "2.5.8-POST-PROCESSING-MATCH-INTELLIGENCE"

def _decision(score, hard_rule_pass, legacy_status, legacy_action):
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

def _team_priority(decision, verification_status, confidence, duplicate_type):
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

    if duplicate_type == "POSSIBLE_DUPLICATE":
        score -= 5
    elif duplicate_type in {"EXACT_DUPLICATE", "HIGH_CONF_DUPLICATE"}:
        score -= 50

    score = max(0, min(100, score))

    if score >= 80:
        return score, "CALL_FIRST"
    if score >= 60:
        return score, "VERIFY_AND_CALL"
    if score >= 40:
        return score, "REVIEW"
    return score, "LOW_PRIORITY"

def _duplicate_map(engine, source_ids):
    out = {}
    uuid_ids = []

    import re
    for x in source_ids:
        if re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            str(x or "")
        ):
            uuid_ids.append(str(x))

    if not uuid_ids:
        return out

    for start in range(0, len(uuid_ids), 100):
        chunk = uuid_ids[start:start+100]
        bind = []
        params = {"version": "2.4.7A-DUPLICATE-SAFETY-CALIBRATION"}

        for i, rid in enumerate(chunk):
            k = f"id{i}"
            bind.append(f"CAST(:{k} AS uuid)")
            params[k] = rid

        with engine.connect() as c:
            rows = c.execute(text(f"""
              SELECT listing_id,duplicate_type,suppress_from_matcher,duplicate_confidence
              FROM ai_whatsapp_entity_resolution
              WHERE model_version=:version
                AND listing_id IN ({",".join(bind)})
            """), params).mappings().all()

        for row in rows:
            out[str(row["listing_id"])] = dict(row)

    return out

def post_process_matches(engine, code, minimum_score=0):
    with engine.connect() as c:
        requirement = c.execute(text("""
          SELECT *
          FROM ai_requirement_index
          WHERE requirement_code=:x OR source_record_id=:x
          ORDER BY requirement_index_id DESC
          LIMIT 1
        """), {"x": code}).mappings().first()

    if not requirement:
        return {"detail": "Requirement not indexed"}

    with engine.connect() as c:
        rows = [dict(r._mapping) for r in c.execute(text("""
          SELECT
            m.match_score,
            m.status,
            m.action,
            m.hard_rule_pass,
            m.rejection_reasons,
            m.positive_reasons,
            m.location_score,
            m.area_score,
            m.rent_score,
            m.type_score,
            m.floor_score,
            m.frontage_score,
            m.suitability_score,
            m.matcher_version,
            p.property_name,
            p.location_raw,
            p.area_min_sqft,
            p.area_max_sqft,
            p.rent_psf_month,
            p.monthly_rent,
            p.transaction_type,
            p.canonical_property_type,
            p.floor_raw,
            p.frontage_ft,
            p.source_type,
            p.source_name,
            p.verification_status,
            p.data_confidence_score,
            p.source_record_id
          FROM ai_match_v2 m
          JOIN ai_requirement_index r
            ON r.requirement_index_id=m.requirement_index_id
          JOIN ai_property_match_index p
            ON p.match_index_id=m.match_index_id
          WHERE (r.requirement_code=:x OR r.source_record_id=:x)
            AND m.match_score>=:s
          ORDER BY m.match_score DESC
          LIMIT 500
        """), {"x": code, "s": float(minimum_score or 0)}).fetchall()]

    dup_map = _duplicate_map(
        engine,
        [r.get("source_record_id") for r in rows]
    )

    processed = []
    suppressed_duplicates = 0
    possible_duplicates = 0

    for row in rows:
        source_id = str(row.get("source_record_id") or "")
        dup = dup_map.get(source_id, {})
        duplicate_type = dup.get("duplicate_type") or "UNIQUE"
        duplicate_confidence = dup.get("duplicate_confidence")
        suppress = bool(dup.get("suppress_from_matcher") or False)

        if suppress and duplicate_type in {"EXACT_DUPLICATE", "HIGH_CONF_DUPLICATE"}:
            suppressed_duplicates += 1
            continue

        if duplicate_type == "POSSIBLE_DUPLICATE":
            possible_duplicates += 1

        decision, next_action = _decision(
            row.get("match_score"),
            bool(row.get("hard_rule_pass")),
            row.get("status"),
            row.get("action"),
        )

        team_priority_score, team_action = _team_priority(
            decision,
            row.get("verification_status"),
            row.get("data_confidence_score"),
            duplicate_type,
        )

        item = {
            "match_score": row.get("match_score"),
            "v25_decision": decision,
            "v25_action": next_action,
            "team_priority_score": team_priority_score,
            "team_action": team_action,

            "legacy_status": row.get("status"),
            "legacy_action": row.get("action"),
            "hard_rule_pass": row.get("hard_rule_pass"),

            "property_name": row.get("property_name"),
            "location": row.get("location_raw"),
            "area_min_sqft": row.get("area_min_sqft"),
            "area_max_sqft": row.get("area_max_sqft"),
            "rent_psf_month": row.get("rent_psf_month"),
            "monthly_rent": row.get("monthly_rent"),
            "transaction_type": row.get("transaction_type"),
            "canonical_property_type": row.get("canonical_property_type"),
            "floor": row.get("floor_raw"),
            "frontage_ft": row.get("frontage_ft"),

            "verification_status": row.get("verification_status"),
            "data_confidence": row.get("data_confidence_score"),

            "duplicate_type": duplicate_type,
            "duplicate_confidence": duplicate_confidence,
            "duplicate_suppressed": suppress,

            "source_type": row.get("source_type"),
            "source": row.get("source_name"),
            "source_record_id": row.get("source_record_id"),

            "reasons": row.get("rejection_reasons") or [],
            "positive_reasons": row.get("positive_reasons") or [],

            "component_scores": {
                "location": row.get("location_score"),
                "area": row.get("area_score"),
                "rent": row.get("rent_score"),
                "type": row.get("type_score"),
                "floor": row.get("floor_score"),
                "frontage": row.get("frontage_score"),
                "suitability": row.get("suitability_score"),
            },

            "core_matcher_version": row.get("matcher_version"),
        }

        processed.append(item)

    processed.sort(
        key=lambda x: (
            x["team_priority_score"],
            float(x["match_score"] or 0)
        ),
        reverse=True
    )

    strong = [x for x in processed if x["v25_decision"] == "STRONG_MATCH"]
    good = [x for x in processed if x["v25_decision"] == "GOOD_MATCH"]
    possible = [x for x in processed if x["v25_decision"] == "POSSIBLE_MATCH"]
    rejected = [x for x in processed if x["v25_decision"] == "REJECT"]

    inventory_gap = None
    if not (strong or good or possible):
        inventory_gap = {
            "status": "OPEN",
            "requirement_code": requirement["requirement_code"],
            "company_name": requirement["company_name"],
            "locations": requirement["preferred_locations_raw"],
            "transaction_type": requirement["transaction_type"],
            "minimum_area_sqft": requirement["minimum_area_sqft"],
            "maximum_area_sqft": requirement["maximum_area_sqft"],
            "minimum_frontage_ft": requirement["minimum_frontage_ft"],
            "required_floor": requirement["required_floor"],
            "suitable_for": requirement["suitable_for"],
            "reason": "No actionable post-processed V2.5 match found from stable core matcher results."
        }

    return {
        "version": MODULE_VERSION,
        "requirement_code": requirement["requirement_code"],
        "company_name": requirement["company_name"],
        "execution_mode": "READ_ONLY_POST_PROCESSOR",
        "core_matcher_untouched": True,

        "summary": {
            "rows_read": len(rows),
            "rows_returned": len(processed),
            "strong_matches": len(strong),
            "good_matches": len(good),
            "possible_matches": len(possible),
            "rejected": len(rejected),
            "possible_duplicates": possible_duplicates,
            "suppressed_duplicates": suppressed_duplicates,
        },

        "matches": processed[:100],
        "inventory_gap": inventory_gap,
    }

def register_v25h_routes(core):
    app, engine = core.app, core.engine

    @app.get("/api/v2/intelligence/v25h/matches/{code}")
    def results(
        code: str,
        req: Request,
        minimum_score: float = 0
    ):
        if hasattr(core, "need_login"):
            core.need_login(req)
        return post_process_matches(
            engine,
            code,
            minimum_score
        )

    @app.get("/v2/match-intelligence", response_class=HTMLResponse)
    def dashboard(req: Request):
        if hasattr(core, "need_login"):
            core.need_login(req)

        return HTMLResponse("""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>V2.5H Match Intelligence</title>
</head>
<body style="font-family:Arial;background:#f5f7fa">
<div style="max-width:1050px;margin:30px auto;background:white;padding:28px;border-radius:14px">
<h1>V2.5H Post-Processing Match Intelligence</h1>
<p>The stable core matcher is untouched.</p>
<p>This layer reads existing <b>ai_match_v2</b> results and adds V2.5 decisions, duplicate context and team priority.</p>
<p>Decision bands:</p>
<ul>
<li>90–100: STRONG_MATCH</li>
<li>80–89: GOOD_MATCH</li>
<li>70–79: POSSIBLE_MATCH</li>
<li>Hard-rule failure: REJECT</li>
</ul>
</div>
</body>
</html>""")

    return app
