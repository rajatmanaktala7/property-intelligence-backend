from __future__ import annotations

import json
import re
import unicodedata
import uuid
from collections import Counter, defaultdict

from fastapi import Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_infrastructure_curriculum_v291 as v291

VERSION = "2.9.2-OWNERSHIP-WIRING-STRUCTURAL-RESOLUTION"
MODE = "PROVEN_PARENT_WIRING_COVERAGE_GATE_MESSAGE_TREE_DIMENSION_TYPED_GEOGRAPHY_ABLATION"
ENGINE_VERSION = "ALLIANCE_OWNERSHIP_STRUCTURAL_V292"
TREE_VERSION = "ALLIANCE_MESSAGE_TREE_V1"
COVERAGE_GATE_VERSION = "ALLIANCE_VALIDATED_COVERAGE_GATE_V1"
ONTOLOGY_VERSION = "ALLIANCE_GEOGRAPHY_DIMENSIONS_V1"
ABLATION_VERSION = "ALLIANCE_EVIDENCE_ABLATION_V1"
PROJECT_GAZETTEER_VERSION = "ALLIANCE_PROJECT_GAZETTEER_V1"

DDL = [
"""CREATE TABLE IF NOT EXISTS alliance_message_tree_v292(
tree_id UUID PRIMARY KEY,
entity_id TEXT NOT NULL UNIQUE,
message_id TEXT,
source_item_no INTEGER,
tree_json JSONB NOT NULL DEFAULT '{}'::jsonb,
field_tracks JSONB NOT NULL DEFAULT '{}'::jsonb,
tree_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
tree_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_ownership_resolution_v292(
resolution_id UUID PRIMARY KEY,
entity_id TEXT NOT NULL UNIQUE,
message_id TEXT,
validated_location JSONB NOT NULL DEFAULT '{}'::jsonb,
owned_parent_location JSONB NOT NULL DEFAULT '{}'::jsonb,
final_geography JSONB NOT NULL DEFAULT '{}'::jsonb,
dimension_type TEXT,
ownership_status TEXT NOT NULL,
ownership_reason TEXT,
coverage_eligible BOOLEAN NOT NULL DEFAULT FALSE,
coverage_gate_reason TEXT,
review_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
engine_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_geography_dimension_v292(
dimension_id UUID PRIMARY KEY,
entity_id TEXT NOT NULL,
message_id TEXT,
raw_value TEXT,
dimension_type TEXT NOT NULL,
relative_qualifier TEXT,
canonical_value TEXT,
city TEXT,state TEXT,country TEXT,
quality TEXT,
provenance TEXT,
confidence NUMERIC(5,2),
engine_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_project_gazetteer_v292(
project_norm TEXT PRIMARY KEY,
project_name TEXT NOT NULL,
locality TEXT,
city TEXT,
state TEXT,
country TEXT NOT NULL DEFAULT 'India',
approved BOOLEAN NOT NULL DEFAULT TRUE,
source TEXT NOT NULL,
version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_ablation_exam_v292(
ablation_id UUID PRIMARY KEY,
entity_id TEXT NOT NULL UNIQUE,
message_id TEXT,
original_candidate JSONB NOT NULL DEFAULT '{}'::jsonb,
masked_text TEXT,
post_mask_candidates JSONB NOT NULL DEFAULT '[]'::jsonb,
ablation_result TEXT NOT NULL,
notes JSONB NOT NULL DEFAULT '[]'::jsonb,
exam_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_eval_v292(
metric_key TEXT PRIMARY KEY,
metric_value NUMERIC(10,4),
numerator INTEGER NOT NULL DEFAULT 0,
denominator INTEGER NOT NULL DEFAULT 0,
metric_scope TEXT NOT NULL,
requires_gold BOOLEAN NOT NULL DEFAULT FALSE,
notes TEXT,
eval_version TEXT NOT NULL,
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
]

PROJECT_SEEDS = [
    ("DLF Phase 3", "DLF Phase 3", None, "Gurugram", "Haryana", "MANUAL_APPROVED_CORE"),
    ("Central Park Flower Valley", "Central Park Flower Valley", None, "Gurugram", "Haryana", "MANUAL_APPROVED_CORE"),
]

HEADER_PATTERNS = {
    "transaction": re.compile(r"\b(for\s+sale|sale|rent|rental|for\s+rent|lease|for\s+lease)\b", re.I),
    "city": re.compile(r"\b(delhi|new\s+delhi|gurgaon|gurugram|noida|greater\s+noida|faridabad|ghaziabad|goa|panaji|panjim|mumbai|jaipur)\b", re.I),
    "sector": re.compile(r"\b(?:sector|sec)\s*[- ]?\s*\d+[a-z]?\b", re.I),
}

RELATIVE_RE = re.compile(r"\b(near|close\s+to|opposite|behind|beside|next\s+to|adjacent\s+to)\b", re.I)
LANDMARK_WORDS = re.compile(r"\b(mandir|temple|school|hospital|metro|station|mall|market|airport|church|mosque|gurudwara|club)\b", re.I)
ROAD_WORDS = re.compile(r"\b(road|rd|marg|street|lane|highway|expressway)\b", re.I)
PROJECT_WORDS = re.compile(r"\b(phase|tower|residency|residences|heights|valley|park|greens|estate|society|enclave)\b", re.I)

def _engine(core):
    return foundation._engine_from_core(core)

def _app(core):
    return getattr(core, "app", None) or core

def _loads(v, default):
    if v is None:
        return default
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return default

def _norm(v):
    s = unicodedata.normalize("NFKC", str(v or "")).casefold()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def _install(engine):
    with engine.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))
        for project_name, canonical, locality, city, state, source in PROJECT_SEEDS:
            conn.execute(text("""
                INSERT INTO alliance_project_gazetteer_v292
                (project_norm,project_name,locality,city,state,country,approved,source,version)
                VALUES(:pn,:p,:loc,:city,:state,'India',TRUE,:source,:v)
                ON CONFLICT(project_norm) DO UPDATE SET
                  project_name=EXCLUDED.project_name,locality=EXCLUDED.locality,
                  city=EXCLUDED.city,state=EXCLUDED.state,approved=EXCLUDED.approved,
                  source=EXCLUDED.source,version=EXCLUDED.version,updated_at=now()
            """), {"pn":_norm(project_name),"p":canonical,"loc":locality,
                   "city":city,"state":state,"source":source,"v":PROJECT_GAZETTEER_VERSION})

def _dimension_type(value):
    raw = str(value or "").strip()
    n = _norm(raw)
    if not n:
        return "UNKNOWN", None
    rel = RELATIVE_RE.search(raw)
    if rel:
        return "LANDMARK_REFERENCE", rel.group(1).lower()
    if LANDMARK_WORDS.search(raw):
        return "LANDMARK_REFERENCE", None
    if re.search(r"\b(?:sector|sec)\s*[- ]?\s*\d+[a-z]?\b", raw, re.I):
        return "SECTOR", None
    if ROAD_WORDS.search(raw):
        return "ROAD", None
    if PROJECT_WORDS.search(raw):
        return "PROJECT", None
    if re.search(r"\b(delhi|gurgaon|gurugram|noida|faridabad|ghaziabad|mumbai|jaipur|panaji|panjim)\b", raw, re.I):
        return "CITY", None
    return "LOCALITY", None

def _build_tree(parent_text, raw_text, source_item_no):
    lines = [x.strip() for x in str(parent_text or "").splitlines() if x.strip()]
    nodes = []
    for idx, line in enumerate(lines):
        kinds = []
        for key, pat in HEADER_PATTERNS.items():
            if pat.search(line):
                kinds.append(key.upper())
        if len(line) <= 80 and kinds:
            nodes.append({"line_no":idx+1,"text":line,"node_type":"HEADER","field_types":kinds})
        elif len(line) <= 50 and not re.search(r"\b\d{7,}\b", line):
            # soft heading candidate retained but not trusted automatically
            nodes.append({"line_no":idx+1,"text":line,"node_type":"SOFT_HEADER","field_types":[]})

    field_tracks = {}
    for field in ("CITY","TRANSACTION","SECTOR"):
        vals = [n for n in nodes if field in n.get("field_types",[])]
        field_tracks[field] = vals

    flags = []
    city_headers = field_tracks["CITY"]
    if len(city_headers) > 1:
        unique = {_norm(x["text"]) for x in city_headers}
        if len(unique) > 1:
            flags.append("MULTI_CITY_OR_COMPETING_CITY_HEADERS")
    tx_headers = field_tracks["TRANSACTION"]
    if len(tx_headers) > 1:
        vals = {_norm(x["text"]) for x in tx_headers}
        if len(vals) > 1:
            flags.append("MULTI_TRANSACTION_OR_COMPETING_HEADERS")

    return {
        "root":{"node_type":"MESSAGE","children":nodes},
        "atomic_child":{"source_item_no":source_item_no,"raw_text":str(raw_text or "")},
        "ownership_model":"NEAREST_UNAMBIGUOUS_ANCESTOR_PER_FIELD_TYPE_CHILD_OVERRIDE_ALWAYS_WINS"
    }, field_tracks, sorted(set(flags))

def _project_lookup(engine, value):
    n = _norm(value)
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT project_name,locality,city,state,country,source,version
            FROM alliance_project_gazetteer_v292
            WHERE approved=TRUE AND (
                project_norm=:n OR :n LIKE '%'||project_norm||'%' OR project_norm LIKE '%'||:n||'%'
            )
            ORDER BY length(project_norm) DESC LIMIT 1
        """), {"n":n}).mappings().first()
    return dict(row) if row else {}

def _validated_v291(row):
    cclass = str(row.get("candidate_class") or "")
    resolved = _loads(row.get("resolved_geography"), {})
    value = row.get("candidate_value")
    eligible_classes = {"VALID_KNOWN_PLACE","VALID_UNKNOWN_PLACE","AMBIGUOUS_LOCATION","COMPOUND_LOCATION"}
    eligible = bool(value and cclass in eligible_classes)
    reason = "PASSED_V291_CANDIDATE_VALIDATION" if eligible else "FAILED_OR_MISSING_CANDIDATE_VALIDATION"
    return {
        "candidate_value":value,
        "candidate_class":cclass,
        "resolved_geography":resolved,
        "validation_evidence":_loads(row.get("validation_evidence"), {}),
        "confidence":float(row.get("candidate_confidence") or 0),
        "eligible":eligible,
        "reason":reason,
    }

def _owned_parent_location(owned_fields):
    candidates = []
    for field in ("locality","city"):
        item = owned_fields.get(field) or {}
        if item.get("status") == "OWNED_PARENT_SCOPED":
            vals = [str(x).strip() for x in (item.get("values") or []) if str(x or "").strip()]
            if len(set(v.casefold() for v in vals)) == 1:
                candidates.append({
                    "field":field,
                    "value":vals[0],
                    "status":"OWNED_PARENT_SCOPED",
                    "scope_reason":item.get("scope_reason"),
                    "evidence":item.get("evidence"),
                })
    # Prefer locality over city because it is more specific.
    candidates.sort(key=lambda x:0 if x["field"]=="locality" else 1)
    return candidates[0] if candidates else {}

def _resolve_parent_candidate(engine, parent_candidate):
    if not parent_candidate:
        return {}
    value = parent_candidate["value"]

    aliases = v291._gazetteer_aliases(engine)
    resolved = v291._resolve_known(value, aliases)
    if resolved:
        resolved = dict(resolved)
        resolved["quality"] = "SUPPORTED_PARENT"
        resolved["provenance"] = "OWNED_PARENT_LOCATION_V25"
        resolved["ownership_scope_reason"] = parent_candidate.get("scope_reason")
        return resolved

    dtype, rel = _dimension_type(value)
    if dtype == "PROJECT":
        project = _project_lookup(engine, value)
        if project:
            return {
                "literal_location":value,
                "canonical_name":project["project_name"],
                "place_type":"PROJECT",
                "locality":project.get("locality"),
                "city":project.get("city"),
                "state":project.get("state"),
                "country":project.get("country"),
                "quality":"SUPPORTED_PARENT",
                "provenance":"OWNED_PARENT_PLUS_PROJECT_GAZETTEER",
                "confidence":95,
                "project_source":project.get("source"),
            }

    return {
        "literal_location":value,
        "canonical_name":None,
        "place_type":dtype,
        "quality":"SUPPORTED_PARENT",
        "provenance":"OWNED_PARENT_UNNORMALIZED",
        "confidence":85,
        "ownership_scope_reason":parent_candidate.get("scope_reason"),
    }

def _choose_final(validated, parent_resolved):
    # Child explicit/validated always wins over parent.
    if validated.get("eligible"):
        child = dict(validated.get("resolved_geography") or {})
        if not child:
            child = {
                "literal_location":validated.get("candidate_value"),
                "canonical_name":None,
                "quality":"EXPLICIT_ATOMIC",
                "provenance":"VALIDATED_ATOMIC_UNRESOLVED",
                "confidence":validated.get("confidence"),
            }
        return child, "OWNED_ATOMIC_VALIDATED", "CHILD_OVERRIDE_OR_ATOMIC_FIRST", True, "VALIDATED_ATOMIC_GATE"

    if parent_resolved:
        return parent_resolved, "OWNED_PARENT_WIRED", "ALREADY_PROVEN_V25_PARENT_OWNERSHIP", True, "VALIDATED_OWNERSHIP_GATE"

    return {}, "UNRESOLVED", "NO_VALIDATED_ATOMIC_OR_PROVEN_PARENT_LOCATION", False, "ABSTAINED"

def _mask_once(text_value, evidence):
    text_value = str(text_value or "")
    evidence = str(evidence or "").strip()
    if not evidence:
        return text_value
    pattern = re.compile(re.escape(evidence), re.I)
    return pattern.sub("[MASKED_EVIDENCE]", text_value, count=1)

def _ablation_exam(engine, raw_text, validated):
    if not validated.get("eligible"):
        return "NOT_APPLICABLE", "", [], ["No validated atomic location candidate."]
    candidate = validated.get("candidate_value")
    if not candidate:
        return "NOT_APPLICABLE", "", [], ["No candidate value."]
    masked = _mask_once(raw_text, candidate)
    aliases = v291._gazetteer_aliases(engine)
    profile = {"atomic_explicit":{}}
    post = v291._extract_atomic_location_candidates(masked, profile, aliases)
    survivors = []
    for item in post:
        cclass, ev, conf = v291._validate_candidate(item.get("value"), aliases)
        if cclass != "NOT_LOCATION":
            survivors.append({"value":item.get("value"),"class":cclass,"confidence":conf})
    if any(_norm(x.get("value")) == _norm(candidate) for x in survivors):
        return "FAIL_RIGHT_ANSWER_WITHOUT_MASKED_EVIDENCE", masked, survivors, ["Original candidate survived its own evidence ablation."]
    return "PASS_ABSTAINS_OR_CHANGES_AFTER_EVIDENCE_REMOVAL", masked, survivors, []

def _write_metric(engine, key, num, den, scope, requires_gold=False, notes=None):
    value = round(100.0*num/max(den,1),4) if den else 0.0
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO alliance_eval_v292(metric_key,metric_value,numerator,denominator,metric_scope,requires_gold,notes,eval_version)
            VALUES(:k,:v,:n,:d,:s,:g,:notes,:ev)
            ON CONFLICT(metric_key) DO UPDATE SET metric_value=EXCLUDED.metric_value,
             numerator=EXCLUDED.numerator,denominator=EXCLUDED.denominator,metric_scope=EXCLUDED.metric_scope,
             requires_gold=EXCLUDED.requires_gold,notes=EXCLUDED.notes,eval_version=EXCLUDED.eval_version,updated_at=now()
        """), {"k":key,"v":value,"n":num,"d":den,"s":scope,"g":requires_gold,"notes":notes,"ev":ENGINE_VERSION})

def _queue_structural_gold(engine, row, category, priority, reason, payload):
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO alliance_gold_v2_candidate_queue
            (candidate_id,entity_id,message_id,category,priority_score,reason,payload,status,source_version)
            VALUES(:id,:eid,:mid,:cat,:p,:reason,CAST(:payload AS jsonb),'OPEN',:v)
            ON CONFLICT(entity_id,category,source_version) DO UPDATE SET
              priority_score=GREATEST(alliance_gold_v2_candidate_queue.priority_score,EXCLUDED.priority_score),
              reason=EXCLUDED.reason,payload=EXCLUDED.payload
        """), {
            "id":str(uuid.uuid4()),"eid":row["entity_id"],"mid":row.get("message_id"),
            "cat":category,"p":priority,"reason":reason,
            "payload":json.dumps(foundation._json_safe(payload),ensure_ascii=False),
            "v":ENGINE_VERSION
        })

def run(engine, limit=1000):
    _install(engine)

    with engine.connect() as conn:
        rows = [dict(x) for x in conn.execute(text("""
            SELECT v.entity_id,v.message_id,v.source_item_no,v.raw_text,v.parent_message_text,
                   o.owned_fields,o.rejected_inheritance,o.sibling_context,
                   d.diagnostic_class,
                   c.candidate_value,c.candidate_class,c.validation_evidence,
                   c.resolved_geography,c.provenance AS candidate_provenance,
                   c.confidence AS candidate_confidence,
                   m.source_class
            FROM alliance_topper_availability_v24 v
            LEFT JOIN alliance_context_ownership_v25 o ON o.entity_id=v.entity_id
            LEFT JOIN alliance_location_diagnostic_v291 d ON d.entity_id=v.entity_id
            LEFT JOIN alliance_location_candidate_validation_v291 c ON c.entity_id=v.entity_id
            LEFT JOIN alliance_magic_examiner_v26 m ON m.entity_id=v.entity_id
            WHERE v.extractor_version='ALLIANCE_AVAILABILITY_EXTRACTOR_V2'
            ORDER BY v.updated_at DESC LIMIT :n
        """), {"n":int(limit)}).mappings().all()]

    counts = Counter()
    ownership_dist = Counter()
    dimension_dist = Counter()
    ablation_dist = Counter()
    failures = []
    samples = []

    for row in rows:
        try:
            raw = str(row.get("raw_text") or "")
            parent_text = str(row.get("parent_message_text") or "")
            owned = _loads(row.get("owned_fields"), {})
            rejected = _loads(row.get("rejected_inheritance"), {})
            sibling = _loads(row.get("sibling_context"), {})

            tree, field_tracks, tree_flags = _build_tree(parent_text, raw, row.get("source_item_no"))
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO alliance_message_tree_v292
                    (tree_id,entity_id,message_id,source_item_no,tree_json,field_tracks,tree_flags,tree_version)
                    VALUES(:id,:eid,:mid,:item,CAST(:tree AS jsonb),CAST(:tracks AS jsonb),CAST(:flags AS jsonb),:v)
                    ON CONFLICT(entity_id) DO UPDATE SET message_id=EXCLUDED.message_id,
                      source_item_no=EXCLUDED.source_item_no,tree_json=EXCLUDED.tree_json,
                      field_tracks=EXCLUDED.field_tracks,tree_flags=EXCLUDED.tree_flags,
                      tree_version=EXCLUDED.tree_version,updated_at=now()
                """), {
                    "id":str(uuid.uuid4()),"eid":row["entity_id"],"mid":row.get("message_id"),
                    "item":row.get("source_item_no"),"tree":json.dumps(tree,ensure_ascii=False),
                    "tracks":json.dumps(field_tracks,ensure_ascii=False),
                    "flags":json.dumps(tree_flags),"v":TREE_VERSION
                })

            validated = _validated_v291(row)
            parent_candidate = _owned_parent_location(owned)
            parent_resolved = _resolve_parent_candidate(engine, parent_candidate)
            final, ownership_status, ownership_reason, coverage_eligible, gate_reason = _choose_final(validated, parent_resolved)

            dtype = None
            rel = None
            raw_for_type = final.get("literal_location") or final.get("canonical_name") or validated.get("candidate_value")
            if raw_for_type:
                dtype, rel = _dimension_type(raw_for_type)
                if dtype == "PROJECT":
                    project = _project_lookup(engine, raw_for_type)
                    if project:
                        final = dict(final)
                        final.update({
                            "project":project.get("project_name"),
                            "city":final.get("city") or project.get("city"),
                            "state":final.get("state") or project.get("state"),
                            "country":final.get("country") or project.get("country"),
                            "project_provenance":"PROJECT_GAZETTEER_V292"
                        })

            flags = []
            if row.get("diagnostic_class") == "OWNED_PARENT_LOCATION_AVAILABLE" and ownership_status != "OWNED_PARENT_WIRED" and not validated.get("eligible"):
                flags.append("PROVEN_PARENT_LOCATION_NOT_WIRED")
            if row.get("candidate_class") == "NOT_LOCATION" and coverage_eligible:
                flags.append("COVERAGE_GATE_BREACH")
            if dtype == "LANDMARK_REFERENCE":
                flags.append("LANDMARK_NOT_LOCALITY")
            if tree_flags:
                flags.extend(tree_flags)

            if coverage_eligible:
                counts["validated_location_coverage"] += 1
            if ownership_status == "OWNED_PARENT_WIRED":
                counts["wired_parent_locations"] += 1
            if ownership_status == "OWNED_ATOMIC_VALIDATED":
                counts["validated_atomic_locations"] += 1
            if final.get("city"):
                counts["city_resolved"] += 1
            if final:
                counts["geography_resolved_any"] += 1
            ownership_dist[ownership_status] += 1
            if dtype:
                dimension_dist[dtype] += 1

            if dtype and raw_for_type:
                with engine.begin() as conn:
                    conn.execute(text("""
                        INSERT INTO alliance_geography_dimension_v292
                        (dimension_id,entity_id,message_id,raw_value,dimension_type,relative_qualifier,
                         canonical_value,city,state,country,quality,provenance,confidence,engine_version)
                        VALUES(:id,:eid,:mid,:raw,:dtype,:rel,:canon,:city,:state,:country,:quality,:prov,:conf,:v)
                    """), {
                        "id":str(uuid.uuid4()),"eid":row["entity_id"],"mid":row.get("message_id"),
                        "raw":raw_for_type,"dtype":dtype,"rel":rel,
                        "canon":final.get("canonical_name") or final.get("literal_location"),
                        "city":final.get("city"),"state":final.get("state"),"country":final.get("country"),
                        "quality":final.get("quality"),"prov":final.get("provenance"),
                        "conf":float(final.get("confidence") or 0),"v":ENGINE_VERSION
                    })

            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO alliance_ownership_resolution_v292
                    (resolution_id,entity_id,message_id,validated_location,owned_parent_location,
                     final_geography,dimension_type,ownership_status,ownership_reason,
                     coverage_eligible,coverage_gate_reason,review_flags,engine_version)
                    VALUES(:id,:eid,:mid,CAST(:validated AS jsonb),CAST(:parent AS jsonb),CAST(:final AS jsonb),
                           :dtype,:os,:or,:ce,:gr,CAST(:flags AS jsonb),:v)
                    ON CONFLICT(entity_id) DO UPDATE SET message_id=EXCLUDED.message_id,
                      validated_location=EXCLUDED.validated_location,owned_parent_location=EXCLUDED.owned_parent_location,
                      final_geography=EXCLUDED.final_geography,dimension_type=EXCLUDED.dimension_type,
                      ownership_status=EXCLUDED.ownership_status,ownership_reason=EXCLUDED.ownership_reason,
                      coverage_eligible=EXCLUDED.coverage_eligible,coverage_gate_reason=EXCLUDED.coverage_gate_reason,
                      review_flags=EXCLUDED.review_flags,engine_version=EXCLUDED.engine_version,updated_at=now()
                """), {
                    "id":str(uuid.uuid4()),"eid":row["entity_id"],"mid":row.get("message_id"),
                    "validated":json.dumps(validated,ensure_ascii=False),
                    "parent":json.dumps(parent_candidate,ensure_ascii=False),
                    "final":json.dumps(final,ensure_ascii=False),
                    "dtype":dtype,"os":ownership_status,"or":ownership_reason,
                    "ce":coverage_eligible,"gr":gate_reason,
                    "flags":json.dumps(sorted(set(flags))),"v":ENGINE_VERSION
                })

            ab_res, masked, survivors, ab_notes = _ablation_exam(engine, raw, validated)
            ablation_dist[ab_res] += 1
            if ab_res.startswith("PASS"):
                counts["ablation_pass"] += 1
            if ab_res.startswith("FAIL"):
                counts["ablation_fail"] += 1
                _queue_structural_gold(engine,row,"ABLATION_FAILURE",100,
                    "Candidate survives supporting-evidence ablation; inspect right-answer-wrong-reason risk.",
                    {"validated":validated,"masked_text":masked,"survivors":survivors})
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO alliance_ablation_exam_v292
                    (ablation_id,entity_id,message_id,original_candidate,masked_text,
                     post_mask_candidates,ablation_result,notes,exam_version)
                    VALUES(:id,:eid,:mid,CAST(:orig AS jsonb),:masked,CAST(:post AS jsonb),:res,CAST(:notes AS jsonb),:v)
                    ON CONFLICT(entity_id) DO UPDATE SET message_id=EXCLUDED.message_id,
                      original_candidate=EXCLUDED.original_candidate,masked_text=EXCLUDED.masked_text,
                      post_mask_candidates=EXCLUDED.post_mask_candidates,ablation_result=EXCLUDED.ablation_result,
                      notes=EXCLUDED.notes,exam_version=EXCLUDED.exam_version,updated_at=now()
                """), {
                    "id":str(uuid.uuid4()),"eid":row["entity_id"],"mid":row.get("message_id"),
                    "orig":json.dumps(validated,ensure_ascii=False),"masked":masked,
                    "post":json.dumps(survivors,ensure_ascii=False),"res":ab_res,
                    "notes":json.dumps(ab_notes),"v":ABLATION_VERSION
                })

            # Structural Gold V2 curriculum.
            if row.get("diagnostic_class") in ("PARENT_KNOWN_PLACE_NOT_OWNED","PARENT_LOCATION_UNOWNED"):
                _queue_structural_gold(engine,row,"STRUCTURAL_OWNERSHIP",100,
                    "Parent geography exists but ownership remains unresolved; ideal Context Ownership Gold case.",
                    {"diagnostic_class":row.get("diagnostic_class"),"tree":tree,
                     "owned_fields":owned,"rejected_inheritance":rejected,"sibling_context":sibling})
            if "MULTI_CITY_OR_COMPETING_CITY_HEADERS" in tree_flags:
                _queue_structural_gold(engine,row,"MULTI_CITY_PARENT",100,
                    "Competing city headers require explicit ownership Gold.",
                    {"tree":tree,"field_tracks":field_tracks})
            if str(row.get("source_class") or "").upper() == "NOISE":
                _queue_structural_gold(engine,row,"UNDERREPRESENTED_NOISE",95,
                    "Noise example for Gold V2 balance.",{"raw_text":raw,"parent_message_text":parent_text})
            if dtype == "LANDMARK_REFERENCE":
                _queue_structural_gold(engine,row,"LANDMARK_RELATION",90,
                    "Landmark/relative-location example for geography ontology Gold.",
                    {"raw_value":raw_for_type,"relative_qualifier":rel,"final_geography":final})

            if len(samples) < 30 and (
                ownership_status == "OWNED_PARENT_WIRED" or flags or dtype in ("LANDMARK_REFERENCE","PROJECT","SECTOR")
            ):
                samples.append({
                    "entity_id":row["entity_id"],
                    "diagnostic_class":row.get("diagnostic_class"),
                    "ownership_status":ownership_status,
                    "ownership_reason":ownership_reason,
                    "coverage_eligible":coverage_eligible,
                    "dimension_type":dtype,
                    "final_geography":final,
                    "flags":sorted(set(flags)),
                    "ablation_result":ab_res,
                })

        except Exception as exc:
            failures.append(f"{row.get('entity_id')}:{type(exc).__name__}:{exc}"[:700])

    total = len(rows)
    validated_count = counts["validated_location_coverage"]

    # Permanent invariant: coverage derives only from saved rows where coverage_eligible=TRUE.
    with engine.connect() as conn:
        persisted = conn.execute(text("""
            SELECT count(*) FROM alliance_ownership_resolution_v292
            WHERE engine_version=:v AND coverage_eligible=TRUE
        """), {"v":ENGINE_VERSION}).scalar() or 0
        breaches = conn.execute(text("""
            SELECT count(*) FROM alliance_ownership_resolution_v292
            WHERE engine_version=:v AND coverage_eligible=TRUE
              AND (validated_location->>'candidate_class')='NOT_LOCATION'
        """), {"v":ENGINE_VERSION}).scalar() or 0

    counts["persisted_coverage_rows"] = int(persisted)
    counts["coverage_gate_breaches"] = int(breaches)

    _write_metric(engine,"validated_source_location_coverage",int(persisted),total,
                  "LIVE_AUTOMATIC",False,
                  "Counts only records downstream of atomic candidate validation OR proven parent ownership.")
    _write_metric(engine,"proven_parent_wiring_rate",counts["wired_parent_locations"],total,
                  "LIVE_AUTOMATIC",False,
                  "Previously proven parent location now consumed by structural resolution.")
    _write_metric(engine,"city_resolution_coverage",counts["city_resolved"],total,
                  "LIVE_AUTOMATIC",False,
                  "Resolved city from validated atomic or proven parent/project geography.")
    _write_metric(engine,"coverage_gate_breach_rate",int(breaches),total,
                  "STRUCTURAL_INVARIANT",False,
                  "Must remain exactly zero.")
    ab_den = counts["ablation_pass"] + counts["ablation_fail"]
    _write_metric(engine,"evidence_ablation_pass_rate",counts["ablation_pass"],ab_den,
                  "HALLUCINATION_CANARY",False,
                  "Validated atomic candidates should disappear/change when supporting evidence is removed.")
    _write_metric(engine,"context_ownership_grant_accuracy",0,0,
                  "GOLD_RELEASE_GATE",True,"Requires Gold V2 structural ownership labels.")
    _write_metric(engine,"context_ownership_deny_accuracy",0,0,
                  "GOLD_RELEASE_GATE",True,"Requires Gold V2 structural ownership labels.")

    return {
        "status":"PASS" if not failures and breaches==0 else ("PARTIAL" if failures else "FAIL_INVARIANT"),
        "version":VERSION,
        "mode":MODE,
        "engine_version":ENGINE_VERSION,
        "seen":total,
        "processed":total-len(failures),
        "failed":len(failures),
        "ownership_distribution":dict(ownership_dist),
        "dimension_distribution":dict(dimension_dist),
        "ablation_distribution":dict(ablation_dist),
        "automatic_counts":dict(counts),
        "coverage_gate_invariant":{
            "version":COVERAGE_GATE_VERSION,
            "persisted_eligible_rows":int(persisted),
            "not_location_counted_as_coverage":int(breaches),
            "passed":int(breaches)==0
        },
        "teaching_samples":samples,
        "errors":failures[:10],
        "llm_ownership_adjudicator":"NOT_BUILT_BY_DESIGN_DEFER_TO_2_9_3",
        "production_writes":0,
        "whatsapp_live_writes":0,
        "gold_v1_mutations":0
    }

def status(engine):
    _install(engine)
    with engine.connect() as conn:
        metrics = [dict(x) for x in conn.execute(text("""
            SELECT metric_key,metric_value,numerator,denominator,metric_scope,requires_gold,notes
            FROM alliance_eval_v292 ORDER BY metric_scope,metric_key
        """)).mappings().all()]
        ownership = [dict(x) for x in conn.execute(text("""
            SELECT ownership_status,count(*) cases
            FROM alliance_ownership_resolution_v292 WHERE engine_version=:v
            GROUP BY ownership_status ORDER BY cases DESC
        """),{"v":ENGINE_VERSION}).mappings().all()]
        dimensions = [dict(x) for x in conn.execute(text("""
            SELECT dimension_type,count(*) cases
            FROM alliance_geography_dimension_v292 WHERE engine_version=:v
            GROUP BY dimension_type ORDER BY cases DESC
        """),{"v":ENGINE_VERSION}).mappings().all()]
        ablation = [dict(x) for x in conn.execute(text("""
            SELECT ablation_result,count(*) cases
            FROM alliance_ablation_exam_v292 WHERE exam_version=:v
            GROUP BY ablation_result ORDER BY cases DESC
        """),{"v":ABLATION_VERSION}).mappings().all()]
        queue = [dict(x) for x in conn.execute(text("""
            SELECT category,count(*) cases,round(avg(priority_score),2) avg_priority
            FROM alliance_gold_v2_candidate_queue
            WHERE source_version=:v AND status='OPEN'
            GROUP BY category ORDER BY avg_priority DESC,cases DESC
        """),{"v":ENGINE_VERSION}).mappings().all()]
        examples = [dict(x) for x in conn.execute(text("""
            SELECT entity_id,ownership_status,dimension_type,coverage_eligible,
                   coverage_gate_reason,final_geography,review_flags
            FROM alliance_ownership_resolution_v292
            WHERE engine_version=:v
            ORDER BY updated_at DESC LIMIT 30
        """),{"v":ENGINE_VERSION}).mappings().all()]
    return foundation._json_safe({
        "status":"PASS",
        "version":VERSION,
        "mode":MODE,
        "engine_version":ENGINE_VERSION,
        "tree_version":TREE_VERSION,
        "coverage_gate_version":COVERAGE_GATE_VERSION,
        "ontology_version":ONTOLOGY_VERSION,
        "ablation_version":ABLATION_VERSION,
        "project_gazetteer_version":PROJECT_GAZETTEER_VERSION,
        "automatic_metrics":metrics,
        "ownership_distribution":ownership,
        "dimension_distribution":dimensions,
        "ablation_distribution":ablation,
        "gold_v2_structural_queue":queue,
        "examples":examples,
        "child_override_policy":"ABSOLUTE",
        "multi_city_parent_policy":"ABSTAIN_UNLESS_NEAREST_UNAMBIGUOUS_SUBHEADER_OR_EXISTING_PROVEN_OWNERSHIP",
        "sender_history_geography_policy":"FORBIDDEN",
        "legacy_database_geography_policy":"FORBIDDEN",
        "llm_ownership_adjudicator":"DEFERRED_TO_2_9_3_AFTER_GOLD_STRUCTURAL_BASELINE",
        "production_writes":0,
        "whatsapp_live_writes":0,
        "gold_v1_mutations":0
    })

DASH = """<!doctype html><html><body style='font-family:Arial;background:#08111b;color:#eef6ff;max-width:1340px;margin:28px auto'>
<h1>🧠 Foundation 2.9.2 — Ownership Wiring & Structural Resolution</h1>
<p>Proven parent wiring + permanent coverage gate + message tree + typed geography + evidence ablation.</p>
<p><b>LLM intentionally deferred.</b> This release establishes the deterministic ownership baseline first.</p>
<button onclick='run()' style='padding:14px 22px;border:0;border-radius:9px;background:#f5d76e;font-weight:bold'>Run Structural 1000</button>
<button onclick='status()' style='padding:14px 22px'>Refresh</button>
<h2>Scoreboard</h2><pre id='s'></pre>
<h2>Run Result</h2><pre id='r'>No run yet.</pre>
<script>
async function call(p,m='GET'){const x=await fetch(p,{method:m});const t=await x.text();let d;try{d=JSON.parse(t)}catch(e){d={raw:t}}if(!x.ok)throw Error(d.detail||d.raw||('HTTP '+x.status));return d}
async function status(){try{document.getElementById('s').textContent=JSON.stringify(await call('/api/property-brain/ownership-v292/status'),null,2)}catch(e){document.getElementById('s').textContent='ERROR '+e.message}}
async function run(){document.getElementById('r').textContent='Running structural resolution...';try{document.getElementById('r').textContent=JSON.stringify(await call('/api/property-brain/ownership-v292/run?limit=1000','POST'),null,2);await status()}catch(e){document.getElementById('r').textContent='ERROR '+e.message}}
status()
</script></body></html>"""

def register(core):
    engine = _engine(core)
    app = _app(core)
    _install(engine)

    if not foundation._route_exists(app, "/api/property-brain/ownership-v292/status"):
        @app.get("/api/property-brain/ownership-v292/status")
        def _status():
            return status(engine)

    if not foundation._route_exists(app, "/api/property-brain/ownership-v292/run"):
        @app.post("/api/property-brain/ownership-v292/run")
        def _run(limit:int=Query(default=1000,ge=1,le=5000)):
            return run(engine,limit)

    if not foundation._route_exists(app, "/property-brain/ownership-v292"):
        @app.get("/property-brain/ownership-v292",response_class=HTMLResponse)
        def _dashboard():
            return HTMLResponse(DASH)

    return {
        "status":"REGISTERED",
        "version":VERSION,
        "dashboard":"/property-brain/ownership-v292",
        "production_writes":0,
        "whatsapp_live_writes":0,
        "gold_v1_mutations":0
    }
