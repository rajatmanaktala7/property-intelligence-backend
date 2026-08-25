import re,json,hashlib
from sqlalchemy import text

TYPE_MAP={
"retail shop":"RETAIL_SHOP","shop":"RETAIL_SHOP","showroom":"RETAIL_SHOP","retail":"RETAIL_SHOP",
"high street retail":"HIGH_STREET_RETAIL","mall retail":"MALL_RETAIL","office":"OFFICE",
"commercial":"COMMERCIAL","restaurant":"RESTAURANT","cafe":"CAFE","banquet wedding venue":"BANQUET",
"banquet":"BANQUET","hotel":"HOTEL","guest house":"GUEST_HOUSE","lounge":"LOUNGE",
"club":"CLUB","bar":"BAR","farmhouse":"FARMHOUSE","warehouse":"WAREHOUSE","industrial":"INDUSTRIAL",
"land":"LAND","mixed use":"MIXED_USE","residential villa":"VILLA","villa":"VILLA","apartment":"RESIDENTIAL"
}
CONF={"MANUAL_SURVEY":100,"MANUAL":95,"WHATSAPP":65,"NEWSPAPER":60,"WEB_DISCOVERY":55,"LEGACY":45}

def norm(v):
    s=re.sub(r"[^a-z0-9]+"," ",str(v or "").lower()).strip()
    return re.sub(r"\s+"," ",s)

def phone(v):
    d=re.sub(r"\D","",str(v or ""))
    d=d[-10:] if len(d)>=10 else ""
    return d or None

def money(v):
    s=str(v or "").lower().replace(",","")
    m=re.search(r"(\d+(?:\.\d+)?)\s*(crore|cr|lakhs?|lacs?|lac|lakh|k|thousand)?",s)
    if not m:return None
    n=float(m.group(1));u=(m.group(2) or "")
    if u in {"crore","cr"}:n*=10000000
    elif u in {"lakh","lakhs","lac","lacs"}:n*=100000
    elif u in {"k","thousand"}:n*=1000
    return round(n,2)

def area(v):
    s=str(v or "").lower().replace(",","")
    a=[float(x) for x in re.findall(r"\d+(?:\.\d+)?",s)]
    if not a:return None,None
    mult=9 if any(x in s for x in ["sq yd","sqyd","square yard"]) else 10.7639104167 if any(x in s for x in ["sqm","sq m","square meter","square metre"]) else 43560 if "acre" in s else 1
    a=[round(x*mult,2) for x in a[:2]]
    return a[0],a[1] if len(a)>1 else a[0]

def tx(v):
    s=norm(v)
    if "both" in s or ("sale" in s and ("rent" in s or "lease" in s)):return "LEASE_OR_SALE"
    if any(x in s for x in ["sale","sell","buy"]):return "SALE"
    if any(x in s for x in ["lease","rent"]):return "LEASE"
    return "UNKNOWN"

def ptype(v):
    if isinstance(v,list):v=v[0] if v else ""
    s=norm(v)
    return TYPE_MAP.get(s,s.upper().replace(" ","_") if s else "UNKNOWN")

def pick(r,*ks):
    for k in ks:
        if k in r and r[k] not in (None,"","NA","N/A","Unknown","-"):
            return r[k]

def num(v):
    try:return float(v) if v is not None else None
    except:return None

def pid(t,r):return "PID-"+hashlib.sha1(f"{t}:{r}".encode()).hexdigest()[:16].upper()
def cid(p):return "CNT-"+hashlib.sha1(p.encode()).hexdigest()[:16].upper()

def contact(c,p,n=None,co=None,cf=60):
    p=phone(p)
    if not p:return None
    i=cid(p)
    c.execute(text("""INSERT INTO ai_contact_identity(contact_id,normalized_phone,display_name,company_name,confidence)
    VALUES(:i,:p,:n,:co,:cf)
    ON CONFLICT(contact_id) DO UPDATE SET
      display_name=COALESCE(EXCLUDED.display_name,ai_contact_identity.display_name),
      company_name=COALESCE(EXCLUDED.company_name,ai_contact_identity.company_name),
      confidence=GREATEST(ai_contact_identity.confidence,EXCLUDED.confidence),
      updated_at=NOW()"""),{"i":i,"p":p,"n":n,"co":co,"cf":cf})
    return i

def req_types(v):
    if isinstance(v,list):return [ptype(x) for x in v]
    if isinstance(v,str):
        try:
            q=json.loads(v)
            if isinstance(q,list):return [ptype(x) for x in q]
        except:pass
        return [ptype(x) for x in re.split(r"[,;/|]+",v) if x.strip()]
    return []

def frontage_ft(v):
    s=str(v or "").lower().replace(",","")
    if not s:return None
    m=re.search(r"(\d+(?:\.\d+)?)\s*(?:ft|feet|foot|')",s)
    if m:return float(m.group(1))
    m=re.search(r"frontage\s*[:=-]?\s*(\d+(?:\.\d+)?)",s)
    return float(m.group(1)) if m else None

def infer_frontage(text_value):
    s=str(text_value or "")
    patterns=[
        r"(?:minimum|min\.?|min)?\s*frontage\s*[:=-]?\s*(\d+(?:\.\d+)?)\s*(?:ft|feet|foot|')?",
        r"(\d+(?:\.\d+)?)\s*(?:ft|feet|foot|')\s*(?:frontage|front)"
    ]
    for p in patterns:
        m=re.search(p,s,re.I)
        if m:return float(m.group(1))
    return None

def floor_norm(v):
    s=norm(v)
    if not s:return None
    if any(x in s for x in ["ground floor","ground","gf"]):return "GROUND"
    if any(x in s for x in ["basement","bmt","lower ground","lgf"]):return "BASEMENT"
    if any(x in s for x in ["first floor","1st floor","ff"]):return "FIRST"
    if any(x in s for x in ["second floor","2nd floor","sf"]):return "SECOND"
    if "stilt" in s:return "STILT"
    return s.upper().replace(" ","_")

def infer_required_floor(text_value):
    s=norm(text_value)
    if not s:return None
    if any(x in s for x in ["only ground floor","ground floor only","need ground floor","ground floor"]):return "GROUND"
    if "basement" in s:return "BASEMENT"
    if "first floor" in s:return "FIRST"
    return None

def infer_suitable(text_value):
    s=norm(text_value)
    tags=[]
    for token,label in [
        ("fine dine","FINE_DINE"),("fine dining","FINE_DINE"),("restaurant","RESTAURANT"),
        ("food brand","F&B"),("cafe","CAFE"),("qsr","QSR"),("apparel","APPAREL"),
        ("retail","RETAIL"),("office","OFFICE"),("banquet","BANQUET"),("hotel","HOTEL")
    ]:
        if token in s and label not in tags:tags.append(label)
    return ",".join(tags) if tags else None
