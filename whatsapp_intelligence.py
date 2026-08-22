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

def split_inventory(txt):
    parts=[x.strip() for x in txt.split("|") if x.strip()]
    if len(parts)>1 and sum(any(w in p.lower() for w in PROPERTY_WORDS) for p in parts)>=2:return parts
    numbered=[x.strip() for x in re.split(r"\n(?=\s*(?:\d+[\).\:-]|\*|-)\s*)",txt) if x.strip()]
    if len(numbered)>1 and sum(any(w in p.lower() for w in PROPERTY_WORDS) for p in numbered)>=2:return numbered
    return [txt]

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

@router.on_event("startup")
def wa_startup():
    if wa_engine is not None:
        try:init_wa_db()
        except Exception as e:print("WhatsApp Intelligence DB init warning:",e)

@router.get("",response_class=HTMLResponse)
def dashboard():
    require_wa_db(); init_wa_db()
    with wa_engine.begin() as c:
        stats={}
        for key,q in {
            "Messages":"SELECT COUNT(*) FROM wa_messages",
            "Properties":"SELECT COUNT(*) FROM wa_properties",
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
            for part in parts:
                data=deterministic_extract(part,kind,m["sender"])
                data=enrich_missing(data,ai_enrich(part,kind))
                conf=confidence(data,base)
                fp=fingerprint(data,kind)
                if kind=="PROPERTY_INVENTORY":
                    best,dupof=duplicate_candidate(c,data)
                    ds="DUPLICATE" if best>=.88 else "POSSIBLE_DUPLICATE" if best>=.70 else "UNIQUE"
                    if ds!="UNIQUE":counts["duplicates"]+=1
                    pid="WAP-"+uuid.uuid4().hex[:10].upper()
                    c.execute(text("""INSERT INTO wa_properties(
                    wa_property_id,source_id,message_id,fingerprint,property_type,transaction_type,city,location,locality,address,landmark,
                    area_sqft,available_area_sqft,floor,frontage,rent_inr,sale_price_inr,cam_inr,possession,parking,suitable_for,nearby_brands,
                    availability,broker_name,broker_phone,owner_name,owner_phone,sender_name,sender_phone,duplicate_status,duplicate_of,confidence,raw_text,first_seen,last_seen)
                    VALUES(:pid,:sid,:mid,:fp,:property_type,:transaction_type,:city,:location,:locality,:address,:landmark,:area_sqft,:available_area_sqft,:floor,:frontage,
                    :rent_inr,:sale_price_inr,:cam_inr,:possession,:parking,:suitable_for,:nearby_brands,:availability,:broker_name,:broker_phone,:owner_name,:owner_phone,
                    :sender_name,:sender_phone,:ds,:dupof,:conf,:raw,:seen,:seen)"""),
                    dict(data,pid=pid,sid=sid,mid=mid,fp=fp,ds=ds,dupof=dupof,conf=conf,raw=part,seen=m["timestamp"]))
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
    sql="SELECT * FROM wa_properties WHERE 1=1"; p={}
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
    with wa_engine.begin() as c:rows=filter_properties(c,q,location,property_type,transaction_type,verification_status,availability,min_area,max_area,min_price,max_price)
    forms="""<form class=gridform method=get><input name=q placeholder="Keyword / phone / sender"><input name=location placeholder=Location>
    <input name=property_type placeholder="Property type"><select name=transaction_type><option value="">Rent/Sale</option><option>RENT</option><option>SALE</option></select>
    <select name=verification_status><option value="">Verification</option><option>UNVERIFIED</option><option>VERIFIED_AVAILABLE</option><option>VERIFIED_UNAVAILABLE</option></select>
    <input name=min_area type=number placeholder="Min area sqft"><input name=max_area type=number placeholder="Max area sqft">
    <input name=max_price type=number placeholder="Max rent/sale INR"><button class=btn>Search</button></form><br>"""
    trs=""
    for r in rows:
        contact=r["owner_phone"] or r["broker_phone"] or r["sender_phone"]
        trs+=f"""<tr><td>{esc(r['wa_property_id'])}</td><td>{esc(r['location'])}</td><td>{esc(r['property_type'])}</td><td>{esc(r['area_sqft'])}</td>
        <td>{esc(r['floor'])}</td><td>{money(r['rent_inr'])}</td><td>{money(r['sale_price_inr'])}</td><td>{esc(contact)}</td>
        <td>{esc(r['verification_status'])}</td><td>{esc(r['duplicate_status'])}</td><td>{esc(r['confidence'])}%</td>
        <td><a href="/whatsapp-intelligence/property/{esc(r['wa_property_id'])}">Open</a></td></tr>"""
    body=f"<h2>WhatsApp Property Database</h2>{forms}<div class=scroll><table><tr><th>ID</th><th>Location</th><th>Type</th><th>Area</th><th>Floor</th><th>Rent</th><th>Sale</th><th>Contact</th><th>Verification</th><th>Duplicate</th><th>AI</th><th></th></tr>{trs}</table></div>"
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
