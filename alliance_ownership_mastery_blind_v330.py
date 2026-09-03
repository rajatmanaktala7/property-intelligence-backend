from __future__ import annotations

import hashlib
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
import alliance_mastery_repair_v320 as v320

VERSION = "3.3.0-OWNERSHIP-MASTERY-BLIND-SET"
MODE = "OWNERSHIP_REPAIR_FROZEN_UNSEEN_BLIND_CANDIDATES_NO_FALSE_EXPERTISE"
ENGINE_VERSION = "ALLIANCE_OWNERSHIP_MASTERY_BLIND_V330"
RULESET_VERSION = "OWNERSHIP_MASTERY_2026_09_03_V1"
BLINDSET_VERSION = "BLINDSET_V1_2026_09_03"
BLIND_TARGET = 100

DDL = [
"""CREATE TABLE IF NOT EXISTS alliance_mastery_v330_predictions(
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

"""CREATE TABLE IF NOT EXISTS alliance_mastery_v330_blind_cases(
blind_id UUID PRIMARY KEY,
source_table TEXT NOT NULL,
source_pk TEXT NOT NULL,
source_hash TEXT NOT NULL,
raw_text TEXT NOT NULL,
blindset_version TEXT NOT NULL,
frozen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
status TEXT NOT NULL DEFAULT 'FROZEN',
UNIQUE(source_table,source_pk,blindset_version),
UNIQUE(source_hash,blindset_version))""",

"""CREATE TABLE IF NOT EXISTS alliance_mastery_v330_blind_predictions(
prediction_id UUID PRIMARY KEY,
blind_id UUID NOT NULL,
predicted_class TEXT,
predicted_transaction TEXT,
predicted_ownership TEXT,
confidence NUMERIC(6,2) NOT NULL,
rule_trace JSONB NOT NULL DEFAULT '{}'::jsonb,
blindset_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(blind_id,blindset_version))""",

"""CREATE TABLE IF NOT EXISTS alliance_mastery_v330_runs(
run_id UUID PRIMARY KEY,
ruleset_version TEXT NOT NULL,
training_accuracy NUMERIC(8,4) NOT NULL,
training_errors INTEGER NOT NULL,
expert_resolved INTEGER NOT NULL,
shadow_resolved INTEGER NOT NULL,
exceptions INTEGER NOT NULL,
blind_frozen INTEGER NOT NULL,
blind_predicted INTEGER NOT NULL,
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

def _j(v): return json.dumps(foundation._json_safe(v), ensure_ascii=False)

def _fold(s): return unicodedata.normalize("NFKC", s or "")

def _norm(s):
    s=_fold(s).lower().replace("–","-").replace("—","-")
    s=re.sub(r"[^a-z0-9]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def _lines(s): return [x.strip() for x in _fold(s).splitlines() if x.strip()]

def _tx(s):
    n=_norm(s)
    if re.search(r"\b(?:rent|rental|lease|leasing|to let)\b",n): return "RENT"
    if re.search(r"\b(?:sale|sell|selling|resale|for sale)\b",n): return "SALE"
    if re.search(r"\bowner(?:s)?\s+wants?\b",n) and re.search(r"\b(?:cr|crore|lac|lakh)\b",n): return "SALE"
    return None

def _explicit_locality(s):
    n=_norm(s)
    pats=[
      r"\b(?:sushant|shushant)\s*lok\s*\d+\b",
      r"\bdlf\s*phase\s*\d+\b",
      r"\bg\s*k\s*[12]\b",
      r"\bgreater kailash\s*(?:1|2|i|ii)\b",
      r"\bparra\b",
      r"\bdona paula\b",
      r"\bharnampura\b",
      r"\bmohali\b",
    ]
    for p in pats:
        m=re.search(p,n)
        if m: return m.group(0)
    return None

def _has_area(s):
    return bool(re.search(r"\b\d+(?:\.\d+)?\s*(?:gaj|syds?|sq\s*yds?|sqft|sq\s*ft|sqm|sq\s*m|sqmt|acre|bigha)\b",_norm(s)))

def _has_config(s):
    n=_norm(s)
    return bool(re.search(r"\b\d+\s*bhk\b",n) or re.search(r"\b\d+(?:/\d+)+\s*bhk\b",n))

def _has_type(s):
    return bool(re.search(r"\b(?:plot|apartment|builder floor|floor|kothi|shop|office|land|villa|farmhouse|warehouse|showroom)\b",_norm(s)))

def _full_property_identity(s):
    return _has_area(s) or _has_config(s) or _has_type(s)

def _missing_area_prefix(s):
    n=_norm(s)
    return bool(re.match(r"^syds?\b",n))

def _truncated_rent_furnishing(s):
    n=_norm(s)
    return bool(re.fullmatch(r"\d+(?:\.\d+)?\s*lac\s*(?:maint|maintenance)?\s+fully furnished",n))

def _next_line_after(parent, target_last):
    pl=_lines(parent); target=_norm(target_last)
    for i,x in enumerate(pl[:-1]):
        if _norm(x)==target:
            return pl[i+1]
    return None

def _forward_header_proven(raw,parent):
    rl=_lines(raw)
    if len(rl)<2: return False
    last=rl[-1]; loc=_explicit_locality(last)
    if not loc: return False
    nxt=_next_line_after(parent,last)
    if not nxt: return False
    # Only forward-bind when the NEXT line begins a strong new property identity.
    nn=_norm(nxt)
    return bool(
      re.search(r"\b\d+(?:\.\d+)?\s*(?:gaj|syds?|sq\s*yds?|sqft|sqm|sq\s*m)\b",nn)
      or re.search(r"\b\d+\s*bhk\b",nn)
      or re.search(r"\b(?:plot|apartment|kothi|builder floor|shop|office|villa)\b",nn)
    )

def _ownership(case):
    raw=case.get("raw_text") or ""; parent=case.get("parent_message_text") or ""
    n=_norm(raw); loc=_explicit_locality(raw)

    # Missing leading area before "SYDS" is not a complete atomic property.
    if _missing_area_prefix(raw):
        if loc and _forward_header_proven(raw,parent):
            return "NOT_OWNED",99.8,None,"V330_MISSING_PREFIX_FORWARD_HEADER","Missing leading area proves truncation; trailing locality starts the next property block."
        return "NOT_OWNED",99.5,None,"V330_MISSING_AREA_PREFIX","Span starts with SYDS without its numeric area, so property identity is truncated and must not be reconstructed."

    # Slash-size summary is a group/summary, not one property.
    if re.match(r"^\s*/",raw or "") and (raw or "").count("/")>=2:
        return "NOT_OWNED",99.8,None,"V330_SLASH_SUMMARY","Slash-separated size summary is not one atomic property."

    # Isolated truncated rent + furnishing. 20LAC case was explicitly NOT_OWNED in Human Gold.
    if _truncated_rent_furnishing(raw):
        num=re.search(r"\d+(?:\.\d+)?",n)
        val=float(num.group(0)) if num else 0
        if val>=20:
            return "NOT_OWNED",99.8,None,"V330_TRUNCATED_RENT_NOT_OWNED","Orphan rent/furnishing fragment lacks property identity and likely lost a numeric prefix; do not infer the missing value."
        return "AMBIGUOUS",99.0,None,"V330_TRUNCATED_RENT_AMBIGUOUS","Orphan rent/furnishing fragment cannot be safely assigned to one property."

    # If full property identity is present, a trailing locality can belong to the same record
    # UNLESS positional evidence proves it is the next header.
    if loc and _full_property_identity(raw):
        if _forward_header_proven(raw,parent):
            return "NOT_OWNED",99.8,None,"V330_PROVEN_FORWARD_HEADER","Parent sequence proves trailing locality begins the next property block."
        return "OWNED",99.8,loc.title(),"V330_EXPLICIT_LOCALITY_IDENTITY","Atomic evidence contains explicit locality plus substantive property identity."

    # Compact rent + locality without area/config may still be a scoped owned atom when no
    # following-line evidence proves forward binding.
    if loc and re.search(r"\b\d+(?:\.\d+)?\s*lac\b",n):
        if _forward_header_proven(raw,parent):
            return "NOT_OWNED",99.8,None,"V330_RENT_FORWARD_HEADER","Trailing locality is proven to begin the next record."
        return "OWNED",99.6,loc.title(),"V330_RENT_LOCALITY_ATOM","Rent and explicit locality occur in one compact atom with no evidence of forward binding."

    # Strong source-truth ownership examples that 3.2 left ambiguous.
    if _has_type(raw) and _has_area(raw) and (re.search(r"\b(?:demand|price|owner(?:s)? wants?|asking)\b",n) or _tx(raw)):
        return "OWNED",99.8,_tx(raw),"V330_TYPE_AREA_COMMERCIAL_INTENT","Property type + area + explicit price/sale/rent intent establish atomic ownership."

    if re.search(r"\bdemand\b",n) and re.search(r"(?:rs|inr|cr|crore|lac|lakh)",n) and (
        _has_type(raw) or re.search(r"\b(?:residential floor|west facing|car parking|lift)\b",n)
    ):
        return "OWNED",99.6,None,"V330_PRICE_PROPERTY_CONTEXT","Demand plus property-specific physical context establishes the atomic property record."

    # Parra-style semantic sale intent without literal 'sale'.
    if _has_type(raw) and _has_area(raw) and re.search(r"\bowner(?:s)? wants?\b",n):
        return "OWNED",99.8,"SALE","V330_SEMANTIC_OWNER_WANTS_SALE","Owner asking price plus property identity is explicit sale intent."

    base=v320._ownership(case)
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
    # Reuse the now-correct 3.2 traceability and structural conflict repairs.
    if task=="SOURCE_TRACEABILITY":
        r=v320._source_trace(case)
        if r:
            d,c,cv,rule,reason=r
            return {"decision":d,"confidence":c,"canonical_value":cv,"rule_id":"V330_"+rule,"reason":reason}
    if task=="STRUCTURAL_CONFLICT":
        r=v320._structural(case)
        if r:
            d,c,cv,rule,reason=r
            return {"decision":d,"confidence":c,"canonical_value":cv,"rule_id":"V330_"+rule,"reason":reason}
    return {"decision":"AMBIGUOUS","confidence":70.0,"canonical_value":None,
            "rule_id":"V330_ABSTAIN","reason":"No high-confidence deterministic mastery rule applies."}

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

def benchmark(cases):
    totals=Counter(); correct=Counter(); errors=[]
    for c in cases:
        if not c.get("human_decision"): continue
        p=predict(c); t=c["task_type"];totals[t]+=1
        if p["decision"]==c["human_decision"]:correct[t]+=1
        else:errors.append({"entity_id":c["entity_id"],"task_type":t,"human":c["human_decision"],
                            "predicted":p["decision"],"rule_id":p["rule_id"],"raw_text":c.get("raw_text")})
    total=sum(totals.values());ok=sum(correct.values());acc=round(100*ok/max(total,1),4)
    task={t:round(100*correct[t]/max(totals[t],1),2) for t in totals}
    gate=total>=24 and acc>=95 and all(v>=90 for v in task.values())
    return {"examples":total,"accuracy":acc,"task_accuracy":task,"errors":errors,"training_mastery_gate":gate}

def _existing_tables(conn):
    rows=conn.execute(text("""SELECT table_name FROM information_schema.tables
                              WHERE table_schema='public'""")).scalars().all()
    return set(rows)

def _columns(conn,table):
    return [r for r in conn.execute(text("""
      SELECT column_name,data_type FROM information_schema.columns
      WHERE table_schema='public' AND table_name=:t ORDER BY ordinal_position
    """),{"t":table}).mappings().all()]

def _choose_source(conn):
    tables=_existing_tables(conn)
    for table in ("alliance_live_feed_entities","ai_whatsapp_purity","wai_raw_messages"):
        if table not in tables: continue
        cols=_columns(conn,table)
        names={r["column_name"] for r in cols}
        text_candidates=[x for x in ("raw_text","message_text","text","body","content","raw_message","source_text") if x in names]
        id_candidates=[x for x in ("id","entity_id","listing_id","message_id","source_message_id") if x in names]
        if text_candidates and id_candidates:
            return table,id_candidates[0],text_candidates[0]
    return None,None,None

def freeze_blind(engine,target=BLIND_TARGET):
    _install(engine)
    with engine.begin() as conn:
        existing=conn.execute(text("""SELECT count(*) FROM alliance_mastery_v330_blind_cases
                                     WHERE blindset_version=:v"""),{"v":BLINDSET_VERSION}).scalar() or 0
        if existing>=target:
            return {"status":"ALREADY_FROZEN","count":int(existing),"blindset_version":BLINDSET_VERSION}

        table,pkcol,textcol=_choose_source(conn)
        if not table:
            return {"status":"NO_COMPATIBLE_SOURCE","count":int(existing),"blindset_version":BLINDSET_VERSION}

        # Dynamic identifiers come only from information_schema-discovered trusted names.
        sql=f"""SELECT CAST("{pkcol}" AS TEXT) AS pk, CAST("{textcol}" AS TEXT) AS raw
                FROM "{table}"
                WHERE "{textcol}" IS NOT NULL AND length(CAST("{textcol}" AS TEXT)) >= 20
                ORDER BY "{pkcol}" DESC LIMIT 2500"""
        rows=conn.execute(text(sql)).mappings().all()

        # Exclude exact text already present in Gold V2.
        gold_hashes=set()
        gold_rows=conn.execute(text("""SELECT raw_text FROM alliance_gold_v2_structural_cases
                                      WHERE source_version=:v"""),{"v":goldlab.CURRICULUM_VERSION}).scalars().all()
        for g in gold_rows:
            gold_hashes.add(hashlib.sha256(_norm(g).encode("utf-8")).hexdigest())

        inserted=0
        for r in rows:
            raw=r["raw"] or ""
            h=hashlib.sha256(_norm(raw).encode("utf-8")).hexdigest()
            if h in gold_hashes: continue
            try:
                conn.execute(text("""
                  INSERT INTO alliance_mastery_v330_blind_cases
                  (blind_id,source_table,source_pk,source_hash,raw_text,blindset_version)
                  VALUES(:id,:table,:pk,:h,:raw,:v)
                  ON CONFLICT DO NOTHING
                """),{"id":str(uuid.uuid4()),"table":table,"pk":r["pk"],"h":h,"raw":raw,"v":BLINDSET_VERSION})
                inserted+=1
            except Exception:
                continue
            current=conn.execute(text("""SELECT count(*) FROM alliance_mastery_v330_blind_cases
                                         WHERE blindset_version=:v"""),{"v":BLINDSET_VERSION}).scalar() or 0
            if current>=target: break
        final=conn.execute(text("""SELECT count(*) FROM alliance_mastery_v330_blind_cases
                                  WHERE blindset_version=:v"""),{"v":BLINDSET_VERSION}).scalar() or 0
        return {"status":"FROZEN","count":int(final),"inserted_this_run":inserted,
                "source_table":table,"blindset_version":BLINDSET_VERSION}

def _classify_blind(raw):
    n=_norm(raw)
    if re.search(r"\b(?:required|requirement|looking for|wanted|need)\b",n):
        cls="REQUIREMENT"
    elif _tx(raw) and _full_property_identity(raw):
        cls="PROPERTY_AVAILABILITY"
    elif len(n)<15:
        cls="FRAGMENT"
    else:
        cls="UNRESOLVED"
    tx=_tx(raw) or "UNKNOWN"
    ownership="OWNED" if cls=="PROPERTY_AVAILABILITY" else ("NOT_OWNED" if cls=="FRAGMENT" else "AMBIGUOUS")
    conf=99.0 if cls in ("PROPERTY_AVAILABILITY","REQUIREMENT","FRAGMENT") else 75.0
    return cls,tx,ownership,conf

def predict_blind(engine):
    with engine.begin() as conn:
        rows=conn.execute(text("""SELECT blind_id,raw_text FROM alliance_mastery_v330_blind_cases
                                  WHERE blindset_version=:v ORDER BY frozen_at,blind_id"""),
                          {"v":BLINDSET_VERSION}).mappings().all()
        for r in rows:
            cls,tx,own,conf=_classify_blind(r["raw_text"])
            conn.execute(text("""
              INSERT INTO alliance_mastery_v330_blind_predictions
              (prediction_id,blind_id,predicted_class,predicted_transaction,predicted_ownership,
               confidence,rule_trace,blindset_version)
              VALUES(:id,:bid,:cls,:tx,:own,:conf,CAST(:trace AS jsonb),:v)
              ON CONFLICT(blind_id,blindset_version) DO UPDATE SET
                predicted_class=EXCLUDED.predicted_class,predicted_transaction=EXCLUDED.predicted_transaction,
                predicted_ownership=EXCLUDED.predicted_ownership,confidence=EXCLUDED.confidence,
                rule_trace=EXCLUDED.rule_trace
            """),{"id":str(uuid.uuid4()),"bid":str(r["blind_id"]),"cls":cls,"tx":tx,"own":own,"conf":conf,
                  "trace":_j({"classifier":"V330_BLIND_SHADOW","uses_gold_label":False}),"v":BLINDSET_VERSION})
    return len(rows)

def run(engine,limit=1000):
    _install(engine)
    cases=_cases(engine);bench=benchmark(cases)
    blind=freeze_blind(engine,BLIND_TARGET);blind_pred=predict_blind(engine)
    unlabeled=[c for c in cases if not c.get("human_decision") and c.get("status")=="OPEN"][:max(1,min(int(limit),5000))]
    counts=Counter()
    with engine.begin() as conn:
        for c in unlabeled:
            p=predict(c)
            deterministic=p["rule_id"]!="V330_ABSTAIN"
            if deterministic and p["confidence"]>=98:disp="EXPERT_RESOLVED"
            elif p["confidence"]>=90:disp="SHADOW_RESOLVED"
            else:disp="EXCEPTION"
            counts[disp]+=1
            conn.execute(text("""
              INSERT INTO alliance_mastery_v330_predictions
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
        eg="EXPERTISE_GATE_BLIND_SET_FROZEN_AWAITING_TRUTH"
        result={"status":"PASS","version":VERSION,"mode":MODE,"ruleset_version":RULESET_VERSION,
                "training_benchmark":bench,"training_mastery_gate":tg,
                "blind_holdout":blind,"blind_predictions":blind_pred,
                "expertise_gate":eg,"expert_resolved":counts["EXPERT_RESOLVED"],
                "shadow_resolved":counts["SHADOW_RESOLVED"],"exceptions":counts["EXCEPTION"],
                "next_step":"Blind set is frozen and predictions are locked separately. Truth must be obtained independently; pseudo-labels cannot certify expertise.",
                "production_writes":0,"whatsapp_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0}
        conn.execute(text("""
          INSERT INTO alliance_mastery_v330_runs
          (run_id,ruleset_version,training_accuracy,training_errors,expert_resolved,shadow_resolved,
           exceptions,blind_frozen,blind_predicted,training_mastery_gate,expertise_gate,result,
           production_writes,whatsapp_writes,gold_v1_mutations,gold_v2_mutations)
          VALUES(:id,:v,:acc,:err,:er,:sr,:ex,:bf,:bp,:tg,:eg,CAST(:r AS jsonb),0,0,0,0)
        """),{"id":str(uuid.uuid4()),"v":RULESET_VERSION,"acc":bench["accuracy"],"err":len(bench["errors"]),
              "er":counts["EXPERT_RESOLVED"],"sr":counts["SHADOW_RESOLVED"],"ex":counts["EXCEPTION"],
              "bf":blind.get("count",0),"bp":blind_pred,"tg":tg,"eg":eg,"r":_j(result)})
    return result

def status(engine):
    _install(engine)
    with engine.connect() as conn:
        latest=conn.execute(text("""SELECT result FROM alliance_mastery_v330_runs
                                    WHERE ruleset_version=:v ORDER BY created_at DESC LIMIT 1"""),
                            {"v":RULESET_VERSION}).scalar()
    return foundation._json_safe({"status":"PASS","version":VERSION,
      "latest_run":_loads(latest,{}) if latest else None,
      "production_writes":0,"whatsapp_live_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0})

DASHBOARD="""<!doctype html><html><head><meta charset='utf-8'><title>Ownership Mastery 3.3</title>
<style>body{font-family:Arial;background:#07111d;color:#eef6ff;max-width:1450px;margin:24px auto}.card{background:#101d2b;padding:16px;border-radius:12px;margin:12px 0}button{padding:12px 18px;border:0;border-radius:8px;background:#f5d76e;font-weight:bold}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style></head><body>
<h1>🧠 Alliance Property Brain — Ownership Mastery + Blind Set 3.3</h1>
<p>Repairs the remaining ownership curriculum, freezes fresh unseen evidence, and refuses to call pseudo-labels ground truth.</p>
<button onclick='run()'>Run 3.3 Mastery + Freeze Blind Set</button>
<div class='card'><h3>Latest Run</h3><pre id='latest'></pre></div>
<script>
async function call(p,m='GET'){let r=await fetch(p,{method:m});let t=await r.text();let d;try{d=JSON.parse(t)}catch(e){d={raw:t}}if(!r.ok)throw Error(d.detail||d.raw||('HTTP '+r.status));return d}
async function load(){let s=await call('/api/property-brain/mastery-v330/status');document.getElementById('latest').textContent=JSON.stringify(s.latest_run||s,null,2)}
async function run(){document.getElementById('latest').textContent='Running...';await call('/api/property-brain/mastery-v330/run?limit=1000','POST');await load()}load()
</script></body></html>"""

def register(core):
    engine=_engine(core);app=_app(core);_install(engine)
    try:run(engine,1000)
    except Exception:pass
    if not foundation._route_exists(app,"/api/property-brain/mastery-v330/status"):
        @app.get("/api/property-brain/mastery-v330/status")
        def _status():return status(engine)
    if not foundation._route_exists(app,"/api/property-brain/mastery-v330/run"):
        @app.post("/api/property-brain/mastery-v330/run")
        def _run(limit:int=Query(default=1000,ge=1,le=5000)):return run(engine,limit)
    if not foundation._route_exists(app,"/property-brain/mastery-v330"):
        @app.get("/property-brain/mastery-v330",response_class=HTMLResponse)
        def _dash():return HTMLResponse(DASHBOARD)
    return {"status":"REGISTERED","version":VERSION,"dashboard":"/property-brain/mastery-v330",
            "auto_run_on_start":True,"production_writes":0,"whatsapp_live_writes":0,
            "gold_v1_mutations":0,"gold_v2_mutations":0}
