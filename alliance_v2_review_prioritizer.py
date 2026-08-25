
import html
import re
from sqlalchemy import text
from fastapi import Request, Query, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from alliance_v2_whatsapp_review_queue import (
    ensure_review_schema, save_decision
)

MODULE_VERSION = "2.4.3-CALIBRATION-ENGINE"

BUCKETS = {
    "AUTO_APPROVE_CANDIDATE",
    "QUICK_REVIEW",
    "DEEP_REVIEW",
    "MISSING_CRITICAL_DATA",
    "REJECT_NOISE",
}

NOISE_PHRASES = [
    "good morning", "good evening", "good night", "happy birthday",
    "congratulations", "festival wishes", "breaking news", "market news",
    "real estate news", "subscribe", "youtube", "webinar", "seminar",
    "training session", "job opening", "hiring", "vacancy", "loan available",
    "home loan", "insurance", "political", "election", "stock market", "crypto",
    "motivational", "quote of the day", "thank you", "thanks everyone",
    "welcome", "meeting reminder", "event invite", "join us", "download app",
]

PROPERTY_SIGNALS = [
    "sale", "rent", "lease", "property", "shop", "office", "floor", "plot",
    "apartment", "flat", "villa", "showroom", "warehouse", "restaurant",
    "commercial", "residential", "sqft", "sq ft", "sqyd", "acre", "bhk",
    "require", "required", "looking for", "need", "tenant", "buyer",
    "available", "owner", "broker", "frontage", "ground floor", "first floor",
]

GENERIC_LOCATIONS = {
    "", "other", "others", "unknown", "na", "n/a", "none",
    "delhi ncr", "india",
}

def _s(v):
    return str(v or "").strip()

def _f(v, default=0.0):
    try:
        if v is None:
            return default
        x = float(v)
        if x != x:
            return default
        return x
    except Exception:
        return default

def ensure_calibration_schema(engine):
    ensure_review_schema(engine)
    with engine.begin() as c:
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_whatsapp_review_priority (
          listing_id UUID PRIMARY KEY,
          priority_score NUMERIC(6,2) NOT NULL DEFAULT 0,
          bucket TEXT NOT NULL,
          auto_approve_safe BOOLEAN NOT NULL DEFAULT FALSE,
          reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
          risk_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
          duplicate_cluster_size INTEGER NOT NULL DEFAULT 1,
          model_version TEXT NOT NULL,
          evaluated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """))
        c.execute(text("""
          CREATE INDEX IF NOT EXISTS ix_ai_whatsapp_review_priority_bucket
          ON ai_whatsapp_review_priority(bucket,priority_score DESC)
        """))

def run_prioritization(engine):
    """
    Bulk SQL calibration.
    This remains decoupled from rebuild-index.
    """
    ensure_calibration_schema(engine)

    with engine.begin() as c:
        result = c.execute(text("""
        WITH dup AS (
          SELECT duplicate_cluster_key,COUNT(*)::int AS duplicate_cluster_size
          FROM ai_whatsapp_purity
          WHERE duplicate_cluster_key IS NOT NULL
            AND duplicate_cluster_key <> ''
          GROUP BY duplicate_cluster_key
        ),
        base AS (
          SELECT
            p.listing_id,
            p.review_status,
            COALESCE(p.purity_score,0)::numeric AS purity_score,
            COALESCE(p.transaction_confidence,0)::numeric AS tx_conf,
            COALESCE(p.property_type_confidence,0)::numeric AS type_conf,
            UPPER(COALESCE(p.recovered_role,'')) AS role,
            UPPER(COALESCE(p.recovered_transaction,'')) AS tx,
            COALESCE(p.recovered_location,'') AS loc,
            UPPER(COALESCE(p.recovered_property_type,'')) AS ptype,
            COALESCE(p.recovered_area_min_sqft,0)::numeric AS amin,
            COALESCE(p.recovered_area_max_sqft,0)::numeric AS amax,
            LOWER(COALESCE(p.raw_text,'')) AS raw,
            COALESCE(dq.duplicate_cluster_size,1)::int AS dup_size
          FROM ai_whatsapp_purity p
          LEFT JOIN ai_whatsapp_review_decision d ON d.listing_id=p.listing_id
          LEFT JOIN dup dq ON dq.duplicate_cluster_key=p.duplicate_cluster_key
          WHERE COALESCE(d.decision,'PENDING')='PENDING'
            AND p.review_status IN ('NEEDS_REVIEW','LOW_CONFIDENCE','UNKNOWN')
        ),
        feat AS (
          SELECT *,
            (
              CASE WHEN role IN ('SUPPLY','REQUIREMENT') THEN 15 ELSE 0 END +
              CASE WHEN tx IN ('SALE','LEASE','LEASE_OR_SALE') THEN 15 ELSE 0 END +
              CASE WHEN tx_conf >= 95 THEN 12 WHEN tx_conf >= 90 THEN 10 WHEN tx_conf >= 75 THEN 7 ELSE 0 END +
              CASE WHEN ptype <> '' AND ptype <> 'UNKNOWN' THEN 12 ELSE 0 END +
              CASE WHEN type_conf >= 95 THEN 10 WHEN type_conf >= 90 THEN 8 WHEN type_conf >= 75 THEN 5 ELSE 0 END +
              CASE WHEN LOWER(loc) NOT IN ('','other','others','unknown','na','n/a','none','delhi ncr','india') THEN 15 ELSE 0 END +
              CASE WHEN amin > 0 AND amax > 0 AND amin <= amax AND amax <= 100000000 THEN 14 ELSE 0 END +
              CASE WHEN purity_score >= 75 THEN 8 WHEN purity_score >= 65 THEN 5 WHEN purity_score >= 55 THEN 3 ELSE 0 END +
              CASE WHEN dup_size <= 1 THEN 5 ELSE 0 END
            )::numeric AS positive_score,

            (
              CASE WHEN role NOT IN ('SUPPLY','REQUIREMENT') THEN 1 ELSE 0 END +
              CASE WHEN tx NOT IN ('SALE','LEASE','LEASE_OR_SALE') THEN 1 ELSE 0 END +
              CASE WHEN LOWER(loc) IN ('','other','others','unknown','na','n/a','none','delhi ncr','india') THEN 1 ELSE 0 END +
              CASE WHEN ptype='' OR ptype='UNKNOWN' THEN 1 ELSE 0 END +
              CASE WHEN NOT (amin > 0 AND amax > 0 AND amin <= amax AND amax <= 100000000) THEN 1 ELSE 0 END
            )::int AS critical_missing,

            (
              CASE
                WHEN raw = '' THEN 100
                ELSE LEAST(100,
                  CASE WHEN raw LIKE '%good morning%' OR raw LIKE '%good evening%'
                            OR raw LIKE '%good night%' OR raw LIKE '%happy birthday%'
                            OR raw LIKE '%congratulations%' OR raw LIKE '%festival wishes%'
                            OR raw LIKE '%breaking news%' OR raw LIKE '%market news%'
                            OR raw LIKE '%real estate news%' OR raw LIKE '%subscribe%'
                            OR raw LIKE '%youtube%' OR raw LIKE '%webinar%'
                            OR raw LIKE '%seminar%' OR raw LIKE '%training session%'
                            OR raw LIKE '%job opening%' OR raw LIKE '%hiring%'
                            OR raw LIKE '%vacancy%' OR raw LIKE '%loan available%'
                            OR raw LIKE '%home loan%' OR raw LIKE '%insurance%'
                            OR raw LIKE '%political%' OR raw LIKE '%election%'
                            OR raw LIKE '%stock market%' OR raw LIKE '%crypto%'
                            OR raw LIKE '%motivational%' OR raw LIKE '%quote of the day%'
                            OR raw LIKE '%thank you%' OR raw LIKE '%thanks everyone%'
                            OR raw LIKE '%welcome%' OR raw LIKE '%meeting reminder%'
                            OR raw LIKE '%event invite%' OR raw LIKE '%join us%'
                            OR raw LIKE '%download app%'
                       THEN 40 ELSE 0 END
                  +
                  CASE WHEN NOT (
                            raw LIKE '%sale%' OR raw LIKE '%rent%' OR raw LIKE '%lease%'
                            OR raw LIKE '%property%' OR raw LIKE '%shop%' OR raw LIKE '%office%'
                            OR raw LIKE '%floor%' OR raw LIKE '%plot%' OR raw LIKE '%apartment%'
                            OR raw LIKE '%flat%' OR raw LIKE '%villa%' OR raw LIKE '%showroom%'
                            OR raw LIKE '%warehouse%' OR raw LIKE '%restaurant%'
                            OR raw LIKE '%commercial%' OR raw LIKE '%residential%'
                            OR raw LIKE '%sqft%' OR raw LIKE '%sq ft%' OR raw LIKE '%sqyd%'
                            OR raw LIKE '%acre%' OR raw LIKE '%bhk%' OR raw LIKE '%require%'
                            OR raw LIKE '%required%' OR raw LIKE '%looking for%'
                            OR raw LIKE '%need%' OR raw LIKE '%tenant%' OR raw LIKE '%buyer%'
                            OR raw LIKE '%available%' OR raw LIKE '%owner%'
                            OR raw LIKE '%broker%' OR raw LIKE '%frontage%'
                            OR raw LIKE '%ground floor%' OR raw LIKE '%first floor%'
                       )
                       THEN 45 ELSE 0 END
                  +
                  CASE WHEN LENGTH(raw) < 18 THEN 30 ELSE 0 END
                  +
                  CASE WHEN raw ~ '^[0-9 +()-]{7,}$' THEN 35 ELSE 0 END
                )
              END
            )::numeric AS noise_score
          FROM base
        ),
        scored AS (
          SELECT *,
            GREATEST(0,LEAST(100,
              positive_score
              - CASE WHEN noise_score >= 70 THEN 45
                     WHEN noise_score >= 40 THEN 25
                     WHEN noise_score >= 25 THEN 10
                     ELSE 0 END
            ))::numeric(6,2) AS priority_score,

            (
              review_status='NEEDS_REVIEW'
              AND purity_score >= 75
              AND tx_conf >= 90
              AND type_conf >= 90
              AND role IN ('SUPPLY','REQUIREMENT')
              AND tx IN ('SALE','LEASE','LEASE_OR_SALE')
              AND LOWER(loc) NOT IN ('','other','others','unknown','na','n/a','none','delhi ncr','india')
              AND ptype <> '' AND ptype <> 'UNKNOWN'
              AND amin > 0 AND amax > 0 AND amin <= amax AND amax <= 100000000
              AND dup_size <= 1
              AND noise_score < 25
              AND positive_score >= 88
              AND critical_missing = 0
            ) AS auto_safe
          FROM feat
        ),
        final AS (
          SELECT *,
            CASE
              WHEN noise_score >= 75 THEN 'REJECT_NOISE'
              WHEN critical_missing >= 2 THEN 'MISSING_CRITICAL_DATA'
              WHEN auto_safe THEN 'AUTO_APPROVE_CANDIDATE'
              WHEN priority_score >= 70 AND critical_missing <= 1 THEN 'QUICK_REVIEW'
              ELSE 'DEEP_REVIEW'
            END AS bucket
          FROM scored
        ),
        upserted AS (
          INSERT INTO ai_whatsapp_review_priority(
            listing_id,priority_score,bucket,auto_approve_safe,reasons,risk_flags,
            duplicate_cluster_size,model_version,evaluated_at
          )
          SELECT
            listing_id,
            priority_score,
            bucket,
            auto_safe,
            to_jsonb(array_remove(ARRAY[
              CASE WHEN role IN ('SUPPLY','REQUIREMENT') THEN 'Role classified' END,
              CASE WHEN tx IN ('SALE','LEASE','LEASE_OR_SALE') THEN 'Transaction classified' END,
              CASE WHEN tx_conf >= 90 THEN 'Strong transaction confidence' END,
              CASE WHEN ptype <> '' AND ptype <> 'UNKNOWN' THEN 'Property type available' END,
              CASE WHEN type_conf >= 90 THEN 'Strong property type confidence' END,
              CASE WHEN LOWER(loc) NOT IN ('','other','others','unknown','na','n/a','none','delhi ncr','india') THEN 'Specific location available' END,
              CASE WHEN amin > 0 AND amax > 0 AND amin <= amax AND amax <= 100000000 THEN 'Plausible area range' END,
              CASE WHEN dup_size <= 1 THEN 'No duplicate cluster conflict' END
            ]::text[],NULL)),
            to_jsonb(array_remove(ARRAY[
              CASE WHEN role NOT IN ('SUPPLY','REQUIREMENT') THEN 'Role unresolved' END,
              CASE WHEN tx NOT IN ('SALE','LEASE','LEASE_OR_SALE') THEN 'Transaction unresolved' END,
              CASE WHEN tx_conf < 75 THEN 'Low transaction confidence' END,
              CASE WHEN ptype='' OR ptype='UNKNOWN' THEN 'Property type unresolved' END,
              CASE WHEN type_conf < 75 THEN 'Low property type confidence' END,
              CASE WHEN LOWER(loc) IN ('','other','others','unknown','na','n/a','none','delhi ncr','india') THEN 'Location missing or too generic' END,
              CASE WHEN NOT (amin > 0 AND amax > 0 AND amin <= amax AND amax <= 100000000) THEN 'Area missing or implausible' END,
              CASE WHEN dup_size > 1 THEN 'Duplicate cluster conflict' END,
              CASE WHEN noise_score >= 75 THEN 'Likely non-property/noise message' END,
              CASE WHEN critical_missing >= 2 THEN 'Multiple critical fields missing' END
            ]::text[],NULL)),
            dup_size,
            :version,
            NOW()
          FROM final
          ON CONFLICT(listing_id) DO UPDATE SET
            priority_score=EXCLUDED.priority_score,
            bucket=EXCLUDED.bucket,
            auto_approve_safe=EXCLUDED.auto_approve_safe,
            reasons=EXCLUDED.reasons,
            risk_flags=EXCLUDED.risk_flags,
            duplicate_cluster_size=EXCLUDED.duplicate_cluster_size,
            model_version=EXCLUDED.model_version,
            evaluated_at=NOW()
          RETURNING bucket,auto_approve_safe
        )
        SELECT
          COUNT(*)::int AS evaluated,
          COUNT(*) FILTER (WHERE auto_approve_safe)::int AS auto_candidates,
          COUNT(*) FILTER (WHERE bucket='AUTO_APPROVE_CANDIDATE')::int AS auto_bucket,
          COUNT(*) FILTER (WHERE bucket='QUICK_REVIEW')::int AS quick_bucket,
          COUNT(*) FILTER (WHERE bucket='DEEP_REVIEW')::int AS deep_bucket,
          COUNT(*) FILTER (WHERE bucket='MISSING_CRITICAL_DATA')::int AS missing_bucket,
          COUNT(*) FILTER (WHERE bucket='REJECT_NOISE')::int AS noise_bucket
        FROM upserted
        """), {"version": MODULE_VERSION}).mappings().one()

        c.execute(text("""
          DELETE FROM ai_whatsapp_review_priority q
          WHERE NOT EXISTS (
            SELECT 1
            FROM ai_whatsapp_purity p
            LEFT JOIN ai_whatsapp_review_decision d ON d.listing_id=p.listing_id
            WHERE p.listing_id=q.listing_id
              AND COALESCE(d.decision,'PENDING')='PENDING'
              AND p.review_status IN ('NEEDS_REVIEW','LOW_CONFIDENCE','UNKNOWN')
          )
        """))

    return {
        "version": MODULE_VERSION,
        "evaluated": int(result["evaluated"] or 0),
        "auto_approve_candidates": int(result["auto_candidates"] or 0),
        "buckets": {
            "AUTO_APPROVE_CANDIDATE": int(result["auto_bucket"] or 0),
            "QUICK_REVIEW": int(result["quick_bucket"] or 0),
            "DEEP_REVIEW": int(result["deep_bucket"] or 0),
            "MISSING_CRITICAL_DATA": int(result["missing_bucket"] or 0),
            "REJECT_NOISE": int(result["noise_bucket"] or 0),
        },
        "auto_approval_default": "DISABLED",
        "execution_mode": "BULK_SQL",
    }

def priority_summary(engine):
    ensure_calibration_schema(engine)
    with engine.connect() as c:
        rows = c.execute(text("""
          SELECT bucket,COUNT(*)::int n,
                 COUNT(*) FILTER (WHERE auto_approve_safe)::int safe
          FROM ai_whatsapp_review_priority
          GROUP BY bucket
        """)).mappings().all()
        counts = {b: 0 for b in BUCKETS}
        safe = 0
        evaluated = 0
        for r in rows:
            counts[r["bucket"]] = int(r["n"] or 0)
            safe += int(r["safe"] or 0)
            evaluated += int(r["n"] or 0)
    return {
        "version": MODULE_VERSION,
        "evaluated": evaluated,
        "auto_approve_candidates": safe,
        "buckets": counts,
        "auto_approval_default": "DISABLED",
        "execution_mode": "CACHED",
        "refresh_endpoint": "/api/v2/intelligence/whatsapp-priority/run",
    }

def priority_rows(engine, bucket="ALL", limit=100, offset=0, search=""):
    ensure_calibration_schema(engine)
    bucket = _s(bucket).upper() or "ALL"
    search = _s(search)
    where = ["COALESCE(d.decision,'PENDING')='PENDING'"]
    params = {"lim": int(limit), "off": int(offset), "q": f"%{search.lower()}%"}
    if bucket != "ALL":
        where.append("q.bucket=:bucket")
        params["bucket"] = bucket
    if search:
        where.append("""(
          LOWER(COALESCE(p.raw_text,'')) LIKE :q OR
          LOWER(COALESCE(p.recovered_location,'')) LIKE :q OR
          LOWER(COALESCE(p.recovered_property_type,'')) LIKE :q OR
          LOWER(COALESCE(p.source_group_name,'')) LIKE :q
        )""")

    with engine.connect() as c:
        rows = c.execute(text(f"""
          SELECT q.*,p.recovered_role,p.recovered_transaction,p.recovered_location,
                 p.recovered_property_type,p.recovered_area_min_sqft,p.recovered_area_max_sqft,
                 p.recovered_budget,p.recovered_frontage_ft,p.recovered_required_floor,
                 p.recovered_suitable_for,p.purity_score,p.source_group_name,p.poster_name,p.raw_text,
                 COALESCE(d.decision,'PENDING') human_decision
          FROM ai_whatsapp_review_priority q
          JOIN ai_whatsapp_purity p ON p.listing_id=q.listing_id
          LEFT JOIN ai_whatsapp_review_decision d ON d.listing_id=q.listing_id
          WHERE {" AND ".join(where)}
          ORDER BY
            CASE q.bucket
              WHEN 'AUTO_APPROVE_CANDIDATE' THEN 1
              WHEN 'QUICK_REVIEW' THEN 2
              WHEN 'MISSING_CRITICAL_DATA' THEN 3
              WHEN 'DEEP_REVIEW' THEN 4
              ELSE 5
            END,
            q.priority_score DESC
          LIMIT :lim OFFSET :off
        """), params).mappings().all()
    return [dict(r) for r in rows]

def apply_safe_auto_approvals(engine, reviewer_name="V2.4.3 Safe Auto Approval", max_records=10):
    ensure_calibration_schema(engine)
    max_records = max(1, min(int(max_records), 25))
    with engine.connect() as c:
        rows = c.execute(text("""
          SELECT q.listing_id,p.*
          FROM ai_whatsapp_review_priority q
          JOIN ai_whatsapp_purity p ON p.listing_id=q.listing_id
          LEFT JOIN ai_whatsapp_review_decision d ON d.listing_id=q.listing_id
          WHERE q.auto_approve_safe=TRUE
            AND q.bucket='AUTO_APPROVE_CANDIDATE'
            AND COALESCE(d.decision,'PENDING')='PENDING'
          ORDER BY q.priority_score DESC
          LIMIT :lim
        """), {"lim": max_records}).mappings().all()

    approved = []
    for r in rows:
        save_decision(
            engine,
            str(r["listing_id"]),
            "APPROVED",
            r.get("recovered_role"),
            r.get("recovered_transaction"),
            r.get("recovered_location"),
            r.get("recovered_property_type"),
            r.get("recovered_area_min_sqft"),
            r.get("recovered_area_max_sqft"),
            r.get("recovered_budget"),
            r.get("recovered_frontage_ft"),
            r.get("recovered_required_floor"),
            r.get("recovered_suitable_for"),
            reviewer_name,
            "V2.4.3 calibrated safe-auto criteria passed"
        )
        approved.append(str(r["listing_id"]))

    return {
        "version": MODULE_VERSION,
        "approved_count": len(approved),
        "approved_listing_ids": approved,
        "post_run": run_prioritization(engine),
    }

def _e(v):
    return html.escape(str(v or ""))

def render_priority_page(engine, bucket="ALL", limit=50, offset=0, search=""):
    summary = priority_summary(engine)
    rows = priority_rows(engine, bucket, limit, offset, search)
    counts = summary["buckets"]

    cards = []
    for r in rows:
        reasons = r.get("reasons") or []
        risks = r.get("risk_flags") or []
        cards.append(f"""
        <section class="card">
          <div class="head">
            <div>
              <span class="bucket">{_e(r.get('bucket'))}</span>
              <h3>{_e(r.get('recovered_location') or 'Location missing')} · {_e(r.get('recovered_property_type') or 'Type missing')}</h3>
              <div class="meta">{_e(r.get('source_group_name'))} · Purity {_e(r.get('purity_score'))} · Duplicate cluster {_e(r.get('duplicate_cluster_size'))}</div>
            </div>
            <div class="score">{_e(r.get('priority_score'))}</div>
          </div>
          <div class="raw">{_e(r.get('raw_text'))}</div>
          <div class="cols">
            <div><b>Recovered</b><br>
              Role: {_e(r.get('recovered_role'))}<br>
              Transaction: {_e(r.get('recovered_transaction'))}<br>
              Area: {_e(r.get('recovered_area_min_sqft'))} - {_e(r.get('recovered_area_max_sqft'))}<br>
              Budget: {_e(r.get('recovered_budget'))}
            </div>
            <div><b>Why prioritized</b><br>{'<br>'.join('✓ '+_e(x) for x in reasons)}</div>
            <div><b>Risks</b><br>{'<br>'.join('⚠ '+_e(x) for x in risks) if risks else 'No material risk flag'}</div>
          </div>
        </section>
        """)

    prev_off = max(0, offset-limit)
    next_off = offset+limit

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>V2.4.3 Calibration Engine</title>
<style>
body{{font-family:Arial,sans-serif;background:#f5f7fa;margin:0;color:#172033}}
header{{background:#111827;color:white;padding:22px 28px}}
.wrap{{max-width:1450px;margin:22px auto;padding:0 18px}}
.stats{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:15px}}
.stat,.filters,.card{{background:white;border:1px solid #e5e7eb;border-radius:12px;padding:14px}}
.stat b{{display:block;font-size:22px}}
.filters form{{display:flex;gap:10px;flex-wrap:wrap}}
input,select{{padding:9px;border:1px solid #cbd5e1;border-radius:7px}}
.card{{margin-top:14px}} .head{{display:flex;justify-content:space-between;gap:20px}}
.bucket{{padding:5px 9px;border-radius:999px;background:#e2e8f0;font-size:12px;font-weight:bold}}
.score{{font-size:30px;font-weight:bold}} .meta{{font-size:12px;color:#64748b}}
.raw{{background:#f8fafc;padding:12px;border-radius:8px;margin:12px 0;white-space:pre-wrap}}
.cols{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;line-height:1.5}}
button{{background:#1d4ed8;color:white;padding:10px 13px;border:0;border-radius:8px;font-weight:bold}}
.auto{{background:#15803d}}
.pagination{{display:flex;justify-content:space-between;margin:20px 0}}
@media(max-width:900px){{.stats,.cols{{grid-template-columns:1fr 1fr}}}}
</style></head>
<body>
<header><h1>V2.4.3 Calibration Engine</h1><div>Five-way prioritization with conservative manual-opt-in auto approval.</div></header>
<div class="wrap">
<div class="stats">
<div class="stat">Auto Candidates<b>{counts.get('AUTO_APPROVE_CANDIDATE',0)}</b></div>
<div class="stat">Quick Review<b>{counts.get('QUICK_REVIEW',0)}</b></div>
<div class="stat">Missing Critical Data<b>{counts.get('MISSING_CRITICAL_DATA',0)}</b></div>
<div class="stat">Deep Review<b>{counts.get('DEEP_REVIEW',0)}</b></div>
<div class="stat">Reject / Noise<b>{counts.get('REJECT_NOISE',0)}</b></div>
</div>
<div class="filters">
<form method="get" action="/v2/whatsapp-priority">
<select name="bucket"><option>{_e(bucket)}</option><option>ALL</option><option>AUTO_APPROVE_CANDIDATE</option><option>QUICK_REVIEW</option><option>MISSING_CRITICAL_DATA</option><option>DEEP_REVIEW</option><option>REJECT_NOISE</option></select>
<input name="search" value="{_e(search)}" placeholder="Search location, group, text">
<input type="hidden" name="limit" value="{int(limit)}">
<button>Filter</button>
</form>
<form method="post" action="/api/v2/intelligence/whatsapp-priority/apply-auto-approve" style="margin-top:10px">
<input type="hidden" name="confirm" value="YES">
<input type="number" name="max_records" value="10" min="1" max="25">
<button class="auto">Apply Safe Auto Approvals</button>
<span>Manual opt-in only. Start with 10.</span>
</form>
</div>
{''.join(cards) if cards else '<div class="card"><b>No rows in this bucket.</b></div>'}
<div class="pagination">
<a href="/v2/whatsapp-priority?bucket={_e(bucket)}&search={_e(search)}&limit={int(limit)}&offset={prev_off}">← Previous</a>
<a href="/v2/whatsapp-priority?bucket={_e(bucket)}&search={_e(search)}&limit={int(limit)}&offset={next_off}">Next →</a>
</div>
</div></body></html>"""

def register_priority_routes(core):
    app, engine = core.app, core.engine
    ensure_calibration_schema(engine)

    @app.post("/api/v2/intelligence/whatsapp-priority/run")
    def priority_run(req: Request):
        if hasattr(core, "need_login"):
            core.need_login(req)
        return run_prioritization(engine)

    @app.get("/api/v2/intelligence/whatsapp-priority")
    def priority_api(req: Request, bucket: str = Query("ALL"),
                     limit: int = Query(100, ge=1, le=1000),
                     offset: int = Query(0, ge=0),
                     search: str = Query("")):
        if hasattr(core, "need_login"):
            core.need_login(req)
        rows = priority_rows(engine,bucket,limit,offset,search)
        return {
            "version": MODULE_VERSION,
            "summary": priority_summary(engine),
            "count": len(rows),
            "rows": rows,
        }

    @app.post("/api/v2/intelligence/whatsapp-priority/apply-auto-approve")
    def priority_auto_approve(req: Request,
                              confirm: str = Form(...),
                              max_records: int = Form(10)):
        if hasattr(core, "need_login"):
            core.need_login(req)
        if _s(confirm).upper() != "YES":
            raise HTTPException(400, "Explicit confirm=YES required")
        apply_safe_auto_approvals(engine, "V2.4.3 Safe Auto Approval", max_records)
        return RedirectResponse("/v2/whatsapp-priority?bucket=AUTO_APPROVE_CANDIDATE", status_code=303)

    @app.get("/v2/whatsapp-priority", response_class=HTMLResponse)
    def priority_page(req: Request, bucket: str = Query("ALL"),
                      limit: int = Query(50, ge=1, le=200),
                      offset: int = Query(0, ge=0),
                      search: str = Query("")):
        if hasattr(core, "need_login"):
            core.need_login(req)
        return HTMLResponse(render_priority_page(engine,bucket,limit,offset,search))

    return app
