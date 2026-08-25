
import os
import re
import json
from sqlalchemy import create_engine, text
from fastapi import Request
from fastapi.responses import HTMLResponse

MODULE_VERSION = "2.4.5-CRITICAL-FIELD-RECOVERY"
BATCH_SIZE = 250

LOCATION_ALIASES = {
    "cp": "Connaught Place",
    "connaught place": "Connaught Place",
    "greater kailash 1": "Greater Kailash 1",
    "gk 1": "Greater Kailash 1",
    "gk1": "Greater Kailash 1",
    "greater kailash 2": "Greater Kailash 2",
    "gk 2": "Greater Kailash 2",
    "gk2": "Greater Kailash 2",
    "hauz khas": "Hauz Khas",
    "kailash colony": "Kailash Colony",
    "lajpat nagar": "Lajpat Nagar",
    "south extension": "South Extension",
    "pitampura": "Pitampura",
    "kohat enclave": "Kohat Enclave",
    "vasant kunj": "Vasant Kunj",
    "vasant vihar": "Vasant Vihar",
    "defence colony": "Defence Colony",
    "new friends colony": "New Friends Colony",
    "friends colony": "Friends Colony",
    "rajouri garden": "Rajouri Garden",
    "punjabi bagh": "Punjabi Bagh",
    "karol bagh": "Karol Bagh",
    "saket": "Saket",
    "malviya nagar": "Malviya Nagar",
    "green park": "Green Park",
    "nehru place": "Nehru Place",
    "okhla": "Okhla",
    "noida": "Noida",
    "gurgaon": "Gurgaon",
    "gurugram": "Gurgaon",
    "faridabad": "Faridabad",
    "dwarka": "Dwarka",
    "rohini": "Rohini",
    "janakpuri": "Janakpuri",
    "paschim vihar": "Paschim Vihar",
    "south delhi": "South Delhi",
    "west delhi": "West Delhi",
    "north delhi": "North Delhi",
    "east delhi": "East Delhi",
    "central delhi": "Central Delhi",
    "goa": "Goa",
    "north goa": "North Goa",
    "south goa": "South Goa",
    "siolim": "Siolim",
    "assagao": "Assagao",
    "vagator": "Vagator",
    "morjim": "Morjim",
    "anjuna": "Anjuna",
    "panjim": "Panjim",
}

PROPERTY_TYPE_PATTERNS = [
    ("RESTAURANT", [r"\brestaurant\b", r"\bfine\s*dine\b", r"\bcafe\b", r"\bcafé\b", r"\blounge\b", r"\bclub\b"]),
    ("RETAIL_SHOP", [r"\bshop\b", r"\bshowroom\b", r"\bretail\b", r"\bhigh\s*street\b"]),
    ("OFFICE", [r"\boffice\b", r"\bworkspace\b", r"\bcowork(?:ing)?\b", r"\bcommercial\s*office\b"]),
    ("WAREHOUSE", [r"\bwarehouse\b", r"\bgodown\b", r"\blogistics\b"]),
    ("BANQUET", [r"\bbanquet\b", r"\bmarriage\s*hall\b", r"\bwedding\s*venue\b"]),
    ("HOTEL", [r"\bhotel\b", r"\bguest\s*house\b", r"\bguesthouse\b"]),
    ("VILLA", [r"\bvilla\b"]),
    ("APARTMENT", [r"\bapartment\b", r"\bflat\b", r"\b\d+\s*bhk\b"]),
    ("PLOT", [r"\bplot\b", r"\bland\b"]),
    ("KOTHI", [r"\bkothi\b", r"\bbungalow\b", r"\bindependent\s*house\b"]),
    ("COMMERCIAL", [r"\bcommercial\b"]),
]

LEASE_WORDS = [
    r"\blease\b", r"\brent\b", r"\bon\s+rent\b", r"\btenant\b", r"\bleasing\b",
    r"\brental\b", r"\bmonthly\s*rent\b",
]
SALE_WORDS = [
    r"\bsale\b", r"\bfor\s+sale\b", r"\bsell\b", r"\bselling\b", r"\bbuyer\b",
    r"\bpurchase\b", r"\bbuy\b", r"\bout\s*right\s*sale\b", r"\boutright\s*sale\b",
]

REQUIREMENT_WORDS = [
    r"\brequired\b", r"\brequirement\b", r"\blooking\s+for\b", r"\bneed\b",
    r"\bwanted\b", r"\bseeking\b", r"\btenant\s+requirement\b", r"\bbuyer\s+requirement\b",
]
SUPPLY_WORDS = [
    r"\bavailable\b", r"\bfor\s+sale\b", r"\bfor\s+rent\b", r"\bfor\s+lease\b",
    r"\bproperty\s+available\b", r"\bshop\s+available\b", r"\boffice\s+available\b",
]

GENERIC_LOCATIONS = {"", "unknown", "other", "others", "india", "delhi ncr", "ncr", "na", "n/a", "none"}

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
        CREATE TABLE IF NOT EXISTS ai_whatsapp_critical_field_intelligence (
          listing_id UUID PRIMARY KEY,
          recovered_role TEXT,
          role_confidence NUMERIC(5,2),
          recovered_transaction TEXT,
          transaction_confidence NUMERIC(5,2),
          recovered_location TEXT,
          location_confidence NUMERIC(5,2),
          recovered_property_type TEXT,
          property_type_confidence NUMERIC(5,2),
          evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
          risk_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
          model_version TEXT NOT NULL,
          evaluated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """))
        c.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_ai_whatsapp_critical_field_model
        ON ai_whatsapp_critical_field_intelligence(model_version,evaluated_at DESC)
        """))

def _norm(s):
    return re.sub(r"\s+", " ", str(s or "").strip().lower())

def _contains_any(text_value, patterns):
    hits = []
    for p in patterns:
        if re.search(p, text_value, re.I):
            hits.append(p)
    return hits

def recover_role(raw):
    t = _norm(raw)
    req_hits = _contains_any(t, REQUIREMENT_WORDS)
    supply_hits = _contains_any(t, SUPPLY_WORDS)

    # Explicit requirement wording wins unless the sentence is a clear listing.
    if req_hits and not supply_hits:
        return "REQUIREMENT", 96, {"requirement_hits": req_hits}
    if supply_hits and not req_hits:
        return "SUPPLY", 94, {"supply_hits": supply_hits}
    if req_hits and supply_hits:
        # Example: "need property available" is ambiguous, keep conservative.
        return None, 0, {"requirement_hits": req_hits, "supply_hits": supply_hits}
    return None, 0, {}

def recover_transaction(raw):
    t = _norm(raw)
    lease_hits = _contains_any(t, LEASE_WORDS)
    sale_hits = _contains_any(t, SALE_WORDS)

    if lease_hits and sale_hits:
        return "LEASE_OR_SALE", 92, {"lease_hits": lease_hits, "sale_hits": sale_hits}
    if lease_hits:
        return "LEASE", 96, {"lease_hits": lease_hits}
    if sale_hits:
        return "SALE", 96, {"sale_hits": sale_hits}
    return None, 0, {}

def recover_location(raw):
    t = _norm(raw)
    found = []
    for alias, canonical in LOCATION_ALIASES.items():
        # Strong token/phrase boundary to prevent accidental CP-style false positives.
        pat = r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])"
        if re.search(pat, t, re.I):
            found.append((alias, canonical))

    if not found:
        return None, 0, {}

    # Prefer the longest alias because it is more specific.
    found.sort(key=lambda x: len(x[0]), reverse=True)
    unique = []
    for _, canonical in found:
        if canonical not in unique:
            unique.append(canonical)

    if len(unique) == 1:
        return unique[0], 98, {"location_hits": unique}
    # Multiple valid locations can be requirements. Preserve joined text.
    return " | ".join(unique[:5]), 90, {"location_hits": unique[:5]}

def recover_property_type(raw):
    t = _norm(raw)
    hits = []
    for canonical, patterns in PROPERTY_TYPE_PATTERNS:
        for p in patterns:
            if re.search(p, t, re.I):
                hits.append(canonical)
                break

    hits = list(dict.fromkeys(hits))
    if not hits:
        return None, 0, {}
    if len(hits) == 1:
        return hits[0], 96, {"property_type_hits": hits}

    # Specific types beat generic COMMERCIAL.
    specific = [x for x in hits if x != "COMMERCIAL"]
    if len(specific) == 1:
        return specific[0], 94, {"property_type_hits": hits}
    return None, 0, {"property_type_hits": hits}

def recover_critical_fields(raw):
    role, role_conf, role_ev = recover_role(raw)
    tx, tx_conf, tx_ev = recover_transaction(raw)
    loc, loc_conf, loc_ev = recover_location(raw)
    ptype, ptype_conf, ptype_ev = recover_property_type(raw)

    risks = []
    if not role:
        risks.append("role_unresolved")
    if not tx:
        risks.append("transaction_unresolved")
    if not loc:
        risks.append("location_unresolved")
    if not ptype:
        risks.append("property_type_unresolved")

    return {
        "recovered_role": role,
        "role_confidence": role_conf,
        "recovered_transaction": tx,
        "transaction_confidence": tx_conf,
        "recovered_location": loc,
        "location_confidence": loc_conf,
        "recovered_property_type": ptype,
        "property_type_confidence": ptype_conf,
        "evidence": {
            **role_ev, **tx_ev, **loc_ev, **ptype_ev
        },
        "risk_flags": risks,
    }

def run_critical_field_recovery(primary_engine):
    ensure_schema(primary_engine)
    source_engine, owned = _source_engine(primary_engine)

    try:
        with primary_engine.connect() as c:
            ids = [
                str(r["listing_id"])
                for r in c.execute(text("""
                  SELECT p.listing_id
                  FROM ai_whatsapp_purity p
                  LEFT JOIN ai_whatsapp_critical_field_intelligence a
                    ON a.listing_id=p.listing_id
                   AND a.model_version=:version
                  WHERE (
                       p.recovered_role IS NULL
                    OR p.recovered_role='UNKNOWN'
                    OR p.recovered_transaction IS NULL
                    OR p.recovered_transaction='UNKNOWN'
                    OR p.recovered_location IS NULL
                    OR LOWER(COALESCE(p.recovered_location,'')) IN ('','unknown','other','others','india','delhi ncr','ncr','na','n/a','none')
                    OR p.recovered_property_type IS NULL
                    OR p.recovered_property_type='UNKNOWN'
                  )
                    AND a.listing_id IS NULL
                  ORDER BY p.listing_id
                  LIMIT :lim
                """), {"version": MODULE_VERSION, "lim": BATCH_SIZE}).mappings().all()
            ]

        if not ids:
            return {
                "version": MODULE_VERSION,
                "batch_size": BATCH_SIZE,
                "evaluated_this_batch": 0,
                "updated_role": 0,
                "updated_transaction": 0,
                "updated_location": 0,
                "updated_property_type": 0,
                "remaining_unprocessed": 0,
                "complete": True,
                "source_data_modified": False,
                "next_step": "Run V2.4.3 prioritization",
            }

        binds = []
        params = {}
        for i, rid in enumerate(ids):
            k = f"id{i}"
            binds.append(f"CAST(:{k} AS uuid)")
            params[k] = rid

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
            "role": 0,
            "tx": 0,
            "loc": 0,
            "ptype": 0,
            "source_missing": 0,
        }

        for rid in ids:
            row = rows.get(rid)
            if row is None:
                stats["source_missing"] += 1
                rec = {
                    "recovered_role": None,
                    "role_confidence": 0,
                    "recovered_transaction": None,
                    "transaction_confidence": 0,
                    "recovered_location": None,
                    "location_confidence": 0,
                    "recovered_property_type": None,
                    "property_type_confidence": 0,
                    "evidence": {},
                    "risk_flags": ["source_row_not_found"],
                }
            else:
                raw = row.get("raw_listing_text") or row.get("summary") or ""
                rec = recover_critical_fields(raw)

            checkpoints.append({
                "id": rid,
                "role": rec["recovered_role"],
                "role_conf": rec["role_confidence"],
                "tx": rec["recovered_transaction"],
                "tx_conf": rec["transaction_confidence"],
                "loc": rec["recovered_location"],
                "loc_conf": rec["location_confidence"],
                "ptype": rec["recovered_property_type"],
                "ptype_conf": rec["property_type_confidence"],
                "evidence": json.dumps(rec["evidence"]),
                "risks": json.dumps(rec["risk_flags"]),
                "version": MODULE_VERSION,
            })

            updates.append({
                "id": rid,
                "role": rec["recovered_role"],
                "role_conf": rec["role_confidence"],
                "tx": rec["recovered_transaction"],
                "tx_conf": rec["transaction_confidence"],
                "loc": rec["recovered_location"],
                "loc_conf": rec["location_confidence"],
                "ptype": rec["recovered_property_type"],
                "ptype_conf": rec["property_type_confidence"],
            })

        with primary_engine.begin() as c:
            c.execute(text("""
              INSERT INTO ai_whatsapp_critical_field_intelligence(
                listing_id,recovered_role,role_confidence,recovered_transaction,
                transaction_confidence,recovered_location,location_confidence,
                recovered_property_type,property_type_confidence,evidence,risk_flags,
                model_version,evaluated_at
              )
              VALUES(
                CAST(:id AS uuid),:role,:role_conf,:tx,:tx_conf,:loc,:loc_conf,
                :ptype,:ptype_conf,CAST(:evidence AS jsonb),CAST(:risks AS jsonb),
                :version,NOW()
              )
              ON CONFLICT(listing_id) DO UPDATE SET
                recovered_role=EXCLUDED.recovered_role,
                role_confidence=EXCLUDED.role_confidence,
                recovered_transaction=EXCLUDED.recovered_transaction,
                transaction_confidence=EXCLUDED.transaction_confidence,
                recovered_location=EXCLUDED.recovered_location,
                location_confidence=EXCLUDED.location_confidence,
                recovered_property_type=EXCLUDED.recovered_property_type,
                property_type_confidence=EXCLUDED.property_type_confidence,
                evidence=EXCLUDED.evidence,
                risk_flags=EXCLUDED.risk_flags,
                model_version=EXCLUDED.model_version,
                evaluated_at=NOW()
            """), checkpoints)

            for u in updates:
                # Derived purity only. Fill missing/unknown fields conservatively.
                r = c.execute(text("""
                  UPDATE ai_whatsapp_purity
                  SET
                    recovered_role=CASE
                      WHEN (recovered_role IS NULL OR recovered_role='UNKNOWN')
                           AND :role IS NOT NULL AND :role_conf >= 94
                      THEN :role ELSE recovered_role END,

                    recovered_transaction=CASE
                      WHEN (recovered_transaction IS NULL OR recovered_transaction='UNKNOWN')
                           AND :tx IS NOT NULL AND :tx_conf >= 92
                      THEN :tx ELSE recovered_transaction END,

                    transaction_confidence=CASE
                      WHEN :tx IS NOT NULL AND :tx_conf > COALESCE(transaction_confidence,0)
                      THEN :tx_conf ELSE transaction_confidence END,

                    recovered_location=CASE
                      WHEN LOWER(COALESCE(recovered_location,'')) IN
                           ('','unknown','other','others','india','delhi ncr','ncr','na','n/a','none')
                           AND :loc IS NOT NULL AND :loc_conf >= 90
                      THEN :loc ELSE recovered_location END,

                    recovered_property_type=CASE
                      WHEN (recovered_property_type IS NULL OR recovered_property_type='UNKNOWN')
                           AND :ptype IS NOT NULL AND :ptype_conf >= 94
                      THEN :ptype ELSE recovered_property_type END,

                    property_type_confidence=CASE
                      WHEN :ptype IS NOT NULL AND :ptype_conf > COALESCE(property_type_confidence,0)
                      THEN :ptype_conf ELSE property_type_confidence END,

                    purity_score=LEAST(
                      100,
                      COALESCE(purity_score,0)
                      + CASE WHEN :role IS NOT NULL AND :role_conf >= 94 THEN 3 ELSE 0 END
                      + CASE WHEN :tx IS NOT NULL AND :tx_conf >= 92 THEN 4 ELSE 0 END
                      + CASE WHEN :loc IS NOT NULL AND :loc_conf >= 90 THEN 4 ELSE 0 END
                      + CASE WHEN :ptype IS NOT NULL AND :ptype_conf >= 94 THEN 4 ELSE 0 END
                    ),
                    last_recovered_at=NOW()
                  WHERE listing_id=CAST(:id AS uuid)
                  RETURNING
                    recovered_role,recovered_transaction,recovered_location,recovered_property_type
                """), u).mappings().one_or_none()

                if r:
                    if u["role"] and r["recovered_role"] == u["role"]:
                        stats["role"] += 1
                    if u["tx"] and r["recovered_transaction"] == u["tx"]:
                        stats["tx"] += 1
                    if u["loc"] and r["recovered_location"] == u["loc"]:
                        stats["loc"] += 1
                    if u["ptype"] and r["recovered_property_type"] == u["ptype"]:
                        stats["ptype"] += 1

        with primary_engine.connect() as c:
            remaining = c.execute(text("""
              SELECT COUNT(*)::int
              FROM ai_whatsapp_purity p
              LEFT JOIN ai_whatsapp_critical_field_intelligence a
                ON a.listing_id=p.listing_id
               AND a.model_version=:version
              WHERE (
                   p.recovered_role IS NULL
                OR p.recovered_role='UNKNOWN'
                OR p.recovered_transaction IS NULL
                OR p.recovered_transaction='UNKNOWN'
                OR p.recovered_location IS NULL
                OR LOWER(COALESCE(p.recovered_location,'')) IN ('','unknown','other','others','india','delhi ncr','ncr','na','n/a','none')
                OR p.recovered_property_type IS NULL
                OR p.recovered_property_type='UNKNOWN'
              )
                AND a.listing_id IS NULL
            """), {"version": MODULE_VERSION}).scalar() or 0

        return {
            "version": MODULE_VERSION,
            "batch_size": BATCH_SIZE,
            "evaluated_this_batch": len(ids),
            "updated_role": stats["role"],
            "updated_transaction": stats["tx"],
            "updated_location": stats["loc"],
            "updated_property_type": stats["ptype"],
            "source_rows_missing": stats["source_missing"],
            "remaining_unprocessed": int(remaining),
            "complete": int(remaining) == 0,
            "source_data_modified": False,
            "next_step": "Run V2.4.3 prioritization" if int(remaining) == 0 else "Run next critical-field batch",
        }
    finally:
        if owned:
            source_engine.dispose()

def critical_summary(engine):
    ensure_schema(engine)
    with engine.connect() as c:
        r = c.execute(text("""
          SELECT
            COUNT(*)::int total,
            COUNT(*) FILTER (WHERE recovered_role IS NOT NULL)::int role_rows,
            COUNT(*) FILTER (WHERE recovered_transaction IS NOT NULL)::int tx_rows,
            COUNT(*) FILTER (WHERE recovered_location IS NOT NULL)::int location_rows,
            COUNT(*) FILTER (WHERE recovered_property_type IS NOT NULL)::int property_type_rows
          FROM ai_whatsapp_critical_field_intelligence
          WHERE model_version=:version
        """), {"version": MODULE_VERSION}).mappings().one()
    return dict(r)

def register_critical_field_routes(core):
    app, engine = core.app, core.engine
    ensure_schema(engine)

    @app.post("/api/v2/intelligence/whatsapp-critical/run")
    def critical_run(req: Request):
        if hasattr(core, "need_login"):
            core.need_login(req)
        return run_critical_field_recovery(engine)

    @app.get("/api/v2/intelligence/whatsapp-critical/summary")
    def summary(req: Request):
        if hasattr(core, "need_login"):
            core.need_login(req)
        return {"version": MODULE_VERSION, **critical_summary(engine)}

    @app.get("/v2/whatsapp-critical-intelligence", response_class=HTMLResponse)
    def page(req: Request):
        if hasattr(core, "need_login"):
            core.need_login(req)
        s = critical_summary(engine)
        return HTMLResponse(f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Critical Field Recovery</title></head>
<body style="font-family:Arial;background:#f5f7fa">
<div style="max-width:1000px;margin:30px auto;background:white;padding:25px;border-radius:12px">
<h1>V2.4.5 Critical Field Recovery</h1>
<p>Evaluated rows: <b>{s.get('total',0)}</b></p>
<p>Role recovered: <b>{s.get('role_rows',0)}</b></p>
<p>Transaction recovered: <b>{s.get('tx_rows',0)}</b></p>
<p>Location recovered: <b>{s.get('location_rows',0)}</b></p>
<p>Property type recovered: <b>{s.get('property_type_rows',0)}</b></p>
<p>Raw WhatsApp source data is never modified.</p>
</div></body></html>""")

    return app
