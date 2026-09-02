from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Dict

from fastapi import Body, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation

VERSION = "2.0.0-GOLD-V1-TRAINING-BENCHMARK"
MODE = "GOLD_V1_FREEZE_TRAIN_EVALUATE"
SNAPSHOT_VERSION = "GOLD_V1_100"
TRAINING_VERSION = "PROPERTY_BRAIN_GOLD_TUTOR_V1"

DDL = [
    """
    CREATE TABLE IF NOT EXISTS alliance_gold_dataset_snapshots (
        snapshot_id UUID PRIMARY KEY,
        snapshot_version TEXT NOT NULL UNIQUE,
        gold_count INTEGER NOT NULL,
        snapshot_hash TEXT NOT NULL,
        snapshot_payload JSONB NOT NULL,
        frozen BOOLEAN NOT NULL DEFAULT TRUE,
        notes TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS alliance_gold_normalization_rules (
        rule_id UUID PRIMARY KEY,
        dimension TEXT NOT NULL,
        source_value TEXT NOT NULL,
        normalized_value TEXT NOT NULL,
        rule_status TEXT NOT NULL DEFAULT 'ACTIVE',
        provenance TEXT NOT NULL,
        notes TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(dimension, source_value)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS alliance_gold_training_profiles (
        profile_id UUID PRIMARY KEY,
        training_version TEXT NOT NULL UNIQUE,
        snapshot_version TEXT NOT NULL,
        gold_count INTEGER NOT NULL,
        learned_profile JSONB NOT NULL,
        active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS alliance_gold_benchmark_cases (
        case_id UUID PRIMARY KEY,
        run_id UUID NOT NULL,
        snapshot_version TEXT NOT NULL,
        span_id UUID NOT NULL,
        gold_label JSONB NOT NULL,
        brain_prediction JSONB NOT NULL,
        comparison JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
]

POLICY = {
    "one_property_one_atomic_entity": True,
    "inventory_group_when_multiple_unidentified_assets": True,
    "never_infer_unsupported_geography": True,
    "preserve_raw_source_text": True,
    "normalize_separately_from_source_truth": True,
    "contact_precedence": [
        "EXPLICIT_MESSAGE_CONTACT",
        "SOURCE_SHARED_CONTACT",
        "WHATSAPP_SENDER",
    ],
    "owner_broker_role_requires_evidence": True,
    "ambiguous_numbers_are_not_silently_typed": True,
    "production_write_permission": False,
}


def _engine(core):
    return foundation._engine_from_core(core)


def _app(core):
    app = getattr(core, "app", None)
    return app if app is not None else core


def _loads(v, default):
    return foundation._loads(v, default)


def _json_safe(v):
    return foundation._json_safe(v)


def _install_tables(engine):
    with engine.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))


def _gold_rows(engine, limit=100):
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT sp.span_id, sp.source_message_id,
                   COALESCE(sp.human_text, sp.proposed_text) AS span_text,
                   sp.boundary_action, sp.proposal_confidence,
                   s.raw_text AS source_raw_text, s.source_table, s.sampling_bucket,
                   l.labeler_id, l.content_type, l.human_confidence,
                   l.transaction_type, l.project_name, l.building_name,
                   l.unit_identifier, l.city, l.locality,
                   l.acceptable_locations, l.areas, l.money_mentions,
                   l.suitable_uses, l.contacts, l.property_fields,
                   l.requirement_fields, l.notes, l.created_at AS label_created_at
            FROM alliance_gold_spans sp
            JOIN alliance_gold_source_messages s ON s.source_message_id=sp.source_message_id
            JOIN alliance_gold_span_labels l ON l.span_id=sp.span_id AND l.active=TRUE
            WHERE COALESCE(sp.span_status,'ACTIVE')='ACTIVE'
              AND sp.boundary_status='LABELED'
            ORDER BY l.created_at, sp.created_at, sp.span_order
            LIMIT :limit
        """), {"limit": int(limit)}).mappings().all()
    return [_json_safe(dict(r)) for r in rows]


def _canonical_payload(rows):
    out = []
    for r in rows:
        d = dict(r)
        for key in ("acceptable_locations","areas","money_mentions","suitable_uses","contacts","property_fields","requirement_fields"):
            d[key] = _loads(d.get(key), {} if key in {"property_fields","requirement_fields"} else [])
        out.append(d)
    return out


def freeze_gold_v1(engine):
    _install_tables(engine)
    with engine.connect() as conn:
        existing = conn.execute(text(
            "SELECT snapshot_id,snapshot_version,gold_count,snapshot_hash,created_at "
            "FROM alliance_gold_dataset_snapshots WHERE snapshot_version=:v"
        ), {"v": SNAPSHOT_VERSION}).mappings().first()
    if existing:
        return {"status":"ALREADY_FROZEN","snapshot":_json_safe(dict(existing)),"production_writes":0}

    rows = _gold_rows(engine, 100)
    if len(rows) < 100:
        raise HTTPException(409, f"Gold V1 requires 100 active labeled spans; found {len(rows)}")
    payload = _canonical_payload(rows[:100])
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",",":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    sid = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO alliance_gold_dataset_snapshots
            (snapshot_id,snapshot_version,gold_count,snapshot_hash,snapshot_payload,frozen,notes)
            VALUES (:id,:v,100,:h,CAST(:p AS jsonb),TRUE,:n)
        """), {
            "id": sid, "v": SNAPSHOT_VERSION, "h": digest, "p": canonical,
            "n": "First 100 human-labeled Alliance Gold examples. Immutable benchmark snapshot."
        })
    return {"status":"FROZEN","snapshot_version":SNAPSHOT_VERSION,"gold_count":100,"snapshot_hash":digest,"snapshot_id":sid,"production_writes":0}


def _seed_normalization_rules(engine):
    rules = [
        ("CITY","GGN","Gurgaon","Human approved: GGN normalized to Gurgaon."),
        ("CITY","GURGAON","Gurgaon","Canonical operating name chosen by human labeler."),
        ("CITY","GURUGRAM","Gurgaon","Alias normalization only; source text remains unchanged."),
    ]
    with engine.begin() as conn:
        for dimension, source_value, normalized_value, notes in rules:
            conn.execute(text("""
                INSERT INTO alliance_gold_normalization_rules
                (rule_id,dimension,source_value,normalized_value,provenance,notes)
                VALUES (:id,:d,:s,:n,'HUMAN_GOLD',:notes)
                ON CONFLICT (dimension,source_value) DO UPDATE SET
                    normalized_value=EXCLUDED.normalized_value,
                    provenance='HUMAN_GOLD', notes=EXCLUDED.notes,
                    rule_status='ACTIVE', updated_at=now()
            """), {"id":str(uuid.uuid4()),"d":dimension,"s":source_value,"n":normalized_value,"notes":notes})


def _normalize_city(engine, value):
    if not value:
        return value
    key = re.sub(r"\s+"," ",str(value)).strip().upper()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT normalized_value FROM alliance_gold_normalization_rules
            WHERE dimension='CITY' AND source_value=:v AND rule_status='ACTIVE'
        """), {"v":key}).first()
    return row[0] if row else value


def _norm(v):
    if v is None:
        return None
    s = re.sub(r"\s+"," ",str(v)).strip()
    return s.casefold() if s else None


def _phone_set(items):
    out=set()
    for item in items or []:
        v=item.get("phone") if isinstance(item,dict) else item
        digits=re.sub(r"\D","",str(v or ""))
        if len(digits)>=10:
            out.add(digits[-10:])
    return sorted(out)


def _proposal_from_text(engine, span_text, source_text):
    raw=str(span_text or "")
    source=str(source_text or "")
    try:
        p=foundation._v16_enrich_proposal(raw)
    except Exception:
        p={}
    req_re=getattr(foundation,"REQUIREMENT_SIGNAL_RE",re.compile(r"$^"))
    requirement=bool(req_re.search(raw))
    if requirement:
        content="REQUIREMENT"
    elif re.search(r"\b(?:PLOT|LAND|BHK|FLAT|APARTMENT|VILLA|KOTHI|HOUSE|OFFICE|SHOP|SHOWROOM|HOTEL|BANQUET|RESTAURANT|CAFE)\b",raw,re.I):
        content="PROPERTY_AVAILABILITY"
    else:
        content=p.get("content_type_hint") or "UNKNOWN"
    tx=p.get("transaction_type_hint") or "UNKNOWN"
    if str(tx).upper() in {"","UNKNOWN","AMBIGUOUS"}:
        combined=raw+"\n"+source
        if requirement and re.search(r"\b(?:RENT|LEASE|RENTAL)\b",combined,re.I):
            tx="RENT"
        elif re.search(r"\b(?:FOR SALE|SALE|SELL|ASKING|DEMAND)\b",combined,re.I):
            tx="SALE"
    contacts=p.get("contacts") or []
    if not contacts:
        contacts=[]
        for m in re.finditer(r"(?<!\d)(?:\+?91[\s-]?)?[6-9](?:[\s-]?\d){9}(?!\d)",raw):
            digits=re.sub(r"\D","",m.group(0))
            if len(digits)>=10:
                contacts.append({"phone":digits[-10:],"provenance":"EXPLICIT_MESSAGE_CONTACT"})
    city=p.get("city_hint")
    return {
        "content_type":content,
        "transaction_type":str(tx or "UNKNOWN").upper(),
        "city":_normalize_city(engine,city) if city else None,
        "locality":p.get("locality_hint"),
        "project_name":p.get("project_name_hint") or p.get("project_hint"),
        "areas":p.get("areas") or [],
        "money_mentions":p.get("money_mentions") or [],
        "contacts":contacts,
    }


def _compare_case(engine,row):
    gold={
        "content_type":row.get("content_type"),"transaction_type":row.get("transaction_type"),
        "city":row.get("city"),"locality":row.get("locality"),"project_name":row.get("project_name"),
        "areas":_loads(row.get("areas"),[]),"money_mentions":_loads(row.get("money_mentions"),[]),
        "contacts":_loads(row.get("contacts"),[]),
    }
    pred=_proposal_from_text(engine,row.get("span_text"),row.get("source_raw_text"))
    checks={}
    for key in ("content_type","transaction_type","city","locality","project_name"):
        gv=gold.get(key); pv=pred.get(key)
        if key=="city":
            gv=_normalize_city(engine,gv) if gv else gv
            pv=_normalize_city(engine,pv) if pv else pv
        checks[key]=(_norm(gv)==_norm(pv))
    gp=_phone_set(gold["contacts"]); pp=_phone_set(pred["contacts"])
    checks["contacts"]=(gp==pp) if gp else (not pp)
    checks["areas_presence"]=bool(gold["areas"])==bool(pred["areas"])
    checks["money_presence"]=bool(gold["money_mentions"])==bool(pred["money_mentions"])
    unsupported=[]
    for key in ("city","locality","project_name"):
        if not gold.get(key) and pred.get(key):
            unsupported.append({"field":key,"predicted":pred.get(key)})
    score=round(sum(1 for v in checks.values() if v)/max(1,len(checks)),4)
    return gold,pred,{"checks":checks,"case_score":score,"unsupported_inference":unsupported,"gold_phones":gp,"predicted_phones":pp}


def run_benchmark(engine):
    _install_tables(engine)
    freeze_gold_v1(engine)
    with engine.connect() as conn:
        snap=conn.execute(text("SELECT snapshot_payload FROM alliance_gold_dataset_snapshots WHERE snapshot_version=:v"),{"v":SNAPSHOT_VERSION}).first()
    payload=_loads(snap[0] if snap else None,[])
    if len(payload)!=100:
        raise HTTPException(409,"Gold V1 snapshot is not exactly 100 examples")
    run_id=str(uuid.uuid4()); hits={}; totals={}; unsupported_total=0; boundary_correct=0
    with engine.begin() as conn:
        for row in payload:
            gold,pred,comp=_compare_case(engine,row)
            conn.execute(text("""
                INSERT INTO alliance_gold_benchmark_cases
                (case_id,run_id,snapshot_version,span_id,gold_label,brain_prediction,comparison)
                VALUES (:cid,:rid,:sv,:sid,CAST(:g AS jsonb),CAST(:p AS jsonb),CAST(:c AS jsonb))
            """),{
                "cid":str(uuid.uuid4()),"rid":run_id,"sv":SNAPSHOT_VERSION,"sid":row["span_id"],
                "g":json.dumps(_json_safe(gold),ensure_ascii=False),"p":json.dumps(_json_safe(pred),ensure_ascii=False),
                "c":json.dumps(_json_safe(comp),ensure_ascii=False),
            })
            for k,v in comp["checks"].items():
                totals[k]=totals.get(k,0)+1; hits[k]=hits.get(k,0)+int(bool(v))
            unsupported_total+=len(comp["unsupported_inference"])
            if str(row.get("boundary_action") or "").upper()=="CORRECT":
                boundary_correct+=1
        metrics={k:round(hits.get(k,0)/max(1,totals.get(k,0)),4) for k in totals}
        metrics["boundary_acceptance_rate"]=round(boundary_correct/100,4)
        metrics["false_inference_events"]=unsupported_total
        metrics["false_inference_rate_per_case"]=round(unsupported_total/100,4)
        metrics["overall_field_score"]=round(sum(hits.values())/max(1,sum(totals.values())),4)
        failures=[]
        if unsupported_total:
            failures.append({"metric":"UNSUPPORTED_GEOGRAPHY_OR_PROJECT_INFERENCE","count":unsupported_total})
        promotion_ready=(unsupported_total==0 and metrics["overall_field_score"]>=0.90)
        conn.execute(text("""
            INSERT INTO alliance_gold_evaluation_runs
            (run_id,engine_version,dataset_snapshot,metrics,zero_tolerance_failures,passed)
            VALUES (:rid,:ev,CAST(:ds AS jsonb),CAST(:m AS jsonb),CAST(:f AS jsonb),:passed)
        """),{
            "rid":run_id,"ev":VERSION,
            "ds":json.dumps({"snapshot_version":SNAPSHOT_VERSION,"gold_count":100,"training_version":TRAINING_VERSION}),
            "m":json.dumps(metrics),"f":json.dumps(failures),"passed":promotion_ready,
        })
    return {"status":"PASS","run_id":run_id,"snapshot_version":SNAPSHOT_VERSION,"gold_count":100,
            "metrics":metrics,"zero_tolerance_failures":failures,"promotion_ready":promotion_ready,"production_writes":0}


def train_from_gold(engine):
    _install_tables(engine)
    frozen=freeze_gold_v1(engine)
    _seed_normalization_rules(engine)
    with engine.connect() as conn:
        ct=conn.execute(text("""
            SELECT l.content_type,count(*) FROM alliance_gold_span_labels l
            JOIN alliance_gold_spans sp ON sp.span_id=l.span_id
            WHERE l.active=TRUE AND COALESCE(sp.span_status,'ACTIVE')='ACTIVE' AND sp.boundary_status='LABELED'
            GROUP BY l.content_type ORDER BY l.content_type
        """)).all()
        tx=conn.execute(text("""
            SELECT COALESCE(l.transaction_type,'UNKNOWN'),count(*) FROM alliance_gold_span_labels l
            JOIN alliance_gold_spans sp ON sp.span_id=l.span_id
            WHERE l.active=TRUE AND COALESCE(sp.span_status,'ACTIVE')='ACTIVE' AND sp.boundary_status='LABELED'
            GROUP BY COALESCE(l.transaction_type,'UNKNOWN') ORDER BY 1
        """)).all()
    profile={
        "training_version":TRAINING_VERSION,"snapshot_version":SNAPSHOT_VERSION,"gold_examples":100,
        "method":"HUMAN_GOLD_RETRIEVAL_RULES_BENCHMARK_NOT_LLM_FINETUNE","policy":POLICY,
        "content_type_distribution":{str(k):int(v) for k,v in ct},
        "transaction_distribution":{str(k):int(v) for k,v in tx},
        "normalization":{"city":{"GGN":"Gurgaon","GURGAON":"Gurgaon","GURUGRAM":"Gurgaon"}},
        "production_promotion":"BLOCKED_UNTIL_BENCHMARK_PASSES",
    }
    with engine.begin() as conn:
        existing=conn.execute(text("SELECT profile_id FROM alliance_gold_training_profiles WHERE training_version=:v"),{"v":TRAINING_VERSION}).first()
        if existing:
            conn.execute(text("""
                UPDATE alliance_gold_training_profiles
                SET learned_profile=CAST(:p AS jsonb),gold_count=100,snapshot_version=:s,active=TRUE
                WHERE training_version=:v
            """),{"p":json.dumps(profile,ensure_ascii=False),"s":SNAPSHOT_VERSION,"v":TRAINING_VERSION})
        else:
            conn.execute(text("""
                INSERT INTO alliance_gold_training_profiles
                (profile_id,training_version,snapshot_version,gold_count,learned_profile,active)
                VALUES (:id,:v,:s,100,CAST(:p AS jsonb),TRUE)
            """),{"id":str(uuid.uuid4()),"v":TRAINING_VERSION,"s":SNAPSHOT_VERSION,"p":json.dumps(profile,ensure_ascii=False)})
    benchmark=run_benchmark(engine)
    return {"status":"TRAINED_AND_EVALUATED","freeze":frozen,"training_profile":profile,"benchmark":benchmark,
            "production_writes":0,"next_step":"Fix weakest benchmark categories, rerun Gold V1, then apply validated tutor to Silver in shadow mode."}


def status(engine):
    _install_tables(engine)
    with engine.connect() as conn:
        live_gold=int(conn.execute(text("""
            SELECT count(*) FROM alliance_gold_span_labels l
            JOIN alliance_gold_spans sp ON sp.span_id=l.span_id
            WHERE l.active=TRUE AND COALESCE(sp.span_status,'ACTIVE')='ACTIVE' AND sp.boundary_status='LABELED'
        """)).scalar() or 0)
        snap=conn.execute(text("SELECT snapshot_version,gold_count,snapshot_hash,created_at FROM alliance_gold_dataset_snapshots ORDER BY created_at DESC LIMIT 1")).mappings().first()
        profile=conn.execute(text("SELECT training_version,snapshot_version,gold_count,created_at FROM alliance_gold_training_profiles WHERE active=TRUE ORDER BY created_at DESC LIMIT 1")).mappings().first()
        run=conn.execute(text("SELECT run_id,engine_version,metrics,zero_tolerance_failures,passed,created_at FROM alliance_gold_evaluation_runs ORDER BY created_at DESC LIMIT 1")).mappings().first()
    return _json_safe({"status":"PASS","version":VERSION,"mode":MODE,"live_active_gold_labels":live_gold,
                       "milestone_reached":live_gold>=100,"snapshot":dict(snap) if snap else None,
                       "training_profile":dict(profile) if profile else None,"latest_benchmark":dict(run) if run else None,
                       "production_write_permission":False,"production_writes":0})

DASHBOARD = """<!doctype html>
<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Alliance Property Brain - Gold V1 Training</title>
<style>body{font-family:Arial,sans-serif;background:#f3ede4;color:#2c2722;margin:0}main{max-width:1100px;margin:30px auto;padding:24px}.card{background:white;border-radius:14px;padding:20px;margin:14px 0;box-shadow:0 2px 10px #00000012}button{padding:12px 18px;border:0;border-radius:9px;cursor:pointer;margin-right:8px}.primary{background:#2c2722;color:#fff}pre{white-space:pre-wrap;background:#f7f3ed;padding:14px;border-radius:9px;overflow:auto}</style></head>
<body><main><h1>Alliance Property Brain - Gold V1 Training</h1><p>Human Gold remains ground truth. This screen never writes production inventory.</p>
<div class='card'><button class='primary' onclick='train()'>Train From Gold V1</button><button onclick='bench()'>Run Benchmark Again</button><button onclick='refreshStatus()'>Refresh Status</button></div>
<div class='card'><h3>Status</h3><pre id='status'>Loading...</pre></div><div class='card'><h3>Result</h3><pre id='result'>No action yet.</pre></div>
<script>
async function api(path,method='GET'){const r=await fetch(path,{method,headers:{'Content-Type':'application/json'}});const t=await r.text();let d={};try{d=JSON.parse(t)}catch(e){d={raw:t}}if(!r.ok)throw new Error(d.detail||d.raw||('HTTP '+r.status));return d}
async function refreshStatus(){try{document.getElementById('status').textContent=JSON.stringify(await api('/api/property-brain/gold-v2/status'),null,2)}catch(e){document.getElementById('status').textContent='ERROR: '+e.message}}
async function train(){document.getElementById('result').textContent='Freezing Gold V1, learning rules and benchmarking...';try{const d=await api('/api/property-brain/gold-v2/train','POST');document.getElementById('result').textContent=JSON.stringify(d,null,2);await refreshStatus()}catch(e){document.getElementById('result').textContent='ERROR: '+e.message}}
async function bench(){document.getElementById('result').textContent='Running Gold benchmark...';try{const d=await api('/api/property-brain/gold-v2/benchmark','POST');document.getElementById('result').textContent=JSON.stringify(d,null,2);await refreshStatus()}catch(e){document.getElementById('result').textContent='ERROR: '+e.message}}
refreshStatus();
</script></main></body></html>"""


def register(core):
    engine=_engine(core); app=_app(core); _install_tables(engine)
    if not foundation._route_exists(app,"/api/property-brain/gold-v2/status"):
        @app.get("/api/property-brain/gold-v2/status")
        def gold_v2_status(): return status(engine)
    if not foundation._route_exists(app,"/api/property-brain/gold-v2/freeze"):
        @app.post("/api/property-brain/gold-v2/freeze")
        def gold_v2_freeze(payload:Dict[str,Any]=Body(default={})): return freeze_gold_v1(engine)
    if not foundation._route_exists(app,"/api/property-brain/gold-v2/train"):
        @app.post("/api/property-brain/gold-v2/train")
        def gold_v2_train(payload:Dict[str,Any]=Body(default={})): return train_from_gold(engine)
    if not foundation._route_exists(app,"/api/property-brain/gold-v2/benchmark"):
        @app.post("/api/property-brain/gold-v2/benchmark")
        def gold_v2_benchmark(payload:Dict[str,Any]=Body(default={})): return run_benchmark(engine)
    if not foundation._route_exists(app,"/property-brain/gold-v2"):
        @app.get("/property-brain/gold-v2",response_class=HTMLResponse)
        def gold_v2_dashboard(): return HTMLResponse(DASHBOARD)
    return {"status":"REGISTERED","version":VERSION,"mode":MODE,"dashboard":"/property-brain/gold-v2",
            "status_route":"/api/property-brain/gold-v2/status","train_route":"/api/property-brain/gold-v2/train",
            "benchmark_route":"/api/property-brain/gold-v2/benchmark","production_write_permission":False,"production_writes":0}
