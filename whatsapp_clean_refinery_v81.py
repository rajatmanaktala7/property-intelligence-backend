import re, hashlib, uuid
from sqlalchemy import text

PHONE_RE = re.compile(r"(?<!\d)(?:(?:\+?91)[\s-]?|0)?[6-9]\d(?:[\s-]?\d){8}(?!\d)")
AREA_PATTERNS = [
    (re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|sft|square\s*feet)\b", re.I), 1.0, "sqft"),
    (re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:sq\.?\s*yd|sqyd|sq\.?\s*yard|gaj|yards?)\b", re.I), 9.0, "sq yd"),
    (re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:sq\.?\s*m|sqm|sq mtrs?)\b", re.I), 10.7639, "sqm"),
]
PROPERTY_TYPES = [
    ("Warehouse / Industrial", ["warehouse","industrial","factory","godown"]),
    ("Commercial Showroom", ["showroom"]),
    ("Commercial Shop", ["shop"]),
    ("Office", ["office"]),
    ("Plot / Land", ["plot","land"]),
    ("Farmhouse", ["farmhouse","farm house"]),
    ("Banquet", ["banquet"]),
    ("Hotel", ["hotel","resort"]),
    ("Restaurant", ["restaurant","restro"]),
    ("Cafe", ["cafe"]),
    ("Lounge", ["lounge"]),
    ("Club", ["club"]),
    ("Guest House", ["guest house","guesthouse"]),
    ("Independent House / Villa", ["villa","kothi","independent house"]),
    ("Apartment", ["apartment","flat","bhk"]),
    ("Commercial / Retail", ["retail","commercial"]),
]
LOCATIONS = [
    "Greater Kailash 1","Greater Kailash 2","Greater Kailash","Defence Colony","Vasant Kunj",
    "Vasant Vihar","Saket","Hauz Khas","Green Park","South Extension","East of Kailash",
    "Kailash Colony","Connaught Place","Gurugram","Gurgaon","Noida","Greater Noida","Faridabad",
    "Ghaziabad","Vaishali","Kaushambi","Pitampura","Karol Bagh","Rohini","Janakpuri","Dwarka",
    "Surajkund","Hapur","Prithla","Greenfield Colony","Sohna Road","Mathura Road","Alaknanda",
    "Lajpat Nagar","Rajouri Garden","Punjabi Bagh","Cyber Hub","DLF Phase 3","Panjim","Dona Paula"
]
PROPERTY_WORDS = [
    "shop","showroom","office","warehouse","industrial","factory","plot","land","farmhouse","farm house",
    "villa","apartment","flat","floor","building","hotel","resort","banquet","restaurant","restro","cafe",
    "lounge","club","guest house","retail","commercial","residential","bhk","preleased","pre-leased"
]
REQ_WORDS = ["require","required","requirement","wanted","looking for","need","client looking","buyer looking","tenant looking"]
OFFER_WORDS = ["sale","rent","lease","available","selling","asking","demand","preleased","pre-leased","auction","reserve price"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS v81_wa_clean_properties(
  id BIGSERIAL PRIMARY KEY,
  clean_property_id TEXT UNIQUE NOT NULL,
  source_message_id UUID,
  source_id UUID,
  source_group TEXT,
  source_account TEXT,
  raw_message TEXT NOT NULL,
  raw_property_text TEXT NOT NULL,
  contact_numbers TEXT,
  contact_person TEXT,
  agency_brand TEXT,
  transaction_type TEXT,
  property_type TEXT,
  city TEXT,
  locality TEXT,
  area_sqft NUMERIC(14,2),
  area_display TEXT,
  price_inr NUMERIC(16,2),
  price_display TEXT,
  floor TEXT,
  configuration_details TEXT,
  notes TEXT,
  completeness TEXT DEFAULT 'Partial',
  extraction_confidence NUMERIC(5,2),
  fingerprint TEXT UNIQUE,
  record_status TEXT DEFAULT 'ACTIVE',
  extractor TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS v81_wa_clean_requirements(
  id BIGSERIAL PRIMARY KEY,
  clean_requirement_id TEXT UNIQUE NOT NULL,
  source_requirement_id TEXT,
  source_message_id UUID,
  source_id UUID,
  source_group TEXT,
  source_account TEXT,
  raw_message TEXT NOT NULL,
  contact_numbers TEXT,
  contact_person TEXT,
  client_name TEXT,
  transaction_type TEXT,
  property_type TEXT,
  city TEXT,
  preferred_location TEXT,
  minimum_area_sqft NUMERIC(14,2),
  maximum_area_sqft NUMERIC(14,2),
  budget_min_inr NUMERIC(16,2),
  budget_max_inr NUMERIC(16,2),
  floor_preference TEXT,
  frontage_requirement TEXT,
  suitable_category TEXT,
  completeness TEXT DEFAULT 'Partial',
  extraction_confidence NUMERIC(5,2),
  fingerprint TEXT UNIQUE,
  record_status TEXT DEFAULT 'ACTIVE',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS v81_refinery_runs(
  id BIGSERIAL PRIMARY KEY,
  run_id TEXT UNIQUE NOT NULL,
  started_at TIMESTAMPTZ DEFAULT NOW(),
  completed_at TIMESTAMPTZ,
  messages_scanned INTEGER DEFAULT 0,
  clean_properties INTEGER DEFAULT 0,
  clean_requirements INTEGER DEFAULT 0,
  duplicates_skipped INTEGER DEFAULT 0,
  fragments_rejected INTEGER DEFAULT 0,
  error_message TEXT
);
"""

def init_clean_db(engine):
    with engine.begin() as c:
        for stmt in [x.strip() for x in SCHEMA.split(";") if x.strip()]:
            c.execute(text(stmt))

def norm(v):
    return re.sub(r"\s+"," ",str(v or "").replace("\u00a0"," ")).strip()

def phone_numbers(txt):
    out=[]
    for m in PHONE_RE.finditer(txt or ""):
        d=re.sub(r"\D","",m.group(0))
        if len(d)==11 and d.startswith("0"): d=d[1:]
        if len(d)==12 and d.startswith("91"): d=d[2:]
        if len(d)==10 and d[0] in "6789":
            p="+91"+d
            if p not in out: out.append(p)
    return out

def area_values(txt):
    out=[]
    for pat,mul,unit in AREA_PATTERNS:
        for m in pat.finditer(txt or ""):
            try:
                raw=float(m.group(1).replace(",",""))
                out.append((round(raw*mul,2),m.group(0)))
            except Exception:
                pass
    return out

def contextual_price(txt):
    labels = ["reserve price","sale demand","selling price","sale price","asking price","asking","demand","asking rent","monthly rent","rent","price"]
    label="|".join(re.escape(x) for x in labels)
    pats=[
        rf"(?:{label})\s*[-:=@]?\s*(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?)\s*(k|l|lac|lakh|lakhs|cr|crore|crores)?\b",
        rf"(?:₹|rs\.?|inr)\s*(\d[\d,]*(?:\.\d+)?)\s*(k|l|lac|lakh|lakhs|cr|crore|crores)?\b",
    ]
    for p in pats:
        m=re.search(p,txt or "",re.I)
        if not m: 
            continue
        raw=m.group(1).strip()
        suffix=(m.group(2) or "").lower()
        if suffix in {"cr","crore","crores"} and re.fullmatch(r"\d{1,2},\d{1,2}",raw):
            raw=raw.replace(",",".")
        else:
            raw=raw.replace(",","")
        try:
            v=float(raw)
        except:
            continue
        if suffix=="k":v*=1000
        elif suffix in {"l","lac","lakh","lakhs"}:v*=100000
        elif suffix in {"cr","crore","crores"}:v*=10000000
        return v,m.group(0)
    return None,""

def property_type(txt):
    low=(txt or "").lower()
    for out,keys in PROPERTY_TYPES:
        if any(k in low for k in keys):
            return out
    return "UNKNOWN"

def transaction(txt):
    low=(txt or "").lower()
    sale=any(x in low for x in ["for sale","sale","selling","buy","buyer","purchase","auction","reserve price"])
    rent=any(x in low for x in ["for rent","rent","lease","letting","asking rent"])
    if sale and rent:return "SALE_RENT"
    if sale:return "SALE"
    if rent:return "RENT"
    return "UNKNOWN"

def locality(txt):
    low=(txt or "").lower()
    found=[]
    aliases={"gurgaon":"Gurugram","gk 1":"Greater Kailash 1","gk-1":"Greater Kailash 1","gk1":"Greater Kailash 1",
             "gk 2":"Greater Kailash 2","gk-2":"Greater Kailash 2","gk2":"Greater Kailash 2"}
    for a,b in aliases.items():
        if a in low and b not in found:
            found.append(b)
    for loc in LOCATIONS:
        if loc.lower() in low and loc not in found:
            found.append(loc)
    for m in re.finditer(r"\b(?:sector|sec)[\s\-]*([0-9]{1,3}[a-z]?)\b",txt or "",re.I):
        val="Sector "+m.group(1).upper()
        if val not in found:
            found.append(val)
    return ", ".join(found) if found else "UNKNOWN"

def floor_value(txt):
    low=(txt or "").lower()
    for out,keys in [
        ("Lower Ground Floor",["lower ground","lgf"]),
        ("Ground Floor",["ground floor","gf"]),
        ("First Floor",["first floor","1st floor"]),
        ("Second Floor",["second floor","2nd floor"]),
        ("Third Floor",["third floor","3rd floor"]),
        ("Basement",["basement"])
    ]:
        if any(k in low for k in keys):
            return out
    return "UNKNOWN"

def meaningful_property(txt):
    low=(txt or "").lower()
    prop=any(x in low for x in PROPERTY_WORDS)
    detail=bool(area_values(txt)) or any(x in low for x in OFFER_WORDS) or contextual_price(txt)[0] is not None
    return prop and detail

def meaningful_requirement(txt):
    low=(txt or "").lower()
    return any(x in low for x in REQ_WORDS) and (any(x in low for x in PROPERTY_WORDS) or bool(area_values(txt)) or locality(txt)!="UNKNOWN")

def split_property_entities(raw):
    raw=str(raw or "").replace("\r\n","\n").replace("\r","\n")
    lines=raw.splitlines()
    marker=re.compile(r"^\s*(?:property\s*)?(\d{1,3})\s*(?:[\.\)\-:]|>>|\s{2,})\s*(.+)$",re.I)
    chunks=[]; current=[]; started=False
    for line in lines:
        t=norm(line)
        if not t:
            continue
        m=marker.match(t)
        if m:
            if current:
                chunks.append("\n".join(current))
            current=[m.group(2)]
            started=True
        elif started:
            current.append(t)
    if current:
        chunks.append("\n".join(current))
    good=[norm(x) for x in chunks if meaningful_property(x)]
    if len(good)>=2:
        return good

    parts=re.split(r"(?i)(?=(?:>>\s*)?OPTION\s*\d+\b)",raw)
    good=[norm(x) for x in parts if meaningful_property(x)]
    if len(good)>=2:
        return good

    parts=re.split(r"(?i)(?=(?:FOR\s+SALE|FOR\s+RENT|AVAILABLE\s+FOR\s+SALE|AVAILABLE\s+FOR\s+RENT)\s*[:\-])",raw)
    good=[norm(x) for x in parts if meaningful_property(x)]
    if len(good)>=2:
        return good

    return [norm(raw)] if meaningful_property(raw) else []

def contact_context(raw):
    ph=" / ".join(phone_numbers(raw))
    lines=[norm(x) for x in str(raw or "").splitlines() if norm(x)]
    agency="";person=""
    for line in reversed(lines[-8:]):
        if len(line)<120 and any(x in line.lower() for x in ["properties","property & builders","realty","associates","estate","realtors"]):
            agency=line
            break
    for line in reversed(lines[-8:]):
        m=re.search(r"(?:contact|call|regards|broker|owner)\s*[:\-@]?\s*([A-Za-z][A-Za-z .]{2,40})",line,re.I)
        if m:
            person=norm(m.group(1))
            break
    return person,agency,ph

def fingerprint(values):
    return hashlib.sha256("|".join(str(x or "").lower().strip() for x in values).encode()).hexdigest()

def confidence(d):
    score=45
    if d.get("locality") not in ("","UNKNOWN",None):score+=12
    if d.get("property_type") not in ("","UNKNOWN",None):score+=10
    if d.get("area_sqft"):score+=10
    if d.get("price_inr"):score+=8
    if d.get("contact_numbers"):score+=8
    if d.get("transaction_type") not in ("","UNKNOWN",None):score+=7
    return min(score,99)

def rebuild_clean_database(engine):
    init_clean_db(engine)
    run_id="V811RUN-"+uuid.uuid4().hex[:10].upper()
    stats={"messages":0,"properties":0,"requirements":0,"duplicates":0,"fragments":0}

    with engine.begin() as c:
        c.execute(text("INSERT INTO v81_refinery_runs(run_id) VALUES(:r)"),{"r":run_id})
        c.execute(text("DELETE FROM v81_wa_clean_properties"))
        c.execute(text("DELETE FROM v81_wa_clean_requirements"))

        messages=c.execute(text("""
          SELECT m.message_id,m.source_id,m.raw_text,m.sender_name,m.sender_phone,
                 s.group_name,s.source_name
          FROM wa_messages m
          LEFT JOIN wa_sources s ON s.source_id=m.source_id
          WHERE COALESCE(m.raw_text,'')<>''
          ORDER BY m.id
        """)).mappings().all()

        seen=set()
        for m in messages:
            raw=m["raw_text"] or ""
            stats["messages"]+=1
            if meaningful_requirement(raw):
                continue
            entities=split_property_entities(raw)
            if not entities:
                stats["fragments"]+=1
                continue

            person,agency,parent_phones=contact_context(raw)
            for idx,seg in enumerate(entities,1):
                av=area_values(seg)
                area_sqft=av[0][0] if av else None
                area_display=av[0][1] if av else ""
                price,price_display=contextual_price(seg)
                loc=locality(seg)
                pt=property_type(seg)
                tx=transaction(seg)
                localphones=" / ".join(phone_numbers(seg)) or parent_phones
                row={"locality":loc,"property_type":pt,"transaction_type":tx,"area_sqft":area_sqft,
                     "floor":floor_value(seg),"price_inr":price,"raw_property_text":seg,"contact_numbers":localphones}
                fp=fingerprint([loc,pt,tx,area_sqft,row["floor"],price,seg])
                if fp in seen:
                    stats["duplicates"]+=1
                    continue
                seen.add(fp)
                cid="WAC-"+hashlib.sha1((str(m["message_id"])+"|"+str(idx)+"|"+fp).encode()).hexdigest()[:10].upper()
                c.execute(text("""
                  INSERT INTO v81_wa_clean_properties(
                    clean_property_id,source_message_id,source_id,source_group,source_account,raw_message,
                    raw_property_text,contact_numbers,contact_person,agency_brand,transaction_type,property_type,
                    city,locality,area_sqft,area_display,price_inr,price_display,floor,configuration_details,
                    notes,completeness,extraction_confidence,fingerprint,extractor
                  ) VALUES(
                    :id,:mid,:sid,:grp,:acct,:raw,:seg,:phones,:person,:agency,:tx,:ptype,:city,:loc,:area,:ad,
                    :price,:pd,:floor,:cfg,:notes,:comp,:conf,:fp,'Newspaper-style WhatsApp refinery'
                  )
                """),{
                  "id":cid,"mid":m["message_id"],"sid":m["source_id"],"grp":m["group_name"] or "",
                  "acct":m["source_name"] or "","raw":raw,"seg":seg,"phones":localphones,"person":person,
                  "agency":agency,"tx":tx,"ptype":pt,"city":"Delhi NCR" if loc!="UNKNOWN" else "UNKNOWN",
                  "loc":loc,"area":area_sqft,"ad":area_display,"price":price,"pd":price_display,
                  "floor":floor_value(seg),"cfg":seg,"notes":"","comp":"Complete" if confidence(row)>=85 else "Partial",
                  "conf":confidence(row),"fp":fp
                })
                stats["properties"]+=1

        reqs=c.execute(text("""
          SELECT r.*,s.group_name,s.source_name
          FROM wa_requirements r
          LEFT JOIN wa_sources s ON s.source_id=r.source_id
          WHERE COALESCE(r.status,'ACTIVE')='ACTIVE'
          ORDER BY r.id
        """)).mappings().all()

        rseen=set()
        for r in reqs:
            fp=fingerprint([
                r["raw_text"],r["property_type"],r["transaction_type"],r["preferred_locations"],
                r["minimum_area_sqft"],r["maximum_area_sqft"],r["budget_min_inr"],r["budget_max_inr"],
                r["contact_phone"]
            ])
            if fp in rseen:
                stats["duplicates"]+=1
                continue
            rseen.add(fp)
            rid="WARC-"+hashlib.sha1((r["wa_requirement_id"]+"|"+fp).encode()).hexdigest()[:10].upper()
            c.execute(text("""
              INSERT INTO v81_wa_clean_requirements(
                clean_requirement_id,source_requirement_id,source_message_id,source_id,source_group,source_account,
                raw_message,contact_numbers,contact_person,client_name,transaction_type,property_type,city,
                preferred_location,minimum_area_sqft,maximum_area_sqft,budget_min_inr,budget_max_inr,
                floor_preference,frontage_requirement,suitable_category,completeness,extraction_confidence,fingerprint
              ) VALUES(
                :id,:srid,:mid,:sid,:grp,:acct,:raw,:phones,:person,:client,:tx,:ptype,:city,:loc,:mina,:maxa,
                :bmin,:bmax,:floor,:frontage,:cat,:comp,:conf,:fp
              )
            """),{
              "id":rid,"srid":r["wa_requirement_id"],"mid":r["message_id"],"sid":r["source_id"],
              "grp":r["group_name"] or "","acct":r["source_name"] or "","raw":r["raw_text"],
              "phones":r["contact_phone"] or " / ".join(phone_numbers(r["raw_text"] or "")),
              "person":r["contact_name"] or "","client":r["client_name"] or "",
              "tx":r["transaction_type"] or "UNKNOWN","ptype":r["property_type"] or "UNKNOWN",
              "city":r["city"] or "UNKNOWN","loc":r["preferred_locations"] or "UNKNOWN",
              "mina":r["minimum_area_sqft"],"maxa":r["maximum_area_sqft"],
              "bmin":r["budget_min_inr"],"bmax":r["budget_max_inr"],"floor":r["floor_preference"] or "",
              "frontage":r["frontage_requirement"] or "","cat":r["suitable_category"] or "",
              "comp":"Complete" if (r["preferred_locations"] and (r["minimum_area_sqft"] or r["maximum_area_sqft"])) else "Partial",
              "conf":r["confidence"] or 0,"fp":fp
            })
            stats["requirements"]+=1

        c.execute(text("""
          UPDATE v81_refinery_runs SET completed_at=NOW(),messages_scanned=:m,clean_properties=:p,
          clean_requirements=:r,duplicates_skipped=:d,fragments_rejected=:f WHERE run_id=:id
        """),{"m":stats["messages"],"p":stats["properties"],"r":stats["requirements"],
              "d":stats["duplicates"],"f":stats["fragments"],"id":run_id})

    return stats
