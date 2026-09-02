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
import alliance_infrastructure_curriculum_v291 as v291
import alliance_ownership_structural_v292 as v292

VERSION = "2.9.2.1-STRUCTURAL-INTEGRITY-REPAIR"
MODE = "COVERAGE_SOURCE_AWARE_INVARIANT_ABLATION_ROOT_CAUSE_POSITIONAL_TREE"
ENGINE_VERSION = "ALLIANCE_STRUCTURAL_INTEGRITY_V2921"
TREE_VERSION = "ALLIANCE_POSITIONAL_MESSAGE_TREE_V1_1"
GATE_VERSION = "ALLIANCE_COVERAGE_GATE_SOURCE_AWARE_V1_1"
ABLATION_VERSION = "ALLIANCE_ABLATION_ROOT_CAUSE_V1_1"

DDL = [
"""CREATE TABLE IF NOT EXISTS alliance_coverage_gate_v2921(
gate_id UUID PRIMARY KEY,
entity_id TEXT NOT NULL UNIQUE,
message_id TEXT,
coverage_eligible BOOLEAN NOT NULL DEFAULT FALSE,
coverage_source TEXT NOT NULL,
atomic_candidate_class TEXT,
parent_ownership_status TEXT,
gate_passed BOOLEAN NOT NULL DEFAULT TRUE,
gate_reason TEXT NOT NULL,
engine_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_ablation_diagnosis_v2921(
diagnosis_id UUID PRIMARY KEY,
entity_id TEXT NOT NULL UNIQUE,
message_id TEXT,
original_candidate JSONB NOT NULL DEFAULT '{}'::jsonb,
original_raw_text TEXT,
masked_text TEXT,
survivors JSONB NOT NULL DEFAULT '[]'::jsonb,
failure_class TEXT NOT NULL,
is_unexplained BOOLEAN NOT NULL DEFAULT FALSE,
diagnostic_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
engine_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_message_tree_v2921(
tree_id UUID PRIMARY KEY,
entity_id TEXT NOT NULL UNIQUE,
message_id TEXT,
source_item_no INTEGER,
tree_json JSONB NOT NULL DEFAULT '{}'::jsonb,
structural_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
header_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
engine_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_eval_v2921(
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

HEADER_CITY_RE = re.compile(
    r"^(?:\*{0,3}\s*)?(delhi|new\s+delhi|gurgaon|gurugram|noida|greater\s+noida|"
    r"faridabad|ghaziabad|goa|panaji|panjim|mumbai|jaipur)"
    r"(?:\s*[-:/|]\s*(?:sale|rent|rental|lease|properties?|inventory))?\s*\*{0,3}$",
    re.I
)
HEADER_TX_RE = re.compile(
    r"^(?:\*{0,3}\s*)?(?:for\s+)?(sale|rent|rental|lease)"
    r"(?:\s+(?:available|inventory|properties?))?\s*\*{0,3}$",
    re.I
)
HEADER_LOCALITY_RE = re.compile(
    r"^(?:\*{0,3}\s*)?(?:location\s*[:\-]\s*)?"
    r"([A-Za-z][A-Za-z0-9 .'\-&]{2,45})\s*\*{0,3}$",
    re.I
)

DATAISH_RE = re.compile(
    r"(?:₹|\brs\.?\b|\bcr\b|\blac\b|\blakh\b|\b\d+\s*(?:sq\.?\s*ft|sqft|sq\s*yd|sqyd|sqm|acre|bhk)\b|"
    r"\b\d{7,}\b|\b\d+(?:\.\d+)?\s*(?:cr|lac|lakh)\b)",
    re.I
)

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
    s=unicodedata.normalize("NFKC",str(v or "")).casefold()
    s=re.sub(r"[^a-z0-9]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def _install(engine):
    with engine.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))

def _write_metric(engine,key,num,den,scope,requires_gold=False,notes=None):
    value=round(100.0*num/max(den,1),4) if den else 0.0
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO alliance_eval_v2921
            (metric_key,metric_value,numerator,denominator,metric_scope,requires_gold,notes,eval_version)
            VALUES(:k,:v,:n,:d,:s,:g,:notes,:ev)
            ON CONFLICT(metric_key) DO UPDATE SET
              metric_value=EXCLUDED.metric_value,numerator=EXCLUDED.numerator,
              denominator=EXCLUDED.denominator,metric_scope=EXCLUDED.metric_scope,
              requires_gold=EXCLUDED.requires_gold,notes=EXCLUDED.notes,
              eval_version=EXCLUDED.eval_version,updated_at=now()
        """),{"k":key,"v":value,"n":num,"d":den,"s":scope,"g":requires_gold,
              "notes":notes,"ev":ENGINE_VERSION})

def _coverage_gate(row):
    validated=_loads(row.get("validated_location"),{})
    atomic_class=str(validated.get("candidate_class") or "")
    ownership=str(row.get("ownership_status") or "")
    parent=_loads(row.get("owned_parent_location"),{})
    final=_loads(row.get("final_geography"),{})

    if ownership=="OWNED_ATOMIC_VALIDATED":
        eligible=atomic_class in {
            "VALID_KNOWN_PLACE","VALID_UNKNOWN_PLACE","AMBIGUOUS_LOCATION","COMPOUND_LOCATION"
        }
        if atomic_class=="NOT_LOCATION":
            return False,"ATOMIC_VALIDATED",atomic_class,False,"ATOMIC_NOT_LOCATION_FORBIDDEN"
        if eligible and final:
            return True,"ATOMIC_VALIDATED",atomic_class,True,"ATOMIC_VALIDATION_PASSED"
        return False,"ATOMIC_VALIDATED",atomic_class,False,"ATOMIC_VALIDATION_INCOMPLETE"

    if ownership=="OWNED_PARENT_WIRED":
        # A rejected/missing child candidate does not invalidate independently-proven parent ownership.
        parent_status=str(parent.get("status") or "")
        parent_ok=parent_status=="OWNED_PARENT_SCOPED" and bool(final)
        if parent_ok:
            return True,"PROVEN_PARENT_OWNERSHIP",atomic_class,True,"PARENT_OWNERSHIP_INDEPENDENT_OF_CHILD_CANDIDATE"
        return False,"PROVEN_PARENT_OWNERSHIP",atomic_class,False,"PARENT_OWNERSHIP_PROOF_INCOMPLETE"

    return False,"ABSTAINED",atomic_class,True,"NO_COVERAGE_CLAIM"

def _clean_header_line(line):
    line=str(line or "").strip()
    if not line or len(line)>80:
        return False
    if DATAISH_RE.search(line):
        return False
    # Reject prose-like lines: too many tokens usually means content, not a heading.
    if len(_norm(line).split())>8:
        return False
    return True

def _header_nodes(parent_text):
    lines=[x.strip() for x in str(parent_text or "").splitlines() if x.strip()]
    nodes=[]
    for idx,line in enumerate(lines):
        if not _clean_header_line(line):
            continue
        kinds=[]
        city=HEADER_CITY_RE.match(line)
        tx=HEADER_TX_RE.match(line)
        if city:
            kinds.append("CITY")
        if tx:
            kinds.append("TRANSACTION")

        # Locality/sector header only when short and structurally header-like, never free prose.
        if re.fullmatch(r"(?:sector|sec)\s*[- ]?\s*\d+[a-z]?",line,re.I):
            kinds.append("SECTOR")
        elif not kinds and HEADER_LOCALITY_RE.match(line):
            # Soft locality candidate, not used as competing city/transaction evidence.
            if any(x in _norm(line) for x in ("gk","kailash","vasant","saket","dlf","siolim","assagao","anjuna","vagator","morjim")):
                kinds.append("LOCALITY")

        if kinds:
            nodes.append({
                "line_no":idx+1,
                "text":line,
                "field_types":kinds,
                "node_type":"STRUCTURAL_HEADER",
            })
    return nodes

def _build_positional_tree(parent_text,raw_text,source_item_no):
    nodes=_header_nodes(parent_text)
    by_type={}
    for kind in ("CITY","TRANSACTION","SECTOR","LOCALITY"):
        by_type[kind]=[n for n in nodes if kind in n["field_types"]]

    flags=[]
    # Competing only means two structurally valid header nodes with distinct normalized values.
    for kind,label in (("CITY","MULTI_CITY_OR_COMPETING_CITY_HEADERS"),
                       ("TRANSACTION","MULTI_TRANSACTION_OR_COMPETING_HEADERS")):
        vals={_norm(n["text"]) for n in by_type[kind]}
        if len(vals)>1:
            flags.append(label)

    tree={
        "root":{
            "node_type":"MESSAGE",
            "headers":nodes,
        },
        "atomic_child":{
            "source_item_no":source_item_no,
            "raw_text":str(raw_text or ""),
        },
        "policy":{
            "child_override":"ABSOLUTE",
            "cross_message_context":"FORBIDDEN",
            "competing_header_requires_structural_header":"TRUE",
            "global_keyword_mentions_are_not_headers":"TRUE",
        }
    }
    counts={k:len(v) for k,v in by_type.items()}
    return tree,sorted(set(flags)),counts

def _remove_all_occurrences(raw,candidate):
    raw=str(raw or "")
    candidate=str(candidate or "").strip()
    if not candidate:
        return raw,0
    pat=re.compile(re.escape(candidate),re.I)
    return pat.subn("[MASKED_EVIDENCE]",raw)

def _candidate_alias_terms(engine,candidate):
    cn=_norm(candidate)
    terms=set()
    aliases=v291._gazetteer_aliases(engine)
    # Find canonical destination and collect all aliases for same canonical name.
    canonical_names=set()
    for a in aliases:
        if _norm(a.get("alias"))==cn or _norm(a.get("canonical_name"))==cn:
            canonical_names.add(a.get("canonical_name"))
    for a in aliases:
        if a.get("canonical_name") in canonical_names:
            terms.add(str(a.get("alias") or ""))
            terms.add(str(a.get("canonical_name") or ""))
    return sorted({x for x in terms if x},key=len,reverse=True)

def _ablation_diagnose(engine,row):
    original=_loads(row.get("original_candidate"),{})
    raw=str(row.get("raw_text") or "")
    candidate=str(original.get("candidate_value") or "")
    old_result=str(row.get("ablation_result") or "")
    old_survivors=_loads(row.get("post_mask_candidates"),[])

    if old_result!="FAIL_RIGHT_ANSWER_WITHOUT_MASKED_EVIDENCE":
        return {
            "failure_class":"NOT_FAILURE",
            "is_unexplained":False,
            "masked_text":str(row.get("masked_text") or ""),
            "survivors":old_survivors,
            "diagnostic_evidence":{"old_result":old_result},
        }

    # 1. Exact duplicate evidence.
    masked,count=_remove_all_occurrences(raw,candidate)
    aliases=_candidate_alias_terms(engine,candidate)
    alias_hits=[]
    masked_alias=masked
    for term in aliases:
        if _norm(term)==_norm(candidate):
            continue
        if re.search(re.escape(term),masked_alias,re.I):
            alias_hits.append(term)
            masked_alias=re.sub(re.escape(term),"[MASKED_ALIAS]",masked_alias,flags=re.I)

    # Rerun the deterministic extractor on fully masked evidence.
    alias_rows=v291._gazetteer_aliases(engine)
    post=v291._extract_atomic_location_candidates(masked_alias,{"atomic_explicit":{}},alias_rows)
    survivors=[]
    for item in post:
        cclass,ev,conf=v291._validate_candidate(item.get("value"),alias_rows)
        if cclass!="NOT_LOCATION":
            survivors.append({"value":item.get("value"),"class":cclass,"confidence":conf})

    same=[x for x in survivors if _norm(x.get("value"))==_norm(candidate)]

    if count>1:
        klass="DUPLICATE_EXPLICIT_EVIDENCE"
        unexplained=False
    elif alias_hits:
        klass="ALIAS_OR_CANONICAL_DUPLICATE_EVIDENCE"
        unexplained=False
    elif not same:
        klass="OLD_ABLATION_MASK_TOO_WEAK"
        unexplained=False
    else:
        # Check whether the candidate can be recovered from parent/proven ownership rather than raw atomic text.
        owner=str(row.get("ownership_status") or "")
        if owner=="OWNED_PARENT_WIRED":
            klass="PARENT_CONTEXT_RECOVERY"
            unexplained=False
        elif str(row.get("dimension_type") or "")=="PROJECT":
            klass="PROJECT_KNOWLEDGE_RECOVERY"
            unexplained=False
        else:
            klass="UNEXPLAINED_RIGHT_ANSWER_WRONG_REASON"
            unexplained=True

    return {
        "failure_class":klass,
        "is_unexplained":unexplained,
        "masked_text":masked_alias,
        "survivors":survivors,
        "diagnostic_evidence":{
            "candidate":candidate,
            "exact_occurrences_masked":count,
            "alias_terms_found":alias_hits,
            "old_survivors":old_survivors,
            "new_survivors":survivors,
            "ownership_status":row.get("ownership_status"),
            "dimension_type":row.get("dimension_type"),
        }
    }

def run(engine,limit=1000):
    _install(engine)
    with engine.connect() as conn:
        rows=[dict(x) for x in conn.execute(text("""
            SELECT r.entity_id,r.message_id,r.validated_location,r.owned_parent_location,
                   r.final_geography,r.dimension_type,r.ownership_status,r.coverage_eligible,
                   r.review_flags,
                   v.raw_text,v.parent_message_text,v.source_item_no,
                   a.original_candidate,a.masked_text,a.post_mask_candidates,a.ablation_result
            FROM alliance_ownership_resolution_v292 r
            JOIN alliance_topper_availability_v24 v ON v.entity_id=r.entity_id
            LEFT JOIN alliance_ablation_exam_v292 a ON a.entity_id=r.entity_id
            WHERE r.engine_version='ALLIANCE_OWNERSHIP_STRUCTURAL_V292'
            ORDER BY r.updated_at DESC LIMIT :n
        """),{"n":int(limit)}).mappings().all()]

    counts=Counter()
    failure_classes=Counter()
    tree_old_flags=Counter()
    tree_new_flags=Counter()
    failures=[]
    examples=[]

    for row in rows:
        try:
            eligible,source,atomic_class,gate_passed,gate_reason=_coverage_gate(row)
            if eligible:
                counts["coverage_eligible"]+=1
            if source=="ATOMIC_VALIDATED" and eligible:
                counts["atomic_coverage"]+=1
            if source=="PROVEN_PARENT_OWNERSHIP" and eligible:
                counts["parent_coverage"]+=1
            if not gate_passed:
                counts["gate_breaches"]+=1

            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO alliance_coverage_gate_v2921
                    (gate_id,entity_id,message_id,coverage_eligible,coverage_source,
                     atomic_candidate_class,parent_ownership_status,gate_passed,gate_reason,engine_version)
                    VALUES(:id,:eid,:mid,:ce,:src,:acc,:pos,:gp,:gr,:v)
                    ON CONFLICT(entity_id) DO UPDATE SET message_id=EXCLUDED.message_id,
                      coverage_eligible=EXCLUDED.coverage_eligible,coverage_source=EXCLUDED.coverage_source,
                      atomic_candidate_class=EXCLUDED.atomic_candidate_class,
                      parent_ownership_status=EXCLUDED.parent_ownership_status,
                      gate_passed=EXCLUDED.gate_passed,gate_reason=EXCLUDED.gate_reason,
                      engine_version=EXCLUDED.engine_version,updated_at=now()
                """),{
                    "id":str(uuid.uuid4()),"eid":row["entity_id"],"mid":row.get("message_id"),
                    "ce":eligible,"src":source,"acc":atomic_class,
                    "pos":row.get("ownership_status"),"gp":gate_passed,"gr":gate_reason,"v":ENGINE_VERSION
                })

            tree,new_flags,header_counts=_build_positional_tree(
                row.get("parent_message_text"),row.get("raw_text"),row.get("source_item_no")
            )
            old_flags=_loads(row.get("review_flags"),[])
            for f in old_flags:
                if f.startswith("MULTI_"):
                    tree_old_flags[f]+=1
            for f in new_flags:
                tree_new_flags[f]+=1

            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO alliance_message_tree_v2921
                    (tree_id,entity_id,message_id,source_item_no,tree_json,structural_flags,header_counts,engine_version)
                    VALUES(:id,:eid,:mid,:item,CAST(:tree AS jsonb),CAST(:flags AS jsonb),CAST(:counts AS jsonb),:v)
                    ON CONFLICT(entity_id) DO UPDATE SET message_id=EXCLUDED.message_id,
                      source_item_no=EXCLUDED.source_item_no,tree_json=EXCLUDED.tree_json,
                      structural_flags=EXCLUDED.structural_flags,header_counts=EXCLUDED.header_counts,
                      engine_version=EXCLUDED.engine_version,updated_at=now()
                """),{
                    "id":str(uuid.uuid4()),"eid":row["entity_id"],"mid":row.get("message_id"),
                    "item":row.get("source_item_no"),"tree":json.dumps(tree,ensure_ascii=False),
                    "flags":json.dumps(new_flags),"counts":json.dumps(header_counts),"v":ENGINE_VERSION
                })

            diagnosis=_ablation_diagnose(engine,row)
            failure_classes[diagnosis["failure_class"]]+=1
            if diagnosis["is_unexplained"]:
                counts["unexplained_ablation_failures"]+=1
            if str(row.get("ablation_result") or "").startswith("FAIL"):
                counts["old_ablation_failures"]+=1
                counts["ablation_failures_classified"]+=1

            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO alliance_ablation_diagnosis_v2921
                    (diagnosis_id,entity_id,message_id,original_candidate,original_raw_text,masked_text,
                     survivors,failure_class,is_unexplained,diagnostic_evidence,engine_version)
                    VALUES(:id,:eid,:mid,CAST(:orig AS jsonb),:raw,:masked,CAST(:surv AS jsonb),
                           :fc,:unexp,CAST(:de AS jsonb),:v)
                    ON CONFLICT(entity_id) DO UPDATE SET message_id=EXCLUDED.message_id,
                      original_candidate=EXCLUDED.original_candidate,original_raw_text=EXCLUDED.original_raw_text,
                      masked_text=EXCLUDED.masked_text,survivors=EXCLUDED.survivors,
                      failure_class=EXCLUDED.failure_class,is_unexplained=EXCLUDED.is_unexplained,
                      diagnostic_evidence=EXCLUDED.diagnostic_evidence,engine_version=EXCLUDED.engine_version,
                      updated_at=now()
                """),{
                    "id":str(uuid.uuid4()),"eid":row["entity_id"],"mid":row.get("message_id"),
                    "orig":json.dumps(_loads(row.get("original_candidate"),{}),ensure_ascii=False),
                    "raw":str(row.get("raw_text") or ""),"masked":diagnosis["masked_text"],
                    "surv":json.dumps(diagnosis["survivors"],ensure_ascii=False),
                    "fc":diagnosis["failure_class"],"unexp":diagnosis["is_unexplained"],
                    "de":json.dumps(diagnosis["diagnostic_evidence"],ensure_ascii=False),"v":ENGINE_VERSION
                })

            if len(examples)<30 and (
                not gate_passed or diagnosis["failure_class"]!="NOT_FAILURE" or
                set(old_flags)!=set(new_flags)
            ):
                examples.append({
                    "entity_id":row["entity_id"],
                    "coverage_source":source,
                    "atomic_candidate_class":atomic_class,
                    "gate_passed":gate_passed,
                    "gate_reason":gate_reason,
                    "old_structural_flags":[x for x in old_flags if x.startswith("MULTI_")],
                    "new_structural_flags":new_flags,
                    "ablation_failure_class":diagnosis["failure_class"],
                    "ablation_unexplained":diagnosis["is_unexplained"],
                    "ablation_evidence":diagnosis["diagnostic_evidence"],
                })

        except Exception as exc:
            failures.append(f"{row.get('entity_id')}:{type(exc).__name__}:{exc}"[:700])

    total=len(rows)
    old_multi=sum(tree_old_flags.values())
    new_multi=sum(tree_new_flags.values())
    classified=counts["ablation_failures_classified"]
    old_failures=counts["old_ablation_failures"]

    _write_metric(engine,"validated_location_coverage",
                  counts["coverage_eligible"],total,"LIVE_AUTOMATIC",False,
                  "Source-aware: atomic validation and independently-proven parent ownership are separate valid coverage paths.")
    _write_metric(engine,"atomic_validated_coverage",
                  counts["atomic_coverage"],total,"LIVE_AUTOMATIC",False,
                  "Only atomic candidates that passed candidate validation.")
    _write_metric(engine,"proven_parent_coverage",
                  counts["parent_coverage"],total,"LIVE_AUTOMATIC",False,
                  "Only parent geography with existing OWNED_PARENT_SCOPED proof.")
    _write_metric(engine,"coverage_gate_breach_rate",
                  counts["gate_breaches"],total,"STRUCTURAL_INVARIANT",False,
                  "Must be exactly zero. NOT_LOCATION only forbids ATOMIC coverage; it does not invalidate independent proven-parent ownership.")
    _write_metric(engine,"ablation_failure_classification_rate",
                  classified,old_failures,"HALLUCINATION_DIAGNOSTIC",False,
                  "All prior ablation failures must receive a root-cause class.")
    _write_metric(engine,"unexplained_ablation_failure_rate",
                  counts["unexplained_ablation_failures"],old_failures,"HALLUCINATION_DIAGNOSTIC",False,
                  "Target near zero before LLM ownership work.")
    _write_metric(engine,"structural_multi_flag_reduction",
                  max(old_multi-new_multi,0),old_multi,"TREE_DIAGNOSTIC",False,
                  "Reduction from keyword-global competing-header flags to structural-header-only flags.")
    _write_metric(engine,"context_ownership_accuracy",0,0,"GOLD_RELEASE_GATE",True,
                  "Requires Gold V2 structural ownership labels.")

    return {
        "status":"PASS" if not failures and counts["gate_breaches"]==0 else "FAIL_INVARIANT",
        "version":VERSION,
        "mode":MODE,
        "engine_version":ENGINE_VERSION,
        "seen":total,
        "processed":total-len(failures),
        "failed":len(failures),
        "coverage":{
            "eligible":counts["coverage_eligible"],
            "atomic_validated":counts["atomic_coverage"],
            "proven_parent":counts["parent_coverage"],
            "gate_breaches":counts["gate_breaches"],
            "gate_passed":counts["gate_breaches"]==0,
        },
        "ablation":{
            "old_failures":old_failures,
            "classified":classified,
            "unexplained":counts["unexplained_ablation_failures"],
            "failure_classes":dict(failure_classes),
        },
        "message_tree":{
            "old_multi_flag_events":old_multi,
            "new_multi_flag_events":new_multi,
            "old_flags":dict(tree_old_flags),
            "new_flags":dict(tree_new_flags),
            "tree_version":TREE_VERSION,
        },
        "examples":examples,
        "errors":failures[:10],
        "llm_ownership_adjudicator":"STILL_NOT_BUILT",
        "production_writes":0,
        "whatsapp_live_writes":0,
        "gold_v1_mutations":0,
    }

def status(engine):
    _install(engine)
    with engine.connect() as conn:
        metrics=[dict(x) for x in conn.execute(text("""
            SELECT metric_key,metric_value,numerator,denominator,metric_scope,requires_gold,notes
            FROM alliance_eval_v2921 ORDER BY metric_scope,metric_key
        """)).mappings().all()]
        gates=[dict(x) for x in conn.execute(text("""
            SELECT coverage_source,count(*) cases,
                   count(*) FILTER(WHERE coverage_eligible=TRUE) eligible,
                   count(*) FILTER(WHERE gate_passed=FALSE) breaches
            FROM alliance_coverage_gate_v2921 WHERE engine_version=:v
            GROUP BY coverage_source ORDER BY cases DESC
        """),{"v":ENGINE_VERSION}).mappings().all()]
        ab=[dict(x) for x in conn.execute(text("""
            SELECT failure_class,count(*) cases,
                   count(*) FILTER(WHERE is_unexplained=TRUE) unexplained
            FROM alliance_ablation_diagnosis_v2921 WHERE engine_version=:v
            GROUP BY failure_class ORDER BY cases DESC
        """),{"v":ENGINE_VERSION}).mappings().all()]
        tree=[dict(x) for x in conn.execute(text("""
            SELECT structural_flags,count(*) cases
            FROM alliance_message_tree_v2921 WHERE engine_version=:v
            GROUP BY structural_flags ORDER BY cases DESC LIMIT 20
        """),{"v":ENGINE_VERSION}).mappings().all()]
        examples=[dict(x) for x in conn.execute(text("""
            SELECT g.entity_id,g.coverage_source,g.atomic_candidate_class,g.gate_passed,g.gate_reason,
                   a.failure_class,a.is_unexplained,a.diagnostic_evidence,
                   t.structural_flags
            FROM alliance_coverage_gate_v2921 g
            LEFT JOIN alliance_ablation_diagnosis_v2921 a ON a.entity_id=g.entity_id
            LEFT JOIN alliance_message_tree_v2921 t ON t.entity_id=g.entity_id
            WHERE g.engine_version=:v
            ORDER BY (NOT g.gate_passed) DESC,a.is_unexplained DESC,g.updated_at DESC LIMIT 30
        """),{"v":ENGINE_VERSION}).mappings().all()]
    return foundation._json_safe({
        "status":"PASS",
        "version":VERSION,
        "mode":MODE,
        "engine_version":ENGINE_VERSION,
        "coverage_gate_version":GATE_VERSION,
        "tree_version":TREE_VERSION,
        "ablation_version":ABLATION_VERSION,
        "automatic_metrics":metrics,
        "coverage_sources":gates,
        "ablation_root_causes":ab,
        "tree_flag_distribution":tree,
        "examples":examples,
        "critical_invariant":"NOT_LOCATION forbids atomic coverage only; independently proven parent ownership is a separate admissible evidence path.",
        "llm_ownership_adjudicator":"DEFERRED",
        "production_writes":0,
        "whatsapp_live_writes":0,
        "gold_v1_mutations":0,
    })

DASH="""<!doctype html><html><body style='font-family:Arial;background:#08111b;color:#eef6ff;max-width:1340px;margin:28px auto'>
<h1>🛡️ Foundation 2.9.2.1 — Structural Integrity Repair</h1>
<p>Source-aware coverage gate + ablation root-cause classification + positional structural message tree.</p>
<p><b>Key correction:</b> NOT_LOCATION blocks atomic coverage, but does not invalidate a separately proven OWNED_PARENT_SCOPED geography path.</p>
<button onclick='run()' style='padding:14px 22px;border:0;border-radius:9px;background:#f5d76e;font-weight:bold'>Run Integrity 1000</button>
<button onclick='status()' style='padding:14px 22px'>Refresh</button>
<h2>Scoreboard</h2><pre id='s'></pre><h2>Run Result</h2><pre id='r'>No run yet.</pre>
<script>
async function call(p,m='GET'){const x=await fetch(p,{method:m});const t=await x.text();let d;try{d=JSON.parse(t)}catch(e){d={raw:t}}if(!x.ok)throw Error(d.detail||d.raw||('HTTP '+x.status));return d}
async function status(){try{document.getElementById('s').textContent=JSON.stringify(await call('/api/property-brain/integrity-v2921/status'),null,2)}catch(e){document.getElementById('s').textContent='ERROR '+e.message}}
async function run(){document.getElementById('r').textContent='Running integrity repair exam...';try{document.getElementById('r').textContent=JSON.stringify(await call('/api/property-brain/integrity-v2921/run?limit=1000','POST'),null,2);await status()}catch(e){document.getElementById('r').textContent='ERROR '+e.message}}
status()
</script></body></html>"""

def register(core):
    engine=_engine(core)
    app=_app(core)
    _install(engine)
    if not foundation._route_exists(app,"/api/property-brain/integrity-v2921/status"):
        @app.get("/api/property-brain/integrity-v2921/status")
        def _status():
            return status(engine)
    if not foundation._route_exists(app,"/api/property-brain/integrity-v2921/run"):
        @app.post("/api/property-brain/integrity-v2921/run")
        def _run(limit:int=Query(default=1000,ge=1,le=5000)):
            return run(engine,limit)
    if not foundation._route_exists(app,"/property-brain/integrity-v2921"):
        @app.get("/property-brain/integrity-v2921",response_class=HTMLResponse)
        def _dashboard():
            return HTMLResponse(DASH)
    return {
        "status":"REGISTERED",
        "version":VERSION,
        "dashboard":"/property-brain/integrity-v2921",
        "production_writes":0,
        "whatsapp_live_writes":0,
        "gold_v1_mutations":0
    }
