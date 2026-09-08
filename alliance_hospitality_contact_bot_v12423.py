from __future__ import annotations
import html, json, re, threading
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlparse
from fastapi import Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION="12.4.23-TRUE-HOSPITALITY-CONTACT-DISCOVERY"
CATEGORIES=("RESTAURANT","CAFE","LOUNGE","CLUB","BANQUET","GUEST_HOUSE","HOTEL","BAR","CLOUD_KITCHEN")
MARKETS=("Delhi","Gurugram","Noida","Greater Noida","Ghaziabad","Faridabad")
WEAK=("justdial.","tripadvisor.","zomato.","swiggy.","magicpin.","sloshout.","wedmegood.","wanderlog.","agoda.","facebook.","instagram.")
BAD={"9876543210","9999999999","8888888888","1234567890","0000000000"}
LOCK=threading.Lock()
JOB={"running":False,"requested":0,"category":"ALL","market":"ALL","queries":0,"results_seen":0,"new_businesses":0,"existing_enriched":0,"phones_added":0,"emails_added":0,"duplicates_avoided":0,"noise_rejected":0,"errors":0,"started_at":None,"completed_at":None,"last_message":"Ready"}

def _app(core): return getattr(core,"app",None) or core
def _login(core,req):
    fn=getattr(core,"need_login",None); return fn(req) if fn else "team"
def _utc(): return datetime.now(timezone.utc).isoformat()
def _norm(v): return re.sub(r"\s+"," ",str(v or "").strip())
def _e(v): return html.escape("" if v is None else str(v),quote=True)
def _host(url):
    try: return urlparse(_norm(url)).netloc.lower().replace("www.","")
    except Exception: return ""
def _weak(url): return any(x in _host(url) for x in WEAK)
def _phone(v):
    d=re.sub(r"\D","",str(v or ""))
    if len(d)>10: d=d[-10:]
    if len(d)!=10 or d[0] not in "6789" or d in BAD or len(set(d))<4: return None
    return d
def _phones(s):
    out=[]
    for raw in re.findall(r"(?<!\d)(?:(?:\+?91|0)[\s().-]*)?[6-9](?:[\s().-]*\d){9}(?!\d)",str(s or "")):
        p=_phone(raw)
        if p and p not in out: out.append(p)
    return out
def _emails(s):
    out=[]
    for x in re.findall(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",str(s or "")):
        x=x.lower()
        if x not in out and x!="you@email.com": out.append(x)
    return out
def _clean_title(v):
    s=_norm(v)
    s=re.sub(r"\s+[|–—]\s+.*$","",s)
    s=re.sub(r"\s+-\s+(Delhi|New Delhi|Gurugram|Gurgaon|Noida|Greater Noida|Ghaziabad|Faridabad).*$","",s,flags=re.I)
    return _norm(s)[:180]
def _plausible(name):
    low=_norm(name).lower()
    bad=("top 10","top 20","best restaurants","best cafes","best hotels","list of ","directory","near me","search results")
    return 3<=len(name)<=180 and not any(x in low for x in bad) and len(re.findall(r"[A-Za-z0-9]+",name))<=14
def _blob(item): return " ".join(_norm(item.get(k)) for k in ("name","title","summary","snippet","description","url"))
def _market_ok(market,blob):
    if market=="ALL": return True
    b=blob.lower()
    aliases={"Delhi":("delhi","new delhi"),"Gurugram":("gurugram","gurgaon"),"Noida":("noida",),"Greater Noida":("greater noida",),"Ghaziabad":("ghaziabad",),"Faridabad":("faridabad",)}
    return any(x in b for x in aliases.get(market,(market.lower(),)))
def _queries(cat,market):
    c=cat.replace("_"," ").lower()
    return [f'{c} "{market}" phone contact',f'{c} "{market}" mobile whatsapp',f'{c} "{market}" official website contact',f'{c} "{market}" reservations phone']

def ensure_schema(engine):
    with engine.begin() as c:
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_hospitality_discovery_candidate_v12423(
          candidate_id BIGSERIAL PRIMARY KEY,business_name TEXT NOT NULL,category TEXT NOT NULL,
          market TEXT NOT NULL,phone TEXT,email TEXT,website TEXT,source_url TEXT NOT NULL,
          confidence INT NOT NULL DEFAULT 0,decision TEXT NOT NULL DEFAULT 'REVIEW',
          raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,first_seen_at TIMESTAMPTZ DEFAULT NOW(),
          last_seen_at TIMESTAMPTZ DEFAULT NOW(),UNIQUE(business_name,market,source_url,phone)
        )"""))
    return True

def _candidate(engine,rec):
    with engine.begin() as c:
        c.execute(text("""
        INSERT INTO ai_hospitality_discovery_candidate_v12423(
          business_name,category,market,phone,email,website,source_url,confidence,decision,raw_payload,last_seen_at
        ) VALUES(:business_name,:category,:market,:phone,:email,:website,:source_url,:confidence,:decision,CAST(:raw_payload AS jsonb),NOW())
        ON CONFLICT(business_name,market,source_url,phone) DO UPDATE SET
          confidence=GREATEST(ai_hospitality_discovery_candidate_v12423.confidence,EXCLUDED.confidence),
          decision=EXCLUDED.decision,email=COALESCE(NULLIF(EXCLUDED.email,''),ai_hospitality_discovery_candidate_v12423.email),
          website=COALESCE(NULLIF(EXCLUDED.website,''),ai_hospitality_discovery_candidate_v12423.website),last_seen_at=NOW()
        """),rec)

def _existing(engine,name,market):
    with engine.connect() as c:
        rows=[dict(x) for x in c.execute(text("""
        SELECT hospitality_id,location,city,contact_phone,email,website FROM ai_hospitality_entity
        WHERE active=TRUE AND LOWER(TRIM(business_name))=LOWER(TRIM(:n))
        """),{"n":name}).mappings().all()]
    aliases={"Gurugram":("gurugram","gurgaon"),"Delhi":("delhi","new delhi"),"Noida":("noida",),"Greater Noida":("greater noida",),"Ghaziabad":("ghaziabad",),"Faridabad":("faridabad",)}
    for r in rows:
        b=(_norm(r.get("location"))+" "+_norm(r.get("city"))).lower()
        if any(x in b for x in aliases.get(market,(market.lower(),))): return r
    return None

def _process(engine,cat,market,item):
    name=_clean_title(item.get("name") or item.get("title"))
    blob=_blob(item); url=_norm(item.get("url"))
    if not url or not _plausible(name) or not _market_ok(market,blob): return {"decision":"NOISE"}
    ps=_phones(blob); es=_emails(blob)
    score=25+(35 if ps else 0)+(10 if es else 0)+(15 if _market_ok(market,blob) else 0)+(10 if not _weak(url) else 0)
    decision="PROMOTE" if ps and score>=70 else ("REVIEW" if score>=50 else "NOISE")
    rec={"business_name":name,"category":cat,"market":market,"phone":ps[0] if ps else None,"email":es[0] if es else None,
         "website":url if not _weak(url) else None,"source_url":url,"confidence":score,"decision":decision,"raw_payload":json.dumps(item,default=str)}
    _candidate(engine,rec)
    if decision!="PROMOTE": return {"decision":decision}
    ex=_existing(engine,name,market)
    if ex:
        with engine.begin() as c:
            c.execute(text("""UPDATE ai_hospitality_entity SET
              contact_phone=CASE WHEN COALESCE(contact_phone,'')='' THEN :p ELSE contact_phone END,
              email=CASE WHEN COALESCE(email,'')='' THEN :e ELSE email END,
              website=CASE WHEN COALESCE(website,'')='' THEN :w ELSE website END,
              last_seen_at=NOW(),updated_at=NOW() WHERE hospitality_id=:id"""),
              {"p":rec["phone"],"e":rec["email"],"w":rec["website"],"id":int(ex["hospitality_id"])})
            c.execute(text("""INSERT INTO ai_hospitality_source_history(
              hospitality_id,source_type,source_name,source_url,evidence_text,raw_payload,seen_at
            ) VALUES(:id,'CONTACT_DISCOVERY_12423','LANGSEARCH',:u,:ev,CAST(:raw AS jsonb),NOW())"""),
              {"id":int(ex["hospitality_id"]),"u":url,"ev":f"confidence={score}; phone={rec['phone'] or ''}","raw":rec["raw_payload"]})
        return {"decision":"ENRICHED","phone_added":1 if not _norm(ex.get("contact_phone")) and rec["phone"] else 0,"email_added":1 if not _norm(ex.get("email")) and rec["email"] else 0,"duplicate":1}
    import alliance_v31_hospitality as h
    h.upsert_hospitality(engine,{"business_name":name,"category":cat,"location":market,"city":market,"contact_phone":rec["phone"],"email":rec["email"],"website":rec["website"],"verification_status":"UNVERIFIED"},
      {"source_type":"CONTACT_DISCOVERY_12423","source_name":"LANGSEARCH","source_url":url,"evidence_text":f"confidence={score}; phone={rec['phone'] or ''}","raw_payload":item})
    return {"decision":"CREATED","phone_added":1 if rec["phone"] else 0,"email_added":1 if rec["email"] else 0,"duplicate":0}

def _worker(engine,target,category,market):
    global JOB
    import alliance_v31_hospitality as h
    cats=list(CATEGORIES) if category=="ALL" else [category]
    markets=list(MARKETS) if market=="ALL" else [market]
    new=enr=ph=em=dupes=noise=errs=qs=seen=0
    stop=False
    try:
        for mk in markets:
            if stop: break
            for cat in cats:
                if stop: break
                for q in _queries(cat,mk):
                    if new+enr>=target: stop=True; break
                    r=h._langsearch(q,8); qs+=1
                    if r.get("status")!="OK": errs+=1; continue
                    for item in r.get("results") or []:
                        if new+enr>=target: stop=True; break
                        seen+=1
                        try:
                            o=_process(engine,cat,mk,item); d=o.get("decision")
                            if d=="CREATED": new+=1
                            elif d=="ENRICHED": enr+=1
                            elif d=="NOISE": noise+=1
                            ph+=int(o.get("phone_added") or 0); em+=int(o.get("email_added") or 0); dupes+=int(o.get("duplicate") or 0)
                        except Exception: errs+=1
                        with LOCK: JOB.update({"queries":qs,"results_seen":seen,"new_businesses":new,"existing_enriched":enr,"phones_added":ph,"emails_added":em,"duplicates_avoided":dupes,"noise_rejected":noise,"errors":errs,"last_message":f"Running: {new} new / {enr} enriched / {ph} phones"})
    except Exception as exc:
        errs+=1
        with LOCK: JOB["last_message"]=f"ERROR: {type(exc).__name__}: {exc}"
    with LOCK:
        JOB.update({"running":False,"queries":qs,"results_seen":seen,"new_businesses":new,"existing_enriched":enr,"phones_added":ph,"emails_added":em,"duplicates_avoided":dupes,"noise_rejected":noise,"errors":errs,"completed_at":_utc(),"last_message":f"Completed: {new} new / {enr} enriched / {ph} phones / {errs} errors"})

def _start(engine,target,category,market):
    target=max(1,min(int(target),250)); category=(category or "ALL").upper(); market=_norm(market or "ALL")
    if category!="ALL" and category not in CATEGORIES: category="ALL"
    if market!="ALL" and market not in MARKETS: market="ALL"
    with LOCK:
        if JOB["running"]: return False,"A discovery job is already running."
        JOB.update({"running":True,"requested":target,"category":category,"market":market,"queries":0,"results_seen":0,"new_businesses":0,"existing_enriched":0,"phones_added":0,"emails_added":0,"duplicates_avoided":0,"noise_rejected":0,"errors":0,"started_at":_utc(),"completed_at":None,"last_message":f"Started target {target}"})
    threading.Thread(target=_worker,args=(engine,target,category,market),daemon=True,name="hospitality-contact-v12423").start()
    return True,f"Started target {target}."

def _counts(engine):
    ensure_schema(engine)
    with engine.connect() as c:
        return {"active":int(c.execute(text("SELECT COUNT(*) FROM ai_hospitality_entity WHERE active=TRUE")).scalar() or 0),
                "phones":int(c.execute(text("SELECT COUNT(*) FROM ai_hospitality_entity WHERE active=TRUE AND COALESCE(contact_phone,'')<>''")).scalar() or 0),
                "candidates":int(c.execute(text("SELECT COUNT(*) FROM ai_hospitality_discovery_candidate_v12423")).scalar() or 0)}

def _page(core,req,msg=""):
    _login(core,req); c=_counts(core.engine)
    with LOCK: j=dict(JOB)
    cats='<option value="ALL">ALL CATEGORIES</option>'+''.join(f'<option value="{x}">{x.replace("_"," ")}</option>' for x in CATEGORIES)
    markets='<option value="ALL">ALL DELHI NCR</option>'+''.join(f'<option value="{x}">{x}</option>' for x in MARKETS)
    metrics=[("Active Businesses",c["active"]),("With Phone",c["phones"]),("Discovery Candidates",c["candidates"]),("New This Job",j["new_businesses"]),("Existing Enriched",j["existing_enriched"]),("Phones Added",j["phones_added"]),("Emails Added",j["emails_added"]),("Duplicates Avoided",j["duplicates_avoided"]),("Noise Rejected",j["noise_rejected"]),("Errors",j["errors"])]
    mh=''.join(f'<div class="m"><span>{_e(k)}</span><strong>{v}</strong></div>' for k,v in metrics)
    return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Hospitality Contact Discovery Bot</title>
<style>*{{box-sizing:border-box}}body{{font-family:Arial;background:#f5f7fb;color:#172033;margin:0}}.w{{max-width:1500px;margin:auto;padding:18px}}.card{{background:#fff;border:1px solid #dfe6ee;border-radius:12px;padding:15px;margin-bottom:14px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}}.m{{background:#f8fafc;border:1px solid #e4e7ec;border-radius:10px;padding:12px}}.m strong{{display:block;font-size:25px}}.btn,button{{background:#102a43;color:#fff;border:0;border-radius:8px;padding:10px 12px;text-decoration:none;cursor:pointer}}select{{padding:9px;margin:4px;border:1px solid #cfd8e3;border-radius:7px}}.ok{{background:#ecfdf3;padding:10px;border-radius:8px}}</style></head>
<body><div class="w"><div class="card"><h1>Hospitality Contact Discovery Bot - 12.4.23</h1><p>TRUE discovery: new hospitality businesses + contact enrichment. Master database stays at Hospitality Intelligence.</p><a class="btn" href="/hospitality-intelligence">Open Hospitality Intelligence</a> <a class="btn" href="/alliance/primary">Back to Dashboard</a></div>
{f'<div class="ok">{_e(msg)}</div>' if msg else ''}<div class="card"><div class="grid">{mh}</div></div>
<div class="card"><h2>Discover More Contacts</h2><form method="post" action="/v3/hospitality-intelligence/discover"><select name="category">{cats}</select><select name="market">{markets}</select><select name="limit"><option value="10">Test 10</option><option value="50">Fetch 50</option><option value="100">Fetch 100</option><option value="250">Fetch 250</option></select><button>Run Contact Discovery</button></form></div>
<div class="card"><h2>Current Job</h2><p><b>{"RUNNING" if j["running"] else "READY"}</b> - {_e(j["last_message"])}</p><p>Queries {j["queries"]} - Results seen {j["results_seen"]}</p><a class="btn" href="/v3/hospitality-intelligence">Refresh</a></div>
<div class="card"><b>Repair rule:</b> 12.4.22 only recovered phones for existing businesses. 12.4.23 adds true new-business discovery, candidate staging, branch-aware duplicate protection and preserves existing non-empty contacts.</div></div></body></html>""",headers={"Cache-Control":"no-store"})

def _remove(app,path):
    keep=[]; n=0
    for r in list(app.router.routes):
        if getattr(r,"path",None)==path: n+=1
        else: keep.append(r)
    app.router.routes[:]=keep
    return n

def register(core):
    app=_app(core); ensure_schema(core.engine)
    n=sum(_remove(app,p) for p in ("/v3/hospitality-intelligence","/v3/hospitality-intelligence/run","/v3/hospitality-intelligence/discover","/api/alliance/hospitality-contact-bot/status"))
    @app.get("/v3/hospitality-intelligence",response_class=HTMLResponse)
    def page(req:Request,msg:str=Query("")): return _page(core,req,msg)
    @app.post("/v3/hospitality-intelligence/discover")
    def discover(req:Request,category:str=Form("ALL"),market:str=Form("ALL"),limit:int=Form(10)):
        _login(core,req); _,msg=_start(core.engine,limit,category,market); return RedirectResponse("/v3/hospitality-intelligence?msg="+quote_plus(msg),303)
    @app.get("/api/alliance/hospitality-contact-bot/status")
    def status(req:Request):
        _login(core,req)
        with LOCK: j=dict(JOB)
        return {"status":"PASS","version":VERSION,"true_new_business_discovery":True,"master_database_route":"/hospitality-intelligence","contact_bot_route":"/v3/hospitality-intelligence","candidate_staging":True,"branch_duplicate_guard":True,"existing_nonempty_contacts_preserved":True,"job":j,"coverage":_counts(core.engine)}
    return {"status":"AUTHORITATIVE","version":VERSION,"removed_stale_routes":n,"true_new_business_discovery":True,"master_database_unchanged":True}
