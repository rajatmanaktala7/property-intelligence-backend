
import re
import json
from sqlalchemy import text
from fastapi import Request, Body
from fastapi.responses import HTMLResponse

MODULE_VERSION = "2.9.0-EXTERNAL-VERIFICATION-INVENTORY-PROMOTION"

NOISE_TERMS = [
    "rented out","sold out","sold","not available","unavailable",
    "blog","article","news","guide","how to choose","residential flat",
    "apartment for sale","home for sale",
]

AREA_PATTERNS = [
    r"\b(\d{3,5})\s*(?:sq\.?\s*ft|sqft|square\s*feet|sft)\b",
    r"\barea\s*(?:of|:|-)?\s*(\d{3,5})\s*(?:feet|ft)\b",
    r"\b(\d{3,5})\s*feet\b",
]

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)

def _norm(v):
    return re.sub(r"\s+", " ", str(v or "").strip().lower())

def _ensure_schema(engine):
    with engine.begin() as c:
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_v29_verified_external_inventory(
          verified_external_id BIGSERIAL PRIMARY KEY,
          discovery_id BIGINT NOT NULL UNIQUE,
          action_id BIGINT NOT NULL,
          requirement_code TEXT NOT NULL,
          source_url TEXT NOT NULL,
          provider TEXT,
          property_name TEXT,
          location TEXT,
          transaction_type TEXT,
          area_min_sqft NUMERIC(14,2),
          area_max_sqft NUMERIC(14,2),
          frontage_ft NUMERIC(14,2),
          suitable_for TEXT,
          monthly_rent NUMERIC(16,2),
          contact_name TEXT,
          contact_phone TEXT,
          contact_email TEXT,
          availability_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
          verification_notes TEXT,
          verification_score NUMERIC(6,2) DEFAULT 0,
          promoted_status TEXT NOT NULL DEFAULT 'STAGED',
          created_at TIMESTAMPTZ DEFAULT NOW(),
          updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """))
        c.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_ai_v29_action
        ON ai_v29_verified_external_inventory(action_id,updated_at DESC)
        """))

def extract_area(text_blob):
    t = _norm(text_blob)
    vals = []
    for pat in AREA_PATTERNS:
        for m in re.finditer(pat, t, flags=re.I):
            try:
                v = float(m.group(1).replace(",",""))
                if 100 <= v <= 500000:
                    vals.append(v)
            except Exception:
                pass
    vals = sorted(set(vals))
    if not vals:
        return None, None
    if len(vals) == 1:
        return vals[0], vals[0]
    return vals[0], vals[-1]

def extract_contacts(text_blob):
    t = str(text_blob or "")
    compact = re.sub(r"[^\d+]","",t)
    phone = None

    # Indian mobile formats: +919876543210 / 919876543210 / 9876543210
    for pat in [r"\+91([6-9]\d{9})", r"91([6-9]\d{9})", r"\b([6-9]\d{9})\b"]:
        m = re.search(pat, compact)
        if m:
            phone = m.group(1)
            break

    emails = EMAIL_RE.findall(t)
    return phone, (emails[0] if emails else None)

def classify_discovery(title, snippet, req):
    blob = _norm(f"{title or ''} {snippet or ''}")
    reasons = []
    hard = False

    for term in NOISE_TERMS:
        if term in blob:
            hard = True
            reasons.append(f"Noise/unavailable signal: {term}")

    if req.get("transaction_type"):
        tx = _norm(req.get("transaction_type"))
        if tx == "lease" and any(x in blob for x in ["sale","for sale","outright"]):
            hard = True
            reasons.append("Transaction conflict with LEASE")
        elif tx == "sale" and any(x in blob for x in ["rent","lease","to let"]):
            hard = True
            reasons.append("Transaction conflict with SALE")

    amin, amax = extract_area(blob)
    rmin = req.get("minimum_area_sqft")
    rmax = req.get("maximum_area_sqft")
    area_ok = None

    try:
        if amin is not None and rmin is not None and rmax is not None:
            rmin = float(rmin); rmax = float(rmax)
            area_ok = (amax >= rmin*0.9 and amin <= rmax*1.1)
            if area_ok:
                reasons.append("Area evidence fits requirement")
            else:
                reasons.append("Area evidence outside requirement")
    except Exception:
        area_ok = None

    score = 50
    if hard:
        score = 0
    else:
        if area_ok is True: score += 25
        if any(x in blob for x in ["restaurant","cafe","bar","fine dine","food"]):
            score += 15
            reasons.append("Hospitality use signal")
        if any(x in blob for x in ["available","lease","rent","to let"]):
            score += 10
            reasons.append("Availability/lease signal")

    score = max(0, min(100, score))
    return {
        "verification_score": score,
        "hard_reject": hard,
        "area_min_sqft": amin,
        "area_max_sqft": amax,
        "reasons": reasons,
    }

def get_discovery(engine, discovery_id):
    with engine.connect() as c:
        return c.execute(text("""
          SELECT *
          FROM ai_v28_external_discovery
          WHERE discovery_id=:id
          LIMIT 1
        """), {"id": int(discovery_id)}).mappings().first()

def verify_discovery(engine, discovery_id, payload):
    _ensure_schema(engine)

    d = get_discovery(engine, discovery_id)
    if not d:
        return {"version":MODULE_VERSION,"status":"NOT_FOUND","detail":"Discovery not found"}

    with engine.connect() as c:
        req = c.execute(text("""
          SELECT requirement_code,company_name,preferred_locations_raw AS locations,
                 transaction_type,minimum_area_sqft,maximum_area_sqft,
                 minimum_frontage_ft,required_floor,suitable_for
          FROM ai_requirement_index
          WHERE requirement_code=:code
          ORDER BY requirement_index_id DESC
          LIMIT 1
        """), {"code": d["requirement_code"]}).mappings().first()

    ai_check = classify_discovery(d.get("title"), d.get("snippet"), req or {})
    combined = f"{d.get('title') or ''} {d.get('snippet') or ''}"
    auto_phone, auto_email = extract_contacts(combined)

    if ai_check["hard_reject"]:
        with engine.begin() as c:
            c.execute(text("""
              UPDATE ai_v28_external_discovery
              SET review_status='REJECTED',updated_at=NOW()
              WHERE discovery_id=:id
            """), {"id":int(discovery_id)})
        return {
            "version":MODULE_VERSION,
            "status":"REJECTED",
            "discovery_id":int(discovery_id),
            "verification_score":ai_check["verification_score"],
            "reasons":ai_check["reasons"],
            "promoted":False,
        }

    availability = str(payload.get("availability_status") or "UNVERIFIED").upper()
    allowed = {"UNVERIFIED","AVAILABLE","NOT_AVAILABLE","CALLBACK_REQUIRED"}
    if availability not in allowed:
        return {"version":MODULE_VERSION,"status":"ERROR","message":"Invalid availability_status"}

    if availability == "NOT_AVAILABLE":
        with engine.begin() as c:
            c.execute(text("""
              UPDATE ai_v28_external_discovery
              SET review_status='REJECTED',updated_at=NOW()
              WHERE discovery_id=:id
            """), {"id":int(discovery_id)})
        return {
            "version":MODULE_VERSION,
            "status":"REJECTED",
            "reason":"Team marked property not available",
            "promoted":False,
        }

    area_min = payload.get("area_min_sqft")
    area_max = payload.get("area_max_sqft")
    if area_min is None:
        area_min = ai_check["area_min_sqft"]
    if area_max is None:
        area_max = ai_check["area_max_sqft"]

    contact_phone = payload.get("contact_phone") or auto_phone
    contact_email = payload.get("contact_email") or auto_email

    verification_score = ai_check["verification_score"]
    if availability == "AVAILABLE":
        verification_score = min(100, verification_score + 15)
    if contact_phone:
        verification_score = min(100, verification_score + 5)

    review_status = "VERIFIED_CANDIDATE" if availability == "AVAILABLE" and verification_score >= 70 else "VERIFYING"
    promoted_status = "PROMOTED_TO_VERIFICATION" if review_status == "VERIFIED_CANDIDATE" else "STAGED"

    with engine.begin() as c:
        row = c.execute(text("""
          INSERT INTO ai_v29_verified_external_inventory(
            discovery_id,action_id,requirement_code,source_url,provider,
            property_name,location,transaction_type,area_min_sqft,area_max_sqft,
            frontage_ft,suitable_for,monthly_rent,contact_name,contact_phone,
            contact_email,availability_status,verification_notes,verification_score,
            promoted_status,created_at,updated_at
          )
          VALUES(
            :discovery_id,:action_id,:requirement_code,:source_url,:provider,
            :property_name,:location,:transaction_type,:area_min_sqft,:area_max_sqft,
            :frontage_ft,:suitable_for,:monthly_rent,:contact_name,:contact_phone,
            :contact_email,:availability_status,:verification_notes,:verification_score,
            :promoted_status,NOW(),NOW()
          )
          ON CONFLICT(discovery_id) DO UPDATE SET
            property_name=EXCLUDED.property_name,
            location=EXCLUDED.location,
            transaction_type=EXCLUDED.transaction_type,
            area_min_sqft=EXCLUDED.area_min_sqft,
            area_max_sqft=EXCLUDED.area_max_sqft,
            frontage_ft=EXCLUDED.frontage_ft,
            suitable_for=EXCLUDED.suitable_for,
            monthly_rent=EXCLUDED.monthly_rent,
            contact_name=EXCLUDED.contact_name,
            contact_phone=EXCLUDED.contact_phone,
            contact_email=EXCLUDED.contact_email,
            availability_status=EXCLUDED.availability_status,
            verification_notes=EXCLUDED.verification_notes,
            verification_score=EXCLUDED.verification_score,
            promoted_status=EXCLUDED.promoted_status,
            updated_at=NOW()
          RETURNING *
        """), {
            "discovery_id":int(discovery_id),
            "action_id":d["action_id"],
            "requirement_code":d["requirement_code"],
            "source_url":d["source_url"],
            "provider":d["provider"],
            "property_name":payload.get("property_name") or d.get("title"),
            "location":payload.get("location") or (req.get("locations") if req else None),
            "transaction_type":payload.get("transaction_type") or (req.get("transaction_type") if req else None),
            "area_min_sqft":area_min,
            "area_max_sqft":area_max,
            "frontage_ft":payload.get("frontage_ft"),
            "suitable_for":payload.get("suitable_for") or (req.get("suitable_for") if req else None),
            "monthly_rent":payload.get("monthly_rent"),
            "contact_name":payload.get("contact_name"),
            "contact_phone":contact_phone,
            "contact_email":contact_email,
            "availability_status":availability,
            "verification_notes":payload.get("verification_notes"),
            "verification_score":verification_score,
            "promoted_status":promoted_status,
        }).mappings().one()

        c.execute(text("""
          UPDATE ai_v28_external_discovery
          SET review_status=:status,updated_at=NOW()
          WHERE discovery_id=:id
        """), {"status":review_status,"id":int(discovery_id)})

    action_created = None
    if review_status == "VERIFIED_CANDIDATE":
        from alliance_v26_team_action import create_or_update_action
        action_created = create_or_update_action(engine, {
            "requirement_code": d["requirement_code"],
            "source_record_id": f"EXT-{int(discovery_id)}",
            "decision": "GOOD_MATCH",
            "priority_score": verification_score,
            "workflow_status": "VERIFYING",
            "internal_contact_name": payload.get("contact_name"),
            "internal_contact_phone": contact_phone,
            "internal_contact_role": "EXTERNAL_BROKER_OR_OWNER",
            "notes": "V2.9 verified external candidate. Final physical/availability verification required before sharing.",
            "property": {
                "property_name": row["property_name"],
                "location": row["location"],
                "area_min_sqft": row["area_min_sqft"],
                "area_max_sqft": row["area_max_sqft"],
                "monthly_rent": row["monthly_rent"],
                "transaction_type": row["transaction_type"],
                "frontage_ft": row["frontage_ft"],
                "source_record_id": f"EXT-{int(discovery_id)}",
                "verification_status": "EXTERNAL_VERIFIED_CANDIDATE",
            }
        })

    return {
        "version":MODULE_VERSION,
        "status":review_status,
        "verification_score":verification_score,
        "reasons":ai_check["reasons"],
        "verified_external_inventory":dict(row),
        "v26_action_created":action_created,
        "promoted_to_core_match_index":False,
        "next_step":"FINAL_TEAM_VERIFICATION" if action_created else "CONTINUE_VERIFICATION",
    }

def register_v29_routes(core):
    app,engine=core.app,core.engine
    _ensure_schema(engine)

    @app.post("/api/v2/intelligence/v29/verify/{discovery_id}")
    def verify(discovery_id:int,req:Request,payload:dict=Body(default={})):
        if hasattr(core,"need_login"):
            core.need_login(req)
        try:
            return verify_discovery(engine,discovery_id,payload or {})
        except Exception as exc:
            return {"version":MODULE_VERSION,"status":"ERROR","message":str(exc)}

    @app.get("/api/v2/intelligence/v29/verified")
    def verified(req:Request,action_id:int=0,limit:int=100):
        if hasattr(core,"need_login"):
            core.need_login(req)
        limit=max(1,min(int(limit or 100),200))
        clauses=["TRUE"]
        params={"lim":limit}
        if action_id:
            clauses.append("action_id=:action_id")
            params["action_id"]=int(action_id)
        with engine.connect() as c:
            rows=c.execute(text(f"""
              SELECT *
              FROM ai_v29_verified_external_inventory
              WHERE {" AND ".join(clauses)}
              ORDER BY verification_score DESC,updated_at DESC
              LIMIT :lim
            """),params).mappings().all()
        return {"version":MODULE_VERSION,"count":len(rows),"verified_external_inventory":[dict(x) for x in rows]}

    @app.get("/v2/external-verification",response_class=HTMLResponse)
    def dashboard(req:Request):
        if hasattr(core,"need_login"):
            core.need_login(req)
        return HTMLResponse("""<!doctype html>
<html><head><meta charset="utf-8"><title>V2.9 External Verification</title></head>
<body style="font-family:Arial;background:#f5f7fa">
<div style="max-width:1050px;margin:30px auto;background:white;padding:28px;border-radius:14px">
<h1>V2.9 External Verification & Inventory Promotion</h1>
<p>Rejects obvious noise/unavailable listings, extracts area/contact evidence, records team verification, and promotes only AVAILABLE candidates into V2.6 VERIFYING tasks.</p>
<p>External discoveries are staged separately and are not silently inserted into the core matcher index.</p>
</div></body></html>""")
    return app
