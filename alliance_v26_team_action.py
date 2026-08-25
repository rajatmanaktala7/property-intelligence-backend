
import json
from sqlalchemy import text
from fastapi import Request, Body
from fastapi.responses import HTMLResponse

MODULE_VERSION = "2.6.0-TEAM-ACTION-WORKFLOW"

ALLOWED_STATUSES = {
    "NEW",
    "ASSIGNED",
    "VERIFYING",
    "VERIFIED",
    "READY_TO_SHARE",
    "SHARED",
    "FOLLOW_UP",
    "INVENTORY_SEARCH",
    "CLOSED",
    "REJECTED",
}

def action_for_decision(decision):
    d = str(decision or "").upper()
    if d == "STRONG_MATCH":
        return "VERIFY_AVAILABILITY", "NEW"
    if d == "GOOD_MATCH":
        return "REVIEW_MATCH", "NEW"
    if d == "POSSIBLE_MATCH":
        return "MANUAL_REVIEW", "NEW"
    if d == "REJECT":
        return "NO_ACTION", "REJECTED"
    return "REVIEW_MATCH", "NEW"

def sanitize_share_payload(property_row):
    """
    External/client-facing payload.
    Intentionally excludes owner/broker/contact fields.
    """
    p = property_row or {}
    allowed = [
        "property_name",
        "location",
        "area_min_sqft",
        "area_max_sqft",
        "rent_psf_month",
        "monthly_rent",
        "transaction_type",
        "canonical_property_type",
        "floor",
        "frontage_ft",
        "verification_status",
        "source_record_id",
    ]
    return {k: p.get(k) for k in allowed if p.get(k) is not None}

def _ensure_schema(engine):
    with engine.begin() as c:
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_v26_team_action (
          action_id BIGSERIAL PRIMARY KEY,
          requirement_code TEXT NOT NULL,
          source_record_id TEXT,
          decision TEXT NOT NULL,
          action_type TEXT NOT NULL,
          workflow_status TEXT NOT NULL DEFAULT 'NEW',
          assigned_to TEXT,
          priority_score NUMERIC(6,2) DEFAULT 0,

          internal_contact_name TEXT,
          internal_contact_phone TEXT,
          internal_contact_role TEXT,

          share_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
          notes TEXT,

          created_at TIMESTAMPTZ DEFAULT NOW(),
          updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """))
        c.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_ai_v26_status
        ON ai_v26_team_action(workflow_status, updated_at DESC)
        """))
        c.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_ai_v26_requirement
        ON ai_v26_team_action(requirement_code, updated_at DESC)
        """))
        c.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_v26_req_source_action
        ON ai_v26_team_action(requirement_code, COALESCE(source_record_id,''), action_type)
        """))

def _get_action(engine, action_id):
    with engine.connect() as c:
        return c.execute(text("""
          SELECT *
          FROM ai_v26_team_action
          WHERE action_id=:id
        """), {"id": int(action_id)}).mappings().first()

def _property_from_index(engine, source_record_id):
    if not source_record_id:
        return None
    with engine.connect() as c:
        return c.execute(text("""
          SELECT
            property_name,
            location_raw AS location,
            area_min_sqft,
            area_max_sqft,
            rent_psf_month,
            monthly_rent,
            transaction_type,
            canonical_property_type,
            floor_raw AS floor,
            frontage_ft,
            verification_status,
            source_record_id
          FROM ai_property_match_index
          WHERE source_record_id=:sid
          ORDER BY updated_at DESC
          LIMIT 1
        """), {"sid": source_record_id}).mappings().first()

def create_or_update_action(engine, payload):
    _ensure_schema(engine)

    requirement_code = str(payload.get("requirement_code") or "").strip()
    if not requirement_code:
        raise ValueError("requirement_code is required")

    source_record_id = payload.get("source_record_id")
    decision = str(payload.get("decision") or "POSSIBLE_MATCH").upper()

    action_type, default_status = action_for_decision(decision)
    action_type = str(payload.get("action_type") or action_type).upper()

    status = str(payload.get("workflow_status") or default_status).upper()
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"Invalid workflow_status: {status}")

    property_row = _property_from_index(engine, source_record_id)
    share_payload = sanitize_share_payload(dict(property_row) if property_row else payload.get("property") or {})

    params = {
        "requirement_code": requirement_code,
        "source_record_id": source_record_id,
        "decision": decision,
        "action_type": action_type,
        "workflow_status": status,
        "assigned_to": payload.get("assigned_to"),
        "priority_score": float(payload.get("priority_score") or 0),
        "internal_contact_name": payload.get("internal_contact_name"),
        "internal_contact_phone": payload.get("internal_contact_phone"),
        "internal_contact_role": payload.get("internal_contact_role"),
        "share_payload": json.dumps(share_payload),
        "notes": payload.get("notes"),
    }

    with engine.begin() as c:
        row = c.execute(text("""
          INSERT INTO ai_v26_team_action(
            requirement_code,source_record_id,decision,action_type,workflow_status,
            assigned_to,priority_score,
            internal_contact_name,internal_contact_phone,internal_contact_role,
            share_payload,notes,created_at,updated_at
          )
          VALUES(
            :requirement_code,:source_record_id,:decision,:action_type,:workflow_status,
            :assigned_to,:priority_score,
            :internal_contact_name,:internal_contact_phone,:internal_contact_role,
            CAST(:share_payload AS jsonb),:notes,NOW(),NOW()
          )
          ON CONFLICT(requirement_code,COALESCE(source_record_id,''),action_type)
          DO UPDATE SET
            decision=EXCLUDED.decision,
            workflow_status=EXCLUDED.workflow_status,
            assigned_to=COALESCE(EXCLUDED.assigned_to,ai_v26_team_action.assigned_to),
            priority_score=EXCLUDED.priority_score,
            internal_contact_name=COALESCE(EXCLUDED.internal_contact_name,ai_v26_team_action.internal_contact_name),
            internal_contact_phone=COALESCE(EXCLUDED.internal_contact_phone,ai_v26_team_action.internal_contact_phone),
            internal_contact_role=COALESCE(EXCLUDED.internal_contact_role,ai_v26_team_action.internal_contact_role),
            share_payload=EXCLUDED.share_payload,
            notes=COALESCE(EXCLUDED.notes,ai_v26_team_action.notes),
            updated_at=NOW()
          RETURNING *
        """), params).mappings().one()

    return dict(row)

def transition_action(engine, action_id, payload):
    _ensure_schema(engine)

    status = str(payload.get("workflow_status") or "").upper()
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"Invalid workflow_status: {status}")

    current = _get_action(engine, action_id)
    if not current:
        raise ValueError("Action not found")

    # Safety: READY_TO_SHARE requires verified workflow.
    if status == "READY_TO_SHARE" and current["workflow_status"] not in {"VERIFIED", "READY_TO_SHARE"}:
        raise ValueError("Property must be VERIFIED before READY_TO_SHARE")

    with engine.begin() as c:
        row = c.execute(text("""
          UPDATE ai_v26_team_action
          SET
            workflow_status=:status,
            assigned_to=COALESCE(:assigned_to,assigned_to),
            notes=COALESCE(:notes,notes),
            updated_at=NOW()
          WHERE action_id=:id
          RETURNING *
        """), {
            "status": status,
            "assigned_to": payload.get("assigned_to"),
            "notes": payload.get("notes"),
            "id": int(action_id),
        }).mappings().one()

    return dict(row)

def build_actions_from_v25i(engine, code, limit=50):
    """
    Uses already-stored match results only.
    No core matcher execution.
    No duplicate scan.
    """
    from alliance_v25i_post_processor import post_process_matches

    result = post_process_matches(
        engine,
        code,
        minimum_score=0,
        limit=min(max(int(limit or 50), 1), 50),
    )

    matches = result.get("matches") or []
    created = []

    for item in matches:
        decision = item.get("v25_decision")
        if decision == "REJECT":
            continue

        row = create_or_update_action(engine, {
            "requirement_code": result.get("requirement_code") or code,
            "source_record_id": item.get("source_record_id"),
            "decision": decision,
            "priority_score": item.get("team_priority_score") or 0,
            "workflow_status": "NEW",
        })
        created.append(row)

    inventory_gap_created = False
    if not created and result.get("inventory_gap"):
        row = create_or_update_action(engine, {
            "requirement_code": result.get("requirement_code") or code,
            "source_record_id": None,
            "decision": "INVENTORY_GAP",
            "action_type": "INVENTORY_ACQUISITION",
            "workflow_status": "INVENTORY_SEARCH",
            "priority_score": 80,
            "notes": result["inventory_gap"].get("reason"),
        })
        created.append(row)
        inventory_gap_created = True

    return {
        "version": MODULE_VERSION,
        "requirement_code": result.get("requirement_code") or code,
        "execution_mode": "STORED_RESULT_TO_TEAM_ACTION",
        "core_matcher_untouched": True,
        "actions_created_or_updated": len(created),
        "inventory_gap_action_created": inventory_gap_created,
        "actions": created,
    }

def register_v26_routes(core):
    app, engine = core.app, core.engine
    _ensure_schema(engine)

    @app.post("/api/v2/intelligence/v26/actions")
    def create_action(req: Request, payload: dict = Body(...)):
        if hasattr(core, "need_login"):
            core.need_login(req)
        try:
            return {
                "version": MODULE_VERSION,
                "action": create_or_update_action(engine, payload),
            }
        except Exception as exc:
            return {
                "version": MODULE_VERSION,
                "status": "ERROR",
                "message": str(exc),
            }

    @app.post("/api/v2/intelligence/v26/from-v25i/{code}")
    def from_v25i(
        code: str,
        req: Request,
        limit: int = 50,
    ):
        if hasattr(core, "need_login"):
            core.need_login(req)
        try:
            return build_actions_from_v25i(engine, code, limit)
        except Exception as exc:
            return {
                "version": MODULE_VERSION,
                "status": "ERROR",
                "message": str(exc),
            }

    @app.get("/api/v2/intelligence/v26/actions")
    def list_actions(
        req: Request,
        status: str = "",
        assigned_to: str = "",
        limit: int = 100,
    ):
        if hasattr(core, "need_login"):
            core.need_login(req)

        limit = min(max(int(limit or 100), 1), 200)
        clauses = ["TRUE"]
        params = {"lim": limit}

        if status:
            clauses.append("workflow_status=:status")
            params["status"] = status.upper()

        if assigned_to:
            clauses.append("assigned_to=:assigned_to")
            params["assigned_to"] = assigned_to

        with engine.connect() as c:
            rows = c.execute(text(f"""
              SELECT
                action_id,requirement_code,source_record_id,decision,action_type,
                workflow_status,assigned_to,priority_score,notes,created_at,updated_at
              FROM ai_v26_team_action
              WHERE {" AND ".join(clauses)}
              ORDER BY priority_score DESC,updated_at DESC
              LIMIT :lim
            """), params).mappings().all()

        return {
            "version": MODULE_VERSION,
            "count": len(rows),
            "actions": [dict(x) for x in rows],
        }

    @app.get("/api/v2/intelligence/v26/actions/{action_id}/internal")
    def internal_action(action_id: int, req: Request):
        if hasattr(core, "need_login"):
            core.need_login(req)

        row = _get_action(engine, action_id)
        if not row:
            return {"status": "NOT_FOUND"}

        return {
            "version": MODULE_VERSION,
            "visibility": "INTERNAL_ONLY",
            "action": dict(row),
        }

    @app.get("/api/v2/intelligence/v26/actions/{action_id}/share")
    def share_action(action_id: int, req: Request):
        if hasattr(core, "need_login"):
            core.need_login(req)

        row = _get_action(engine, action_id)
        if not row:
            return {"status": "NOT_FOUND"}

        if row["workflow_status"] not in {"READY_TO_SHARE", "SHARED", "FOLLOW_UP", "CLOSED"}:
            return {
                "version": MODULE_VERSION,
                "status": "BLOCKED",
                "reason": "Property must be VERIFIED and moved to READY_TO_SHARE before external sharing.",
            }

        return {
            "version": MODULE_VERSION,
            "visibility": "EXTERNAL_SAFE",
            "requirement_code": row["requirement_code"],
            "decision": row["decision"],
            "property": row["share_payload"],
            "contact_fields_included": False,
        }

    @app.post("/api/v2/intelligence/v26/actions/{action_id}/status")
    def update_status(
        action_id: int,
        req: Request,
        payload: dict = Body(...),
    ):
        if hasattr(core, "need_login"):
            core.need_login(req)

        try:
            return {
                "version": MODULE_VERSION,
                "action": transition_action(engine, action_id, payload),
            }
        except Exception as exc:
            return {
                "version": MODULE_VERSION,
                "status": "ERROR",
                "message": str(exc),
            }

    @app.get("/v2/team-action-workflow", response_class=HTMLResponse)
    def dashboard(req: Request):
        if hasattr(core, "need_login"):
            core.need_login(req)

        return HTMLResponse("""<!doctype html>
<html>
<head><meta charset="utf-8"><title>V2.6 Team Action Workflow</title></head>
<body style="font-family:Arial;background:#f5f7fa">
<div style="max-width:1050px;margin:30px auto;background:white;padding:28px;border-radius:14px">
<h1>V2.6 Team Action Workflow</h1>
<p>Stable core matcher remains untouched.</p>
<p><b>STRONG_MATCH</b> → Verify availability → Verified → Ready to share.</p>
<p><b>GOOD_MATCH</b> → Team review.</p>
<p><b>POSSIBLE_MATCH</b> → Manual review.</p>
<p><b>REJECT</b> → No communication.</p>
<p><b>Inventory Gap</b> → Inventory acquisition/search queue.</p>
<p>Owner/broker contact fields are internal-only and are never included in the external share payload.</p>
</div>
</body>
</html>""")

    return app
