
import re
import json
from sqlalchemy import text
from fastapi import Request
from fastapi.responses import HTMLResponse

MODULE_VERSION = "2.5.0-PRODUCTION-MATCH-INTELLIGENCE"

def _norm(v):
    return re.sub(r"\s+", " ", str(v or "").strip().lower())

def _tokens(v):
    return {x for x in re.split(r"[^a-z0-9]+", _norm(v)) if len(x) >= 2}

def _location_score(req_loc, prop_loc):
    a = _norm(req_loc)
    b = _norm(prop_loc)
    if not a or not b:
        return 0, ["Location missing"]
    if a == b:
        return 30, ["Exact location match"]
    at = _tokens(a)
    bt = _tokens(b)
    if not at or not bt:
        return 0, ["Location not comparable"]
    inter = len(at & bt)
    union = len(at | bt)
    j = inter / union if union else 0
    if j >= 0.75:
        return 28, ["Strong location overlap"]
    if j >= 0.5:
        return 24, ["Good location overlap"]
    if j >= 0.25:
        return 15, ["Partial location overlap"]
    return 0, ["Location mismatch"]

def _area_score(rmin, rmax, pmin, pmax):
    try:
        rmin = float(rmin) if rmin is not None else None
        rmax = float(rmax) if rmax is not None else None
        pmin = float(pmin) if pmin is not None else None
        pmax = float(pmax) if pmax is not None else None
    except Exception:
        return 0, False, ["Area parse issue"]

    if rmin is None and rmax is None:
        return 20, True, ["Requirement area not specified"]
    if pmin is None and pmax is None:
        return 0, False, ["Property area missing"]
    if rmin is None:
        rmin = rmax
    if rmax is None:
        rmax = rmin
    if pmin is None:
        pmin = pmax
    if pmax is None:
        pmax = pmin

    if pmin <= rmax and pmax >= rmin:
        if rmin <= pmin <= rmax and rmin <= pmax <= rmax:
            return 20, True, ["Area within requirement"]
        return 16, True, ["Area overlaps requirement"]

    if pmin > rmax:
        delta = (pmin - rmax) / max(1, rmax)
    else:
        delta = (rmin - pmax) / max(1, rmin)

    if delta <= 0.10:
        return 12, True, ["Area slightly outside preferred range"]
    if delta <= 0.20:
        return 7, True, ["Area outside preferred range"]
    return 0, False, ["Area materially outside requirement"]

def _type_score(req_type, prop_type, suitable_for):
    r = _norm(req_type)
    p = _norm(prop_type)
    s = _norm(suitable_for)

    if not r:
        return 10, True, ["Requirement type not specified"]
    if not p and not s:
        return 0, False, ["Property type/suitability missing"]

    # Full points if the declared suitability explicitly matches the requirement use.
    if r and s and (r == s or r in s or s in r):
        return 10, True, ["Exact suitability match"]
    if r == p:
        return 10, True, ["Exact property type match"]

    rt = _tokens(r)
    pt = _tokens(p + " " + s)
    if rt & pt:
        return 8, True, ["Property type/suitability aligned"]

    broad_commercial = {"commercial", "retail", "shop", "restaurant", "office", "showroom", "cafe", "hotel", "banquet", "fine", "dine"}
    if (rt & broad_commercial) and (pt & broad_commercial):
        return 5, True, ["Broad commercial compatibility"]

    return 0, False, ["Property type mismatch"]

def _floor_score(req_floor, prop_floor):
    r = _norm(req_floor)
    p = _norm(prop_floor)
    if not r:
        return 5, True, ["Floor not mandatory"]
    if not p:
        return 2, True, ["Floor needs verification"]
    aliases = {
        "gf": "ground", "ground floor": "ground", "ground": "ground",
        "ff": "first", "first floor": "first", "1st": "first",
        "sf": "second", "second floor": "second", "2nd": "second",
    }
    rn = aliases.get(r, r)
    pn = aliases.get(p, p)
    if rn in pn or pn in rn:
        return 5, True, ["Floor aligned"]
    return 0, False, ["Floor mismatch"]

def _frontage_score(req_frontage, prop_frontage):
    try:
        r = float(req_frontage) if req_frontage is not None else None
        p = float(prop_frontage) if prop_frontage is not None else None
    except Exception:
        return 0, False, ["Frontage parse issue"]

    if r is None:
        return 5, True, ["Frontage not mandatory"]
    if p is None:
        return 2, True, ["Frontage needs verification"]
    if p >= r:
        return 5, True, ["Frontage meets requirement"]
    if p >= r * 0.9:
        return 3, True, ["Frontage slightly below requirement"]
    return 0, False, ["Frontage below requirement"]

def _rent_score(req_max_rent, prop_monthly_rent, prop_rent_psf):
    try:
        rr = float(req_max_rent) if req_max_rent is not None else None
        pm = float(prop_monthly_rent) if prop_monthly_rent is not None else None
    except Exception:
        rr = pm = None
    if rr is None:
        return 10, True, ["Rent budget not specified"]
    if pm is None:
        return 5, True, ["Rent needs verification"]
    if pm <= rr:
        return 10, True, ["Rent within budget"]
    if pm <= rr * 1.10:
        return 7, True, ["Rent slightly above budget"]
    if pm <= rr * 1.20:
        return 4, True, ["Rent above preferred budget"]
    return 0, False, ["Rent materially above budget"]

def _confidence_bonus(verification_status, confidence, duplicate_type):
    score = 0
    reasons = []
    if _norm(verification_status) == "verified":
        score += 5
        reasons.append("Verified property")
    try:
        c = float(confidence or 0)
    except Exception:
        c = 0
    if c >= 90:
        score += 5
        reasons.append("High data confidence")
    elif c >= 75:
        score += 3
        reasons.append("Good data confidence")

    if duplicate_type == "POSSIBLE_DUPLICATE":
        score -= 2
        reasons.append("Possible duplicate flagged")
    elif duplicate_type in ("EXACT_DUPLICATE","HIGH_CONF_DUPLICATE"):
        score -= 100
        reasons.append("Suppressed duplicate")
    return score, reasons

def _decision(score, hard_rule_pass):
    if not hard_rule_pass:
        return "REJECT", "DO_NOT_SEND"
    if score >= 90:
        return "STRONG_MATCH", "VERIFY_CONTACT"
    if score >= 80:
        return "GOOD_MATCH", "REVIEW"
    if score >= 70:
        return "POSSIBLE_MATCH", "REVIEW"
    return "WEAK", "DO_NOT_SEND"

def score_match(req, prop):
    reasons = []
    rejection_reasons = []
    hard = True

    req_tx = _norm(req.get("transaction_type"))
    prop_tx = _norm(prop.get("transaction_type"))
    if req_tx and prop_tx and req_tx != prop_tx and prop_tx != "lease_or_sale":
        hard = False
        rejection_reasons.append("Transaction type mismatch")

    loc_score, loc_reasons = _location_score(req.get("locations"), prop.get("location_raw"))
    reasons += loc_reasons
    if loc_score == 0 and req.get("locations"):
        hard = False
        rejection_reasons.append("Location mismatch")

    area_score, area_pass, area_reasons = _area_score(
        req.get("minimum_area_sqft"), req.get("maximum_area_sqft"),
        prop.get("area_min_sqft"), prop.get("area_max_sqft"),
    )
    reasons += area_reasons
    if not area_pass:
        hard = False
        rejection_reasons += [x for x in area_reasons if x not in rejection_reasons]

    type_score, type_pass, type_reasons = _type_score(
        req.get("suitable_for") or req.get("requirement_type"),
        prop.get("canonical_property_type"),
        prop.get("suitable_for"),
    )
    reasons += type_reasons
    if not type_pass:
        rejection_reasons += [x for x in type_reasons if x not in rejection_reasons]

    floor_score, floor_pass, floor_reasons = _floor_score(req.get("required_floor"), prop.get("floor"))
    reasons += floor_reasons
    if not floor_pass:
        hard = False
        rejection_reasons += [x for x in floor_reasons if x not in rejection_reasons]

    frontage_score, frontage_pass, frontage_reasons = _frontage_score(
        req.get("minimum_frontage_ft"), prop.get("frontage_ft")
    )
    reasons += frontage_reasons
    if not frontage_pass:
        hard = False
        rejection_reasons += [x for x in frontage_reasons if x not in rejection_reasons]

    rent_score, rent_pass, rent_reasons = _rent_score(
        req.get("maximum_rent"), prop.get("monthly_rent"), prop.get("rent_psf_month")
    )
    reasons += rent_reasons
    if not rent_pass:
        rejection_reasons += [x for x in rent_reasons if x not in rejection_reasons]

    confidence_bonus, confidence_reasons = _confidence_bonus(
        prop.get("verification_status"),
        prop.get("data_confidence_score"),
        prop.get("duplicate_type"),
    )
    reasons += confidence_reasons

    base = loc_score + area_score + type_score + floor_score + frontage_score + rent_score + confidence_bonus
    score = max(0, min(100, round(base, 2)))
    status, action = _decision(score, hard)

    return {
        "match_score": score,
        "status": status,
        "action": action,
        "hard_rule_pass": hard,
        "rejection_reasons": rejection_reasons,
        "positive_reasons": reasons,
        "location_score": loc_score,
        "area_score": area_score,
        "type_score": type_score,
        "floor_score": floor_score,
        "frontage_score": frontage_score,
        "rent_score": rent_score,
    }

def ensure_schema(engine):
    with engine.begin() as c:
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_v25_match_results(
          requirement_code TEXT NOT NULL,
          source_record_id TEXT NOT NULL,
          match_score NUMERIC(6,2) NOT NULL,
          status TEXT NOT NULL,
          action TEXT NOT NULL,
          hard_rule_pass BOOLEAN NOT NULL,
          rejection_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
          positive_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
          location_score NUMERIC(6,2),
          area_score NUMERIC(6,2),
          type_score NUMERIC(6,2),
          floor_score NUMERIC(6,2),
          frontage_score NUMERIC(6,2),
          rent_score NUMERIC(6,2),
          model_version TEXT NOT NULL,
          evaluated_at TIMESTAMPTZ DEFAULT NOW(),
          PRIMARY KEY(requirement_code,source_record_id)
        )
        """))

def run_match(engine, requirement_code):
    ensure_schema(engine)

    with engine.connect() as c:
        req = c.execute(text("""
          SELECT requirement_code,company_name,preferred_locations AS locations,
                 minimum_area_sqft,maximum_area_sqft,maximum_rent,transaction_type,
                 additional_points,minimum_frontage_ft,required_floor,suitable_for
          FROM ai_requirement_index
          WHERE requirement_code=:code
          LIMIT 1
        """), {"code": requirement_code}).mappings().one_or_none()

    if not req:
        return {"detail": "Requirement not indexed"}

    with engine.connect() as c:
        props = c.execute(text("""
          SELECT p.source_record_id,p.property_name,p.location_raw,p.area_min_sqft,p.area_max_sqft,
                 p.rent_psf_month,p.monthly_rent,p.transaction_type,p.canonical_property_type,
                 p.floor,p.frontage_ft,p.suitable_for,p.source_type,p.source_name,
                 p.verification_status,p.data_confidence_score,
                 COALESCE(e.duplicate_type,'UNIQUE') duplicate_type,
                 COALESCE(e.suppress_from_matcher,FALSE) suppress_from_matcher
          FROM ai_property_index p
          LEFT JOIN ai_whatsapp_entity_resolution e
            ON e.listing_id::text=p.source_record_id
           AND e.model_version='2.4.7A-DUPLICATE-SAFETY-CALIBRATION'
          WHERE COALESCE(e.suppress_from_matcher,FALSE)=FALSE
            AND p.match_eligible=TRUE
        """)).mappings().all()

    ranked = []
    rows_for_db = []
    for p in props:
        result = score_match(req, p)
        item = {**result, **dict(p)}
        ranked.append(item)
        rows_for_db.append({
            "code": requirement_code, "id": p["source_record_id"],
            "score": result["match_score"], "status": result["status"],
            "action": result["action"], "hard": result["hard_rule_pass"],
            "rej": json.dumps(result["rejection_reasons"]),
            "pos": json.dumps(result["positive_reasons"]),
            "ls": result["location_score"], "ascore": result["area_score"],
            "ts": result["type_score"], "fs": result["floor_score"],
            "frs": result["frontage_score"], "rs": result["rent_score"],
            "version": MODULE_VERSION,
        })

    if rows_for_db:
        with engine.begin() as c:
            c.execute(text("""
              INSERT INTO ai_v25_match_results(
                requirement_code,source_record_id,match_score,status,action,hard_rule_pass,
                rejection_reasons,positive_reasons,location_score,area_score,type_score,
                floor_score,frontage_score,rent_score,model_version,evaluated_at
              )
              VALUES(
                :code,:id,:score,:status,:action,:hard,
                CAST(:rej AS jsonb),CAST(:pos AS jsonb),:ls,:ascore,:ts,:fs,:frs,:rs,:version,NOW()
              )
              ON CONFLICT(requirement_code,source_record_id) DO UPDATE SET
                match_score=EXCLUDED.match_score,status=EXCLUDED.status,action=EXCLUDED.action,
                hard_rule_pass=EXCLUDED.hard_rule_pass,rejection_reasons=EXCLUDED.rejection_reasons,
                positive_reasons=EXCLUDED.positive_reasons,location_score=EXCLUDED.location_score,
                area_score=EXCLUDED.area_score,type_score=EXCLUDED.type_score,
                floor_score=EXCLUDED.floor_score,frontage_score=EXCLUDED.frontage_score,
                rent_score=EXCLUDED.rent_score,model_version=EXCLUDED.model_version,evaluated_at=NOW()
            """), rows_for_db)

    ranked.sort(key=lambda x: (x["hard_rule_pass"], x["match_score"]), reverse=True)
    actionable = [x for x in ranked if x["hard_rule_pass"] and x["match_score"] >= 70]
    top_rejected = [x for x in ranked if not x["hard_rule_pass"]][:10]

    inventory_gap = None
    if not actionable:
        inventory_gap = {
            "status": "OPEN",
            "requirement_code": requirement_code,
            "company_name": req.get("company_name"),
            "locations": req.get("locations"),
            "transaction_type": req.get("transaction_type"),
            "minimum_area_sqft": req.get("minimum_area_sqft"),
            "maximum_area_sqft": req.get("maximum_area_sqft"),
            "minimum_frontage_ft": req.get("minimum_frontage_ft"),
            "required_floor": req.get("required_floor"),
            "suitable_for": req.get("suitable_for"),
            "reason": "No actionable property passed V2.5 production hard rules and minimum score."
        }

    return {
        "version": MODULE_VERSION,
        "requirement_code": requirement_code,
        "matches": actionable[:30],
        "inventory_gap": inventory_gap,
        "top_rejected": top_rejected,
        "evaluated_properties": len(ranked),
        "suppression_policy": "EXACT_DUPLICATE and HIGH_CONF_DUPLICATE only",
    }

def register_v25_match_routes(core):
    app, engine = core.app, core.engine
    ensure_schema(engine)

    @app.post("/api/v2/intelligence/v25/match/{requirement_code}")
    def match(req: Request, requirement_code: str):
        if hasattr(core, "need_login"):
            core.need_login(req)
        return run_match(engine, requirement_code)

    @app.get("/v2/production-matcher", response_class=HTMLResponse)
    def page(req: Request):
        if hasattr(core, "need_login"):
            core.need_login(req)
        return HTMLResponse("""<!doctype html>
<html><head><meta charset="utf-8"><title>V2.5 Production Matcher</title></head>
<body style="font-family:Arial;background:#f5f7fa">
<div style="max-width:1000px;margin:30px auto;background:white;padding:25px;border-radius:12px">
<h1>V2.5 Production Match Intelligence</h1>
<p>90–100 STRONG_MATCH · 80–89 GOOD_MATCH · 70–79 POSSIBLE_MATCH · hard-rule failures REJECT.</p>
<p>Only V2.4.7A exact/high-confidence duplicates are suppressed.</p>
</div></body></html>""")
    return app
