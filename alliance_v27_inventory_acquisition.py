
import re
import json
from sqlalchemy import text
from fastapi import Request
from fastapi.responses import HTMLResponse

MODULE_VERSION = "2.7.0-INVENTORY-ACQUISITION-BOT"

SOURCE_PRIORITY = {
    "MANUAL_SURVEY": 5,
    "WHATSAPP": 4,
    "NEWSPAPER": 3,
    "LEGACY": 2,
}

def _norm(v):
    return re.sub(r"\s+", " ", str(v or "").strip().lower())

def _tokens(v):
    return {x for x in re.split(r"[^a-z0-9]+", _norm(v)) if len(x) >= 2}

def _ensure_schema(engine):
    with engine.begin() as c:
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_v27_inventory_candidate(
          candidate_id BIGSERIAL PRIMARY KEY,
          action_id BIGINT NOT NULL,
          requirement_code TEXT NOT NULL,
          source_record_id TEXT NOT NULL,
          source_type TEXT,
          source_name TEXT,
          candidate_score NUMERIC(6,2) NOT NULL DEFAULT 0,
          decision TEXT NOT NULL,
          verification_status TEXT,
          reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
          positive_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
          candidate_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at TIMESTAMPTZ DEFAULT NOW(),
          updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """))
        c.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_v27_action_source
        ON ai_v27_inventory_candidate(action_id,source_record_id)
        """))
        c.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_ai_v27_action_score
        ON ai_v27_inventory_candidate(action_id,candidate_score DESC)
        """))

def _location_score(req_loc, prop_loc):
    rt = _tokens(req_loc)
    pt = _tokens(prop_loc)
    if not rt or not pt:
        return 0, "Location missing"
    if _norm(req_loc) == _norm(prop_loc):
        return 30, "Exact location match"
    overlap = len(rt & pt) / max(1, len(rt))
    if overlap >= 0.75:
        return 28, "Strong location match"
    if overlap >= 0.5:
        return 24, "Good location match"
    if overlap >= 0.25:
        return 15, "Partial location match"
    return 0, "Location mismatch"

def _area_score(rmin, rmax, pmin, pmax):
    try:
        rmin = float(rmin) if rmin is not None else None
        rmax = float(rmax) if rmax is not None else None
        pmin = float(pmin) if pmin is not None else None
        pmax = float(pmax) if pmax is not None else None
    except Exception:
        return 0, False, "Area parse issue"

    if rmin is None and rmax is None:
        return 20, True, "Area not mandatory"
    if pmin is None and pmax is None:
        return 0, False, "Property area missing"

    rmin = rmin if rmin is not None else rmax
    rmax = rmax if rmax is not None else rmin
    pmin = pmin if pmin is not None else pmax
    pmax = pmax if pmax is not None else pmin

    if pmin <= rmax and pmax >= rmin:
        return 20, True, "Area overlaps requirement"

    if pmin > rmax:
        delta = (pmin-rmax)/max(1,rmax)
    else:
        delta = (rmin-pmax)/max(1,rmin)

    if delta <= 0.10:
        return 12, True, "Area slightly outside preferred range"
    if delta <= 0.20:
        return 7, True, "Area outside preferred range"
    return 0, False, "Area materially outside requirement"

def _frontage_score(req_front, prop_front):
    try:
        rf = float(req_front) if req_front is not None else None
        pf = float(prop_front) if prop_front is not None else None
    except Exception:
        return 0, False, "Frontage parse issue"

    if rf is None:
        return 10, True, "Frontage not mandatory"
    if pf is None:
        return 3, True, "Frontage needs verification"
    if pf >= rf:
        return 10, True, "Frontage meets requirement"
    if pf >= rf*0.9:
        return 6, True, "Frontage slightly below requirement"
    return 0, False, "Frontage below requirement"

def _suitability_score(req_suit, prop_suit, prop_type):
    r = _norm(req_suit)
    combined = _norm(f"{prop_suit or ''} {prop_type or ''}")
    if not r:
        return 10, "Use not mandatory"
    rt = _tokens(r)
    pt = _tokens(combined)
    if r and r in combined:
        return 10, "Exact suitability match"
    if rt & pt:
        return 8, "Suitability aligned"
    broad = {"fine","dine","restaurant","cafe","retail","shop","commercial"}
    if (rt & broad) and (pt & broad):
        return 5, "Broad commercial suitability"
    return 1, "Suitability needs verification"

def score_candidate(req, prop):
    reasons = []
    positive = []
    hard = False

    req_tx = _norm(req.get("transaction_type"))
    prop_tx = _norm(prop.get("transaction_type"))
    if req_tx and prop_tx and req_tx != prop_tx and prop_tx != "lease_or_sale":
        hard = True
        reasons.append("Transaction type mismatch")
        tx_score = 0
    else:
        tx_score = 20
        positive.append("Transaction aligned")

    ls, lmsg = _location_score(req.get("locations"), prop.get("location_raw"))
    (positive if ls >= 24 else reasons).append(lmsg)
    if ls == 0:
        hard = True

    ars, area_pass, amsg = _area_score(
        req.get("minimum_area_sqft"), req.get("maximum_area_sqft"),
        prop.get("area_min_sqft"), prop.get("area_max_sqft")
    )
    (positive if area_pass and ars >= 12 else reasons).append(amsg)
    if not area_pass:
        hard = True

    frs, fr_pass, frmsg = _frontage_score(
        req.get("minimum_frontage_ft"), prop.get("frontage_ft")
    )
    (positive if fr_pass and frs >= 6 else reasons).append(frmsg)

    ss, smsg = _suitability_score(
        req.get("suitable_for"), prop.get("suitable_for"), prop.get("canonical_property_type")
    )
    (positive if ss >= 8 else reasons).append(smsg)

    verification_bonus = 5 if _norm(prop.get("verification_status")) == "verified" else 0
    try:
        confidence = float(prop.get("data_confidence_score") or 0)
    except Exception:
        confidence = 0
    confidence_bonus = 5 if confidence >= 90 else (3 if confidence >= 75 else 0)

    source_bonus = SOURCE_PRIORITY.get(str(prop.get("source_type") or "").upper(), 1)

    score = tx_score + ls + ars + frs + ss + verification_bonus + confidence_bonus + source_bonus
    score = max(0, min(100, round(score,2)))
    if hard:
        score = min(score,59)

    if hard:
        decision = "REJECT"
    elif score >= 90:
        decision = "STRONG_CANDIDATE"
    elif score >= 80:
        decision = "GOOD_CANDIDATE"
    elif score >= 70:
        decision = "POSSIBLE_CANDIDATE"
    else:
        decision = "LOW_PRIORITY"

    return {
        "candidate_score": score,
        "decision": decision,
        "hard_rule_pass": not hard,
        "reasons": reasons,
        "positive_reasons": positive,
    }

def run_inventory_search(engine, action_id, limit=300):
    _ensure_schema(engine)

    with engine.connect() as c:
        action = c.execute(text("""
          SELECT *
          FROM ai_v26_team_action
          WHERE action_id=:id
          LIMIT 1
        """), {"id": int(action_id)}).mappings().first()

    if not action:
        return {"version": MODULE_VERSION, "status": "NOT_FOUND", "detail": "V2.6 action not found"}

    if action["workflow_status"] != "INVENTORY_SEARCH":
        return {
            "version": MODULE_VERSION,
            "status": "BLOCKED",
            "detail": f"Action status must be INVENTORY_SEARCH, found {action['workflow_status']}",
        }

    with engine.connect() as c:
        req = c.execute(text("""
          SELECT
            requirement_code,
            company_name,
            preferred_locations_raw AS locations,
            transaction_type,
            minimum_area_sqft,
            maximum_area_sqft,
            minimum_frontage_ft,
            required_floor,
            suitable_for
          FROM ai_requirement_index
          WHERE requirement_code=:code
          ORDER BY requirement_index_id DESC
          LIMIT 1
        """), {"code": action["requirement_code"]}).mappings().first()

    if not req:
        return {"version": MODULE_VERSION, "status": "NOT_FOUND", "detail": "Requirement not indexed"}

    params = {}
    clauses = ["COALESCE(match_eligible,FALSE)=TRUE"]

    req_tx = _norm(req.get("transaction_type"))
    if req_tx:
        clauses.append("(LOWER(COALESCE(transaction_type,''))=:tx OR LOWER(COALESCE(transaction_type,''))='lease_or_sale')")
        params["tx"] = req_tx

    loc_tokens = [x for x in _tokens(req.get("locations")) if x not in {"place","road","delhi","ncr"}]
    if loc_tokens:
        bits = []
        for i,tok in enumerate(sorted(loc_tokens,key=len,reverse=True)[:3]):
            k=f"loc{i}"
            bits.append(f"(LOWER(COALESCE(location_raw,'')) LIKE :{k} OR LOWER(COALESCE(location_normalized,'')) LIKE :{k})")
            params[k]=f"%{tok}%"
        clauses.append("("+" OR ".join(bits)+")")

    try:
        rmin=float(req.get("minimum_area_sqft")) if req.get("minimum_area_sqft") is not None else None
        rmax=float(req.get("maximum_area_sqft")) if req.get("maximum_area_sqft") is not None else None
    except Exception:
        rmin=rmax=None

    if rmin is not None or rmax is not None:
        if rmin is None:rmin=rmax
        if rmax is None:rmax=rmin
        params["alow"]=rmin*0.8
        params["ahigh"]=rmax*1.2
        clauses.append("(area_max_sqft IS NULL OR area_min_sqft IS NULL OR (area_max_sqft>=:alow AND area_min_sqft<=:ahigh))")

    limit=max(1,min(int(limit or 300),500))

    with engine.connect() as c:
        props=c.execute(text(f"""
          SELECT
            source_record_id,property_name,location_raw,location_normalized,
            area_min_sqft,area_max_sqft,rent_psf_month,monthly_rent,
            transaction_type,canonical_property_type,floor_raw,frontage_ft,
            suitable_for,source_type,source_name,verification_status,data_confidence_score
          FROM ai_property_match_index
          WHERE {" AND ".join(clauses)}
          LIMIT {limit}
        """),params).mappings().all()

    candidates=[]
    for p0 in props:
        p=dict(p0)
        scored=score_candidate(req,p)
        if scored["decision"]=="REJECT":
            continue

        payload={
            "property_name":p.get("property_name"),
            "location":p.get("location_raw"),
            "area_min_sqft":p.get("area_min_sqft"),
            "area_max_sqft":p.get("area_max_sqft"),
            "rent_psf_month":p.get("rent_psf_month"),
            "monthly_rent":p.get("monthly_rent"),
            "transaction_type":p.get("transaction_type"),
            "canonical_property_type":p.get("canonical_property_type"),
            "floor":p.get("floor_raw"),
            "frontage_ft":p.get("frontage_ft"),
            "verification_status":p.get("verification_status"),
            "source_record_id":p.get("source_record_id"),
        }

        row={
            **scored,
            **payload,
            "source_type":p.get("source_type"),
            "source":p.get("source_name"),
            "data_confidence":p.get("data_confidence_score"),
        }
        candidates.append(row)

        with engine.begin() as c:
            c.execute(text("""
              INSERT INTO ai_v27_inventory_candidate(
                action_id,requirement_code,source_record_id,source_type,source_name,
                candidate_score,decision,verification_status,reasons,positive_reasons,
                candidate_payload,created_at,updated_at
              )
              VALUES(
                :action_id,:requirement_code,:source_record_id,:source_type,:source_name,
                :candidate_score,:decision,:verification_status,
                CAST(:reasons AS jsonb),CAST(:positive AS jsonb),CAST(:payload AS jsonb),NOW(),NOW()
              )
              ON CONFLICT(action_id,source_record_id) DO UPDATE SET
                candidate_score=EXCLUDED.candidate_score,
                decision=EXCLUDED.decision,
                verification_status=EXCLUDED.verification_status,
                reasons=EXCLUDED.reasons,
                positive_reasons=EXCLUDED.positive_reasons,
                candidate_payload=EXCLUDED.candidate_payload,
                updated_at=NOW()
            """),{
                "action_id":int(action_id),
                "requirement_code":action["requirement_code"],
                "source_record_id":p["source_record_id"],
                "source_type":p.get("source_type"),
                "source_name":p.get("source_name"),
                "candidate_score":scored["candidate_score"],
                "decision":scored["decision"],
                "verification_status":p.get("verification_status"),
                "reasons":json.dumps(scored["reasons"]),
                "positive":json.dumps(scored["positive_reasons"]),
                "payload":json.dumps(payload),
            })

    candidates.sort(key=lambda x:x["candidate_score"],reverse=True)
    shortlist=[x for x in candidates if x["candidate_score"]>=70][:25]

    verify_actions=[]
    if shortlist:
        from alliance_v26_team_action import create_or_update_action
        for x in shortlist[:10]:
            decision = "STRONG_MATCH" if x["candidate_score"]>=90 else ("GOOD_MATCH" if x["candidate_score"]>=80 else "POSSIBLE_MATCH")
            act = create_or_update_action(engine,{
                "requirement_code":action["requirement_code"],
                "source_record_id":x["source_record_id"],
                "decision":decision,
                "priority_score":x["candidate_score"],
                "workflow_status":"VERIFYING",
                "assigned_to":action.get("assigned_to"),
                "notes":"Candidate discovered by V2.7 Inventory Acquisition Bot. Verify availability before sharing."
            })
            verify_actions.append(act)

        with engine.begin() as c:
            c.execute(text("""
              UPDATE ai_v26_team_action
              SET notes=:notes,updated_at=NOW()
              WHERE action_id=:id
            """),{
                "id":int(action_id),
                "notes":f"V2.7 found {len(shortlist)} candidate(s). Verification tasks created for top {len(verify_actions)}."
            })

    return {
        "version":MODULE_VERSION,
        "action_id":int(action_id),
        "requirement_code":action["requirement_code"],
        "execution_mode":"EXISTING_INDEX_INVENTORY_SEARCH",
        "core_matcher_untouched":True,
        "sources_searched":["MANUAL_SURVEY","WHATSAPP","NEWSPAPER","LEGACY"],
        "properties_scanned":len(props),
        "candidates_found":len(candidates),
        "shortlist_count":len(shortlist),
        "verification_actions_created_or_updated":len(verify_actions),
        "shortlist":shortlist,
        "next_step":"VERIFY_CANDIDATES" if shortlist else "NO_EXISTING_INDEX_CANDIDATE",
    }

def register_v27_routes(core):
    app,engine=core.app,core.engine
    _ensure_schema(engine)

    @app.post("/api/v2/intelligence/v27/search/{action_id}")
    def search(action_id:int,req:Request,limit:int=300):
        if hasattr(core,"need_login"):
            core.need_login(req)
        try:
            return run_inventory_search(engine,action_id,limit)
        except Exception as exc:
            return {"version":MODULE_VERSION,"status":"ERROR","message":str(exc)}

    @app.get("/api/v2/intelligence/v27/candidates/{action_id}")
    def candidates(action_id:int,req:Request,limit:int=100):
        if hasattr(core,"need_login"):
            core.need_login(req)
        limit=max(1,min(int(limit or 100),200))
        with engine.connect() as c:
            rows=c.execute(text("""
              SELECT candidate_id,action_id,requirement_code,source_record_id,
                     source_type,source_name,candidate_score,decision,verification_status,
                     reasons,positive_reasons,candidate_payload,updated_at
              FROM ai_v27_inventory_candidate
              WHERE action_id=:id
              ORDER BY candidate_score DESC,updated_at DESC
              LIMIT :lim
            """),{"id":int(action_id),"lim":limit}).mappings().all()
        return {"version":MODULE_VERSION,"count":len(rows),"candidates":[dict(x) for x in rows]}

    @app.get("/v2/inventory-acquisition",response_class=HTMLResponse)
    def dashboard(req:Request):
        if hasattr(core,"need_login"):
            core.need_login(req)
        return HTMLResponse("""<!doctype html>
<html><head><meta charset="utf-8"><title>V2.7 Inventory Acquisition</title></head>
<body style="font-family:Arial;background:#f5f7fa">
<div style="max-width:1050px;margin:30px auto;background:white;padding:28px;border-radius:14px">
<h1>V2.7 Inventory Acquisition Bot</h1>
<p>Consumes V2.6 INVENTORY_SEARCH actions and searches existing indexed inventory first.</p>
<p>Sources: Manual / Survey, WhatsApp, Newspaper, Legacy Master Database.</p>
<p>Strong candidates are converted to VERIFYING tasks. Nothing is shared externally until V2.6 verification rules are satisfied.</p>
</div></body></html>""")
    return app
