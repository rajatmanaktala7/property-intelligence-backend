"""Fail-safe optional registration for Property Brain + Enrichment + Property Data Quality."""
VERSION = "1.1.0-OPTIONAL-PROPERTY-MODULES"

def _route_exists(app, path):
    try:
        return any(getattr(r, "path", None) == path for r in app.router.routes)
    except Exception:
        return False

def register(wrapped):
    core=wrapped.core; app=wrapped.app
    result={
        "version":VERSION,
        "property_brain":{"status":"NOT_RUN","error":None},
        "property_enrichment":{"status":"NOT_RUN","error":None},
        "property_data_quality":{"status":"NOT_RUN","error":None},
        "startup_ddl":False,
        "fail_safe":True
    }
    try:
        if _route_exists(app,"/property-brain/status"):
            result["property_brain"]["status"]="ALREADY_REGISTERED"
        else:
            import alliance_property_brain_v1 as pb
            pb.register(core)
            result["property_brain"]["status"]="REGISTERED" if _route_exists(app,"/property-brain/status") else "NO_ROUTE"
    except Exception as exc:
        result["property_brain"]={"status":"ERROR","error":f"{type(exc).__name__}: {exc}"}

    try:
        if _route_exists(app,"/property-brain/enrichment/batch/{limit}"):
            result["property_enrichment"]["status"]="ALREADY_REGISTERED"
        else:
            import alliance_property_enrichment_v1 as pe
            pe.register(core)
            result["property_enrichment"]["status"]="REGISTERED" if _route_exists(app,"/property-brain/enrichment/batch/{limit}") else "NO_ROUTE"
    except Exception as exc:
        result["property_enrichment"]={"status":"ERROR","error":f"{type(exc).__name__}: {exc}"}

    try:
        import alliance_v3_property_data_quality as dq
        dq_result=dq.register(core)
        result["property_data_quality"]={
            "status":dq_result.get("status","REGISTERED"),
            "version":dq_result.get("version"),
            "error":None
        }
        result["startup_ddl"]=True
    except Exception as exc:
        result["property_data_quality"]={"status":"ERROR","error":f"{type(exc).__name__}: {exc}"}

    return result
