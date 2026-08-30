from __future__ import annotations

import importlib
from datetime import datetime, timezone
from typing import Any

MODULE_VERSION = "3.8.2-PHASE6-JSON-SAFE-REGISTRY"

MODULES = [
    ("newspaper_v83", "newspaper_upload_v83"),
    ("source_aware_matcher", "alliance_v38_source_aware_matcher"),
    ("clean_entity_v382b", "alliance_v382b_clean_entity_databases"),
    ("phase6_verification", "alliance_phase6_verification_workflow"),
]

STATE = {}


def _json_safe_result(result: Any):
    """Return a small JSON-safe registration summary.

    Older optional modules return FastAPI APIRouter objects. Keeping those raw
    objects inside STATE makes /module-health recursively serialize FastAPI's
    route tree. Preserve useful registration evidence without storing the
    router object itself.
    """
    if result is None or isinstance(result, (str, int, float, bool)):
        return result
    if isinstance(result, dict):
        safe = {}
        for key, value in result.items():
            if value is None or isinstance(value, (str, int, float, bool)):
                safe[str(key)] = value
            elif isinstance(value, (list, tuple, set)):
                safe[str(key)] = [
                    x if x is None or isinstance(x, (str, int, float, bool)) else str(x)
                    for x in value
                ]
            else:
                safe[str(key)] = str(value)
        return safe

    routes = getattr(result, "routes", None)
    if routes is not None:
        route_paths = []
        for route in routes:
            path = getattr(route, "path", None)
            if path:
                route_paths.append(str(path))
        return {
            "type": type(result).__name__,
            "route_count": len(route_paths),
            "routes": route_paths,
        }

    return {"type": type(result).__name__, "repr": repr(result)[:500]}


def register_optional(core, key, module_name):
    try:
        mod = importlib.import_module(module_name)
        result = mod.register(core)
        STATE[key] = {
            "status": "HEALTHY",
            "module": module_name,
            "result": _json_safe_result(result),
            "error": None,
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }
        print(f"[module-registry] {key}: HEALTHY")
    except Exception as exc:
        STATE[key] = {
            "status": "DEGRADED",
            "module": module_name,
            "result": None,
            "error": f"{type(exc).__name__}: {exc}",
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }
        print(f"[module-registry] {key}: DEGRADED - {type(exc).__name__}: {exc}")


def register_all(core):
    for key, module_name in MODULES:
        register_optional(core, key, module_name)
    return STATE
