from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import datetime, timezone

from fastapi import Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation

VERSION = "2.9.3-GOLD-V2-STRUCTURAL-LAB"
MODE = "HUMAN_STRUCTURAL_GROUND_TRUTH_ACTIVE_LEARNING_NO_GOLD_V1_MUTATION"
ENGINE_VERSION = "ALLIANCE_GOLD_V2_STRUCTURAL_LAB_V1"
CURRICULUM_VERSION = "GOLD_V2_STRUCTURAL_100_V1"
LABEL_SCHEMA_VERSION = "STRUCTURAL_LABEL_SCHEMA_V1"
TARGET = 100

DDL = [
"""CREATE TABLE IF NOT EXISTS alliance_gold_v2_structural_cases(
case_id UUID PRIMARY KEY,
entity_id TEXT NOT NULL,
message_id TEXT,
task_type TEXT NOT NULL,
category TEXT NOT NULL,
priority_score NUMERIC(6,2) NOT NULL,
candidate_value TEXT,
field_type TEXT,
raw_text TEXT,
parent_message_text TEXT,
machine_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
source_version TEXT NOT NULL,
status TEXT NOT NULL DEFAULT 'OPEN',
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(entity_id,task_type,category,source_version))""",

"""CREATE TABLE IF NOT EXISTS alliance_gold_v2_structural_labels(
label_id UUID PRIMARY KEY,
case_id UUID NOT NULL UNIQUE,
entity_id TEXT NOT NULL,
task_type TEXT NOT NULL,
human_decision TEXT NOT NULL,
human_confidence TEXT NOT NULL,
field_type TEXT,
canonical_value TEXT,
boundary_valid BOOLEAN,
reason TEXT,
labeler_id TEXT,
label_schema_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_gold_v2_structural_eval(
metric_key TEXT PRIMARY KEY,
metric_value NUMERIC(10,4),
numerator INTEGER NOT NULL DEFAULT 0,
denominator INTEGER NOT NULL DEFAULT 0,
metric_scope TEXT NOT NULL,
notes TEXT,
eval_version TEXT NOT NULL,
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
]

def _engine(core):
    return foundation._engine_from_core(core)

def _app(core):
    return getattr(core, "app", None) or core

def _loads(v, default):
    if v is None:
        return default
    if isinstance(v,(dict,list)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return default

def _install(engine):
    with engine.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))

def _insert_case(conn, *, entity_id, message_id, task_type, category, priority,
                 candidate_value=None, field_type=None, raw_text=None, parent_text=None,
                 payload=None):
    conn.execute(text("""
        INSERT INTO alliance_gold_v2_structural_cases
        (case_id,entity_id,message_id,task_type,category,priority_score,candidate_value,
         field_type,raw_text,parent_message_text,machine_payload,source_version,status)
        VALUES(:id,:eid,:mid,:task,:cat,:p,:cv,:ft,:raw,:parent,CAST(:payload AS jsonb),:v,'OPEN')
        ON CONFLICT(entity_id,task_type,category,source_version) DO NOTHING
    """),{
        "id":str(uuid.uuid4()),"eid":entity_id,"mid":message_id,"task":task_type,
        "cat":category,"p":priority,"cv":candidate_value,"ft":field_type,
        "raw":raw_text,"parent":parent_text,
        "payload":json.dumps(foundation._json_safe(payload or {}),ensure_ascii=False),
        "v":CURRICULUM_VERSION,
    })

def seed(engine, target=TARGET):
    _install(engine)
    target=max(20,min(int(target),300))
    with engine.begin() as conn:
        existing=conn.execute(text("""
            SELECT count(*) FROM alliance_gold_v2_structural_cases
            WHERE source_version=:v
        """),{"v":CURRICULUM_VERSION}).scalar() or 0

        # 1. Highest-risk source-traceability anomalies from the final examiner.
        rows=conn.execute(text("""
            SELECT e.entity_id,e.message_id,e.ablation_evidence,e.structural_audit_evidence,
                   r.validated_location,r.final_geography,r.dimension_type,
                   v.raw_text,v.parent_message_text
            FROM alliance_final_exam_v2922 e
            LEFT JOIN alliance_ownership_resolution_v292 r ON r.entity_id=e.entity_id
            LEFT JOIN alliance_topper_availability_v24 v ON v.entity_id=e.entity_id
            WHERE e.exam_version='ALLIANCE_STRUCTURAL_FINAL_EXAM_V1'
              AND e.ablation_final_unexplained=TRUE
            ORDER BY e.updated_at DESC
        """)).mappings().all()
        for r in rows:
            ev=_loads(r["ablation_evidence"],{})
            _insert_case(conn,
                entity_id=r["entity_id"],message_id=r["message_id"],
                task_type="SOURCE_TRACEABILITY",category="UNEXPLAINED_ABLATION",
                priority=100,candidate_value=ev.get("candidate"),
                field_type=r.get("dimension_type"),raw_text=r.get("raw_text"),
                parent_text=r.get("parent_message_text"),
                payload={"final_exam":ev,"validated_location":_loads(r.get("validated_location"),{}),
                         "final_geography":_loads(r.get("final_geography"),{})})

        # 2. Genuine structural conflicts.
        rows=conn.execute(text("""
            SELECT e.entity_id,e.message_id,e.structural_audit_evidence,
                   v.raw_text,v.parent_message_text
            FROM alliance_final_exam_v2922 e
            LEFT JOIN alliance_topper_availability_v24 v ON v.entity_id=e.entity_id
            WHERE e.exam_version='ALLIANCE_STRUCTURAL_FINAL_EXAM_V1'
              AND e.structural_audit_class='TRUE_STRUCTURAL_CONFLICT'
            ORDER BY e.updated_at DESC
        """)).mappings().all()
        for r in rows:
            ev=_loads(r["structural_audit_evidence"],{})
            _insert_case(conn,
                entity_id=r["entity_id"],message_id=r["message_id"],
                task_type="STRUCTURAL_CONFLICT",category="TRUE_CONFLICT_EXAM",
                priority=100,field_type=ev.get("field"),
                raw_text=r.get("raw_text"),parent_text=r.get("parent_message_text"),
                payload={"structural_audit":ev})

        # 3. Ambiguous ownership cases from the active-learning queue.
        rows=conn.execute(text("""
            SELECT q.entity_id,q.message_id,q.category,q.priority_score,q.reason,q.payload,
                   v.raw_text,v.parent_message_text,
                   o.owned_fields,o.rejected_inheritance,o.sibling_context
            FROM alliance_gold_v2_candidate_queue q
            LEFT JOIN alliance_topper_availability_v24 v ON v.entity_id=q.entity_id
            LEFT JOIN alliance_context_ownership_v25 o ON o.entity_id=q.entity_id
            WHERE q.status='OPEN'
              AND q.category IN ('STRUCTURAL_OWNERSHIP','MULTI_CITY_PARENT','HARD_GEOGRAPHY',
                                 'NEGATIVE_LOCATION','HARD_TRANSACTION',
                                 'UNDERREPRESENTED_REQUIREMENT','UNDERREPRESENTED_NOISE')
            ORDER BY q.priority_score DESC,q.created_at ASC
            LIMIT 500
        """)).mappings().all()

        per_cat=Counter()
        caps={
            "STRUCTURAL_OWNERSHIP":55,
            "MULTI_CITY_PARENT":15,
            "HARD_GEOGRAPHY":8,
            "NEGATIVE_LOCATION":6,
            "HARD_TRANSACTION":8,
            "UNDERREPRESENTED_REQUIREMENT":5,
            "UNDERREPRESENTED_NOISE":5,
        }
        for r in rows:
            cat=r["category"]
            if per_cat[cat]>=caps.get(cat,10):
                continue
            task="OWNERSHIP"
            if cat=="MULTI_CITY_PARENT":
                task="STRUCTURAL_CONFLICT"
            elif cat in ("NEGATIVE_LOCATION","HARD_GEOGRAPHY"):
                task="SOURCE_TRACEABILITY"
            elif cat=="HARD_TRANSACTION":
                task="OWNERSHIP"
            elif cat in ("UNDERREPRESENTED_REQUIREMENT","UNDERREPRESENTED_NOISE"):
                task="CONTENT_CLASS"

            payload={
                "queue_reason":r.get("reason"),
                "queue_payload":_loads(r.get("payload"),{}),
                "owned_fields":_loads(r.get("owned_fields"),{}),
                "rejected_inheritance":_loads(r.get("rejected_inheritance"),{}),
                "sibling_context":_loads(r.get("sibling_context"),{}),
            }
            _insert_case(conn,
                entity_id=r["entity_id"],message_id=r["message_id"],
                task_type=task,category=cat,priority=float(r["priority_score"] or 0),
                raw_text=r.get("raw_text"),parent_text=r.get("parent_message_text"),
                payload=payload)
            per_cat[cat]+=1

        # Hard cap curriculum to target, preserving highest-priority first.
        conn.execute(text("""
            DELETE FROM alliance_gold_v2_structural_cases
            WHERE source_version=:v AND case_id IN (
              SELECT case_id FROM alliance_gold_v2_structural_cases
              WHERE source_version=:v
              ORDER BY priority_score DESC,created_at ASC
              OFFSET :target
            )
        """),{"v":CURRICULUM_VERSION,"target":target})

        total=conn.execute(text("""
            SELECT count(*) FROM alliance_gold_v2_structural_cases WHERE source_version=:v
        """),{"v":CURRICULUM_VERSION}).scalar() or 0

    return {"status":"PASS","curriculum_version":CURRICULUM_VERSION,
            "target":target,"cases":int(total),"created_or_preserved":int(total),
            "gold_v1_mutations":0}

def _decision_options(task_type):
    if task_type=="OWNERSHIP":
        return ["OWNED","NOT_OWNED","AMBIGUOUS"]
    if task_type=="SOURCE_TRACEABILITY":
        return ["SOURCE_SUPPORTED","NOT_SOURCE_SUPPORTED","AMBIGUOUS"]
    if task_type=="STRUCTURAL_CONFLICT":
        return ["TRUE_CONFLICT","FALSE_CONFLICT","AMBIGUOUS"]
    if task_type=="CONTENT_CLASS":
        return ["PROPERTY_AVAILABILITY","REQUIREMENT","INVENTORY_GROUP","NOISE","FRAGMENT"]
    return ["CORRECT","INCORRECT","AMBIGUOUS"]

class LabelIn(BaseModel):
    case_id: str
    human_decision: str
    human_confidence: str = "HIGH"
    field_type: str | None = None
    canonical_value: str | None = None
    boundary_valid: bool | None = None
    reason: str | None = None
    labeler_id: str | None = None

def _fetch_case(engine, case_id=None):
    with engine.connect() as conn:
        if case_id:
            row=conn.execute(text("""
                SELECT c.*,l.human_decision,l.human_confidence,l.canonical_value,
                       l.boundary_valid,l.reason,l.labeler_id
                FROM alliance_gold_v2_structural_cases c
                LEFT JOIN alliance_gold_v2_structural_labels l ON l.case_id=c.case_id
                WHERE c.case_id=:id
            """),{"id":case_id}).mappings().first()
        else:
            row=conn.execute(text("""
                SELECT c.*
                FROM alliance_gold_v2_structural_cases c
                LEFT JOIN alliance_gold_v2_structural_labels l ON l.case_id=c.case_id
                WHERE c.source_version=:v AND l.case_id IS NULL AND c.status='OPEN'
                ORDER BY c.priority_score DESC,c.created_at ASC
                LIMIT 1
            """),{"v":CURRICULUM_VERSION}).mappings().first()
    if not row:
        return None
    d=dict(row)
    d["machine_payload"]=_loads(d.get("machine_payload"),{})
    d["decision_options"]=_decision_options(d["task_type"])
    return foundation._json_safe(d)

def save_label(engine, data:LabelIn):
    _install(engine)
    with engine.begin() as conn:
        case=conn.execute(text("""
            SELECT * FROM alliance_gold_v2_structural_cases WHERE case_id=:id
        """),{"id":data.case_id}).mappings().first()
        if not case:
            return {"status":"ERROR","error":"CASE_NOT_FOUND"}

        allowed=_decision_options(case["task_type"])
        if data.human_decision not in allowed:
            return {"status":"ERROR","error":"INVALID_DECISION","allowed":allowed}
        if data.human_confidence not in ("HIGH","MEDIUM","LOW"):
            return {"status":"ERROR","error":"INVALID_CONFIDENCE"}

        conn.execute(text("""
            INSERT INTO alliance_gold_v2_structural_labels
            (label_id,case_id,entity_id,task_type,human_decision,human_confidence,
             field_type,canonical_value,boundary_valid,reason,labeler_id,label_schema_version)
            VALUES(:lid,:cid,:eid,:task,:decision,:conf,:ft,:cv,:bv,:reason,:labeler,:v)
            ON CONFLICT(case_id) DO UPDATE SET human_decision=EXCLUDED.human_decision,
              human_confidence=EXCLUDED.human_confidence,field_type=EXCLUDED.field_type,
              canonical_value=EXCLUDED.canonical_value,boundary_valid=EXCLUDED.boundary_valid,
              reason=EXCLUDED.reason,labeler_id=EXCLUDED.labeler_id,
              label_schema_version=EXCLUDED.label_schema_version,updated_at=now()
        """),{
            "lid":str(uuid.uuid4()),"cid":data.case_id,"eid":case["entity_id"],
            "task":case["task_type"],"decision":data.human_decision,
            "conf":data.human_confidence,"ft":data.field_type or case["field_type"],
            "cv":data.canonical_value,"bv":data.boundary_valid,"reason":data.reason,
            "labeler":data.labeler_id,"v":LABEL_SCHEMA_VERSION
        })
        conn.execute(text("""
            UPDATE alliance_gold_v2_structural_cases SET status='LABELED',updated_at=now()
            WHERE case_id=:id
        """),{"id":data.case_id})
    return {"status":"SAVED","case_id":data.case_id,"gold_v1_mutations":0}

def skip_case(engine, case_id):
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE alliance_gold_v2_structural_cases SET status='SKIPPED',updated_at=now()
            WHERE case_id=:id
        """),{"id":case_id})
    return {"status":"SKIPPED","case_id":case_id}

def evaluate(engine):
    _install(engine)
    with engine.connect() as conn:
        rows=[dict(x) for x in conn.execute(text("""
            SELECT c.entity_id,c.task_type,c.category,c.machine_payload,
                   l.human_decision,l.human_confidence,
                   r.ownership_status,
                   e.structural_audit_class,e.ablation_final_unexplained
            FROM alliance_gold_v2_structural_cases c
            JOIN alliance_gold_v2_structural_labels l ON l.case_id=c.case_id
            LEFT JOIN alliance_ownership_resolution_v292 r ON r.entity_id=c.entity_id
            LEFT JOIN alliance_final_exam_v2922 e ON e.entity_id=c.entity_id
            WHERE c.source_version=:v
        """),{"v":CURRICULUM_VERSION}).mappings().all()]

    totals=Counter()
    correct=Counter()
    for r in rows:
        task=r["task_type"]
        human=r["human_decision"]
        totals[task]+=1
        machine=None
        if task=="OWNERSHIP":
            status=str(r.get("ownership_status") or "")
            if status in ("OWNED_PARENT_WIRED","OWNED_ATOMIC_VALIDATED"):
                machine="OWNED"
            elif status=="UNRESOLVED":
                machine="AMBIGUOUS"
        elif task=="STRUCTURAL_CONFLICT":
            machine="TRUE_CONFLICT" if r.get("structural_audit_class")=="TRUE_STRUCTURAL_CONFLICT" else "FALSE_CONFLICT"
        elif task=="SOURCE_TRACEABILITY":
            machine="NOT_SOURCE_SUPPORTED" if bool(r.get("ablation_final_unexplained")) else "SOURCE_SUPPORTED"
        if machine==human:
            correct[task]+=1

    with engine.begin() as conn:
        for task,n in totals.items():
            num=correct[task]
            val=round(100.0*num/max(n,1),4)
            conn.execute(text("""
                INSERT INTO alliance_gold_v2_structural_eval
                (metric_key,metric_value,numerator,denominator,metric_scope,notes,eval_version)
                VALUES(:k,:v,:n,:d,'GOLD_V2_STRUCTURAL',
                       'Accuracy against human structural Gold labels',:ev)
                ON CONFLICT(metric_key) DO UPDATE SET metric_value=EXCLUDED.metric_value,
                  numerator=EXCLUDED.numerator,denominator=EXCLUDED.denominator,
                  metric_scope=EXCLUDED.metric_scope,notes=EXCLUDED.notes,
                  eval_version=EXCLUDED.eval_version,updated_at=now()
            """),{"k":task.lower()+"_accuracy","v":val,"n":num,"d":n,"ev":ENGINE_VERSION})

    labeled=len(rows)
    readiness = labeled>=50 and all(
        (correct[t]/totals[t] if totals[t] else 1.0)>=0.90
        for t in ("OWNERSHIP","STRUCTURAL_CONFLICT","SOURCE_TRACEABILITY")
    )
    return {
        "status":"PASS",
        "labeled":labeled,
        "target":TARGET,
        "task_totals":dict(totals),
        "task_correct":dict(correct),
        "accuracy":{t:round(100*correct[t]/totals[t],2) for t in totals if totals[t]},
        "llm_shadow_eligible":readiness,
        "rule":"At least 50 structural Gold labels and >=90% accuracy on each populated critical structural task.",
        "gold_v1_mutations":0,
    }

def status(engine):
    _install(engine)
    seed(engine,TARGET)
    with engine.connect() as conn:
        counts=[dict(x) for x in conn.execute(text("""
            SELECT task_type,category,status,count(*) cases
            FROM alliance_gold_v2_structural_cases
            WHERE source_version=:v
            GROUP BY task_type,category,status
            ORDER BY task_type,category,status
        """),{"v":CURRICULUM_VERSION}).mappings().all()]
        progress=conn.execute(text("""
            SELECT count(*) total,
                   count(*) FILTER(WHERE status='LABELED') labeled,
                   count(*) FILTER(WHERE status='OPEN') open,
                   count(*) FILTER(WHERE status='SKIPPED') skipped
            FROM alliance_gold_v2_structural_cases WHERE source_version=:v
        """),{"v":CURRICULUM_VERSION}).mappings().first()
        metrics=[dict(x) for x in conn.execute(text("""
            SELECT metric_key,metric_value,numerator,denominator,metric_scope,notes
            FROM alliance_gold_v2_structural_eval ORDER BY metric_key
        """)).mappings().all()]
    ev=evaluate(engine)
    return foundation._json_safe({
        "status":"PASS","version":VERSION,"mode":MODE,
        "engine_version":ENGINE_VERSION,"curriculum_version":CURRICULUM_VERSION,
        "label_schema_version":LABEL_SCHEMA_VERSION,
        "target":TARGET,"progress":dict(progress) if progress else {},
        "distribution":counts,"evaluation":ev,"stored_metrics":metrics,
        "gold_v1_policy":"IMMUTABLE_NO_MUTATION",
        "production_writes":0,"whatsapp_live_writes":0,"gold_v1_mutations":0,
    })

LAB_HTML="""<!doctype html><html><head><meta charset='utf-8'><title>Gold V2 Structural Lab</title></head>
<body style='font-family:Arial;background:#07111d;color:#eef6ff;max-width:1450px;margin:24px auto'>
<h1>🏆 Alliance Property Brain — Gold V2 Structural Lab</h1>
<p>Human structural judgment is ground truth. Gold V1 remains immutable. Production and WhatsApp are never written from this screen.</p>
<div id='progress'></div>
<div style='display:grid;grid-template-columns:1fr 1fr;gap:16px'>
<div style='background:#101d2b;padding:16px;border-radius:12px'>
<h3>Atomic / Child Evidence</h3><pre id='raw' style='white-space:pre-wrap'></pre>
</div>
<div style='background:#101d2b;padding:16px;border-radius:12px'>
<h3>Parent / Full Message Context</h3><pre id='parent' style='white-space:pre-wrap'></pre>
</div></div>
<div style='background:#101d2b;padding:16px;border-radius:12px;margin-top:16px'>
<h3>Machine Evidence</h3><pre id='machine' style='white-space:pre-wrap'></pre>
</div>
<div style='margin-top:16px'>
<b>Task:</b> <span id='task'></span> | <b>Category:</b> <span id='cat'></span> |
<b>Priority:</b> <span id='priority'></span>
</div>
<div id='buttons' style='margin-top:18px'></div>
<div style='margin-top:12px'>
<select id='confidence'><option>HIGH</option><option>MEDIUM</option><option>LOW</option></select>
<input id='labeler' placeholder='Labeler ID' style='padding:8px'>
<input id='canonical' placeholder='Canonical value if needed' style='padding:8px;width:280px'>
</div>
<textarea id='reason' placeholder='Reason / teaching note' style='width:100%;height:90px;margin-top:10px'></textarea>
<div style='margin-top:12px'>
<button onclick='save()' style='padding:12px 22px;background:#f5d76e;border:0;border-radius:8px;font-weight:bold'>Save Gold V2 Label</button>
<button onclick='skip()' style='padding:12px 22px'>Skip</button>
</div>
<pre id='msg'></pre>
<script>
let current=null, decision=null;
async function call(p,m='GET',body=null){const x=await fetch(p,{method:m,headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):null});const t=await x.text();let d;try{d=JSON.parse(t)}catch(e){d={raw:t}}if(!x.ok)throw Error(d.detail||d.raw||('HTTP '+x.status));return d}
async function load(){
  const s=await call('/api/property-brain/gold-v2-structural/status');
  document.getElementById('progress').textContent='Progress: '+JSON.stringify(s.progress)+' | LLM shadow eligible: '+s.evaluation.llm_shadow_eligible;
  current=await call('/api/property-brain/gold-v2-structural/next');
  if(!current){document.getElementById('msg').textContent='All current cases labeled or skipped.';return}
  document.getElementById('raw').textContent=current.raw_text||'';
  document.getElementById('parent').textContent=current.parent_message_text||'';
  document.getElementById('machine').textContent=JSON.stringify(current.machine_payload,null,2);
  document.getElementById('task').textContent=current.task_type;
  document.getElementById('cat').textContent=current.category;
  document.getElementById('priority').textContent=current.priority_score;
  document.getElementById('buttons').innerHTML='';
  current.decision_options.forEach(x=>{let b=document.createElement('button');b.textContent=x;b.style='padding:12px 18px;margin:4px';b.onclick=()=>{decision=x;document.getElementById('msg').textContent='Selected: '+x};document.getElementById('buttons').appendChild(b)});
}
async function save(){
 if(!current||!decision){document.getElementById('msg').textContent='Select a decision first.';return}
 await call('/api/property-brain/gold-v2-structural/label','POST',{
  case_id:current.case_id,human_decision:decision,human_confidence:document.getElementById('confidence').value,
  canonical_value:document.getElementById('canonical').value||null,reason:document.getElementById('reason').value||null,
  labeler_id:document.getElementById('labeler').value||null
 });
 decision=null;document.getElementById('reason').value='';document.getElementById('canonical').value='';await load();
}
async function skip(){if(!current)return;await call('/api/property-brain/gold-v2-structural/skip?case_id='+encodeURIComponent(current.case_id),'POST');await load()}
load()
</script></body></html>"""

def register(core):
    engine=_engine(core)
    app=_app(core)
    _install(engine)
    seed(engine,TARGET)

    if not foundation._route_exists(app,"/api/property-brain/gold-v2-structural/status"):
        @app.get("/api/property-brain/gold-v2-structural/status")
        def _status():
            return status(engine)

    if not foundation._route_exists(app,"/api/property-brain/gold-v2-structural/seed"):
        @app.post("/api/property-brain/gold-v2-structural/seed")
        def _seed(target:int=Query(default=TARGET,ge=20,le=300)):
            return seed(engine,target)

    if not foundation._route_exists(app,"/api/property-brain/gold-v2-structural/next"):
        @app.get("/api/property-brain/gold-v2-structural/next")
        def _next():
            return _fetch_case(engine)

    if not foundation._route_exists(app,"/api/property-brain/gold-v2-structural/label"):
        @app.post("/api/property-brain/gold-v2-structural/label")
        def _label(data:LabelIn):
            return save_label(engine,data)

    if not foundation._route_exists(app,"/api/property-brain/gold-v2-structural/skip"):
        @app.post("/api/property-brain/gold-v2-structural/skip")
        def _skip(case_id:str):
            return skip_case(engine,case_id)

    if not foundation._route_exists(app,"/api/property-brain/gold-v2-structural/evaluate"):
        @app.post("/api/property-brain/gold-v2-structural/evaluate")
        def _evaluate():
            return evaluate(engine)

    if not foundation._route_exists(app,"/property-brain/gold-v2-structural"):
        @app.get("/property-brain/gold-v2-structural",response_class=HTMLResponse)
        def _dashboard():
            return HTMLResponse(LAB_HTML)

    return {
        "status":"REGISTERED","version":VERSION,
        "dashboard":"/property-brain/gold-v2-structural",
        "target":TARGET,"gold_v1_mutations":0,
        "production_writes":0,"whatsapp_live_writes":0,
    }
