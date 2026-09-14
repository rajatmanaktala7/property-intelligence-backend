from __future__ import annotations

import threading
from datetime import datetime, timezone
from fastapi import Request
from fastapi.responses import HTMLResponse

VERSION="2.0.0-ALLIANCE-CONTINUOUS-DOCTOR"
INTERVAL_SECONDS=300
MARKER="ALLIANCE_AI_DOCTOR_V2"

STATE={
    "status":"STARTING",
    "audit_runs":0,
    "last_audit_at":None,
    "missing":[],
    "wrong_method":[],
    "route_count":0,
    "worker_started":False,
}

CRITICAL={
    "/alliance/primary":"GET",
    "/alliance/final/database/master":"GET",
    "/alliance/final/requirements":"GET",
    "/alliance/master-requirement-matcher":"GET",
    "/alliance/primary/matcher":"GET",
    "/deal-match-ai-v60":"GET",
    "/commercial-intelligence":"GET",
    "/commercial-intelligence/research/{asset_code}":"POST",
    "/commercial-intelligence/research-all":"POST",
    "/alliance/newspaper-capture":"GET",
    "/newspaper-v83":"GET",
    "/api/newspaper-v83/process":"POST",
    "/api/newspaper-v83/health":"GET",
    "/whatsapp-live":"GET",
    "/alliance/primary/data-health":"GET",
    "/alliance/system-doctor":"GET",
}

def _utc():
    return datetime.now(timezone.utc).isoformat()

def _route(app,path,method=None):
    for r in list(getattr(getattr(app,"router",None),"routes",[]) or []):
        if getattr(r,"path",None)!=path:
            continue
        methods=set(getattr(r,"methods",set()) or set())
        if method is None or method.upper() in methods:
            return r
    return None

def audit(app):
    missing=[]
    wrong=[]
    for path,method in CRITICAL.items():
        if _route(app,path,method) is None:
            if _route(app,path,None) is None:
                missing.append(path)
            else:
                wrong.append({"path":path,"required_method":method})

    STATE["audit_runs"]+=1
    STATE["last_audit_at"]=_utc()
    STATE["missing"]=missing
    STATE["wrong_method"]=wrong
    STATE["route_count"]=len(list(getattr(app.router,"routes",[]) or []))
    STATE["status"]="PASS" if not missing and not wrong else "DEGRADED"
    return dict(STATE)

def _loop(app):
    while True:
        try:
            audit(app)
        except Exception as exc:
            STATE["status"]="ERROR"
            STATE["last_error"]=f"{type(exc).__name__}: {exc}"
        threading.Event().wait(INTERVAL_SECONDS)

def _canonical_login(core,req):
    try:
        import app as canonical_app
        fn=getattr(canonical_app,"need_login",None)
        if callable(fn):
            return fn(req)
    except Exception:
        pass
    return core.need_login(req)

NEWSPAPER_PAGE=r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alliance Newspaper Capture</title>
<style>
body{font-family:Arial;margin:0;background:#f4f7fb;color:#172033}
.wrap{max-width:980px;margin:auto;padding:22px}
.card{background:#fff;border:1px solid #dbe2ea;border-radius:14px;padding:18px;margin:12px 0}
button,.btn{display:inline-block;padding:10px 14px;border:0;border-radius:9px;background:#10223f;color:#fff;text-decoration:none;cursor:pointer}
input[type=file]{display:block;margin:12px 0;padding:12px;border:1px dashed #94a3b8;width:100%;box-sizing:border-box}
#result{white-space:pre-wrap;background:#f8fafc;padding:12px;border-radius:9px}
.ok{color:#08783e}.bad{color:#b42318}
</style></head>
<body><div class="wrap">
<div class="card"><a class="btn" href="/alliance/primary">Dashboard</a> <a class="btn" href="/newspaper-v83">Newspaper Database</a></div>
<div class="card">
<h2>Newspaper Capture</h2>
<p>Select an image or PDF. This workspace sends it directly to the existing V8.3 processor and shows the real server response.</p>
<form id="newspaperForm" enctype="multipart/form-data">
<input type="file" name="file" accept="image/*,.pdf" required>
<button id="uploadButton" type="submit">Upload & Process</button>
</form>
<pre id="result">Ready.</pre>
</div></div>
<script id="ALLIANCE_NEWSPAPER_DOCTOR_V2">
document.getElementById('newspaperForm').addEventListener('submit',async function(e){
 e.preventDefault();
 const button=document.getElementById('uploadButton');
 const out=document.getElementById('result');
 button.disabled=true;button.textContent='Uploading?';out.className='';out.textContent='Uploading and processing?';
 try{
   const response=await fetch('/api/newspaper-v83/process',{
     method:'POST',
     body:new FormData(this),
     credentials:'same-origin'
   });
   const text=await response.text();
   out.textContent='HTTP '+response.status+'\n'+text;
   out.className=response.ok?'ok':'bad';
   if(response.ok)setTimeout(()=>location.href='/newspaper-v83',1200);
 }catch(err){
   out.className='bad';
   out.textContent='Upload error: '+err;
 }finally{
   button.disabled=false;
   button.textContent='Upload & Process';
 }
});
</script></body></html>"""

def register(core,served_app=None):
    app=served_app or getattr(core,"app",None) or core

    # Processor MUST already exist. Doctor never replaces the processing backend.
    if _route(app,"/api/newspaper-v83/process","POST") is None:
        raise RuntimeError("Newspaper V8.3 POST processor is not registered")
    if _route(app,"/api/newspaper-v83/health","GET") is None:
        raise RuntimeError("Newspaper V8.3 health route is not registered")

    # One canonical newspaper capture GET owner.
    app.router.routes[:]=[
        r for r in list(app.router.routes)
        if not(
            getattr(r,"path",None)=="/alliance/newspaper-capture"
            and "GET" in set(getattr(r,"methods",set()) or set())
        )
    ]

    @app.get("/alliance/newspaper-capture",response_class=HTMLResponse,include_in_schema=False)
    def newspaper_capture(req:Request):
        _canonical_login(core,req)
        return HTMLResponse(
            NEWSPAPER_PAGE,
            headers={"Cache-Control":"no-store","X-Alliance-Newspaper-Doctor":VERSION},
        )

    # Make the canonical capture route deterministic.
    capture=_route(app,"/alliance/newspaper-capture","GET")
    if capture in app.router.routes:
        app.router.routes.remove(capture)
        app.router.routes.insert(0,capture)

    if _route(app,"/api/alliance/doctor-v2/status","GET") is None:
        @app.get("/api/alliance/doctor-v2/status",include_in_schema=False)
        def doctor_status():
            return audit(app)

    result=audit(app)

    if not STATE["worker_started"]:
        STATE["worker_started"]=True
        threading.Thread(
            target=_loop,
            args=(app,),
            daemon=True,
            name="alliance-ai-doctor-v2",
        ).start()

    return {
        "status":result["status"],
        "version":VERSION,
        "interval_seconds":INTERVAL_SECONDS,
        "business_data_mutations":0,
        "matcher_routes_mutated":0,
        "requirement_routes_re_registered":0,
        "newspaper_processor_replaced":False,
    }
