from __future__ import annotations
import html, json, re
from datetime import datetime, timezone
from fastapi import Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
VERSION="1.1.0-ALLIANCE-BABY-NATURAL-LANGUAGE"

def _app(core): return getattr(core,"app",None) or core
def _engine(core): return getattr(core,"engine",None)
def _login(core,req):
    fn=getattr(core,"need_login",None)
    return fn(req) if fn else "team"

def _find_req(engine, rid):
    import alliance_primary_workspace_v730 as ws
    try: return ws._requirement(engine,rid)
    except Exception: return None

def _run_saved(engine,rid):
    import alliance_primary_workspace_v730 as ws
    return ws._match_full(engine,rid,80)

def _extract_location(raw):
    import alliance_micromarket_knowledge_v1 as geo
    n=re.sub(r"\s+"," ",str(raw or "")).strip().lower()
    aliases=[]
    for a,canonical in getattr(geo,"ALIASES",{}).items():
        aliases.append((str(a),str(canonical)))
    for x in getattr(geo,"MARKETS",[]):
        aliases.append((str(x[1]),str(x[1])))
    aliases.sort(key=lambda x:len(x[0]),reverse=True)
    for candidate,canonical in aliases:
        if re.search(r"(?<!\w)"+re.escape(candidate.lower())+r"(?!\w)",n):
            m=geo.market(canonical)
            return canonical,(m[2] if m else "")
    return "",""

def _parse_free_text(raw):
    import alliance_authentic_reasoning_orchestrator_v1 as brain
    loc,city=_extract_location(raw)
    base={"raw_text":raw,"location":loc,"locality":loc,"city":city}
    interpreted=brain.interpret(base)
    tx=interpreted.get("transaction") or "UNKNOWN"
    area=None
    m=re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|square\s*feet)",raw,re.I)
    if m:
        try: area=float(m.group(1).replace(",",""))
        except Exception: area=None
    if area is None:
        m=re.search(r"\b(\d{3,6})\s*(?:sft|sf)\b",raw,re.I)
        if m:
            try: area=float(m.group(1))
            except Exception: area=None
    req={
      "canonical_id":"ADHOC",
      "raw_text":raw,
      "locality":loc,
      "location":loc,
      "city":city,
      "transaction_type": tx if tx in {"RENT","SALE"} else "",
      "area_sqft":area,
      "property_type":"",
      "category":interpreted.get("use") or "GENERAL",
      "purpose":interpreted.get("use") or "GENERAL",
      "promotion_status":"ADHOC_QUERY",
      "verification_status":"ADHOC_QUERY",
    }
    return req

def _run_adhoc(engine,req,limit=80):
    import alliance_master_integration_v720 as v720
    tx=req.get("transaction_type") or ""
    props=v720._search_properties(engine,tx=tx,limit=4000)
    exact=[];same_city=[];broader=[]
    rl=(req.get("locality") or "").strip().lower()
    rc=(req.get("city") or "").strip().lower()
    area_req=req.get("area_sqft")
    for p in props:
        if str(p.get("availability_status") or "").upper() in {"UNAVAILABLE","INACTIVE"}:
            continue
        try: score,reasons=v720._score(req,p)
        except Exception:
            score,reasons=0,[]
        pl=(p.get("locality") or p.get("location") or "").strip().lower()
        pc=(p.get("city") or "").strip().lower()
        if rl and pl and (rl in pl or pl in rl):
            tier="EXACT_LOCALITY"; bonus=10
        elif rc and pc and rc==pc:
            tier="SAME_CITY_ALTERNATIVE"; bonus=3
        else:
            tier="TRANSACTION_AREA_ALTERNATIVE"; bonus=0
        area_prop=p.get("area_sqft")
        area_fit=False
        if area_req and area_prop:
            try:
                diff=abs(float(area_prop)-float(area_req))/max(float(area_req),1)
                area_fit=diff<=0.50
            except Exception: pass
        tx_fit=(not tx) or tx==p.get("transaction_type")
        # Keep exact locality, sufficiently scored results, or sensible area+transaction alternatives.
        if tier=="EXACT_LOCALITY" or score>=35 or (tier!="EXACT_LOCALITY" and area_fit and tx_fit):
            item={"score":min(100,score+bonus),"base_score":score,"tier":tier,"match_tier":tier,"reasons":reasons,"property":p}
            (exact if tier=="EXACT_LOCALITY" else same_city if tier=="SAME_CITY_ALTERNATIVE" else broader).append(item)
    for bucket in (exact,same_city,broader):
        bucket.sort(key=lambda x:(x["score"],x["property"].get("verification_status")=="VERIFIED"),reverse=True)
    return req,(exact+same_city+broader)[:limit]

def _think(req,matches):
    import alliance_authentic_reasoning_orchestrator_v1 as brain
    result=brain.reason(req,matches)
    try:
        import alliance_micromarket_knowledge_v1 as geo
        loc=req.get("locality") or req.get("location") or req.get("primary_location") or ""
        raw=" ".join(str(req.get(k) or "") for k in ("raw_text","purpose","category","property_type","subtype"))
        result["micromarkets"]=geo.suggest(loc,raw,8) if loc else {"status":"UNKNOWN_LOCATION","suggestions":[]}
    except Exception as e:
        result["micromarkets"]={"status":"UNAVAILABLE","suggestions":[],"error":type(e).__name__}
    return result

def _answer(req,matches,thought):
    exact=thought.get("exact") or []
    alts=thought.get("alternatives") or []
    micro=(thought.get("micromarkets") or {}).get("suggestions") or []
    lines=[]
    loc=req.get("locality") or req.get("location") or "location not identified"
    if exact:
        lines.append(f"I found {len(exact)} exact-location candidate(s) for {loc} in Master inventory.")
    elif alts:
        lines.append(f"I did not find an acceptable exact result for {loc}. I found Master alternatives and kept them clearly labelled.")
    else:
        lines.append(f"I did not find an acceptable current Master property for {loc}.")
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
        lines.append("Next search: internal source evidence → Property Discovery → Commercial Intelligence → human verification → Master → rerun.")
    if not (req.get("locality") or req.get("location")):
        lines.append("I could not confidently identify the requested locality. Add the locality/city for stronger nearby-market reasoning.")
    lines.append("Truth rule: unverified source/web evidence is never presented as an available property.")
    return "\n".join(lines)

def analyze(engine,user_input):
    s=str(user_input or "").strip()
    if not s:
        return {"status":"EMPTY","message":"Type a requirement, for example: Need 2,000 sqft restaurant on lease in Saket."}
    saved=_find_req(engine,s)
    if saved:
        req,matches=_run_saved(engine,s); source_mode="VERIFIED_MASTER_REQUIREMENT"
    else:
        req=_parse_free_text(s)
        req,matches=_run_adhoc(engine,req,80); source_mode="NATURAL_LANGUAGE_QUERY"
    thought=_think(req,matches)
    return {"status":"OK","version":VERSION,"generated_at":datetime.now(timezone.utc).isoformat(),
            "source_mode":source_mode,"requirement":req,"match_count":len(matches),
            "reasoning":thought,"answer":_answer(req,matches,thought)}

def _page(data,user_input=""):
    if data.get("status")!="OK":
        body=f"<div class=card>{html.escape(data.get('message','Unable to analyse'))}</div>"
    else:
        r=data["reasoning"]; micro=(r.get("micromarkets") or {}).get("suggestions") or []
        req=data.get("requirement") or {}
        rows="".join(f"<tr><td>{html.escape(str(x.get('location') or ''))}</td><td>{html.escape(str(x.get('market_type') or ''))}</td><td>{html.escape(str(x.get('distance_km') or ''))}</td><td>{html.escape(str(x.get('reason') or ''))}</td></tr>" for x in micro)
        parsed=f"Location: {req.get('locality') or 'not identified'} · City: {req.get('city') or 'not identified'} · Transaction: {req.get('transaction_type') or 'unknown'} · Area: {req.get('area_sqft') or 'not specified'}"
        body=f"""<div class=card><h3>Baby's Answer</h3><pre>{html.escape(data['answer'])}</pre></div>
<div class=card><b>Input mode:</b> {html.escape(str(data.get('source_mode')))}<br><b>Parsed:</b> {html.escape(parsed)}<br><b>Decision:</b> {html.escape(str(r.get('decision')))} · <b>Master matches:</b> {data['match_count']}</div>
<div class=card><h3>Micro-market intelligence</h3><table><tr><th>Market</th><th>Type</th><th>Approx km</th><th>Reason</th></tr>{rows}</table></div>
<div class=card><h3>Search escalation</h3><pre>{html.escape(json.dumps(r.get('search_escalation'),ensure_ascii=False,indent=2,default=str))}</pre></div>"""
    return f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Alliance Baby</title>
<style>body{{font-family:Arial;background:#f4f7fb;color:#172033;margin:0;padding:20px}}.card{{background:white;border:1px solid #dfe6ee;border-radius:12px;padding:16px;margin:12px 0}}input,button{{padding:11px}}input{{width:min(850px,75vw)}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #aaa;padding:8px;text-align:left}}pre{{white-space:pre-wrap;word-break:break-word}}</style></head><body>
<p><a href="javascript:history.back()">← Previous Page</a> · <a href="/alliance/primary">Dashboard</a></p>
<h2>Alliance Baby · CRE Copilot</h2><p>Type the requirement in normal language. A verified Master Requirement ID also works.</p>
<form method=get action='/alliance/baby'><input name=q value="{html.escape(user_input,quote=True)}" placeholder='Example: Need 2,000 sqft restaurant on lease in Saket' autofocus><button>Think & Match</button></form>{body}</body></html>"""

def register(core):
    app=_app(core); eng=_engine(core)
    owned={"/alliance/baby","/api/alliance/baby"}
    app.router.routes[:]=[r for r in app.router.routes if getattr(r,"path",None) not in owned]
    @app.get("/alliance/baby",response_class=HTMLResponse)
    async def baby(req:Request, q:str=Query(default=""), requirement_id:str=Query(default="")):
        _login(core,req)
        user_input=(q or requirement_id or "").strip()
        data=analyze(eng,user_input)
        return HTMLResponse(_page(data,user_input))
    @app.get("/api/alliance/baby")
    async def baby_api(req:Request, q:str=Query(default=""), requirement_id:str=Query(default="")):
        _login(core,req)
        return JSONResponse(analyze(eng,(q or requirement_id or "").strip()))
    return {"status":"REGISTERED","version":VERSION,"routes":["/alliance/baby","/api/alliance/baby"],"natural_language":True}

def self_test():
    r=_parse_free_text("Need 2000 sqft restaurant on lease in Saket")
    assert r["locality"]=="Saket" and r["transaction_type"]=="RENT" and r["area_sqft"]==2000
    r2=_parse_free_text("Looking for retail store in Bandra West")
    assert r2["locality"]=="Bandra West"
    return True
