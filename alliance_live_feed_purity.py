from __future__ import annotations

import re
from datetime import datetime, date
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
import alliance_live_feed_purity_legacy36 as _legacy

VERSION = "6.0-WHATSAPP-LOCATION-FIRST-GATE"
OWNER = "ALLIANCE_V60_WHATSAPP_LOCATION_FIRST_GATE"

LOCATION_ALIASES = {
    "KALKAJI":["KALKAJI"],
    "SAKET":["SAKET","SAKET DISTRICT CENTRE","DISTRICT CENTRE SAKET","DLF AVENUE SAKET","SELECT CITYWALK","SELECT CITY WALK"],
    "MALVIYA NAGAR":["MALVIYA NAGAR"],
    "HAUZ KHAS":["HAUZ KHAS"],
    "GREEN PARK":["GREEN PARK"],
    "GREATER KAILASH 1":["GK 1","GK-1","GK1","GREATER KAILASH 1"],
    "GREATER KAILASH 2":["GK 2","GK-2","GK2","GREATER KAILASH 2"],
    "CR PARK":["CR PARK","C R PARK","CHITTARANJAN PARK"],
    "NEHRU PLACE":["NEHRU PLACE"],
    "EAST OF KAILASH":["EAST OF KAILASH"],
    "KAILASH COLONY":["KAILASH COLONY"],
    "DEFENCE COLONY":["DEFENCE COLONY"],
    "SOUTH EXTENSION":["SOUTH EXTENSION","SOUTH EX"],
    "VASANT KUNJ":["VASANT KUNJ"],
    "VASANT VIHAR":["VASANT VIHAR"],
    "PANCHSHEEL PARK":["PANCHSHEEL PARK"],
    "OKHLA":["OKHLA"],
    "JASOLA":["JASOLA"],
    "MEHRAULI":["MEHRAULI"],
    "CHHATARPUR":["CHHATARPUR","CHATTARPUR"],
    "CONNAUGHT PLACE":["CONNAUGHT PLACE","CP"],
    "GURUGRAM":["GURUGRAM","GURGAON","GGN"],
    "DLF PHASE 1":["DLF PHASE 1","DLFPHASE1"],
    "DLF PHASE 2":["DLF PHASE 2","DLFPHASE2"],
    "DLF PHASE 4":["DLF PHASE 4","DLFPHASE4"],
    "SUSHANT LOK 1":["SUSHANT LOK 1","SUSHANTLOK1"],
    "SIOLIM":["SIOLIM"],
    "ASSAGAO":["ASSAGAO"],
    "PANAJI":["PANAJI","PANJIM"],
}

PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s().-]*)?[6-9](?:[\s().-]*\d){9}(?!\d)")

def esc(v):
    return str(v or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;").replace("'","&#39;")

def norm(v):
    return re.sub(r"\s+"," ",re.sub(r"[^A-Z0-9]+"," ",str(v or "").upper())).strip()

def canonical_location(*vals):
    blob = norm(" ".join(str(v or "") for v in vals))
    found = []
    for canon, aliases in LOCATION_ALIASES.items():
        for a in aliases:
            aa = norm(a)
            if re.search(r"(?<![A-Z0-9])"+re.escape(aa)+r"(?![A-Z0-9])", blob):
                found.append((len(aa), canon))
    if found:
        return sorted(found, reverse=True)[0][1].title()
    return "Unknown"

def tx(v):
    n = norm(v)
    if any(x in n for x in ("RENT","RENTAL","LEASE","LEASING","TO LET")): return "RENT"
    if any(x in n for x in ("SALE","SELL","OUTRIGHT","PURCHASE","RESALE")): return "SALE"
    return "UNKNOWN"

def ptype(v):
    n = norm(v)
    if any(x in n for x in ("COMMERCIAL","OFFICE","SHOP","SHOWROOM","RETAIL","WAREHOUSE","GODOWN","BANQUET","RESTAURANT","CAFE","LOUNGE","HOTEL","GUEST HOUSE")):
        return "Commercial"
    if any(x in n for x in ("RESIDENTIAL","APARTMENT","FLAT","VILLA","KOTHI","BHK","BUILDER FLOOR","INDEPENDENT HOUSE","PENTHOUSE")):
        return "Residential"
    if any(x in n for x in ("PLOT","LAND","FARMHOUSE","FARM HOUSE")):
        return "Land"
    return "Property"

def noise(v):
    n = norm(v)
    return any(x in n for x in ("GOOD MORNING","GOOD NIGHT","MOTIVATIONAL","HAPPY BIRTHDAY","CONGRATULATIONS","REJECTED"))

def requirement_like(v):
    n = norm(v)
    demand = ("REQUIRE","REQUIREMENT","LOOKING FOR","WANTED","CLIENT NEED","URGENT REQUIREMENT")
    supply = ("AVAILABLE","FOR RENT","FOR SALE","TO LET","EXCLUSIVE MANDATE","RESALE","READY TO MOVE")
    return any(x in n for x in demand) and not any(x in n for x in supply)

def ordinal(d):
    if not d: return "—"
    try:
        if isinstance(d, str):
            dt = datetime.fromisoformat(d.replace("Z","+00:00"))
        elif isinstance(d, date) and not isinstance(d, datetime):
            dt = datetime(d.year,d.month,d.day)
        else:
            dt = d
        day = dt.day
        suf = "th" if 11 <= day % 100 <= 13 else {1:"st",2:"nd",3:"rd"}.get(day % 10, "th")
        return f"{day}{suf} {dt.strftime('%b %Y')}"
    except Exception:
        return str(d)

def normalize_phone(raw):
    d = re.sub(r"\D", "", str(raw or ""))
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    return d if len(d) == 10 and d[0] in "6789" else ""

def extract_phones(*values):
    seen = []
    for value in values:
        textv = str(value or "")
        # first capture explicit phone-like patterns
        for m in PHONE_RE.finditer(textv):
            p = normalize_phone(m.group(0))
            if p and p not in seen:
                seen.append(p)
        # fallback for compact strings after stripping formatting
        digits = re.findall(r"(?:\+?91)?[6-9]\d{9}", re.sub(r"[\s().-]", "", textv))
        for d in digits:
            p = normalize_phone(d)
            if p and p not in seen:
                seen.append(p)
    return seen

def format_phones(phones):
    return " / ".join("+91 " + p for p in phones)

def clean_contact_name(value):
    raw = str(value or "").strip()
    # remove all phone patterns from combined contact field
    raw = PHONE_RE.sub(" ", raw)
    raw = re.sub(r"\+?91?\d{10,12}", " ", raw)
    raw = re.sub(r"\s*[·|/,-]\s*$", "", raw)
    raw = re.sub(r"\s+", " ", raw).strip(" ·|/,-")
    return raw


GENERIC_BAD = {
    "PROPERTY","PROPERTY AVAILABLE","AVAILABLE PROPERTY","AVAILABLE","DETAILS AVAILABLE",
    "CONTACT FOR DETAILS","PLEASE CALL","CALL FOR DETAILS","MORE DETAILS","UNKNOWN",
    "PROPERTY AVAILABILITY","RENT PROPERTY","SALE PROPERTY"
}


def _has_specific_property_identity(item):
    desc=str(item.get("description") or "").strip(); n=norm(desc)
    loc=str(item.get("location") or "").strip(); fam=str(item.get("property_type") or "").strip()
    area=str(item.get("area") or "").strip(); price=str(item.get("price") or "").strip()
    strong=any(x in n for x in ("BHK","SQFT","SQ FT","SQ YD","APARTMENT","FLAT","VILLA","FLOOR","SHOP","SHOWROOM","OFFICE","RESTAURANT","BANQUET","WAREHOUSE","GODOWN","HOTEL","PLOT","LAND","FARMHOUSE"))
    has_area=bool(area and area not in {"-","0","None","Unknown"})
    has_price=bool(price and price not in {"-","0","None","Unknown"})
    has_loc=bool(loc and loc.lower()!="unknown"); typed=bool(fam and fam!="Property")
    if n in {"RESIDENTIAL","COMMERCIAL","LAND","PROPERTY"}: return False
    if has_price and not (strong or has_area or (has_loc and typed)): return False
    if has_loc and not (strong or has_area or has_price or typed): return False
    return strong or has_area or (has_loc and typed and has_price)

def _money_review(item):
    txv=str(item.get("transaction") or "").upper()
    raw=str(item.get("description") or item.get("raw_text") or "")
    try: value=float(str(item.get("price") or "").replace(",","").strip())
    except Exception: value=None
    if value is None or value<=0: return "UNKNOWN","Commercial term not stated"
    if txv=="RENT":
        if re.search(r"(?i)(?:per\s*sq|/\s*sq|psf|sq\.?\s*ft\s*(?:pm|per month))",raw):
            return "NEEDS_REVIEW","Rent appears to be a per-square-foot rate"
        if value<5000: return "NEEDS_REVIEW","Stored rent is unusually low; may be per-square-foot or missing unit"
        if value>=100000000: return "NEEDS_REVIEW","Stored monthly rent is unusually high; verify unit and amount"
    if txv=="SALE" and value<100000: return "NEEDS_REVIEW","Stored sale price is unusually low; verify unit and amount"
    return "OK",""

def _purity_key(item):
    desc=norm(item.get("clean_description") or item.get("description") or "")
    for token in ("FULLY FURNISHED","SEMI FURNISHED","UNFURNISHED"): desc=desc.replace(token," ")
    desc=re.sub(r"\s+"," ",desc).strip()
    return "|".join((norm(item.get("location")),norm(item.get("transaction")),norm(item.get("property_type")),norm(item.get("area")),norm(item.get("price")),desc))

def _dedupe_clean_properties(rows):
    chosen={}; order=[]
    def richness(x):
        score=sum(1 for k in ("location","property_type","area","price","contact_name","contact_number") if str(x.get(k) or "").strip().lower() not in {"","unknown","property","-","none"})
        return score+min(len(str(x.get("clean_description") or ""))//40,5)
    for item in rows:
        key=_purity_key(item)
        if key not in chosen: chosen[key]=item; order.append(key)
        elif richness(item)>richness(chosen[key]): chosen[key]=item
    return [chosen[k] for k in order]

def meaningful_property(item):
    """Read-side quality gate. Never deletes the source row."""
    desc = str(item.get("description") or item.get("raw_text") or "").strip()
    n = norm(desc)
    if not n or n in GENERIC_BAD or len(n) < 18:
        return False

    loc = str(item.get("location") or "")
    txn = str(item.get("transaction") or "")
    fam = str(item.get("property_type") or "")
    area = str(item.get("area") or "").strip()
    price = str(item.get("price") or "").strip()

    # A usable inventory row needs a property identity, not merely a person/contact fragment.
    anchors = 0
    if loc and loc.lower() != "unknown": anchors += 1
    if txn and txn != "UNKNOWN": anchors += 1
    if fam and fam != "Property": anchors += 1
    if area and area not in {"-", "0", "None"}: anchors += 1
    if price and price not in {"-", "0", "None"}: anchors += 1

    # Strong property words also count as identity evidence.
    if any(x in n for x in (
        "BHK","SQFT","SQ FT","SQ YD","SQYD","APARTMENT","FLAT","VILLA","FLOOR",
        "SHOP","SHOWROOM","OFFICE","RESTAURANT","BANQUET","PLOT","LAND","FARM",
        "WAREHOUSE","GODOWN","HOTEL","PENTHOUSE"
    )):
        anchors += 1

    # Girja / broker summary fragments should not appear unless the actual property is identifiable.
    if "GIRJA" in n and anchors < 3:
        return False

    if not _has_specific_property_identity(item):
        return False

    return anchors >= 2

def phone_lines_html(value):
    phones = [x.strip() for x in str(value or "").split("/") if x.strip()]
    if not phones:
        return "—"
    if len(phones) == 1:
        return f"<span class='phone'>{esc(phones[0])}</span>"
    return "".join(
        f"<div class='phoneLine'><b>{i}.</b> {esc(ph)}</div>"
        for i, ph in enumerate(phones, 1)
    )

def compact_description(value, limit=220):
    raw = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(raw) <= limit:
        return raw
    return raw[:limit-1].rstrip() + "…"



def _money_label(v, transaction):
    try:
        x=float(v)
    except Exception:
        return str(v or "").strip()
    if x<=0:return ""
    if x>=10000000:
        return f"₹{x/10000000:.2f} Cr"
    if x>=100000:
        return f"₹{x/100000:.2f} L"
    if transaction=="RENT":
        return f"₹{x:,.0f}/month"
    return f"₹{x:,.0f}"

def _configuration_from_text(textv):
    n=str(textv or "")
    bits=[]
    for pat in [
        r"(?i)\b\d(?:\.5)?\s*BHK(?:\s*\+?\s*(?:SERVANT|SER|SQ))?\b",
        r"(?i)\b\d\s*/\s*\d\s*BHK(?:\s*\+?\s*(?:SERVANT|SER))?\b",
        r"(?i)\b(?:FULLY|SEMI)\s+FURNISHED\b",
        r"(?i)\bUNFURNISHED\b",
        r"(?i)\bGROUND FLOOR\b|\bFIRST FLOOR\b|\bSECOND FLOOR\b|\bTHIRD FLOOR\b|\bLOWER FLOOR\b|\bUPPER GROUND\b",
    ]:
        m=re.search(pat,n)
        if m:
            val=re.sub(r"\s+"," ",m.group(0)).strip()
            if val.upper() not in [x.upper() for x in bits]: bits.append(val)
    return " · ".join(bits)

def _area_from_text(textv):
    s=str(textv or "")
    patterns=[
        (r"(?i)\b(\d{2,7}(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|sft)\b","sq ft"),
        (r"(?i)\b(\d{2,6}(?:\.\d+)?)\s*(?:sq\.?\s*yds?|sq\.?\s*yards?|yds?|gaj)\b","sq yd"),
        (r"(?i)\b(\d+(?:\.\d+)?)\s*(?:acres?|acre)\b","acre"),
        (r"(?i)\b(\d{2,7}(?:\.\d+)?)\s*(?:sq\.?\s*m|sqm|m2)\b","sq m"),
    ]
    for pat,unit in patterns:
        m=re.search(pat,s)
        if m:return f"{m.group(1)} {unit}"
    return ""

def _price_from_text(textv, transaction):
    s=str(textv or "")
    # Prefer explicit rent for RENT and explicit total/price for SALE.
    pats=[]
    if transaction=="RENT":
        pats += [r"(?i)(?:rent|rental|asking)\s*[:@-]?\s*₹?\s*(\d+(?:\.\d+)?)\s*(l|lac|lakh|k)?"]
    else:
        pats += [r"(?i)(?:price|total|sale|asking)\s*[:@-]?\s*₹?\s*(\d+(?:\.\d+)?)\s*(cr|crore|l|lac|lakh)?"]
    pats += [r"(?i)₹\s*(\d+(?:\.\d+)?)\s*(cr|crore|l|lac|lakh|k)"]
    for pat in pats:
        m=re.search(pat,s)
        if not m:continue
        x=float(m.group(1));u=(m.group(2) or '').lower()
        if u in ('cr','crore'):x*=10000000
        elif u in ('l','lac','lakh'):x*=100000
        elif u=='k':x*=1000
        return x
    return None

def build_clean_description(item):
    loc=str(item.get('location') or '').strip()
    fam=str(item.get('property_type') or '').strip()
    txv=str(item.get('transaction') or '').strip()
    raw=str(item.get('description') or item.get('raw_text') or '')
    cfg=_configuration_from_text(raw)
    area=str(item.get('area') or '').strip() or _area_from_text(raw)
    price=item.get('price')
    if not price:
        price=_price_from_text(raw,txv)
    parts=[]
    if loc and loc.lower()!='unknown':parts.append(loc)
    if fam and fam!='Property':parts.append(fam)
    if cfg:parts.append(cfg)
    if area and area not in {'-','None','0'}:parts.append(area)
    pl=_money_label(price,txv)
    if pl:parts.append(('Rent ' if txv=='RENT' else 'Price ')+pl)
    # Add only meaningful short feature phrases, never dump the raw broker message.
    n=norm(raw)
    for label,words in [
        ('Park Facing',['PARK FACING']),('Fully Furnished',['FULLY FURNISHED']),
        ('Semi Furnished',['SEMI FURNISHED']),('Unfurnished',['UNFURNISHED']),
        ('Private Pool',['PRIVATE POOL']),('Terrace',['TERRACE']),('Sea View',['SEA VIEW'])
    ]:
        if any(w in n for w in words) and label not in parts:parts.append(label)
    return ' · '.join(parts[:7]) or compact_description(raw,180)


def _parent_context(item):
    raw=str(item.get("raw_text") or "")
    desc=str(item.get("description") or "")
    parent={}
    loc=canonical_location(raw,desc,item.get("location"))
    if loc and loc.lower()!="unknown": parent["location"]=loc
    tv=tx(raw)
    if tv!="UNKNOWN": parent["transaction"]=tv
    pv=ptype(raw)
    if pv!="Property": parent["property_type"]=pv
    for k in ("contact_name","contact_number","source","captured_on","verification"):
        v=item.get(k)
        if v not in (None,"","Unknown","—"): parent[k]=v
    return parent

def _inherit_parent_context(child,parent):
    out=dict(child)
    for k,v in parent.items():
        if out.get(k) in (None,"","Unknown","—","Property","UNKNOWN"): out[k]=v
    return out

def _valid_location_value(value):
    v=str(value or "").strip()
    if not v or v.lower() in {"unknown","none","na","n/a","-","—"}: return False
    nv=norm(v)
    if any(x in nv for x in ("PROPERTY PROPOSAL","PROPERTY GROUP","REAL ESTATE AGENT","WESTERN LINE PROPERTY")): return False
    return True

def _location_first_status(item):
    if not _has_specific_property_identity(item): return "REJECT_FRAGMENT"
    if not _valid_location_value(item.get("location")): return "HOLDING_LOCATION_REQUIRED"
    return "CLEAN"

def _apply_location_first_gate(rows):
    clean=[]; holding=[]; rejected=[]
    for item in rows:
        x=dict(item); status=_location_first_status(x)
        x["property_purity_status"]=status; x["matcher_eligible"]=(status=="CLEAN")
        if status=="CLEAN": clean.append(x)
        elif status=="HOLDING_LOCATION_REQUIRED":
            x["holding_reason"]="Location could not be confidently recovered from property or parent message"; holding.append(x)
        else:
            x["holding_reason"]="Not a meaningful independent property entity"; rejected.append(x)
    return clean,holding,rejected

def split_multi_property(item):
    """Non-destructive read-side splitter for obvious bundled listings."""
    description=str(item.get('description') or '').strip()
    raw_text=str(item.get('raw_text') or '').strip()
    parent=_parent_context(item)
    raw=description if description and norm(description) not in GENERIC_BAD else (raw_text or description)
    # Numbered/emoji list rows or repeated line items with rent/price.
    lines=[re.sub(r"\s+"," ",x).strip() for x in raw.splitlines() if re.sub(r"\s+"," ",x).strip()]
    starts=[]
    for i,line in enumerate(lines):
        if re.match(r"^(?:\d+[.)]|[1-9]️⃣|[①②③④⑤⑥⑦⑧⑨]|•\s*\d+)",line):
            starts.append(i)
    chunks=[]
    if len(starts)>=2:
        for j,st in enumerate(starts):
            en=starts[j+1] if j+1<len(starts) else len(lines)
            ch=' '.join(lines[st:en])
            if len(norm(ch))>=15:chunks.append(ch)
    else:
        # Common broker list: each property sits on its own line and contains area/rent/price/config.
        candidate_lines=[x for x in lines if (
            re.search(r"(?i)\b(BHK|SQ\.?\s*FT|SQFT|SQ\.?\s*YD|RENT|PRICE|CR\b|LAC|LAKH)\b",x)
            and len(norm(x))>=18
        )]
        if len(candidate_lines)>=3:
            chunks=candidate_lines
    if len(chunks)<2:
        x=_inherit_parent_context(dict(item),parent)
        x['clean_description']=build_clean_description(x)
        return [x]

    out=[]
    for idx,ch in enumerate(chunks,1):
        child=dict(item)
        child['raw_text']=ch
        child['description']=ch
        child['location']=canonical_location(ch,item.get('location'))
        child['transaction']=tx(ch) if tx(ch)!='UNKNOWN' else item.get('transaction')
        child['property_type']=ptype(ch) if ptype(ch)!='Property' else item.get('property_type')
        child['area']=_area_from_text(ch) or item.get('area')
        pv=_price_from_text(ch,child.get('transaction'))
        if pv is not None:child['price']=pv
        child=_inherit_parent_context(child,parent)
        child['clean_description']=build_clean_description(child)
        child['display_child_index']=idx
        out.append(child)
    return out

def _engine():
    import whatsapp_live_bridge as live
    return live.wa_engine

def _ensure(engine):
    if engine is None: return
    with engine.begin() as c:
        c.execute(text("""CREATE TABLE IF NOT EXISTS alliance_whatsapp_v52_overrides(
          record_id TEXT PRIMARY KEY,description_override TEXT,location_override TEXT,transaction_override TEXT,
          property_type_override TEXT,area_override TEXT,price_override TEXT,verification_override TEXT,
          deleted BOOLEAN DEFAULT FALSE,updated_at TIMESTAMPTZ DEFAULT NOW())"""))

def _latest_generation(engine):
    try:
        with engine.connect() as c:
            return c.execute(text("""SELECT generation_id FROM pi_whatsapp_property_master_generation
              WHERE status='COMPLETED' ORDER BY completed_at DESC NULLS LAST,id DESC LIMIT 1""")).scalar()
    except Exception:
        return None

def properties(q="", include_deleted=False, limit=2500):
    engine = _engine()
    if engine is None: return []
    _ensure(engine)
    rows = []
    gen = _latest_generation(engine)

    if gen:
        try:
            with engine.connect() as c:
                rows = [dict(r) for r in c.execute(text("""SELECT
                    m.record_id,m.lead_type,m.description,m.area,m.configuration_details,m.price,
                    m.contact_name_number,m.source,m.captured_on,m.verification,m.source_count,
                    wp.owner_name,wp.owner_phone,wp.broker_name,wp.broker_phone,
                    wp.sender_name,wp.sender_phone,wp.raw_text
                  FROM pi_whatsapp_property_master m
                  LEFT JOIN wa_properties wp ON wp.wa_property_id=m.record_id
                  WHERE m.generation_id=:g
                  ORDER BY m.captured_on DESC NULLS LAST,m.id DESC LIMIT :lim"""),
                  {"g":gen,"lim":limit}).mappings().all()]
        except Exception:
            rows = []

    if not rows:
        try:
            with engine.connect() as c:
                rows = [dict(r) for r in c.execute(text("""SELECT
                  p.wa_property_id record_id,p.transaction_type lead_type,p.raw_text description,
                  COALESCE(p.available_area_sqft,p.area_sqft)::text area,
                  CONCAT_WS(' · ',p.location,p.locality,p.floor,p.property_type) configuration_details,
                  COALESCE(p.rent_inr,p.sale_price_inr)::text price,
                  CONCAT_WS(' · ',COALESCE(p.owner_name,p.broker_name,p.sender_name),
                    COALESCE(p.owner_phone,p.broker_phone,p.sender_phone)) contact_name_number,
                  COALESCE(s.group_name,s.source_name,'WhatsApp') source,
                  p.last_seen captured_on,COALESCE(p.verification_status,p.availability,'UNVERIFIED') verification,
                  1 source_count,p.owner_name,p.owner_phone,p.broker_name,p.broker_phone,p.sender_name,p.sender_phone,p.raw_text
                  FROM wa_properties p LEFT JOIN wa_sources s ON s.source_id=p.source_id
                  WHERE COALESCE(p.record_status,'ACTIVE')='ACTIVE'
                  ORDER BY p.id DESC LIMIT :lim"""),{"lim":limit}).mappings().all()]
        except Exception:
            rows = []

    ovs = {}
    try:
        with engine.connect() as c:
            ovs = {str(r["record_id"]):dict(r) for r in c.execute(text("SELECT * FROM alliance_whatsapp_v52_overrides")).mappings().all()}
    except Exception:
        pass

    out = []
    qn = norm(q)

    for r in rows:
        rid = str(r.get("record_id") or "")
        ov = ovs.get(rid,{})
        if ov.get("deleted") and not include_deleted:
            continue

        raw = " ".join(str(r.get(k) or "") for k in ("description","configuration_details","lead_type","raw_text"))
        if noise(raw) or requirement_like(raw):
            continue

        phones = extract_phones(
            r.get("owner_phone"), r.get("broker_phone"), r.get("sender_phone"),
            r.get("contact_name_number"), r.get("raw_text"), r.get("description")
        )

        cname = (
            r.get("owner_name")
            or r.get("broker_name")
            or r.get("sender_name")
            or clean_contact_name(r.get("contact_name_number"))
        )

        item = {
            **r,
            "record_id": rid,
            "description": ov.get("description_override") or r.get("description") or r.get("raw_text") or "Property availability",
            "location": ov.get("location_override") or canonical_location(r.get("configuration_details"), r.get("description"), r.get("raw_text")),
            "transaction": ov.get("transaction_override") or tx(raw),
            "property_type": ov.get("property_type_override") or ptype(raw),
            "area": ov.get("area_override") or r.get("area"),
            "price": ov.get("price_override") or r.get("price"),
            "verification": ov.get("verification_override") or r.get("verification") or "UNVERIFIED",
            "contact_name": cname or "",
            "contact_number": format_phones(phones),
        }

        if not meaningful_property(item):
            continue

        if qn:
            hay = norm(" ".join(str(v or "") for v in item.values()))
            if qn not in hay and not all(t in hay for t in qn.split()):
                continue

        for child in split_multi_property(item):
            if meaningful_property(child):
                child["clean_description"] = child.get("clean_description") or build_clean_description(child)
                term_status,term_reason=_money_review(child)
                child["commercial_terms_status"]=term_status
                child["commercial_terms_reason"]=term_reason
                out.append(child)

    deduped=_dedupe_clean_properties(out)
    clean,holding,rejected=_apply_location_first_gate(deduped)
    return clean

def _parse_message_ts(value):
    if not value: return None
    s = str(value).strip()
    try:
        return datetime.fromisoformat(s.replace("Z","+00:00"))
    except Exception:
        pass
    for fmt in ("%d/%m/%Y, %H:%M","%d/%m/%Y %H:%M","%m/%d/%Y, %H:%M","%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s,fmt)
        except Exception:
            pass
    return None

def requirement_rows(selected_date=None, limit=2000):
    engine = _engine()
    if engine is None: return []
    try:
        with engine.connect() as c:
            rows = [dict(r) for r in c.execute(text("""SELECT
              r.*,COALESCE(s.group_name,s.source_name,'WhatsApp') source_group,
              m.message_timestamp original_message_timestamp,m.created_at message_ingested_at,
              m.sender_name message_sender_name,m.sender_phone message_sender_phone,m.raw_text message_raw_text
              FROM wa_requirements r
              LEFT JOIN wa_sources s ON s.source_id=r.source_id
              LEFT JOIN wa_messages m ON m.message_id=r.message_id
              WHERE COALESCE(r.status,'ACTIVE')='ACTIVE'
              ORDER BY r.id DESC LIMIT :lim"""),{"lim":limit}).mappings().all()]
    except Exception:
        return []

    out = []
    for r in rows:
        dt = _parse_message_ts(r.get("original_message_timestamp"))
        if dt is None:
            dt = r.get("created_at")

        local_date = None
        if isinstance(dt,datetime):
            if dt.tzinfo is not None:
                try:
                    from zoneinfo import ZoneInfo
                    dt = dt.astimezone(ZoneInfo("Asia/Kolkata"))
                except Exception:
                    pass
            local_date = dt.date().isoformat()
        elif dt:
            local_date = str(dt)[:10]

        phones = extract_phones(
            r.get("contact_phone"),
            r.get("message_sender_phone"),
            r.get("raw_text"),
            r.get("message_raw_text")
        )
        cname = (
            r.get("contact_name")
            or r.get("client_name")
            or r.get("message_sender_name")
            or ""
        )

        r["effective_date"] = local_date
        r["effective_date_label"] = ordinal(dt)
        r["display_contact_name"] = cname
        r["display_contact_number"] = format_phones(phones)

        if selected_date and local_date != selected_date:
            continue
        out.append(r)

    return out

def rloc(r):
    return canonical_location(r.get("preferred_locations"), r.get("raw_text"))

def rtx(r):
    return tx(" ".join(str(r.get(k) or "") for k in ("transaction_type","raw_text")))

def rpt(r):
    return ptype(" ".join(str(r.get(k) or "") for k in ("property_type","raw_text")))

def page(body,active="availability"):
    tabs = "".join(
        f"<a class='tab {'on' if active==k else ''}' href='/whatsapp-live?section={k}'>{label}</a>"
        for k,label in [
            ("availability","1. Availability"),
            ("requirements","2. Date-wise Requirements"),
        ]
    )
    tabs += "<a class='tab matcher' href='/deal-match-ai-v60'>3. Alliance Deal Matcher</a>"

    return f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>
    <title>WhatsApp Group Property Workspace</title><style>
    *{{box-sizing:border-box}}
    body{{margin:0;font-family:Arial;background:#efe4d2;color:#2c251e}}
    header{{background:#594634;color:#fff;padding:18px 24px}}
    nav,main{{max-width:1900px;margin:auto}}
    nav{{padding:12px 18px;background:#fffaf4;display:flex;gap:8px;flex-wrap:wrap}}
    a,.btn,button{{background:#6c543f;color:#fff;text-decoration:none;border:0;border-radius:7px;padding:9px 12px;font-weight:800;cursor:pointer}}
    main{{padding:18px}}
    .tabs{{display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap}}
    .tab{{background:#ad9882}}.tab.on{{background:#594634}}.tab.matcher{{background:#315f8d}}
    .card{{background:#fffdf9;border:1px solid #d9c9b7;border-radius:12px;padding:14px;margin-bottom:14px}}
    .scroll{{overflow:auto;max-height:72vh}}
    table{{width:100%;min-width:1280px;border-collapse:collapse;background:white;table-layout:auto}}
    th,td{{padding:5px 6px;border-bottom:1px solid #eee1d1;text-align:left;vertical-align:top;font-size:13px;line-height:1.35}}
    th{{background:#f7ecdf;position:sticky;top:0;font-size:12px}}
    .desc{{width:420px;max-width:420px;line-height:1.4;font-size:13.5px}}
    .loc{{font-weight:800;width:95px}}
    .phone{{font-weight:900;white-space:nowrap}}
    .phoneLine{{white-space:nowrap;margin-bottom:2px}}
    
    .smallcol{{width:72px}}.namecol{{width:95px}}.sourcecol{{width:105px}}
    .actioncol{{width:82px}}
    input,textarea,select{{width:100%;padding:9px;border:1px solid #cdbba8;border-radius:7px}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}}
    .green{{background:#39734a}}.red{{background:#963d35}}.blue{{background:#315f8d}}
    .muted{{color:#756757}}
    </style></head><body>
    <header><h2 style='margin:0'>WhatsApp Group Property Workspace</h2>
    <small>Clean Availability → Accurate Requirements → One Alliance Deal Matcher</small></header>
    <nav>
      <a href='/team-dashboard-v376'>← Dashboard</a>
      <a href='/workspace'>Working Space</a>
      <a href='/whatsapp-live/sources'>WhatsApp Sources</a>
      <a href='/deal-match-ai-v60'>Alliance Deal Match AI</a>
      <a href='/whatsapp-live/raw-audit'>Admin Raw Audit</a>
    </nav>
    <main><div class=tabs>{tabs}</div>{body}</main>
    </body></html>"""

def prop_table(rows):
    tr=[]
    for r in rows:
        rid = esc(r.get("record_id"))
        full_desc = str(r.get("clean_description") or r.get("description") or "")
        short_desc = compact_description(full_desc, 360)
        tr.append(f"""<tr>
        <td class=smallcol>{esc(r.get('transaction'))}</td>
        <td class=desc title="{esc(full_desc)}">{esc(short_desc)}</td>
        <td class=loc>{esc(r.get('location'))}</td>
        <td class=smallcol>{esc(r.get('property_type'))}</td>
        <td class=smallcol>{esc(r.get('area'))}</td>
        <td class=smallcol>{esc(r.get('price'))}
          {("<div style='color:#9a3d2f;font-weight:800'>NEEDS REVIEW</div><div class=muted>"+esc(r.get('commercial_terms_reason'))+"</div>") if r.get('commercial_terms_status')=='NEEDS_REVIEW' else ""}
        </td>
        <td class=namecol>{esc(r.get('contact_name'))}</td>
        <td>{phone_lines_html(r.get('contact_number'))}</td>
        <td class=sourcecol>{esc(r.get('source'))}</td>
        <td class=smallcol>{esc(r.get('captured_on'))}</td>
        <td class=smallcol>{esc(r.get('verification'))}</td>
        <td class=actioncol>
          <a class='btn green' href='/whatsapp-live/edit/{rid}'>Edit</a>
        </td>
        </tr>""")
    return "".join(tr)

def render_workspace(request):
    sec = request.query_params.get("section","availability")

    if sec == "match":
        return RedirectResponse("/deal-match-ai-v60",303)

    if sec == "requirements":
        selected = request.query_params.get("date","").strip()
        rs = requirement_rows(selected or None)
        trs = ""

        for r in rs:
            full_raw = str(r.get("raw_text") or "")
            short_raw = compact_description(full_raw, 360)
            matcher_q = __import__("urllib.parse", fromlist=["quote_plus"]).quote_plus(full_raw)
            matcher_url = f"/deal-match-ai-v60?q={matcher_q}&mode=SMART&min_score=70"
            trs += f"""<tr>
              <td class=smallcol><b>{esc(r.get('effective_date_label'))}</b></td>
                            <td class=loc>{esc(rloc(r))}</td>
              <td class=smallcol>{esc(rtx(r))}</td>
              <td class=smallcol>{esc(rpt(r))}</td>
              <td class=desc title="{esc(full_raw)}">{esc(short_raw)}</td>
              <td class=smallcol>{esc(r.get('minimum_area_sqft'))} - {esc(r.get('maximum_area_sqft'))}</td>
              <td class=smallcol>{esc(r.get('budget_max_inr'))}</td>
              <td class=namecol>{esc(r.get('display_contact_name'))}</td>
              <td>{phone_lines_html(r.get('display_contact_number'))}</td>
              <td class=sourcecol>{esc(r.get('source_group'))}</td>
              <td class=actioncol><a class='btn blue' href='{esc(matcher_url)}'>Run Matcher</a></td>
            </tr>"""

        body=f"""<div class=card>
        <h2>2. Date-wise Requirements</h2>
        <p class=muted>Date comes from the original WhatsApp message timestamp first and is shown in India time.</p>
        <p><a class='btn blue' href='/deal-match-ai-v60'>Run Alliance Deal Matcher</a>
        <span class=muted>Single matcher for all property databases.</span></p>
        <form method=get>
          <input type=hidden name=section value=requirements>
          <div class=grid>
            <div><label>Requirement Date</label><input type=date name=date value='{esc(selected)}'></div>
            <div style='align-self:end'><button>Show Date</button> <a class=btn href='/whatsapp-live?section=requirements'>Show All</a></div>
          </div>
        </form></div>
        <div class=scroll><table>
        <tr>
          <th>Date</th><th>Location</th><th>Transaction</th><th>Type</th>
          <th>Description</th><th>Area</th><th>Budget</th><th>Contact Name</th>
          <th>Contact Number</th><th>Source</th><th>Matcher</th>
        </tr>
        {trs or '<tr><td colspan=11>No active requirements for this date.</td></tr>'}
        </table></div>"""

        return HTMLResponse(page(body,"requirements"))

    q = request.query_params.get("q","")
    rows = properties(q=q)

    body=f"""<div class=card>
    <h2>1. Availability</h2>
    <form>
      <input type=hidden name=section value=availability>
      <div class=grid>
        <div><label>Search</label><input name=q value='{esc(q)}' placeholder='Kalkaji, Saket, rent, commercial, phone...'></div>
        <div style='align-self:end'><button>Search</button></div>
      </div>
    </form>
    <p class=muted>Clean working inventory only. Meaningless broker/summary fragments are hidden from this view and from matching; original WhatsApp source data remains preserved.</p>
    </div>
    <div class=scroll><table>
    <tr>
      <th>Rent/Sale</th>
      <th>Description</th>
      <th>Location</th>
      <th>Property Type</th>
      <th>Area</th>
      <th>Price/Rent</th>
      <th>Contact Name</th>
      <th>Contact Number</th>
      <th>Source</th>
      <th>Captured</th>
      <th>Verification</th>
      <th>Action</th>
    </tr>
    {prop_table(rows) or '<tr><td colspan=12>No clean availability records.</td></tr>'}
    </table></div>"""

    return HTMLResponse(page(body,"availability"))

def render_edit(record_id):
    row = next((r for r in properties(include_deleted=True,limit=3000) if str(r.get("record_id")) == record_id),None)
    if not row:
        return HTMLResponse("Property not found",404)

    body=f"""<div class=card><h2>Edit Property</h2>
    <p class=muted>Working-layer edit only. Original WhatsApp source remains unchanged.</p>
    <form method=post><div class=grid>
      <div><label>Location</label><input name=location value='{esc(row.get("location"))}'></div>
      <div><label>Transaction</label><select name=transaction>
        <option>{esc(row.get("transaction"))}</option><option>RENT</option><option>SALE</option>
      </select></div>
      <div><label>Property Type</label><select name=property_type>
        <option>{esc(row.get("property_type"))}</option><option>Commercial</option><option>Residential</option><option>Land</option><option>Property</option>
      </select></div>
      <div><label>Area</label><input name=area value='{esc(row.get("area"))}'></div>
      <div><label>Price/Rent</label><input name=price value='{esc(row.get("price"))}'></div>
      <div><label>Verification</label><select name=verification>
        <option>{esc(row.get("verification"))}</option><option>VERIFIED</option><option>UNVERIFIED</option><option>NOT AVAILABLE</option>
      </select></div>
      <div style='grid-column:1/-1'><label>Description</label><textarea name=description rows=5>{esc(row.get("description"))}</textarea></div>
    </div><p><button class=green>Save Changes</button></p></form></div>"""

    return HTMLResponse(page(body,"availability"))

async def save_edit(request,record_id):
    form = await request.form()
    engine = _engine()
    if engine is None:
        return HTMLResponse("WhatsApp DB unavailable",503)

    _ensure(engine)
    vals = {k:str(form.get(k) or "") for k in ("description","location","transaction","property_type","area","price","verification")}

    with engine.begin() as c:
        c.execute(text("""INSERT INTO alliance_whatsapp_v52_overrides(
          record_id,description_override,location_override,transaction_override,property_type_override,
          area_override,price_override,verification_override,deleted,updated_at)
          VALUES(:r,:d,:l,:t,:pt,:a,:p,:v,FALSE,NOW())
          ON CONFLICT(record_id) DO UPDATE SET
          description_override=EXCLUDED.description_override,
          location_override=EXCLUDED.location_override,
          transaction_override=EXCLUDED.transaction_override,
          property_type_override=EXCLUDED.property_type_override,
          area_override=EXCLUDED.area_override,
          price_override=EXCLUDED.price_override,
          verification_override=EXCLUDED.verification_override,
          deleted=FALSE,updated_at=NOW()"""),
          {"r":record_id,"d":vals["description"],"l":vals["location"],"t":vals["transaction"],
           "pt":vals["property_type"],"a":vals["area"],"p":vals["price"],"v":vals["verification"]})

    return RedirectResponse("/whatsapp-live?section=availability",303)

async def soft_delete(record_id):
    engine = _engine()
    if engine is None:
        return HTMLResponse("WhatsApp DB unavailable",503)

    _ensure(engine)
    with engine.begin() as c:
        c.execute(text("""INSERT INTO alliance_whatsapp_v52_overrides(record_id,deleted,updated_at)
          VALUES(:r,TRUE,NOW())
          ON CONFLICT(record_id) DO UPDATE SET deleted=TRUE,updated_at=NOW()"""),{"r":record_id})

    return RedirectResponse("/whatsapp-live?section=availability",303)

def raw_audit():
    engine = _engine()
    if engine is None:
        return HTMLResponse(page("<div class=card>WhatsApp DB unavailable.</div>"),503)

    try:
        with engine.connect() as c:
            rows = c.execute(text("""SELECT e.created_at,e.sender_name,e.sender_phone,e.raw_text,e.classification,e.entity_id,e.status,
              g.group_name,a.label account_label
              FROM wa_bridge_events e
              LEFT JOIN wa_bridge_groups g ON g.group_id=e.group_id
              LEFT JOIN wa_bridge_accounts a ON a.account_id=g.account_id
              ORDER BY e.id DESC LIMIT 500""")).mappings().all()
    except Exception as e:
        return HTMLResponse(page(f"<div class=card>Raw audit unavailable: {esc(e)}</div>"),500)

    trs="".join(
        f"<tr><td>{esc(r.get('created_at'))}</td><td>{esc(r.get('account_label'))}</td><td>{esc(r.get('group_name'))}</td>"
        f"<td>{esc(r.get('sender_name'))} {esc(r.get('sender_phone'))}</td><td class=desc>{esc(r.get('raw_text'))}</td>"
        f"<td>{esc(r.get('classification'))}</td><td>{esc(r.get('status'))}</td><td>{esc(r.get('entity_id'))}</td></tr>"
        for r in rows
    )

    return HTMLResponse(page(
        f"<div class=card><h2>Admin Raw WhatsApp Audit</h2></div>"
        f"<div class=scroll><table><tr><th>Received</th><th>Mobile</th><th>Group</th><th>Sender</th><th>Raw Message</th>"
        f"<th>Classification</th><th>Status</th><th>Entity</th></tr>{trs}</table></div>",""))

class V55AuthoritativeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self,request,call_next):
        p = request.url.path.rstrip("/") or "/"
        method = request.method.upper()

        if method == "GET" and p == "/whatsapp-live":
            return render_workspace(request)
        if method == "GET" and p == "/whatsapp-live/feed":
            return RedirectResponse("/whatsapp-live?section=availability",303)
        if method == "GET" and p == "/whatsapp-live/requirements":
            return RedirectResponse("/whatsapp-live?section=requirements",303)
        if method == "GET" and p == "/whatsapp-live/raw-audit":
            return raw_audit()
        if method == "GET" and p.startswith("/whatsapp-live/edit/"):
            return render_edit(p.split("/edit/",1)[1])
        if method == "POST" and p.startswith("/whatsapp-live/edit/"):
            return await save_edit(request,p.split("/edit/",1)[1])
        if method == "POST" and p.startswith("/whatsapp-live/delete/"):
            return await soft_delete(p.split("/delete/",1)[1])

        return await call_next(request)

def register(wrapped):
    try:
        legacy_result = _legacy.register(wrapped)
    except Exception as e:
        legacy_result = {"status":"DEGRADED","error":f"{type(e).__name__}: {e}"}

    app = wrapped.app

    # Reuse previous middleware state key to avoid duplicate layers.
    if not getattr(app.state,"alliance_v53_authoritative_middleware",False):
        app.add_middleware(V55AuthoritativeMiddleware)
        app.state.alliance_v53_authoritative_middleware = True

    theme_status = None
    try:
        import alliance_results_theme_v70 as _theme70
        theme_status = _theme70.register(wrapped)
    except Exception as e:
        theme_status = {"status":"DEGRADED","error":f"{type(e).__name__}: {e}"}

    accuracy_status = None
    try:
        import alliance_match_accuracy_v61 as _acc61
        accuracy_status = _acc61.install()
    except Exception as e:
        accuracy_status = {"status":"DEGRADED","error":f"{type(e).__name__}: {e}"}

    @app.get("/api/v57/status")
    def status():
        return {
            "status":"OK",
            "version":VERSION,
            "owner":OWNER,
            "presentation_only":True,
            "database_mutation":False,
            "wrapper_change_required":False,
            "field_order":[
                "Record","Rent/Sale","Description","Location","Property Type","Area","Price/Rent",
                "Contact Name","Contact Number","Source","Captured","Verification","Action"
            ],
            "phone_recovery_order":[
                "stored contact_phone/owner_phone/broker_phone/sender_phone",
                "combined contact field",
                "raw WhatsApp message"
            ],
            "multiple_phone_numbers":True,
            "single_matcher":"/deal-match-ai-v60",
            "requirement_row_matcher_buttons":True,
            "old_match_section_redirect":True,"compact_table":False,"uniform_result_theme":True,"technical_ids_hidden":True,"description_expanded":True,"meaningful_inventory_gate":True,"multi_property_reconstruction":True,"clean_description_primary":True,
            "source_preserved":True,"matcher_accuracy_layer":"V6.1","property_purity_gate":True,"fragment_filter":True,"read_side_dedupe":True,"commercial_terms_validator":True,"parent_context_inheritance":True,"location_first_gate":True,"unknown_location_excluded":True,"matcher_requires_location":True,
        }

    return {"status":"REGISTERED","version":VERSION,"owner":OWNER,"legacy":legacy_result}
