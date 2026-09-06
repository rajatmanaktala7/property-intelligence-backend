
from __future__ import annotations

import html, json, re, threading, traceback
from collections import Counter, defaultdict
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

VERSION = "12.0.3-SINGLE-WRITER-GOLDEN-DATA"
STAGE = "pi_magazine_golden_stage_v12003"
RUNS = "pi_magazine_governance_runs_v12003"
SNAPSHOT = "pi_magazine_master_raw_snapshot_v12000"
LOCK_KEY = 120030001

BAD = {"", "MISSING", "UNKNOWN", "N/A", "NA", "NONE", "NULL", "UNSPECIFIED"}
ORG_RE = re.compile(r"(?i)\b(?:CONSTRUCTION|CONSTRUCTIONS|BUILDER|BUILDERS|DEVELOPER|DEVELOPERS|REALTOR|REALTORS|REALTY|ESTATE|ESTATES|PROPERTIES|PROPERTY\s+DEALER|INFRA|INFRASTRUCTURE|ASSOCIATES|CONSULTANTS|CONSULTANCY|PVT|LTD|LLP|ENTERPRISES|CORPORATION|COMPANY|CO\.?|GROUP|INTERIORS|ARCHITECTS)\b")
PHONE_RE = re.compile(r"(?<!\d)(?:[6-9]\d{9}|0?11[-\s]?\d{7,8})(?!\d)")
AREA_RE = re.compile(r"(?i)\b\d{2,7}(?:\.\d+)?\s*(?:SQ\.?\s*FT|SQFT|FT|SQ\.?\s*YD|SQYD|YD|Y|SQ\.?\s*M|SQM|ACRE)\b")
FLOOR_RE = re.compile(r"(?i)\b(?:BMT|BASEMENT|LGF|UGF|GF|GROUND\s*FLOOR|FF|FIRST\s*FLOOR|SF|SECOND\s*FLOOR|TF|THIRD\s*FLOOR|MEZZ|\d+(?:ST|ND|RD|TH)?\s*FLOOR)\b")
BHK_RE = re.compile(r"(?i)\b\d+\s*(?:BHK|BR)\b")
PTYPE_RE = re.compile(r"(?i)\b(?:APARTMENT|APT|FLAT|KOTHI|VILLA|PLOT|OFFICE|SHOP|SHOWROOM|WAREHOUSE|GODOWN|FACTORY|BUILDING|FARMHOUSE|FARM\s*HOUSE)\b")

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

LOCK = threading.Lock()
STATE = {
    "status":"IDLE","phase":"WAITING","started_at":None,"completed_at":None,
    "rows_total":0,"rows_scanned":0,"progress_percent":0.0,
    "gold":0,"silver":0,"review":0,"quarantined":0,"non_property":0,
    "duplicate_suppressed":0,"location_repairs":0,"invalid_location_removed":0,
    "conflicts":0,"last_source_id":None,"heartbeat_at":None,"error":None,
    "details":{"write_policy":"STAGE_ONLY"}
}

def _now(): return datetime.now(timezone.utc).isoformat()
def _app(core): return getattr(core,"app",None) or core
def _engine(core): return getattr(core,"engine",None)
def _login(core,req):
    fn=getattr(core,"need_login",None)
    return fn(req) if fn else "team"
def _norm(v): return re.sub(r"\s+"," ",str(v or "")).strip()
def _key(v):
    s=PHONE_RE.sub(" ",_norm(v).upper())
    s=re.sub(r"[^A-Z0-9]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()
def _valid_geo(v):
    s=_norm(v)
    if not s or s.upper() in BAD:return False
    if ORG_RE.search(s) or PHONE_RE.search(s) or AREA_RE.search(s):return False
    if len(s)>75:return False
    return True
def _canon(v):
    u=_norm(v).upper().strip(" ,;:|")
    if u in STATIC:return STATIC[u]
    m=re.fullmatch(r"(NOIDA|GURUGRAM|GURGAON|DWARKA|ROHINI)\s+SECTOR\s+(\d+[A-Z]?)",u)
    if m:
        city="Gurugram" if m.group(1) in {"GURGAON","GURUGRAM"} else m.group(1).title()
        return f"{city} Sector {m.group(2)}"
    return _norm(v) if _valid_geo(v) else None
def _property_like(desc):
    u=_norm(desc).upper()
    if not u:return False
    address=bool(re.match(r"^\s*(?:[A-Z]{0,4}[-/]?\d+[A-Z]?|\d+[A-Z]?|[A-Z]-BLOCK|[A-Z]-BLK)\b",u))
    details=bool(AREA_RE.search(u) or FLOOR_RE.search(u) or BHK_RE.search(u) or PTYPE_RE.search(u))
    return address and details
def _obvious_non_property(desc):
    s=_norm(desc)
    if not s:return True
    if not (AREA_RE.search(s) or FLOOR_RE.search(s) or BHK_RE.search(s) or PTYPE_RE.search(s)):
        if PHONE_RE.search(s) and len(_norm(PHONE_RE.sub(" ",s)))<45:return True
    return False
def _direct(desc):
    u=_norm(desc).upper()
    hits=[]
    for raw,canon in sorted(STATIC.items(),key=lambda kv:len(kv[0]),reverse=True):
        if re.search(r"(?<![A-Z0-9])"+re.escape(raw)+r"(?![A-Z0-9])",u):
            if canon not in hits:hits.append(canon)
    hits.sort(key=lambda x:(bool(re.search(r"\d",x)),len(x)),reverse=True)
    return hits[0] if hits else None

def _setup(e):
    with e.begin() as c:
        c.execute(text(f"""CREATE TABLE IF NOT EXISTS {STAGE}(
          source_id TEXT PRIMARY KEY,
          raw_location TEXT,
          canonical_location TEXT,
          location_confidence INTEGER NOT NULL DEFAULT 0,
          location_rule TEXT,
          conflict BOOLEAN NOT NULL DEFAULT FALSE,
          duplicate_group TEXT,
          duplicate_rank INTEGER,
          quality_status TEXT NOT NULL,
          quality_score INTEGER NOT NULL DEFAULT 0,
          property_status TEXT NOT NULL,
          evidence JSONB NOT NULL DEFAULT '{{}}'::jsonb,
          version TEXT NOT NULL,
          updated_at TIMESTAMPTZ DEFAULT NOW()
        )"""))
        c.execute(text(f"""CREATE TABLE IF NOT EXISTS {RUNS}(
          id BIGSERIAL PRIMARY KEY,version TEXT NOT NULL,status TEXT NOT NULL,
          started_at TIMESTAMPTZ DEFAULT NOW(),completed_at TIMESTAMPTZ,
          summary JSONB NOT NULL DEFAULT '{{}}'::jsonb
        )"""))
        c.execute(text(f"""DO $$ BEGIN
          IF to_regclass('{SNAPSHOT}') IS NULL THEN
            EXECUTE 'CREATE TABLE {SNAPSHOT} AS SELECT * FROM pi_magazine_master';
          END IF;
        END $$;"""))

def _table_exists(e,t):
    with e.connect() as c:
        return bool(c.execute(text("SELECT to_regclass(:t) IS NOT NULL"),{"t":t}).scalar())

def _layout_index(e):
    idx=defaultdict(list)
    if not _table_exists(e,"pi_magazine_layout_evidence_v11921"):return idx
    with e.connect() as c:
        rows=c.execute(text("""SELECT original_text,locality,page_number
        FROM pi_magazine_layout_evidence_v11921
        WHERE locality IS NOT NULL AND BTRIM(locality)<>''""")).mappings().all()
    for rr in rows:
        r=dict(rr)
        if _valid_geo(r["locality"]):idx[_key(r["original_text"])].append(r)
    return idx

def _complete_index(e):
    idx=defaultdict(list)
    if not _table_exists(e,"pi_magazine_complete_v860"):return idx
    with e.connect() as c:
        rows=c.execute(text("""SELECT source_record_id,original_description,description,original_section,location,page_number
        FROM pi_magazine_complete_v860""")).mappings().all()
    for rr in rows:
        r=dict(rr)
        desc=r.get("original_description") or r.get("description") or ""
        locs=[]
        for v in (r.get("original_section"),r.get("location")):
            if _valid_geo(v):
                cv=_canon(v)
                if cv and cv not in locs:locs.append(cv)
        if locs:
            r["_locs"]=locs
            idx[_key(desc)].append(r)
    return idx

def _resolve(row,lidx,cidx):
    desc=_norm(row["original_raw_text"])
    old=_norm(row["locality"])
    k=_key(desc)
    cand=[]
    d=_direct(desc)
    if d:cand.append((100,d,"EXPLICIT_LOCATION_IN_DESCRIPTION"))
    if k in lidx:
        locs=[]
        for r in lidx[k]:
            v=_canon(r["locality"])
            if v and v not in locs:locs.append(v)
        if len(locs)==1:cand.append((96,locs[0],"EXACT_LAYOUT_MATCH"))
    if k in cidx:
        locs=[]
        for r in cidx[k]:
            for v in r["_locs"]:
                if v not in locs:locs.append(v)
        if len(locs)==1:cand.append((93,locs[0],"EXACT_COMPLETE_MATCH"))
    if _valid_geo(old):cand.append((70,_canon(old),"EXISTING_VALID_GEOGRAPHY"))
    cand.sort(key=lambda x:x[0],reverse=True)
    if not cand:return None,0,"NO_PROVEN_LOCATION",False,{"candidates":[]}
    distinct=[]
    for x in cand:
        if x[1].casefold() not in [z.casefold() for z in distinct]:
            distinct.append(x[1])
    conflict=len(cand)>1 and len(distinct)>1 and cand[0][0]-cand[1][0]<10
    ev={"candidates":[{"confidence":x[0],"location":x[1],"rule":x[2]} for x in cand]}
    return cand[0][1],cand[0][0],cand[0][2],conflict,ev

def _quality(row,loc,conf,conflict):
    st=_norm(row["record_status"]).upper()
    desc=_norm(row["original_raw_text"])
    if st=="EXCLUDE_NON_PROPERTY" or _obvious_non_property(desc):
        return "EXCLUDED_NON_PROPERTY",0,"NON_PROPERTY"
    if not _property_like(desc):
        return "QUARANTINED",20,"WEAK_PROPERTY_EVIDENCE"
    if conflict:
        return "REVIEW",55,"SOURCE_CONFLICT"
    if loc and conf>=93:
        return "GOLD",(95 if conf>=96 else 90),"CERTIFIED"
    if loc and conf>=70:
        return "SILVER",75,"USABLE_NEEDS_VERIFICATION"
    return "REVIEW",45,"LOCATION_UNRESOLVED"

def _run(core):
    e=_engine(core)
    if e is None:return
    with LOCK:
        if STATE["status"]=="RUNNING":return
        STATE.update({"status":"RUNNING","phase":"ACQUIRING_SINGLE_WRITER_LOCK","started_at":_now(),
                      "completed_at":None,"rows_total":0,"rows_scanned":0,"progress_percent":0.0,
                      "gold":0,"silver":0,"review":0,"quarantined":0,"non_property":0,
                      "duplicate_suppressed":0,"location_repairs":0,"invalid_location_removed":0,
                      "conflicts":0,"last_source_id":None,"heartbeat_at":_now(),"error":None,
                      "details":{"write_policy":"STAGE_ONLY"}})
    lock_conn=None
    run_id=None
    try:
        _setup(e)

        # Database-wide singleton lock. Only one reconciliation process may exist across all Railway workers.
        lock_conn=e.connect()
        got=bool(lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"),{"k":LOCK_KEY}).scalar())
        if not got:
            STATE["status"]="SKIPPED"
            STATE["phase"]="ANOTHER_RECONCILIATION_ALREADY_RUNNING"
            STATE["completed_at"]=_now()
            STATE["heartbeat_at"]=_now()
            STATE["details"]={"single_writer_lock":False,"write_policy":"STAGE_ONLY"}
            return

        STATE["phase"]="COUNTING_MASTER"
        STATE["heartbeat_at"]=_now()
        with e.connect() as c:
            total=int(c.execute(text("SELECT COUNT(*) FROM pi_magazine_master")).scalar() or 0)
        STATE["rows_total"]=total

        STATE["phase"]="BUILDING_SOURCE_INDEXES"
        STATE["heartbeat_at"]=_now()
        lidx=_layout_index(e)
        cidx=_complete_index(e)

        with e.begin() as c:
            run_id=c.execute(text(f"INSERT INTO {RUNS}(version,status) VALUES(:v,'RUNNING') RETURNING id"),{"v":VERSION}).scalar()
            c.execute(text(f"DELETE FROM {STAGE}"))

        counters=Counter()
        batch_size=100
        offset=0

        while offset<total:
            STATE["phase"]="READING_BATCH"
            STATE["heartbeat_at"]=_now()
            with e.connect() as c:
                batch=[dict(r) for r in c.execute(text("""
                SELECT
                  CAST(source_id AS TEXT) AS source_id,
                  COALESCE(locality,'') AS locality,
                  COALESCE(original_raw_text,'') AS original_raw_text,
                  COALESCE(record_status,'') AS record_status,
                  COALESCE(valid_mobiles::text,'') AS valid_mobiles
                FROM pi_magazine_master
                ORDER BY source_id
                LIMIT :lim OFFSET :off
                """),{"lim":batch_size,"off":offset}).mappings().all()]
            if not batch:break

            # Stage-only writes. pi_magazine_master is never updated during reconciliation.
            with e.begin() as c:
                for row in batch:
                    sid=row["source_id"]
                    old=_norm(row["locality"])
                    loc,conf,rule,conflict,evidence=_resolve(row,lidx,cidx)
                    q,score,pstatus=_quality(row,loc,conf,conflict)
                    invalid=bool(old and (ORG_RE.search(old) or PHONE_RE.search(old) or AREA_RE.search(old)))
                    canonical=loc or ("MISSING" if invalid else (old if _valid_geo(old) else "MISSING"))

                    c.execute(text(f"""INSERT INTO {STAGE}
                    (source_id,raw_location,canonical_location,location_confidence,location_rule,conflict,
                     quality_status,quality_score,property_status,evidence,version,updated_at)
                    VALUES(:sid,:raw,:loc,:conf,:rule,:cf,:q,:score,:ps,CAST(:ev AS JSONB),:ver,NOW())
                    ON CONFLICT(source_id) DO UPDATE SET
                      raw_location=EXCLUDED.raw_location,
                      canonical_location=EXCLUDED.canonical_location,
                      location_confidence=EXCLUDED.location_confidence,
                      location_rule=EXCLUDED.location_rule,
                      conflict=EXCLUDED.conflict,
                      quality_status=EXCLUDED.quality_status,
                      quality_score=EXCLUDED.quality_score,
                      property_status=EXCLUDED.property_status,
                      evidence=EXCLUDED.evidence,
                      version=EXCLUDED.version,
                      updated_at=NOW()
                    """),{"sid":sid,"raw":old,"loc":canonical,"conf":conf,"rule":rule,"cf":conflict,
                         "q":q,"score":score,"ps":pstatus,"ev":json.dumps(evidence,ensure_ascii=False),"ver":VERSION})

                    counters[q]+=1
                    if old.casefold()!=canonical.casefold():
                        if invalid:counters["invalid_location_removed"]+=1
                        else:counters["location_repairs"]+=1
                    if conflict:counters["conflicts"]+=1

            offset+=len(batch)
            STATE.update({
                "phase":"RECONCILING","rows_scanned":offset,
                "progress_percent":round(offset*100.0/total,2) if total else 100.0,
                "gold":counters["GOLD"],"silver":counters["SILVER"],"review":counters["REVIEW"],
                "quarantined":counters["QUARANTINED"],"non_property":counters["EXCLUDED_NON_PROPERTY"],
                "location_repairs":counters["location_repairs"],
                "invalid_location_removed":counters["invalid_location_removed"],
                "conflicts":counters["conflicts"],"last_source_id":batch[-1]["source_id"],
                "heartbeat_at":_now()
            })

        STATE["phase"]="BUILDING_CERTIFIED_VIEWS"
        STATE["heartbeat_at"]=_now()
        with e.begin() as c:
            c.execute(text("DROP VIEW IF EXISTS pi_magazine_ai_training_v12003"))
            c.execute(text(f"""CREATE VIEW pi_magazine_ai_training_v12003 AS
            SELECT m.*, g.canonical_location AS governed_location,
                   g.location_confidence, g.location_rule, g.quality_status, g.quality_score
            FROM pi_magazine_master m
            JOIN {STAGE} g ON g.source_id=CAST(m.source_id AS TEXT)
            WHERE g.version='{VERSION}' AND g.quality_status='GOLD' AND g.conflict=FALSE
            """))
            c.execute(text("DROP VIEW IF EXISTS pi_magazine_operational_v12003"))
            c.execute(text(f"""CREATE VIEW pi_magazine_operational_v12003 AS
            SELECT m.*, g.canonical_location AS governed_location,
                   g.location_confidence, g.location_rule, g.quality_status, g.quality_score
            FROM pi_magazine_master m
            JOIN {STAGE} g ON g.source_id=CAST(m.source_id AS TEXT)
            WHERE g.version='{VERSION}' AND g.quality_status IN ('GOLD','SILVER')
            """))

        STATE["status"]="PASS"
        STATE["phase"]="COMPLETE"
        STATE["completed_at"]=_now()
        STATE["heartbeat_at"]=_now()
        STATE["details"]={
            "single_writer_lock":True,
            "write_policy":"STAGE_ONLY",
            "master_mutation":"NONE_DURING_RECONCILIATION",
            "operational_view":"pi_magazine_operational_v12003",
            "ai_training_view":"pi_magazine_ai_training_v12003",
            "reason":"Prevents deadlocks and preserves raw master as source evidence."
        }
        with e.begin() as c:
            c.execute(text(f"UPDATE {RUNS} SET status='PASS',completed_at=NOW(),summary=CAST(:s AS JSONB) WHERE id=:id"),
                      {"id":run_id,"s":json.dumps(STATE,ensure_ascii=False)})
    except Exception as exc:
        STATE["status"]="ERROR"
        STATE["phase"]="FAILED"
        STATE["completed_at"]=_now()
        STATE["heartbeat_at"]=_now()
        STATE["error"]=f"{type(exc).__name__}: {exc}"
        STATE["details"]={"trace":traceback.format_exc()[-7000:],"write_policy":"STAGE_ONLY"}
        if run_id:
            try:
                with e.begin() as c:
                    c.execute(text(f"UPDATE {RUNS} SET status='ERROR',completed_at=NOW(),summary=CAST(:s AS JSONB) WHERE id=:id"),
                              {"id":run_id,"s":json.dumps(STATE,ensure_ascii=False)})
            except Exception:
                pass
    finally:
        if lock_conn is not None:
            try:
                lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"),{"k":LOCK_KEY})
            except Exception:
                pass
            try:
                lock_conn.close()
            except Exception:
                pass

def _patch_magazine_screen():
    try:
        import alliance_cre_os_v1171 as cre
    except Exception:
        return False
    if getattr(cre,"_GOLDEN_SCREEN_12003",False):
        return True
    original=cre.source_data

    def source_data(engine,k,q,page,per_page):
        if k!="magazine":
            return original(engine,k,q,page,per_page)

        off=(page-1)*per_page
        pat="%"+q+"%"
        where_search="" if not q else " AND to_jsonb(x)::text ILIKE :pat "
        params={"v":VERSION,"q":q,"pat":pat,"lim":per_page,"off":off}

        with engine.connect() as c:
            total=int(c.execute(text(f"""
            SELECT COUNT(*)
            FROM pi_magazine_master x
            JOIN {STAGE} g ON g.source_id=CAST(x.source_id AS TEXT)
            WHERE g.version=:v
              AND g.quality_status IN ('GOLD','SILVER')
              AND NOT EXISTS(
                SELECT 1 FROM ai_source_record_archives a
                WHERE a.source_type='magazine' AND a.source_record_id=CAST(x.source_id AS TEXT)
              )
            """),params).scalar() or 0)

            filtered=total
            if q:
                filtered=int(c.execute(text(f"""
                SELECT COUNT(*)
                FROM pi_magazine_master x
                JOIN {STAGE} g ON g.source_id=CAST(x.source_id AS TEXT)
                WHERE g.version=:v
                  AND g.quality_status IN ('GOLD','SILVER')
                  {where_search}
                  AND NOT EXISTS(
                    SELECT 1 FROM ai_source_record_archives a
                    WHERE a.source_type='magazine' AND a.source_record_id=CAST(x.source_id AS TEXT)
                  )
                """),params).scalar() or 0)

            rs=c.execute(text(f"""
            SELECT to_jsonb(x) ||
                   jsonb_build_object(
                     'locality',g.canonical_location,
                     'data_quality_status',g.quality_status,
                     'data_quality_score',g.quality_score,
                     'location_quality_status',g.location_rule
                   )
            FROM pi_magazine_master x
            JOIN {STAGE} g ON g.source_id=CAST(x.source_id AS TEXT)
            WHERE g.version=:v
              AND g.quality_status IN ('GOLD','SILVER')
              {where_search}
              AND NOT EXISTS(
                SELECT 1 FROM ai_source_record_archives a
                WHERE a.source_type='magazine' AND a.source_record_id=CAST(x.source_id AS TEXT)
              )
            ORDER BY x.source_id DESC NULLS LAST
            LIMIT :lim OFFSET :off
            """),params).scalars().all()

        return total,filtered,[r if isinstance(r,dict) else json.loads(r) for r in rs]

    cre.source_data=source_data
    cre._GOLDEN_SCREEN_12003=True
    return True

def _start(core):
    threading.Thread(target=_run,args=(core,),daemon=True,name="golden-data-12003").start()

def register(core):
    app=_app(core); e=_engine(core)
    if app is None or e is None:raise RuntimeError("12.0.3 requires app + engine")
    _setup(e)
    patched=_patch_magazine_screen()

    @app.get("/alliance/admin/data-governance-12003",response_class=HTMLResponse)
    def page(req:Request):
        _login(core,req); s=dict(STATE)
        body=f"""<!doctype html><html><head><meta charset='utf-8'>
        <meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Alliance Golden Data 12.0.3</title>
        <style>
        body{{font-family:Arial;background:#f5f2ec;color:#27231e;margin:0}}
        main{{max-width:1160px;margin:28px auto;background:#fff;padding:25px;border-radius:14px}}
        .g{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}
        .c{{border:1px solid #ddd;border-radius:10px;padding:13px}}.n{{font-size:23px;font-weight:bold}}
        .bar{{height:18px;background:#eee;border-radius:9px;overflow:hidden}}
        .fill{{height:100%;background:#555;width:{s.get("progress_percent",0)}%}}
        button{{padding:10px 16px;border:0;border-radius:8px;cursor:pointer}}
        pre{{white-space:pre-wrap;background:#f7f7f7;padding:14px;border-radius:10px}}
        </style></head><body><main>
        <h2>Alliance Golden Data Foundation · 12.0.3</h2>
        <p><b>Single writer. Stage first. Raw master preserved.</b></p>
        <p><b>{html.escape(str(s.get("phase")))}</b></p>
        <div class='bar'><div class='fill'></div></div>
        <p>{s.get("progress_percent",0)}% · {s.get("rows_scanned",0)} / {s.get("rows_total",0)}</p>
        <div class='g'>
        <div class='c'><b>Status</b><div class='n'>{html.escape(str(s.get("status")))}</div></div>
        <div class='c'><b>GOLD</b><div class='n'>{s.get("gold",0)}</div></div>
        <div class='c'><b>SILVER</b><div class='n'>{s.get("silver",0)}</div></div>
        <div class='c'><b>Review</b><div class='n'>{s.get("review",0)}</div></div>
        <div class='c'><b>Quarantined</b><div class='n'>{s.get("quarantined",0)}</div></div>
        <div class='c'><b>Location changes proposed</b><div class='n'>{s.get("location_repairs",0)}</div></div>
        <div class='c'><b>Invalid locations removed</b><div class='n'>{s.get("invalid_location_removed",0)}</div></div>
        </div>
        <p>Heartbeat: {html.escape(str(s.get("heartbeat_at")))} · Last row: {html.escape(str(s.get("last_source_id")))}</p>
        <p><button onclick='run()'>Run Golden Reconciliation</button>
        <a href='/alliance/source/magazine'>Open Governed Magazine Database</a></p>
        <pre id='o'>{html.escape(json.dumps(s,indent=2,ensure_ascii=False))}</pre>
        <script>
        async function run(){{let r=await fetch('/api/alliance/admin/data-governance-12003/run',{{method:'POST'}});
        o.textContent=JSON.stringify(await r.json(),null,2);setTimeout(()=>location.reload(),2500)}}
        setTimeout(()=>location.reload(),5000);
        </script></main></body></html>"""
        return HTMLResponse(body,headers={"Cache-Control":"no-store"})

    @app.get("/api/alliance/admin/data-governance-12003/status")
    def status(req:Request):
        _login(core,req);return JSONResponse(dict(STATE))

    @app.post("/api/alliance/admin/data-governance-12003/run")
    def run(req:Request):
        _login(core,req)
        if STATE["status"]=="RUNNING":return {"status":"ALREADY_RUNNING","version":VERSION}
        _start(core);return {"status":"STARTED","version":VERSION}

    _start(core)
    return {
        "status":"REGISTERED","version":VERSION,
        "single_writer":True,"stage_only":True,"clean_screen":patched,
        "admin_url":"/alliance/admin/data-governance-12003"
    }
