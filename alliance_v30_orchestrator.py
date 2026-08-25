
import json
from decimal import Decimal
from datetime import datetime, date, time, timezone
from sqlalchemy import text
from fastapi import Request, Body
from fastapi.responses import HTMLResponse

MODULE_VERSION = "3.0.1-JSON-SAFE-ORCHESTRATOR"

def _json_safe(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


PIPELINE_STEPS = [
    "V2.6_TEAM_ACTION",
    "V2.7_EXISTING_INVENTORY",
    "V2.8_EXTERNAL_DISCOVERY",
    "V2.9A_SPLIT_EXTERNAL_ENTITIES",
    "V2.9.5_ENTITY_VERIFICATION",
]

def _ensure_schema(engine):
    with engine.begin() as c:
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_v30_orchestrator_run(
          run_id BIGSERIAL PRIMARY KEY,
          requirement_code TEXT,
          action_id BIGINT,
          trigger_source TEXT NOT NULL DEFAULT 'MANUAL',
          run_status TEXT NOT NULL DEFAULT 'RUNNING',
          current_step TEXT,
          next_step TEXT,
          requires_human_review BOOLEAN NOT NULL DEFAULT FALSE,
          summary JSONB NOT NULL DEFAULT '{}'::jsonb,
          error_message TEXT,
          started_at TIMESTAMPTZ DEFAULT NOW(),
          completed_at TIMESTAMPTZ,
          updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """))
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_v30_orchestrator_config(
          config_key TEXT PRIMARY KEY,
          config_value TEXT NOT NULL,
          updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """))
        c.execute(text("""
        INSERT INTO ai_v30_orchestrator_config(config_key,config_value,updated_at)
        VALUES
          ('enabled','true',NOW()),
          ('auto_external_discovery','true',NOW()),
          ('auto_split','true',NOW()),
          ('auto_verify','false',NOW()),
          ('auto_share','false',NOW()),
          ('auto_promote_core_index','false',NOW())
        ON CONFLICT(config_key) DO NOTHING
        """))

def _get_config(engine):
    _ensure_schema(engine)
    with engine.connect() as c:
        rows = c.execute(text("""
          SELECT config_key,config_value
          FROM ai_v30_orchestrator_config
        """)).mappings().all()
    return {r["config_key"]: r["config_value"].lower() == "true" for r in rows}

def _set_config(engine, key, value):
    _ensure_schema(engine)
    with engine.begin() as c:
        c.execute(text("""
          INSERT INTO ai_v30_orchestrator_config(config_key,config_value,updated_at)
          VALUES(:k,:v,NOW())
          ON CONFLICT(config_key) DO UPDATE SET
            config_value=EXCLUDED.config_value,
            updated_at=NOW()
        """), {"k": key, "v": "true" if bool(value) else "false"})

def _new_run(engine, requirement_code, action_id, trigger_source):
    _ensure_schema(engine)
    with engine.begin() as c:
        row = c.execute(text("""
          INSERT INTO ai_v30_orchestrator_run(
            requirement_code,action_id,trigger_source,run_status,current_step,next_step,
            requires_human_review,summary,started_at,updated_at
          )
          VALUES(
            :requirement_code,:action_id,:trigger_source,'RUNNING','START','V2.6_TEAM_ACTION',
            FALSE,'{}'::jsonb,NOW(),NOW()
          )
          RETURNING run_id
        """), {
            "requirement_code": requirement_code,
            "action_id": action_id,
            "trigger_source": trigger_source,
        }).mappings().one()
    return int(row["run_id"])

def _update_run(engine, run_id, **kwargs):
    allowed = {
        "run_status","current_step","next_step","requires_human_review",
        "summary","error_message","completed_at","action_id"
    }
    fields = []
    params = {"id": int(run_id)}
    for k,v in kwargs.items():
        if k not in allowed:
            continue
        if k == "summary":
            fields.append("summary=CAST(:summary AS jsonb)")
            params["summary"] = json.dumps(_json_safe(v or {}), ensure_ascii=False)
        elif k == "completed_at":
            fields.append("completed_at=NOW()" if v else "completed_at=NULL")
        else:
            fields.append(f"{k}=:{k}")
            params[k] = v
    fields.append("updated_at=NOW()")
    with engine.begin() as c:
        c.execute(text(f"""
          UPDATE ai_v30_orchestrator_run
          SET {", ".join(fields)}
          WHERE run_id=:id
        """), params)

def _find_action_for_requirement(engine, requirement_code):
    with engine.connect() as c:
        return c.execute(text("""
          SELECT action_id,workflow_status,action_type,priority_score
          FROM ai_v26_team_action
          WHERE requirement_code=:code
          ORDER BY updated_at DESC,action_id DESC
          LIMIT 1
        """), {"code": requirement_code}).mappings().first()

def _run_v26(engine, requirement_code):
    from alliance_v26_team_action import build_actions_from_v25i
    return build_actions_from_v25i(engine, requirement_code, limit=50)

def _run_v27(engine, action_id):
    from alliance_v27_inventory_acquisition import run_inventory_search
    return run_inventory_search(engine, action_id, limit=300)

def _run_v28(engine, action_id):
    from alliance_v28_external_discovery import run_external_discovery
    return run_external_discovery(engine, action_id, count=8)

def _run_v29a_until_done(engine, discovery_id, max_batches=5):
    from alliance_v29a_listing_splitter import split_discovery_batch
    results = []
    for _ in range(max_batches):
        r = split_discovery_batch(engine, discovery_id, batch_size=20)
        results.append(r)
        if r.get("complete"):
            break
        if r.get("status") == "ERROR":
            break
    return results

def _get_top_discovery(engine, action_id):
    with engine.connect() as c:
        return c.execute(text("""
          SELECT discovery_id,evidence_score,review_status,title,source_url
          FROM ai_v28_external_discovery
          WHERE action_id=:id
          ORDER BY evidence_score DESC,discovery_id
          LIMIT 1
        """), {"id": int(action_id)}).mappings().first()

def _get_verification_queue(engine, requirement_code):
    with engine.connect() as c:
        rows = c.execute(text("""
          SELECT split_entity_id,external_entity_code,splitter_score,splitter_status,
                 property_name,property_type,area_min_sqft,monthly_rent
          FROM ai_v29a_split_external_entity
          WHERE requirement_code=:code
            AND splitter_status IN ('VERIFY_FIRST','REVIEW')
          ORDER BY splitter_score DESC,split_entity_id
          LIMIT 50
        """), {"code": requirement_code}).mappings().all()
    return [dict(x) for x in rows]

def orchestrate_requirement(engine, requirement_code, trigger_source="MANUAL"):
    config = _get_config(engine)
    if not config.get("enabled", True):
        return {
            "version": MODULE_VERSION,
            "status": "DISABLED",
            "requirement_code": requirement_code,
        }

    existing_action = _find_action_for_requirement(engine, requirement_code)
    action_id = int(existing_action["action_id"]) if existing_action else None
    run_id = _new_run(engine, requirement_code, action_id, trigger_source)

    summary = {"steps": []}

    try:
        # STEP 1: V2.6
        _update_run(engine, run_id, current_step="V2.6_TEAM_ACTION", next_step="V2.7_EXISTING_INVENTORY")
        if not existing_action:
            r26 = _run_v26(engine, requirement_code)
            summary["steps"].append({"step":"V2.6","result":r26})
            if r26.get("actions"):
                action_id = int(r26["actions"][0]["action_id"])
                _update_run(engine, run_id, action_id=action_id)
        else:
            summary["steps"].append({
                "step":"V2.6",
                "result":{
                    "reused_existing_action":True,
                    "action_id":action_id,
                    "workflow_status":existing_action["workflow_status"],
                }
            })

        if not action_id:
            _update_run(
                engine, run_id,
                run_status="STOPPED",
                current_step="V2.6_TEAM_ACTION",
                next_step="HUMAN_REVIEW",
                requires_human_review=True,
                summary=summary,
                error_message="No V2.6 action available",
                completed_at=True,
            )
            return {
                "version":MODULE_VERSION,
                "run_id":run_id,
                "status":"STOPPED",
                "reason":"No V2.6 action available",
            }

        # STEP 2: V2.7 existing inventory
        _update_run(engine, run_id, current_step="V2.7_EXISTING_INVENTORY", next_step="V2.8_EXTERNAL_DISCOVERY")
        r27 = _run_v27(engine, action_id)
        summary["steps"].append({"step":"V2.7","result":r27})

        if r27.get("shortlist_count", 0) > 0:
            _update_run(
                engine, run_id,
                run_status="WAITING_HUMAN",
                current_step="V2.7_EXISTING_INVENTORY",
                next_step="VERIFY_EXISTING_CANDIDATES",
                requires_human_review=True,
                summary=summary,
                completed_at=True,
            )
            return {
                "version":MODULE_VERSION,
                "run_id":run_id,
                "status":"WAITING_HUMAN",
                "action_id":action_id,
                "next_step":"VERIFY_EXISTING_CANDIDATES",
                "summary":_json_safe(summary),
            }

        if not config.get("auto_external_discovery", True):
            _update_run(
                engine, run_id,
                run_status="WAITING_HUMAN",
                current_step="V2.7_EXISTING_INVENTORY",
                next_step="RUN_EXTERNAL_DISCOVERY_MANUALLY",
                requires_human_review=True,
                summary=summary,
                completed_at=True,
            )
            return {
                "version":MODULE_VERSION,
                "run_id":run_id,
                "status":"WAITING_HUMAN",
                "next_step":"RUN_EXTERNAL_DISCOVERY_MANUALLY",
            }

        # STEP 3: V2.8
        _update_run(engine, run_id, current_step="V2.8_EXTERNAL_DISCOVERY", next_step="V2.9A_SPLIT_EXTERNAL_ENTITIES")
        r28 = _run_v28(engine, action_id)
        summary["steps"].append({"step":"V2.8","result":r28})

        if r28.get("status") not in {"OK", None}:
            _update_run(
                engine, run_id,
                run_status="STOPPED",
                current_step="V2.8_EXTERNAL_DISCOVERY",
                next_step="RETRY_EXTERNAL_DISCOVERY",
                requires_human_review=False,
                summary=summary,
                error_message=r28.get("message") or r28.get("status"),
                completed_at=True,
            )
            return {
                "version":MODULE_VERSION,
                "run_id":run_id,
                "status":"STOPPED",
                "next_step":"RETRY_EXTERNAL_DISCOVERY",
                "provider_result":_json_safe(r28),
            }

        if r28.get("unique_discoveries", 0) == 0:
            _update_run(
                engine, run_id,
                run_status="COMPLETE",
                current_step="V2.8_EXTERNAL_DISCOVERY",
                next_step="NO_CANDIDATE_FOUND",
                requires_human_review=False,
                summary=summary,
                completed_at=True,
            )
            return {
                "version":MODULE_VERSION,
                "run_id":run_id,
                "status":"COMPLETE",
                "next_step":"NO_CANDIDATE_FOUND",
            }

        if not config.get("auto_split", True):
            _update_run(
                engine, run_id,
                run_status="WAITING_HUMAN",
                current_step="V2.8_EXTERNAL_DISCOVERY",
                next_step="RUN_SPLITTER_MANUALLY",
                requires_human_review=True,
                summary=summary,
                completed_at=True,
            )
            return {
                "version":MODULE_VERSION,
                "run_id":run_id,
                "status":"WAITING_HUMAN",
                "next_step":"RUN_SPLITTER_MANUALLY",
            }

        # STEP 4: split best discovery only
        top = _get_top_discovery(engine, action_id)
        if not top:
            _update_run(
                engine, run_id,
                run_status="COMPLETE",
                current_step="V2.8_EXTERNAL_DISCOVERY",
                next_step="NO_DISCOVERY_ROW",
                summary=summary,
                completed_at=True,
            )
            return {
                "version":MODULE_VERSION,
                "run_id":run_id,
                "status":"COMPLETE",
                "next_step":"NO_DISCOVERY_ROW",
            }

        _update_run(engine, run_id, current_step="V2.9A_SPLIT_EXTERNAL_ENTITIES", next_step="V2.9.5_ENTITY_VERIFICATION")
        split_results = _run_v29a_until_done(engine, int(top["discovery_id"]))
        summary["steps"].append({
            "step":"V2.9A",
            "discovery_id":int(top["discovery_id"]),
            "result":split_results,
        })

        queue = _get_verification_queue(engine, requirement_code)
        summary["verification_queue_count"] = len(queue)
        summary["verification_queue"] = queue[:20]

        # STEP 5: stop at human review unless explicitly allowed.
        # We intentionally do not auto-verify because availability, frontage,
        # contact identity and use permissions require human confirmation.
        _update_run(
            engine, run_id,
            run_status="WAITING_HUMAN",
            current_step="V2.9.5_ENTITY_VERIFICATION",
            next_step="VERIFY_INDIVIDUAL_ENTITIES",
            requires_human_review=True,
            summary=summary,
            completed_at=True,
        )

        return {
            "version":MODULE_VERSION,
            "run_id":run_id,
            "status":"WAITING_HUMAN",
            "requirement_code":requirement_code,
            "action_id":action_id,
            "next_step":"VERIFY_INDIVIDUAL_ENTITIES",
            "verification_queue_count":len(queue),
            "verification_queue":_json_safe(queue[:20]),
            "safety":{
                "auto_verify":False,
                "auto_share":False,
                "auto_promote_core_index":False,
            },
        }

    except Exception as exc:
        summary["exception"] = str(exc)
        _update_run(
            engine, run_id,
            run_status="ERROR",
            current_step="ERROR",
            next_step="MANUAL_REVIEW",
            requires_human_review=True,
            summary=summary,
            error_message=str(exc),
            completed_at=True,
        )
        return {
            "version":MODULE_VERSION,
            "run_id":run_id,
            "status":"ERROR",
            "message":str(exc),
        }

def register_v30_routes(core):
    app,engine=core.app,core.engine

    @app.get("/api/v2/intelligence/v30/status")
    def status(req:Request):
        if hasattr(core,"need_login"):
            core.need_login(req)
        config = _get_config(engine)
        with engine.connect() as c:
            latest = c.execute(text("""
              SELECT run_id,requirement_code,action_id,run_status,current_step,next_step,
                     requires_human_review,started_at,completed_at,error_message
              FROM ai_v30_orchestrator_run
              ORDER BY run_id DESC
              LIMIT 1
            """)).mappings().first()
        return {
            "version":MODULE_VERSION,
            "status":"OK",
            "config":config,
            "latest_run":dict(latest) if latest else None,
        }

    @app.post("/api/v2/intelligence/v30/run/{requirement_code}")
    def run(requirement_code:str,req:Request):
        if hasattr(core,"need_login"):
            core.need_login(req)
        return orchestrate_requirement(engine, requirement_code, "MANUAL_RUN_NOW")

    @app.get("/api/v2/intelligence/v30/runs")
    def runs(req:Request,limit:int=50):
        if hasattr(core,"need_login"):
            core.need_login(req)
        limit=max(1,min(int(limit or 50),100))
        _ensure_schema(engine)
        with engine.connect() as c:
            rows=c.execute(text("""
              SELECT run_id,requirement_code,action_id,trigger_source,run_status,
                     current_step,next_step,requires_human_review,error_message,
                     started_at,completed_at,updated_at
              FROM ai_v30_orchestrator_run
              ORDER BY run_id DESC
              LIMIT :lim
            """),{"lim":limit}).mappings().all()
        return {"version":MODULE_VERSION,"count":len(rows),"runs":[dict(x) for x in rows]}

    @app.get("/api/v2/intelligence/v30/runs/{run_id}")
    def run_detail(run_id:int,req:Request):
        if hasattr(core,"need_login"):
            core.need_login(req)
        _ensure_schema(engine)
        with engine.connect() as c:
            row=c.execute(text("""
              SELECT *
              FROM ai_v30_orchestrator_run
              WHERE run_id=:id
              LIMIT 1
            """),{"id":int(run_id)}).mappings().first()
        return {"version":MODULE_VERSION,"run":dict(row) if row else None}

    @app.post("/api/v2/intelligence/v30/config")
    def config(req:Request,payload:dict=Body(...)):
        if hasattr(core,"need_login"):
            core.need_login(req)

        allowed = {
            "enabled",
            "auto_external_discovery",
            "auto_split",
            "auto_verify",
            "auto_share",
            "auto_promote_core_index",
        }

        changed = {}
        for k,v in payload.items():
            if k not in allowed:
                continue
            # hard safety rails
            if k in {"auto_verify","auto_share","auto_promote_core_index"} and bool(v):
                return {
                    "version":MODULE_VERSION,
                    "status":"BLOCKED",
                    "reason":f"{k} cannot be enabled in V3.0 safety policy",
                }
            _set_config(engine,k,bool(v))
            changed[k]=bool(v)

        return {
            "version":MODULE_VERSION,
            "status":"OK",
            "changed":changed,
            "config":_get_config(engine),
        }

    @app.get("/v3/property-orchestrator-legacy",response_class=HTMLResponse)
    def dashboard(req:Request):
        if hasattr(core,"need_login"):
            core.need_login(req)
        return HTMLResponse("""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Alliance V3.0 Control Centre</title>
<style>
body{font-family:Arial;background:#f4f6f8;margin:0}
.wrap{max-width:1100px;margin:32px auto;padding:0 18px}
.card{background:white;border-radius:16px;padding:24px;margin-bottom:18px;box-shadow:0 1px 8px rgba(0,0,0,.06)}
h1{margin-top:0}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.kpi{background:#f8fafc;border-radius:12px;padding:18px}
code{background:#f2f4f7;padding:3px 6px;border-radius:6px}
</style>
</head>
<body>
<div class="wrap">
<div class="card">
<h1>Alliance V3.0 Autonomous Orchestrator & Control Centre</h1>
<p>Runs the property acquisition pipeline in order and stops at mandatory human verification gates.</p>
</div>
<div class="grid">
<div class="kpi"><b>V2.6</b><br>Team Action</div>
<div class="kpi"><b>V2.7</b><br>Existing Inventory</div>
<div class="kpi"><b>V2.8</b><br>External Discovery</div>
<div class="kpi"><b>V2.9A</b><br>Entity Splitter</div>
<div class="kpi"><b>V2.9.5</b><br>Entity Verification</div>
<div class="kpi"><b>Safety</b><br>No Auto Share</div>
</div>
<div class="card">
<p><b>RUN NOW:</b> POST <code>/api/v2/intelligence/v30/run/{requirement_code}</code></p>
<p><b>Status:</b> GET <code>/api/v2/intelligence/v30/status</code></p>
<p><b>Runs:</b> GET <code>/api/v2/intelligence/v30/runs</code></p>
</div>
</div>
</body>
</html>""")

    return app
