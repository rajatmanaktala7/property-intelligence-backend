from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION = "3.0.0-MALL-BRAND-PREMISES-INTELLIGENCE"

TARGET_CITIES = [
    "Delhi", "Gurugram", "Noida", "Greater Noida", "Ghaziabad", "Faridabad",
    "Chandigarh", "Mohali", "Ludhiana", "Amritsar", "Jalandhar", "Jaipur",
    "Udaipur", "Lucknow", "Kanpur", "Agra", "Dehradun",
]

GOV_SOURCE_CODES = {"DMRC", "DDA", "NOIDA", "GNIDA", "YEIDA", "RLDA", "IREPS", "AAI", "CPPP", "MSTC", "RIICO"}
NOISE_DOMAINS = {
    "indeed.com", "naukri.com", "apna.co", "glassdoor.com", "jooble.org",
    "jobsora.com", "wikipedia.org", "wanderlog.com", "holidify.com", "tripadvisor.in",
}

BRAND_CATALOG = {
    "Fashion": ["Zara", "H&M", "Uniqlo", "Marks & Spencer", "Lifestyle", "Shoppers Stop", "Westside", "Pantaloons", "Levi's", "Tommy Hilfiger", "Calvin Klein", "Rare Rabbit", "Allen Solly", "Van Heusen", "Louis Philippe", "Jack & Jones", "Only", "Vero Moda", "Forever New", "Superdry"],
    "Sports": ["Nike", "Adidas", "Puma", "Skechers", "Under Armour", "Decathlon", "Asics"],
    "Beauty": ["Sephora", "Nykaa", "MAC", "Forest Essentials", "The Body Shop", "Kiko Milano"],
    "Jewellery": ["Tanishq", "CaratLane", "Kalyan Jewellers", "Malabar Gold", "Senco Gold"],
    "Coffee & Bakery": ["Starbucks", "Third Wave Coffee", "Blue Tokai", "Tim Hortons", "Chaayos", "Chai Point", "Theobroma"],
    "QSR": ["McDonald's", "KFC", "Burger King", "Subway", "Pizza Hut", "Domino's", "Taco Bell", "Wow! Momo"],
    "Casual Dining": ["Social", "Chili's", "Punjab Grill", "Mamagoto", "Burma Burma", "YouMee", "Smoke House Deli"],
    "Indian Dining": ["Haldiram's", "Bikanervala", "Daryaganj", "Punjab Grill", "Moti Mahal"],
    "Entertainment": ["PVR INOX", "Cinepolis", "Timezone", "Smaaash", "Fun City"],
    "Kids": ["Hamleys", "Mothercare", "FirstCry", "Miniso", "Fun City"],
    "Electronics": ["Croma", "Reliance Digital", "Samsung", "Apple", "Vijay Sales"],
    "Home": ["Home Centre", "IKEA", "Pepperfry", "Chumbak", "Miniso"],
    "Grocery": ["Reliance Smart", "Spencer's", "Modern Bazaar", "Nature's Basket"],
    "Pharmacy & Wellness": ["Apollo Pharmacy", "Guardian", "Wellness Forever", "Health & Glow"],
    "Services": ["Looks Salon", "Geetanjali Salon", "VLCC", "Toni & Guy"],
}

SCHEMA = r'''
CREATE TABLE IF NOT EXISTS aci_intel_assets(
 id BIGSERIAL PRIMARY KEY, asset_code TEXT UNIQUE NOT NULL, asset_name TEXT NOT NULL,
 asset_class TEXT NOT NULL, city TEXT, location TEXT, developer_or_authority TEXT,
 lifecycle_status TEXT DEFAULT 'UNKNOWN', official_url TEXT, source_url TEXT, source_provider TEXT,
 confidence INTEGER DEFAULT 0, last_researched_at TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT NOW(),
 updated_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE IF NOT EXISTS aci_intel_brands(
 id BIGSERIAL PRIMARY KEY, asset_code TEXT NOT NULL, brand_name TEXT NOT NULL, category TEXT,
 presence_status TEXT DEFAULT 'REPORTED', performance_score INTEGER, performance_label TEXT,
 evidence_count INTEGER DEFAULT 1, source_url TEXT, last_verified_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW(),
 UNIQUE(asset_code,brand_name));
CREATE TABLE IF NOT EXISTS aci_intel_vacancies(
 id BIGSERIAL PRIMARY KEY, asset_code TEXT NOT NULL, availability_status TEXT DEFAULT 'UNKNOWN',
 area_text TEXT, floor_text TEXT, rent_text TEXT, permitted_use TEXT, source_url TEXT,
 confidence INTEGER DEFAULT 0, last_verified_at TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT NOW(),
 updated_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE IF NOT EXISTS aci_intel_contacts(
 id BIGSERIAL PRIMARY KEY, asset_code TEXT NOT NULL, contact_name TEXT, designation TEXT,
 phone TEXT, email TEXT, company_name TEXT, source_url TEXT, verification_status TEXT DEFAULT 'PUBLIC_REPORTED',
 confidence INTEGER DEFAULT 0, last_verified_at TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT NOW(),
 updated_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE IF NOT EXISTS aci_intel_recommendations(
 id BIGSERIAL PRIMARY KEY, asset_code TEXT NOT NULL, brand_name TEXT NOT NULL, category TEXT,
 fit_score INTEGER NOT NULL, reason TEXT NOT NULL, evidence_basis TEXT,
 recommendation_status TEXT DEFAULT 'AI_RECOMMENDATION', created_at TIMESTAMPTZ DEFAULT NOW(),
 updated_at TIMESTAMPTZ DEFAULT NOW(), UNIQUE(asset_code,brand_name));
CREATE TABLE IF NOT EXISTS aci_intel_scope(
 id BIGSERIAL PRIMARY KEY, asset_code TEXT UNIQUE NOT NULL, opportunity_score INTEGER DEFAULT 0,
 strongest_scope TEXT, secondary_scope TEXT, weak_scope TEXT, catchment_summary TEXT,
 competition_summary TEXT, vacancy_summary TEXT, contact_summary TEXT, ai_summary TEXT,
 confidence INTEGER DEFAULT 0, last_researched_at TIMESTAMPTZ, updated_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE IF NOT EXISTS aci_intel_evidence(
 id BIGSERIAL PRIMARY KEY, asset_code TEXT NOT NULL, evidence_type TEXT NOT NULL, title TEXT,
 snippet TEXT, source_url TEXT, source_provider TEXT, captured_at TIMESTAMPTZ DEFAULT NOW(),
 UNIQUE(asset_code,evidence_type,source_url));
CREATE TABLE IF NOT EXISTS aci_intel_runs(
 id BIGSERIAL PRIMARY KEY, run_code TEXT UNIQUE NOT NULL, run_type TEXT NOT NULL,
 status TEXT DEFAULT 'RUNNING', assets_found INTEGER DEFAULT 0, assets_researched INTEGER DEFAULT 0,
 errors INTEGER DEFAULT 0, started_at TIMESTAMPTZ DEFAULT NOW(), completed_at TIMESTAMPTZ, note TEXT);
CREATE INDEX IF NOT EXISTS idx_aci_intel_asset_class ON aci_intel_assets(asset_class,city);
CREATE INDEX IF NOT EXISTS idx_aci_intel_brand_asset ON aci_intel_brands(asset_code);
CREATE INDEX IF NOT EXISTS idx_aci_intel_rec_asset ON aci_intel_recommendations(asset_code,fit_score DESC);
CREATE INDEX IF NOT EXISTS idx_aci_intel_contact_asset ON aci_intel_contacts(asset_code);
'''


def _clean(v): return re.sub(r"\s+", " ", str(v or "")).strip()
def _esc(v): return html.escape(str(v or ""))
def _slug(v): return re.sub(r"[^a-z0-9]+", " ", _clean(v).lower()).strip()
def _code(prefix, seed): return prefix + "-" + hashlib.sha1(seed.encode("utf-8", "ignore")).hexdigest()[:12].upper()

def _domain(url):
    try:
        h = (urlparse(_clean(url)).hostname or "").lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""

def _phone(blob):
    m = re.search(r"(?<!\d)(?:\+?91[\s.-]?)?([6-9]\d(?:[\s.-]?\d){8})(?!\d)", str(blob or ""))
    if not m: return None
    d = re.sub(r"\D", "", m.group(1))
    return d if len(d) == 10 else None

def _email(blob):
    m = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", str(blob or ""), re.I)
    return m.group(0) if m else None

def _extract_brands(blob):
    low = " " + _clean(blob).lower() + " "
    out, seen = [], set()
    for cat, brands in BRAND_CATALOG.items():
        for brand in brands:
            if re.search(r"(?<![a-z0-9])" + re.escape(brand.lower()) + r"(?![a-z0-9])", low) and brand.lower() not in seen:
                seen.add(brand.lower()); out.append((brand, cat))
    return out

def _is_noise(url, title, snippet):
    dom = _domain(url)
    if dom in NOISE_DOMAINS or any(dom.endswith("." + d) for d in NOISE_DOMAINS): return True
    low = f"{title} {snippet}".lower()
    return any(x in low for x in ["job vacancy", "jobs in ", "career guide", "tourism guide", "things to do"])

def _looks_like_mall(title, snippet):
    low = f"{title} {snippet}".lower()
    return any(x in low for x in ["mall", "shopping centre", "shopping center", "retail destination", "high street"])

def _availability(blob):
    low = blob.lower()
    if any(x in low for x in ["fully leased", "100% leased", "no vacancy", "fully occupied"]): return "NO_VERIFIED_AVAILABILITY"
    if any(x in low for x in ["space available", "space for lease", "retail space available", "vacancy", "leasing opportunity", "shop available", "unit available", "commercial space tender", "auction", "allotment"]): return "REPORTED_AVAILABLE"
    return "UNKNOWN"

def _area(blob):
    m = re.search(r"\b(\d[\d,]*(?:\.\d+)?)\s*(sq\.?\s*ft|sqft|square feet|sqm|sq\.?\s*m)\b", blob, re.I)
    return _clean(m.group(0)) if m else None

def _floor(blob):
    m = re.search(r"\b(ground floor|lower ground|first floor|second floor|third floor|\d+(?:st|nd|rd|th)? floor)\b", blob, re.I)
    return _clean(m.group(0)).title() if m else None

def _rent(blob):
    m = re.search(r"₹\s*[\d,.]+\s*(?:lakh|lac|crore|cr)?(?:\s*/\s*(?:month|sqft|sq ft))?", blob, re.I)
    return _clean(m.group(0)) if m else None

def ensure_schema(engine):
    with engine.begin() as c:
        for stmt in [x.strip() for x in SCHEMA.split(";") if x.strip()]: c.execute(text(stmt))

def _search(queries, deep=False):
    import property_discovery
    rows, logs = property_discovery.search_waterfall(queries, deep=deep)
    return rows or [], logs or []

def _save_evidence(c, code, etype, row):
    url = _clean(row.get("url"))
    if not url: return
    c.execute(text('''INSERT INTO aci_intel_evidence(asset_code,evidence_type,title,snippet,source_url,source_provider)
      VALUES(:a,:e,:t,:s,:u,:p) ON CONFLICT(asset_code,evidence_type,source_url)
      DO UPDATE SET title=EXCLUDED.title,snippet=EXCLUDED.snippet,source_provider=EXCLUDED.source_provider,captured_at=NOW()'''),
      {"a":code,"e":etype,"t":_clean(row.get("title"))[:500],"s":_clean(row.get("snippet"))[:3000],"u":url,"p":_clean(row.get("source_provider"))[:100]})

def _upsert_asset(c, name, cls, city, location=None, org=None, url=None, provider=None, lifecycle="UNKNOWN", confidence=55):
    code = _code("ACI", f"{cls}|{_slug(name)}|{_slug(city)}")
    c.execute(text('''INSERT INTO aci_intel_assets(asset_code,asset_name,asset_class,city,location,developer_or_authority,lifecycle_status,source_url,source_provider,confidence,last_researched_at)
      VALUES(:code,:name,:cls,:city,:loc,:org,:life,:url,:provider,:conf,NULL)
      ON CONFLICT(asset_code) DO UPDATE SET asset_name=EXCLUDED.asset_name,city=COALESCE(EXCLUDED.city,aci_intel_assets.city),
      location=COALESCE(EXCLUDED.location,aci_intel_assets.location),developer_or_authority=COALESCE(EXCLUDED.developer_or_authority,aci_intel_assets.developer_or_authority),
      lifecycle_status=CASE WHEN EXCLUDED.lifecycle_status='UNKNOWN' THEN aci_intel_assets.lifecycle_status ELSE EXCLUDED.lifecycle_status END,
      source_url=COALESCE(EXCLUDED.source_url,aci_intel_assets.source_url),source_provider=COALESCE(EXCLUDED.source_provider,aci_intel_assets.source_provider),
      confidence=GREATEST(aci_intel_assets.confidence,EXCLUDED.confidence),updated_at=NOW()'''),
      {"code":code,"name":name[:300],"cls":cls,"city":city,"loc":location,"org":org,"life":lifecycle,"url":url,"provider":provider,"conf":confidence})
    return code

def _seed_from_raw(engine):
    ensure_schema(engine)
    with engine.begin() as c:
        rows = c.execute(text('''SELECT asset_name,title,lifecycle_status,city,location,developer_company,related_company,source_code,source_url,source_provider,raw_text
          FROM aci_discoveries WHERE COALESCE(research_status,'NEW')<>'REJECTED' ORDER BY discovered_at DESC LIMIT 2500''')).mappings().all()
        for r in rows:
            name = _clean(r.get("asset_name") or r.get("title")); blob = f"{name} {_clean(r.get('raw_text'))}"
            if not name: continue
            source = _clean(r.get("source_code")).upper()
            if source in GOV_SOURCE_CODES:
                _upsert_asset(c,name,"GOVERNMENT_PREMISES",_clean(r.get("city")),_clean(r.get("location")),source,_clean(r.get("source_url")),_clean(r.get("source_provider")),_clean(r.get("lifecycle_status")) or "AVAILABLE",75)
            elif _looks_like_mall(name,blob):
                _upsert_asset(c,name,"MALL",_clean(r.get("city")),_clean(r.get("location")),_clean(r.get("developer_company") or r.get("related_company")),_clean(r.get("source_url")),_clean(r.get("source_provider")),_clean(r.get("lifecycle_status")) or "UNKNOWN",55)

def _discover_malls_city(engine, city):
    rows,_ = _search([f'shopping malls in {city} India official brands directory',f'new upcoming shopping mall {city} developer brands opening',f'largest malls {city} retail tenant mix leasing'],False)
    count=0
    with engine.begin() as c:
        for row in rows[:50]:
            title,snippet,url=_clean(row.get("title")),_clean(row.get("snippet")),_clean(row.get("url"))
            if not title or _is_noise(url,title,snippet) or not _looks_like_mall(title,snippet): continue
            name=re.split(r"\s+[|–—-]\s+(?:stores|brands|timings|reviews|official|shopping|about|contact)\b",title,maxsplit=1,flags=re.I)[0][:240]
            code=_upsert_asset(c,_clean(name),"MALL",city,url=url,provider=row.get("source_provider"),confidence=55)
            _save_evidence(c,code,"MALL_DISCOVERY",row); count+=1
    return count

def _brand_perf(evidence_count, blob):
    score=45+min(25,evidence_count*7); low=blob.lower()
    score+=sum(4 for x in ["popular","crowd","busy","high footfall","anchor","flagship","successful","strong demand","opened"] if x in low)
    score-=sum(8 for x in ["closed","shut","vacant","poor reviews","low footfall"] if x in low)
    return max(20,min(95,score))

def _perf_label(score):
    return "Strong public signals" if score>=80 else "Positive public signals" if score>=65 else "Mixed / limited signals" if score>=50 else "Needs verification"

def _build_mall_recommendations(c, asset, code, joined):
    present=[dict(x) for x in c.execute(text("SELECT brand_name,category,performance_score FROM aci_intel_brands WHERE asset_code=:a"),{"a":code}).mappings().all()]
    pnames={x["brand_name"].lower() for x in present}; counts={}
    for x in present: counts[x["category"]]=counts.get(x["category"],0)+1
    weighted=[]
    for cat in BRAND_CATALOG:
        base=78-min(28,counts.get(cat,0)*8)+(7 if cat in {"Coffee & Bakery","QSR","Casual Dining","Beauty","Entertainment","Kids"} else 0)
        weighted.append((base,cat))
    weighted.sort(reverse=True); recs=[]
    for base,cat in weighted[:7]:
        added=0
        for brand in BRAND_CATALOG[cat]:
            if brand.lower() in pnames: continue
            fit=min(94,base+(8 if brand.lower() in joined.lower() else 0))
            reason=f"{cat} appears under-represented in the observed tenant mix. {brand} is not currently identified as present. Fit uses category gap, mall positioning and public research for {_clean(asset.get('city')) or 'this location'}."
            recs.append((fit,brand,cat,reason)); added+=1
            if added>=3: break
    recs.sort(reverse=True)
    for fit,brand,cat,reason in recs[:18]:
        c.execute(text('''INSERT INTO aci_intel_recommendations(asset_code,brand_name,category,fit_score,reason,evidence_basis)
          VALUES(:a,:b,:c,:f,:r,'PUBLIC_WEB + TENANT_MIX + CATEGORY_GAP') ON CONFLICT(asset_code,brand_name)
          DO UPDATE SET category=EXCLUDED.category,fit_score=EXCLUDED.fit_score,reason=EXCLUDED.reason,evidence_basis=EXCLUDED.evidence_basis,updated_at=NOW()'''),{"a":code,"b":brand,"c":cat,"f":fit,"r":reason})
    strong=weighted[0][1] if weighted else "Needs research"; second=weighted[1][1] if len(weighted)>1 else "Needs research"; weak=max(counts,key=counts.get) if counts else "Unknown"
    vac=c.execute(text("SELECT availability_status,area_text,floor_text FROM aci_intel_vacancies WHERE asset_code=:a ORDER BY last_verified_at DESC NULLS LAST LIMIT 1"),{"a":code}).mappings().first()
    nc=c.execute(text("SELECT COUNT(*) FROM aci_intel_contacts WHERE asset_code=:a"),{"a":code}).scalar() or 0
    avg=int(sum((x.get("performance_score") or 50) for x in present)/max(1,len(present))); opp=min(95,max(45,55+len(present)*2+(10 if recs else 0)))
    vs="No verified availability found in current public research." if not vac else " · ".join(_clean(x) for x in [vac.get("availability_status"),vac.get("area_text"),vac.get("floor_text")] if x)
    summary=f"Observed {len(present)} publicly reported brands. Public-signal brand rating averages {avg}/100. Best white-space categories: {strong} and {second}. Recommendations remain hypotheses until leasing availability and brand expansion intent are verified."
    c.execute(text('''INSERT INTO aci_intel_scope(asset_code,opportunity_score,strongest_scope,secondary_scope,weak_scope,catchment_summary,competition_summary,vacancy_summary,contact_summary,ai_summary,confidence,last_researched_at)
      VALUES(:a,:o,:s,:ss,:w,:ca,:co,:v,:ct,:sm,:cf,NOW()) ON CONFLICT(asset_code) DO UPDATE SET opportunity_score=EXCLUDED.opportunity_score,strongest_scope=EXCLUDED.strongest_scope,secondary_scope=EXCLUDED.secondary_scope,weak_scope=EXCLUDED.weak_scope,catchment_summary=EXCLUDED.catchment_summary,competition_summary=EXCLUDED.competition_summary,vacancy_summary=EXCLUDED.vacancy_summary,contact_summary=EXCLUDED.contact_summary,ai_summary=EXCLUDED.ai_summary,confidence=EXCLUDED.confidence,last_researched_at=NOW(),updated_at=NOW()'''),{"a":code,"o":opp,"s":strong,"ss":second,"w":weak,"ca":f"Catchment signals researched for {_clean(asset.get('location') or asset.get('city'))}; exact footfall and demographics require source verification.","co":"Competition inferred from tenant/category mix and public evidence; compare with similar malls before outreach.","v":vs,"ct":f"{nc} public leasing/business contact signal(s) found.","sm":summary,"cf":70 if len(present)>=5 else 55})

def _research_mall(engine, code):
    with engine.connect() as c: asset=c.execute(text("SELECT * FROM aci_intel_assets WHERE asset_code=:a"),{"a":code}).mappings().first()
    if not asset: return
    name,city=_clean(asset.get("asset_name")),_clean(asset.get("city"))
    rows,_=_search([f'"{name}" {city} brands stores directory',f'"{name}" {city} tenants brands opening',f'"{name}" {city} leasing contact phone email',f'"{name}" {city} retail space available lease vacancy',f'"{name}" {city} reviews footfall popular stores',f'"{name}" {city} developer owner GLA opening'],True)
    brand_ev={}; blobs=[]
    with engine.begin() as c:
        for row in rows[:80]:
            title,snippet,url=_clean(row.get("title")),_clean(row.get("snippet")),_clean(row.get("url")); blob=f"{title} {snippet}"
            if not title or _is_noise(url,title,snippet): continue
            blobs.append(blob); _save_evidence(c,code,"MALL_RESEARCH",row)
            for brand,cat in _extract_brands(blob): brand_ev.setdefault((brand,cat),[]).append(row)
            av=_availability(blob)
            if av!="UNKNOWN":
                c.execute(text("INSERT INTO aci_intel_vacancies(asset_code,availability_status,area_text,floor_text,rent_text,source_url,confidence,last_verified_at) VALUES(:a,:s,:ar,:fl,:r,:u,:cf,NOW())"),{"a":code,"s":av,"ar":_area(blob),"fl":_floor(blob),"r":_rent(blob),"u":url,"cf":70})
            ph,em=_phone(blob),_email(blob)
            if (ph or em) and any(x in blob.lower() for x in ["leasing","lease","mall management","retail","business development","contact"]):
                c.execute(text('''INSERT INTO aci_intel_contacts(asset_code,phone,email,company_name,source_url,confidence,last_verified_at)
                  SELECT :a,:p,:e,:co,:u,65,NOW() WHERE NOT EXISTS(SELECT 1 FROM aci_intel_contacts WHERE asset_code=:a AND COALESCE(phone,'')=COALESCE(:p,'') AND COALESCE(email,'')=COALESCE(:e,''))'''),{"a":code,"p":ph,"e":em,"co":name,"u":url})
        joined=" ".join(blobs)
        for (brand,cat),evs in brand_ev.items():
            score=_brand_perf(len(evs),joined)
            c.execute(text('''INSERT INTO aci_intel_brands(asset_code,brand_name,category,presence_status,performance_score,performance_label,evidence_count,source_url,last_verified_at)
              VALUES(:a,:b,:c,'PUBLICLY_REPORTED',:s,:l,:n,:u,NOW()) ON CONFLICT(asset_code,brand_name) DO UPDATE SET category=EXCLUDED.category,presence_status='PUBLICLY_REPORTED',performance_score=EXCLUDED.performance_score,performance_label=EXCLUDED.performance_label,evidence_count=EXCLUDED.evidence_count,source_url=EXCLUDED.source_url,last_verified_at=NOW(),updated_at=NOW()'''),{"a":code,"b":brand,"c":cat,"s":score,"l":_perf_label(score),"n":len(evs),"u":_clean(evs[0].get("url"))})
        _build_mall_recommendations(c,asset,code,joined)
        c.execute(text("UPDATE aci_intel_assets SET last_researched_at=NOW(),updated_at=NOW() WHERE asset_code=:a"),{"a":code})

def _research_gov(engine, code):
    with engine.connect() as c: asset=c.execute(text("SELECT * FROM aci_intel_assets WHERE asset_code=:a"),{"a":code}).mappings().first()
    if not asset: return
    name,city,auth=_clean(asset.get("asset_name")),_clean(asset.get("city")),_clean(asset.get("developer_or_authority"))
    rows,_=_search([f'"{name}" {auth} {city} commercial space tender lease',f'"{name}" {auth} {city} area reserve rent EMD tender',f'"{name}" {auth} {city} contact phone commercial leasing',f'"{name}" {city} nearby restaurants retail brands offices metro',f'"{name}" {city} footfall catchment commercial potential'],True)
    with engine.begin() as c:
        available=False
        for row in rows[:70]:
            title,snippet,url=_clean(row.get("title")),_clean(row.get("snippet")),_clean(row.get("url")); blob=f"{title} {snippet}"
            if not title or _is_noise(url,title,snippet): continue
            _save_evidence(c,code,"GOV_PREMISES_RESEARCH",row); av=_availability(blob)
            if av=="REPORTED_AVAILABLE" or any(x in blob.lower() for x in ["tender","auction","licensing","allotment"]):
                available=True; c.execute(text("INSERT INTO aci_intel_vacancies(asset_code,availability_status,area_text,floor_text,rent_text,permitted_use,source_url,confidence,last_verified_at) VALUES(:a,'REPORTED_AVAILABLE',:ar,:fl,:r,'Subject to official authority/tender terms',:u,75,NOW())"),{"a":code,"ar":_area(blob),"fl":_floor(blob),"r":_rent(blob),"u":url})
            ph,em=_phone(blob),_email(blob)
            if (ph or em) and any(x in blob.lower() for x in ["contact","commercial","leasing","tender","estate","property"]):
                c.execute(text('''INSERT INTO aci_intel_contacts(asset_code,phone,email,company_name,source_url,confidence,last_verified_at)
                  SELECT :a,:p,:e,:co,:u,70,NOW() WHERE NOT EXISTS(SELECT 1 FROM aci_intel_contacts WHERE asset_code=:a AND COALESCE(phone,'')=COALESCE(:p,'') AND COALESCE(email,'')=COALESCE(:e,''))'''),{"a":code,"p":ph,"e":em,"co":auth,"u":url})
        transit=any(x in (auth+" "+name).lower() for x in ["dmrc","metro","rail","station","rlda","ireps"]); airport=any(x in (auth+" "+name).lower() for x in ["airport","aai"])
        rank=[("QSR",90),("Coffee & Bakery",88),("Pharmacy & Wellness",80),("Services",76),("Grocery",70)] if transit else [("Coffee & Bakery",90),("QSR",88),("Casual Dining",82),("Fashion",72),("Services",70)] if airport else [("QSR",82),("Coffee & Bakery",80),("Services",76),("Pharmacy & Wellness",74),("Fashion",68)]
        for cat,base in rank:
            for brand in BRAND_CATALOG.get(cat,[])[:4]:
                reason=f"{brand} is a candidate because {cat} matches the inferred premises/catchment. Actual fit depends on unit size, frontage, permitted use, terms, footfall and brand expansion approval."
                c.execute(text('''INSERT INTO aci_intel_recommendations(asset_code,brand_name,category,fit_score,reason,evidence_basis)
                  VALUES(:a,:b,:c,:f,:r,'PREMISES_TYPE + PUBLIC_WEB + CATCHMENT_SIGNALS') ON CONFLICT(asset_code,brand_name) DO UPDATE SET category=EXCLUDED.category,fit_score=EXCLUDED.fit_score,reason=EXCLUDED.reason,evidence_basis=EXCLUDED.evidence_basis,updated_at=NOW()'''),{"a":code,"b":brand,"c":cat,"f":base,"r":reason})
        nc=c.execute(text("SELECT COUNT(*) FROM aci_intel_contacts WHERE asset_code=:a"),{"a":code}).scalar() or 0
        summary=f"{auth or 'Institutional'} premises. Strongest inferred scope: {rank[0][0]}; secondary: {rank[1][0]}. Availability, rent, footfall and permitted use remain unverified unless official evidence supports them."
        c.execute(text('''INSERT INTO aci_intel_scope(asset_code,opportunity_score,strongest_scope,secondary_scope,weak_scope,catchment_summary,competition_summary,vacancy_summary,contact_summary,ai_summary,confidence,last_researched_at)
          VALUES(:a,82,:s,:ss,'Premium concepts without catchment proof',:ca,:co,:v,:ct,:sm,70,NOW()) ON CONFLICT(asset_code) DO UPDATE SET opportunity_score=EXCLUDED.opportunity_score,strongest_scope=EXCLUDED.strongest_scope,secondary_scope=EXCLUDED.secondary_scope,weak_scope=EXCLUDED.weak_scope,catchment_summary=EXCLUDED.catchment_summary,competition_summary=EXCLUDED.competition_summary,vacancy_summary=EXCLUDED.vacancy_summary,contact_summary=EXCLUDED.contact_summary,ai_summary=EXCLUDED.ai_summary,confidence=EXCLUDED.confidence,last_researched_at=NOW(),updated_at=NOW()'''),{"a":code,"s":rank[0][0],"ss":rank[1][0],"ca":"Public catchment signals collected; verify exact entrance, pedestrian flow, offices/residential and operating hours.","co":"Nearby competition inferred from public research; exact radius-level census remains verification work.","v":"Reported available / tender opportunity." if available else "No current availability verified.","ct":f"{nc} public authority/leasing contact signal(s) found.","sm":summary})
        c.execute(text("UPDATE aci_intel_assets SET last_researched_at=NOW(),updated_at=NOW() WHERE asset_code=:a"),{"a":code})

def _research_asset(engine, code):
    with engine.connect() as c: cls=c.execute(text("SELECT asset_class FROM aci_intel_assets WHERE asset_code=:a"),{"a":code}).scalar()
    if cls=="MALL": _research_mall(engine,code)
    elif cls=="GOVERNMENT_PREMISES": _research_gov(engine,code)

def _run_full(engine):
    run=_code("RUN",datetime.now(timezone.utc).isoformat()); ensure_schema(engine)
    with engine.begin() as c: c.execute(text("INSERT INTO aci_intel_runs(run_code,run_type,status) VALUES(:r,'FULL_MALL_AND_PREMISES','RUNNING')"),{"r":run})
    found=researched=errors=0
    try:
        _seed_from_raw(engine)
        for city in TARGET_CITIES:
            try: found+=_discover_malls_city(engine,city)
            except Exception: errors+=1
        with engine.connect() as c: assets=c.execute(text("SELECT asset_code FROM aci_intel_assets ORDER BY CASE WHEN last_researched_at IS NULL THEN 0 ELSE 1 END,updated_at DESC LIMIT 120")).scalars().all()
        for code in assets:
            try: _research_asset(engine,code); researched+=1
            except Exception: errors+=1
    finally:
        with engine.begin() as c: c.execute(text("UPDATE aci_intel_runs SET status='COMPLETED',assets_found=:f,assets_researched=:rr,errors=:e,completed_at=NOW() WHERE run_code=:r"),{"f":found,"rr":researched,"e":errors,"r":run})

def _page_role(core, req):
    fn=getattr(core,"page_role_or_redirect",None)
    if callable(fn): return fn(req)
    try: core.need_login(req); return "team"
    except Exception: return None

def _detail(engine, code):
    with engine.connect() as c:
        brands=[dict(x) for x in c.execute(text("SELECT * FROM aci_intel_brands WHERE asset_code=:a ORDER BY performance_score DESC NULLS LAST,brand_name"),{"a":code}).mappings().all()]
        recs=[dict(x) for x in c.execute(text("SELECT * FROM aci_intel_recommendations WHERE asset_code=:a ORDER BY fit_score DESC LIMIT 18"),{"a":code}).mappings().all()]
        vac=[dict(x) for x in c.execute(text("SELECT * FROM aci_intel_vacancies WHERE asset_code=:a ORDER BY last_verified_at DESC NULLS LAST LIMIT 6"),{"a":code}).mappings().all()]
        contacts=[dict(x) for x in c.execute(text("SELECT * FROM aci_intel_contacts WHERE asset_code=:a ORDER BY confidence DESC,last_verified_at DESC NULLS LAST LIMIT 8"),{"a":code}).mappings().all()]
    return brands,recs,vac,contacts

def _load(engine,view,city):
    with engine.connect() as c: rows=[dict(x) for x in c.execute(text('''SELECT a.*,s.opportunity_score,s.strongest_scope,s.secondary_scope,s.weak_scope,s.vacancy_summary,s.contact_summary,s.ai_summary FROM aci_intel_assets a LEFT JOIN aci_intel_scope s ON s.asset_code=a.asset_code ORDER BY COALESCE(s.opportunity_score,0) DESC,a.updated_at DESC LIMIT 500''')).mappings().all()]
    if city:
        q=city.lower(); rows=[x for x in rows if q in _clean(x.get("city")).lower() or q in _clean(x.get("location")).lower()]
    if view=="MALLS": rows=[x for x in rows if x.get("asset_class")=="MALL"]
    elif view=="GOV": rows=[x for x in rows if x.get("asset_class")=="GOVERNMENT_PREMISES"]
    elif view=="RESEARCH": rows=[x for x in rows if not x.get("last_researched_at")]
    return rows[:180]

def _render(engine,view,city,message):
    cards=[]
    for r in _load(engine,view,city):
        brands,recs,vac,contacts=_detail(engine,r["asset_code"]); score=int(r.get("opportunity_score") or 0)
        bhtml="".join(f'<span class="brand"><b>{_esc(x["brand_name"])}</b><small>{_esc(x.get("category"))} · {int(x.get("performance_score") or 0)}/100</small></span>' for x in brands[:16]) or '<span class="muted">Brand census not researched yet.</span>'
        rec_html="".join(f'<div class="rec"><b>{_esc(x["brand_name"])}</b><span>{int(x["fit_score"])}/100</span><small>{_esc(x["category"])}</small><p>{_esc(x["reason"])}</p></div>' for x in recs[:8]) or '<div class="muted">Run research to create brand-fit recommendations.</div>'
        if vac:
            v=vac[0]; vacancy=" · ".join(_esc(x) for x in [v.get("availability_status"),v.get("area_text"),v.get("floor_text")] if x); vacancy+=(f' · <a href="{_esc(v.get("source_url"))}" target="_blank">reference</a>' if v.get("source_url") else "")
        else: vacancy="No verified space availability found."
        contact_html="".join(f'<div><b>{_esc(x.get("contact_name") or x.get("company_name") or "Public contact")}</b> {_esc(x.get("designation"))}<br>{_esc(x.get("phone"))} {_esc(x.get("email"))} ' + (f'<a href="{_esc(x.get("source_url"))}" target="_blank">source</a>' if x.get("source_url") else "") + '</div>' for x in contacts[:4]) or "No leasing/business contact verified yet."
        kind="Mall & Brand Intelligence" if r["asset_class"]=="MALL" else "Government / Institutional Premises"
        source_btn=f'<a class="sourcebtn" href="{_esc(r.get("source_url"))}" target="_blank">Open source</a>' if r.get("source_url") else ""
        cards.append(f'''<article class="asset"><div class="head"><div><div class="kind">{kind}</div><h2>{_esc(r["asset_name"])}</h2><div class="muted">{_esc(r.get("city"))} · {_esc(r.get("location"))} · {_esc(r.get("developer_or_authority"))}</div></div><div class="score">{score}<small>/100</small></div></div><div class="grid"><section><h3>Scope</h3><p><b>Strongest:</b> {_esc(r.get("strongest_scope") or "Needs research")}<br><b>Secondary:</b> {_esc(r.get("secondary_scope") or "Needs research")}<br><b>Weak / saturated:</b> {_esc(r.get("weak_scope") or "Unknown")}</p><p>{_esc(r.get("ai_summary") or "Run research for source-backed commercial scope analysis.")}</p></section><section><h3>Space availability</h3><p>{vacancy}</p></section><section><h3>Leasing / authority contacts</h3>{contact_html}</section></div><h3>{"Brands present / publicly reported" if r["asset_class"]=="MALL" else "Premises intelligence"}</h3><div class="brands">{bhtml}</div><h3>Potential brands not currently identified here</h3><div class="recommendations">{rec_html}</div><div class="actions"><form method="post" action="/commercial-intelligence/research/{_esc(r["asset_code"])}"><button>Research this asset</button></form>{source_btn}</div></article>''')
    notice=f'<div class="notice">{_esc(message)}</div>' if message else ""
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Mall & Premises Intelligence</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#f4f7fb;color:#152238;font-family:Arial,sans-serif}}header{{background:#0c2032;color:white;padding:18px 24px}}.wrap{{max-width:1500px;margin:auto;padding:20px}}.toolbar{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px}}a,button{{cursor:pointer}}.tab,.sourcebtn,button{{border:0;border-radius:9px;padding:10px 14px;text-decoration:none;background:#e8eef5;color:#18324a;font-weight:700}}button{{background:#0d6efd;color:white}}.asset{{background:white;border:1px solid #dfe6ee;border-radius:16px;padding:20px;margin:16px 0;box-shadow:0 3px 12px #0000000b}}.head{{display:flex;justify-content:space-between;gap:15px}}h2{{margin:4px 0 6px}}h3{{margin:16px 0 8px}}.kind{{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:#58728a}}.muted{{color:#6d7d8b}}.score{{font-size:28px;font-weight:800;background:#e9f7ef;color:#0c6a3d;border-radius:14px;padding:12px;min-width:88px;text-align:center}}.score small{{font-size:11px;display:block}}.grid{{display:grid;grid-template-columns:1.4fr 1fr 1fr;gap:12px}}section{{background:#f8fafc;border-radius:12px;padding:12px}}.brands{{display:flex;gap:8px;flex-wrap:wrap}}.brand{{background:#eef4fb;padding:8px 10px;border-radius:10px}}.brand small{{display:block;color:#6b7d8f;margin-top:3px}}.recommendations{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.rec{{border:1px solid #dfe6ee;border-radius:12px;padding:10px}}.rec>span{{float:right;font-weight:800;color:#0c6a3d}}.rec small{{display:block;color:#63788b;margin-top:4px}}.rec p{{font-size:12px;color:#526473}}.actions{{display:flex;gap:10px;margin-top:14px}}.notice{{padding:12px;border-radius:10px;background:#e9f8f1;color:#075c3e;margin-bottom:12px}}input{{padding:10px;border:1px solid #ccd8e3;border-radius:8px}}@media(max-width:900px){{.grid,.recommendations{{grid-template-columns:1fr}}}}</style></head><body><header><h1>Mall & Premises Intelligence AI</h1><div>Mall brand census, performance signals, vacancies, leasing contacts, premises scope and brand-fit opportunities.</div></header><div class="wrap">{notice}<div class="toolbar"><a class="tab" href="/commercial-intelligence?view=ALL">All</a><a class="tab" href="/commercial-intelligence?view=MALLS">Malls</a><a class="tab" href="/commercial-intelligence?view=GOV">DMRC / Government</a><a class="tab" href="/commercial-intelligence?view=RESEARCH">Needs Research</a><form method="get" action="/commercial-intelligence"><input type="hidden" name="view" value="{_esc(view)}"><input name="city" value="{_esc(city)}" placeholder="City / micro-market"><button>Filter</button></form><form method="post" action="/commercial-intelligence/research-all"><button>Research All Malls & Premises</button></form></div>{''.join(cards) or '<div class="asset"><h2>No intelligence yet</h2><p>Use Research All Malls & Premises to build the first working set.</p></div>'}</div></body></html>'''

def register(core):
    engine,app=core.engine,core.app; router=APIRouter(); ensure_schema(engine); _seed_from_raw(engine)
    @router.get("/commercial-intelligence",response_class=HTMLResponse)
    def dashboard(req:Request,view:str=Query("ALL"),city:str=Query(""),message:str=Query("")):
        role=_page_role(core,req)
        if not role:
            login=getattr(core,"login_page",None)
            if callable(login): return HTMLResponse(login())
            raise HTTPException(401,"Login required")
        return HTMLResponse(_render(engine,_clean(view).upper(),_clean(city),_clean(message)))
    @router.post("/commercial-intelligence/research-all")
    def research_all(req:Request,background_tasks:BackgroundTasks):
        core.need_login(req); background_tasks.add_task(_run_full,engine)
        return RedirectResponse("/commercial-intelligence?message=Full+mall+and+premises+research+started.+Refresh+later+for+results.",status_code=303)
    @router.post("/commercial-intelligence/research/{asset_code}")
    def research_one(asset_code:str,req:Request,background_tasks:BackgroundTasks):
        core.need_login(req); background_tasks.add_task(_research_asset,engine,asset_code)
        return RedirectResponse("/commercial-intelligence?message=Asset+research+started.+Refresh+shortly.",status_code=303)
    @router.get("/api/commercial-intelligence/status")
    def status(req:Request):
        core.need_login(req)
        with engine.connect() as c:
            counts=c.execute(text("SELECT COUNT(*) total_assets,COUNT(*) FILTER(WHERE asset_class='MALL') malls,COUNT(*) FILTER(WHERE asset_class='GOVERNMENT_PREMISES') government_premises,COUNT(*) FILTER(WHERE last_researched_at IS NOT NULL) researched FROM aci_intel_assets")).mappings().first()
            brands=c.execute(text("SELECT COUNT(*) FROM aci_intel_brands")).scalar() or 0; recs=c.execute(text("SELECT COUNT(*) FROM aci_intel_recommendations")).scalar() or 0; contacts=c.execute(text("SELECT COUNT(*) FROM aci_intel_contacts")).scalar() or 0; vac=c.execute(text("SELECT COUNT(*) FROM aci_intel_vacancies")).scalar() or 0
            run=c.execute(text("SELECT * FROM aci_intel_runs ORDER BY started_at DESC LIMIT 1")).mappings().first()
        return {"version":VERSION,**dict(counts or {}),"brands_observed":brands,"brand_recommendations":recs,"public_contacts":contacts,"vacancy_signals":vac,"last_run":dict(run) if run else None,"truth_policy":{"brand_presence":"PUBLICLY_REPORTED until verified","brand_performance":"public-signal score, not audited sales","availability":"reported/verified evidence only","contacts":"public business contacts with source provenance","recommendations":"AI recommendations, not confirmed brand requirements"}}
    app.include_router(router)
    try:
        import alliance_commercial_intelligence_network as network
        network._dashboard_link_patch(app)
    except Exception: pass
    return {"registered":True,"version":VERSION,"route":"/commercial-intelligence"}
