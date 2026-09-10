from __future__ import annotations
import html, json, re, hashlib
from datetime import datetime, timezone
from fastapi import Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
VERSION="1.3.0-ALLIANCE-BABY-EVIDENCE-DEDUP-TRUST"

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
    return ws._match_full(engine,rid,120)

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

def _clean_record(p):
    cr=p.get("clean_record")
    return cr if isinstance(cr,dict) else {}

def _pick(p,names):
    cr=_clean_record(p)
    for src in (p,cr):
        low={str(k).lower():k for k in src.keys()}
        for n in names:
            key=low.get(str(n).lower())
            if key is not None:
                v=src.get(key)
                if v not in (None,"",[],{}): return v
    return None

def _ptext(p):
    vals=[]
    for k in ("property_type","category","subtype","description","title","remarks","suitable_category","use_type","locality","city"):
        v=p.get(k)
        if v not in (None,""): vals.append(str(v))
    cr=_clean_record(p)
    if cr: vals.append(json.dumps(cr,ensure_ascii=False,default=str))
    return " ".join(vals).upper()

def _use_fit(req,p):
    use=str(req.get("category") or req.get("purpose") or "GENERAL").upper()
    t=_ptext(p)
    residential=any(x in t for x in ("RESIDENTIAL","APARTMENT","FLAT","VILLA","HOUSE","PENTHOUSE","BUILDER FLOOR"))
    evidence=[]
    if use=="FNB":
        for token in ("RESTAURANT","CAFE","F&B","FNB","FOOD","BAR","LOUNGE","COMMERCIAL","RETAIL","SHOP","SHOWROOM"):
            if token in t: evidence.append(token)
        if residential and not evidence: return "REJECT_RESIDENTIAL",["RESIDENTIAL_ONLY"]
        return ("FIT",evidence[:5]) if evidence else ("UNKNOWN",[])
    if use=="RETAIL":
        for token in ("RETAIL","SHOP","SHOWROOM","COMMERCIAL","MARKET","MALL"):
            if token in t: evidence.append(token)
        if residential and not evidence: return "REJECT_RESIDENTIAL",["RESIDENTIAL_ONLY"]
        return ("FIT",evidence[:5]) if evidence else ("UNKNOWN",[])
    if use=="OFFICE":
        for token in ("OFFICE","COMMERCIAL","BUSINESS","CORPORATE"):
            if token in t: evidence.append(token)
        if residential and not evidence: return "REJECT_RESIDENTIAL",["RESIDENTIAL_ONLY"]
        return ("FIT",evidence[:5]) if evidence else ("UNKNOWN",[])
    if use=="RESIDENTIAL":
        return ("FIT",["RESIDENTIAL"]) if residential else ("UNKNOWN",[])
    return "UNKNOWN",[]

def _workflow_truth(p):
    v=str(p.get("verification_status") or "UNVERIFIED").upper()
    a=str(p.get("availability_status") or "UNKNOWN").upper()
    if v=="VERIFIED" and a=="AVAILABLE": return "VERIFIED_AVAILABLE",100
    if v=="VERIFIED": return "VERIFIED_AVAILABILITY_UNKNOWN",65
    if a=="AVAILABLE": return "UNVERIFIED_AVAILABILITY_CLAIM",40
    return "NEEDS_VERIFICATION",20

def _location(p):
    return _pick(p,("locality","location","primary_location","area","micro_market")) or p.get("city") or "Location not captured"

def _ptype(p):
    return _pick(p,("property_type","subtype","category","property_category","asset_type","type")) or ""

def _amount(p):
    return p.get("rent_amount") or p.get("sale_amount") or p.get("price_raw") or _pick(p,("rent","rent_amount","asking_rent","amount","price")) or ""

def _description(p):
    v=_pick(p,("description","details","remarks","property_details","message_text","raw_text","title"))
    if isinstance(v,(dict,list)): return json.dumps(v,ensure_ascii=False,default=str)
    return str(v or "")

def _fingerprint(p):
    phone=_pick(p,("phone","contact_number","mobile","broker_phone","owner_phone")) or ""
    basis="|".join(str(x or "").strip().lower() for x in (
        _location(p),p.get("transaction_type"),p.get("area_sqft"),_amount(p),_ptype(p),phone,_description(p)[:120]
    ))
    return hashlib.sha1(basis.encode("utf-8","ignore")).hexdigest()

def _run_adhoc(engine,req,limit=120):
    import alliance_master_integration_v720 as v720
    tx=req.get("transaction_type") or ""
    props=v720._search_properties(engine,tx=tx,limit=4000)
    exact=[];same_city=[];broader=[]
    rl=(req.get("locality") or "").strip().lower()
    rc=(req.get("city") or "").strip().lower()
    area_req=req.get("area_sqft")
    seen=set()
    for p in props:
        if str(p.get("availability_status") or "").upper() in {"UNAVAILABLE","INACTIVE"}: continue
        fp=_fingerprint(p)
        if fp in seen: continue
        seen.add(fp)
        use_fit,use_evidence=_use_fit(req,p)
        if use_fit=="REJECT_RESIDENTIAL": continue
        try: score,reasons=v720._score(req,p)
        except Exception: score,reasons=0,[]
        pl=str(_location(p) or "").strip().lower()
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
            relevance=min(100,score+bonus+(8 if use_fit=="FIT" else 0))
            item={"score":relevance,"relevance_score":relevance,"base_score":score,"tier":tier,"match_tier":tier,
                  "reasons":list(reasons),"property":p,"use_fit":use_fit,"use_evidence":use_evidence}
            (exact if tier=="EXACT_LOCALITY" else same_city if tier=="SAME_CITY_ALTERNATIVE" else broader).append(item)
    for bucket in (exact,same_city,broader):
        bucket.sort(key=lambda x:(_workflow_truth(x["property"])[1],x["use_fit"]=="FIT",x["relevance_score"]),reverse=True)
    return req,(exact+same_city+broader)[:limit]

def _micromarkets(req):
    try:
        import alliance_micromarket_knowledge_v1 as geo
        loc=req.get("locality") or req.get("location") or ""
        raw=" ".join(str(req.get(k) or "") for k in ("raw_text","purpose","category","property_type","subtype"))
        return geo.suggest(loc,raw,8) if loc else {"status":"UNKNOWN_LOCATION","suggestions":[]}
    except Exception as e:
        return {"status":"UNAVAILABLE","suggestions":[],"error":type(e).__name__}

def _normalize_matches(req,matches):
    out=[];seen=set()
    for m in matches or []:
        p=m.get("property") or {}
        fp=_fingerprint(p)
        if fp in seen: continue
        seen.add(fp)
        truth,trust=_workflow_truth(p)
        uf,ue=_use_fit(req,p)
        tier=m.get("tier") or m.get("match_tier") or "UNKNOWN"
        relevance=float(m.get("relevance_score") or m.get("score") or 0)
        row={
          "canonical_id":p.get("canonical_id"),"location":_location(p),"city":p.get("city"),
          "area_sqft":p.get("area_sqft_display") or p.get("area_sqft"),"transaction":p.get("transaction_type"),
          "property_type":_ptype(p),"amount":_amount(p),"description":_description(p),
          "tier":tier,"relevance_score":relevance,"trust_score":trust,"truth":truth,
          "use_fit":uf,"use_evidence":ue,
          "client_safe":truth=="VERIFIED_AVAILABLE" and uf=="FIT"
        }
        out.append(row)
    return out

def _assess(req,matches):
    rows=_normalize_matches(req,matches)
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
        plan.append({"priority":1,"action":"VERIFY_EXACT_CANDIDATES","why":f"Exact {loc} candidates exist but trust is insufficient. Verify source, current availability and {use} suitability."})
    plan.extend([
      {"priority":2,"action":"CHECK_MICROMARKETS","why":f"Search comparable {use} markets near {loc} if exact candidates fail verification."},
      {"priority":3,"action":"SEARCH_INTERNAL_EVIDENCE","why":"Search WhatsApp, magazine, newspaper and manual evidence for fresh candidates."},
      {"priority":4,"action":"RUN_PROPERTY_DISCOVERY","why":"Search public property sources as candidate evidence only."},
      {"priority":5,"action":"RUN_COMMERCIAL_INTELLIGENCE","why":"Search supply, occupier and expansion intelligence."},
      {"priority":6,"action":"HUMAN_VERIFY_AND_PROMOTE","why":"Only verified + available + use-fit records become client-safe."},
    ])
    return plan

def _answer(req,a,micro):
    loc=req.get("locality") or req.get("location") or "requested location"
    lines=[]
    if a["exact_safe"]:
        lines.append(f"I found {len(a['exact_safe'])} VERIFIED + AVAILABLE + USE-FIT exact result(s) for {loc}.")
        src=a["exact_safe"]
    elif a["exact_verify"]:
        lines.append(f"I found {len(a['exact_verify'])} unique exact-location candidate(s) for {loc}, but NONE is client-safe yet.")
        lines.append("Relevance and trust are now separated. A high relevance score does not mean the record is verified.")
        src=a["exact_verify"]
    elif a["alternative_safe"]:
        lines.append(f"No client-safe exact result in {loc}. I found {len(a['alternative_safe'])} verified alternative(s).")
        src=a["alternative_safe"]
    elif a["alternatives"]:
        lines.append(f"No client-safe exact result in {loc}. Alternative Master candidates exist but still need verification.")
        src=a["alternatives"]
    else:
        lines.append(f"No acceptable Master candidate is currently available for {loc}.")
        src=[]
    for x in src[:5]:
        ev=", ".join(x["use_evidence"]) if x["use_evidence"] else "no explicit use evidence"
        lines.append(f"• {x['location']} | {x['area_sqft'] or 'area unknown'} sqft | relevance {x['relevance_score']} | trust {x['trust_score']} | {x['truth']} | use {x['use_fit']} ({ev})")
    suggestions=(micro or {}).get("suggestions") or []
    if not a["exact_safe"] and suggestions:
        lines.append("Comparable markets to investigate next:")
        for x in suggestions[:5]:
            lines.append(f"• {x['location']} | {x['market_type']} | ~{x['distance_km']} km")
    lines.append("Truth rule: relevance is not verification. Client-safe requires VERIFIED + AVAILABLE + explicit use-fit.")
    return "\n".join(lines)

def analyze(engine,user_input):
    s=str(user_input or "").strip()
    if not s: return {"status":"EMPTY","message":"Type a requirement, for example: Need 2,000 sqft restaurant on lease in Saket."}
    saved=_find_req(engine,s)
    if saved:
        req,matches=_run_saved(engine,s); source_mode="VERIFIED_MASTER_REQUIREMENT"
    else:
        req=_parse_free_text(s); req,matches=_run_adhoc(engine,req,120); source_mode="NATURAL_LANGUAGE_QUERY"
    a=_assess(req,matches); micro=_micromarkets(req)
    return {"status":"OK","version":VERSION,"generated_at":datetime.now(timezone.utc).isoformat(),
            "source_mode":source_mode,"requirement":req,"raw_match_count":len(matches),
            "unique_candidate_count":len(a["all"]),"assessment":a,"micromarkets":micro,
            "search_plan":_search_plan(req,a),"answer":_answer(req,a,micro)}

def _rows_html(items):
    rows=[]
    for x in items[:15]:
        ev=", ".join(x.get("use_evidence") or []) or "—"
        desc=(x.get("description") or "")
        if len(desc)>180: desc=desc[:177]+"..."
        vals=(x.get("location"),x.get("area_sqft"),x.get("property_type") or "—",x.get("transaction"),x.get("amount") or "—",
              x.get("tier"),x.get("relevance_score"),x.get("trust_score"),x.get("truth"),x.get("use_fit"),ev,desc or "—")
        rows.append("<tr>"+"".join("<td>"+html.escape(str(v if v not in (None,"") else "—"))+"</td>" for v in vals)+"</tr>")
    return "".join(rows)

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
<div class=card><b>Input mode:</b> {html.escape(data['source_mode'])}<br><b>Parsed:</b> {html.escape(parsed)}<br><b>Decision:</b> {html.escape(a['decision'])}<br><b>Raw candidates:</b> {data['raw_match_count']} · <b>Unique after dedup:</b> {data['unique_candidate_count']}</div>
<div class=card><h3>Evidence-backed Property Shortlist</h3><table><tr><th>Location</th><th>Area</th><th>Type</th><th>Txn</th><th>Amount</th><th>Tier</th><th>Relevance</th><th>Trust</th><th>Truth</th><th>Use Fit</th><th>Use Evidence</th><th>Description / Evidence</th></tr>{table}</table></div>
<div class=card><h3>Micro-market intelligence</h3><table><tr><th>Market</th><th>Type</th><th>Approx km</th><th>Reason</th></tr>{mrows}</table></div>
<div class=card><h3>Automatic Next Actions</h3><pre>{html.escape(json.dumps(data['search_plan'],ensure_ascii=False,indent=2))}</pre></div>"""
    return f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Alliance Baby</title>
<style>body{{font-family:Arial;background:#f4f7fb;color:#172033;margin:0;padding:20px}}.card{{background:white;border:1px solid #dfe6ee;border-radius:12px;padding:16px;margin:12px 0;overflow:auto}}input,button{{padding:11px}}input{{width:min(850px,75vw)}}table{{border-collapse:collapse;width:max-content;min-width:100%;font-size:12px}}th,td{{border:1px solid #aaa;padding:8px;text-align:left;vertical-align:top;max-width:320px}}pre{{white-space:pre-wrap;word-break:break-word}}</style></head><body>
<p><a href="javascript:history.back()">← Previous Page</a> · <a href="/alliance/primary">Dashboard</a></p>
<h2>Alliance Baby · CRE Copilot</h2><p>Natural-language CRE reasoning · relevance ≠ trust · evidence-aware · deduplicated · automatic next actions</p>
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
    return {"status":"REGISTERED","version":VERSION,"routes":["/alliance/baby","/api/alliance/baby"],"evidence_dedup_trust":True}

def self_test():
    req=_parse_free_text("Need 2000 sqft restaurant on lease in Saket")
    assert req["locality"]=="Saket" and req["transaction_type"]=="RENT" and req["area_sqft"]==2000 and req["category"]=="FNB"
    uf,ue=_use_fit(req,{"clean_record":{"description":"commercial shop suitable for restaurant"}})
    assert uf=="FIT" and "RESTAURANT" in ue
    assert _workflow_truth({"verification_status":"VERIFIED","availability_status":"AVAILABLE"})==("VERIFIED_AVAILABLE",100)
    assert _workflow_truth({"verification_status":"UNVERIFIED","availability_status":"UNKNOWN"})[1]==20
    return True
