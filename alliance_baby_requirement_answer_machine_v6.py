from __future__ import annotations
import html,json,re
from datetime import datetime,timezone
from decimal import Decimal
from fastapi import Request
from fastapi.responses import HTMLResponse,JSONResponse
from pydantic import BaseModel,Field
VERSION="6.3.0-ALLIANCE-BABY-COMMERCIAL-MARKET-BRAIN"
MAX_OVER_BUDGET_PCT=.15
class AnswerInput(BaseModel):
    requirement:str=Field(min_length=5)
    limit_per_section:int=Field(default=8,ge=1,le=25)
def _app(core):return getattr(core,"app",None) or core
def _engine(core):return getattr(core,"engine",None)
def _login(core,r):
    fn=getattr(core,"need_login",None);return fn(r) if fn else "team"
def _n(v):return re.sub(r"\s+"," ",str(v or "").strip()).upper()
def _clean(v):return re.sub(r"\s+"," ",str(v or "").strip())
def _num(v):
    try:return float(str(v).replace(",","").strip())
    except:return None
def _money_to_rupees(s):
    m=re.search(r"(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?)\s*(crore|cr|lakh|lac|lacs|lakhs|k|thousand)?",str(s or ""),re.I)
    if not m:return None
    n=_num(m.group(1));u=(m.group(2) or "").lower()
    if n is None:return None
    if u in {'crore','cr'}:return n*10000000
    if u in {'lakh','lac','lacs','lakhs'}:return n*100000
    if u in {'k','thousand'}:return n*1000
    return n
def _budget(raw):
    s=str(raw or "")
    market=bool(re.search(r"\b(?:current|prevailing)?\s*market\s+rate\b",s,re.I))
    monthly=bool(re.search(
        r"(?:/|\bper\s+)\s*(?:month|monthly|mo\b)|\bmonthly\b|\bp\.?m\.?\b",
        s,re.I,
    ))
    yearly=bool(re.search(
        r"(?:/|\bper\s+)\s*(?:year|annum)|\bannual(?:ly)?\b|\bp\.?a\.?\b",
        s,re.I,
    ))
    period="MONTH" if monthly else "YEAR" if yearly else ""
    pats=[
        r"(?:budget|upto|up to|max(?:imum)?|within)\s*(?::|-)?\s*(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?)\s*(crore|cr|lakh|lac|lacs|lakhs|k|thousand)",
        r"(?:₹|rs\.?|inr)\s*(\d[\d,]*(?:\.\d+)?)\s*(crore|cr|lakh|lac|lacs|lakhs|k|thousand)",
        r"\b(\d[\d,]*(?:\.\d+)?)\s*(crore|cr|lakh|lac|lacs|lakhs|k|thousand)\b",
    ]
    for p in pats:
        m=re.search(p,s,re.I)
        if m:
            return {
                "budget_rupees":_money_to_rupees(m.group(1)+" "+m.group(2)),
                "budget_raw":_clean(m.group(0)),
                "budget_market_rate":market,
                "budget_period":period,
            }
    return {
        "budget_rupees":None,
        "budget_raw":"MARKET_RATE" if market else "",
        "budget_market_rate":market,
        "budget_period":period,
    }

def _area(raw):
    pats=[('SQYD',r"(\d[\d,]*(?:\.\d+)?)\s*(?:sq\.?\s*y(?:d|ards?)|sqyd|square\s*yards?|yards?)\b",9),('SQFT',r"(\d[\d,]*(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|square\s*feet|sft)\b",1),('SQM',r"(\d[\d,]*(?:\.\d+)?)\s*(?:sq\.?\s*m(?:tr|etre|eter)?s?|sqm|square\s*met(?:re|er)s?)\b",10.7639104167),('ACRE',r"(\d[\d,]*(?:\.\d+)?)\s*acres?\b",43560)]
    for unit,p,f in pats:
        m=re.search(p,str(raw or ''),re.I)
        if m:
            n=_num(m.group(1))
            return {'area_original':n,'area_unit':unit,'area_sqft':round(n*f,2),'area_raw':_clean(m.group(0))}
    return {'area_original':None,'area_unit':'','area_sqft':None,'area_raw':''}
def _location(raw):
    try:
        import alliance_baby_cre_copilot_v1 as b
        loc,city=b._extract_location(raw)
        if loc:return loc,city
    except:pass
    m=re.search(r"(?:location|loc)\s*:\s*([A-Za-z0-9 .&'/-]{2,60})",str(raw or ''),re.I)
    if m:return _clean(re.split(r"[\n\r•|]",m.group(1))[0]),''
    return '',''
def _tx(raw):
    s=" "+_n(raw)+" "
    sale_patterns=(
        r"\bREADY\s+BUYER\b",
        r"\bSERIOUS\s+BUYER\b",
        r"\bBUYER\b",
        r"\bTO\s+BUY\b",
        r"\bPURCHASE\b",
        r"\bFOR\s+SALE\b",
        r"\bSALE\b",
        r"\bBUY\b",
    )
    rent_patterns=(
        r"\bRENT\b",
        r"\bRENTAL\b",
        r"\bLEASE\b",
        r"\bLEASING\b",
        r"\bTO\s+LET\b",
    )
    sale=any(re.search(p,s,re.I) for p in sale_patterns)
    rent=any(re.search(p,s,re.I) for p in rent_patterns)
    if sale and not rent:return "SALE"
    if rent and not sale:return "RENT"
    return ""

def _cat(raw):
    s=_n(raw)
    if any(x in s for x in ('RESTAURANT','CAFE','CAFÉ','F&B','FNB','BAR','LOUNGE','QSR','CLOUD KITCHEN')):return 'FNB'
    if any(x in s for x in ('GARMENT','APPAREL','FASHION','CLOTHING')):return 'FASHION'
    if any(x in s for x in ('JEWELLERY','JEWELRY','GOLD','DIAMOND')):return 'JEWELLERY'
    if 'OFFICE' in s:return 'OFFICE'
    if any(x in s for x in ('RETAIL','SHOP','SHOWROOM','STORE')):return 'RETAIL'
    if any(x in s for x in ('VILLA','APARTMENT','FLAT','RESIDENTIAL','HOUSE','PENTHOUSE','BUILDER FLOOR','STILT')):return 'RESIDENTIAL'
    if re.search(r"\bSQ\.?\s*Y(?:D|ARDS?)\b|\bSQYD\b",s) and _tx(raw)=='SALE':return 'RESIDENTIAL'
    return 'GENERAL'
def _prefs(raw):
    s=_n(raw);out=[]
    for k,toks in [('STILT',('STILT',)),('GROUND_FLOOR',('GROUND FLOOR',' GF ')),('FIRST_FLOOR',('FIRST FLOOR','1ST FLOOR')),('CORNER',('CORNER',)),('PARKING',('PARKING',)),('MAIN_ROAD',('MAIN ROAD','MAIN MARKET'))]:
        if any(t in ' '+s+' ' for t in toks):out.append(k)
    return out
def _money_label(v):
    try:
        n=float(v)
    except (TypeError,ValueError):
        return ""
    if n>=10000000:
        return "₹"+("{:g}".format(round(n/10000000,2)))+" crore"
    if n>=100000:
        return "₹"+("{:g}".format(round(n/100000,2)))+" lakh"
    if n>=1000:
        return "₹"+("{:g}".format(round(n/1000,2)))+"k"
    return "₹"+("{:,.0f}".format(n))

def _budget_display(req):
    if req.get("budget_market_rate"):
        return "Market rate"
    value=req.get("budget_rupees")
    if not value:
        return "not captured"
    label=_money_label(value)
    if req.get("transaction_type")=="RENT":
        period=req.get("budget_period") or "MONTH"
        if period=="MONTH":
            return label+"/month"
        if period=="YEAR":
            return label+"/year"
    return label

def _rent_economics(req):
    if req.get("transaction_type")!="RENT":
        return {
            "rent_budget_monthly":None,
            "rent_budget_per_sqft_month":None,
            "rent_economics_status":"NOT_APPLICABLE",
        }
    budget=req.get("budget_rupees")
    area=req.get("area_sqft")
    period=req.get("budget_period") or "MONTH"
    if not budget:
        return {
            "rent_budget_monthly":None,
            "rent_budget_per_sqft_month":None,
            "rent_economics_status":"BUDGET_MISSING",
        }
    monthly=float(budget)
    if period=="YEAR":
        monthly=monthly/12.0
    per_sqft=None
    if area:
        try:
            per_sqft=round(monthly/float(area),2)
        except Exception:
            per_sqft=None
    return {
        "rent_budget_monthly":round(monthly,2),
        "rent_budget_per_sqft_month":per_sqft,
        "rent_economics_status":"COMPLETE" if per_sqft is not None else "AREA_MISSING",
    }
def _requirement_quality(req):
    missing=[]
    if not req.get("locality"):missing.append("location")
    if not req.get("transaction_type"):missing.append("transaction")
    if _n(req.get("category")) in {"","GENERAL"}:missing.append("property use/category")
    if not req.get("area_sqft"):missing.append("area")
    if not req.get("budget_rupees") and not req.get("budget_market_rate"):missing.append("budget")
    captured=[x for x in ("location","transaction","property use/category","area","budget") if x not in missing]
    pct=round(len(captured)*100/5)
    quality="COMPLETE" if not missing else "PARTIAL" if pct>=60 else "INCOMPLETE"
    return {
        "requirement_quality":quality,
        "requirement_completeness_pct":pct,
        "missing_fields":missing,
        "captured_fields":captured,
        "needs_clarification":bool(missing),
    }

def _market_fit(req,x):
    cat=_n(req.get("category") or "GENERAL")
    market_type=_n(x.get("market_type"))
    source_fit=_n(x.get("fit"))
    source_reason=_clean(x.get("reason"))

    if cat in {"","GENERAL"}:
        return {
            "market_fit":"UNASSESSED",
            "market_fit_score":0,
            "market_fit_reason":"Category/use missing; commercial equivalence is not asserted.",
        }

    token_map={
        "FNB":("FNB","RESTAURANT","CAFE","TOURISM","LIFESTYLE","HIGH_STREET","DESTINATION"),
        "FASHION":("RETAIL","HIGH_STREET","MALL","PREMIUM","DESTINATION"),
        "JEWELLERY":("RETAIL","HIGH_STREET","MALL","PREMIUM","LUXURY"),
        "RETAIL":("RETAIL","HIGH_STREET","MALL","PREMIUM","DESTINATION"),
        "OFFICE":("OFFICE","CBD","DISTRICT_CENTRE","CORRIDOR","TECH"),
        "RESIDENTIAL":("RESIDENTIAL","VILLA","LIFESTYLE","PREMIUM","MIXED"),
    }
    wanted=token_map.get(cat,(cat,))
    hits=[tok for tok in wanted if tok in market_type]

    score=25*len(hits)
    if source_fit=="USE_MATCH":score+=35
    if "compatible" in source_reason.lower():score+=15
    score=min(100,max(0,score))

    label="STRONG" if score>=70 else "GOOD" if score>=45 else "WEAK"
    why=[]
    if hits:why.append("market type supports "+", ".join(hits[:3]))
    if source_fit=="USE_MATCH":why.append("micro-market engine reports use compatibility")
    if not why:why.append("geographic proximity only; use-case equivalence is weak")

    return {
        "market_fit":label,
        "market_fit_score":score,
        "market_fit_reason":"; ".join(why),
    }
def _split(raw):
    text=str(raw or '').strip();ms=list(re.finditer(r"(?:^|\n|\r|\s)(?:🏠\s*)?Requirement\s*(\d+)\s*:",text,re.I))
    if len(ms)<2:return [text] if text else []
    prefix=text[:ms[0].start()].strip();loc,_=_location(prefix);tx=_tx(prefix);out=[]
    for i,m in enumerate(ms):
        seg=text[m.end():(ms[i+1].start() if i+1<len(ms) else len(text))].strip();inherit=[]
        if loc and loc.lower() not in seg.lower():inherit.append('Location: '+loc)
        if tx=='SALE' and not _tx(seg):inherit.append('Ready buyer purchase')
        elif tx=='RENT' and not _tx(seg):inherit.append('Lease requirement')
        out.append(' | '.join(inherit+[seg]))
    return out
def parse_requirement(raw,i=1):
    loc,city=_location(raw)
    a=_area(raw)
    b=_budget(raw)
    c=_cat(raw)
    req={
        "requirement_no":i,
        "raw_text":_clean(raw),
        "locality":loc,
        "location":loc,
        "city":city,
        "transaction_type":_tx(raw),
        "category":c,
        "purpose":c,
        "preferences":_prefs(raw),
        **a,
        **b,
    }
    req.update(_rent_economics(req))
    req.update(_requirement_quality(req))
    return req

def parse_post(raw):return [parse_requirement(x,i+1) for i,x in enumerate(_split(raw))]
def _ptext(p):
    vals=[]
    for k in ('locality','city','property_type','category','description','remarks','price_raw','transaction_type','clean_record'):
        v=p.get(k)
        if v not in (None,'',[],{}):vals.append(json.dumps(v,ensure_ascii=False,default=str) if isinstance(v,(dict,list)) else str(v))
    return _n(' '.join(vals))
def _ploc(p):return _clean(p.get('locality') or p.get('location') or p.get('city') or '')
def _location_tokens(v):
    return [x.strip() for x in re.split(r"[,;/|]+",_clean(v)) if x.strip()]

def _single_market(v):
    parts=_location_tokens(v)
    return parts[0] if len(parts)==1 else ""

def _location_quality(v):
    parts=_location_tokens(v)
    if not parts:return "MISSING"
    if len(parts)>1:return "MULTI_MARKET"
    return "SINGLE_MARKET"

def _same(a,b):
    a=_single_market(a);b=_single_market(b)
    a,b=_n(a),_n(b)
    return bool(a and b and a==b)
def _aband(ra,pa):
    if not ra or not pa:return 'UNKNOWN',None
    try:d=abs(float(pa)-float(ra))/max(float(ra),1)
    except:return 'UNKNOWN',None
    pct=round(d*100,1)
    return ('STRONG' if d<=.15 else 'GOOD' if d<=.30 else 'BROAD' if d<=.50 else 'OUTSIDE'),pct
def _price(p):
    raw=p.get('sale_amount') or p.get('rent_amount') or p.get('price_raw') or ''
    if isinstance(raw,Decimal):return float(raw)
    if isinstance(raw,(int,float)):return float(raw) if float(raw)>=10000 else None
    return _money_to_rupees(raw)
def _bfit(req,p):
    b=req.get('budget_rupees');pr=_price(p)
    if not b:return 'NO_NUMERIC_LIMIT',None,pr
    if not pr:return 'PRICE_UNKNOWN',None,None
    d=(pr-float(b))/max(float(b),1)
    if d<=0:return 'WITHIN_BUDGET',round(d*100,1),pr
    if d<=MAX_OVER_BUDGET_PCT:return 'SLIGHTLY_OVER_BUDGET',round(d*100,1),pr
    return 'OVER_BUDGET',round(d*100,1),pr
def _truth(p):
    v=_n(p.get('verification_status') or 'UNVERIFIED');a=_n(p.get('availability_status') or 'UNKNOWN')
    if v=='VERIFIED' and a=='AVAILABLE':return 'VERIFIED_AVAILABLE',100
    if v=='VERIFIED' and a in {'UNAVAILABLE','INACTIVE'}:return 'VERIFIED_NOT_AVAILABLE',0
    if v=='VERIFIED':return 'VERIFIED_NEEDS_AVAILABILITY_UPDATE',75
    if a=='AVAILABLE':return 'UNVERIFIED_AVAILABILITY_CLAIM',55
    if a in {'UNAVAILABLE','INACTIVE'}:return 'UNVERIFIED_NOT_AVAILABLE',0
    return 'UNVERIFIED_NEEDS_UPDATE',35
def _pfit(req,p):
    prefs=req.get('preferences') or []
    if not prefs:return 'NO_PREFERENCE',[]
    t=' '+_ptext(p)+' ';mp={'STILT':('STILT',),'GROUND_FLOOR':('GROUND FLOOR',' GF '),'FIRST_FLOOR':('FIRST FLOOR','1ST FLOOR'),'CORNER':('CORNER',),'PARKING':('PARKING',),'MAIN_ROAD':('MAIN ROAD','MAIN MARKET')};found=[]
    for pref in prefs:
        if any(tok in t for tok in mp.get(pref,(pref,))):found.append(pref)
    return ('FULL' if len(found)==len(prefs) else 'PARTIAL' if found else 'UNKNOWN'),found
def _nearby(req):
    loc=req.get("locality") or ""
    if not loc:return []
    try:
        import alliance_micromarket_knowledge_v1 as g
        response=g.suggest(loc,req.get("raw_text") or "",30) or {}
        out=[]
        seen=set()
        category=_n(req.get("category") or "GENERAL")
        max_km=3.0 if category=="OFFICE" else 12.0

        for suggestion in response.get("suggestions") or []:
            place=_clean(suggestion.get("location"))
            if not place:continue
            if _location_quality(place)!="SINGLE_MARKET":continue
            if _same(place,loc):continue

            try:
                distance=float(suggestion.get("distance_km"))
            except (TypeError,ValueError):
                continue

            if distance<0 or distance>max_km:continue

            key=_n(place)
            if key in seen:continue
            seen.add(key)

            fit=_market_fit(req,suggestion)
            out.append({
                "location":place,
                "distance_km":round(distance,2),
                "market_type":suggestion.get("market_type"),
                "source_fit":suggestion.get("fit"),
                "source_reason":_clean(suggestion.get("reason")),
                **fit,
            })

        if category in {"","GENERAL"}:
            out.sort(key=lambda z:z["distance_km"])
        else:
            out.sort(key=lambda z:(-z.get("market_fit_score",0),z["distance_km"]))

        return out[:8]
    except Exception:
        return []

def _commercial_market_brain(req):
    loc=req.get("locality") or ""
    if not loc:
        return []
    category=_n(req.get("category") or "GENERAL")
    if category in {"","GENERAL"}:
        return []
    try:
        import alliance_micromarket_knowledge_v1 as g
        response=g.suggest(loc,req.get("raw_text") or "",40) or {}
    except Exception:
        return []
    max_km=3.0 if category=="OFFICE" else 15.0
    out=[]
    seen=set()
    for suggestion in response.get("suggestions") or []:
        place=_clean(suggestion.get("location"))
        if not place or _location_quality(place)!="SINGLE_MARKET":
            continue
        if _same(place,loc):
            continue
        try:
            distance=float(suggestion.get("distance_km"))
        except (TypeError,ValueError):
            continue
        if distance<0 or distance>max_km:
            continue
        key=_n(place)
        if key in seen:
            continue
        seen.add(key)
        fit=_market_fit(req,suggestion)
        source_fit=_n(suggestion.get("fit"))
        out.append({
            "location":place,
            "distance_km":round(distance,2),
            "market_type":suggestion.get("market_type") or "",
            "market_fit":fit.get("market_fit"),
            "market_fit_score":fit.get("market_fit_score",0),
            "market_fit_reason":fit.get("market_fit_reason"),
            "source_fit":source_fit,
            "source_reason":_clean(suggestion.get("reason")),
            "commercially_equivalent":bool(
                source_fit=="USE_MATCH"
                or fit.get("market_fit") in {"STRONG","GOOD"}
            ),
        })
    out.sort(
        key=lambda x:(
            x.get("commercially_equivalent",False),
            x.get("market_fit_score",0),
            -x.get("distance_km",999),
        ),
        reverse=True,
    )
    return out[:10]
def _comparables(engine,req):
    if req.get('category') in {'RESIDENTIAL','GENERAL'}:return []
    try:
        import alliance_baby_market_evidence_collector_v41 as v41
        plan=v41.plan(engine,req) or {};out=[]
        for c in plan.get('recommended_markets') or []:
            d=c.get('v4_decision') or {};e=c.get('evidence') or {}
            out.append({'location':c.get('market'),'score':d.get('score'),'evidence_status':e.get('status'),'evidence_count':e.get('distinct_count'),'reasons':d.get('reasons') or []})
        return out
    except:return []
def _phones(p):
    v=p.get('phones') or []
    if isinstance(v,dict):v=list(v.values())
    if isinstance(v,str):v=re.findall(r"(?:\+?91[\s-]?)?[6-9]\d{9}",v)
    return [str(x) for x in v if str(x).strip()][:5]
def _eligible(req,p):
    pl=_ploc(p)
    if _location_quality(pl)=="MULTI_MARKET":return False
    if req.get('transaction_type') and p.get('transaction_type') and _n(p.get('transaction_type'))!=req['transaction_type']:return False
    tr,_=_truth(p)
    if tr in {'VERIFIED_NOT_AVAILABLE','UNVERIFIED_NOT_AVAILABLE'}:return False
    ab,_=_aband(req.get('area_sqft'),p.get('area_sqft'))
    if req.get('area_sqft') and ab=='OUTSIDE':return False
    bf,_,_=_bfit(req,p)
    if bf=='OVER_BUDGET':return False
    if req.get('category')=='RESIDENTIAL':
        t=_ptext(p);res=any(x in t for x in ('RESIDENTIAL','BUILDER FLOOR','APARTMENT','FLAT','HOUSE','VILLA'))
        commercial=any(x in t for x in ('COMMERCIAL','SHOP','SHOWROOM','OFFICE','RESTAURANT','BANQUET'))
        if commercial and not res:return False
    return True
def _row(req,p,tier,reason):
    tr,trust=_truth(p)
    ab,ad=_aband(req.get("area_sqft"),p.get("area_sqft"))
    bf,bd,pr=_bfit(req,p)
    pf,pfound=_pfit(req,p)

    score=(
        {'EXACT':40,'NEARBY':26,'COMPARABLE':20,'OTHER':5}.get(tier,0)
        +{'STRONG':25,'GOOD':18,'BROAD':8,'UNKNOWN':0}.get(ab,0)
        +{'WITHIN_BUDGET':16,'NO_NUMERIC_LIMIT':4,'PRICE_UNKNOWN':0,'SLIGHTLY_OVER_BUDGET':5}.get(bf,0)
        +{'FULL':9,'PARTIAL':5,'NO_PREFERENCE':2,'UNKNOWN':0}.get(pf,0)
        +(10 if tr=='VERIFIED_AVAILABLE' else 6 if tr.startswith('VERIFIED') else 2)
    )

    if not req.get("area_sqft"):score-=8
    if not req.get("budget_rupees") and not req.get("budget_market_rate"):score-=6
    if _n(req.get("category")) in {"","GENERAL"}:score-=8
    if not req.get("transaction_type"):score-=8
    score=max(0,score)

    if req.get("requirement_quality")!="COMPLETE":
        action="CAPTURE_REQUIREMENT_DETAILS"
    elif tr=="VERIFIED_AVAILABLE":
        action="READY_TO_PITCH"
    else:
        action="VERIFY_AVAILABILITY_AND_DETAILS"

    cr=p.get("clean_record") if isinstance(p.get("clean_record"),dict) else {}

    return {
        "canonical_id":p.get("canonical_id"),
        "location":_ploc(p),
        "city":p.get("city"),
        "area_sqft":p.get("area_sqft"),
        "area_sqyd":p.get("area_sqyd"),
        "property_type":p.get("property_type") or p.get("category") or "",
        "transaction":p.get("transaction_type"),
        "price_raw":p.get("price_raw") or p.get("sale_amount") or p.get("rent_amount") or "",
        "price_rupees":pr,
        "verification_status":p.get("verification_status") or "UNVERIFIED",
        "availability_status":p.get("availability_status") or "UNKNOWN",
        "truth":tr,
        "trust_score":trust,
        "location_tier":tier,
        "market_reason":reason,
        "area_band":ab,
        "area_diff_pct":ad,
        "budget_fit":bf,
        "budget_diff_pct":bd,
        "preference_fit":pf,
        "preferences_found":pfound,
        "match_score":min(100,score),
        "phones":_phones(p),
        "assigned_to":p.get("assigned_to"),
        "action":action,
        "description":_clean(p.get("description") or cr.get("description") or ""),
    }

def match_requirement(engine,req,limit_per_section=8):
    import alliance_master_integration_v720 as master
    props=master._search_properties(
        engine,
        tx=req.get("transaction_type") or "",
        limit=4000,
    )
    nearby=_nearby(req)
    comparable=_comparables(engine,req)
    market_brain=_commercial_market_brain(req)

    nearby_by_location={_n(x["location"]):x for x in nearby}
    comparable_by_location={_n(x["location"]):x for x in comparable}

    verified_exact=[]
    unverified_exact=[]
    nearby_rows=[]
    comparable_rows=[]

    for p in props:
        if not _eligible(req,p):
            continue
        property_location=_ploc(p)
        property_key=_n(property_location)
        market_meta=None

        if req.get("locality") and _same(req["locality"],property_location):
            tier="EXACT"
            reason="Exact requested locality"
        elif property_key in nearby_by_location:
            tier="NEARBY"
            market_meta=nearby_by_location[property_key]
            reason=(
                "Nearby alternative"
                +" · "+str(market_meta.get("distance_km"))+" km"
                +" · market fit "+str(market_meta.get("market_fit"))
            )
        elif property_key in comparable_by_location:
            tier="COMPARABLE"
            comp=comparable_by_location[property_key]
            reason="Use-case comparable: "+", ".join(comp.get("reasons") or [])
        else:
            continue

        row=_row(req,p,tier,reason)
        if market_meta:
            row["distance_km"]=market_meta.get("distance_km")
            row["market_fit"]=market_meta.get("market_fit")
            row["market_fit_score"]=market_meta.get("market_fit_score")
            row["market_fit_reason"]=market_meta.get("market_fit_reason")
            row["market_type"]=market_meta.get("market_type")

        if tier=="EXACT":
            if row["truth"]=="VERIFIED_AVAILABLE":
                verified_exact.append(row)
            else:
                unverified_exact.append(row)
        elif tier=="NEARBY":
            nearby_rows.append(row)
        else:
            comparable_rows.append(row)

    def sort_rows(rows):
        rows.sort(
            key=lambda x:(
                x["truth"]=="VERIFIED_AVAILABLE",
                x.get("market_fit_score",0),
                x["match_score"],
                x["trust_score"],
            ),
            reverse=True,
        )
        return rows[:limit_per_section]

    verified_exact=sort_rows(verified_exact)
    unverified_exact=sort_rows(unverified_exact)
    nearby_rows=sort_rows(nearby_rows)
    comparable_rows=sort_rows(comparable_rows)

    inventory_counts={}
    for row in verified_exact+unverified_exact+nearby_rows+comparable_rows:
        key=_n(row.get("location"))
        inventory_counts[key]=inventory_counts.get(key,0)+1

    for market in market_brain:
        count=inventory_counts.get(_n(market.get("location")),0)
        market["master_inventory_found"]=count
        market["inventory_status"]="FOUND_IN_MASTER" if count else "NO_MATCHING_MASTER_INVENTORY"
        market["next_action"]="REVIEW_MATCHING_INVENTORY" if count else "SOURCE_AND_VERIFY"

    any_rows=bool(verified_exact or unverified_exact or nearby_rows or comparable_rows)

    if req.get("requirement_quality")=="INCOMPLETE":
        outcome=(
            "REQUIREMENT_INCOMPLETE_PRELIMINARY_OPTIONS_ONLY"
            if any_rows or market_brain
            else "REQUIREMENT_INCOMPLETE_NEEDS_DETAILS"
        )
    elif verified_exact:
        outcome="AVAILABLE_OPTIONS_FOUND"
    elif any_rows:
        outcome="MATCHES_FOUND_VERIFY_BEFORE_PITCH"
    elif market_brain:
        outcome="NO_MATCHING_INVENTORY_MARKETS_IDENTIFIED_FOR_SOURCING"
    else:
        outcome="NO_SUITABLE_MASTER_INVENTORY_SOURCING_REQUIRED"

    queue=[]
    for priority,bucket in ((1,unverified_exact),(2,nearby_rows),(3,comparable_rows)):
        for row in bucket:
            if row["truth"]=="VERIFIED_AVAILABLE":
                continue
            queue.append({
                "priority":priority,
                "canonical_id":row.get("canonical_id"),
                "location":row.get("location"),
                "match_score":row.get("match_score"),
                "phones":row.get("phones"),
                "distance_km":row.get("distance_km"),
                "market_fit":row.get("market_fit"),
                "verify":[
                    "current availability",
                    "current asking price/rent",
                    "area",
                    "floor/configuration",
                    "transaction",
                ]+(
                    ["stilt preference"]
                    if "STILT" in (req.get("preferences") or [])
                    else []
                ),
            })

    sourcing_queue=[
        {
            "priority":1 if m.get("market_fit")=="STRONG" else 2,
            "location":m.get("location"),
            "distance_km":m.get("distance_km"),
            "market_type":m.get("market_type"),
            "market_fit":m.get("market_fit"),
            "market_fit_score":m.get("market_fit_score"),
            "reason":m.get("market_fit_reason"),
            "action":"SOURCE_AND_VERIFY",
        }
        for m in market_brain
        if not m.get("master_inventory_found")
    ][:10]

    return {
        "requirement":req,
        "outcome":outcome,
        "verified_available":verified_exact,
        "unverified_exact":unverified_exact,
        "nearby_matches":nearby_rows,
        "comparable_matches":comparable_rows,
        "commercial_market_intelligence":market_brain,
        "market_sourcing_queue":sourcing_queue,
        "verification_queue":queue[:20],
        "nearby_markets_checked":nearby,
        "comparable_markets_checked":comparable,
        "master_candidates_scanned":len(props),
        "truth_rule":"Alternative-market intelligence never means property availability. Only Master inventory is shown as property inventory, and unverified inventory requires human verification.",
    }

def _client(res):
    req=res["requirement"]
    loc=req.get("locality") or "requested location"
    missing=req.get("missing_fields") or []

    if missing:
        return (
            "We have captured "+loc+" as the preferred location. "
            +"To shortlist suitable options, please confirm: "
            +", ".join(missing)
            +". Any nearby alternatives shown internally are preliminary "
            +"and will be verified before sharing."
        )

    verified=res.get("verified_available") or []
    if verified:
        lines=[loc+": "+str(len(verified))+" verified available option(s) found."]
        for x in verified[:5]:
            area=x.get("area_sqyd") or x.get("area_sqft")
            unit="sq yd" if x.get("area_sqyd") else "sqft"
            lines.append(
                "• "+str(x.get("location"))
                +" | "+str(area)+" "+unit
                +" | "+str(x.get("price_raw") or "price on request")
            )
        return "\n".join(lines)

    if (
        res.get("unverified_exact")
        or res.get("nearby_matches")
        or res.get("comparable_matches")
    ):
        return (
            "We have identified potential options for "+loc+". "
            +"Availability, commercial suitability and current details "
            +"are being reconfirmed before sharing confirmed options."
        )

    return (
        "No suitable verified inventory is currently confirmed for "
        +loc
        +". Sourcing and verification are required."
    )

def answer(engine,raw,limit_per_section=8):
    rs=[match_requirement(engine,r,limit_per_section) for r in parse_post(raw)]
    return {'status':'OK','version':VERSION,'generated_at':datetime.now(timezone.utc).isoformat(),'requirements_detected':len(rs),'results':rs,'client_safe_answers':[_client(x) for x in rs],'team_rule':'Show useful unverified exact/nearby/comparable Master properties internally. Team verifies and chooses what to pitch.'}
def exam():
    tests=[]
    def add(n,ok):tests.append({'name':n,'pass':bool(ok)})
    add('125 sqyd -> 1125 sqft',_area('125 Sq. Yards')['area_sqft']==1125)
    add('160 sqyd -> 1440 sqft',_area('160 sq yards')['area_sqft']==1440)
    add('2.5 crore budget',_budget('Budget: Up to ₹2.5 Crore')['budget_rupees']==25000000)
    add('market rate',_budget('budget as per current market rate')['budget_market_rate'])
    add('buyer SALE',_tx('Serious & Ready Buyer')=='SALE');add('lease RENT',_tx('Need shop on lease')=='RENT');add('stilt', 'STILT' in _prefs('Stilt floor preferred'));add('residential inference',_cat('125 sq yd stilt floor ready buyer')=='RESIDENTIAL')
    post='URGENT PROPERTY REQUIREMENT – CR PARK\nLocation: CR Park\nRequirement 1: 125 Sq. Yards • Stilt floor preferred • Budget: Up to ₹2.5 Crore\nRequirement 2: 160 Sq. Yards • Budget as per current market rate\nSerious & Ready Buyer';p=parse_post(post)
    add('split two',len(p)==2);add('location inherited',len(p)==2 and all(_n(x['locality'])=='CR PARK' for x in p));add('req1 area',p[0]['area_sqft']==1125);add('req2 area',p[1]['area_sqft']==1440);add('req1 budget',p[0]['budget_rupees']==25000000);add('req2 market',p[1]['budget_market_rate']);add('unverified truth',_truth({'verification_status':'UNVERIFIED','availability_status':'UNKNOWN'})[0]=='UNVERIFIED_NEEDS_UPDATE');add('verified truth',_truth({'verification_status':'VERIFIED','availability_status':'AVAILABLE'})[0]=='VERIFIED_AVAILABLE');add('slightly over',_bfit({'budget_rupees':25000000},{'price_raw':'2.6 Cr'})[0]=='SLIGHTLY_OVER_BUDGET');add('far over',_bfit({'budget_rupees':25000000},{'price_raw':'3.2 Cr'})[0]=='OVER_BUDGET')
    rent_req=parse_requirement('Need restaurant on lease in Calangute, 1500 sqft, budget 2 lakh/month')
    add('v63 monthly budget period',rent_req.get('budget_period')=='MONTH')
    add('v63 monthly budget amount',rent_req.get('rent_budget_monthly')==200000)
    add('v63 rent per sqft',rent_req.get('rent_budget_per_sqft_month')==133.33)
    add('v63 clean budget display',_budget_display(rent_req)=='₹2 lakh/month')
    sale_req=parse_requirement('Want to buy a villa in Calangute, 3000 sqft, budget 8 crore')
    add('v63 sale has no rent economics',sale_req.get('rent_economics_status')=='NOT_APPLICABLE')
    passed=sum(x['pass'] for x in tests);return {'status':'PASS' if passed==len(tests) else 'FAIL','tested':len(tests),'passed':passed,'failed':len(tests)-passed,'tests':tests}
def _table(title,rows):
    if not rows:
        return "<div class=card><h3>"+html.escape(title)+"</h3><p>None.</p></div>"

    header=(
        "<tr>"
        "<th>Score</th>"
        "<th>Status</th>"
        "<th>Location</th>"
        "<th>Distance</th>"
        "<th>Market fit</th>"
        "<th>Area</th>"
        "<th>Price</th>"
        "<th>Fit</th>"
        "<th>Contact</th>"
        "<th>Action</th>"
        "</tr>"
    )

    body=[]

    for x in rows:
        if x.get("area_sqyd"):
            area=str(x.get("area_sqyd"))+" sq yd"
        else:
            area=str(x.get("area_sqft") or "—")+" sqft"

        fit=(
            str(x.get("location_tier"))
            +" | area "+str(x.get("area_band"))
            +" | "+str(x.get("budget_fit"))
            +" | pref "+str(x.get("preference_fit"))
        )

        phones=", ".join(x.get("phones") or []) or "—"

        if x.get("distance_km") is not None:
            distance=str(x.get("distance_km"))+" km"
        else:
            distance="—"

        market_fit=x.get("market_fit")
        if not market_fit and x.get("location_tier")=="EXACT":
            market_fit="EXACT"
        if not market_fit:
            market_fit="—"

        reason=x.get("market_fit_reason")
        if reason:
            market_fit=str(market_fit)+" · "+str(reason)

        values=(
            x.get("match_score"),
            x.get("truth"),
            x.get("location") or "—",
            distance,
            market_fit,
            area,
            x.get("price_raw") or "—",
            fit,
            phones,
            x.get("action"),
        )

        cells="".join(
            "<td>"+html.escape(str(value))+"</td>"
            for value in values
        )
        body.append("<tr>"+cells+"</tr>")

    return (
        "<div class=card><h3>"
        +html.escape(title)
        +"</h3><div class=scroll><table>"
        +header
        +"".join(body)
        +"</table></div></div>"
    )

def _market_table(title,rows):
    if not rows:
        return "<div class=card><h3>"+html.escape(title)+"</h3><p>None.</p></div>"
    header=(
        "<tr>"
        "<th>Rank</th>"
        "<th>Alternative market</th>"
        "<th>Distance</th>"
        "<th>Market type</th>"
        "<th>Commercial fit</th>"
        "<th>Master inventory</th>"
        "<th>Next action</th>"
        "</tr>"
    )
    body=[]
    for idx,x in enumerate(rows,1):
        distance=str(x.get("distance_km"))+" km" if x.get("distance_km") is not None else "—"
        fit=str(x.get("market_fit") or "—")
        if x.get("market_fit_reason"):
            fit+=" · "+str(x.get("market_fit_reason"))
        count=int(x.get("master_inventory_found") or 0)
        inventory=str(count)+" matching candidate(s)" if count else "None in current Master match"
        vals=(
            idx,
            x.get("location") or "—",
            distance,
            x.get("market_type") or "—",
            fit,
            inventory,
            x.get("next_action") or "SOURCE_AND_VERIFY",
        )
        body.append(
            "<tr>"
            +"".join("<td>"+html.escape(str(v))+"</td>" for v in vals)
            +"</tr>"
        )
    return (
        "<div class=card><h3>"
        +html.escape(title)
        +"</h3><p><b>Important:</b> These are market recommendations, not claims of property availability.</p>"
        +"<div class=scroll><table>"
        +header
        +"".join(body)
        +"</table></div></div>"
    )
def _render(data,raw):
    out=[]
    if data:
        for i,res in enumerate(data.get("results") or [],1):
            req=res["requirement"]
            missing=req.get("missing_fields") or []
            quality=req.get("requirement_quality") or "UNKNOWN"
            completeness=req.get("requirement_completeness_pct",0)
            tx=req.get("transaction_type") or "TRANSACTION UNKNOWN"
            category=req.get("category") or "GENERAL"

            hero=(
                "<div class='card hero'>"
                +"<h2>Requirement "+str(i)+": "
                +html.escape(req.get("locality") or "Location not captured")
                +"</h2>"
                +"<p><b>"+html.escape(res.get("outcome"))+"</b></p>"
                +"<p>"+html.escape(tx)
                +" · "+html.escape(category)
                +" · "+html.escape(str(req.get("area_raw") or "area not captured"))
                +" · Budget "+html.escape(_budget_display(req))
                +"</p>"
                +"<p><b>Requirement quality:</b> "
                +html.escape(quality)
                +" · "+str(completeness)+"% complete</p>"
            )

            if req.get("transaction_type")=="RENT":
                monthly=req.get("rent_budget_monthly")
                per_sqft=req.get("rent_budget_per_sqft_month")
                if monthly:
                    hero+=(
                        "<p><b>Rent economics:</b> "
                        +html.escape(_money_label(monthly))+"/month"
                    )
                    if per_sqft is not None:
                        hero+=(
                            " · ₹"
                            +html.escape("{:,.2f}".format(per_sqft))
                            +"/sqft/month"
                        )
                    hero+="</p>"

            if missing:
                hero+=(
                    "<p><b>Still required:</b> "
                    +html.escape(", ".join(missing))
                    +"</p>"
                )
            else:
                hero+="<p><b>Requirement complete.</b></p>"
            hero+="</div>"
            out.append(hero)

            out.append(_table(
                "A. VERIFIED AVAILABLE · EXACT REQUESTED MARKET",
                res.get("verified_available") or [],
            ))
            out.append(_table(
                "B. UNVERIFIED EXACT MATCHES · VERIFY FIRST",
                res.get("unverified_exact") or [],
            ))
            out.append(_market_table(
                "C. COMMERCIAL MARKET INTELLIGENCE · ALTERNATIVE MARKETS CONSIDERED",
                res.get("commercial_market_intelligence") or [],
            ))

            nearby_title=(
                "D. ACTUAL MASTER INVENTORY FOUND IN NEARBY MARKETS"
                if quality=="COMPLETE"
                else "D. PRELIMINARY MASTER INVENTORY IN NEARBY MARKETS · COMPLETE REQUIREMENT BEFORE PITCH"
            )
            out.append(_table(
                nearby_title,
                res.get("nearby_matches") or [],
            ))
            out.append(_table(
                "E. EVIDENCE-QUALIFIED COMPARABLE INVENTORY",
                res.get("comparable_matches") or [],
            ))

            queue=res.get("verification_queue") or []
            if queue:
                items=[]
                for x in queue:
                    item=(
                        "<li><b>P"+str(x.get("priority"))+"</b> · "
                        +html.escape(str(x.get("location") or ""))
                        +" · "
                        +html.escape(str(x.get("canonical_id") or ""))
                        +" · score "+str(x.get("match_score"))
                        +" · "
                        +html.escape(", ".join(x.get("phones") or []) or "no contact in Master")
                    )
                    if x.get("distance_km") is not None:
                        item+=" · "+str(x.get("distance_km"))+" km"
                    if x.get("market_fit"):
                        item+=" · market fit "+html.escape(str(x.get("market_fit")))
                    item+="</li>"
                    items.append(item)
                out.append(
                    "<div class=card><h3>F. TEAM VERIFY ACTUAL INVENTORY NOW</h3><ol>"
                    +"".join(items)
                    +"</ol></div>"
                )

            sourcing=res.get("market_sourcing_queue") or []
            if sourcing:
                items=[]
                for x in sourcing:
                    items.append(
                        "<li><b>P"+str(x.get("priority"))+"</b> · "
                        +html.escape(str(x.get("location") or ""))
                        +" · "+html.escape(str(x.get("market_fit") or ""))
                        +" · "+str(x.get("distance_km"))+" km"
                        +" · SOURCE + VERIFY</li>"
                    )
                out.append(
                    "<div class=card><h3>G. SOURCING QUEUE · GOOD MARKET, NO MATCHING MASTER INVENTORY</h3><ol>"
                    +"".join(items)
                    +"</ol></div>"
                )

            out.append(
                "<div class=card><h3>Client-safe draft</h3><pre>"
                +html.escape(data["client_safe_answers"][i-1])
                +"</pre></div>"
            )

    page=(
        "<!doctype html><html><head>"
        "<meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Baby V6.3</title>"
        "<style>"
        "body{font-family:Arial;background:#f5f7fb;color:#172033;margin:0}"
        ".wrap{max-width:1500px;margin:auto;padding:20px}"
        ".card{background:#fff;border:1px solid #dde3ea;border-radius:12px;padding:14px;margin:12px 0}"
        "textarea{width:100%;min-height:150px;padding:12px}"
        "button{padding:10px 16px}"
        "table{border-collapse:collapse;width:100%;font-size:12px}"
        "th,td{border-bottom:1px solid #eee;padding:8px;text-align:left;vertical-align:top}"
        ".scroll{overflow:auto}"
        "pre{white-space:pre-wrap}"
        "</style></head><body><div class=wrap>"
        "<p><a href='javascript:history.back()'>← Previous Page</a>"
        " · <a href=/alliance/primary>Dashboard</a></p>"
        "<h1>Alliance Baby V6.3 · Commercial Market Brain + Rent Economics</h1>"
        "<p>Exact inventory first. Alternative markets are intelligence, not availability claims. "
        "Rent budgets are normalized with monthly economics.</p>"
        "<form method=get>"
        "<textarea name=q>"+html.escape(raw or "")+"</textarea><br>"
        "<button>Find Availability + Smart Market Options</button>"
        "</form>"
        +"".join(out)
        +"</div></body></html>"
    )
    return page

def register(core):
    app=_app(core);eng=_engine(core)
    if eng is None:raise RuntimeError('core.engine missing')
    owned={'/alliance/baby/v6-answer-machine','/api/alliance/baby/v6-answer-machine','/api/alliance/baby/v6-exam'};app.router.routes[:]=[r for r in app.router.routes if getattr(r,'path',None) not in owned]
    @app.get('/alliance/baby/v6-answer-machine',response_class=HTMLResponse)
    async def page(request:Request,q:str=''):
        _login(core,request);return HTMLResponse(_render(answer(eng,q,8) if q.strip() else None,q))
    @app.post('/api/alliance/baby/v6-answer-machine')
    async def api(request:Request,inp:AnswerInput):
        _login(core,request);return JSONResponse(answer(eng,inp.requirement,inp.limit_per_section))
    @app.get('/api/alliance/baby/v6-exam')
    async def ex(request:Request):
        _login(core,request);return JSONResponse(exam())
    return {'status':'REGISTERED','version':VERSION,'routes':sorted(owned),'exam':exam(),'truth_policy':'Unverified team-visible; never falsely available.'}
