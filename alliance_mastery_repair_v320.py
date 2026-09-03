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
import alliance_expertise_bootcamp_v310 as v310

VERSION = "3.2.0-MASTERY-REPAIR"
MODE = "UNICODE_TRACE_BOUNDARY_SEMANTICS_MULTI_INVENTORY_REPAIR_BLIND_HOLDOUT_REQUIRED"
ENGINE_VERSION = "ALLIANCE_MASTERY_REPAIR_V320"
RULESET_VERSION = "MASTERY_REPAIR_2026_09_03_V1"

DDL = [
"""CREATE TABLE IF NOT EXISTS alliance_mastery_v320_predictions(
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

"""CREATE TABLE IF NOT EXISTS alliance_mastery_v320_benchmark(
metric_key TEXT PRIMARY KEY,
metric_value NUMERIC(10,4),
numerator INTEGER NOT NULL DEFAULT 0,
denominator INTEGER NOT NULL DEFAULT 0,
passed BOOLEAN NOT NULL DEFAULT FALSE,
benchmark_kind TEXT NOT NULL,
notes TEXT,
ruleset_version TEXT NOT NULL,
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_mastery_v320_runs(
run_id UUID PRIMARY KEY,
ruleset_version TEXT NOT NULL,
training_accuracy NUMERIC(8,4) NOT NULL,
training_errors INTEGER NOT NULL,
unlabeled_seen INTEGER NOT NULL,
expert_resolved INTEGER NOT NULL,
shadow_resolved INTEGER NOT NULL,
exceptions INTEGER NOT NULL,
training_mastery_gate TEXT NOT NULL,
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

def _j(v): return json.dumps(foundation._json_safe(v),ensure_ascii=False)

def _ascii_fold(s):
    # Critical: mathematical bold/styled Unicode such as 𝐆.𝐤-𝟏 -> G.k-1.
    s = unicodedata.normalize("NFKC", s or "")
    return s

def _norm(s):
    s=_ascii_fold(s).lower().replace("–","-").replace("—","-")
    s=re.sub(r"[^a-z0-9]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def _lines(s): return [x.strip() for x in _ascii_fold(s).splitlines() if x.strip()]

def _tx(s):
    n=_norm(s)
    if re.search(r"\b(?:rent|rental|lease|leasing|to let)\b",n): return "RENT"
    if re.search(r"\b(?:sale|sell|selling|resale|for sale)\b",n): return "SALE"
    return None

def _has_identity(s):
    n=_norm(s)
    return bool(
        re.search(r"\b\d+\s*bhk\b",n) or
        re.search(r"\b(?:plot|apartment|builder floor|kothi|shop|office|land|villa|farmhouse|warehouse|showroom)\b",n) or
        re.search(r"\b\d+(?:\.\d+)?\s*(?:gaj|syds?|sq\s*yds?|sqft|sq\s*ft|sqm|sq\s*m|acre|bigha)\b",n)
    )

def _explicit_locality(s):
    n=_norm(s)
    pats=[
        r"\b(?:sushant|shushant)\s*lok\s*\d+\b",
        r"\bdlf\s*phase\s*\d+\b",
        r"\bgreater kailash\s*(?:1|2|i|ii)\b",
        r"\bg\s*k\s*[12]\b",
        r"\bsector\s*\d+\b",
    ]
    return next((m.group(0) for p in pats if (m:=re.search(p,n))),None)

def _inventory_code_count(s):
    # Big-ticket CRE inventory often uses C-214, C-211 etc.
    return len(set(re.findall(r"(?im)\b[A-Z]{1,3}\s*-\s*\d{2,5}\b",_ascii_fold(s))))

def _repeated_rent_price_blocks(s):
    rent=len(re.findall(r"(?im)^\s*RENT\s*:",_ascii_fold(s)))
    price=len(re.findall(r"(?im)^\s*PRICE\s*:",_ascii_fold(s)))
    return min(rent,price)

def _multi_city_subjects(s):
    n=_norm(s)
    # Subject cities/localities only; ignore highway/airport style references line by line.
    hits=set()
    for line in _lines(s):
        ln=_norm(line)
        if re.search(r"\b(?:airport|highway|railway|station|km|away|connectivity)\b",ln): continue
        for city in ("noida","gurugram","gurgaon","new delhi","south delhi","delhi","jaipur","goa","mohali"):
            if re.search(r"\b"+re.escape(city)+r"\b",ln): hits.add(city)
    return hits

def _multi_inventory(s):
    return _inventory_code_count(s)>=2 or _repeated_rent_price_blocks(s)>=2

def _source_trace(case):
    raw=case.get("raw_text") or ""
    cand=case.get("candidate_value") or ""
    nr,nc=_norm(raw),_norm(cand)
    if nc and nc in nr:
        return "SOURCE_SUPPORTED",99.9,cand,"V320_UNICODE_LITERAL_TRACE","Unicode-normalized atomic source directly supports the candidate."
    aliases={
      "greater kailash 1":[r"\bg\s*k\s*1\b",r"\bgk\s*1\b",r"\bgreater kailash\s*(?:1|i)\b"],
      "greater kailash 2":[r"\bg\s*k\s*2\b",r"\bgk\s*2\b",r"\bgreater kailash\s*(?:2|ii)\b"],
      "sushant lok 1":[r"\b(?:sushant|shushant)\s*lok\s*1\b"],
    }
    if nc in aliases and any(re.search(p,nr) for p in aliases[nc]):
        return "SOURCE_SUPPORTED",99.9,cand,"V320_UNICODE_ALIAS_TRACE","Unicode-normalized alias in atomic evidence supports the canonical candidate."
    return None

def _structural(case):
    raw=case.get("raw_text") or ""
    parent=case.get("parent_message_text") or ""
    source=raw if len(_norm(raw))>len(_norm(parent))*0.65 else parent

    # MUST run before single-listing coherence.
    if _multi_inventory(source):
        return "TRUE_CONFLICT",99.9,None,"V320_MULTI_INVENTORY_CODES","Multiple inventory codes / repeated RENT+PRICE blocks prove a multi-property parent requiring atomic splitting."

    cities=_multi_city_subjects(source)
    if len(cities)>=2 and _multi_inventory(source):
        return "TRUE_CONFLICT",99.9,None,"V320_MULTI_CITY_INVENTORY","Multiple subject cities occur across a multi-property commercial inventory."

    # Single coherent listing with reference places.
    n=_norm(raw)
    ref_lines=[x for x in _lines(raw) if re.search(r"\b(?:airport|highway|railway|station|km|away|connectivity)\b",_norm(x))]
    property_lines=[x for x in _lines(raw) if not re.search(r"\b(?:airport|highway|railway|station|km|away|connectivity)\b",_norm(x))]
    if len(n)>180 and _has_identity(raw) and (_tx(raw) or re.search(r"\bprice\b",n)):
        subject_cities=_multi_city_subjects("\n".join(property_lines))
        if len(subject_cities)<=1 and ref_lines:
            return "FALSE_CONFLICT",99.3,None,"V320_REFERENCE_LOCATION_ONLY","Other place names occur only in connectivity/reference lines; the property listing itself has one location context."
    return None

def _ownership(case):
    raw=case.get("raw_text") or ""
    parent=case.get("parent_message_text") or ""
    n=_norm(raw)
    loc=_explicit_locality(raw)
    has_id=_has_identity(raw)
    tx=_tx(raw)

    # Broken size/config fragment: no numeric size before SYDS.
    if re.fullmatch(r"syds?\s+\d+\s*bhk\s*(?:ser|servant)?",n):
        return "AMBIGUOUS",99.0,None,"V320_MISSING_AREA_PREFIX","Configuration fragment is missing the area number before SYDS; ownership cannot be reconstructed safely."

    # Rent-looking numeric prefix with furnishing only is an orphan/truncated fragment.
    if re.fullmatch(r"\d+(?:\.\d+)?\s*lac\s*(?:maint|maintenance)?\s+fully furnished",n):
        # Gold demonstrates these fragments are not consistently reconstructable.
        # The safe canonical action is abstention, not invention.
        return "AMBIGUOUS",99.0,None,"V320_TRUNCATED_RENT_FRAGMENT","Rent/furnishing fragment lacks unique property identity; do not guess missing leading digits or sibling ownership."

    # Explicit locality plus substantive property config is owned, even if locality is last line.
    if loc and (has_id or re.search(r"\b\d+(?:/\d+)+\s*bhk\b",n)):
        return "OWNED",99.7,loc.title(),"V320_LOCALITY_WITH_IDENTITY","Explicit locality is part of the same atomic record because the span also contains substantive configuration/property identity."

    # Explicit locality + rent line but no identity: Human Gold showed this can still be a scoped owned atom.
    if loc and re.search(r"\b\d+(?:\.\d+)?\s*lac\b",n) and len(_lines(raw))<=3:
        return "OWNED",99.2,loc.title(),"V320_LOCALITY_SCOPED_RENT_ATOM","Compact atom contains explicit locality and rent; locality belongs to this record rather than a following record."

    # Avoid the over-broad resale summary rule when configuration is present.
    if "new floors in resale" in _norm(parent) and (raw or "").count("/")>=2 and not has_id:
        return "NOT_OWNED",99.5,None,"V320_TRUE_SUMMARY_FRAGMENT","Slash-separated resale-size summary without property identity is not an atomic property."

    if tx and has_id:
        return "OWNED",99.6,tx,"V320_ATOMIC_TX_IDENTITY","Atomic record contains transaction plus property identity."
    return None

def predict(case):
    task=case.get("task_type")
    r=None
    if task=="SOURCE_TRACEABILITY": r=_source_trace(case)
    elif task=="STRUCTURAL_CONFLICT": r=_structural(case)
    elif task=="OWNERSHIP": r=_ownership(case)
    if r:
        d,c,cv,rule,reason=r
        return {"decision":d,"confidence":c,"canonical_value":cv,"rule_id":rule,"reason":reason}
    base=v310.predict(case,[])
    return {"decision":base["decision"],"confidence":min(float(base["confidence"]),89.0),
            "canonical_value":base.get("canonical_value"),"rule_id":"V320_SAFE_FALLBACK",
            "reason":"No repaired deterministic rule applies; prior result is retained below promotion threshold."}

def _cases(engine):
    with engine.connect() as conn:
        rows=[dict(r) for r in conn.execute(text("""
          SELECT c.*,l.human_decision,l.human_confidence,l.canonical_value AS human_canonical
          FROM alliance_gold_v2_structural_cases c
          LEFT JOIN alliance_gold_v2_structural_labels l ON l.case_id=c.case_id
          WHERE c.source_version=:v ORDER BY c.priority_score DESC,c.created_at ASC
        """),{"v":goldlab.CURRICULUM_VERSION}).mappings().all()]
    for r in rows:r["machine_payload"]=_loads(r.get("machine_payload"),{})
    return rows

def training_benchmark(engine,cases):
    labeled=[c for c in cases if c.get("human_decision")]
    totals=Counter(); correct=Counter(); errors=[]
    for c in labeled:
        p=predict(c); totals[c["task_type"]]+=1
        if p["decision"]==c["human_decision"]: correct[c["task_type"]]+=1
        else: errors.append({"entity_id":c["entity_id"],"task_type":c["task_type"],
                             "human":c["human_decision"],"predicted":p["decision"],
                             "rule_id":p["rule_id"],"raw_text":c.get("raw_text")})
    total=sum(totals.values()); ok=sum(correct.values()); acc=round(100*ok/max(total,1),4)
    task={t:round(100*correct[t]/max(totals[t],1),2) for t in totals}
    train_gate=total>=24 and acc>=95 and all(v>=90 for v in task.values())
    with engine.begin() as conn:
        metrics={"training_overall_accuracy":(acc,ok,total,train_gate)}
        for t in totals:
            pct=task[t]; metrics[t.lower()+"_training_accuracy"]=(pct,correct[t],totals[t],pct>=90)
        for k,(pct,num,den,passed) in metrics.items():
            conn.execute(text("""
              INSERT INTO alliance_mastery_v320_benchmark
              (metric_key,metric_value,numerator,denominator,passed,benchmark_kind,notes,ruleset_version)
              VALUES(:k,:pct,:num,:den,:passed,'TRAINING_GOLD','Used for rule repair only; not an independent expertise claim.',:v)
              ON CONFLICT(metric_key) DO UPDATE SET metric_value=EXCLUDED.metric_value,numerator=EXCLUDED.numerator,
                denominator=EXCLUDED.denominator,passed=EXCLUDED.passed,benchmark_kind=EXCLUDED.benchmark_kind,
                notes=EXCLUDED.notes,ruleset_version=EXCLUDED.ruleset_version,updated_at=now()
            """),{"k":k,"pct":pct,"num":num,"den":den,"passed":passed,"v":RULESET_VERSION})
    return {"examples":total,"accuracy":acc,"task_accuracy":task,"errors":errors,"training_mastery_gate":train_gate}

def run(engine,limit=1000):
    _install(engine)
    cases=_cases(engine)
    bench=training_benchmark(engine,cases)
    unlabeled=[c for c in cases if not c.get("human_decision") and c.get("status")=="OPEN"]
    unlabeled=unlabeled[:max(1,min(int(limit),5000))]
    counts=Counter()
    with engine.begin() as conn:
        for c in unlabeled:
            p=predict(c)
            deterministic=p["rule_id"].startswith("V320_") and p["rule_id"]!="V320_SAFE_FALLBACK"
            if deterministic and p["confidence"]>=98: disp="EXPERT_RESOLVED"
            elif p["confidence"]>=92: disp="SHADOW_RESOLVED"
            else: disp="EXCEPTION"
            counts[disp]+=1
            conn.execute(text("""
              INSERT INTO alliance_mastery_v320_predictions
              (prediction_id,case_id,entity_id,task_type,decision,confidence,canonical_value,
               rule_id,disposition,reason,evidence,ruleset_version)
              VALUES(:id,:cid,:eid,:task,:d,:conf,:cv,:rule,:disp,:reason,CAST(:ev AS jsonb),:v)
              ON CONFLICT(case_id,ruleset_version) DO UPDATE SET decision=EXCLUDED.decision,
                confidence=EXCLUDED.confidence,canonical_value=EXCLUDED.canonical_value,
                rule_id=EXCLUDED.rule_id,disposition=EXCLUDED.disposition,reason=EXCLUDED.reason,
                evidence=EXCLUDED.evidence,updated_at=now()
            """),{"id":str(uuid.uuid4()),"cid":str(c["case_id"]),"eid":c["entity_id"],"task":c["task_type"],
                  "d":p["decision"],"conf":p["confidence"],"cv":p.get("canonical_value"),
                  "rule":p["rule_id"],"disp":disp,"reason":p["reason"],
                  "ev":_j({"raw_text":c.get("raw_text"),"parent_message_text":c.get("parent_message_text")}),
                  "v":RULESET_VERSION})

        training_gate="TRAINING_MASTERY_PASS" if bench["training_mastery_gate"] else "TRAINING_MASTERY_HOLD"
        # Expertise intentionally remains held until a fresh blind set exists.
        expertise_gate="EXPERTISE_GATE_REQUIRES_BLIND_HOLDOUT"
        result={"status":"PASS","version":VERSION,"mode":MODE,"ruleset_version":RULESET_VERSION,
                "training_benchmark":bench,"training_mastery_gate":training_gate,
                "expertise_gate":expertise_gate,"human_gold":bench["examples"],
                "unlabeled_cases_seen":len(unlabeled),"expert_resolved":counts["EXPERT_RESOLVED"],
                "shadow_resolved":counts["SHADOW_RESOLVED"],"exceptions":counts["EXCEPTION"],
                "next_required_exam":"Fresh automatically sampled blind holdout from unseen WhatsApp/Silver data. Do not train on it before scoring.",
                "production_writes":0,"whatsapp_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0}
        conn.execute(text("""
          INSERT INTO alliance_mastery_v320_runs
          (run_id,ruleset_version,training_accuracy,training_errors,unlabeled_seen,
           expert_resolved,shadow_resolved,exceptions,training_mastery_gate,expertise_gate,result,
           production_writes,whatsapp_writes,gold_v1_mutations,gold_v2_mutations)
          VALUES(:id,:v,:acc,:err,:seen,:er,:sr,:ex,:tg,:eg,CAST(:result AS jsonb),0,0,0,0)
        """),{"id":str(uuid.uuid4()),"v":RULESET_VERSION,"acc":bench["accuracy"],"err":len(bench["errors"]),
              "seen":len(unlabeled),"er":counts["EXPERT_RESOLVED"],"sr":counts["SHADOW_RESOLVED"],
              "ex":counts["EXCEPTION"],"tg":training_gate,"eg":expertise_gate,"result":_j(result)})
    return result

def status(engine):
    _install(engine)
    with engine.connect() as conn:
        latest=conn.execute(text("""
          SELECT result FROM alliance_mastery_v320_runs WHERE ruleset_version=:v
          ORDER BY created_at DESC LIMIT 1
        """),{"v":RULESET_VERSION}).scalar()
        dist=[dict(r) for r in conn.execute(text("""
          SELECT disposition,count(*) n FROM alliance_mastery_v320_predictions
          WHERE ruleset_version=:v GROUP BY disposition
        """),{"v":RULESET_VERSION}).mappings().all()]
    return foundation._json_safe({"status":"PASS","version":VERSION,
      "latest_run":_loads(latest,{}) if latest else None,
      "prediction_distribution":{r["disposition"]:int(r["n"]) for r in dist},
      "production_writes":0,"whatsapp_live_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0})

DASHBOARD="""<!doctype html><html><head><meta charset='utf-8'><title>Mastery Repair 3.2</title>
<style>body{font-family:Arial;background:#07111d;color:#eef6ff;max-width:1450px;margin:24px auto}.card{background:#101d2b;padding:16px;border-radius:12px;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.big{font-size:26px;font-weight:bold}button{padding:12px 18px;border:0;border-radius:8px;background:#f5d76e;font-weight:bold}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style></head><body>
<h1>🏆 Alliance Property Brain — Mastery Repair 3.2</h1>
<p>Training mastery and real expertise are separate. A fresh blind holdout is required before expertise can pass.</p>
<button onclick='run()'>Run Mastery Repair</button><div id='cards' class='grid'></div>
<div class='card'><h3>Latest Run</h3><pre id='latest'></pre></div>
<script>
async function call(p,m='GET'){let r=await fetch(p,{method:m});let t=await r.text();let d;try{d=JSON.parse(t)}catch(e){d={raw:t}}if(!r.ok)throw Error(d.detail||d.raw||('HTTP '+r.status));return d}
function c(k,v){return `<div class="card"><div>${k}</div><div class="big">${v??0}</div></div>`}
async function load(){let s=await call('/api/property-brain/mastery-v320/status');let d=s.prediction_distribution||{};let l=s.latest_run||{};document.getElementById('cards').innerHTML=c('Expert Resolved',d.EXPERT_RESOLVED)+c('Shadow Resolved',d.SHADOW_RESOLVED)+c('Exceptions',d.EXCEPTION)+c('Expertise Gate',l.expertise_gate||'NOT RUN');document.getElementById('latest').textContent=JSON.stringify(l,null,2)}
async function run(){document.getElementById('latest').textContent='Running...';await call('/api/property-brain/mastery-v320/run?limit=1000','POST');await load()}load()
</script></body></html>"""

def register(core):
    engine=_engine(core);app=_app(core);_install(engine)
    try:run(engine,1000)
    except Exception:pass
    if not foundation._route_exists(app,"/api/property-brain/mastery-v320/status"):
        @app.get("/api/property-brain/mastery-v320/status")
        def _status():return status(engine)
    if not foundation._route_exists(app,"/api/property-brain/mastery-v320/run"):
        @app.post("/api/property-brain/mastery-v320/run")
        def _run(limit:int=Query(default=1000,ge=1,le=5000)):return run(engine,limit)
    if not foundation._route_exists(app,"/property-brain/mastery-v320"):
        @app.get("/property-brain/mastery-v320",response_class=HTMLResponse)
        def _dash():return HTMLResponse(DASHBOARD)
    return {"status":"REGISTERED","version":VERSION,"dashboard":"/property-brain/mastery-v320",
            "auto_run_on_start":True,"production_writes":0,"whatsapp_live_writes":0,
            "gold_v1_mutations":0,"gold_v2_mutations":0}
