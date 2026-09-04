from __future__ import annotations
import hashlib, html, json, re, threading, time
from collections import defaultdict, Counter
from datetime import datetime, timezone
from decimal import Decimal

from fastapi.responses import HTMLResponse
from sqlalchemy import text

VERSION="7.0.1-ALLIANCE-UNIFIED-NEWSPAPER-MAGAZINE-DATA-REPAIR-COLLECTIONS-FIX"
MODE="SOURCE_IMMUTABLE_DETERMINISTIC_CLEAN_SHADOW_STRONG_DEDUPE_CANONICAL_DERIVED_NO_AI_QUOTA_REQUIRED"

STATE={"status":"NOT_STARTED","phase":"WAITING","started_at":None,"finished_at":None,
       "current_table":None,"processed":0,"result":None,"last_error":None}
_LOCK=threading.Lock();_STARTED=False

DDL=[
"""CREATE TABLE IF NOT EXISTS pi_unified_clean_shadow_v700(
 id BIGSERIAL PRIMARY KEY,
 source_type TEXT NOT NULL,
 source_table TEXT NOT NULL,
 source_pk TEXT NOT NULL,
 raw_record JSONB NOT NULL,
 clean_record JSONB NOT NULL,
 entity_type TEXT,
 canonical_transaction TEXT,
 locality_clean TEXT,
 city_clean TEXT,
 area_value NUMERIC(18,4),
 area_unit TEXT,
 area_sqft NUMERIC(18,4),
 price_raw TEXT,
 price_kind TEXT,
 phones JSONB DEFAULT '[]'::jsonb,
 strong_fingerprint TEXT,
 duplicate_group TEXT,
 issues JSONB DEFAULT '[]'::jsonb,
 auto_status TEXT,
 confidence NUMERIC(6,2),
 source_row_hash TEXT NOT NULL,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 UNIQUE(source_table,source_pk,source_row_hash)
)""",
"""CREATE INDEX IF NOT EXISTS idx_unified_clean_source_v700 ON pi_unified_clean_shadow_v700(source_type,source_table)""",
"""CREATE INDEX IF NOT EXISTS idx_unified_clean_fp_v700 ON pi_unified_clean_shadow_v700(strong_fingerprint)""",
"""CREATE TABLE IF NOT EXISTS pi_unified_canonical_v700(
 canonical_id TEXT PRIMARY KEY,
 strong_fingerprint TEXT UNIQUE,
 entity_type TEXT,
 canonical_transaction TEXT,
 locality_clean TEXT,
 city_clean TEXT,
 area_value NUMERIC(18,4),
 area_unit TEXT,
 area_sqft NUMERIC(18,4),
 price_raw TEXT,
 price_kind TEXT,
 phones JSONB DEFAULT '[]'::jsonb,
 clean_record JSONB NOT NULL,
 source_count INTEGER DEFAULT 1,
 status TEXT DEFAULT 'DERIVED_CLEAN',
 created_at TIMESTAMPTZ DEFAULT NOW(),
 updated_at TIMESTAMPTZ DEFAULT NOW()
)""",
"""CREATE TABLE IF NOT EXISTS pi_unified_source_links_v700(
 id BIGSERIAL PRIMARY KEY,
 canonical_id TEXT NOT NULL,
 source_type TEXT NOT NULL,
 source_table TEXT NOT NULL,
 source_pk TEXT NOT NULL,
 source_row_hash TEXT NOT NULL,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 UNIQUE(canonical_id,source_table,source_pk,source_row_hash)
)""",
"""CREATE TABLE IF NOT EXISTS pi_unified_repair_runs_v700(
 run_id BIGSERIAL PRIMARY KEY,
 version TEXT NOT NULL,
 mode TEXT NOT NULL,
 status TEXT NOT NULL,
 source_rows INTEGER DEFAULT 0,
 clean_rows INTEGER DEFAULT 0,
 canonical_rows INTEGER DEFAULT 0,
 duplicate_evidence_links INTEGER DEFAULT 0,
 result JSONB NOT NULL,
 created_at TIMESTAMPTZ DEFAULT NOW()
)"""
]

TEXT_ALIASES=["description","remarks","property_name","details","raw_text","text","ad_text","listing_text","property_details","additional_points"]
LOCALITY_ALIASES=["locality","location","area_name","micro_market","sector","colony","preferred_locations"]
CITY_ALIASES=["city","district"]
PHONE_ALIASES=["phone","contact","contact_no","contact_number","mobile","owner_contact","broker_contact","general_contact","contact_phone"]
LEAD_ALIASES=["lead_type","listing_type","record_type","type","category"]
TX_ALIASES=["rent_or_sale","transaction","transaction_type","deal_type"]
AREA_ALIASES=["available_area_sqft","area_sqft","area","plot_area","size","builtup_area","built_up_area","super_area"]
PRICE_ALIASES=["price","asking_price","rent","amount","sale_amount","rental_amount","monthly_rent"]

BAD_VALUES={"","na","n/a","none","null","unknown","-","unspecified","not available"}

def _engine(core):return getattr(core,"engine",None)
def _app(core):return getattr(core,"app",None) or core
def _route_exists(a,p):
    try:return any(getattr(r,"path",None)==p for r in a.routes)
    except:return False
def _safe(v):
    if isinstance(v,Decimal): return float(v)
    if isinstance(v,(datetime,)): return v.isoformat()
    return v
def _jsonable(row):return {str(k):_safe(v) for k,v in row.items()}
def _norm_text(v):
    s=re.sub(r"\s+"," ",str(v or "")).strip()
    return "" if s.lower() in BAD_VALUES else s
def _first(row,aliases):
    low={str(k).lower():v for k,v in row.items()}
    for a in aliases:
        if a in low and _norm_text(low[a]): return low[a]
    return None
def _all_text(row):
    vals=[]
    for a in TEXT_ALIASES+LEAD_ALIASES+TX_ALIASES+LOCALITY_ALIASES:
        v=_first(row,[a])
        if v is not None: vals.append(_norm_text(v))
    return " | ".join(dict.fromkeys([x for x in vals if x]))
def _phone_list(row):
    textv=[]
    for a in PHONE_ALIASES:
        v=_first(row,[a])
        if v is not None:textv.append(str(v))
    s=" | ".join(textv)
    nums=[]
    compact=re.sub(r"[\s().-]","",s)
    for m in re.finditer(r"(?<!\d)([6-9]\d{9})/(\d{1,4})(?!\d)",compact):
        b,suf=m.groups();nums.extend([b,b[:-len(suf)]+suf])
    nums.extend(re.findall(r"(?<!\d)([6-9]\d{9})(?!\d)",compact))
    nums.extend(re.findall(r"(?<!\d)(0\d{10})(?!\d)",compact))
    return sorted(dict.fromkeys(nums))

def _entity_type(row,textv):
    lead=_norm_text(_first(row,LEAD_ALIASES)).lower()
    s=(lead+" "+textv.lower()).strip()
    if re.search(r"\b(required|requirement|wanted|looking\s+to\s+(buy|rent|lease)|need(?:ed)?|seeking)\b",s):
        return "REQUIREMENT"
    if re.search(r"\b(available|sale|rent|resale|booking|pre[- ]?rented|property|floor|plot|house|apartment|shop|office)\b",s):
        return "PROPERTY_AVAILABILITY"
    return "UNKNOWN"

def _transaction(row,textv,entity):
    tx=_norm_text(_first(row,TX_ALIASES)).lower()
    lead=_norm_text(_first(row,LEAD_ALIASES)).lower()
    s=" ".join([tx,lead,textv.lower()])
    sale=bool(re.search(r"\b(sale|sell|resale|buy|purchase|asking|for\s+sale)\b",s))
    rent=bool(re.search(r"\b(rent|lease|rental|to\s+let|for\s+rent)\b",s))
    # pre-rented inventory with asking/sale language remains SALE
    if "pre-rented" in s or "pre rented" in s:
        if sale or re.search(r"\b(cr|crore|lakh|lac)\b",s): return "SALE"
    if sale and rent:
        return "UNKNOWN"
    if sale:return "SALE"
    if rent:return "RENT"
    return "UNKNOWN"

def _parse_area(row,textv):
    raw=_first(row,AREA_ALIASES)
    unit=""
    val=None
    if raw is not None:
        if isinstance(raw,(int,float,Decimal)):
            val=float(raw)
            # columns explicitly ending sqft are sqft
            for a in AREA_ALIASES:
                if a.endswith("sqft") and _first(row,[a]) is not None:
                    unit="SQFT";break
        else:
            s=str(raw)
            m=re.search(r"(\d[\d,]*(?:\.\d+)?)",s)
            if m: val=float(m.group(1).replace(",",""))
            unit=_unit_from_text(s)
    if val is None:
        # conservative fallback: only explicit unit expressions in text
        m=re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(sq\.?\s*ft|sqft|sq\.?\s*yd|sqyd|sq\.?\s*m|sqm|sq\s*mtr|acre)s?\b",textv,re.I)
        if m:
            val=float(m.group(1).replace(",",""));unit=_unit_from_text(m.group(2))
    sqft=None
    if val is not None:
        if unit=="SQFT":sqft=val
        elif unit=="SQYD":sqft=val*9
        elif unit=="SQM":sqft=val*10.7639104167
        elif unit=="ACRE":sqft=val*43560
    return val,unit or None,sqft

def _unit_from_text(s):
    t=str(s).lower().replace(".","")
    if re.search(r"\b(sq\s*ft|sqft)\b",t):return "SQFT"
    if re.search(r"\b(sq\s*yd|sqyd)\b",t):return "SQYD"
    if re.search(r"\b(sq\s*m|sqm|sq\s*mtr)\b",t):return "SQM"
    if re.search(r"\bacre\b",t):return "ACRE"
    return ""

def _price(row,textv,tx):
    raw=_first(row,PRICE_ALIASES)
    s=_norm_text(raw)
    if not s:
        # only capture explicit @ or currency/magnitude amount
        m=re.search(r"(?:₹|rs\.?|inr|@)\s*[\d,.]+\s*(?:cr|crore|lac|lakh|k|/month|pm|psf|/sqft)?",textv,re.I)
        if m:s=m.group(0).strip()
    kind="UNKNOWN"
    ls=s.lower()
    if re.search(r"(psf|/sqft|per\s*sq\.?\s*ft)",ls):kind="RATE"
    elif tx=="RENT" and s:kind="RENT_AMOUNT"
    elif tx=="SALE" and s:kind="SALE_AMOUNT"
    elif s:kind="TEXT_PRICE"
    return s or None,kind

def _locality(row):
    return _norm_text(_first(row,LOCALITY_ALIASES)) or None
def _city(row):
    return _norm_text(_first(row,CITY_ALIASES)) or None

def _row_hash(raw):
    return hashlib.sha256(json.dumps(raw,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()

def _strong_fp(entity,tx,locality,area_sqft,phones,textv):
    loc=re.sub(r"[^a-z0-9]+","",str(locality or "").lower())
    phone=",".join(phones)
    area="" if area_sqft is None else str(round(float(area_sqft),1))
    desc=re.sub(r"[^a-z0-9]+","",str(textv or "").lower())[:180]
    # Strong duplicate only where there is enough identity evidence.
    if loc and phone and (area or desc):
        seed="|".join([entity,tx,loc,area,phone,desc[:80]])
    elif loc and area and len(desc)>=40:
        seed="|".join([entity,tx,loc,area,desc])
    else:
        return None
    return hashlib.sha256(seed.encode()).hexdigest()

def _issues(entity,tx,loc,city,area_val,area_unit,phones,price_raw):
    out=[]
    if entity=="UNKNOWN":out.append("UNKNOWN_ENTITY_TYPE")
    if tx=="UNKNOWN":out.append("UNKNOWN_TRANSACTION")
    if not loc:out.append("MISSING_LOCALITY")
    if not city:out.append("CITY_NOT_EXPLICIT")
    if area_val is None:out.append("MISSING_AREA")
    elif not area_unit:out.append("MISSING_AREA_UNIT")
    if not phones:out.append("MISSING_VALID_CONTACT")
    if not price_raw:out.append("PRICE_NOT_EXPLICIT")
    return out

def _candidate_tables(engine):
    with engine.connect() as c:
        names=[r[0] for r in c.execute(text("""
          SELECT table_name FROM information_schema.tables
          WHERE table_schema='public' AND table_type='BASE TABLE'
          ORDER BY table_name
        """)).all()]
    include=[]
    for n in names:
        ln=n.lower()
        if ln=="pi_magazine_master" or "newspaper" in ln:
            if not any(x in ln for x in ["academy","run","lesson","exam","shadow","vault","audit","benchmark"]):
                include.append((n,"MAGAZINE" if "magazine" in ln else "NEWSPAPER","ALL"))
    if "pi_properties" in names:
        include.append(("pi_properties","MIXED_PROPERTY_SOURCE","SOURCE_FILTER"))
    return include

def _pk_column(engine,table):
    with engine.connect() as c:
        rows=c.execute(text("""
          SELECT a.attname
          FROM pg_index i
          JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey)
          WHERE i.indrelid=to_regclass(:t) AND i.indisprimary
          ORDER BY a.attnum LIMIT 1
        """),{"t":table}).all()
    return rows[0][0] if rows else None

def _read_rows(engine,table,mode):
    pk=_pk_column(engine,table)
    with engine.connect() as c:
        rows=c.execute(text(f'SELECT * FROM "{table}"')).mappings().all()
    out=[]
    for idx,r in enumerate(rows,1):
        d=dict(r)
        if mode=="SOURCE_FILTER":
            src=str(d.get("source") or d.get("source_type") or "").lower()
            if "magazine" not in src and "newspaper" not in src:
                continue
        spk=str(d.get(pk)) if pk and d.get(pk) is not None else str(idx)
        out.append((spk,_jsonable(d)))
    return out

def _source_type(table,base,raw):
    if base!="MIXED_PROPERTY_SOURCE":return base
    s=str(raw.get("source") or raw.get("source_type") or "").lower()
    return "MAGAZINE" if "magazine" in s else "NEWSPAPER" if "newspaper" in s else "OTHER"

def _clean_one(raw):
    textv=_all_text(raw)
    entity=_entity_type(raw,textv)
    tx=_transaction(raw,textv,entity)
    loc=_locality(raw);city=_city(raw)
    area_val,area_unit,area_sqft=_parse_area(raw,textv)
    phones=_phone_list(raw)
    price_raw,price_kind=_price(raw,textv,tx)
    issues=_issues(entity,tx,loc,city,area_val,area_unit,phones,price_raw)
    fp=_strong_fp(entity,tx,loc,area_sqft,phones,textv)
    status="AUTO_CLEAN" if len([x for x in issues if x not in {"CITY_NOT_EXPLICIT","PRICE_NOT_EXPLICIT"}])==0 else "NEEDS_REVIEW"
    conf=max(0.0,100.0-len(issues)*8.0)
    clean={
        "entity_type":entity,"transaction":tx,"locality":loc,"city":city,
        "area_value":area_val,"area_unit":area_unit,"area_sqft":area_sqft,
        "price_raw":price_raw,"price_kind":price_kind,"phones":phones,
        "description_clean":_norm_text(_first(raw,TEXT_ALIASES)) or None
    }
    return clean,fp,issues,status,conf

def run_once(core):
    if not _LOCK.acquire(False):return STATE.get("result") or dict(STATE)
    try:
        STATE.update(status="RUNNING",phase="DISCOVER_SOURCE_TABLES",started_at=datetime.now(timezone.utc).isoformat(),
                     finished_at=None,current_table=None,processed=0,last_error=None)
        engine=_engine(core)
        if engine is None:raise RuntimeError("Database engine unavailable")
        with engine.begin() as c:
            for ddl in DDL:c.execute(text(ddl))

        tables=_candidate_tables(engine)
        source_rows=0;inserted=0;by_source=defaultdict(int);issue_counts=Counter()
        STATE["phase"]="BUILD_CLEAN_SHADOW"

        with engine.begin() as c:
            # Rebuild derived shadow/canonical safely. Raw/source tables are never touched.
            c.execute(text("TRUNCATE pi_unified_source_links_v700, pi_unified_canonical_v700, pi_unified_clean_shadow_v700 RESTART IDENTITY"))

        for table,base_type,mode in tables:
            STATE["current_table"]=table
            rows=_read_rows(engine,table,mode)
            source_rows+=len(rows)
            for spk,raw in rows:
                st=_source_type(table,base_type,raw)
                clean,fp,issues,status,conf=_clean_one(raw)
                rh=_row_hash(raw)
                with engine.begin() as c:
                    c.execute(text("""INSERT INTO pi_unified_clean_shadow_v700(
                      source_type,source_table,source_pk,raw_record,clean_record,entity_type,canonical_transaction,
                      locality_clean,city_clean,area_value,area_unit,area_sqft,price_raw,price_kind,phones,
                      strong_fingerprint,issues,auto_status,confidence,source_row_hash)
                      VALUES(:st,:tb,:pk,CAST(:raw AS JSONB),CAST(:clean AS JSONB),:et,:tx,:loc,:city,:av,:au,:asq,
                             :pr,:pkind,CAST(:phones AS JSONB),:fp,CAST(:issues AS JSONB),:status,:conf,:rh)
                    """),{
                        "st":st,"tb":table,"pk":spk,"raw":json.dumps(raw,ensure_ascii=False),
                        "clean":json.dumps(clean,ensure_ascii=False),"et":clean["entity_type"],"tx":clean["transaction"],
                        "loc":clean["locality"],"city":clean["city"],"av":clean["area_value"],"au":clean["area_unit"],
                        "asq":clean["area_sqft"],"pr":clean["price_raw"],"pkind":clean["price_kind"],
                        "phones":json.dumps(clean["phones"]),"fp":fp,"issues":json.dumps(issues),
                        "status":status,"conf":conf,"rh":rh
                    })
                inserted+=1;by_source[st]+=1;STATE["processed"]=inserted
                issue_counts.update(issues)

        STATE["phase"]="STRONG_DEDUPE_AND_CANONICALIZE"
        with engine.begin() as c:
            # duplicate group only when strong fingerprint repeats
            c.execute(text("""
              UPDATE pi_unified_clean_shadow_v700 s SET duplicate_group='DUP-'||substr(s.strong_fingerprint,1,12)
              WHERE s.strong_fingerprint IS NOT NULL
                AND (SELECT COUNT(*) FROM pi_unified_clean_shadow_v700 x
                     WHERE x.strong_fingerprint=s.strong_fingerprint)>1
            """))
            # Canonical rows: strong identity merges evidence. Weak identity remains one canonical per source row.
            rows=c.execute(text("""SELECT * FROM pi_unified_clean_shadow_v700 ORDER BY id""")).mappings().all()

        canonical_map={}
        links=0
        for r in rows:
            d=dict(r)
            key=d.get("strong_fingerprint") or ("ROW:"+d["source_table"]+":"+d["source_pk"]+":"+d["source_row_hash"])
            cid=canonical_map.get(key)
            if not cid:
                cid="CAN-"+hashlib.sha256(key.encode()).hexdigest()[:16].upper()
                canonical_map[key]=cid
                with engine.begin() as c:
                    c.execute(text("""INSERT INTO pi_unified_canonical_v700(
                      canonical_id,strong_fingerprint,entity_type,canonical_transaction,locality_clean,city_clean,
                      area_value,area_unit,area_sqft,price_raw,price_kind,phones,clean_record,source_count,status)
                      VALUES(:cid,:fp,:et,:tx,:loc,:city,:av,:au,:asq,:pr,:pkind,CAST(:phones AS JSONB),
                             CAST(:clean AS JSONB),1,:status)
                    """),{"cid":cid,"fp":d.get("strong_fingerprint"),"et":d.get("entity_type"),
                           "tx":d.get("canonical_transaction"),"loc":d.get("locality_clean"),"city":d.get("city_clean"),
                           "av":d.get("area_value"),"au":d.get("area_unit"),"asq":d.get("area_sqft"),
                           "pr":d.get("price_raw"),"pkind":d.get("price_kind"),
                           "phones":json.dumps(d.get("phones") or []),"clean":json.dumps(d.get("clean_record") or {},ensure_ascii=False),
                           "status":"DERIVED_CLEAN"})
            else:
                with engine.begin() as c:
                    c.execute(text("UPDATE pi_unified_canonical_v700 SET source_count=source_count+1,updated_at=NOW() WHERE canonical_id=:cid"),{"cid":cid})
            with engine.begin() as c:
                c.execute(text("""INSERT INTO pi_unified_source_links_v700(
                  canonical_id,source_type,source_table,source_pk,source_row_hash)
                  VALUES(:cid,:st,:tb,:pk,:rh) ON CONFLICT DO NOTHING
                """),{"cid":cid,"st":d["source_type"],"tb":d["source_table"],"pk":d["source_pk"],"rh":d["source_row_hash"]})
            links+=1

        with engine.connect() as c:
            canon=c.execute(text("SELECT COUNT(*) FROM pi_unified_canonical_v700")).scalar_one()
            dup_groups=c.execute(text("SELECT COUNT(DISTINCT duplicate_group) FROM pi_unified_clean_shadow_v700 WHERE duplicate_group IS NOT NULL")).scalar_one()
            dup_rows=c.execute(text("SELECT COUNT(*) FROM pi_unified_clean_shadow_v700 WHERE duplicate_group IS NOT NULL")).scalar_one()
            auto_clean=c.execute(text("SELECT COUNT(*) FROM pi_unified_clean_shadow_v700 WHERE auto_status='AUTO_CLEAN'")).scalar_one()
            needs_review=c.execute(text("SELECT COUNT(*) FROM pi_unified_clean_shadow_v700 WHERE auto_status='NEEDS_REVIEW'")).scalar_one()
            reqs=c.execute(text("SELECT COUNT(*) FROM pi_unified_clean_shadow_v700 WHERE entity_type='REQUIREMENT'")).scalar_one()
            props=c.execute(text("SELECT COUNT(*) FROM pi_unified_clean_shadow_v700 WHERE entity_type='PROPERTY_AVAILABILITY'")).scalar_one()

        result={
            "version":VERSION,"mode":MODE,"status":"COMPLETE",
            "source_tables":[{"table":t,"base_type":s,"mode":m} for t,s,m in tables],
            "counts":{"source_rows":source_rows,"clean_shadow_rows":inserted,"canonical_rows":canon,
                      "source_links":links,"strong_duplicate_groups":dup_groups,"rows_in_strong_duplicate_groups":dup_rows,
                      "auto_clean":auto_clean,"needs_review":needs_review,
                      "property_availability":props,"requirements":reqs,"by_source":dict(by_source)},
            "top_issues":dict(issue_counts.most_common(20)),
            "architecture":{
                "raw_source_tables_mutated":False,
                "clean_shadow_rebuilt":True,
                "canonical_is_derived_not_source_truth":True,
                "strong_dedupe_only":True,
                "weak_possible_duplicates_not_merged":True,
                "ai_calls_used":0,
                "gemini_quota_required":False,
                "next_step":"PROMOTE_ONLY_VALIDATED_DERIVED_CLEAN_RECORDS_TO_MASTER_DATABASE_AFTER_AUDIT"
            },
            "safety":{"raw_deletes":0,"source_updates":0,"gold_mutations":0,"champion_mutations":0,
                      "newspaper_source_mutations":0,"magazine_source_mutations":0}
        }
        with engine.begin() as c:
            c.execute(text("""INSERT INTO pi_unified_repair_runs_v700(
              version,mode,status,source_rows,clean_rows,canonical_rows,duplicate_evidence_links,result)
              VALUES(:v,:m,'COMPLETE',:sr,:cr,:ca,:dl,CAST(:r AS JSONB))"""),
              {"v":VERSION,"m":MODE,"sr":source_rows,"cr":inserted,"ca":canon,"dl":links-canon,
               "r":json.dumps(result,ensure_ascii=False)})
        STATE.update(status="COMPLETE",phase="COMPLETE",finished_at=datetime.now(timezone.utc).isoformat(),
                     current_table=None,result=result)
        return result
    except Exception as exc:
        STATE.update(status="ERROR",phase="ERROR",finished_at=datetime.now(timezone.utc).isoformat(),
                     current_table=None,last_error=f"{type(exc).__name__}: {exc}")
        return dict(STATE)
    finally:_LOCK.release()

def status(core):
    return STATE.get("result") or dict(STATE)

def dashboard(core):
    s=status(core);counts=s.get("counts") or {}
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta http-equiv='refresh' content='10'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>Unified Data Repair 7.0</title>
<style>body{{font-family:Arial;background:#f5f7fb;color:#172033;margin:0}}header{{background:#102235;color:white;padding:18px}}
.wrap{{max-width:1500px;margin:auto;padding:18px}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(160px,1fr));gap:10px}}
.card{{background:white;border:1px solid #e2e8f0;border-radius:12px;padding:14px;margin-bottom:12px}}b.num{{font-size:25px;display:block}}
pre{{white-space:pre-wrap;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px;overflow:auto}}</style></head>
<body><header><b>Alliance Unified Newspaper + Magazine Data Repair 7.0</b><br>
<small>Raw source immutable · deterministic cleanup · strong dedupe · zero AI calls</small></header>
<div class='wrap'><div class='card'><b>{html.escape(str(s.get("status")))}</b> · Phase {html.escape(str(s.get("phase")))}</div>
<div class='grid'>
<div class='card'>Source rows<b class='num'>{counts.get("source_rows","-")}</b></div>
<div class='card'>Clean shadow<b class='num'>{counts.get("clean_shadow_rows","-")}</b></div>
<div class='card'>Canonical<b class='num'>{counts.get("canonical_rows","-")}</b></div>
<div class='card'>Strong duplicate groups<b class='num'>{counts.get("strong_duplicate_groups","-")}</b></div>
<div class='card'>Auto clean<b class='num'>{counts.get("auto_clean","-")}</b></div>
<div class='card'>Needs review<b class='num'>{counts.get("needs_review","-")}</b></div>
<div class='card'>Properties<b class='num'>{counts.get("property_availability","-")}</b></div>
<div class='card'>Requirements<b class='num'>{counts.get("requirements","-")}</b></div>
</div><pre>{html.escape(json.dumps(s,ensure_ascii=False,indent=2))}</pre></div></body></html>"""

def register(core):
    app=_app(core)
    if not _route_exists(app,"/api/property-brain/unified-data-repair-v700/status"):
        @app.get("/api/property-brain/unified-data-repair-v700/status")
        def _s():return status(core)
    if not _route_exists(app,"/property-brain/unified-data-repair-v700"):
        @app.get("/property-brain/unified-data-repair-v700",response_class=HTMLResponse)
        def _p():return HTMLResponse(dashboard(core))

def _runner(core):
    time.sleep(45);run_once(core)

def start(core):
    global _STARTED
    register(core)
    if not _STARTED:
        _STARTED=True
        threading.Thread(target=_runner,args=(core,),daemon=True,name="unified-data-repair-v700").start()
    return STATE
