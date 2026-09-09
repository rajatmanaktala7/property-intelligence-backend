from __future__ import annotations
import html, json
from datetime import datetime, timezone
from fastapi import Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
VERSION="1.0.0-ALLIANCE-BABY-CRE-COPILOT"

def _app(core): return getattr(core,"app",None) or core
def _engine(core): return getattr(core,"engine",None)
def _login(core,req):
    fn=getattr(core,"need_login",None)
    return fn(req) if fn else "team"

def _find_req(engine, rid):
    import alliance_primary_workspace_v730 as ws
    return ws._requirement(engine,rid)

def _run(engine,rid):
    import alliance_primary_workspace_v730 as ws
    req,matches=ws._match_full(engine,rid,80)
    return req,matches

def _think(req,matches):
    import alliance_authentic_reasoning_orchestrator_v1 as brain
    result=brain.reason(req,matches)
    try:
        import alliance_micromarket_knowledge_v1 as geo
        loc=req.get("locality") or req.get("location") or req.get("primary_location") or ""
        raw=" ".join(str(req.get(k) or "") for k in ("raw_text","purpose","category","property_type","subtype"))
        result["micromarkets"]=geo.suggest(loc,raw,8)
    except Exception as e:
        result["micromarkets"]={"status":"UNAVAILABLE","suggestions":[],"error":type(e).__name__}
    return result

def _answer(req,matches,thought):
    exact=thought.get("exact") or []
    alts=thought.get("alternatives") or []
    micro=(thought.get("micromarkets") or {}).get("suggestions") or []
    lines=[]
    if exact:
        lines.append(f"I found {len(exact)} exact-location candidate(s) in the Master database.")
    elif alts:
        lines.append("No acceptable exact-location result was found. I found alternative Master properties and kept them clearly labelled as alternatives.")
    else:
        lines.append("No acceptable property is currently available in verified Master inventory.")
    if exact:
        for x in exact[:5]:
            lines.append(f"• {x.get('location') or 'Location not captured'} | score {x.get('score')} | evidence {x.get('evidence')} | {'client-safe' if x.get('client_safe') else 'verify before sharing'}")
    if alts:
        lines.append("Alternative properties:")
        for x in alts[:5]:
            lines.append(f"• {x.get('location') or 'Location not captured'} | {x.get('tier')} | score {x.get('score')} | evidence {x.get('evidence')}")
    if micro and not exact:
        lines.append("Markets I would investigate next:")
        for x in micro[:5]:
            lines.append(f"• {x['location']} | {x['market_type']} | ~{x['distance_km']} km | {x['reason']}")
    if not exact and not alts:
        lines.append("Automatic next-search plan: Master → comparable micro-markets → internal source evidence → Property Discovery → Commercial Intelligence → human verification → Master → rerun matcher.")
    lines.append("Truth rule: I will not call an unverified web/source lead an available property.")
    return "\n".join(lines)

def analyze(engine,rid):
    req=_find_req(engine,rid)
    if not req: return {"status":"NOT_FOUND","message":"Requirement must be promoted and VERIFIED in Master before Baby can match it."}
    req,matches=_run(engine,rid)
    thought=_think(req,matches)
    return {"status":"OK","version":VERSION,"generated_at":datetime.now(timezone.utc).isoformat(),
            "requirement":req,"match_count":len(matches),"reasoning":thought,"answer":_answer(req,matches,thought)}

def _page(data):
    if data.get("status")!="OK":
        body=f"<div class=card>{html.escape(data.get('message','Unable to analyse'))}</div>"
    else:
        r=data["reasoning"]; micro=(r.get("micromarkets") or {}).get("suggestions") or []
        rows="".join(f"<tr><td>{html.escape(str(x.get('location') or ''))}</td><td>{html.escape(str(x.get('market_type') or ''))}</td><td>{html.escape(str(x.get('distance_km') or ''))}</td><td>{html.escape(str(x.get('reason') or ''))}</td></tr>" for x in micro)
        body=f"""<div class=card><h3>Baby's Answer</h3><pre>{html.escape(data['answer'])}</pre></div>
<div class=card><b>Decision:</b> {html.escape(str(r.get('decision')))} · <b>Master matches:</b> {data['match_count']}</div>
<div class=card><h3>Micro-market intelligence</h3><table><tr><th>Market</th><th>Type</th><th>Approx km</th><th>Reason</th></tr>{rows}</table></div>
<div class=card><h3>Search escalation</h3><pre>{html.escape(json.dumps(r.get('search_escalation'),ensure_ascii=False,indent=2,default=str))}</pre></div>"""
    return f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Alliance Baby</title>
<style>body{{font-family:Arial;background:#f4f7fb;color:#172033;margin:0;padding:20px}}.card{{background:white;border:1px solid #dfe6ee;border-radius:12px;padding:16px;margin:12px 0}}input,button{{padding:10px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #aaa;padding:8px;text-align:left}}pre{{white-space:pre-wrap;word-break:break-word}}</style></head><body>
<h2>Alliance Baby · CRE Copilot</h2><p>Master-first reasoning · micro-market intelligence · evidence critic · automatic search escalation</p>
<form method=get action='/alliance/baby'><input name=requirement_id placeholder='Verified Master Requirement ID' size=45><button>Think & Match</button></form>{body}</body></html>"""

def register(core):
    app=_app(core); eng=_engine(core)
    owned={"/alliance/baby","/api/alliance/baby"}
    app.router.routes[:]=[r for r in app.router.routes if getattr(r,"path",None) not in owned]
    @app.get("/alliance/baby",response_class=HTMLResponse)
    async def baby(req:Request, requirement_id:str=Query(default="")):
        _login(core,req)
        data={"status":"EMPTY","message":"Enter a VERIFIED Master Requirement ID."}
        if requirement_id.strip(): data=analyze(eng,requirement_id.strip())
        return HTMLResponse(_page(data))
    @app.get("/api/alliance/baby")
    async def baby_api(req:Request, requirement_id:str):
        _login(core,req); return JSONResponse(analyze(eng,requirement_id))
    return {"status":"REGISTERED","version":VERSION,"routes":["/alliance/baby","/api/alliance/baby"]}

def self_test():
    import alliance_authentic_reasoning_orchestrator_v1 as b
    assert b.interpret({"raw_text":"restaurant for lease in Saket","location":"Saket"})["use"]=="FNB"
    return True
