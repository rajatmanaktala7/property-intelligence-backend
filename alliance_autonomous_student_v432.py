from __future__ import annotations

import hashlib
import html
import inspect
import json
import re
import uuid
from collections import defaultdict

from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_cre_academy_v400 as v400
import alliance_cre_academy_v401 as v401
import alliance_cre_academy_v402 as v402
import alliance_cre_championship_v410 as v410
import alliance_automation_truth_escalator_v421 as v421
import alliance_automation_closure_v422 as v422
import alliance_automation_grammar_rescue_v423 as v423
import alliance_acquisition_intent_closure_v425 as v425
import alliance_autonomous_student_v430 as v430
import alliance_autonomous_student_v431 as v431

VERSION = "4.3.2-ALLIANCE-AUTONOMOUS-STUDENT-V5-FINAL-TRAINING-REPAIR"
MODE = "CUMULATIVE_MIXED_PARENT_PRECEDENCE_AND_CLAUSE_RELATION_REPAIR_THEN_FRESH_V5"
RULESET_VERSION = "CRE_STUDENT_2026_09_03_V432"
V4_EXAM_VERSION = v410.EXAM_VERSION
V5_EXAM_VERSION = "BLIND_AUDIT_V5_432_2026_09_03"
V5_TARGET = 20
OVERALL_PASS = 95.0
FIELD_PASS = 90.0

DDL = [
"""CREATE TABLE IF NOT EXISTS alliance_student_v432_runs(
run_id UUID PRIMARY KEY,
ruleset_version TEXT NOT NULL,
v4_training_accuracy NUMERIC(8,4),
v4_class_accuracy NUMERIC(8,4),
v4_transaction_accuracy NUMERIC(8,4),
v4_ownership_accuracy NUMERIC(8,4),
legacy_regression_accuracy NUMERIC(8,4),
lesson_regression_accuracy NUMERIC(8,4),
training_gate TEXT NOT NULL,
result JSONB NOT NULL DEFAULT '{}'::jsonb,
created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_v5_432_manifest(
manifest_id UUID PRIMARY KEY,
exam_version TEXT NOT NULL UNIQUE,
target INTEGER NOT NULL,
predictor_version TEXT NOT NULL,
predictor_sha256 TEXT NOT NULL,
selection_policy TEXT NOT NULL,
case_manifest_hash TEXT NOT NULL,
status TEXT NOT NULL DEFAULT 'FROZEN',
frozen_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_v5_432_cases(
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

"""CREATE TABLE IF NOT EXISTS alliance_v5_432_truth(
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

"""CREATE TABLE IF NOT EXISTS alliance_v5_432_results(
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
def _app(core): return getattr(core,"app",None) or core
def _j(v): return json.dumps(foundation._json_safe(v),ensure_ascii=False)
def _norm(raw): return v400._norm(raw or "")

def _install(engine):
    with engine.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))

def _clause_relation(raw):
    # Preserve punctuation/newlines for relation scoping. The older 100-character
    # window could connect unrelated "requirement" and "rent/lease" mentions.
    src=(raw or "").lower()
    clauses=[re.sub(r"\s+"," ",c).strip() for c in re.split(r"[\n\r.;!?|]+",src) if c.strip()]
    req_pat=re.compile(r"\b(?:require|requires|required|requirement|wanted|need(?:ed)?|looking for|tenant requirement)\b")
    rent_pat=re.compile(r"\b(?:for rent|on rent|rent(?:al)?|lease|to[- ]?let)\b")
    purchase_pat=re.compile(r"\b(?:purchase|buy|buyer|acquire|acquisition)\b")
    strong_rent=False
    strong_purchase=False
    for i,c in enumerate(clauses):
        if not req_pat.search(c):
            continue
        if rent_pat.search(c):
            strong_rent=True
        if purchase_pat.search(c):
            strong_purchase=True
        # Permit a short immediately-adjacent transaction clause only.
        if i+1 < len(clauses) and len(clauses[i+1]) <= 70:
            if rent_pat.search(clauses[i+1]): strong_rent=True
            if purchase_pat.search(clauses[i+1]): strong_purchase=True
    return strong_rent,strong_purchase,clauses

def _mixed_parent(raw):
    n=_norm(raw)
    sale_headers=len(re.findall(r"\b(?:for sale|available for sale|sale inventory|available on sale)\b",n))
    rent_headers=len(re.findall(r"\b(?:for rent|available for rent|available for lease|available on lease)\b",n))
    return sale_headers>0 and rent_headers>0, sale_headers, rent_headers

def predict_message(raw):
    raw=raw or ""
    p=v431.predict_message(raw)
    n=_norm(raw)
    rules=[p.get("rule","V431_BASE")]
    evidence=dict(p.get("evidence") or {})
    cls=p["class"]; tx=p["transaction"]; own=p["ownership"]; conf=float(p["confidence"])

    strong_rent,strong_purchase,clauses=_clause_relation(raw)
    mixed,sale_headers,rent_headers=_mixed_parent(raw)
    rf=v431._repair_features(raw)

    # Final ontology precedence: explicit independent sale and rent sections in a
    # parent inventory can never be collapsed back to SALE by a later rent/price rule.
    if cls=="INVENTORY_GROUP" and mixed:
        tx="AMBIGUOUS"
        own="OWNED"
        conf=max(conf,99.2)
        rules.append("V432_MIXED_PARENT_FINAL_PRECEDENCE")

    # Requirement transaction must be owned by the requirement clause. A distant
    # rent/lease word elsewhere does not make the demand a rental requirement.
    elif cls=="REQUIREMENT":
        if strong_purchase or rf["explicit_purchase_requirement"]:
            tx="SALE"
            conf=max(conf,99.0)
            rules.append("V432_REQUIREMENT_CLAUSE_PURCHASE")
        elif strong_rent:
            tx="RENT"
            conf=max(conf,98.9)
            rules.append("V432_REQUIREMENT_CLAUSE_RENT")
        elif rf["low_budget"] and rf["residential_spec"]:
            tx="RENT"
            conf=max(conf,98.8)
            rules.append("V432_REQUIREMENT_RESIDENTIAL_MONTHLY_BUDGET")
        elif rf["plural_capital"]:
            tx="SALE"
            conf=max(conf,99.0)
            rules.append("V432_REQUIREMENT_CAPITAL_BUDGET_SALE")
        else:
            tx="UNKNOWN"
            rules.append("V432_REQUIREMENT_RELATION_ABSTAIN")

    if cls in {"PROPERTY_AVAILABILITY","INVENTORY_GROUP","REQUIREMENT"}:
        own="OWNED"
    elif cls=="NOISE":
        own="NOT_OWNED"

    evidence["v432_relation"]={
        "strong_rent_clause":strong_rent,
        "strong_purchase_clause":strong_purchase,
        "mixed_parent":mixed,
        "sale_headers":sale_headers,
        "rent_headers":rent_headers,
        "policy":"Clause-local transaction ownership plus final mixed-parent precedence."
    }
    return {
        "class":cls,"transaction":tx,"ownership":own,"confidence":round(conf,2),
        "rule":"|".join(r for r in rules if r),"evidence":evidence
    }

FINAL_REPAIR_REGRESSION=[
    ("mixed_parent_final_precedence",
     "FOR SALE: 3 BHK 3 Cr. FOR RENT: 4 BHK semi furnished rent 80k.",
     "INVENTORY_GROUP","AMBIGUOUS","OWNED"),
    ("mixed_parent_variant_final_precedence",
     "AVAILABLE FOR SALE: 3 BHK 3.2 Cr, 4 BHK 4.1 Cr. AVAILABLE FOR RENT: 3 BHK 75k, 4 BHK 90k.",
     "INVENTORY_GROUP","AMBIGUOUS","OWNED"),
    ("requirement_rent_same_clause",
     "Require furnished 2 BHK on rent in Mapusa for company staff.",
     "REQUIREMENT","RENT","OWNED"),
    ("requirement_unknown_distant_rent_word",
     "Requirement house 215 sq yd Sushant Lok 1. Broker handles rentals also.",
     "REQUIREMENT","UNKNOWN","OWNED"),
    ("requirement_low_budget_inference",
     "Require 1 BHK furnished in Mapusa for airport staff. Budget 20k.",
     "REQUIREMENT","RENT","OWNED"),
    ("requirement_capital_budget",
     "Immediate required 300 yds in GK1. Client budget 31 Cr. Clear title.",
     "REQUIREMENT","SALE","OWNED"),
]

def _score_cases(cases):
    fs={f:[0,0] for f in ("class","transaction","ownership")}
    errors=[]; caseok=0
    for name,raw,hc,ht,ho in cases:
        p=predict_message(raw); exp={"class":hc,"transaction":ht,"ownership":ho}; ok=True
        for f in fs:
            fs[f][1]+=1
            if p[f]==exp[f]: fs[f][0]+=1
            else:
                ok=False
                errors.append({"name":name,"field":f,"truth":exp[f],"student":p[f]})
        caseok+=int(ok)
    total=sum(v[1] for v in fs.values()); correct=sum(v[0] for v in fs.values())
    return {"cases":len(cases),"accuracy":round(100*correct/max(total,1),4),
            "field_accuracy":{k:round(100*v[0]/max(v[1],1),4) for k,v in fs.items()},
            "case_accuracy":round(100*caseok/max(len(cases),1),4),"errors":errors}

def training_status(engine):
    v4=v430._v4_truth_rows(engine)
    v4_cases=[]
    for item in v4:
        x=item["row"]; hc,ht,ho=item["truth"]
        v4_cases.append((f"V4_{x['ordinal']}",x["raw_text"],hc,ht,ho))
    v4_score=_score_cases(v4_cases)

    legacy=[]; seen=set()
    for suite in (v400.CURRICULUM,v400.ADVERSARIAL,v401.REPAIR_REGRESSION,v402.REPAIR_REGRESSION):
        for row in suite:
            key=tuple(row)
            if key not in seen:
                seen.add(key); legacy.append(row)
    legacy_score=_score_cases(legacy)

    lessons=list(v430.LESSON_REGRESSION)+list(v431.MASTER_REPAIR_REGRESSION)+FINAL_REPAIR_REGRESSION
    lesson_score=_score_cases(lessons)

    v4_ok=(len(v4_cases)==20 and v4_score["accuracy"]>=98.0 and all(v>=95.0 for v in v4_score["field_accuracy"].values()))
    legacy_ok=(legacy_score["accuracy"]>=99.0 and all(v>=98.0 for v in legacy_score["field_accuracy"].values()))
    lesson_ok=(lesson_score["accuracy"]==100.0 and all(v==100.0 for v in lesson_score["field_accuracy"].values()))
    passed=v4_ok and legacy_ok and lesson_ok
    return {
        "version":VERSION,"v4_training":v4_score,"legacy_regression":legacy_score,
        "lesson_regression":lesson_score,"v4_truth_cases":len(v4_cases),
        "training_gate":"V432_TRAINING_PASS_READY_FOR_FRESH_V5" if passed else "V432_TRAINING_HOLD_DO_NOT_FREEZE_V5",
        "scientific_policy":"V4 closed training only. Fresh V5 remains unseen until all cumulative gates pass.",
        "safety":{"production_writes":0,"whatsapp_writes":0,"gold_mutations":0}
    }

def _record_training(engine,s):
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO alliance_student_v432_runs
        (run_id,ruleset_version,v4_training_accuracy,v4_class_accuracy,v4_transaction_accuracy,
         v4_ownership_accuracy,legacy_regression_accuracy,lesson_regression_accuracy,training_gate,result)
        VALUES(:id,:rv,:va,:vc,:vt,:vo,:la,:lr,:gate,CAST(:res AS JSONB))"""),
        {"id":str(uuid.uuid4()),"rv":RULESET_VERSION,"va":s["v4_training"]["accuracy"],
         "vc":s["v4_training"]["field_accuracy"]["class"],"vt":s["v4_training"]["field_accuracy"]["transaction"],
         "vo":s["v4_training"]["field_accuracy"]["ownership"],"la":s["legacy_regression"]["accuracy"],
         "lr":s["lesson_regression"]["accuracy"],"gate":s["training_gate"],"res":_j(s)})

def _table_exists(conn,t):
    return bool(conn.execute(text("""SELECT EXISTS(SELECT 1 FROM information_schema.tables
      WHERE table_schema=current_schema() AND table_name=:t)"""),{"t":t}).scalar())

def _column_exists(conn,t,c):
    return bool(conn.execute(text("""SELECT EXISTS(SELECT 1 FROM information_schema.columns
      WHERE table_schema=current_schema() AND table_name=:t AND column_name=:c)"""),{"t":t,"c":c}).scalar())

def _used_blind_ids(conn):
    used=set()
    for t in ["alliance_mastery_v340_blind_audit_cases","alliance_mastery_v360_exam_v2_cases",
              "alliance_mastery_v380_exam_v3_cases","alliance_championship_v410_cases",
              "alliance_v5_cases","alliance_v5_431_cases","alliance_v5_432_cases"]:
        if _table_exists(conn,t) and _column_exists(conn,t,"blind_id"):
            try:
                used.update(str(x) for x in conn.execute(text(f"SELECT blind_id FROM {t} WHERE blind_id IS NOT NULL")).scalars().all())
            except Exception:
                pass
    return used

def _candidate_pool(engine):
    with engine.connect() as conn:
        rows=[dict(r) for r in conn.execute(text("""
        SELECT blind_id,source_hash,raw_text,frozen_at,status
        FROM alliance_mastery_v330_blind_cases WHERE status='FROZEN' ORDER BY blind_id
        """)).mappings()]
        used=_used_blind_ids(conn)
    return [r for r in rows if str(r["blind_id"]) not in used]

def _risk_bucket(p,raw):
    n=_norm(raw); score=0
    if p["class"]=="INVENTORY_GROUP": score+=5
    if p["class"]=="REQUIREMENT": score+=4
    if p["class"] in {"NOISE","FRAGMENT","UNRESOLVED"}: score+=4
    if p["transaction"] in {"AMBIGUOUS","UNKNOWN"}: score+=4
    if "pre" in n and ("lease" in n or "rent" in n): score+=3
    if len(raw or "")>800: score+=3
    if any(x in n for x in ["looking for","client wants","getting vacated","many more options","inventory"]): score+=2
    return score

def _select_v5(pool):
    if len(pool)<V5_TARGET:
        raise RuntimeError(f"Only {len(pool)} untouched blind cases remain; need {V5_TARGET}.")
    enriched=[]
    for r in pool:
        p=predict_message(r["raw_text"])
        tie=int(hashlib.sha256((V5_EXAM_VERSION+str(r["blind_id"])).encode()).hexdigest()[:12],16)
        enriched.append((r,p,_risk_bucket(p,r["raw_text"]),tie))
    groups={}
    for item in enriched:
        groups.setdefault((item[1]["class"],item[1]["transaction"]),[]).append(item)
    for vals in groups.values():
        vals.sort(key=lambda x:(-x[2],x[3]))
    selected=[]
    for key in sorted(groups):
        if groups[key] and len(selected)<V5_TARGET:
            selected.append(groups[key].pop(0))
    remaining=[x for vals in groups.values() for x in vals]
    remaining.sort(key=lambda x:(-x[2],x[3]))
    for x in remaining:
        if len(selected)>=V5_TARGET: break
        selected.append(x)
    selected.sort(key=lambda x:int(hashlib.sha256(("V5_432_ORDER|"+str(x[0]["blind_id"])).encode()).hexdigest()[:12],16))
    return selected[:V5_TARGET]

def _predictor_hash():
    try: return hashlib.sha256(inspect.getsource(predict_message).encode("utf-8")).hexdigest()
    except Exception: return "UNAVAILABLE"

def freeze_v5(engine):
    _install(engine)
    train=training_status(engine)
    if train["training_gate"]!="V432_TRAINING_PASS_READY_FOR_FRESH_V5":
        return {"status":"BLOCKED","reason":"4.3.2 training/regression gate HOLD.","training":train}
    with engine.connect() as conn:
        existing=conn.execute(text("SELECT COUNT(*) FROM alliance_v5_432_cases WHERE exam_version=:e"),{"e":V5_EXAM_VERSION}).scalar() or 0
        if existing:
            manifest=conn.execute(text("SELECT predictor_sha256,case_manifest_hash,status FROM alliance_v5_432_manifest WHERE exam_version=:e"),{"e":V5_EXAM_VERSION}).mappings().first()
            return {"status":"ALREADY_FROZEN","total":int(existing),"manifest":dict(manifest) if manifest else None}
    selected=_select_v5(_candidate_pool(engine))
    psha=_predictor_hash()
    payload=[{"blind_id":str(r["blind_id"]),"source_hash":r["source_hash"],"predicted_class":p["class"],
              "predicted_transaction":p["transaction"],"predicted_ownership":p["ownership"]} for r,p,_,_ in selected]
    mhash=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO alliance_v5_432_manifest
        (manifest_id,exam_version,target,predictor_version,predictor_sha256,selection_policy,case_manifest_hash,status)
        VALUES(:id,:e,:t,:pv,:ps,:policy,:mh,'FROZEN')"""),
        {"id":str(uuid.uuid4()),"e":V5_EXAM_VERSION,"t":V5_TARGET,"pv":VERSION,"ps":psha,
         "policy":"Untouched frozen blind pool only; excludes all prior V1/V2/V3/V4/V5 IDs; diversity selection uses Student 4.3.2 predictions only; no truth used.",
         "mh":mhash})
        for ordinal,(r,p,_,_) in enumerate(selected,1):
            conn.execute(text("""INSERT INTO alliance_v5_432_cases
            (audit_id,blind_id,exam_version,ordinal,source_hash,raw_text,predicted_class,predicted_transaction,
             predicted_ownership,prediction_confidence,prediction_rule)
            VALUES(:id,:bid,:e,:ord,:sh,:raw,:cl,:tx,:ow,:cf,:rule)"""),
            {"id":str(uuid.uuid4()),"bid":str(r["blind_id"]),"e":V5_EXAM_VERSION,"ord":ordinal,
             "sh":r["source_hash"],"raw":r["raw_text"],"cl":p["class"],"tx":p["transaction"],
             "ow":p["ownership"],"cf":float(p["confidence"]),"rule":p.get("rule")})
    return {"status":"FROZEN","total":V5_TARGET,"predictor_sha256":psha,"case_manifest_hash":mhash}

def _exam_judges(engine,raw):
    out={}
    for name,j in v421._judges(engine,raw).items():
        out[name]={"class":j[0],"transaction":j[1],"ownership":j[2],
                   "class_confidence":float(j[3]),"transaction_confidence":float(j[4]),
                   "ownership_confidence":float(j[5]),"evidence":j[6]}
    for name,fn in [("G_V422_SEMANTIC",v422.semantic_truth),
                    ("H_V423_DUAL_GRAMMAR",v423.rescue_truth),
                    ("I_V425_DUAL_ACQUISITION",v425.acquisition_truth)]:
        j=fn(raw)
        out[name]={"class":j[0],"transaction":j[1],"ownership":j[2],
                   "class_confidence":float(j[3]),"transaction_confidence":float(j[3]),
                   "ownership_confidence":float(j[3]),"evidence":j[4]}
    return out

def _resolve_field(judges,field):
    ck=f"{field}_confidence"; votes=[]
    for name,j in judges.items():
        val=j.get(field); cf=float(j.get(ck) or 0)
        if val and cf>=0.95: votes.append((name,val,cf))
    if not votes: return {"status":"UNRESOLVED","reason":"NO_QUALIFIED_VOTES"}
    by=defaultdict(list)
    for name,val,cf in votes: by[val].append((name,cf))
    winner,wvotes=max(by.items(),key=lambda kv:(len(kv[1]),sum(x[1] for x in kv[1])))
    avg=sum(x[1] for x in wvotes)/len(wvotes)
    strong_dissent=[(name,val,cf) for name,val,cf in votes if val!=winner and cf>=0.985]
    semantic_core={"A_EVIDENCE_CONTRACT","B_COUNTERFACTUAL_CRITIC","D_CRE_DECISION_GRAPH","F_INTENT_HIERARCHY"}
    core=sum(1 for name,cf in wvotes if name in semantic_core)
    accepted=((len(wvotes)>=4 and avg>=0.96 and len(strong_dissent)<=1)
              or (len(wvotes)>=3 and avg>=0.975 and core>=2 and not strong_dissent))
    if not accepted:
        return {"status":"UNRESOLVED","majority":winner,"count":len(wvotes),"avg_confidence":round(avg,4),
                "strong_dissent":strong_dissent,"votes":[{"judge":n,"value":v,"confidence":c} for n,v,c in votes]}
    return {"status":"RESOLVED","value":winner,"confidence":round(avg,4),
            "votes":[{"judge":n,"value":v,"confidence":c} for n,v,c in votes]}

def _adjudicate(engine):
    frozen=freeze_v5(engine)
    if frozen.get("status")=="BLOCKED": return {"status":"BLOCKED","freeze":frozen}
    with engine.connect() as conn:
        rows=[dict(r) for r in conn.execute(text("SELECT * FROM alliance_v5_432_cases WHERE exam_version=:e ORDER BY ordinal"),{"e":V5_EXAM_VERSION}).mappings()]
    for r in rows:
        with engine.connect() as conn:
            if conn.execute(text("SELECT 1 FROM alliance_v5_432_truth WHERE audit_id=:id"),{"id":str(r["audit_id"])}).scalar(): continue
        judges=_exam_judges(engine,r["raw_text"])
        fields={f:_resolve_field(judges,f) for f in ("class","transaction","ownership")}
        ok=all(v.get("status")=="RESOLVED" for v in fields.values())
        with engine.begin() as conn:
            conn.execute(text("""INSERT INTO alliance_v5_432_truth
            (truth_id,audit_id,exam_version,truth_class,truth_transaction,truth_ownership,
             class_confidence,transaction_confidence,ownership_confidence,consensus,status)
            VALUES(:id,:aid,:e,:cl,:tx,:ow,:cc,:tc,:oc,CAST(:con AS JSONB),:st)"""),
            {"id":str(uuid.uuid4()),"aid":str(r["audit_id"]),"e":V5_EXAM_VERSION,
             "cl":fields["class"].get("value") if ok else None,
             "tx":fields["transaction"].get("value") if ok else None,
             "ow":fields["ownership"].get("value") if ok else None,
             "cc":fields["class"].get("confidence",0),"tc":fields["transaction"].get("confidence",0),
             "oc":fields["ownership"].get("confidence",0),
             "con":_j({"status":"AUTO_RESOLVED" if ok else "EXCEPTION","fields":fields,
                       "policy":"Frozen pre-V5 examiner stack; Student 4.3.2 excluded."}),
             "st":"AUTO_RESOLVED" if ok else "EXCEPTION"})
    return v5_report(engine)

def v5_report(engine):
    with engine.connect() as conn:
        rows=[dict(r) for r in conn.execute(text("""
        SELECT c.*,t.truth_class,t.truth_transaction,t.truth_ownership,t.status truth_status,t.consensus
        FROM alliance_v5_432_cases c
        LEFT JOIN alliance_v5_432_truth t ON t.audit_id=c.audit_id
        WHERE c.exam_version=:e ORDER BY c.ordinal"""),{"e":V5_EXAM_VERSION}).mappings()]
    unresolved=[{"ordinal":r["ordinal"],"audit_id":str(r["audit_id"]),"reason":r["consensus"]} for r in rows if r["truth_status"]!="AUTO_RESOLVED"]
    if unresolved:
        return {"version":VERSION,"exam_version":V5_EXAM_VERSION,"total":len(rows),
                "auto_resolved":len(rows)-len(unresolved),"unresolved":len(unresolved),
                "unresolved_cases":unresolved,"manual_work_required":0,
                "certification_gate":"V5_AUTOMATED_TRUTH_INCOMPLETE_EXCEPTION_ONLY",
                "safety":{"student_tuning_during_v5":0,"production_writes":0,"whatsapp_writes":0,"gold_mutations":0}}
    fs={f:[0,0] for f in ("class","transaction","ownership")}; errors=[]; caseok=0; payload=[]
    for r in rows:
        t={"class":r["truth_class"],"transaction":r["truth_transaction"],"ownership":r["truth_ownership"]}
        p={"class":r["predicted_class"],"transaction":r["predicted_transaction"],"ownership":r["predicted_ownership"]}
        payload.append((str(r["audit_id"]),t["class"],t["transaction"],t["ownership"]))
        ok=True
        for f in fs:
            fs[f][1]+=1
            if p[f]==t[f]: fs[f][0]+=1
            else: ok=False; errors.append({"ordinal":r["ordinal"],"field":f,"truth":t[f],"student":p[f]})
        caseok+=int(ok)
    cmp=sum(v[1] for v in fs.values()); cor=sum(v[0] for v in fs.values())
    acc=round(100*cor/cmp,4); fa={k:round(100*v[0]/v[1],4) for k,v in fs.items()}; ca=round(100*caseok/len(rows),4)
    gate="AUTOMATED_INDEPENDENT_V5_PASS" if acc>=OVERALL_PASS and all(v>=FIELD_PASS for v in fa.values()) else "AUTOMATED_INDEPENDENT_V5_HOLD"
    result={"version":VERSION,"exam_version":V5_EXAM_VERSION,"total":len(rows),"auto_resolved":len(rows),
            "unresolved":0,"manual_work_required":0,"correct_fields":cor,"comparable_fields":cmp,
            "accuracy":acc,"field_accuracy":fa,"case_accuracy":ca,"errors":errors,"certification_gate":gate,
            "truth_policy":"Frozen 4.2.1/4.2.2/4.2.3/4.2.5 examiner stack; Student 4.3.2 excluded.",
            "safety":{"student_tuning_during_v5":0,"production_writes":0,"whatsapp_writes":0,"gold_mutations":0}}
    th=hashlib.sha256(json.dumps(payload,separators=(",",":")).encode()).hexdigest()
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO alliance_v5_432_results
        (result_id,exam_version,total_cases,auto_resolved,unresolved,comparable_fields,correct_fields,
         overall_accuracy,class_accuracy,transaction_accuracy,ownership_accuracy,case_accuracy,
         certification_gate,truth_hash,result)
        VALUES(:id,:e,:tot,:a,0,:cmp,:cor,:oa,:ca,:ta,:ow,:casea,:gate,:th,CAST(:res AS JSONB))
        ON CONFLICT(exam_version) DO NOTHING"""),
        {"id":str(uuid.uuid4()),"e":V5_EXAM_VERSION,"tot":len(rows),"a":len(rows),"cmp":cmp,"cor":cor,
         "oa":acc,"ca":fa["class"],"ta":fa["transaction"],"ow":fa["ownership"],"casea":ca,
         "gate":gate,"th":th,"res":_j(result)})
    with engine.connect() as conn:
        return conn.execute(text("SELECT result FROM alliance_v5_432_results WHERE exam_version=:e"),{"e":V5_EXAM_VERSION}).scalar() or result

def run(engine):
    _install(engine)
    train=training_status(engine); _record_training(engine,train)
    if train["training_gate"]!="V432_TRAINING_PASS_READY_FOR_FRESH_V5":
        return {"version":VERSION,"status":"TRAINING_HOLD","training":train,"v5":{"status":"NOT_FROZEN"},
                "next_step":"Automation must continue repairing only failed cumulative training/regression concepts before V5.",
                "safety":{"production_writes":0,"whatsapp_writes":0,"gold_mutations":0}}
    frozen=freeze_v5(engine); exam=_adjudicate(engine)
    return {"version":VERSION,"status":"V5_RUNNING_OR_COMPLETE","training":train,
            "v5_freeze":frozen,"v5_exam":exam,
            "next_step":"Do not tune V5. If examiner abstains, automate truth only. If complete, accept PASS/HOLD.",
            "safety":{"production_writes":0,"whatsapp_writes":0,"gold_mutations":0}}

def _dashboard(engine):
    s=run(engine); tr=s.get("training",{}); ex=s.get("v5_exam",{})
    if s.get("status")=="TRAINING_HOLD":
        banner="<div class='warn'>4.3.2 cumulative training/regression HOLD. Fresh V5 NOT frozen.</div>"
    elif ex.get("certification_gate")=="AUTOMATED_INDEPENDENT_V5_PASS":
        banner="<div class='ok'>✓ STUDENT 4.3.2 PASSED FRESH AUTOMATED V5</div>"
    elif ex.get("certification_gate")=="AUTOMATED_INDEPENDENT_V5_HOLD":
        banner="<div class='warn'>Fresh V5 complete: Student 4.3.2 HOLD. V5 remains immutable.</div>"
    else:
        banner=f"<div class='warn'>V5 automated truth remaining: {ex.get('unresolved','—')}. No manual work requested.</div>"
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Alliance Autonomous Student 4.3.2</title>
    <style>body{{font-family:Arial;margin:30px;background:#f6f7fb;color:#172033;max-width:1200px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}}.card{{background:#fff;padding:18px;border-radius:14px;box-shadow:0 2px 10px #0001}}strong{{display:block;font-size:25px;margin-top:8px}}.ok{{background:#e8f8ee;padding:16px;border-radius:10px;font-weight:700;margin:18px 0}}.warn{{background:#fff4cf;padding:16px;border-radius:10px;font-weight:700;margin:18px 0}}pre{{white-space:pre-wrap;background:#101624;color:#eef3ff;padding:16px;border-radius:10px}}</style></head><body>
    <h1>Alliance Autonomous Student 4.3.2 → Fresh V5</h1>
    <p>Final cumulative training repair before V5: mixed-parent transaction precedence plus clause-local requirement transaction ownership.</p>
    <div class='grid'>
      <div class='card'>V4 Training<strong>{tr.get('v4_training',{}).get('accuracy','—')}%</strong></div>
      <div class='card'>Legacy Regression<strong>{tr.get('legacy_regression',{}).get('accuracy','—')}%</strong></div>
      <div class='card'>Lesson Regression<strong>{tr.get('lesson_regression',{}).get('accuracy','—')}%</strong></div>
      <div class='card'>V5 Auto Resolved<strong>{ex.get('auto_resolved','—')}</strong></div>
      <div class='card'>V5 Remaining<strong>{ex.get('unresolved','—')}</strong></div>
      <div class='card'>V5 Accuracy<strong>{ex.get('accuracy','—')}</strong></div>
      <div class='card'>V5 Gate<strong style='font-size:14px'>{html.escape(str(ex.get('certification_gate','NOT READY')))}</strong></div>
    </div>{banner}<h2>Machine Report</h2><pre>{html.escape(json.dumps(foundation._json_safe(s),ensure_ascii=False,indent=2))}</pre></body></html>"""

def register(core):
    engine=_engine(core); app=_app(core); _install(engine)
    if not foundation._route_exists(app,"/api/property-brain/autonomous-v432/status"):
        @app.get("/api/property-brain/autonomous-v432/status")
        def status_v432(): return run(engine)
    if not foundation._route_exists(app,"/property-brain/autonomous-v432"):
        @app.get("/property-brain/autonomous-v432",response_class=HTMLResponse)
        def page_v432(): return HTMLResponse(_dashboard(engine))
    try: run(engine)
    except Exception: pass
    return {"status":"REGISTERED","version":VERSION,"route":"/property-brain/autonomous-v432",
            "policy":"CUMULATIVE_FINAL_TRAINING_REPAIR_THEN_FRESH_V5",
            "production_writes":0,"whatsapp_writes":0,"gold_mutations":0}
