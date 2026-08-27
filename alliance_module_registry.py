
from __future__ import annotations
import importlib
from datetime import datetime, timezone

MODULE_VERSION="3.8-STABLE-MODULE-REGISTRY"

MODULES = [
    ("newspaper_v83", "newspaper_upload_v83"),
    ("source_aware_matcher", "alliance_v38_source_aware_matcher"),
    ("clean_entity_v382b", "alliance_v382b_clean_entity_databases"),
]

STATE = {}

def register_optional(core, key, module_name):
    try:
        mod=importlib.import_module(module_name)
        mod.register(core)
        STATE[key]={
            "status":"HEALTHY",
            "module":module_name,
            "error":None,
            "registered_at":datetime.now(timezone.utc).isoformat(),
        }
        print(f"[module-registry] {key}: HEALTHY")
    except Exception as exc:
        STATE[key]={
            "status":"DEGRADED",
            "module":module_name,
            "error":f"{type(exc).__name__}: {exc}",
            "registered_at":datetime.now(timezone.utc).isoformat(),
        }
        print(f"[module-registry] {key}: DEGRADED - {type(exc).__name__}: {exc}")

def register_all(core):
    for key,module_name in MODULES:
        register_optional(core,key,module_name)
    return STATE
