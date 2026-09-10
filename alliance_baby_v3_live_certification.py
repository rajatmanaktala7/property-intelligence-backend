from __future__ import annotations
import html,json
from datetime import datetime,timezone
from fastapi import Request
from fastapi.responses import HTMLResponse,JSONResponse
VERSION="3.1.0-ALLIANCE-BABY-LIVE-GEOGRAPHY-CERTIFICATION"
BENCHMARKS=[
("Need 2000 sqft restaurant on lease in Saket","Saket"),
("Need 1200 sqft retail shop for rent in Khan Market","Khan Market"),
("Looking to buy 3000 sqft office in BKC","BKC"),
("Want villa for sale in Siolim 4000 sqft","Siolim"),
("Need 1800 sqft cafe in Bandra West on rent","Bandra West"),
("Need 2500 sqft office on lease in DLF Cyber City","DLF Cyber City"),
("Looking for 900 sqft showroom for rent in Sector 18 Noida","Sector 18 Noida"),
("Need 1500 sqft restaurant on rent in Hauz Khas","Hauz Khas")]
def _app(core): return getattr(core,"app",None) or core
def _engine(core): return getattr(core,"engine",None)
def _login(core,req):
    fn=getattr(core,"need_login",None); return fn(req) if fn else "team"
def _n(v): return str(v or "").strip().upper()
def run_live(engine):
    import alliance_baby_cre_copilot_v1 as baby
    import alliance_baby_geographic_intelligence_v3 as geo
    results=[]
    for q,expected_location in BENCHMARKS:
        try:
            data=baby.analyze(engine,q); req=data.get("requirement") or {}
            rows=(data.get("assessment") or {}).get("all") or []
            d=geo.decide(req,rows); exact=d["exact"]; comp=d["comparable"]; search=d["search_only"]; issues=[]
            for r in exact:
                if _n(r.get("location") or r.get("locality"))!=_n(expected_location): issues.append("NON_EXACT_ROW_IN_EXACT")
            for r in comp:
                g=geo.classify_geography(req,r)
                if g.get("class")!="COMPARABLE" or not g.get("recommendable"): issues.append("UNSUPPORTED_COMPARABLE")
            exact_ids={str(r.get("canonical_id") or r.get("id") or id(r)) for r in exact}
            comp_ids={str(r.get("canonical_id") or r.get("id") or id(r)) for r in comp}
            for r in search:
                rid=str(r.get("canonical_id") or r.get("id") or id(r))
                if rid in exact_ids or rid in comp_ids: issues.append("SEARCH_ONLY_LEAK")
            if not exact and not comp and d["decision"]!="NO_RELEVANT_MASTER_INVENTORY": issues.append("FALSE_ALTERNATIVE_DECISION")
            if d["decision"]=="NO_RELEVANT_MASTER_INVENTORY" and (exact or comp): issues.append("FALSE_NO_RESULT")
            results.append({"query":q,"pass":not issues,"decision":d["decision"],"counts":d["counts"],
              "issues":sorted(set(issues)),"exact_locations":sorted(set(str(r.get("location") or r.get("locality") or "") for r in exact))[:20],
              "comparable_locations":sorted(set(str(r.get("location") or r.get("locality") or "") for r in comp))[:20]})
        except Exception as e: results.append({"query":q,"pass":False,"error":f"{type(e).__name__}: {e}"})
    passed=sum(1 for r in results if r.get("pass"))
    return {"status":"PASS" if passed==len(results) else "FAIL","tested":len(results),"passed":passed,"failed":len(results)-passed,"benchmarks":results}
def certification(engine):
    import alliance_baby_cre_copilot_v1 as baby
    import alliance_baby_geographic_intelligence_v3 as geo
    s=baby.training_exam(); a=geo.exam(); live=run_live(engine)
    ok=s["status"]=="PASS" and a["status"]=="PASS" and live["status"]=="PASS"
    return {"version":VERSION,"generated_at":datetime.now(timezone.utc).isoformat(),"certification":"CERTIFIED" if ok else "NOT_CERTIFIED","certified":ok,
      "safety":{"status":s["status"],"passed":s["passed"],"tested":s["tested"]},
      "geography_architect":{"status":a["status"],"passed":a["passed"],"tested":a["tested"]},"live_geography":live}
def _page(r):
    rows=[]
    for b in r["live_geography"]["benchmarks"]:
        rows.append("<tr><td>"+html.escape(b.get("query",""))+"</td><td>"+("PASS" if b.get("pass") else "FAIL")+"</td><td>"+
          html.escape(str(b.get("decision","")))+"</td><td>"+html.escape(json.dumps(b.get("counts") or {}))+"</td><td>"+
          html.escape(", ".join(b.get("comparable_locations") or []))+"</td><td>"+html.escape(json.dumps(b.get("issues") or b.get("error","")))+"</td></tr>")
    return f'''<!doctype html><html><head><meta charset=utf-8><title>Baby V3 Live Geography Certification</title>
<style>body{{font-family:Arial;background:#f4f7fb;color:#172033;padding:20px}}.card{{background:#fff;border:1px solid #dde5ee;border-radius:12px;padding:16px;margin:12px 0;overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{border:1px solid #bbb;padding:8px;text-align:left;vertical-align:top}}</style></head><body>
<p><a href="/alliance/baby">← Baby</a> · <a href="/alliance/baby/autonomous-audit">Safety Audit</a> · <a href="/alliance/primary">Dashboard</a></p>
<h2>Alliance Baby V3 · Live Geography Certification</h2><div class=card><h3>{r["certification"]}</h3><p><b>Certified:</b> {r["certified"]}</p>
<p><b>V2 Safety:</b> {r["safety"]["status"]} · {r["safety"]["passed"]}/{r["safety"]["tested"]}</p>
<p><b>V3 Architect:</b> {r["geography_architect"]["status"]} · {r["geography_architect"]["passed"]}/{r["geography_architect"]["tested"]}</p>
<p><b>Live Geography:</b> {r["live_geography"]["status"]} · {r["live_geography"]["passed"]}/{r["live_geography"]["tested"]}</p></div>
<div class=card><table><tr><th>Requirement</th><th>Result</th><th>Decision</th><th>Counts</th><th>Comparable Markets</th><th>Issues</th></tr>{''.join(rows)}</table></div></body></html>'''
def register(core):
    app=_app(core); eng=_engine(core); owned={"/alliance/baby/v3-live-certification","/api/alliance/baby/v3-live-certification"}
    app.router.routes[:]=[r for r in app.router.routes if getattr(r,"path",None) not in owned]
    @app.get("/alliance/baby/v3-live-certification",response_class=HTMLResponse)
    async def page(req:Request):
        _login(core,req); return HTMLResponse(_page(certification(eng)))
    @app.get("/api/alliance/baby/v3-live-certification")
    async def api(req:Request):
        _login(core,req); return JSONResponse(certification(eng))
    return {"status":"REGISTERED","version":VERSION,"routes":sorted(owned)}
