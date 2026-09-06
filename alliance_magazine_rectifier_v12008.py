
from __future__ import annotations

import html, json, re, threading, time, traceback
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

VERSION = "12.0.8-RECTIFIED-GOLDEN-MASTER"
STAGE = "pi_magazine_golden_stage_v12003"
CERT = "pi_magazine_certification_v12004"
RECOVERY = "pi_magazine_evidence_recovery_v12007"
CONFLICT_DEC = "pi_magazine_conflict_decisions_v12007"
DUPMAP = "pi_magazine_duplicate_map_v12005"
DUPDEC = "pi_magazine_duplicate_decisions_v12005"

RECT = "pi_magazine_rectified_v12008"
RUNS = "pi_magazine_rectification_runs_v12008"
AUDIT = "pi_magazine_rectification_audit_v12008"
LOCK_KEY = 120080001

BAD = {"", "MISSING", "UNKNOWN", "N/A", "NA", "NONE", "NULL", "UNSPECIFIED"}

SECTION_WORD_RE = re.compile(
    r"(?i)\b(?:RESIDENTIAL|COMMERCIAL|INDUSTRIAL|INSTITUTIONAL|HOSPITALITY|RETAIL|"
    r"SALE|RENT|LEASE|PURCHASE|BUY|SELL|REQUIREMENT|REQUIREMENTS|PROPERTY|PROPERTIES)\b"
)
PHONE_RE = re.compile(r"(?<!\d)(?:[6-9]\d{9}|0?11[-\s]?\d{7,8})(?!\d)")
AREA_RE = re.compile(r"(?i)\b\d{2,7}(?:\.\d+)?\s*(?:SQ\.?\s*FT|SQFT|FT|SQ\.?\s*YD|SQYD|YD|Y|SQ\.?\s*M|SQM|ACRE)\b")
ORG_RE = re.compile(r"(?i)\b(?:CONSTRUCTION|CONSTRUCTIONS|BUILDER|BUILDERS|DEVELOPER|DEVELOPERS|REALTOR|REALTORS|REALTY|ESTATE|ESTATES|PROPERTIES|PROPERTY\s+DEALER|INFRA|INFRASTRUCTURE|ASSOCIATES|CONSULTANTS|CONSULTANCY|PVT|LTD|LLP|ENTERPRISES|CORPORATION|COMPANY|CO\.?|GROUP|INTERIORS|ARCHITECTS)\b")

STATIC = {
    "ALAKNANDA":"Alaknanda","ANAND LOK":"Anand Lok","ANAND NIKETAN":"Anand Niketan",
    "BHIKAJI CAMA PLACE":"Bhikaji Cama Place","CHANAKYAPURI":"Chanakyapuri",
    "CHHATARPUR":"Chhatarpur","CHHATARPUR ENCLAVE":"Chhatarpur Enclave",
    "CHIRAG DELHI":"Chirag Delhi","CHITRANJAN PARK":"Chitranjan Park","CR PARK":"Chitranjan Park","C R PARK":"Chitranjan Park",
    "CONNAUGHT PLACE":"Connaught Place","CP":"Connaught Place","DEFENCE COLONY":"Defence Colony",
    "DERA MANDI":"Dera Mandi","DWARKA":"Dwarka","EAST OF KAILASH":"East of Kailash",
    "FRIENDS COLONY":"Friends Colony","GAUTAM NAGAR":"Gautam Nagar","GOLF LINKS":"Golf Links",
    "GREATER KAILASH 1":"Greater Kailash 1","GREATER KAILASH I":"Greater Kailash 1","GREATER KAILASH-1":"Greater Kailash 1","GK 1":"Greater Kailash 1","GK-I":"Greater Kailash 1",
    "GREATER KAILASH 2":"Greater Kailash 2","GREATER KAILASH II":"Greater Kailash 2","GREATER KAILASH-2":"Greater Kailash 2","GK 2":"Greater Kailash 2","GK-II":"Greater Kailash 2",
    "GREEN PARK":"Green Park","GREEN PARK EXTN":"Green Park Extension","GREEN PARK EXTENSION":"Green Park Extension",
    "GURGAON":"Gurugram","GURUGRAM":"Gurugram","HAUZ KHAS":"Hauz Khas","JASOLA":"Jasola",
    "JOR BAGH":"Jor Bagh","KAILASH COLONY":"Kailash Colony","LAJPAT NAGAR":"Lajpat Nagar",
    "LAJPAT NAGAR 1":"Lajpat Nagar 1","LAJPAT NAGAR-1":"Lajpat Nagar 1","LAJPAT NAGAR I":"Lajpat Nagar 1",
    "LAJPAT NAGAR 2":"Lajpat Nagar 2","LAJPAT NAGAR-2":"Lajpat Nagar 2","LAJPAT NAGAR II":"Lajpat Nagar 2",
    "LAJPAT NAGAR 3":"Lajpat Nagar 3","LAJPAT NAGAR-3":"Lajpat Nagar 3","LAJPAT NAGAR III":"Lajpat Nagar 3",
    "LAJPAT NAGAR 4":"Lajpat Nagar 4","LAJPAT NAGAR-4":"Lajpat Nagar 4","LAJPAT NAGAR IV":"Lajpat Nagar 4",
    "MAHARANI BAGH":"Maharani Bagh","MALVIYA NAGAR":"Malviya Nagar",
    "MOHAN CO-OPERATIVE":"Mohan Cooperative","MOHAN COOPERATIVE":"Mohan Cooperative",
    "NEW FRIENDS COLONY":"New Friends Colony","NFC":"New Friends Colony","NITI BAGH":"Niti Bagh",
    "NIZAMUDDIN":"Nizamuddin","NIZAMUDDIN EAST":"Nizamuddin East","NIZAMUDDIN WEST":"Nizamuddin West",
    "PANCHSHEEL ENCLAVE":"Panchsheel Enclave","PANCHSHEEL PARK":"Panchsheel Park","PITAMPURA":"Pitampura",
    "ROHINI":"Rohini","SAFDARJUNG ENCLAVE":"Safdarjung Enclave","SAFDARJUNG DEVELOPMENT AREA":"Safdarjung Development Area",
    "SDA":"Safdarjung Development Area","SAINIK FARM":"Sainik Farm","SAKET":"Saket",
    "SARVODAYA ENCLAVE":"Sarvodaya Enclave","SHANTI NIKETAN":"Shanti Niketan",
    "SOUTH EXTENSION":"South Extension","SOUTH EXTENSION 1":"South Extension 1","SOUTH EXTENSION I":"South Extension 1",
    "SOUTH EXTENSION 2":"South Extension 2","SOUTH EXTENSION II":"South Extension 2",
    "SUNDER NAGAR":"Sunder Nagar","TUGHLAKABAD":"Tughlakabad","TUGHLAKABAD EXTN":"Tughlakabad Extension",
    "VASANT KUNJ":"Vasant Kunj","VASANT VIHAR":"Vasant Vihar",
}
for p in (1,2,3):
    for raw in (f"OKHLA PHASE {p}",f"OKHLA PHASE-{p}",f"OKHLA-{p}",f"OKHLA {p}"):
        STATIC[raw]=f"Okhla Phase {p}"
STATIC["OKHLA PHASE I"]="Okhla Phase 1"
STATIC["OKHLA PHASE II"]="Okhla Phase 2"
STATIC["OKHLA PHASE III"]="Okhla Phase 3"

STATE = {
    "status":"IDLE","phase":"WAITING","started_at":None,"completed_at":None,
    "rows_total":0,"rows_rectified":0,
    "gold_existing":0,"gold_recovered":0,"gold_human_conflict":0,
    "silver":0,"review":0,"quarantined":0,"excluded_non_property":0,
    "duplicate_blocked":0,"conflict_blocked":0,"weak_93_blocked":0,
    "operational_rows":0,"ai_training_rows":0,
    "error":None,"details":{}
}
LOCK=threading.Lock()

def _now(): return datetime.now(timezone.utc).isoformat()
def _app(core): return getattr(core,"app",None) or core
def _engine(core): return getattr(core,"engine",None)
def _login(core,req):
    fn=getattr(core,"need_login",None)
    return fn(req) if fn else "team"
def _norm(v): return re.sub(r"\s+"," ",str(v or "")).strip()

def _valid_geo(v):
    s=_norm(v)
    if not s or s.upper() in BAD:return False
    if SECTION_WORD_RE.search(s) or ORG_RE.search(s) or PHONE_RE.search(s) or AREA_RE.search(s): return False
    if re.search(r"\b20\d{2}\b",s): return False
    if len(s)>75:return False
    return True

def _canon(v):
    u=_norm(v).upper().strip(" ,;:|")
    if not u:return None
    if u in STATIC:return STATIC[u]
    m=re.fullmatch(r"(NOIDA|GURUGRAM|GURGAON|DWARKA|ROHINI)\s+SECTOR\s+(\d+[A-Z]?)",u)
    if m:
        city="Gurugram" if m.group(1) in {"GURGAON","GURUGRAM"} else m.group(1).title()
        return f"{city} Sector {m.group(2)}"
    return _norm(v) if _valid_geo(v) else None

def _setup(e):
    with e.begin() as c:
        for t in (STAGE,CERT,RECOVERY,CONFLICT_DEC,DUPMAP,DUPDEC):
            if not c.execute(text("SELECT to_regclass(:t) IS NOT NULL"),{"t":t}).scalar():
                raise RuntimeError(f"Required dependency missing: {t}")
        c.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {RECT}(
              source_id TEXT PRIMARY KEY,
              final_location TEXT,
              final_location_confidence INTEGER NOT NULL DEFAULT 0,
              final_status TEXT NOT NULL,
              rectification_rule TEXT NOT NULL,
              stage_quality_status TEXT,
              stage_property_status TEXT,
              certification_decision TEXT,
              recovery_status TEXT,
              recovery_rule TEXT,
              recovery_confidence INTEGER NOT NULL DEFAULT 0,
              source_conflict BOOLEAN NOT NULL DEFAULT FALSE,
              conflict_decision TEXT,
              duplicate_group TEXT,
              duplicate_decision TEXT,
              duplicate_blocked BOOLEAN NOT NULL DEFAULT FALSE,
              conflict_blocked BOOLEAN NOT NULL DEFAULT FALSE,
              ai_eligible BOOLEAN NOT NULL DEFAULT FALSE,
              operational_eligible BOOLEAN NOT NULL DEFAULT FALSE,
              evidence JSONB NOT NULL DEFAULT '{{}}'::jsonb,
              version TEXT NOT NULL,
              updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        c.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {RUNS}(
              id BIGSERIAL PRIMARY KEY,version TEXT NOT NULL,status TEXT NOT NULL,
              started_at TIMESTAMPTZ DEFAULT NOW(),completed_at TIMESTAMPTZ,
              summary JSONB NOT NULL DEFAULT '{{}}'::jsonb
            )
        """))
        c.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {AUDIT}(
              id BIGSERIAL PRIMARY KEY,source_id TEXT,event_type TEXT NOT NULL,
              old_status TEXT,new_status TEXT,old_location TEXT,new_location TEXT,
              rule TEXT,evidence JSONB NOT NULL DEFAULT '{{}}'::jsonb,
              created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))

def _wait_ready(e,timeout=150):
    deadline=time.monotonic()+timeout
    last={}
    while time.monotonic()<deadline:
        try:
            with e.connect() as c:
                raw=int(c.execute(text("SELECT COUNT(*) FROM pi_magazine_master")).scalar() or 0)
                stage=int(c.execute(text(f"SELECT COUNT(*) FROM {STAGE} WHERE version='12.0.3-SINGLE-WRITER-GOLDEN-DATA'")).scalar() or 0)
                recovery=int(c.execute(text(f"SELECT COUNT(*) FROM {RECOVERY} WHERE version LIKE '12.0.7%'")).scalar() or 0)
                rrun=c.execute(text("""
                    SELECT status,summary FROM pi_magazine_evidence_recovery_runs_v12007
                    ORDER BY id DESC LIMIT 1
                """)).mappings().first()
            rstatus=rrun.get("status") if rrun else None
            summary=rrun.get("summary") if rrun else {}
            if isinstance(summary,str):
                try: summary=json.loads(summary)
                except Exception: summary={}
            last={"raw_count":raw,"stage_count":stage,"recovery_rows":recovery,
                  "recovery_run_status":rstatus}
            # Recovery table intentionally contains only review/silver/conflict rows, not all raw rows.
            if raw>0 and stage==raw and recovery>0 and rstatus=="PASS":
                return last
        except Exception as exc:
            last={"error":f"{type(exc).__name__}: {exc}"}
        time.sleep(2)
    raise RuntimeError("12.0.7 recovery/final stage not ready: "+json.dumps(last,default=str))

def _decide(r):
    q=_norm(r.get("quality_status")).upper()
    ps=_norm(r.get("property_status")).upper()
    cert=_norm(r.get("cert_decision")).upper()
    stage_loc=_canon(r.get("canonical_location"))
    stage_conf=int(r.get("location_confidence") or 0)
    stage_conflict=bool(r.get("conflict"))

    rec_status=_norm(r.get("recovery_status")).upper()
    rec_loc=_canon(r.get("recovered_location"))
    rec_conf=int(r.get("recovery_confidence") or 0)
    rec_rule=_norm(r.get("recovery_rule")).upper()

    conflict_dec=_norm(r.get("conflict_decision")).upper()
    chosen_loc=_canon(r.get("chosen_location"))

    dg=r.get("duplicate_group")
    dd=_norm(r.get("duplicate_decision")).upper() or "PENDING"
    duplicate_blocked=bool(dg and dd=="PENDING")

    ev={
        "stage":{"location":stage_loc,"confidence":stage_conf,"quality":q,
                 "property_status":ps,"conflict":stage_conflict},
        "recovery":{"status":rec_status,"location":rec_loc,
                    "confidence":rec_conf,"rule":rec_rule},
        "conflict":{"decision":conflict_dec,"chosen_location":chosen_loc},
        "duplicate":{"group":dg,"decision":dd},
    }

    if q=="EXCLUDED_NON_PROPERTY" or ps=="NON_PROPERTY":
        return "EXCLUDED_NON_PROPERTY",stage_loc or rec_loc,0,"EXCLUDED_NON_PROPERTY",False,False,False,ev

    # Existing trusted certification is retained.
    if cert in {"AUTO_GOLD","HUMAN_APPROVED"} and stage_loc and not duplicate_blocked:
        return "GOLD",stage_loc,max(stage_conf,93),"EXISTING_CERTIFICATION",True,True,False,ev

    # Human conflict decision may settle a genuine conflict, but only with valid geography.
    if conflict_dec=="RESOLVED" and chosen_loc and not duplicate_blocked:
        return "GOLD",chosen_loc,100,"HUMAN_CONFLICT_RESOLUTION",True,True,False,ev

    # Any unresolved source conflict stays review. Never guess.
    if stage_conflict or rec_status=="RECOVERED_CONFLICT":
        return "REVIEW",stage_loc or rec_loc,max(stage_conf,rec_conf),"UNRESOLVED_SOURCE_CONFLICT",False,False,True,ev

    # Duplicate identity must be settled before certification.
    if duplicate_blocked:
        loc=rec_loc or stage_loc
        return "REVIEW",loc,max(stage_conf,rec_conf),"DUPLICATE_FIRST",False,False,False,ev

    # Safe automatic rectification: retained source recovery >=95 only.
    # 93-level structural+existing geography is deliberately NOT auto-certified.
    if rec_status=="RECOVERED_CANDIDATE" and rec_loc and rec_conf>=95 and \
       rec_rule in {"EXACT_SOURCE_RECOVERY","STRUCTURAL_TWO_SOURCE_CONFIRMATION"} and \
       ps not in {"NON_PROPERTY","WEAK_PROPERTY_EVIDENCE"}:
        return "GOLD",rec_loc,rec_conf,"EVIDENCE_RECOVERED_GOLD",True,True,False,ev

    if rec_status=="RECOVERED_CANDIDATE" and rec_conf==93:
        loc=rec_loc or stage_loc
        return "SILVER",loc,93,"RECOVERED_93_NEEDS_HUMAN_CONFIRMATION",False,True,False,ev

    if q=="SILVER" and stage_loc:
        return "SILVER",stage_loc,stage_conf,"EXISTING_SILVER_UNPROVEN",False,True,False,ev

    if q=="QUARANTINED":
        return "QUARANTINED",stage_loc or rec_loc,max(stage_conf,rec_conf),"WEAK_PROPERTY_EVIDENCE",False,False,False,ev

    return "REVIEW",stage_loc or rec_loc,max(stage_conf,rec_conf),"INSUFFICIENT_EVIDENCE",False,False,False,ev

def _rebuild_views(e):
    with e.begin() as c:
        # Child-first drop order.
        c.execute(text("DROP VIEW IF EXISTS pi_magazine_ai_training_v12008"))
        c.execute(text("DROP VIEW IF EXISTS pi_magazine_operational_v12008"))
        c.execute(text("DROP VIEW IF EXISTS pi_magazine_review_queue_v12008"))
        c.execute(text("DROP VIEW IF EXISTS pi_magazine_golden_master_v12008"))

        c.execute(text(f"""
            CREATE VIEW pi_magazine_golden_master_v12008 AS
            SELECT m.*,r.final_location AS rectified_location,
                   r.final_location_confidence AS rectified_location_confidence,
                   r.rectification_rule
            FROM pi_magazine_master m
            JOIN {RECT} r ON r.source_id=CAST(m.source_id AS TEXT)
            WHERE r.final_status='GOLD'
              AND r.ai_eligible=TRUE
              AND r.duplicate_blocked=FALSE
              AND r.conflict_blocked=FALSE
        """))
        c.execute(text(f"""
            CREATE VIEW pi_magazine_operational_v12008 AS
            SELECT m.*,r.final_location AS rectified_location,
                   r.final_status AS rectified_quality_status,
                   r.final_location_confidence AS rectified_location_confidence,
                   r.rectification_rule
            FROM pi_magazine_master m
            JOIN {RECT} r ON r.source_id=CAST(m.source_id AS TEXT)
            WHERE r.operational_eligible=TRUE
              AND r.final_status IN ('GOLD','SILVER')
              AND r.duplicate_blocked=FALSE
              AND r.conflict_blocked=FALSE
        """))
        c.execute(text("""
            CREATE VIEW pi_magazine_ai_training_v12008 AS
            SELECT * FROM pi_magazine_golden_master_v12008
        """))
        c.execute(text(f"""
            CREATE VIEW pi_magazine_review_queue_v12008 AS
            SELECT m.*,r.final_location AS rectified_location,
                   r.final_status AS rectified_quality_status,
                   r.final_location_confidence AS rectified_location_confidence,
                   r.rectification_rule,r.duplicate_blocked,r.conflict_blocked,r.evidence
            FROM pi_magazine_master m
            JOIN {RECT} r ON r.source_id=CAST(m.source_id AS TEXT)
            WHERE r.final_status IN ('REVIEW','QUARANTINED')
        """))

def _build(core):
    e=_engine(core)
    if e is None:return
    with LOCK:
        if STATE["status"]=="RUNNING":return
        STATE.update({"status":"RUNNING","phase":"SETUP","started_at":_now(),"completed_at":None,
            "rows_total":0,"rows_rectified":0,"gold_existing":0,"gold_recovered":0,
            "gold_human_conflict":0,"silver":0,"review":0,"quarantined":0,
            "excluded_non_property":0,"duplicate_blocked":0,"conflict_blocked":0,
            "weak_93_blocked":0,"operational_rows":0,"ai_training_rows":0,
            "error":None,"details":{}})
    lock_conn=None; run_id=None
    try:
        _setup(e)
        lock_conn=e.connect()
        if not bool(lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"),{"k":LOCK_KEY}).scalar()):
            STATE.update({"status":"SKIPPED","phase":"ANOTHER_RUN_ACTIVE","completed_at":_now()});return

        STATE["phase"]="WAITING_FOR_DEPENDENCIES"
        readiness=_wait_ready(e)

        with e.begin() as c:
            run_id=c.execute(text(f"INSERT INTO {RUNS}(version,status) VALUES(:v,'RUNNING') RETURNING id"),
                             {"v":VERSION}).scalar()

        STATE["phase"]="READING"
        with e.connect() as c:
            rows=[dict(x) for x in c.execute(text(f"""
                SELECT g.source_id,g.canonical_location,g.location_confidence,g.location_rule,
                       g.quality_status,g.property_status,g.conflict,
                       COALESCE(cert.decision,'PENDING') AS cert_decision,
                       rec.recovery_status,rec.recovered_location,rec.recovery_confidence,rec.recovery_rule,
                       COALESCE(cd.decision,'PENDING') AS conflict_decision,cd.chosen_location,
                       dm.duplicate_group,COALESCE(dd.decision,'PENDING') AS duplicate_decision
                FROM {STAGE} g
                LEFT JOIN {CERT} cert ON cert.source_id=g.source_id
                LEFT JOIN {RECOVERY} rec ON rec.source_id=g.source_id
                LEFT JOIN {CONFLICT_DEC} cd ON cd.source_id=g.source_id
                LEFT JOIN {DUPMAP} dm ON dm.source_id=g.source_id
                LEFT JOIN {DUPDEC} dd ON dd.duplicate_group=dm.duplicate_group
                WHERE g.version='12.0.3-SINGLE-WRITER-GOLDEN-DATA'
                ORDER BY g.source_id
            """)).mappings().all()]
        STATE["rows_total"]=len(rows)

        counts=defaultdict(int)
        STATE["phase"]="RECTIFYING"
        with e.begin() as c:
            for r in rows:
                status,loc,conf,rule,ai_ok,op_ok,conflict_blocked,ev=_decide(r)
                duplicate_blocked=rule=="DUPLICATE_FIRST"
                if rule=="EXISTING_CERTIFICATION": counts["gold_existing"]+=1
                if rule=="EVIDENCE_RECOVERED_GOLD": counts["gold_recovered"]+=1
                if rule=="HUMAN_CONFLICT_RESOLUTION": counts["gold_human_conflict"]+=1
                if status=="SILVER": counts["silver"]+=1
                if status=="REVIEW": counts["review"]+=1
                if status=="QUARANTINED": counts["quarantined"]+=1
                if status=="EXCLUDED_NON_PROPERTY": counts["excluded_non_property"]+=1
                if duplicate_blocked: counts["duplicate_blocked"]+=1
                if conflict_blocked: counts["conflict_blocked"]+=1
                if rule=="RECOVERED_93_NEEDS_HUMAN_CONFIRMATION": counts["weak_93_blocked"]+=1

                old=c.execute(text(f"SELECT final_status,final_location FROM {RECT} WHERE source_id=:sid"),
                              {"sid":r["source_id"]}).mappings().first()
                c.execute(text(f"""
                    INSERT INTO {RECT}(
                      source_id,final_location,final_location_confidence,final_status,rectification_rule,
                      stage_quality_status,stage_property_status,certification_decision,
                      recovery_status,recovery_rule,recovery_confidence,source_conflict,
                      conflict_decision,duplicate_group,duplicate_decision,duplicate_blocked,
                      conflict_blocked,ai_eligible,operational_eligible,evidence,version,updated_at
                    )
                    VALUES(:sid,:loc,:conf,:status,:rule,:sq,:sp,:cert,:rs,:rr,:rc,:sc,:cd,:dg,:dd,
                           :db,:cb,:ai,:op,CAST(:ev AS JSONB),:v,NOW())
                    ON CONFLICT(source_id) DO UPDATE SET
                      final_location=EXCLUDED.final_location,
                      final_location_confidence=EXCLUDED.final_location_confidence,
                      final_status=EXCLUDED.final_status,
                      rectification_rule=EXCLUDED.rectification_rule,
                      stage_quality_status=EXCLUDED.stage_quality_status,
                      stage_property_status=EXCLUDED.stage_property_status,
                      certification_decision=EXCLUDED.certification_decision,
                      recovery_status=EXCLUDED.recovery_status,
                      recovery_rule=EXCLUDED.recovery_rule,
                      recovery_confidence=EXCLUDED.recovery_confidence,
                      source_conflict=EXCLUDED.source_conflict,
                      conflict_decision=EXCLUDED.conflict_decision,
                      duplicate_group=EXCLUDED.duplicate_group,
                      duplicate_decision=EXCLUDED.duplicate_decision,
                      duplicate_blocked=EXCLUDED.duplicate_blocked,
                      conflict_blocked=EXCLUDED.conflict_blocked,
                      ai_eligible=EXCLUDED.ai_eligible,
                      operational_eligible=EXCLUDED.operational_eligible,
                      evidence=EXCLUDED.evidence,version=EXCLUDED.version,updated_at=NOW()
                """),{"sid":r["source_id"],"loc":loc,"conf":conf,"status":status,"rule":rule,
                       "sq":r.get("quality_status"),"sp":r.get("property_status"),"cert":r.get("cert_decision"),
                       "rs":r.get("recovery_status"),"rr":r.get("recovery_rule"),
                       "rc":int(r.get("recovery_confidence") or 0),"sc":bool(r.get("conflict")),
                       "cd":r.get("conflict_decision"),"dg":r.get("duplicate_group"),
                       "dd":r.get("duplicate_decision"),"db":duplicate_blocked,"cb":conflict_blocked,
                       "ai":ai_ok,"op":op_ok,"ev":json.dumps(ev,ensure_ascii=False),"v":VERSION})
                if old and (old.get("final_status")!=status or _norm(old.get("final_location"))!=_norm(loc)):
                    c.execute(text(f"""
                        INSERT INTO {AUDIT}(source_id,event_type,old_status,new_status,old_location,new_location,rule,evidence)
                        VALUES(:sid,'RECTIFICATION_CHANGE',:os,:ns,:ol,:nl,:rule,CAST(:ev AS JSONB))
                    """),{"sid":r["source_id"],"os":old.get("final_status"),"ns":status,
                           "ol":old.get("final_location"),"nl":loc,"rule":rule,
                           "ev":json.dumps(ev,ensure_ascii=False)})

        STATE["phase"]="BUILDING_VIEWS"
        _rebuild_views(e)
        with e.connect() as c:
            op=int(c.execute(text("SELECT COUNT(*) FROM pi_magazine_operational_v12008")).scalar() or 0)
            ai=int(c.execute(text("SELECT COUNT(*) FROM pi_magazine_ai_training_v12008")).scalar() or 0)

        STATE.update({"status":"PASS","phase":"COMPLETE","completed_at":_now(),"rows_rectified":len(rows),
            "gold_existing":counts["gold_existing"],"gold_recovered":counts["gold_recovered"],
            "gold_human_conflict":counts["gold_human_conflict"],"silver":counts["silver"],
            "review":counts["review"],"quarantined":counts["quarantined"],
            "excluded_non_property":counts["excluded_non_property"],
            "duplicate_blocked":counts["duplicate_blocked"],"conflict_blocked":counts["conflict_blocked"],
            "weak_93_blocked":counts["weak_93_blocked"],"operational_rows":op,"ai_training_rows":ai,
            "details":{
                "raw_master_mutation":"NONE",
                "stage_v12003_mutation":"NONE",
                "certification_v12004_mutation":"NONE",
                "rectified_database_table":RECT,
                "golden_master_view":"pi_magazine_golden_master_v12008",
                "operational_view":"pi_magazine_operational_v12008",
                "ai_training_view":"pi_magazine_ai_training_v12008",
                "review_queue_view":"pi_magazine_review_queue_v12008",
                "auto_rectification_threshold":"recovery >=95 from exact source or two-source structural evidence",
                "confidence_93_policy":"SILVER_NEEDS_HUMAN_CONFIRMATION",
                "unresolved_conflict_policy":"REVIEW",
                "unresolved_duplicate_policy":"REVIEW",
                "readiness":readiness
            }})
        if run_id:
            with e.begin() as c:
                c.execute(text(f"UPDATE {RUNS} SET status='PASS',completed_at=NOW(),summary=CAST(:s AS JSONB) WHERE id=:id"),
                          {"id":run_id,"s":json.dumps(STATE,default=str)})
    except Exception as exc:
        STATE.update({"status":"ERROR","phase":"FAILED","completed_at":_now(),
                      "error":f"{type(exc).__name__}: {exc}",
                      "details":{"trace":traceback.format_exc()[-7000:],"raw_master_mutation":"NONE"}})
        if run_id:
            try:
                with e.begin() as c:
                    c.execute(text(f"UPDATE {RUNS} SET status='ERROR',completed_at=NOW(),summary=CAST(:s AS JSONB) WHERE id=:id"),
                              {"id":run_id,"s":json.dumps(STATE,default=str)})
            except Exception: pass
    finally:
        if lock_conn is not None:
            try: lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"),{"k":LOCK_KEY})
            except Exception: pass
            try: lock_conn.close()
            except Exception: pass

def _start(core):
    threading.Thread(target=_build,args=(core,),daemon=True,name="mag-rectifier-12008").start()

def register(core):
    app=_app(core); e=_engine(core)
    if app is None or e is None: raise RuntimeError("12.0.8 requires app + engine")
    _setup(e)

    @app.get("/api/alliance/admin/magazine-rectifier/status")
    def status():
        return JSONResponse(STATE)

    @app.post("/api/alliance/admin/magazine-rectifier/rebuild")
    def rebuild():
        _start(core)
        return JSONResponse({"status":"STARTED","version":VERSION})

    @app.get("/alliance/admin/magazine-rectifier",response_class=HTMLResponse)
    def page(req:Request,bucket:str="GOLD",page:int=1):
        _login(core,req)
        allowed={"GOLD","SILVER","REVIEW","QUARANTINED","EXCLUDED_NON_PROPERTY"}
        bucket=_norm(bucket).upper()
        if bucket not in allowed:bucket="GOLD"
        page=max(1,int(page or 1));per=40;off=(page-1)*per
        with e.connect() as c:
            counts={r[0]:int(r[1]) for r in c.execute(text(f"SELECT final_status,COUNT(*) FROM {RECT} GROUP BY final_status")).all()}
            rows=c.execute(text(f"""
                SELECT r.source_id,r.final_location,r.final_location_confidence,r.final_status,
                       r.rectification_rule,r.duplicate_blocked,r.conflict_blocked,m.original_raw_text
                FROM {RECT} r JOIN pi_magazine_master m ON CAST(m.source_id AS TEXT)=r.source_id
                WHERE r.final_status=:b ORDER BY r.final_location_confidence DESC,r.source_id
                LIMIT :lim OFFSET :off
            """),{"b":bucket,"lim":per,"off":off}).mappings().all()
        cards=[]
        for r in rows:
            cards.append(f"""<div class='card'><b>{html.escape(str(r['source_id']))}</b> ·
              {html.escape(str(r['final_status']))} · {r['final_location_confidence']}
              <br><b>Location:</b> {html.escape(str(r['final_location'] or 'MISSING'))}
              <br><b>Rule:</b> {html.escape(str(r['rectification_rule']))}
              <br><b>Description:</b> {html.escape(str(r['original_raw_text'] or ''))}
              <br><b>Duplicate blocked:</b> {r['duplicate_blocked']} ·
              <b>Conflict blocked:</b> {r['conflict_blocked']}</div>""")
        return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'><title>Magazine Rectifier 12.0.8</title>
        <style>body{{font-family:Arial;margin:24px;background:#f5f2eb}}.card,.top{{background:#fff;padding:14px;border:1px solid #ddd;border-radius:10px;margin:10px 0;line-height:1.5}}a{{margin-right:12px}}</style>
        </head><body><h1>Magazine Rectified Database · 12.0.8</h1>
        <div class='top'>GOLD <b>{counts.get('GOLD',0)}</b> · SILVER <b>{counts.get('SILVER',0)}</b> ·
        REVIEW <b>{counts.get('REVIEW',0)}</b> · QUARANTINED <b>{counts.get('QUARANTINED',0)}</b> ·
        EXCLUDED <b>{counts.get('EXCLUDED_NON_PROPERTY',0)}</b><br>
        <a href='?bucket=GOLD'>Gold</a><a href='?bucket=SILVER'>Silver</a>
        <a href='?bucket=REVIEW'>Review</a><a href='?bucket=QUARANTINED'>Quarantined</a>
        <a href='/alliance/admin/magazine-conflicts'>Conflict Workbench</a>
        <a href='/alliance/admin/magazine-duplicate-review'>Duplicate Review</a>
        <a href='/api/alliance/admin/magazine-rectifier/status'>Status JSON</a></div>
        {''.join(cards) if cards else "<div class='card'>No records.</div>"}
        <p><a href='?bucket={bucket}&page={page+1}'>Next page →</a></p></body></html>""")

    _start(core)
    return {"status":"REGISTERED","version":VERSION,
            "status_api":"/api/alliance/admin/magazine-rectifier/status",
            "workbench":"/alliance/admin/magazine-rectifier",
            "golden_master":"pi_magazine_golden_master_v12008",
            "operational":"pi_magazine_operational_v12008",
            "ai_training":"pi_magazine_ai_training_v12008"}
