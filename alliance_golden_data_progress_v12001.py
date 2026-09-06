
from __future__ import annotations

import html, json, re, threading, traceback, time
from collections import Counter, defaultdict
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

VERSION = "12.0.1-GOLDEN-DATA-PROGRESS-FIX"
SNAPSHOT = "pi_magazine_master_raw_snapshot_v12000"
STAGE = "pi_magazine_golden_stage_v12000"
AUDIT = "pi_magazine_governance_audit_v12000"
RUNS = "pi_magazine_governance_runs_v12000"

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
    "MAHARANI BAGH":"Maharani Bagh","MALVIYA NAGAR":"Malviya Nagar","MOHAN CO-OPERATIVE":"Mohan Cooperative","MOHAN COOPERATIVE":"Mohan Cooperative",
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
STATIC["OKHLA PHASE I"]="Okhla Phase 1"; STATIC["OKHLA PHASE II"]="Okhla Phase 2"; STATIC["OKHLA PHASE III"]="Okhla Phase 3"

LOCK=threading.Lock()
STATE={
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
    fn=getattr(core,"need_login",None); return fn(req) if fn else "team"
def _norm(v): return re.sub(r"\s+"," ",str(v or "")).strip()
def _key(v):
    s=PHONE_RE.sub(" ",_norm(v).upper()); s=re.sub(r"[^A-Z0-9]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()
def _bad(v): return _norm(v).upper() in BAD
def _property_like(v):
    u=_norm(v).upper()
    if not u: return False
    address=bool(re.match(r"^\s*(?:[A-Z]{0,4}[-/]?\d+[A-Z]?|\d+[A-Z]?|[A-Z]-BLOCK|[A-Z]-BLK)\b",u))
    return address and bool(AREA_RE.search(u) or FLOOR_RE.search(u) or BHK_RE.search(u) or PTYPE_RE.search(u))
def _obvious_non_property(v):
    s=_norm(v)
    if not s: return True
    if not (AREA_RE.search(s) or FLOOR_RE.search(s) or BHK_RE.search(s) or PTYPE_RE.search(s)):
        if PHONE_RE.search(s) and len(_norm(PHONE_RE.sub(" ",s)))<45: return True
    return False
def _canonicalize(raw):
    u=_norm(raw).upper().strip(" ,;:|")
    if u in STATIC: return STATIC[u]
    m=re.fullmatch(r"(NOIDA|GURUGRAM|GURGAON|DWARKA|ROHINI)\s+SECTOR\s+(\d+[A-Z]?)",u)
    if m:
        city="Gurugram" if m.group(1) in {"GURUGRAM","GURGAON"} else m.group(1).title()
        return f"{city} Sector {m.group(2)}"
    return None
def _valid_geo(raw):
    s=_norm(raw)
    if not s or s.upper() in BAD: return False
    if ORG_RE.search(s) or PHONE_RE.search(s) or AREA_RE.search(s): return False
    if len(s)>70: return False
    if _canonicalize(s): return True
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z .'-]{2,45}(?:\s+(?:NAGAR|VIHAR|KUNJ|BAGH|PARK|COLONY|ENCLAVE|EXTENSION|EXTN|PLACE|MARKET|MARG|ROAD|PHASE\s+\d+))",s,re.I))
def _entity_type(raw):
    s=_norm(raw)
    if not s:return "EMPTY"
    if ORG_RE.search(s):return "ORGANIZATION"
    if PHONE_RE.search(s):return "CONTACT"
    if AREA_RE.search(s):return "PROPERTY_DETAIL"
    if _valid_geo(s):return "LOCATION"
    return "UNKNOWN"
def _pk(d):
    for k in ("source_id","id","record_id","property_id"):
        if d.get(k) is not None:return str(d.get(k))
    return ""
def _desc(d):
    for k in ("original_raw_text","original_description","description"):
        if d.get(k):return _norm(d.get(k))
    return ""
def _loc(d):
    for k in ("locality","location","locality_clean"):
        if k in d:return _norm(d.get(k))
    return ""
def _phone_key(d):
    for k in ("valid_mobiles","contact_number","contact_numbers","valid_landlines","partial_contacts"):
        if d.get(k) not in (None,"",[],{}):
            return _key(json.dumps(d.get(k),ensure_ascii=False) if isinstance(d.get(k),(list,dict)) else d.get(k))
    return ""
def _table_exists(e,t):
    with e.connect() as c:return bool(c.execute(text("SELECT to_regclass(:t) IS NOT NULL"),{"t":t}).scalar())

def _setup(e):
    with e.begin() as c:
        c.execute(text(f"""CREATE TABLE IF NOT EXISTS {STAGE}(
          source_id TEXT PRIMARY KEY,raw_location TEXT,canonical_location TEXT,location_confidence INTEGER NOT NULL DEFAULT 0,
          location_rule TEXT,location_entity_type TEXT,conflict BOOLEAN NOT NULL DEFAULT FALSE,duplicate_group TEXT,
          duplicate_rank INTEGER,quality_status TEXT NOT NULL,quality_score INTEGER NOT NULL DEFAULT 0,property_status TEXT NOT NULL,
          evidence JSONB NOT NULL DEFAULT '{{}}'::jsonb,version TEXT NOT NULL,updated_at TIMESTAMPTZ DEFAULT NOW())"""))
        c.execute(text(f"""CREATE TABLE IF NOT EXISTS {AUDIT}(
          id BIGSERIAL PRIMARY KEY,source_id TEXT NOT NULL,field_name TEXT NOT NULL,before_value TEXT,after_value TEXT,
          action TEXT NOT NULL,confidence INTEGER NOT NULL,rule TEXT NOT NULL,evidence JSONB NOT NULL DEFAULT '{{}}'::jsonb,
          version TEXT NOT NULL,created_at TIMESTAMPTZ DEFAULT NOW())"""))
        c.execute(text(f"""CREATE TABLE IF NOT EXISTS {RUNS}(
          id BIGSERIAL PRIMARY KEY,version TEXT NOT NULL,status TEXT NOT NULL,started_at TIMESTAMPTZ DEFAULT NOW(),
          completed_at TIMESTAMPTZ,summary JSONB NOT NULL DEFAULT '{{}}'::jsonb)"""))
        for col,typ in [("data_quality_status","TEXT"),("data_quality_score","INTEGER"),("location_quality_status","TEXT"),("duplicate_group","TEXT"),("settlement_version","TEXT")]:
            c.execute(text(f"ALTER TABLE pi_magazine_master ADD COLUMN IF NOT EXISTS {col} {typ}"))
        c.execute(text(f"CREATE TABLE IF NOT EXISTS {SNAPSHOT} AS SELECT *, NOW() snapshot_created_at FROM pi_magazine_master"))

def _master_rows(e):
    with e.connect() as c:
        vals=c.execute(text("SELECT to_jsonb(x) FROM pi_magazine_master x ORDER BY source_id")).scalars().all()
    return [v if isinstance(v,dict) else json.loads(v) for v in vals]

def _references(e,masters):
    refs=dict(STATIC)
    cnt=Counter(_loc(d) for d in masters if _valid_geo(_loc(d)))
    for v,n in cnt.items():
        if n>=3: refs[v.upper()]=_canonicalize(v) or v
    return refs

def _direct(desc,refs):
    u=_norm(desc).upper()
    hits=[]
    for raw,canon in sorted(refs.items(),key=lambda kv:len(kv[0]),reverse=True):
        if len(raw)>=3 and re.search(r"(?<![A-Z0-9])"+re.escape(raw)+r"(?![A-Z0-9])",u):
            hits.append(canon)
    uniq=[]
    for x in hits:
        if x not in uniq:uniq.append(x)
    uniq.sort(key=lambda x:(bool(re.search(r"\d",x)),len(x)),reverse=True)
    return uniq[0] if uniq else None

def _layout_index(e):
    idx=defaultdict(list)
    if not _table_exists(e,"pi_magazine_layout_evidence_v11921"):return idx
    with e.connect() as c:
        rows=c.execute(text("""SELECT original_text,locality,page_number,upload_id::text
        FROM pi_magazine_layout_evidence_v11921 WHERE locality IS NOT NULL AND BTRIM(locality)<>''""")).mappings().all()
    for r in rows:
        r=dict(r)
        if _valid_geo(r["locality"]):idx[_key(r["original_text"])].append(r)
    return idx

def _complete_index(e):
    idx=defaultdict(list)
    if not _table_exists(e,"pi_magazine_complete_v860"):return idx
    with e.connect() as c:
        rows=c.execute(text("""SELECT source_record_id,original_description,description,original_section,location,page_number,upload_id::text
        FROM pi_magazine_complete_v860""")).mappings().all()
    for rr in rows:
        r=dict(rr); desc=r.get("original_description") or r.get("description") or ""
        locs=[]
        for v in (r.get("original_section"),r.get("location")):
            if _valid_geo(v):
                cv=_canonicalize(v) or _norm(v)
                if cv not in locs:locs.append(cv)
        if locs:
            r["_locs"]=locs; idx[_key(desc)].append(r)
    return idx

def _resolve(d,refs,lidx,cidx):
    desc=_desc(d); old=_loc(d); key=_key(desc); cand=[]
    x=_direct(desc,refs)
    if x:cand.append((100,x,"EXPLICIT_LOCATION_IN_DESCRIPTION",{"description":desc}))
    if key in lidx:
        locs=[]
        for r in lidx[key]:
            v=_canonicalize(r["locality"]) or _norm(r["locality"])
            if v not in locs:locs.append(v)
        if len(locs)==1:cand.append((96,locs[0],"EXACT_LAYOUT_MATCH",{"matches":len(lidx[key])}))
    if key in cidx:
        locs=[]; witness=None
        for r in cidx[key]:
            for v in r["_locs"]:
                if v not in locs:locs.append(v)
            witness=r
        if len(locs)==1:cand.append((93,locs[0],"EXACT_COMPLETE_MATCH",{"source_record_id":witness.get("source_record_id") if witness else None}))
    if _valid_geo(old):cand.append((70,_canonicalize(old) or old,"EXISTING_VALID_GEOGRAPHY",{"old":old}))
    cand.sort(key=lambda z:z[0],reverse=True)
    if not cand:return None,0,"NO_PROVEN_LOCATION",{},False
    conflict=len({x[1].casefold() for x in cand})>1 and len(cand)>1 and cand[0][0]-cand[1][0]<10
    return cand[0][1],cand[0][0],cand[0][2],{"candidates":[{"confidence":x[0],"location":x[1],"rule":x[2]} for x in cand]},conflict

def _quality(d,loc,conf,conflict,rank):
    st=_norm(d.get("record_status") or d.get("verification_status")).upper()
    if st=="EXCLUDE_NON_PROPERTY" or _obvious_non_property(_desc(d)):return "EXCLUDED_NON_PROPERTY",0,"NON_PROPERTY"
    if not _property_like(_desc(d)):return "QUARANTINED",20,"WEAK_PROPERTY_EVIDENCE"
    if rank and rank>1:return "DUPLICATE_SUPPRESSED",40,"DUPLICATE"
    if conflict:return "REVIEW",55,"SOURCE_CONFLICT"
    if loc and conf>=93:return "GOLD",(95 if conf>=96 else 90),"CERTIFIED"
    if loc and conf>=70:return "SILVER",75,"USABLE_NEEDS_VERIFICATION"
    return "REVIEW",45,"LOCATION_UNRESOLVED"

def _set_progress(phase,scanned,total,counters=None,sid=None):
    STATE["phase"]=phase; STATE["rows_scanned"]=scanned; STATE["rows_total"]=total
    STATE["progress_percent"]=round((scanned*100.0/total),2) if total else 0.0
    STATE["heartbeat_at"]=_now()
    if sid is not None:STATE["last_source_id"]=sid
    if counters:
        for k in ("GOLD","SILVER","REVIEW","QUARANTINED","EXCLUDED_NON_PROPERTY","DUPLICATE_SUPPRESSED","location_repairs","invalid_location_removed","conflicts"):
            target={"GOLD":"gold","SILVER":"silver","REVIEW":"review","QUARANTINED":"quarantined","EXCLUDED_NON_PROPERTY":"non_property","DUPLICATE_SUPPRESSED":"duplicate_suppressed"}.get(k,k)
            STATE[target]=int(counters[k])

def _reconcile(core):
    e=_engine(core)
    if e is None:return
    with LOCK:
        if STATE["status"]=="RUNNING":return
        STATE.update({"status":"RUNNING","phase":"INITIALIZING","started_at":_now(),"completed_at":None,"rows_total":0,"rows_scanned":0,
                      "progress_percent":0.0,"gold":0,"silver":0,"review":0,"quarantined":0,"non_property":0,
                      "duplicate_suppressed":0,"location_repairs":0,"invalid_location_removed":0,"conflicts":0,
                      "last_source_id":None,"heartbeat_at":_now(),"error":None,"details":{}})
    run_id=None
    try:
        _setup(e)
        _set_progress("LOADING_MASTER",0,0)
        masters=_master_rows(e); total=len(masters)
        STATE["rows_total"]=total; STATE["heartbeat_at"]=_now()
        refs=_references(e,masters)
        _set_progress("BUILDING_SOURCE_INDEXES",0,total)
        lidx=_layout_index(e); cidx=_complete_index(e)

        grp=defaultdict(list)
        for d in masters:
            k=(_key(_desc(d)),_phone_key(d))
            if k[0]:grp[k].append(d)
        dup={}
        gi=0
        for vals in grp.values():
            if len(vals)<=1:continue
            gi+=1; gid=f"MAG-GOLD-DUP-{gi:05d}"
            vals=sorted(vals,key=lambda x:(1 if _property_like(_desc(x)) else 0,1 if _valid_geo(_loc(x)) else 0,len([v for v in x.values() if v not in (None,"",[],{})])),reverse=True)
            for rank,d in enumerate(vals,1):dup[_pk(d)]=(gid,rank)

        with e.begin() as c:
            run_id=c.execute(text(f"INSERT INTO {RUNS}(version,status) VALUES(:v,'RUNNING') RETURNING id"),{"v":VERSION}).scalar()
            c.execute(text(f"DELETE FROM {STAGE}"))

        counters=Counter()
        BATCH=100
        for start in range(0,total,BATCH):
            batch=masters[start:start+BATCH]
            with e.begin() as c:
                for d in batch:
                    sid=_pk(d)
                    if not sid:continue
                    old=_loc(d); loc,conf,rule,evidence,conflict=_resolve(d,refs,lidx,cidx)
                    dg,rank=dup.get(sid,(None,None))
                    q,score,pstatus=_quality(d,loc,conf,conflict,rank)
                    et=_entity_type(old)
                    canonical=loc or ("MISSING" if et in {"ORGANIZATION","CONTACT","PROPERTY_DETAIL"} else old or "MISSING")
                    if canonical!="MISSING" and not _valid_geo(canonical):canonical="MISSING"

                    c.execute(text(f"""INSERT INTO {STAGE}
                    (source_id,raw_location,canonical_location,location_confidence,location_rule,location_entity_type,conflict,
                     duplicate_group,duplicate_rank,quality_status,quality_score,property_status,evidence,version,updated_at)
                    VALUES(:sid,:raw,:loc,:conf,:rule,:et,:cf,:dg,:rank,:q,:score,:ps,CAST(:ev AS JSONB),:ver,NOW())
                    ON CONFLICT(source_id) DO UPDATE SET raw_location=EXCLUDED.raw_location,canonical_location=EXCLUDED.canonical_location,
                    location_confidence=EXCLUDED.location_confidence,location_rule=EXCLUDED.location_rule,location_entity_type=EXCLUDED.location_entity_type,
                    conflict=EXCLUDED.conflict,duplicate_group=EXCLUDED.duplicate_group,duplicate_rank=EXCLUDED.duplicate_rank,
                    quality_status=EXCLUDED.quality_status,quality_score=EXCLUDED.quality_score,property_status=EXCLUDED.property_status,
                    evidence=EXCLUDED.evidence,version=EXCLUDED.version,updated_at=NOW()"""),
                    {"sid":sid,"raw":old,"loc":canonical,"conf":conf,"rule":rule,"et":et,"cf":conflict,"dg":dg,"rank":rank,
                     "q":q,"score":score,"ps":pstatus,"ev":json.dumps(evidence,ensure_ascii=False),"ver":VERSION})

                    if q in {"GOLD","SILVER","REVIEW"} and old.casefold()!=canonical.casefold():
                        c.execute(text("UPDATE pi_magazine_master SET locality=:loc WHERE CAST(source_id AS TEXT)=:sid"),{"loc":canonical,"sid":sid})
                        counters["location_repairs"]+=1
                    elif et in {"ORGANIZATION","CONTACT","PROPERTY_DETAIL"} and old!="MISSING":
                        c.execute(text("UPDATE pi_magazine_master SET locality='MISSING' WHERE CAST(source_id AS TEXT)=:sid"),{"sid":sid})
                        counters["invalid_location_removed"]+=1

                    c.execute(text("""UPDATE pi_magazine_master SET data_quality_status=:q,data_quality_score=:score,
                    location_quality_status=:lq,duplicate_group=:dg,settlement_version=:ver WHERE CAST(source_id AS TEXT)=:sid"""),
                    {"q":q,"score":score,"lq":rule,"dg":dg,"ver":VERSION,"sid":sid})

                    counters[q]+=1
                    if conflict:counters["conflicts"]+=1

            scanned=min(start+BATCH,total)
            _set_progress("RECONCILING",scanned,total,counters,batch[-1] and _pk(batch[-1]))

        _set_progress("BUILDING_CERTIFIED_VIEWS",total,total,counters,STATE["last_source_id"])
        with e.begin() as c:
            c.execute(text("DROP VIEW IF EXISTS pi_magazine_ai_training_v12000"))
            c.execute(text(f"""CREATE VIEW pi_magazine_ai_training_v12000 AS
            SELECT m.* FROM pi_magazine_master m JOIN {STAGE} g ON g.source_id=CAST(m.source_id AS TEXT)
            WHERE g.quality_status='GOLD' AND COALESCE(g.duplicate_rank,1)=1 AND g.conflict=FALSE"""))
            c.execute(text("DROP VIEW IF EXISTS pi_magazine_operational_v12000"))
            c.execute(text(f"""CREATE VIEW pi_magazine_operational_v12000 AS
            SELECT m.* FROM pi_magazine_master m JOIN {STAGE} g ON g.source_id=CAST(m.source_id AS TEXT)
            WHERE g.quality_status IN ('GOLD','SILVER') AND COALESCE(g.duplicate_rank,1)=1"""))

        STATE["status"]="PASS"; STATE["phase"]="COMPLETE"; STATE["completed_at"]=_now(); STATE["heartbeat_at"]=_now()
        STATE["details"]={"batch_size":BATCH,"gold_view":"pi_magazine_ai_training_v12000","operational_view":"pi_magazine_operational_v12000",
                          "note":"12.0.1 reports live progress; 12.0.0 showed zeros until the entire reconciliation completed."}
        with e.begin() as c:
            c.execute(text(f"UPDATE {RUNS} SET status='PASS',completed_at=NOW(),summary=CAST(:s AS JSONB) WHERE id=:id"),
                      {"id":run_id,"s":json.dumps(STATE,ensure_ascii=False)})
    except Exception as exc:
        STATE["status"]="ERROR"; STATE["phase"]="FAILED"; STATE["completed_at"]=_now(); STATE["heartbeat_at"]=_now()
        STATE["error"]=f"{type(exc).__name__}: {exc}"; STATE["details"]={"trace":traceback.format_exc()[-7000:]}
        if run_id:
            try:
                with e.begin() as c:c.execute(text(f"UPDATE {RUNS} SET status='ERROR',completed_at=NOW(),summary=CAST(:s AS JSONB) WHERE id=:id"),{"id":run_id,"s":json.dumps(STATE,ensure_ascii=False)})
            except Exception:pass

def _start(core):
    threading.Thread(target=_reconcile,args=(core,),daemon=True,name="golden-data-12001").start()

def register(core):
    app=_app(core); e=_engine(core)
    if app is None or e is None:raise RuntimeError("12.0.1 requires app + engine")
    _setup(e)

    @app.get("/alliance/admin/data-governance-12001",response_class=HTMLResponse)
    def page(req:Request):
        _login(core,req); s=dict(STATE)
        body=f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Alliance Golden Data 12.0.1</title><style>
        body{{font-family:Arial;background:#f5f2ec;color:#27231e;margin:0}}main{{max-width:1150px;margin:30px auto;background:#fff;padding:26px;border-radius:14px}}
        .g{{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:10px}}.c{{border:1px solid #ddd;border-radius:10px;padding:14px}}
        .n{{font-size:24px;font-weight:bold}}.bar{{height:18px;background:#eee;border-radius:9px;overflow:hidden}}.fill{{height:100%;background:#555;width:{s.get("progress_percent",0)}%}}
        button{{padding:10px 16px;border:0;border-radius:8px;cursor:pointer}}pre{{white-space:pre-wrap;background:#f7f7f7;padding:14px;border-radius:10px}}
        </style></head><body><main><h2>Alliance Golden Data Foundation · 12.0.1</h2>
        <p><b>{html.escape(str(s.get("phase")))}</b> · live progress, 100-row transactional batches.</p>
        <div class='bar'><div class='fill'></div></div><p>{s.get("progress_percent",0)}% · {s.get("rows_scanned",0)} / {s.get("rows_total",0)}</p>
        <div class='g'>
        <div class='c'><b>Status</b><div class='n'>{html.escape(str(s.get("status")))}</div></div>
        <div class='c'><b>GOLD</b><div class='n'>{s.get("gold",0)}</div></div><div class='c'><b>SILVER</b><div class='n'>{s.get("silver",0)}</div></div>
        <div class='c'><b>Review</b><div class='n'>{s.get("review",0)}</div></div><div class='c'><b>Quarantined</b><div class='n'>{s.get("quarantined",0)}</div></div>
        <div class='c'><b>Duplicates</b><div class='n'>{s.get("duplicate_suppressed",0)}</div></div><div class='c'><b>Repairs</b><div class='n'>{s.get("location_repairs",0)}</div></div>
        </div><p>Heartbeat: {html.escape(str(s.get("heartbeat_at")))} · Last row: {html.escape(str(s.get("last_source_id")))}</p>
        <p><button onclick='run()'>Run Reconciliation</button> <a href='/alliance/source/magazine'>Open Magazine Database</a></p>
        <pre id='o'>{html.escape(json.dumps(s,indent=2,ensure_ascii=False))}</pre>
        <script>
        async function run(){{let r=await fetch('/api/alliance/admin/data-governance-12001/run',{{method:'POST'}});o.textContent=JSON.stringify(await r.json(),null,2);setTimeout(()=>location.reload(),2500)}}
        setTimeout(()=>location.reload(),5000);
        </script></main></body></html>"""
        return HTMLResponse(body,headers={"Cache-Control":"no-store"})

    @app.get("/api/alliance/admin/data-governance-12001/status")
    def status(req:Request):_login(core,req);return JSONResponse(dict(STATE))
    @app.post("/api/alliance/admin/data-governance-12001/run")
    def run(req:Request):
        _login(core,req)
        if STATE["status"]=="RUNNING":return {"status":"ALREADY_RUNNING","version":VERSION}
        _start(core);return {"status":"STARTED","version":VERSION}

    _start(core)
    return {"status":"REGISTERED","version":VERSION,"admin_url":"/alliance/admin/data-governance-12001","auto_reconcile":True}
