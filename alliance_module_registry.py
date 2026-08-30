from __future__ import annotations
import importlib
from datetime import datetime, timezone

MODULE_VERSION="3.8.1-PHASE6-VERIFICATION-REGISTRY"

MODULES = [
    ("newspaper_v83", "newspaper_upload_v83"),
    ("source_aware_matcher", "alliance_v38_source_aware_matcher"),
    ("clean_entity_v382b", "alliance_v382b_clean_entity_databases"),
    ("phase6_verification", "alliance_phase6_verification_workflow"),
]

STATE = {}

def register_optional(core, key, module_name):
    try:
        mod=importlib.import_module(module_name)
        result=mod.register(core)
        STATE[key]={
            "status":"HEALTHY",
            "module":module_name,
            "result":result,
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
