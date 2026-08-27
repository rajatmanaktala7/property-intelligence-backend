
from __future__ import annotations
import os,re,hashlib,uuid,io
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from fastapi import APIRouter,Request,Query,HTTPException
from fastapi.responses import HTMLResponse,RedirectResponse,StreamingResponse
from sqlalchemy import create_engine,text

VERSION="4.1B-EXACT-GIRJA-REPEATED-HEADER-SPLITTER"

# ---------- shared normalization ----------

def _db_url(u):
    u=(u or "").strip()
    if u.startswith("postgres://"): return u.replace("postgres://","postgresql+psycopg://",1)
    if u.startswith("postgresql://"): return u.replace("postgresql://","postgresql+psycopg://",1)
    return u

def _wa_engine():
    u=os.getenv("WHATSAPP_DATABASE_URL","").strip()
    return create_engine(_db_url(u),pool_pre_ping=True,pool_recycle=300,connect_args={"connect_timeout":5}) if u else None

def _norm(v):
    return re.sub(r"\s+"," ",re.sub(r"[^A-Z0-9]+"," ",str(v or "").upper())).strip()

def _phones(*vals):
    out=[]
    for v in vals:
        for x in re.findall(r"(?<!\d)(?:\+?91[\s-]?)?([6-9]\d{9})(?!\d)",str(v or "")):
            if x not in out: out.append(x)
    return out

def _money_num(v, kind=None):
    if v in (None,""): return None
    if isinstance(v,(int,float)): return float(v)
    s=str(v).lower().replace(",","").replace("₹","").strip()
    m=re.search(r"(\d+(?:\.\d+)?)",s)
    if not m:return None
    n=float(m.group(1))
    if "crore" in s or re.search(r"\bcr\b",s):n*=10_000_000
    elif "lakh" in s or "lac" in s or re.search(r"\bl\b",s):n*=100_000
    elif re.search(r"\bk\b",s):n*=1_000
    return n

def _money_unit(v):
    s=str(v or "").lower()
    if "crore" in s or re.search(r"\bcr\b",s): return "Cr"
    if "lakh" in s or "lac" in s or re.search(r"\bl\b",s): return "Lakh"
    if re.search(r"\bk\b",s): return "K"
    return "INR" if s else None

def _area(raw):
    s=str(raw or "")
    pats=[
        (r"(?i)(\d+(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|sft)\b","sqft"),
        (r"(?i)(\d+(?:\.\d+)?)\s*(?:sq\.?\s*(?:yds?|yards?)|sqyds?|syds|yards|yds)\b","sq.yd"),
        (r"(?i)(\d+(?:\.\d+)?)\s*(?:acres?|acre)\b","acres"),
        (r"(?i)(\d+(?:\.\d+)?)\s*(?:sq\.?\s*m|sqm|sq\.?\s*metres?)\b","sq.m"),
    ]
    for p,u in pats:
        m=re.search(p,s)
        if m:return float(m.group(1)),u
    return None,None

def _configuration(raw):
    s=str(raw or "")
    m=re.search(r"(?i)\b(\d+(?:/\d+)?\s*BHK(?:\s*\+\s*(?:SER|SERVANT))?)\b",s)
    if m:return re.sub(r"\s+"," ",m.group(1).upper())
    if re.search(r"(?i)\bFULL\s*BLDG\b|\bFULL\s*BUILDING\b",s):return "Full building"
    return None

def _floor(raw):
    m=re.search(r"(?i)\b(ground|first|second|third|fourth|fifth|lower ground|basement)\s*floor\b",str(raw or ""))
    return (m.group(1).title()+" Floor") if m else None

def _property_type(raw):
    s=_norm(raw)
    if any(x in s for x in ["HOSPITAL","NURSING HOME"]):return "Hospital"
    if any(x in s for x in ["WAREHOUSE","GODOWN"]):return "Warehouse"
    if any(x in s for x in ["VILLA","KOTHI","BUNGALOW"]):return "Villa"
    if any(x in s for x in ["PLOT","LAND","ACRE"]):return "Plot"
    if any(x in s for x in ["SHOP","OFFICE","COMMERCIAL","BANK","SHOWROOM"]):return "Commercial"
    if any(x in s for x in ["BHK","APARTMENT","FLAT","FLOOR"]):return "Apartment"
    return "Other"

def _transaction(raw):
    s=_norm(raw)
    if any(x in s for x in ["REQUIRE","REQUIREMENT","WANTED","LOOKING FOR","NEED "]):return "REQUIREMENT"
    if any(x in s for x in ["FOR RENT","RENT ","LEASE","LEASING"]):return "Rent"
    if any(x in s for x in ["FOR SALE","SALE ","PRICE ","DEMAND ","ASKING "]):return "Sale"
    return "Unknown"

def _confidence(rec):
    score=35
    if rec.get("project_name") or rec.get("locality"):score+=15
    if rec.get("area_value"):score+=15
    if rec.get("configuration"):score+=8
    if rec.get("price_value") or rec.get("rent_value"):score+=12
    if rec.get("broker_phone"):score+=8
    if rec.get("transaction_type") in ("Sale","Rent"):score+=7
    return min(score,100)

def _canonical_key(rec):
    parts=[
        rec.get("city"),rec.get("locality"),rec.get("project_name"),rec.get("property_type"),
        rec.get("transaction_type"),rec.get("configuration"),
        rec.get("area_value"),rec.get("area_unit"),
        rec.get("price_value"),rec.get("rent_value")
    ]
    return hashlib.sha256("|".join(_norm(x) for x in parts).encode()).hexdigest()

# ---------- location/project inference ----------

KNOWN_CITY = {
    "GURGAON":"Gurugram","GURUGRAM":"Gurugram","DELHI":"Delhi","NEW DELHI":"Delhi",
    "NOIDA":"Noida","MUMBAI":"Mumbai","GOA":"Goa"
}

def _city(raw):
    up=_norm(raw)
    for k,v in KNOWN_CITY.items():
        if k in up:return v
    return None

def _project_candidate(line):
    s=re.sub(r"[\*_#`]+","",str(line or "")).strip(" -–—|:,")
    if not s:return None
    up=_norm(s)
    if len(up)<3:return None
    if re.search(r"\b(?:PRICE|RENT|DEMAND|ASKING|AREA|SIZE|CONTACT|MOB|MOBILE)\b",up):return None
    if re.search(r"\b\d+(?:/\d+)?\s*BHK\b",up):return None
    if re.search(r"\b\d+(?:\.\d+)?\s*(?:SQFT|SQ FT|SQ YD|SYDS|YARDS|ACRES?)\b",up):return None
    if up in {"INVENTORY FOR SALE","INVENTORY","AVAILABLE FOR SALE","AVAILABLE FOR RENT","FOR SALE","FOR RENT","RENTALS"}:return None
    return s

def _derive_place_context(raw):
    lines=[x.strip() for x in str(raw or "").replace("\r","\n").splitlines() if x.strip()]
    city=_city(raw)
    locality=None
    project=None
    for line in lines:
        cand=_project_candidate(line)
        if not cand:continue
        up=_norm(cand)
        if re.search(r"\b(?:DLF\s*PHASE\s*\d+|SUSHANT\s*LOK\s*\d+|SHUSHANT\s*LOK\s*\d+|SECTOR\s*\d+|SEC\s*\d+)\b",up):
            locality=cand
        elif any(k in up for k in [
            "ESTATE","TOWER","HEIGHTS","RESORT","RESORTS","APARTMENT","VILLA","VILAS","PALMS","LAGOON",
            "COURT","PARK","CREST","GALLERY","CENTRAL","STREET","EXOTICA","OMKAR","LODHA","M3M","EMAAR",
            "AIPL","UNITECH","TATA","SOBHA","AMBIENCE","RICHMOND","RIDGEWOOD","REGENCY"
        ]):
            project=cand
    return city,locality,project

# ---------- classification and splitting ----------

REQ_PAT=re.compile(r"(?i)\b(requirement|required|wanted|looking\s+for|need(?:ed)?|urgent\s+requirement|purchase\s+requirement)\b")

def classify_listing_vs_requirement(raw):
    s=str(raw or "")
    if REQ_PAT.search(s):
        if not re.search(r"(?i)\b(for sale|available for sale|for rent|available for rent|rent\s*[:\-]|price\s*[:\-]|demand\s*[:\-])",s):
            return "REQUIREMENT"
    return "LISTING"


def _is_exact_broker_locality_header(line):
    s=_norm(line)
    return bool(
        re.fullmatch(r"(?:DLF\s*PHASE\s*[124]|DLFPHASE[124]|SHUSHANT\s*LOK\s*1|SHUSHANTLOK1|SUSHANT\s*LOK\s*1|SUSHANTLOK1)",s)
        or re.fullmatch(r"(?:SECTOR|SEC)\s*[- ]?\d+[A-Z]?",s)
    )

def _is_non_specific_inventory_summary(line):
    s=_norm(line)
    return any(x in s for x in [
        "NEW FLOORS IN RESALE",
        "NEW FLOORS RESALE",
        "4 5 6 BHK KOTHI",
    ])

def split_exact_repeated_header_inventory(raw):
    """Split Girja-style repeated location inventories into one property block per repeated header."""
    lines=[x.rstrip() for x in str(raw or "").replace("\r","\n").splitlines()]
    blocks=[]
    current=[]
    started=False
    for line in lines:
        t=line.strip()
        if not t:
            continue
        if _is_non_specific_inventory_summary(t):
            if current:
                blocks.append("\n".join(current).strip())
            break
        if _is_exact_broker_locality_header(t):
            started=True
            if current:
                blocks.append("\n".join(current).strip())
            current=[t]
            continue
        if started and current:
            current.append(t)
    if current and (not blocks or blocks[-1] != "\n".join(current).strip()):
        blocks.append("\n".join(current).strip())
    good=[]
    for b in blocks:
        if not re.search(r"(?i)\b\d{2,4}\s*(?:syds|sq\s*yds|sqyds|yards|yds)\b",b):
            continue
        if not (re.search(r"(?i)\b\d+(?:/\d+)?\s*BHK\b",b) or re.search(r"(?i)\b(?:rent\s*)?\d+(?:\.\d+)?\s*(?:k|lac|lakh|l)\b",b)):
            continue
        good.append(b)
    return good

def expand_specific_rent_variants(block):
    """One row per independently priced furnishing variant for the same property block."""
    vals=[]
    for m in re.finditer(r"(?i)\b(?:rent\s*)?(\d+(?:\.\d+)?)\s*(k|lac|lakh|l)\b",block):
        raw=m.group(1)+" "+m.group(2)
        val=_money_num(raw)
        if val and val not in [x[0] for x in vals]:
            vals.append((val,raw,m.start()))
    if len(vals)<=1:
        return [block]
    lines=[x.strip() for x in block.splitlines() if x.strip()]
    header=lines[:2]
    out=[]
    for _,raw,pos in vals:
        before=block[max(0,pos-120):pos].upper()
        status=""
        if "FULLY FURNISHED" in before:
            status="FULLY FURNISHED"
        elif "SEMI FURNISHED" in before:
            status="SEMI FURNISHED"
        out.append("\n".join(header+([status] if status else [])+[f"Rent {raw}"]))
    return out

def split_multi_listing(raw):
    """
    Returns child text blocks.
    Handles:
      - emoji/numbered listing markers
      - separator blocks
      - repeated project headers with multiple config lines
      - repeated rent/price pairs
    """
    s=str(raw or "").replace("\r","\n").strip()
    if not s:return []

    repeated=split_exact_repeated_header_inventory(s)
    if len(repeated)>1:
        return repeated

    # Strong numbered markers first: 1️⃣, 2️⃣, 1., 2)
    marker=r"(?m)(?=^\s*(?:[1-9]\ufe0f?\u20e3|[1-9][\.\)]|[①②③④⑤⑥⑦⑧⑨]))"
    parts=[x.strip() for x in re.split(marker,s) if x.strip()]
    if len(parts)>1:
        header=""
        if not re.match(r"^\s*(?:[1-9]\ufe0f?\u20e3|[1-9][\.\)]|[①②③④⑤⑥⑦⑧⑨])",parts[0]):
            header=parts.pop(0)
        return [((header+"\n"+p).strip() if header else p) for p in parts]

    # Long separators
    parts=[x.strip() for x in re.split(r"\n\s*[-_=]{5,}\s*\n",s) if x.strip()]
    if len(parts)>1:return parts

    # Structured project + multiple BHK lines. Each BHK+area line becomes a child,
    # inheriting the latest project header.
    lines=[x.strip() for x in s.splitlines() if x.strip()]
    children=[]
    current_header=[]
    project=None
    footer=[]
    for i,line in enumerate(lines):
        cand=_project_candidate(line)
        is_unit=bool(re.search(r"(?i)\b\d+(?:/\d+)?\s*BHK\b",line) and _area(line)[0])
        if cand and not is_unit:
            project=cand
            current_header=[line]
            continue
        if is_unit and project:
            # include immediate next detail line if it is price/rent/remarks and not next unit/project
            block=[project,line]
            if i+1<len(lines):
                nxt=lines[i+1]
                if not (_project_candidate(nxt) and not re.search(r"(?i)\b(?:PRICE|RENT|DEMAND|ASKING)\b",nxt)) and not (re.search(r"(?i)\b\d+\s*BHK\b",nxt) and _area(nxt)[0]):
                    if re.search(r"(?i)\b(?:PRICE|RENT|DEMAND|ASKING|PARKING|RENOVATED|FURNISHED)\b",nxt):
                        block.append(nxt)
            children.append("\n".join(block))
    if len(children)>1:
        # Parent contact/footer is appended to each child at normalization stage via group raw.
        return children

    # Repeated Rent:/Price: pairs after unit labels
    pair_starts=[m.start() for m in re.finditer(r"(?im)^\s*(?:[A-Z0-9\-\/ ]{2,50})\s*[—\-:]\s*(?:Private|Govt|Pvt|Bank|Office|Shop)",s)]
    if len(pair_starts)>1:
        return [s[pair_starts[i]:pair_starts[i+1] if i+1<len(pair_starts) else None].strip() for i in range(len(pair_starts))]

    return [s]

def group_message_bursts(rows,window_seconds=180):
    """
    Group consecutive messages by sender + source group if sent within tight window.
    rows must be chronological.
    """
    out=[]
    current=None
    for r in rows:
        d=dict(r)
        sender=str(d.get("sender_phone") or d.get("sender_name") or "UNKNOWN")
        source=str(d.get("source_id") or d.get("group_name") or "UNKNOWN")
        ts=d.get("created_at")
        key=(sender,source)
        if current is None:
            current={"key":key,"start":ts,"last":ts,"rows":[d]}
            continue
        close=False
        if key==current["key"] and ts and current["last"]:
            try: close=(ts-current["last"]).total_seconds()<=window_seconds
            except: close=False
        if close:
            current["rows"].append(d);current["last"]=ts
        else:
            out.append(current);current={"key":key,"start":ts,"last":ts,"rows":[d]}
    if current:out.append(current)
    return out

# ---------- record extraction ----------

def normalize_listing(child_raw,parent_raw,meta):
    combined=(child_raw+"\n"+parent_raw).strip()
    city,locality,project=_derive_place_context(child_raw)
    pcity,plocality,pproject=_derive_place_context(parent_raw)
    city=city or pcity
    locality=locality or plocality
    project=project or pproject

    area_value,area_unit=_area(child_raw)
    if area_value is None:
        area_value,area_unit=_area(parent_raw)

    config=_configuration(child_raw) or _configuration(parent_raw)
    txn=_transaction(child_raw)
    if txn=="Unknown":txn=_transaction(parent_raw)
    if txn=="REQUIREMENT":return None

    sale_raw=None;rent_raw=None
    sm=re.search(r"(?i)(?:price|demand|asking|@)\s*[:\-@]?\s*₹?\s*([\d,.]+\s*(?:cr|crore|lac|lakh))",child_raw)
    rm=re.search(r"(?i)(?:rent)\s*[:\-]?\s*₹?\s*([\d,.]+\s*(?:lac|lakh|l|k)|[\d,]{4,7})",child_raw)
    if not rm:
        rm=re.search(r"(?i)\b([\d.]+\s*(?:lac|lakh|l|k))\s*(?:\+\s*maint|\+maint|$)",child_raw)
    if sm:sale_raw=sm.group(1)
    if rm:rent_raw=rm.group(1)

    # fallback to parent only when child did not carry its own value
    if txn=="Sale" and not sale_raw:
        m=re.search(r"(?i)(?:price|demand|asking|@)\s*[:\-@]?\s*₹?\s*([\d,.]+\s*(?:cr|crore|lac|lakh))",parent_raw)
        if m:sale_raw=m.group(1)
    if txn=="Rent" and not rent_raw:
        m=re.search(r"(?i)(?:rent)\s*[:\-]?\s*₹?\s*([\d,.]+\s*(?:lac|lakh|l|k)|[\d,]{4,7})",parent_raw)
        if m:rent_raw=m.group(1)

    price_value=_money_num(sale_raw) if txn=="Sale" else None
    rent_value=_money_num(rent_raw) if txn=="Rent" else None
    phones=_phones(meta.get("sender_phone"),combined)
    broker_name=str(meta.get("sender_name") or "").strip()

    rec={
        "city":city,"locality":locality,"project_name":project,
        "property_type":_property_type(combined),
        "transaction_type":txn if txn in ("Sale","Rent") else "Unknown",
        "configuration":config,
        "area_value":area_value,"area_unit":area_unit,
        "price_value":price_value,"price_unit":_money_unit(sale_raw),
        "rent_value":rent_value,"rent_unit":_money_unit(rent_raw),
        "floor":_floor(combined),
        "broker_name":broker_name,"broker_phone":" | ".join(phones),
        "source_group":str(meta.get("group_name") or ""),
        "raw_message":parent_raw,
    }
    rec["confidence"]=_confidence(rec)
    rec["canonical_key"]=_canonical_key(rec)
    return rec

def _ensure(engine):
    with engine.begin() as c:
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS pi_whatsapp_normalized_generation(
          id BIGSERIAL PRIMARY KEY,
          generation_id UUID UNIQUE NOT NULL,
          started_at TIMESTAMPTZ DEFAULT NOW(),
          completed_at TIMESTAMPTZ,
          raw_messages INTEGER DEFAULT 0,
          bursts INTEGER DEFAULT 0,
          extracted_children INTEGER DEFAULT 0,
          canonical_rows INTEGER DEFAULT 0,
          requirements_filtered INTEGER DEFAULT 0,
          duplicates_merged INTEGER DEFAULT 0,
          status TEXT DEFAULT 'RUNNING'
        )"""))
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS pi_whatsapp_normalized_property(
          id BIGSERIAL PRIMARY KEY,
          generation_id UUID NOT NULL,
          canonical_key TEXT NOT NULL,
          record_code TEXT NOT NULL,
          listing_group_id UUID NOT NULL,
          city TEXT,
          locality TEXT,
          project_name TEXT,
          property_type TEXT,
          transaction_type TEXT,
          configuration TEXT,
          area_value NUMERIC(16,2),
          area_unit TEXT,
          price_value NUMERIC(18,2),
          price_unit TEXT,
          rent_value NUMERIC(18,2),
          rent_unit TEXT,
          floor TEXT,
          broker_name TEXT,
          broker_phone TEXT,
          source_group TEXT,
          source_count INTEGER DEFAULT 1,
          raw_message TEXT,
          confidence NUMERIC(6,2),
          verification TEXT DEFAULT 'UNVERIFIED',
          captured_at TIMESTAMPTZ DEFAULT NOW(),
          UNIQUE(generation_id,canonical_key)
        )"""))

def _serialize(r):
    d=dict(r)
    for k,v in list(d.items()):
        if isinstance(v,uuid.UUID):d[k]=str(v)
        elif hasattr(v,"isoformat"):d[k]=v.isoformat()
    return d

def register(core):
    app=core.app;engine=core.engine;need_login=core.need_login;page_role_or_redirect=core.page_role_or_redirect
    router=APIRouter()

    @router.get("/api/v40/status")
    def status(req:Request):
        need_login(req)
        return {
            "version":VERSION,"status":"OK","startup_db_work":False,
            "newspaper_source_mutation":False,
            "whatsapp_pipeline":"GROUP→CLASSIFY→SPLIT→NORMALIZE→DEDUPE",
            "whatsapp_database":"/whatsapp-property-database-v40",
            "newspaper_database":"/newspaper-property-database-v40",
            "export":"/whatsapp-intelligence/export-v40.xlsx"
        }

    @router.post("/api/v40/setup")
    def setup(req:Request):
        need_login(req);_ensure(engine)
        return {"version":VERSION,"status":"READY"}

    @router.post("/api/v40/whatsapp/rebuild")
    def rebuild(req:Request,limit:int=Query(10000,ge=1,le=40000)):
        need_login(req);_ensure(engine)
        w=_wa_engine()
        if w is None:raise HTTPException(503,"WHATSAPP_DATABASE_URL not configured")

        with w.connect() as c:
            rows=c.execute(text("""
              SELECT m.message_id,m.raw_text,m.created_at,m.sender_name,m.sender_phone,
                     m.source_id,s.group_name
              FROM wa_messages m
              LEFT JOIN wa_sources s ON s.source_id=m.source_id
              ORDER BY m.created_at ASC NULLS LAST,m.id ASC
              LIMIT :lim
            """),{"lim":limit}).mappings().all()

        bursts=group_message_bursts(rows,180)
        gen=uuid.uuid4()
        with engine.begin() as c:
            c.execute(text("INSERT INTO pi_whatsapp_normalized_generation(generation_id,status) VALUES(:g,'RUNNING')"),{"g":gen})

        canonical={}
        requirements=0
        child_count=0

        for burst in bursts:
            parent="\n".join(str(x.get("raw_text") or "") for x in burst["rows"] if str(x.get("raw_text") or "").strip())
            if not parent.strip():continue

            if classify_listing_vs_requirement(parent)=="REQUIREMENT":
                requirements+=1
                continue

            listing_group=uuid.uuid4()
            meta=burst["rows"][-1]
            base_children=split_multi_listing(parent)
            children=[]
            for child_block in base_children:
                children.extend(expand_specific_rent_variants(child_block))
            child_count+=len(children)

            for child in children:
                rec=normalize_listing(child,parent,meta)
                if not rec:continue
                # Reject very low-information child blobs from property DB.
                if not (rec.get("project_name") or rec.get("locality")):
                    if not rec.get("area_value") or not (rec.get("price_value") or rec.get("rent_value")):
                        continue
                k=rec["canonical_key"]
                if k not in canonical:
                    canonical[k]=rec|{
                        "listing_group_id":listing_group,
                        "phones":set([p for p in rec["broker_phone"].split(" | ") if p]),
                        "groups":set([rec["source_group"]] if rec["source_group"] else []),
                        "source_count":1
                    }
                else:
                    x=canonical[k]
                    x["phones"].update([p for p in rec["broker_phone"].split(" | ") if p])
                    if rec["source_group"]:x["groups"].add(rec["source_group"])
                    if not x["broker_name"] and rec["broker_name"]:x["broker_name"]=rec["broker_name"]
                    x["source_count"]+=1
                    x["confidence"]=max(x["confidence"],rec["confidence"])

        with engine.begin() as c:
            for k,x in canonical.items():
                c.execute(text("""
                  INSERT INTO pi_whatsapp_normalized_property(
                    generation_id,canonical_key,record_code,listing_group_id,
                    city,locality,project_name,property_type,transaction_type,configuration,
                    area_value,area_unit,price_value,price_unit,rent_value,rent_unit,floor,
                    broker_name,broker_phone,source_group,source_count,raw_message,confidence
                  ) VALUES(
                    :g,:k,:record_code,:listing_group_id,
                    :city,:locality,:project_name,:property_type,:transaction_type,:configuration,
                    :area_value,:area_unit,:price_value,:price_unit,:rent_value,:rent_unit,:floor,
                    :broker_name,:broker_phone,:source_group,:source_count,:raw_message,:confidence
                  )
                """),{
                    "g":gen,"k":k,"record_code":"WAP-"+k[:10].upper(),"listing_group_id":x["listing_group_id"],
                    "city":x["city"],"locality":x["locality"],"project_name":x["project_name"],
                    "property_type":x["property_type"],"transaction_type":x["transaction_type"],
                    "configuration":x["configuration"],"area_value":x["area_value"],"area_unit":x["area_unit"],
                    "price_value":x["price_value"],"price_unit":x["price_unit"],"rent_value":x["rent_value"],
                    "rent_unit":x["rent_unit"],"floor":x["floor"],"broker_name":x["broker_name"],
                    "broker_phone":" | ".join(sorted(x["phones"])),"source_group":" | ".join(sorted(x["groups"])),
                    "source_count":x["source_count"],"raw_message":x["raw_message"],"confidence":x["confidence"]
                })
            c.execute(text("""
              UPDATE pi_whatsapp_normalized_generation
              SET completed_at=NOW(),raw_messages=:raw,bursts=:bursts,extracted_children=:children,
                  canonical_rows=:rows,requirements_filtered=:req,
                  duplicates_merged=:dup,status='COMPLETED'
              WHERE generation_id=:g
            """),{
                "raw":len(rows),"bursts":len(bursts),"children":child_count,"rows":len(canonical),
                "req":requirements,"dup":max(child_count-len(canonical),0),"g":gen
            })

        return {
            "status":"OK","generation_id":str(gen),"raw_messages":len(rows),"bursts":len(bursts),
            "extracted_children":child_count,"canonical_rows":len(canonical),
            "requirements_filtered":requirements,"duplicates_merged":max(child_count-len(canonical),0)
        }

    @router.get("/api/v40/whatsapp/rows")
    def wa_rows(req:Request,q:str="",limit:int=Query(1000,ge=1,le=3000)):
        need_login(req);_ensure(engine)
        p={"lim":limit};where=""
        if q.strip():
            where="""AND (
              COALESCE(city,'') ILIKE :q OR COALESCE(locality,'') ILIKE :q OR
              COALESCE(project_name,'') ILIKE :q OR COALESCE(configuration,'') ILIKE :q OR
              COALESCE(broker_phone,'') ILIKE :q OR COALESCE(source_group,'') ILIKE :q
            )"""
            p["q"]="%"+q.strip()+"%"
        with engine.connect() as c:
            gen=c.execute(text("""SELECT generation_id FROM pi_whatsapp_normalized_generation
              WHERE status='COMPLETED' ORDER BY completed_at DESC NULLS LAST,id DESC LIMIT 1""")).scalar()
            if not gen:return {"status":"REBUILD_REQUIRED","count":0,"rows":[]}
            p["g"]=gen
            rows=c.execute(text(f"""SELECT * FROM pi_whatsapp_normalized_property
              WHERE generation_id=:g {where} ORDER BY id DESC LIMIT :lim"""),p).mappings().all()
        return {"status":"OK","generation_id":str(gen),"count":len(rows),"rows":[_serialize(r) for r in rows]}

    @router.get("/api/v40/newspaper/rows")
    def news_rows(req:Request,q:str="",limit:int=Query(1000,ge=1,le=3000)):
        need_login(req)
        p={"lim":limit};where=""
        if q.strip():
            where="""AND (
              COALESCE(locality,'') ILIKE :q OR COALESCE(configuration_details,'') ILIKE :q OR
              COALESCE(contact_person,'') ILIKE :q OR COALESCE(phone_numbers,'') ILIKE :q OR
              COALESCE(notes,'') ILIKE :q
            )"""
            p["q"]="%"+q.strip()+"%"
        with engine.connect() as c:
            rows=c.execute(text(f"""
              WITH n AS (
                SELECT p.*,
                  regexp_replace(upper(COALESCE(locality,'')),'[^A-Z0-9]+','','g') k_loc,
                  regexp_replace(upper(COALESCE(area,'')),'[^A-Z0-9]+','','g') k_area,
                  regexp_replace(upper(COALESCE(configuration_details,'')),'[^A-Z0-9]+','','g') k_cfg,
                  regexp_replace(upper(COALESCE(price,'')),'[^A-Z0-9]+','','g') k_price,
                  regexp_replace(COALESCE(phone_numbers,''),'[^0-9]+','','g') k_phone
                FROM pi_newspaper_properties p
              ), ranked AS (
                SELECT n.*,ROW_NUMBER() OVER(
                  PARTITION BY k_loc,k_area,k_cfg,k_price,k_phone
                  ORDER BY CASE WHEN UPPER(COALESCE(verification,''))='VERIFIED' THEN 0 ELSE 1 END,id DESC
                ) rn FROM n
              )
              SELECT * FROM ranked WHERE rn=1 {where}
              ORDER BY id DESC LIMIT :lim
            """),p).mappings().all()
        return {"status":"OK","count":len(rows),"rows":[_serialize(r) for r in rows],"dedupe":"NON_DESTRUCTIVE"}

    @router.get("/whatsapp-intelligence/export-v40.xlsx")
    def export_xlsx(req:Request):
        need_login(req);_ensure(engine)
        try:
            from openpyxl import Workbook
        except Exception as e:
            raise HTTPException(500,f"openpyxl unavailable: {e}")
        with engine.connect() as c:
            gen=c.execute(text("""SELECT generation_id FROM pi_whatsapp_normalized_generation
              WHERE status='COMPLETED' ORDER BY completed_at DESC NULLS LAST,id DESC LIMIT 1""")).scalar()
            if not gen:raise HTTPException(409,"Run V4.0 WhatsApp rebuild first.")
            rows=c.execute(text("""SELECT * FROM pi_whatsapp_normalized_property
              WHERE generation_id=:g ORDER BY id"""),{"g":gen}).mappings().all()
        wb=Workbook();ws=wb.active;ws.title="WhatsApp Properties"
        headers=["record_code","city","locality","project_name","property_type","transaction_type","configuration",
                 "area_value","area_unit","price_value","price_unit","rent_value","rent_unit","floor",
                 "broker_name","broker_phone","source_group","source_count","confidence","verification",
                 "listing_group_id","raw_message"]
        ws.append(headers)
        for r in rows:
            d=dict(r)
            vals=[]
            for h in headers:
                v=d.get(h)
                # Critical fix: UUID and other non-Excel-native values are converted to strings.
                if isinstance(v,uuid.UUID):v=str(v)
                elif hasattr(v,"isoformat"):v=v.isoformat()
                vals.append(v)
            ws.append(vals)
        bio=io.BytesIO();wb.save(bio);bio.seek(0)
        return StreamingResponse(bio,media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition":'attachment; filename="whatsapp_property_database_v40.xlsx"'})

    @router.get("/whatsapp-property-database-v40",response_class=HTMLResponse)
    def wa_page(req:Request):
        if not page_role_or_redirect(req):return RedirectResponse("/login",303)
        return HTMLResponse(PAGE.replace("__SRC__","whatsapp").replace("__TITLE__","WhatsApp Property Database"))

    @router.get("/newspaper-property-database-v40",response_class=HTMLResponse)
    def news_page(req:Request):
        if not page_role_or_redirect(req):return RedirectResponse("/login",303)
        return HTMLResponse(PAGE.replace("__SRC__","newspaper").replace("__TITLE__","Newspaper Property Database"))

    app.include_router(router)
    return router

PAGE=r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title><style>
body{margin:0;font-family:Arial;background:#efe4d2;color:#2d261f}header{background:#5d4937;color:#fff;padding:16px 20px}.wrap{max-width:1750px;margin:auto;padding:18px}
.card{background:#fffdf9;border:1px solid #dccdbb;border-radius:14px;padding:16px;margin-bottom:14px}.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}
.btn,button{padding:9px 12px;border:0;border-radius:8px;background:#6c543f;color:#fff;font-weight:800;text-decoration:none;cursor:pointer}input{padding:9px;border:1px solid #d8c8b4;border-radius:8px;min-width:330px}
.tablewrap{overflow:auto;max-height:72vh;border:1px solid #ddcfbd;border-radius:10px}table{width:100%;border-collapse:collapse;min-width:1750px;background:#fff}
th,td{padding:9px;border-bottom:1px solid #eee0ce;text-align:left;font-size:12px;vertical-align:top}th{background:#f7ecdf;position:sticky;top:0}.phone{font-weight:900}.raw{max-width:340px;white-space:pre-wrap}
</style></head><body><header><b>__TITLE__</b> · Newspaper-Reference Format V4.0</header><div class=wrap>
<div class=card><div class=toolbar><a class=btn href="/workspace">← Dashboard</a><a class=btn href="/newspaper-v83">Newspaper Upload</a><a class=btn href="/whatsapp-live">WhatsApp Live</a><a class=btn href="/whatsapp-intelligence/export-v40.xlsx">Export Excel</a></div></div>
<div class=card><div class=toolbar><input id=q placeholder="Search city, locality, project, configuration, phone, source"><button onclick=load()>Search</button><button id=rebuild onclick=rebuildNow()>Rebuild WhatsApp Database</button></div><div id=summary></div>
<div class=tablewrap><table><thead id=head></thead><tbody id=rows></tbody></table></div></div></div>
<script>
const SRC="__SRC__";if(SRC==="newspaper")document.getElementById("rebuild").style.display="none";
const E=v=>String(v??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m]));
async function J(u,o={}){let r=await fetch(u,{credentials:"include",...o});let t=await r.text();let d;try{d=JSON.parse(t)}catch(e){d={detail:t}};if(!r.ok)throw Error(d.detail||t);return d}
async function rebuildNow(){let d=await J("/api/v40/whatsapp/rebuild",{method:"POST"});alert(JSON.stringify(d,null,2));load()}
async function load(){
 let d=await J("/api/v40/"+SRC+"/rows?q="+encodeURIComponent(q.value||"")+"&limit=1500");summary.textContent=(d.count||0)+" clean canonical records";
 if(SRC==="whatsapp"){
  head.innerHTML="<tr><th>Record</th><th>City</th><th>Locality</th><th>Project / Building</th><th>Type</th><th>Txn</th><th>Configuration</th><th>Area</th><th>Sale Price</th><th>Rent</th><th>Floor</th><th>Broker</th><th>Phone</th><th>Source Group</th><th>Confidence</th><th>Verification</th></tr>";
  rows.innerHTML=(d.rows||[]).map(x=>`<tr><td>${E(x.record_code)}</td><td>${E(x.city)}</td><td>${E(x.locality)}</td><td><b>${E(x.project_name)}</b></td><td>${E(x.property_type)}</td><td>${E(x.transaction_type)}</td><td>${E(x.configuration)}</td><td>${E(x.area_value)} ${E(x.area_unit)}</td><td>${E(x.price_value)}</td><td>${E(x.rent_value)}</td><td>${E(x.floor)}</td><td>${E(x.broker_name)}</td><td class=phone>${E(x.broker_phone)}</td><td>${E(x.source_group)}</td><td>${E(x.confidence)}</td><td>${E(x.verification)}</td></tr>`).join("");
 }else{
  head.innerHTML="<tr><th>Record</th><th>Type</th><th>Location / Project</th><th>Area</th><th>Configuration</th><th>Price</th><th>Agency</th><th>Contact</th><th>Phone</th><th>Description</th><th>Verification</th><th>Source</th></tr>";
  rows.innerHTML=(d.rows||[]).map(x=>`<tr><td>${E(x.record_id||x.id)}</td><td>${E(x.lead_type)}</td><td><b>${E(x.locality)}</b></td><td>${E(x.area)}</td><td>${E(x.configuration_details)}</td><td>${E(x.price)}</td><td>${E(x.agency_brand)}</td><td>${E(x.contact_person)}</td><td class=phone>${E(x.phone_numbers)}</td><td class=raw>${E(x.notes)}</td><td>${E(x.verification)}</td><td>${E(x.source)}</td></tr>`).join("");
 }
}load();
</script></body></html>"""
