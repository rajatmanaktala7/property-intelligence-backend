
import html
from sqlalchemy import text
from fastapi import Request, Query, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

MODULE_VERSION = "2.3-WHATSAPP-REVIEW-QUEUE"

ALLOWED_DECISIONS = {"APPROVED", "REJECTED", "PENDING"}
ALLOWED_ROLES = {"SUPPLY", "REQUIREMENT", "UNKNOWN"}
ALLOWED_TRANSACTIONS = {"SALE", "LEASE", "LEASE_OR_SALE", "UNKNOWN"}

def _s(v):
    return str(v or "").strip()

def _num(v):
    if v in (None, ""):
        return None
    try:
        x = float(v)
        if x != x or abs(x) > 1_000_000_000_000:
            return None
        return x
    except Exception:
        return None

def ensure_review_schema(engine):
    with engine.begin() as c:
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_whatsapp_review_decision (
          listing_id UUID PRIMARY KEY,
          decision TEXT NOT NULL DEFAULT 'PENDING',
          override_role TEXT,
          override_transaction TEXT,
          override_location TEXT,
          override_property_type TEXT,
          override_area_min_sqft NUMERIC(14,2),
          override_area_max_sqft NUMERIC(14,2),
          override_budget NUMERIC(18,2),
          override_frontage_ft NUMERIC(12,2),
          override_required_floor TEXT,
          override_suitable_for TEXT,
          reviewer_name TEXT,
          reviewer_note TEXT,
          decided_at TIMESTAMPTZ,
          updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """))
        c.execute(text("""
          CREATE INDEX IF NOT EXISTS ix_ai_whatsapp_review_decision
          ON ai_whatsapp_review_decision(decision, updated_at DESC)
        """))

def apply_review_decisions(engine):
    ensure_review_schema(engine)
    with engine.begin() as c:
        approved = c.execute(text("""
          UPDATE ai_whatsapp_purity p
          SET
            recovered_role=COALESCE(d.override_role,p.recovered_role),
            recovered_transaction=COALESCE(d.override_transaction,p.recovered_transaction),
            recovered_location=COALESCE(NULLIF(d.override_location,''),p.recovered_location),
            recovered_property_type=COALESCE(NULLIF(d.override_property_type,''),p.recovered_property_type),
            recovered_area_min_sqft=COALESCE(d.override_area_min_sqft,p.recovered_area_min_sqft),
            recovered_area_max_sqft=COALESCE(d.override_area_max_sqft,p.recovered_area_max_sqft),
            recovered_budget=COALESCE(d.override_budget,p.recovered_budget),
            recovered_frontage_ft=COALESCE(d.override_frontage_ft,p.recovered_frontage_ft),
            recovered_required_floor=COALESCE(NULLIF(d.override_required_floor,''),p.recovered_required_floor),
            recovered_suitable_for=COALESCE(NULLIF(d.override_suitable_for,''),p.recovered_suitable_for),
            review_status='USABLE',
            purity_score=GREATEST(COALESCE(p.purity_score,0),95),
            last_recovered_at=NOW()
          FROM ai_whatsapp_review_decision d
          WHERE p.listing_id=d.listing_id AND d.decision='APPROVED'
        """)).rowcount or 0

        rejected = c.execute(text("""
          UPDATE ai_whatsapp_purity p
          SET review_status='MANUAL_REJECTED',last_recovered_at=NOW()
          FROM ai_whatsapp_review_decision d
          WHERE p.listing_id=d.listing_id AND d.decision='REJECTED'
        """)).rowcount or 0

    return {
        "version": MODULE_VERSION,
        "approved_reapplied": approved,
        "rejected_reapplied": rejected,
    }

def queue_summary(engine):
    ensure_review_schema(engine)
    with engine.connect() as c:
        purity = {
            r["review_status"]: r["n"]
            for r in c.execute(text("""
              SELECT review_status,COUNT(*) n
              FROM ai_whatsapp_purity
              GROUP BY review_status
            """)).mappings().all()
        }
        decisions = {
            r["decision"]: r["n"]
            for r in c.execute(text("""
              SELECT decision,COUNT(*) n
              FROM ai_whatsapp_review_decision
              GROUP BY decision
            """)).mappings().all()
        }
    return {"purity_status": purity, "human_decisions": decisions}

def get_queue(engine, status="NEEDS_REVIEW", limit=100, offset=0, search=""):
    ensure_review_schema(engine)
    status = _s(status).upper() or "NEEDS_REVIEW"
    search = _s(search)
    params = {"status": status, "lim": int(limit), "off": int(offset), "q": f"%{search.lower()}%"}
    where = []
    if status != "ALL":
        if status in ALLOWED_DECISIONS:
            where.append("COALESCE(d.decision,'PENDING')=:status")
        else:
            where.append("p.review_status=:status")
    if search:
        where.append("""(
          LOWER(COALESCE(p.raw_text,'')) LIKE :q OR
          LOWER(COALESCE(p.recovered_location,'')) LIKE :q OR
          LOWER(COALESCE(p.recovered_property_type,'')) LIKE :q OR
          LOWER(COALESCE(p.source_group_name,'')) LIKE :q OR
          LOWER(COALESCE(p.poster_name,'')) LIKE :q
        )""")
    clause = " AND ".join(where) if where else "TRUE"

    with engine.connect() as c:
        rows = c.execute(text(f"""
          SELECT
            p.*,
            COALESCE(d.decision,'PENDING') human_decision,
            d.override_role,d.override_transaction,d.override_location,d.override_property_type,
            d.override_area_min_sqft,d.override_area_max_sqft,d.override_budget,d.override_frontage_ft,
            d.override_required_floor,d.override_suitable_for,
            d.reviewer_name,d.reviewer_note,d.decided_at,d.updated_at decision_updated_at
          FROM ai_whatsapp_purity p
          LEFT JOIN ai_whatsapp_review_decision d ON d.listing_id=p.listing_id
          WHERE {clause}
          ORDER BY
            CASE WHEN COALESCE(d.decision,'PENDING')='PENDING' THEN 0 ELSE 1 END,
            p.purity_score DESC,p.last_recovered_at DESC
          LIMIT :lim OFFSET :off
        """), params).mappings().all()
    return [dict(r) for r in rows]

def save_decision(engine, listing_id, decision, role=None, transaction=None, location=None,
                  property_type=None, area_min=None, area_max=None, budget=None, frontage=None,
                  required_floor=None, suitable_for=None, reviewer_name=None, reviewer_note=None):
    ensure_review_schema(engine)
    decision = _s(decision).upper()
    if decision not in ALLOWED_DECISIONS:
        raise ValueError("Invalid decision")

    role = _s(role).upper() or None
    transaction = _s(transaction).upper() or None
    if role and role not in ALLOWED_ROLES:
        raise ValueError("Invalid role")
    if transaction and transaction not in ALLOWED_TRANSACTIONS:
        raise ValueError("Invalid transaction")

    amin = _num(area_min)
    amax = _num(area_max)
    if amin is not None and amax is not None and amin > amax:
        amin, amax = amax, amin

    with engine.begin() as c:
        exists = c.execute(
            text("SELECT 1 FROM ai_whatsapp_purity WHERE listing_id=CAST(:id AS uuid)"),
            {"id": listing_id}
        ).scalar()
        if not exists:
            raise ValueError("WhatsApp purity row not found")

        c.execute(text("""
          INSERT INTO ai_whatsapp_review_decision(
            listing_id,decision,override_role,override_transaction,override_location,
            override_property_type,override_area_min_sqft,override_area_max_sqft,
            override_budget,override_frontage_ft,override_required_floor,override_suitable_for,
            reviewer_name,reviewer_note,decided_at,updated_at
          )
          VALUES(
            CAST(:id AS uuid),:decision,:role,:tx,:loc,:ptype,:amin,:amax,:budget,:front,
            :floor,:suitable,:reviewer,:note,
            CASE WHEN :decision IN ('APPROVED','REJECTED') THEN NOW() ELSE NULL END,NOW()
          )
          ON CONFLICT(listing_id) DO UPDATE SET
            decision=EXCLUDED.decision,
            override_role=EXCLUDED.override_role,
            override_transaction=EXCLUDED.override_transaction,
            override_location=EXCLUDED.override_location,
            override_property_type=EXCLUDED.override_property_type,
            override_area_min_sqft=EXCLUDED.override_area_min_sqft,
            override_area_max_sqft=EXCLUDED.override_area_max_sqft,
            override_budget=EXCLUDED.override_budget,
            override_frontage_ft=EXCLUDED.override_frontage_ft,
            override_required_floor=EXCLUDED.override_required_floor,
            override_suitable_for=EXCLUDED.override_suitable_for,
            reviewer_name=EXCLUDED.reviewer_name,
            reviewer_note=EXCLUDED.reviewer_note,
            decided_at=EXCLUDED.decided_at,
            updated_at=NOW()
        """), {
            "id": listing_id, "decision": decision, "role": role, "tx": transaction,
            "loc": _s(location) or None, "ptype": _s(property_type).upper() or None,
            "amin": amin, "amax": amax, "budget": _num(budget), "front": _num(frontage),
            "floor": _s(required_floor) or None, "suitable": _s(suitable_for).upper() or None,
            "reviewer": _s(reviewer_name) or None, "note": _s(reviewer_note) or None,
        })
    return apply_review_decisions(engine)

def _e(v):
    return html.escape(str(v or ""))

def _opt(value, current):
    selected = " selected" if _s(current).upper() == value else ""
    return f'<option value="{value}"{selected}>{value}</option>'

def render_queue_page(engine, status="NEEDS_REVIEW", limit=50, offset=0, search=""):
    rows = get_queue(engine, status, limit, offset, search)
    summary = queue_summary(engine)
    cards = []

    for r in rows:
        role = r.get("override_role") or r.get("recovered_role")
        tx = r.get("override_transaction") or r.get("recovered_transaction")
        loc = r.get("override_location") or r.get("recovered_location")
        ptype = r.get("override_property_type") or r.get("recovered_property_type")
        amin = r.get("override_area_min_sqft") if r.get("override_area_min_sqft") is not None else r.get("recovered_area_min_sqft")
        amax = r.get("override_area_max_sqft") if r.get("override_area_max_sqft") is not None else r.get("recovered_area_max_sqft")
        budget = r.get("override_budget") if r.get("override_budget") is not None else r.get("recovered_budget")
        frontage = r.get("override_frontage_ft") if r.get("override_frontage_ft") is not None else r.get("recovered_frontage_ft")
        floor = r.get("override_required_floor") or r.get("recovered_required_floor")
        suitable = r.get("override_suitable_for") or r.get("recovered_suitable_for")

        cards.append(f"""
        <section class="card">
          <div class="top">
            <div>
              <span class="badge">{_e(r.get('review_status'))}</span>
              <span class="decision">{_e(r.get('human_decision'))}</span>
              <h3>{_e(loc or 'Location missing')} · {_e(ptype or 'Type missing')}</h3>
              <div class="meta">Purity {_e(r.get('purity_score'))} · {_e(r.get('source_group_name'))} · {_e(r.get('poster_name'))}</div>
            </div>
            <div class="score">{_e(r.get('purity_score'))}</div>
          </div>

          <div class="raw"><b>Raw WhatsApp text</b><br>{_e(r.get('raw_text'))}</div>

          <form method="post" action="/api/v2/intelligence/whatsapp-review/{_e(r.get('listing_id'))}">
            <div class="grid">
              <label>Role<select name="role">{_opt("SUPPLY",role)}{_opt("REQUIREMENT",role)}{_opt("UNKNOWN",role)}</select></label>
              <label>Transaction<select name="transaction">{_opt("SALE",tx)}{_opt("LEASE",tx)}{_opt("LEASE_OR_SALE",tx)}{_opt("UNKNOWN",tx)}</select></label>
              <label>Location<input name="location" value="{_e(loc)}"></label>
              <label>Property type<input name="property_type" value="{_e(ptype)}"></label>
              <label>Area min sqft<input name="area_min" value="{_e(amin)}"></label>
              <label>Area max sqft<input name="area_max" value="{_e(amax)}"></label>
              <label>Budget / rent<input name="budget" value="{_e(budget)}"></label>
              <label>Frontage ft<input name="frontage" value="{_e(frontage)}"></label>
              <label>Required floor<input name="required_floor" value="{_e(floor)}"></label>
              <label>Suitable for<input name="suitable_for" value="{_e(suitable)}"></label>
              <label>Reviewer<input name="reviewer_name" value="{_e(r.get('reviewer_name'))}"></label>
              <label class="wide">Review note<input name="reviewer_note" value="{_e(r.get('reviewer_note'))}"></label>
            </div>
            <div class="actions">
              <button class="approve" name="decision" value="APPROVED">Approve + Matcher Eligible</button>
              <button class="reject" name="decision" value="REJECTED">Reject</button>
              <button class="pending" name="decision" value="PENDING">Save for Later</button>
            </div>
          </form>
        </section>
        """)

    counts = summary.get("purity_status", {})
    decisions = summary.get("human_decisions", {})
    prev_off = max(0, offset-limit)
    next_off = offset+limit

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>WhatsApp Review Queue</title>
<style>
body{{font-family:Arial,sans-serif;background:#f5f7fa;margin:0;color:#14213d}}
header{{background:#111827;color:white;padding:22px 28px;position:sticky;top:0;z-index:10}}
header h1{{margin:0 0 6px}} header p{{margin:0;color:#cbd5e1}}
.wrap{{max-width:1450px;margin:22px auto;padding:0 18px}}
.stats{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:16px}}
.stat,.filters,.card{{background:white;border:1px solid #e5e7eb;border-radius:12px;padding:14px}}
.stat b{{font-size:22px;display:block}}
.filters{{margin-bottom:16px}} .filters form{{display:flex;gap:10px;flex-wrap:wrap}}
.filters input{{width:300px}} .filters select{{width:210px}}
input,select{{box-sizing:border-box;width:100%;padding:9px;border:1px solid #cbd5e1;border-radius:7px;background:white}}
.card{{margin-bottom:14px}} .top{{display:flex;justify-content:space-between;gap:20px}}
.badge,.decision{{display:inline-block;padding:4px 8px;border-radius:999px;background:#eef2ff;margin-right:6px;font-size:12px;font-weight:bold}}
.decision{{background:#ecfeff}} .meta{{font-size:12px;color:#64748b}} .score{{font-size:28px;font-weight:bold}}
.raw{{background:#f8fafc;padding:12px;border-radius:9px;margin:14px 0;white-space:pre-wrap;line-height:1.4}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}
label{{font-size:12px;font-weight:700;color:#475569}} .wide{{grid-column:span 2}}
.actions{{display:flex;gap:8px;margin-top:14px;flex-wrap:wrap}}
button,.linkbtn{{border:0;border-radius:8px;padding:10px 13px;font-weight:700;cursor:pointer;text-decoration:none;display:inline-block}}
.approve{{background:#15803d;color:white}} .reject{{background:#b91c1c;color:white}} .pending{{background:#475569;color:white}}
.pagination{{display:flex;justify-content:space-between;margin:20px 0}}
@media(max-width:900px){{.stats,.grid{{grid-template-columns:1fr 1fr}}}}
</style></head>
<body>
<header><h1>WhatsApp Review Queue</h1><p>Approve recovered WhatsApp records without modifying raw source data.</p></header>
<div class="wrap">
<div class="stats">
<div class="stat">Needs Review<b>{counts.get('NEEDS_REVIEW',0)}</b></div>
<div class="stat">Auto Usable<b>{counts.get('USABLE',0)}</b></div>
<div class="stat">Approved<b>{decisions.get('APPROVED',0)}</b></div>
<div class="stat">Rejected<b>{decisions.get('REJECTED',0)}</b></div>
<div class="stat">Pending decisions<b>{decisions.get('PENDING',0)}</b></div>
</div>
<div class="filters"><form method="get" action="/v2/whatsapp-review">
<select name="status"><option>{_e(status)}</option><option>NEEDS_REVIEW</option><option>LOW_CONFIDENCE</option><option>UNKNOWN</option><option>USABLE</option><option>APPROVED</option><option>REJECTED</option><option>ALL</option></select>
<input name="search" placeholder="Search raw text, location, group..." value="{_e(search)}">
<input type="hidden" name="limit" value="{int(limit)}"><button>Filter</button></form></div>
{''.join(cards) if cards else '<div class="card"><b>No records in this queue.</b></div>'}
<div class="pagination">
<a class="linkbtn" href="/v2/whatsapp-review?status={_e(status)}&search={_e(search)}&limit={int(limit)}&offset={prev_off}">← Previous</a>
<a class="linkbtn" href="/v2/whatsapp-review?status={_e(status)}&search={_e(search)}&limit={int(limit)}&offset={next_off}">Next →</a>
</div></div></body></html>"""

def register_review_queue(core):
    app, engine = core.app, core.engine
    ensure_review_schema(engine)

    @app.get("/api/v2/intelligence/whatsapp-review/summary")
    def review_summary(req: Request):
        if hasattr(core, "need_login"):
            core.need_login(req)
        return {"version": MODULE_VERSION, **queue_summary(engine)}

    @app.get("/api/v2/intelligence/whatsapp-review")
    def review_api(req: Request, status: str = Query("NEEDS_REVIEW"),
                   limit: int = Query(100, ge=1, le=1000),
                   offset: int = Query(0, ge=0),
                   search: str = Query("")):
        if hasattr(core, "need_login"):
            core.need_login(req)
        rows = get_queue(engine, status, limit, offset, search)
        return {"version": MODULE_VERSION, "status": status.upper(),
                "count": len(rows), "summary": queue_summary(engine), "rows": rows}

    @app.post("/api/v2/intelligence/whatsapp-review/{listing_id}")
    def review_action(listing_id: str, req: Request,
                      decision: str = Form(...), role: str = Form(""),
                      transaction: str = Form(""), location: str = Form(""),
                      property_type: str = Form(""), area_min: str = Form(""),
                      area_max: str = Form(""), budget: str = Form(""),
                      frontage: str = Form(""), required_floor: str = Form(""),
                      suitable_for: str = Form(""), reviewer_name: str = Form(""),
                      reviewer_note: str = Form("")):
        if hasattr(core, "need_login"):
            core.need_login(req)
        try:
            save_decision(engine, listing_id, decision, role, transaction, location,
                          property_type, area_min, area_max, budget, frontage,
                          required_floor, suitable_for, reviewer_name, reviewer_note)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return RedirectResponse(url="/v2/whatsapp-review?status=NEEDS_REVIEW", status_code=303)

    @app.get("/v2/whatsapp-review", response_class=HTMLResponse)
    def review_page(req: Request, status: str = Query("NEEDS_REVIEW"),
                    limit: int = Query(50, ge=1, le=200),
                    offset: int = Query(0, ge=0),
                    search: str = Query("")):
        if hasattr(core, "need_login"):
            core.need_login(req)
        return HTMLResponse(render_queue_page(engine, status, limit, offset, search))

    return app
