from __future__ import annotations
import html, json, os, re, threading
from datetime import datetime, timezone
from urllib.parse import urlparse
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

VERSION="12.4.25-AUTOMATIC-HOSPITALITY-INTELLIGENCE"
CATEGORIES=("RESTAURANT","CAFE","LOUNGE","CLUB","BANQUET","GUEST_HOUSE","HOTEL","BAR","CLOUD_KITCHEN")
MARKETS=("Delhi","Gurugram","Noida","Greater Noida","Ghaziabad","Faridabad")
BAD={"9876543210","9999999999","8888888888","1234567890","0000000000"}
WEAK=("justdial.","tripadvisor.","zomato.","swiggy.","magicpin.","sloshout.","wedmegood.","wanderlog.","agoda.","facebook.","instagram.","youtube.")
GENERIC=("top 10","top 20","best restaurants","best cafes","best hotels","list of ","directory","near me","search results","things to do")
_LOCK=threading.Lock()
_THREAD=None
_STOP=threading.Event()

def _app(core): return getattr(core,"app",None) or core
def _login(core,req):
    fn=getattr(core,"need_login",None); return fn(req) if fn else "team"
def _norm(v): return re.sub(r"\s+"," ",str(v or "").strip())
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
def _clean_name(v):
    s=_norm(v)
    s=re.sub(r"\s+[|–—]\s+.*$","",s)
    s=re.sub(r"\s+-\s+(Delhi|New Delhi|Gurugram|Gurgaon|Noida|Greater Noida|Ghaziabad|Faridabad).*$","",s,flags=re.I)
    return _norm(s)[:180]
def _plausible(v):
    s=_norm(v); low=s.lower()
    return 3<=len(s)<=180 and not any(x in low for x in GENERIC) and 1<=len(re.findall(r"[A-Za-z0-9&']+",s))<=14
def _aliases(m):
    return {"Delhi":("delhi","new delhi"),"Gurugram":("gurugram","gurgaon"),"Noida":("noida",),"Greater Noida":("greater noida",),"Ghaziabad":("ghaziabad",),"Faridabad":("faridabad",)}.get(m,(m.lower(),))
def _market_ok(m,s): return any(x in _norm(s).lower() for x in _aliases(m))
def _blob(i): return " ".join(_norm(i.get(k)) for k in ("name","title","summary","snippet","description","url"))
def _queries(cat,market):
    c=cat.replace("_"," ").lower()
    return (f'{c} "{market}" phone contact',f'{c} "{market}" mobile whatsapp',f'{c} "{market}" official website contact',f'{c} "{market}" reservations phone')

def ensure_schema(engine):
    with engine.begin() as c:
        c.execute(text("""CREATE TABLE IF NOT EXISTS ai_hospitality_auto_control_v12425(
        singleton_id INT PRIMARY KEY DEFAULT 1 CHECK(singleton_id=1),enabled BOOLEAN NOT NULL DEFAULT TRUE,
        interval_minutes INT NOT NULL DEFAULT 60,cursor_index INT NOT NULL DEFAULT 0,last_run_at TIMESTAMPTZ,
        next_run_at TIMESTAMPTZ,last_status TEXT,last_error TEXT,total_cycles BIGINT NOT NULL DEFAULT 0,
        total_queries BIGINT NOT NULL DEFAULT 0,total_candidates BIGINT NOT NULL DEFAULT 0,
        total_promoted BIGINT NOT NULL DEFAULT 0,total_enriched BIGINT NOT NULL DEFAULT 0,
        total_noise BIGINT NOT NULL DEFAULT 0,updated_at TIMESTAMPTZ DEFAULT NOW())"""))
        c.execute(text("INSERT INTO ai_hospitality_auto_control_v12425(singleton_id,enabled,interval_minutes) VALUES(1,TRUE,60) ON CONFLICT(singleton_id) DO NOTHING"))
        c.execute(text("""CREATE TABLE IF NOT EXISTS ai_hospitality_auto_candidate_v12425(
        candidate_id BIGSERIAL PRIMARY KEY,business_name TEXT NOT NULL,category TEXT NOT NULL,market TEXT NOT NULL,
        phone TEXT,email TEXT,website TEXT,source_url TEXT NOT NULL,confidence INT NOT NULL,decision TEXT NOT NULL,
        raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,first_seen_at TIMESTAMPTZ DEFAULT NOW(),last_seen_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(business_name,market,source_url,phone))"""))
    return True

def _control(engine):
    ensure_schema(engine)
    with engine.connect() as c: r=c.execute(text("SELECT * FROM ai_hospitality_auto_control_v12425 WHERE singleton_id=1")).mappings().first()
    return dict(r) if r else {}

def _candidate(engine,rec):
    with engine.begin() as c:
        c.execute(text("""INSERT INTO ai_hospitality_auto_candidate_v12425(
        business_name,category,market,phone,email,website,source_url,confidence,decision,raw_payload,last_seen_at)
        VALUES(:business_name,:category,:market,:phone,:email,:website,:source_url,:confidence,:decision,CAST(:raw_payload AS jsonb),NOW())
        ON CONFLICT(business_name,market,source_url,phone) DO UPDATE SET
        confidence=GREATEST(ai_hospitality_auto_candidate_v12425.confidence,EXCLUDED.confidence),
        decision=EXCLUDED.decision,email=COALESCE(NULLIF(EXCLUDED.email,''),ai_hospitality_auto_candidate_v12425.email),
        website=COALESCE(NULLIF(EXCLUDED.website,''),ai_hospitality_auto_candidate_v12425.website),
        raw_payload=EXCLUDED.raw_payload,last_seen_at=NOW()"""),rec)

def _existing(engine,name,market):
    with engine.connect() as c:
        rows=[dict(x) for x in c.execute(text("""SELECT hospitality_id,location,city,contact_phone,email,website
        FROM ai_hospitality_entity WHERE active=TRUE AND LOWER(TRIM(business_name))=LOWER(TRIM(:n))"""),{"n":name}).mappings().all()]
    for r in rows:
        b=(_norm(r.get("location"))+" "+_norm(r.get("city"))).lower()
        if any(x in b for x in _aliases(market)): return r
    return None

def _promote(engine,rec,item):
    ex=_existing(engine,rec["business_name"],rec["market"])
    if ex:
        with engine.begin() as c:
            c.execute(text("""UPDATE ai_hospitality_entity SET
            contact_phone=CASE WHEN COALESCE(contact_phone,'')='' THEN :p ELSE contact_phone END,
            email=CASE WHEN COALESCE(email,'')='' THEN :e ELSE email END,
            website=CASE WHEN COALESCE(website,'')='' THEN :w ELSE website END,
            last_seen_at=NOW(),updated_at=NOW() WHERE hospitality_id=:id"""),
            {"p":rec.get("phone"),"e":rec.get("email"),"w":rec.get("website"),"id":int(ex["hospitality_id"])})
            c.execute(text("""INSERT INTO ai_hospitality_source_history(
            hospitality_id,source_type,source_name,source_url,evidence_text,raw_payload,seen_at)
            VALUES(:id,'AUTO_DISCOVERY_12425','LANGSEARCH',:u,:ev,CAST(:raw AS jsonb),NOW())"""),
            {"id":int(ex["hospitality_id"]),"u":rec["source_url"],"ev":f"confidence={rec['confidence']}; phone={rec.get('phone') or ''}","raw":json.dumps(item,default=str)})
        return "ENRICHED"
    import alliance_v31_hospitality as h
    h.upsert_hospitality(engine,{"business_name":rec["business_name"],"category":rec["category"],"location":rec["market"],"city":rec["market"],"contact_phone":rec.get("phone"),"email":rec.get("email"),"website":rec.get("website"),"verification_status":"UNVERIFIED"},
    {"source_type":"AUTO_DISCOVERY_12425","source_name":"LANGSEARCH","source_url":rec["source_url"],"evidence_text":f"confidence={rec['confidence']}; phone={rec.get('phone') or ''}","raw_payload":item})
    return "PROMOTED"

def _process(engine,cat,market,item):
    name=_clean_name(item.get("name") or item.get("title")); blob=_blob(item); url=_norm(item.get("url"))
    if not url or not _plausible(name) or not _market_ok(market,blob): return "NOISE"
    ps=_phones(blob); es=_emails(blob)
    score=25+20+(30 if ps else 0)+(10 if es else 0)+(15 if not _weak(url) else 0)
    decision="PROMOTE" if ps and score>=75 else ("REVIEW" if score>=50 else "NOISE")
    rec={"business_name":name,"category":cat,"market":market,"phone":ps[0] if ps else None,"email":es[0] if es else None,
         "website":url if not _weak(url) else None,"source_url":url,"confidence":score,"decision":decision,"raw_payload":json.dumps(item,default=str)}
    _candidate(engine,rec)
    if decision!="PROMOTE": return decision
    return _promote(engine,rec,item)

def run_cycle(engine):
    ensure_schema(engine)
    lock=engine.connect()
    if not bool(lock.execute(text("SELECT pg_try_advisory_lock(12425001)")).scalar()):
        lock.close(); return {"status":"SKIP_LOCKED","version":VERSION}
    qn=seen=prom=enr=noise=err=0
    try:
        ctl=_control(engine); idx=int(ctl.get("cursor_index") or 0)
        combos=[(c,m) for m in MARKETS for c in CATEGORIES]
        cat,market=combos[idx%len(combos)]
        import alliance_v31_hospitality as h
        for q in _queries(cat,market):
            r=h._langsearch(q,8); qn+=1
            if r.get("status")!="OK": err+=1; continue
            for item in r.get("results") or []:
                seen+=1
                try:
                    state=_process(engine,cat,market,item)
                    if state=="PROMOTED": prom+=1
                    elif state=="ENRICHED": enr+=1
                    elif state=="NOISE": noise+=1
                except Exception: err+=1
        status="PASS" if err==0 else ("PARTIAL" if seen else "ERROR")
        with engine.begin() as c:
            c.execute(text("""UPDATE ai_hospitality_auto_control_v12425 SET cursor_index=:n,last_run_at=NOW(),
            next_run_at=NOW() + (interval_minutes || ' minutes')::interval,last_status=:s,last_error=:e,
            total_cycles=total_cycles+1,total_queries=total_queries+:q,total_candidates=total_candidates+:seen,
            total_promoted=total_promoted+:p,total_enriched=total_enriched+:en,total_noise=total_noise+:noise,updated_at=NOW()
            WHERE singleton_id=1"""),
            {"n":(idx+1)%len(combos),"s":status,"e":None if err==0 else f"{err} errors","q":qn,"seen":seen,"p":prom,"en":enr,"noise":noise})
        return {"status":status,"version":VERSION,"category":cat,"market":market,"queries":qn,"results_seen":seen,"promoted":prom,"enriched":enr,"noise":noise,"errors":err}
    finally:
        try: lock.execute(text("SELECT pg_advisory_unlock(12425001)"))
        except Exception: pass
        lock.close()

def _loop(engine):
    while not _STOP.is_set():
        try:
            ctl=_control(engine)
            if bool(ctl.get("enabled",True)):
                due=ctl.get("next_run_at")
                if due is None or datetime.now(timezone.utc)>=due: run_cycle(engine)
            _STOP.wait(30)
        except Exception:
            _STOP.wait(60)

def start_worker(engine):
    global _THREAD
    with _LOCK:
        if _THREAD and _THREAD.is_alive(): return {"status":"ALREADY_RUNNING","version":VERSION}
        _STOP.clear()
        _THREAD=threading.Thread(target=_loop,args=(engine,),daemon=True,name="hospitality-auto-v12425")
        _THREAD.start()
        return {"status":"STARTED","version":VERSION}

def _status(engine):
    ctl=_control(engine)
    with engine.connect() as c:
        active=int(c.execute(text("SELECT COUNT(*) FROM ai_hospitality_entity WHERE active=TRUE")).scalar() or 0)
        phones=int(c.execute(text("SELECT COUNT(*) FROM ai_hospitality_entity WHERE active=TRUE AND COALESCE(contact_phone,'')<>''")).scalar() or 0)
        review=int(c.execute(text("SELECT COUNT(*) FROM ai_hospitality_auto_candidate_v12425 WHERE decision='REVIEW'")).scalar() or 0)
    return {"version":VERSION,"worker_alive":bool(_THREAD and _THREAD.is_alive()),"active_businesses":active,"with_phone":phones,"review_candidates":review,"control":ctl}

def register(core):
    app=_app(core); ensure_schema(core.engine)
    app.router.routes[:]=[r for r in list(app.router.routes) if getattr(r,"path",None) not in {"/hospitality-auto","/api/alliance/hospitality-auto/status","/api/alliance/hospitality-auto/run-now"}]
    @app.get("/hospitality-auto",response_class=HTMLResponse)
    def page(req:Request):
        _login(core,req); st=_status(core.engine); ctl=st["control"]
        return HTMLResponse(f"""<html><body><h1>Hospitality Automatic Intelligence - 12.4.25</h1>
        <p>Worker: <b>{'RUNNING' if st['worker_alive'] else 'STOPPED'}</b></p>
        <p>Active businesses: {st['active_businesses']} | With phone: {st['with_phone']} | Review candidates: {st['review_candidates']}</p>
        <p>Last status: {html.escape(str(ctl.get('last_status') or 'Not run yet'))}</p>
        <p>Last run: {html.escape(str(ctl.get('last_run_at') or ''))}</p>
        <p>Next run: {html.escape(str(ctl.get('next_run_at') or ''))}</p>
        <p>Total cycles: {ctl.get('total_cycles',0)} | Promoted: {ctl.get('total_promoted',0)} | Enriched: {ctl.get('total_enriched',0)}</p>
        <p><a href='/hospitality-intelligence'>Open Hospitality Intelligence</a></p>
        <p>Safety: one category-market slice per cycle, DB advisory lock, phone-backed auto-promotion only, review staging for uncertain candidates, no overwrite of existing non-empty contacts.</p>
        </body></html>""",headers={"Cache-Control":"no-store"})
    @app.get("/api/alliance/hospitality-auto/status")
    def status(req:Request):
        _login(core,req); return JSONResponse({"status":"PASS",**_status(core.engine)})
    @app.post("/api/alliance/hospitality-auto/run-now")
    def run_now(req:Request):
        _login(core,req); return JSONResponse(run_cycle(core.engine))
    return {"status":"REGISTERED","version":VERSION,"worker":start_worker(core.engine),"automatic":True,"master_database":"/hospitality-intelligence","status_page":"/hospitality-auto"}
