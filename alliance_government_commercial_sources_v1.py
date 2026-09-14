from __future__ import annotations
import io, re, urllib.request
from html import escape
from urllib.parse import urlparse
from sqlalchemy import text

VERSION="1.0.0-GOV-SOURCES-STRICT-CITY"

CITY_ALIASES={
 "gurgaon":("gurgaon","gurugram"),"gurugram":("gurgaon","gurugram"),
 "delhi":("delhi","new delhi"),"new delhi":("delhi","new delhi"),
 "noida":("noida",),"greater noida":("greater noida",),"ghaziabad":("ghaziabad",),"faridabad":("faridabad",)
}

SOURCES={
 "DMRC":[
   ("VACANT_PROPERTIES","https://backend.delhimetrorail.com/documents/10517/Know-About-Vacant-Properties-09.04.2026.pdf","AVAILABLE"),
   ("PROPERTY_DEVELOPMENT_STATUS","https://backend.delhimetrorail.com/documents/8732/property-development-status-june2025.pdf","OPERATIONAL_OR_AWARDED"),
 ],
 "DDA":[
   ("COMMERCIAL_PROPERTIES","https://dda.gov.in/commercial-properties","REFERENCE"),
   ("COMMERCIAL_TENDERS","https://dda.gov.in/tender-documents-commercial-properties","TENDER"),
   ("BUILT_UP_SHOPS","https://dda.gov.in/tender-documents-built-unitsshops","TENDER"),
 ],
 "NDMC":[
   ("ESTATE_COMMERCIAL","https://www.ndmc.gov.in/departments/estate_allotment_of_shops.aspx","REFERENCE"),
 ],
}

DOMAINS={
 "DMRC":("delhimetrorail.com","backend.delhimetrorail.com"),
 "DDA":("dda.gov.in","www.dda.gov.in"),
 "NDMC":("ndmc.gov.in","www.ndmc.gov.in"),
 "MCD":("mcdonline.nic.in","mcdonline.gov.in","mcd.nic.in"),
 "RLDA":("rlda.indianrailways.gov.in",),
 "AAI":("aai.aero",),
 "NOIDA":("noidaauthorityonline.in","noidaauthorityonline.com"),
 "GNIDA":("greaternoidaauthority.in",),
 "YEIDA":("yamunaexpresswayauthority.com",),
}

SCHEMA=r'''
CREATE TABLE IF NOT EXISTS aci_gov_source_documents(
 id BIGSERIAL PRIMARY KEY,authority TEXT NOT NULL,source_type TEXT NOT NULL,source_url TEXT NOT NULL,
 document_status TEXT,title TEXT,fetch_status TEXT DEFAULT 'OK',notes TEXT,fetched_at TIMESTAMPTZ DEFAULT NOW(),
 UNIQUE(authority,source_url));
CREATE TABLE IF NOT EXISTS aci_gov_developer_portfolio(
 id BIGSERIAL PRIMARY KEY,authority TEXT NOT NULL,developer_name TEXT NOT NULL,asset_code TEXT,asset_name TEXT,
 city TEXT,location TEXT,relationship_status TEXT,phone TEXT,email TEXT,source_url TEXT NOT NULL,
 evidence_text TEXT,last_verified_at TIMESTAMPTZ DEFAULT NOW(),
 UNIQUE(authority,developer_name,asset_name,source_url));
'''

def _clean(v): return re.sub(r"\s+"," ",str(v or "")).strip()
def _norm(v): return _clean(v).lower()

def ensure_schema(engine):
    with engine.begin() as c:
        for stmt in [x.strip() for x in SCHEMA.split(";") if x.strip()]:
            c.execute(text(stmt))

def _get(url):
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 AllianceCommercialIntelligence/1.0"})
    with urllib.request.urlopen(req,timeout=30) as r:
        return r.read(),r.headers.get("Content-Type","")

def _pdf_text(data):
    try:
        import pymupdf
        d=pymupdf.open(stream=data,filetype="pdf")
        return "\n".join(p.get_text("text") for p in d)
    except Exception:
        try:
            import fitz
            d=fitz.open(stream=data,filetype="pdf")
            return "\n".join(p.get_text("text") for p in d)
        except Exception:
            from pypdf import PdfReader
            return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)

def _html_text(data):
    s=data.decode("utf-8","ignore")
    s=re.sub(r"(?is)<script.*?</script>|<style.*?</style>"," ",s)
    s=re.sub(r"(?i)<br\s*/?>|</p>|</tr>|</li>|</h\d>","\n",s)
    s=re.sub(r"(?s)<[^>]+>"," ",s)
    return "\n".join(_clean(x) for x in s.splitlines() if _clean(x))

def _phones(blob):
    out=[]
    for m in re.finditer(r"(?<!\d)(?:\+?91[\s.-]?)?([6-9]\d(?:[\s.-]?\d){8})(?!\d)",blob or ""):
        d=re.sub(r"\D","",m.group(1))
        if len(d)==10 and d not in out: out.append(d)
    return out[:4]

def _email(blob):
    m=re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",blob or "",re.I)
    return m.group(0) if m else None

def _area(blob):
    m=re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(sq\.?\s*m\.?|sqm|sq\s*mt|sq\.?\s*meter|sq\.?\s*ft\.?|sqft)",blob or "",re.I)
    return _clean(m.group(0)) if m else None

def _city(blob):
    low=_norm(blob)
    for c,als in [("Gurugram",("gurgaon","gurugram")),("Greater Noida",("greater noida",)),("Noida",("noida",)),("Ghaziabad",("ghaziabad",)),("Faridabad",("faridabad",)),("Delhi",("delhi","new delhi"))]:
        if any(a in low for a in als): return c
    return "Delhi"

def _developer(blob):
    pats=[
      r"(?:developer|lessee|licensee)\s*[:\-]\s*([^\n,;]{3,100})",
      r"(?:M/s\.?\s*)?([A-Z][A-Za-z0-9&.,'() \-]{2,80}(?:Ltd\.?|Limited|Pvt\.?\s*Ltd\.?|Projects|Buildwell|Infrastructure|Infracon|Developers|Group))"
    ]
    for p in pats:
        m=re.search(p,blob or "",re.I)
        if m:
            x=_clean(m.group(1)).strip(" .,-")
            if len(x)>=3:return x[:180]
    return None

def _chunks(blob):
    lines=[_clean(x) for x in (blob or "").splitlines() if _clean(x)]
    out=[];cur=[]
    for line in lines:
        if re.match(r"^\d{1,3}[\.\)]\s+",line) and cur:
            out.append("\n".join(cur));cur=[line]
        else: cur.append(line)
        if len(cur)>=8 and (_area("\n".join(cur)) or _phones("\n".join(cur))):
            out.append("\n".join(cur));cur=[]
    if cur: out.append("\n".join(cur))
    return [x for x in out if _area(x) or _phones(x) or re.search(r"\b(lease|vacant|commercial|shop|office|property|developer|lessee|tender|auction)\b",x,re.I)][:250]

def _name(chunk,authority):
    for line in [_clean(x) for x in chunk.splitlines() if _clean(x)][:8]:
        x=re.sub(r"^\d+[\.\)]?\s*","",line).strip(" :-")
        if len(x)>=4 and not re.match(r"^(area|contact|phone|mobile|email)\b",x,re.I):
            return x[:220]
    return authority+" Commercial Property"

def _save_asset(c,authority,stype,url,status,chunk):
    import hashlib
    name=_name(chunk,authority);city=_city(chunk);dev=_developer(chunk)
    code="GOV-"+hashlib.sha1(f"{authority}|{name}|{url}".lower().encode()).hexdigest()[:12].upper()
    conf=95 if stype=="VACANT_PROPERTIES" else 85
    c.execute(text('''
      INSERT INTO aci_intel_assets(asset_code,asset_name,asset_class,city,location,developer_or_authority,
       lifecycle_status,official_url,source_url,source_provider,confidence,last_researched_at,visibility_status,purity_score,purity_reason)
      VALUES(:code,:name,'GOVERNMENT_PREMISES',:city,:loc,:org,:life,:url,:url,:prov,:conf,NOW(),'ACTIVE',100,:reason)
      ON CONFLICT(asset_code) DO UPDATE SET asset_name=EXCLUDED.asset_name,city=EXCLUDED.city,location=EXCLUDED.location,
       developer_or_authority=EXCLUDED.developer_or_authority,lifecycle_status=EXCLUDED.lifecycle_status,
       official_url=EXCLUDED.official_url,source_url=EXCLUDED.source_url,source_provider=EXCLUDED.source_provider,
       confidence=GREATEST(aci_intel_assets.confidence,EXCLUDED.confidence),last_researched_at=NOW(),
       visibility_status='ACTIVE',purity_score=100,purity_reason=EXCLUDED.purity_reason,updated_at=NOW()
    '''),{"code":code,"name":name,"city":city,"loc":name,"org":dev or authority,"life":status,"url":url,"prov":"OFFICIAL_"+authority,"conf":conf,"reason":f"Official {authority} source: {stype}"})
    ar=_area(chunk)
    if status in ("AVAILABLE","TENDER"):
        c.execute(text('''
          INSERT INTO aci_intel_vacancies(asset_code,availability_status,area_text,permitted_use,source_url,confidence,last_verified_at)
          SELECT :a,:s,:ar,'Subject to official authority/tender terms',:u,:cf,NOW()
          WHERE NOT EXISTS(SELECT 1 FROM aci_intel_vacancies WHERE asset_code=:a AND source_url=:u AND COALESCE(area_text,'')=COALESCE(:ar,''))
        '''),{"a":code,"s":"REPORTED_AVAILABLE" if status=="AVAILABLE" else status,"ar":ar,"u":url,"cf":conf})
    phs=_phones(chunk);em=_email(chunk)
    for ph in phs or [None]:
        if ph or em:
            c.execute(text('''
              INSERT INTO aci_intel_contacts(asset_code,phone,email,company_name,source_url,verification_status,confidence,last_verified_at)
              SELECT :a,:p,:e,:co,:u,'OFFICIAL_SOURCE',:cf,NOW()
              WHERE NOT EXISTS(SELECT 1 FROM aci_intel_contacts WHERE asset_code=:a AND COALESCE(phone,'')=COALESCE(:p,'') AND COALESCE(email,'')=COALESCE(:e,''))
            '''),{"a":code,"p":ph,"e":em,"co":dev or authority,"u":url,"cf":conf})
    if dev and dev.upper() not in ("DMRC","DDA","NDMC","MCD"):
        c.execute(text('''
          INSERT INTO aci_gov_developer_portfolio(authority,developer_name,asset_code,asset_name,city,location,relationship_status,phone,email,source_url,evidence_text,last_verified_at)
          VALUES(:au,:d,:ac,:an,:city,:loc,:rs,:p,:e,:u,:ev,NOW())
          ON CONFLICT(authority,developer_name,asset_name,source_url) DO UPDATE SET
           asset_code=EXCLUDED.asset_code,phone=COALESCE(EXCLUDED.phone,aci_gov_developer_portfolio.phone),
           email=COALESCE(EXCLUDED.email,aci_gov_developer_portfolio.email),evidence_text=EXCLUDED.evidence_text,last_verified_at=NOW()
        '''),{"au":authority,"d":dev,"ac":code,"an":name,"city":city,"loc":name,"rs":status,"p":phs[0] if phs else None,"e":em,"u":url,"ev":_clean(chunk)[:2500]})
    c.execute(text('''
      INSERT INTO aci_intel_evidence(asset_code,evidence_type,title,snippet,source_url,source_provider,captured_at)
      VALUES(:a,'OFFICIAL_GOV_SOURCE',:t,:s,:u,:p,NOW())
      ON CONFLICT(asset_code,evidence_type,source_url) DO UPDATE SET title=EXCLUDED.title,snippet=EXCLUDED.snippet,captured_at=NOW()
    '''),{"a":code,"t":authority+" "+stype,"s":_clean(chunk)[:3000],"u":url,"p":"OFFICIAL_"+authority})

def sync_government_sources(engine,search_func=None):
    ensure_schema(engine)
    result={"sources":0,"assets":0,"errors":[]}
    for authority,items in SOURCES.items():
        for stype,url,status in items:
            try:
                data,ctype=_get(url)
                blob=_pdf_text(data) if ("pdf" in ctype.lower() or url.lower().endswith(".pdf")) else _html_text(data)
                chunks=_chunks(blob)
                with engine.begin() as c:
                    c.execute(text('''
                      INSERT INTO aci_gov_source_documents(authority,source_type,source_url,document_status,title,fetch_status,fetched_at)
                      VALUES(:a,:t,:u,:s,:title,'OK',NOW())
                      ON CONFLICT(authority,source_url) DO UPDATE SET source_type=EXCLUDED.source_type,document_status=EXCLUDED.document_status,title=EXCLUDED.title,fetch_status='OK',fetched_at=NOW()
                    '''),{"a":authority,"t":stype,"u":url,"s":status,"title":authority+" "+stype})
                    for ch in chunks:
                        _save_asset(c,authority,stype,url,status,ch);result["assets"]+=1
                result["sources"]+=1
            except Exception as e:
                result["errors"].append(f"{authority} {stype}: {type(e).__name__}: {e}")
    if callable(search_func):
        for authority in ("MCD","RLDA","AAI","NOIDA","GNIDA","YEIDA"):
            try:
                rows,_=search_func([f'{authority} commercial property lease tender auction shops office'],True)
                for r in (rows or [])[:40]:
                    u=_clean(r.get("url"));d=(urlparse(u).hostname or "").lower()
                    if not any(d==x or d.endswith("."+x) for x in DOMAINS.get(authority,())): continue
                    blob=f"{_clean(r.get('title'))}\n{_clean(r.get('snippet'))}"
                    if not re.search(r"\b(commercial|property|shop|office|retail|lease|tender|auction|vacant)\b",blob,re.I): continue
                    with engine.begin() as c:_save_asset(c,authority,"OFFICIAL_WEB_DISCOVERY",u,"TENDER" if re.search(r"\b(tender|auction)\b",blob,re.I) else "REFERENCE",blob)
                    result["assets"]+=1
            except Exception as e: result["errors"].append(f"{authority}: {type(e).__name__}: {e}")
    return result

def research_government_asset(engine,code,search_func):
    ensure_schema(engine)
    with engine.connect() as c:
        a=c.execute(text("SELECT * FROM aci_intel_assets WHERE asset_code=:a"),{"a":code}).mappings().first()
    if not a:return
    q=f'"{_clean(a.get("asset_name"))}" commercial lease tender developer lessee contact phone'
    try: rows,_=search_func([q],True)
    except Exception: rows=[]
    with engine.begin() as c:
        for r in (rows or [])[:40]:
            blob=f"{_clean(r.get('title'))} {_clean(r.get('snippet'))}"
            for ph in _phones(blob):
                c.execute(text('''
                  INSERT INTO aci_intel_contacts(asset_code,phone,company_name,source_url,verification_status,confidence,last_verified_at)
                  SELECT :a,:p,:co,:u,'PUBLIC_REPORTED',65,NOW()
                  WHERE NOT EXISTS(SELECT 1 FROM aci_intel_contacts WHERE asset_code=:a AND phone=:p)
                '''),{"a":code,"p":ph,"co":_clean(a.get("developer_or_authority")),"u":_clean(r.get("url"))})
        c.execute(text("UPDATE aci_intel_assets SET last_researched_at=NOW(),updated_at=NOW() WHERE asset_code=:a"),{"a":code})

def _parse(city,view):
    raw=_clean(city);low=raw.lower();v=(view or "ALL").upper()
    if re.search(r"\bmalls?\b",low):
        v="MALLS";low=re.sub(r"\bmalls?\b"," ",low)
    low=re.sub(r"\b(details?|commercial|properties|property|in|at|of|show|only)\b"," ",low)
    city2=_clean(low)
    aliases=CITY_ALIASES.get(city2.lower(),(city2.lower(),) if city2 else ())
    return v,city2,aliases

def render_commercial(engine,view,city,message=""):
    ensure_schema(engine)
    v,city2,aliases=_parse(city,view)
    where=["COALESCE(visibility_status,'ACTIVE')='ACTIVE'"];p={}
    if v=="MALLS":where.append("asset_class='MALL'")
    elif v in ("GOV","GOVERNMENT"):where.append("asset_class='GOVERNMENT_PREMISES'")
    elif v=="RESEARCH":where.append("(last_researched_at IS NULL OR confidence<60)")
    if aliases:
        ors=[]
        for i,a in enumerate(aliases):
            p[f"c{i}"]=a
            ors.append(f"LOWER(TRIM(COALESCE(city,'')))=:c{i}")
            ors.append(f"(COALESCE(city,'')='' AND LOWER(COALESCE(location,'')) LIKE '%' || :c{i} || '%')")
        where.append("("+" OR ".join(ors)+")")
    with engine.connect() as c:
        assets=[dict(x) for x in c.execute(text(f"SELECT * FROM aci_intel_assets WHERE {' AND '.join(where)} ORDER BY confidence DESC,asset_name LIMIT 500"),p).mappings().all()]
        devs=[dict(x) for x in c.execute(text("SELECT authority,developer_name,COUNT(*) property_count,STRING_AGG(DISTINCT COALESCE(phone,''),', ') phones FROM aci_gov_developer_portfolio GROUP BY authority,developer_name ORDER BY COUNT(*) DESC,developer_name LIMIT 100")).mappings().all()]
    cards=[]
    for a in assets:
        with engine.connect() as c:
            vac=[dict(x) for x in c.execute(text("SELECT * FROM aci_intel_vacancies WHERE asset_code=:a ORDER BY last_verified_at DESC NULLS LAST LIMIT 5"),{"a":a["asset_code"]}).mappings().all()]
            con=[dict(x) for x in c.execute(text("SELECT * FROM aci_intel_contacts WHERE asset_code=:a ORDER BY confidence DESC,last_verified_at DESC NULLS LAST LIMIT 6"),{"a":a["asset_code"]}).mappings().all()]
        vh="".join(f"<div><b>{escape(_clean(x.get('availability_status')))}</b> {escape(_clean(x.get('area_text')))} {escape(_clean(x.get('rent_text')))}</div>" for x in vac) or "No verified availability."
        ch="".join(f"<div><b>{escape(_clean(x.get('company_name') or x.get('contact_name')))}</b> {escape(_clean(x.get('phone')))} {escape(_clean(x.get('email')))}</div>" for x in con) or "No public contact verified."
        src=f'<a class="btn" target="_blank" href="{escape(_clean(a.get("source_url")))}">Open source</a>' if a.get("source_url") else ""
        cards.append(f'''<div class="card"><h2>{escape(_clean(a.get("asset_name")))}</h2><p>{escape(_clean(a.get("city")))} · {escape(_clean(a.get("location")))} · <b>{escape(_clean(a.get("developer_or_authority")))}</b></p><div class="grid"><div><h3>Status</h3>{escape(_clean(a.get("lifecycle_status")))}</div><div><h3>Availability</h3>{vh}</div><div><h3>Developer / Contact</h3>{ch}</div></div><div class="bar"><form method="post" action="/commercial-intelligence/research/{escape(a["asset_code"])}"><button>Research this asset</button></form>{src}</div></div>''')
    devhtml=""
    if v in ("ALL","GOV","GOVERNMENT") and devs:
        rows="".join(f"<tr><td>{escape(_clean(x['authority']))}</td><td>{escape(_clean(x['developer_name']))}</td><td>{x['property_count']}</td><td>{escape(_clean(x.get('phones')))}</td></tr>" for x in devs)
        devhtml=f'''<div class="card"><h2>Developer / Lessee Portfolio</h2><p>Historical developer relationships are kept separate from current vacancy status.</p><table><tr><th>Authority</th><th>Developer</th><th>Properties</th><th>Public Phones</th></tr>{rows}</table></div>'''
    note=f"{len(assets)} result(s). "+(f"Strict filter: {city2} / {v}" if city2 else f"View: {v}")
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Commercial Intelligence</title><style>*{{box-sizing:border-box}}body{{font-family:Arial;margin:0;background:#f4f7fb;color:#172437}}header{{background:#102235;color:white;padding:18px}}.wrap{{max-width:1500px;margin:auto;padding:18px}}.bar{{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}}.btn,button{{padding:9px 12px;border:0;border-radius:8px;background:#1677ff;color:white;text-decoration:none;font-weight:bold;cursor:pointer}}input{{padding:9px;border:1px solid #ccd6e0;border-radius:8px;min-width:230px}}.card{{background:white;border:1px solid #e1e7ee;border-radius:14px;padding:16px;margin:12px 0}}.grid{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}}.grid>div{{background:#f8fafc;padding:10px;border-radius:10px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:8px;border-bottom:1px solid #e7edf3;text-align:left}}.note{{background:#fff6df;padding:10px;border-radius:9px}}@media(max-width:900px){{.grid{{grid-template-columns:1fr}}}}</style></head><body><header><a class="btn" style="float:right;background:white;color:#102235" href="/team-dashboard-v376">Back to Main Dashboard</a><h1>Commercial Intelligence</h1><div>Malls + Government Commercial Opportunities + Developer Intelligence</div></header><div class="wrap"><div class="bar"><a class="btn" href="/commercial-intelligence?view=ALL">All</a><a class="btn" href="/commercial-intelligence?view=MALLS">Malls</a><a class="btn" href="/commercial-intelligence?view=GOV">Government</a><form method="get" action="/commercial-intelligence"><input type="hidden" name="view" value="{escape(v)}"><input name="city" value="{escape(city or '')}" placeholder="e.g. Gurgaon malls"><button>Filter</button></form><form method="post" action="/commercial-intelligence/government-sync"><button>Sync Government Sources</button></form></div><div class="note">{escape(note)}. Gurgaon/Gurugram mall searches are restricted to Gurgaon/Gurugram mall records only.</div>{devhtml}{''.join(cards) if cards else '<div class="card"><h2>No matching results</h2><p>Run research/sync or change the filter.</p></div>'}</div></body></html>'''
