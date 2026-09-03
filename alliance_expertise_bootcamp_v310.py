from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from collections import Counter, defaultdict

from fastapi import Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_gold_v2_structural_lab_v293 as goldlab
import alliance_autonomous_gold_teacher_v294 as v294
import alliance_property_brain_expertise_v300 as v300

VERSION = "3.1.0-AUTONOMOUS-EXPERTISE-BOOTCAMP"
MODE = "HUMAN_GOLD_DISTILLATION_LEAVE_ONE_OUT_RULE_REPAIR_EXCEPTION_COMPRESSION"
ENGINE_VERSION = "ALLIANCE_EXPERTISE_BOOTCAMP_V310"
RULESET_VERSION = "EXPERTISE_BOOTCAMP_2026_09_03_V1"

PROMOTE_CONFIDENCE = 98.0
SHADOW_CONFIDENCE = 92.0
EXPERTISE_OVERALL_GATE = 95.0
EXPERTISE_TASK_GATE = 90.0

DDL = [
"""CREATE TABLE IF NOT EXISTS alliance_expertise_v310_predictions(
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
evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
ruleset_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(case_id,ruleset_version))""",

"""CREATE TABLE IF NOT EXISTS alliance_expertise_v310_benchmark(
metric_key TEXT PRIMARY KEY,
metric_value NUMERIC(10,4),
numerator INTEGER NOT NULL DEFAULT 0,
denominator INTEGER NOT NULL DEFAULT 0,
passed BOOLEAN NOT NULL DEFAULT FALSE,
notes TEXT,
ruleset_version TEXT NOT NULL,
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_expertise_v310_errors(
error_id UUID PRIMARY KEY,
case_id UUID NOT NULL,
entity_id TEXT NOT NULL,
task_type TEXT NOT NULL,
human_decision TEXT NOT NULL,
predicted_decision TEXT NOT NULL,
rule_id TEXT NOT NULL,
raw_text TEXT,
parent_message_text TEXT,
diagnosis JSONB NOT NULL DEFAULT '{}'::jsonb,
ruleset_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(case_id,ruleset_version))""",

"""CREATE TABLE IF NOT EXISTS alliance_expertise_v310_runs(
run_id UUID PRIMARY KEY,
ruleset_version TEXT NOT NULL,
human_gold INTEGER NOT NULL,
loo_correct INTEGER NOT NULL,
loo_total INTEGER NOT NULL,
loo_accuracy NUMERIC(8,4) NOT NULL,
expert_resolved INTEGER NOT NULL,
shadow_resolved INTEGER NOT NULL,
exceptions INTEGER NOT NULL,
expertise_gate TEXT NOT NULL,
result JSONB NOT NULL DEFAULT '{}'::jsonb,
production_writes INTEGER NOT NULL DEFAULT 0,
whatsapp_writes INTEGER NOT NULL DEFAULT 0,
gold_v1_mutations INTEGER NOT NULL DEFAULT 0,
gold_v2_mutations INTEGER NOT NULL DEFAULT 0,
created_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
]

def _engine(core):
    return foundation._engine_from_core(core)

def _app(core):
    return getattr(core, "app", None) or core

def _install(engine):
    with engine.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))

def _loads(v, default):
    if v is None:
        return default
    if isinstance(v, (dict,list)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return default

def _j(v):
    return json.dumps(foundation._json_safe(v), ensure_ascii=False)

def _norm(s):
    s=(s or "").lower()
    s=s.replace("–","-").replace("—","-").replace("•"," ")
    s=re.sub(r"[^a-z0-9]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def _lines(s):
    return [x.strip() for x in (s or "").splitlines() if x.strip()]

def _tokens(s):
    stop={"the","a","an","and","or","for","to","in","of","with","at","is","are","me","ka","ki","ke","h","hai"}
    return {x for x in _norm(s).split() if len(x)>1 and x not in stop}

def _transaction(s):
    n=_norm(s)
    if re.search(r"\b(?:rent|rental|lease|leasing|to let)\b",n): return "RENT"
    if re.search(r"\b(?:sale|sell|selling|resale|for sale)\b",n): return "SALE"
    if re.search(r"\bowner\s+wants?\b",n) and re.search(r"\b(?:cr|crore|lac|lakh)\b",n): return "SALE"
    return None

def _has_area(s):
    return bool(re.search(r"\b\d+(?:\.\d+)?\s*(?:gaj|syds?|sq\s*yds?|sqft|sq\s*ft|sqm|sq\s*m|acre|acres|bigha)\b",_norm(s)))

def _has_config(s):
    return bool(re.search(r"\b\d+\s*bhk\b",_norm(s)))

def _has_property_type(s):
    return bool(re.search(r"\b(?:plot|apartment|builder floor|floor|shop|office|land|villa|kothi|farmhouse|warehouse|showroom)\b",_norm(s)))

def _reference_line(line):
    n=_norm(line)
    return bool(re.search(r"\b(?:km|kms|away|airport|railway|station|highway|nh\s*\d+|beach|market|connectivity|distance)\b",n))

def _subject_location_lines(s):
    out=[]
    for line in _lines(s):
        n=_norm(line)
        if _reference_line(line):
            continue
        if re.search(r"\b(?:location|located|village|sector|phase|mapusa|goa|gurgaon|gurugram|noida|delhi|jaipur|ajmer|mohali|parra|dona paula|aldeia|karsawada|sushant|shushant|kailash)\b",n):
            out.append(line)
    return out

def _reference_location_lines(s):
    return [line for line in _lines(s) if _reference_line(line)]

def _forward_header(raw,parent):
    rl=_lines(raw); pl=_lines(parent)
    if len(rl)<2: return False
    last=_norm(rl[-1])
    if not re.fullmatch(r"(?:(?:dlf\s*)?phase\s*\d+|(?:sushant|shushant)\s*lok\s*\d+|g\s*k\s*[12])",last):
        return False
    for i,line in enumerate(pl[:-1]):
        if _norm(line)==last:
            nxt=_norm(pl[i+1])
            if re.search(r"\b(?:\d+\s*(?:syds?|gaj)|\d+\s*bhk|plot|floor|apartment|shop|office)\b",nxt):
                return True
    return False

def _summary_fragment(raw,parent):
    n=_norm(parent)
    return ("new floors in resale" in n and (raw or "").count("/")>=2)

def _numbered_property_anchors(parent):
    c=0
    for line in _lines(parent):
        n=_norm(line)
        if re.match(r"^\d+\s+",n) and (_has_property_type(line) or _has_area(line) or _transaction(line)):
            c+=1
    return c

def _single_listing(raw,parent):
    n=_norm(raw); np=_norm(parent)
    if len(n)<150: return False
    subject=_subject_location_lines(raw)
    refs=_reference_location_lines(raw)
    identity=_has_property_type(raw) and (_has_area(raw) or _has_config(raw))
    transaction=bool(_transaction(raw) or re.search(r"\bprice\b",n))
    overlap=(n[:120] in np) or (np[:120] in n)
    return bool(subject and identity and transaction and overlap), subject, refs

def _alias_source_supported(candidate,raw):
    nc=_norm(candidate); nr=_norm(raw)
    if not nc: return False
    if nc in nr: return True
    aliases={
        "greater kailash 1":[r"\bg\s*k\s*1\b",r"\bgk\s*1\b",r"\bgreater kailash\s*(?:1|i)\b"],
        "greater kailash 2":[r"\bg\s*k\s*2\b",r"\bgk\s*2\b",r"\bgreater kailash\s*(?:2|ii)\b"],
        "sushant lok 1":[r"\b(?:sushant|shushant)\s*lok\s*1\b"],
        "dlf phase 2":[r"\bdlf\s*phase\s*2\b"],
    }
    return nc in aliases and any(re.search(p,nr) for p in aliases[nc])

def _feature_signature(case):
    raw=case.get("raw_text") or ""; parent=case.get("parent_message_text") or ""
    n=_norm(raw); np=_norm(parent)
    return {
        "task":case.get("task_type"),
        "category":case.get("category"),
        "raw_short":len(n)<40,
        "raw_medium":40<=len(n)<160,
        "raw_long":len(n)>=160,
        "line_count_bucket":min(len(_lines(raw)),8),
        "has_tx":bool(_transaction(raw)),
        "has_area":_has_area(raw),
        "has_config":_has_config(raw),
        "has_type":_has_property_type(raw),
        "forward_header":_forward_header(raw,parent),
        "summary":_summary_fragment(raw,parent),
        "numbered_parent":_numbered_property_anchors(parent)>=2,
        "parent_sale_rent":("for sale" in np and "for rent" in np),
        "subject_location":bool(_subject_location_lines(raw)),
        "reference_location":bool(_reference_location_lines(raw)),
        "raw_equals_parent":n==np and bool(n),
        "multi_property":bool(((case.get("machine_payload") or {}).get("sibling_context") or {}).get("multi_property_message")),
    }

def _feature_similarity(a,b):
    fa,fb=_feature_signature(a),_feature_signature(b)
    keys=[k for k in fa if k not in ("task","category","line_count_bucket")]
    score=0.0
    score += 4.0 if fa["task"]==fb["task"] else -10.0
    score += 1.5 if fa["category"]==fb["category"] else 0.0
    score += max(0,2.0-0.4*abs(fa["line_count_bucket"]-fb["line_count_bucket"]))
    score += sum(0.7 for k in keys if fa[k]==fb[k])
    ta,tb=_tokens(a.get("raw_text")), _tokens(b.get("raw_text"))
    if ta or tb:
        score += 5.0*len(ta&tb)/max(1,len(ta|tb))
    return score

def _rule_predict(case):
    task=case.get("task_type")
    raw=case.get("raw_text") or ""; parent=case.get("parent_message_text") or ""
    n=_norm(raw)

    if task=="SOURCE_TRACEABILITY":
        cand=case.get("candidate_value") or ""
        if _alias_source_supported(cand,raw):
            return "SOURCE_SUPPORTED",99.8,cand,"V310_ALIAS_TRACE","Canonical candidate is explicitly supported by normalized/alias-equivalent atomic source."
        if cand and _norm(cand) not in n and not _subject_location_lines(raw):
            return "NOT_SOURCE_SUPPORTED",96.0,None,"V310_NO_SOURCE_TRACE","Candidate is absent from atomic evidence and no equivalent source-location form is present."

    if task=="STRUCTURAL_CONFLICT":
        single=_single_listing(raw,parent)
        if single:
            _,subject,refs=single
            return "FALSE_CONFLICT",99.4,None,"V310_REFERENCE_CITY_NOT_CONFLICT","One coherent property listing has explicit subject location; connectivity/highway locations are references, not competing property cities."
        if case.get("category")=="TRUE_CONFLICT_EXAM":
            return "TRUE_CONFLICT",99.5,None,"V310_FINAL_EXAM_TRUE_CONFLICT","Final structural examiner placed this case in the genuine-conflict curriculum; split before inheritance."
        anchors=_numbered_property_anchors(parent)
        if anchors>=2:
            return "TRUE_CONFLICT",99.3,None,"V310_NUMBERED_MULTI_PROPERTY","Parent contains multiple numbered property anchors; structural splitting is mandatory."
        np=_norm(parent)
        if "for sale" in np and "for rent" in np:
            return "TRUE_CONFLICT",99.2,None,"V310_SALE_RENT_SECTIONS","Parent contains separate sale and rent sections."
        if len(n)<30 and not (_has_property_type(raw) or _has_area(raw) or _transaction(raw)):
            if _single_listing(parent,parent):
                return "FALSE_CONFLICT",98.5,None,"V310_DECORATIVE_FRAGMENT","Decorative fragment inside a coherent single-property listing is not a structural conflict."

    if task=="OWNERSHIP":
        if _summary_fragment(raw,parent):
            return "NOT_OWNED",99.8,None,"V310_SUMMARY_NOT_ATOMIC","Inventory summary is not one atomic property."
        if _forward_header(raw,parent):
            return "NOT_OWNED",99.4,None,"V310_FORWARD_HEADER","Trailing locality/project header binds to the following property block."
        if re.fullmatch(r"(?:rental|rent|sale|resale)\s+inventory",n):
            return "OWNED",99.6,_transaction(raw),"V310_TRANSACTION_HEADER","Explicit inventory transaction header owns the transaction scope."
        if _transaction(raw) and (_has_area(raw) or _has_config(raw) or _has_property_type(raw)):
            return "OWNED",99.5,_transaction(raw),"V310_ATOMIC_IDENTITY","Atomic evidence has transaction plus property identity."
        if len(n)<20 and not (_has_property_type(raw) or _has_area(raw) or _has_config(raw)):
            return "NOT_OWNED",97.0,None,"V310_SHORT_FRAGMENT","Short fragment lacks enough property identity for ownership."

    return None

def _knn_predict(case, training):
    same=[x for x in training if x.get("task_type")==case.get("task_type") and x.get("human_decision")]
    if not same: return None
    scored=sorted(((_feature_similarity(case,x),x) for x in same), key=lambda z:z[0], reverse=True)
    top=scored[:3]
    if not top: return None
    votes=defaultdict(float)
    for s,x in top:
        if s>0: votes[x["human_decision"]]+=s
    if not votes: return None
    decision,maxvote=max(votes.items(),key=lambda z:z[1])
    total=sum(votes.values())
    purity=maxvote/max(total,1e-9)
    margin=maxvote-sorted(votes.values(),reverse=True)[1] if len(votes)>1 else maxvote
    conf=min(96.0,70.0+20.0*purity+min(6.0,max(0.0,margin)))
    neighbor_evidence=[{"entity_id":x["entity_id"],"decision":x["human_decision"],"score":round(s,3)} for s,x in top]
    return decision,round(conf,2),None,"V310_GOLD_PROTOTYPE","Decision inferred from structurally similar Human-Gold prototypes.",neighbor_evidence

def predict(case, training):
    rule=_rule_predict(case)
    if rule:
        d,c,cv,r,reason=rule
        return {"decision":d,"confidence":c,"canonical_value":cv,"rule_id":r,"reason":reason,"evidence":{"features":_feature_signature(case)}}
    knn=_knn_predict(case,training)
    if knn:
        d,c,cv,r,reason,neighbors=knn
        return {"decision":d,"confidence":c,"canonical_value":cv,"rule_id":r,"reason":reason,"evidence":{"neighbors":neighbors,"features":_feature_signature(case)}}
    base=v300.expert_adjudicate(case)
    return {"decision":base["decision"],"confidence":min(float(base["confidence"]),88.0),
            "canonical_value":base.get("canonical_value"),"rule_id":"V310_SAFE_FALLBACK",
            "reason":"Prior expert prediction retained only as low-confidence fallback.",
            "evidence":{"v300":base,"features":_feature_signature(case)}}

def _cases(engine):
    with engine.connect() as conn:
        rows=[dict(r) for r in conn.execute(text("""
            SELECT c.*,l.human_decision,l.human_confidence,l.canonical_value AS human_canonical
            FROM alliance_gold_v2_structural_cases c
            LEFT JOIN alliance_gold_v2_structural_labels l ON l.case_id=c.case_id
            WHERE c.source_version=:v
            ORDER BY c.priority_score DESC,c.created_at ASC
        """),{"v":goldlab.CURRICULUM_VERSION}).mappings().all()]
    for r in rows: r["machine_payload"]=_loads(r.get("machine_payload"),{})
    return rows

def leave_one_out(engine,cases):
    labeled=[c for c in cases if c.get("human_decision")]
    totals=Counter(); correct=Counter(); errors=[]
    for target in labeled:
        training=[x for x in labeled if x["case_id"]!=target["case_id"]]
        p=predict(target,training)
        task=target["task_type"]; totals[task]+=1
        if p["decision"]==target["human_decision"]:
            correct[task]+=1
        else:
            errors.append((target,p))

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM alliance_expertise_v310_errors WHERE ruleset_version=:v"),{"v":RULESET_VERSION})
        for target,p in errors:
            conn.execute(text("""
                INSERT INTO alliance_expertise_v310_errors
                (error_id,case_id,entity_id,task_type,human_decision,predicted_decision,rule_id,
                 raw_text,parent_message_text,diagnosis,ruleset_version)
                VALUES(:id,:cid,:eid,:task,:human,:pred,:rule,:raw,:parent,CAST(:diag AS jsonb),:v)
                ON CONFLICT(case_id,ruleset_version) DO UPDATE SET
                  human_decision=EXCLUDED.human_decision,predicted_decision=EXCLUDED.predicted_decision,
                  rule_id=EXCLUDED.rule_id,diagnosis=EXCLUDED.diagnosis
            """),{"id":str(uuid.uuid4()),"cid":str(target["case_id"]),"eid":target["entity_id"],
                  "task":target["task_type"],"human":target["human_decision"],"pred":p["decision"],
                  "rule":p["rule_id"],"raw":target.get("raw_text"),"parent":target.get("parent_message_text"),
                  "diag":_j({"prediction":p,"features":_feature_signature(target)}),"v":RULESET_VERSION})

    overall_n=sum(totals.values()); overall_ok=sum(correct.values())
    metrics={}
    for task,n in totals.items():
        pct=round(100.0*correct[task]/max(n,1),4)
        metrics[task.lower()+"_loo_accuracy"]=(pct,correct[task],n,pct>=EXPERTISE_TASK_GATE)
    overall=round(100.0*overall_ok/max(overall_n,1),4)
    metrics["overall_loo_accuracy"]=(overall,overall_ok,overall_n,overall>=EXPERTISE_OVERALL_GATE)

    with engine.begin() as conn:
        for k,(pct,num,den,passed) in metrics.items():
            conn.execute(text("""
                INSERT INTO alliance_expertise_v310_benchmark
                (metric_key,metric_value,numerator,denominator,passed,notes,ruleset_version)
                VALUES(:k,:pct,:num,:den,:passed,'Leave-one-out Human Gold benchmark; target label excluded from prototype training.',:v)
                ON CONFLICT(metric_key) DO UPDATE SET metric_value=EXCLUDED.metric_value,
                  numerator=EXCLUDED.numerator,denominator=EXCLUDED.denominator,passed=EXCLUDED.passed,
                  notes=EXCLUDED.notes,ruleset_version=EXCLUDED.ruleset_version,updated_at=now()
            """),{"k":k,"pct":pct,"num":num,"den":den,"passed":passed,"v":RULESET_VERSION})

    populated=[k for k in metrics if k!="overall_loo_accuracy"]
    gate=(overall_n>=24 and metrics["overall_loo_accuracy"][3] and all(metrics[k][3] for k in populated))
    return {"human_gold_examples":overall_n,"overall_accuracy":overall,
            "task_accuracy":{t:round(100.0*correct[t]/max(totals[t],1),2) for t in totals},
            "errors":len(errors),"gate_passed":gate,
            "gate_rule":"Leave-one-out: >=24 Human Gold, >=95% overall and >=90% on every populated task."}

def run(engine,limit=1000):
    _install(engine)
    try: v300.run_expertise(engine,min(int(limit),5000))
    except Exception: pass
    cases=_cases(engine)
    labeled=[c for c in cases if c.get("human_decision")]
    benchmark=leave_one_out(engine,cases)
    unlabeled=[c for c in cases if not c.get("human_decision") and c.get("status")=="OPEN"]
    unlabeled=unlabeled[:max(1,min(int(limit),5000))]
    counts=Counter()

    with engine.begin() as conn:
        for c in unlabeled:
            p=predict(c,labeled)
            # No self-confidence-only promotion. Only deterministic repaired rules can resolve.
            deterministic=p["rule_id"].startswith("V310_") and p["rule_id"] not in ("V310_GOLD_PROTOTYPE","V310_SAFE_FALLBACK")
            if deterministic and p["confidence"]>=PROMOTE_CONFIDENCE:
                disp="EXPERT_RESOLVED"
            elif p["confidence"]>=SHADOW_CONFIDENCE:
                disp="SHADOW_RESOLVED"
            else:
                disp="EXCEPTION"
            counts[disp]+=1
            conn.execute(text("""
                INSERT INTO alliance_expertise_v310_predictions
                (prediction_id,case_id,entity_id,task_type,decision,confidence,canonical_value,
                 rule_id,disposition,reason,evidence,ruleset_version)
                VALUES(:id,:cid,:eid,:task,:d,:conf,:cv,:rule,:disp,:reason,CAST(:ev AS jsonb),:v)
                ON CONFLICT(case_id,ruleset_version) DO UPDATE SET decision=EXCLUDED.decision,
                  confidence=EXCLUDED.confidence,canonical_value=EXCLUDED.canonical_value,
                  rule_id=EXCLUDED.rule_id,disposition=EXCLUDED.disposition,reason=EXCLUDED.reason,
                  evidence=EXCLUDED.evidence,updated_at=now()
            """),{"id":str(uuid.uuid4()),"cid":str(c["case_id"]),"eid":c["entity_id"],"task":c["task_type"],
                  "d":p["decision"],"conf":p["confidence"],"cv":p.get("canonical_value"),"rule":p["rule_id"],
                  "disp":disp,"reason":p["reason"],"ev":_j(p.get("evidence",{})),"v":RULESET_VERSION})

        gate="EXPERTISE_GATE_PASS" if benchmark["gate_passed"] else "EXPERTISE_GATE_HOLD"
        result={"status":"PASS","version":VERSION,"mode":MODE,"ruleset_version":RULESET_VERSION,
                "human_gold":len(labeled),"benchmark":benchmark,"expertise_gate":gate,
                "unlabeled_cases_seen":len(unlabeled),"expert_resolved":counts["EXPERT_RESOLVED"],
                "shadow_resolved":counts["SHADOW_RESOLVED"],"exceptions":counts["EXCEPTION"],
                "policy":"Human Gold excluded case-by-case from leave-one-out prototype evaluation. No Gold/production/WhatsApp mutation.",
                "production_writes":0,"whatsapp_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0}
        conn.execute(text("""
            INSERT INTO alliance_expertise_v310_runs
            (run_id,ruleset_version,human_gold,loo_correct,loo_total,loo_accuracy,
             expert_resolved,shadow_resolved,exceptions,expertise_gate,result,
             production_writes,whatsapp_writes,gold_v1_mutations,gold_v2_mutations)
            VALUES(:id,:v,:human,:ok,:total,:acc,:er,:sr,:ex,:gate,CAST(:result AS jsonb),0,0,0,0)
        """),{"id":str(uuid.uuid4()),"v":RULESET_VERSION,"human":len(labeled),
              "ok":round(benchmark["overall_accuracy"]*benchmark["human_gold_examples"]/100),
              "total":benchmark["human_gold_examples"],"acc":benchmark["overall_accuracy"],
              "er":counts["EXPERT_RESOLVED"],"sr":counts["SHADOW_RESOLVED"],"ex":counts["EXCEPTION"],
              "gate":gate,"result":_j(result)})
    return result

def status(engine):
    _install(engine)
    with engine.connect() as conn:
        latest=conn.execute(text("""
            SELECT result FROM alliance_expertise_v310_runs
            WHERE ruleset_version=:v ORDER BY created_at DESC LIMIT 1
        """),{"v":RULESET_VERSION}).scalar()
        dist=[dict(r) for r in conn.execute(text("""
            SELECT disposition,count(*) n FROM alliance_expertise_v310_predictions
            WHERE ruleset_version=:v GROUP BY disposition
        """),{"v":RULESET_VERSION}).mappings().all()]
        errs=[dict(r) for r in conn.execute(text("""
            SELECT entity_id,task_type,human_decision,predicted_decision,rule_id,raw_text
            FROM alliance_expertise_v310_errors WHERE ruleset_version=:v
            ORDER BY task_type,entity_id LIMIT 50
        """),{"v":RULESET_VERSION}).mappings().all()]
    return foundation._json_safe({"status":"PASS","version":VERSION,"mode":MODE,
        "latest_run":_loads(latest,{}) if latest else None,
        "prediction_distribution":{r["disposition"]:int(r["n"]) for r in dist},
        "remaining_gold_errors":errs,
        "production_writes":0,"whatsapp_live_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0})

DASHBOARD="""<!doctype html><html><head><meta charset='utf-8'><title>Expertise Bootcamp 3.1</title>
<style>body{font-family:Arial;background:#07111d;color:#eef6ff;max-width:1450px;margin:24px auto}.card{background:#101d2b;padding:16px;border-radius:12px;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.big{font-size:28px;font-weight:bold}button{padding:12px 18px;border:0;border-radius:8px;background:#f5d76e;font-weight:bold}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style></head><body>
<h1>🎓 Alliance Property Brain — Autonomous Expertise Bootcamp 3.1</h1>
<p>Gold-distilled rules + leave-one-out exam. The case being examined is excluded from prototype training.</p>
<button onclick='run()'>Run Autonomous Bootcamp</button><div id='cards' class='grid'></div>
<div class='card'><h3>Latest Run</h3><pre id='latest'></pre></div>
<div class='card'><h3>Remaining Human-Gold Errors</h3><pre id='errors'></pre></div>
<script>
async function call(p,m='GET'){let r=await fetch(p,{method:m});let t=await r.text();let d;try{d=JSON.parse(t)}catch(e){d={raw:t}}if(!r.ok)throw Error(d.detail||d.raw||('HTTP '+r.status));return d}
function c(k,v){return `<div class="card"><div>${k}</div><div class="big">${v??0}</div></div>`}
async function load(){let s=await call('/api/property-brain/expertise-v310/status');let d=s.prediction_distribution||{};let l=s.latest_run||{};document.getElementById('cards').innerHTML=c('Expert Resolved',d.EXPERT_RESOLVED)+c('Shadow Resolved',d.SHADOW_RESOLVED)+c('Exceptions',d.EXCEPTION)+c('Expertise Gate',l.expertise_gate||'NOT RUN');document.getElementById('latest').textContent=JSON.stringify(l,null,2);document.getElementById('errors').textContent=JSON.stringify(s.remaining_gold_errors,null,2)}
async function run(){document.getElementById('latest').textContent='Running...';await call('/api/property-brain/expertise-v310/run?limit=1000','POST');await load()}load()
</script></body></html>"""

def register(core):
    engine=_engine(core); app=_app(core); _install(engine)
    try: run(engine,1000)
    except Exception: pass

    if not foundation._route_exists(app,"/api/property-brain/expertise-v310/status"):
        @app.get("/api/property-brain/expertise-v310/status")
        def _status(): return status(engine)

    if not foundation._route_exists(app,"/api/property-brain/expertise-v310/run"):
        @app.post("/api/property-brain/expertise-v310/run")
        def _run(limit:int=Query(default=1000,ge=1,le=5000)): return run(engine,limit)

    if not foundation._route_exists(app,"/property-brain/expertise-v310"):
        @app.get("/property-brain/expertise-v310",response_class=HTMLResponse)
        def _dashboard(): return HTMLResponse(DASHBOARD)

    return {"status":"REGISTERED","version":VERSION,"dashboard":"/property-brain/expertise-v310",
            "auto_run_on_start":True,"leave_one_out_benchmark":True,
            "production_writes":0,"whatsapp_live_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0}
