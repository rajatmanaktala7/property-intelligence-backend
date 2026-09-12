from __future__ import annotations
import html, json, threading, time, uuid
from datetime import datetime, timezone
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import inspect, text

VERSION = "2.0.0-DETERMINISTIC-RECTIFICATION-CLASSIFIER"
RUN_EVERY_SECONDS = 900
BOOT_DELAY_SECONDS = 120
_LOCK = threading.Lock()
_LAST = {"status":"IDLE","version":VERSION,"mode":"DETERMINISTIC_SAFE_REPAIR"}

def _now():
    return datetime.now(timezone.utc)

def _app(core):
    app = getattr(core, "app", None)
    return app or core

def _auth(core, request: Request):
    try:
        core.need_login(request)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Login required") from exc

def _exists(engine, table):
    try:
        return bool(inspect(engine).has_table(table))
    except Exception:
        return False

def _ensure_schema(engine):
    stmts = [
        "ALTER TABLE alliance_wa_source_canonical_v1 ADD COLUMN IF NOT EXISTS effective_messages BIGINT",
        "ALTER TABLE alliance_wa_source_canonical_v1 ADD COLUMN IF NOT EXISTS gap_code TEXT",
        "ALTER TABLE alliance_wa_source_canonical_v1 ADD COLUMN IF NOT EXISTS repair_state TEXT",
        "ALTER TABLE alliance_wa_source_canonical_v1 ADD COLUMN IF NOT EXISTS last_rectified_at TIMESTAMPTZ",
        """CREATE TABLE IF NOT EXISTS alliance_rectification_actions_v2(
            action_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            canonical_source_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            gap_code TEXT NOT NULL,
            risk TEXT NOT NULL,
            auto_apply_eligible BOOLEAN NOT NULL DEFAULT FALSE,
            applied BOOLEAN NOT NULL DEFAULT FALSE,
            before_state JSONB NOT NULL DEFAULT '{}'::jsonb,
            after_state JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        "CREATE INDEX IF NOT EXISTS ix_alliance_rectification_actions_v2_run ON alliance_rectification_actions_v2(run_id)",
        "CREATE INDEX IF NOT EXISTS ix_alliance_rectification_actions_v2_source ON alliance_rectification_actions_v2(canonical_source_id)",
    ]
    with engine.begin() as cx:
        for stmt in stmts:
            cx.execute(text(stmt))

def _classify(row):
    declared = int(row["declared_messages"] or 0)
    actual = int(row["actual_messages"] or 0)
    wai = int(row["wai_messages"] or 0)
    aliases = int(row["alias_count"] or 0)

    if declared == actual and wai == actual:
        return "RECONCILED", "NONE", False
    if declared != actual and wai == actual:
        return "STALE_SOURCE_COUNTER", "LOW", True
    if actual > wai:
        if wai == 0:
            return "WAI_ABSENT_OR_UNMAPPED", "HIGH", False
        return "WA_TO_WAI_MISSING", "MEDIUM", False
    if wai > actual:
        return "WAI_ONLY_OR_DUPLICATE_IMPORT", "MEDIUM", False
    if aliases > 1:
        return "SOURCE_ALIAS_CLUSTER", "MEDIUM", False
    return "AMBIGUOUS_MAPPING", "HIGH", False

def _run(core):
    if not _LOCK.acquire(blocking=False):
        return {"status":"BUSY","version":VERSION}
    run_id = str(uuid.uuid4())
    try:
        eng = core.engine
        if not _exists(eng, "alliance_wa_source_canonical_v1"):
            return {"status":"ERROR","version":VERSION,"error":"V1 canonical source table missing"}
        _ensure_schema(eng)

        with eng.connect() as cx:
            rows = [dict(r) for r in cx.execute(text("""
                SELECT canonical_source_id, canonical_key, display_name, alias_count,
                       declared_messages, actual_messages, wai_messages,
                       counter_drift, status
                FROM alliance_wa_source_canonical_v1
                ORDER BY display_name
            """)).mappings()]

        counts = {}
        auto_applied = 0
        with eng.begin() as cx:
            for row in rows:
                code, risk, auto = _classify(row)
                counts[code] = counts.get(code, 0) + 1
                effective = int(row["actual_messages"] or 0)
                repair_state = "AUTO_REPAIRED_METADATA" if auto else ("CLEAN" if code=="RECONCILED" else "NEEDS_REVIEW")
                before = {
                    "declared_messages": int(row["declared_messages"] or 0),
                    "actual_messages": int(row["actual_messages"] or 0),
                    "wai_messages": int(row["wai_messages"] or 0),
                    "effective_messages": None,
                    "gap_code": None,
                    "repair_state": None,
                }
                after = {
                    "declared_messages": before["declared_messages"],
                    "actual_messages": before["actual_messages"],
                    "wai_messages": before["wai_messages"],
                    "effective_messages": effective,
                    "gap_code": code,
                    "repair_state": repair_state,
                }
                action_id = "RECT2-" + uuid.uuid4().hex.upper()
                cx.execute(text("""
                    INSERT INTO alliance_rectification_actions_v2
                    (action_id,run_id,canonical_source_id,action_type,gap_code,risk,
                     auto_apply_eligible,applied,before_state,after_state)
                    VALUES(:aid,:rid,:sid,:atype,:gap,:risk,:eligible,:applied,
                           CAST(:before AS JSONB),CAST(:after AS JSONB))
                """), {
                    "aid":action_id,"rid":run_id,"sid":row["canonical_source_id"],
                    "atype":"RECTIFY_CANONICAL_METADATA","gap":code,"risk":risk,
                    "eligible":bool(auto),"applied":bool(auto),
                    "before":json.dumps(before),"after":json.dumps(after),
                })
                cx.execute(text("""
                    UPDATE alliance_wa_source_canonical_v1
                    SET effective_messages=:effective,
                        gap_code=:gap,
                        repair_state=:state,
                        last_rectified_at=NOW()
                    WHERE canonical_source_id=:sid
                """), {"effective":effective,"gap":code,"state":repair_state,"sid":row["canonical_source_id"]})
                if auto:
                    auto_applied += 1

        result = {
            "status":"PASS",
            "version":VERSION,
            "run_id":run_id,
            "mode":"DETERMINISTIC_SAFE_REPAIR",
            "canonical_sources":len(rows),
            "classifications":counts,
            "auto_applied_metadata_repairs":auto_applied,
            "raw_whatsapp_mutations":0,
            "wai_raw_mutations":0,
            "master_property_mutations":0,
            "matcher_mutations":0,
            "rule":"Only stale canonical metadata is auto-repaired; ambiguous/raw/master records are never changed.",
        }
        _LAST.update({"status":"PASS","last_run":_now().isoformat(),"result":result})
        return result
    except Exception as exc:
        _LAST.update({"status":"ERROR","last_run":_now().isoformat(),"error":f"{type(exc).__name__}: {exc}"})
        return {"status":"ERROR","version":VERSION,"error":_LAST["error"]}
    finally:
        _LOCK.release()

def _status(core):
    out = dict(_LAST)
    try:
        if _exists(core.engine, "alliance_wa_source_canonical_v1"):
            with core.engine.connect() as cx:
                rows = [dict(r) for r in cx.execute(text("""
                    SELECT gap_code, repair_state, COUNT(*) AS n
                    FROM alliance_wa_source_canonical_v1
                    GROUP BY gap_code, repair_state
                    ORDER BY n DESC
                """)).mappings()]
            out["live_classification_counts"] = rows
    except Exception as exc:
        out["status_read_error"] = f"{type(exc).__name__}: {exc}"
    return out

def _page(core):
    s = _status(core)
    esc = lambda x: html.escape(str(x))
    result = s.get("result") or {}
    rows = "".join("<tr><th>"+esc(k)+"</th><td>"+esc(v)+"</td></tr>" for k,v in result.items())
    classes = s.get("live_classification_counts") or []
    crows = "".join("<tr><td>"+esc(r.get("gap_code"))+"</td><td>"+esc(r.get("repair_state"))+"</td><td>"+esc(r.get("n"))+"</td></tr>" for r in classes)
    return "<!doctype html><html><head><title>Alliance Database Rectification V2</title><style>body{font-family:Arial;margin:24px}table{border-collapse:collapse;margin-bottom:24px}td,th{border:1px solid #ddd;padding:8px;text-align:left}</style></head><body><h1>Alliance Database Rectification V2</h1><p>Deterministic classifier and safe canonical-metadata repair. Raw WhatsApp, WAI raw, Master Properties and Matcher remain untouched.</p><table>"+rows+"</table><h2>Live Classification</h2><table><tr><th>Gap Code</th><th>State</th><th>Count</th></tr>"+crows+"</table></body></html>"

def _worker(core):
    time.sleep(BOOT_DELAY_SECONDS)
    while True:
        try:
            _run(core)
        except Exception as exc:
            _LAST.update({"status":"ERROR","error":f"{type(exc).__name__}: {exc}"})
        time.sleep(RUN_EVERY_SECONDS)

def register(core):
    app = _app(core)
    routes = {getattr(r,"path",None) for r in getattr(app,"routes",[])}
    if not routes and hasattr(app,"router"):
        routes = {getattr(r,"path",None) for r in getattr(app.router,"routes",[])}
    added = []
    if "/api/alliance/database-rectification-v2/status" not in routes:
        @app.get("/api/alliance/database-rectification-v2/status")
        async def rect2_status(request: Request):
            _auth(core,request)
            return JSONResponse(_status(core))
        added.append("/api/alliance/database-rectification-v2/status")
    if "/api/alliance/database-rectification-v2/run" not in routes:
        @app.post("/api/alliance/database-rectification-v2/run")
        async def rect2_run(request: Request):
            _auth(core,request)
            return JSONResponse(_run(core))
        added.append("/api/alliance/database-rectification-v2/run")
    if "/alliance/admin/database-rectification-v2" not in routes:
        @app.get("/alliance/admin/database-rectification-v2", response_class=HTMLResponse)
        async def rect2_page(request: Request):
            _auth(core,request)
            return HTMLResponse(_page(core))
        added.append("/alliance/admin/database-rectification-v2")
    state = getattr(app,"state",None)
    if state is not None and not getattr(state,"alliance_rectification_worker_v2",False):
        setattr(state,"alliance_rectification_worker_v2",True)
        threading.Thread(target=_worker,args=(core,),daemon=True,name="alliance-rectification-v2").start()
    return {
        "status":"REGISTERED","version":VERSION,"mode":"DETERMINISTIC_SAFE_REPAIR",
        "routes":added,"automatic_interval_seconds":RUN_EVERY_SECONDS,
        "raw_whatsapp_mutations":0,"wai_raw_mutations":0,
        "master_property_mutations":0,"matcher_mutations":0
    }
