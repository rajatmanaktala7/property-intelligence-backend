from __future__ import annotations
import html, json, re
from datetime import datetime, timezone
from fastapi import Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
VERSION="1.2.0-ALLIANCE-BABY-TRUTHFUL-SHORTLIST"

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
    return {
      "canonical_id":"ADHOC","raw_text":raw,"locality":loc,"location":loc,"city":city,
      "transaction_type": tx if tx in {"RENT","SALE"} else "","area_sqft":area,
      "property_type":"","category":interpreted.get("use") or "GENERAL","purpose":interpreted.get("use") or "GENERAL",
      "promotion_status":"ADHOC_QUERY","verification_status":"ADHOC_QUERY",
    }

def _ptext(p):
    vals=[]
    for k in ("property_type","category","subtype","description","title","remarks","suitable_category","use_type","locality","city"):
        v=p.get(k)
        if v not in (None,""): vals.append(str(v))
    cr=p.get("clean_record")
    if cr not in (None,""):
        try: vals.append(json.dumps(cr,ensure_ascii=False,default=str))
        except Exception: vals.append(str(cr))
    return " ".join(vals).upper()

def _use_fit(req,p):
    use=str(req.get("category") or req.get("purpose") or "GENERAL").upper()
    t=_ptext(p)
    residential=any(x in t for x in ("RESIDENTIAL","APARTMENT","FLAT","VILLA","HOUSE","PENTHOUSE","BUILDER FLOOR"))
    if use=="FNB":
        positive=any(x in t for x in ("RESTAURANT","CAFE","F&B","FNB","FOOD","BAR","LOUNGE","COMMERCIAL","RETAIL","SHOP","SHOWROOM"))
        if residential and not positive: return "REJECT_RESIDENTIAL"
        return "FIT" if positive else "UNKNOWN"
    if use=="RETAIL":
        positive=any(x in t for x in ("RETAIL","SHOP","SHOWROOM","COMMERCIAL","MARKET","MALL"))
        if residential and not positive: return "REJECT_RESIDENTIAL"
        return "FIT" if positive else "UNKNOWN"
    if use=="OFFICE":
        positive=any(x in t for x in ("OFFICE","COMMERCIAL","BUSINESS","CORPORATE"))
        if residential and not positive: return "REJECT_RESIDENTIAL"
        return "FIT" if positive else "UNKNOWN"
    if use=="RESIDENTIAL":
        return "FIT" if residential else "UNKNOWN"
    return "UNKNOWN"

def _workflow_truth(p):
    v=str(p.get("verification_status") or "UNVERIFIED").upper()
    a=str(p.get("availability_status") or "UNKNOWN").upper()
    if v=="VERIFIED" and a=="AVAILABLE": return "VERIFIED_AVAILABLE"
    if v=="VERIFIED": return "VERIFIED_AVAILABILITY_UNKNOWN"
    if a=="AVAILABLE": return "UNVERIFIED_AVAILABILITY_CLAIM"
    return "NEEDS_VERIFICATION"

def _location(p):
    return p.get("locality") or p.get("location") or p.get("primary_location") or p.get("city") or "Location not captured"

def _run_adhoc(engine,req,limit=80):
    import alliance_master_integration_v720 as v720
    tx=req.get("transaction_type") or ""
    props=v720._search_properties(engine,tx=tx,limit=4000)
    exact=[];same_city=[];broader=[]
    rl=(req.get("locality") or "").strip().lower()
    rc=(req.get("city") or "").strip().lower()
    area_req=req.get("area_sqft")
    for p in props:
        if str(p.get("availability_status") or "").upper() in {"UNAVAILABLE","INACTIVE"}: continue
        use_fit=_use_fit(req,p)
        if use_fit=="REJECT_RESIDENTIAL": continue
        try: score,reasons=v720._score(req,p)
        except Exception: score,reasons=0,[]
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
            try: area_fit=abs(float(area_prop)-float(area_req))/max(float(area_req),1)<=0.50
            except Exception: pass
        tx_fit=(not tx) or tx==p.get("transaction_type")
        if tier=="EXACT_LOCALITY" or score>=35 or (tier!="EXACT_LOCALITY" and area_fit and tx_fit):
            use_bonus=8 if use_fit=="FIT" else 0
            item={"score":min(100,score+bonus+use_bonus),"base_score":score,"tier":tier,"match_tier":tier,
                  "reasons":list(reasons)+([f"use:{use_fit}"] if use_fit else []),"property":p,"use_fit":use_fit}
            (exact if tier=="EXACT_LOCALITY" else same_city if tier=="SAME_CITY_ALTERNATIVE" else broader).append(item)
    for bucket in (exact,same_city,broader):
        bucket.sort(key=lambda x:(_workflow_truth(x["property"])=="VERIFIED_AVAILABLE",x["use_fit"]=="FIT",x["score"]),reverse=True)
    return req,(exact+same_city+broader)[:limit]

def _micromarkets(req):
    try:
        import alliance_micromarket_knowledge_v1 as geo
        loc=req.get("locality") or req.get("location") or ""
        raw=" ".join(str(req.get(k) or "") for k in ("raw_text","purpose","category","property_type","subtype"))
        return geo.suggest(loc,raw,8) if loc else {"status":"UNKNOWN_LOCATION","suggestions":[]}
    except Exception as e:
        return {"status":"UNAVAILABLE","suggestions":[],"error":type(e).__name__}

def _assess(req,matches):
    rows=[]
    for m in matches or []:
        p=m.get("property") or {}
        truth=_workflow_truth(p)
        use_fit=m.get("use_fit") or _use_fit(req,p)
        rows.append({
          "canonical_id":p.get("canonical_id"),"location":_location(p),"city":p.get("city"),
          "area_sqft":p.get("area_sqft_display") or p.get("area_sqft"),"transaction":p.get("transaction_type"),
          "property_type":p.get("property_type") or p.get("subtype") or p.get("category") or "",
          "amount":p.get("rent_amount") or p.get("sale_amount") or p.get("price_raw") or "",
          "tier":m.get("tier") or m.get("match_tier") or "UNKNOWN","score":float(m.get("score") or 0),
          "truth":truth,"use_fit":use_fit,
          "client_safe":truth=="VERIFIED_AVAILABLE" and use_fit!="UNKNOWN"
        })
    exact=[x for x in rows if x["tier"]=="EXACT_LOCALITY"]
    exact_safe=[x for x in exact if x["client_safe"]]
    exact_verify=[x for x in exact if not x["client_safe"]]
    alternatives=[x for x in rows if x["tier"]!="EXACT_LOCALITY"]
    alternative_safe=[x for x in alternatives if x["client_safe"]]
    if exact_safe: decision="VERIFIED_EXACT_RESULTS"
    elif exact: decision="EXACT_CANDIDATES_NEED_VERIFICATION"
    elif alternative_safe: decision="VERIFIED_ALTERNATIVES"
    elif alternatives: decision="ALTERNATIVES_NEED_VERIFICATION"
    else: decision="NO_MASTER_CANDIDATE"
    return {"decision":decision,"exact_safe":exact_safe,"exact_verify":exact_verify,
            "alternative_safe":alternative_safe,"alternatives":alternatives,"all":rows}

def _search_plan(req,assessment):
    loc=req.get("locality") or req.get("location") or "requested market"
    use=req.get("category") or req.get("purpose") or "property"
    plan=[]
    if assessment["exact_verify"]:
        plan.append({"priority":1,"action":"VERIFY_EXACT_CANDIDATES","why":f"Exact {loc} records exist but are not yet client-safe. Verify availability, use suitability and source."})
    plan.extend([
      {"priority":2,"action":"CHECK_MICROMARKETS","why":f"Search comparable {use} markets near {loc} if exact candidates fail verification."},
      {"priority":3,"action":"SEARCH_INTERNAL_EVIDENCE","why":"Search WhatsApp, magazine, newspaper and manual source evidence."},
      {"priority":4,"action":"RUN_PROPERTY_DISCOVERY","why":"Search fresh public property sources as leads, not as verified inventory."},
      {"priority":5,"action":"RUN_COMMERCIAL_INTELLIGENCE","why":"Search occupier/supply intelligence and market signals."},
      {"priority":6,"action":"HUMAN_VERIFY_AND_PROMOTE","why":"Only verified evidence may become client-safe Master inventory."},
    ])
    return plan

def _answer(req,assessment,micro):
    loc=req.get("locality") or req.get("location") or "requested location"
    lines=[]
    if assessment["exact_safe"]:
        lines.append(f"I found {len(assessment['exact_safe'])} VERIFIED + AVAILABLE exact-location result(s) for {loc}.")
        for x in assessment["exact_safe"][:5]:
            lines.append(f"• {x['location']} | {x['area_sqft'] or 'area unknown'} sqft | {x['property_type'] or 'type not captured'} | score {x['score']}")
    elif assessment["exact_verify"]:
        lines.append(f"I found {len(assessment['exact_verify'])} exact-location candidate(s) for {loc}, but NONE is currently client-safe.")
        lines.append("They require verification of availability and/or commercial-use suitability before sharing.")
        for x in assessment["exact_verify"][:5]:
            lines.append(f"• {x['location']} | {x['area_sqft'] or 'area unknown'} sqft | {x['property_type'] or 'type not captured'} | {x['truth']} | use {x['use_fit']} | score {x['score']}")
    elif assessment["alternative_safe"]:
        lines.append(f"No verified exact result in {loc}. I found {len(assessment['alternative_safe'])} verified alternative(s).")
        for x in assessment["alternative_safe"][:5]:
            lines.append(f"• {x['location']} | {x['tier']} | {x['area_sqft'] or 'area unknown'} sqft | score {x['score']}")
    elif assessment["alternatives"]:
        lines.append(f"No client-safe exact result in {loc}. Alternative Master candidates exist, but they still need verification.")
        for x in assessment["alternatives"][:5]:
            lines.append(f"• {x['location']} | {x['tier']} | {x['truth']} | use {x['use_fit']} | score {x['score']}")
    else:
        lines.append(f"No acceptable Master candidate is currently available for {loc}.")
    suggestions=(micro or {}).get("suggestions") or []
    if not assessment["exact_safe"] and suggestions:
        lines.append("Comparable markets to investigate next:")
        for x in suggestions[:5]:
            lines.append(f"• {x['location']} | {x['market_type']} | ~{x['distance_km']} km | {x['reason']}")
    lines.append("Truth rule: a Master record is not client-safe unless verification + availability + use-fit are satisfactory.")
    return "\n".join(lines)

def analyze(engine,user_input):
    s=str(user_input or "").strip()
    if not s: return {"status":"EMPTY","message":"Type a requirement, for example: Need 2,000 sqft restaurant on lease in Saket."}
    saved=_find_req(engine,s)
    if saved:
        req,matches=_run_saved(engine,s); source_mode="VERIFIED_MASTER_REQUIREMENT"
        # add Baby use-fit metadata to saved matcher results without changing primary scoring
        for m in matches:
            m["use_fit"]=_use_fit(req,m.get("property") or {})
    else:
        req=_parse_free_text(s); req,matches=_run_adhoc(engine,req,80); source_mode="NATURAL_LANGUAGE_QUERY"
    assessment=_assess(req,matches)
    micro=_micromarkets(req)
    return {"status":"OK","version":VERSION,"generated_at":datetime.now(timezone.utc).isoformat(),
            "source_mode":source_mode,"requirement":req,"raw_match_count":len(matches),
            "assessment":assessment,"micromarkets":micro,"search_plan":_search_plan(req,assessment),
            "answer":_answer(req,assessment,micro)}

def _rows_html(items):
    return "".join(
      "<tr>"+ "".join("<td>"+html.escape(str(v if v not in (None,"") else "—"))+"</td>" for v in
      (x.get("location"),x.get("area_sqft"),x.get("property_type"),x.get("transaction"),x.get("amount"),x.get("tier"),x.get("truth"),x.get("use_fit"),x.get("score"))) +"</tr>"
      for x in items[:15]
    )

def _page(data,user_input=""):
    if data.get("status")!="OK":
        body=f"<div class=card>{html.escape(data.get('message','Unable to analyse'))}</div>"
    else:
        a=data["assessment"]; req=data["requirement"]; micro=(data.get("micromarkets") or {}).get("suggestions") or []
        shortlist=a["exact_safe"] or a["exact_verify"] or a["alternative_safe"] or a["alternatives"]
        table=_rows_html(shortlist)
        mrows="".join(f"<tr><td>{html.escape(str(x.get('location') or ''))}</td><td>{html.escape(str(x.get('market_type') or ''))}</td><td>{html.escape(str(x.get('distance_km') or ''))}</td><td>{html.escape(str(x.get('reason') or ''))}</td></tr>" for x in micro)
        parsed=f"Location: {req.get('locality') or 'not identified'} · City: {req.get('city') or 'not identified'} · Transaction: {req.get('transaction_type') or 'unknown'} · Area: {req.get('area_sqft') or 'not specified'} · Use: {req.get('category') or req.get('purpose') or 'general'}"
        body=f"""<div class=card><h3>Baby's Answer</h3><pre>{html.escape(data['answer'])}</pre></div>
<div class=card><b>Input mode:</b> {html.escape(data['source_mode'])}<br><b>Parsed:</b> {html.escape(parsed)}<br><b>Decision:</b> {html.escape(a['decision'])} · <b>Raw Master candidates considered:</b> {data['raw_match_count']}</div>
<div class=card><h3>Property Shortlist</h3><table><tr><th>Location</th><th>Area sqft</th><th>Type</th><th>Txn</th><th>Amount</th><th>Tier</th><th>Truth</th><th>Use Fit</th><th>Score</th></tr>{table}</table></div>
<div class=card><h3>Micro-market intelligence</h3><table><tr><th>Market</th><th>Type</th><th>Approx km</th><th>Reason</th></tr>{mrows}</table></div>
<div class=card><h3>Automatic Next Actions</h3><pre>{html.escape(json.dumps(data['search_plan'],ensure_ascii=False,indent=2))}</pre></div>"""
    return f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Alliance Baby</title>
<style>body{{font-family:Arial;background:#f4f7fb;color:#172033;margin:0;padding:20px}}.card{{background:white;border:1px solid #dfe6ee;border-radius:12px;padding:16px;margin:12px 0}}input,button{{padding:11px}}input{{width:min(850px,75vw)}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{border:1px solid #aaa;padding:8px;text-align:left;vertical-align:top}}pre{{white-space:pre-wrap;word-break:break-word}}</style></head><body>
<p><a href="javascript:history.back()">← Previous Page</a> · <a href="/alliance/primary">Dashboard</a></p>
<h2>Alliance Baby · CRE Copilot</h2><p>Natural-language CRE reasoning · Master-first · verification-aware · use-aware · automatic next actions</p>
<form method=get action='/alliance/baby'><input name=q value="{html.escape(user_input,quote=True)}" placeholder='Example: Need 2,000 sqft restaurant on lease in Saket' autofocus><button>Think & Match</button></form>{body}</body></html>"""

def register(core):
    app=_app(core); eng=_engine(core)
    owned={"/alliance/baby","/api/alliance/baby"}
    app.router.routes[:]=[r for r in app.router.routes if getattr(r,"path",None) not in owned]
    @app.get("/alliance/baby",response_class=HTMLResponse)
    async def baby(req:Request,q:str=Query(default=""),requirement_id:str=Query(default="")):
        _login(core,req); user_input=(q or requirement_id or "").strip()
        return HTMLResponse(_page(analyze(eng,user_input),user_input))
    @app.get("/api/alliance/baby")
    async def baby_api(req:Request,q:str=Query(default=""),requirement_id:str=Query(default="")):
        _login(core,req); return JSONResponse(analyze(eng,(q or requirement_id or "").strip()))
    return {"status":"REGISTERED","version":VERSION,"routes":["/alliance/baby","/api/alliance/baby"],"truthful_shortlist":True}

def self_test():
    req=_parse_free_text("Need 2000 sqft restaurant on lease in Saket")
    assert req["locality"]=="Saket" and req["transaction_type"]=="RENT" and req["area_sqft"]==2000 and req["category"]=="FNB"
    assert _location({"locality":"Saket"})=="Saket"
    assert _use_fit(req,{"property_type":"Residential Apartment"})=="REJECT_RESIDENTIAL"
    assert _workflow_truth({"verification_status":"VERIFIED","availability_status":"AVAILABLE"})=="VERIFIED_AVAILABLE"
    return True
