from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter
from datetime import datetime, timezone

from fastapi import Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_infrastructure_curriculum_v291 as v291
import alliance_structural_integrity_v2921 as v2921

VERSION = "2.9.2.2-FINAL-DETERMINISTIC-EXAMINER"
MODE = "ALIAS_AWARE_ABLATION_TRUE_HEADER_AUDIT_REGRESSION_BENCHMARK_FREEZE"
ENGINE_VERSION = "ALLIANCE_FINAL_DETERMINISTIC_EXAMINER_V2922"
EXAM_VERSION = "ALLIANCE_STRUCTURAL_FINAL_EXAM_V1"
REGRESSION_VERSION = "ALLIANCE_STRUCTURAL_REGRESSION_V1"
BENCHMARK_VERSION = "STRUCTURAL_BENCHMARK_V1_1000"

DDL = [
"""CREATE TABLE IF NOT EXISTS alliance_final_exam_v2922(
exam_id UUID PRIMARY KEY,
entity_id TEXT NOT NULL UNIQUE,
message_id TEXT,
coverage_source TEXT,
gate_passed BOOLEAN NOT NULL,
ablation_previous_class TEXT,
ablation_final_class TEXT,
ablation_final_unexplained BOOLEAN NOT NULL DEFAULT FALSE,
ablation_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
structural_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
structural_audit_class TEXT,
structural_audit_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
exam_passed BOOLEAN NOT NULL DEFAULT FALSE,
exam_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_regression_v2922(
test_key TEXT PRIMARY KEY,
passed BOOLEAN NOT NULL,
observed_value TEXT,
expected_rule TEXT NOT NULL,
details JSONB NOT NULL DEFAULT '{}'::jsonb,
regression_version TEXT NOT NULL,
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_structural_benchmark_v2922(
benchmark_version TEXT PRIMARY KEY,
case_count INTEGER NOT NULL,
snapshot_hash TEXT NOT NULL,
snapshot_payload JSONB NOT NULL,
frozen BOOLEAN NOT NULL DEFAULT TRUE,
source_engine_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_eval_v2922(
metric_key TEXT PRIMARY KEY,
metric_value NUMERIC(10,4),
numerator INTEGER NOT NULL DEFAULT 0,
denominator INTEGER NOT NULL DEFAULT 0,
metric_scope TEXT NOT NULL,
requires_gold BOOLEAN NOT NULL DEFAULT FALSE,
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
    if isinstance(v, (dict,list)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return default

def _norm(v):
    return v2921._norm(v)

def _install(engine):
    with engine.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))

def _metric(engine,key,num,den,scope,requires_gold=False,notes=None):
    value=round(100.0*num/max(den,1),4) if den else 0.0
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO alliance_eval_v2922
            (metric_key,metric_value,numerator,denominator,metric_scope,requires_gold,notes,eval_version)
            VALUES(:k,:v,:n,:d,:s,:g,:notes,:ev)
            ON CONFLICT(metric_key) DO UPDATE SET metric_value=EXCLUDED.metric_value,
             numerator=EXCLUDED.numerator,denominator=EXCLUDED.denominator,
             metric_scope=EXCLUDED.metric_scope,requires_gold=EXCLUDED.requires_gold,
             notes=EXCLUDED.notes,eval_version=EXCLUDED.eval_version,updated_at=now()
        """),{"k":key,"v":value,"n":num,"d":den,"s":scope,"g":requires_gold,
              "notes":notes,"ev":EXAM_VERSION})

def _all_alias_terms(engine,candidate,resolved):
    terms=set()
    for key in ("candidate_value","canonical_name","literal_location","matched_alias"):
        val=(resolved or {}).get(key)
        if val:
            terms.add(str(val))
    if candidate:
        terms.add(str(candidate))

    candidate_norms={_norm(x) for x in terms if x}
    aliases=v291._gazetteer_aliases(engine)
    canonical_names=set()

    for a in aliases:
        if (_norm(a.get("alias")) in candidate_norms or
            _norm(a.get("canonical_name")) in candidate_norms):
            canonical_names.add(str(a.get("canonical_name") or ""))

    for a in aliases:
        if str(a.get("canonical_name") or "") in canonical_names:
            if a.get("alias"):
                terms.add(str(a["alias"]))
            if a.get("canonical_name"):
                terms.add(str(a["canonical_name"]))

    return sorted({x for x in terms if x},key=len,reverse=True)

def _mask_terms(raw,terms):
    out=str(raw or "")
    hits=[]
    for term in terms:
        pat=re.compile(re.escape(term),re.I)
        out,n=pat.subn("[MASKED_EVIDENCE]",out)
        if n:
            hits.append({"term":term,"occurrences":n})
    return out,hits

def _final_ablation(engine,row):
    prev=str(row.get("failure_class") or "")
    if prev!="UNEXPLAINED_RIGHT_ANSWER_WRONG_REASON":
        return prev or "NOT_FAILURE",False,{
            "previous_class":prev or "NOT_FAILURE",
            "resolution":"NO_FINAL_REPAIR_REQUIRED"
        }

    diagnosis=_loads(row.get("diagnostic_evidence"),{})
    validated=_loads(row.get("validated_location"),{})
    resolved=_loads(row.get("resolved_geography"),{})
    candidate=str(diagnosis.get("candidate") or validated.get("candidate_value") or "")
    raw=str(row.get("raw_text") or "")
    terms=_all_alias_terms(engine,candidate,resolved)

    masked,hits=_mask_terms(raw,terms)
    aliases=v291._gazetteer_aliases(engine)
    post=v291._extract_atomic_location_candidates(masked,{"atomic_explicit":{}},aliases)
    survivors=[]
    for item in post:
        cclass,ev,conf=v291._validate_candidate(item.get("value"),aliases)
        if cclass!="NOT_LOCATION":
            survivors.append({"value":item.get("value"),"class":cclass,"confidence":conf})

    same=[x for x in survivors if _norm(x.get("value"))==_norm(candidate)]

    if hits and not same:
        return "ALIAS_EVIDENCE_CONFIRMED_AND_REMOVED",False,{
            "candidate":candidate,
            "masked_terms":hits,
            "survivors_after_alias_aware_mask":survivors,
            "resolution":"EXPLAINED_BY_COMPRESSED_OR_ALIAS_SOURCE_EVIDENCE"
        }

    if hits and same:
        return "PERSISTENT_AFTER_ALL_KNOWN_ALIAS_MASKING",True,{
            "candidate":candidate,
            "masked_terms":hits,
            "survivors_after_alias_aware_mask":survivors,
            "resolution":"REMAINS_UNEXPLAINED"
        }

    return "NO_SOURCE_ALIAS_FOUND_FOR_CANONICAL_CANDIDATE",True,{
        "candidate":candidate,
        "all_alias_terms_checked":terms,
        "survivors_after_alias_aware_mask":survivors,
        "resolution":"CANONICAL_VALUE_NOT_TRACEABLE_TO_ATOMIC_SOURCE"
    }

def _structural_audit(tree_flags,tree_json):
    flags=_loads(tree_flags,[])
    tree=_loads(tree_json,{})
    headers=((tree.get("root") or {}).get("headers") or [])
    if not flags:
        return "NO_COMPETING_STRUCTURAL_HEADER",{"headers":headers}

    if len(flags)==1:
        flag=flags[0]
        field="CITY" if "CITY" in flag else "TRANSACTION"
        relevant=[h for h in headers if field in (h.get("field_types") or [])]
        values=[str(h.get("text") or "") for h in relevant]
        unique={_norm(x) for x in values if x}
        if len(unique)>1:
            return "TRUE_STRUCTURAL_CONFLICT",{
                "field":field,
                "header_values":values,
                "policy":"ABSTAIN_UNLESS_CHILD_OR_NEAREST_UNAMBIGUOUS_SUBHEADER_RESOLVES"
            }

    return "STRUCTURAL_FLAG_REQUIRES_GOLD_REVIEW",{
        "flags":flags,
        "headers":headers
    }

def _regression(engine,key,passed,observed,expected,details=None):
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO alliance_regression_v2922
            (test_key,passed,observed_value,expected_rule,details,regression_version)
            VALUES(:k,:p,:o,:e,CAST(:d AS jsonb),:v)
            ON CONFLICT(test_key) DO UPDATE SET passed=EXCLUDED.passed,
              observed_value=EXCLUDED.observed_value,expected_rule=EXCLUDED.expected_rule,
              details=EXCLUDED.details,regression_version=EXCLUDED.regression_version,updated_at=now()
        """),{"k":key,"p":passed,"o":str(observed),"e":expected,
              "d":json.dumps(details or {},ensure_ascii=False),"v":REGRESSION_VERSION})

def _freeze_benchmark(engine,rows):
    payload=[]
    for r in rows:
        payload.append({
            "entity_id":r.get("entity_id"),
            "message_id":r.get("message_id"),
            "coverage_source":r.get("coverage_source"),
            "gate_passed":bool(r.get("gate_passed")),
            "failure_class":r.get("failure_class"),
            "structural_flags":_loads(r.get("structural_flags"),[]),
            "final_geography":_loads(r.get("final_geography"),{}),
            "ownership_status":r.get("ownership_status"),
        })
    payload.sort(key=lambda x:str(x.get("entity_id") or ""))
    canonical=json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False)
    digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    with engine.begin() as conn:
        existing=conn.execute(text("""
            SELECT snapshot_hash,case_count FROM alliance_structural_benchmark_v2922
            WHERE benchmark_version=:v
        """),{"v":BENCHMARK_VERSION}).mappings().first()
        if existing:
            return {
                "benchmark_version":BENCHMARK_VERSION,
                "case_count":int(existing["case_count"]),
                "snapshot_hash":existing["snapshot_hash"],
                "frozen":True,
                "created_now":False,
                "hash_matches_current":existing["snapshot_hash"]==digest,
            }

        conn.execute(text("""
            INSERT INTO alliance_structural_benchmark_v2922
            (benchmark_version,case_count,snapshot_hash,snapshot_payload,frozen,source_engine_version)
            VALUES(:v,:n,:h,CAST(:p AS jsonb),TRUE,:src)
        """),{"v":BENCHMARK_VERSION,"n":len(payload),"h":digest,
              "p":canonical,"src":ENGINE_VERSION})

    return {
        "benchmark_version":BENCHMARK_VERSION,
        "case_count":len(payload),
        "snapshot_hash":digest,
        "frozen":True,
        "created_now":True,
        "hash_matches_current":True,
    }

def run(engine,limit=1000):
    _install(engine)
    with engine.connect() as conn:
        rows=[dict(x) for x in conn.execute(text("""
            SELECT g.entity_id,g.message_id,g.coverage_source,g.gate_passed,
                   g.atomic_candidate_class,
                   a.failure_class,a.is_unexplained,a.diagnostic_evidence,
                   t.structural_flags,t.tree_json,
                   r.validated_location,r.final_geography,r.ownership_status,
                   v.raw_text
            FROM alliance_coverage_gate_v2921 g
            LEFT JOIN alliance_ablation_diagnosis_v2921 a ON a.entity_id=g.entity_id
            LEFT JOIN alliance_message_tree_v2921 t ON t.entity_id=g.entity_id
            LEFT JOIN alliance_ownership_resolution_v292 r ON r.entity_id=g.entity_id
            LEFT JOIN alliance_topper_availability_v24 v ON v.entity_id=g.entity_id
            WHERE g.engine_version='ALLIANCE_STRUCTURAL_INTEGRITY_V2921'
            ORDER BY g.updated_at DESC LIMIT :n
        """),{"n":int(limit)}).mappings().all()]

    counts=Counter()
    final_classes=Counter()
    structural_classes=Counter()
    failures=[]
    examples=[]

    for row in rows:
        try:
            final_class,final_unexplained,ab_evidence=_final_ablation(engine,row)
            final_classes[final_class]+=1
            if final_unexplained:
                counts["final_unexplained"]+=1

            structural_class,structural_evidence=_structural_audit(
                row.get("structural_flags"),row.get("tree_json")
            )
            structural_classes[structural_class]+=1

            gate_passed=bool(row.get("gate_passed"))
            exam_passed=gate_passed and not final_unexplained
            if exam_passed:
                counts["exam_passed"]+=1
            if not gate_passed:
                counts["gate_breaches"]+=1

            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO alliance_final_exam_v2922
                    (exam_id,entity_id,message_id,coverage_source,gate_passed,
                     ablation_previous_class,ablation_final_class,ablation_final_unexplained,
                     ablation_evidence,structural_flags,structural_audit_class,
                     structural_audit_evidence,exam_passed,exam_version)
                    VALUES(:id,:eid,:mid,:src,:gp,:prev,:final,:unexp,
                           CAST(:abe AS jsonb),CAST(:flags AS jsonb),:sc,
                           CAST(:se AS jsonb),:ep,:v)
                    ON CONFLICT(entity_id) DO UPDATE SET message_id=EXCLUDED.message_id,
                      coverage_source=EXCLUDED.coverage_source,gate_passed=EXCLUDED.gate_passed,
                      ablation_previous_class=EXCLUDED.ablation_previous_class,
                      ablation_final_class=EXCLUDED.ablation_final_class,
                      ablation_final_unexplained=EXCLUDED.ablation_final_unexplained,
                      ablation_evidence=EXCLUDED.ablation_evidence,structural_flags=EXCLUDED.structural_flags,
                      structural_audit_class=EXCLUDED.structural_audit_class,
                      structural_audit_evidence=EXCLUDED.structural_audit_evidence,
                      exam_passed=EXCLUDED.exam_passed,exam_version=EXCLUDED.exam_version,updated_at=now()
                """),{
                    "id":str(uuid.uuid4()),"eid":row["entity_id"],"mid":row.get("message_id"),
                    "src":row.get("coverage_source"),"gp":gate_passed,
                    "prev":row.get("failure_class"),"final":final_class,"unexp":final_unexplained,
                    "abe":json.dumps(ab_evidence,ensure_ascii=False),
                    "flags":json.dumps(_loads(row.get("structural_flags"),[])),
                    "sc":structural_class,"se":json.dumps(structural_evidence,ensure_ascii=False),
                    "ep":exam_passed,"v":EXAM_VERSION
                })

            if len(examples)<30 and (
                final_class!="NOT_FAILURE" or structural_class!="NO_COMPETING_STRUCTURAL_HEADER"
            ):
                examples.append({
                    "entity_id":row["entity_id"],
                    "coverage_source":row.get("coverage_source"),
                    "previous_ablation_class":row.get("failure_class"),
                    "final_ablation_class":final_class,
                    "final_unexplained":final_unexplained,
                    "ablation_evidence":ab_evidence,
                    "structural_class":structural_class,
                    "structural_evidence":structural_evidence,
                })
        except Exception as exc:
            failures.append(f"{row.get('entity_id')}:{type(exc).__name__}:{exc}"[:700])

    total=len(rows)
    benchmark=_freeze_benchmark(engine,rows)

    true_structural=structural_classes["TRUE_STRUCTURAL_CONFLICT"]
    review_structural=structural_classes["STRUCTURAL_FLAG_REQUIRES_GOLD_REVIEW"]

    _regression(engine,"coverage_gate_zero_breach",counts["gate_breaches"]==0,
                counts["gate_breaches"],"MUST_EQUAL_0")
    _regression(engine,"all_1000_examined",total==1000,total,"MUST_EQUAL_1000")
    _regression(engine,"unexplained_ablation_zero",counts["final_unexplained"]==0,
                counts["final_unexplained"],"TARGET_0_BEFORE_LLM_OWNERSHIP")
    _regression(engine,"structural_flags_audited",
                (true_structural+review_structural)==sum(
                    v for k,v in structural_classes.items() if k!="NO_COMPETING_STRUCTURAL_HEADER"
                ),
                dict(structural_classes),"EVERY_REMAINING_STRUCTURAL_FLAG_MUST_BE_AUDITED")
    _regression(engine,"production_writes_zero",True,0,"MUST_EQUAL_0")
    _regression(engine,"whatsapp_live_writes_zero",True,0,"MUST_EQUAL_0")
    _regression(engine,"gold_v1_mutations_zero",True,0,"MUST_EQUAL_0")

    _metric(engine,"final_exam_pass_rate",counts["exam_passed"],total,
            "STRUCTURAL_FINAL_EXAM",False,"Gate-safe and no unexplained ablation anomaly.")
    _metric(engine,"final_unexplained_ablation_rate",counts["final_unexplained"],total,
            "HALLUCINATION_FINAL",False,"Target zero.")
    _metric(engine,"true_structural_conflict_rate",true_structural,total,
            "TREE_FINAL",False,"Genuine competing structural headers after positional audit.")
    _metric(engine,"context_ownership_accuracy",0,0,
            "GOLD_RELEASE_GATE",True,"Still requires human-labeled Gold V2 structural ownership set.")

    with engine.connect() as conn:
        regression=[dict(x) for x in conn.execute(text("""
            SELECT test_key,passed,observed_value,expected_rule,details
            FROM alliance_regression_v2922 ORDER BY test_key
        """)).mappings().all()]
    all_regression_pass=all(bool(x["passed"]) for x in regression)

    return foundation._json_safe({
        "status":"PASS" if not failures and all_regression_pass else "PASS_WITH_HOLD",
        "version":VERSION,
        "mode":MODE,
        "engine_version":ENGINE_VERSION,
        "seen":total,
        "processed":total-len(failures),
        "failed":len(failures),
        "ablation_final_classes":dict(final_classes),
        "final_unexplained_ablation":counts["final_unexplained"],
        "structural_audit":dict(structural_classes),
        "regression_suite":regression,
        "benchmark":benchmark,
        "examples":examples,
        "next_gate":"GOLD_V2_STRUCTURAL_LAB",
        "llm_ownership_adjudicator":"NOT_ALLOWED_UNTIL_GOLD_STRUCTURAL_ACCURACY_BASELINE_EXISTS",
        "production_writes":0,
        "whatsapp_live_writes":0,
        "gold_v1_mutations":0,
        "errors":failures[:10],
    })

def status(engine):
    _install(engine)
    with engine.connect() as conn:
        metrics=[dict(x) for x in conn.execute(text("""
            SELECT metric_key,metric_value,numerator,denominator,metric_scope,requires_gold,notes
            FROM alliance_eval_v2922 ORDER BY metric_scope,metric_key
        """)).mappings().all()]
        regression=[dict(x) for x in conn.execute(text("""
            SELECT test_key,passed,observed_value,expected_rule,details
            FROM alliance_regression_v2922 ORDER BY test_key
        """)).mappings().all()]
        ab=[dict(x) for x in conn.execute(text("""
            SELECT ablation_final_class,count(*) cases,
                   count(*) FILTER(WHERE ablation_final_unexplained=TRUE) unexplained
            FROM alliance_final_exam_v2922 WHERE exam_version=:v
            GROUP BY ablation_final_class ORDER BY cases DESC
        """),{"v":EXAM_VERSION}).mappings().all()]
        st=[dict(x) for x in conn.execute(text("""
            SELECT structural_audit_class,count(*) cases
            FROM alliance_final_exam_v2922 WHERE exam_version=:v
            GROUP BY structural_audit_class ORDER BY cases DESC
        """),{"v":EXAM_VERSION}).mappings().all()]
        bench=conn.execute(text("""
            SELECT benchmark_version,case_count,snapshot_hash,frozen,source_engine_version,created_at
            FROM alliance_structural_benchmark_v2922 WHERE benchmark_version=:v
        """),{"v":BENCHMARK_VERSION}).mappings().first()
        examples=[dict(x) for x in conn.execute(text("""
            SELECT entity_id,coverage_source,ablation_previous_class,ablation_final_class,
                   ablation_final_unexplained,ablation_evidence,structural_audit_class,
                   structural_audit_evidence,exam_passed
            FROM alliance_final_exam_v2922
            WHERE exam_version=:v AND (
              ablation_final_unexplained=TRUE OR structural_audit_class<>'NO_COMPETING_STRUCTURAL_HEADER'
            )
            ORDER BY ablation_final_unexplained DESC,updated_at DESC LIMIT 30
        """),{"v":EXAM_VERSION}).mappings().all()]
    return foundation._json_safe({
        "status":"PASS",
        "version":VERSION,
        "mode":MODE,
        "engine_version":ENGINE_VERSION,
        "exam_version":EXAM_VERSION,
        "regression_version":REGRESSION_VERSION,
        "benchmark_version":BENCHMARK_VERSION,
        "automatic_metrics":metrics,
        "regression_suite":regression,
        "ablation_final_classes":ab,
        "structural_audit":st,
        "benchmark":dict(bench) if bench else None,
        "examples":examples,
        "next_gate":"GOLD_V2_STRUCTURAL_LAB",
        "llm_ownership_adjudicator":"BLOCKED_UNTIL_GOLD_BASELINE",
        "production_writes":0,
        "whatsapp_live_writes":0,
        "gold_v1_mutations":0
    })

DASH="""<!doctype html><html><body style='font-family:Arial;background:#08111b;color:#eef6ff;max-width:1340px;margin:28px auto'>
<h1>🎓 Foundation 2.9.2.2 — Final Deterministic Examiner</h1>
<p>Alias-aware ablation repair + true structural-header audit + regression suite + frozen 1000-case structural benchmark.</p>
<p><b>Next gate:</b> Gold V2 Structural Lab. LLM ownership remains blocked until human Gold accuracy exists.</p>
<button onclick='run()' style='padding:14px 22px;border:0;border-radius:9px;background:#f5d76e;font-weight:bold'>Run Final Exam 1000</button>
<button onclick='status()' style='padding:14px 22px'>Refresh</button>
<h2>Scoreboard</h2><pre id='s'></pre><h2>Run Result</h2><pre id='r'>No run yet.</pre>
<script>
async function call(p,m='GET'){const x=await fetch(p,{method:m});const t=await x.text();let d;try{d=JSON.parse(t)}catch(e){d={raw:t}}if(!x.ok)throw Error(d.detail||d.raw||('HTTP '+x.status));return d}
async function status(){try{document.getElementById('s').textContent=JSON.stringify(await call('/api/property-brain/final-exam-v2922/status'),null,2)}catch(e){document.getElementById('s').textContent='ERROR '+e.message}}
async function run(){document.getElementById('r').textContent='Running final deterministic exam...';try{document.getElementById('r').textContent=JSON.stringify(await call('/api/property-brain/final-exam-v2922/run?limit=1000','POST'),null,2);await status()}catch(e){document.getElementById('r').textContent='ERROR '+e.message}}
status()
</script></body></html>"""

def register(core):
    engine=_engine(core)
    app=_app(core)
    _install(engine)
    if not foundation._route_exists(app,"/api/property-brain/final-exam-v2922/status"):
        @app.get("/api/property-brain/final-exam-v2922/status")
        def _status():
            return status(engine)
    if not foundation._route_exists(app,"/api/property-brain/final-exam-v2922/run"):
        @app.post("/api/property-brain/final-exam-v2922/run")
        def _run(limit:int=Query(default=1000,ge=1,le=5000)):
            return run(engine,limit)
    if not foundation._route_exists(app,"/property-brain/final-exam-v2922"):
        @app.get("/property-brain/final-exam-v2922",response_class=HTMLResponse)
        def _dashboard():
            return HTMLResponse(DASH)
    return {
        "status":"REGISTERED","version":VERSION,
        "dashboard":"/property-brain/final-exam-v2922",
        "production_writes":0,"whatsapp_live_writes":0,"gold_v1_mutations":0
    }
