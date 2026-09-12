from __future__ import annotations
import asyncio, html, re
from urllib.parse import urlparse
import httpx
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

VERSION="2.2.0-FULL-CRAWL-COVERAGE"
BATCH_SIZE=4
PAUSE_SECONDS=0.15
MAX_PAGES=2000
SEEDS=["/alliance/primary","/alliance/primary/properties","/alliance/primary/requirements","/alliance/primary/availability","/alliance/primary/matcher","/alliance/primary/followups","/property-manual","/requirement-manual","/alliance/primary/databases","/alliance/primary/requirements-hub","/alliance/final/databases","/alliance/final/requirements","/deal-match-ai-v60","/whatsapp-live","/capture-intelligence","/property-discovery","/commercial-intelligence","/hospitality-intelligence","/retail-expansion","/requirement-discovery","/marketing-contacts","/alliance/primary/ai-control","/alliance/primary/data-health","/alliance/system-doctor"]
STATE={"running":False,"queue":[],"results":{},"discovered":set(),"error":None,"generation":0}

def _app(core): return getattr(core,"app",None) or core
def _login(core,req):
    fn=getattr(core,"need_login",None)
    if fn: fn(req)
def _eligible(raw):
    raw=html.unescape(str(raw or "")).strip()
    if not raw or raw.startswith(("#","javascript:","mailto:","tel:")): return None
    if raw.startswith(("http://","https://")):
        u=urlparse(raw)
        if u.hostname not in ("audit.local","app.allianceinfrastructure.co.in"): return None
        raw=u.path+(("?"+u.query) if u.query else "")
    if not raw.startswith("/"): return None
    p=urlparse(raw).path
    if p.startswith(("/api/","/logout","/static/","/media/")): return None
    if p in ("/alliance/deep-audit","/alliance/deep-audit/start"): return None
    if "{" in p: return None
    return raw
def _links(body):
    vals=re.findall(r'href\s*=\s*["\']([^"\']+)["\']',body,re.I); out=[]
    for v in vals:
        x=_eligible(v)
        if x and x not in out: out.append(x)
    return out
def _snap():
    vals=list(STATE["results"].values()); red=[x for x in vals if not x["ok"]]
    tested=len(vals); discovered=len(STATE["discovered"]); remaining=len(STATE["queue"])
    capped = tested >= MAX_PAGES and (remaining > 0 or discovered > tested)
    return {"version":VERSION,"running":STATE["running"],"tested":tested,"green":tested-len(red),"red":len(red),"remaining":remaining,"discovered":discovered,"max_pages":MAX_PAGES,"capped":capped,"failures":red,"results":vals,"error":STATE["error"]}
async def _worker(core,cookies,g):
    transport=httpx.ASGITransport(app=_app(core),raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(transport=transport,base_url="http://audit.local",follow_redirects=True,cookies=cookies,timeout=8.0) as client:
            while STATE["running"] and STATE["generation"]==g:
                batch=[]
                while STATE["queue"] and len(batch)<BATCH_SIZE:
                    p=STATE["queue"].pop(0)
                    if p not in STATE["results"]: batch.append(p)
                if not batch: break
                for path in batch:
                    item={"path":path,"ok":False,"status":None,"reason":""}
                    try:
                        r=await client.get(path); body=r.text[:120000]; low=body.lower()
                        item["status"]=r.status_code
                        reason=f"HTTP_{r.status_code}" if r.status_code>=400 else ("AUTH_REDIRECT" if r.url.path=="/login" else "")
                        if not reason:
                            for marker in ("alliance is busy processing earlier requests","internal server error","traceback (most recent call last)","application error","upstream error","database error","operationalerror"):
                                if marker in low: reason="ERROR_CONTENT:"+marker; break
                        item["reason"]=reason or "OK"; item["ok"]=not bool(reason)
                        for x in _links(body):
                            STATE["discovered"].add(x)
                            if x not in STATE["results"] and x not in STATE["queue"] and len(STATE["results"])+len(STATE["queue"])<MAX_PAGES:
                                STATE["queue"].append(x)
                    except Exception as exc: item["reason"]=f"{type(exc).__name__}: {exc}"
                    STATE["results"][path]=item
                    await asyncio.sleep(PAUSE_SECONDS)
    except Exception as exc: STATE["error"]=f"{type(exc).__name__}: {exc}"
    finally:
        if STATE["generation"]==g: STATE["running"]=False
def _render(s):
    rows="".join(f"<tr><td>{'🟢' if x['ok'] else '🔴'}</td><td><code>{html.escape(x['path'])}</code></td><td>{x['status'] or ''}</td><td>{html.escape(x['reason'])}</td></tr>" for x in s["results"])
    failures="<br>".join(f"🔴 <code>{html.escape(x['path'])}</code> — {html.escape(x['reason'])}" for x in s["failures"]) or "None"
    refresh="<meta http-equiv='refresh' content='3'>" if s["running"] else ""
    return f"""<!doctype html><html><head><meta charset=utf-8>{refresh}<meta name=viewport content='width=device-width,initial-scale=1'><title>Alliance Deep Audit V2.1</title><style>body{{font-family:Arial;background:#f4f7fb;margin:0}}main{{padding:18px}}.c{{background:white;border:1px solid #ddd;border-radius:10px;padding:12px;margin:10px 0}}table{{border-collapse:collapse;width:100%;background:white;font-size:11px}}th,td{{border:1px solid #aaa;padding:6px;text-align:left}}th{{background:#e9eef5}}a.btn{{display:inline-block;padding:10px 16px;background:#222;color:#fff;text-decoration:none;border-radius:7px;font-weight:700}}</style></head><body><main><h2>Alliance Deep Runtime Audit V2.2</h2><div class=c><b>{'RUNNING' if s['running'] else 'IDLE / COMPLETE'}</b> · Tested {s['tested']} · 🟢 {s['green']} · 🔴 {s['red']} · Queue {s['remaining']} · Discovered {s['discovered']} · Cap {s['max_pages']} · {'⚠️ CAPPED' if s['capped'] else 'FULL CRAWL'}</div><a class=btn href='/alliance/deep-audit/start'>Start / Restart Audit</a><h3>Failures</h3><div class=c>{failures}</div><h3>All Results</h3><table><tr><th></th><th>Page</th><th>HTTP</th><th>Result</th></tr>{rows}</table><p><small>Read-only GET audit. No business POST/PUT/PATCH/DELETE action is executed.</small></p></main></body></html>"""
def register(core):
    app=_app(core); owned={"/alliance/deep-audit","/alliance/deep-audit/start","/api/alliance/deep-audit"}
    app.router.routes[:]=[r for r in app.router.routes if getattr(r,"path",None) not in owned]
    @app.get("/api/alliance/deep-audit")
    async def api(req:Request): _login(core,req); return JSONResponse(_snap())
    @app.get("/alliance/deep-audit",response_class=HTMLResponse)
    async def page(req:Request): _login(core,req); return HTMLResponse(_render(_snap()))
    @app.get("/alliance/deep-audit/start")
    async def start(req:Request):
        _login(core,req)
        if not STATE["running"]:
            STATE["generation"]+=1; STATE["running"]=True; STATE["queue"]=list(SEEDS); STATE["results"]={}; STATE["discovered"]=set(); STATE["error"]=None
            asyncio.create_task(_worker(core,dict(req.cookies),STATE["generation"]))
        return RedirectResponse("/alliance/deep-audit",status_code=303)
    methods={(getattr(r,"path",None),m) for r in app.router.routes for m in (getattr(r,"methods",set()) or set())}
    required={("/alliance/deep-audit","GET"),("/alliance/deep-audit/start","GET"),("/api/alliance/deep-audit","GET")}
    missing=sorted(required-methods)
    if missing: raise RuntimeError("Deep audit route contract missing: "+repr(missing))
    return {"status":"REGISTERED","version":VERSION,"route_contract":"PASS"}
