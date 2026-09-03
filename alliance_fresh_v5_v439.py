from __future__ import annotations

import hashlib
import html
import inspect
import json
import uuid
from collections import defaultdict

from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_autonomous_student_v430 as v430
import alliance_autonomous_student_v438 as v438
import alliance_automation_truth_escalator_v421 as v421
import alliance_automation_closure_v422 as v422
import alliance_automation_grammar_rescue_v423 as v423
import alliance_acquisition_intent_closure_v425 as v425
import alliance_truth_integrity_v426 as v426

VERSION = "4.3.9-ALLIANCE-FRESH-V5-FROZEN-CERTIFICATION"
MODE = "LOCK_V438_FREEZE_FRESH_UNSEEN_V5_AUTOMATED_INDEPENDENT_TRUTH_NO_TUNING"
EXAM_VERSION = "BLIND_AUDIT_V5_439_2026_09_03"
TARGET = 20
OVERALL_PASS = 95.0
FIELD_PASS = 90.0

DDL = [
"""CREATE TABLE IF NOT EXISTS alliance_v5_439_manifest(
manifest_id UUID PRIMARY KEY,
exam_version TEXT NOT NULL UNIQUE,
target INTEGER NOT NULL,
predictor_version TEXT NOT NULL,
predictor_sha256 TEXT NOT NULL,
selection_policy TEXT NOT NULL,
case_manifest_hash TEXT NOT NULL,
status TEXT NOT NULL DEFAULT 'FROZEN',
frozen_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_v5_439_cases(
audit_id UUID PRIMARY KEY,
blind_id UUID NOT NULL UNIQUE,
exam_version TEXT NOT NULL,
ordinal INTEGER NOT NULL,
source_hash TEXT NOT NULL,
raw_text TEXT NOT NULL,
predicted_class TEXT NOT NULL,
predicted_transaction TEXT NOT NULL,
predicted_ownership TEXT NOT NULL,
prediction_confidence NUMERIC(6,2),
prediction_rule TEXT,
frozen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(exam_version,ordinal))""",

"""CREATE TABLE IF NOT EXISTS alliance_v5_439_truth(
truth_id UUID PRIMARY KEY,
audit_id UUID NOT NULL UNIQUE,
exam_version TEXT NOT NULL,
truth_class TEXT,
truth_transaction TEXT,
truth_ownership TEXT,
class_confidence NUMERIC(6,4),
transaction_confidence NUMERIC(6,4),
ownership_confidence NUMERIC(6,4),
consensus JSONB NOT NULL DEFAULT '{}'::jsonb,
status TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_v5_439_results(
result_id UUID PRIMARY KEY,
exam_version TEXT NOT NULL UNIQUE,
total_cases INTEGER NOT NULL,
auto_resolved INTEGER NOT NULL,
unresolved INTEGER NOT NULL,
comparable_fields INTEGER NOT NULL DEFAULT 0,
correct_fields INTEGER NOT NULL DEFAULT 0,
overall_accuracy NUMERIC(8,4),
class_accuracy NUMERIC(8,4),
transaction_accuracy NUMERIC(8,4),
ownership_accuracy NUMERIC(8,4),
case_accuracy NUMERIC(8,4),
certification_gate TEXT NOT NULL,
truth_hash TEXT,
result JSONB NOT NULL DEFAULT '{}'::jsonb,
created_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
]

def _engine(core): return foundation._engine_from_core(core)
def _app(core): return getattr(core, "app", None) or core
def _j(v): return json.dumps(foundation._json_safe(v), ensure_ascii=False)

def _install(engine):
    with engine.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))

def _table_exists(conn, table):
    return bool(conn.execute(text("""SELECT EXISTS(
      SELECT 1 FROM information_schema.tables
      WHERE table_schema=current_schema() AND table_name=:t)"""), {"t":table}).scalar())

def _column_exists(conn, table, col):
    return bool(conn.execute(text("""SELECT EXISTS(
      SELECT 1 FROM information_schema.columns
      WHERE table_schema=current_schema() AND table_name=:t AND column_name=:c)"""),
      {"t":table,"c":col}).scalar())

def _used_blind_ids(conn):
    used=set()

    # Historical certification / blind tables.
    fixed=[
        "alliance_mastery_v340_blind_audit_cases",
        "alliance_mastery_v360_exam_v2_cases",
        "alliance_mastery_v380_exam_v3_cases",
        "alliance_championship_v410_cases",
    ]
    for t in fixed:
        if _table_exists(conn,t) and _column_exists(conn,t,"blind_id"):
            try:
                used.update(str(x) for x in conn.execute(text(
                    f"SELECT blind_id FROM {t} WHERE blind_id IS NOT NULL"
                )).scalars().all())
            except Exception:
                pass

    # Exclude every prior/future V5 table that already contains a blind_id.
    tables=[str(x) for x in conn.execute(text("""
      SELECT table_name FROM information_schema.tables
      WHERE table_schema=current_schema() AND table_name LIKE 'alliance_v5%'
    """)).scalars().all()]
    for t in tables:
        if t=="alliance_v5_439_cases":
            continue
        if _column_exists(conn,t,"blind_id"):
            try:
                used.update(str(x) for x in conn.execute(text(
                    f"SELECT blind_id FROM {t} WHERE blind_id IS NOT NULL"
                )).scalars().all())
            except Exception:
                pass
    return used

def _candidate_pool(engine):
    with engine.connect() as conn:
        if not _table_exists(conn,"alliance_mastery_v330_blind_cases"):
            raise RuntimeError("Foundation 3.3 blind pool table is missing.")
        rows=[dict(r) for r in conn.execute(text("""
          SELECT blind_id,source_hash,raw_text,frozen_at,status
          FROM alliance_mastery_v330_blind_cases
          WHERE status='FROZEN'
          ORDER BY blind_id
        """)).mappings()]
        used=_used_blind_ids(conn)
    return [r for r in rows if str(r["blind_id"]) not in used]

def _risk_bucket(p, raw):
    n=(raw or "").lower()
    score=0
    if p["class"]=="INVENTORY_GROUP": score+=5
    if p["class"]=="REQUIREMENT": score+=4
    if p["class"] in {"NOISE","FRAGMENT","UNRESOLVED"}: score+=4
    if p["transaction"] in {"AMBIGUOUS","UNKNOWN"}: score+=4
    if "pre" in n and ("lease" in n or "rent" in n): score+=3
    if len(raw or "")>800: score+=3
    if any(x in n for x in ["looking for","client wants","getting vacated","available for lease","inventory","required","need urgently"]): score+=2
    return score

def _select(pool):
    if len(pool)<TARGET:
        raise RuntimeError(f"Only {len(pool)} untouched blind cases remain; need {TARGET}.")
    enriched=[]
    for r in pool:
        p=v438.predict_message(r["raw_text"])
        tie=int(hashlib.sha256((EXAM_VERSION+str(r["blind_id"])).encode()).hexdigest()[:12],16)
        enriched.append((r,p,_risk_bucket(p,r["raw_text"]),tie))

    groups={}
    for item in enriched:
        key=(item[1]["class"],item[1]["transaction"])
        groups.setdefault(key,[]).append(item)
    for vals in groups.values():
        vals.sort(key=lambda x:(-x[2],x[3]))

    selected=[]
    for key in sorted(groups):
        if groups[key] and len(selected)<TARGET:
            selected.append(groups[key].pop(0))
    remaining=[x for vals in groups.values() for x in vals]
    remaining.sort(key=lambda x:(-x[2],x[3]))
    for x in remaining:
        if len(selected)>=TARGET: break
        selected.append(x)

    selected.sort(key=lambda x:int(hashlib.sha256(
        ("V5_439_ORDER|"+str(x[0]["blind_id"])).encode()).hexdigest()[:12],16))
    return selected[:TARGET]

def _predictor_hash():
    try:
        payload=inspect.getsource(v438.predict_message)+inspect.getsource(v438.leading_demand_object)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    except Exception:
        return "UNAVAILABLE"

def freeze_v5(engine):
    _install(engine)

    train=v438.training_status(engine)
    if train.get("training_gate")!="V438_TRAINING_PASS_READY_FOR_FRESH_V5_FREEZER":
        return {"status":"BLOCKED","reason":"4.3.8 training gate is not PASS.","training":train}

    with engine.connect() as conn:
        existing=conn.execute(text(
            "SELECT COUNT(*) FROM alliance_v5_439_cases WHERE exam_version=:e"
        ),{"e":EXAM_VERSION}).scalar() or 0
        if existing:
            manifest=conn.execute(text("""
              SELECT predictor_version,predictor_sha256,case_manifest_hash,status,frozen_at
              FROM alliance_v5_439_manifest WHERE exam_version=:e
            """),{"e":EXAM_VERSION}).mappings().first()
            return {"status":"ALREADY_FROZEN","total":int(existing),
                    "manifest":dict(manifest) if manifest else None}

    selected=_select(_candidate_pool(engine))
    psha=_predictor_hash()
    payload=[{
        "blind_id":str(r["blind_id"]),
        "source_hash":r["source_hash"],
        "predicted_class":p["class"],
        "predicted_transaction":p["transaction"],
        "predicted_ownership":p["ownership"],
    } for r,p,_,_ in selected]
    mhash=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()

    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO alliance_v5_439_manifest
        (manifest_id,exam_version,target,predictor_version,predictor_sha256,selection_policy,case_manifest_hash,status)
        VALUES(:id,:e,:t,:pv,:ps,:policy,:mh,'FROZEN')"""),
        {"id":str(uuid.uuid4()),"e":EXAM_VERSION,"t":TARGET,"pv":v438.VERSION,"ps":psha,
         "policy":"Untouched Foundation 3.3 blind pool only. Excludes every prior V1/V2/V3/V4 and any previously materialized V5 blind_id. Diversity-first selection uses frozen 4.3.8 predictions only. No V5 truth is consulted.",
         "mh":mhash})

        for ordinal,(r,p,_,_) in enumerate(selected,1):
            conn.execute(text("""INSERT INTO alliance_v5_439_cases
            (audit_id,blind_id,exam_version,ordinal,source_hash,raw_text,predicted_class,predicted_transaction,
             predicted_ownership,prediction_confidence,prediction_rule)
            VALUES(:id,:bid,:e,:ord,:sh,:raw,:cl,:tx,:ow,:cf,:rule)"""),
            {"id":str(uuid.uuid4()),"bid":str(r["blind_id"]),"e":EXAM_VERSION,"ord":ordinal,
             "sh":r["source_hash"],"raw":r["raw_text"],"cl":p["class"],"tx":p["transaction"],
             "ow":p["ownership"],"cf":float(p["confidence"]),"rule":p.get("rule")})

    return {"status":"FROZEN","total":TARGET,"predictor_version":v438.VERSION,
            "predictor_sha256":psha,"case_manifest_hash":mhash}

def _truth_integrity_judge(raw):
    # Independent examiner-side ontology guard created before V5 freeze.
    base=v422.semantic_truth(raw)
    cls,tx,ow=float_or_none(base,0),None,None
    # semantic_truth tuple format is class, tx, ownership, confidence, evidence.
    c,t,o,cf,ev=base
    c2,t2,o2=c,t,o
    reasons=[]
    dc=v426.demand_contract(raw)
    rc=v426.rental_contract(raw)
    sc=v426.sale_contract(raw)
    if dc and c2=="PROPERTY_AVAILABILITY":
        c2="REQUIREMENT"; reasons.append("V426_DEMAND_TRUTH_CORRECTION")
    if dc and rc:
        t2="RENT"; reasons.append("V426_RENTAL_TRUTH_CORRECTION")
    if dc and sc:
        t2="SALE"; reasons.append("V426_SALE_TRUTH_CORRECTION")
    return (c2,t2,o2,max(float(cf),0.985),{"base":ev,"reasons":reasons,
        "demand_contract":dc,"rental_contract":rc,"sale_contract":sc})

def float_or_none(x, i):
    try: return x[i]
    except Exception: return None

def _judges(engine,raw):
    out={}
    for name,j in v421._judges(engine,raw).items():
        out[name]={
            "class":j[0],"transaction":j[1],"ownership":j[2],
            "class_confidence":float(j[3]),"transaction_confidence":float(j[4]),
            "ownership_confidence":float(j[5]),"evidence":j[6],
        }

    for name,fn in [
        ("G_V422_SEMANTIC",v422.semantic_truth),
        ("H_V423_DUAL_GRAMMAR",v423.rescue_truth),
        ("I_V425_DUAL_ACQUISITION",v425.acquisition_truth),
        ("J_V426_TRUTH_INTEGRITY",_truth_integrity_judge),
    ]:
        j=fn(raw)
        out[name]={
            "class":j[0],"transaction":j[1],"ownership":j[2],
            "class_confidence":float(j[3]),"transaction_confidence":float(j[3]),
            "ownership_confidence":float(j[3]),"evidence":j[4],
        }
    return out

def _resolve_field(judges,field):
    ck=f"{field}_confidence"
    votes=[]
    for name,j in judges.items():
        val=j.get(field); cf=float(j.get(ck) or 0)
        if val and cf>=0.95:
            votes.append((name,val,cf))
    if not votes:
        return {"status":"UNRESOLVED","reason":"NO_QUALIFIED_VOTES"}

    by=defaultdict(list)
    for name,val,cf in votes:
        by[val].append((name,cf))
    winner,wvotes=max(by.items(),key=lambda kv:(len(kv[1]),sum(x[1] for x in kv[1])))
    count=len(wvotes); avg=sum(x[1] for x in wvotes)/count
    dissent=[(n,v,c) for n,v,c in votes if v!=winner and c>=0.985]

    semantic_core={"A_EVIDENCE_CONTRACT","B_COUNTERFACTUAL_CRITIC","D_CRE_DECISION_GRAPH",
                   "F_INTENT_HIERARCHY","G_V422_SEMANTIC","J_V426_TRUTH_INTEGRITY"}
    core=sum(1 for n,c in wvotes if n in semantic_core)

    accepted=(
        (count>=4 and avg>=0.96 and len(dissent)<=1)
        or (count>=3 and avg>=0.975 and core>=2 and not dissent)
    )
    if not accepted:
        return {"status":"UNRESOLVED","majority":winner,"count":count,
                "avg_confidence":round(avg,4),"strong_dissent":dissent,
                "votes":[{"judge":n,"value":v,"confidence":c} for n,v,c in votes]}
    return {"status":"RESOLVED","value":winner,"confidence":round(avg,4),
            "votes":[{"judge":n,"value":v,"confidence":c} for n,v,c in votes],
            "dissent":[{"judge":n,"value":v,"confidence":c} for n,v,c in votes if v!=winner]}

def adjudicate(engine):
    fr=freeze_v5(engine)
    if fr.get("status")=="BLOCKED":
        return {"status":"BLOCKED","freeze":fr}

    with engine.connect() as conn:
        rows=[dict(r) for r in conn.execute(text(
            "SELECT * FROM alliance_v5_439_cases WHERE exam_version=:e ORDER BY ordinal"
        ),{"e":EXAM_VERSION}).mappings()]

    for r in rows:
        with engine.connect() as conn:
            exists=conn.execute(text(
                "SELECT 1 FROM alliance_v5_439_truth WHERE audit_id=:id"
            ),{"id":str(r["audit_id"])}).scalar()
        if exists:
            continue

        judges=_judges(engine,r["raw_text"])
        fields={f:_resolve_field(judges,f) for f in ("class","transaction","ownership")}
        ok=all(v.get("status")=="RESOLVED" for v in fields.values())

        with engine.begin() as conn:
            conn.execute(text("""INSERT INTO alliance_v5_439_truth
            (truth_id,audit_id,exam_version,truth_class,truth_transaction,truth_ownership,
             class_confidence,transaction_confidence,ownership_confidence,consensus,status)
            VALUES(:id,:aid,:e,:c,:tx,:o,:cc,:tc,:oc,CAST(:cons AS JSONB),:st)
            ON CONFLICT(audit_id) DO NOTHING"""),
            {"id":str(uuid.uuid4()),"aid":str(r["audit_id"]),"e":EXAM_VERSION,
             "c":fields["class"].get("value") if ok else None,
             "tx":fields["transaction"].get("value") if ok else None,
             "o":fields["ownership"].get("value") if ok else None,
             "cc":fields["class"].get("confidence",0),
             "tc":fields["transaction"].get("confidence",0),
             "oc":fields["ownership"].get("confidence",0),
             "cons":_j({"judges":judges,"fields":fields}),
             "st":"AUTO_RESOLVED" if ok else "UNRESOLVED"})

    return score(engine)

def score(engine):
    with engine.connect() as conn:
        rows=[dict(r) for r in conn.execute(text("""
          SELECT c.*,t.truth_class,t.truth_transaction,t.truth_ownership,t.status truth_status,t.consensus
          FROM alliance_v5_439_cases c
          LEFT JOIN alliance_v5_439_truth t ON t.audit_id=c.audit_id
          WHERE c.exam_version=:e ORDER BY c.ordinal
        """),{"e":EXAM_VERSION}).mappings()]

    total=len(rows)
    resolved=[r for r in rows if r.get("truth_status")=="AUTO_RESOLVED"]
    unresolved=total-len(resolved)

    if total==0:
        return {"status":"NOT_FROZEN","total":0}
    if unresolved:
        return {"status":"V5_AUTOMATED_TRUTH_INCOMPLETE_EXCEPTION_ONLY","total":total,
                "auto_resolved":len(resolved),"remaining":unresolved,
                "manual_work_required":0,
                "policy":"Frozen student predictions remain immutable. Unresolved examiner cases are not silently dropped.",
                "safety":{"student_tuning":0,"production_writes":0,"whatsapp_writes":0,"gold_mutations":0}}

    fstats={f:[0,0] for f in ("class","transaction","ownership")}
    errors=[]; caseok=0
    for r in resolved:
        exp={"class":r["truth_class"],"transaction":r["truth_transaction"],"ownership":r["truth_ownership"]}
        pred={"class":r["predicted_class"],"transaction":r["predicted_transaction"],"ownership":r["predicted_ownership"]}
        ok=True
        for f in fstats:
            fstats[f][1]+=1
            if exp[f]==pred[f]:
                fstats[f][0]+=1
            else:
                ok=False
                errors.append({"ordinal":r["ordinal"],"field":f,"truth":exp[f],"student":pred[f]})
        caseok+=int(ok)

    comparable=sum(v[1] for v in fstats.values())
    correct=sum(v[0] for v in fstats.values())
    overall=round(100*correct/comparable,4)
    fields={k:round(100*v[0]/v[1],4) for k,v in fstats.items()}
    caseacc=round(100*caseok/total,4)
    passed=overall>=OVERALL_PASS and all(v>=FIELD_PASS for v in fields.values())
    gate="AUTOMATED_INDEPENDENT_V5_PASS" if passed else "AUTOMATED_INDEPENDENT_V5_HOLD"

    truth_payload=[(r["ordinal"],r["truth_class"],r["truth_transaction"],r["truth_ownership"]) for r in resolved]
    th=hashlib.sha256(json.dumps(truth_payload,separators=(",",":")).encode()).hexdigest()
    result={"version":VERSION,"exam_version":EXAM_VERSION,"status":"COMPLETE",
            "total":total,"auto_resolved":total,"remaining":0,"manual_work_required":0,
            "correct_fields":correct,"comparable_fields":comparable,"accuracy":overall,
            "field_accuracy":fields,"case_accuracy":caseacc,"errors":errors,
            "expertise_gate":gate,
            "policy":"Student 4.3.8 predictions were frozen before V5 truth. Student is excluded from truth. Result is immutable once stored.",
            "safety":{"student_tuning":0,"production_writes":0,"whatsapp_writes":0,"gold_mutations":0}}

    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO alliance_v5_439_results
        (result_id,exam_version,total_cases,auto_resolved,unresolved,comparable_fields,correct_fields,
         overall_accuracy,class_accuracy,transaction_accuracy,ownership_accuracy,case_accuracy,
         certification_gate,truth_hash,result)
        VALUES(:id,:e,:t,:ar,0,:cmp,:cor,:oa,:ca,:ta,:ow,:casea,:gate,:th,CAST(:res AS JSONB))
        ON CONFLICT(exam_version) DO NOTHING"""),
        {"id":str(uuid.uuid4()),"e":EXAM_VERSION,"t":total,"ar":total,"cmp":comparable,"cor":correct,
         "oa":overall,"ca":fields["class"],"ta":fields["transaction"],"ow":fields["ownership"],
         "casea":caseacc,"gate":gate,"th":th,"res":_j(result)})

    with engine.connect() as conn:
        stored=conn.execute(text(
            "SELECT result FROM alliance_v5_439_results WHERE exam_version=:e"
        ),{"e":EXAM_VERSION}).scalar()
    return stored or result

def machine_report(engine):
    train=v438.training_status(engine)
    fr=freeze_v5(engine)
    result=adjudicate(engine) if fr.get("status")!="BLOCKED" else {"status":"BLOCKED"}
    return {"version":VERSION,"student_version":v438.VERSION,
            "training_gate":train.get("training_gate"),"freeze":fr,"v5":result,
            "scientific_policy":"4.3.8 is locked before truth. V5 is fresh and unseen. Examiner stack excludes 4.3.8 and uses only pre-freeze independent judges plus pre-freeze truth-integrity guard.",
            "safety":{"student_tuning":0,"production_writes":0,"whatsapp_writes":0,"gold_mutations":0}}

def _dashboard(engine):
    s=machine_report(engine)
    gate=(s.get("v5") or {}).get("expertise_gate") or (s.get("v5") or {}).get("status")
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Alliance Fresh V5 4.3.9</title>
<style>body{{font-family:Arial;margin:30px;background:#f6f7fb;color:#172033;max-width:1250px}}pre{{white-space:pre-wrap;background:#101624;color:#eef3ff;padding:18px;border-radius:10px}}.gate{{padding:16px;background:#fff4cf;border-radius:10px;font-weight:700}}</style></head><body>
<h1>Alliance Fresh V5 — Frozen Certification 4.3.9</h1>
<div class='gate'>{html.escape(str(gate or "RUNNING"))}</div>
<p>Student 4.3.8 is locked. Fresh V5 predictions are immutable before independent automated truth.</p>
<pre>{html.escape(json.dumps(foundation._json_safe(s),ensure_ascii=False,indent=2))}</pre></body></html>"""

def register(core):
    engine=_engine(core); app=_app(core); _install(engine)
    freeze_v5(engine)

    if not foundation._route_exists(app,"/api/property-brain/fresh-v5-v439/status"):
        @app.get("/api/property-brain/fresh-v5-v439/status")
        def status_v439():
            return machine_report(engine)

    if not foundation._route_exists(app,"/property-brain/fresh-v5-v439"):
        @app.get("/property-brain/fresh-v5-v439",response_class=HTMLResponse)
        def page_v439():
            return HTMLResponse(_dashboard(engine))

    return {"status":"REGISTERED","version":VERSION,"route":"/property-brain/fresh-v5-v439",
            "student_version":v438.VERSION,
            "production_writes":0,"whatsapp_writes":0,"gold_mutations":0}
