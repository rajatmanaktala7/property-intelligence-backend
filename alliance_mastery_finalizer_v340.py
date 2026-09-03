from __future__ import annotations

import json
import re
import uuid
from collections import Counter

from fastapi import Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_gold_v2_structural_lab_v293 as goldlab
import alliance_ownership_mastery_blind_v330 as v330

VERSION = "3.4.0-MASTERY-FINALIZER-BLIND-AUDIT"
MODE = "CONTEXT_SENSITIVE_OWNERSHIP_FINALIZER_MINIMAL_INDEPENDENT_BLIND_AUDIT"
ENGINE_VERSION = "ALLIANCE_MASTERY_FINALIZER_V340"
RULESET_VERSION = "MASTERY_FINALIZER_2026_09_03_V1"
AUDIT_VERSION = "BLIND_AUDIT_V1_2026_09_03"
AUDIT_TARGET = 12

DDL = [
"""CREATE TABLE IF NOT EXISTS alliance_mastery_v340_predictions(
prediction_id UUID PRIMARY KEY,
case_id UUID NOT NULL,
entity_id TEXT NOT NULL,
task_type TEXT NOT NULL,
decision TEXT NOT NULL,
confidence NUMERIC(6,2) NOT NULL,
canonical_value TEXT,
rule_id TEXT NOT NULL,
disposition TEXT NOT NULL,
reason TEXT NOT NULL,
ruleset_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(case_id,ruleset_version))""",

"""CREATE TABLE IF NOT EXISTS alliance_mastery_v340_blind_audit_cases(
audit_id UUID PRIMARY KEY,
blind_id UUID NOT NULL,
audit_version TEXT NOT NULL,
priority INTEGER NOT NULL,
reason TEXT NOT NULL,
raw_text TEXT NOT NULL,
predicted_class TEXT,
predicted_transaction TEXT,
predicted_ownership TEXT,
prediction_confidence NUMERIC(6,2),
human_class TEXT,
human_transaction TEXT,
human_ownership TEXT,
human_confidence TEXT,
human_reason TEXT,
review_status TEXT NOT NULL DEFAULT 'OPEN',
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(blind_id,audit_version))""",

"""CREATE TABLE IF NOT EXISTS alliance_mastery_v340_runs(
run_id UUID PRIMARY KEY,
ruleset_version TEXT NOT NULL,
training_accuracy NUMERIC(8,4) NOT NULL,
training_errors INTEGER NOT NULL,
training_mastery_gate TEXT NOT NULL,
audit_total INTEGER NOT NULL,
audit_labeled INTEGER NOT NULL,
audit_accuracy NUMERIC(8,4),
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
def _norm(s): return v330._norm(s)
def _lines(s): return v330._lines(s)

def _rental_inventory_header(raw):
    return bool(re.fullmatch(r"(?:rental|rent|sale|resale)\s+inventory", _norm(raw)))

def _raw_occurrences(parent, raw):
    nr=_norm(raw); np=_norm(parent)
    if not nr or not np: return 0
    return np.count(nr)

def _missing_syds_fragment(raw):
    return bool(re.match(r"^syds?\b", _norm(raw)))

def _compact_rent_locality(raw):
    n=_norm(raw)
    loc=v330._explicit_locality(raw)
    if not loc: return False
    return bool(re.search(r"\b\d+(?:\.\d+)?\s*lac\b",n) and len(_lines(raw))<=3 and not _missing_syds_fragment(raw))

def _ownership(case):
    raw=case.get("raw_text") or ""
    parent=case.get("parent_message_text") or ""
    n=_norm(raw)

    if _rental_inventory_header(raw):
        return "OWNED",99.9,v330._tx(raw),"V340_TRANSACTION_INVENTORY_HEADER","Explicit transaction inventory header owns the transaction scope."

    # Identical orphan fragments can occur repeatedly in the same long message.
    # If the exact fragment repeats, source position is insufficient to assign one occurrence safely.
    if re.fullmatch(r"syds?\s+\d+\s*bhk\s*(?:ser|servant)?",n):
        occurrences=_raw_occurrences(parent,raw)
        if occurrences>=2:
            return "AMBIGUOUS",99.5,None,"V340_DUPLICATE_TRUNCATED_FRAGMENT","The same truncated fragment occurs multiple times in the parent, so exact occurrence ownership is not provable."
        return "NOT_OWNED",99.5,None,"V340_UNIQUE_TRUNCATED_FRAGMENT","The unique fragment is missing its leading area and cannot form a complete atomic property."

    # Compact rent + locality with no missing-SYDS prefix belongs to this atom.
    # This explicitly repairs 30/40 LAC + Sushant Lok cases. Forward-header logic is
    # NOT allowed to override because there is no truncated property prefix in this atom.
    if _compact_rent_locality(raw):
        loc=v330._explicit_locality(raw)
        return "OWNED",99.8,loc.title() if loc else None,"V340_COMPACT_RENT_LOCALITY_OWNED","Compact rent+locality atom has no missing property prefix; locality belongs to the current record."

    # Missing-SYDS records remain non-owned even if they contain locality/rent.
    if _missing_syds_fragment(raw):
        return "NOT_OWNED",99.8,None,"V340_MISSING_PREFIX_NOT_OWNED","Span begins with SYDS but lacks its numeric area, proving truncation."

    base=v330._ownership(case)
    if base:
        return base
    return None

def predict(case):
    task=case.get("task_type")
    if task=="OWNERSHIP":
        r=_ownership(case)
        if r:
            d,c,cv,rule,reason=r
            return {"decision":d,"confidence":c,"canonical_value":cv,"rule_id":rule,"reason":reason}
    # Preserve already-mastered source traceability and structural conflict.
    if task in ("SOURCE_TRACEABILITY","STRUCTURAL_CONFLICT"):
        return v330.predict(case)
    return {"decision":"AMBIGUOUS","confidence":70.0,"canonical_value":None,
            "rule_id":"V340_ABSTAIN","reason":"No deterministic mastery rule applies."}

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
    return {"examples":total,"accuracy":acc,"task_accuracy":task,"errors":errors,"training_mastery_gate":gate}

def seed_audit(engine,target=AUDIT_TARGET):
    _install(engine)
    with engine.begin() as conn:
        existing=conn.execute(text("""SELECT count(*) FROM alliance_mastery_v340_blind_audit_cases
                                      WHERE audit_version=:v"""),{"v":AUDIT_VERSION}).scalar() or 0
        if existing>=target:
            return {"status":"ALREADY_SEEDED","total":int(existing)}

        rows=[dict(r) for r in conn.execute(text("""
          SELECT b.blind_id,b.raw_text,p.predicted_class,p.predicted_transaction,
                 p.predicted_ownership,p.confidence
          FROM alliance_mastery_v330_blind_cases b
          JOIN alliance_mastery_v330_blind_predictions p ON p.blind_id=b.blind_id
          WHERE b.blindset_version=:bv AND p.blindset_version=:bv
          ORDER BY p.confidence ASC,b.frozen_at ASC
        """),{"bv":v330.BLINDSET_VERSION}).mappings().all()]

        def risk(r):
            raw=r["raw_text"] or ""; n=_norm(raw)
            score=0
            reasons=[]
            if float(r.get("confidence") or 0)<90:
                score+=50; reasons.append("LOW_CONFIDENCE")
            if re.search(r"\b(?:syds?|maint|fully furnished|inventory)\b",n):
                score+=20; reasons.append("BOUNDARY_PATTERN")
            if re.search(r"\b(?:highway|airport|railway|km|away)\b",n):
                score+=15; reasons.append("REFERENCE_LOCATION")
            if len(_lines(raw))>=8:
                score+=10; reasons.append("LONG_MULTI_LINE")
            if r.get("predicted_class")=="UNRESOLVED":
                score+=30; reasons.append("UNRESOLVED_CLASS")
            return score,",".join(reasons) or "DIVERSITY_SAMPLE"

        ranked=sorted(((risk(r),r) for r in rows), key=lambda x:(-x[0][0],str(x[1]["blind_id"])))
        chosen=[]
        seen_sig=set()
        for (score,reason),r in ranked:
            sig=(r.get("predicted_class"),r.get("predicted_transaction"),r.get("predicted_ownership"),reason.split(",")[0])
            if sig in seen_sig and len(chosen)<target//2:
                continue
            chosen.append((score,reason,r));seen_sig.add(sig)
            if len(chosen)>=target: break
        if len(chosen)<target:
            for (score,reason),r in ranked:
                if any(str(x[2]["blind_id"])==str(r["blind_id"]) for x in chosen): continue
                chosen.append((score,reason,r))
                if len(chosen)>=target: break

        for score,reason,r in chosen:
            conn.execute(text("""
              INSERT INTO alliance_mastery_v340_blind_audit_cases
              (audit_id,blind_id,audit_version,priority,reason,raw_text,predicted_class,
               predicted_transaction,predicted_ownership,prediction_confidence)
              VALUES(:id,:bid,:av,:priority,:reason,:raw,:pc,:pt,:po,:conf)
              ON CONFLICT(blind_id,audit_version) DO NOTHING
            """),{"id":str(uuid.uuid4()),"bid":str(r["blind_id"]),"av":AUDIT_VERSION,
                  "priority":int(score),"reason":reason,"raw":r["raw_text"],
                  "pc":r.get("predicted_class"),"pt":r.get("predicted_transaction"),
                  "po":r.get("predicted_ownership"),"conf":r.get("confidence")})
        final=conn.execute(text("""SELECT count(*) FROM alliance_mastery_v340_blind_audit_cases
                                   WHERE audit_version=:v"""),{"v":AUDIT_VERSION}).scalar() or 0
        return {"status":"SEEDED","total":int(final),"target":target}

def audit_status(engine):
    with engine.connect() as conn:
        total=conn.execute(text("""SELECT count(*) FROM alliance_mastery_v340_blind_audit_cases
                                   WHERE audit_version=:v"""),{"v":AUDIT_VERSION}).scalar() or 0
        labeled=conn.execute(text("""SELECT count(*) FROM alliance_mastery_v340_blind_audit_cases
                                     WHERE audit_version=:v AND review_status='LABELED'"""),{"v":AUDIT_VERSION}).scalar() or 0
        rows=[dict(r) for r in conn.execute(text("""
          SELECT * FROM alliance_mastery_v340_blind_audit_cases
          WHERE audit_version=:v AND review_status='LABELED'
        """),{"v":AUDIT_VERSION}).mappings().all()]
    comparable=correct=0
    for r in rows:
        pairs=[
          (r.get("predicted_class"),r.get("human_class")),
          (r.get("predicted_transaction"),r.get("human_transaction")),
          (r.get("predicted_ownership"),r.get("human_ownership")),
        ]
        for p,h in pairs:
            if h:
                comparable+=1
                if p==h: correct+=1
    accuracy=round(100.0*correct/max(comparable,1),2) if comparable else None
    return {"total":int(total),"labeled":int(labeled),"comparable_fields":comparable,
            "correct_fields":correct,"accuracy":accuracy}

def next_audit(engine):
    with engine.connect() as conn:
        r=conn.execute(text("""
          SELECT audit_id,blind_id,priority,reason,raw_text,predicted_class,predicted_transaction,
                 predicted_ownership,prediction_confidence
          FROM alliance_mastery_v340_blind_audit_cases
          WHERE audit_version=:v AND review_status='OPEN'
          ORDER BY priority DESC,created_at ASC LIMIT 1
        """),{"v":AUDIT_VERSION}).mappings().first()
    return foundation._json_safe(dict(r) if r else {"status":"COMPLETE"})

def save_audit(engine,payload):
    allowed_class={"PROPERTY_AVAILABILITY","REQUIREMENT","INVENTORY_GROUP","FRAGMENT","NOISE","UNRESOLVED","AMBIGUOUS"}
    allowed_tx={"SALE","RENT","UNKNOWN","AMBIGUOUS"}
    allowed_own={"OWNED","NOT_OWNED","AMBIGUOUS"}
    hc=payload.get("human_class")
    ht=payload.get("human_transaction")
    ho=payload.get("human_ownership")
    if hc and hc not in allowed_class: raise ValueError("Invalid human_class")
    if ht and ht not in allowed_tx: raise ValueError("Invalid human_transaction")
    if ho and ho not in allowed_own: raise ValueError("Invalid human_ownership")
    with engine.begin() as conn:
        conn.execute(text("""
          UPDATE alliance_mastery_v340_blind_audit_cases
          SET human_class=:hc,human_transaction=:ht,human_ownership=:ho,
              human_confidence=:conf,human_reason=:reason,review_status='LABELED',updated_at=now()
          WHERE audit_id=:id AND audit_version=:av
        """),{"hc":hc,"ht":ht,"ho":ho,"conf":payload.get("human_confidence","HIGH"),
              "reason":payload.get("human_reason"),"id":payload["audit_id"],"av":AUDIT_VERSION})
    return audit_status(engine)

def run(engine,limit=1000):
    _install(engine)
    cases=_cases(engine);bench=benchmark(cases)
    seed=seed_audit(engine,AUDIT_TARGET)
    aud=audit_status(engine)
    unlabeled=[c for c in cases if not c.get("human_decision") and c.get("status")=="OPEN"][:max(1,min(int(limit),5000))]
    counts=Counter()
    with engine.begin() as conn:
        for c in unlabeled:
            p=predict(c)
            if p["rule_id"]!="V340_ABSTAIN" and p["confidence"]>=98:disp="EXPERT_RESOLVED"
            elif p["confidence"]>=90:disp="SHADOW_RESOLVED"
            else:disp="EXCEPTION"
            counts[disp]+=1
            conn.execute(text("""
              INSERT INTO alliance_mastery_v340_predictions
              (prediction_id,case_id,entity_id,task_type,decision,confidence,canonical_value,
               rule_id,disposition,reason,ruleset_version)
              VALUES(:id,:cid,:eid,:task,:d,:conf,:cv,:rule,:disp,:reason,:v)
              ON CONFLICT(case_id,ruleset_version) DO UPDATE SET decision=EXCLUDED.decision,
                confidence=EXCLUDED.confidence,canonical_value=EXCLUDED.canonical_value,
                rule_id=EXCLUDED.rule_id,disposition=EXCLUDED.disposition,reason=EXCLUDED.reason,updated_at=now()
            """),{"id":str(uuid.uuid4()),"cid":str(c["case_id"]),"eid":c["entity_id"],"task":c["task_type"],
                  "d":p["decision"],"conf":p["confidence"],"cv":p.get("canonical_value"),"rule":p["rule_id"],
                  "disp":disp,"reason":p["reason"],"v":RULESET_VERSION})

        tg="TRAINING_MASTERY_PASS" if bench["training_mastery_gate"] else "TRAINING_MASTERY_HOLD"
        if not bench["training_mastery_gate"]:
            eg="EXPERTISE_GATE_TRAINING_HOLD"
        elif aud["labeled"]<AUDIT_TARGET:
            eg="EXPERTISE_GATE_AWAITING_MINIMAL_BLIND_AUDIT"
        elif aud["accuracy"] is not None and aud["accuracy"]>=95:
            eg="EXPERTISE_GATE_PASS"
        else:
            eg="EXPERTISE_GATE_BLIND_AUDIT_HOLD"

        result={"status":"PASS","version":VERSION,"mode":MODE,"ruleset_version":RULESET_VERSION,
                "training_benchmark":bench,"training_mastery_gate":tg,
                "blind_audit":aud,"blind_audit_seed":seed,"expertise_gate":eg,
                "expert_resolved":counts["EXPERT_RESOLVED"],"shadow_resolved":counts["SHADOW_RESOLVED"],
                "exceptions":counts["EXCEPTION"],
                "policy":"Only 12 high-information blind cases require independent truth. The other 88 frozen cases remain untouched. Pseudo-labels cannot certify expertise.",
                "production_writes":0,"whatsapp_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0}
        conn.execute(text("""
          INSERT INTO alliance_mastery_v340_runs
          (run_id,ruleset_version,training_accuracy,training_errors,training_mastery_gate,
           audit_total,audit_labeled,audit_accuracy,expertise_gate,result,
           production_writes,whatsapp_writes,gold_v1_mutations,gold_v2_mutations)
          VALUES(:id,:v,:acc,:err,:tg,:at,:al,:aa,:eg,CAST(:r AS jsonb),0,0,0,0)
        """),{"id":str(uuid.uuid4()),"v":RULESET_VERSION,"acc":bench["accuracy"],"err":len(bench["errors"]),
              "tg":tg,"at":aud["total"],"al":aud["labeled"],"aa":aud["accuracy"],"eg":eg,"r":_j(result)})
    return result

def status(engine):
    _install(engine)
    with engine.connect() as conn:
        latest=conn.execute(text("""SELECT result FROM alliance_mastery_v340_runs
                                    WHERE ruleset_version=:v ORDER BY created_at DESC LIMIT 1"""),
                            {"v":RULESET_VERSION}).scalar()
    return foundation._json_safe({"status":"PASS","version":VERSION,
      "latest_run":_loads(latest,{}) if latest else None,
      "audit":audit_status(engine),
      "production_writes":0,"whatsapp_live_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0})

DASHBOARD="""<!doctype html><html><head><meta charset='utf-8'><title>Mastery Finalizer 3.4</title>
<style>body{font-family:Arial;background:#07111d;color:#eef6ff;max-width:1450px;margin:24px auto}.card{background:#101d2b;padding:16px;border-radius:12px;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.big{font-size:26px;font-weight:bold}button{padding:10px 15px;margin:4px;border:0;border-radius:8px;background:#f5d76e;font-weight:bold}pre{white-space:pre-wrap;overflow-wrap:anywhere}textarea{width:100%;min-height:90px}</style></head><body>
<h1>🏁 Alliance Property Brain — Mastery Finalizer 3.4</h1>
<p>Fixes the last training failures and reduces independent blind truth to a 12-case audit, not lead-by-lead review.</p>
<button onclick='run()'>Run 3.4 Finalizer</button>
<div id='cards' class='grid'></div><div class='card'><h3>Latest Run</h3><pre id='latest'></pre></div>
<div class='card'><h3>Current Blind Audit Case</h3><pre id='case'></pre>
<div>Class:
<button onclick="label('PROPERTY_AVAILABILITY',null,null)">Property</button>
<button onclick="label('REQUIREMENT',null,null)">Requirement</button>
<button onclick="label('FRAGMENT',null,null)">Fragment</button>
<button onclick="label('NOISE',null,null)">Noise</button></div>
<div>Transaction:
<button onclick="label(null,'SALE',null)">Sale</button>
<button onclick="label(null,'RENT',null)">Rent</button>
<button onclick="label(null,'UNKNOWN',null)">Unknown</button></div>
<div>Ownership:
<button onclick="label(null,null,'OWNED')">Owned</button>
<button onclick="label(null,null,'NOT_OWNED')">Not Owned</button>
<button onclick="label(null,null,'AMBIGUOUS')">Ambiguous</button></div>
<p>Choose only the field(s) you can independently verify. Save combines your selections.</p>
<textarea id='reason' placeholder='Optional reason'></textarea><br><button onclick='save()'>Save Blind Audit Label</button></div>
<script>
let cur=null,sel={};
async function call(p,m='GET',body=null){let r=await fetch(p,{method:m,headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):null});let t=await r.text();let d;try{d=JSON.parse(t)}catch(e){d={raw:t}}if(!r.ok)throw Error(d.detail||d.raw||('HTTP '+r.status));return d}
function c(k,v){return `<div class="card"><div>${k}</div><div class="big">${v??0}</div></div>`}
async function load(){let s=await call('/api/property-brain/mastery-v340/status');let l=s.latest_run||{};let a=s.audit||{};document.getElementById('cards').innerHTML=c('Training Accuracy',l.training_benchmark?.accuracy)+c('Audit Labeled',(a.labeled||0)+'/'+(a.total||0))+c('Blind Audit Accuracy',a.accuracy??'—')+c('Expertise Gate',l.expertise_gate||'NOT RUN');document.getElementById('latest').textContent=JSON.stringify(l,null,2);cur=await call('/api/property-brain/mastery-v340/audit/next');document.getElementById('case').textContent=JSON.stringify(cur,null,2);sel={}}
function label(c,t,o){if(c)sel.human_class=c;if(t)sel.human_transaction=t;if(o)sel.human_ownership=o}
async function save(){if(!cur||!cur.audit_id)return;await call('/api/property-brain/mastery-v340/audit/label','POST',{audit_id:cur.audit_id,...sel,human_confidence:'HIGH',human_reason:document.getElementById('reason').value});document.getElementById('reason').value='';await run()}
async function run(){await call('/api/property-brain/mastery-v340/run?limit=1000','POST');await load()}load()
</script></body></html>"""

def register(core):
    engine=_engine(core);app=_app(core);_install(engine)
    try:run(engine,1000)
    except Exception:pass

    if not foundation._route_exists(app,"/api/property-brain/mastery-v340/status"):
        @app.get("/api/property-brain/mastery-v340/status")
        def _status(): return status(engine)

    if not foundation._route_exists(app,"/api/property-brain/mastery-v340/run"):
        @app.post("/api/property-brain/mastery-v340/run")
        def _run(limit:int=Query(default=1000,ge=1,le=5000)): return run(engine,limit)

    if not foundation._route_exists(app,"/api/property-brain/mastery-v340/audit/next"):
        @app.get("/api/property-brain/mastery-v340/audit/next")
        def _next(): return next_audit(engine)

    if not foundation._route_exists(app,"/api/property-brain/mastery-v340/audit/label"):
        @app.post("/api/property-brain/mastery-v340/audit/label")
        def _label(payload:dict): return save_audit(engine,payload)

    if not foundation._route_exists(app,"/property-brain/mastery-v340"):
        @app.get("/property-brain/mastery-v340",response_class=HTMLResponse)
        def _dash(): return HTMLResponse(DASHBOARD)

    return {"status":"REGISTERED","version":VERSION,"dashboard":"/property-brain/mastery-v340",
            "audit_target":AUDIT_TARGET,"production_writes":0,"whatsapp_live_writes":0,
            "gold_v1_mutations":0,"gold_v2_mutations":0}
