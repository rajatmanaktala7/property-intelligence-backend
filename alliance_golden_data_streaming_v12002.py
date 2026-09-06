
from __future__ import annotations

import html, json, re, threading, traceback
from collections import Counter, defaultdict
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

VERSION = "12.0.2-STREAMING-RECONCILIATION"

STAGE = "pi_magazine_golden_stage_v12000"
RUNS = "pi_magazine_governance_runs_v12000"
SNAPSHOT = "pi_magazine_master_raw_snapshot_v12000"

BAD = {"", "MISSING", "UNKNOWN", "N/A", "NA", "NONE", "NULL", "UNSPECIFIED"}
ORG_RE = re.compile(r"(?i)\b(?:CONSTRUCTION|CONSTRUCTIONS|BUILDER|BUILDERS|DEVELOPER|DEVELOPERS|REALTOR|REALTORS|REALTY|ESTATE|ESTATES|PROPERTIES|PROPERTY\s+DEALER|INFRA|INFRASTRUCTURE|ASSOCIATES|CONSULTANTS|CONSULTANCY|PVT|LTD|LLP|ENTERPRISES|CORPORATION|COMPANY|CO\.?|GROUP|INTERIORS|ARCHITECTS)\b")
PHONE_RE = re.compile(r"(?<!\d)(?:[6-9]\d{9}|0?11[-\s]?\d{7,8})(?!\d)")
AREA_RE = re.compile(r"(?i)\b\d{2,7}(?:\.\d+)?\s*(?:SQ\.?\s*FT|SQFT|FT|SQ\.?\s*YD|SQYD|YD|Y|SQ\.?\s*M|SQM|ACRE)\b")
FLOOR_RE = re.compile(r"(?i)\b(?:BMT|BASEMENT|LGF|UGF|GF|GROUND\s*FLOOR|FF|FIRST\s*FLOOR|SF|SECOND\s*FLOOR|TF|THIRD\s*FLOOR|MEZZ|\d+(?:ST|ND|RD|TH)?\s*FLOOR)\b")
BHK_RE = re.compile(r"(?i)\b\d+\s*(?:BHK|BR)\b")
PTYPE_RE = re.compile(r"(?i)\b(?:APARTMENT|APT|FLAT|KOTHI|VILLA|PLOT|OFFICE|SHOP|SHOWROOM|WAREHOUSE|GODOWN|FACTORY|BUILDING|FARMHOUSE|FARM\s*HOUSE)\b")

STATIC = {
    "CHITRANJAN PARK":"Chitranjan Park","CR PARK":"Chitranjan Park","C R PARK":"Chitranjan Park",
    "CONNAUGHT PLACE":"Connaught Place","CP":"Connaught Place","DEFENCE COLONY":"Defence Colony",
    "EAST OF KAILASH":"East of Kailash","GREATER KAILASH 1":"Greater Kailash 1","GREATER KAILASH I":"Greater Kailash 1",
    "GREATER KAILASH 2":"Greater Kailash 2","GREATER KAILASH II":"Greater Kailash 2",
    "GREEN PARK":"Green Park","HAUZ KHAS":"Hauz Khas","JASOLA":"Jasola","KAILASH COLONY":"Kailash Colony",
    "LAJPAT NAGAR":"Lajpat Nagar","LAJPAT NAGAR 1":"Lajpat Nagar 1","LAJPAT NAGAR-1":"Lajpat Nagar 1","LAJPAT NAGAR I":"Lajpat Nagar 1",
    "LAJPAT NAGAR 2":"Lajpat Nagar 2","LAJPAT NAGAR-2":"Lajpat Nagar 2","LAJPAT NAGAR II":"Lajpat Nagar 2",
    "LAJPAT NAGAR 3":"Lajpat Nagar 3","LAJPAT NAGAR-3":"Lajpat Nagar 3","LAJPAT NAGAR III":"Lajpat Nagar 3",
    "LAJPAT NAGAR 4":"Lajpat Nagar 4","LAJPAT NAGAR-4":"Lajpat Nagar 4","LAJPAT NAGAR IV":"Lajpat Nagar 4",
    "MALVIYA NAGAR":"Malviya Nagar","NEW FRIENDS COLONY":"New Friends Colony","NFC":"New Friends Colony",
    "PANCHSHEEL PARK":"Panchsheel Park","SAFDARJUNG ENCLAVE":"Safdarjung Enclave","SAKET":"Saket",
    "SOUTH EXTENSION 1":"South Extension 1","SOUTH EXTENSION I":"South Extension 1",
    "SOUTH EXTENSION 2":"South Extension 2","SOUTH EXTENSION II":"South Extension 2",
    "VASANT KUNJ":"Vasant Kunj","VASANT VIHAR":"Vasant Vihar","GURGAON":"Gurugram","GURUGRAM":"Gurugram"
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
    "conflicts":0,"last_source_id":None,"heartbeat_at":None,"error":None,"details":{}
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
          source_id TEXT PRIMARY KEY,raw_location TEXT,canonical_location TEXT,location_confidence INTEGER NOT NULL DEFAULT 0,
          location_rule TEXT,location_entity_type TEXT,conflict BOOLEAN NOT NULL DEFAULT FALSE,duplicate_group TEXT,
          duplicate_rank INTEGER,quality_status TEXT NOT NULL,quality_score INTEGER NOT NULL DEFAULT 0,property_status TEXT NOT NULL,
          evidence JSONB NOT NULL DEFAULT '{{}}'::jsonb,version TEXT NOT NULL,updated_at TIMESTAMPTZ DEFAULT NOW())"""))
        c.execute(text(f"""CREATE TABLE IF NOT EXISTS {RUNS}(
          id BIGSERIAL PRIMARY KEY,version TEXT NOT NULL,status TEXT NOT NULL,started_at TIMESTAMPTZ DEFAULT NOW(),
          completed_at TIMESTAMPTZ,summary JSONB NOT NULL DEFAULT '{{}}'::jsonb)"""))
        for col,typ in [("data_quality_status","TEXT"),("data_quality_score","INTEGER"),("location_quality_status","TEXT"),("duplicate_group","TEXT"),("settlement_version","TEXT")]:
            c.execute(text(f"ALTER TABLE pi_magazine_master ADD COLUMN IF NOT EXISTS {col} {typ}"))
        # Snapshot only if absent. Reuse existing snapshot from 12.0.0.
        c.execute(text(f"""DO $$ BEGIN
          IF to_regclass('{SNAPSHOT}') IS NULL THEN
            EXECUTE 'CREATE TABLE {SNAPSHOT} AS SELECT * FROM pi_magazine_master';
          END IF;
        END $$;"""))

def _layout_index(e):
    idx=defaultdict(list)
    with e.connect() as c:
        exists=c.execute(text("SELECT to_regclass('pi_magazine_layout_evidence_v11921') IS NOT NULL")).scalar()
        if not exists:return idx
        rows=c.execute(text("""SELECT original_text,locality,page_number
        FROM pi_magazine_layout_evidence_v11921
        WHERE locality IS NOT NULL AND BTRIM(locality)<>''""")).mappings().all()
    for rr in rows:
        r=dict(rr)
        if _valid_geo(r["locality"]):idx[_key(r["original_text"])].append(r)
    return idx

def _complete_index(e):
    idx=defaultdict(list)
    with e.connect() as c:
        exists=c.execute(text("SELECT to_regclass('pi_magazine_complete_v860') IS NOT NULL")).scalar()
        if not exists:return idx
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
    if not cand:return None,0,"NO_PROVEN_LOCATION",False
    conflict=len(cand)>1 and len({x[1].casefold() for x in cand})>1 and cand[0][0]-cand[1][0]<10
    return cand[0][1],cand[0][0],cand[0][2],conflict

def _quality(row,loc,conf,conflict):
    st=_norm(row["record_status"]).upper()
    desc=_norm(row["original_raw_text"])
    if st=="EXCLUDE_NON_PROPERTY" or _obvious_non_property(desc):return "EXCLUDED_NON_PROPERTY",0
    if not _property_like(desc):return "QUARANTINED",20
    if conflict:return "REVIEW",55
    if loc and conf>=93:return "GOLD",95 if conf>=96 else 90
    if loc and conf>=70:return "SILVER",75
    return "REVIEW",45

def _reconcile(core):
    e=_engine(core)
    if e is None:return
    with LOCK:
        if STATE["status"]=="RUNNING":return
        STATE.update({"status":"RUNNING","phase":"INITIALIZING","started_at":_now(),"completed_at":None,
                      "rows_total":0,"rows_scanned":0,"progress_percent":0.0,"gold":0,"silver":0,"review":0,
                      "quarantined":0,"non_property":0,"duplicate_suppressed":0,"location_repairs":0,
                      "invalid_location_removed":0,"conflicts":0,"last_source_id":None,"heartbeat_at":_now(),"error":None,"details":{}})
    run_id=None
    try:
        _setup(e)
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
        offset=0
        batch_size=100
        while offset<total:
            STATE["phase"]="READING_BATCH"
            STATE["heartbeat_at"]=_now()

            # Critical fix: explicit lightweight columns only. No to_jsonb(x), no full-table preload.
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

            with e.begin() as c:
                for row in batch:
                    sid=row["source_id"]
                    old=_norm(row["locality"])
                    loc,conf,rule,conflict=_resolve(row,lidx,cidx)
                    q,score=_quality(row,loc,conf,conflict)

                    # Organization/contact noise is never allowed to remain a location.
                    invalid=bool(old and (ORG_RE.search(old) or PHONE_RE.search(old) or AREA_RE.search(old)))
                    canonical=loc or ("MISSING" if invalid else (old if _valid_geo(old) else "MISSING"))

                    c.execute(text(f"""INSERT INTO {STAGE}
                    (source_id,raw_location,canonical_location,location_confidence,location_rule,location_entity_type,
                     conflict,quality_status,quality_score,property_status,evidence,version,updated_at)
                    VALUES(:sid,:raw,:loc,:conf,:rule,:etype,:cf,:q,:score,:ps,'{{}}'::jsonb,:ver,NOW())
                    ON CONFLICT(source_id) DO UPDATE SET
                      raw_location=EXCLUDED.raw_location,canonical_location=EXCLUDED.canonical_location,
                      location_confidence=EXCLUDED.location_confidence,location_rule=EXCLUDED.location_rule,
                      location_entity_type=EXCLUDED.location_entity_type,conflict=EXCLUDED.conflict,
                      quality_status=EXCLUDED.quality_status,quality_score=EXCLUDED.quality_score,
                      property_status=EXCLUDED.property_status,version=EXCLUDED.version,updated_at=NOW()
                    """),{"sid":sid,"raw":old,"loc":canonical,"conf":conf,"rule":rule,
                         "etype":"ORGANIZATION" if ORG_RE.search(old or "") else ("LOCATION" if _valid_geo(old) else "UNKNOWN"),
                         "cf":conflict,"q":q,"score":score,"ps":"CERTIFIED" if q=="GOLD" else q,"ver":VERSION})

                    if old.casefold()!=canonical.casefold():
                        c.execute(text("UPDATE pi_magazine_master SET locality=:loc WHERE CAST(source_id AS TEXT)=:sid"),
                                  {"loc":canonical,"sid":sid})
                        if invalid:counters["invalid_location_removed"]+=1
                        else:counters["location_repairs"]+=1

                    c.execute(text("""UPDATE pi_magazine_master SET
                      data_quality_status=:q,data_quality_score=:score,location_quality_status=:rule,
                      settlement_version=:ver
                      WHERE CAST(source_id AS TEXT)=:sid"""),
                              {"q":q,"score":score,"rule":rule,"ver":VERSION,"sid":sid})

                    counters[q]+=1
                    if conflict:counters["conflicts"]+=1

            offset += len(batch)
            STATE["phase"]="RECONCILING"
            STATE["rows_scanned"]=offset
            STATE["progress_percent"]=round(offset*100.0/total,2) if total else 100.0
            STATE["gold"]=counters["GOLD"]
            STATE["silver"]=counters["SILVER"]
            STATE["review"]=counters["REVIEW"]
            STATE["quarantined"]=counters["QUARANTINED"]
            STATE["non_property"]=counters["EXCLUDED_NON_PROPERTY"]
            STATE["location_repairs"]=counters["location_repairs"]
            STATE["invalid_location_removed"]=counters["invalid_location_removed"]
            STATE["conflicts"]=counters["conflicts"]
            STATE["last_source_id"]=batch[-1]["source_id"]
            STATE["heartbeat_at"]=_now()

        STATE["phase"]="BUILDING_CERTIFIED_VIEWS"
        STATE["heartbeat_at"]=_now()
        with e.begin() as c:
            c.execute(text("DROP VIEW IF EXISTS pi_magazine_ai_training_v12000"))
            c.execute(text(f"""CREATE VIEW pi_magazine_ai_training_v12000 AS
            SELECT m.* FROM pi_magazine_master m
            JOIN {STAGE} g ON g.source_id=CAST(m.source_id AS TEXT)
            WHERE g.version='{VERSION}' AND g.quality_status='GOLD' AND g.conflict=FALSE"""))
            c.execute(text("DROP VIEW IF EXISTS pi_magazine_operational_v12000"))
            c.execute(text(f"""CREATE VIEW pi_magazine_operational_v12000 AS
            SELECT m.* FROM pi_magazine_master m
            JOIN {STAGE} g ON g.source_id=CAST(m.source_id AS TEXT)
            WHERE g.version='{VERSION}' AND g.quality_status IN ('GOLD','SILVER')"""))

        STATE["status"]="PASS"
        STATE["phase"]="COMPLETE"
        STATE["completed_at"]=_now()
        STATE["heartbeat_at"]=_now()
        STATE["details"]={
            "fix":"Master rows are streamed in 100-row batches with five explicit lightweight columns.",
            "removed_bottleneck":"SELECT to_jsonb(x) FROM pi_magazine_master",
            "ai_view":"pi_magazine_ai_training_v12000",
            "operational_view":"pi_magazine_operational_v12000"
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
        STATE["details"]={"trace":traceback.format_exc()[-7000:]}
        if run_id:
            try:
                with e.begin() as c:
                    c.execute(text(f"UPDATE {RUNS} SET status='ERROR',completed_at=NOW(),summary=CAST(:s AS JSONB) WHERE id=:id"),
                              {"id":run_id,"s":json.dumps(STATE,ensure_ascii=False)})
            except Exception:
                pass

def _start(core):
    threading.Thread(target=_reconcile,args=(core,),daemon=True,name="golden-data-12002").start()

def register(core):
    app=_app(core); e=_engine(core)
    if app is None or e is None:raise RuntimeError("12.0.2 requires app + engine")
    _setup(e)

    @app.get("/alliance/admin/data-governance-12002",response_class=HTMLResponse)
    def page(req:Request):
        _login(core,req)
        s=dict(STATE)
        body=f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Alliance Golden Data 12.0.2</title><style>
        body{{font-family:Arial;background:#f5f2ec;color:#27231e;margin:0}}
        main{{max-width:1150px;margin:28px auto;background:#fff;padding:25px;border-radius:14px}}
        .g{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}
        .c{{border:1px solid #ddd;border-radius:10px;padding:13px}}.n{{font-size:23px;font-weight:bold}}
        .bar{{height:18px;background:#eee;border-radius:9px;overflow:hidden}}.fill{{height:100%;background:#555;width:{s.get("progress_percent",0)}%}}
        button{{padding:10px 16px;border:0;border-radius:8px;cursor:pointer}}pre{{white-space:pre-wrap;background:#f7f7f7;padding:14px;border-radius:10px}}
        </style></head><body><main>
        <h2>Alliance Golden Data Foundation · 12.0.2</h2>
        <p><b>{html.escape(str(s.get("phase")))}</b> · streaming reconciliation. No whole-table JSON load.</p>
        <div class='bar'><div class='fill'></div></div><p>{s.get("progress_percent",0)}% · {s.get("rows_scanned",0)} / {s.get("rows_total",0)}</p>
        <div class='g'>
        <div class='c'><b>Status</b><div class='n'>{html.escape(str(s.get("status")))}</div></div>
        <div class='c'><b>GOLD</b><div class='n'>{s.get("gold",0)}</div></div>
        <div class='c'><b>SILVER</b><div class='n'>{s.get("silver",0)}</div></div>
        <div class='c'><b>Review</b><div class='n'>{s.get("review",0)}</div></div>
        <div class='c'><b>Quarantined</b><div class='n'>{s.get("quarantined",0)}</div></div>
        <div class='c'><b>Repairs</b><div class='n'>{s.get("location_repairs",0)}</div></div>
        <div class='c'><b>Invalid removed</b><div class='n'>{s.get("invalid_location_removed",0)}</div></div>
        </div>
        <p>Heartbeat: {html.escape(str(s.get("heartbeat_at")))} · Last row: {html.escape(str(s.get("last_source_id")))}</p>
        <p><button onclick='run()'>Run Streaming Reconciliation</button> <a href='/alliance/source/magazine'>Open Magazine Database</a></p>
        <pre id='o'>{html.escape(json.dumps(s,indent=2,ensure_ascii=False))}</pre>
        <script>
        async function run(){{let r=await fetch('/api/alliance/admin/data-governance-12002/run',{{method:'POST'}});
        o.textContent=JSON.stringify(await r.json(),null,2);setTimeout(()=>location.reload(),2500)}}
        setTimeout(()=>location.reload(),5000);
        </script></main></body></html>"""
        return HTMLResponse(body,headers={"Cache-Control":"no-store"})

    @app.get("/api/alliance/admin/data-governance-12002/status")
    def status(req:Request):
        _login(core,req);return JSONResponse(dict(STATE))

    @app.post("/api/alliance/admin/data-governance-12002/run")
    def run(req:Request):
        _login(core,req)
        if STATE["status"]=="RUNNING":return {"status":"ALREADY_RUNNING","version":VERSION}
        _start(core);return {"status":"STARTED","version":VERSION}

    _start(core)
    return {"status":"REGISTERED","version":VERSION,"admin_url":"/alliance/admin/data-governance-12002","auto_reconcile":True}
