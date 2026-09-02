from __future__ import annotations
import json,re,unicodedata,uuid
from fastapi import Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text
import alliance_property_brain_foundation_v1 as foundation
VERSION="2.6.0-MAGIC-SOURCE-TRUTH-ENGINE"; ENGINE_VERSION="ALLIANCE_MAGIC_EXAMINER_V1"
DDL="""CREATE TABLE IF NOT EXISTS alliance_magic_examiner_v26(
magic_id UUID PRIMARY KEY,entity_id TEXT NOT NULL UNIQUE,message_id TEXT,source_class TEXT NOT NULL,
source_truth JSONB NOT NULL DEFAULT '{}'::jsonb,live_conflicts JSONB NOT NULL DEFAULT '[]'::jsonb,
grounded_score NUMERIC(5,2),decision TEXT NOT NULL,engine_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
def _engine(c): return foundation._engine_from_core(c)
def _app(c): return getattr(c,"app",None) or c
def _loads(v,d): return foundation._loads(v,d)
def _norm(s): return re.sub(r"[ \t]+"," ",unicodedata.normalize("NFKC",str(s or "")).replace("₹"," Rs "))
def _install(e):
    with e.begin() as c:c.execute(text(DDL))
def _phones(t):
    z=[]
    for m in re.finditer(r"(?<!\d)(?:\+?91[\s-]?)?([6-9]\d(?:[\s-]?\d){8})(?!\d)",t):
        d=re.sub(r"\D","",m.group(1))
        if len(d)==10:z.append({"value":d,"evidence":m.group(0).strip()})
    return list({x["value"]:x for x in z}.values())
def _analyse(r):
    raw=_norm(r.get("raw_text") or r.get("parent_message_text") or ""); low=raw.casefold()
    if re.search(r"\b(funds?\s+available|loan\s+available|finance\s+available|against\s+collateral|against\s+colleteral)\b",low):
        cls="NOT_PROPERTY"
    elif re.search(r"\b(requirement|wanted|looking\s+for|need|required)\b",low): cls="REQUIREMENT"
    else: cls="PROPERTY_AVAILABILITY"
    sale=bool(re.search(r"\b(for\s*sale|sale|selling|demand|asking|reserve\s*price|owner'?s?\s*wants?)\b",low))
    rent=bool(re.search(r"\b(for\s*rent|for\s*lease|on\s*lease|lease\s*available|rent\s*[-:@]|rental)\b",low))
    occupied=bool(re.search(r"\b(rented|tenanted|leased\s*out|rental\s*income|income\s*rent)\b",low))
    tx="BOTH" if sale and (rent or occupied) else ("SALE" if sale else ("RENT" if rent else None))
    truth={"source_class":{"value":cls,"evidence":"literal source classification"}}
    if tx: truth["transaction_type"]={"value":tx,"evidence":"literal transaction signals"}
    types=[]
    for v,p in [("GUEST_HOUSE",r"\bguest\s*house\b"),("HOTEL",r"\bhotel\b"),("BANQUET",r"\bbanquet\b"),("FARMHOUSE",r"\bfarm\s*house\b|\bfarmhouse\b"),("WAREHOUSE",r"\bwarehouse\b"),("INDUSTRIAL",r"\bindustrial\b"),("OFFICE",r"\boffice\b"),("SHOP",r"\bshop\b"),("BASEMENT",r"\bbasement\b|\bbasment\b"),("VILLA",r"\bvilla\b|\bmansion\b"),("KOTHI",r"\bkothi\b"),("FLOOR",r"\bfloor\b"),("PLOT",r"\bplot\b"),("LAND",r"\bland\b"),("BHK",r"\b[1-9]\s*bhk\b")]:
        m=re.search(p,low)
        if m:types.append({"value":v,"evidence":m.group(0)})
    if types:truth["property_type"]=types
    ph=_phones(raw)
    if ph:truth["contacts"]=ph
    areas=[{"value":m.group(0),"evidence":m.group(0)} for m in re.finditer(r"(?<!\d)\d+(?:\.\d+)?\s*(?:sq\.?\s*(?:ft|feet|yard|yards|yd|yds|m|mt|meter|metre)|sqft|sqyd|sqmt|gaj|bigha|acre|hectare)",raw,re.I)]
    if areas:truth["areas"]=areas[:10]
    money=[{"value":m.group(0),"evidence":m.group(0)} for m in re.finditer(r"(?:Rs\.?\s*|INR\s*)?\d+(?:\.\d+)?\s*(?:cr|crore|lac|lakh|lacs|lakhs|k)\b",raw,re.I)]
    if money:truth["money"]=money[:10]
    loc=[]
    for p in [r"\b(?:location|located)\s*[:\-]\s*([^\n|]{2,70})",r"\b(?:for\s+sale|for\s+rent|for\s+lease)\s+(?:in|at)\s+([^\n|]{2,70})"]:
        for m in re.finditer(p,raw,re.I):loc.append({"value":m.group(1).strip(" .-*"),"evidence":m.group(0).strip()})
    if loc:truth["literal_location"]=loc[:5]
    if re.search(r"\bfully\s+furnished\b",low):truth["furnishing"]={"value":"FULLY_FURNISHED","evidence":"fully furnished"}
    if re.search(r"\b(stilt\s+parking|car\s+parking|parking)\b",low):truth["parking"]={"value":"MENTIONED","evidence":"literal parking"}
    fq=_loads(r.get("field_quality"),{});conf=[]
    lv=(fq.get("transaction_type") or {}).get("live_value")
    if tx and lv and str(lv).upper()!=tx:conf.append({"field":"transaction_type","live_value":lv,"source_truth":tx})
    if cls!="PROPERTY_AVAILABILITY":conf.append({"field":"source_class","live_value":"PROPERTY_PIPELINE","source_truth":cls})
    grounded=sum(k in truth for k in ("source_class","transaction_type","property_type","literal_location","contacts","areas","money","furnishing","parking"))
    score=round(100*grounded/9,2)
    return cls,truth,conf,score,("MAGIC_REVIEW" if conf else "SOURCE_GROUNDED")
def run(e,limit=1000):
    _install(e)
    with e.connect() as c:rows=[dict(x) for x in c.execute(text("""SELECT entity_id,message_id,raw_text,parent_message_text,field_quality FROM alliance_topper_availability_v24 ORDER BY updated_at DESC LIMIT :n"""),{"n":limit}).mappings().all()]
    fail=[];changed=0;classes={};samples=[]
    for r in rows:
        try:
            cls,truth,conf,score,decision=_analyse(r);classes[cls]=classes.get(cls,0)+1;changed+=bool(conf)
            with e.begin() as c:c.execute(text("""INSERT INTO alliance_magic_examiner_v26(magic_id,entity_id,message_id,source_class,source_truth,live_conflicts,grounded_score,decision,engine_version)
            VALUES(:id,:eid,:mid,:cls,CAST(:t AS jsonb),CAST(:c AS jsonb),:s,:d,:ev)
            ON CONFLICT(entity_id) DO UPDATE SET message_id=EXCLUDED.message_id,source_class=EXCLUDED.source_class,source_truth=EXCLUDED.source_truth,live_conflicts=EXCLUDED.live_conflicts,grounded_score=EXCLUDED.grounded_score,decision=EXCLUDED.decision,engine_version=EXCLUDED.engine_version,updated_at=now()"""),
            {"id":str(uuid.uuid4()),"eid":r["entity_id"],"mid":r.get("message_id"),"cls":cls,"t":json.dumps(truth,ensure_ascii=False),"c":json.dumps(conf,ensure_ascii=False),"s":score,"d":decision,"ev":ENGINE_VERSION})
            if conf and len(samples)<15:samples.append({"entity_id":r["entity_id"],"source_class":cls,"source_truth":truth,"live_conflicts":conf,"grounded_score":score})
        except Exception as x:fail.append(f"{r.get('entity_id')}:{type(x).__name__}:{x}"[:400])
    return {"status":"PASS" if not fail else "PARTIAL","version":VERSION,"seen":len(rows),"examined":len(rows)-len(fail),"failed":len(fail),"source_class_counts":classes,"magic_disagreements_with_live":changed,"wow_samples":samples,"errors":fail[:10],"production_writes":0,"whatsapp_live_writes":0,"gold_v1_mutations":0}
def status(e):
    _install(e)
    with e.connect() as c:
        s=c.execute(text("""SELECT count(*) n,avg(grounded_score) a,count(*) FILTER(WHERE decision='MAGIC_REVIEW') r,count(*) FILTER(WHERE source_class='NOT_PROPERTY') np,count(*) FILTER(WHERE source_class='REQUIREMENT') rq FROM alliance_magic_examiner_v26 WHERE engine_version=:v"""),{"v":ENGINE_VERSION}).mappings().first()
        top=[dict(x) for x in c.execute(text("""SELECT entity_id,source_class,grounded_score,decision,source_truth,live_conflicts FROM alliance_magic_examiner_v26 WHERE engine_version=:v ORDER BY CASE WHEN live_conflicts<>'[]'::jsonb THEN 0 ELSE 1 END,grounded_score DESC LIMIT 20"""),{"v":ENGINE_VERSION}).mappings().all()]
    return foundation._json_safe({"status":"PASS","version":VERSION,"engine_version":ENGINE_VERSION,"examined_profiles":int(s["n"] or 0),"average_source_grounded_score":round(float(s["a"] or 0),2),"magic_review_cases":int(s["r"] or 0),"non_property_caught":int(s["np"] or 0),"requirements_caught":int(s["rq"] or 0),"top_magic_results":top,"production_writes":0,"whatsapp_live_writes":0,"gold_v1_mutations":0})
DASH="""<!doctype html><html><body style='font-family:Arial;background:#101217;color:#eee;max-width:1200px;margin:30px auto'><h1>✨ Alliance Magic Examiner 2.6</h1><p>Source truth first. Unicode-aware. Shadow only.</p><button onclick='go()' style='padding:14px'>✨ Run Magic on Latest 1000</button> <button onclick='st()' style='padding:14px'>Refresh</button><h2>Scoreboard</h2><pre id=s></pre><h2>Action Result</h2><pre id=r></pre><script>async function a(p,m='GET'){let x=await fetch(p,{method:m}),d=await x.json();if(!x.ok)throw Error(JSON.stringify(d));return d}async function st(){try{s.textContent=JSON.stringify(await a('/api/property-brain/magic-v26/status'),null,2)}catch(e){s.textContent='ERROR '+e}}async function go(){r.textContent='Performing magic...';try{r.textContent=JSON.stringify(await a('/api/property-brain/magic-v26/run?limit=1000','POST'),null,2);st()}catch(e){r.textContent='ERROR '+e}}st()</script></body></html>"""
def register(core):
    e=_engine(core);app=_app(core);_install(e)
    if not foundation._route_exists(app,"/api/property-brain/magic-v26/status"):
        @app.get("/api/property-brain/magic-v26/status")
        def _s():return status(e)
    if not foundation._route_exists(app,"/api/property-brain/magic-v26/run"):
        @app.post("/api/property-brain/magic-v26/run")
        def _r(limit:int=Query(default=1000,ge=1,le=5000)):return run(e,limit)
    if not foundation._route_exists(app,"/property-brain/magic-v26"):
        @app.get("/property-brain/magic-v26",response_class=HTMLResponse)
        def _d():return HTMLResponse(DASH)
    return {"status":"REGISTERED","version":VERSION,"dashboard":"/property-brain/magic-v26","production_writes":0}
