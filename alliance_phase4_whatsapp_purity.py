from __future__ import annotations
import hashlib, re
from typing import Any, Mapping

VERSION="4.1.1-PHASE4.1-WHATSAPP-PURITY"

CITY_ONLY={"DELHI","NEW DELHI","GURGAON","GURUGRAM","NOIDA","GREATER NOIDA","FARIDABAD","GOA","MUMBAI","BENGALURU","BANGALORE","HYDERABAD"}

NOISE_RE=[
 re.compile(r"^\s*(good morning|gm|good evening|good night|thanks|thank you|ok|okay|noted|shared)\b",re.I),
 re.compile(r"happy\s+(diwali|holi|new year|dussehra|eid|christmas)",re.I),
 re.compile(r"messages are end-to-end encrypted",re.I),
 re.compile(r"changed the group|created group|added .* to the group|left$",re.I),
]
REQ_RE=re.compile(r"\b(requirement|required|wanted|looking\s+for|need(?:ed)?|client\s+looking|tenant\s+requirement|buyer\s+requirement)\b",re.I)
SALE_RE=re.compile(r"\b(for\s+sale|sale|resale|outright|asking|demand|price)\b",re.I)
RENT_RE=re.compile(r"\b(for\s+rent|rent|rental|lease|leasing|to\s*let)\b",re.I)
SERVICE_RE=re.compile(r"\b(realtors?|estate\s+consultants?|property\s+consultants?|dealers?|brokers?|deals\s+in|collaboration)\b",re.I)
PROPERTY_RE=re.compile(r"\b(shop|showroom|office|warehouse|godown|plot|land|floor|kothi|villa|apartment|flat|commercial|residential|farmhouse|banquet|restaurant|cafe|lounge|club|guest\s*house|hotel|hospital|nursing\s*home|bhk)\b",re.I)

AREA_PATTERNS=[
 (re.compile(r"(?i)\b(\d+(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|sft)\b"),"sqft",1.0),
 (re.compile(r"(?i)\b(\d+(?:\.\d+)?)\s*(?:sq\.?\s*(?:yds?|yards?)|sqyds?|syds|yards?|yds?|gaj)\b"),"sq.yd",9.0),
 (re.compile(r"(?i)\b(\d+(?:\.\d+)?)\s*(?:sq\.?\s*m|sqm|sq\.?\s*metres?|sq\.?\s*meters?)\b"),"sq.m",10.7639104167),
 (re.compile(r"(?i)\b(\d+(?:\.\d+)?)\s*(?:acres?|acre)\b"),"acres",43560.0),
]
MONEY_TOKEN=re.compile(r"(?i)(?:₹\s*)?(\d+(?:\.\d+)?)\s*(cr|crore|crores|lac|lakh|lakhs|k)\b")
SALE_CONTEXT=re.compile(r"(?i)\b(price|demand|asking|sale|resale)\b|@")
RENT_CONTEXT=re.compile(r"(?i)\b(rent|rental|lease|leasing|per\s*month|p\.?m\.?)\b")
GENERIC_PREFIX_RE=re.compile(r"(?i)^\s*(?:shops?|properties?|spaces?|units?)\s+(?:available\s+)?(?:for\s+)?(?:lease|rent|sale)\s*[:\-]?\s*")

def norm(v:Any)->str:
    return re.sub(r"\s+"," ",re.sub(r"[^A-Z0-9]+"," ",str(v or "").upper())).strip()

def phones(*vals):
    out=[]
    for v in vals:
        for p in re.findall(r"(?<!\d)(?:\+?91[\s-]?)?([6-9]\d{9})(?!\d)",str(v or "")):
            if p not in out:out.append(p)
    return out

def classify_text(raw:str)->str:
    s=str(raw or "").strip()
    if not s:return "NOISE"
    if any(p.search(s) for p in NOISE_RE):return "NOISE"
    if SERVICE_RE.search(s) and not any(p.search(s) for p,_,_ in AREA_PATTERNS):return "SERVICE_AD"
    req=bool(REQ_RE.search(s)); supply=bool(re.search(r"(?i)\b(available|for\s+sale|for\s+rent|for\s+lease|vacant|ready\s+to\s+move)\b",s))
    if req and not supply:return "REQUIREMENT"
    if PROPERTY_RE.search(s) or any(p.search(s) for p,_,_ in AREA_PATTERNS) or supply:return "LISTING"
    return "REVIEW"

def transaction(raw:str):
    s=str(raw or "");sale=bool(SALE_RE.search(s));rent=bool(RENT_RE.search(s))
    if sale and rent:return "Unknown",["DUAL_TRANSACTION_OFFERED"]
    if sale:return "Sale",[]
    if rent:return "Rent",[]
    return "Unknown",["TRANSACTION_UNKNOWN"]

def area(raw:str):
    s=str(raw or "")
    for pat,unit,factor in AREA_PATTERNS:
        m=pat.search(s)
        if m:
            v=float(m.group(1));sqft=v*factor
            if v<=0:return None,None,None,["AREA_INVALID"]
            if sqft<20 or sqft>20_000_000:return v,unit,sqft,["AREA_OUT_OF_RANGE"]
            return v,unit,sqft,[]
    return None,None,None,["AREA_MISSING"]

def money_num(token:str):
    m=MONEY_TOKEN.search(str(token or ""))
    if not m:return None
    n=float(m.group(1));u=m.group(2).lower()
    if u.startswith("cr"):n*=10_000_000
    elif u in {"lac","lakh","lakhs"}:n*=100_000
    elif u=="k":n*=1_000
    return n

def contextual_money(raw:str,txn:str):
    s=str(raw or "")
    if txn not in {"Sale","Rent"}:return None,None,"NOT_APPLICABLE",["TRANSACTION_UNKNOWN"]
    tokens=list(MONEY_TOKEN.finditer(s))
    relevant=[]
    for m in tokens:
        token=m.group(0);u=m.group(2).lower()
        ctx=s[max(0,m.start()-50):min(len(s),m.end()+40)]
        sale_ctx=bool(SALE_CONTEXT.search(ctx));rent_ctx=bool(RENT_CONTEXT.search(ctx))
        kind=None
        if sale_ctx and not rent_ctx:kind="Sale"
        elif rent_ctx and not sale_ctx:kind="Rent"
        elif sale_ctx and rent_ctx:kind="Ambiguous"
        elif txn=="Sale" and u.startswith("cr"):kind="Sale"
        elif txn=="Rent" and u in {"k","lac","lakh","lakhs"}:kind="Rent"
        if kind==txn:relevant.append((token,money_num(token)))
    # Strong fallback when the whole statement has one explicit transaction and multiple same-family values.
    if not relevant and tokens:
        whole_tx,_=transaction(s)
        if whole_tx==txn:
            for m in tokens:
                u=m.group(2).lower()
                if txn=="Rent" and u in {"k","lac","lakh","lakhs"}:relevant.append((m.group(0),money_num(m.group(0))))
                elif txn=="Sale" and u.startswith("cr"):relevant.append((m.group(0),money_num(m.group(0))))
    unique=[]
    for x in relevant:
        if x[1] not in [u[1] for u in unique]:unique.append(x)
    if len(unique)>1:return None,None,"AMBIGUOUS",["MULTIPLE_MONEY_VALUES"]
    if len(unique)==1:return unique[0][0],unique[0][1],"PARSED",[]
    return None,None,"MISSING",[]

def property_type(raw:str):
    s=norm(raw)
    for words,label in [
      (["HOSPITAL","NURSING HOME"],"Hospital"),(["WAREHOUSE","GODOWN"],"Warehouse"),(["BANQUET"],"Banquet"),
      (["RESTAURANT"],"Restaurant"),(["CAFE"],"Cafe"),(["LOUNGE"],"Lounge"),(["GUEST HOUSE"],"Guest House"),
      (["HOTEL"],"Hotel"),(["VILLA","KOTHI","BUNGALOW"],"Villa"),(["PLOT","LAND","ACRE"],"Plot"),
      (["SHOP","SHOWROOM","OFFICE","COMMERCIAL"],"Commercial"),(["BHK","APARTMENT","FLAT","BUILDER FLOOR"],"Apartment")
    ]:
        if any(w in s for w in words):return label
    return "Other"

def configuration(raw:str):
    m=re.search(r"(?i)\b(\d+(?:/\d+)?\s*BHK(?:\s*\+\s*(?:SER|SERVANT))?)\b",str(raw or ""))
    return re.sub(r"\s+"," ",m.group(1).upper()) if m else None

def floor(raw:str):
    m=re.search(r"(?i)\b(ground|first|second|third|fourth|fifth|lower ground|basement)\s*floor\b",str(raw or ""))
    return (m.group(1).title()+" Floor") if m else None

def city(raw:str):
    up=norm(raw)
    for token,label in [("GREATER NOIDA","Greater Noida"),("NEW DELHI","Delhi"),("GURUGRAM","Gurugram"),("GURGAON","Gurugram"),
                        ("FARIDABAD","Faridabad"),("NOIDA","Noida"),("MUMBAI","Mumbai"),("GOA","Goa"),("DELHI","Delhi")]:
        if token in up:return label
    return None

def locality_project(raw:str):
    lines=[re.sub(r"[*_#`]+","",x).strip(" -–—|:,") for x in str(raw or "").replace("\r","\n").splitlines() if x.strip()]
    loc=None;proj=None
    for x in lines:
        up=norm(x)
        if up in CITY_ONLY:continue
        if re.search(r"\b(?:SECTOR|SEC)\s*[- ]?\d+[A-Z]?\b",up) or re.search(r"\bDLF\s*PHASE\s*\d+\b",up):loc=x
        elif any(k in up for k in ["VASANT VIHAR","VASANT KUNJ","SAKET","GREATER KAILASH","KALKAJI","DEFENCE COLONY","GREEN PARK",
                                   "HAUZ KHAS","EAST OF KAILASH","LAJPAT NAGAR","SUSHANT LOK","SHUSHANT LOK","RAJOURI GARDEN",
                                   "DWARKA","FATORDA","CARMONA","RAMADA","MARGAO"]):loc=x
        elif any(k in up for k in ["ESTATE","TOWER","HEIGHTS","RESORT","APARTMENT","VILLA","PALMS","COURT","CREST","GALLERY","CENTRAL",
                                   "STREET","EXOTICA","LODHA","M3M","EMAAR","AIPL","UNITECH","TATA","SOBHA","AMBIENCE","REGENCY"]):proj=x
    return loc,proj

def _segment_location_area(seg:str)->bool:
    s=GENERIC_PREFIX_RE.sub("",str(seg or "")).strip(" ,;:-")
    matches=[]
    for pat,_,_ in AREA_PATTERNS:
        m=pat.search(s)
        if m:matches.append(m)
    if not matches:return False
    m=min(matches,key=lambda x:x.start())
    prefix=s[:m.start()].strip(" ,;:-")
    if not prefix:return False
    up=norm(prefix)
    if up in CITY_ONLY:return False
    words=[w for w in re.findall(r"[A-Za-z][A-Za-z0-9'.-]*",prefix) if norm(w) not in {"SHOP","SHOPS","OFFICE","OFFICES","AREA","SIZE","MEZZ","MEZZANINE","FLOOR","ROAD","MAIN","NEAR"}]
    return len(words)>=1

def identity_attribute_count(raw:str)->int:
    score=0
    av,au,_,_=area(raw)
    if av is not None and au:score+=1
    if configuration(raw):score+=1
    loc,proj=locality_project(raw)
    if loc or proj:score+=1
    if property_type(raw)!="Other":score+=1
    tx,_=transaction(raw)
    if tx in {"Sale","Rent"}:score+=1
    return score

def _semicolon_split(s:str):
    parts=[x.strip(" ,;-") for x in re.split(r"\s*;\s*",s) if x.strip(" ,;-")]
    if len(parts)<2:return []
    if not all(_segment_location_area(x) for x in parts):return []
    tx,_=transaction(s);out=[]
    for p in parts:
        p=GENERIC_PREFIX_RE.sub("",p).strip()
        if tx=="Rent" and not RENT_RE.search(p):p="For Rent\n"+p
        elif tx=="Sale" and not SALE_RE.search(p):p="For Sale\n"+p
        out.append(p)
    return out

def _strong_numbered_parts(s:str):
    marker=r"(?m)(?=^\s*(?:[1-9]\ufe0f?\u20e3|[1-9][\.\)]|[①②③④⑤⑥⑦⑧⑨])\s*)"
    parts=[x.strip() for x in re.split(marker,s) if x.strip()]
    if len(parts)<=1:return []
    return parts if all(identity_attribute_count(p)>=2 for p in parts) else []

def split_multi_listing(raw:str):
    s=str(raw or "").replace("\r","\n").strip()
    if not s:return []
    n=_strong_numbered_parts(s)
    if n:return n
    semi=_semicolon_split(s)
    if semi:return semi
    sep=[x.strip() for x in re.split(r"\n\s*[-_=]{5,}\s*\n",s) if x.strip()]
    if len(sep)>=2 and all(identity_attribute_count(x)>=2 for x in sep):return sep
    return [s]

def _unsplittable_multi(s:str)->bool:
    area_count=sum(len(p.findall(s)) for p,_,_ in AREA_PATTERNS)
    if area_count<2:return False
    # If we have multiple location+area comma/pipe chunks but no safe semicolon/numbered split, hold.
    chunks=[x.strip() for x in re.split(r"[,|]\s*",s) if x.strip()]
    strong=sum(1 for x in chunks if _segment_location_area(x))
    return strong>=2

def multi_property_status(raw:str):
    s=str(raw or "");parts=split_multi_listing(s)
    if len(parts)>1:return "SPLIT_STRONG_BOUNDARY"
    if _unsplittable_multi(s):return "MULTI_PROPERTY_BLOCK"
    return "SINGLE_OR_UNCERTAIN"

def expand_specific_rent_variants(block:str):
    return [str(block or "").strip()] if str(block or "").strip() else []

def group_message_bursts(rows,window_seconds=180):
    out=[];cur=None
    for r in rows:
        d=dict(r);key=(str(d.get("sender_phone") or d.get("sender_name") or "UNKNOWN"),str(d.get("source_id") or d.get("group_name") or "UNKNOWN"));ts=d.get("created_at")
        if cur is None:cur={"key":key,"start":ts,"last":ts,"rows":[d]};continue
        close=False
        if key==cur["key"] and ts and cur["last"]:
            try:close=(ts-cur["last"]).total_seconds()<=window_seconds
            except Exception:close=False
        if close:cur["rows"].append(d);cur["last"]=ts
        else:out.append(cur);cur={"key":key,"start":ts,"last":ts,"rows":[d]}
    if cur:out.append(cur)
    return out

def identity_key(rec:Mapping[str,Any])->str:
    parts=[rec.get("city"),rec.get("locality"),rec.get("project_name"),rec.get("property_type"),rec.get("transaction_type"),
           rec.get("configuration"),rec.get("area_value"),rec.get("area_unit"),rec.get("floor")]
    return hashlib.sha256("|".join(norm(x) for x in parts).encode()).hexdigest()

def normalize_listing(child_raw:str,parent_raw:str,meta:Mapping[str,Any]):
    if classify_text(child_raw) in {"NOISE","SERVICE_AD","REQUIREMENT"}:return None
    tx,txr=transaction(child_raw)
    if tx=="Unknown":
        ptx,pr=transaction(parent_raw)
        if ptx in {"Sale","Rent"}:tx=ptx;txr=pr
    loc,proj=locality_project(child_raw);ploc,pproj=locality_project(parent_raw);loc=loc or ploc;proj=proj or pproj
    av,au,asq,ar=area(child_raw)
    if av is None:av,au,asq,ar=area(parent_raw)
    mr,mv,ms,mrns=contextual_money(child_raw,tx)
    if ms=="MISSING":mr,mv,ms,mrns=contextual_money(parent_raw,tx)
    reasons=list(dict.fromkeys(txr+ar+mrns))
    mp=multi_property_status(parent_raw)
    # If this child is one of a strong split, do not penalize it for the parent containing multiple properties.
    if mp=="MULTI_PROPERTY_BLOCK":reasons.append("MULTI_PROPERTY_BLOCK")
    if not loc and not proj:reasons.append("LOCATION_UNRESOLVED")
    if tx=="Unknown":reasons.append("TRANSACTION_UNKNOWN")
    hold=(mp=="MULTI_PROPERTY_BLOCK" or ms=="AMBIGUOUS" or tx=="Unknown" or (not loc and not proj))
    rec={"city":city(child_raw) or city(parent_raw),"locality":loc,"project_name":proj,"property_type":property_type(child_raw+"\n"+parent_raw),
         "transaction_type":tx,"configuration":configuration(child_raw) or configuration(parent_raw),"area_value":av,"area_unit":au,"area_sqft":asq,
         "price_value":mv if tx=="Sale" and ms=="PARSED" else None,"price_unit":"INR" if tx=="Sale" and ms=="PARSED" else None,
         "rent_value":mv if tx=="Rent" and ms=="PARSED" else None,"rent_unit":"INR" if tx=="Rent" and ms=="PARSED" else None,
         "floor":floor(child_raw+"\n"+parent_raw),"broker_name":str(meta.get("sender_name") or "").strip(),
         "broker_phone":" | ".join(phones(meta.get("sender_phone"),child_raw,parent_raw)),"source_group":str(meta.get("group_name") or ""),
         "raw_message":parent_raw,"money_status":ms,"purity_reasons":reasons,"review_hold":hold,"multi_property_status":mp}
    score=35+(20 if loc or proj else 0)+(15 if av is not None and au else 0)+(8 if rec["configuration"] else 0)+(10 if tx in {"Sale","Rent"} else 0)+(7 if ms=="PARSED" else 0)+(5 if rec["broker_phone"] else 0)
    if hold:score=min(score,69)
    rec["confidence"]=min(score,100);rec["canonical_key"]=identity_key(rec)
    return rec
