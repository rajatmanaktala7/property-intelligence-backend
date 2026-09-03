from __future__ import annotations

import hashlib
import inspect
import json
import random
import uuid
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_cre_academy_v402 as v402
import alliance_ownership_mastery_blind_v330 as v330

VERSION = "4.1.0-ALLIANCE-CRE-CHAMPIONSHIP-BLIND-V4"
MODE = "FRESH_UNSEEN_FROZEN_PREDICTIONS_INDEPENDENT_HUMAN_CERTIFICATION_NO_TUNING"
EXAM_VERSION = "BLIND_AUDIT_V4_2026_09_03"
TARGET = 20
OVERALL_PASS = 95.0
FIELD_PASS = 90.0

CLASS_OPTIONS = [
    "PROPERTY_AVAILABILITY","INVENTORY_GROUP","REQUIREMENT",
    "CONTACT_ONLY","PROJECT_HEADER","LOCALITY_HEADER","FRAGMENT","NOISE","UNRESOLVED"
]
TX_OPTIONS = ["SALE","RENT","AMBIGUOUS","UNKNOWN"]
OWN_OPTIONS = ["OWNED","NOT_OWNED","AMBIGUOUS"]

DDL = [
"""CREATE TABLE IF NOT EXISTS alliance_championship_v410_manifest(
manifest_id UUID PRIMARY KEY,
exam_version TEXT NOT NULL UNIQUE,
target INTEGER NOT NULL,
predictor_version TEXT NOT NULL,
predictor_sha256 TEXT NOT NULL,
precert_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
selection_policy TEXT NOT NULL,
case_manifest_hash TEXT,
status TEXT NOT NULL DEFAULT 'FROZEN',
frozen_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
"""CREATE TABLE IF NOT EXISTS alliance_championship_v410_cases(
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
human_class TEXT,
human_transaction TEXT,
human_ownership TEXT,
human_reason TEXT,
review_status TEXT NOT NULL DEFAULT 'OPEN',
frozen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(exam_version,ordinal))""",
"""CREATE TABLE IF NOT EXISTS alliance_championship_v410_results(
result_id UUID PRIMARY KEY,
exam_version TEXT NOT NULL UNIQUE,
total_cases INTEGER NOT NULL,
labeled_cases INTEGER NOT NULL,
comparable_fields INTEGER NOT NULL,
correct_fields INTEGER NOT NULL,
overall_accuracy NUMERIC(8,4),
class_accuracy NUMERIC(8,4),
transaction_accuracy NUMERIC(8,4),
ownership_accuracy NUMERIC(8,4),
case_accuracy NUMERIC(8,4),
expertise_gate TEXT NOT NULL,
truth_hash TEXT,
result JSONB NOT NULL DEFAULT '{}'::jsonb,
scored_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
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

def _predictor_hash():
    try:
        p=Path(v402.__file__)
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except Exception:
        try:
            return hashlib.sha256(inspect.getsource(v402.predict_message).encode("utf-8")).hexdigest()
        except Exception:
            return "UNAVAILABLE"

def _used_blind_ids(conn):
    used=set()
    # Every earlier audit that contains blind_id is excluded, whether labeled or not.
    candidates=[
        "alliance_mastery_v340_blind_audit_cases",
        "alliance_mastery_v360_exam_v2_cases",
        "alliance_mastery_v380_exam_v3_cases",
        "alliance_championship_v410_cases",
    ]
    for t in candidates:
        if _table_exists(conn,t) and _column_exists(conn,t,"blind_id"):
            try:
                used.update(str(x) for x in conn.execute(text(f"SELECT blind_id FROM {t} WHERE blind_id IS NOT NULL")).scalars().all())
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
    # Selection may use prediction type only for coverage; truth remains unseen.
    cls=p["class"]; tx=p["transaction"]; n=(raw or "").lower()
    score=0
    if cls=="INVENTORY_GROUP": score+=5
    if cls=="REQUIREMENT": score+=4
    if cls in {"NOISE","FRAGMENT","UNRESOLVED"}: score+=4
    if tx in {"AMBIGUOUS","UNKNOWN"}: score+=4
    if "pre" in n and ("lease" in n or "rent" in n): score+=3
    if len(raw or "")>800: score+=3
    if any(x in n for x in ["ideal for","looking for","demand","owner wants","available for lease"]): score+=2
    return score

def _select_cases(pool):
    if len(pool)<TARGET:
        raise RuntimeError(f"Only {len(pool)} untouched blind cases remain; need {TARGET}.")
    enriched=[]
    for r in pool:
        p=v402.predict_message(r["raw_text"])
        tie=int(hashlib.sha256((EXAM_VERSION+str(r["blind_id"])).encode()).hexdigest()[:12],16)
        enriched.append((r,p,_risk_bucket(p,r["raw_text"]),tie))

    # Diversity-first deterministic selection. No human truth is used.
    groups={}
    for item in enriched:
        key=(item[1]["class"], item[1]["transaction"])
        groups.setdefault(key,[]).append(item)
    for vals in groups.values():
        vals.sort(key=lambda x:(-x[2],x[3]))

    selected=[]
    # First pass: one per predicted class/transaction bucket.
    for key in sorted(groups):
        if groups[key] and len(selected)<TARGET:
            selected.append(groups[key].pop(0))

    # Second pass: hardest remaining, deterministic tie-break.
    remaining=[x for vals in groups.values() for x in vals]
    remaining.sort(key=lambda x:(-x[2],x[3]))
    for x in remaining:
        if len(selected)>=TARGET: break
        selected.append(x)

    # Freeze order independently of risk so reviewer doesn't infer difficulty.
    selected.sort(key=lambda x:int(hashlib.sha256(("ORDER|"+str(x[0]["blind_id"])).encode()).hexdigest()[:12],16))
    return selected[:TARGET]

def _manifest_hash(rows):
    payload=[{
        "blind_id":str(r["blind_id"]),
        "source_hash":r["source_hash"],
        "predicted_class":p["class"],
        "predicted_transaction":p["transaction"],
        "predicted_ownership":p["ownership"],
    } for r,p,_,_ in rows]
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()

def freeze_exam(engine):
    _install(engine)
    precert=v402.academy_status()
    if precert.get("precert_gate")!="PRECERT_PASS_READY_TO_FREEZE_NEW_UNSEEN_V4":
        return {"status":"BLOCKED","reason":"Foundation 4.0.2 pre-certification gate is not PASS.","precert":precert}

    with engine.connect() as conn:
        existing=conn.execute(text("SELECT COUNT(*) FROM alliance_championship_v410_cases WHERE exam_version=:e"),{"e":EXAM_VERSION}).scalar() or 0
        manifest=conn.execute(text("SELECT predictor_sha256,case_manifest_hash,status FROM alliance_championship_v410_manifest WHERE exam_version=:e"),{"e":EXAM_VERSION}).mappings().first()
    if existing:
        return {"status":"ALREADY_FROZEN","total":int(existing),"manifest":dict(manifest) if manifest else None}

    selected=_select_cases(_candidate_pool(engine))
    psha=_predictor_hash()
    mhash=_manifest_hash(selected)
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO alliance_championship_v410_manifest
        (manifest_id,exam_version,target,predictor_version,predictor_sha256,precert_snapshot,selection_policy,case_manifest_hash,status)
        VALUES(:id,:e,:t,:pv,:ps,CAST(:pre AS JSONB),:policy,:mh,'FROZEN')"""),
        {"id":str(uuid.uuid4()),"e":EXAM_VERSION,"t":TARGET,"pv":v402.VERSION,"ps":psha,
         "pre":_j(precert),
         "policy":"Fresh blind pool only; exclude all V1/V2/V3 blind_ids; deterministic diversity-first selection using predictions only; no truth used.",
         "mh":mhash})
        for ordinal,(r,p,_,_) in enumerate(selected,1):
            conn.execute(text("""INSERT INTO alliance_championship_v410_cases
            (audit_id,blind_id,exam_version,ordinal,source_hash,raw_text,predicted_class,predicted_transaction,predicted_ownership,prediction_confidence,prediction_rule)
            VALUES(:id,:bid,:e,:ord,:sh,:raw,:c,:tx,:o,:cf,:rule)"""),
            {"id":str(uuid.uuid4()),"bid":str(r["blind_id"]),"e":EXAM_VERSION,"ord":ordinal,"sh":r["source_hash"],"raw":r["raw_text"],
             "c":p["class"],"tx":p["transaction"],"o":p["ownership"],"cf":float(p["confidence"]),"rule":p.get("rule")})
    return {"status":"FROZEN","total":TARGET,"predictor_sha256":psha,"case_manifest_hash":mhash}

def _truth_hash(rows):
    payload=[(str(r["audit_id"]),r["human_class"],r["human_transaction"],r["human_ownership"]) for r in rows]
    return hashlib.sha256(json.dumps(payload,separators=(",",":")).encode()).hexdigest()

def score_exam(engine):
    with engine.connect() as conn:
        rows=[dict(r) for r in conn.execute(text("""
          SELECT * FROM alliance_championship_v410_cases
          WHERE exam_version=:e ORDER BY ordinal
        """),{"e":EXAM_VERSION}).mappings()]
    total=len(rows); labeled=sum(1 for r in rows if r["review_status"]=="SAVED")
    if not total:
        return {"total":0,"labeled":0,"remaining":TARGET,"accuracy":None,"expertise_gate":"V4_NOT_FROZEN"}
    if labeled<total:
        return {"total":total,"labeled":labeled,"remaining":total-labeled,"accuracy":None,"expertise_gate":"AWAITING_INDEPENDENT_V4_TRUTH"}

    fstats={f:[0,0] for f in ("class","transaction","ownership")}
    case_correct=0; errors=[]
    for r in rows:
        expected={"class":r["human_class"],"transaction":r["human_transaction"],"ownership":r["human_ownership"]}
        predicted={"class":r["predicted_class"],"transaction":r["predicted_transaction"],"ownership":r["predicted_ownership"]}
        allok=True
        for f in fstats:
            fstats[f][1]+=1
            if expected[f]==predicted[f]:
                fstats[f][0]+=1
            else:
                allok=False
                errors.append({"ordinal":r["ordinal"],"audit_id":str(r["audit_id"]),"field":f,"human":expected[f],"predicted":predicted[f]})
        if allok: case_correct+=1

    comparable=sum(v[1] for v in fstats.values()); correct=sum(v[0] for v in fstats.values())
    overall=round(100*correct/comparable,4)
    fields={k:round(100*v[0]/v[1],4) for k,v in fstats.items()}
    case_acc=round(100*case_correct/total,4)
    passed=overall>=OVERALL_PASS and all(v>=FIELD_PASS for v in fields.values())
    gate="EXPERTISE_PASS_INDEPENDENT_BLIND_V4" if passed else "EXPERTISE_HOLD_INDEPENDENT_BLIND_V4"
    result={"version":VERSION,"exam_version":EXAM_VERSION,"total":total,"labeled":labeled,"remaining":0,
            "correct_fields":correct,"comparable_fields":comparable,"accuracy":overall,"field_accuracy":fields,
            "case_accuracy":case_acc,"errors":errors,"expertise_gate":gate,
            "policy":"Frozen predictions scored once against independent human truth. Never rewrite this score after learning.",
            "safety":{"production_writes":0,"whatsapp_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0}}
    th=_truth_hash(rows)
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO alliance_championship_v410_results
        (result_id,exam_version,total_cases,labeled_cases,comparable_fields,correct_fields,overall_accuracy,class_accuracy,transaction_accuracy,ownership_accuracy,case_accuracy,expertise_gate,truth_hash,result)
        VALUES(:id,:e,:t,:l,:cmp,:cor,:oa,:ca,:ta,:ow,:casea,:gate,:th,CAST(:res AS JSONB))
        ON CONFLICT(exam_version) DO NOTHING"""),
        {"id":str(uuid.uuid4()),"e":EXAM_VERSION,"t":total,"l":labeled,"cmp":comparable,"cor":correct,"oa":overall,
         "ca":fields["class"],"ta":fields["transaction"],"ow":fields["ownership"],"casea":case_acc,"gate":gate,"th":th,"res":_j(result)})
    # If already scored, immutable stored result wins.
    with engine.connect() as conn:
        stored=conn.execute(text("SELECT result FROM alliance_championship_v410_results WHERE exam_version=:e"),{"e":EXAM_VERSION}).scalar()
    return stored or result

def _next_case(engine):
    with engine.connect() as conn:
        return conn.execute(text("""SELECT audit_id,ordinal,raw_text FROM alliance_championship_v410_cases
        WHERE exam_version=:e AND review_status='OPEN' ORDER BY ordinal LIMIT 1"""),{"e":EXAM_VERSION}).mappings().first()

async def _save(request,engine):
    data=await request.json()
    aid=str(data.get("audit_id") or "")
    hc=str(data.get("human_class") or "")
    ht=str(data.get("human_transaction") or "")
    ho=str(data.get("human_ownership") or "")
    reason=str(data.get("human_reason") or "").strip()
    if hc not in CLASS_OPTIONS or ht not in TX_OPTIONS or ho not in OWN_OPTIONS:
        return JSONResponse({"status":"ERROR","error":"Invalid label value."},status_code=400)
    with engine.begin() as conn:
        row=conn.execute(text("SELECT review_status,ordinal FROM alliance_championship_v410_cases WHERE audit_id=:id AND exam_version=:e FOR UPDATE"),
                         {"id":aid,"e":EXAM_VERSION}).mappings().first()
        if not row: return JSONResponse({"status":"ERROR","error":"Audit case not found."},status_code=404)
        if row["review_status"]=="SAVED":
            return {"status":"ALREADY_SAVED","audit_id":aid,"ordinal":row["ordinal"]}
        conn.execute(text("""UPDATE alliance_championship_v410_cases SET
          human_class=:c,human_transaction=:tx,human_ownership=:o,human_reason=:r,
          review_status='SAVED',updated_at=now() WHERE audit_id=:id"""),
          {"c":hc,"tx":ht,"o":ho,"r":reason,"id":aid})
    s=score_exam(engine)
    return {"status":"SAVED","audit_id":aid,"labels":{"class":hc,"transaction":ht,"ownership":ho},"score":s}

def _esc(s):
    import html
    return html.escape(str(s or ""))

def _dashboard(engine):
    frozen=freeze_exam(engine)
    score=score_exam(engine)
    case=_next_case(engine)
    total=score.get("total",TARGET); labeled=score.get("labeled",0); remaining=score.get("remaining",TARGET)
    if case:
        case_html=f"""
        <div class="case"><div class="meta">Current Case {case['ordinal']} / {total} • Remaining {remaining}</div>
        <pre>{_esc(case['raw_text'])}</pre>
        <div class="grid">
          <label>Content Class<select id="hc">{''.join(f'<option>{x}</option>' for x in CLASS_OPTIONS)}</select></label>
          <label>Transaction<select id="ht">{''.join(f'<option>{x}</option>' for x in TX_OPTIONS)}</select></label>
          <label>Ownership<select id="ho">{''.join(f'<option>{x}</option>' for x in OWN_OPTIONS)}</select></label>
        </div>
        <label>Reason / note (optional)<textarea id="reason"></textarea></label>
        <button id="save" onclick="saveCase()">Save Independent Label</button>
        <div id="saved"></div>
        <script>
        async function saveCase(){{
          const b=document.getElementById('save'); b.disabled=true;
          const res=await fetch('/api/property-brain/championship-v410/save',{{method:'POST',headers:{{'Content-Type':'application/json'}},
            body:JSON.stringify({{audit_id:'{case['audit_id']}',human_class:hc.value,human_transaction:ht.value,human_ownership:ho.value,human_reason:reason.value}})}});
          const j=await res.json();
          if(!res.ok){{b.disabled=false; saved.innerHTML='<div class="bad">'+(j.error||'Save failed')+'</div>';return;}}
          saved.innerHTML='<div class="ok">✓ SAVED Case {case['ordinal']} — '+j.labels.class+' | '+j.labels.transaction+' | '+j.labels.ownership+'</div>';
          setTimeout(()=>location.reload(),900);
        }}
        </script>"""
    else:
        case_html="<div class='ok'>✓ All V4 cases have independent human labels.</div>"

    # Hide machine predictions and score until exam complete.
    if remaining:
        result_html=f"<div class='gate'>V4 FROZEN • {labeled}/{total} completed • Machine predictions are hidden.</div>"
    else:
        result_html=f"<div class='gate'>{_esc(score.get('expertise_gate'))} • Overall {score.get('accuracy')}% • Class {score.get('field_accuracy',{}).get('class')}% • Transaction {score.get('field_accuracy',{}).get('transaction')}% • Ownership {score.get('field_accuracy',{}).get('ownership')}%</div><pre>{_esc(json.dumps(score,ensure_ascii=False,indent=2))}</pre>"

    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Alliance CRE Championship V4</title>
    <style>body{{font-family:Arial;margin:30px;background:#f5f7fb;color:#172033;max-width:1100px}}h1{{margin-bottom:4px}}.sub{{color:#667085}}
    .case{{background:white;padding:22px;border-radius:14px;box-shadow:0 2px 12px #0001;margin-top:18px}}pre{{white-space:pre-wrap;background:#101624;color:#eef3ff;padding:18px;border-radius:10px;line-height:1.45}}
    .grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}select,textarea{{width:100%;padding:10px;margin-top:5px;box-sizing:border-box}}textarea{{min-height:70px}}
    button{{margin-top:14px;padding:12px 18px;border:0;border-radius:9px;background:#172033;color:white;font-weight:700}}.meta{{font-weight:700;margin-bottom:10px}}
    .ok{{padding:14px;background:#e8f8ee;border-radius:10px;margin-top:12px;font-weight:700}}.bad{{padding:14px;background:#fdecec;border-radius:10px;margin-top:12px}}
    .gate{{padding:16px;background:#fff4cf;border-radius:12px;margin:18px 0;font-weight:700}}</style></head><body>
    <h1>Alliance CRE Championship — Independent Blind V4</h1>
    <div class='sub'>Fresh unseen cases • 4.0.2 predictor frozen • no tuning • no machine answer shown • certification only</div>
    {result_html}{case_html}
    <p><b>Pass standard:</b> ≥95% overall field accuracy and ≥90% in Class, Transaction and Ownership. This exam score becomes immutable once complete.</p>
    </body></html>"""

def register(core):
    engine=_engine(core); app=_app(core); _install(engine)
    freeze_exam(engine)

    if not foundation._route_exists(app,"/api/property-brain/championship-v410/status"):
        @app.get("/api/property-brain/championship-v410/status")
        def status_v410():
            return {"version":VERSION,"mode":MODE,"freeze":freeze_exam(engine),"score":score_exam(engine),
                    "predictor_version":v402.VERSION,
                    "safety":{"production_writes":0,"whatsapp_writes":0,"gold_v1_mutations":0,"gold_v2_mutations":0}}
    if not foundation._route_exists(app,"/api/property-brain/championship-v410/save"):
        @app.post("/api/property-brain/championship-v410/save")
        async def save_v410(request:Request):
            return await _save(request,engine)
    if not foundation._route_exists(app,"/property-brain/championship-v410"):
        @app.get("/property-brain/championship-v410",response_class=HTMLResponse)
        def page_v410():
            return HTMLResponse(_dashboard(engine))

    return {"status":"REGISTERED","version":VERSION,"route":"/property-brain/championship-v410",
            "exam_version":EXAM_VERSION,"target":TARGET,
            "policy":"INDEPENDENT_BLIND_CERTIFICATION_NO_TUNING_UNTIL_SCORE_FROZEN",
            "production_writes":0,"whatsapp_writes":0,"gold_mutations":0}
