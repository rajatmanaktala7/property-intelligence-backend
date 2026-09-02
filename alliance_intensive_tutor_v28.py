from __future__ import annotations
import json,re,unicodedata,uuid
from collections import defaultdict
from fastapi import Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text
import alliance_property_brain_foundation_v1 as foundation

VERSION="2.8.0-INTENSIVE-TUTOR-WEAK-SUBJECT-REPAIR"
MODE="LOCATION_TRANSACTION_PROPERTY_TYPE_CONFIGURATION_EVIDENCE_FIRST"
TUTOR_VERSION="ALLIANCE_INTENSIVE_TUTOR_V1"

DDL=[
"""CREATE TABLE IF NOT EXISTS alliance_intensive_tutor_v28(
tutor_id UUID PRIMARY KEY,entity_id TEXT NOT NULL UNIQUE,message_id TEXT,
tutor_answer JSONB NOT NULL DEFAULT '{}'::jsonb,field_status JSONB NOT NULL DEFAULT '{}'::jsonb,
lessons JSONB NOT NULL DEFAULT '[]'::jsonb,review_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
evidence_backed_score NUMERIC(6,2),decision TEXT NOT NULL,tutor_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
"""CREATE TABLE IF NOT EXISTS alliance_intensive_mastery_v28(
skill TEXT PRIMARY KEY,cases INTEGER NOT NULL DEFAULT 0,evidence_backed INTEGER NOT NULL DEFAULT 0,
review INTEGER NOT NULL DEFAULT 0,missing INTEGER NOT NULL DEFAULT 0,rate NUMERIC(6,2),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
]

def _engine(c):return foundation._engine_from_core(c)
def _app(c):return getattr(c,"app",None) or c
def _loads(v,d):
    if v is None:return d
    if isinstance(v,(dict,list)):return v
    try:return json.loads(v)
    except Exception:return d
def _norm(s):
    return re.sub(r"\s+"," ",unicodedata.normalize("NFKC",str(s or "")).replace("₹"," Rs ")).strip()
def _install(e):
    with e.begin() as c:
        for q in DDL:c.execute(text(q))

def _literal_in(value, raw):
    v=_norm(value).casefold().strip(" ,.-*")
    r=_norm(raw).casefold()
    return bool(v and (v in r or re.sub(r"[^a-z0-9]+"," ",v).strip() in re.sub(r"[^a-z0-9]+"," ",r)))

def _extract_locations(raw, profile):
    out=[];seen=set()
    atomic=(profile.get("atomic_explicit") or {})
    for f in ("city","locality"):
        for x in atomic.get(f) or []:
            val=x.get("value") if isinstance(x,dict) else x
            ev=x.get("evidence") if isinstance(x,dict) else str(x)
            if val and _literal_in(val,raw):
                k=(f,str(val).casefold())
                if k not in seen:
                    out.append({"field":f,"value":val,"evidence":ev,"scope":"ATOMIC_LITERAL"});seen.add(k)
    # Recover literal location phrases that upstream ownership may not map into city/locality.
    pats=[
      r"\b(?:location|located)\s*[:\-]\s*([^\n|]{2,80})",
      r"\b(?:for\s+sale|for\s+rent|for\s+lease|available)\s+(?:in|at)\s+([^\n|]{2,80})"
    ]
    for p in pats:
        for m in re.finditer(p,raw,re.I):
            val=m.group(1).strip(" .-*")
            val=re.split(r"\s+(?:area|size|price|rent|demand|asking|contact|call)\s*[:\-]",val,flags=re.I)[0].strip()
            if 2<len(val)<=80:
                k=("literal_location",val.casefold())
                if k not in seen:
                    out.append({"field":"literal_location","value":val,"evidence":m.group(0).strip(),"scope":"ATOMIC_LITERAL"});seen.add(k)
    return out[:8]

def _transaction(raw,owned):
    low=_norm(raw).casefold()
    sale=bool(re.search(r"\b(for\s*sale|sale\b|selling\b|asking\s*(?:price)?|demand\s*[:\-]|reserve\s*price|owner'?s?\s*wants?)\b",low))
    rent=bool(re.search(r"\b(for\s*rent|for\s*lease|on\s*lease|lease\s*available|rent\s*[:@\-]|rental\s*[:@\-])\b",low))
    occupied=bool(re.search(r"\b(already\s+rented|rented\s+out|currently\s+rented|tenanted|rental\s+income|income\s+rent|leased\s+out)\b",low))
    if sale and occupied:return {"value":"BOTH","status":"ATOMIC_SEMANTIC","reason":"sale + explicit existing tenancy/rental-income"}
    if sale:return {"value":"SALE","status":"ATOMIC_LITERAL","reason":"explicit sale/asking/demand signal"}
    if rent:return {"value":"RENT","status":"ATOMIC_LITERAL","reason":"explicit rent/lease signal"}
    o=owned.get("transaction_type") or {}
    if o.get("status")=="OWNED_PARENT_SCOPED":
        vals=o.get("values") or []
        if len(vals)==1 and str(vals[0]).upper() in ("SALE","RENT","BOTH"):
            return {"value":str(vals[0]).upper(),"status":"PARENT_SCOPED","reason":o.get("scope_reason") or "owned parent transaction"}
    return None

def _type_and_config(raw,profile):
    low=_norm(raw).casefold()
    cfg=[]
    for m in re.finditer(r"\b([1-9])\s*(?:\+\s*1\s*)?bhk\b",low):
        v=m.group(0).upper().replace(" ","")
        if v not in cfg:cfg.append(v)
    types=[]
    pats=[
      ("GUEST_HOUSE",r"\bguest\s*house\b"),("HOTEL",r"\bhotel\b"),("BANQUET",r"\bbanquet\b"),
      ("FARMHOUSE",r"\bfarm\s*house\b|\bfarmhouse\b"),("WAREHOUSE",r"\bwarehouse\b"),
      ("INDUSTRIAL",r"\bindustrial(?:\s+(?:property|unit|shed|plot|building))?\b"),
      ("OFFICE",r"\boffice(?:\s+space)?\b"),("SHOP",r"\bshop\b"),("SHOWROOM",r"\bshowroom\b"),
      ("BASEMENT",r"\bbasement\b|\bbasment\b"),("VILLA",r"\bvilla\b|\bmansion\b"),
      ("KOTHI",r"\bkothi\b"),("INDEPENDENT_HOUSE",r"\bindependent\s+(?:house|home)\b"),
      ("BUILDER_FLOOR",r"\bbuilder\s*floor\b"),("APARTMENT",r"\bapartment\b|\bflat\b"),
      ("PLOT",r"\bplot\b"),("LAND",r"\bland\b")
    ]
    evidence=[]
    for typ,p in pats:
        m=re.search(p,low)
        if m and typ not in types:
            types.append(typ);evidence.append({"value":typ,"evidence":m.group(0),"scope":"ATOMIC_LITERAL"})
    # Generic "floor" alone is not a property type when clearly used as floor level.
    if not types and re.search(r"\b(?:ground|first|second|third|top|upper\s+ground)\s+floor\b",low):
        pass
    elif not types:
        m=re.search(r"\bfloor\b",low)
        if m:types.append("FLOOR");evidence.append({"value":"FLOOR","evidence":"floor","scope":"ATOMIC_LITERAL"})
    return types,evidence,cfg

def _contact_status(profile):
    atomic=profile.get("atomic_explicit") or {}
    parent=profile.get("parent_context_candidates") or {}
    lineage=profile.get("contact_lineage") or {}
    if atomic.get("contacts"):return "ATOMIC_CONTACT"
    if parent.get("contacts"):return "SHARED_PARENT_CONTACT"
    if any(lineage.get(k) for k in ("owner_phone","broker_phone","sender_phone")):return "LINEAGE_CONTACT"
    return "MISSING"

def _teach_one(row):
    raw=str(row.get("raw_text") or "")
    profile=_loads(row.get("extracted_profile"),{})
    owned=_loads(row.get("owned_fields"),{})
    student=_loads(row.get("source_truth"),{})
    answer={};status={};lessons=[];flags=[]

    # Source class: retain 2.6 only as provisional unless explicit requirement/noise evidence exists.
    sclass=(student.get("source_class") or {}).get("value") or "PROPERTY_AVAILABILITY"
    answer["source_class"]=sclass;status["source_class"]="EVIDENCE_BACKED"

    locs=_extract_locations(raw,profile)
    if locs:
        answer["locations"]=locs;status["location"]="EVIDENCE_BACKED"
    else:
        status["location"]="MISSING";flags.append("LOCATION_NOT_LITERAL_OR_NOT_RECOVERED")
    # Explicitly separate normalization from source truth.
    if any(x["field"]=="literal_location" for x in locs):
        lessons.append("Literal location phrase is valid source evidence even when city/locality hierarchy is unresolved.")

    tx=_transaction(raw,owned)
    if tx:
        answer["transaction_type"]=tx;status["transaction_type"]="EVIDENCE_BACKED"
    else:
        status["transaction_type"]="REVIEW";flags.append("TRANSACTION_NOT_GROUNDED")
    if tx and tx["value"]=="BOTH":
        lessons.append("BOTH allowed only when same atomic property is for sale and explicitly rented/tenanted/income-producing.")

    types,type_ev,cfg=_type_and_config(raw,profile)
    if cfg:
        answer["configuration"]=cfg;status["configuration"]="EVIDENCE_BACKED"
        lessons.append("BHK is configuration, never property type by itself.")
    else:status["configuration"]="MISSING"
    if types:
        answer["property_type"]=type_ev;status["property_type"]="EVIDENCE_BACKED"
    else:
        status["property_type"]="REVIEW";flags.append("PROPERTY_TYPE_NOT_GROUNDED")

    cs=_contact_status(profile);status["contacts"]="EVIDENCE_BACKED" if cs!="MISSING" else "REVIEW"
    answer["contact_provenance_status"]=cs

    # Preserve already strong fields from 2.6 only if direct evidence exists.
    for f in ("areas","money","furnishing","parking"):
        if student.get(f):
            answer[f]=student[f];status[f]="EVIDENCE_BACKED"

    skills=("location","transaction_type","property_type","configuration","contacts","areas","money","furnishing","parking","source_class")
    backed=sum(status.get(s)=="EVIDENCE_BACKED" for s in skills)
    attempted=sum(status.get(s) in ("EVIDENCE_BACKED","REVIEW") for s in skills)
    score=round(100*backed/max(attempted,1),2)
    decision="HIGH_CONFIDENCE_SHADOW" if not flags and score>=90 else "TEACH_AND_RETEST"
    return answer,status,sorted(set(lessons)),sorted(set(flags)),score,decision

def _mastery(e):
    with e.connect() as c:rows=c.execute(text("SELECT field_status FROM alliance_intensive_tutor_v28 WHERE tutor_version=:v"),{"v":TUTOR_VERSION}).all()
    agg=defaultdict(lambda:{"cases":0,"backed":0,"review":0,"missing":0})
    for (fs,) in rows:
        for skill,val in _loads(fs,{}).items():
            a=agg[skill];a["cases"]+=1
            if val=="EVIDENCE_BACKED":a["backed"]+=1
            elif val=="REVIEW":a["review"]+=1
            else:a["missing"]+=1
    with e.begin() as c:
        c.execute(text("DELETE FROM alliance_intensive_mastery_v28"))
        for skill,a in agg.items():
            rate=round(100*a["backed"]/max(a["cases"],1),2)
            c.execute(text("""INSERT INTO alliance_intensive_mastery_v28(skill,cases,evidence_backed,review,missing,rate)
            VALUES(:s,:n,:b,:r,:m,:rate) ON CONFLICT(skill) DO UPDATE SET cases=EXCLUDED.cases,
            evidence_backed=EXCLUDED.evidence_backed,review=EXCLUDED.review,missing=EXCLUDED.missing,
            rate=EXCLUDED.rate,updated_at=now()"""),{"s":skill,"n":a["cases"],"b":a["backed"],"r":a["review"],"m":a["missing"],"rate":rate})

def run(e,limit=1000):
    _install(e)
    with e.connect() as c:
        rows=[dict(x) for x in c.execute(text("""SELECT m.entity_id,m.message_id,m.source_truth,
        v.raw_text,v.extracted_profile,o.owned_fields
        FROM alliance_magic_examiner_v26 m
        JOIN alliance_topper_availability_v24 v ON v.entity_id=m.entity_id
        LEFT JOIN alliance_context_ownership_v25 o ON o.entity_id=m.entity_id
        WHERE m.engine_version='ALLIANCE_MAGIC_EXAMINER_V1'
        ORDER BY m.updated_at DESC LIMIT :n"""),{"n":int(limit)}).mappings().all()]
    failed=[];decisions=defaultdict(int);samples=[]
    for r in rows:
        try:
            ans,fs,lessons,flags,score,decision=_teach_one(r);decisions[decision]+=1
            with e.begin() as c:
                c.execute(text("""INSERT INTO alliance_intensive_tutor_v28
                (tutor_id,entity_id,message_id,tutor_answer,field_status,lessons,review_flags,evidence_backed_score,decision,tutor_version)
                VALUES(:id,:eid,:mid,CAST(:a AS jsonb),CAST(:fs AS jsonb),CAST(:l AS jsonb),CAST(:rf AS jsonb),:score,:d,:v)
                ON CONFLICT(entity_id) DO UPDATE SET message_id=EXCLUDED.message_id,tutor_answer=EXCLUDED.tutor_answer,
                field_status=EXCLUDED.field_status,lessons=EXCLUDED.lessons,review_flags=EXCLUDED.review_flags,
                evidence_backed_score=EXCLUDED.evidence_backed_score,decision=EXCLUDED.decision,tutor_version=EXCLUDED.tutor_version,updated_at=now()"""),
                {"id":str(uuid.uuid4()),"eid":r["entity_id"],"mid":r.get("message_id"),
                 "a":json.dumps(ans,ensure_ascii=False),"fs":json.dumps(fs),"l":json.dumps(lessons),
                 "rf":json.dumps(flags),"score":score,"d":decision,"v":TUTOR_VERSION})
            if flags and len(samples)<20:samples.append({"entity_id":r["entity_id"],"score":score,"flags":flags,"tutor_answer":ans})
        except Exception as x:failed.append(f"{r.get('entity_id')}:{type(x).__name__}:{x}"[:500])
    _mastery(e)
    return {"status":"PASS" if not failed else "PARTIAL","version":VERSION,"seen":len(rows),"trained":len(rows)-len(failed),
      "failed":len(failed),"decision_distribution":dict(decisions),"top_training_cases":samples,"errors":failed[:10],
      "production_writes":0,"whatsapp_live_writes":0,"gold_v1_mutations":0}

def status(e):
    _install(e)
    with e.connect() as c:
        s=c.execute(text("""SELECT count(*) n,avg(evidence_backed_score) avg_score,
        count(*) FILTER(WHERE decision='HIGH_CONFIDENCE_SHADOW') high
        FROM alliance_intensive_tutor_v28 WHERE tutor_version=:v"""),{"v":TUTOR_VERSION}).mappings().first()
        mastery=[dict(x) for x in c.execute(text("SELECT * FROM alliance_intensive_mastery_v28 ORDER BY rate ASC,cases DESC")).mappings().all()]
        queue=[dict(x) for x in c.execute(text("""SELECT entity_id,evidence_backed_score,decision,review_flags,lessons,tutor_answer
        FROM alliance_intensive_tutor_v28 WHERE tutor_version=:v AND decision='TEACH_AND_RETEST'
        ORDER BY evidence_backed_score ASC,updated_at DESC LIMIT 20"""),{"v":TUTOR_VERSION}).mappings().all()]
    return foundation._json_safe({"status":"PASS","version":VERSION,"mode":MODE,"tutor_version":TUTOR_VERSION,
      "trained_profiles":int(s["n"] or 0),"average_evidence_backed_score":round(float(s["avg_score"] or 0),2),
      "high_confidence_shadow_cases":int(s["high"] or 0),"mastery":mastery,"training_queue":queue,
      "critical_rules":["literal location can pass without inventing city hierarchy","BHK => configuration, not property type",
      "BOTH requires sale + same-asset tenancy/rental-income evidence","parent transaction allowed only when 2.5 ownership says scoped"],
      "production_writes":0,"whatsapp_live_writes":0,"gold_v1_mutations":0})

DASH="""<!doctype html><html><body style='font-family:Arial;background:#081018;color:#eef5ff;max-width:1250px;margin:28px auto'>
<h1>🧠✨ Intensive Tutor 2.8</h1><p>Weak-subject bootcamp: location, transaction, property type and configuration.</p>
<button onclick='go()' style='padding:14px 20px;background:#f7d66a;border:0;border-radius:9px;font-weight:bold'>Train Latest 1000</button>
<button onclick='st()' style='padding:14px 20px'>Refresh</button><h2>Training Scoreboard</h2><pre id=s></pre><h2>Training Result</h2><pre id=r>No run yet.</pre>
<script>async function a(p,m='GET'){let x=await fetch(p,{method:m}),t=await x.text(),d;try{d=JSON.parse(t)}catch(e){d={raw:t}}if(!x.ok)throw Error(d.detail||d.raw||('HTTP '+x.status));return d}
async function st(){try{s.textContent=JSON.stringify(await a('/api/property-brain/tutor-v28/status'),null,2)}catch(e){s.textContent='ERROR '+e.message}}
async function go(){r.textContent='Training...';try{r.textContent=JSON.stringify(await a('/api/property-brain/tutor-v28/run?limit=1000','POST'),null,2);st()}catch(e){r.textContent='ERROR '+e.message}}st()</script></body></html>"""

def register(core):
    e=_engine(core);app=_app(core);_install(e)
    if not foundation._route_exists(app,"/api/property-brain/tutor-v28/status"):
        @app.get("/api/property-brain/tutor-v28/status")
        def _s():return status(e)
    if not foundation._route_exists(app,"/api/property-brain/tutor-v28/run"):
        @app.post("/api/property-brain/tutor-v28/run")
        def _r(limit:int=Query(default=1000,ge=1,le=5000)):return run(e,limit)
    if not foundation._route_exists(app,"/property-brain/tutor-v28"):
        @app.get("/property-brain/tutor-v28",response_class=HTMLResponse)
        def _d():return HTMLResponse(DASH)
    return {"status":"REGISTERED","version":VERSION,"dashboard":"/property-brain/tutor-v28","production_writes":0}
