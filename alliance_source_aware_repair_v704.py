from __future__ import annotations
import hashlib, html, json, re, threading, time, uuid
from collections import Counter, defaultdict
from datetime import date, datetime, time as dt_time, timezone
from decimal import Decimal

from fastapi.responses import HTMLResponse
from sqlalchemy import text

VERSION="7.0.4-ALLIANCE-SOURCE-AWARE-NEWSPAPER-MAGAZINE-FORENSIC-REPAIR"
MODE="SOURCE_ROLE_AWARE_DYNAMIC_SCHEMA_CONTACT_RECOVERY_NORMALIZE_DEDUPE_EXCEPTION_QUEUE_NO_AI_NO_SOURCE_MUTATION"

STATE={"status":"NOT_STARTED","phase":"WAITING","started_at":None,"finished_at":None,
       "current_table":None,"processed":0,"result":None,"last_error":None}
_LOCK=threading.Lock();_STARTED=False

ENTITY_TABLES={"pi_magazine_master":"MAGAZINE","pi_newspaper_properties":"NEWSPAPER"}
OPTIONAL_ENTITY_TABLES={"pi_properties":"MIXED"}
PROVENANCE_ONLY={"pi_newspaper_sources":"NEWSPAPER_SOURCE","pi_newspaper_capture_sync":"NEWSPAPER_SYNC","pi_magazine_property_map":"MAGAZINE_MAP"}
INFRA_ONLY={"pi_whatsapp_newspaper_format":"FORMAT","pi_whatsapp_newspaper_format_generation":"FORMAT_GENERATION"}
CONTACT_TABLE_CANDIDATES=["pi_magazine_contact_links","pi_magazine_contacts","pi_property_contacts","pi_contacts"]

DDL=[
"""CREATE TABLE IF NOT EXISTS pi_source_aware_clean_v704(
 id BIGSERIAL PRIMARY KEY, source_type TEXT NOT NULL, source_table TEXT NOT NULL, source_pk TEXT NOT NULL,
 raw_record JSONB NOT NULL, clean_record JSONB NOT NULL, entity_type TEXT, canonical_transaction TEXT,
 locality_clean TEXT, city_clean TEXT, area_value NUMERIC(18,4), area_unit TEXT, area_sqft NUMERIC(18,4),
 price_raw TEXT, price_kind TEXT, phones JSONB DEFAULT '[]'::jsonb, contact_provenance JSONB DEFAULT '[]'::jsonb,
 source_row_hash TEXT NOT NULL, identity_fingerprint TEXT, duplicate_group TEXT, quality_status TEXT NOT NULL,
 confidence NUMERIC(6,2), issues JSONB DEFAULT '[]'::jsonb, created_at TIMESTAMPTZ DEFAULT NOW(),
 UNIQUE(source_table,source_pk,source_row_hash))""",
"""CREATE INDEX IF NOT EXISTS idx_source_aware_v704_source ON pi_source_aware_clean_v704(source_type,source_table)""",
"""CREATE INDEX IF NOT EXISTS idx_source_aware_v704_fp ON pi_source_aware_clean_v704(identity_fingerprint)""",
"""CREATE TABLE IF NOT EXISTS pi_source_aware_canonical_v704(
 canonical_id TEXT PRIMARY KEY, identity_fingerprint TEXT, entity_type TEXT, canonical_transaction TEXT,
 locality_clean TEXT, city_clean TEXT, area_value NUMERIC(18,4), area_unit TEXT, area_sqft NUMERIC(18,4),
 price_raw TEXT, price_kind TEXT, phones JSONB DEFAULT '[]'::jsonb, clean_record JSONB NOT NULL,
 source_count INTEGER DEFAULT 1, quality_status TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW())""",
"""CREATE TABLE IF NOT EXISTS pi_source_aware_links_v704(
 id BIGSERIAL PRIMARY KEY, canonical_id TEXT NOT NULL, source_type TEXT NOT NULL, source_table TEXT NOT NULL,
 source_pk TEXT NOT NULL, source_row_hash TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW(),
 UNIQUE(canonical_id,source_table,source_pk,source_row_hash))""",
"""CREATE TABLE IF NOT EXISTS pi_source_aware_exception_queue_v704(
 id BIGSERIAL PRIMARY KEY, source_type TEXT NOT NULL, source_table TEXT NOT NULL, source_pk TEXT NOT NULL,
 issues JSONB NOT NULL, clean_record JSONB NOT NULL, status TEXT DEFAULT 'OPEN', created_at TIMESTAMPTZ DEFAULT NOW(),
 UNIQUE(source_table,source_pk))""",
"""CREATE TABLE IF NOT EXISTS pi_source_aware_runs_v704(
 run_id BIGSERIAL PRIMARY KEY, version TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL,
 result JSONB NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW())"""
]

TEXT_ALIASES=["description","remarks","property_name","details","raw_text","text","ad_text","listing_text","property_details","additional_points","property","inventory","inventory_text","ad_description"]
LOCALITY_ALIASES=["locality","location","area_name","micro_market","sector","colony","neighbourhood","neighborhood","property_location","address_locality"]
CITY_ALIASES=["city","district","city_name"]
PHONE_ALIASES=["phone","contact","contact_no","contact_number","mobile","mobile_no","mobile_number","owner_contact","broker_contact","general_contact","contact_phone","phone_number","contact_numbers","phones"]
LEAD_ALIASES=["lead_type","listing_type","record_type","type","category","entity_type"]
TX_ALIASES=["rent_or_sale","transaction","transaction_type","deal_type","purpose","offer_type"]
AREA_ALIASES=["available_area_sqft","area_sqft","area","plot_area","size","builtup_area","built_up_area","super_area","carpet_area","covered_area","area_value"]
AREA_UNIT_ALIASES=["area_unit","unit","area_uom","uom"]
PRICE_ALIASES=["price","asking_price","rent","amount","sale_amount","rental_amount","monthly_rent","asking","rate","budget"]
PK_ALIASES=["id","property_id","listing_id","record_id","magazine_id","newspaper_property_id","ref_id","reference_id","ref"]
BAD_VALUES={"","na","n/a","none","null","unknown","-","unspecified","not available","nil"}

def _engine(core):return getattr(core,"engine",None)
def _app(core):return getattr(core,"app",None) or core
def _route_exists(a,p):
    try:return any(getattr(r,"path",None)==p for r in a.routes)
    except:return False
def _safe(v):
    if v is None or isinstance(v,(str,int,float,bool)):return v
    if isinstance(v,Decimal):return float(v)
    if isinstance(v,(datetime,date,dt_time)):return v.isoformat()
    if isinstance(v,uuid.UUID):return str(v)
    if isinstance(v,(bytes,bytearray,memoryview)):return bytes(v).hex()
    if isinstance(v,dict):return {str(k):_safe(val) for k,val in v.items()}
    if isinstance(v,(list,tuple,set)):return [_safe(x) for x in v]
    return str(v)
def _jsonable(row):return {str(k):_safe(v) for k,v in row.items()}
def _norm(v):
    s=re.sub(r"\s+"," ",str(v or "")).strip()
    return "" if s.lower() in BAD_VALUES else s
def _lower_map(row):return {str(k).lower():v for k,v in row.items()}
def _first(row,aliases):
    low=_lower_map(row)
    for a in aliases:
        if a in low and _norm(low[a]):return low[a]
    return None
def _table_exists(engine,t):
    with engine.connect() as c:return bool(c.execute(text("SELECT to_regclass(:t) IS NOT NULL"),{"t":t}).scalar())
def _columns(engine,t):
    with engine.connect() as c:
        rows=c.execute(text("""SELECT column_name,data_type FROM information_schema.columns
          WHERE table_schema='public' AND table_name=:t ORDER BY ordinal_position"""),{"t":t}).all()
    return [{"name":r[0],"type":r[1]} for r in rows]
def _pk_column(engine,t):
    with engine.connect() as c:
        rows=c.execute(text("""SELECT a.attname FROM pg_index i JOIN pg_attribute a
          ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey)
          WHERE i.indrelid=to_regclass(:t) AND i.indisprimary ORDER BY a.attnum LIMIT 1"""),{"t":t}).all()
    return rows[0][0] if rows else None
def _foreign_keys(engine,t):
    with engine.connect() as c:
        rows=c.execute(text("""SELECT kcu.column_name,ccu.table_name,ccu.column_name
          FROM information_schema.table_constraints tc
          JOIN information_schema.key_column_usage kcu ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema
          JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name=tc.constraint_name AND ccu.table_schema=tc.table_schema
          WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema='public' AND tc.table_name=:t"""),{"t":t}).all()
    return [{"column":r[0],"foreign_table":r[1],"foreign_column":r[2]} for r in rows]

def _read_rows(engine,t,filter_source=False):
    pk=_pk_column(engine,t)
    with engine.connect() as c:rows=c.execute(text(f'SELECT * FROM "{t}"')).mappings().all()
    out=[]
    for idx,r in enumerate(rows,1):
        d=_jsonable(dict(r))
        if filter_source:
            src=str(d.get("source") or d.get("source_type") or "").lower()
            if "magazine" not in src and "newspaper" not in src:continue
        spk=str(d.get(pk)) if pk and d.get(pk) is not None else None
        if not spk:
            for a in PK_ALIASES:
                if d.get(a) not in (None,""):spk=str(d[a]);break
        if not spk:spk=str(idx)
        out.append((spk,d))
    return out

def _phones_from_any(v):
    if v is None:return []
    if isinstance(v,(list,tuple,set)):
        out=[]
        for x in v:out.extend(_phones_from_any(x))
        return sorted(dict.fromkeys(out))
    if isinstance(v,dict):
        out=[]
        for x in v.values():out.extend(_phones_from_any(x))
        return sorted(dict.fromkeys(out))
    compact=re.sub(r"[\s().-]","",str(v));out=[]
    for m in re.finditer(r"(?<!\d)([6-9]\d{9})/(\d{1,4})(?!\d)",compact):
        b,suf=m.groups();out.extend([b,b[:-len(suf)]+suf])
    out.extend(re.findall(r"(?<!\d)([6-9]\d{9})(?!\d)",compact))
    out.extend(re.findall(r"(?<!\d)(0\d{10})(?!\d)",compact))
    return sorted(dict.fromkeys(out))
def _direct_contacts(row):
    low=_lower_map(row);phones=[];prov=[]
    for a in PHONE_ALIASES:
        if a in low:
            vals=_phones_from_any(low[a])
            if vals:phones.extend(vals);prov.append({"kind":"DIRECT_COLUMN","column":a,"phones":vals})
    return sorted(dict.fromkeys(phones)),prov

def _build_contact_index(engine):
    index=defaultdict(lambda:{"phones":[],"provenance":[]});audit=[]
    for t in [x for x in CONTACT_TABLE_CANDIDATES if _table_exists(engine,x)]:
        cols=[x["name"] for x in _columns(engine,t)];fks=_foreign_keys(engine,t);rows=_read_rows(engine,t,False)
        audit.append({"table":t,"columns":cols,"foreign_keys":fks,"rows":len(rows)})
        id_cols=[c for c in cols if c.lower() in {"property_id","magazine_id","magazine_property_id","listing_id","record_id","master_id","ref_id","reference_id"}]
        for fk in fks:
            if fk["foreign_table"]=="pi_magazine_master" and fk["column"] not in id_cols:id_cols.append(fk["column"])
        phone_cols=[c for c in cols if any(k in c.lower() for k in ["phone","mobile","contact"])]
        for _,row in rows:
            phones=[]
            for pc in phone_cols:phones.extend(_phones_from_any(row.get(pc)))
            phones=sorted(dict.fromkeys(phones))
            if not phones:continue
            for ic in id_cols:
                key=row.get(ic)
                if key not in (None,""):
                    k=("pi_magazine_master",str(key))
                    index[k]["phones"].extend(phones)
                    index[k]["provenance"].append({"kind":"LINKED_CONTACT_TABLE","table":t,"id_column":ic,"phones":phones})
    for k,v in index.items():v["phones"]=sorted(dict.fromkeys(v["phones"]))
    return index,audit

def _text_bundle(row):
    vals=[]
    for a in TEXT_ALIASES+LEAD_ALIASES+TX_ALIASES+LOCALITY_ALIASES+PRICE_ALIASES:
        v=_first(row,[a])
        if v is not None:vals.append(_norm(v))
    return " | ".join(dict.fromkeys(x for x in vals if x))
def _entity_type(row,textv):
    lead=_norm(_first(row,LEAD_ALIASES)).lower();s=(lead+" "+textv.lower()).strip()
    if re.search(r"\b(requirement|required|wanted|looking\s+(?:to\s+)?(?:buy|rent|lease)|need(?:ed)?|seeking|want\s+to\s+(?:buy|rent|lease))\b",s):return "REQUIREMENT"
    if re.search(r"\b(available|availability|sale|rent|resale|booking|pre[- ]?rented|property|floor|plot|house|apartment|shop|office|villa|farmhouse|commercial)\b",s):return "PROPERTY_AVAILABILITY"
    return "UNKNOWN"
def _transaction(row,textv,entity):
    tx=_norm(_first(row,TX_ALIASES)).lower();lead=_norm(_first(row,LEAD_ALIASES)).lower();s=" ".join([tx,lead,textv.lower()])
    sale=bool(re.search(r"\b(sale|sell|resale|buy|purchase|purchase requirement|for sale)\b",s))
    rent=bool(re.search(r"\b(rent|lease|rental|to let|for rent|rental requirement)\b",s))
    if ("pre-rented" in s or "pre rented" in s) and (sale or re.search(r"\b(cr|crore|lakh|lac)\b",s)):return "SALE"
    if sale and rent:return "UNKNOWN"
    if sale:return "SALE"
    if rent:return "RENT"
    return "UNKNOWN"
def _unit_from_text(s):
    t=str(s or "").lower().replace(".","")
    if re.search(r"\b(sq\s*ft|sqft|ft2|sft)\b",t):return "SQFT"
    if re.search(r"\b(sq\s*yd|sqyd|sq\s*yard|yards?)\b",t):return "SQYD"
    if re.search(r"\b(sq\s*m|sqm|sq\s*mtr|m2)\b",t):return "SQM"
    if re.search(r"\bacre?s?\b",t):return "ACRE"
    return ""
def _area(row,textv):
    raw=_first(row,AREA_ALIASES);unit=_unit_from_text(_first(row,AREA_UNIT_ALIASES));val=None
    if raw is not None:
        if isinstance(raw,(int,float,Decimal)):val=float(raw)
        else:
            m=re.search(r"(\d[\d,]*(?:\.\d+)?)",str(raw))
            if m:val=float(m.group(1).replace(",",""))
            if not unit:unit=_unit_from_text(raw)
    if val is None:
        m=re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(sq\.?\s*ft|sqft|sft|sq\.?\s*yd|sqyd|sq\.?\s*yard|yards?|sq\.?\s*m|sqm|sq\s*mtr|acre)s?\b",textv,re.I)
        if m:val=float(m.group(1).replace(",",""));unit=_unit_from_text(m.group(2))
    sqft=None
    if val is not None:
        if unit=="SQFT":sqft=val
        elif unit=="SQYD":sqft=val*9
        elif unit=="SQM":sqft=val*10.7639104167
        elif unit=="ACRE":sqft=val*43560
    return val,unit or None,sqft
def _price(row,textv,tx):
    raw=_first(row,PRICE_ALIASES);s=_norm(raw)
    if not s:
        m=re.search(r"(?:₹|rs\.?|inr|@)\s*[\d,.]+\s*(?:cr|crore|lac|lakh|k|/month|pm|psf|/sqft)?",textv,re.I)
        if m:s=m.group(0).strip()
    kind="UNKNOWN";ls=s.lower()
    if re.search(r"(psf|/sqft|per\s*sq\.?\s*ft)",ls):kind="RATE"
    elif tx=="RENT" and s:kind="RENT_AMOUNT"
    elif tx=="SALE" and s:kind="SALE_AMOUNT"
    elif s:kind="TEXT_PRICE"
    return s or None,kind
def _locality(row):return _norm(_first(row,LOCALITY_ALIASES)) or None
def _city(row):return _norm(_first(row,CITY_ALIASES)) or None
def _row_hash(raw):return hashlib.sha256(json.dumps(raw,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
def _quality(entity,tx,loc,area_val,phones,textv):
    issues=[]
    if entity=="UNKNOWN":issues.append("UNKNOWN_ENTITY_TYPE")
    if tx=="UNKNOWN":issues.append("UNKNOWN_TRANSACTION")
    if not loc:issues.append("MISSING_LOCALITY")
    if area_val is None:issues.append("MISSING_AREA")
    if not phones:issues.append("MISSING_VALID_CONTACT")
    if len(_norm(textv))<12:issues.append("LOW_EVIDENCE_TEXT")
    promotion_ready=(entity!="UNKNOWN" and tx!="UNKNOWN" and bool(loc) and bool(phones) and len(_norm(textv))>=12)
    clean_usable=(entity!="UNKNOWN" and bool(loc) and len(_norm(textv))>=12)
    status="PROMOTION_READY" if promotion_ready else "CLEAN_USABLE" if clean_usable else "EXCEPTION"
    conf=100;weights={"UNKNOWN_ENTITY_TYPE":28,"UNKNOWN_TRANSACTION":18,"MISSING_LOCALITY":24,"MISSING_AREA":7,"MISSING_VALID_CONTACT":15,"LOW_EVIDENCE_TEXT":18}
    for x in issues:conf-=weights.get(x,5)
    return issues,status,max(0,conf)
def _fingerprint(entity,tx,loc,area_sqft,phones,textv):
    locn=re.sub(r"[^a-z0-9]+","",str(loc or "").lower());phone=",".join(sorted(phones))
    area="" if area_sqft is None else str(round(float(area_sqft),1));desc=re.sub(r"[^a-z0-9]+","",str(textv or "").lower())[:160]
    if locn and phone:seed="|".join([entity,tx,locn,phone,area,desc[:80]])
    elif locn and area and len(desc)>=70:seed="|".join([entity,tx,locn,area,desc])
    else:return None
    return hashlib.sha256(seed.encode()).hexdigest()
def _clean_record(row,linked):
    textv=_text_bundle(row);entity=_entity_type(row,textv);tx=_transaction(row,textv,entity);loc=_locality(row);city=_city(row)
    av,au,asq=_area(row,textv);pr,pk=_price(row,textv,tx);direct,prov=_direct_contacts(row)
    phones=sorted(dict.fromkeys(direct+(linked or {}).get("phones",[])));prov=prov+(linked or {}).get("provenance",[])
    issues,status,conf=_quality(entity,tx,loc,av,phones,textv)
    clean={"entity_type":entity,"transaction":tx,"locality":loc,"city":city,"area_value":av,"area_unit":au,"area_sqft":asq,
           "price_raw":pr,"price_kind":pk,"phones":phones,"description_clean":_norm(_first(row,TEXT_ALIASES)) or None,"source_text":textv or None}
    return clean,prov,issues,status,conf,_fingerprint(entity,tx,loc,asq,phones,textv)

def run_once(core):
    if not _LOCK.acquire(False):return STATE.get("result") or dict(STATE)
    try:
        STATE.update(status="RUNNING",phase="SCHEMA_FORENSICS",started_at=datetime.now(timezone.utc).isoformat(),finished_at=None,current_table=None,processed=0,result=None,last_error=None)
        engine=_engine(core)
        if engine is None:raise RuntimeError("Database engine unavailable")
        with engine.begin() as c:
            for ddl in DDL:c.execute(text(ddl))
        existing={}
        for t,st in {**ENTITY_TABLES,**OPTIONAL_ENTITY_TABLES,**PROVENANCE_ONLY,**INFRA_ONLY}.items():
            if _table_exists(engine,t):
                existing[t]={"role":"ENTITY" if t in ENTITY_TABLES or t in OPTIONAL_ENTITY_TABLES else "PROVENANCE_ONLY" if t in PROVENANCE_ONLY else "INFRA_ONLY",
                             "source_type":st,"columns":_columns(engine,t),"primary_key":_pk_column(engine,t),"foreign_keys":_foreign_keys(engine,t)}
        contact_index,contact_audit=_build_contact_index(engine)
        with engine.begin() as c:
            c.execute(text("TRUNCATE pi_source_aware_links_v704, pi_source_aware_canonical_v704, pi_source_aware_exception_queue_v704, pi_source_aware_clean_v704 RESTART IDENTITY"))
        source_counts=Counter();status_counts=Counter();issue_counts=Counter();total_entity_rows=0;processed=0
        plan=[(t,st,False) for t,st in ENTITY_TABLES.items() if t in existing]
        if "pi_properties" in existing:plan.append(("pi_properties","MIXED",True))
        STATE["phase"]="SOURCE_AWARE_CLEANING"
        for t,base,filter_source in plan:
            STATE["current_table"]=t;rows=_read_rows(engine,t,filter_source);total_entity_rows+=len(rows)
            for spk,raw in rows:
                st=base
                if base=="MIXED":
                    sv=str(raw.get("source") or raw.get("source_type") or "").lower()
                    st="MAGAZINE" if "magazine" in sv else "NEWSPAPER"
                clean,prov,issues,qstatus,conf,fp=_clean_record(raw,contact_index.get((t,spk),{}));rh=_row_hash(raw)
                with engine.begin() as c:
                    c.execute(text("""INSERT INTO pi_source_aware_clean_v704(
                      source_type,source_table,source_pk,raw_record,clean_record,entity_type,canonical_transaction,locality_clean,city_clean,
                      area_value,area_unit,area_sqft,price_raw,price_kind,phones,contact_provenance,source_row_hash,identity_fingerprint,
                      quality_status,confidence,issues)
                      VALUES(:st,:tb,:pk,CAST(:raw AS JSONB),CAST(:clean AS JSONB),:et,:tx,:loc,:city,:av,:au,:asq,:pr,:pkind,
                      CAST(:phones AS JSONB),CAST(:prov AS JSONB),:rh,:fp,:qs,:conf,CAST(:issues AS JSONB))"""),
                      {"st":st,"tb":t,"pk":spk,"raw":json.dumps(raw,ensure_ascii=False),"clean":json.dumps(clean,ensure_ascii=False),
                       "et":clean["entity_type"],"tx":clean["transaction"],"loc":clean["locality"],"city":clean["city"],"av":clean["area_value"],
                       "au":clean["area_unit"],"asq":clean["area_sqft"],"pr":clean["price_raw"],"pkind":clean["price_kind"],
                       "phones":json.dumps(clean["phones"]),"prov":json.dumps(prov),"rh":rh,"fp":fp,"qs":qstatus,"conf":conf,"issues":json.dumps(issues)})
                    if qstatus=="EXCEPTION":
                        c.execute(text("""INSERT INTO pi_source_aware_exception_queue_v704(source_type,source_table,source_pk,issues,clean_record)
                          VALUES(:st,:tb,:pk,CAST(:issues AS JSONB),CAST(:clean AS JSONB))
                          ON CONFLICT(source_table,source_pk) DO UPDATE SET issues=EXCLUDED.issues,clean_record=EXCLUDED.clean_record,status='OPEN'"""),
                          {"st":st,"tb":t,"pk":spk,"issues":json.dumps(issues),"clean":json.dumps(clean,ensure_ascii=False)})
                processed+=1;STATE["processed"]=processed;source_counts[st]+=1;status_counts[qstatus]+=1;issue_counts.update(issues)
        STATE["phase"]="STRONG_DEDUPE_CANONICAL"
        with engine.begin() as c:
            c.execute(text("""UPDATE pi_source_aware_clean_v704 s SET duplicate_group='DUP-'||substr(s.identity_fingerprint,1,12)
              WHERE s.identity_fingerprint IS NOT NULL AND
              (SELECT COUNT(*) FROM pi_source_aware_clean_v704 x WHERE x.identity_fingerprint=s.identity_fingerprint)>1"""))
            rows=c.execute(text("SELECT * FROM pi_source_aware_clean_v704 ORDER BY id")).mappings().all()
        cmap={};links=0
        for r in rows:
            d=dict(r);fp=d.get("identity_fingerprint");key=fp or ("ROW:"+d["source_table"]+":"+d["source_pk"]+":"+d["source_row_hash"]);cid=cmap.get(key)
            if not cid:
                cid="CAN-"+hashlib.sha256(key.encode()).hexdigest()[:16].upper();cmap[key]=cid
                with engine.begin() as c:
                    c.execute(text("""INSERT INTO pi_source_aware_canonical_v704(
                      canonical_id,identity_fingerprint,entity_type,canonical_transaction,locality_clean,city_clean,area_value,area_unit,
                      area_sqft,price_raw,price_kind,phones,clean_record,source_count,quality_status)
                      VALUES(:cid,:fp,:et,:tx,:loc,:city,:av,:au,:asq,:pr,:pkind,CAST(:phones AS JSONB),CAST(:clean AS JSONB),1,:qs)"""),
                      {"cid":cid,"fp":fp,"et":d["entity_type"],"tx":d["canonical_transaction"],"loc":d["locality_clean"],"city":d["city_clean"],
                       "av":d["area_value"],"au":d["area_unit"],"asq":d["area_sqft"],"pr":d["price_raw"],"pkind":d["price_kind"],
                       "phones":json.dumps(_safe(d["phones"])),"clean":json.dumps(_safe(d["clean_record"]),ensure_ascii=False),"qs":d["quality_status"]})
            else:
                with engine.begin() as c:c.execute(text("UPDATE pi_source_aware_canonical_v704 SET source_count=source_count+1,updated_at=NOW() WHERE canonical_id=:cid"),{"cid":cid})
            with engine.begin() as c:
                c.execute(text("""INSERT INTO pi_source_aware_links_v704(canonical_id,source_type,source_table,source_pk,source_row_hash)
                  VALUES(:cid,:st,:tb,:pk,:rh) ON CONFLICT DO NOTHING"""),
                  {"cid":cid,"st":d["source_type"],"tb":d["source_table"],"pk":d["source_pk"],"rh":d["source_row_hash"]})
            links+=1
        with engine.connect() as c:
            canon=c.execute(text("SELECT COUNT(*) FROM pi_source_aware_canonical_v704")).scalar_one()
            dgroups=c.execute(text("SELECT COUNT(DISTINCT duplicate_group) FROM pi_source_aware_clean_v704 WHERE duplicate_group IS NOT NULL")).scalar_one()
            drows=c.execute(text("SELECT COUNT(*) FROM pi_source_aware_clean_v704 WHERE duplicate_group IS NOT NULL")).scalar_one()
            with_contact=c.execute(text("SELECT COUNT(*) FROM pi_source_aware_clean_v704 WHERE jsonb_array_length(phones)>0")).scalar_one()
            props=c.execute(text("SELECT COUNT(*) FROM pi_source_aware_clean_v704 WHERE entity_type='PROPERTY_AVAILABILITY'")).scalar_one()
            reqs=c.execute(text("SELECT COUNT(*) FROM pi_source_aware_clean_v704 WHERE entity_type='REQUIREMENT'")).scalar_one()
            unknown=c.execute(text("SELECT COUNT(*) FROM pi_source_aware_clean_v704 WHERE entity_type='UNKNOWN'")).scalar_one()
            tx_unknown=c.execute(text("SELECT COUNT(*) FROM pi_source_aware_clean_v704 WHERE canonical_transaction='UNKNOWN'")).scalar_one()
        result={"version":VERSION,"mode":MODE,"status":"COMPLETE","schema_forensics":existing,"contact_recovery_audit":contact_audit,
                "entity_tables_used":[x[0] for x in plan],
                "excluded_from_property_counts":{"provenance_only":[t for t in PROVENANCE_ONLY if t in existing],"infra_only":[t for t in INFRA_ONLY if t in existing]},
                "counts":{"entity_source_rows":total_entity_rows,"clean_rows":processed,"canonical_rows":canon,"source_links":links,
                          "promotion_ready":status_counts["PROMOTION_READY"],"clean_usable":status_counts["CLEAN_USABLE"],"exceptions":status_counts["EXCEPTION"],
                          "with_valid_contact":with_contact,"property_availability":props,"requirements":reqs,"unknown_entity":unknown,
                          "unknown_transaction":tx_unknown,"strong_duplicate_groups":dgroups,"rows_in_strong_duplicate_groups":drows,"by_source":dict(source_counts)},
                "top_issues":dict(issue_counts.most_common(20)),
                "repair_principles":{"source_tables_mutated":False,"city_missing_is_not_failure":True,"price_missing_is_not_failure":True,
                                     "provenance_tables_not_counted_as_properties":True,"format_generation_tables_not_counted_as_properties":True,
                                     "contacts_recovered_from_direct_and_linked_tables":True,"strong_dedupe_only":True,"weak_duplicates_preserved_separately":True,
                                     "ai_calls_used":0,"promotion_to_master_performed":False},
                "next_gate":"AUDIT_PROMOTION_READY_THEN_BUILD_SAFE_MASTER_PROMOTION_V710"}
        with engine.begin() as c:
            c.execute(text("INSERT INTO pi_source_aware_runs_v704(version,mode,status,result) VALUES(:v,:m,'COMPLETE',CAST(:r AS JSONB))"),
                      {"v":VERSION,"m":MODE,"r":json.dumps(result,ensure_ascii=False)})
        STATE.update(status="COMPLETE",phase="COMPLETE",finished_at=datetime.now(timezone.utc).isoformat(),current_table=None,result=result)
        return result
    except Exception as exc:
        STATE.update(status="ERROR",phase="ERROR",finished_at=datetime.now(timezone.utc).isoformat(),current_table=None,last_error=f"{type(exc).__name__}: {exc}")
        return dict(STATE)
    finally:_LOCK.release()

def status(core):return STATE.get("result") or dict(STATE)
def dashboard(core):
    s=status(core);c=s.get("counts") or {}
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta http-equiv='refresh' content='10'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>Source Aware Repair 7.0.4</title>
<style>body{{font-family:Arial;background:#f5f7fb;color:#172033;margin:0}}header{{background:#102235;color:white;padding:18px}}
.wrap{{max-width:1500px;margin:auto;padding:18px}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(160px,1fr));gap:10px}}
.card{{background:white;border:1px solid #e2e8f0;border-radius:12px;padding:14px;margin-bottom:12px}}b.num{{font-size:24px;display:block}}
pre{{white-space:pre-wrap;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px;overflow:auto}}</style></head>
<body><header><b>Alliance Source-Aware Newspaper + Magazine Repair 7.0.4</b><br>
<small>Entity tables only · contact recovery · dynamic schema · strong dedupe · zero AI calls</small></header>
<div class='wrap'><div class='card'><b>{html.escape(str(s.get("status")))}</b> · Phase {html.escape(str(s.get("phase")))}</div>
<div class='grid'>
<div class='card'>Entity source rows<b class='num'>{c.get("entity_source_rows","-")}</b></div>
<div class='card'>Promotion ready<b class='num'>{c.get("promotion_ready","-")}</b></div>
<div class='card'>Clean usable<b class='num'>{c.get("clean_usable","-")}</b></div>
<div class='card'>Exceptions<b class='num'>{c.get("exceptions","-")}</b></div>
<div class='card'>With contact<b class='num'>{c.get("with_valid_contact","-")}</b></div>
<div class='card'>Canonical<b class='num'>{c.get("canonical_rows","-")}</b></div>
<div class='card'>Properties<b class='num'>{c.get("property_availability","-")}</b></div>
<div class='card'>Requirements<b class='num'>{c.get("requirements","-")}</b></div>
</div><pre>{html.escape(json.dumps(s,ensure_ascii=False,indent=2))}</pre></div></body></html>"""
def register(core):
    app=_app(core)
    if not _route_exists(app,"/api/property-brain/source-aware-repair-v704/status"):
        @app.get("/api/property-brain/source-aware-repair-v704/status")
        def _s():return status(core)
    if not _route_exists(app,"/property-brain/source-aware-repair-v704"):
        @app.get("/property-brain/source-aware-repair-v704",response_class=HTMLResponse)
        def _p():return HTMLResponse(dashboard(core))
def _runner(core):
    time.sleep(45);run_once(core)
def start(core):
    global _STARTED
    register(core)
    if not _STARTED:
        _STARTED=True
        threading.Thread(target=_runner,args=(core,),daemon=True,name="source-aware-repair-v704").start()
    return STATE
