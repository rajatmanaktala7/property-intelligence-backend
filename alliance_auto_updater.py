from __future__ import annotations
import os, time, threading, traceback
from datetime import datetime, timezone
from sqlalchemy import text

VERSION = "4.4.0-AUTO-UPDATER"
STATE = {
    "status": "IDLE",
    "version": VERSION,
    "last_run": None,
    "last_result": None,
    "last_error": None,
    "runs": 0,
    "deployment_activation": False,
    "refresh_interval_seconds": 300,
}
_LOCK = threading.Lock()
_STARTED = False

def _utc():
    return datetime.now(timezone.utc).isoformat()

def _ensure_state(core):
    with core.engine.begin() as c:
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS pi_auto_update_state(
          component TEXT PRIMARY KEY,
          deployed_version TEXT,
          last_source_id BIGINT,
          last_run_at TIMESTAMPTZ,
          last_result TEXT,
          updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """))

def _source_max_id():
    import alliance_v44_whatsapp_property_master as v44
    w = v44._wa_engine()
    if w is None:
        return 0
    with w.connect() as c:
        return int(c.execute(text("SELECT COALESCE(MAX(id),0) FROM wa_messages")).scalar() or 0)

def _saved_state(core):
    _ensure_state(core)
    with core.engine.connect() as c:
        r = c.execute(text(
            "SELECT deployed_version,last_source_id "
            "FROM pi_auto_update_state WHERE component='WHATSAPP_MASTER'"
        )).first()
    return (r[0], int(r[1] or 0)) if r else (None, 0)

def _save(core, source_id, result):
    with core.engine.begin() as c:
        c.execute(text("""
          INSERT INTO pi_auto_update_state(
            component,deployed_version,last_source_id,last_run_at,last_result,updated_at
          )
          VALUES('WHATSAPP_MASTER',:v,:sid,NOW(),:res,NOW())
          ON CONFLICT(component) DO UPDATE SET
            deployed_version=EXCLUDED.deployed_version,
            last_source_id=EXCLUDED.last_source_id,
            last_run_at=NOW(),
            last_result=EXCLUDED.last_result,
            updated_at=NOW()
        """), {
            "v": VERSION,
            "sid": source_id,
            "res": str(result)[:4000],
        })

def run_once(core, force=False, limit=5000):
    if not _LOCK.acquire(blocking=False):
        return {"status": "SKIPPED", "reason": "RUN_ALREADY_ACTIVE"}
    try:
        STATE["status"] = "CHECKING"
        STATE["last_error"] = None

        deployed_version, last_source_id = _saved_state(core)
        current_source_id = _source_max_id()

        version_changed = deployed_version != VERSION
        source_changed = current_source_id > last_source_id

        if not force and not version_changed and not source_changed:
            STATE["status"] = "UP_TO_DATE"
            return {
                "status": "UP_TO_DATE",
                "source_max_id": current_source_id,
                "deployed_version": deployed_version,
            }

        fn = getattr(core, "_v44_rebuild_whatsapp_master", None)
        if not callable(fn):
            raise RuntimeError("V4.4 rebuild callback is not registered")

        STATE["status"] = "REBUILDING"
        result = fn(limit)
        _save(core, current_source_id, result)

        STATE["status"] = "OK"
        STATE["last_run"] = _utc()
        STATE["last_result"] = result
        STATE["runs"] = STATE["runs"] + 1
        STATE["deployment_activation"] = version_changed
        return result

    except Exception as e:
        STATE["status"] = "ERROR"
        STATE["last_run"] = _utc()
        STATE["last_error"] = f"{type(e).__name__}: {e}"
        print("[auto-updater]", STATE["last_error"])
        traceback.print_exc()
        return {"status": "ERROR", "error": STATE["last_error"]}
    finally:
        _LOCK.release()

def _loop(core):
    time.sleep(int(os.getenv("ALLIANCE_AUTO_UPDATE_START_DELAY", "12")))
    limit = int(os.getenv("ALLIANCE_AUTO_UPDATE_LIMIT", "5000"))
    run_once(core, force=False, limit=limit)

    interval = max(120, int(os.getenv("ALLIANCE_AUTO_REFRESH_SECONDS", "300")))
    STATE["refresh_interval_seconds"] = interval

    while True:
        time.sleep(interval)
        run_once(core, force=False, limit=limit)

def start(core):
    global _STARTED
    if _STARTED:
        return STATE

    _STARTED = True
    thread = threading.Thread(
        target=_loop,
        args=(core,),
        name="alliance-auto-updater",
        daemon=True,
    )
    thread.start()
    return STATE
