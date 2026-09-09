from __future__ import annotations

import html
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import Request
from fastapi.responses import HTMLResponse

VERSION = "1.0.0-DEEP-RUNTIME-LINK-AUDITOR"

BUSY_MARKERS = (
    "alliance is busy processing earlier requests",
    "please retry",
    "internal server error",
    "traceback (most recent call last)",
    "application error",
)

EXCLUDED_PREFIXES = (
    "/api/", "/health", "/ready", "/logout", "/static/", "/media/",
)

SEED_PATHS = [
    "/alliance/primary", "/alliance/primary/properties", "/alliance/primary/requirements",
    "/alliance/primary/availability", "/alliance/primary/matcher", "/alliance/primary/followups",
    "/property-manual", "/requirement-manual", "/alliance/primary/databases",
    "/alliance/primary/requirements-hub", "/alliance/final/databases", "/alliance/final/requirements",
    "/deal-match-ai-v60", "/whatsapp-live", "/capture-intelligence", "/property-discovery",
    "/commercial-intelligence", "/hospitality-intelligence", "/retail-expansion",
    "/requirement-discovery", "/marketing-contacts", "/alliance/primary/ai-control",
    "/alliance/primary/data-health", "/alliance/system-doctor",
]

def _app(core): return getattr(core, "app", None) or core

def _login(core, req):
    fn = getattr(core, "need_login", None)
    if fn: fn(req)

def _route_inventory(app):
    out=[]
    for r in app.router.routes:
        p=getattr(r,"path",None)
        if not p: continue
        out.append({"path":p,"methods":sorted(getattr(r,"methods",set()) or set()),"name":getattr(r,"name",None)})
    return out

def _route_matches(pattern,path):
    rx=re.sub(r"\{[^/{}]+\}",r"[^/]+",pattern)
    return re.fullmatch(rx,path) is not None

def _method_supported(routes,path,method):
    method=method.upper()
    return any(method in r["methods"] and _route_matches(r["path"],path) for r in routes)

def _page_like(path):
    if not path or path=="/": return False
    if any(path.startswith(x) for x in EXCLUDED_PREFIXES): return False
    if "{" in path or "}" in path: return False
    if path.endswith((".png",".jpg",".jpeg",".gif",".svg",".pdf",".csv",".xlsx",".zip")): return False
    return True

def _normalize_internal(raw):
    raw=html.unescape(str(raw or "")).strip()
    if not raw or raw.startswith(("#","javascript:","mailto:","tel:","data:")): return None
    if raw.startswith("http://") or raw.startswith("https://"):
        u=urlparse(raw)
        if u.hostname not in {"app.allianceinfrastructure.co.in","localhost","127.0.0.1","audit.local"}: return None
        raw=u.path + (("?"+u.query) if u.query else "")
    if not raw.startswith("/"): raw="/"+raw.lstrip("./")
    return raw

def _extract_links(body):
    hrefs=[]; forms=[]
    for m in re.finditer(r'''href\s*=\s*["']([^"']+)["']''',body,re.I):
        p=_normalize_internal(m.group(1))
        if p: hrefs.append(p)
    for m in re.finditer(r'''<form\b[^>]*action\s*=\s*["']([^"']+)["'][^>]*>''',body,re.I):
        tag=m.group(0); p=_normalize_internal(m.group(1))
        mm=re.search(r'''method\s*=\s*["']([^"']+)["']''',tag,re.I)
        method=(mm.group(1) if mm else "GET").upper()
        if p: forms.append({"path":p,"method":method})
    return sorted(set(hrefs)),forms

def _source_static_links():
    root=Path(__file__).resolve().parent
    links=set(); actions=set(); scanned=0
    for p in root.glob("*.py"):
        try: txt=p.read_text(encoding="utf-8",errors="ignore")
        except Exception: continue
        scanned+=1
        for m in re.finditer(r'''href\s*=\s*["'](/[^"'#\s]+)["']''',txt,re.I): links.add(m.group(1))
        for m in re.finditer(r'''action\s*=\s*["'](/[^"'#\s]+)["']''',txt,re.I): actions.add(m.group(1))
    return {"files_scanned":scanned,"hrefs":sorted(links),"actions":sorted(actions)}

async def deep_snapshot(core,req):
    app=_app(core); routes=_route_inventory(app)
    static_get_routes=sorted({r["path"] for r in routes if "GET" in r["methods"] and _page_like(r["path"])})
    queue=[]
    for p in SEED_PATHS+static_get_routes:
        if p not in queue: queue.append(p)
    transport=httpx.ASGITransport(app=app,raise_app_exceptions=False)
    audited={}; discovered_forms=[]; discovered_links=set(); max_pages=300
    headers={}
    if req.headers.get("x-user-name"): headers["x-user-name"]=req.headers["x-user-name"]
    async with httpx.AsyncClient(transport=transport,base_url="http://audit.local",follow_redirects=True,cookies=dict(req.cookies),headers=headers,timeout=12.0) as client:
        while queue and len(audited)<max_pages:
            path=queue.pop(0)
            if path in audited: continue
            bp=urlparse(path).path
            if not _page_like(bp): continue
            item={"path":path,"ok":False,"status":None,"final_path":None,"title":"","reason":"","links_found":0}
            try:
                resp=await client.get(path)
                item["status"]=resp.status_code; item["final_path"]=resp.url.path
                body=resp.text[:200000]; lower=body.lower()
                tm=re.search(r"<title[^>]*>(.*?)</title>",body,re.I|re.S)
                hm=re.search(r"<h[12][^>]*>(.*?)</h[12]>",body,re.I|re.S)
                title=tm.group(1) if tm else (hm.group(1) if hm else "")
                item["title"]=re.sub(r"<[^>]+>","",html.unescape(title)).strip()[:180]
                reason=""
                if resp.status_code>=400: reason=f"HTTP_{resp.status_code}"
                elif resp.url.path=="/login": reason="AUTH_REDIRECT"
                else:
                    marker=next((x for x in BUSY_MARKERS if x in lower),None)
                    if marker: reason="ERROR_CONTENT:"+marker
                hrefs,forms=_extract_links(body); item["links_found"]=len(hrefs)
                for h in hrefs:
                    discovered_links.add(h); hp=urlparse(h).path
                    if _page_like(hp) and h not in audited and h not in queue and len(queue)<max_pages*2: queue.append(h)
                for f in forms: discovered_forms.append({"source":path,**f})
                item["reason"]=reason or "OK"; item["ok"]=not bool(reason)
            except Exception as exc:
                item["reason"]=f"{type(exc).__name__}: {exc}"
            audited[path]=item
    form_checks=[]; seen=set()
    for f in discovered_forms:
        key=(f["source"],f["method"],f["path"])
        if key in seen: continue
        seen.add(key)
        form_checks.append({**f,"registered_method":_method_supported(routes,urlparse(f["path"]).path,f["method"])})
    source=_source_static_links(); source_checks=[]
    for p in source["hrefs"]:
        source_checks.append({"kind":"href","path":p,"registered":_method_supported(routes,urlparse(p).path,"GET")})
    for p in source["actions"]:
        bp=urlparse(p).path
        supported=any(_method_supported(routes,bp,m) for m in ("POST","PUT","DELETE","PATCH","GET"))
        source_checks.append({"kind":"action","path":p,"registered":supported})
    duplicate_routes={}
    for r in routes:
        key=(r["path"],tuple(r["methods"])); duplicate_routes[key]=duplicate_routes.get(key,0)+1
    duplicates=[{"path":p,"methods":list(m),"count":n} for (p,m),n in duplicate_routes.items() if n>1]
    page_failures=[x for x in audited.values() if not x["ok"]]
    broken_forms=[x for x in form_checks if not x["registered_method"]]
    broken_source=[x for x in source_checks if not x["registered"]]
    blockers=[f"PAGE:{x['path']}:{x['reason']}" for x in page_failures]
    blockers += [f"FORM:{x['method']}:{x['path']}" for x in broken_forms]
    blockers += [f"SOURCE_{x['kind'].upper()}:{x['path']}" for x in broken_source]
    return {
        "status":"PASS" if not blockers else "FAIL","version":VERSION,
        "pages_tested":len(audited),"pages_green":len(audited)-len(page_failures),"pages_red":len(page_failures),
        "rendered_links_found":len(discovered_links),"forms_checked":len(form_checks),
        "source_files_scanned":source["files_scanned"],"source_links_checked":len(source_checks),
        "duplicate_route_definitions":duplicates,"page_results":list(audited.values()),
        "form_results":form_checks,"source_link_results":source_checks,"blockers":blockers,
        "note":"GET pages are executed with the current logged-in session. Mutating POST/PUT/DELETE/PATCH forms are route/method checked but never executed."
    }

def _dot(ok): return "🟢" if ok else "🔴"

def _page_html(data):
    page_rows="".join(f"<tr><td>{_dot(x['ok'])}</td><td><code>{html.escape(x['path'])}</code></td><td>{html.escape(str(x['status'] or ''))}</td><td>{html.escape(x['title'])}</td><td>{html.escape(x['reason'])}</td><td>{x['links_found']}</td></tr>" for x in data["page_results"])
    form_rows="".join(f"<tr><td>{_dot(x['registered_method'])}</td><td>{html.escape(x['method'])}</td><td><code>{html.escape(x['path'])}</code></td><td><code>{html.escape(x['source'])}</code></td></tr>" for x in data["form_results"])
    source_bad=[x for x in data["source_link_results"] if not x["registered"]]
    source_rows="".join(f"<tr><td>{_dot(x['registered'])}</td><td>{html.escape(x['kind'])}</td><td><code>{html.escape(x['path'])}</code></td></tr>" for x in source_bad[:300]) or "<tr><td>🟢</td><td colspan='2'>No broken static source links/actions detected.</td></tr>"
    blockers="<br>".join(html.escape(x) for x in data["blockers"]) or "None"
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Alliance Deep Runtime Audit</title>
<style>body{{font-family:Arial;background:#f4f7fb;margin:0;color:#172033}}main{{padding:18px;max-width:1900px;margin:auto}}.card{{background:white;border:1px solid #dfe6ee;border-radius:12px;padding:14px;margin-bottom:12px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}}.big{{font-size:28px;font-weight:800}}table{{border-collapse:collapse;width:max-content;min-width:100%;font-size:11px}}th,td{{padding:6px 7px;border:1px solid #98a2b3;vertical-align:top;text-align:left}}th{{background:#e9eef5;position:sticky;top:0}}.box{{overflow:auto;max-height:70vh}}code{{white-space:nowrap}}.pass{{color:#087647}}.fail{{color:#b42318}}</style></head><body><main>
<h2>Alliance Deep Runtime Audit</h2><div class="card"><b>Status:</b> <span class="{'pass' if data['status']=='PASS' else 'fail'}">{_dot(data['status']=='PASS')} {data['status']}</span><br><b>Meaning:</b> live GET execution + internal-link crawl + form-method validation + source-link scan.</div>
<div class="grid"><div class="card"><div class="big">{data['pages_tested']}</div>Pages tested</div><div class="card"><div class="big">🟢 {data['pages_green']}</div>Pages green</div><div class="card"><div class="big">🔴 {data['pages_red']}</div>Pages red</div><div class="card"><div class="big">{data['rendered_links_found']}</div>Rendered links found</div><div class="card"><div class="big">{data['forms_checked']}</div>Forms checked</div><div class="card"><div class="big">{data['source_links_checked']}</div>Source links/actions checked</div></div>
<div class="card"><b>Blockers</b><br>{blockers}</div><div class="card box"><h3>Live Page Results</h3><table><tr><th></th><th>Page</th><th>HTTP</th><th>Title</th><th>Result</th><th>Links</th></tr>{page_rows}</table></div><div class="card box"><h3>Rendered Form Actions</h3><table><tr><th></th><th>Method</th><th>Action</th><th>Found On</th></tr>{form_rows}</table></div><div class="card box"><h3>Broken Static Source Links / Actions</h3><table><tr><th></th><th>Kind</th><th>Path</th></tr>{source_rows}</table></div><div class="card"><small>{html.escape(data['note'])}</small></div></main></body></html>'''

def register(core):
    app=_app(core); owned={"/alliance/deep-audit","/api/alliance/deep-audit"}
    app.router.routes[:]=[r for r in app.router.routes if getattr(r,"path",None) not in owned]
    @app.get("/api/alliance/deep-audit")
    async def api(req:Request):
        _login(core,req); return await deep_snapshot(core,req)
    @app.get("/alliance/deep-audit",response_class=HTMLResponse)
    async def page(req:Request):
        _login(core,req); return HTMLResponse(_page_html(await deep_snapshot(core,req)))
    return {"status":"REGISTERED","version":VERSION}
