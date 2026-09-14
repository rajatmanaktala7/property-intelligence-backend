from __future__ import annotations
import threading
from datetime import datetime, timezone

VERSION="2.1.0-ALLIANCE-CONTINUOUS-DOCTOR"
MARKER="ALLIANCE_AI_DOCTOR_V21"
INTERVAL_SECONDS=300
STATE={"status":"STARTING","audit_runs":0,"last_audit_at":None,"route_count":0,"missing":[],"wrong_method":[],"duplicates":[],"worker_started":False}

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
DUPLICATE_SENSITIVE={("/alliance/final/requirements","GET"),("/commercial-intelligence","GET")}

def _utc(): return datetime.now(timezone.utc).isoformat()
def _routes(app): return list(getattr(getattr(app,"router",None),"routes",[]) or [])
def _matches(app,path,method=None):
    found=[]
    for route in _routes(app):
        if getattr(route,"path",None)!=path: continue
        methods=set(getattr(route,"methods",set()) or set())
        if method is None or method.upper() in methods: found.append(route)
    return found

def audit(app):
    missing=[]; wrong=[]; duplicates=[]
    for path,method in CRITICAL.items():
        if _matches(app,path,method): continue
        if not _matches(app,path,None): missing.append(path)
        else: wrong.append({"path":path,"required_method":method})
    for path,method in DUPLICATE_SENSITIVE:
        hits=_matches(app,path,method)
        if len(hits)>1: duplicates.append({"path":path,"method":method,"count":len(hits)})
    STATE["audit_runs"]+=1
    STATE["last_audit_at"]=_utc()
    STATE["route_count"]=len(_routes(app))
    STATE["missing"]=missing
    STATE["wrong_method"]=wrong
    STATE["duplicates"]=duplicates
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
    app=served_app or getattr(core,"app",None) or core
    if not _matches(app,"/api/alliance/doctor-v21/status","GET"):
        @app.get("/api/alliance/doctor-v21/status",include_in_schema=False)
        def doctor_status(): return audit(app)
    result=audit(app)
    if not STATE["worker_started"]:
        STATE["worker_started"]=True
        threading.Thread(target=_worker,args=(app,),daemon=True,name="alliance-ai-doctor-v21").start()
    return {"status":result["status"],"version":VERSION,"interval_seconds":INTERVAL_SECONDS,"business_data_mutations":0,"matcher_routes_mutated":0,"requirement_routes_re_registered":0,"commercial_routes_re_registered":0,"newspaper_routes_re_registered":0}
