
import os
import re
import json
import hashlib
from sqlalchemy import create_engine, text
from fastapi import Request
from fastapi.responses import HTMLResponse

MODULE_VERSION = "2.4.7A-DUPLICATE-SAFETY-CALIBRATION"
BATCH_SIZE = 250

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
        CREATE TABLE IF NOT EXISTS ai_whatsapp_entity_resolution (
          listing_id UUID PRIMARY KEY,
          entity_kind TEXT NOT NULL,
          canonical_entity_key TEXT NOT NULL,
          duplicate_type TEXT NOT NULL,
          duplicate_confidence NUMERIC(5,2) NOT NULL DEFAULT 0,
          canonical_listing_id UUID,
          suppress_from_matcher BOOLEAN NOT NULL DEFAULT FALSE,
          exact_fingerprint TEXT,
          fuzzy_fingerprint TEXT,
          phone_fingerprint TEXT,
          evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
          model_version TEXT NOT NULL,
          evaluated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """))
        c.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_ai_whatsapp_entity_resolution_key
        ON ai_whatsapp_entity_resolution(canonical_entity_key)
        """))
        c.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_ai_whatsapp_entity_resolution_suppress
        ON ai_whatsapp_entity_resolution(suppress_from_matcher,duplicate_type)
        """))

def _norm(v):
    return re.sub(r"\s+", " ", str(v or "").strip().lower())

def _slug(v):
    t = _norm(v)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", "-", t).strip("-")

def _phone_tokens(raw):
    nums = re.findall(r"(?:\+?91[\s-]?)?([6-9]\d{9})", str(raw or ""))
    return sorted(set(nums))

def _round_area(v):
    try:
        x = float(v)
        if x <= 0:
            return ""
        if x < 1000:
            step = 50
        elif x < 5000:
            step = 100
        elif x < 20000:
            step = 250
        else:
            step = 500
        return str(int(round(x / step) * step))
    except Exception:
        return ""

def _sha(*parts):
    s = "|".join(str(x or "") for x in parts)
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()

def derive_entity_identity(row, raw_text):
    role = _norm(row.get("recovered_role")).upper()
    tx = _norm(row.get("recovered_transaction")).upper()
    loc = _slug(row.get("recovered_location"))
    ptype = _slug(row.get("recovered_property_type"))
    amin = _round_area(row.get("recovered_area_min_sqft"))
    amax = _round_area(row.get("recovered_area_max_sqft"))
    floor = _slug(row.get("recovered_required_floor"))
    suitable = _slug(row.get("recovered_suitable_for"))
    phones = _phone_tokens(raw_text)

    if role == "REQUIREMENT":
        entity_kind = "REQUIREMENT"
    elif role == "SUPPLY":
        entity_kind = "PROPERTY"
    else:
        entity_kind = "UNKNOWN"

    raw_clean = _norm(raw_text)
    raw_clean = re.sub(r"(?:\+?91[\s-]?)?[6-9]\d{9}", " PHONE ", raw_clean)
    raw_clean = re.sub(r"\s+", " ", raw_clean).strip()

    exact_fp = _sha(entity_kind, tx, loc, ptype, amin, amax, floor, suitable, raw_clean)
    phone_fp = _sha(entity_kind, tx, loc, ptype, amin, amax, ",".join(phones)) if phones else None
    fuzzy_fp = _sha(entity_kind, tx, loc, ptype, amin, amax, floor, suitable)
    canonical_key = _sha(entity_kind, tx, loc, ptype, amin, amax, floor, suitable)

    return {
        "entity_kind": entity_kind,
        "canonical_entity_key": canonical_key,
        "exact_fingerprint": exact_fp,
        "fuzzy_fingerprint": fuzzy_fp,
        "phone_fingerprint": phone_fp,
        "phones": phones,
        "normalized": {
            "transaction": tx,
            "location": loc,
            "property_type": ptype,
            "area_min": amin,
            "area_max": amax,
            "floor": floor,
            "suitable_for": suitable,
        }
    }

def _strong_identity(ident):
    n = ident["normalized"]
    return bool(
        ident["entity_kind"] in ("PROPERTY","REQUIREMENT")
        and n["transaction"]
        and n["location"]
        and n["property_type"]
        and n["area_min"]
        and n["area_max"]
    )

def run_entity_resolution(primary_engine):
    ensure_schema(primary_engine)
    source_engine, owned = _source_engine(primary_engine)

    try:
        with primary_engine.connect() as c:
            ids = [
                str(r["listing_id"])
                for r in c.execute(text("""
                  SELECT p.listing_id
                  FROM ai_whatsapp_purity p
                  LEFT JOIN ai_whatsapp_entity_resolution e
                    ON e.listing_id=p.listing_id
                   AND e.model_version=:version
                  WHERE e.listing_id IS NULL
                    AND p.review_status IN ('USABLE','NEEDS_REVIEW','LOW_CONFIDENCE','UNKNOWN')
                  ORDER BY p.listing_id
                  LIMIT :lim
                """), {"version": MODULE_VERSION, "lim": BATCH_SIZE}).mappings().all()
            ]

        if not ids:
            return {
                "version": MODULE_VERSION,
                "batch_size": BATCH_SIZE,
                "evaluated_this_batch": 0,
                "canonical_entities_created": 0,
                "exact_duplicates": 0,
                "high_conf_duplicates": 0,
                "possible_duplicates": 0,
                "unique_rows": 0,
                "suppressed_from_matcher": 0,
                "remaining_unprocessed": 0,
                "complete": True,
                "source_data_modified": False,
                "next_step": "Inspect summary, then V2.5 production matcher",
            }

        bind_names, params = [], {}
        for i, rid in enumerate(ids):
            k = f"id{i}"
            bind_names.append(f"CAST(:{k} AS uuid)")
            params[k] = rid

        with primary_engine.connect() as c:
            purity_rows = {
                str(r["listing_id"]): dict(r)
                for r in c.execute(text(f"""
                  SELECT listing_id,recovered_role,recovered_transaction,recovered_location,
                         recovered_property_type,recovered_area_min_sqft,recovered_area_max_sqft,
                         recovered_required_floor,recovered_suitable_for,purity_score
                  FROM ai_whatsapp_purity
                  WHERE listing_id IN ({",".join(bind_names)})
                """), params).mappings().all()
            }

        with source_engine.connect() as src:
            source_rows = {
                str(r["id"]): dict(r)
                for r in src.execute(text(f"""
                  SELECT id,raw_listing_text,summary
                  FROM wai_listings
                  WHERE id IN ({",".join(bind_names)})
                """), params).mappings().all()
            }

        with primary_engine.connect() as c:
            existing = c.execute(text("""
              SELECT listing_id,canonical_entity_key,exact_fingerprint,fuzzy_fingerprint,
                     phone_fingerprint,entity_kind,duplicate_type,suppress_from_matcher
              FROM ai_whatsapp_entity_resolution
              WHERE model_version=:version
              ORDER BY evaluated_at,listing_id
            """), {"version": MODULE_VERSION}).mappings().all()

        by_exact, by_phone, by_fuzzy, canonical_by_key = {}, {}, {}, {}
        for r in existing:
            lid = str(r["listing_id"])
            if r["exact_fingerprint"] and r["exact_fingerprint"] not in by_exact:
                by_exact[r["exact_fingerprint"]] = lid
            if r["phone_fingerprint"] and r["phone_fingerprint"] not in by_phone:
                by_phone[r["phone_fingerprint"]] = lid
            if r["fuzzy_fingerprint"] and r["fuzzy_fingerprint"] not in by_fuzzy:
                by_fuzzy[r["fuzzy_fingerprint"]] = lid
            if r["canonical_entity_key"] and r["canonical_entity_key"] not in canonical_by_key:
                canonical_by_key[r["canonical_entity_key"]] = lid

        inserts = []
        stats = {
            "canonical": 0, "exact": 0, "high": 0, "possible": 0,
            "unique": 0, "suppressed": 0, "source_missing": 0
        }

        for rid in ids:
            p = purity_rows.get(rid, {})
            s = source_rows.get(rid, {})
            if not s:
                stats["source_missing"] += 1
            raw = s.get("raw_listing_text") or s.get("summary") or ""
            ident = derive_entity_identity(p, raw)

            dtype = "UNIQUE"
            conf = 0
            canonical_listing_id = rid
            suppress = False
            evidence = {
                "normalized": ident["normalized"],
                "phones": ident["phones"],
                "suppression_rule": None,
            }

            if ident["exact_fingerprint"] in by_exact:
                dtype = "EXACT_DUPLICATE"
                conf = 100
                canonical_listing_id = by_exact[ident["exact_fingerprint"]]
                suppress = True
                evidence["suppression_rule"] = "exact_fingerprint"
                stats["exact"] += 1

            elif (
                ident["phone_fingerprint"]
                and ident["phone_fingerprint"] in by_phone
                and _strong_identity(ident)
            ):
                dtype = "HIGH_CONF_DUPLICATE"
                conf = 97
                canonical_listing_id = by_phone[ident["phone_fingerprint"]]
                suppress = True
                evidence["suppression_rule"] = "phone+transaction+location+type+area"
                stats["high"] += 1

            elif ident["fuzzy_fingerprint"] in by_fuzzy:
                dtype = "POSSIBLE_DUPLICATE"
                conf = 86
                canonical_listing_id = by_fuzzy[ident["fuzzy_fingerprint"]]
                suppress = False
                evidence["suppression_rule"] = "none_fuzzy_review_only"
                stats["possible"] += 1

            elif ident["canonical_entity_key"] in canonical_by_key:
                dtype = "POSSIBLE_DUPLICATE"
                conf = 82
                canonical_listing_id = canonical_by_key[ident["canonical_entity_key"]]
                suppress = False
                evidence["suppression_rule"] = "none_canonical_review_only"
                stats["possible"] += 1

            else:
                stats["canonical"] += 1
                stats["unique"] += 1

            if suppress:
                stats["suppressed"] += 1

            if ident["exact_fingerprint"] and ident["exact_fingerprint"] not in by_exact:
                by_exact[ident["exact_fingerprint"]] = canonical_listing_id
            if ident["phone_fingerprint"] and ident["phone_fingerprint"] not in by_phone:
                by_phone[ident["phone_fingerprint"]] = canonical_listing_id
            if ident["fuzzy_fingerprint"] and ident["fuzzy_fingerprint"] not in by_fuzzy:
                by_fuzzy[ident["fuzzy_fingerprint"]] = canonical_listing_id
            if ident["canonical_entity_key"] and ident["canonical_entity_key"] not in canonical_by_key:
                canonical_by_key[ident["canonical_entity_key"]] = canonical_listing_id

            inserts.append({
                "id": rid,
                "kind": ident["entity_kind"],
                "key": ident["canonical_entity_key"],
                "dtype": dtype,
                "conf": conf,
                "canonical": canonical_listing_id,
                "suppress": suppress,
                "exact": ident["exact_fingerprint"],
                "fuzzy": ident["fuzzy_fingerprint"],
                "phone": ident["phone_fingerprint"],
                "evidence": json.dumps(evidence),
                "version": MODULE_VERSION,
            })

        with primary_engine.begin() as c:
            c.execute(text("""
              INSERT INTO ai_whatsapp_entity_resolution(
                listing_id,entity_kind,canonical_entity_key,duplicate_type,
                duplicate_confidence,canonical_listing_id,suppress_from_matcher,
                exact_fingerprint,fuzzy_fingerprint,phone_fingerprint,evidence,
                model_version,evaluated_at
              )
              VALUES(
                CAST(:id AS uuid),CAST(:kind AS TEXT),CAST(:key AS TEXT),
                CAST(:dtype AS TEXT),CAST(:conf AS NUMERIC),CAST(:canonical AS uuid),
                CAST(:suppress AS BOOLEAN),CAST(:exact AS TEXT),CAST(:fuzzy AS TEXT),
                CAST(:phone AS TEXT),CAST(:evidence AS jsonb),:version,NOW()
              )
              ON CONFLICT(listing_id) DO UPDATE SET
                entity_kind=EXCLUDED.entity_kind,
                canonical_entity_key=EXCLUDED.canonical_entity_key,
                duplicate_type=EXCLUDED.duplicate_type,
                duplicate_confidence=EXCLUDED.duplicate_confidence,
                canonical_listing_id=EXCLUDED.canonical_listing_id,
                suppress_from_matcher=EXCLUDED.suppress_from_matcher,
                exact_fingerprint=EXCLUDED.exact_fingerprint,
                fuzzy_fingerprint=EXCLUDED.fuzzy_fingerprint,
                phone_fingerprint=EXCLUDED.phone_fingerprint,
                evidence=EXCLUDED.evidence,
                model_version=EXCLUDED.model_version,
                evaluated_at=NOW()
            """), inserts)

        with primary_engine.connect() as c:
            remaining = c.execute(text("""
              SELECT COUNT(*)::int
              FROM ai_whatsapp_purity p
              LEFT JOIN ai_whatsapp_entity_resolution e
                ON e.listing_id=p.listing_id
               AND e.model_version=:version
              WHERE e.listing_id IS NULL
                AND p.review_status IN ('USABLE','NEEDS_REVIEW','LOW_CONFIDENCE','UNKNOWN')
            """), {"version": MODULE_VERSION}).scalar() or 0

        return {
            "version": MODULE_VERSION,
            "batch_size": BATCH_SIZE,
            "evaluated_this_batch": len(ids),
            "canonical_entities_created": stats["canonical"],
            "exact_duplicates": stats["exact"],
            "high_conf_duplicates": stats["high"],
            "possible_duplicates": stats["possible"],
            "unique_rows": stats["unique"],
            "suppressed_from_matcher": stats["suppressed"],
            "source_rows_missing": stats["source_missing"],
            "remaining_unprocessed": int(remaining),
            "complete": int(remaining) == 0,
            "source_data_modified": False,
            "next_step": "Inspect summary, then V2.5 production matcher" if int(remaining) == 0 else "Run next duplicate-safety batch",
        }
    finally:
        if owned:
            source_engine.dispose()

def entity_summary(engine):
    ensure_schema(engine)
    with engine.connect() as c:
        r = c.execute(text("""
          SELECT
            COUNT(*)::int total,
            COUNT(*) FILTER (WHERE duplicate_type='UNIQUE')::int unique_rows,
            COUNT(*) FILTER (WHERE duplicate_type='EXACT_DUPLICATE')::int exact_duplicates,
            COUNT(*) FILTER (WHERE duplicate_type='HIGH_CONF_DUPLICATE')::int high_conf_duplicates,
            COUNT(*) FILTER (WHERE duplicate_type='POSSIBLE_DUPLICATE')::int possible_duplicates,
            COUNT(*) FILTER (WHERE suppress_from_matcher)::int suppressed,
            COUNT(DISTINCT canonical_entity_key)::int canonical_entities
          FROM ai_whatsapp_entity_resolution
          WHERE model_version=:version
        """), {"version": MODULE_VERSION}).mappings().one()
    return dict(r)

def register_entity_resolution_routes(core):
    app, engine = core.app, core.engine
    ensure_schema(engine)

    @app.post("/api/v2/intelligence/whatsapp-entities-safe/run")
    def run(req: Request):
        if hasattr(core, "need_login"):
            core.need_login(req)
        return run_entity_resolution(engine)

    @app.get("/api/v2/intelligence/whatsapp-entities-safe/summary")
    def get_summary(req: Request):
        if hasattr(core, "need_login"):
            core.need_login(req)
        return {"version": MODULE_VERSION, **entity_summary(engine)}

    @app.get("/v2/whatsapp-entity-resolution-safe", response_class=HTMLResponse)
    def page(req: Request):
        if hasattr(core, "need_login"):
            core.need_login(req)
        s = entity_summary(engine)
        return HTMLResponse(f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Duplicate Safety Calibration</title></head>
<body style="font-family:Arial;background:#f5f7fa">
<div style="max-width:1000px;margin:30px auto;background:white;padding:25px;border-radius:12px">
<h1>V2.4.7A Duplicate Safety Calibration</h1>
<p>Total evaluated: <b>{s.get('total',0)}</b></p>
<p>Canonical entities: <b>{s.get('canonical_entities',0)}</b></p>
<p>Unique rows: <b>{s.get('unique_rows',0)}</b></p>
<p>Exact duplicates: <b>{s.get('exact_duplicates',0)}</b></p>
<p>High-confidence duplicates: <b>{s.get('high_conf_duplicates',0)}</b></p>
<p>Possible duplicates: <b>{s.get('possible_duplicates',0)}</b></p>
<p>Actually suppressed: <b>{s.get('suppressed',0)}</b></p>
<p>Possible duplicates remain matcher-visible.</p>
</div></body></html>""")

    return app
