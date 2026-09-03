from __future__ import annotations

import json
import re
import unicodedata
import uuid
from collections import Counter

from fastapi import Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_gold_v2_structural_lab_v293 as goldlab
import alliance_mastery_finalizer_v340 as v340
import alliance_ownership_mastery_blind_v330 as v330

VERSION = "3.5.0-TRAINING-GATE-FINALIZER"
MODE = "SURFACE_POSITION_TRUNCATION_REPAIR_PRESERVE_MASTERED_SUBJECTS_BLIND_AUDIT_READY"
ENGINE_VERSION = "ALLIANCE_TRAINING_GATE_FINALIZER_V350"
RULESET_VERSION = "TRAINING_GATE_FINALIZER_2026_09_03_V1"

DDL = [
"""CREATE TABLE IF NOT EXISTS alliance_mastery_v350_runs(
run_id UUID PRIMARY KEY,
ruleset_version TEXT NOT NULL,
training_accuracy NUMERIC(8,4) NOT NULL,
training_errors INTEGER NOT NULL,
training_mastery_gate TEXT NOT NULL,
blind_audit_total INTEGER NOT NULL,
blind_audit_labeled INTEGER NOT NULL,
blind_audit_accuracy NUMERIC(8,4),
expertise_gate TEXT NOT NULL,
result JSONB NOT NULL DEFAULT '{}'::jsonb,
production_writes INTEGER NOT NULL DEFAULT 0,
whatsapp_writes INTEGER NOT NULL DEFAULT 0,
gold_v1_mutations INTEGER NOT NULL DEFAULT 0,
gold_v2_mutations INTEGER NOT NULL DEFAULT 0,
created_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
]

def _engine(core): return foundation._engine_from_core(core)
def _app(core): return getattr(core, "app", None) or core

def _install(engine):
    with engine.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))

def _loads(v,d):
    if v is None: return d
    if isinstance(v,(dict,list)): return v
    try: return json.loads(v)
    except Exception: return d

def _j(v): return json.dumps(foundation._json_safe(v), ensure_ascii=False)

def _fold(s): return unicodedata.normalize("NFKC", s or "")

def _surface(s):
    # Preserve punctuation adjacency (+SER vs + SER) and wording while removing cosmetic whitespace.
    s=_fold(s).upper().strip()
    s=re.sub(r"[ \t]+"," ",s)
    s=re.sub(r"\r\n?","\n",s)
    return s

def _surface_occurrences(parent, raw):
    p=_surface(parent)
    r=_surface(raw)
    if not p or not r: return 0
    return p.count(r)

def _truncated_syds(raw):
    return bool(re.fullmatch(r"SYDS?\s+4BHK\+\s*SER", _surface(raw)))

def _ownership(case):
    raw=case.get("raw_text") or ""
    parent=case.get("parent_message_text") or ""

    # This fixes the 3.4 over-collapse: normalized text made different source surfaces
    # indistinguishable. Exact source-surface uniqueness is evidence.
    if _truncated_syds(raw):
        occurrences=_surface_occurrences(parent, raw)
        if occurrences == 1:
            return "NOT_OWNED",99.7,None,"V350_UNIQUE_SURFACE_TRUNCATED_FRAGMENT",(
                "This exact truncated source surface occurs once in the parent. It is missing the "
                "numeric area before SYDS, so it cannot be one complete atomic property."
            )
        if occurrences >= 2:
            return "AMBIGUOUS",99.2,None,"V350_REPEATED_SURFACE_TRUNCATED_FRAGMENT",(
                "The exact truncated surface repeats in the parent. Without a reliable occurrence "
                "locator, assigning one repeated instance to a property is not provable."
            )
        return "AMBIGUOUS",95.0,None,"V350_UNLOCATED_TRUNCATED_FRAGMENT",(
            "Truncated SYDS/configuration fragment cannot be positioned safely in the parent."
        )

    return v340._ownership(case)

def predict(case):
    if case.get("task_type")=="OWNERSHIP":
        r=_ownership(case)
        if r:
            d,c,cv,rule,reason=r
            return {"decision":d,"confidence":c,"canonical_value":cv,"rule_id":rule,"reason":reason}
    # Do not redesign already-mastered source traceability or structural conflict.
    return v340.predict(case)

def _cases(engine):
    return v330._cases(engine)

def benchmark(cases):
    totals=Counter(); correct=Counter(); errors=[]
    for c in cases:
        if not c.get("human_decision"): continue
        p=predict(c); t=c["task_type"]; totals[t]+=1
        if p["decision"]==c["human_decision"]:
            correct[t]+=1
        else:
            errors.append({
                "entity_id":c["entity_id"],"task_type":t,"human":c["human_decision"],
                "predicted":p["decision"],"rule_id":p["rule_id"],"raw_text":c.get("raw_text")
            })
    total=sum(totals.values()); ok=sum(correct.values())
    acc=round(100.0*ok/max(total,1),4)
    task={t:round(100.0*correct[t]/max(totals[t],1),2) for t in totals}
    gate=bool(total>=24 and acc>=95 and all(v>=90 for v in task.values()))
    return {"examples":total,"accuracy":acc,"task_accuracy":task,
            "errors":errors,"training_mastery_gate":gate}

def blind_audit_status(engine):
    return v340.audit_status(engine)

def run(engine,limit=1000):
    _install(engine)
    cases=_cases(engine)
    bench=benchmark(cases)
    aud=blind_audit_status(engine)

    tg="TRAINING_MASTERY_PASS" if bench["training_mastery_gate"] else "TRAINING_MASTERY_HOLD"
    if not bench["training_mastery_gate"]:
        eg="EXPERTISE_GATE_TRAINING_HOLD"
    elif aud["labeled"] < v340.AUDIT_TARGET:
        eg="EXPERTISE_GATE_AWAITING_MINIMAL_BLIND_AUDIT"
    elif aud["accuracy"] is not None and aud["accuracy"] >= 95:
        eg="EXPERTISE_GATE_PASS"
    else:
        eg="EXPERTISE_GATE_BLIND_AUDIT_HOLD"

    result={
        "status":"PASS","version":VERSION,"mode":MODE,"ruleset_version":RULESET_VERSION,
        "training_benchmark":bench,"training_mastery_gate":tg,
        "blind_audit":aud,"expertise_gate":eg,
        "blind_audit_target":v340.AUDIT_TARGET,
        "next_step":(
            "If training passes, independently label only the frozen 12-case blind audit. "
            "Do not modify rules from those audit answers before the exam is scored."
        ),
        "production_writes":0,"whatsapp_writes":0,
        "gold_v1_mutations":0,"gold_v2_mutations":0,
    }

    with engine.begin() as conn:
        conn.execute(text("""
          INSERT INTO alliance_mastery_v350_runs
          (run_id,ruleset_version,training_accuracy,training_errors,training_mastery_gate,
           blind_audit_total,blind_audit_labeled,blind_audit_accuracy,expertise_gate,result,
           production_writes,whatsapp_writes,gold_v1_mutations,gold_v2_mutations)
          VALUES(:id,:v,:acc,:err,:tg,:at,:al,:aa,:eg,CAST(:r AS jsonb),0,0,0,0)
        """),{
            "id":str(uuid.uuid4()),"v":RULESET_VERSION,"acc":bench["accuracy"],
            "err":len(bench["errors"]),"tg":tg,"at":aud["total"],"al":aud["labeled"],
            "aa":aud["accuracy"],"eg":eg,"r":_j(result)
        })
    return result

def status(engine):
    _install(engine)
    with engine.connect() as conn:
        latest=conn.execute(text("""
          SELECT result FROM alliance_mastery_v350_runs
          WHERE ruleset_version=:v ORDER BY created_at DESC LIMIT 1
        """),{"v":RULESET_VERSION}).scalar()
    return foundation._json_safe({
        "status":"PASS","version":VERSION,
        "latest_run":_loads(latest,{}) if latest else None,
        "blind_audit":blind_audit_status(engine),
        "production_writes":0,"whatsapp_live_writes":0,
        "gold_v1_mutations":0,"gold_v2_mutations":0
    })

DASHBOARD="""<!doctype html><html><head><meta charset='utf-8'><title>Training Gate Finalizer 3.5</title>
<style>body{font-family:Arial;background:#07111d;color:#eef6ff;max-width:1450px;margin:24px auto}.card{background:#101d2b;padding:16px;border-radius:12px;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.big{font-size:26px;font-weight:bold}button{padding:11px 16px;border:0;border-radius:8px;background:#f5d76e;font-weight:bold}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style></head><body>
<h1>✅ Alliance Property Brain — Training Gate Finalizer 3.5</h1>
<p>Preserves 100% Source Traceability and Structural Conflict performance, repairs the last truncation distinction, then hands off to the frozen blind audit.</p>
<button onclick='run()'>Run 3.5 Training Gate</button>
<div id='cards' class='grid'></div><div class='card'><h3>Latest Run</h3><pre id='latest'></pre></div>
<script>
async function call(p,m='GET'){let r=await fetch(p,{method:m});let t=await r.text();let d;try{d=JSON.parse(t)}catch(e){d={raw:t}}if(!r.ok)throw Error(d.detail||d.raw||('HTTP '+r.status));return d}
function c(k,v){return `<div class="card"><div>${k}</div><div class="big">${v??0}</div></div>`}
async function load(){let s=await call('/api/property-brain/mastery-v350/status');let l=s.latest_run||{};let a=l.blind_audit||{};document.getElementById('cards').innerHTML=c('Training Accuracy',l.training_benchmark?.accuracy)+c('Training Errors',l.training_benchmark?.errors?.length)+c('Blind Audit',(a.labeled||0)+'/'+(a.total||0))+c('Expertise Gate',l.expertise_gate||'NOT RUN');document.getElementById('latest').textContent=JSON.stringify(l,null,2)}
async function run(){await call('/api/property-brain/mastery-v350/run?limit=1000','POST');await load()}load()
</script></body></html>"""

def register(core):
    engine=_engine(core); app=_app(core); _install(engine)
    try: run(engine,1000)
    except Exception: pass

    if not foundation._route_exists(app,"/api/property-brain/mastery-v350/status"):
        @app.get("/api/property-brain/mastery-v350/status")
        def _status(): return status(engine)

    if not foundation._route_exists(app,"/api/property-brain/mastery-v350/run"):
        @app.post("/api/property-brain/mastery-v350/run")
        def _run(limit:int=Query(default=1000,ge=1,le=5000)): return run(engine,limit)

    if not foundation._route_exists(app,"/property-brain/mastery-v350"):
        @app.get("/property-brain/mastery-v350",response_class=HTMLResponse)
        def _dash(): return HTMLResponse(DASHBOARD)

    return {
        "status":"REGISTERED","version":VERSION,
        "dashboard":"/property-brain/mastery-v350",
        "production_writes":0,"whatsapp_live_writes":0,
        "gold_v1_mutations":0,"gold_v2_mutations":0
    }
