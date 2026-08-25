
import re
import json
from sqlalchemy import text
from fastapi import Request, Body
from fastapi.responses import HTMLResponse

MODULE_VERSION = "2.9.5A-INDIVIDUAL-ENTITY-VERIFICATION"

PROPERTY_TERMS = {
    "RESTAURANT": "restaurant",
    "CAFE": "cafe",
    "BAR": "bar",
    "SHOP": "shop",
    "SHOWROOM": "showroom",
    "RETAIL_SPACE": "retail space",
    "OFFICE_SPACE": "office space",
    "COMMERCIAL_PROPERTY": "commercial property",
}

def _norm(v):
    return re.sub(r"\s+", " ", str(v or "").strip())

def _ensure_schema(engine):
    with engine.begin() as c:
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_v295_entity_verification(
          verification_id BIGSERIAL PRIMARY KEY,
          split_entity_id BIGINT NOT NULL UNIQUE,
          external_entity_code TEXT NOT NULL,
          discovery_id BIGINT NOT NULL,
          action_id BIGINT NOT NULL,
          requirement_code TEXT NOT NULL,
          verified_property_type TEXT,
          suitability_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
          availability_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
          frontage_ft NUMERIC(14,2),
          contact_name TEXT,
          contact_phone TEXT,
          contact_email TEXT,
          verification_score NUMERIC(6,2) DEFAULT 0,
          verification_status TEXT NOT NULL DEFAULT 'PENDING',
          reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
          notes TEXT,
          created_at TIMESTAMPTZ DEFAULT NOW(),
          updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """))
        c.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_ai_v295_req_status
        ON ai_v295_entity_verification(requirement_code,verification_status,updated_at DESC)
        """))

def anchored_property_type(raw_text):
    """
    Pick the earliest property-type marker in the listing heading.
    Nearby landmarks/brands later in the text cannot override it.
    """
    t = _norm(raw_text).lower()
    head = t[:140]

    found = []
    for ptype, term in PROPERTY_TERMS.items():
        m = re.search(rf"\b{re.escape(term)}\b", head)
        if m:
            found.append((m.start(), ptype))

    if not found:
        return "COMMERCIAL_PROPERTY"

    found.sort(key=lambda x: x[0])
    return found[0][1]

def suitability_for_fine_dine(property_type):
    p = str(property_type or "").upper()

    if p in {"RESTAURANT","CAFE","BAR"}:
        return "SUITABLE", 25, ["Property type directly supports F&B"]

    if p in {"SHOP","SHOWROOM","RETAIL_SPACE","COMMERCIAL_PROPERTY"}:
        return "VERIFY_USE", 15, ["Retail/commercial use may support F&B; verify permissions/services"]

    if p == "OFFICE_SPACE":
        return "VERIFY_CONVERSION", 5, ["Office space requires conversion/use verification for fine dine"]

    return "UNSUITABLE", 0, ["Property type not suitable for fine dine"]

def verify_entity(engine, split_entity_id, payload):
    _ensure_schema(engine)

    with engine.connect() as c:
        ent = c.execute(text("""
          SELECT *
          FROM ai_v29a_split_external_entity
          WHERE split_entity_id=:id
          LIMIT 1
        """), {"id": int(split_entity_id)}).mappings().first()

    if not ent:
        return {"version":MODULE_VERSION,"status":"NOT_FOUND","detail":"Split entity not found"}

    if ent["splitter_status"] == "REJECT":
        return {
            "version":MODULE_VERSION,
            "status":"BLOCKED",
            "reason":"Splitter already rejected this entity",
            "external_entity_code":ent["external_entity_code"],
        }

    with engine.connect() as c:
        req = c.execute(text("""
          SELECT requirement_code,transaction_type,minimum_area_sqft,maximum_area_sqft,
                 minimum_frontage_ft,suitable_for
          FROM ai_requirement_index
          WHERE requirement_code=:code
          ORDER BY requirement_index_id DESC
          LIMIT 1
        """), {"code":ent["requirement_code"]}).mappings().first()

    raw = ent.get("raw_entity_text") or ""
    anchored_type = anchored_property_type(raw)
    verified_type = str(payload.get("verified_property_type") or anchored_type).upper()

    suitability_status, _, suitability_reasons = suitability_for_fine_dine(verified_type)
    if payload.get("suitability_status"):
        suitability_status = str(payload["suitability_status"]).upper()

    availability = str(payload.get("availability_status") or "UNVERIFIED").upper()
    if availability not in {"UNVERIFIED","AVAILABLE","NOT_AVAILABLE","CALLBACK_REQUIRED"}:
        return {"version":MODULE_VERSION,"status":"ERROR","message":"Invalid availability_status"}

    frontage = payload.get("frontage_ft")
    min_front = req.get("minimum_frontage_ft") if req else None

    reasons = list(suitability_reasons)
    score = min(float(ent.get("splitter_score") or 0), 70)

    if availability == "AVAILABLE":
        score += 15
        reasons.append("Availability confirmed")
    elif availability == "NOT_AVAILABLE":
        score = 0
        reasons.append("Property not available")

    if suitability_status == "SUITABLE":
        score += 20
    elif suitability_status == "VERIFY_USE":
        score += 10
    elif suitability_status == "UNSUITABLE":
        score = min(score, 40)

    try:
        if min_front is not None and frontage is not None:
            if float(frontage) >= float(min_front):
                score += 10
                reasons.append("Frontage meets requirement")
            else:
                score = min(score, 59)
                reasons.append("Frontage below requirement")
        elif min_front is not None:
            reasons.append("Frontage not yet verified")
    except Exception:
        reasons.append("Frontage parse issue")

    contact_phone = payload.get("contact_phone")
    if contact_phone:
        score += 5
        reasons.append("Contact available")

    score = max(0,min(100,round(score,2)))

    if availability == "NOT_AVAILABLE" or suitability_status == "UNSUITABLE":
        final_status = "REJECTED"
    elif availability == "AVAILABLE" and suitability_status in {"SUITABLE","VERIFY_USE"} and score >= 75:
        final_status = "VERIFIED_CANDIDATE"
    elif availability in {"UNVERIFIED","CALLBACK_REQUIRED"}:
        final_status = "NEEDS_AVAILABILITY_CHECK"
    else:
        final_status = "NEEDS_REVIEW"

    with engine.begin() as c:
        row = c.execute(text("""
          INSERT INTO ai_v295_entity_verification(
            split_entity_id,external_entity_code,discovery_id,action_id,requirement_code,
            verified_property_type,suitability_status,availability_status,frontage_ft,
            contact_name,contact_phone,contact_email,verification_score,
            verification_status,reasons,notes,created_at,updated_at
          )
          VALUES(
            :split_entity_id,:external_entity_code,:discovery_id,:action_id,:requirement_code,
            :verified_property_type,:suitability_status,:availability_status,:frontage_ft,
            :contact_name,:contact_phone,:contact_email,:verification_score,
            :verification_status,CAST(:reasons AS jsonb),:notes,NOW(),NOW()
          )
          ON CONFLICT(split_entity_id) DO UPDATE SET
            verified_property_type=EXCLUDED.verified_property_type,
            suitability_status=EXCLUDED.suitability_status,
            availability_status=EXCLUDED.availability_status,
            frontage_ft=EXCLUDED.frontage_ft,
            contact_name=EXCLUDED.contact_name,
            contact_phone=EXCLUDED.contact_phone,
            contact_email=EXCLUDED.contact_email,
            verification_score=EXCLUDED.verification_score,
            verification_status=EXCLUDED.verification_status,
            reasons=EXCLUDED.reasons,
            notes=EXCLUDED.notes,
            updated_at=NOW()
          RETURNING *
        """), {
            "split_entity_id":int(split_entity_id),
            "external_entity_code":ent["external_entity_code"],
            "discovery_id":ent["discovery_id"],
            "action_id":ent["action_id"],
            "requirement_code":ent["requirement_code"],
            "verified_property_type":verified_type,
            "suitability_status":suitability_status,
            "availability_status":availability,
            "frontage_ft":frontage,
            "contact_name":payload.get("contact_name"),
            "contact_phone":contact_phone,
            "contact_email":payload.get("contact_email"),
            "verification_score":score,
            "verification_status":final_status,
            "reasons":json.dumps(reasons),
            "notes":payload.get("notes"),
        }).mappings().one()

    v26_action = None
    if final_status == "VERIFIED_CANDIDATE":
        from alliance_v26_team_action import create_or_update_action
        v26_action = create_or_update_action(engine, {
            "requirement_code": ent["requirement_code"],
            "source_record_id": ent["external_entity_code"],
            "decision": "GOOD_MATCH",
            "priority_score": score,
            "workflow_status": "VERIFYING",
            "internal_contact_name": payload.get("contact_name"),
            "internal_contact_phone": contact_phone,
            "internal_contact_role": "EXTERNAL_BROKER_OR_OWNER",
            "notes": "V2.9.5 entity-level verified candidate. Final availability/physical verification required before sharing.",
            "property": {
                "property_name": ent["property_name"],
                "location": ent["location"],
                "area_min_sqft": ent["area_min_sqft"],
                "area_max_sqft": ent["area_max_sqft"],
                "monthly_rent": ent["monthly_rent"],
                "transaction_type": ent["transaction_type"],
                "canonical_property_type": verified_type,
                "frontage_ft": frontage,
                "source_record_id": ent["external_entity_code"],
                "verification_status": "EXTERNAL_VERIFIED_CANDIDATE",
            }
        })

    return {
        "version":MODULE_VERSION,
        "split_entity_id":int(split_entity_id),
        "external_entity_code":ent["external_entity_code"],
        "anchored_property_type":anchored_type,
        "verified_property_type":verified_type,
        "suitability_status":suitability_status,
        "availability_status":availability,
        "verification_score":score,
        "verification_status":final_status,
        "reasons":reasons,
        "v26_action_created":v26_action,
        "promoted_to_core_match_index":False,
    }

def register_v295_routes(core):
    app,engine=core.app,core.engine
    _ensure_schema(engine)

    @app.post("/api/v2/intelligence/v295/verify/{split_entity_id}")
    def verify(split_entity_id:int,req:Request,payload:dict=Body(default={})):
        if hasattr(core,"need_login"):
            core.need_login(req)
        try:
            return verify_entity(engine,split_entity_id,payload or {})
        except Exception as exc:
            return {"version":MODULE_VERSION,"status":"ERROR","message":str(exc)}

    @app.get("/api/v2/intelligence/v295/queue")
    def queue(req:Request,requirement_code:str="",limit:int=100):
        if hasattr(core,"need_login"):
            core.need_login(req)
        limit=max(1,min(int(limit or 100),200))
        clauses=["splitter_status IN ('VERIFY_FIRST','REVIEW')"]
        params={"lim":limit}
        if requirement_code:
            clauses.append("requirement_code=:code")
            params["code"]=requirement_code

        with engine.connect() as c:
            rows=c.execute(text(f"""
              SELECT split_entity_id,external_entity_code,requirement_code,property_name,
                     location,property_type,transaction_type,area_min_sqft,area_max_sqft,
                     monthly_rent,splitter_score,splitter_status,source_url
              FROM ai_v29a_split_external_entity
              WHERE {" AND ".join(clauses)}
              ORDER BY splitter_score DESC,split_entity_id
              LIMIT :lim
            """),params).mappings().all()

        return {"version":MODULE_VERSION,"count":len(rows),"queue":[dict(x) for x in rows]}

    @app.get("/v2/entity-verification",response_class=HTMLResponse)
    def dashboard(req:Request):
        if hasattr(core,"need_login"):
            core.need_login(req)
        return HTMLResponse("""<!doctype html>
<html><head><meta charset="utf-8"><title>V2.9.5 Entity Verification</title></head>
<body style="font-family:Arial;background:#f5f7fa">
<div style="max-width:1050px;margin:30px auto;background:white;padding:28px;border-radius:14px">
<h1>V2.9.5A Individual Entity Verification</h1>
<p>Verifies one split property at a time.</p>
<p>Property type is anchored to the earliest listing type, not nearby restaurant/brand names.</p>
</div></body></html>""")
    return app
