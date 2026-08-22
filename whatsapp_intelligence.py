import os, re, io, csv, json, hashlib, uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Optional
from urllib.parse import quote_plus

from fastapi import APIRouter, Request, UploadFile, File, Form, Query, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from sqlalchemy import create_engine, text
from google import genai
from openpyxl import Workbook

router = APIRouter(prefix="/whatsapp-intelligence", tags=["WhatsApp Intelligence"])

WA_DATABASE_URL = os.getenv("WHATSAPP_DATABASE_URL", "").strip()
WA_GEMINI_API_KEY = os.getenv("WHATSAPP_GEMINI_API_KEY", os.getenv("GEMINI_API_KEY", "")).strip()
WA_GEMINI_MODEL = os.getenv("WHATSAPP_GEMINI_MODEL", os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")).strip()
WA_AI_ENABLED = os.getenv("WHATSAPP_AI_ENABLED", "true").lower() in {"1","true","yes","on"}

def _db_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url

wa_engine = create_engine(_db_url(WA_DATABASE_URL), pool_pre_ping=True, pool_recycle=300) if WA_DATABASE_URL else None
wa_client = genai.Client(api_key=WA_GEMINI_API_KEY) if WA_GEMINI_API_KEY and WA_AI_ENABLED else None

WA_SCHEMA = """
CREATE TABLE IF NOT EXISTS wa_sources(
 id BIGSERIAL PRIMARY KEY,
 source_id UUID UNIQUE NOT NULL,
 source_name TEXT,
 original_filename TEXT,
 group_name TEXT,
 ingestion_status TEXT DEFAULT 'RECEIVED',
 total_messages INTEGER DEFAULT 0,
 inventory_found INTEGER DEFAULT 0,
 requirements_found INTEGER DEFAULT 0,
 contacts_found INTEGER DEFAULT 0,
 duplicates_found INTEGER DEFAULT 0,
 review_found INTEGER DEFAULT 0,
 rejected_found INTEGER DEFAULT 0,
 error_message TEXT,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 processed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS wa_messages(
 id BIGSERIAL PRIMARY KEY,
 message_id UUID UNIQUE NOT NULL,
 source_id UUID NOT NULL,
 message_timestamp TEXT,
 sender_name TEXT,
 sender_phone TEXT,
 raw_text TEXT NOT NULL,
 classification TEXT,
 confidence NUMERIC(5,2),
 rejection_reason TEXT,
 processing_status TEXT DEFAULT 'PROCESSED',
 created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wa_properties(
 id BIGSERIAL PRIMARY KEY,
 wa_property_id TEXT UNIQUE NOT NULL,
 source_id UUID NOT NULL,
 message_id UUID NOT NULL,
 fingerprint TEXT,
 property_type TEXT,
 transaction_type TEXT,
 city TEXT,
 location TEXT,
 locality TEXT,
 address TEXT,
 landmark TEXT,
 area_sqft NUMERIC(14,2),
 available_area_sqft NUMERIC(14,2),
 floor TEXT,
 frontage TEXT,
 rent_inr NUMERIC(16,2),
 sale_price_inr NUMERIC(16,2),
 cam_inr NUMERIC(16,2),
 possession TEXT,
 parking TEXT,
 suitable_for TEXT,
 nearby_brands TEXT,
 availability TEXT DEFAULT 'UNKNOWN',
 broker_name TEXT,
 broker_phone TEXT,
 owner_name TEXT,
 owner_phone TEXT,
 sender_name TEXT,
 sender_phone TEXT,
 verification_status TEXT DEFAULT 'UNVERIFIED',
 duplicate_status TEXT DEFAULT 'UNIQUE',
 duplicate_of TEXT,
 confidence NUMERIC(5,2),
 raw_text TEXT NOT NULL,
 first_seen TEXT,
 last_seen TEXT,
 source_item_no INTEGER,
 parent_message_text TEXT,
 record_status TEXT DEFAULT 'ACTIVE',
 approved_to_main BOOLEAN DEFAULT FALSE,
 main_property_id TEXT,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wa_requirements(
 id BIGSERIAL PRIMARY KEY,
 wa_requirement_id TEXT UNIQUE NOT NULL,
 source_id UUID NOT NULL,
 message_id UUID NOT NULL,
 fingerprint TEXT,
 client_name TEXT,
 company_name TEXT,
 property_type TEXT,
 transaction_type TEXT,
 city TEXT,
 preferred_locations TEXT,
 minimum_area_sqft NUMERIC(14,2),
 maximum_area_sqft NUMERIC(14,2),
 budget_min_inr NUMERIC(16,2),
 budget_max_inr NUMERIC(16,2),
 floor_preference TEXT,
 frontage_requirement TEXT,
 suitable_category TEXT,
 contact_name TEXT,
 contact_phone TEXT,
 contact_type TEXT DEFAULT 'UNKNOWN',
 status TEXT DEFAULT 'ACTIVE',
 confidence NUMERIC(5,2),
 raw_text TEXT NOT NULL,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wa_contacts(
 id BIGSERIAL PRIMARY KEY,
 contact_id TEXT UNIQUE NOT NULL,
 name TEXT,
 phone TEXT UNIQUE NOT NULL,
 contact_type TEXT DEFAULT 'UNKNOWN',
 first_seen TEXT,
 last_seen TEXT,
 groups TEXT,
 locations TEXT,
 property_types TEXT,
 properties_shared INTEGER DEFAULT 0,
 requirements_shared INTEGER DEFAULT 0,
 verification_status TEXT DEFAULT 'UNVERIFIED',
 created_at TIMESTAMPTZ DEFAULT NOW(),
 updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wa_matches(
 id BIGSERIAL PRIMARY KEY,
 wa_requirement_id TEXT NOT NULL,
 wa_property_id TEXT NOT NULL,
 score NUMERIC(5,2),
 grade TEXT,
 reasons JSONB DEFAULT '[]'::jsonb,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 UNIQUE(wa_requirement_id, wa_property_id)
);

CREATE TABLE IF NOT EXISTS wa_review_queue(
 id BIGSERIAL PRIMARY KEY,
 message_id UUID NOT NULL,
 source_id UUID NOT NULL,
 review_reason TEXT,
 confidence NUMERIC(5,2),
 status TEXT DEFAULT 'OPEN',
 resolution_notes TEXT,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS wa_rejected(
 id BIGSERIAL PRIMARY KEY,
 message_id UUID NOT NULL,
 source_id UUID NOT NULL,
 rejection_reason TEXT,
 raw_text TEXT,
 created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wa_audit_log(
 id BIGSERIAL PRIMARY KEY,
 entity_type TEXT,
 entity_id TEXT,
 action TEXT NOT NULL,
 details JSONB DEFAULT '{}'::jsonb,
 performed_by TEXT,
 created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE wa_properties ADD COLUMN IF NOT EXISTS source_item_no INTEGER;
ALTER TABLE wa_properties ADD COLUMN IF NOT EXISTS parent_message_text TEXT;
ALTER TABLE wa_properties ADD COLUMN IF NOT EXISTS record_status TEXT DEFAULT 'ACTIVE';

CREATE INDEX IF NOT EXISTS idx_wa_properties_location ON wa_properties(location);
CREATE INDEX IF NOT EXISTS idx_wa_properties_phone ON wa_properties(sender_phone);
CREATE INDEX IF NOT EXISTS idx_wa_properties_verification ON wa_properties(verification_status);
CREATE INDEX IF NOT EXISTS idx_wa_requirements_status ON wa_requirements(status);
CREATE INDEX IF NOT EXISTS idx_wa_messages_classification ON wa_messages(classification);
"""

HEADER_PATTERNS = [
    re.compile(r"^\[?(?P<date>\d{1,2}/\d{1,2}/\d{2,4}),?\s+(?P<time>\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?)\]?\s*[-–]\s*(?P<sender>[^:]+):\s*(?P<text>.*)$", re.I),
    re.compile(r"^(?P<date>\d{1,2}/\d{1,2}/\d{2,4}),\s*(?P<time>\d{1,2}:\d{2}\s*(?:am|pm)?)\s*-\s*(?P<sender>[^:]+):\s*(?P<text>.*)$", re.I),
]

PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)")
PROPERTY_WORDS = [
    "sqft","sq ft","sft","gaj","yard","sqm","sq m","acre","rent","lease","sale","shop",
    "showroom","office","warehouse","plot","floor","kothi","villa","apartment","flat",
    "commercial","residential","frontage","possession","farmhouse","banquet","restaurant",
    "cafe","lounge","club","guest house","hotel"
]
SUPPLY_WORDS = [
    "available","for rent","for lease","for sale","ready to move","direct sale",
    "property available","shop available","office available","plot for sale",
    "kothi available","floor available","warehouse available","vacant"
]
DEMAND_WORDS = [
    "required","requirement","looking for","need ","client looking","client requires",
    "urgent requirement","mandate","tenant requirement","buyer requirement",
    "looking to purchase","looking to lease","wanted"
]
NOISE_PATTERNS = [
    r"^\s*(good morning|gm|good evening|good night|thanks|thank you|ok|okay|noted|shared)\b",
    r"happy (diwali|holi|new year|dussehra|eid|christmas)",
    r"messages are end-to-end encrypted",
    r"changed the group|created group|added .* to the group|left$"
]
PROPERTY_TYPES = {
    "warehouse":"Warehouse / Industrial","showroom":"Commercial Showroom","shop":"Commercial Shop",
    "office":"Office","plot":"Plot / Land","kothi":"Independent House / Villa",
    "villa":"Independent House / Villa","apartment":"Apartment","flat":"Apartment",
    "builder floor":"Builder Floor","farmhouse":"Farmhouse","banquet":"Banquet",
    "restaurant":"Restaurant","cafe":"Cafe","lounge":"Lounge","club":"Club",
    "guest house":"Guest House","hotel":"Hotel"
}
LOCATION_ALIASES = {
    "gk 1":"Greater Kailash 1","gk-1":"Greater Kailash 1","gk1":"Greater Kailash 1","gk 2":"Greater Kailash 2","gk-2":"Greater Kailash 2","gk2":"Greater Kailash 2",
    "greater kailash 1":"Greater Kailash 1","greater kailash 2":"Greater Kailash 2","eok":"East of Kailash","east of kailash":"East of Kailash","kailash colony":"Kailash Colony","green park":"Green Park","south ex":"South Extension",
    "south extension":"South Extension","cp":"Connaught Place","connaught place":"Connaught Place",
    "gurgaon":"Gurugram","gurugram":"Gurugram","defence colony":"Defence Colony",
    "vasant vihar":"Vasant Vihar","saket":"Saket","hauz khas":"Hauz Khas",
    "rajouri garden":"Rajouri Garden","dwarka":"Dwarka","noida":"Noida",
    "greater noida":"Greater Noida","faridabad":"Faridabad"
}
FLOORS = {
    "gf":"Ground Floor","ground floor":"Ground Floor","ff":"First Floor","first floor":"First Floor",
    "sf":"Second Floor","second floor":"Second Floor","tf":"Third Floor","third floor":"Third Floor",
    "basement":"Basement","lower ground":"Lower Ground Floor","lg":"Lower Ground Floor"
}

def require_wa_db():
    if wa_engine is None:
        raise HTTPException(
            status_code=503,
            detail="WHATSAPP_DATABASE_URL is not configured. Add a separate PostgreSQL service in Railway and set WHATSAPP_DATABASE_URL."
        )

def init_wa_db():
    require_wa_db()
    with wa_engine.begin() as c:
        for stmt in [x.strip() for x in WA_SCHEMA.split(";") if x.strip()]:
            c.execute(text(stmt))

def esc(v):
    s = "" if v is None else str(v)
    return (s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;"))

def money(v):
    if v in (None,"","UNKNOWN"): return "—"
    try:
        n=float(v)
        if n>=10_000_000: return f"₹{n/10_000_000:.2f} Cr"
        if n>=100_000: return f"₹{n/100_000:.2f} L"
        return f"₹{n:,.0f}"
    except: return esc(v)

def shell(title, body, active=""):
    nav = [
        ("Dashboard","/whatsapp-intelligence"),
        ("Import","/whatsapp-intelligence/import"),
        ("Properties","/whatsapp-intelligence/properties"),
        ("Requirements","/whatsapp-intelligence/requirements"),
        ("Contacts","/whatsapp-intelligence/contacts"),
        ("Brokers","/whatsapp-intelligence/brokers"),
        ("Search","/whatsapp-intelligence/search"),
        ("Review","/whatsapp-intelligence/review"),
        ("Rejected","/whatsapp-intelligence/rejected"),
        ("Export","/whatsapp-intelligence/export.xlsx"),
    ]
    links="".join(f'<a class="{"active" if n==active else ""}" href="{u}">{n}</a>' for n,u in nav)
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><style>
*{{box-sizing:border-box}}body{{margin:0;font-family:Inter,Arial,sans-serif;background:#f4f6f8;color:#101828}}
header{{background:#101828;color:#fff;padding:18px 24px}}header h1{{margin:0;font-size:23px}}header small{{color:#98a2b3}}
nav{{display:flex;gap:6px;flex-wrap:wrap;background:#fff;padding:10px 18px;border-bottom:1px solid #e4e7ec;position:sticky;top:0;z-index:5}}
nav a{{text-decoration:none;color:#344054;padding:9px 12px;border-radius:8px}}nav a:hover,nav a.active{{background:#101828;color:#fff}}
main{{max-width:1500px;margin:22px auto;padding:0 18px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px}}
.card{{background:#fff;border:1px solid #e4e7ec;border-radius:12px;padding:17px;box-shadow:0 1px 2px rgba(16,24,40,.04)}}
.num{{font-size:30px;font-weight:750;margin-top:4px}}.muted{{color:#667085}}.btn{{display:inline-block;border:0;background:#101828;color:#fff;padding:10px 14px;border-radius:8px;text-decoration:none;cursor:pointer}}
.btn2{{background:#e4e7ec;color:#101828}}.good{{background:#ecfdf3;color:#027a48;padding:4px 8px;border-radius:999px}}.warn{{background:#fffaeb;color:#b54708;padding:4px 8px;border-radius:999px}}
.bad{{background:#fef3f2;color:#b42318;padding:4px 8px;border-radius:999px}}table{{width:100%;border-collapse:collapse;background:#fff;font-size:13px}}th,td{{padding:10px;border-bottom:1px solid #eaecf0;text-align:left;vertical-align:top}}th{{background:#f9fafb;position:sticky;top:58px}}
.scroll{{overflow:auto;max-height:72vh;border:1px solid #e4e7ec;border-radius:12px}}input,select,textarea{{width:100%;padding:10px;border:1px solid #d0d5dd;border-radius:8px;background:#fff}}
form.gridform{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}}pre{{white-space:pre-wrap}}.score{{font-size:19px;font-weight:700}}
</style></head><body><header><h1>WhatsApp Property Intelligence</h1><small>Separate database · separate search · separate matcher</small></header>
<nav>{links}<a href="/workspace">← Main Workspace</a></nav><main>{body}</main></body></html>"""

def parse_chat(raw: str):
    out=[]; cur=None
    for raw_line in raw.splitlines():
        line=raw_line.replace("\u200e","").replace("\u200f","")
        m=None
        for p in HEADER_PATTERNS:
            m=p.match(line)
            if m: break
        if m:
            if cur: out.append(cur)
            cur={"timestamp":f'{m.group("date")} {m.group("time")}',"sender":m.group("sender").strip(),"text":m.group("text").strip()}
        elif cur:
            cur["text"] += "\n"+line
    if cur: out.append(cur)
    return out

def all_phones(text_value):
    found=[]
    for m in PHONE_RE.finditer(text_value or ""):
        d=re.sub(r"\\D","",m.group(0))
        if len(d)==11 and d.startswith("0"):
            d=d[1:]
        elif len(d)==12 and d.startswith("91"):
            d=d[2:]
        if len(d)==10 and d[0] in "6789":
            p="+91"+d
            if p not in found:
                found.append(p)
    return found

def phone(text_value):
    vals=all_phones(text_value)
    return vals[0] if vals else None

def is_noise(txt):
    low=txt.lower().strip()
    for p in NOISE_PATTERNS:
        if re.search(p,low,re.I): return True,"Greeting/system/non-transactional message"
    signals=PROPERTY_WORDS+SUPPLY_WORDS+DEMAND_WORDS
    if not any(x in low for x in signals) and not phone(txt):
        return True,"No actionable property signal"
    return False,""

def classify(txt):
    low=txt.lower()
    supply=sum(1 for x in SUPPLY_WORDS if x in low)
    demand=sum(1 for x in DEMAND_WORDS if x in low)
    prop=sum(1 for x in PROPERTY_WORDS if x in low)
    if demand>demand*0 and demand>supply:
        return "PROPERTY_REQUIREMENT", min(.98,.74+demand*.05+prop*.015)
    if supply>0 or prop>=2:
        return "PROPERTY_INVENTORY", min(.98,.72+supply*.05+prop*.015)
    if phone(txt): return "PROPERTY_CONTACT",.72
    return "NEEDS_REVIEW",.55

def area_sqft(txt):
    pats=[
        (r"(\d[\d,]*(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|sft)\b",1.0),
        (r"(\d[\d,]*(?:\.\d+)?)\s*(?:gaj|sq\.?\s*yd|sq\.?\s*yard|yards?)\b",9.0),
        (r"(\d[\d,]*(?:\.\d+)?)\s*(?:sq\.?\s*m|sqm|sq\.?\s*meter)\b",10.7639),
        (r"(\d[\d,]*(?:\.\d+)?)\s*(?:acre|acres)\b",43560.0)
    ]
    for pat,mul in pats:
        m=re.search(pat,txt,re.I)
        if m:return round(float(m.group(1).replace(",",""))*mul,2)
    return None

def all_areas(txt):
    range_patterns=[
        (r"(\\d[\\d,]*(?:\\.\\d+)?)\\s*(?:-|–|to)\\s*(\\d[\\d,]*(?:\\.\\d+)?)\\s*(?:sq\\.?\\s*ft|sqft|sft)\\b",1.0),
        (r"(\\d[\\d,]*(?:\\.\\d+)?)\\s*(?:-|–|to)\\s*(\\d[\\d,]*(?:\\.\\d+)?)\\s*(?:gaj|sq\\.?\\s*yd|sq\\.?\\s*yard|sqyd|yards?)\\b",9.0),
        (r"(\\d[\\d,]*(?:\\.\\d+)?)\\s*(?:-|–|to)\\s*(\\d[\\d,]*(?:\\.\\d+)?)\\s*(?:sq\\.?\\s*m|sqm)\\b",10.7639),
        (r"(\\d[\\d,]*(?:\\.\\d+)?)\\s*(?:-|–|to)\\s*(\\d[\\d,]*(?:\\.\\d+)?)\\s*(?:acre|acres)\\b",43560.0),
    ]
    for pat,mul in range_patterns:
        m=re.search(pat,txt,re.I)
        if m:
            a=round(float(m.group(1).replace(",",""))*mul,2)
            b=round(float(m.group(2).replace(",",""))*mul,2)
            return sorted([a,b])

    vals=[]
    for pat,mul in [
        (r"(\\d[\\d,]*(?:\\.\\d+)?)\\s*(?:sq\\.?\\s*ft|sqft|sft)\\b",1.0),
        (r"(\\d[\\d,]*(?:\\.\\d+)?)\\s*(?:gaj|sq\\.?\\s*yd|sq\\.?\\s*yard|sqyd|yards?)\\b",9.0),
        (r"(\\d[\\d,]*(?:\\.\\d+)?)\\s*(?:sq\\.?\\s*m|sqm)\\b",10.7639),
        (r"(\\d[\\d,]*(?:\\.\\d+)?)\\s*(?:acre|acres)\\b",43560.0),
    ]:
        for m in re.finditer(pat,txt,re.I):
            vals.append(round(float(m.group(1).replace(",",""))*mul,2))
    return vals

def money_values(txt):
    vals=[]
    for m in re.finditer(r"(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?)\s*(k|l|lac|lakh|lakhs|cr|crore|crores)?\b",txt,re.I):
        suf=(m.group(2) or "").lower()
        token=m.group(0).lower()
        if not suf and not ("₹" in token or "rs" in token or "inr" in token): continue
        v=float(m.group(1).replace(",",""))
        if suf=="k":v*=1_000
        elif suf in {"l","lac","lakh","lakhs"}:v*=100_000
        elif suf in {"cr","crore","crores"}:v*=10_000_000
        vals.append(v)
    return vals

def location(txt):
    low=txt.lower()
    found=[]
    # longest aliases first avoids CP accidental issues
    for alias,canon in sorted(LOCATION_ALIASES.items(),key=lambda x:len(x[0]),reverse=True):
        if re.search(r"(?<!\w)"+re.escape(alias)+r"(?!\w)",low) and canon not in found:
            found.append(canon)
    return ", ".join(found) if found else "UNKNOWN"

def ptype(txt):
    low=txt.lower()
    for k,v in sorted(PROPERTY_TYPES.items(),key=lambda x:len(x[0]),reverse=True):
        if k in low:return v
    return "UNKNOWN"

def floor_value(txt):
    low=txt.lower()
    for k,v in sorted(FLOORS.items(),key=lambda x:len(x[0]),reverse=True):
        if re.search(r"(?<!\w)"+re.escape(k)+r"(?!\w)",low):return v
    return "UNKNOWN"

def role(txt):
    low=txt.lower()
    if any(x in low for x in ["direct owner","owner listing","my property","call owner"]):return "OWNER"
    if any(x in low for x in ["brokerage","co-broker","broker","estate consultant","realtor"]):return "BROKER"
    return "UNKNOWN"

def txn(txt, requirement=False):
    low=txt.lower()
    if any(x in low for x in ["for sale","buy","buyer","purchase","sale"]): return "SALE"
    return "RENT"

def extract_broker_identity(raw_text, sender):
    sender_name=(sender or "").strip() or "UNKNOWN"
    sender_phone=phone(sender_name)
    phones=all_phones(raw_text) if "all_phones" in globals() else ([phone(raw_text)] if phone(raw_text) else [])
    broker_phone=sender_phone or (phones[0] if phones else None)

    lines=[x.strip(" *-_") for x in (raw_text or "").splitlines() if x.strip()]
    first_numbered=None
    for i,line in enumerate(lines):
        if re.match(r"^\d{1,3}(?:[\.\)\-:]|\s)\s*", line):
            first_numbered=i
            break

    if first_numbered is not None and first_numbered>0:
        for candidate in reversed(lines[:first_numbered]):
            low=candidate.lower()
            if not any(w in low for w in ["property","properties","estate","realty","group","available","sale","rent"]):
                if 2 <= len(candidate.split()) <= 6 and not phone(candidate):
                    sender_name=candidate
                    break
    return sender_name, broker_phone

def split_inventory(txt):
    """
    ONE returned item = ONE physical property entity.

    Supported broker formats:
      1 Kalkaji builder floor for sale...
      2 Vasant Kunj apartment for sale...
      3. Vasant Vihar ground floor for rent...
      4) GK-1 showroom...
      5- Defence Colony office...

    The broker/contact preamble before item 1 is not copied into child property text.
    Wrapped lines after an item stay only with that property.
    """
    raw=(txt or "").replace("\r\n","\n").replace("\r","\n")
    lines=raw.split("\n")
    items=[]
    current_no=None
    current=[]
    item_re=re.compile(r"^\s*(\d{1,3})\s*(?:[\.\)\-:]|\s)\s*(.+?)\s*$")

    for line in lines:
        m=item_re.match(line)
        if m:
            if current_no is not None and current:
                child="\n".join(current).strip()
                if child:
                    items.append(child)
            current_no=int(m.group(1))
            current=[m.group(2).strip()]
        elif current_no is not None and line.strip():
            current.append(line.strip())

    if current_no is not None and current:
        child="\n".join(current).strip()
        if child:
            items.append(child)

    if len(items)>=2:
        property_like=sum(1 for item in items if any(word in item.lower() for word in PROPERTY_WORDS + SUPPLY_WORDS))
        if property_like>=2:
            return items

    pipe_parts=[p.strip() for p in raw.split("|") if p.strip()]
    if len(pipe_parts)>=2:
        property_like=sum(1 for item in pipe_parts if any(word in item.lower() for word in PROPERTY_WORDS + SUPPLY_WORDS))
        if property_like>=2:
            return pipe_parts

    return [raw.strip()]

def deterministic_extract(txt, kind, sender):
    areas=all_areas(txt); cash=money_values(txt); low=txt.lower()
    loc=location(txt); typ=ptype(txt); t=txn(txt,kind=="PROPERTY_REQUIREMENT"); ph=phone(txt); ct=role(txt)
    if kind=="PROPERTY_REQUIREMENT":
        return {
            "client_name": sender or "UNKNOWN","company_name":None,"property_type":typ,"transaction_type":t,
            "city":"Delhi NCR" if loc!="UNKNOWN" else "UNKNOWN","preferred_locations":loc,
            "minimum_area_sqft":min(areas) if areas else None,"maximum_area_sqft":max(areas) if areas else None,
            "budget_min_inr":min(cash) if cash else None,"budget_max_inr":max(cash) if cash else None,
            "floor_preference":floor_value(txt),"frontage_requirement":None,"suitable_category":typ,
            "contact_name":sender or "UNKNOWN","contact_phone":ph,"contact_type":ct
        }
    cashv=max(cash) if cash else None
    return {
        "property_type":typ,"transaction_type":t,"city":"Delhi NCR" if loc!="UNKNOWN" else "UNKNOWN",
        "location":loc,"locality":loc,"address":None,"landmark":None,"area_sqft":areas[0] if areas else None,
        "available_area_sqft":areas[0] if areas else None,"floor":floor_value(txt),"frontage":None,
        "rent_inr":cashv if t=="RENT" else None,"sale_price_inr":cashv if t=="SALE" else None,
        "cam_inr":None,"possession":"Immediate" if "immediate" in low else None,"parking":None,
        "suitable_for":None,"nearby_brands":None,
        "availability":"AVAILABLE" if any(x in low for x in ["available","for rent","for sale","vacant","ready to move"]) else "UNKNOWN",
        "broker_name":sender if ct=="BROKER" else None,"broker_phone":ph if ct=="BROKER" else None,
        "owner_name":sender if ct=="OWNER" else None,"owner_phone":ph if ct=="OWNER" else None,
        "sender_name":sender,"sender_phone":ph
    }

def ai_enrich(txt, kind):
    if not wa_client:return None
    fields = (
        "property_type,transaction_type,city,location,locality,address,landmark,area_sqft,available_area_sqft,"
        "floor,frontage,rent_inr,sale_price_inr,cam_inr,possession,parking,suitable_for,nearby_brands,"
        "availability,broker_name,broker_phone,owner_name,owner_phone"
        if kind=="PROPERTY_INVENTORY" else
        "client_name,company_name,property_type,transaction_type,city,preferred_locations,minimum_area_sqft,"
        "maximum_area_sqft,budget_min_inr,budget_max_inr,floor_preference,frontage_requirement,suitable_category,"
        "contact_name,contact_phone,contact_type"
    )
    prompt=f"""Extract ONLY explicitly supported Indian real-estate data from this WhatsApp message.
Never guess. Never invent. Use null for missing values. Contact role OWNER/BROKER only when explicit. If a budget or price has no explicit currency unit such as lakh, lac, L, crore, Cr, rupees, Rs or ₹, return null for that money field.
Return one flat JSON object with only these fields:
{fields}
Message:
{txt}"""
    try:
        r=wa_client.models.generate_content(
            model=WA_GEMINI_MODEL,
            contents=prompt,
            config={"response_mime_type":"application/json"}
        )
        parsed = json.loads(r.text)
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return next((item for item in parsed if isinstance(item, dict)), None)
        return None
    except Exception:
        return None

def enrich_missing(base, ai):
    # AI is enrichment only. Deterministic parsers are authoritative for
    # money, area and phone fields so invalid strings never reach NUMERIC columns.
    if not ai:
        return base

    if isinstance(ai, list):
        ai = next((item for item in ai if isinstance(item, dict)), None)

    if not isinstance(ai, dict):
        return base

    protected_numeric_fields={
        "area_sqft","available_area_sqft","rent_inr","sale_price_inr","cam_inr",
        "minimum_area_sqft","maximum_area_sqft","budget_min_inr","budget_max_inr"
    }
    protected_phone_fields={"contact_phone","broker_phone","owner_phone","sender_phone"}

    for k,v in ai.items():
        if k not in base:
            continue
        if k in protected_numeric_fields or k in protected_phone_fields:
            continue
        if base.get(k) in (None,"","UNKNOWN") and v not in (None,"","UNKNOWN"):
            base[k]=v
    return base

def fingerprint(data, kind):
    if kind=="PROPERTY_INVENTORY":
        raw="|".join(str(data.get(k) or "").lower().strip() for k in ["location","property_type","area_sqft","floor","rent_inr","sale_price_inr"])
    else:
        raw="|".join(str(data.get(k) or "").lower().strip() for k in ["preferred_locations","property_type","minimum_area_sqft","maximum_area_sqft","budget_max_inr"])
    return hashlib.sha256(raw.encode()).hexdigest()

def sim(a,b):
    return SequenceMatcher(None,str(a or "").lower(),str(b or "").lower()).ratio()

def num_sim(a,b):
    try:
        a=float(a); b=float(b)
        if not a or not b:return 0
        return max(0,1-abs(a-b)/max(abs(a),abs(b)))
    except:return 0

def duplicate_candidate(c,data):
    rows=c.execute(text("""SELECT wa_property_id,location,property_type,area_sqft,floor,rent_inr,sale_price_inr
                           FROM wa_properties ORDER BY id DESC LIMIT 1500""")).mappings().all()
    best=(0,None)
    for r in rows:
        score=.35*sim(data.get("location"),r["location"])+.25*num_sim(data.get("area_sqft"),r["area_sqft"])+\
              .15*num_sim(data.get("rent_inr") or data.get("sale_price_inr"),r["rent_inr"] or r["sale_price_inr"])+\
              .15*(1 if data.get("floor") not in (None,"UNKNOWN") and data.get("floor")==r["floor"] else 0)+\
              .10*(1 if data.get("property_type")==r["property_type"] else 0)
        if score>best[0]:best=(score,r["wa_property_id"])
    return best

def confidence(data, base):
    present=sum(1 for v in data.values() if v not in (None,"","UNKNOWN"))
    return round(min(99,base*100+min(14,present)),2)

def upsert_contact(c,name,ph,ctype,seen,group,loc,typ,is_property):
    if not ph:return
    existing=c.execute(text("SELECT * FROM wa_contacts WHERE phone=:p"),{"p":ph}).mappings().first()
    if existing:
        c.execute(text("""UPDATE wa_contacts SET name=COALESCE(NULLIF(name,'UNKNOWN'),:n),
          contact_type=CASE WHEN contact_type='UNKNOWN' THEN :ct ELSE contact_type END,
          last_seen=:seen, groups=:g, locations=:loc, property_types=:typ,
          properties_shared=properties_shared+:ps, requirements_shared=requirements_shared+:rs, updated_at=NOW()
          WHERE phone=:p"""),{"n":name,"ct":ctype,"seen":seen,"g":group,"loc":loc,"typ":typ,"ps":1 if is_property else 0,"rs":0 if is_property else 1,"p":ph})
    else:
        c.execute(text("""INSERT INTO wa_contacts(contact_id,name,phone,contact_type,first_seen,last_seen,groups,locations,property_types,properties_shared,requirements_shared)
          VALUES(:id,:n,:p,:ct,:seen,:seen,:g,:loc,:typ,:ps,:rs)"""),
          {"id":"WAC-"+uuid.uuid4().hex[:10].upper(),"n":name or "UNKNOWN","p":ph,"ct":ctype,"seen":seen,"g":group,"loc":loc,"typ":typ,"ps":1 if is_property else 0,"rs":0 if is_property else 1})

def match_score(req,prop):
    score=0; reasons=[]
    req_loc=str(req["preferred_locations"] or "").lower(); p_loc=str(prop["location"] or "").lower()
    if req_loc and p_loc and (req_loc in p_loc or p_loc in req_loc):score+=30;reasons.append("Strong location match")
    elif sim(req_loc,p_loc)>=.70:score+=22;reasons.append("Similar location")
    a=prop["area_sqft"]; mn=req["minimum_area_sqft"]; mx=req["maximum_area_sqft"]
    if a and mn and mx and float(mn)<=float(a)<=float(mx):score+=20;reasons.append("Area fits")
    elif a and (mn or mx):
        target=float(mn or mx)
        if num_sim(float(a),target)>=.80:score+=12;reasons.append("Area near requirement")
    if req["property_type"]!="UNKNOWN" and req["property_type"]==prop["property_type"]:score+=10;reasons.append("Property type")
    if req["transaction_type"]==prop["transaction_type"]:score+=10;reasons.append("Transaction")
    budget=req["budget_max_inr"]; price=prop["rent_inr"] if prop["transaction_type"]=="RENT" else prop["sale_price_inr"]
    if budget and price and float(price)<=float(budget):score+=15;reasons.append("Within budget")
    if prop["verification_status"]=="VERIFIED_AVAILABLE":score+=10;reasons.append("Verified available")
    elif prop["availability"]=="AVAILABLE":score+=5;reasons.append("Availability signal")
    if prop["sender_phone"] or prop["broker_phone"] or prop["owner_phone"]:score+=5;reasons.append("Contact available")
    grade="EXCELLENT" if score>=90 else "STRONG" if score>=80 else "POSSIBLE" if score>=70 else "WEAK"
    return min(score,100),grade,reasons

def mark_legacy_combined_properties():
    """Hide old pre-upgrade rows that still contain multiple physical properties."""
    if wa_engine is None:
        return
    with wa_engine.begin() as c:
        rows=c.execute(text("""SELECT wa_property_id,raw_text FROM wa_properties
                               WHERE COALESCE(record_status,'ACTIVE')='ACTIVE'
                                 AND source_item_no IS NULL""")).mappings().all()
        for row in rows:
            try:
                if len(split_inventory(row["raw_text"] or "")) > 1:
                    c.execute(text("UPDATE wa_properties SET record_status='LEGACY_COMBINED',updated_at=NOW() WHERE wa_property_id=:p"),
                              {"p":row["wa_property_id"]})
            except Exception:
                continue

@router.on_event("startup")
def wa_startup():
    if wa_engine is not None:
        try:
            init_wa_db()
            mark_legacy_combined_properties()
        except Exception as e:print("WhatsApp Intelligence DB init warning:",e)

@router.get("",response_class=HTMLResponse)
def dashboard():
    require_wa_db(); init_wa_db()
    with wa_engine.begin() as c:
        stats={}
        for key,q in {
            "Messages":"SELECT COUNT(*) FROM wa_messages",
            "Properties":"SELECT COUNT(*) FROM wa_properties WHERE COALESCE(record_status,'ACTIVE')='ACTIVE' AND duplicate_status<>'DUPLICATE'",
            "Requirements":"SELECT COUNT(*) FROM wa_requirements",
            "Contacts":"SELECT COUNT(*) FROM wa_contacts",
            "Verified":"SELECT COUNT(*) FROM wa_properties WHERE verification_status='VERIFIED_AVAILABLE'",
            "Review":"SELECT COUNT(*) FROM wa_review_queue WHERE status='OPEN'",
            "Rejected":"SELECT COUNT(*) FROM wa_rejected",
            "Duplicates":"SELECT COUNT(*) FROM wa_properties WHERE duplicate_status<>'UNIQUE'"
        }.items():stats[key]=c.execute(text(q)).scalar() or 0
        recent=c.execute(text("SELECT * FROM wa_sources ORDER BY id DESC LIMIT 10")).mappings().all()
    cards="".join(f'<div class="card"><div class="muted">{k}</div><div class="num">{v}</div></div>' for k,v in stats.items())
    rows="".join(f"<tr><td>{esc(r['created_at'])}</td><td>{esc(r['group_name'])}</td><td>{r['total_messages']}</td><td>{r['inventory_found']}</td><td>{r['requirements_found']}</td><td>{r['review_found']}</td><td>{r['rejected_found']}</td></tr>" for r in recent)
    body=f"""<div style="display:flex;justify-content:space-between;align-items:center"><div><h2>Command Centre</h2><p class=muted>This module does not write to your main property tables.</p></div><a class=btn href="/whatsapp-intelligence/import">Import WhatsApp</a></div>
    <div class=grid>{cards}</div><h3>Recent Imports</h3><div class=scroll><table><tr><th>Date</th><th>Group/File</th><th>Messages</th><th>Inventory</th><th>Requirements</th><th>Review</th><th>Rejected</th></tr>{rows}</table></div>"""
    return HTMLResponse(shell("WhatsApp Intelligence",body,"Dashboard"))

@router.get("/import",response_class=HTMLResponse)
def import_page():
    body="""<h2>Import WhatsApp Chat</h2><div class=card><form method=post enctype=multipart/form-data>
    <label>Group / Source Name</label><input name=group_name placeholder="e.g. South Delhi Commercial Brokers"><br><br>
    <label>WhatsApp .txt Export</label><input type=file name=chat_file accept=".txt" required><br><br>
    <button class=btn type=submit>Process Into Separate WhatsApp Database</button></form>
    <p class=muted>On WhatsApp: open group → More → Export chat → Without media → upload the .txt file.</p></div>"""
    return HTMLResponse(shell("Import WhatsApp",body,"Import"))

@router.post("/import")
async def process_import(group_name: str=Form(""), chat_file: UploadFile=File(...)):
    require_wa_db(); init_wa_db()
    if not (chat_file.filename or "").lower().endswith(".txt"):
        raise HTTPException(400,"Please upload a .txt WhatsApp export")
    raw=(await chat_file.read()).decode("utf-8-sig",errors="replace")
    msgs=parse_chat(raw)
    sid=uuid.uuid4()
    counts={"inventory":0,"requirements":0,"contacts":0,"duplicates":0,"review":0,"rejected":0}
    source_name=group_name.strip() or (chat_file.filename or "WhatsApp Export")
    with wa_engine.begin() as c:
        c.execute(text("""INSERT INTO wa_sources(source_id,source_name,original_filename,group_name,ingestion_status,total_messages)
        VALUES(:sid,:sn,:fn,:g,'PROCESSING',:n)"""),{"sid":sid,"sn":source_name,"fn":chat_file.filename,"g":source_name,"n":len(msgs)})
        for m in msgs:
            mid=uuid.uuid4(); rawtxt=m["text"]; noise,reason=is_noise(rawtxt)
            if noise:
                c.execute(text("""INSERT INTO wa_messages(message_id,source_id,message_timestamp,sender_name,sender_phone,raw_text,classification,confidence,rejection_reason)
                VALUES(:mid,:sid,:ts,:sn,:sp,:raw,'REJECTED',99,:reason)"""),
                {"mid":mid,"sid":sid,"ts":m["timestamp"],"sn":m["sender"],"sp":phone(m["sender"]),"raw":rawtxt,"reason":reason})
                c.execute(text("INSERT INTO wa_rejected(message_id,source_id,rejection_reason,raw_text) VALUES(:mid,:sid,:r,:raw)"),
                          {"mid":mid,"sid":sid,"r":reason,"raw":rawtxt})
                counts["rejected"]+=1;continue
            kind,base=classify(rawtxt)
            c.execute(text("""INSERT INTO wa_messages(message_id,source_id,message_timestamp,sender_name,sender_phone,raw_text,classification,confidence)
            VALUES(:mid,:sid,:ts,:sn,:sp,:raw,:k,:conf)"""),
            {"mid":mid,"sid":sid,"ts":m["timestamp"],"sn":m["sender"],"sp":phone(m["sender"]),"raw":rawtxt,"k":kind,"conf":round(base*100,2)})
            if kind=="NEEDS_REVIEW":
                c.execute(text("""INSERT INTO wa_review_queue(message_id,source_id,review_reason,confidence)
                VALUES(:mid,:sid,'Ambiguous property message',:conf)"""),{"mid":mid,"sid":sid,"conf":round(base*100,2)})
                counts["review"]+=1;continue
            if kind=="PROPERTY_CONTACT":
                ph=phone(rawtxt); upsert_contact(c,m["sender"],ph,"UNKNOWN",m["timestamp"],source_name,None,None,True)
                if ph:counts["contacts"]+=1
                continue
            parts=split_inventory(rawtxt) if kind=="PROPERTY_INVENTORY" else [rawtxt]
            parent_broker_name,parent_broker_phone=extract_broker_identity(rawtxt,m["sender"])
            for item_no,part in enumerate(parts, start=1):
                data=deterministic_extract(part,kind,m["sender"])
                data=enrich_missing(data,ai_enrich(part,kind))
                if kind=="PROPERTY_INVENTORY":
                    data["sender_name"]=parent_broker_name or data.get("sender_name")
                    data["sender_phone"]=parent_broker_phone or data.get("sender_phone")
                    if not data.get("broker_name"):
                        data["broker_name"]=parent_broker_name
                    if not data.get("broker_phone"):
                        data["broker_phone"]=parent_broker_phone
                conf=confidence(data,base)
                fp=fingerprint(data,kind)
                if kind=="PROPERTY_INVENTORY":
                    best,dupof=duplicate_candidate(c,data)
                    ds="DUPLICATE" if best>=.88 else "POSSIBLE_DUPLICATE" if best>=.70 else "UNIQUE"
                    if ds!="UNIQUE":counts["duplicates"]+=1
                    if ds=="DUPLICATE" and dupof:
                        c.execute(text("""UPDATE wa_properties SET
                          last_seen=:seen,
                          availability=CASE WHEN :availability='AVAILABLE' THEN 'AVAILABLE' ELSE availability END,
                          broker_name=COALESCE(NULLIF(broker_name,''),:broker_name),
                          broker_phone=COALESCE(NULLIF(broker_phone,''),:broker_phone),
                          sender_name=COALESCE(NULLIF(sender_name,''),:sender_name),
                          sender_phone=COALESCE(NULLIF(sender_phone,''),:sender_phone),
                          updated_at=NOW()
                          WHERE wa_property_id=:dupof"""),
                          dict(data,seen=m["timestamp"],dupof=dupof))
                        ph=data.get("broker_phone") or data.get("owner_phone") or data.get("sender_phone")
                        nm=data.get("broker_name") or data.get("owner_name") or data.get("sender_name")
                        upsert_contact(c,nm,ph,"BROKER",m["timestamp"],source_name,data.get("location"),data.get("property_type"),True)
                        continue
                    pid="WAP-"+uuid.uuid4().hex[:10].upper()
                    c.execute(text("""INSERT INTO wa_properties(
                    wa_property_id,source_id,message_id,source_item_no,parent_message_text,record_status,fingerprint,property_type,transaction_type,city,location,locality,address,landmark,
                    area_sqft,available_area_sqft,floor,frontage,rent_inr,sale_price_inr,cam_inr,possession,parking,suitable_for,nearby_brands,
                    availability,broker_name,broker_phone,owner_name,owner_phone,sender_name,sender_phone,duplicate_status,duplicate_of,confidence,raw_text,first_seen,last_seen)
                    VALUES(:pid,:sid,:mid,:item_no,:parent_raw,'ACTIVE',:fp,:property_type,:transaction_type,:city,:location,:locality,:address,:landmark,:area_sqft,:available_area_sqft,:floor,:frontage,
                    :rent_inr,:sale_price_inr,:cam_inr,:possession,:parking,:suitable_for,:nearby_brands,:availability,:broker_name,:broker_phone,:owner_name,:owner_phone,
                    :sender_name,:sender_phone,:ds,:dupof,:conf,:raw,:seen,:seen)"""),
                    dict(data,pid=pid,sid=sid,mid=mid,item_no=item_no,parent_raw=rawtxt,fp=fp,ds=ds,dupof=dupof,conf=conf,raw=part,seen=m["timestamp"]))
                    ph=data.get("owner_phone") or data.get("broker_phone") or data.get("sender_phone")
                    nm=data.get("owner_name") or data.get("broker_name") or data.get("sender_name")
                    ct="OWNER" if data.get("owner_phone") else "BROKER" if data.get("broker_phone") else "UNKNOWN"
                    upsert_contact(c,nm,ph,ct,m["timestamp"],source_name,data.get("location"),data.get("property_type"),True)
                    counts["inventory"]+=1
                    if ph:counts["contacts"]+=1
                else:
                    rid="WAR-"+uuid.uuid4().hex[:10].upper()
                    c.execute(text("""INSERT INTO wa_requirements(
                    wa_requirement_id,source_id,message_id,fingerprint,client_name,company_name,property_type,transaction_type,city,preferred_locations,
                    minimum_area_sqft,maximum_area_sqft,budget_min_inr,budget_max_inr,floor_preference,frontage_requirement,suitable_category,
                    contact_name,contact_phone,contact_type,confidence,raw_text)
                    VALUES(:rid,:sid,:mid,:fp,:client_name,:company_name,:property_type,:transaction_type,:city,:preferred_locations,:minimum_area_sqft,
                    :maximum_area_sqft,:budget_min_inr,:budget_max_inr,:floor_preference,:frontage_requirement,:suitable_category,:contact_name,:contact_phone,:contact_type,:conf,:raw)"""),
                    dict(data,rid=rid,sid=sid,mid=mid,fp=fp,conf=conf,raw=part))
                    upsert_contact(c,data.get("contact_name"),data.get("contact_phone"),data.get("contact_type"),m["timestamp"],source_name,data.get("preferred_locations"),data.get("property_type"),False)
                    counts["requirements"]+=1
                    if data.get("contact_phone"):counts["contacts"]+=1
        c.execute(text("""UPDATE wa_sources SET ingestion_status='COMPLETED',inventory_found=:i,requirements_found=:rq,contacts_found=:ct,
        duplicates_found=:d,review_found=:rv,rejected_found=:rj,processed_at=NOW() WHERE source_id=:sid"""),
        {"i":counts["inventory"],"rq":counts["requirements"],"ct":counts["contacts"],"d":counts["duplicates"],"rv":counts["review"],"rj":counts["rejected"],"sid":sid})
    return RedirectResponse("/whatsapp-intelligence",303)

def filter_properties(c,q="",location_q="",ptype_q="",txn_q="",verification="",availability="",min_area=None,max_area=None,min_price=None,max_price=None):
    sql="SELECT * FROM wa_properties WHERE duplicate_status<>'DUPLICATE' AND COALESCE(record_status,'ACTIVE')='ACTIVE'"; p={}
    if q:
        sql+=" AND (raw_text ILIKE :q OR sender_name ILIKE :q OR sender_phone ILIKE :q OR broker_phone ILIKE :q OR owner_phone ILIKE :q)";p["q"]=f"%{q}%"
    if location_q:sql+=" AND location ILIKE :loc";p["loc"]=f"%{location_q}%"
    if ptype_q:sql+=" AND property_type ILIKE :pt";p["pt"]=f"%{ptype_q}%"
    if txn_q:sql+=" AND transaction_type=:tx";p["tx"]=txn_q
    if verification:sql+=" AND verification_status=:v";p["v"]=verification
    if availability:sql+=" AND availability=:av";p["av"]=availability
    if min_area is not None:sql+=" AND area_sqft>=:mina";p["mina"]=min_area
    if max_area is not None:sql+=" AND area_sqft<=:maxa";p["maxa"]=max_area
    if min_price is not None:sql+=" AND COALESCE(rent_inr,sale_price_inr)>=:minp";p["minp"]=min_price
    if max_price is not None:sql+=" AND COALESCE(rent_inr,sale_price_inr)<=:maxp";p["maxp"]=max_price
    sql+=" ORDER BY id DESC LIMIT 1000"
    return c.execute(text(sql),p).mappings().all()

@router.get("/properties",response_class=HTMLResponse)
def properties(q:str="",location:str="",property_type:str="",transaction_type:str="",verification_status:str="",availability:str="",
               min_area:Optional[float]=None,max_area:Optional[float]=None,min_price:Optional[float]=None,max_price:Optional[float]=None):
    require_wa_db();init_wa_db()
    with wa_engine.begin() as c:
        rows=filter_properties(c,q,location,property_type,transaction_type,verification_status,availability,min_area,max_area,min_price,max_price)

    cards=[]
    for r in rows:
        pid=esc(r["wa_property_id"])
        contact=r["broker_phone"] or r["owner_phone"] or r["sender_phone"] or "—"
        broker=r["broker_name"] or r["sender_name"] or "Unknown broker"
        price=money(r["rent_inr"]) if r["transaction_type"]=="RENT" else money(r["sale_price_inr"])
        price_label="Rent" if r["transaction_type"]=="RENT" else "Sale Price"
        verification=r["verification_status"] or "UNVERIFIED"
        if verification=="VERIFIED_AVAILABLE":
            verification_html='<span class="good">VERIFIED AVAILABLE</span>'
        elif verification=="VERIFIED_UNAVAILABLE":
            verification_html='<span class="bad">VERIFIED UNAVAILABLE</span>'
        else:
            verification_html='<span class="warn">UNVERIFIED</span>'
        description=esc(r["raw_text"] or "No WhatsApp description available.")

        cards.append(f"""
        <section class="wa-property-card">
          <div class="wa-card-head">
            <div>
              <div class="wa-id">{pid}</div>
              <div class="wa-location">{esc(r['location'] or 'UNKNOWN')}</div>
            </div>
            <div>{verification_html}</div>
          </div>
          <div class="wa-key-grid">
            <div><span>Property Type</span><strong>{esc(r['property_type'] or 'UNKNOWN')}</strong></div>
            <div><span>Transaction</span><strong>{esc(r['transaction_type'] or 'UNKNOWN')}</strong></div>
            <div><span>Area</span><strong>{esc(r['area_sqft'] or '—')} sqft</strong></div>
            <div><span>Floor</span><strong>{esc(r['floor'] or 'UNKNOWN')}</strong></div>
            <div><span>{price_label}</span><strong>{price}</strong></div>
            <div><span>Broker Contact</span><strong>{esc(contact)}</strong></div>
          </div>
          <div class="wa-description">
            <div class="wa-description-title">PROPERTY DESCRIPTION - ORIGINAL WHATSAPP ITEM</div>
            <div class="wa-description-text">{description}</div>
          </div>
          <div class="wa-broker-strip">
            <div><b>Broker:</b> {esc(broker)}</div>
            <div><b>Phone:</b> {esc(contact)}</div>
            <div><b>AI Confidence:</b> {esc(r['confidence'])}%</div>
            <div><b>Last Seen:</b> {esc(r['last_seen'] or '—')}</div>
          </div>
          <div class="wa-actions">
            <a class=btn href="/whatsapp-intelligence/property/{pid}/verify/available">Verified Available</a>
            <a class="btn btn2" href="/whatsapp-intelligence/property/{pid}/verify/unavailable">Mark Unavailable</a>
            <a class="wa-open" href="/whatsapp-intelligence/property/{pid}">Open Details →</a>
          </div>
        </section>""")

    css="""<style>
    .wa-property-list{display:grid;gap:16px}.wa-property-card{background:#fff;border:1px solid #d0d5dd;border-radius:14px;padding:18px;box-shadow:0 2px 7px rgba(16,24,40,.06)}
    .wa-card-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;padding-bottom:12px;border-bottom:1px solid #eaecf0}
    .wa-id{font-size:12px;color:#667085;font-weight:700}.wa-location{font-size:22px;font-weight:800;margin-top:3px}
    .wa-key-grid{display:grid;grid-template-columns:repeat(6,minmax(120px,1fr));gap:10px;margin:14px 0}
    .wa-key-grid>div{background:#f8fafc;border:1px solid #eaecf0;border-radius:9px;padding:10px}
    .wa-key-grid span{display:block;font-size:11px;color:#667085;text-transform:uppercase;margin-bottom:5px}
    .wa-description{border:2px solid #f0b429;background:#fff8db;border-radius:11px;padding:14px 16px;margin:12px 0}
    .wa-description-title{font-size:12px;font-weight:800;color:#8a5800;margin-bottom:8px}
    .wa-description-text{font-size:15px;line-height:1.55;font-weight:650;white-space:pre-wrap;overflow-wrap:anywhere}
    .wa-broker-strip{display:flex;flex-wrap:wrap;gap:10px 22px;padding:10px 12px;background:#eef4ff;border-radius:9px;color:#344054}
    .wa-actions{display:flex;flex-wrap:wrap;gap:9px;align-items:center;margin-top:14px;padding-top:13px;border-top:1px solid #eaecf0}
    .wa-open{font-weight:700;color:#344054;text-decoration:none;margin-left:auto}
    @media(max-width:1100px){.wa-key-grid{grid-template-columns:repeat(3,1fr)}}@media(max-width:650px){.wa-key-grid{grid-template-columns:repeat(2,1fr)}.wa-open{margin-left:0}}
    </style>"""

    body=f"""{css}
    <div style="display:flex;justify-content:space-between;gap:12px;align-items:end;flex-wrap:wrap">
      <div><h2>WhatsApp Property Database</h2><p class=muted>Broker lists are split into individual properties. Exact duplicates are hidden.</p></div>
      <a class="btn btn2" href="/whatsapp-intelligence/brokers">Broker Accounts</a>
    </div>
    <div class=card style="margin-bottom:14px"><form class=gridform method=get>
      <input name=q value="{esc(q)}" placeholder="Keyword / broker / phone / description">
      <input name=location value="{esc(location)}" placeholder="Location">
      <input name=property_type value="{esc(property_type)}" placeholder="Property type">
      <select name=transaction_type><option value="">Rent/Sale</option><option value="RENT">RENT</option><option value="SALE">SALE</option></select>
      <input name=min_area type=number placeholder="Min area sqft"><input name=max_area type=number placeholder="Max area sqft">
      <input name=max_price type=number placeholder="Max rent/sale INR"><button class=btn>Search</button>
    </form></div>
    <div class=muted style="margin-bottom:10px"><b>{len(rows)}</b> individual properties shown</div>
    <div class="wa-property-list">{''.join(cards) if cards else '<div class=card>No properties found.</div>'}</div>"""
    return HTMLResponse(shell("WhatsApp Properties",body,"Properties"))

@router.get("/property/{pid}",response_class=HTMLResponse)
def property_detail(pid:str):
    require_wa_db()
    with wa_engine.begin() as c:r=c.execute(text("SELECT * FROM wa_properties WHERE wa_property_id=:p"),{"p":pid}).mappings().first()
    if not r:raise HTTPException(404,"Property not found")
    contact=r["owner_phone"] or r["broker_phone"] or r["sender_phone"]
    body=f"""<h2>{esc(pid)}</h2><div class=grid>
    <div class=card><b>Location</b><p>{esc(r['location'])}</p><b>Type</b><p>{esc(r['property_type'])}</p><b>Area</b><p>{esc(r['area_sqft'])} sqft</p><b>Floor</b><p>{esc(r['floor'])}</p></div>
    <div class=card><b>Rent</b><p>{money(r['rent_inr'])}</p><b>Sale</b><p>{money(r['sale_price_inr'])}</p><b>Contact for verification</b><p>{esc(contact)}</p><b>Status</b><p>{esc(r['verification_status'])}</p></div></div>
    <p><a class=btn href="/whatsapp-intelligence/property/{pid}/verify/available">Mark Verified Available</a>
    <a class="btn btn2" href="/whatsapp-intelligence/property/{pid}/verify/unavailable">Mark Unavailable</a></p>
    <div class=card><b>Original WhatsApp Message</b><pre>{esc(r['raw_text'])}</pre></div>
    <p class=muted>“Approve to Main Database” is intentionally not automatic. It can be enabled only after mapping your final main-property fields.</p>"""
    return HTMLResponse(shell(pid,body,"Properties"))

@router.get("/property/{pid}/verify/{status}")
def verify_property(pid:str,status:str):
    require_wa_db()
    value={"available":"VERIFIED_AVAILABLE","unavailable":"VERIFIED_UNAVAILABLE","unverified":"UNVERIFIED"}.get(status)
    if not value:raise HTTPException(400,"Invalid status")
    with wa_engine.begin() as c:
        c.execute(text("UPDATE wa_properties SET verification_status=:v,availability=:a,updated_at=NOW() WHERE wa_property_id=:p"),
                  {"v":value,"a":"AVAILABLE" if value=="VERIFIED_AVAILABLE" else "UNAVAILABLE" if value=="VERIFIED_UNAVAILABLE" else "UNKNOWN","p":pid})
        c.execute(text("""INSERT INTO wa_audit_log(entity_type,entity_id,action,details)
        VALUES('PROPERTY',:p,'VERIFICATION_CHANGED',CAST(:d AS JSONB))"""),{"p":pid,"d":json.dumps({"verification_status":value})})
    return RedirectResponse(f"/whatsapp-intelligence/property/{pid}",303)

@router.get("/requirements",response_class=HTMLResponse)
def requirements():
    require_wa_db()
    with wa_engine.begin() as c:rows=c.execute(text("SELECT * FROM wa_requirements ORDER BY id DESC LIMIT 1000")).mappings().all()
    trs="".join(f"""<tr><td>{esc(r['wa_requirement_id'])}</td><td>{esc(r['preferred_locations'])}</td><td>{esc(r['property_type'])}</td>
    <td>{esc(r['minimum_area_sqft'])}–{esc(r['maximum_area_sqft'])}</td><td>{money(r['budget_max_inr'])}</td><td>{esc(r['contact_name'])}</td><td>{esc(r['contact_phone'])}</td>
    <td><a class=btn href="/whatsapp-intelligence/requirement/{esc(r['wa_requirement_id'])}/matches">Find Matches</a></td></tr>""" for r in rows)
    body=f"<h2>WhatsApp Requirements</h2><div class=scroll><table><tr><th>ID</th><th>Locations</th><th>Type</th><th>Area</th><th>Budget</th><th>Contact</th><th>Phone</th><th></th></tr>{trs}</table></div>"
    return HTMLResponse(shell("WhatsApp Requirements",body,"Requirements"))

@router.get("/requirement/{rid}/matches",response_class=HTMLResponse)
def run_matches(rid:str):
    require_wa_db()
    with wa_engine.begin() as c:
        req=c.execute(text("SELECT * FROM wa_requirements WHERE wa_requirement_id=:r"),{"r":rid}).mappings().first()
        if not req:raise HTTPException(404,"Requirement not found")
        props=c.execute(text("""SELECT * FROM wa_properties WHERE duplicate_status<>'DUPLICATE'
        AND COALESCE(record_status,'ACTIVE')='ACTIVE'
        AND verification_status<>'VERIFIED_UNAVAILABLE' ORDER BY id DESC LIMIT 2000""")).mappings().all()
        c.execute(text("DELETE FROM wa_matches WHERE wa_requirement_id=:r"),{"r":rid})
        results=[]
        for p in props:
            s,g,rs=match_score(req,p)
            if s>=40:
                c.execute(text("""INSERT INTO wa_matches(wa_requirement_id,wa_property_id,score,grade,reasons)
                VALUES(:r,:p,:s,:g,CAST(:rs AS JSONB)) ON CONFLICT(wa_requirement_id,wa_property_id)
                DO UPDATE SET score=EXCLUDED.score,grade=EXCLUDED.grade,reasons=EXCLUDED.reasons,created_at=NOW()"""),
                {"r":rid,"p":p["wa_property_id"],"s":s,"g":g,"rs":json.dumps(rs)})
                results.append((p,s,g,rs))
        results.sort(key=lambda x:x[1],reverse=True)
    trs="".join(f"""<tr><td class=score>{s}%</td><td>{esc(g)}</td><td>{esc(p['wa_property_id'])}</td><td>{esc(p['location'])}</td><td>{esc(p['area_sqft'])}</td>
    <td>{money(p['rent_inr'] or p['sale_price_inr'])}</td><td>{esc(p['verification_status'])}</td><td>{esc(", ".join(rs))}</td><td><a href="/whatsapp-intelligence/property/{esc(p['wa_property_id'])}">Open</a></td></tr>""" for p,s,g,rs in results)
    body=f"<h2>Matches: {esc(rid)}</h2><div class=card><pre>{esc(req['raw_text'])}</pre></div><br><div class=scroll><table><tr><th>Score</th><th>Grade</th><th>Property</th><th>Location</th><th>Area</th><th>Price</th><th>Verification</th><th>Why</th><th></th></tr>{trs}</table></div>"
    return HTMLResponse(shell("Requirement Matches",body,"Requirements"))

@router.get("/brokers",response_class=HTMLResponse)
def brokers(q:str=""):
    require_wa_db();init_wa_db()
    sql="""
    SELECT
      COALESCE(NULLIF(broker_name,''), NULLIF(sender_name,''), 'Unknown Broker') AS broker_name,
      COALESCE(NULLIF(broker_phone,''), NULLIF(sender_phone,''), 'No phone') AS broker_phone,
      COUNT(*) AS property_count,
      COUNT(*) FILTER (WHERE verification_status='VERIFIED_AVAILABLE') AS verified_count,
      MAX(last_seen) AS last_seen,
      STRING_AGG(DISTINCT NULLIF(location,'UNKNOWN'), ', ') AS locations
    FROM wa_properties
    WHERE duplicate_status<>'DUPLICATE'
      AND COALESCE(record_status,'ACTIVE')='ACTIVE'
    """
    params={}
    if q:
        sql+=" AND (COALESCE(broker_name,sender_name,'') ILIKE :q OR COALESCE(broker_phone,sender_phone,'') ILIKE :q)"
        params["q"]=f"%{q}%"
    sql+="""
    GROUP BY
      COALESCE(NULLIF(broker_name,''), NULLIF(sender_name,''), 'Unknown Broker'),
      COALESCE(NULLIF(broker_phone,''), NULLIF(sender_phone,''), 'No phone')
    ORDER BY property_count DESC, broker_name
    """
    with wa_engine.begin() as c:
        rows=c.execute(text(sql),params).mappings().all()

    cards=[]
    for r in rows:
        key=str(r["broker_phone"] if r["broker_phone"]!="No phone" else r["broker_name"])
        cards.append(f"""
        <div class=card style="margin-bottom:12px">
          <div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap">
            <div><h3 style="margin:0 0 6px">{esc(r['broker_name'])}</h3>
            <div><b>Contact:</b> {esc(r['broker_phone'])}</div>
            <div class=muted style="margin-top:5px">{esc(r['locations'] or 'Locations not identified')}</div></div>
            <div style="display:flex;gap:18px;text-align:center">
              <div><div class=num>{r['property_count']}</div><small>Properties</small></div>
              <div><div class=num>{r['verified_count']}</div><small>Verified</small></div>
            </div>
          </div>
          <div style="margin-top:10px"><b>Last Seen:</b> {esc(r['last_seen'] or '—')}</div>
          <div style="margin-top:12px"><a class=btn href="/whatsapp-intelligence/properties?q={quote_plus(key)}">View Broker Properties</a></div>
        </div>""")

    body=f"""<div style="display:flex;justify-content:space-between;align-items:end;gap:12px;flex-wrap:wrap">
      <div><h2>Broker Accounts</h2><p class=muted>One broker account with live count of unique properties posted.</p></div>
      <form method=get style="display:flex;gap:8px;min-width:320px"><input name=q value="{esc(q)}" placeholder="Broker name or phone"><button class=btn>Search</button></form>
    </div>
    <div class=card style="margin:14px 0;background:#fff8db;border:1px solid #f0b429">
      Exact duplicates are excluded from broker totals and normal inventory.
    </div>
    {''.join(cards) if cards else '<div class=card>No broker accounts found.</div>'}"""
    return HTMLResponse(shell("Broker Accounts",body,"Brokers"))

@router.get("/contacts",response_class=HTMLResponse)
def contacts(q:str=""):
    require_wa_db()
    sql="SELECT * FROM wa_contacts";p={}
    if q:sql+=" WHERE name ILIKE :q OR phone ILIKE :q OR locations ILIKE :q";p["q"]=f"%{q}%"
    sql+=" ORDER BY id DESC LIMIT 1000"
    with wa_engine.begin() as c:rows=c.execute(text(sql),p).mappings().all()
    trs="".join(f"<tr><td>{esc(r['name'])}</td><td>{esc(r['phone'])}</td><td>{esc(r['contact_type'])}</td><td>{esc(r['locations'])}</td><td>{r['properties_shared']}</td><td>{r['requirements_shared']}</td><td>{esc(r['verification_status'])}</td></tr>" for r in rows)
    body=f"""<h2>WhatsApp Contacts</h2><form method=get class=gridform><input name=q placeholder="Name, phone or location"><button class=btn>Search</button></form><br>
    <div class=scroll><table><tr><th>Name</th><th>Phone</th><th>Role</th><th>Locations</th><th>Properties</th><th>Requirements</th><th>Verified</th></tr>{trs}</table></div>"""
    return HTMLResponse(shell("WhatsApp Contacts",body,"Contacts"))

@router.get("/search",response_class=HTMLResponse)
def search_page():
    body="""<h2>Separate WhatsApp Database Search</h2><div class=card>
    <form method=get action="/whatsapp-intelligence/properties" class=gridform>
    <input name=q placeholder="Any keyword / phone / sender"><input name=location placeholder="Location">
    <input name=property_type placeholder="Property type"><select name=transaction_type><option value="">Rent/Sale</option><option>RENT</option><option>SALE</option></select>
    <input name=min_area type=number placeholder="Minimum area"><input name=max_area type=number placeholder="Maximum area">
    <input name=max_price type=number placeholder="Maximum rent/sale INR"><button class=btn>Search WhatsApp Properties</button></form></div>
    <p class=muted>This search reads only the WhatsApp database. It does not query the existing main property database.</p>"""
    return HTMLResponse(shell("WhatsApp Search",body,"Search"))

@router.get("/review",response_class=HTMLResponse)
def review():
    require_wa_db()
    with wa_engine.begin() as c:
        rows=c.execute(text("""SELECT q.*,m.sender_name,m.sender_phone,m.raw_text FROM wa_review_queue q
        JOIN wa_messages m ON m.message_id=q.message_id WHERE q.status='OPEN' ORDER BY q.id DESC""")).mappings().all()
    trs="".join(f"<tr><td>{esc(r['sender_name'])}</td><td>{esc(r['sender_phone'])}</td><td>{esc(r['raw_text'])}</td><td>{esc(r['review_reason'])}</td><td>{esc(r['confidence'])}%</td></tr>" for r in rows)
    return HTMLResponse(shell("Needs Review",f"<h2>Needs Review</h2><div class=scroll><table><tr><th>Sender</th><th>Phone</th><th>Message</th><th>Reason</th><th>Confidence</th></tr>{trs}</table></div>","Review"))

@router.get("/rejected",response_class=HTMLResponse)
def rejected():
    require_wa_db()
    with wa_engine.begin() as c:rows=c.execute(text("SELECT * FROM wa_rejected ORDER BY id DESC LIMIT 1000")).mappings().all()
    trs="".join(f"<tr><td>{esc(r['rejection_reason'])}</td><td>{esc(r['raw_text'])}</td><td>{esc(r['created_at'])}</td></tr>" for r in rows)
    return HTMLResponse(shell("Rejected Messages",f"<h2>Rejected / Noise</h2><div class=scroll><table><tr><th>Reason</th><th>Original Message</th><th>Date</th></tr>{trs}</table></div>","Rejected"))

@router.get("/export.xlsx")
def export_excel():
    require_wa_db()
    wb=Workbook();wb.remove(wb.active)
    queries=[
        ("WA Properties","SELECT * FROM wa_properties ORDER BY id"),
        ("WA Requirements","SELECT * FROM wa_requirements ORDER BY id"),
        ("WA Contacts","SELECT * FROM wa_contacts ORDER BY id"),
        ("Needs Review","SELECT * FROM wa_review_queue ORDER BY id"),
        ("Rejected","SELECT * FROM wa_rejected ORDER BY id"),
        ("Source Messages","SELECT * FROM wa_messages ORDER BY id")
    ]
    with wa_engine.begin() as c:
        for name,q in queries:
            ws=wb.create_sheet(name)
            rows=c.execute(text(q)).mappings().all()
            if rows:
                headers=list(rows[0].keys());ws.append(headers)
                for r in rows:
                    vals=[]
                    for h in headers:
                        v=r[h]
                        if isinstance(v,(dict,list)):v=json.dumps(v,ensure_ascii=False)
                        vals.append(v)
                    ws.append(vals)
    bio=io.BytesIO();wb.save(bio);bio.seek(0)
    headers={"Content-Disposition":'attachment; filename="whatsapp_property_intelligence.xlsx"'}
    return StreamingResponse(bio,media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers=headers)

@router.get("/health")
def health():
    if wa_engine is None:return JSONResponse({"ok":False,"database":"not_configured","required_env":"WHATSAPP_DATABASE_URL"},status_code=503)
    try:
        with wa_engine.begin() as c:c.execute(text("SELECT 1"))
        return {"ok":True,"database":"connected","ai_enabled":bool(wa_client),"model":WA_GEMINI_MODEL if wa_client else None}
    except Exception as e:
        return JSONResponse({"ok":False,"database":"error","detail":str(e)},status_code=503)
