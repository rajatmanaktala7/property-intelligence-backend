
from __future__ import annotations
import os,re,uuid,inspect
from fastapi import APIRouter,Request,Form,Query
from fastapi.responses import HTMLResponse,RedirectResponse
from fastapi.routing import APIRoute
from sqlalchemy import text

VERSION="4.6.0-UNIFIED-LIVE-INTELLIGENCE-MATCHER"

# -------------------- helpers --------------------
def esc(v):
    s=str(v or "")
    return (s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
             .replace('"',"&quot;").replace("'","&#39;"))

def norm(v):
    return re.sub(r"\s+"," ",re.sub(r"[^A-Z0-9]+"," ",str(v or "").upper())).strip()

RENT_WORDS={"RENT","RENTAL","LEASE","LEASING","TO LET","TOLET"}
SALE_WORDS={"SALE","BUY","PURCHASE","OUTRIGHT"}
COMMERCIAL_WORDS={"COMMERCIAL","OFFICE","SHOP","SHOWROOM","RETAIL","BASEMENT","WAREHOUSE","GODOWN","BANQUET","RESTAURANT","CAFE","LOUNGE"}
RESIDENTIAL_WORDS={"RESIDENTIAL","APARTMENT","FLAT","VILLA","KOTHI","FLOOR","BUILDER FLOOR","BHK"}

SYNONYMS={
 "RENT":["RENT","RENTAL","LEASE","LEASING","TO LET"],
 "COMMERCIAL":["COMMERCIAL","OFFICE","SHOP","SHOWROOM","RETAIL","BASEMENT","WAREHOUSE","GODOWN","BANQUET","RESTAURANT","CAFE","LOUNGE"],
 "RESIDENTIAL":["RESIDENTIAL","APARTMENT","FLAT","VILLA","KOTHI","BUILDER FLOOR","BHK"],
 "SAKET":["SAKET","MALVIYA NAGAR","SELECT CITYWALK"],
 "GURGAON":["GURGAON","GURUGRAM"],
 "DELHI":["DELHI","NEW DELHI"],
}

def parse_requirement(q):
    raw=str(q or "").strip()
    up=norm(raw)
    tokens=[t for t in up.split() if len(t)>2 and t not in {
        "LOOKING","PROPERTY","PROPERTIES","WANT","NEED","REQUIRE","REQUIRED","FOR","THE","WITH","AND","IN","AT","OF"
    }]
    txn=None
    if any(w in up for w in RENT_WORDS): txn="RENT"
    elif any(w in up for w in SALE_WORDS): txn="SALE"
    ptype=None
    if any(w in up for w in COMMERCIAL_WORDS): ptype="COMMERCIAL"
    elif any(w in up for w in RESIDENTIAL_WORDS): ptype="RESIDENTIAL"

    area=None
    m=re.search(r"(?i)\b(\d{2,6})\s*(?:sq\.?\s*ft|sqft|sft)\b",raw)
    if m: area=float(m.group(1))
    budget=None
    m=re.search(r"(?i)\b(?:budget|rent|upto|up to|max)?\s*₹?\s*(\d+(?:\.\d+)?)\s*(cr|crore|lac|lakh|k)\b",raw)
    if m:
        n=float(m.group(1));u=m.group(2).lower()
        if u in {"cr","crore"}: n*=10_000_000
        elif u in {"lac","lakh"}: n*=100_000
        elif u=="k": n*=1_000
        budget=n

    expanded=set(tokens)
    for t in list(tokens):
        for key,vals in SYNONYMS.items():
            if t==key or t in vals:
                expanded.update(vals)
    return {"raw":raw,"tokens":tokens,"expanded":sorted(expanded),"transaction":txn,"property_type":ptype,"area":area,"budget":budget}

def score_text(req,textv,txn=None,ptype=None,area=None,price=None):
    txt=norm(textv)
    if not txt:return 0,[]
    score=0;reasons=[]
    expanded=req["expanded"]
    hits=0
    for t in expanded:
        if t and t in txt:
            hits+=1
    if expanded:
        score += min(45, round(45*hits/max(1,len(set(expanded)))))
        if hits: reasons.append(f"{hits} semantic keyword matches")

    if req["transaction"]:
        rt=req["transaction"]
        tnorm=norm(txn or txt)
        if rt=="RENT" and any(w in tnorm for w in RENT_WORDS):
            score+=20;reasons.append("rent/lease intent")
        elif rt=="SALE" and any(w in tnorm for w in SALE_WORDS):
            score+=20;reasons.append("sale intent")

    if req["property_type"]:
        pt=req["property_type"]
        pnorm=norm(ptype or txt)
        bank=COMMERCIAL_WORDS if pt=="COMMERCIAL" else RESIDENTIAL_WORDS
        if any(w in pnorm for w in bank):
            score+=18;reasons.append(f"{pt.lower()} type")

    if req["area"] and area:
        try:
            ratio=abs(float(area)-req["area"])/max(req["area"],1)
            if ratio<=.15:score+=12;reasons.append("area within 15%")
            elif ratio<=.30:score+=7;reasons.append("area within 30%")
        except:pass

    if req["budget"] and price:
        try:
            p=float(price)
            if p<=req["budget"]:score+=5;reasons.append("within budget")
            elif p<=req["budget"]*1.15:score+=2;reasons.append("near budget")
        except:pass
    return min(100,score),reasons

def table_exists(engine,name):
    with engine.connect() as c:
        return bool(c.execute(text("""
          SELECT 1 FROM information_schema.tables
          WHERE table_schema='public' AND table_name=:n
        """),{"n":name}).first())

def columns(engine,name):
    with engine.connect() as c:
        return [r[0] for r in c.execute(text("""
          SELECT column_name FROM information_schema.columns
          WHERE table_schema='public' AND table_name=:n ORDER BY ordinal_position
        """),{"n":name}).all()]

def pick(d,*names):
    for n in names:
        if n in d and d[n] not in (None,""): return d[n]
    return None

def num(v):
    if v in (None,""):return None
    try:return float(v)
    except:return None

def latest_wa_generation(engine):
    try:
        with engine.connect() as c:
            return c.execute(text("""
              SELECT generation_id FROM pi_whatsapp_property_master_generation
              WHERE status='COMPLETED'
              ORDER BY completed_at DESC NULLS LAST,id DESC LIMIT 1
            """)).scalar()
    except:return None

# -------------------- source adapters --------------------
def whatsapp_availability(engine,req,limit=100):
    gen=latest_wa_generation(engine)
    if not gen:return []
    with engine.connect() as c:
        rows=c.execute(text("""
          SELECT record_id,lead_type,description,area,configuration_details,price,
                 contact_name_number,source,captured_on,verification,source_count
          FROM pi_whatsapp_property_master
          WHERE generation_id=:g
          ORDER BY captured_on DESC NULLS LAST,id DESC LIMIT 3000
        """),{"g":gen}).mappings().all()
    out=[]
    for r in rows:
        d=dict(r)
        sc,why=score_text(req," ".join(str(x or "") for x in d.values()),d.get("lead_type"),d.get("description"))
        if sc>=15:
            d.update(score=sc,reasons=", ".join(why),source_bucket="1. WhatsApp Group")
            out.append(d)
    return sorted(out,key=lambda x:x["score"],reverse=True)[:limit]

def whatsapp_requirements():
    try:
        import whatsapp_live_bridge as legacy
        if legacy.wa_engine is None:return []
        with legacy.wa_engine.connect() as c:
            rows=c.execute(text("""
              SELECT r.wa_requirement_id record_id,
                     COALESCE(r.transaction_type,'REQUIREMENT') lead_type,
                     CONCAT_WS(' | ',r.preferred_locations,r.property_type,r.suitable_category,
                       CASE WHEN r.minimum_area_sqft IS NOT NULL THEN r.minimum_area_sqft::text||' sqft' END,
                       CASE WHEN r.maximum_area_sqft IS NOT NULL THEN 'max '||r.maximum_area_sqft::text||' sqft' END,
                       r.raw_text) description,
                     CONCAT_WS(' - ',r.minimum_area_sqft::text,r.maximum_area_sqft::text) area,
                     r.property_type configuration_details,
                     CASE WHEN r.budget_max_inr IS NOT NULL THEN '₹'||r.budget_max_inr::text END price,
                     CONCAT_WS(' · ',r.contact_name,r.contact_phone) contact_name_number,
                     s.group_name source,
                     r.created_at captured_on,
                     COALESCE(r.status,'ACTIVE') verification,
                     1 source_count
              FROM wa_requirements r
              LEFT JOIN wa_sources s ON s.source_id=r.source_id
              WHERE COALESCE(r.status,'ACTIVE')='ACTIVE'
              ORDER BY r.id DESC LIMIT 1000
            """)).mappings().all()
        return [dict(r) for r in rows]
    except:return []

def newspaper_results(engine,req,limit=100):
    if not table_exists(engine,"pi_newspaper_properties"):return []
    with engine.connect() as c:
        rows=c.execute(text("""
          SELECT * FROM pi_newspaper_properties ORDER BY id DESC LIMIT 3000
        """)).mappings().all()
    canonical={}
    for r in rows:
        d=dict(r)
        textv=" ".join(str(v or "") for v in d.values())
        key=norm("|".join(str(pick(d,*names) or "") for names in [
            ("locality","location"),("area",),("configuration_details","configuration"),("price","rent"),("phone_numbers","contact_number")
        ]))
        if not key:key=str(d.get("id"))
        if key in canonical:continue
        sc,why=score_text(req,textv,pick(d,"lead_type","transaction_type"),pick(d,"property_type","description"))
        if sc>=15:
            canonical[key]={
                "record_id":pick(d,"record_id","id"),
                "lead_type":pick(d,"lead_type","transaction_type") or "PROPERTY",
                "description":pick(d,"notes","description","raw_text","locality","location") or textv[:500],
                "area":pick(d,"area","available_area"),
                "configuration_details":pick(d,"configuration_details","configuration","property_type"),
                "price":pick(d,"price","rent","sale_price"),
                "contact_name_number":" · ".join(str(x) for x in [pick(d,"contact_person","broker_name","agency_brand"),pick(d,"phone_numbers","contact_number","broker_phone")] if x),
                "source":"Newspaper",
                "captured_on":pick(d,"captured_at","created_at"),
                "verification":pick(d,"verification","verification_status") or "Unverified",
                "source_count":1,
                "score":sc,"reasons":", ".join(why),"source_bucket":"2. Newspaper Database"
            }
    return sorted(canonical.values(),key=lambda x:x["score"],reverse=True)[:limit]

MASTER_TABLE_CANDIDATES=["pi_properties","manual_properties","properties","property_records","master_properties"]
def master_results(engine,req,limit=100):
    table=next((t for t in MASTER_TABLE_CANDIDATES if table_exists(engine,t)),None)
    if not table:return [],None
    cols=columns(engine,table)
    safe=", ".join('"'+c+'"' for c in cols[:80])
    with engine.connect() as c:
        rows=c.execute(text(f'SELECT {safe} FROM "{table}" ORDER BY 1 DESC LIMIT 4000')).mappings().all()
    out=[]
    for r in rows:
        d=dict(r)
        textv=" ".join(str(v or "") for v in d.values())
        sc,why=score_text(req,textv,pick(d,"transaction_type","rent_sale","lead_type"),pick(d,"property_type","category"))
        if sc<15:continue
        out.append({
            "record_id":pick(d,"property_code","record_id","id"),
            "lead_type":pick(d,"transaction_type","rent_sale","lead_type") or "PROPERTY",
            "description":pick(d,"description","remarks","notes","property_name","location") or textv[:700],
            "area":pick(d,"available_area","area","area_sqft","size"),
            "configuration_details":pick(d,"configuration","property_type","category"),
            "price":pick(d,"rent","rent_inr","sale_price","sale_price_inr","price"),
            "contact_name_number":" · ".join(str(x) for x in [pick(d,"owner_name","broker_name","contact_name"),pick(d,"contact_number","phone","owner_phone","broker_phone")] if x),
            "source":f"Master Database ({table})",
            "captured_on":pick(d,"captured_at","created_at","updated_at"),
            "verification":pick(d,"verification","verification_status","status") or "Unverified",
            "source_count":1,
            "score":sc,"reasons":", ".join(why),"source_bucket":"3. Master Database"
        })
    return sorted(out,key=lambda x:x["score"],reverse=True)[:limit],table

def discovery_results(req,limit=50):
    # Best-effort adapter to existing discovery engine; no fake web results.
    try:
        import property_discovery as pd
    except Exception as e:
        return [],f"Discovery module unavailable: {type(e).__name__}"
    for fname in ["search_properties","discover_properties","run_search","search_web","discover"]:
        fn=getattr(pd,fname,None)
        if not callable(fn):continue
        try:
            sig=inspect.signature(fn)
            kwargs={}
            if "query" in sig.parameters:kwargs["query"]=req["raw"]
            elif "q" in sig.parameters:kwargs["q"]=req["raw"]
            else:
                res=fn(req["raw"])
                return _normalize_discovery(res,req,limit),None
            if "limit" in sig.parameters:kwargs["limit"]=limit
            res=fn(**kwargs)
            return _normalize_discovery(res,req,limit),None
        except Exception:
            continue
    return [],"Existing discovery module found, but no compatible callable adapter was detected."

def _normalize_discovery(res,req,limit):
    if isinstance(res,dict):
        for k in ["results","properties","items","data"]:
            if isinstance(res.get(k),list):res=res[k];break
    if not isinstance(res,list):return []
    out=[]
    for i,x in enumerate(res[:limit*3]):
        d=dict(x) if isinstance(x,dict) else {"description":str(x)}
        textv=" ".join(str(v or "") for v in d.values())
        sc,why=score_text(req,textv,pick(d,"transaction_type","type"),pick(d,"property_type","category"))
        if sc<10:continue
        out.append({
          "record_id":pick(d,"id","record_id") or f"DISC-{i+1}",
          "lead_type":pick(d,"transaction_type","type") or "DISCOVERY",
          "description":pick(d,"description","title","snippet") or textv[:700],
          "area":pick(d,"area","size"),
          "configuration_details":pick(d,"configuration","property_type"),
          "price":pick(d,"price","rent"),
          "contact_name_number":pick(d,"contact","phone"),
          "source":pick(d,"source","source_url","url") or "Search Discovery",
          "captured_on":pick(d,"published_at","created_at"),
          "verification":"DISCOVERED",
          "source_count":1,
          "score":sc,"reasons":", ".join(why),"source_bucket":"4. Search / Discovery"
        })
    return sorted(out,key=lambda x:x["score"],reverse=True)[:limit]

# -------------------- rendering --------------------
COLS=["record_id","lead_type","description","area","configuration_details","price","contact_name_number","source","captured_on","verification"]
HEADS=["Record","Type","Description","Area","Configuration","Price / Rent","Contact Name / Number","Source","Captured","Verification"]

def render_table(rows,title,number=None,status=None):
    prefix=f"{number}. " if number else ""
    trs=""
    for r in rows:
        trs+="<tr>"+ "".join(
          f"<td class='{'desc' if c=='description' else ''}'>{esc(r.get(c,''))}</td>" for c in COLS
        ) + (f"<td><b>{r.get('score',0)}%</b><br><small>{esc(r.get('reasons',''))}</small></td>" if "score" in r else "") +"</tr>"
    extra="<th>Match</th>" if rows and "score" in rows[0] else ""
    msg=f"<p class=muted>{esc(status)}</p>" if status else ""
    return f"""<div class=card><h2>{prefix}{esc(title)}</h2>{msg}<div class=scroll><table>
    <tr>{''.join('<th>'+h+'</th>' for h in HEADS)}{extra}</tr>
    {trs or '<tr><td colspan=11>No matching records.</td></tr>'}</table></div></div>"""

def page(title,body):
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>{esc(title)}</title><style>
    *{{box-sizing:border-box}}body{{margin:0;font-family:Arial;background:#efe4d2;color:#2d261f}}
    header{{background:#5d4937;color:white;padding:18px 24px}}nav{{background:#fffdf9;padding:10px 18px;display:flex;gap:8px;flex-wrap:wrap}}
    nav a,.btn,button{{background:#6c543f;color:white;text-decoration:none;padding:9px 12px;border-radius:7px;border:0;font-weight:800;cursor:pointer}}
    main{{max-width:1800px;margin:auto;padding:18px}}.card{{background:#fffdf9;border:1px solid #dccdbb;border-radius:13px;padding:14px;margin-bottom:14px}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}}input,textarea,select{{width:100%;padding:10px;border:1px solid #d0c1af;border-radius:8px}}
    label{{font-weight:800}}.scroll{{overflow:auto;max-height:68vh}}table{{width:100%;min-width:1450px;border-collapse:collapse;background:white}}
    th,td{{padding:9px;border-bottom:1px solid #eee0ce;text-align:left;vertical-align:top;font-size:12px}}th{{background:#f7ecdf;position:sticky;top:0}}
    .desc{{min-width:420px;max-width:650px;line-height:1.4}}.muted{{color:#7a6b5c}}.notice{{padding:10px;background:#fff3cd;border-radius:8px}}
    </style></head><body><header><h2 style='margin:0'>Alliance Live Property Intelligence V4.6</h2>
    <small>Clean availability + clean requirements + semantic matching across 4 sources</small></header>
    <nav><a href='/team-dashboard-v376'>← Dashboard</a><a href='/workspace'>Working Space</a><a href='/whatsapp-live'>WhatsApp Live</a>
    <a href='/whatsapp-live/feed'>Availabilities</a><a href='/whatsapp-live/requirements-live'>Requirements</a><a href='/property-match-ai-v46'>AI Property Match</a></nav>
    <main>{body}</main></body></html>"""

# -------------------- routes --------------------
def register(core):
    app=core.app;engine=core.engine
    router=APIRouter()

    # Take over only visible GET pages. Keep ingest/source POST routes.
    kept=[]
    for route in app.router.routes:
        path=getattr(route,"path",None);methods=getattr(route,"methods",set()) or set()
        if isinstance(route,APIRoute) and "GET" in methods and path in {"/whatsapp-live","/whatsapp-live/feed"}:
            continue
        kept.append(route)
    app.router.routes[:]=kept

    @router.get("/api/v46/status")
    def status():
        gen=latest_wa_generation(engine)
        _,mt=master_results(engine,parse_requirement("property"),1)
        return {"version":VERSION,"status":"OK","whatsapp_generation":str(gen) if gen else None,
                "master_table":mt,"rejected_visible":False,
                "semantic_search":True,"four_source_matcher":True}

    @router.get("/api/v46/semantic-search")
    def semantic_search(q:str,limit:int=50):
        req=parse_requirement(q)
        wa=whatsapp_availability(engine,req,limit)
        news=newspaper_results(engine,req,limit)
        master,mt=master_results(engine,req,limit)
        return {"query":q,"parsed":req,"count":len(wa)+len(news)+len(master),
                "whatsapp":wa,"newspaper":news,"master":master,"master_table":mt}

    @router.get("/whatsapp-live",response_class=HTMLResponse)
    def live_dashboard():
        gen=latest_wa_generation(engine)
        wa=whatsapp_availability(engine,parse_requirement("property rent sale commercial residential"),20)
        reqs=whatsapp_requirements()[:20]
        body=f"""<div class=grid><div class=card><b>Live canonical generation</b><h3>{esc(gen or 'Not ready')}</h3></div>
        <div class=card><b>Availability sample</b><h2>{len(wa)}</h2></div><div class=card><b>Active requirements sample</b><h2>{len(reqs)}</h2></div>
        <div class=card><b>Rejected/noise displayed</b><h2>0</h2></div></div>
        <div class=card><a class=btn href='/whatsapp-live/feed'>Open Clean Availabilities</a>
        <a class=btn href='/whatsapp-live/requirements-live'>Open Clean Requirements</a>
        <a class=btn href='/property-match-ai-v46'>Open AI Property Matcher</a></div>"""
        return HTMLResponse(page("WhatsApp Live",body))

    @router.get("/whatsapp-live/feed",response_class=HTMLResponse)
    def live_availability(request:Request):
        q=str(request.query_params.get("q") or "").strip()
        req=parse_requirement(q or "property rent sale commercial residential")
        rows=whatsapp_availability(engine,req,1000)
        if not q:
            # for default feed, sort latest-ish score is not meaningful but keeps only canonical properties.
            pass
        form=f"""<div class=card><form method=get><div class=grid><div><label>Search availability naturally</label>
        <input name=q value='{esc(q)}' placeholder='e.g. commercial rental property in Saket'></div>
        <div style='align-self:end'><button>Semantic Search</button></div></div></form>
        <p class=muted>Rejected, greetings, contacts-only and review/noise messages are not displayed here.</p></div>"""
        return HTMLResponse(page("Live Property Availabilities",form+render_table(rows,"WhatsApp Property Availability")))

    @router.get("/whatsapp-live/requirements-live",response_class=HTMLResponse)
    def live_requirements():
        rows=whatsapp_requirements()
        return HTMLResponse(page("Live Property Requirements",
          "<div class=notice>Only active property requirements are displayed. Rejected/noise messages are excluded.</div>"+
          render_table(rows,"WhatsApp Property Requirements")))

    @router.get("/property-match-ai-v46",response_class=HTMLResponse)
    def match_form():
        body="""<div class=card><h2>AI-Assisted Property Match</h2>
        <form method=post action='/property-match-ai-v46'>
        <label>Describe the requirement naturally</label>
        <textarea name=requirement rows=4 placeholder='Looking for commercial rental property in Saket, around 2000 sqft, budget 3 lakh per month' required></textarea>
        <div class=grid style='margin-top:12px'>
        <label><input type=checkbox name=s1 value=1 checked style='width:auto'> 1. WhatsApp Group</label>
        <label><input type=checkbox name=s2 value=1 checked style='width:auto'> 2. Newspaper Database</label>
        <label><input type=checkbox name=s3 value=1 checked style='width:auto'> 3. Master Database</label>
        <label><input type=checkbox name=s4 value=1 checked style='width:auto'> 4. Search / Discovery</label></div>
        <p><button>Find Best Matching Properties</button></p></form></div>"""
        return HTMLResponse(page("AI Property Match",body))

    @router.post("/property-match-ai-v46",response_class=HTMLResponse)
    def match_run(requirement:str=Form(...),s1:str|None=Form(None),s2:str|None=Form(None),s3:str|None=Form(None),s4:str|None=Form(None)):
        req=parse_requirement(requirement)
        sections=[]
        sections.append(f"""<div class=card><h2>Requirement understood by matcher</h2>
          <b>Original:</b> {esc(requirement)}<br><b>Transaction:</b> {esc(req['transaction'] or 'Any')}
          · <b>Type:</b> {esc(req['property_type'] or 'Any')} · <b>Area:</b> {esc(req['area'] or 'Any')}
          · <b>Budget:</b> {esc(req['budget'] or 'Any')}<br><b>Expanded search terms:</b> {esc(', '.join(req['expanded']))}</div>""")
        if s1:
            sections.append(render_table(whatsapp_availability(engine,req,100),"WhatsApp Group",1))
        else:sections.append(render_table([],"WhatsApp Group",1,"Source not selected"))
        if s2:
            sections.append(render_table(newspaper_results(engine,req,100),"Newspaper Database",2))
        else:sections.append(render_table([],"Newspaper Database",2,"Source not selected"))
        if s3:
            rows,mt=master_results(engine,req,100)
            sections.append(render_table(rows,"Master Database",3,f"Detected table: {mt}" if mt else "No supported master table detected"))
        else:sections.append(render_table([],"Master Database",3,"Source not selected"))
        if s4:
            rows,err=discovery_results(req,50)
            sections.append(render_table(rows,"Search / Discovery",4,err))
        else:sections.append(render_table([],"Search / Discovery",4,"Source not selected"))
        return HTMLResponse(page("AI Match Results","".join(sections)))

    app.include_router(router)
    return router
