from __future__ import annotations
import html, json, re, hashlib
from datetime import datetime, timezone
from fastapi import Request, Query
from fastapi.responses import HTMLResponse, JSONResponse

VERSION="2.0.0-ALLIANCE-BABY-AUTOMATIC-EXAMINER"

def _app(core): return getattr(core,"app",None) or core
def _engine(core): return getattr(core,"engine",None)
def _login(core,req):
    fn=getattr(core,"need_login",None)
    return fn(req) if fn else "team"

def _find_req(engine,rid):
    import alliance_primary_workspace_v730 as ws
    try: return ws._requirement(engine,rid)
    except Exception: return None

def _run_saved(engine,rid):
    import alliance_primary_workspace_v730 as ws
    return ws._match_full(engine,rid,150)

def _norm(s): return re.sub(r"\s+"," ",str(s or "").strip()).upper()

def _extract_location(raw):
    import alliance_micromarket_knowledge_v1 as geo
    n=_norm(raw)
    candidates=[]
    for a,canonical in getattr(geo,"ALIASES",{}).items():
        candidates.append((str(a),str(canonical)))
    for x in getattr(geo,"MARKETS",[]):
        candidates.append((str(x[1]),str(x[1])))
    candidates.sort(key=lambda x:len(x[0]),reverse=True)
    for token,canonical in candidates:
        if re.search(r"(?<!\w)"+re.escape(_norm(token))+r"(?!\w)",n):
            m=geo.market(canonical)
            return canonical,(m[2] if m else "")
    return "",""

def _detect_transaction(raw):
    n=_norm(raw)
    if any(x in n for x in (" FOR SALE"," SALE ","BUY ","PURCHASE","TO BUY","SALE PROPERTY","SELL")) or n.startswith("SALE "):
        return "SALE"
    if any(x in n for x in (" RENT","LEASE","LEASING","TO LET","ON RENT")):
        return "RENT"
    return ""

def _detect_use(raw):
    n=_norm(raw)
    if any(x in n for x in ("RESTAURANT","CAFE","CAFÉ","F&B","FNB","BAR","LOUNGE","QSR","CLOUD KITCHEN","FOOD OUTLET")): return "FNB"
    if any(x in n for x in ("RETAIL","SHOP","SHOWROOM","STORE","BRAND OUTLET")): return "RETAIL"
    if any(x in n for x in ("OFFICE","CORPORATE OFFICE","WORKSPACE","BUSINESS CENTRE","BUSINESS CENTER")): return "OFFICE"
    if any(x in n for x in ("BANQUET","MARRIAGE HALL","WEDDING HALL")): return "BANQUET"
    if any(x in n for x in ("VILLA","APARTMENT","FLAT","RESIDENTIAL","HOUSE","PENTHOUSE","BUILDER FLOOR")): return "RESIDENTIAL"
    return "GENERAL"

def _detect_area(raw):
    pats=[
      r"(\d[\d,]*(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|square\s*feet)",
      r"\b(\d{3,6})\s*(?:sft|sf)\b"
    ]
    for pat in pats:
        m=re.search(pat,str(raw or ""),re.I)
        if m:
            try:return float(m.group(1).replace(",",""))
            except Exception: pass
    return None

def _parse_free_text(raw):
    loc,city=_extract_location(raw)
    tx=_detect_transaction(raw)
    use=_detect_use(raw)
    # Existing reasoning brain may improve detection, but never overrules explicit deterministic facts.
    try:
        import alliance_authentic_reasoning_orchestrator_v1 as brain
        interpreted=brain.interpret({"raw_text":raw,"location":loc,"locality":loc,"city":city}) or {}
        if not tx and interpreted.get("transaction") in {"RENT","SALE"}: tx=interpreted["transaction"]
        if use=="GENERAL" and interpreted.get("use"): use=str(interpreted["use"]).upper()
    except Exception:
        pass
    return {"canonical_id":"ADHOC","raw_text":raw,"locality":loc,"location":loc,"city":city,
            "transaction_type":tx,"area_sqft":_detect_area(raw),
            "property_type":"","category":use,"purpose":use,
            "promotion_status":"ADHOC_QUERY","verification_status":"ADHOC_QUERY"}

def _clean_record(p):
    cr=p.get("clean_record")
    return cr if isinstance(cr,dict) else {}

def _pick(p,names):
    cr=_clean_record(p)
    for src in (p,cr):
        low={str(k).lower():k for k in src.keys()}
        for n in names:
            k=low.get(str(n).lower())
            if k is not None:
                v=src.get(k)
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

    if use=="FNB":
        strong=[x for x in ("RESTAURANT","CAFE","CAFÉ","F&B","FNB","FOOD","BAR","LOUNGE","QSR","KITCHEN") if x in t]
        generic=[x for x in ("COMMERCIAL","SHOP","SHOWROOM","RETAIL SPACE","COMMERCIAL SPACE") if x in t]
        if residential and not strong:return "REJECT_RESIDENTIAL",["RESIDENTIAL_ONLY"]
        if strong:return "EXPLICIT_FIT",strong[:5]
        if generic:return "POSSIBLE_COMMERCIAL",generic[:5]
        return "UNKNOWN",[]

    if use=="RETAIL":
        strong=[x for x in ("RETAIL","SHOP","SHOWROOM","STORE","MALL") if x in t]
        if residential and not strong:return "REJECT_RESIDENTIAL",["RESIDENTIAL_ONLY"]
        if strong:return "EXPLICIT_FIT",strong[:5]
        if "COMMERCIAL" in t:return "POSSIBLE_COMMERCIAL",["COMMERCIAL"]
        return "UNKNOWN",[]

    if use=="OFFICE":
        strong=[x for x in ("OFFICE","CORPORATE","BUSINESS CENTRE","BUSINESS CENTER","WORKSPACE") if x in t]
        if residential and not strong:return "REJECT_RESIDENTIAL",["RESIDENTIAL_ONLY"]
        if strong:return "EXPLICIT_FIT",strong[:5]
        if "COMMERCIAL" in t:return "POSSIBLE_COMMERCIAL",["COMMERCIAL"]
        return "UNKNOWN",[]

    if use=="BANQUET":
        strong=[x for x in ("BANQUET","MARRIAGE HALL","WEDDING HALL","EVENT VENUE") if x in t]
        if residential and not strong:return "REJECT_RESIDENTIAL",["RESIDENTIAL_ONLY"]
        if strong:return "EXPLICIT_FIT",strong[:5]
        if "COMMERCIAL" in t:return "POSSIBLE_COMMERCIAL",["COMMERCIAL"]
        return "UNKNOWN",[]

    if use=="RESIDENTIAL":
        return ("EXPLICIT_FIT",["RESIDENTIAL"]) if residential else ("UNKNOWN",[])
    return "UNKNOWN",[]

def _area_band(req,p):
    ra=req.get("area_sqft");pa=p.get("area_sqft")
    if not ra or not pa:return "UNKNOWN",None
    try: diff=abs(float(pa)-float(ra))/max(float(ra),1)
    except Exception:return "UNKNOWN",None
    pct=round(diff*100,1)
    if diff<=0.15:return "STRONG",pct
    if diff<=0.30:return "GOOD",pct
    if diff<=0.50:return "BROAD",pct
    return "OUTSIDE",pct

def _workflow_truth(p):
    v=str(p.get("verification_status") or "UNVERIFIED").upper()
    a=str(p.get("availability_status") or "UNKNOWN").upper()
    if v=="VERIFIED" and a=="AVAILABLE": return "VERIFIED_AVAILABLE",100
    if v=="VERIFIED": return "VERIFIED_AVAILABILITY_UNKNOWN",65
    if a=="AVAILABLE": return "UNVERIFIED_AVAILABILITY_CLAIM",40
    return "NEEDS_VERIFICATION",20

def _location(p): return _pick(p,("locality","location","primary_location","area","micro_market")) or p.get("city") or "Location not captured"
def _ptype(p): return _pick(p,("property_type","subtype","category","property_category","asset_type","type")) or ""
def _amount(p): return p.get("rent_amount") or p.get("sale_amount") or p.get("price_raw") or _pick(p,("rent","rent_amount","asking_rent","amount","price")) or ""
def _description(p):
    v=_pick(p,("description","details","remarks","property_details","message_text","raw_text","title"))
    if isinstance(v,(dict,list)): return json.dumps(v,ensure_ascii=False,default=str)
    return str(v or "")

def _fingerprint(p):
    phone=_pick(p,("phone","contact_number","mobile","broker_phone","owner_phone")) or ""
    basis="|".join(str(x or "").strip().lower() for x in (_location(p),p.get("transaction_type"),p.get("area_sqft"),_amount(p),_ptype(p),phone,_description(p)[:120]))
    return hashlib.sha1(basis.encode("utf-8","ignore")).hexdigest()

def _hard_eligible(req,p):
    if req.get("transaction_type") and p.get("transaction_type") and req["transaction_type"]!=p["transaction_type"]:
        return False,"TRANSACTION_MISMATCH"
    ab,_=_area_band(req,p)
    if req.get("area_sqft") and ab=="OUTSIDE":
        return False,"AREA_OUTSIDE_50_PERCENT"
    uf,_=_use_fit(req,p)
    if uf=="REJECT_RESIDENTIAL":
        return False,"USE_REJECT_RESIDENTIAL"
    if str(p.get("availability_status") or "").upper() in {"UNAVAILABLE","INACTIVE"}:
        return False,"NOT_AVAILABLE"
    return True,"ELIGIBLE"

def _run_adhoc(engine,req,limit=150):
    import alliance_master_integration_v720 as v720
    tx=req.get("transaction_type") or ""
    props=v720._search_properties(engine,tx=tx,limit=4000)
    exact=[]; same_city=[]; broader=[]; seen=set()
    rl=(req.get("locality") or "").strip().lower()
    rc=(req.get("city") or "").strip().lower()

    for p in props:
        fp=_fingerprint(p)
        if fp in seen: continue
        seen.add(fp)
        eligible,_=_hard_eligible(req,p)
        if not eligible: continue

        uf,ue=_use_fit(req,p)
        ab,adiff=_area_band(req,p)
        try: base,reasons=v720._score(req,p)
        except Exception: base,reasons=0,[]

        pl=str(_location(p) or "").strip().lower()
        pc=(p.get("city") or "").strip().lower()
        if rl and pl and (rl in pl or pl in rl): tier="EXACT_LOCALITY"; bonus=10
        elif rc and pc and rc==pc: tier="SAME_CITY_ALTERNATIVE"; bonus=3
        else: tier="TRANSACTION_AREA_ALTERNATIVE"; bonus=0

        use_bonus=8 if uf=="EXPLICIT_FIT" else 2 if uf=="POSSIBLE_COMMERCIAL" else 0
        area_bonus={"STRONG":8,"GOOD":5,"BROAD":2,"UNKNOWN":0}.get(ab,0)
        relevance=min(100,float(base)+bonus+use_bonus+area_bonus)
        item={"score":relevance,"relevance_score":relevance,"base_score":base,"tier":tier,"match_tier":tier,
              "reasons":list(reasons),"property":p,"use_fit":uf,"use_evidence":ue,
              "area_band":ab,"area_diff_pct":adiff}
        (exact if tier=="EXACT_LOCALITY" else same_city if tier=="SAME_CITY_ALTERNATIVE" else broader).append(item)

    use_rank={"EXPLICIT_FIT":3,"POSSIBLE_COMMERCIAL":2,"UNKNOWN":1}
    area_rank={"STRONG":4,"GOOD":3,"BROAD":2,"UNKNOWN":1}
    for bucket in (exact,same_city,broader):
        bucket.sort(key=lambda x:(_workflow_truth(x["property"])[1],use_rank.get(x["use_fit"],0),area_rank.get(x["area_band"],0),x["relevance_score"]),reverse=True)
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
        eligible,_=_hard_eligible(req,p)
        if not eligible: continue

        truth,trust=_workflow_truth(p)
        uf,ue=_use_fit(req,p)
        ab,adiff=_area_band(req,p)
        tier=m.get("tier") or m.get("match_tier") or "UNKNOWN"
        relevance=float(m.get("relevance_score") or m.get("score") or 0)
        out.append({"canonical_id":p.get("canonical_id"),"location":_location(p),"city":p.get("city"),
                    "area_sqft":p.get("area_sqft_display") or p.get("area_sqft"),
                    "transaction":p.get("transaction_type"),"property_type":_ptype(p),
                    "amount":_amount(p),"description":_description(p),"tier":tier,
                    "relevance_score":relevance,"trust_score":trust,"truth":truth,
                    "use_fit":uf,"use_evidence":ue,"area_band":ab,"area_diff_pct":adiff,
                    "client_safe":truth=="VERIFIED_AVAILABLE" and uf=="EXPLICIT_FIT" and ab in {"STRONG","GOOD","BROAD","UNKNOWN"}})
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
    return {"decision":decision,"exact_safe":exact_safe,"exact_verify":exact_verify,"alternative_safe":alternative_safe,"alternatives":alternatives,"all":rows}

def _diagnostics(a):
    rows=a["all"]
    return {"eligible_unique":len(rows),"client_safe":sum(x["client_safe"] for x in rows),
            "explicit_use_fit":sum(x["use_fit"]=="EXPLICIT_FIT" for x in rows),
            "possible_commercial":sum(x["use_fit"]=="POSSIBLE_COMMERCIAL" for x in rows),
            "unknown_use":sum(x["use_fit"]=="UNKNOWN" for x in rows),
            "strong_area":sum(x["area_band"]=="STRONG" for x in rows),
            "good_area":sum(x["area_band"]=="GOOD" for x in rows),
            "broad_area":sum(x["area_band"]=="BROAD" for x in rows)}

def _search_plan(req,a):
    loc=req.get("locality") or req.get("location") or "requested market"
    use=req.get("category") or req.get("purpose") or "property"
    plan=[]
    if a["exact_verify"]:
        plan.append({"priority":1,"action":"VERIFY_EXACT_CANDIDATES","why":f"Verify source, current availability and explicit {use} suitability for best area-matched {loc} candidates."})
    plan += [
      {"priority":2,"action":"CHECK_MICROMARKETS","why":f"Search comparable {use} markets near {loc} if exact candidates fail."},
      {"priority":3,"action":"SEARCH_INTERNAL_EVIDENCE","why":"Search WhatsApp, magazine, newspaper and manual evidence."},
      {"priority":4,"action":"RUN_PROPERTY_DISCOVERY","why":"Search public sources as candidate evidence only."},
      {"priority":5,"action":"RUN_COMMERCIAL_INTELLIGENCE","why":"Search supply and occupier intelligence."},
      {"priority":6,"action":"HUMAN_VERIFY_AND_PROMOTE","why":"Only verified + available + explicit-use-fit inventory becomes client-safe."}
    ]
    return plan

def _answer(req,a,micro):
    loc=req.get("locality") or req.get("location") or "requested location"
    if a["exact_safe"]:
        src=a["exact_safe"]; lines=[f"I found {len(src)} VERIFIED + AVAILABLE + EXPLICIT-USE exact result(s) for {loc}."]
    elif a["exact_verify"]:
        src=a["exact_verify"]; lines=[f"I found {len(src)} area-eligible exact-location candidate(s) for {loc}, but NONE is client-safe yet.",
                                     "Generic COMMERCIAL evidence is only POSSIBLE_COMMERCIAL, not proof of restaurant/F&B suitability."]
    elif a["alternative_safe"]:
        src=a["alternative_safe"]; lines=[f"No client-safe exact result in {loc}. I found {len(src)} verified alternative(s)."]
    elif a["alternatives"]:
        src=a["alternatives"]; lines=[f"No client-safe exact result in {loc}. Alternative candidates exist but still need verification."]
    else:
        src=[]; lines=[f"No acceptable Master candidate is currently available for {loc}."]

    for x in src[:5]:
        ev=", ".join(x["use_evidence"]) if x["use_evidence"] else "no explicit use evidence"
        lines.append(f"• {x['location']} | {x['area_sqft'] or 'area unknown'} sqft | area {x['area_band']} | relevance {x['relevance_score']} | trust {x['trust_score']} | use {x['use_fit']} ({ev})")

    if not a["exact_safe"]:
        sug=(micro or {}).get("suggestions") or []
        if sug:
            lines.append("Comparable markets to investigate next:")
            for x in sug[:5]: lines.append(f"• {x['location']} | {x['market_type']} | ~{x['distance_km']} km")
    lines.append("Truth rule: exact locality alone is not enough. Client-safe requires area-fit + VERIFIED + AVAILABLE + EXPLICIT_FIT.")
    return "\n".join(lines)

def analyze(engine,user_input):
    s=str(user_input or "").strip()
    if not s:return {"status":"EMPTY","message":"Type a requirement, for example: Need 2,000 sqft restaurant on lease in Saket."}
    saved=_find_req(engine,s)
    if saved:
        req,matches=_run_saved(engine,s);mode="VERIFIED_MASTER_REQUIREMENT"
    else:
        req=_parse_free_text(s);req,matches=_run_adhoc(engine,req,150);mode="NATURAL_LANGUAGE_QUERY"
    a=_assess(req,matches);micro=_micromarkets(req)
    return {"status":"OK","version":VERSION,"generated_at":datetime.now(timezone.utc).isoformat(),
            "source_mode":mode,"requirement":req,"raw_match_count":len(matches),
            "eligible_unique_count":len(a["all"]),"assessment":a,"diagnostics":_diagnostics(a),
            "micromarkets":micro,"search_plan":_search_plan(req,a),"answer":_answer(req,a,micro)}

def training_exam():
    # Deterministic CRE examiner. These tests are intentionally independent of live inventory.
    tests=[]
    def check(name,ok,detail=""):
        tests.append({"name":name,"pass":bool(ok),"detail":detail})
    qcases=[
      ("Need 2000 sqft restaurant on lease in Saket","RENT","FNB",2000.0),
      ("Need 1200 sqft retail shop for rent in Khan Market","RENT","RETAIL",1200.0),
      ("Looking to buy 3000 sqft office in BKC","SALE","OFFICE",3000.0),
      ("Need 5000 sqft banquet hall on lease in Rajouri Garden","RENT","BANQUET",5000.0),
      ("Want villa for sale in Siolim 4000 sqft","SALE","RESIDENTIAL",4000.0),
      ("Need 1800 sqft cafe in Bandra West on rent","RENT","FNB",1800.0),
      ("Need 2500 sqft office on lease in DLF Cyber City","RENT","OFFICE",2500.0),
      ("Looking for 900 sqft showroom for rent in Sector 18 Noida","RENT","RETAIL",900.0),
    ]
    for q,tx,use,area in qcases:
        check("parse:"+q,_detect_transaction(q)==tx and _detect_use(q)==use and _detect_area(q)==area)
    req={"category":"FNB","purpose":"FNB","area_sqft":2000,"transaction_type":"RENT"}
    check("FNB generic commercial is not proof",_use_fit(req,{"property_type":"Commercial"})[0]=="POSSIBLE_COMMERCIAL")
    check("FNB restaurant explicit",_use_fit(req,{"description":"Restaurant space with kitchen"})[0]=="EXPLICIT_FIT")
    check("FNB residential reject",_use_fit(req,{"property_type":"Residential apartment"})[0]=="REJECT_RESIDENTIAL")
    check("area 1800 strong",_area_band(req,{"area_sqft":1800})[0]=="STRONG")
    check("area 1400 good",_area_band(req,{"area_sqft":1400})[0]=="GOOD")
    check("area 1000 broad",_area_band(req,{"area_sqft":1000})[0]=="BROAD")
    check("area 700 outside",_area_band(req,{"area_sqft":700})[0]=="OUTSIDE")
    check("hard gate removes 700",not _hard_eligible(req,{"area_sqft":700,"transaction_type":"RENT","property_type":"Commercial"})[0])
    check("hard gate transaction mismatch",not _hard_eligible(req,{"area_sqft":2000,"transaction_type":"SALE","property_type":"Commercial"})[0])
    check("verified available trust 100",_workflow_truth({"verification_status":"VERIFIED","availability_status":"AVAILABLE"})==("VERIFIED_AVAILABLE",100))
    check("unverified unknown trust 20",_workflow_truth({"verification_status":"UNVERIFIED","availability_status":"UNKNOWN"})==("NEEDS_VERIFICATION",20))
    passed=sum(x["pass"] for x in tests)
    return {"version":VERSION,"tested":len(tests),"passed":passed,"failed":len(tests)-passed,"status":"PASS" if passed==len(tests) else "FAIL","tests":tests}

def _rows_html(items):
    rows=[]
    for x in items[:15]:
        ev=", ".join(x.get("use_evidence") or []) or "—"
        desc=x.get("description") or ""
        if len(desc)>160:desc=desc[:157]+"..."
        vals=(x.get("location"),x.get("area_sqft"),x.get("area_band"),x.get("property_type") or "—",x.get("transaction"),
              x.get("amount") or "—",x.get("tier"),x.get("relevance_score"),x.get("trust_score"),x.get("truth"),
              x.get("use_fit"),ev,desc or "—")
        rows.append("<tr>"+"".join("<td>"+html.escape(str(v if v not in (None,"") else "—"))+"</td>" for v in vals)+"</tr>")
    return "".join(rows)

def _page(data,user_input=""):
    if data.get("status")!="OK":
        body=f"<div class=card>{html.escape(data.get('message','Unable to analyse'))}</div>"
    else:
        a=data["assessment"];req=data["requirement"];d=data["diagnostics"]
        shortlist=a["exact_safe"] or a["exact_verify"] or a["alternative_safe"] or a["alternatives"]
        micro=(data.get("micromarkets") or {}).get("suggestions") or []
        mrows="".join(f"<tr><td>{html.escape(str(x.get('location') or ''))}</td><td>{html.escape(str(x.get('market_type') or ''))}</td><td>{html.escape(str(x.get('distance_km') or ''))}</td><td>{html.escape(str(x.get('reason') or ''))}</td></tr>" for x in micro)
        parsed=f"Location: {req.get('locality') or 'not identified'} · City: {req.get('city') or 'not identified'} · Transaction: {req.get('transaction_type') or 'unknown'} · Area: {req.get('area_sqft') or 'not specified'} · Use: {req.get('category') or 'general'}"
        diag=" · ".join(f"{k.replace('_',' ').title()}: {v}" for k,v in d.items())
        body=f"""<div class=card><h3>Baby's Answer</h3><pre>{html.escape(data['answer'])}</pre></div>
<div class=card><b>Input mode:</b> {html.escape(data['source_mode'])}<br><b>Parsed:</b> {html.escape(parsed)}<br><b>Decision:</b> {html.escape(a['decision'])}<br><b>Diagnostics:</b> {html.escape(diag)}</div>
<div class=card><h3>Evidence-backed Shortlist</h3><table><tr><th>Location</th><th>Area</th><th>Area Fit</th><th>Type</th><th>Txn</th><th>Amount</th><th>Tier</th><th>Relevance</th><th>Trust</th><th>Truth</th><th>Use Fit</th><th>Use Evidence</th><th>Description</th></tr>{_rows_html(shortlist)}</table></div>
<div class=card><h3>Micro-market intelligence</h3><table><tr><th>Market</th><th>Type</th><th>Approx km</th><th>Reason</th></tr>{mrows}</table></div>
<div class=card><h3>Automatic Next Actions</h3><pre>{html.escape(json.dumps(data['search_plan'],ensure_ascii=False,indent=2))}</pre></div>"""
    return f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Alliance Baby</title>
<style>body{{font-family:Arial;background:#f4f7fb;color:#172033;margin:0;padding:20px}}.card{{background:white;border:1px solid #dfe6ee;border-radius:12px;padding:16px;margin:12px 0;overflow:auto}}input,button{{padding:11px}}input{{width:min(850px,75vw)}}table{{border-collapse:collapse;width:max-content;min-width:100%;font-size:12px}}th,td{{border:1px solid #aaa;padding:8px;text-align:left;vertical-align:top;max-width:300px}}pre{{white-space:pre-wrap;word-break:break-word}}</style></head><body>
<p><a href="javascript:history.back()">← Previous Page</a> · <a href="/alliance/primary">Dashboard</a> · <a href="/alliance/baby/training-audit">Training Audit</a></p>
<h2>Alliance Baby · CRE Copilot</h2><p>Automatically examined · hard-gated · evidence-aware · relevance ≠ trust</p>
<form method=get action='/alliance/baby'><input name=q value="{html.escape(user_input,quote=True)}" placeholder='Example: Need 2,000 sqft restaurant on lease in Saket' autofocus><button>Think & Match</button></form>{body}</body></html>"""

def _exam_page():
    r=training_exam()
    rows="".join(f"<tr><td>{html.escape(x['name'])}</td><td>{'PASS' if x['pass'] else 'FAIL'}</td><td>{html.escape(x.get('detail',''))}</td></tr>" for x in r["tests"])
    return f"""<!doctype html><html><head><meta charset=utf-8><title>Baby Training Audit</title><style>body{{font-family:Arial;padding:20px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccc;padding:8px;text-align:left}}</style></head><body>
<p><a href="/alliance/baby">← Baby</a></p><h2>Alliance Baby · Automatic Training Audit</h2>
<p><b>Status:</b> {r['status']} · <b>Tested:</b> {r['tested']} · <b>Passed:</b> {r['passed']} · <b>Failed:</b> {r['failed']}</p>
<table><tr><th>Question / Rule</th><th>Result</th><th>Detail</th></tr>{rows}</table></body></html>"""

def register(core):
    app=_app(core);eng=_engine(core)
    owned={"/alliance/baby","/api/alliance/baby","/alliance/baby/training-audit","/api/alliance/baby/training-audit"}
    app.router.routes[:]=[r for r in app.router.routes if getattr(r,"path",None) not in owned]
    @app.get("/alliance/baby",response_class=HTMLResponse)
    async def baby(req:Request,q:str=Query(default=""),requirement_id:str=Query(default="")):
        _login(core,req);s=(q or requirement_id or "").strip()
        return HTMLResponse(_page(analyze(eng,s),s))
    @app.get("/api/alliance/baby")
    async def baby_api(req:Request,q:str=Query(default=""),requirement_id:str=Query(default="")):
        _login(core,req);return JSONResponse(analyze(eng,(q or requirement_id or "").strip()))
    @app.get("/alliance/baby/training-audit",response_class=HTMLResponse)
    async def baby_training(req:Request):
        _login(core,req);return HTMLResponse(_exam_page())
    @app.get("/api/alliance/baby/training-audit")
    async def baby_training_api(req:Request):
        _login(core,req);return JSONResponse(training_exam())
    return {"status":"REGISTERED","version":VERSION,"routes":list(owned),"automatic_examiner":True}

def self_test():
    r=training_exam()
    assert r["status"]=="PASS",r
    return True
