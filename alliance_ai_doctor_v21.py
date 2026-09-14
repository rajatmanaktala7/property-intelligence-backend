from __future__ import annotations
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone

VERSION="2.4.2-ALLIANCE-EXTERNAL-TRUTH-DOCTOR"
MARKER="ALLIANCE_AI_DOCTOR_V21"
INTERVAL_SECONDS=300
BASE="https://app.allianceinfrastructure.co.in"
STATE={"status":"STARTING","audit_runs":0,"last_audit_at":None,"route_count":0,"missing":[],"wrong_method":[],"duplicates":[],"visibility_gaps":[],"external_truth":[],"worker_started":False}

CRITICAL={
    "/alliance/primary":"GET",
    "/alliance/final/requirements":"GET",
    "/alliance/final/requirements/{source}":"GET",
    "/alliance/master-requirement-matcher":"GET",
    "/alliance/primary/matcher":"GET",
    "/deal-match-ai-v60":"GET",
    "/commercial-intelligence":"GET",
    "/commercial-intelligence/research/{asset_code}":"POST",
    "/commercial-intelligence/research-all":"POST",
    "/newspaper-v83":"GET",
    "/api/newspaper-v83/health":"GET",
    "/api/newspaper-v83/process":"POST",
    "/whatsapp-live":"GET",
}
HIDDEN={
    "/alliance/final/requirements":"GET",
    "/alliance/final/requirements/{source}":"GET",
}
DUPLICATE_SENSITIVE={("/alliance/final/requirements","GET"),("/commercial-intelligence","GET")}
EXISTS_HTTP={200,302,303,307,308,401,403}

def _utc(): return datetime.now(timezone.utc).isoformat()

def _routes(app):
    apps=app if isinstance(app,(tuple,list)) else (app,)
    out=[]; seen=set()
    for candidate in apps:
        if candidate is None: continue
        for route in list(getattr(getattr(candidate,"router",None),"routes",[]) or []):
            key=(id(route),getattr(route,"path",None),tuple(sorted(getattr(route,"methods",set()) or set())))
            if key in seen: continue
            seen.add(key); out.append(route)
    return out

def _matches(app,path,method=None):
    found=[]
    for route in _routes(app):
        if getattr(route,"path",None)!=path: continue
        methods=set(getattr(route,"methods",set()) or set())
        if method is None or method.upper() in methods: found.append(route)
    return found

def _openapi_paths():
    req=urllib.request.Request(BASE+"/openapi.json",headers={"User-Agent":"Alliance-Doctor-V242/1.0"})
    with urllib.request.urlopen(req,timeout=20) as r:
        return (json.loads(r.read().decode("utf-8")).get("paths") or {})

def _safe_get_exists(path):
    real=path.replace("{source}","whatsapp")+"?limit=1" if "{source}" in path else path
    req=urllib.request.Request(BASE+real,method="GET",headers={"User-Agent":"Alliance-Doctor-V242/1.0"})
    try:
        with urllib.request.urlopen(req,timeout=20) as r:
            return int(r.status) in EXISTS_HTTP, int(r.status)
    except urllib.error.HTTPError as e:
        return int(e.code) in EXISTS_HTTP, int(e.code)

def audit(app):
    missing=[]; wrong=[]; duplicates=[]; visibility=[]; external=[]
    schema=None; schema_error=None
    for path,method in CRITICAL.items():
        if _matches(app,path,method):
            continue
        if _matches(app,path,None):
            wrong.append({"path":path,"required_method":method})
            continue

        # Hidden Requirement routes intentionally do not appear in OpenAPI.
        if path in HIDDEN:
            try:
                ok,status=_safe_get_exists(path)
                external.append({"path":path,"method":method,"source":"HTTP","status":status,"exists":ok})
                if ok:
                    visibility.append({"path":path,"method":method,"reason":"not_visible_in_local_router_but_live_http_exists"})
                    continue
            except Exception as exc:
                external.append({"path":path,"method":method,"source":"HTTP","error":f"{type(exc).__name__}: {exc}"})
            missing.append(path)
            continue

        # Schema-visible routes: use live OpenAPI as the external routing truth.
        if schema is None and schema_error is None:
            try: schema=_openapi_paths()
            except Exception as exc: schema_error=f"{type(exc).__name__}: {exc}"
        methods=(schema or {}).get(path) or {}
        if method.lower() in methods:
            external.append({"path":path,"method":method,"source":"OPENAPI","exists":True})
            visibility.append({"path":path,"method":method,"reason":"not_visible_in_local_router_but_live_openapi_exists"})
            continue
        if schema_error:
            external.append({"path":path,"method":method,"source":"OPENAPI","error":schema_error})
        missing.append(path)

    for path,method in DUPLICATE_SENSITIVE:
        hits=_matches(app,path,method)
        if len(hits)>1:
            duplicates.append({"path":path,"method":method,"count":len(hits)})

    STATE["audit_runs"]+=1
    STATE["last_audit_at"]=_utc()
    STATE["route_count"]=len(_routes(app))
    STATE["missing"]=missing
    STATE["wrong_method"]=wrong
    STATE["duplicates"]=duplicates
    STATE["visibility_gaps"]=visibility
    STATE["external_truth"]=external
    STATE["status"]="PASS" if not missing and not wrong else "DEGRADED"
    return dict(STATE)

def _worker(app):
    event=threading.Event()
    while True:
        try: audit(app)
        except Exception as exc:
            STATE["status"]="ERROR"; STATE["last_error"]=f"{type(exc).__name__}: {exc}"
        event.wait(INTERVAL_SECONDS)

def register(core,served_app=None):
    served=served_app or getattr(core,"app",None) or core
    core_app=getattr(core,"app",None)
    target=(served,core_app) if core_app is not None and core_app is not served else served
    if not _matches(served,"/api/alliance/doctor-v21/status","GET"):
        @served.get("/api/alliance/doctor-v21/status",include_in_schema=False)
        def doctor_status(): return audit(target)
    result=audit(target)
    if not STATE["worker_started"]:
        STATE["worker_started"]=True
        threading.Thread(target=_worker,args=(target,),daemon=True,name="alliance-ai-doctor-v242").start()
    return {"status":result["status"],"version":VERSION,"interval_seconds":INTERVAL_SECONDS,"route_authority":"LOCAL_PLUS_LIVE_EXTERNAL_TRUTH","business_data_mutations":0,"matcher_routes_mutated":0,"requirement_routes_re_registered":0,"commercial_routes_re_registered":0,"newspaper_routes_re_registered":0}
