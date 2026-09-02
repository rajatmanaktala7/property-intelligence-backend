from __future__ import annotations
import json, re, uuid
from collections import defaultdict
from fastapi import Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text
import alliance_property_brain_foundation_v1 as foundation

VERSION="2.7.0-WORLD-TOPPER-MAGIC-ACADEMY"
MODE="EXAM_FIRST_ERROR_MEMORY_NO_BLIND_PROMOTION"
EXAM_VERSION="ALLIANCE_WORLD_TOPPER_EXAM_V1"

DDL=[
"""CREATE TABLE IF NOT EXISTS alliance_topper_exam_v27(
exam_id UUID PRIMARY KEY,entity_id TEXT NOT NULL UNIQUE,message_id TEXT,source_class TEXT,
student_answer JSONB NOT NULL DEFAULT '{}'::jsonb,exam_answer JSONB NOT NULL DEFAULT '{}'::jsonb,
field_marks JSONB NOT NULL DEFAULT '{}'::jsonb,error_memory JSONB NOT NULL DEFAULT '[]'::jsonb,
exam_score NUMERIC(5,2),precision_score NUMERIC(5,2),hallucination_penalty NUMERIC(5,2),
grade TEXT,decision TEXT,exam_version TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
"""CREATE TABLE IF NOT EXISTS alliance_topper_mastery_v27(
skill TEXT PRIMARY KEY,cases INTEGER NOT NULL DEFAULT 0,correct INTEGER NOT NULL DEFAULT 0,
wrong INTEGER NOT NULL DEFAULT 0,review INTEGER NOT NULL DEFAULT 0,precision NUMERIC(5,2),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
]
FIELDS=("source_class","transaction_type","property_type","literal_location","contacts","areas","money","furnishing","parking")

def _engine(c): return foundation._engine_from_core(c)
def _app(c): return getattr(c,"app",None) or c
def _loads(v,d): return foundation._loads(v,d)
def _install(e):
    with e.begin() as c:
        for q in DDL:c.execute(text(q))

def _vals(v):
    if v is None:return []
    if isinstance(v,dict) and "value" in v:v=[v]
    elif not isinstance(v,list):v=[v]
    out=[]
    for x in v:
        z=x.get("value") if isinstance(x,dict) else x
        if z not in (None,"","UNKNOWN"):out.append(str(z).strip().casefold())
    return sorted(set(out))

def _exam_case(row):
    student=_loads(row.get("source_truth"),{})
    conflicts=_loads(row.get("live_conflicts"),[])
    ownership=_loads(row.get("owned_fields"),{})
    rejected=_loads(row.get("rejected_inheritance"),{})
    fq=_loads(row.get("field_quality"),{})
    raw=str(row.get("raw_text") or "")
    low=raw.casefold()

    marks={}; errors=[]; answer={}
    # Class is high confidence only on explicit language. Requirement words inside a property
    # description are not enough unless the message is predominantly demand-seeking.
    sclass=(student.get("source_class") or {}).get("value")
    req_signal=bool(re.search(r"\b(requirement|wanted|looking\s+for|we\s+need|client\s+requires?|required\s*:)\b",low))
    supply_signal=bool(re.search(r"\b(for\s+sale|for\s+rent|available|asking|demand|owner|plot|floor|villa|shop|office|warehouse|hotel)\b",low))
    if sclass=="REQUIREMENT" and req_signal and not supply_signal:
        answer["source_class"]="REQUIREMENT";marks["source_class"]="PASS"
    elif sclass=="NOT_PROPERTY":
        answer["source_class"]="NOT_PROPERTY";marks["source_class"]="PASS"
    elif sclass=="PROPERTY_AVAILABILITY":
        answer["source_class"]="PROPERTY_AVAILABILITY";marks["source_class"]="PASS"
    else:
        marks["source_class"]="REVIEW";errors.append("ENTITY_CLASS_AMBIGUOUS")

    # Transaction: BOTH requires sale plus occupancy/rental-income evidence in the same atomic text.
    stx=(student.get("transaction_type") or {}).get("value")
    sale=bool(re.search(r"\b(for\s*sale|sale|selling|asking|demand)\b",low))
    rent=bool(re.search(r"\b(for\s*rent|for\s*lease|on\s*lease|rent\s*[:@-]|lease\s*available)\b",low))
    occupied=bool(re.search(r"\b(already\s+rented|rented\s+out|tenanted|rental\s+income|leased\s+out)\b",low))
    tx="BOTH" if sale and occupied else ("SALE" if sale else ("RENT" if rent else None))
    if tx:
        answer["transaction_type"]=tx
        if stx==tx:marks["transaction_type"]="PASS"
        else:
            marks["transaction_type"]="CORRECTED"
            errors.append(f"TRANSACTION_{stx or 'MISSING'}_TO_{tx}")
    else:marks["transaction_type"]="REVIEW"

    # Other fields: reward only explicit student evidence or owned evidence. Never reward live-only.
    for f in FIELDS[2:]:
        sv=student.get(f)
        owned_f=ownership.get(f) or {}
        status=owned_f.get("status")
        if sv:
            answer[f]=sv
            if status in ("OWNED_ATOMIC","OWNED_PARENT_SCOPED","OWNED_SHARED_PARENT","OWNED_LINEAGE_FALLBACK") or f in ("areas","money","furnishing","parking"):
                marks[f]="PASS"
            else:
                marks[f]="EVIDENCE_ONLY"
        elif status in ("OWNED_ATOMIC","OWNED_PARENT_SCOPED","OWNED_SHARED_PARENT","OWNED_LINEAGE_FALLBACK"):
            answer[f]=owned_f
            marks[f]="PASS"
        elif rejected.get(f):
            marks[f]="REVIEW"
        else:
            marks[f]="MISSING"

    # Penalize unsupported critical claims and known conflicts.
    hallucinations=0
    for f in ("city","locality","property_type","transaction_type"):
        q=fq.get(f) or {}
        if q.get("status")=="LIVE_ONLY_UNPROVEN":
            hallucinations+=1;errors.append(f"UNSUPPORTED_{f.upper()}")
    for c in conflicts:
        if c.get("field") in ("transaction_type","source_class"):
            errors.append("LIVE_CONFLICT_"+str(c.get("field")).upper())

    scored=[v for v in marks.values() if v not in ("MISSING",)]
    correct=sum(v in ("PASS","CORRECTED") for v in scored)
    review=sum(v=="REVIEW" for v in scored)
    wrong=sum(v=="FAIL" for v in scored)
    precision=round(100*correct/max(correct+wrong+review,1),2)
    penalty=min(50.0,hallucinations*12.5)
    score=max(0.0,round(precision-penalty,2))
    grade="A+" if score>=95 and review==0 else "A" if score>=90 else "B" if score>=80 else "C" if score>=70 else "D" if score>=55 else "F"
    decision="GRADUATE_CANDIDATE" if grade in ("A+","A") and not errors else "TEACH_AND_RETEST"
    return student,answer,marks,sorted(set(errors)),score,precision,penalty,grade,decision

def _rebuild_mastery(e):
    with e.connect() as c:rows=c.execute(text("SELECT field_marks FROM alliance_topper_exam_v27 WHERE exam_version=:v"),{"v":EXAM_VERSION}).all()
    agg=defaultdict(lambda:{"cases":0,"correct":0,"wrong":0,"review":0})
    for (m,) in rows:
        for f,v in _loads(m,{}).items():
            if v=="MISSING":continue
            a=agg[f];a["cases"]+=1
            if v in ("PASS","CORRECTED"):a["correct"]+=1
            elif v=="REVIEW":a["review"]+=1
            else:a["wrong"]+=1
    with e.begin() as c:
        for f,a in agg.items():
            p=round(100*a["correct"]/max(a["cases"],1),2)
            c.execute(text("""INSERT INTO alliance_topper_mastery_v27(skill,cases,correct,wrong,review,precision)
            VALUES(:f,:n,:ok,:w,:r,:p) ON CONFLICT(skill) DO UPDATE SET cases=EXCLUDED.cases,correct=EXCLUDED.correct,
            wrong=EXCLUDED.wrong,review=EXCLUDED.review,precision=EXCLUDED.precision,updated_at=now()"""),
            {"f":f,"n":a["cases"],"ok":a["correct"],"w":a["wrong"],"r":a["review"],"p":p})

def run(e,limit=1000):
    _install(e)
    with e.connect() as c:
        rows=[dict(x) for x in c.execute(text("""SELECT m.entity_id,m.message_id,m.source_class,m.source_truth,m.live_conflicts,
        v.raw_text,v.field_quality,o.owned_fields,o.rejected_inheritance
        FROM alliance_magic_examiner_v26 m
        JOIN alliance_topper_availability_v24 v ON v.entity_id=m.entity_id
        LEFT JOIN alliance_context_ownership_v25 o ON o.entity_id=m.entity_id
        WHERE m.engine_version='ALLIANCE_MAGIC_EXAMINER_V1'
        ORDER BY m.updated_at DESC LIMIT :n"""),{"n":limit}).mappings().all()]
    failed=[];grades=defaultdict(int);decisions=defaultdict(int);samples=[]
    for r in rows:
        try:
            student,answer,marks,errors,score,precision,penalty,grade,decision=_exam_case(r)
            grades[grade]+=1;decisions[decision]+=1
            with e.begin() as c:c.execute(text("""INSERT INTO alliance_topper_exam_v27
            (exam_id,entity_id,message_id,source_class,student_answer,exam_answer,field_marks,error_memory,
            exam_score,precision_score,hallucination_penalty,grade,decision,exam_version)
            VALUES(:id,:eid,:mid,:cls,CAST(:stu AS jsonb),CAST(:ans AS jsonb),CAST(:marks AS jsonb),CAST(:err AS jsonb),
            :score,:prec,:pen,:grade,:dec,:ver)
            ON CONFLICT(entity_id) DO UPDATE SET message_id=EXCLUDED.message_id,source_class=EXCLUDED.source_class,
            student_answer=EXCLUDED.student_answer,exam_answer=EXCLUDED.exam_answer,field_marks=EXCLUDED.field_marks,
            error_memory=EXCLUDED.error_memory,exam_score=EXCLUDED.exam_score,precision_score=EXCLUDED.precision_score,
            hallucination_penalty=EXCLUDED.hallucination_penalty,grade=EXCLUDED.grade,decision=EXCLUDED.decision,
            exam_version=EXCLUDED.exam_version,updated_at=now()"""),
            {"id":str(uuid.uuid4()),"eid":r["entity_id"],"mid":r.get("message_id"),"cls":r.get("source_class"),
             "stu":json.dumps(student,ensure_ascii=False),"ans":json.dumps(answer,ensure_ascii=False),
             "marks":json.dumps(marks,ensure_ascii=False),"err":json.dumps(errors,ensure_ascii=False),
             "score":score,"prec":precision,"pen":penalty,"grade":grade,"dec":decision,"ver":EXAM_VERSION})
            if errors and len(samples)<20:samples.append({"entity_id":r["entity_id"],"grade":grade,"score":score,"decision":decision,"errors":errors,"exam_answer":answer})
        except Exception as x:failed.append(f"{r.get('entity_id')}:{type(x).__name__}:{x}"[:500])
    _rebuild_mastery(e)
    return {"status":"PASS" if not failed else "PARTIAL","version":VERSION,"seen":len(rows),"examined":len(rows)-len(failed),
            "failed":len(failed),"grade_distribution":dict(grades),"decision_distribution":dict(decisions),
            "top_teaching_examples":samples,"errors":failed[:10],"production_writes":0,"whatsapp_live_writes":0,"gold_v1_mutations":0}

def status(e):
    _install(e)
    with e.connect() as c:
        s=c.execute(text("""SELECT count(*) n,avg(exam_score) score,avg(precision_score) precision,
        avg(hallucination_penalty) penalty,count(*) FILTER(WHERE decision='GRADUATE_CANDIDATE') grad
        FROM alliance_topper_exam_v27 WHERE exam_version=:v"""),{"v":EXAM_VERSION}).mappings().first()
        mastery=[dict(x) for x in c.execute(text("SELECT * FROM alliance_topper_mastery_v27 ORDER BY precision ASC,cases DESC")).mappings().all()]
        teach=[dict(x) for x in c.execute(text("""SELECT entity_id,grade,exam_score,precision_score,hallucination_penalty,decision,error_memory,exam_answer
        FROM alliance_topper_exam_v27 WHERE exam_version=:v AND decision='TEACH_AND_RETEST'
        ORDER BY hallucination_penalty DESC,exam_score ASC LIMIT 20"""),{"v":EXAM_VERSION}).mappings().all()]
    return foundation._json_safe({"status":"PASS","version":VERSION,"mode":MODE,"exam_version":EXAM_VERSION,
        "examined_profiles":int(s["n"] or 0),"average_exam_score":round(float(s["score"] or 0),2),
        "answer_precision":round(float(s["precision"] or 0),2),"average_hallucination_penalty":round(float(s["penalty"] or 0),2),
        "graduate_candidates":int(s["grad"] or 0),"mastery":mastery,"teaching_queue":teach,
        "graduation_gate":{"precision":95,"unsupported_inference":"near_zero","rule":"A/A+ plus no error memory"},
        "production_writes":0,"whatsapp_live_writes":0,"gold_v1_mutations":0})

DASH="""<!doctype html><html><body style='font-family:Arial;background:#090d12;color:#eef3f8;max-width:1250px;margin:28px auto'>
<h1>🪄 World Topper Magic Academy 2.7</h1><p>Gold discipline + atomic ownership + live WhatsApp exams + error memory.</p>
<button onclick='go()' style='padding:14px 20px;background:#ffd76a;border:0;border-radius:9px;font-weight:bold'>Run World Topper Exam</button>
<button onclick='st()' style='padding:14px 20px'>Refresh</button><h2>Academy Scoreboard</h2><pre id=s></pre><h2>Exam Result</h2><pre id=r></pre>
<script>async function a(p,m='GET'){let x=await fetch(p,{method:m}),t=await x.text(),d;try{d=JSON.parse(t)}catch(e){d={raw:t}}if(!x.ok)throw Error(d.detail||d.raw||('HTTP '+x.status));return d}
async function st(){try{s.textContent=JSON.stringify(await a('/api/property-brain/academy-v27/status'),null,2)}catch(e){s.textContent='ERROR '+e.message}}
async function go(){r.textContent='Examining 1000 live cases...';try{r.textContent=JSON.stringify(await a('/api/property-brain/academy-v27/run?limit=1000','POST'),null,2);st()}catch(e){r.textContent='ERROR '+e.message}}st()</script></body></html>"""

def register(core):
    e=_engine(core);app=_app(core);_install(e)
    if not foundation._route_exists(app,"/api/property-brain/academy-v27/status"):
        @app.get("/api/property-brain/academy-v27/status")
        def _s():return status(e)
    if not foundation._route_exists(app,"/api/property-brain/academy-v27/run"):
        @app.post("/api/property-brain/academy-v27/run")
        def _r(limit:int=Query(default=1000,ge=1,le=5000)):return run(e,limit)
    if not foundation._route_exists(app,"/property-brain/academy-v27"):
        @app.get("/property-brain/academy-v27",response_class=HTMLResponse)
        def _d():return HTMLResponse(DASH)
    return {"status":"REGISTERED","version":VERSION,"dashboard":"/property-brain/academy-v27","production_writes":0}
