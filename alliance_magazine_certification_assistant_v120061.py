
from __future__ import annotations

import html, json, threading, traceback
from datetime import datetime, timezone
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

VERSION = "12.0.6.1-EVIDENCE-CERTIFICATION-ASSISTANT-ROUTE-FIX"
STAGE = "pi_magazine_golden_stage_v12003"
CERT = "pi_magazine_certification_v12004"
DUPMAP = "pi_magazine_duplicate_map_v12005"
DUPDEC = "pi_magazine_duplicate_decisions_v12005"
ASSIST = "pi_magazine_certification_assist_v120061"
RUNS = "pi_magazine_certification_assist_runs_v120061"
LOCK_KEY = 120061001

STATE = {
    "status":"IDLE","phase":"WAITING","started_at":None,"completed_at":None,
    "rows_total":0,"rows_scored":0,"ready_high":0,"review_medium":0,
    "needs_evidence":0,"exclude_candidate":0,"already_certified":0,
    "human_rejected":0,"duplicate_pending":0,"error":None,"details":{}
}
LOCK = threading.Lock()

def _now(): return datetime.now(timezone.utc).isoformat()
def _app(core): return getattr(core, "app", None) or core
def _engine(core): return getattr(core, "engine", None)
def _login(core, req):
    fn = getattr(core, "need_login", None)
    return fn(req) if fn else "team"

def _setup(e):
    with e.begin() as c:
        for t in (STAGE, CERT, DUPMAP, DUPDEC):
            if not c.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t":t}).scalar():
                raise RuntimeError(f"Required dependency missing: {t}")
        c.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {ASSIST}(
              source_id TEXT PRIMARY KEY,
              recommendation TEXT NOT NULL,
              assistant_score INTEGER NOT NULL,
              reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
              blockers JSONB NOT NULL DEFAULT '[]'::jsonb,
              location_confidence INTEGER,
              quality_status TEXT,
              property_status TEXT,
              source_conflict BOOLEAN NOT NULL DEFAULT FALSE,
              duplicate_group TEXT,
              duplicate_decision TEXT,
              version TEXT NOT NULL,
              updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        c.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {RUNS}(
              id BIGSERIAL PRIMARY KEY, version TEXT NOT NULL, status TEXT NOT NULL,
              started_at TIMESTAMPTZ DEFAULT NOW(), completed_at TIMESTAMPTZ,
              summary JSONB NOT NULL DEFAULT '{{}}'::jsonb
            )
        """))

def _score(r):
    reasons=[]; blockers=[]; score=0
    q=str(r.get("quality_status") or "")
    ps=str(r.get("property_status") or "")
    loc=str(r.get("canonical_location") or "")
    conf=int(r.get("location_confidence") or 0)
    conflict=bool(r.get("conflict"))
    cert=str(r.get("cert_decision") or "PENDING")
    dg=r.get("duplicate_group")
    dd=str(r.get("duplicate_decision") or "PENDING")

    if cert in ("AUTO_GOLD","HUMAN_APPROVED"):
        return "ALREADY_CERTIFIED",100,["Already certified"],[]
    if cert=="HUMAN_REJECTED":
        return "HUMAN_REJECTED",0,[],["Human rejected"]
    if q=="EXCLUDED_NON_PROPERTY" or ps=="NON_PROPERTY":
        return "EXCLUDE_CANDIDATE",0,[],["Non-property classification"]

    if loc and loc.upper() not in {"MISSING","UNKNOWN","N/A","NA","NONE","NULL","UNSPECIFIED"}:
        score += min(conf,40); reasons.append(f"Governed locality confidence {conf}")
    else:
        blockers.append("No governed locality")

    if q=="SILVER":
        score += 20; reasons.append("Operational SILVER record")
    elif q=="REVIEW":
        score += 8; blockers.append("Reconciliation status REVIEW")
    elif q=="QUARANTINED":
        blockers.append("Reconciliation status QUARANTINED")

    if ps and ps not in {"NON_PROPERTY","WEAK_PROPERTY_EVIDENCE"}:
        score += 15; reasons.append(f"Property evidence: {ps}")
    else:
        blockers.append("Property identity evidence incomplete")

    if conflict:
        blockers.append("Source conflict requires explicit human resolution")
        score=max(0,score-35)

    if dg and dd=="PENDING":
        blockers.append("Pending duplicate decision")
        score=max(0,score-20)
    elif dg and dd=="SAME_PROPERTY":
        reasons.append("Duplicate group human-confirmed SAME_PROPERTY")
    elif dg and dd=="KEEP_SEPARATE":
        reasons.append("Duplicate group human-confirmed KEEP_SEPARATE")

    if blockers:
        rec="REVIEW_MEDIUM" if score>=55 and not conflict and "No governed locality" not in blockers else "NEEDS_EVIDENCE"
    else:
        rec="READY_HIGH" if score>=70 else "REVIEW_MEDIUM"
    return rec,min(score,99),reasons,blockers

def _build(core):
    e=_engine(core)
    if e is None:return
    with LOCK:
        if STATE["status"]=="RUNNING":return
        STATE.update({"status":"RUNNING","phase":"READING","started_at":_now(),"completed_at":None,
                      "rows_total":0,"rows_scored":0,"ready_high":0,"review_medium":0,
                      "needs_evidence":0,"exclude_candidate":0,"already_certified":0,
                      "human_rejected":0,"duplicate_pending":0,"error":None,"details":{}})
    conn=None; run_id=None
    try:
        _setup(e)
        conn=e.connect()
        if not bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"),{"k":LOCK_KEY}).scalar()):
            STATE.update({"status":"SKIPPED","phase":"ANOTHER_RUN_ACTIVE","completed_at":_now()}); return
        with e.begin() as c:
            run_id=c.execute(text(f"INSERT INTO {RUNS}(version,status) VALUES(:v,'RUNNING') RETURNING id"),{"v":VERSION}).scalar()
        with e.connect() as c:
            rows=[dict(x) for x in c.execute(text(f"""
                SELECT g.source_id,g.canonical_location,g.location_confidence,g.quality_status,
                       g.property_status,g.conflict,COALESCE(c.decision,'PENDING') cert_decision,
                       d.duplicate_group,COALESCE(dd.decision,'PENDING') duplicate_decision
                FROM {STAGE} g
                LEFT JOIN {CERT} c ON c.source_id=g.source_id
                LEFT JOIN {DUPMAP} d ON d.source_id=g.source_id
                LEFT JOIN {DUPDEC} dd ON dd.duplicate_group=d.duplicate_group
                WHERE g.version='12.0.3-SINGLE-WRITER-GOLDEN-DATA'
                ORDER BY g.source_id
            """)).mappings().all()]
        if not rows: raise RuntimeError("No governed rows available.")
        STATE["rows_total"]=len(rows)
        counters={}
        with e.begin() as c:
            for r in rows:
                rec,score,reasons,blockers=_score(r)
                counters[rec]=counters.get(rec,0)+1
                c.execute(text(f"""
                    INSERT INTO {ASSIST}(source_id,recommendation,assistant_score,reasons,blockers,
                      location_confidence,quality_status,property_status,source_conflict,
                      duplicate_group,duplicate_decision,version,updated_at)
                    VALUES(:sid,:rec,:score,CAST(:rs AS JSONB),CAST(:bs AS JSONB),:lc,:qs,:ps,:cf,:dg,:dd,:v,NOW())
                    ON CONFLICT(source_id) DO UPDATE SET recommendation=EXCLUDED.recommendation,
                      assistant_score=EXCLUDED.assistant_score,reasons=EXCLUDED.reasons,blockers=EXCLUDED.blockers,
                      location_confidence=EXCLUDED.location_confidence,quality_status=EXCLUDED.quality_status,
                      property_status=EXCLUDED.property_status,source_conflict=EXCLUDED.source_conflict,
                      duplicate_group=EXCLUDED.duplicate_group,duplicate_decision=EXCLUDED.duplicate_decision,
                      version=EXCLUDED.version,updated_at=NOW()
                """),{"sid":r["source_id"],"rec":rec,"score":score,"rs":json.dumps(reasons),
                       "bs":json.dumps(blockers),"lc":r.get("location_confidence"),"qs":r.get("quality_status"),
                       "ps":r.get("property_status"),"cf":bool(r.get("conflict")),"dg":r.get("duplicate_group"),
                       "dd":r.get("duplicate_decision"),"v":VERSION})
        with e.connect() as c:
            dp=int(c.execute(text(f"""SELECT COUNT(DISTINCT duplicate_group) FROM {DUPDEC}
                WHERE decision='PENDING' AND duplicate_group IN (SELECT DISTINCT duplicate_group FROM {DUPMAP})""")).scalar() or 0)
        STATE.update({"status":"PASS","phase":"COMPLETE","completed_at":_now(),"rows_scored":len(rows),
          "ready_high":counters.get("READY_HIGH",0),"review_medium":counters.get("REVIEW_MEDIUM",0),
          "needs_evidence":counters.get("NEEDS_EVIDENCE",0),"exclude_candidate":counters.get("EXCLUDE_CANDIDATE",0),
          "already_certified":counters.get("ALREADY_CERTIFIED",0),"human_rejected":counters.get("HUMAN_REJECTED",0),
          "duplicate_pending":dp,"details":{"raw_master_mutation":"NONE","certification_mutation":"NONE",
            "policy":"REVIEW_ONLY_NO_AUTOCERTIFICATION","route_fix":True}})
        if run_id:
            with e.begin() as c:
                c.execute(text(f"UPDATE {RUNS} SET status='PASS',completed_at=NOW(),summary=CAST(:s AS JSONB) WHERE id=:id"),
                          {"id":run_id,"s":json.dumps(STATE,default=str)})
    except Exception as exc:
        STATE.update({"status":"ERROR","phase":"FAILED","completed_at":_now(),
                      "error":f"{type(exc).__name__}: {exc}",
                      "details":{"trace":traceback.format_exc()[-5000:]}})
    finally:
        if conn is not None:
            try: conn.execute(text("SELECT pg_advisory_unlock(:k)"),{"k":LOCK_KEY})
            except Exception: pass
            try: conn.close()
            except Exception: pass

def _start(core):
    threading.Thread(target=_build,args=(core,),daemon=True,name="mag-cert-assist-120061").start()

def register(core):
    app=_app(core); e=_engine(core)
    if app is None or e is None: raise RuntimeError("12.0.6.1 requires app + engine")
    _setup(e)

    @app.get("/api/alliance/admin/magazine-certification-assistant/status")
    def assistant_status():
        return JSONResponse(STATE)

    @app.post("/api/alliance/admin/magazine-certification-assistant/rebuild")
    def assistant_rebuild():
        _start(core); return JSONResponse({"status":"STARTED","version":VERSION})

    @app.get("/alliance/admin/magazine-certification-assistant",response_class=HTMLResponse)
    def assistant_page(req:Request,bucket:str="READY_HIGH",page:int=1):
        _login(core,req)
        bucket=str(bucket or "READY_HIGH").upper()
        valid={"READY_HIGH","REVIEW_MEDIUM","NEEDS_EVIDENCE","EXCLUDE_CANDIDATE","ALREADY_CERTIFIED"}
        if bucket not in valid: bucket="READY_HIGH"
        page=max(1,int(page or 1)); per=40; off=(page-1)*per
        with e.connect() as c:
            counts={r[0]:int(r[1]) for r in c.execute(text(f"SELECT recommendation,COUNT(*) FROM {ASSIST} GROUP BY recommendation")).all()}
            rows=c.execute(text(f"""
              SELECT a.*,m.original_raw_text,g.canonical_location FROM {ASSIST} a
              JOIN pi_magazine_master m ON CAST(m.source_id AS TEXT)=a.source_id
              JOIN {STAGE} g ON g.source_id=a.source_id
              WHERE a.recommendation=:b ORDER BY a.assistant_score DESC,a.source_id
              LIMIT :lim OFFSET :off
            """),{"b":bucket,"lim":per,"off":off}).mappings().all()
        cards=[]
        for r in rows:
            rs=r["reasons"] if isinstance(r["reasons"],list) else []
            bs=r["blockers"] if isinstance(r["blockers"],list) else []
            cards.append(f"""<div class='card'><b>{html.escape(str(r['source_id']))}</b> · score {r['assistant_score']}
            <br><b>Location:</b> {html.escape(str(r['canonical_location'] or 'MISSING'))}
            <br><b>Description:</b> {html.escape(str(r['original_raw_text'] or ''))}
            <br><b>Reasons:</b> {html.escape('; '.join(map(str,rs)) or '—')}
            <br><b>Blockers:</b> {html.escape('; '.join(map(str,bs)) or '—')}</div>""")
        return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'><title>Certification Assistant</title>
        <style>body{{font-family:Arial;margin:24px;background:#f5f2eb}}.card,.top{{background:white;padding:14px;border:1px solid #ddd;border-radius:10px;margin:10px 0}}</style>
        </head><body><h1>Magazine Certification Assistant · 12.0.6.1</h1>
        <div class='top'><b>Review only.</b> No auto-certification, rejection, merge, or raw master mutation.<br>
        Ready High <b>{counts.get('READY_HIGH',0)}</b> · Review Medium <b>{counts.get('REVIEW_MEDIUM',0)}</b> ·
        Needs Evidence <b>{counts.get('NEEDS_EVIDENCE',0)}</b> · Exclude Candidate <b>{counts.get('EXCLUDE_CANDIDATE',0)}</b> ·
        Already Certified <b>{counts.get('ALREADY_CERTIFIED',0)}</b><br><br>
        <a href='/alliance/admin/magazine-duplicate-review'>Duplicate Review</a> ·
        <a href='/api/alliance/admin/magazine-certification-assistant/status'>Status JSON</a></div>
        {''.join(cards) if cards else "<div class='card'>No records in this bucket.</div>"}
        <p><a href='?bucket={bucket}&page={page+1}'>Next page →</a></p></body></html>""")
    _start(core)
    return {"status":"REGISTERED","version":VERSION,"status_api":"/api/alliance/admin/magazine-certification-assistant/status"}

