from __future__ import annotations
import html,json,re
from datetime import datetime,timezone
from decimal import Decimal
from fastapi import Request
from fastapi.responses import HTMLResponse,JSONResponse
from pydantic import BaseModel,Field
VERSION="6.0.0-ALLIANCE-BABY-REQUIREMENT-ANSWER-MACHINE"
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
    s=str(raw or '');market=bool(re.search(r"\b(?:current|prevailing)?\s*market\s+rate\b",s,re.I))
    pats=[r"(?:budget|upto|up to|max(?:imum)?|within)\s*(?::|-)?\s*(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?)\s*(crore|cr|lakh|lac|lacs|lakhs)",r"(?:₹|rs\.?|inr)\s*(\d[\d,]*(?:\.\d+)?)\s*(crore|cr|lakh|lac|lacs|lakhs)"]
    for p in pats:
        m=re.search(p,s,re.I)
        if m:return {'budget_rupees':_money_to_rupees(m.group(1)+' '+m.group(2)),'budget_raw':_clean(m.group(0)),'budget_market_rate':market}
    return {'budget_rupees':None,'budget_raw':'MARKET_RATE' if market else '','budget_market_rate':market}
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
    s=_n(raw)
    if any(x in s for x in ('READY BUYER','SERIOUS BUYER','BUYER','TO BUY','PURCHASE','FOR SALE',' SALE ','BUY ')):return 'SALE'
    if any(x in s for x in ('RENT','LEASE','LEASING','TO LET')):return 'RENT'
    if 'BUDGET' in s and any(x in s for x in (' CR','CRORE')):return 'SALE'
    return ''
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
    loc,city=_location(raw);a=_area(raw);b=_budget(raw);c=_cat(raw)
    return {'requirement_no':i,'raw_text':_clean(raw),'locality':loc,'location':loc,'city':city,'transaction_type':_tx(raw),'category':c,'purpose':c,'preferences':_prefs(raw),**a,**b}
def parse_post(raw):return [parse_requirement(x,i+1) for i,x in enumerate(_split(raw))]
def _ptext(p):
    vals=[]
    for k in ('locality','city','property_type','category','description','remarks','price_raw','transaction_type','clean_record'):
        v=p.get(k)
        if v not in (None,'',[],{}):vals.append(json.dumps(v,ensure_ascii=False,default=str) if isinstance(v,(dict,list)) else str(v))
    return _n(' '.join(vals))
def _ploc(p):return _clean(p.get('locality') or p.get('location') or p.get('city') or '')
def _same(a,b):
    a,b=_n(a),_n(b);return bool(a and b and (a==b or a in b or b in a))
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
    loc=req.get('locality') or ''
    if not loc:return []
    try:
        import alliance_micromarket_knowledge_v1 as g
        r=g.suggest(loc,req.get('raw_text') or '',12) or {};out=[]
        for x in r.get('suggestions') or []:
            place=_clean(x.get('location'))
            if place and not _same(place,loc):out.append({'location':place,'distance_km':x.get('distance_km'),'market_type':x.get('market_type')})
        return out
    except:return []
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
    tr,trust=_truth(p);ab,ad=_aband(req.get('area_sqft'),p.get('area_sqft'));bf,bd,pr=_bfit(req,p);pf,pfound=_pfit(req,p)
    score={'EXACT':40,'NEARBY':26,'COMPARABLE':20,'OTHER':5}.get(tier,0)+{'STRONG':25,'GOOD':18,'BROAD':8,'UNKNOWN':3}.get(ab,0)+{'WITHIN_BUDGET':16,'NO_NUMERIC_LIMIT':10,'PRICE_UNKNOWN':4,'SLIGHTLY_OVER_BUDGET':5}.get(bf,0)+{'FULL':9,'PARTIAL':5,'NO_PREFERENCE':5,'UNKNOWN':1}.get(pf,0)+(10 if tr=='VERIFIED_AVAILABLE' else 6 if tr.startswith('VERIFIED') else 2)
    action='READY_TO_PITCH' if tr=='VERIFIED_AVAILABLE' else 'VERIFY_AVAILABILITY_AND_DETAILS'
    cr=p.get('clean_record') if isinstance(p.get('clean_record'),dict) else {}
    return {'canonical_id':p.get('canonical_id'),'location':_ploc(p),'city':p.get('city'),'area_sqft':p.get('area_sqft'),'area_sqyd':p.get('area_sqyd'),'property_type':p.get('property_type') or p.get('category') or '','transaction':p.get('transaction_type'),'price_raw':p.get('price_raw') or p.get('sale_amount') or p.get('rent_amount') or '','price_rupees':pr,'verification_status':p.get('verification_status') or 'UNVERIFIED','availability_status':p.get('availability_status') or 'UNKNOWN','truth':tr,'trust_score':trust,'location_tier':tier,'market_reason':reason,'area_band':ab,'area_diff_pct':ad,'budget_fit':bf,'budget_diff_pct':bd,'preference_fit':pf,'preferences_found':pfound,'match_score':min(100,score),'phones':_phones(p),'assigned_to':p.get('assigned_to'),'action':action,'description':_clean(p.get('description') or cr.get('description') or '')}
def match_requirement(engine,req,limit_per_section=8):
    import alliance_master_integration_v720 as m
    props=m._search_properties(engine,tx=req.get('transaction_type') or '',limit=4000);near=_nearby(req);comp=_comparables(engine,req);nd={_n(x['location']):x for x in near};cd={_n(x['location']):x for x in comp};ve=[];ue=[];nr=[];cr=[]
    for p in props:
        if not _eligible(req,p):continue
        pl=_ploc(p);pn=_n(pl)
        if req.get('locality') and _same(req['locality'],pl):tier='EXACT';reason='Exact requested locality'
        elif pn in nd:tier='NEARBY';x=nd[pn];reason='Nearby micro-market'+((' ~'+str(x.get('distance_km'))+' km') if x.get('distance_km') is not None else '')
        elif pn in cd:tier='COMPARABLE';reason='Use-case comparable: '+', '.join(cd[pn].get('reasons') or [])
        else:continue
        x=_row(req,p,tier,reason)
        if tier=='EXACT':(ve if x['truth']=='VERIFIED_AVAILABLE' else ue).append(x)
        elif tier=='NEARBY':nr.append(x)
        else:cr.append(x)
    def sort(a):a.sort(key=lambda x:(x['truth']=='VERIFIED_AVAILABLE',x['match_score'],x['trust_score']),reverse=True);return a[:limit_per_section]
    ve,ue,nr,cr=map(sort,(ve,ue,nr,cr));outcome='AVAILABLE_OPTIONS_FOUND' if ve else 'MATCHES_FOUND_VERIFY_BEFORE_PITCH' if (ue or nr or cr) else 'NO_SUITABLE_MASTER_INVENTORY_SOURCING_REQUIRED';q=[]
    for pri,b in ((1,ue),(2,nr),(3,cr)):
        for x in b:
            if x['truth']!='VERIFIED_AVAILABLE':q.append({'priority':pri,'canonical_id':x.get('canonical_id'),'location':x.get('location'),'match_score':x.get('match_score'),'phones':x.get('phones'),'verify':['current availability','current asking price/rent','area','floor/configuration','transaction']+(['stilt preference'] if 'STILT' in (req.get('preferences') or []) else [])})
    return {'requirement':req,'outcome':outcome,'verified_available':ve,'unverified_exact':ue,'nearby_matches':nr,'comparable_matches':cr,'verification_queue':q[:20],'nearby_markets_checked':near,'comparable_markets_checked':comp,'master_candidates_scanned':len(props),'truth_rule':'Unverified matches are shown to Alliance team for verification, never as confirmed available.'}
def _client(res):
    req=res['requirement'];loc=req.get('locality') or 'requested location';v=res.get('verified_available') or []
    if v:
        lines=[f"{loc}: {len(v)} verified available option(s) found."]
        for x in v[:5]:lines.append(f"• {x.get('location')} | {x.get('area_sqyd') or x.get('area_sqft')} {'sq yd' if x.get('area_sqyd') else 'sqft'} | {x.get('price_raw') or 'price on request'}")
        return '\n'.join(lines)
    if res.get('unverified_exact') or res.get('nearby_matches') or res.get('comparable_matches'):return f"We have shortlisted matching options for {loc}. Availability and current details are being reconfirmed before sharing confirmed options."
    return f"No suitable verified inventory is currently confirmed for {loc}. Sourcing and verification are required."
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
    passed=sum(x['pass'] for x in tests);return {'status':'PASS' if passed==len(tests) else 'FAIL','tested':len(tests),'passed':passed,'failed':len(tests)-passed,'tests':tests}
def _table(title,rows):
    if not rows:return f"<div class=card><h3>{html.escape(title)}</h3><p>None.</p></div>"
    h='<tr><th>Score</th><th>Status</th><th>Location</th><th>Area</th><th>Price</th><th>Fit</th><th>Contact</th><th>Action</th></tr>';b=[]
    for x in rows:
        area=f"{x.get('area_sqyd')} sq yd" if x.get('area_sqyd') else f"{x.get('area_sqft') or '—'} sqft";fit=f"{x.get('location_tier')} | area {x.get('area_band')} | {x.get('budget_fit')} | pref {x.get('preference_fit')}";phones=', '.join(x.get('phones') or []) or '—'
        vals=(x.get('match_score'),x.get('truth'),x.get('location') or '—',area,x.get('price_raw') or '—',fit,phones,x.get('action'));b.append('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in vals)+'</tr>')
    return f"<div class=card><h3>{html.escape(title)}</h3><div class=scroll><table>{h}{''.join(b)}</table></div></div>"
def _render(data,raw):
    out=[]
    if data:
        for i,res in enumerate(data.get('results') or [],1):
            r=res['requirement'];out.append(f"<div class='card hero'><h2>Requirement {i}: {html.escape(r.get('locality') or 'Location not captured')}</h2><p><b>{html.escape(res.get('outcome'))}</b></p><p>{html.escape(r.get('transaction_type') or 'transaction unknown')} · {html.escape(r.get('category') or '')} · {html.escape(str(r.get('area_raw') or 'area not captured'))} · Budget {html.escape(str(r.get('budget_raw') or 'not captured'))}</p></div>")
            out += [_table('A. VERIFIED AVAILABLE',res.get('verified_available') or []),_table('B. UNVERIFIED EXACT MATCHES',res.get('unverified_exact') or []),_table('C. NEARBY MATCHES',res.get('nearby_matches') or []),_table('D. COMPARABLE MARKET MATCHES',res.get('comparable_matches') or [])]
            q=res.get('verification_queue') or []
            if q:out.append('<div class=card><h3>E. TEAM VERIFY NOW</h3><ol>'+''.join(f"<li><b>P{x['priority']}</b> · {html.escape(str(x.get('location') or ''))} · {html.escape(str(x.get('canonical_id') or ''))} · score {x.get('match_score')} · {html.escape(', '.join(x.get('phones') or []) or 'no contact in Master')}</li>" for x in q)+'</ol></div>')
            out.append('<div class=card><h3>Client-safe draft</h3><pre>'+html.escape(data['client_safe_answers'][i-1])+'</pre></div>')
    return f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Baby V6</title><style>body{{font-family:Arial;background:#f5f7fb;color:#172033;margin:0}}.wrap{{max-width:1500px;margin:auto;padding:20px}}.card{{background:#fff;border:1px solid #dde3ea;border-radius:12px;padding:14px;margin:12px 0}}textarea{{width:100%;min-height:150px;padding:12px}}button{{padding:10px 16px}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{border-bottom:1px solid #eee;padding:8px;text-align:left;vertical-align:top}}.scroll{{overflow:auto}}pre{{white-space:pre-wrap}}</style></head><body><div class=wrap><p><a href="javascript:history.back()">← Previous Page</a> · <a href=/alliance/primary>Dashboard</a></p><h1>Alliance Baby V6 · Requirement Answer Machine</h1><p>Verified available first. Unverified exact, nearby and evidence-qualified comparable Master properties are team-visible for manual verification.</p><form method=get><textarea name=q>{html.escape(raw or '')}</textarea><br><button>Find Availability + Matching Options</button></form>{''.join(out)}</div></body></html>"""
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
