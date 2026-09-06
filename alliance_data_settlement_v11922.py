from __future__ import annotations
import html, json, re, threading, traceback
from collections import defaultdict
from datetime import datetime, timezone
from sqlalchemy import text
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

VERSION="11.9.22-DATA-SETTLEMENT-ENGINE"
AUDIT="pi_data_settlement_audit_v11922"
RUNS="pi_data_settlement_runs_v11922"
REF="pi_location_reference_v11922"
BAD={"","MISSING","UNKNOWN","N/A","NA","NONE","NULL","UNSPECIFIED"}
NON_PROPERTY={"EXCLUDE_NON_PROPERTY","ARCHIVED","REJECTED"}
ORG_RE=re.compile(r"(?i)\\b(CONSTRUCTION|CONSTRUCTIONS|BUILDER|BUILDERS|DEVELOPER|DEVELOPERS|REALTOR|REALTORS|REALTY|ESTATE|ESTATES|PROPERTIES|PROPERTY DEALER|INFRA|INFRASTRUCTURE|ASSOCIATES|CONSULTANTS|CONSULTANCY|PVT|LTD|LLP|ENTERPRISES|CORPORATION|COMPANY|GROUP|INTERIORS|ARCHITECTS)\\b")
PHONE_RE=re.compile(r"(?<!\\d)(?:[6-9]\\d{9}|0?11[-\\s]?\\d{7,8})(?!\\d)")
AREA_RE=re.compile(r"(?i)\\b\\d{2,7}(?:\\.\\d+)?\\s*(?:SQ\\.?\\s*FT|SQFT|FT|SQ\\.?\\s*YD|SQYD|YD|Y|SQ\\.?\\s*M|SQM|ACRE)\\b")
FLOOR_RE=re.compile(r"(?i)\\b(?:BMT|BASEMENT|LGF|UGF|GF|FF|SF|TF|MEZZ|\\d+(?:ST|ND|RD|TH)?\\s*FLOOR)\\b")

CANONICAL={
"ALAKNANDA":"Alaknanda","ANAND LOK":"Anand Lok","ANAND NIKETAN":"Anand Niketan","ASIAD VILLAGE":"Asiad Village",
"BHIKAJI CAMA PLACE":"Bhikaji Cama Place","CHANAKYAPURI":"Chanakyapuri","CHHATARPUR":"Chhatarpur","CHHATARPUR ENCLAVE":"Chhatarpur Enclave",
"CHIRAG DELHI":"Chirag Delhi","CHITRANJAN PARK":"Chitranjan Park","CR PARK":"Chitranjan Park","C R PARK":"Chitranjan Park",
"CONNAUGHT PLACE":"Connaught Place","CP":"Connaught Place","DEFENCE COLONY":"Defence Colony","DERA MANDI":"Dera Mandi","DWARKA":"Dwarka",
"EAST OF KAILASH":"East of Kailash","FRIENDS COLONY":"Friends Colony","GAUTAM NAGAR":"Gautam Nagar","GOLF LINKS":"Golf Links",
"GREATER KAILASH 1":"Greater Kailash 1","GREATER KAILASH I":"Greater Kailash 1","GREATER KAILASH-1":"Greater Kailash 1","GK 1":"Greater Kailash 1","GK-I":"Greater Kailash 1",
"GREATER KAILASH 2":"Greater Kailash 2","GREATER KAILASH II":"Greater Kailash 2","GREATER KAILASH-2":"Greater Kailash 2","GK 2":"Greater Kailash 2","GK-II":"Greater Kailash 2",
"GREEN PARK":"Green Park","GREEN PARK EXTN":"Green Park Extension","GREEN PARK EXTENSION":"Green Park Extension","GURGAON":"Gurugram","GURUGRAM":"Gurugram",
"HAUZ KHAS":"Hauz Khas","JASOLA":"Jasola","JOR BAGH":"Jor Bagh","KAILASH COLONY":"Kailash Colony","LAJPAT NAGAR":"Lajpat Nagar",
"LAJPAT NAGAR 1":"Lajpat Nagar 1","LAJPAT NAGAR-1":"Lajpat Nagar 1","LAJPAT NAGAR I":"Lajpat Nagar 1",
"LAJPAT NAGAR 2":"Lajpat Nagar 2","LAJPAT NAGAR-2":"Lajpat Nagar 2","LAJPAT NAGAR II":"Lajpat Nagar 2",
"LAJPAT NAGAR 3":"Lajpat Nagar 3","LAJPAT NAGAR-3":"Lajpat Nagar 3","LAJPAT NAGAR III":"Lajpat Nagar 3",
"LAJPAT NAGAR 4":"Lajpat Nagar 4","LAJPAT NAGAR-4":"Lajpat Nagar 4","LAJPAT NAGAR IV":"Lajpat Nagar 4",
"MAHARANI BAGH":"Maharani Bagh","MALVIYA NAGAR":"Malviya Nagar","MOHAN CO-OPERATIVE":"Mohan Cooperative","MOHAN COOPERATIVE":"Mohan Cooperative",
"NEW FRIENDS COLONY":"New Friends Colony","NFC":"New Friends Colony","NITI BAGH":"Niti Bagh","NIZAMUDDIN":"Nizamuddin","NIZAMUDDIN EAST":"Nizamuddin East","NIZAMUDDIN WEST":"Nizamuddin West",
"OKHLA PHASE 1":"Okhla Phase 1","OKHLA PHASE-1":"Okhla Phase 1","OKHLA-1":"Okhla Phase 1","OKHLA 1":"Okhla Phase 1","OKHLA PHASE I":"Okhla Phase 1",
"OKHLA PHASE 2":"Okhla Phase 2","OKHLA PHASE-2":"Okhla Phase 2","OKHLA-2":"Okhla Phase 2","OKHLA 2":"Okhla Phase 2","OKHLA PHASE II":"Okhla Phase 2",
"OKHLA PHASE 3":"Okhla Phase 3","OKHLA PHASE-3":"Okhla Phase 3","OKHLA-3":"Okhla Phase 3","OKHLA 3":"Okhla Phase 3","OKHLA PHASE III":"Okhla Phase 3",
"PANCHSHEEL ENCLAVE":"Panchsheel Enclave","PANCHSHEEL PARK":"Panchsheel Park","PITAMPURA":"Pitampura","ROHINI":"Rohini",
"SAFDARJUNG ENCLAVE":"Safdarjung Enclave","SAFDARJUNG DEVELOPMENT AREA":"Safdarjung Development Area","SDA":"Safdarjung Development Area",
"SAINIK FARM":"Sainik Farm","SAKET":"Saket","SARVODAYA ENCLAVE":"Sarvodaya Enclave","SHANTI NIKETAN":"Shanti Niketan",
"SOUTH EXTENSION":"South Extension","SOUTH EXTENSION 1":"South Extension 1","SOUTH EXTENSION I":"South Extension 1","SOUTH EXTENSION 2":"South Extension 2","SOUTH EXTENSION II":"South Extension 2",
"SUNDER NAGAR":"Sunder Nagar","TUGHLAKABAD":"Tughlakabad","TUGHLAKABAD EXTN":"Tughlakabad Extension","VASANT KUNJ":"Vasant Kunj","VASANT VIHAR":"Vasant Vihar"
}

LOCK=threading.Lock()
STATE={"status":"IDLE","started_at":None,"completed_at":None,"rows_scanned":0,"invalid_locations_quarantined":0,"direct_location_repairs":0,"duplicate_groups":0,"training_ready":0,"needs_review":0,"excluded_non_property":0,"error":None,"details":{}}

def _utcnow(): return datetime.now(timezone.utc).isoformat()
def _app(core): return getattr(core,"app",None) or core
def _engine(core): return getattr(core,"engine",None)
def _login(core,req):
    fn=getattr(core,"need_login",None); return fn(req) if fn else "team"
def _qid(v):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*",str(v or "")): raise ValueError("unsafe identifier")
    return '"'+str(v)+'"'
def _norm(v): return re.sub(r"\\s+"," ",str(v or "")).strip()
def _norm_key(v):
    s=PHONE_RE.sub(" ",_norm(v).upper()); s=re.sub(r"[^A-Z0-9]+"," ",s); return re.sub(r"\\s+"," ",s).strip()
def _bad(v): return _norm(v).upper() in BAD

def _property_like(desc):
    u=_norm(desc).upper()
    addr=bool(re.match(r"^\\s*(?:[A-Z]{0,4}[-/]?\\d+[A-Z]?|\\d+[A-Z]?|[A-Z]-BLOCK|[A-Z]-BLK)\\b",u))
    return bool(u and addr and (AREA_RE.search(u) or FLOOR_RE.search(u)))

def _extract_location(desc):
    u=_norm(desc).upper(); hits=[]
    for raw,canon in sorted(CANONICAL.items(),key=lambda kv:len(kv[0]),reverse=True):
        if re.search(r"(?<![A-Z0-9])"+re.escape(raw)+r"(?![A-Z0-9])",u): hits.append(canon)
    for m in re.finditer(r"\\b(NOIDA|GURUGRAM|GURGAON|DWARKA|ROHINI)\\s+SECTOR\\s+(\\d+[A-Z]?)\\b",u):
        city="Gurugram" if m.group(1) in {"GURUGRAM","GURGAON"} else m.group(1).title(); hits.append(f"{city} Sector {m.group(2)}")
    uniq=[]
    for x in hits:
        if x not in uniq: uniq.append(x)
    for x in uniq:
        if re.search(r"\\d$",x): return x
    return uniq[0] if uniq else None

def _invalid_location(v):
    s=_norm(v); u=s.upper()
    if not s or u in BAD: return False
    if ORG_RE.search(u) or PHONE_RE.search(u) or AREA_RE.search(u): return True
    if re.search(r"(?i)\\b(?:OWNER|BROKER|EMPLOYEE|CARE\\s*TAKER|CONTACT|MOB|PHONE)\\b",u): return True
    if len(s)>70: return True
    if u in CANONICAL: return False
    if re.match(r"(?i)^(?:NOIDA|GURUGRAM|GURGAON|DWARKA|ROHINI)\\s+SECTOR\\s+\\d+[A-Z]?$",s): return False
    return None

def _canon_known(v):
    u=_norm(v).upper().strip(" ,;:|")
    if u in CANONICAL: return CANONICAL[u]
    m=re.fullmatch(r"(NOIDA|GURUGRAM|GURGAON|DWARKA|ROHINI)\\s+SECTOR\\s+(\\d+[A-Z]?)",u)
    if m:
        city="Gurugram" if m.group(1) in {"GURUGRAM","GURGAON"} else m.group(1).title(); return f"{city} Sector {m.group(2)}"
    return None

def _setup(e):
    with e.begin() as c:
        c.execute(text(f"CREATE TABLE IF NOT EXISTS {_qid(REF)}(raw_value TEXT PRIMARY KEY,canonical_value TEXT NOT NULL,entity_type TEXT NOT NULL DEFAULT 'LOCATION',active BOOLEAN NOT NULL DEFAULT TRUE,created_at TIMESTAMPTZ DEFAULT NOW())"))
        for raw,canon in CANONICAL.items(): c.execute(text(f"INSERT INTO {_qid(REF)}(raw_value,canonical_value) VALUES(:r,:c) ON CONFLICT(raw_value) DO UPDATE SET canonical_value=EXCLUDED.canonical_value"),{"r":raw,"c":canon})
        c.execute(text(f"CREATE TABLE IF NOT EXISTS {_qid(AUDIT)}(id BIGSERIAL PRIMARY KEY,source_id TEXT NOT NULL,field_name TEXT NOT NULL,before_value TEXT,after_value TEXT,action TEXT NOT NULL,reason TEXT NOT NULL,confidence INTEGER NOT NULL,version TEXT NOT NULL,created_at TIMESTAMPTZ DEFAULT NOW())"))
        c.execute(text(f"CREATE TABLE IF NOT EXISTS {_qid(RUNS)}(id BIGSERIAL PRIMARY KEY,version TEXT NOT NULL,status TEXT NOT NULL,started_at TIMESTAMPTZ DEFAULT NOW(),completed_at TIMESTAMPTZ,summary JSONB NOT NULL DEFAULT '{{}}'::jsonb)"))
        for col,typ in [("data_quality_status","TEXT"),("data_quality_score","INTEGER"),("location_quality_status","TEXT"),("duplicate_group","TEXT"),("settlement_version","TEXT")]: c.execute(text(f"ALTER TABLE pi_magazine_master ADD COLUMN IF NOT EXISTS {col} {typ}"))

def _meta(e):
    with e.connect() as c: cols=[r[0] for r in c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='pi_magazine_master'")).all()]
    low={x.lower():x for x in cols}
    return {"pk":low.get("source_id") or low.get("id") or low.get("record_id"),"desc":low.get("original_raw_text") or low.get("original_description") or low.get("description"),"loc":low.get("locality") or low.get("location"),"status":low.get("record_status") or low.get("verification_status"),"phone":low.get("valid_mobiles") or low.get("contact_number") or low.get("contact_numbers")}

def _audit(c,sid,field,before,after,action,reason,conf):
    c.execute(text(f"INSERT INTO {_qid(AUDIT)}(source_id,field_name,before_value,after_value,action,reason,confidence,version) VALUES(:i,:f,:b,:a,:x,:r,:c,:v)"),{"i":str(sid),"f":field,"b":before,"a":after,"x":action,"r":reason,"c":conf,"v":VERSION})

def _run(core):
    e=_engine(core)
    if e is None: return
    with LOCK:
        if STATE["status"]=="RUNNING": return
        STATE.update({"status":"RUNNING","started_at":_utcnow(),"completed_at":None,"rows_scanned":0,"invalid_locations_quarantined":0,"direct_location_repairs":0,"duplicate_groups":0,"training_ready":0,"needs_review":0,"excluded_non_property":0,"error":None,"details":{}})
    run_id=None
    try:
        _setup(e); m=_meta(e)
        if not all([m["pk"],m["desc"],m["loc"]]): raise RuntimeError("pi_magazine_master required columns missing")
        sel=[m["pk"],m["desc"],m["loc"]]+([m["status"]] if m["status"] else [])+([m["phone"]] if m["phone"] and m["phone"] not in [m["pk"],m["desc"],m["loc"],m["status"]] else [])
        with e.connect() as c: rows=[dict(r) for r in c.execute(text("SELECT "+",".join(_qid(x) for x in sel)+" FROM pi_magazine_master")).mappings().all()]
        with e.begin() as c: run_id=c.execute(text(f"INSERT INTO {_qid(RUNS)}(version,status) VALUES(:v,'RUNNING') RETURNING id"),{"v":VERSION}).scalar()
        groups=defaultdict(list)
        for r in rows:
            key=(_norm_key(r.get(m["desc"])),_norm(r.get(m["phone"])) if m["phone"] else "")
            if key[0]: groups[key].append(r)
        dups={k:v for k,v in groups.items() if len(v)>1}; dupmap={}
        for i,vals in enumerate(dups.values(),1):
            gid=f"MAG-DUP-{i:05d}"
            for r in vals: dupmap[str(r[m["pk"]])]=gid
        invalid=repairs=ready=review=excluded=0
        with e.begin() as c:
            for r in rows:
                sid=str(r[m["pk"]]); desc=_norm(r.get(m["desc"])); old=_norm(r.get(m["loc"])); st=_norm(r.get(m["status"])).upper() if m["status"] else ""; dg=dupmap.get(sid)
                if st in NON_PROPERTY or not _property_like(desc):
                    dq="EXCLUDED_NON_PROPERTY" if st in NON_PROPERTY else "NEEDS_REVIEW"; lq="NOT_APPLICABLE" if st in NON_PROPERTY else ("MISSING" if _bad(old) else "UNVERIFIED"); score=20 if st in NON_PROPERTY else 35
                    excluded += 1 if st in NON_PROPERTY else 0; review += 0 if st in NON_PROPERTY else 1
                    c.execute(text(f"UPDATE pi_magazine_master SET data_quality_status=:dq,data_quality_score=:sc,location_quality_status=:lq,duplicate_group=:dg,settlement_version=:v WHERE CAST({_qid(m['pk'])} AS TEXT)=:id"),{"dq":dq,"sc":score,"lq":lq,"dg":dg,"v":VERSION,"id":sid}); continue
                direct=_extract_location(desc); inv=_invalid_location(old); new=old; lq="UNVERIFIED"; action=None; reason=None; conf=0
                if direct:
                    new=direct; lq="VERIFIED_FROM_DESCRIPTION"
                    if old.casefold()!=new.casefold(): action="REPAIR"; reason="Explicit geographic locality found in property description"; conf=100
                elif inv is True:
                    new="MISSING"; lq="REJECTED_NON_GEOGRAPHIC"; action="QUARANTINE"; reason="Location contains organization/contact/noise, not geography"; conf=100
                elif not _bad(old):
                    ck=_canon_known(old)
                    if ck:
                        new=ck; lq="VALIDATED_REFERENCE"
                        if old.casefold()!=new.casefold(): action="STANDARDIZE"; reason="Mapped to controlled geographic reference"; conf=98
                    else: lq="UNVERIFIED_GEOGRAPHIC_CANDIDATE"
                else: lq="MISSING"
                if action:
                    c.execute(text(f"UPDATE pi_magazine_master SET {_qid(m['loc'])}=:v WHERE CAST({_qid(m['pk'])} AS TEXT)=:id"),{"v":new,"id":sid}); _audit(c,sid,m["loc"],old,new,action,reason,conf)
                    invalid += 1 if action=="QUARANTINE" else 0; repairs += 0 if action=="QUARANTINE" else 1
                if lq in {"VERIFIED_FROM_DESCRIPTION","VALIDATED_REFERENCE"} and dg is None: dq="TRAINING_READY"; score=95 if lq=="VERIFIED_FROM_DESCRIPTION" else 90; ready+=1
                elif dg is not None: dq="DUPLICATE_REVIEW"; score=55; review+=1
                else: dq="NEEDS_REVIEW"; score=45 if lq=="UNVERIFIED_GEOGRAPHIC_CANDIDATE" else 30; review+=1
                c.execute(text(f"UPDATE pi_magazine_master SET data_quality_status=:dq,data_quality_score=:sc,location_quality_status=:lq,duplicate_group=:dg,settlement_version=:v WHERE CAST({_qid(m['pk'])} AS TEXT)=:id"),{"dq":dq,"sc":score,"lq":lq,"dg":dg,"v":VERSION,"id":sid})
        with e.begin() as c:
            c.execute(text("DROP VIEW IF EXISTS pi_magazine_training_ready_v11922")); c.execute(text("CREATE VIEW pi_magazine_training_ready_v11922 AS SELECT * FROM pi_magazine_master WHERE data_quality_status='TRAINING_READY' AND COALESCE(location_quality_status,'') IN ('VERIFIED_FROM_DESCRIPTION','VALIDATED_REFERENCE') AND duplicate_group IS NULL"))
        STATE.update({"status":"PASS","completed_at":_utcnow(),"rows_scanned":len(rows),"invalid_locations_quarantined":invalid,"direct_location_repairs":repairs,"duplicate_groups":len(dups),"training_ready":ready,"needs_review":review,"excluded_non_property":excluded,"details":{"golden_rule":"Only geographic entities may live in Location","training_source":"pi_magazine_training_ready_v11922","duplicate_policy":"flag, never auto-delete","unverified_policy":"review, never guess","example":"Royal Constructions is rejected as non-geographic; description LAJPAT NAGAR-2 becomes Lajpat Nagar 2"}})
        with e.begin() as c: c.execute(text(f"UPDATE {_qid(RUNS)} SET status='PASS',completed_at=NOW(),summary=CAST(:s AS JSONB) WHERE id=:id"),{"id":run_id,"s":json.dumps(STATE,ensure_ascii=False)})
    except Exception as exc:
        STATE["status"]="ERROR"; STATE["completed_at"]=_utcnow(); STATE["error"]=f"{type(exc).__name__}: {exc}"; STATE["details"]={"trace":traceback.format_exc()[-6000:]}

def _start(core): threading.Thread(target=_run,args=(core,),daemon=True,name="data-settlement-11922").start()

def register(core):
    app=_app(core); e=_engine(core)
    if app is None or e is None: raise RuntimeError("11.9.22 requires app + engine")
    _setup(e)
    @app.get("/alliance/admin/data-settlement",response_class=HTMLResponse)
    def page(req:Request):
        _login(core,req); s=dict(STATE)
        return HTMLResponse(f"""<!doctype html><html><body style='font-family:Arial;background:#f4f1ea'><main style='max-width:1050px;margin:30px auto;background:white;padding:24px;border-radius:14px'><h2>Alliance Data Settlement Engine · 11.9.22</h2><p><b>Data first. AI second.</b></p><p>Status: <b>{html.escape(str(s.get('status')))}</b> · Scanned {s.get('rows_scanned',0)} · Repairs {s.get('direct_location_repairs',0)} · Bad locations quarantined {s.get('invalid_locations_quarantined',0)} · Duplicate groups {s.get('duplicate_groups',0)} · Training ready {s.get('training_ready',0)} · Needs review {s.get('needs_review',0)}</p><button onclick='run()'>Run Settlement Again</button><pre id='o' style='white-space:pre-wrap;background:#f7f7f7;padding:14px'>{html.escape(json.dumps(s,indent=2,ensure_ascii=False))}</pre><script>async function run(){{let r=await fetch('/api/alliance/admin/data-settlement/run',{{method:'POST'}});o.textContent=JSON.stringify(await r.json(),null,2);setTimeout(()=>location.reload(),3000);}}</script></main></body></html>""",headers={"Cache-Control":"no-store"})
    @app.get("/api/alliance/admin/data-settlement/status")
    def status(req:Request): _login(core,req); return JSONResponse(dict(STATE))
    @app.post("/api/alliance/admin/data-settlement/run")
    def run(req:Request):
        _login(core,req)
        if STATE["status"]=="RUNNING": return {"status":"ALREADY_RUNNING","version":VERSION}
        _start(core); return {"status":"STARTED","version":VERSION}
    _start(core)
    return {"status":"REGISTERED","version":VERSION,"training_view":"pi_magazine_training_ready_v11922","admin_url":"/alliance/admin/data-settlement"}
