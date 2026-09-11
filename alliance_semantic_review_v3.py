from __future__ import annotations

import html
import json
from typing import Any, Dict, Optional

from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import text

import alliance_requirement_brain_v3 as brain
from alliance_semantic_schema_v3 import validate_requirement

VERSION = "3.1.0-REVIEW-MODE"

DECISIONS = {"USE_V3", "KEEP_V2", "EDITED", "REJECT"}


class ReviewDecision(BaseModel):
    decision: str
    reviewer: Optional[str] = None
    notes: Optional[str] = None
    corrected_v3: Optional[Dict[str, Any]] = None


def ensure_schema(engine) -> None:
    with engine.begin() as c:
        c.execute(text("""
            CREATE TABLE IF NOT EXISTS pi_semantic_shadow_v3_runs(
                id BIGSERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                source TEXT NOT NULL DEFAULT 'DEAL_MATCH',
                raw_text TEXT NOT NULL,
                v2_snapshot JSONB,
                v3_snapshot JSONB NOT NULL,
                comparison JSONB NOT NULL,
                brain_version TEXT NOT NULL,
                reviewed BOOLEAN NOT NULL DEFAULT FALSE,
                human_correction JSONB,
                notes TEXT
            )
        """))

        # Additive review columns only. No existing data is removed or rewritten.
        for stmt in (
            "ALTER TABLE pi_semantic_shadow_v3_runs ADD COLUMN IF NOT EXISTS review_decision TEXT",
            "ALTER TABLE pi_semantic_shadow_v3_runs ADD COLUMN IF NOT EXISTS reviewer TEXT",
            "ALTER TABLE pi_semantic_shadow_v3_runs ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ",
            "ALTER TABLE pi_semantic_shadow_v3_runs ADD COLUMN IF NOT EXISTS approved_snapshot JSONB",
            "ALTER TABLE pi_semantic_shadow_v3_runs ADD COLUMN IF NOT EXISTS correction_reason TEXT",
        ):
            c.execute(text(stmt))

        c.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_semantic_shadow_v3_reviewed
            ON pi_semantic_shadow_v3_runs(reviewed, id DESC)
        """))
        c.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_semantic_shadow_v3_decision
            ON pi_semantic_shadow_v3_runs(review_decision, id DESC)
        """))


def _row(engine, run_id: int) -> Dict[str, Any]:
    ensure_schema(engine)
    with engine.connect() as c:
        row = c.execute(text("""
            SELECT id, created_at, source, raw_text,
                   v2_snapshot, v3_snapshot, comparison,
                   brain_version, reviewed, review_decision,
                   reviewer, reviewed_at, approved_snapshot,
                   human_correction, notes, correction_reason
            FROM pi_semantic_shadow_v3_runs
            WHERE id=:id
        """), {"id": int(run_id)}).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Semantic review record not found")

    return dict(row)


def _normalize_decision(payload: ReviewDecision) -> str:
    decision = str(payload.decision or "").upper().strip()
    if decision not in DECISIONS:
        raise HTTPException(
            status_code=400,
            detail=f"decision must be one of: {', '.join(sorted(DECISIONS))}",
        )
    return decision


def apply_decision(engine, run_id: int, payload: ReviewDecision) -> Dict[str, Any]:
    row = _row(engine, run_id)
    decision = _normalize_decision(payload)

    approved = None
    correction = None

    if decision == "USE_V3":
        approved = row.get("v3_snapshot")

    elif decision == "KEEP_V2":
        approved = row.get("v2_snapshot")

    elif decision == "EDITED":
        if not payload.corrected_v3:
            raise HTTPException(
                status_code=400,
                detail="corrected_v3 is required when decision=EDITED",
            )
        errors = validate_requirement(payload.corrected_v3)
        if errors:
            raise HTTPException(
                status_code=400,
                detail={"message": "Corrected V3 object is invalid", "errors": errors},
            )
        approved = payload.corrected_v3
        correction = {
            "original_v3": row.get("v3_snapshot"),
            "corrected_v3": payload.corrected_v3,
        }

    elif decision == "REJECT":
        approved = None

    reviewer = (payload.reviewer or "ALLIANCE_REVIEWER").strip()[:200]
    notes = (payload.notes or "").strip()[:5000]

    with engine.begin() as c:
        c.execute(text("""
            UPDATE pi_semantic_shadow_v3_runs
               SET reviewed=TRUE,
                   review_decision=:decision,
                   reviewer=:reviewer,
                   reviewed_at=NOW(),
                   approved_snapshot=CAST(:approved AS JSONB),
                   human_correction=CAST(:correction AS JSONB),
                   notes=:notes,
                   correction_reason=:notes
             WHERE id=:id
        """), {
            "id": int(run_id),
            "decision": decision,
            "reviewer": reviewer,
            "approved": json.dumps(approved, default=str) if approved is not None else None,
            "correction": json.dumps(correction, default=str) if correction is not None else None,
            "notes": notes or None,
        })

    return {
        "status": "REVIEW_SAVED",
        "run_id": int(run_id),
        "decision": decision,
        "reviewer": reviewer,
        "v3_authoritative_for_this_review_record": decision in {"USE_V3", "EDITED"},
        "production_matcher_changed": False,
        "production_matching_brain": "V2.3",
        "approved_snapshot": approved,
    }


def stats(engine) -> Dict[str, Any]:
    ensure_schema(engine)

    with engine.connect() as c:
        summary = c.execute(text("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE reviewed) AS reviewed,
                COUNT(*) FILTER (WHERE NOT reviewed) AS pending,
                COUNT(*) FILTER (WHERE review_decision='USE_V3') AS use_v3,
                COUNT(*) FILTER (WHERE review_decision='KEEP_V2') AS keep_v2,
                COUNT(*) FILTER (WHERE review_decision='EDITED') AS edited,
                COUNT(*) FILTER (WHERE review_decision='REJECT') AS rejected,
                COUNT(*) FILTER (
                    WHERE COALESCE((comparison->>'transaction_changed')::boolean, FALSE)
                       OR COALESCE((comparison->>'asset_changed')::boolean, FALSE)
                       OR COALESCE((comparison->>'location_count_v2')::int, 0)
                          <> COALESCE((comparison->>'location_count_v3')::int, 0)
                ) AS disagreements
            FROM pi_semantic_shadow_v3_runs
        """)).mappings().one()

        safety = c.execute(text("""
            SELECT
                COUNT(*) FILTER (
                    WHERE reviewed
                      AND review_decision IN ('USE_V3','EDITED')
                      AND approved_snapshot IS NULL
                ) AS approved_without_snapshot
            FROM pi_semantic_shadow_v3_runs
        """)).mappings().one()

    d = dict(summary)
    reviewed = int(d.get("reviewed") or 0)
    v3_preferred = int(d.get("use_v3") or 0) + int(d.get("edited") or 0)
    d["v3_preference_rate"] = round((v3_preferred / reviewed) * 100.0, 2) if reviewed else None
    d["promotion_sample_target"] = 50
    d["promotion_sample_met"] = reviewed >= 50
    d["promotion_ready"] = False
    d["promotion_reason"] = (
        "Review sample threshold reached; separate V3→Matcher adapter validation still required."
        if reviewed >= 50
        else f"Need {50-reviewed} more reviewed real requirements before promotion can be considered."
    )
    d["safety"] = dict(safety)
    d["version"] = VERSION
    d["mode"] = "REVIEW"
    d["production_behavior_changed"] = False
    return d


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _summary_card(title: str, snap: Dict[str, Any], is_v3: bool) -> str:
    if is_v3:
        asset = (snap.get("asset") or {}).get("primary_asset")
        use = (snap.get("intended_use") or {}).get("primary_use")
        tx = (snap.get("transaction") or {}).get("value") or "UNKNOWN"
        tx_status = (snap.get("transaction") or {}).get("status")
        locs = [
            f"{x.get('name')} [{x.get('constraint')}]"
            for x in (snap.get("locations") or [])
        ]
        area = snap.get("area") or {}
        budget = snap.get("budget") or {}
        rows = [
            ("Intent", (snap.get("intent") or {}).get("role")),
            ("Asset", asset),
            ("Use", use),
            ("Transaction", f"{tx} · {tx_status}"),
            ("Locations", ", ".join(locs) or "None"),
            ("Area Min Sqft", area.get("min_sqft")),
            ("Area Max Sqft", area.get("max_sqft")),
            ("Budget Status", budget.get("status")),
            ("Readiness", snap.get("matching_readiness")),
            ("Unknowns", ", ".join(snap.get("unknowns") or [])),
        ]
    else:
        rows = [
            ("Role", snap.get("role")),
            ("Transaction", snap.get("transaction") or "UNKNOWN"),
            ("Family", snap.get("family")),
            ("Subtype", snap.get("subtype")),
            ("Locations", ", ".join(snap.get("primary_locations") or []) or snap.get("location")),
            ("Area Min Sqft", snap.get("area_min_sqft")),
            ("Area Max Sqft", snap.get("area_max_sqft")),
            ("Budget Max", snap.get("budget_max")),
            ("Hard Constraints", ", ".join(snap.get("hard_constraints") or [])),
            ("Preferences", ", ".join(snap.get("preferences") or [])),
        ]

    inner = "".join(
        f"<div class='field'><b>{_e(k)}</b><br>{_e(v)}</div>"
        for k, v in rows
    )
    return f"<div class='panel'><h2>{_e(title)}</h2><div class='grid'>{inner}</div></div>"


def _page(body: str) -> str:
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alliance Semantic V3 Review</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;font-family:Arial,sans-serif;background:#f3eee7;color:#2e251e}}
header{{background:#342a22;color:white;padding:16px 22px}}
nav{{background:#fff;padding:10px 18px;display:flex;gap:8px;flex-wrap:wrap}}
nav a,.btn,button{{background:#6b513d;color:#fff;border:0;border-radius:8px;padding:9px 12px;text-decoration:none;font-weight:800;cursor:pointer}}
main{{max-width:1800px;margin:auto;padding:18px}}
.card,.panel{{background:#fff;border:1px solid #dacaba;border-radius:12px;padding:15px;margin-bottom:14px}}
.compare{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}}
.field{{padding:9px;background:#faf6f0;border-radius:8px}}
.green{{color:#17713b;font-weight:800}} .amber{{color:#996000;font-weight:800}} .red{{color:#a32525;font-weight:800}}
pre{{white-space:pre-wrap;overflow:auto;background:#201c19;color:#f7efe7;padding:12px;border-radius:8px;max-height:500px}}
textarea,input{{width:100%;padding:10px;border:1px solid #cbb9a7;border-radius:8px}}
table{{width:100%;border-collapse:collapse;background:white}} th,td{{padding:8px;border-bottom:1px solid #eee;text-align:left}}
@media(max-width:900px){{.compare{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<header><h2 style="margin:0">Alliance Semantic Intelligence OS V3 · Review Mode</h2>
<small>Human-reviewed learning · V2.3 remains production fallback</small></header>
<nav>
<a href="/alliance/primary">Dashboard</a>
<a href="/deal-match-ai-v60">Deal Matcher</a>
<a href="/semantic-v3/review">Review Queue</a>
<a href="/api/semantic-v3/review-stats">Review Stats JSON</a>
</nav>
<main>{body}</main>
</body></html>"""


def review_queue_html(engine, limit: int = 50) -> str:
    ensure_schema(engine)
    limit = max(1, min(int(limit), 200))

    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT id, created_at, source, raw_text, comparison,
                   reviewed, review_decision, reviewer
            FROM pi_semantic_shadow_v3_runs
            ORDER BY reviewed ASC, id DESC
            LIMIT :limit
        """), {"limit": limit}).mappings().all()

    tr = []
    for r in rows:
        c = r.get("comparison") or {}
        tr.append(
            "<tr>"
            f"<td><a class='btn' href='/semantic-v3/review/{_e(r['id'])}'>Review #{_e(r['id'])}</a></td>"
            f"<td>{_e(r['created_at'])}</td>"
            f"<td>{_e(r['source'])}</td>"
            f"<td>{_e(str(r['raw_text'])[:240])}</td>"
            f"<td>{_e(c.get('location_count_v2'))} → {_e(c.get('location_count_v3'))}</td>"
            f"<td>{_e(c.get('transaction_changed'))}</td>"
            f"<td>{_e(c.get('asset_changed'))}</td>"
            f"<td>{_e(r['reviewed'])}</td>"
            f"<td>{_e(r['review_decision'] or '')}</td>"
            "</tr>"
        )

    s = stats(engine)
    body = (
        "<div class='card'><h2>Review Queue</h2>"
        f"<p><b>Total:</b> {_e(s['total'])} · <b>Reviewed:</b> {_e(s['reviewed'])} · "
        f"<b>Pending:</b> {_e(s['pending'])} · <b>V3 preference rate:</b> {_e(s['v3_preference_rate'])}</p>"
        f"<p class='amber'>{_e(s['promotion_reason'])}</p></div>"
        "<div class='card'><table><tr>"
        "<th>Action</th><th>Created</th><th>Source</th><th>Requirement</th>"
        "<th>Locations V2→V3</th><th>Tx Changed</th><th>Asset Changed</th>"
        "<th>Reviewed</th><th>Decision</th></tr>"
        + "".join(tr)
        + "</table></div>"
    )
    return _page(body)


def review_record_html(engine, run_id: int) -> str:
    row = _row(engine, run_id)
    v2 = row.get("v2_snapshot") or {}
    v3 = row.get("v3_snapshot") or {}
    comparison = row.get("comparison") or {}

    body = (
        "<div class='card'><h2>Raw Requirement</h2>"
        f"<p>{_e(row.get('raw_text'))}</p>"
        f"<p><b>Record:</b> #{_e(run_id)} · <b>Source:</b> {_e(row.get('source'))} · "
        f"<b>Reviewed:</b> {_e(row.get('reviewed'))} · <b>Decision:</b> {_e(row.get('review_decision') or 'PENDING')}</p></div>"
        "<div class='compare'>"
        + _summary_card("Current Production Interpretation · V2.3", v2, False)
        + _summary_card("AI V3 Interpretation", v3, True)
        + "</div>"
        "<div class='card'><h3>V2 ↔ V3 Difference</h3><pre>"
        + _e(_json(comparison)) + "</pre></div>"
        "<div class='card'><h3>Human Review</h3>"
        "<label>Reviewer</label><input id='reviewer' value='Alliance Reviewer'>"
        "<label style='display:block;margin-top:8px'>Notes / reason</label>"
        "<textarea id='notes' rows='3' placeholder='Optional reason'></textarea>"
        "<div style='display:flex;gap:8px;flex-wrap:wrap;margin-top:12px'>"
        f"<button onclick=\"saveDecision('USE_V3')\">USE V3 INTERPRETATION</button>"
        f"<button onclick=\"saveDecision('KEEP_V2')\">KEEP CURRENT V2.3</button>"
        f"<button onclick=\"toggleEdit()\">EDIT V3</button>"
        f"<button onclick=\"saveDecision('REJECT')\">REJECT / NEEDS RESEARCH</button>"
        "</div>"
        "<div id='editBox' style='display:none;margin-top:14px'>"
        "<p class='amber'><b>Edit the complete canonical V3 object.</b> Invalid schema is rejected automatically.</p>"
        "<textarea id='editedJson' rows='24'>" + _e(_json(v3)) + "</textarea>"
        "<button style='margin-top:8px' onclick=\"saveEdited()\">SAVE EDITED V3</button>"
        "</div>"
        "<pre id='resultBox' style='display:none'></pre>"
        "</div>"
        "<div class='card'><p class='green'><b>Safety:</b> Approving V3 here makes it authoritative only for this review record. "
        "It does not switch the live matcher away from V2.3.</p></div>"
        f"""<script>
function toggleEdit(){{
  const el=document.getElementById('editBox');
  el.style.display = el.style.display==='none' ? 'block' : 'none';
}}
async function send(payload){{
  const res=await fetch('/api/semantic-v3/review/{int(run_id)}',{{
    method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(payload)
  }});
  const data=await res.json();
  const box=document.getElementById('resultBox');
  box.style.display='block';
  box.textContent=JSON.stringify(data,null,2);
  if(res.ok) setTimeout(()=>location.reload(),500);
}}
function base(decision){{
  return {{
    decision,
    reviewer:document.getElementById('reviewer').value,
    notes:document.getElementById('notes').value
  }};
}}
function saveDecision(decision){{ send(base(decision)); }}
function saveEdited(){{
  let obj;
  try{{ obj=JSON.parse(document.getElementById('editedJson').value); }}
  catch(e){{ alert('Invalid JSON: '+e); return; }}
  const p=base('EDITED'); p.corrected_v3=obj; send(p);
}}
</script>"""
    )
    return _page(body)


def register(core) -> Dict[str, Any]:
    app = core.app
    ensure_schema(core.engine)
    registered = []

    paths = {getattr(r, "path", None) for r in app.router.routes}

    if "/semantic-v3/review" not in paths:
        @app.get("/semantic-v3/review", response_class=HTMLResponse)
        def semantic_v3_review_queue(limit: int = 50):
            return HTMLResponse(review_queue_html(core.engine, limit))
        registered.append("/semantic-v3/review")

    if "/semantic-v3/review/{run_id}" not in paths:
        @app.get("/semantic-v3/review/{run_id}", response_class=HTMLResponse)
        def semantic_v3_review_record(run_id: int):
            return HTMLResponse(review_record_html(core.engine, run_id))
        registered.append("/semantic-v3/review/{run_id}")

    if "/api/semantic-v3/review/{run_id}" not in paths:
        @app.post("/api/semantic-v3/review/{run_id}")
        def semantic_v3_review_save(run_id: int, payload: ReviewDecision):
            return apply_decision(core.engine, run_id, payload)
        registered.append("/api/semantic-v3/review/{run_id}")

    if "/api/semantic-v3/review-stats" not in paths:
        @app.get("/api/semantic-v3/review-stats")
        def semantic_v3_review_stats():
            return stats(core.engine)
        registered.append("/api/semantic-v3/review-stats")

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "mode": "REVIEW",
        "registered_routes": registered,
        "production_behavior_changed": False,
        "production_matching_brain": "V2.3",
        "v3_global_authoritative": False,
    }
