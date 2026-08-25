
import os
import re
import json
from sqlalchemy import create_engine, text
from fastapi import Request
from fastapi.responses import HTMLResponse

MODULE_VERSION = "2.4.6-REQUIREMENT-SUPPLY-INTELLIGENCE"
BATCH_SIZE = 250

REQ_PATTERNS = [
    (r"\bclient\s+(?:is\s+)?looking\s+for\b", 40),
    (r"\blooking\s+for\b", 34),
    (r"\brequired\b", 34),
    (r"\brequirement\b", 32),
    (r"\bneed(?:ed)?\b", 30),
    (r"\bwanted\b", 30),
    (r"\bseeking\b", 28),
    (r"\btenant\s+requirement\b", 40),
    (r"\bbuyer\s+requirement\b", 40),
    (r"\bwe\s+have\s+a\s+client\b", 28),
    (r"\bfor\s+our\s+client\b", 28),
]

SUPPLY_PATTERNS = [
    (r"\bproperty\s+available\b", 40),
    (r"\bavailable\s+for\s+rent\b", 40),
    (r"\bavailable\s+for\s+lease\b", 40),
    (r"\bavailable\s+for\s+sale\b", 40),
    (r"\bfor\s+sale\b", 34),
    (r"\bfor\s+rent\b", 34),
    (r"\bfor\s+lease\b", 34),
    (r"\bto\s+let\b", 34),
    (r"\bowner\s+direct\b", 28),
    (r"\bdirect\s+owner\b", 28),
]

OWNER_PATTERNS = [
    r"\bowner\s+direct\b", r"\bdirect\s+owner\b", r"\bowner\b",
]
BROKER_PATTERNS = [
    r"\bbroker\b", r"\bdealer\b", r"\bproperty\s+consultant\b",
    r"\brealtor\b", r"\bchannel\s+partner\b",
]
CLIENT_PATTERNS = [
    r"\bclient\b", r"\bfor\s+our\s+client\b", r"\btenant\b", r"\bbuyer\b",
]

LEASE_PATTERNS = [
    r"\blease\b", r"\brent\b", r"\bto\s+let\b", r"\brental\b", r"\bleasing\b",
]
SALE_PATTERNS = [
    r"\bsale\b", r"\bsell\b", r"\bselling\b", r"\bpurchase\b", r"\bbuy\b",
    r"\boutright\b",
]

def _db_url(url):
    url = (url or "").strip()
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url

def _source_engine(primary_engine):
    wa = (os.getenv("WHATSAPP_DATABASE_URL") or "").strip()
    primary = (os.getenv("DATABASE_URL") or "").strip()
    if not wa or wa == primary:
        return primary_engine, False
    return create_engine(
        _db_url(wa),
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={"connect_timeout": 10},
    ), True

def ensure_schema(engine):
    with engine.begin() as c:
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_whatsapp_requirement_supply_intelligence (
          listing_id UUID PRIMARY KEY,
          classified_role TEXT,
          role_confidence NUMERIC(5,2),
          contact_role TEXT,
          contact_role_confidence NUMERIC(5,2),
          transaction_hint TEXT,
          transaction_confidence NUMERIC(5,2),
          requirement_score NUMERIC(6,2),
          supply_score NUMERIC(6,2),
          ambiguity_score NUMERIC(6,2),
          evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
          risk_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
          model_version TEXT NOT NULL,
          evaluated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """))
        c.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_ai_whatsapp_req_supply_model
        ON ai_whatsapp_requirement_supply_intelligence(model_version,evaluated_at DESC)
        """))

def _norm(raw):
    return re.sub(r"\s+", " ", str(raw or "").strip().lower())

def _score_patterns(t, patterns):
    score = 0
    hits = []
    for pat, weight in patterns:
        if re.search(pat, t, re.I):
            score += weight
            hits.append(pat)
    return score, hits

def classify_requirement_supply(raw):
    t = _norm(raw)

    req_score, req_hits = _score_patterns(t, REQ_PATTERNS)
    supply_score, supply_hits = _score_patterns(t, SUPPLY_PATTERNS)

    # Sentence-aware conflict handling.
    # "Need shop available in CP" is still a requirement because "need"
    # governs the noun phrase while "available" is descriptive.
    if re.search(r"\bneed(?:ed)?\b.*\bavailable\b", t):
        req_score += 24
        supply_score = max(0, supply_score - 12)

    # "Property available, client looking..." contains both intents: keep ambiguous.
    dual_clause = bool(
        re.search(r"\b(?:but|however|while|also)\b", t)
        and req_score > 0 and supply_score > 0
    )

    if dual_clause:
        role = "AMBIGUOUS"
        role_conf = 35
    elif req_score >= 30 and req_score >= supply_score + 18:
        role = "REQUIREMENT"
        role_conf = min(99, 70 + min(29, (req_score - supply_score) // 2))
    elif supply_score >= 30 and supply_score >= req_score + 18:
        role = "SUPPLY"
        role_conf = min(99, 70 + min(29, (supply_score - req_score) // 2))
    elif req_score == 0 and supply_score == 0:
        role = "AMBIGUOUS"
        role_conf = 0
    else:
        role = "AMBIGUOUS"
        role_conf = 40

    # Contact role.
    owner_hits = [p for p in OWNER_PATTERNS if re.search(p, t, re.I)]
    broker_hits = [p for p in BROKER_PATTERNS if re.search(p, t, re.I)]
    client_hits = [p for p in CLIENT_PATTERNS if re.search(p, t, re.I)]

    contact_role = None
    contact_conf = 0
    contact_groups = sum(bool(x) for x in [owner_hits, broker_hits, client_hits])
    if contact_groups == 1:
        if owner_hits:
            contact_role, contact_conf = "OWNER", 92
        elif broker_hits:
            contact_role, contact_conf = "BROKER", 90
        else:
            contact_role, contact_conf = "CLIENT_SIDE", 88
    elif contact_groups > 1:
        contact_role, contact_conf = "AMBIGUOUS", 35

    # Transaction hint.
    lease_hits = [p for p in LEASE_PATTERNS if re.search(p, t, re.I)]
    sale_hits = [p for p in SALE_PATTERNS if re.search(p, t, re.I)]
    if lease_hits and sale_hits:
        tx, tx_conf = "LEASE_OR_SALE", 90
    elif lease_hits:
        tx, tx_conf = "LEASE", 95
    elif sale_hits:
        tx, tx_conf = "SALE", 95
    else:
        tx, tx_conf = None, 0

    ambiguity = 0
    risks = []
    if role == "AMBIGUOUS":
        ambiguity += 60
        risks.append("role_ambiguous")
    if req_score > 0 and supply_score > 0:
        ambiguity += min(35, min(req_score, supply_score))
        risks.append("mixed_requirement_supply_signals")
    if contact_role == "AMBIGUOUS":
        ambiguity += 15
        risks.append("contact_role_ambiguous")
    ambiguity = min(100, ambiguity)

    return {
        "classified_role": role,
        "role_confidence": role_conf,
        "contact_role": contact_role,
        "contact_role_confidence": contact_conf,
        "transaction_hint": tx,
        "transaction_confidence": tx_conf,
        "requirement_score": req_score,
        "supply_score": supply_score,
        "ambiguity_score": ambiguity,
        "evidence": {
            "requirement_hits": req_hits,
            "supply_hits": supply_hits,
            "owner_hits": owner_hits,
            "broker_hits": broker_hits,
            "client_hits": client_hits,
            "lease_hits": lease_hits,
            "sale_hits": sale_hits,
        },
        "risk_flags": risks,
    }

def run_requirement_supply_intelligence(primary_engine):
    ensure_schema(primary_engine)
    source_engine, owned = _source_engine(primary_engine)

    try:
        with primary_engine.connect() as c:
            ids = [
                str(r["listing_id"])
                for r in c.execute(text("""
                  SELECT p.listing_id
                  FROM ai_whatsapp_purity p
                  LEFT JOIN ai_whatsapp_requirement_supply_intelligence a
                    ON a.listing_id=p.listing_id
                   AND a.model_version=:version
                  WHERE a.listing_id IS NULL
                    AND (
                         p.recovered_role IS NULL
                      OR p.recovered_role='UNKNOWN'
                      OR p.review_status IN ('NEEDS_REVIEW','LOW_CONFIDENCE','UNKNOWN')
                    )
                  ORDER BY p.listing_id
                  LIMIT :lim
                """), {"version": MODULE_VERSION, "lim": BATCH_SIZE}).mappings().all()
            ]

        if not ids:
            return {
                "version": MODULE_VERSION,
                "batch_size": BATCH_SIZE,
                "evaluated_this_batch": 0,
                "classified_requirement": 0,
                "classified_supply": 0,
                "classified_ambiguous": 0,
                "updated_purity_role": 0,
                "updated_transaction": 0,
                "remaining_unprocessed": 0,
                "complete": True,
                "source_data_modified": False,
                "next_step": "Run V2.4.3 prioritization",
            }

        binds, params = [], {}
        for i, rid in enumerate(ids):
            key = f"id{i}"
            binds.append(f"CAST(:{key} AS uuid)")
            params[key] = rid

        with source_engine.connect() as src:
            rows = {
                str(r["id"]): dict(r)
                for r in src.execute(text(f"""
                  SELECT id,raw_listing_text,summary
                  FROM wai_listings
                  WHERE id IN ({",".join(binds)})
                """), params).mappings().all()
            }

        checkpoints = []
        updates = []
        stats = {
            "req": 0, "supply": 0, "ambiguous": 0,
            "role_updates": 0, "tx_updates": 0, "source_missing": 0,
        }

        for rid in ids:
            row = rows.get(rid)
            if row is None:
                stats["source_missing"] += 1
                rec = {
                    "classified_role": "AMBIGUOUS",
                    "role_confidence": 0,
                    "contact_role": None,
                    "contact_role_confidence": 0,
                    "transaction_hint": None,
                    "transaction_confidence": 0,
                    "requirement_score": 0,
                    "supply_score": 0,
                    "ambiguity_score": 100,
                    "evidence": {},
                    "risk_flags": ["source_row_not_found"],
                }
            else:
                raw = row.get("raw_listing_text") or row.get("summary") or ""
                rec = classify_requirement_supply(raw)

            if rec["classified_role"] == "REQUIREMENT":
                stats["req"] += 1
            elif rec["classified_role"] == "SUPPLY":
                stats["supply"] += 1
            else:
                stats["ambiguous"] += 1

            checkpoints.append({
                "id": rid,
                "role": rec["classified_role"],
                "role_conf": rec["role_confidence"],
                "contact_role": rec["contact_role"],
                "contact_conf": rec["contact_role_confidence"],
                "tx": rec["transaction_hint"],
                "tx_conf": rec["transaction_confidence"],
                "req_score": rec["requirement_score"],
                "supply_score": rec["supply_score"],
                "ambiguity": rec["ambiguity_score"],
                "evidence": json.dumps(rec["evidence"]),
                "risks": json.dumps(rec["risk_flags"]),
                "version": MODULE_VERSION,
            })
            updates.append({
                "id": rid,
                "role": rec["classified_role"],
                "role_conf": rec["role_confidence"],
                "tx": rec["transaction_hint"],
                "tx_conf": rec["transaction_confidence"],
            })

        with primary_engine.begin() as c:
            c.execute(text("""
              INSERT INTO ai_whatsapp_requirement_supply_intelligence(
                listing_id,classified_role,role_confidence,contact_role,
                contact_role_confidence,transaction_hint,transaction_confidence,
                requirement_score,supply_score,ambiguity_score,evidence,risk_flags,
                model_version,evaluated_at
              )
              VALUES(
                CAST(:id AS uuid),CAST(:role AS TEXT),CAST(:role_conf AS NUMERIC),
                CAST(:contact_role AS TEXT),CAST(:contact_conf AS NUMERIC),
                CAST(:tx AS TEXT),CAST(:tx_conf AS NUMERIC),
                CAST(:req_score AS NUMERIC),CAST(:supply_score AS NUMERIC),
                CAST(:ambiguity AS NUMERIC),CAST(:evidence AS jsonb),
                CAST(:risks AS jsonb),:version,NOW()
              )
              ON CONFLICT(listing_id) DO UPDATE SET
                classified_role=EXCLUDED.classified_role,
                role_confidence=EXCLUDED.role_confidence,
                contact_role=EXCLUDED.contact_role,
                contact_role_confidence=EXCLUDED.contact_role_confidence,
                transaction_hint=EXCLUDED.transaction_hint,
                transaction_confidence=EXCLUDED.transaction_confidence,
                requirement_score=EXCLUDED.requirement_score,
                supply_score=EXCLUDED.supply_score,
                ambiguity_score=EXCLUDED.ambiguity_score,
                evidence=EXCLUDED.evidence,
                risk_flags=EXCLUDED.risk_flags,
                model_version=EXCLUDED.model_version,
                evaluated_at=NOW()
            """), checkpoints)

            for u in updates:
                r = c.execute(text("""
                  UPDATE ai_whatsapp_purity
                  SET
                    recovered_role=CASE
                      WHEN (recovered_role IS NULL OR recovered_role='UNKNOWN')
                       AND CAST(:role AS TEXT) IN ('REQUIREMENT','SUPPLY')
                       AND CAST(:role_conf AS NUMERIC) >= 90
                      THEN CAST(:role AS TEXT)
                      ELSE recovered_role
                    END,
                    recovered_transaction=CASE
                      WHEN (recovered_transaction IS NULL OR recovered_transaction='UNKNOWN')
                       AND CAST(:tx AS TEXT) IS NOT NULL
                       AND CAST(:tx_conf AS NUMERIC) >= 90
                      THEN CAST(:tx AS TEXT)
                      ELSE recovered_transaction
                    END,
                    transaction_confidence=CASE
                      WHEN CAST(:tx AS TEXT) IS NOT NULL
                       AND CAST(:tx_conf AS NUMERIC) > COALESCE(transaction_confidence,0)
                      THEN CAST(:tx_conf AS NUMERIC)
                      ELSE transaction_confidence
                    END,
                    purity_score=LEAST(
                      100,
                      COALESCE(purity_score,0)
                      + CASE WHEN CAST(:role AS TEXT) IN ('REQUIREMENT','SUPPLY')
                                  AND CAST(:role_conf AS NUMERIC) >= 90 THEN 4 ELSE 0 END
                      + CASE WHEN CAST(:tx AS TEXT) IS NOT NULL
                                  AND CAST(:tx_conf AS NUMERIC) >= 90 THEN 3 ELSE 0 END
                    ),
                    last_recovered_at=NOW()
                  WHERE listing_id=CAST(:id AS uuid)
                  RETURNING recovered_role,recovered_transaction
                """), u).mappings().one_or_none()

                if r:
                    if u["role"] in ("REQUIREMENT","SUPPLY") and r["recovered_role"] == u["role"]:
                        stats["role_updates"] += 1
                    if u["tx"] and r["recovered_transaction"] == u["tx"]:
                        stats["tx_updates"] += 1

        with primary_engine.connect() as c:
            remaining = c.execute(text("""
              SELECT COUNT(*)::int
              FROM ai_whatsapp_purity p
              LEFT JOIN ai_whatsapp_requirement_supply_intelligence a
                ON a.listing_id=p.listing_id
               AND a.model_version=:version
              WHERE a.listing_id IS NULL
                AND (
                     p.recovered_role IS NULL
                  OR p.recovered_role='UNKNOWN'
                  OR p.review_status IN ('NEEDS_REVIEW','LOW_CONFIDENCE','UNKNOWN')
                )
            """), {"version": MODULE_VERSION}).scalar() or 0

        return {
            "version": MODULE_VERSION,
            "batch_size": BATCH_SIZE,
            "evaluated_this_batch": len(ids),
            "classified_requirement": stats["req"],
            "classified_supply": stats["supply"],
            "classified_ambiguous": stats["ambiguous"],
            "updated_purity_role": stats["role_updates"],
            "updated_transaction": stats["tx_updates"],
            "source_rows_missing": stats["source_missing"],
            "remaining_unprocessed": int(remaining),
            "complete": int(remaining) == 0,
            "source_data_modified": False,
            "next_step": "Run V2.4.3 prioritization" if int(remaining) == 0 else "Run next requirement/supply batch",
        }
    finally:
        if owned:
            source_engine.dispose()

def summary(engine):
    ensure_schema(engine)
    with engine.connect() as c:
        r = c.execute(text("""
          SELECT
            COUNT(*)::int total,
            COUNT(*) FILTER (WHERE classified_role='REQUIREMENT')::int requirements,
            COUNT(*) FILTER (WHERE classified_role='SUPPLY')::int supply,
            COUNT(*) FILTER (WHERE classified_role='AMBIGUOUS')::int ambiguous,
            COUNT(*) FILTER (WHERE contact_role='OWNER')::int owner_rows,
            COUNT(*) FILTER (WHERE contact_role='BROKER')::int broker_rows
          FROM ai_whatsapp_requirement_supply_intelligence
          WHERE model_version=:version
        """), {"version": MODULE_VERSION}).mappings().one()
    return dict(r)

def register_requirement_supply_routes(core):
    app, engine = core.app, core.engine
    ensure_schema(engine)

    @app.post("/api/v2/intelligence/whatsapp-role/run")
    def run(req: Request):
        if hasattr(core, "need_login"):
            core.need_login(req)
        return run_requirement_supply_intelligence(engine)

    @app.get("/api/v2/intelligence/whatsapp-role/summary")
    def get_summary(req: Request):
        if hasattr(core, "need_login"):
            core.need_login(req)
        return {"version": MODULE_VERSION, **summary(engine)}

    @app.get("/v2/whatsapp-role-intelligence", response_class=HTMLResponse)
    def page(req: Request):
        if hasattr(core, "need_login"):
            core.need_login(req)
        s = summary(engine)
        return HTMLResponse(f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Requirement/Supply Intelligence</title></head>
<body style="font-family:Arial;background:#f5f7fa">
<div style="max-width:1000px;margin:30px auto;background:white;padding:25px;border-radius:12px">
<h1>V2.4.6 Requirement / Supply Intelligence</h1>
<p>Evaluated: <b>{s.get('total',0)}</b></p>
<p>Requirements: <b>{s.get('requirements',0)}</b></p>
<p>Supply: <b>{s.get('supply',0)}</b></p>
<p>Ambiguous: <b>{s.get('ambiguous',0)}</b></p>
<p>Owner-tagged: <b>{s.get('owner_rows',0)}</b> · Broker-tagged: <b>{s.get('broker_rows',0)}</b></p>
<p>Raw WhatsApp source data is never modified.</p>
</div></body></html>""")

    return app
