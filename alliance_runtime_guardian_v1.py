from __future__ import annotations
import json, re, threading, time
from datetime import datetime, timezone
from pathlib import Path
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

VERSION = "1.0.0-RUNTIME-GUARDIAN"
INTERVAL_SECONDS = 300

CRITICAL = {
    "/alliance/primary": "dashboard",
    "/alliance/final/databases": "master_properties",
    "/alliance/primary/availability": "availability",
    "/property-manual": "add_property",
    "/alliance/final/requirements": "master_requirements",
    "/requirements-workbench": "add_requirement",
    "/alliance/final/requirements/manual": "manual_requirements",
    "/alliance/final/requirements/whatsapp": "whatsapp_requirements",
    "/alliance/primary/matcher": "smart_matcher",
    "/alliance/primary/deal-desk": "deal_desk",
    "/whatsapp-live": "whatsapp_live",
    "/capture-intelligence": "newspaper_capture",
    "/commercial-intelligence": "commercial_intelligence",
    "/hospitality-intelligence": "hospitality_intelligence",
    "/retail-expansion": "retail_intelligence",
    "/alliance/primary/contact-master": "contact_master",
    "/alliance/primary/data-health": "data_health",
    "/alliance/system-doctor": "system_doctor",
}

SAFE_ALIASES = {
    "/requirements-workbench": "/requirement-manual",
    "/alliance/primary/master-properties": "/alliance/final/databases",
    "/alliance/primary/master-requirements": "/alliance/final/requirements",
}

REQUIRED_CONTRACT_TOKENS = {
    "alliance_automated_deal_desk_v1.py": ["MASTER_ONLY", "automatic_send", "EVIDENCE_ONLY_NO_GUESSING"],
    "alliance_master_consolidation_v1.py": ["pi_requirement_gate_v1191", "pi_master_properties_v711", "marketing_eligible BOOLEAN NOT NULL DEFAULT FALSE"],
}

STATE = {
    "status": "INIT",
    "version": VERSION,
    "freeze": "ACTIVE",
    "feature_additions": "FROZEN",
    "last_audit_at": None,
    "last_pass_at": None,
    "audit_count": 0,
    "critical_total": len(CRITICAL),
    "critical_missing": [],
    "contract_failures": [],
    "auto_repairs": [],
    "unresolved": [],
    "worker_started": False,
    "interval_seconds": INTERVAL_SECONDS,
}

LOCK = threading.RLock()

def _app(core):
    return getattr(core, "app", core)

def _paths(app):
    return {getattr(r, "path", None) for r in app.router.routes if getattr(r, "path", None)}

def _route_matches(app, path):
    for r in app.router.routes:
        if getattr(r, "path", None) == path:
            return True
        try:
            if r.path_regex.fullmatch(path):
                return True
        except Exception:
            pass
    return False

def _install_alias(app, src, dst):
    if _route_matches(app, src):
        return False
    if not _route_matches(app, dst):
        return False
    async def _alias(req: Request, _dst=dst):
        return RedirectResponse(_dst, status_code=307)
    app.add_api_route(src, _alias, methods=["GET"], include_in_schema=False)
    return True

def _check_contract_files():
    failures = []
    root = Path.cwd()
    for name, tokens in REQUIRED_CONTRACT_TOKENS.items():
        p = root / name
        if not p.exists():
            failures.append(f"{name}:MISSING")
            continue
        s = p.read_text(encoding="utf-8", errors="ignore")
        for token in tokens:
            if token not in s:
                failures.append(f"{name}:MISSING_TOKEN:{token}")
    freeze = root / "ALLIANCE_RUNTIME_FREEZE_V1.json"
    if not freeze.exists():
        failures.append("ALLIANCE_RUNTIME_FREEZE_V1.json:MISSING")
    else:
        try:
            d = json.loads(freeze.read_text(encoding="utf-8"))
            if d.get("policy", {}).get("matcher_authority") != "MASTER_ONLY":
                failures.append("FREEZE:MATCHER_AUTHORITY")
            if d.get("policy", {}).get("destructive_master_mutations") is not False:
                failures.append("FREEZE:DESTRUCTIVE_MUTATION_POLICY")
        except Exception as exc:
            failures.append(f"FREEZE:INVALID:{type(exc).__name__}")
    return failures

def audit_and_repair(app):
    repairs = []
    unresolved = []

    for src, dst in SAFE_ALIASES.items():
        if not _route_matches(app, src):
            if _install_alias(app, src, dst):
                repairs.append({"type": "SAFE_ROUTE_ALIAS", "from": src, "to": dst})
            else:
                unresolved.append({"type": "ALIAS_DESTINATION_MISSING", "path": src, "destination": dst})

    missing = []
    for p, label in CRITICAL.items():
        if not _route_matches(app, p):
            missing.append({"path": p, "label": label})

    contract_failures = _check_contract_files()
    unresolved.extend({"type": "CRITICAL_ROUTE_MISSING", **x} for x in missing)

    now = datetime.now(timezone.utc).isoformat()
    status = "PASS" if not unresolved and not contract_failures else "DEGRADED"

    with LOCK:
        STATE["status"] = status
        STATE["last_audit_at"] = now
        STATE["audit_count"] = int(STATE.get("audit_count") or 0) + 1
        STATE["critical_missing"] = missing
        STATE["contract_failures"] = contract_failures
        STATE["auto_repairs"] = repairs
        STATE["unresolved"] = unresolved
        if status == "PASS":
            STATE["last_pass_at"] = now
    return dict(STATE)

def _worker(app):
    while True:
        try:
            audit_and_repair(app)
        except Exception as exc:
            with LOCK:
                STATE["status"] = "ERROR"
                STATE["last_worker_error"] = f"{type(exc).__name__}: {exc}"
        time.sleep(INTERVAL_SECONDS)

def register(core):
    app = _app(core)
    if app is None:
        raise RuntimeError("APP_UNAVAILABLE")

    paths = _paths(app)

    if "/api/alliance/runtime-guardian-v1/status" not in paths:
        @app.get("/api/alliance/runtime-guardian-v1/status")
        def guardian_status():
            with LOCK:
                return dict(STATE)

    if "/api/alliance/runtime-guardian-v1/audit" not in paths:
        @app.post("/api/alliance/runtime-guardian-v1/audit")
        def guardian_audit(req: Request):
            try:
                core.need_login(req)
            except Exception:
                return JSONResponse({"detail": "Login required"}, status_code=401)
            return audit_and_repair(app)

    audit_and_repair(app)

    if not STATE.get("worker_started"):
        t = threading.Thread(target=_worker, args=(app,), daemon=True, name="alliance-runtime-guardian-v1")
        t.start()
        with LOCK:
            STATE["worker_started"] = True

    return dict(STATE)
