from __future__ import annotations
import html, json
from typing import Any, Dict, Optional
from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import inspect, text
import alliance_property_brain_foundation_v1 as foundation
import alliance_requirement_brain_v3 as brain

VERSION="1.0.0-TEAM-READY-GOLD-CERTIFICATION"
GOLD_TARGET=100
OVERALL_TARGET=98.0
CRITICAL_TARGET=99.0
SOURCE="TEAM_READY_GOLD_V1"
_STATE={"status":"INIT","version":VERSION,"route_authority":"INIT","routes_registered":False,"database_ready":False,"seeded":0,"startup_error":None}

class GoldReview(BaseModel):
    reviewer:str
    notes:Optional[str]=None
    role:str
    transaction:Optional[str]=None
    asset:Optional[str]=None
    locations:list[str]=[]
    budget_min:Optional[float]=None
    budget_max:Optional[float]=None
    area_min:Optional[float]=None
    area_max:Optional[float]=None

def _app(core): return getattr(core,"app",core)
def _engine(core): return foundation._engine_from_core(core)

def _ensure(engine):
    with engine.begin() as c:
        c.execute(text("""CREATE TABLE IF NOT EXISTS pi_alliance_gold_cases_v1(
            id BIGSERIAL PRIMARY KEY,
            requirement_id BIGINT NOT NULL UNIQUE,
            source_type TEXT,
            raw_text TEXT NOT NULL,
            prediction JSONB NOT NULL,
            brain_version TEXT NOT NULL,
            review_status TEXT NOT NULL DEFAULT 'PENDING',
            reviewer TEXT,
            reviewer_notes TEXT,
            gold_role TEXT,
            gold_transaction TEXT,
            gold_asset TEXT,
            gold_locations JSONB,
            gold_budget_min NUMERIC,
            gold_budget_max NUMERIC,
            gold_area_min NUMERIC,
            gold_area_max NUMERIC,
            reviewed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )"""))
        c.execute(text("""CREATE INDEX IF NOT EXISTS idx_alliance_gold_cases_review_v1
                           ON pi_alliance_gold_cases_v1(review_status,id)"""))

def _has_table(engine,name):
    try: return name in set(inspect(engine).get_table_names())
    except Exception: return False

def _json(v):
    if isinstance(v,(dict,list)): return v
    if not v: return None
    try: return json.loads(v)
    except Exception: return v

def _n(v):
    if v is None or v=="": return None
    try: return float(v)
    except Exception: return None

def _norm(v):
    if v is None: return None
    if isinstance(v,str):
        s=" ".join(v.upper().split())
        return s or None
    return v

def _list_norm(v):
    if not isinstance(v,list): return []
    return sorted({x for x in (_norm(i) for i in v) if x})

def _pred_fields(p):
    intent=p.get("intent") or {}
    tx=p.get("transaction") or {}
    asset=p.get("asset") or {}
    area=p.get("area") or {}
    budget=p.get("budget") or {}
    loc=p.get("location") or p.get("locations") or {}
    locations=[]
    if isinstance(loc,dict):
        for k in ("primary_locations","locations","values","candidates"):
            if isinstance(loc.get(k),list):
                locations=loc.get(k); break
        if not locations and loc.get("primary"): locations=[loc.get("primary")]
    elif isinstance(loc,list):
        locations=loc
    return {
        "role":_norm(intent.get("role") or p.get("role")),
        "transaction":_norm(tx.get("value") if isinstance(tx,dict) else tx),
        "asset":_norm(asset.get("primary_asset") if isinstance(asset,dict) else asset),
        "locations":_list_norm(locations),
        "budget_min":_n(budget.get("min") if isinstance(budget,dict) else None),
        "budget_max":_n(budget.get("max") if isinstance(budget,dict) else None),
        "area_min":_n(area.get("sqft_min") if isinstance(area,dict) else None),
        "area_max":_n(area.get("sqft_max") if isinstance(area,dict) else None),
    }

def _seed(engine,target=GOLD_TARGET):
    if not _has_table(engine,"pi_requirement_gate_v1191"):
        raise RuntimeError("MASTER_REQUIREMENT_AUTHORITY_MISSING")
    with engine.connect() as c:
        rows=c.execute(text("""SELECT id,source_type,original_message
            FROM pi_requirement_gate_v1191
            WHERE original_message IS NOT NULL
              AND BTRIM(original_message)<>''
              AND id NOT IN (SELECT requirement_id FROM pi_alliance_gold_cases_v1)
            ORDER BY md5(CAST(id AS text) || COALESCE(source_type,''))
            LIMIT :lim"""),{"lim":int(target)}).mappings().all()
    added=0
    for r in rows:
        raw=str(r.get("original_message") or "").strip()
        if not raw: continue
        try: pred=brain.analyze(raw,source=SOURCE)
        except TypeError: pred=brain.analyze(raw)
        with engine.begin() as c:
            c.execute(text("""INSERT INTO pi_alliance_gold_cases_v1(
                requirement_id,source_type,raw_text,prediction,brain_version
            ) VALUES(:rid,:source,:raw,CAST(:prediction AS JSONB),:bv)
            ON CONFLICT(requirement_id) DO NOTHING"""),{
                "rid":int(r["id"]),"source":str(r.get("source_type") or ""),"raw":raw,
                "prediction":json.dumps(pred,default=str),"bv":getattr(brain,"VERSION","UNKNOWN")})
        added+=1
    return added

def _summary(engine):
    with engine.connect() as c:
        rows=c.execute(text("""SELECT prediction,review_status,gold_role,gold_transaction,gold_asset,
            gold_locations,gold_budget_min,gold_budget_max,gold_area_min,gold_area_max
            FROM pi_alliance_gold_cases_v1 ORDER BY id""")).mappings().all()
    reviewed=comparable=correct=critical_comp=critical_correct=0
    critical={"role","transaction","asset","locations"}
    for rr in rows:
        r=dict(rr)
        if str(r.get("review_status") or "").upper()!="REVIEWED": continue
        reviewed+=1
        pred=_pred_fields(_json(r.get("prediction")) or {})
        gold={"role":_norm(r.get("gold_role")),"transaction":_norm(r.get("gold_transaction")),
              "asset":_norm(r.get("gold_asset")),"locations":_list_norm(_json(r.get("gold_locations")) or []),
              "budget_min":_n(r.get("gold_budget_min")),"budget_max":_n(r.get("gold_budget_max")),
              "area_min":_n(r.get("gold_area_min")),"area_max":_n(r.get("gold_area_max"))}
        for field,g in gold.items():
            if g is None: continue
            comparable+=1
            ok=pred.get(field)==g
            if ok: correct+=1
            if field in critical:
                critical_comp+=1
                if ok: critical_correct+=1
    overall=round(correct*100/comparable,2) if comparable else None
    critical_acc=round(critical_correct*100/critical_comp,2) if critical_comp else None
    certification="CERTIFIED" if reviewed>=GOLD_TARGET and overall is not None and overall>=OVERALL_TARGET and critical_acc is not None and critical_acc>=CRITICAL_TARGET else "NOT_CERTIFIED"
    return {"status":"OK","version":VERSION,"gold_target":GOLD_TARGET,"gold_cases":len(rows),"reviewed":reviewed,
            "pending":max(len(rows)-reviewed,0),"overall_accuracy_pct":overall,"critical_accuracy_pct":critical_acc,
            "overall_target_pct":OVERALL_TARGET,"critical_target_pct":CRITICAL_TARGET,"certification":certification,
            "production_requirement_mutation":False,"matcher_eligibility_auto_changed":False,
            "matcher_contract_required":"MASTER_ONLY","feature_freeze":"ACTIVE"}

def _case(engine,cid):
    with engine.connect() as c:
        r=c.execute(text("SELECT * FROM pi_alliance_gold_cases_v1 WHERE id=:id"),{"id":int(cid)}).mappings().first()
    if not r: raise HTTPException(status_code=404,detail="Gold case not found")
    return dict(r)

def _save(engine,cid,payload):
    reviewer=str(payload.reviewer or "").strip()
    if len(reviewer)<2: raise HTTPException(status_code=400,detail="Reviewer name required")
    role=_norm(payload.role)
    if role not in {"REQUIREMENT","SUPPLY","NOISE","UNKNOWN"}:
        raise HTTPException(status_code=400,detail="Invalid role")
    with engine.begin() as c:
        result=c.execute(text("""UPDATE pi_alliance_gold_cases_v1 SET
            review_status='REVIEWED',reviewer=:reviewer,reviewer_notes=:notes,
            gold_role=:role,gold_transaction=:transaction,gold_asset=:asset,
            gold_locations=CAST(:locations AS JSONB),gold_budget_min=:bmin,gold_budget_max=:bmax,
            gold_area_min=:amin,gold_area_max=:amax,reviewed_at=NOW(),updated_at=NOW()
            WHERE id=:id"""),{"id":int(cid),"reviewer":reviewer,"notes":(payload.notes or "")[:5000] or None,
            "role":role,"transaction":_norm(payload.transaction),"asset":_norm(payload.asset),
            "locations":json.dumps(_list_norm(payload.locations)),"bmin":payload.budget_min,"bmax":payload.budget_max,
            "amin":payload.area_min,"amax":payload.area_max})
        if result.rowcount!=1: raise HTTPException(status_code=404,detail="Gold case not found")
    return {"status":"REVIEW_SAVED","case_id":int(cid),"production_requirement_mutation":False}

def _e(v): return html.escape("" if v is None else str(v))

def _queue_html(engine):
    s=_summary(engine)
    with engine.connect() as c:
        rows=c.execute(text("""SELECT id,requirement_id,source_type,raw_text,review_status
            FROM pi_alliance_gold_cases_v1
            ORDER BY CASE WHEN review_status='PENDING' THEN 0 ELSE 1 END,id LIMIT 120""")).mappings().all()
    trs=[]
    for r in rows:
        trs.append(f"<tr><td>{r['id']}</td><td>{r['requirement_id']}</td><td>{_e(r['source_type'])}</td><td>{_e(r['review_status'])}</td><td>{_e(str(r['raw_text'])[:220])}</td><td><a href='/alliance/primary/team-ready-certification/review/{r['id']}'>Review</a></td></tr>")
    return f"""<!doctype html><meta charset='utf-8'><title>Alliance Team Ready Certification</title>
    <style>body{{font-family:Arial;margin:24px;max-width:1500px}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:8px;vertical-align:top}}</style>
    <h1>Alliance Team-Ready Gold Certification V1</h1>
    <p><b>Reviewed {s['reviewed']}/{s['gold_target']}</b> · Overall {s['overall_accuracy_pct']} · Critical {s['critical_accuracy_pct']} · {s['certification']}</p>
    <p>Blind human gold review. This console does not update master requirements or matcher eligibility.</p>
    <table><tr><th>Case</th><th>Requirement</th><th>Source</th><th>Status</th><th>Raw requirement</th><th>Action</th></tr>{''.join(trs)}</table>"""

def _review_html(engine,cid):
    r=_case(engine,cid)
    p=_pred_fields(_json(r.get("prediction")) or {})
    return f"""<!doctype html><meta charset='utf-8'><title>Gold Review #{cid}</title>
    <style>body{{font-family:Arial;margin:24px;max-width:1100px}}input,textarea,select{{width:100%;padding:8px;margin:4px 0 12px}}button{{padding:10px 18px}}</style>
    <a href='/alliance/primary/team-ready-certification'>← Queue</a><h1>Blind Gold Review #{cid}</h1>
    <div style='white-space:pre-wrap;border:1px solid #ddd;padding:14px'>{_e(r['raw_text'])}</div>
    <p>Label only what the raw requirement actually says.</p>
    <label>Reviewer</label><input id='reviewer'><label>Role</label>
    <select id='role'><option>REQUIREMENT</option><option>SUPPLY</option><option>NOISE</option><option>UNKNOWN</option></select>
    <label>Transaction</label><input id='transaction'><label>Asset</label><input id='asset'>
    <label>Locations (comma separated)</label><input id='locations'>
    <label>Budget min</label><input id='bmin' type='number' step='any'><label>Budget max</label><input id='bmax' type='number' step='any'>
    <label>Area min sqft</label><input id='amin' type='number' step='any'><label>Area max sqft</label><input id='amax' type='number' step='any'>
    <label>Notes</label><textarea id='notes'></textarea><button onclick='save()'>Save independent gold label</button>
    <details><summary>AI prediction — open only after labeling</summary><pre>{_e(json.dumps(p,indent=2,default=str))}</pre></details><pre id='out'></pre>
    <script>
    function num(id){{const v=document.getElementById(id).value.trim();return v===''?null:Number(v)}}
    async function save(){{
      const payload={{reviewer:reviewer.value,notes:notes.value,role:role.value,transaction:transaction.value||null,asset:asset.value||null,
      locations:locations.value.split(',').map(x=>x.trim()).filter(Boolean),budget_min:num('bmin'),budget_max:num('bmax'),area_min:num('amin'),area_max:num('amax')}};
      const res=await fetch('/alliance/primary/team-ready-certification/review/{cid}',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload)}});
      const data=await res.json();out.textContent=JSON.stringify(data,null,2);if(res.ok)setTimeout(()=>location.href='/alliance/primary/team-ready-certification',500);
    }}
    </script>"""

def register(core)->Dict[str,Any]:
    app=_app(core); engine=_engine(core)
    if app is None: raise RuntimeError("TEAM_READY_APP_UNAVAILABLE")
    paths={getattr(r,"path",None) for r in app.router.routes}
    if "/api/alliance/team-ready-certification-v1/status" not in paths:
        @app.get("/api/alliance/team-ready-certification-v1/status")
        def team_ready_cert_status():
            if engine is None or not _STATE.get("database_ready"): return {**_STATE,"certification":"NOT_CERTIFIED"}
            return {**_STATE,**_summary(engine)}
    if "/alliance/primary/team-ready-certification" not in paths:
        @app.get("/alliance/primary/team-ready-certification",response_class=HTMLResponse)
        def team_ready_cert_queue():
            if engine is None or not _STATE.get("database_ready"): raise HTTPException(status_code=503,detail="Certification database unavailable")
            return HTMLResponse(_queue_html(engine))
    if "/alliance/primary/team-ready-certification/review/{case_id}" not in paths:
        @app.get("/alliance/primary/team-ready-certification/review/{case_id}",response_class=HTMLResponse)
        def team_ready_cert_review(case_id:int): return HTMLResponse(_review_html(engine,case_id))
        @app.post("/alliance/primary/team-ready-certification/review/{case_id}")
        def team_ready_cert_save(case_id:int,payload:GoldReview): return _save(engine,case_id,payload)
    _STATE["route_authority"]="LIVE"; _STATE["routes_registered"]=True
    if engine is None:
        _STATE["status"]="ERROR"; _STATE["startup_error"]="DATABASE_ENGINE_UNAVAILABLE"; return dict(_STATE)
    try:
        _ensure(engine); _STATE["database_ready"]=True; _STATE["seeded"]=_seed(engine,GOLD_TARGET); _STATE["status"]="READY"; _STATE["startup_error"]=None
    except Exception as exc:
        _STATE["status"]="ERROR"; _STATE["startup_error"]=f"{type(exc).__name__}: {exc}"
    return dict(_STATE)
