
import html
import re
from sqlalchemy import text
from fastapi import Request, Query, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from alliance_v2_whatsapp_review_queue import (
    ensure_review_schema, save_decision, queue_summary
)

MODULE_VERSION = "2.4.1-BULK-REVIEW-PRIORITIZATION"

BUCKETS = {
    "AUTO_APPROVE_CANDIDATE",
    "QUICK_REVIEW",
    "DEEP_REVIEW",
    "REJECT_NOISE",
}

NOISE_PHRASES = [
    "good morning", "good evening", "happy birthday", "congratulations",
    "festival wishes", "breaking news", "market news", "real estate news",
    "subscribe", "youtube", "webinar", "seminar", "training session",
    "job opening", "hiring", "vacancy", "loan available", "home loan",
    "insurance", "political", "election", "stock market", "crypto",
]

PROPERTY_SIGNAL_WORDS = [
    "sale", "rent", "lease", "property", "shop", "office", "floor", "plot",
    "apartment", "flat", "villa", "showroom", "warehouse", "restaurant",
    "commercial", "residential", "sqft", "sq ft", "sqyd", "acre", "bhk",
    "require", "required", "looking for", "need", "tenant", "buyer",
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

def _has_value(v):
    return v not in (None, "", "UNKNOWN", "unknown")

def ensure_priority_schema(engine):
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

def _noise_score(raw):
    t = _s(raw).lower()
    if not t:
        return 100, ["Empty raw message"]
    hits = [x for x in NOISE_PHRASES if x in t]
    signals = [x for x in PROPERTY_SIGNAL_WORDS if x in t]
    score = 0
    reasons = []
    if hits:
        score += min(70, len(hits) * 25)
        reasons.append("Non-property/noise language detected: " + ", ".join(hits[:3]))
    if not signals:
        score += 35
        reasons.append("No strong property or requirement signal")
    if len(t) < 20:
        score += 25
        reasons.append("Message too short for reliable property extraction")
    return min(100, score), reasons

def _evaluate_row(r, dup_size):
    reasons = []
    risks = []
    score = 0.0

    purity = _f(r.get("purity_score"))
    tx_conf = _f(r.get("transaction_confidence"))
    type_conf = _f(r.get("property_type_confidence"))
    loc = _s(r.get("recovered_location"))
    ptype = _s(r.get("recovered_property_type")).upper()
    role = _s(r.get("recovered_role")).upper()
    tx = _s(r.get("recovered_transaction")).upper()
    amin = _f(r.get("recovered_area_min_sqft"), 0)
    amax = _f(r.get("recovered_area_max_sqft"), 0)
    raw = _s(r.get("raw_text"))

    noise, noise_reasons = _noise_score(raw)
    if noise >= 60:
        risks.extend(noise_reasons)

    if role in {"SUPPLY", "REQUIREMENT"}:
        score += 15
        reasons.append("Role classified")
    else:
        risks.append("Role unresolved")

    if tx in {"SALE", "LEASE", "LEASE_OR_SALE"}:
        score += 15
        reasons.append("Transaction classified")
    else:
        risks.append("Transaction unresolved")

    if tx_conf >= 95:
        score += 12
        reasons.append("High transaction confidence")
    elif tx_conf >= 75:
        score += 7
    else:
        risks.append("Low transaction confidence")

    if ptype and ptype != "UNKNOWN":
        score += 12
        reasons.append("Property type available")
    else:
        risks.append("Property type unresolved")

    if type_conf >= 95:
        score += 10
        reasons.append("High property type confidence")
    elif type_conf >= 75:
        score += 5
    else:
        risks.append("Low property type confidence")

    if loc and loc.lower() not in GENERIC_LOCATIONS:
        score += 15
        reasons.append("Specific location available")
    else:
        risks.append("Location missing or too generic")

    if amin > 0 and amax > 0 and amin <= amax and amax <= 100_000_000:
        score += 14
        reasons.append("Plausible area range")
    else:
        risks.append("Area missing or implausible")

    if purity >= 70:
        score += 7
    elif purity >= 55:
        score += 3

    if dup_size <= 1:
        score += 5
        reasons.append("No duplicate cluster conflict")
    else:
        risks.append(f"Duplicate cluster contains {dup_size} rows")

    if noise >= 60:
        score -= 40
    elif noise >= 30:
        score -= 15

    score = round(max(0, min(100, score)), 2)

    # Conservative auto-approval eligibility.
    auto_safe = bool(
        r.get("review_status") == "NEEDS_REVIEW"
        and score >= 90
        and purity >= 70
        and tx_conf >= 95
        and type_conf >= 95
        and role in {"SUPPLY", "REQUIREMENT"}
        and tx in {"SALE", "LEASE", "LEASE_OR_SALE"}
        and loc and loc.lower() not in GENERIC_LOCATIONS
        and ptype and ptype != "UNKNOWN"
        and amin > 0 and amax > 0
        and dup_size <= 1
        and noise < 30
    )

    if noise >= 70:
        bucket = "REJECT_NOISE"
    elif auto_safe:
        bucket = "AUTO_APPROVE_CANDIDATE"
    elif score >= 70 and len(risks) <= 2:
        bucket = "QUICK_REVIEW"
    else:
        bucket = "DEEP_REVIEW"

    return {
        "priority_score": score,
        "bucket": bucket,
        "auto_approve_safe": auto_safe,
        "reasons": reasons,
        "risk_flags": risks,
    }

def run_prioritization(engine):
    """
    V2.4.1 bulk implementation.
    Uses one PostgreSQL INSERT..SELECT..ON CONFLICT instead of thousands
    of Python row-by-row UPSERTs.
    """
    ensure_priority_schema(engine)

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
              CASE WHEN tx_conf >= 95 THEN 12 WHEN tx_conf >= 75 THEN 7 ELSE 0 END +
              CASE WHEN ptype <> '' AND ptype <> 'UNKNOWN' THEN 12 ELSE 0 END +
              CASE WHEN type_conf >= 95 THEN 10 WHEN type_conf >= 75 THEN 5 ELSE 0 END +
              CASE WHEN LOWER(loc) NOT IN ('','other','others','unknown','na','n/a','none','delhi ncr','india') THEN 15 ELSE 0 END +
              CASE WHEN amin > 0 AND amax > 0 AND amin <= amax AND amax <= 100000000 THEN 14 ELSE 0 END +
              CASE WHEN purity_score >= 70 THEN 7 WHEN purity_score >= 55 THEN 3 ELSE 0 END +
              CASE WHEN dup_size <= 1 THEN 5 ELSE 0 END
            )::numeric AS positive_score,
            (
              CASE
                WHEN raw = '' THEN 100
                ELSE LEAST(100,
                  CASE WHEN raw LIKE '%good morning%' OR raw LIKE '%good evening%'
                            OR raw LIKE '%happy birthday%' OR raw LIKE '%congratulations%'
                            OR raw LIKE '%festival wishes%' OR raw LIKE '%breaking news%'
                            OR raw LIKE '%market news%' OR raw LIKE '%real estate news%'
                            OR raw LIKE '%subscribe%' OR raw LIKE '%youtube%'
                            OR raw LIKE '%webinar%' OR raw LIKE '%seminar%'
                            OR raw LIKE '%training session%' OR raw LIKE '%job opening%'
                            OR raw LIKE '%hiring%' OR raw LIKE '%vacancy%'
                            OR raw LIKE '%loan available%' OR raw LIKE '%home loan%'
                            OR raw LIKE '%insurance%' OR raw LIKE '%political%'
                            OR raw LIKE '%election%' OR raw LIKE '%stock market%'
                            OR raw LIKE '%crypto%'
                       THEN 25 ELSE 0 END
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
                       )
                       THEN 35 ELSE 0 END
                  +
                  CASE WHEN LENGTH(raw) < 20 THEN 25 ELSE 0 END
                )
              END
            )::numeric AS noise_score
          FROM base
        ),
        scored AS (
          SELECT *,
            GREATEST(0,LEAST(100,
              positive_score
              - CASE WHEN noise_score >= 60 THEN 40
                     WHEN noise_score >= 30 THEN 15 ELSE 0 END
            ))::numeric(6,2) AS priority_score,
            (
              review_status='NEEDS_REVIEW'
              AND purity_score >= 70
              AND tx_conf >= 95
              AND type_conf >= 95
              AND role IN ('SUPPLY','REQUIREMENT')
              AND tx IN ('SALE','LEASE','LEASE_OR_SALE')
              AND LOWER(loc) NOT IN ('','other','others','unknown','na','n/a','none','delhi ncr','india')
              AND ptype <> '' AND ptype <> 'UNKNOWN'
              AND amin > 0 AND amax > 0 AND amin <= amax AND amax <= 100000000
              AND dup_size <= 1
              AND noise_score < 30
              AND positive_score >= 90
            ) AS auto_safe
          FROM feat
        ),
        final AS (
          SELECT *,
            CASE
              WHEN noise_score >= 70 THEN 'REJECT_NOISE'
              WHEN auto_safe THEN 'AUTO_APPROVE_CANDIDATE'
              WHEN priority_score >= 70
                   AND (
                     (CASE WHEN role NOT IN ('SUPPLY','REQUIREMENT') THEN 1 ELSE 0 END) +
                     (CASE WHEN tx NOT IN ('SALE','LEASE','LEASE_OR_SALE') THEN 1 ELSE 0 END) +
                     (CASE WHEN tx_conf < 75 THEN 1 ELSE 0 END) +
                     (CASE WHEN ptype='' OR ptype='UNKNOWN' THEN 1 ELSE 0 END) +
                     (CASE WHEN type_conf < 75 THEN 1 ELSE 0 END) +
                     (CASE WHEN LOWER(loc) IN ('','other','others','unknown','na','n/a','none','delhi ncr','india') THEN 1 ELSE 0 END) +
                     (CASE WHEN NOT (amin > 0 AND amax > 0 AND amin <= amax AND amax <= 100000000) THEN 1 ELSE 0 END) +
                     (CASE WHEN dup_size > 1 THEN 1 ELSE 0 END)
                   ) <= 2
                THEN 'QUICK_REVIEW'
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
              CASE WHEN tx_conf >= 95 THEN 'High transaction confidence' END,
              CASE WHEN ptype <> '' AND ptype <> 'UNKNOWN' THEN 'Property type available' END,
              CASE WHEN type_conf >= 95 THEN 'High property type confidence' END,
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
              CASE WHEN noise_score >= 60 THEN 'Noise/non-property language detected' END
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
            "REJECT_NOISE": int(result["noise_bucket"] or 0),
        },
        "auto_approval_default": "DISABLED",
        "execution_mode": "BULK_SQL",
    }
def priority_rows(engine, bucket="ALL", limit=100, offset=0, search=""):
    ensure_priority_schema(engine)
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
              WHEN 'DEEP_REVIEW' THEN 3
              ELSE 4
            END,
            q.priority_score DESC
          LIMIT :lim OFFSET :off
        """), params).mappings().all()
    return [dict(r) for r in rows]

def apply_safe_auto_approvals(engine, reviewer_name="V2.4 Safe Auto Approval", max_records=25):
    """
    Explicit opt-in only. Never called automatically by a rebuild.
    Uses V2.3 save_decision so approvals remain persistent and auditable.
    """
    ensure_priority_schema(engine)
    max_records = max(1, min(int(max_records), 100))
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
            "V2.4 strict safe-auto criteria passed"
        )
        approved.append(str(r["listing_id"]))

    # Recalculate priority after decisions.
    result = run_prioritization(engine)
    return {
        "version": MODULE_VERSION,
        "approved_count": len(approved),
        "approved_listing_ids": approved,
        "post_run": result,
    }

def _e(v):
    return html.escape(str(v or ""))

def render_priority_page(engine, bucket="ALL", limit=50, offset=0, search=""):
    # Always refresh before rendering so queue is current.
    summary = run_prioritization(engine)
    rows = priority_rows(engine, bucket, limit, offset, search)
    cards = []

    for r in rows:
        reasons = r.get("reasons") or []
        risks = r.get("risk_flags") or []
        cards.append(f"""
        <section class="card">
          <div class="head">
            <div>
              <span class="bucket {_e(r.get('bucket')).lower()}">{_e(r.get('bucket'))}</span>
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
          <div class="actions">
            <a class="review" href="/v2/whatsapp-review?search={_e(r.get('listing_id'))}">Open in Human Review</a>
            {'<span class="safe">Safe auto-approval candidate</span>' if r.get('auto_approve_safe') else ''}
          </div>
        </section>
        """)

    counts = summary["buckets"]
    prev_off = max(0, offset-limit)
    next_off = offset+limit

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>V2.4 Review Prioritization</title>
<style>
body{{font-family:Arial,sans-serif;background:#f5f7fa;margin:0;color:#172033}}
header{{background:#111827;color:white;padding:22px 28px}}
.wrap{{max-width:1450px;margin:22px auto;padding:0 18px}}
.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:15px}}
.stat,.filters,.card{{background:white;border:1px solid #e5e7eb;border-radius:12px;padding:14px}}
.stat b{{display:block;font-size:24px}}
.filters form{{display:flex;gap:10px;flex-wrap:wrap}}
input,select{{padding:9px;border:1px solid #cbd5e1;border-radius:7px}}
.card{{margin-top:14px}} .head{{display:flex;justify-content:space-between;gap:20px}}
.bucket{{padding:5px 9px;border-radius:999px;background:#e2e8f0;font-size:12px;font-weight:bold}}
.score{{font-size:30px;font-weight:bold}} .meta{{font-size:12px;color:#64748b}}
.raw{{background:#f8fafc;padding:12px;border-radius:8px;margin:12px 0;white-space:pre-wrap}}
.cols{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;line-height:1.5}}
.actions{{margin-top:14px;display:flex;gap:10px;align-items:center}}
.review,button{{background:#1d4ed8;color:white;padding:10px 13px;border:0;border-radius:8px;text-decoration:none;font-weight:bold}}
.safe{{background:#dcfce7;color:#166534;padding:8px;border-radius:8px;font-weight:bold}}
.auto{{background:#15803d}}
.pagination{{display:flex;justify-content:space-between;margin:20px 0}}
@media(max-width:900px){{.stats,.cols{{grid-template-columns:1fr 1fr}}}}
</style></head>
<body>
<header><h1>V2.4 AI Review Prioritization</h1><div>Explainable prioritization. Safe auto-approval is disabled by default and requires an explicit action.</div></header>
<div class="wrap">
<div class="stats">
<div class="stat">Auto-Approve Candidates<b>{counts.get('AUTO_APPROVE_CANDIDATE',0)}</b></div>
<div class="stat">Quick Review<b>{counts.get('QUICK_REVIEW',0)}</b></div>
<div class="stat">Deep Review<b>{counts.get('DEEP_REVIEW',0)}</b></div>
<div class="stat">Reject / Noise<b>{counts.get('REJECT_NOISE',0)}</b></div>
</div>
<div class="filters">
<form method="get" action="/v2/whatsapp-priority">
<select name="bucket"><option>{_e(bucket)}</option><option>ALL</option><option>AUTO_APPROVE_CANDIDATE</option><option>QUICK_REVIEW</option><option>DEEP_REVIEW</option><option>REJECT_NOISE</option></select>
<input name="search" value="{_e(search)}" placeholder="Search location, group, text">
<input type="hidden" name="limit" value="{int(limit)}">
<button>Filter</button>
</form>
<form method="post" action="/api/v2/intelligence/whatsapp-priority/apply-auto-approve" style="margin-top:10px">
<input type="hidden" name="confirm" value="YES">
<input type="number" name="max_records" value="10" min="1" max="100">
<button class="auto">Apply Safe Auto Approvals</button>
<span>Explicit action only. Start with 10.</span>
</form>
</div>
{''.join(cards) if cards else '<div class="card"><b>No rows in this priority bucket.</b></div>'}
<div class="pagination">
<a href="/v2/whatsapp-priority?bucket={_e(bucket)}&search={_e(search)}&limit={int(limit)}&offset={prev_off}">← Previous</a>
<a href="/v2/whatsapp-priority?bucket={_e(bucket)}&search={_e(search)}&limit={int(limit)}&offset={next_off}">Next →</a>
</div>
</div></body></html>"""

def register_priority_routes(core):
    app, engine = core.app, core.engine
    ensure_priority_schema(engine)

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
        summary = run_prioritization(engine)
        return {
            "version": MODULE_VERSION,
            "summary": summary,
            "count": len(priority_rows(engine,bucket,limit,offset,search)),
            "rows": priority_rows(engine,bucket,limit,offset,search),
        }

    @app.post("/api/v2/intelligence/whatsapp-priority/apply-auto-approve")
    def priority_auto_approve(req: Request,
                              confirm: str = Form(...),
                              max_records: int = Form(10)):
        if hasattr(core, "need_login"):
            core.need_login(req)
        if _s(confirm).upper() != "YES":
            raise HTTPException(400, "Explicit confirm=YES required")
        apply_safe_auto_approvals(engine, "V2.4 Safe Auto Approval", max_records)
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
