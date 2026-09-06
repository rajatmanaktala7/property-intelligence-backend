
from __future__ import annotations

import html
import json
import re
import threading
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

VERSION = "12.0.0-GOLDEN-DATA-FOUNDATION"

SNAPSHOT = "pi_magazine_master_raw_snapshot_v12000"
STAGE = "pi_magazine_golden_stage_v12000"
AUDIT = "pi_magazine_governance_audit_v12000"
RUNS = "pi_magazine_governance_runs_v12000"

BAD = {"", "MISSING", "UNKNOWN", "N/A", "NA", "NONE", "NULL", "UNSPECIFIED"}
HIDE = {"EXCLUDED_NON_PROPERTY", "DUPLICATE_SUPPRESSED", "QUARANTINED"}
AI_OK = {"GOLD"}
OPS_OK = {"GOLD", "SILVER"}

ORG_RE = re.compile(
    r"(?i)\b(?:CONSTRUCTION|CONSTRUCTIONS|BUILDER|BUILDERS|DEVELOPER|DEVELOPERS|"
    r"REALTOR|REALTORS|REALTY|ESTATE|ESTATES|PROPERTIES|PROPERTY\s+DEALER|"
    r"INFRA|INFRASTRUCTURE|ASSOCIATES|CONSULTANTS|CONSULTANCY|PVT|LTD|LLP|"
    r"ENTERPRISES|CORPORATION|COMPANY|CO\.?|GROUP|INTERIORS|ARCHITECTS)\b"
)
PHONE_RE = re.compile(r"(?<!\d)(?:[6-9]\d{9}|0?11[-\s]?\d{7,8})(?!\d)")
AREA_RE = re.compile(r"(?i)\b\d{2,7}(?:\.\d+)?\s*(?:SQ\.?\s*FT|SQFT|FT|SQ\.?\s*YD|SQYD|YD|Y|SQ\.?\s*M|SQM|ACRE)\b")
FLOOR_RE = re.compile(r"(?i)\b(?:BMT|BASEMENT|LGF|UGF|GF|GROUND\s*FLOOR|FF|FIRST\s*FLOOR|SF|SECOND\s*FLOOR|TF|THIRD\s*FLOOR|MEZZ|\d+(?:ST|ND|RD|TH)?\s*FLOOR)\b")
BHK_RE = re.compile(r"(?i)\b\d+\s*(?:BHK|BR)\b")
PTYPE_RE = re.compile(r"(?i)\b(?:APARTMENT|APT|FLAT|KOTHI|VILLA|PLOT|OFFICE|SHOP|SHOWROOM|WAREHOUSE|GODOWN|FACTORY|BUILDING|FARMHOUSE|FARM\s*HOUSE)\b")

STATIC = {
    "ALAKNANDA":"Alaknanda","ANAND LOK":"Anand Lok","ANAND NIKETAN":"Anand Niketan",
    "BHIKAJI CAMA PLACE":"Bhikaji Cama Place","CHANAKYAPURI":"Chanakyapuri",
    "CHHATARPUR":"Chhatarpur","CHHATARPUR ENCLAVE":"Chhatarpur Enclave",
    "CHIRAG DELHI":"Chirag Delhi","CHITRANJAN PARK":"Chitranjan Park","CR PARK":"Chitranjan Park",
    "C R PARK":"Chitranjan Park","CONNAUGHT PLACE":"Connaught Place","CP":"Connaught Place",
    "DEFENCE COLONY":"Defence Colony","DERA MANDI":"Dera Mandi","DWARKA":"Dwarka",
    "EAST OF KAILASH":"East of Kailash","FRIENDS COLONY":"Friends Colony",
    "GAUTAM NAGAR":"Gautam Nagar","GOLF LINKS":"Golf Links",
    "GREATER KAILASH 1":"Greater Kailash 1","GREATER KAILASH I":"Greater Kailash 1",
    "GREATER KAILASH-1":"Greater Kailash 1","GK 1":"Greater Kailash 1","GK-I":"Greater Kailash 1",
    "GREATER KAILASH 2":"Greater Kailash 2","GREATER KAILASH II":"Greater Kailash 2",
    "GREATER KAILASH-2":"Greater Kailash 2","GK 2":"Greater Kailash 2","GK-II":"Greater Kailash 2",
    "GREEN PARK":"Green Park","GREEN PARK EXTN":"Green Park Extension","GREEN PARK EXTENSION":"Green Park Extension",
    "GURGAON":"Gurugram","GURUGRAM":"Gurugram","HAUZ KHAS":"Hauz Khas","JASOLA":"Jasola",
    "JOR BAGH":"Jor Bagh","KAILASH COLONY":"Kailash Colony","LAJPAT NAGAR":"Lajpat Nagar",
    "LAJPAT NAGAR 1":"Lajpat Nagar 1","LAJPAT NAGAR-1":"Lajpat Nagar 1","LAJPAT NAGAR I":"Lajpat Nagar 1",
    "LAJPAT NAGAR 2":"Lajpat Nagar 2","LAJPAT NAGAR-2":"Lajpat Nagar 2","LAJPAT NAGAR II":"Lajpat Nagar 2",
    "LAJPAT NAGAR 3":"Lajpat Nagar 3","LAJPAT NAGAR-3":"Lajpat Nagar 3","LAJPAT NAGAR III":"Lajpat Nagar 3",
    "LAJPAT NAGAR 4":"Lajpat Nagar 4","LAJPAT NAGAR-4":"Lajpat Nagar 4","LAJPAT NAGAR IV":"Lajpat Nagar 4",
    "MAHARANI BAGH":"Maharani Bagh","MALVIYA NAGAR":"Malviya Nagar",
    "MOHAN CO-OPERATIVE":"Mohan Cooperative","MOHAN COOPERATIVE":"Mohan Cooperative",
    "NEW FRIENDS COLONY":"New Friends Colony","NFC":"New Friends Colony",
    "NITI BAGH":"Niti Bagh","NIZAMUDDIN":"Nizamuddin","NIZAMUDDIN EAST":"Nizamuddin East",
    "NIZAMUDDIN WEST":"Nizamuddin West","PANCHSHEEL ENCLAVE":"Panchsheel Enclave",
    "PANCHSHEEL PARK":"Panchsheel Park","PITAMPURA":"Pitampura","ROHINI":"Rohini",
    "SAFDARJUNG ENCLAVE":"Safdarjung Enclave","SAFDARJUNG DEVELOPMENT AREA":"Safdarjung Development Area",
    "SDA":"Safdarjung Development Area","SAINIK FARM":"Sainik Farm","SAKET":"Saket",
    "SARVODAYA ENCLAVE":"Sarvodaya Enclave","SHANTI NIKETAN":"Shanti Niketan",
    "SOUTH EXTENSION":"South Extension","SOUTH EXTENSION 1":"South Extension 1",
    "SOUTH EXTENSION I":"South Extension 1","SOUTH EXTENSION 2":"South Extension 2",
    "SOUTH EXTENSION II":"South Extension 2","SUNDER NAGAR":"Sunder Nagar",
    "TUGHLAKABAD":"Tughlakabad","TUGHLAKABAD EXTN":"Tughlakabad Extension",
    "VASANT KUNJ":"Vasant Kunj","VASANT VIHAR":"Vasant Vihar",
}
for p in (1,2,3):
    STATIC[f"OKHLA PHASE {p}"] = f"Okhla Phase {p}"
    STATIC[f"OKHLA PHASE-{p}"] = f"Okhla Phase {p}"
    STATIC[f"OKHLA-{p}"] = f"Okhla Phase {p}"
    STATIC[f"OKHLA {p}"] = f"Okhla Phase {p}"
STATIC["OKHLA PHASE I"]="Okhla Phase 1"
STATIC["OKHLA PHASE II"]="Okhla Phase 2"
STATIC["OKHLA PHASE III"]="Okhla Phase 3"

LOCK = threading.Lock()
STATE = {
    "status":"IDLE","started_at":None,"completed_at":None,
    "rows_scanned":0,"gold":0,"silver":0,"review":0,"quarantined":0,
    "non_property":0,"duplicate_suppressed":0,"location_repairs":0,
    "invalid_location_removed":0,"conflicts":0,"error":None,"details":{}
}

def _utcnow(): return datetime.now(timezone.utc).isoformat()
def _app(core): return getattr(core,"app",None) or core
def _engine(core): return getattr(core,"engine",None)
def _login(core,req):
    fn=getattr(core,"need_login",None)
    return fn(req) if fn else "team"

def _qid(v):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*",str(v or "")):
        raise ValueError("unsafe identifier")
    return '"' + str(v) + '"'

def _norm(v): return re.sub(r"\s+"," ",str(v or "")).strip()
def _key(v):
    s=_norm(v).upper()
    s=PHONE_RE.sub(" ",s)
    s=re.sub(r"[^A-Z0-9]+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def _bad(v): return _norm(v).upper() in BAD

def _property_like(v):
    u=_norm(v).upper()
    if not u: return False
    address=bool(re.match(r"^\s*(?:[A-Z]{0,4}[-/]?\d+[A-Z]?|\d+[A-Z]?|[A-Z]-BLOCK|[A-Z]-BLK)\b",u))
    detail=bool(AREA_RE.search(u) or FLOOR_RE.search(u) or BHK_RE.search(u) or PTYPE_RE.search(u))
    return address and detail

def _obvious_non_property(v):
    s=_norm(v)
    if not s: return True
    stripped=PHONE_RE.sub(" ",s)
    stripped=re.sub(r"[\s,;:/().+-]+","",stripped)
    if not stripped: return True
    if not (AREA_RE.search(s) or FLOOR_RE.search(s) or BHK_RE.search(s) or PTYPE_RE.search(s)):
        if PHONE_RE.search(s) and len(_norm(PHONE_RE.sub(" ",s))) < 45:
            return True
    return False

def _canonicalize(raw):
    u=_norm(raw).upper().strip(" ,;:|")
    u=re.sub(r"\s+"," ",u)
    if u in STATIC: return STATIC[u]
    m=re.fullmatch(r"(NOIDA|GURUGRAM|GURGAON|DWARKA|ROHINI)\s+SECTOR\s+(\d+[A-Z]?)",u)
    if m:
        city="Gurugram" if m.group(1) in {"GURUGRAM","GURGAON"} else m.group(1).title()
        return f"{city} Sector {m.group(2)}"
    m=re.fullmatch(r"SECTOR\s+(\d+[A-Z]?)\s+(NOIDA|GURUGRAM|GURGAON|DWARKA|ROHINI)",u)
    if m:
        city="Gurugram" if m.group(2) in {"GURUGRAM","GURGAON"} else m.group(2).title()
        return f"{city} Sector {m.group(1)}"
    return None

def _valid_geo(raw):
    s=_norm(raw)
    if not s or s.upper() in BAD: return False
    u=s.upper()
    if ORG_RE.search(u) or PHONE_RE.search(u) or AREA_RE.search(u): return False
    if re.search(r"(?i)\b(?:OWNER|BROKER|EMPLOYEE|CARE\s*TAKER|CONTACT|MOB|PHONE)\b",u): return False
    if len(s)>70: return False
    if _canonicalize(s): return True
    # Generic geography form allowed only as candidate, not automatically gold.
    if re.fullmatch(r"[A-Za-z][A-Za-z .'-]{2,45}(?:\s+(?:NAGAR|VIHAR|KUNJ|BAGH|PARK|COLONY|ENCLAVE|EXTENSION|EXTN|PLACE|MARKET|MARG|ROAD|PHASE\s+\d+))",s,re.I):
        return True
    return False

def _entity_type(raw):
    s=_norm(raw)
    if not s: return "EMPTY"
    if ORG_RE.search(s): return "ORGANIZATION"
    if PHONE_RE.search(s): return "CONTACT"
    if AREA_RE.search(s): return "PROPERTY_DETAIL"
    if _valid_geo(s): return "LOCATION"
    return "UNKNOWN"

def _setup(e):
    with e.begin() as c:
        # Immutable one-time raw snapshot.
        c.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {_qid(SNAPSHOT)} AS
        SELECT *, NOW() AS snapshot_created_at FROM pi_magazine_master
        """))
        c.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {_qid(STAGE)}(
          source_id TEXT PRIMARY KEY,
          raw_location TEXT,
          canonical_location TEXT,
          location_confidence INTEGER NOT NULL DEFAULT 0,
          location_rule TEXT,
          location_entity_type TEXT,
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
        c.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {_qid(AUDIT)}(
          id BIGSERIAL PRIMARY KEY,
          source_id TEXT NOT NULL,
          field_name TEXT NOT NULL,
          before_value TEXT,
          after_value TEXT,
          action TEXT NOT NULL,
          confidence INTEGER NOT NULL,
          rule TEXT NOT NULL,
          evidence JSONB NOT NULL DEFAULT '{{}}'::jsonb,
          version TEXT NOT NULL,
          created_at TIMESTAMPTZ DEFAULT NOW()
        )"""))
        c.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {_qid(RUNS)}(
          id BIGSERIAL PRIMARY KEY,
          version TEXT NOT NULL,
          status TEXT NOT NULL,
          started_at TIMESTAMPTZ DEFAULT NOW(),
          completed_at TIMESTAMPTZ,
          summary JSONB NOT NULL DEFAULT '{{}}'::jsonb
        )"""))
        for col,typ in [
            ("data_quality_status","TEXT"),("data_quality_score","INTEGER"),
            ("location_quality_status","TEXT"),("duplicate_group","TEXT"),
            ("settlement_version","TEXT")
        ]:
            c.execute(text(f"ALTER TABLE pi_magazine_master ADD COLUMN IF NOT EXISTS {col} {typ}"))

        # Future guard: organization/contact/property-detail values cannot survive in locality.
        c.execute(text("""
        CREATE OR REPLACE FUNCTION alliance_magazine_location_guard_v12000()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.locality IS NOT NULL AND BTRIM(NEW.locality) <> '' AND
             NEW.locality ~* '(construction|builder|developer|realtor|realty|estate|properties|property dealer|infra|associates|consultants|pvt|ltd|llp|company|group|interiors|architects)'
          THEN
             NEW.locality := 'MISSING';
             NEW.location_quality_status := 'REJECTED_NON_GEOGRAPHIC';
             NEW.data_quality_status := 'PENDING_RECONCILIATION';
          END IF;
          RETURN NEW;
        END $$;
        """))
        c.execute(text("""
        DROP TRIGGER IF EXISTS trg_magazine_location_guard_v12000 ON pi_magazine_master
        """))
        c.execute(text("""
        CREATE TRIGGER trg_magazine_location_guard_v12000
        BEFORE INSERT OR UPDATE OF locality ON pi_magazine_master
        FOR EACH ROW EXECUTE FUNCTION alliance_magazine_location_guard_v12000()
        """))

def _table_exists(e,t):
    with e.connect() as c:
        return bool(c.execute(text("SELECT to_regclass(:t) IS NOT NULL"),{"t":t}).scalar())

def _master_rows(e):
    with e.connect() as c:
        return [dict(r) for r in c.execute(text("SELECT to_jsonb(x) d FROM pi_magazine_master x")).scalars().all()]

def _pk(d):
    for k in ("source_id","id","record_id","property_id"):
        if d.get(k) is not None: return str(d.get(k))
    return ""

def _desc(d):
    for k in ("original_raw_text","original_description","description"):
        if d.get(k): return _norm(d.get(k))
    return ""

def _loc(d):
    for k in ("locality","location","locality_clean"):
        if k in d: return _norm(d.get(k))
    return ""

def _phone_key(d):
    for k in ("valid_mobiles","contact_number","contact_numbers","valid_landlines","partial_contacts"):
        if d.get(k) not in (None,"",[],{}):
            return _key(json.dumps(d.get(k),ensure_ascii=False) if isinstance(d.get(k),(list,dict)) else d.get(k))
    return ""

def _learn_reference(e,masters):
    refs=dict(STATIC)
    counts=Counter()
    for d in masters:
        v=_loc(d)
        if _valid_geo(v): counts[v]+=1
    for v,n in counts.items():
        if n>=3:
            refs[v.upper()]=_canonicalize(v) or v

    if _table_exists(e,"pi_magazine_layout_evidence_v11921"):
        with e.connect() as c:
            vals=c.execute(text("""
            SELECT locality,COUNT(*) n
            FROM pi_magazine_layout_evidence_v11921
            WHERE locality IS NOT NULL AND BTRIM(locality)<>''
            GROUP BY locality
            """)).all()
        for v,n in vals:
            if int(n or 0)>=2 and _valid_geo(v):
                refs[_norm(v).upper()]=_canonicalize(v) or _norm(v)
    return refs

def _direct_from_description(desc,refs):
    u=_norm(desc).upper()
    hits=[]
    # Specific aliases first.
    for raw,canon in sorted(refs.items(),key=lambda kv:len(kv[0]),reverse=True):
        if len(raw)<3: continue
        if re.search(r"(?<![A-Z0-9])"+re.escape(raw)+r"(?![A-Z0-9])",u):
            if _valid_geo(canon):
                hits.append(canon)
    # City-sector formats.
    for m in re.finditer(r"\b(NOIDA|GURUGRAM|GURGAON|DWARKA|ROHINI)\s+SECTOR\s+(\d+[A-Z]?)\b",u):
        city="Gurugram" if m.group(1) in {"GURUGRAM","GURGAON"} else m.group(1).title()
        hits.append(f"{city} Sector {m.group(2)}")
    uniq=[]
    for x in hits:
        if x not in uniq: uniq.append(x)
    # Prefer most specific numbered locality.
    uniq.sort(key=lambda x:(bool(re.search(r"\d",x)),len(x)),reverse=True)
    return uniq[0] if uniq else None

def _layout_index(e):
    idx=defaultdict(list)
    if not _table_exists(e,"pi_magazine_layout_evidence_v11921"): return idx
    with e.connect() as c:
        rows=[dict(r) for r in c.execute(text("""
        SELECT original_text,locality,page_number,upload_id::text
        FROM pi_magazine_layout_evidence_v11921
        WHERE locality IS NOT NULL AND BTRIM(locality)<>''
        """)).mappings().all()]
    for r in rows:
        if _valid_geo(r["locality"]):
            idx[_key(r["original_text"])].append(r)
    return idx

def _complete_index(e):
    idx=defaultdict(list)
    if not _table_exists(e,"pi_magazine_complete_v860"): return idx
    with e.connect() as c:
        rows=[dict(r) for r in c.execute(text("""
        SELECT source_record_id,original_description,description,original_section,location,page_number,upload_id::text
        FROM pi_magazine_complete_v860
        """)).mappings().all()]
    for r in rows:
        desc=r.get("original_description") or r.get("description") or ""
        candidates=[]
        for v in (r.get("original_section"),r.get("location")):
            if _valid_geo(v): candidates.append(_canonicalize(v) or _norm(v))
        if candidates:
            r["_localities"]=list(dict.fromkeys(candidates))
            idx[_key(desc)].append(r)
    return idx

def _unique_loc(rows,field="_localities"):
    locs=[]
    winner=None
    for r in rows:
        vals=r.get(field) if isinstance(r.get(field),list) else [r.get(field)]
        for v in vals:
            if _valid_geo(v):
                c=_canonicalize(v) or _norm(v)
                if c not in locs: locs.append(c)
                winner=r
    return (locs[0],winner) if len(locs)==1 else (None,None)

def _resolve(d,refs,lidx,cidx):
    desc=_desc(d)
    old=_loc(d)
    candidates=[]

    direct=_direct_from_description(desc,refs)
    if direct:
        candidates.append((100,direct,"EXPLICIT_LOCATION_IN_PROPERTY_DESCRIPTION",{"description":desc}))

    lk=_key(desc)
    if lk and lk in lidx:
        rows=lidx[lk]
        locs=[]
        for r in rows:
            v=r.get("locality")
            if _valid_geo(v):
                cv=_canonicalize(v) or _norm(v)
                if cv not in locs: locs.append(cv)
        if len(locs)==1:
            candidates.append((96,locs[0],"EXACT_SOURCE_LAYOUT_MATCH",{"matches":len(rows),"pages":sorted({r.get("page_number") for r in rows})}))

    if lk and lk in cidx:
        loc,row=_unique_loc(cidx[lk])
        if loc:
            candidates.append((93,loc,"EXACT_COMPLETE_SOURCE_MATCH",{"source_record_id":row.get("source_record_id"),"page":row.get("page_number")}))

    if _valid_geo(old):
        candidates.append((70,_canonicalize(old) or old,"EXISTING_VALID_GEOGRAPHIC_VALUE",{"existing":old}))

    candidates.sort(key=lambda x:x[0],reverse=True)
    if not candidates:
        return None,0,"NO_PROVEN_LOCATION",{},False

    top=candidates[0]
    conflict=False
    distinct=[]
    for c in candidates:
        if c[1].casefold() not in [x.casefold() for x in distinct]:
            distinct.append(c[1])
    if len(distinct)>1:
        # Strong direct source can override weak old value; close source-vs-source conflicts go to review.
        second=candidates[1]
        if top[0]-second[0] < 10:
            conflict=True
    return top[1],top[0],top[2],{"candidates":[{"confidence":x[0],"location":x[1],"rule":x[2],"evidence":x[3]} for x in candidates]},conflict

def _quality(d,loc,conf,conflict,dup_rank):
    st=_norm(d.get("record_status") or d.get("verification_status")).upper()
    desc=_desc(d)
    if st=="EXCLUDE_NON_PROPERTY" or _obvious_non_property(desc):
        return "EXCLUDED_NON_PROPERTY",0,"NON_PROPERTY"
    if not _property_like(desc):
        return "QUARANTINED",20,"WEAK_PROPERTY_EVIDENCE"
    if dup_rank and dup_rank>1:
        return "DUPLICATE_SUPPRESSED",40,"DUPLICATE"
    if conflict:
        return "REVIEW",55,"SOURCE_CONFLICT"
    if loc and conf>=93:
        return "GOLD",95 if conf>=96 else 90,"CERTIFIED"
    if loc and conf>=70:
        return "SILVER",75,"USABLE_NEEDS_VERIFICATION"
    return "REVIEW",45,"LOCATION_UNRESOLVED"

def _reconcile(core):
    e=_engine(core)
    if e is None: return
    with LOCK:
        if STATE["status"]=="RUNNING": return
        STATE.update({"status":"RUNNING","started_at":_utcnow(),"completed_at":None,"error":None,
                      "rows_scanned":0,"gold":0,"silver":0,"review":0,"quarantined":0,
                      "non_property":0,"duplicate_suppressed":0,"location_repairs":0,
                      "invalid_location_removed":0,"conflicts":0,"details":{}})
    run_id=None
    try:
        _setup(e)
        masters=_master_rows(e)
        refs=_learn_reference(e,masters)
        lidx=_layout_index(e)
        cidx=_complete_index(e)

        # Duplicate grouping without destructive deletes.
        grp=defaultdict(list)
        for d in masters:
            k=(_key(_desc(d)),_phone_key(d))
            if k[0]: grp[k].append(d)
        dup_meta={}
        gi=0
        for vals in grp.values():
            if len(vals)<=1: continue
            gi+=1; gid=f"MAG-GOLD-DUP-{gi:05d}"
            # Best survivor: property-like, valid geo, more populated fields.
            vals=sorted(vals,key=lambda x:(
                1 if _property_like(_desc(x)) else 0,
                1 if _valid_geo(_loc(x)) else 0,
                len([v for v in x.values() if v not in (None,"",[],{})])
            ),reverse=True)
            for rank,d in enumerate(vals,1):
                dup_meta[_pk(d)]=(gid,rank)

        with e.begin() as c:
            run_id=c.execute(text(f"INSERT INTO {_qid(RUNS)}(version,status) VALUES(:v,'RUNNING') RETURNING id"),{"v":VERSION}).scalar()

        counters=Counter()
        with e.begin() as c:
            c.execute(text(f"DELETE FROM {_qid(STAGE)} WHERE version=:v"),{"v":VERSION})
            for d in masters:
                sid=_pk(d)
                if not sid: continue
                old=_loc(d)
                loc,conf,rule,evidence,conflict=_resolve(d,refs,lidx,cidx)
                dg,rank=dup_meta.get(sid,(None,None))
                q,score,pstatus=_quality(d,loc,conf,conflict,rank)
                entity=_entity_type(old)

                canonical=loc or ("MISSING" if entity in {"ORGANIZATION","CONTACT","PROPERTY_DETAIL"} else old or "MISSING")
                if canonical and not _valid_geo(canonical) and canonical!="MISSING":
                    canonical="MISSING"

                c.execute(text(f"""
                INSERT INTO {_qid(STAGE)}
                (source_id,raw_location,canonical_location,location_confidence,location_rule,location_entity_type,
                 conflict,duplicate_group,duplicate_rank,quality_status,quality_score,property_status,evidence,version,updated_at)
                VALUES(:sid,:raw,:loc,:conf,:rule,:etype,:conflict,:dg,:rank,:q,:score,:ps,CAST(:ev AS JSONB),:ver,NOW())
                ON CONFLICT(source_id) DO UPDATE SET
                 raw_location=EXCLUDED.raw_location,canonical_location=EXCLUDED.canonical_location,
                 location_confidence=EXCLUDED.location_confidence,location_rule=EXCLUDED.location_rule,
                 location_entity_type=EXCLUDED.location_entity_type,conflict=EXCLUDED.conflict,
                 duplicate_group=EXCLUDED.duplicate_group,duplicate_rank=EXCLUDED.duplicate_rank,
                 quality_status=EXCLUDED.quality_status,quality_score=EXCLUDED.quality_score,
                 property_status=EXCLUDED.property_status,evidence=EXCLUDED.evidence,version=EXCLUDED.version,updated_at=NOW()
                """),{"sid":sid,"raw":old,"loc":canonical,"conf":conf,"rule":rule,"etype":entity,
                     "conflict":conflict,"dg":dg,"rank":rank,"q":q,"score":score,"ps":pstatus,
                     "ev":json.dumps(evidence,ensure_ascii=False),"ver":VERSION})

                # Settle visible master only from certified/review-safe decisions.
                if q in {"GOLD","SILVER","REVIEW"}:
                    if old.casefold()!=canonical.casefold():
                        c.execute(text("""
                        UPDATE pi_magazine_master SET locality=:loc WHERE CAST(source_id AS TEXT)=:sid
                        """),{"loc":canonical,"sid":sid})
                        c.execute(text(f"""
                        INSERT INTO {_qid(AUDIT)}
                        (source_id,field_name,before_value,after_value,action,confidence,rule,evidence,version)
                        VALUES(:sid,'locality',:b,:a,'RECONCILE',:conf,:rule,CAST(:ev AS JSONB),:ver)
                        """),{"sid":sid,"b":old,"a":canonical,"conf":conf,"rule":rule,
                             "ev":json.dumps(evidence,ensure_ascii=False),"ver":VERSION})
                        counters["location_repairs"]+=1
                elif entity in {"ORGANIZATION","CONTACT","PROPERTY_DETAIL"} and old!="MISSING":
                    c.execute(text("""
                    UPDATE pi_magazine_master SET locality='MISSING' WHERE CAST(source_id AS TEXT)=:sid
                    """),{"sid":sid})
                    counters["invalid_location_removed"]+=1

                c.execute(text("""
                UPDATE pi_magazine_master SET
                  data_quality_status=:q,data_quality_score=:score,
                  location_quality_status=:lq,duplicate_group=:dg,settlement_version=:ver
                WHERE CAST(source_id AS TEXT)=:sid
                """),{"q":q,"score":score,"lq":rule,"dg":dg,"ver":VERSION,"sid":sid})

                counters[q]+=1
                if conflict: counters["conflicts"]+=1

        with e.begin() as c:
            c.execute(text("DROP VIEW IF EXISTS pi_magazine_ai_training_v12000"))
            c.execute(text("""
            CREATE VIEW pi_magazine_ai_training_v12000 AS
            SELECT m.* FROM pi_magazine_master m
            JOIN pi_magazine_golden_stage_v12000 g ON g.source_id=CAST(m.source_id AS TEXT)
            WHERE g.version='12.0.0-GOLDEN-DATA-FOUNDATION'
              AND g.quality_status='GOLD'
              AND g.duplicate_rank IS DISTINCT FROM 2
              AND g.conflict=FALSE
            """))
            c.execute(text("DROP VIEW IF EXISTS pi_magazine_operational_v12000"))
            c.execute(text("""
            CREATE VIEW pi_magazine_operational_v12000 AS
            SELECT m.* FROM pi_magazine_master m
            JOIN pi_magazine_golden_stage_v12000 g ON g.source_id=CAST(m.source_id AS TEXT)
            WHERE g.version='12.0.0-GOLDEN-DATA-FOUNDATION'
              AND g.quality_status IN ('GOLD','SILVER')
              AND COALESCE(g.duplicate_rank,1)=1
            """))

        STATE.update({
            "status":"PASS","completed_at":_utcnow(),"rows_scanned":len(masters),
            "gold":counters["GOLD"],"silver":counters["SILVER"],"review":counters["REVIEW"],
            "quarantined":counters["QUARANTINED"],"non_property":counters["EXCLUDED_NON_PROPERTY"],
            "duplicate_suppressed":counters["DUPLICATE_SUPPRESSED"],
            "location_repairs":counters["location_repairs"],
            "invalid_location_removed":counters["invalid_location_removed"],
            "conflicts":counters["conflicts"],
            "details":{
                "raw_snapshot":SNAPSHOT,
                "operational_view":"pi_magazine_operational_v12000",
                "ai_training_view":"pi_magazine_ai_training_v12000",
                "rules":[
                    "Location may contain geography only",
                    "Explicit description locality outranks derived and old values",
                    "Source-layout evidence must agree before certification",
                    "Organizations/contacts/property details are rejected from Location",
                    "Duplicates are suppressed, never deleted automatically",
                    "Conflicts go to review instead of being guessed"
                ]
            }
        })
        with e.begin() as c:
            c.execute(text(f"UPDATE {_qid(RUNS)} SET status='PASS',completed_at=NOW(),summary=CAST(:s AS JSONB) WHERE id=:id"),
                      {"id":run_id,"s":json.dumps(STATE,ensure_ascii=False)})
    except Exception as exc:
        STATE["status"]="ERROR"; STATE["completed_at"]=_utcnow()
        STATE["error"]=f"{type(exc).__name__}: {exc}"
        STATE["details"]={"trace":traceback.format_exc()[-7000:]}
        if run_id:
            try:
                with e.begin() as c:
                    c.execute(text(f"UPDATE {_qid(RUNS)} SET status='ERROR',completed_at=NOW(),summary=CAST(:s AS JSONB) WHERE id=:id"),
                              {"id":run_id,"s":json.dumps(STATE,ensure_ascii=False)})
            except Exception:
                pass

def _patch_visible_source(core):
    # Default Magazine screen becomes operational clean data.
    try:
        import alliance_cre_os_v1171 as cre
    except Exception:
        return False
    if getattr(cre,"_ALLIANCE_GOLDEN_SOURCE_12000",False): return True

    original=cre.source_data
    def clean_source_data(engine,k,q,page,per_page):
        if k!="magazine":
            return original(engine,k,q,page,per_page)
        off=(page-1)*per_page
        with engine.connect() as c:
            total=int(c.execute(text("""
            SELECT COUNT(*) FROM pi_magazine_master x
            JOIN pi_magazine_golden_stage_v12000 g ON g.source_id=CAST(x.source_id AS TEXT)
            WHERE g.version=:v AND g.quality_status IN ('GOLD','SILVER')
              AND COALESCE(g.duplicate_rank,1)=1
              AND NOT EXISTS(
                SELECT 1 FROM ai_source_record_archives a
                WHERE a.source_type='magazine' AND a.source_record_id=CAST(x.source_id AS TEXT)
              )
            """),{"v":VERSION}).scalar() or 0)
            filtered=total
            if q:
                filtered=int(c.execute(text("""
                SELECT COUNT(*) FROM pi_magazine_master x
                JOIN pi_magazine_golden_stage_v12000 g ON g.source_id=CAST(x.source_id AS TEXT)
                WHERE g.version=:v AND g.quality_status IN ('GOLD','SILVER')
                  AND COALESCE(g.duplicate_rank,1)=1
                  AND to_jsonb(x)::text ILIKE :pat
                  AND NOT EXISTS(
                    SELECT 1 FROM ai_source_record_archives a
                    WHERE a.source_type='magazine' AND a.source_record_id=CAST(x.source_id AS TEXT)
                  )
                """),{"v":VERSION,"pat":"%"+q+"%"}).scalar() or 0)
            rs=c.execute(text("""
            SELECT to_jsonb(x) FROM pi_magazine_master x
            JOIN pi_magazine_golden_stage_v12000 g ON g.source_id=CAST(x.source_id AS TEXT)
            WHERE g.version=:v AND g.quality_status IN ('GOLD','SILVER')
              AND COALESCE(g.duplicate_rank,1)=1
              AND (:q='' OR to_jsonb(x)::text ILIKE :pat)
              AND NOT EXISTS(
                SELECT 1 FROM ai_source_record_archives a
                WHERE a.source_type='magazine' AND a.source_record_id=CAST(x.source_id AS TEXT)
              )
            ORDER BY x.source_id DESC NULLS LAST LIMIT :lim OFFSET :off
            """),{"v":VERSION,"q":q,"pat":"%"+q+"%","lim":per_page,"off":off}).scalars().all()
        return total,filtered,[r if isinstance(r,dict) else json.loads(r) for r in rs]

    cre.source_data=clean_source_data
    cre._ALLIANCE_GOLDEN_SOURCE_12000=True
    return True

def _start(core):
    threading.Thread(target=_reconcile,args=(core,),daemon=True,name="golden-data-12000").start()

def register(core):
    app=_app(core); e=_engine(core)
    if app is None or e is None: raise RuntimeError("12.0.0 requires app + engine")
    _setup(e)
    patched=_patch_visible_source(core)

    @app.get("/alliance/admin/data-governance",response_class=HTMLResponse)
    def page(req:Request):
        _login(core,req)
        s=dict(STATE)
        body=f"""<!doctype html><html><head><meta charset='utf-8'>
        <meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Alliance Golden Data Governance</title>
        <style>
        body{{font-family:Arial;background:#f5f2ec;color:#27231e;margin:0}}
        main{{max-width:1120px;margin:30px auto;background:#fff;padding:26px;border-radius:14px}}
        .g{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}}
        .c{{border:1px solid #ddd;border-radius:10px;padding:14px}}
        .ok{{font-size:24px;font-weight:bold}} button{{padding:10px 16px;border:0;border-radius:8px;cursor:pointer}}
        pre{{white-space:pre-wrap;background:#f7f7f7;padding:14px;border-radius:10px}}
        </style></head><body><main>
        <h2>Alliance Golden Data Foundation · 12.0.0</h2>
        <p><b>Raw → Reconcile → Certify → Operational → AI.</b> The Magazine screen shows only GOLD/SILVER records. Review/quarantine data remains preserved separately.</p>
        <div class='g'>
        <div class='c'><b>Status</b><div class='ok'>{html.escape(str(s.get("status")))}</div></div>
        <div class='c'><b>Scanned</b><div class='ok'>{s.get("rows_scanned",0)}</div></div>
        <div class='c'><b>GOLD</b><div class='ok'>{s.get("gold",0)}</div></div>
        <div class='c'><b>SILVER</b><div class='ok'>{s.get("silver",0)}</div></div>
        <div class='c'><b>Review</b><div class='ok'>{s.get("review",0)}</div></div>
        <div class='c'><b>Quarantined</b><div class='ok'>{s.get("quarantined",0)}</div></div>
        <div class='c'><b>Duplicates suppressed</b><div class='ok'>{s.get("duplicate_suppressed",0)}</div></div>
        <div class='c'><b>Location repairs</b><div class='ok'>{s.get("location_repairs",0)}</div></div>
        </div>
        <p><button onclick='run()'>Run Full Reconciliation</button>
        <a href='/alliance/source/magazine'>Open Clean Magazine Database</a></p>
        <pre id='o'>{html.escape(json.dumps(s,indent=2,ensure_ascii=False))}</pre>
        <script>
        async function run(){{let r=await fetch('/api/alliance/admin/data-governance/run',{{method:'POST'}});
        o.textContent=JSON.stringify(await r.json(),null,2);setTimeout(()=>location.reload(),3500);}}
        </script></main></body></html>"""
        return HTMLResponse(body,headers={"Cache-Control":"no-store"})

    @app.get("/api/alliance/admin/data-governance/status")
    def status(req:Request):
        _login(core,req); return JSONResponse(dict(STATE))

    @app.post("/api/alliance/admin/data-governance/run")
    def run(req:Request):
        _login(core,req)
        if STATE["status"]=="RUNNING": return {"status":"ALREADY_RUNNING","version":VERSION}
        _start(core); return {"status":"STARTED","version":VERSION}

    _start(core)
    return {
        "status":"REGISTERED","version":VERSION,"auto_reconcile":True,
        "clean_magazine_screen":patched,
        "admin_url":"/alliance/admin/data-governance",
        "ai_training_view":"pi_magazine_ai_training_v12000",
        "operational_view":"pi_magazine_operational_v12000"
    }
