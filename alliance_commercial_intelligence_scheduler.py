from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import traceback
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

VERSION = "1.0.0-AUTONOMOUS-SECURE-SCHEDULER"

# Safe defaults. All may be overridden with Railway environment variables.
ENABLED = os.getenv("ACI_AUTOMATION_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
STARTUP_DELAY_SECONDS = max(20, int(os.getenv("ACI_STARTUP_DELAY_SECONDS", "75")))
DAILY_INTERVAL_SECONDS = max(3600, int(os.getenv("ACI_DAILY_INTERVAL_SECONDS", str(24 * 3600))))
WEEKLY_INTERVAL_SECONDS = max(6 * 3600, int(os.getenv("ACI_WEEKLY_INTERVAL_SECONDS", str(7 * 24 * 3600))))
CITY_DELAY_SECONDS = max(1, int(os.getenv("ACI_CITY_DELAY_SECONDS", "4")))
MAX_CITIES_PER_CYCLE = max(1, min(100, int(os.getenv("ACI_MAX_CITIES_PER_CYCLE", "30"))))
MAX_MODES_PER_CITY = max(1, min(5, int(os.getenv("ACI_MAX_MODES_PER_CITY", "5"))))

DEFAULT_CITIES = [
    "Delhi", "Gurugram", "Noida", "Greater Noida", "Ghaziabad", "Faridabad",
    "Chandigarh", "Mohali", "Ludhiana", "Amritsar", "Jalandhar",
    "Jaipur", "Udaipur", "Lucknow", "Kanpur", "Agra", "Dehradun",
]

DAILY_MODES = ["UPCOMING", "EXISTING", "LEASING", "HIRING"]
WEEKLY_MODES = ["GOVERNMENT"]

_RUNTIME = {
    "version": VERSION,
    "enabled": ENABLED,
    "state": "NOT_STARTED",
    "thread_alive": False,
    "started_at": None,
    "last_cycle_started_at": None,
    "last_cycle_completed_at": None,
    "last_cycle_kind": None,
    "last_error": None,
    "last_created": 0,
    "last_results_seen": 0,
    "cycles_completed": 0,
    "lock_skips": 0,
    "next_daily_due_at": None,
    "next_weekly_due_at": None,
}

_THREAD: threading.Thread | None = None
_STOP_EVENT = threading.Event()
_LOCAL_RUN_LOCK = threading.Lock()


def _utcnow():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def _safe_actor(core, req: Request | None = None):
    if req is not None:
        try:
            fn = getattr(core, "actor_name", None)
            if callable(fn):
                return str(fn(req) or "team")[:200]
        except Exception:
            pass
    return "AUTOMATION"


def _cities():
    raw = os.getenv("ACI_CITIES", "").strip()
    if not raw:
        return DEFAULT_CITIES[:MAX_CITIES_PER_CYCLE]
    values = []
    seen = set()
    for part in raw.split(","):
        city = " ".join(part.split()).strip()[:120]
        key = city.lower()
        if city and key not in seen:
            seen.add(key)
            values.append(city)
    return (values or DEFAULT_CITIES)[:MAX_CITIES_PER_CYCLE]


def _advisory_key(name: str) -> int:
    # Fits PostgreSQL signed BIGINT range and remains stable across processes.
    digest = hashlib.sha256(name.encode("utf-8")).digest()[:8]
    value = int.from_bytes(digest, "big", signed=False)
    return value - (1 << 64) if value >= (1 << 63) else value


GLOBAL_LOCK_KEY = _advisory_key("alliance-commercial-intelligence-autonomous-runner-v1")


SCHEMA_SQL = r'''
CREATE TABLE IF NOT EXISTS aci_automation_runs(
    id BIGSERIAL PRIMARY KEY,
    run_code TEXT UNIQUE NOT NULL,
    run_kind TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'RUNNING',
    cities_attempted INTEGER NOT NULL DEFAULT 0,
    searches_attempted INTEGER NOT NULL DEFAULT 0,
    results_seen INTEGER NOT NULL DEFAULT 0,
    discoveries_created INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT,
    created_by TEXT NOT NULL DEFAULT 'AUTOMATION'
);
CREATE INDEX IF NOT EXISTS idx_aci_automation_runs_started
ON aci_automation_runs(started_at DESC);
'''


def ensure_scheduler_schema(engine):
    # Additive only. No DROP/TRUNCATE/DELETE statements.
    with engine.begin() as c:
        for stmt in [x.strip() for x in SCHEMA_SQL.split(";") if x.strip()]:
            c.execute(text(stmt))


def _acquire_global_lock(engine):
    conn = engine.connect()
    try:
        acquired = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": GLOBAL_LOCK_KEY}).scalar())
        if acquired:
            return conn
    except Exception:
        conn.close()
        raise
    conn.close()
    return None


def _release_global_lock(conn):
    if conn is None:
        return
    try:
        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": GLOBAL_LOCK_KEY})
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _new_run(engine, run_kind: str, trigger_type: str, actor: str):
    now = _utcnow()
    seed = f"{run_kind}|{trigger_type}|{now.isoformat()}|{os.getpid()}"
    run_code = "ACIRUN-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:14].upper()
    with engine.begin() as c:
        c.execute(text('''
            INSERT INTO aci_automation_runs(run_code,run_kind,trigger_type,created_by)
            VALUES(:code,:kind,:trigger,:actor)
        '''), {"code": run_code, "kind": run_kind, "trigger": trigger_type, "actor": actor[:200]})
    return run_code


def _finish_run(engine, run_code, status, stats, errors):
    summary = "\n".join(errors[-10:])[:8000] if errors else None
    with engine.begin() as c:
        c.execute(text('''
            UPDATE aci_automation_runs
               SET completed_at=NOW(), status=:status,
                   cities_attempted=:cities, searches_attempted=:searches,
                   results_seen=:seen, discoveries_created=:created,
                   error_count=:errors, error_summary=:summary
             WHERE run_code=:code
        '''), {
            "status": status,
            "cities": stats["cities_attempted"],
            "searches": stats["searches_attempted"],
            "seen": stats["results_seen"],
            "created": stats["discoveries_created"],
            "errors": len(errors),
            "summary": summary,
            "code": run_code,
        })


def run_cycle(core, *, run_kind="DAILY", trigger_type="SCHEDULED", actor="AUTOMATION"):
    """Run one bounded discovery cycle.

    Security/data-safety properties:
    - single local runner + PostgreSQL advisory lock across Railway replicas
    - parameterized database writes
    - additive schema only
    - no database credentials or API keys logged/stored
    - all discoveries remain REPORTED in the existing intelligence module
    - no automatic promotion into verified canonical master data
    """
    if not ENABLED and trigger_type == "SCHEDULED":
        return {"status": "DISABLED", "created": 0, "results_seen": 0}

    if not _LOCAL_RUN_LOCK.acquire(blocking=False):
        return {"status": "LOCAL_RUN_ALREADY_ACTIVE", "created": 0, "results_seen": 0}

    engine = getattr(core, "engine", None)
    if engine is None:
        _LOCAL_RUN_LOCK.release()
        raise RuntimeError("Alliance database engine unavailable")

    global_conn = None
    run_code = None
    try:
        ensure_scheduler_schema(engine)
        global_conn = _acquire_global_lock(engine)
        if global_conn is None:
            _RUNTIME["lock_skips"] += 1
            return {"status": "ANOTHER_INSTANCE_RUNNING", "created": 0, "results_seen": 0}

        import alliance_commercial_intelligence_network as network

        run_kind = str(run_kind or "DAILY").upper()
        modes = DAILY_MODES if run_kind != "WEEKLY" else WEEKLY_MODES
        modes = modes[:MAX_MODES_PER_CITY]
        cities = _cities()
        run_code = _new_run(engine, run_kind, trigger_type, actor)

        stats = {
            "cities_attempted": 0,
            "searches_attempted": 0,
            "results_seen": 0,
            "discoveries_created": 0,
        }
        errors = []
        _RUNTIME.update({
            "state": "RUNNING",
            "last_cycle_started_at": _iso(_utcnow()),
            "last_cycle_kind": run_kind,
            "last_error": None,
        })

        for city in cities:
            if _STOP_EVENT.is_set():
                break
            stats["cities_attempted"] += 1
            for mode in modes:
                if _STOP_EVENT.is_set():
                    break
                stats["searches_attempted"] += 1
                try:
                    result = network._run_search(engine, city, mode, "")
                    stats["discoveries_created"] += int(result.get("created") or 0)
                    stats["results_seen"] += int(result.get("results_seen") or 0)
                    for p in result.get("provider_log") or []:
                        status = str(p.get("status") or "").upper()
                        if "ERROR" in status:
                            errors.append(f"{city}/{mode}/{p.get('provider')}: {p.get('error') or status}")
                except Exception as exc:
                    errors.append(f"{city}/{mode}: {type(exc).__name__}: {exc}")
                time.sleep(CITY_DELAY_SECONDS)

        status = "SUCCESS" if not errors else ("PARTIAL" if stats["results_seen"] or stats["discoveries_created"] else "ERROR")
        _finish_run(engine, run_code, status, stats, errors)
        _RUNTIME.update({
            "state": "IDLE",
            "last_cycle_completed_at": _iso(_utcnow()),
            "last_created": stats["discoveries_created"],
            "last_results_seen": stats["results_seen"],
            "cycles_completed": int(_RUNTIME.get("cycles_completed") or 0) + 1,
            "last_error": errors[-1] if errors else None,
        })
        return {"status": status, "run_code": run_code, **stats, "errors": errors[-5:]}

    except Exception as exc:
        _RUNTIME.update({"state": "ERROR", "last_error": f"{type(exc).__name__}: {exc}"})
        if run_code:
            try:
                _finish_run(engine, run_code, "ERROR", {
                    "cities_attempted": 0, "searches_attempted": 0,
                    "results_seen": 0, "discoveries_created": 0,
                }, [f"{type(exc).__name__}: {exc}"])
            except Exception:
                pass
        raise
    finally:
        _release_global_lock(global_conn)
        _LOCAL_RUN_LOCK.release()


def _last_run_times(engine):
    ensure_scheduler_schema(engine)
    with engine.connect() as c:
        rows = c.execute(text('''
            SELECT run_kind, MAX(started_at) AS last_started
              FROM aci_automation_runs
             WHERE status IN ('SUCCESS','PARTIAL','RUNNING')
             GROUP BY run_kind
        ''')).mappings().all()
    return {str(r["run_kind"]).upper(): r["last_started"] for r in rows}


def _is_due(last_dt, interval_seconds):
    if last_dt is None:
        return True
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    return (_utcnow() - last_dt).total_seconds() >= interval_seconds


def _scheduler_loop(core):
    _RUNTIME.update({"state": "STARTUP_WAIT", "thread_alive": True, "started_at": _iso(_utcnow())})
    if _STOP_EVENT.wait(STARTUP_DELAY_SECONDS):
        return

    engine = getattr(core, "engine", None)
    if engine is None:
        _RUNTIME.update({"state": "ERROR", "last_error": "Alliance database engine unavailable", "thread_alive": False})
        return

    try:
        ensure_scheduler_schema(engine)
    except Exception as exc:
        _RUNTIME.update({"state": "ERROR", "last_error": f"schema: {type(exc).__name__}: {exc}", "thread_alive": False})
        return

    # Immediate initial sweep: daily modes. This builds existing/upcoming data
    # without waiting for the next calendar day.
    try:
        times = _last_run_times(engine)
        if _is_due(times.get("DAILY"), DAILY_INTERVAL_SECONDS):
            run_cycle(core, run_kind="DAILY", trigger_type="STARTUP", actor="AUTOMATION")
    except Exception:
        _RUNTIME["last_error"] = traceback.format_exc(limit=4)[-4000:]

    while not _STOP_EVENT.wait(300):
        try:
            times = _last_run_times(engine)
            if _is_due(times.get("DAILY"), DAILY_INTERVAL_SECONDS):
                run_cycle(core, run_kind="DAILY", trigger_type="SCHEDULED", actor="AUTOMATION")
                times = _last_run_times(engine)
            if _is_due(times.get("WEEKLY"), WEEKLY_INTERVAL_SECONDS):
                run_cycle(core, run_kind="WEEKLY", trigger_type="SCHEDULED", actor="AUTOMATION")

            now = _utcnow()
            _RUNTIME["next_daily_due_at"] = _iso(
                (times.get("DAILY") or now).astimezone(timezone.utc)
            )
            _RUNTIME["next_weekly_due_at"] = _iso(
                (times.get("WEEKLY") or now).astimezone(timezone.utc)
            )
            if _RUNTIME.get("state") != "RUNNING":
                _RUNTIME["state"] = "IDLE"
        except Exception as exc:
            _RUNTIME.update({"state": "ERROR", "last_error": f"{type(exc).__name__}: {exc}"})

    _RUNTIME["thread_alive"] = False
    _RUNTIME["state"] = "STOPPED"


def register(core):
    global _THREAD
    engine = getattr(core, "engine", None)
    if engine is None:
        raise RuntimeError("Alliance database engine unavailable")

    ensure_scheduler_schema(engine)
    router = APIRouter()

    @router.get("/api/commercial-intelligence/automation-status")
    def automation_status(req: Request):
        try:
            core.need_login(req)
        except Exception:
            pass
        with engine.connect() as c:
            recent = c.execute(text('''
                SELECT run_code,run_kind,trigger_type,started_at,completed_at,status,
                       cities_attempted,searches_attempted,results_seen,discoveries_created,error_count
                  FROM aci_automation_runs
                 ORDER BY started_at DESC
                 LIMIT 10
            ''')).mappings().all()
        return {
            **dict(_RUNTIME),
            "enabled": ENABLED,
            "cities": _cities(),
            "daily_modes": DAILY_MODES,
            "weekly_modes": WEEKLY_MODES,
            "daily_interval_seconds": DAILY_INTERVAL_SECONDS,
            "weekly_interval_seconds": WEEKLY_INTERVAL_SECONDS,
            "recent_runs": [dict(x) for x in recent],
            "database_safety": {
                "advisory_lock": True,
                "destructive_sql": False,
                "automatic_master_promotion": False,
                "credentials_logged": False,
                "parameterized_writes": True,
            },
        }

    @router.post("/api/commercial-intelligence/automation-run-now")
    def automation_run_now(req: Request, kind: str = "DAILY"):
        # Existing Alliance authentication remains authoritative.
        try:
            core.need_login(req)
        except Exception as exc:
            raise HTTPException(401, "Login required") from exc
        kind = str(kind or "DAILY").upper()
        if kind not in {"DAILY", "WEEKLY"}:
            raise HTTPException(400, "kind must be DAILY or WEEKLY")
        return run_cycle(core, run_kind=kind, trigger_type="MANUAL", actor=_safe_actor(core, req))

    app = core.app
    existing = {getattr(r, "path", None) for r in app.router.routes}
    if "/api/commercial-intelligence/automation-status" not in existing:
        app.include_router(router)

    if ENABLED and (_THREAD is None or not _THREAD.is_alive()):
        _STOP_EVENT.clear()
        _THREAD = threading.Thread(
            target=_scheduler_loop,
            args=(core,),
            name="alliance-commercial-intelligence-scheduler",
            daemon=True,
        )
        _THREAD.start()

    _RUNTIME["thread_alive"] = bool(_THREAD and _THREAD.is_alive())
    return {
        "status": "REGISTERED",
        "version": VERSION,
        "enabled": ENABLED,
        "thread_alive": _RUNTIME["thread_alive"],
        "route": "/api/commercial-intelligence/automation-status",
        "database_safety": "ADDITIVE_LOCKED_PARAMETERIZED",
    }
