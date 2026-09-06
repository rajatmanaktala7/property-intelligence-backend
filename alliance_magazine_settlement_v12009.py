
from __future__ import annotations
import html, json, re, threading, time, traceback
from collections import defaultdict
from datetime import datetime, timezone
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

VERSION="12.0.9-WORKABLE-DATABASE-SETTLEMENT"
RECT="pi_magazine_rectified_v12008"
STAGE="pi_magazine_golden_stage_v12003"
REC="pi_magazine_evidence_recovery_v12007"
CERT="pi_magazine_certification_v12004"
DUPMAP="pi_magazine_duplicate_map_v12005"
DUPDEC="pi_magazine_duplicate_decisions_v12005"
CONFLICT_DEC="pi_magazine_conflict_decisions_v12007"
SETTLED="pi_magazine_settled_v12009"
DUPFAM="pi_magazine_duplicate_families_v12009"
RUNS="pi_magazine_settlement_runs_v12009"
LOCK_KEY=120090001

BAD={"","MISSING","UNKNOWN","N/A","NA","NONE","NULL","UNSPECIFIED"}
PHONE_RE=re.compile(r"(?<!\d)(?:[6-9]\d{9}|0?11[-\s]?\d{7,8})(?!\d)")
AREA_RE=re.compile(r"(?i)\b(\d{2,7}(?:\.\d+)?)\s*(SQ\.?\s*FT|SQFT|FT|SQ\.?\s*YD|SQYD|YD|Y|SQ\.?\s*M|SQM|ACRE)\b")
FLOOR_RE=re.compile(r"(?i)\b(BMT|BASEMENT|LGF|UGF|GF|GROUND\s*FLOOR|FF|FIRST\s*FLOOR|SF|SECOND\s*FLOOR|TF|THIRD\s*FLOOR|MEZZ|\d+(?:ST|ND|RD|TH)?\s*FLOOR)\b")
ADDRESS_RE=re.compile(r"^\s*(\d+[A-Z]?(?:/[0-9A-Z/-]+)+|[A-Z]{1,4}[-/]\d+[A-Z]?(?:/[0-9A-Z/-]+)*|[A-Z]{0,4}\d+[A-Z]?|\d+[A-Z]?)\b",re.I)

ALIASES={
"OKHLA-1":"Okhla Phase 1","OKHLA 1":"Okhla Phase 1","OKHLA PHASE I":"Okhla Phase 1","OKHLA PHASE 1":"Okhla Phase 1","OKHLA PHASE-1":"Okhla Phase 1",
"OKHLA-2":"Okhla Phase 2","OKHLA 2":"Okhla Phase 2","OKHLA PHASE II":"Okhla Phase 2","OKHLA PHASE 2":"Okhla Phase 2","OKHLA PHASE-2":"Okhla Phase 2",
"OKHLA-3":"Okhla Phase 3","OKHLA 3":"Okhla Phase 3","OKHLA PHASE III":"Okhla Phase 3","OKHLA PHASE 3":"Okhla Phase 3","OKHLA PHASE-3":"Okhla Phase 3",
"LAJPAT NAGAR-1":"Lajpat Nagar 1","LAJPAT NAGAR I":"Lajpat Nagar 1","LAJPAT NAGAR 1":"Lajpat Nagar 1",
"LAJPAT NAGAR-2":"Lajpat Nagar 2","LAJPAT NAGAR II":"Lajpat Nagar 2","LAJPAT NAGAR 2":"Lajpat Nagar 2",
"LAJPAT NAGAR-3":"Lajpat Nagar 3","LAJPAT NAGAR III":"Lajpat Nagar 3","LAJPAT NAGAR 3":"Lajpat Nagar 3",
"LAJPAT NAGAR-4":"Lajpat Nagar 4","LAJPAT NAGAR IV":"Lajpat Nagar 4","LAJPAT NAGAR 4":"Lajpat Nagar 4",
"GREATER KAILASH I":"Greater Kailash 1","GREATER KAILASH-1":"Greater Kailash 1","GK-I":"Greater Kailash 1","GK 1":"Greater Kailash 1",
"GREATER KAILASH II":"Greater Kailash 2","GREATER KAILASH-2":"Greater Kailash 2","GK-II":"Greater Kailash 2","GK 2":"Greater Kailash 2",
"CR PARK":"Chitranjan Park","C R PARK":"Chitranjan Park","NFC":"New Friends Colony","CP":"Connaught Place",
"GURGAON":"Gurugram"
}
STATE={"status":"IDLE","phase":"WAITING","started_at":None,"completed_at":None,"rows_total":0,
"gold":0,"silver":0,"review":0,"quarantined":0,"excluded":0,"duplicate_families":0,
"duplicate_followers":0,"equivalence_conflicts_settled":0,"confidence93_promoted":0,
"workable_rows":0,"ai_rows":0,"error":None,"details":{}}
LOCK=threading.Lock()

def _now():return datetime.now(timezone.utc).isoformat()
def _app(c):return getattr(c,"app",None) or c
def _engine(c):return getattr(c,"engine",None)
def _login(c,r):
    f=getattr(c,"need_login",None);return f(r) if f else "team"
def _norm(v):return re.sub(r"\s+"," ",str(v or "")).strip()
def _key(v):
    s=PHONE_RE.sub(" ",_norm(v).upper());s=re.sub(r"[^A-Z0-9/]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()
def _canon(v):
    s=_norm(v);u=s.upper().strip(" ,;:|")
    if not u or u in BAD:return None
    if u in ALIASES:return ALIASES[u]
    m=re.fullmatch(r"(NOIDA|GURUGRAM|GURGAON|DWARKA|ROHINI)\s+SECTOR\s+(\d+[A-Z]?)",u)
    if m:
        city="Gurugram" if m.group(1) in {"GURGAON","GURUGRAM"} else m.group(1).title()
        return f"{city} Sector {m.group(2)}"
    return s.title() if s.isupper() else s
def _addr(d):
    m=ADDRESS_RE.search(_norm(d));return _key(m.group(1)) if m else ""
def _area(d):
    m=AREA_RE.search(_norm(d))
    if not m:return ""
    u=re.sub(r"[^A-Z]","",m.group(2).upper())
    u={"FT":"SQFT","SQFT":"SQFT","Y":"SQYD","YD":"SQYD","SQYD":"SQYD","SQM":"SQM","ACRE":"ACRE"}.get(u,u)
    return f"{m.group(1)}:{u}"
def _floor(d):
    m=FLOOR_RE.search(_norm(d))
    if not m:return ""
    u=_key(m.group(1));return {"BASEMENT":"BMT","GROUND FLOOR":"GF","FIRST FLOOR":"FF","SECOND FLOOR":"SF","THIRD FLOOR":"TF"}.get(u,u)
def _identity(loc,d):
    a,ar,fl=_addr(d),_area(d),_floor(d)
    return f"{_key(loc)}|{a}|{ar}|{fl}" if loc and a and ar and fl else ""
def _setup(e):
    with e.begin() as c:
        for t in (RECT,STAGE,REC,CERT,DUPMAP,DUPDEC,CONFLICT_DEC):
            if not c.execute(text("SELECT to_regclass(:t) IS NOT NULL"),{"t":t}).scalar():
                raise RuntimeError(f"Missing dependency {t}")
        c.execute(text(f"""CREATE TABLE IF NOT EXISTS {SETTLED}(
        source_id TEXT PRIMARY KEY,settled_location TEXT,settled_confidence INTEGER NOT NULL DEFAULT 0,
        settled_status TEXT NOT NULL,settlement_rule TEXT NOT NULL,survivor_source_id TEXT,
        duplicate_family TEXT,duplicate_rank INTEGER,ai_eligible BOOLEAN NOT NULL DEFAULT FALSE,
        operational_eligible BOOLEAN NOT NULL DEFAULT FALSE,evidence JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        version TEXT NOT NULL,updated_at TIMESTAMPTZ DEFAULT NOW())"""))
        c.execute(text(f"""CREATE TABLE IF NOT EXISTS {DUPFAM}(
        source_id TEXT PRIMARY KEY,duplicate_family TEXT,survivor_source_id TEXT,duplicate_rank INTEGER,
        reason TEXT,confidence INTEGER,version TEXT,updated_at TIMESTAMPTZ DEFAULT NOW())"""))
        c.execute(text(f"""CREATE TABLE IF NOT EXISTS {RUNS}(
        id BIGSERIAL PRIMARY KEY,version TEXT,status TEXT,started_at TIMESTAMPTZ DEFAULT NOW(),
        completed_at TIMESTAMPTZ,summary JSONB NOT NULL DEFAULT '{{}}'::jsonb)"""))

def _wait(e,timeout=150):
    end=time.monotonic()+timeout;last={}
    while time.monotonic()<end:
        with e.connect() as c:
            raw=int(c.execute(text("SELECT COUNT(*) FROM pi_magazine_master")).scalar() or 0)
            rect=int(c.execute(text(f"SELECT COUNT(*) FROM {RECT} WHERE version='12.0.8-RECTIFIED-GOLDEN-MASTER'")).scalar() or 0)
        last={"raw":raw,"rectified":rect}
        if raw>0 and rect==raw:return last
        time.sleep(2)
    raise RuntimeError("12.0.8 not ready: "+json.dumps(last))

def _views(e):
    with e.begin() as c:
        for v in ("pi_magazine_ai_training_v12009","pi_magazine_workable_v12009","pi_magazine_review_v12009","pi_magazine_golden_master_v12009"):
            c.execute(text(f"DROP VIEW IF EXISTS {v}"))
        c.execute(text(f"""CREATE VIEW pi_magazine_golden_master_v12009 AS
        SELECT m.*,s.settled_location,s.settled_confidence,s.settlement_rule
        FROM pi_magazine_master m JOIN {SETTLED} s ON s.source_id=CAST(m.source_id AS TEXT)
        WHERE s.settled_status='GOLD' AND s.ai_eligible=TRUE AND COALESCE(s.duplicate_rank,1)=1"""))
        c.execute(text(f"""CREATE VIEW pi_magazine_workable_v12009 AS
        SELECT m.*,s.settled_location,s.settled_status,s.settled_confidence,s.settlement_rule
        FROM pi_magazine_master m JOIN {SETTLED} s ON s.source_id=CAST(m.source_id AS TEXT)
        WHERE s.operational_eligible=TRUE AND s.settled_status IN ('GOLD','SILVER')
          AND COALESCE(s.duplicate_rank,1)=1"""))
        c.execute(text("CREATE VIEW pi_magazine_ai_training_v12009 AS SELECT * FROM pi_magazine_golden_master_v12009"))
        c.execute(text(f"""CREATE VIEW pi_magazine_review_v12009 AS
        SELECT m.*,s.settled_location,s.settled_status,s.settled_confidence,s.settlement_rule
        FROM pi_magazine_master m JOIN {SETTLED} s ON s.source_id=CAST(m.source_id AS TEXT)
        WHERE s.settled_status IN ('REVIEW','QUARANTINED')"""))

def _build(core):
    e=_engine(core)
    if e is None:return
    with LOCK:
        if STATE["status"]=="RUNNING":return
        STATE.update({"status":"RUNNING","phase":"WAITING_FOR_12_0_8","started_at":_now(),"completed_at":None,
        "rows_total":0,"gold":0,"silver":0,"review":0,"quarantined":0,"excluded":0,
        "duplicate_families":0,"duplicate_followers":0,"equivalence_conflicts_settled":0,
        "confidence93_promoted":0,"workable_rows":0,"ai_rows":0,"error":None,"details":{}})
    lc=None;rid=None
    try:
        _setup(e);lc=e.connect()
        if not lc.execute(text("SELECT pg_try_advisory_lock(:k)"),{"k":LOCK_KEY}).scalar():
            STATE.update({"status":"SKIPPED","phase":"ANOTHER_RUN_ACTIVE","completed_at":_now()});return
        ready=_wait(e)
        with e.begin() as c:
            rid=c.execute(text(f"INSERT INTO {RUNS}(version,status) VALUES(:v,'RUNNING') RETURNING id"),{"v":VERSION}).scalar()
        STATE["phase"]="LOADING"
        with e.connect() as c:
            rows=[dict(r) for r in c.execute(text(f"""SELECT
            r.source_id,r.final_location,r.final_location_confidence,r.final_status,r.rectification_rule,
            r.ai_eligible,r.operational_eligible,r.source_conflict,r.conflict_decision,
            r.duplicate_group,r.duplicate_decision,
            rec.recovery_status,rec.recovered_location,rec.recovery_confidence,rec.recovery_rule,rec.evidence AS recovery_evidence,
            g.evidence AS stage_evidence,m.original_raw_text
            FROM {RECT} r
            JOIN pi_magazine_master m ON CAST(m.source_id AS TEXT)=r.source_id
            LEFT JOIN {REC} rec ON rec.source_id=r.source_id
            LEFT JOIN {STAGE} g ON g.source_id=r.source_id
            WHERE r.version='12.0.8-RECTIFIED-GOLDEN-MASTER' ORDER BY r.source_id""")).mappings().all()]
        STATE["rows_total"]=len(rows)

        # First settle canonical-equivalent conflicts only. No semantic guessing.
        settled=[]
        eq_count=0; p93=0
        for r in rows:
            status=r["final_status"];loc=_canon(r.get("final_location"));conf=int(r.get("final_location_confidence") or 0)
            rule=r["rectification_rule"];ai=bool(r.get("ai_eligible"));op=bool(r.get("operational_eligible"))
            ev={"v12008_rule":rule}
            if status=="REVIEW" and rule=="UNRESOLVED_SOURCE_CONFLICT":
                candidates=[]
                se=r.get("stage_evidence") or {}
                revid=r.get("recovery_evidence") or {}
                if isinstance(se,str):
                    try:se=json.loads(se)
                    except:se={}
                if isinstance(revid,str):
                    try:revid=json.loads(revid)
                    except:revid={}
                for obj in (se.get("candidates",[]) if isinstance(se,dict) else []):
                    x=_canon(obj.get("location"))
                    if x:candidates.append(x)
                for group in ("exact","structural"):
                    for obj in (revid.get(group,[]) if isinstance(revid,dict) else []):
                        x=_canon(obj.get("location"))
                        if x:candidates.append(x)
                uniq={_key(x):x for x in candidates}
                if len(uniq)==1 and uniq:
                    loc=list(uniq.values())[0];conf=max(conf,95);status="GOLD";rule="CANONICAL_EQUIVALENCE_CONFLICT_SETTLED";ai=True;op=True;eq_count+=1
            # Promote 93 only if exact same canonical location is independently present in both stage candidate evidence and recovery evidence.
            if status=="SILVER" and rule=="RECOVERED_93_NEEDS_HUMAN_CONFIRMATION" and loc:
                stage_locs=set(); rec_sources=set()
                se=r.get("stage_evidence") or {}; revid=r.get("recovery_evidence") or {}
                if isinstance(se,str):
                    try:se=json.loads(se)
                    except:se={}
                if isinstance(revid,str):
                    try:revid=json.loads(revid)
                    except:revid={}
                for obj in (se.get("candidates",[]) if isinstance(se,dict) else []):
                    x=_canon(obj.get("location"))
                    if x and int(obj.get("confidence") or 0)>=93:stage_locs.add(_key(x))
                for group in ("exact","structural"):
                    for obj in (revid.get(group,[]) if isinstance(revid,dict) else []):
                        x=_canon(obj.get("location"))
                        if x and _key(x)==_key(loc):rec_sources.add(obj.get("source"))
                if _key(loc) in stage_locs and len({x for x in rec_sources if x})>=1:
                    status="GOLD";conf=95;rule="CONF93_INDEPENDENT_CORROBORATION";ai=True;op=True;p93+=1
            settled.append(dict(r,_status=status,_loc=loc,_conf=conf,_rule=rule,_ai=ai,_op=op,_ev=ev))

        # Conservative duplicate survivorship: only exact canonical property identity.
        fams=defaultdict(list)
        for r in settled:
            if r["_status"] not in {"GOLD","SILVER"}:continue
            ident=_identity(r["_loc"],r.get("original_raw_text"))
            if ident:fams[ident].append(r)
        family_info={}
        fam_count=followers=0
        for ident,items in fams.items():
            if len(items)<2:continue
            # Repeated identical property identity is safe to collapse operationally; raw rows remain untouched.
            items=sorted(items,key=lambda x:(0 if x["_status"]=="GOLD" else 1,-int(x["_conf"]),str(x["source_id"])))
            survivor=items[0]["source_id"];fid="SET9-"+__import__("hashlib").sha1(ident.encode()).hexdigest()[:16].upper()
            fam_count+=1
            for rank,item in enumerate(items,1):
                family_info[item["source_id"]]=(fid,survivor,rank)
                if rank>1:followers+=1

        STATE["phase"]="WRITING_SETTLED"
        counts=defaultdict(int)
        with e.begin() as c:
            c.execute(text(f"DELETE FROM {DUPFAM} WHERE version=:v"),{"v":VERSION})
            for r in settled:
                sid=r["source_id"];fam=family_info.get(sid);rank=fam[2] if fam else 1
                survivor=fam[1] if fam else sid;fid=fam[0] if fam else None
                status=r["_status"];ai=r["_ai"] and rank==1;op=r["_op"] and rank==1
                counts[status.lower()]+=1
                if fam:
                    c.execute(text(f"""INSERT INTO {DUPFAM}(source_id,duplicate_family,survivor_source_id,duplicate_rank,reason,confidence,version,updated_at)
                    VALUES(:sid,:f,:s,:r,'EXACT_CANONICAL_LOCATION_ADDRESS_AREA_FLOOR',99,:v,NOW())
                    ON CONFLICT(source_id) DO UPDATE SET duplicate_family=EXCLUDED.duplicate_family,survivor_source_id=EXCLUDED.survivor_source_id,
                    duplicate_rank=EXCLUDED.duplicate_rank,reason=EXCLUDED.reason,confidence=EXCLUDED.confidence,version=EXCLUDED.version,updated_at=NOW()"""),
                    {"sid":sid,"f":fid,"s":survivor,"r":rank,"v":VERSION})
                c.execute(text(f"""INSERT INTO {SETTLED}(source_id,settled_location,settled_confidence,settled_status,settlement_rule,
                survivor_source_id,duplicate_family,duplicate_rank,ai_eligible,operational_eligible,evidence,version,updated_at)
                VALUES(:sid,:loc,:conf,:st,:rule,:sur,:fam,:rank,:ai,:op,CAST(:ev AS JSONB),:v,NOW())
                ON CONFLICT(source_id) DO UPDATE SET settled_location=EXCLUDED.settled_location,settled_confidence=EXCLUDED.settled_confidence,
                settled_status=EXCLUDED.settled_status,settlement_rule=EXCLUDED.settlement_rule,survivor_source_id=EXCLUDED.survivor_source_id,
                duplicate_family=EXCLUDED.duplicate_family,duplicate_rank=EXCLUDED.duplicate_rank,ai_eligible=EXCLUDED.ai_eligible,
                operational_eligible=EXCLUDED.operational_eligible,evidence=EXCLUDED.evidence,version=EXCLUDED.version,updated_at=NOW()"""),
                {"sid":sid,"loc":r["_loc"],"conf":r["_conf"],"st":status,"rule":r["_rule"],"sur":survivor,
                 "fam":fid,"rank":rank,"ai":ai,"op":op,"ev":json.dumps(r["_ev"]),"v":VERSION})
        _views(e)
        with e.connect() as c:
            workable=int(c.execute(text("SELECT COUNT(*) FROM pi_magazine_workable_v12009")).scalar() or 0)
            airows=int(c.execute(text("SELECT COUNT(*) FROM pi_magazine_ai_training_v12009")).scalar() or 0)
        STATE.update({"status":"PASS","phase":"COMPLETE","completed_at":_now(),
        "gold":counts["gold"],"silver":counts["silver"],"review":counts["review"],"quarantined":counts["quarantined"],
        "excluded":counts["excluded_non_property"],"duplicate_families":fam_count,"duplicate_followers":followers,
        "equivalence_conflicts_settled":eq_count,"confidence93_promoted":p93,"workable_rows":workable,"ai_rows":airows,
        "details":{"raw_master_mutation":"NONE","v12008_mutation":"NONE","settled_table":SETTLED,
        "workable_view":"pi_magazine_workable_v12009","golden_view":"pi_magazine_golden_master_v12009",
        "ai_view":"pi_magazine_ai_training_v12009","review_view":"pi_magazine_review_v12009",
        "duplicate_policy":"exact canonical location+full address+area+floor survivorship; raw evidence retained",
        "conflict_policy":"auto-settle canonical-equivalent geography only; genuine disagreement remains review",
        "unknown_policy":"never invent locality","readiness":ready}})
        with e.begin() as c:
            c.execute(text(f"UPDATE {RUNS} SET status='PASS',completed_at=NOW(),summary=CAST(:s AS JSONB) WHERE id=:id"),
                      {"id":rid,"s":json.dumps(STATE,default=str)})
    except Exception as exc:
        STATE.update({"status":"ERROR","phase":"FAILED","completed_at":_now(),"error":f"{type(exc).__name__}: {exc}",
        "details":{"trace":traceback.format_exc()[-7000:],"raw_master_mutation":"NONE"}})
    finally:
        if lc is not None:
            try:lc.execute(text("SELECT pg_advisory_unlock(:k)"),{"k":LOCK_KEY})
            except:pass
            try:lc.close()
            except:pass

def _start(core):threading.Thread(target=_build,args=(core,),daemon=True,name="mag-settle-12009").start()

def register(core):
    app=_app(core);e=_engine(core)
    if app is None or e is None:raise RuntimeError("12.0.9 requires app + engine")
    _setup(e)
    @app.get("/api/alliance/admin/magazine-settlement/status")
    def status():return JSONResponse(STATE)
    @app.post("/api/alliance/admin/magazine-settlement/rebuild")
    def rebuild():_start(core);return JSONResponse({"status":"STARTED","version":VERSION})
    @app.get("/alliance/admin/magazine-settlement",response_class=HTMLResponse)
    def page(req:Request,bucket:str="GOLD",page:int=1):
        _login(core,req);bucket=_norm(bucket).upper()
        if bucket not in {"GOLD","SILVER","REVIEW","QUARANTINED","EXCLUDED_NON_PROPERTY"}:bucket="GOLD"
        page=max(1,int(page or 1));off=(page-1)*40
        with e.connect() as c:
            counts={r[0]:int(r[1]) for r in c.execute(text(f"SELECT settled_status,COUNT(*) FROM {SETTLED} GROUP BY settled_status")).all()}
            rows=c.execute(text(f"""SELECT s.*,m.original_raw_text FROM {SETTLED} s JOIN pi_magazine_master m
            ON CAST(m.source_id AS TEXT)=s.source_id WHERE s.settled_status=:b ORDER BY s.settled_confidence DESC,s.source_id LIMIT 40 OFFSET :o"""),
            {"b":bucket,"o":off}).mappings().all()
        cards="".join(f"<div class='c'><b>{html.escape(str(r['source_id']))}</b> · {r['settled_status']} · {r['settled_confidence']}<br><b>{html.escape(str(r['settled_location'] or 'MISSING'))}</b><br>{html.escape(str(r['settlement_rule']))}<br>{html.escape(str(r['original_raw_text'] or ''))}<br>Duplicate rank: {r['duplicate_rank'] or 1}</div>" for r in rows)
        return HTMLResponse(f"""<html><head><style>body{{font-family:Arial;margin:24px;background:#f5f2eb}}.c,.top{{background:white;padding:14px;margin:10px 0;border:1px solid #ddd;border-radius:10px;line-height:1.5}}a{{margin-right:12px}}</style></head><body>
        <h1>Magazine Workable Database · 12.0.9</h1><div class='top'>GOLD <b>{counts.get('GOLD',0)}</b> · SILVER <b>{counts.get('SILVER',0)}</b> · REVIEW <b>{counts.get('REVIEW',0)}</b> · QUARANTINED <b>{counts.get('QUARANTINED',0)}</b> · EXCLUDED <b>{counts.get('EXCLUDED_NON_PROPERTY',0)}</b><br>
        <a href='?bucket=GOLD'>Gold</a><a href='?bucket=SILVER'>Silver</a><a href='?bucket=REVIEW'>Review</a><a href='?bucket=QUARANTINED'>Quarantined</a>
        <a href='/api/alliance/admin/magazine-settlement/status'>Status JSON</a></div>{cards or "<div class='c'>No records.</div>"}<p><a href='?bucket={bucket}&page={page+1}'>Next →</a></p></body></html>""")
    _start(core)
    return {"status":"REGISTERED","version":VERSION,"status_api":"/api/alliance/admin/magazine-settlement/status",
    "workbench":"/alliance/admin/magazine-settlement","workable_view":"pi_magazine_workable_v12009"}
