import os
import re
import json
import hashlib
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

DISCOVERY_VERSION = "17.4-VERIFIED-STRUCTURED"
SQM_TO_SQFT = 10.7639104167

LANGSEARCH_API_KEY = os.getenv("LANGSEARCH_API_KEY", "").strip()
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip()
JINA_API_KEY = os.getenv("JINA_API_KEY", "").strip()
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "").strip()
GOOGLE_CSE_API_KEY = os.getenv("GOOGLE_CSE_API_KEY", "").strip()
GOOGLE_CSE_CX = os.getenv("GOOGLE_CSE_CX", "").strip()

DISCOVERY_TIMEOUT = float(os.getenv("DISCOVERY_TIMEOUT", "18"))
DISCOVERY_MIN_SCORE = int(os.getenv("DISCOVERY_MIN_SCORE", "45"))
DISCOVERY_QUICK_TARGET = int(os.getenv("DISCOVERY_QUICK_TARGET", "25"))
DISCOVERY_DEEP_TARGET = int(os.getenv("DISCOVERY_DEEP_TARGET", "70"))
DISCOVERY_PROVIDER_COOLDOWN_SECONDS = int(os.getenv("DISCOVERY_PROVIDER_COOLDOWN_SECONDS", "300"))

class DiscoverySearchInput(BaseModel):
    requirement: str = Field(min_length=5)
    deep_search: bool = False
    created_by: Optional[str] = None
    linked_requirement_id: Optional[str] = None

class DiscoveryStatusInput(BaseModel):
    notes: Optional[str] = None

class VerifyInput(BaseModel):
    verified_by: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    owner_name: Optional[str] = None
    owner_contact: Optional[str] = None
    broker_name: Optional[str] = None
    broker_contact: Optional[str] = None
    notes: Optional[str] = None

_PROVIDER_STATE = {}

def _state(name):
    return _PROVIDER_STATE.setdefault(name, {
        "failures": 0, "cooldown_until": 0.0, "calls": 0,
        "results": 0, "last_error": None, "last_success": None
    })

def _provider_available(name):
    return time.time() >= _state(name)["cooldown_until"]

def _provider_ok(name, count):
    s = _state(name)
    s["failures"] = 0
    s["cooldown_until"] = 0.0
    s["calls"] += 1
    s["results"] += int(count or 0)
    s["last_error"] = None
    s["last_success"] = datetime.now(timezone.utc).isoformat()

def _provider_fail(name, exc):
    s = _state(name)
    s["calls"] += 1
    s["failures"] += 1
    s["last_error"] = str(exc)[:500]
    s["cooldown_until"] = time.time() + min(
        DISCOVERY_PROVIDER_COOLDOWN_SECONDS * max(1, s["failures"]), 3600
    )

def _space(v):
    return re.sub(r"\s+", " ", str(v or "")).strip()

def _num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return None

def _phone(v):
    d = re.sub(r"\D", "", str(v or ""))
    if d.startswith("91") and len(d) >= 12:
        d = d[-10:]
    return d if len(d) == 10 and d[0] in "6789" else None

PROPERTY_TYPES = [
    ("guest house", "Hospitality"), ("guesthouse", "Hospitality"),
    ("farm house", "Farmhouse"), ("farmhouse", "Farmhouse"),
    ("warehouse", "Warehouse"), ("industrial", "Industrial"),
    ("factory", "Industrial"), ("showroom", "Retail"),
    ("retail", "Retail"), ("shop", "Retail"), ("office", "Office"),
    ("restaurant", "Hospitality"), ("banquet", "Hospitality"),
    ("hotel", "Hospitality"), ("cafe", "Hospitality"),
    ("lounge", "Hospitality"), ("villa", "Villa"),
    ("apartment", "Residential"), ("flat", "Residential"),
    ("residential", "Residential"), ("commercial", "Commercial"),
    ("land", "Land"), ("plot", "Land")
]

KNOWN_LOCATIONS = [
    ("south delhi", "Delhi"), ("north goa", "Goa"), ("south goa", "Goa"),
    ("greater noida", "Greater Noida"), ("gurugram", "Gurugram"),
    ("gurgaon", "Gurugram"), ("delhi", "Delhi"), ("noida", "Noida"),
    ("goa", "Goa"), ("siolim", "Goa"), ("assagao", "Goa"),
    ("anjuna", "Goa"), ("vagator", "Goa"), ("morjim", "Goa"),
    ("mumbai", "Mumbai"), ("bengaluru", "Bengaluru"),
    ("bangalore", "Bengaluru"), ("hyderabad", "Hyderabad"), ("pune", "Pune")
]

LOCATION_EXPANSIONS = {
    "south delhi": [
        "Greater Kailash", "GK 1", "GK 2", "South Extension",
        "Defence Colony", "Green Park", "Hauz Khas", "Saket",
        "Vasant Kunj", "Malviya Nagar", "Lajpat Nagar", "Nehru Place"
    ],
    "goa": [
        "North Goa", "South Goa", "Siolim", "Assagao", "Anjuna",
        "Vagator", "Morjim", "Mapusa", "Porvorim", "Candolim",
        "Calangute", "Arpora"
    ],
    "north goa": [
        "Siolim", "Assagao", "Anjuna", "Vagator", "Morjim",
        "Mapusa", "Porvorim", "Candolim", "Calangute", "Arpora"
    ],
    "gurgaon": [
        "Golf Course Road", "Golf Course Extension Road", "MG Road",
        "Sohna Road", "Sector 29", "Sector 44", "Sector 52",
        "Sector 56", "Sector 65", "Sector 66", "Sector 67"
    ],
    "gurugram": [
        "Golf Course Road", "Golf Course Extension Road", "MG Road",
        "Sohna Road", "Sector 29", "Sector 44", "Sector 52",
        "Sector 56", "Sector 65", "Sector 66", "Sector 67"
    ]
}

def parse_requirement(raw):
    original = _space(raw)
    s = original.lower()

    transaction = None
    if any(x in s for x in ["on lease", "for lease", "lease", "rent", "rental"]):
        transaction = "Lease"
    elif any(x in s for x in ["for sale", "purchase", "buy", "sale", "acquire"]):
        transaction = "Sale"

    ptype = None
    for key, label in PROPERTY_TYPES:
        if key in s:
            ptype = label
            break

    floor = None
    if "ground floor" in s or "ground-floor" in s or re.search(r"\bgf\b", s):
        floor = "Ground Floor"
    else:
        m = re.search(r"\b(\d+)(?:st|nd|rd|th)?\s*floor\b", s)
        if m:
            floor = f"{m.group(1)} Floor"

    title_pref = "Clear Title" if any(x in s for x in ["clear title", "clean title", "title clear"]) else None

    unit = None
    a = b = None
    range_patterns = [
        (r"(\d[\d,]*(?:\.\d+)?)\s*(?:to|-|–)\s*(\d[\d,]*(?:\.\d+)?)\s*(?:sq\s*ft|sqft|square\s*feet)", "sqft"),
        (r"(\d[\d,]*(?:\.\d+)?)\s*(?:to|-|–)\s*(\d[\d,]*(?:\.\d+)?)\s*(?:sq\s*m|sqm|sqmt|sq\.?\s*mtr|square\s*met(?:er|re)s?)", "sqm"),
        (r"(\d[\d,]*(?:\.\d+)?)\s*(?:to|-|–)\s*(\d[\d,]*(?:\.\d+)?)\s*(?:sq\s*yd|sqyd|square\s*yards?)", "sqyd"),
        (r"(\d[\d,]*(?:\.\d+)?)\s*(?:to|-|–)\s*(\d[\d,]*(?:\.\d+)?)\s*(?:acre|acres)", "acre")
    ]
    for pat, u in range_patterns:
        m = re.search(pat, s)
        if m:
            a, b, unit = _num(m.group(1)), _num(m.group(2)), u
            break

    if a is None:
        single_patterns = [
            (r"(\d[\d,]*(?:\.\d+)?)\s*(?:sq\s*ft|sqft|square\s*feet)", "sqft"),
            (r"(\d[\d,]*(?:\.\d+)?)\s*(?:sq\s*m|sqm|sqmt|sq\.?\s*mtr|square\s*met(?:er|re)s?)", "sqm"),
            (r"(\d[\d,]*(?:\.\d+)?)\s*(?:sq\s*yd|sqyd|square\s*yards?)", "sqyd"),
            (r"(\d[\d,]*(?:\.\d+)?)\s*(?:acre|acres)", "acre")
        ]
        for pat, u in single_patterns:
            m = re.search(pat, s)
            if m:
                v = _num(m.group(1))
                if v:
                    a, b, unit = v * .90, v * 1.10, u
                break

    def sqft(v, u):
        if v is None:
            return None
        if u == "sqm":
            return v * SQM_TO_SQFT
        if u == "sqyd":
            return v * 9.0
        if u == "acre":
            return v * 43560.0
        return v

    location = city = None
    for loc, c in sorted(KNOWN_LOCATIONS, key=lambda x: len(x[0]), reverse=True):
        if loc in s:
            location, city = loc.title(), c
            break

    return {
        "raw_text": original,
        "transaction_type": transaction,
        "property_type": ptype,
        "city": city,
        "location": location,
        "floor": floor,
        "title_preference": title_pref,
        "min_area_raw": round(a, 2) if a else None,
        "max_area_raw": round(b, 2) if b else None,
        "area_unit": unit,
        "minimum_area_sqft": round(sqft(a, unit), 2) if a else None,
        "maximum_area_sqft": round(sqft(b, unit), 2) if b else None
    }

def _area_phrase(p):
    a, b, u = p.get("min_area_raw"), p.get("max_area_raw"), p.get("area_unit")
    if not a or not b:
        return ""
    return f"{int(a)} to {int(b)} {u or ''}".strip()

def build_queries(p, deep=False):
    ptype = p.get("property_type") or "property"
    loc = p.get("location") or p.get("city") or ""
    area = _area_phrase(p)
    floor = p.get("floor") or ""
    title = p.get("title_preference") or ""
    tx = p.get("transaction_type")
    txs = ["for lease", "for rent"] if tx == "Lease" else ["for sale"] if tx == "Sale" else ["available"]

    synonyms = {
        "Retail": ["retail space", "showroom", "shop", "commercial space"],
        "Land": ["land", "plot", "parcel"],
        "Office": ["office space", "commercial office"],
        "Warehouse": ["warehouse", "godown", "industrial shed"],
        "Hospitality": ["hotel", "guest house", "hospitality property"],
        "Villa": ["villa", "independent house"],
        "Residential": ["apartment", "flat", "residential property"],
        "Farmhouse": ["farmhouse", "farm house"]
    }.get(ptype, [ptype])

    q = []
    for t in txs:
        q.append(_space(" ".join(x for x in [ptype, t, loc, area, floor, title] if x)))
    for syn in synonyms[:3 if deep else 2]:
        q.append(_space(" ".join(x for x in [syn, txs[0], loc, area, floor, title] if x)))

    for place in LOCATION_EXPANSIONS.get((p.get("location") or "").lower(), [])[:10 if deep else 3]:
        q.append(_space(" ".join(x for x in [synonyms[0], txs[0], place, area, floor, title] if x)))

    if deep:
        seed = _space(" ".join(x for x in [ptype, loc, area, floor, txs[0]] if x))
        for domain in [
            "linkedin.com/posts", "instagram.com", "facebook.com",
            "99acres.com", "magicbricks.com", "housing.com", "makaan.com"
        ]:
            q.append(f'site:{domain} "{seed}"')

    q.append(p["raw_text"])
    out, seen = [], set()
    for item in q:
        item = _space(item)
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out[:24 if deep else 8]

def configured_providers():
    return {
        "Jina": bool(JINA_API_KEY),
        "LangSearch": bool(LANGSEARCH_API_KEY),
        "Tavily": bool(TAVILY_API_KEY),
        "Brave": bool(BRAVE_API_KEY),
        "Google CSE": bool(GOOGLE_CSE_API_KEY and GOOGLE_CSE_CX)
    }

def _jina(query, limit=5):
    if not JINA_API_KEY:
        return []
    r = httpx.get(
        "https://s.jina.ai/",
        params={"q": query},
        headers={"Authorization": f"Bearer {JINA_API_KEY}", "Accept": "application/json"},
        timeout=DISCOVERY_TIMEOUT
    )
    r.raise_for_status()
    data = r.json() or {}
    rows = data.get("data") or []
    if isinstance(rows, dict):
        rows = rows.get("results") or rows.get("data") or []
    return [{
        "title": x.get("title") or x.get("name") or "",
        "url": x.get("url") or "",
        "snippet": x.get("description") or x.get("content") or "",
        "source_provider": "Jina"
    } for x in rows[:limit]]

def _langsearch(query, limit=10):
    if not LANGSEARCH_API_KEY:
        return []
    r = httpx.post(
        "https://api.langsearch.com/v1/web-search",
        headers={"Authorization": f"Bearer {LANGSEARCH_API_KEY}", "Content-Type": "application/json"},
        json={"query": query, "freshness": "noLimit", "summary": True, "count": limit},
        timeout=DISCOVERY_TIMEOUT
    )
    r.raise_for_status()
    data = r.json() or {}
    rows = (((data.get("data") or {}).get("webPages") or {}).get("value") or [])
    return [{
        "title": x.get("name") or "",
        "url": x.get("url") or "",
        "snippet": x.get("summary") or x.get("snippet") or "",
        "source_provider": "LangSearch"
    } for x in rows[:limit]]

def _tavily(query, limit=10):
    if not TAVILY_API_KEY:
        return []
    r = httpx.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {TAVILY_API_KEY}", "Content-Type": "application/json"},
        json={
            "query": query, "search_depth": "basic", "max_results": min(limit, 20),
            "include_answer": False, "include_raw_content": False, "country": "india"
        },
        timeout=DISCOVERY_TIMEOUT
    )
    r.raise_for_status()
    data = r.json() or {}
    return [{
        "title": x.get("title") or "",
        "url": x.get("url") or "",
        "snippet": x.get("content") or "",
        "source_provider": "Tavily"
    } for x in (data.get("results") or [])[:limit]]

def _brave(query, limit=10):
    if not BRAVE_API_KEY:
        return []
    r = httpx.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={"X-Subscription-Token": BRAVE_API_KEY, "Accept": "application/json"},
        params={"q": query, "count": min(limit, 20), "country": "IN", "search_lang": "en"},
        timeout=DISCOVERY_TIMEOUT
    )
    r.raise_for_status()
    data = r.json() or {}
    return [{
        "title": x.get("title") or "",
        "url": x.get("url") or "",
        "snippet": x.get("description") or "",
        "source_provider": "Brave"
    } for x in ((data.get("web") or {}).get("results") or [])[:limit]]

def _google(query, limit=10):
    if not GOOGLE_CSE_API_KEY or not GOOGLE_CSE_CX:
        return []
    r = httpx.get(
        "https://www.googleapis.com/customsearch/v1",
        params={"key": GOOGLE_CSE_API_KEY, "cx": GOOGLE_CSE_CX, "q": query, "num": min(limit, 10)},
        timeout=DISCOVERY_TIMEOUT
    )
    r.raise_for_status()
    data = r.json() or {}
    return [{
        "title": x.get("title") or "",
        "url": x.get("link") or "",
        "snippet": x.get("snippet") or "",
        "source_provider": "Google CSE"
    } for x in (data.get("items") or [])[:limit]]

PROVIDERS = [("Jina", _jina), ("LangSearch", _langsearch), ("Tavily", _tavily), ("Brave", _brave), ("Google CSE", _google)]

def search_waterfall(queries, deep=False):
    target = DISCOVERY_DEEP_TARGET if deep else DISCOVERY_QUICK_TARGET
    configured = configured_providers()
    if not any(configured.values()):
        return [], [{"provider": "SYSTEM", "status": "NO_PROVIDER_KEYS"}]

    raw, logs = [], []
    for query in queries:
        for name, fn in PROVIDERS:
            if not configured.get(name) or not _provider_available(name):
                continue
            try:
                rows = fn(query, 10)
                _provider_ok(name, len(rows))
                logs.append({"provider": name, "query": query, "status": "OK", "results": len(rows)})
                raw.extend(rows)
                if rows:
                    break
            except Exception as exc:
                _provider_fail(name, exc)
                logs.append({"provider": name, "query": query, "status": "ERROR", "message": str(exc)[:200]})
        if len(raw) >= target:
            break

    out, seen = [], set()
    for row in raw:
        key = (_space(row.get("url")) or _space(row.get("title"))).lower()
        if key and key not in seen:
            seen.add(key)
            out.append(row)
    return out, logs

AREA_SQFT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|square\s*feet)", re.I)
AREA_SQM = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:sq\.?\s*m|sqm|sqmt|square\s*met(?:er|re)s?)", re.I)
PHONE_RE = re.compile(r"(?:\+?91[\s.-]?)?[6-9]\d(?:[\s.-]?\d){8}")
RENT_RE = re.compile(r"₹\s*([\d,.]+)\s*(lac|lakh|l|cr|crore)?(?:\s*/\s*month|\s*per\s*month)?", re.I)
POSTED_RE = re.compile(r"(?:posted\s*:\s*)?((?:\d+\s*(?:hours?|days?|weeks?)\s*ago)|(?:yesterday)|(?:[a-z]{3,9}\s+\d{1,2}(?:\s*,\s*['’]?\d{2,4})?))", re.I)

LISTING_START_RE = re.compile(
    r"(?=(?:₹\s*[\d,.]+\s*(?:lac|lakh|l|cr|crore)?\s*)?"
    r"(?:shop|showroom|office\s+space|ready\s+to\s+move\s+office\s+space|bare\s+shell\s+office\s+space|"
    r"commercial\s+property|commercial\s+space|warehouse|land|plot|villa|independent\s+house)\s+"
    r"(?:for\s+(?:rent|sale)|in)\b)", re.I)

def _extract_area(blob):
    m = AREA_SQFT.search(blob)
    if m:
        return _num(m.group(1))
    m = AREA_SQM.search(blob)
    if m:
        v = _num(m.group(1))
        return round(v * SQM_TO_SQFT, 2) if v else None
    return None

def _extract_rent(blob):
    m = re.search(r"₹\s*([\d,.]+)\s*(lac|lakh|l|cr|crore)?\s*(?:/\s*month|per\s*month)", blob, re.I)
    if not m:
        m = RENT_RE.search(blob)
    if not m:
        return None
    return "₹ " + m.group(1).strip() + (" " + m.group(2) if m.group(2) else "")

def _extract_posted(blob):
    m = POSTED_RE.search(blob)
    return _space(m.group(1)) if m else None

def _platform(url):
    u = (url or "").lower()
    for needle, label in [
        ("linkedin.com", "LinkedIn"), ("instagram.com", "Instagram"),
        ("facebook.com", "Facebook"), ("99acres.com", "99acres"),
        ("magicbricks.com", "MagicBricks"), ("housing.com", "Housing"),
        ("makaan.com", "Makaan"), ("propertywala.com", "PropertyWala")
    ]:
        if needle in u:
            return label
    return "Public Web"

COLLECTION_TITLE_RE = re.compile(
    r"(?:\d[\d,]*\+\s*(?:shop|showroom|commercial|propert)|\bresults?\b|"
    r"shops?\s+for\s+rent\s+in.+(?:\d+\+)|commercial\s+property\s+for\s+(?:rent|sale)\s+in.+(?:\d+\+)|"
    r"apartments?,\s*independent\s+houses?,\s*shops?)", re.I
)
COLLECTION_URL_RE = re.compile(
    r"(?:99acres\.com/.+(?:-ffid|-xffid)|magicbricks\.com/(?:shops|commercial)-for-|"
    r"propertywala\.com/[a-z_]+(?:_new_delhi)?$)", re.I
)
INDIVIDUAL_URL_RE = re.compile(
    r"(?:99acres\.com/.+-spid-|magicbricks\.com/propertyDetails/|instagram\.com/(?:p|reel)/|"
    r"facebook\.com/.+/posts/|linkedin\.com/posts/)", re.I
)

def _is_collection_page(row):
    title=_space(row.get("title")); url=_space(row.get("url")); snippet=_space(row.get("snippet"))
    if COLLECTION_TITLE_RE.search(title): return True
    if COLLECTION_URL_RE.search(url) and not INDIVIDUAL_URL_RE.search(url): return True
    return len(snippet)>1600 and len(list(LISTING_START_RE.finditer(snippet)))>=3

def _listing_quality(row, blob):
    concrete=sum([
        bool(_extract_area(blob)),
        bool(re.search(r"\bground\s+floor\b|\bfloor\s+ground\b|\b\d+(?:st|nd|rd|th)?\s+floor\b",blob,re.I)),
        bool(_extract_rent(blob)), bool(PHONE_RE.search(blob))
    ])
    if row.get("candidate_kind")=="EXTRACTED_LISTING": return "HIGH" if concrete>=3 else "MEDIUM"
    if INDIVIDUAL_URL_RE.search(row.get("url") or ""): return "HIGH" if concrete>=2 else "MEDIUM"
    if _is_collection_page(row): return "LOW"
    return "HIGH" if concrete>=3 else "MEDIUM" if concrete>=1 else "LOW"

def _split_portal_result(row):
    snippet=_space(row.get("snippet"))
    collection=_is_collection_page(row)
    if not collection:
        x=dict(row); x["candidate_kind"]="INDIVIDUAL"; return [x]
    starts=[m.start() for m in LISTING_START_RE.finditer(snippet)]
    if not starts:
        return []
    out=[]
    for i,st in enumerate(starts):
        en=starts[i+1] if i+1<len(starts) else min(len(snippet),st+1700)
        seg=_space(snippet[st:en])
        if len(seg)<90: continue
        has_area=bool(AREA_SQFT.search(seg) or AREA_SQM.search(seg))
        has_price=bool(RENT_RE.search(seg))
        has_floor=bool(re.search(r"\b(?:ground|first|second|third|\d+(?:st|nd|rd|th)?)\s+floor\b|\bfloor\s+(?:ground|\d+)\b",seg,re.I))
        if not (has_area and (has_price or has_floor)): continue
        m=re.search(r"((?:shop|showroom|office\s+space|commercial\s+space|commercial\s+property|warehouse|land|plot|villa|independent\s+house)\s+(?:for\s+(?:rent|sale|lease)|in).{0,100}?)(?=\s+(?:₹|carpet\s+area|super\s+area|area|floor|highlights|availability|posted|dealer|owner)\b|$)",seg,re.I)
        title=_space(m.group(1)) if m else _space(seg[:120])
        out.append({"title":title[:180] or "Property listing","url":row.get("url") or "","snippet":seg[:1700],"source_provider":row.get("source_provider") or "Web","candidate_kind":"EXTRACTED_LISTING","parent_collection_title":row.get("title") or ""})
        if len(out)>=30: break
    return out

def _best_location(blob, parsed):
    lower = blob.lower()
    primary = parsed.get("location")
    if primary and primary.lower() in lower:
        return primary
    for place in LOCATION_EXPANSIONS.get((primary or "").lower(), []):
        if place.lower() in lower:
            return place
    for place in [
        "South Extension 1", "South Extension 2", "South Extension", "Defence Colony",
        "Greater Kailash 1", "Greater Kailash 2", "Green Park", "Hauz Khas", "SDA",
        "Lajpat Nagar", "Saket", "Vasant Kunj", "Siolim", "Assagao", "Anjuna", "Vagator", "Morjim"
    ]:
        if place.lower() in lower:
            return place
    return primary

def extract_candidate(row, parsed):
    blob=_space(f'{row.get("title","")} {row.get("snippet","")}')
    pm=PHONE_RE.search(blob); phone=_phone(pm.group(0)) if pm else None
    floor=None
    if "ground floor" in blob.lower() or "ground-floor" in blob.lower() or "floor ground" in blob.lower(): floor="Ground Floor"
    else:
        fm=re.search(r"\bfloor\s+(\d+)\b|\b(\d+)(?:st|nd|rd|th)?\s+floor\b",blob,re.I)
        if fm: floor=f"{fm.group(1) or fm.group(2)} Floor"
    snippet=_space(row.get("snippet"))
    c={
        "title":_space(row.get("title")) or "Property result",
        "source_provider":row.get("source_provider") or "Web",
        "source_platform":_platform(row.get("url")),"source_url":row.get("url") or "",
        "snippet":snippet[:300]+("…" if len(snippet)>300 else ""),"full_snippet":snippet[:1700],
        "property_type":parsed.get("property_type"),"transaction_type":parsed.get("transaction_type"),
        "city":parsed.get("city"),"location":_best_location(blob,parsed),
        "available_area_sqft":_extract_area(blob),"floor":floor,"rent_display":_extract_rent(blob),
        "posted_date":_extract_posted(blob),"contact_phone":phone,"verification_status":"UNVERIFIED",
        "candidate_kind":row.get("candidate_kind") or "INDIVIDUAL","parent_collection_title":row.get("parent_collection_title")
    }
    c["listing_quality"]=_listing_quality(row,blob)
    return c

def score_candidate(c,p):
    score=0; reasons=[]; mismatches=[]; missing=[]
    hay=f'{c.get("title","")} {c.get("full_snippet",c.get("snippet",""))}'.lower()
    loc=(p.get("location") or "").lower(); city=(p.get("city") or "").lower()
    if loc and loc in hay: score+=22; reasons.append("Location")
    elif any(x.lower() in hay for x in LOCATION_EXPANSIONS.get(loc,[])): score+=20; reasons.append("Micro-market")
    elif city and city in hay: score+=10; reasons.append("City")
    elif loc: mismatches.append("Location not confirmed")
    ptype=p.get("property_type")
    terms={"Land":["land","plot","parcel"],"Retail":["retail","showroom","shop","commercial space"],"Office":["office"],"Warehouse":["warehouse","godown","shed"],"Industrial":["industrial","factory"],"Hospitality":["hotel","guest house","restaurant","banquet"],"Villa":["villa"],"Residential":["apartment","flat","residential"],"Farmhouse":["farmhouse","farm house"]}.get(ptype,[ptype.lower()] if ptype else [])
    if terms and any(x in hay for x in terms): score+=14; reasons.append("Property type")
    elif ptype: mismatches.append("Property type not confirmed")
    tx=p.get("transaction_type")
    if tx=="Lease":
        if any(x in hay for x in ["lease","rent","rental"]): score+=10; reasons.append("Lease")
        elif any(x in hay for x in ["for sale","sale property"]): mismatches.append("Transaction mismatch")
        else: missing.append("Lease intent missing")
    elif tx=="Sale":
        if any(x in hay for x in ["for sale","sale","sell","purchase"]): score+=10; reasons.append("Sale")
        elif any(x in hay for x in ["for rent","lease"]): mismatches.append("Transaction mismatch")
    req_min,req_max=p.get("minimum_area_sqft"),p.get("maximum_area_sqft"); area=c.get("available_area_sqft"); area_exact=False
    if req_min and req_max:
        if area:
            if req_min<=area<=req_max: score+=30; reasons.append("Area exact"); area_exact=True
            else:
                mid=(req_min+req_max)/2; deviation=abs(area-mid)/max(mid,1)
                if deviation<=.20: score+=12; reasons.append("Area near")
                else: mismatches.append(f"Area mismatch: {int(area):,} sqft")
        else: missing.append("Area missing")
    req_floor=p.get("floor"); floor_exact=False
    if req_floor:
        if c.get("floor")==req_floor or req_floor.lower() in hay or (req_floor=="Ground Floor" and "floor ground" in hay): score+=12; reasons.append("Floor exact"); floor_exact=True
        elif c.get("floor"): mismatches.append(f"Floor mismatch: {c.get('floor')}")
        else: missing.append("Floor missing")
    if p.get("title_preference"):
        if "clear title" in hay: score+=5; reasons.append("Clear title")
        else: missing.append("Title status missing")
    if c.get("contact_phone"): score+=5; reasons.append("Contact")
    if c.get("rent_display"): score+=3; reasons.append("Rent/price")
    if c.get("posted_date"): score+=2; reasons.append("Date")
    if c.get("listing_quality")=="HIGH": score+=4; reasons.append("Concrete listing")
    elif c.get("listing_quality")=="LOW": score-=8
    if any(x.startswith("Area mismatch") for x in mismatches): score=min(score,54)
    if any(x.startswith("Floor mismatch") for x in mismatches): score=min(score,54)
    if "Transaction mismatch" in mismatches: score=min(score,39)
    if req_min and req_max and not area_exact: score=min(score,79)
    if req_floor and not floor_exact: score=min(score,79)
    score=max(0,min(int(round(score)),100))
    exact_required=(not req_min or area_exact) and (not req_floor or floor_exact)
    if score>=82 and exact_required and not mismatches and c.get("listing_quality")!="LOW": band="BEST MATCH"
    elif score>=65 and "Transaction mismatch" not in mismatches: band="POSSIBLE MATCH"
    else: band="NEEDS REVIEW"
    c["match_score"]=score;c["match_reasons"]=reasons;c["hard_mismatches"]=mismatches;c["missing_fields"]=missing;c["match_band"]=band
    return c

SCHEMA = '''
CREATE TABLE IF NOT EXISTS pi_discovery_searches(
 id BIGSERIAL PRIMARY KEY,
 search_id VARCHAR(60) UNIQUE NOT NULL,
 linked_requirement_id VARCHAR(60),
 raw_requirement TEXT NOT NULL,
 parsed_requirement JSONB NOT NULL DEFAULT '{}'::jsonb,
 search_mode VARCHAR(20) DEFAULT 'QUICK',
 created_by TEXT,
 query_count INTEGER DEFAULT 0,
 result_count INTEGER DEFAULT 0,
 status VARCHAR(40) DEFAULT 'COMPLETED',
 created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pi_discovery_candidates(
 id BIGSERIAL PRIMARY KEY,
 candidate_id VARCHAR(60) UNIQUE NOT NULL,
 search_id VARCHAR(60) NOT NULL,
 fingerprint VARCHAR(64),
 title TEXT,
 source_provider VARCHAR(60),
 source_platform VARCHAR(60),
 source_url TEXT,
 snippet TEXT,
 property_type TEXT,
 transaction_type TEXT,
 city TEXT,
 location TEXT,
 available_area_sqft NUMERIC(14,2),
 floor TEXT,
 contact_name TEXT,
 contact_phone TEXT,
 owner_name TEXT,
 owner_contact TEXT,
 broker_name TEXT,
 broker_contact TEXT,
 match_score NUMERIC(5,2) DEFAULT 0,
 match_reasons JSONB DEFAULT '[]'::jsonb,
 verification_status VARCHAR(50) DEFAULT 'UNVERIFIED',
 workflow_status VARCHAR(50) DEFAULT 'DISCOVERED',
 verification_notes TEXT,
 property_id TEXT,
 raw_json JSONB DEFAULT '{}'::jsonb,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pi_discovery_search ON pi_discovery_candidates(search_id);
CREATE INDEX IF NOT EXISTS idx_pi_discovery_status ON pi_discovery_candidates(workflow_status);
CREATE INDEX IF NOT EXISTS idx_pi_discovery_fp ON pi_discovery_candidates(fingerprint);
'''

def init_db(engine):
    with engine.begin() as c:
        for stmt in [x.strip() for x in SCHEMA.split(";") if x.strip()]:
            c.execute(text(stmt))

def _code(prefix):
    salt = hashlib.sha1(os.urandom(16)).hexdigest()[:8].upper()
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{salt}"

def _fp(c):
    raw = "|".join([
        _space(c.get("source_url")).lower(), _space(c.get("title")).lower(),
        _space(c.get("location")).lower(), str(c.get("available_area_sqft") or ""),
        _space(c.get("contact_phone"))
    ])
    return hashlib.sha256(raw.encode()).hexdigest()

def save_search(engine, payload, parsed, queries, count):
    sid = _code("SEARCH")
    with engine.begin() as c:
        c.execute(text('''
            INSERT INTO pi_discovery_searches(
              search_id,linked_requirement_id,raw_requirement,parsed_requirement,
              search_mode,created_by,query_count,result_count
            ) VALUES(:sid,:linked,:raw,CAST(:parsed AS jsonb),:mode,:by,:qc,:rc)
        '''), {
            "sid": sid, "linked": payload.linked_requirement_id,
            "raw": payload.requirement, "parsed": json.dumps(parsed),
            "mode": "DEEP" if payload.deep_search else "QUICK",
            "by": payload.created_by, "qc": len(queries), "rc": count
        })
    return sid

def save_candidates(engine, sid, candidates):
    saved = []
    with engine.begin() as conn:
        for c in candidates:
            fp = _fp(c)
            old = conn.execute(text(
                "SELECT candidate_id FROM pi_discovery_candidates WHERE fingerprint=:fp ORDER BY id DESC LIMIT 1"
            ), {"fp": fp}).first()
            workflow = "POSSIBLE_DUPLICATE" if old else "DISCOVERED"
            cid = _code("CAND")
            conn.execute(text('''
                INSERT INTO pi_discovery_candidates(
                  candidate_id,search_id,fingerprint,title,source_provider,source_platform,
                  source_url,snippet,property_type,transaction_type,city,location,
                  available_area_sqft,floor,contact_phone,match_score,match_reasons,
                  verification_status,workflow_status,raw_json
                ) VALUES(
                  :cid,:sid,:fp,:title,:provider,:platform,:url,:snippet,:ptype,:tx,:city,:location,
                  :area,:floor,:phone,:score,CAST(:reasons AS jsonb),
                  'UNVERIFIED',:workflow,CAST(:raw AS jsonb)
                )
            '''), {
                "cid": cid, "sid": sid, "fp": fp, "title": c.get("title"),
                "provider": c.get("source_provider"), "platform": c.get("source_platform"),
                "url": c.get("source_url"), "snippet": c.get("snippet"),
                "ptype": c.get("property_type"), "tx": c.get("transaction_type"),
                "city": c.get("city"), "location": c.get("location"),
                "area": c.get("available_area_sqft"), "floor": c.get("floor"),
                "phone": c.get("contact_phone"), "score": c.get("match_score"),
                "reasons": json.dumps(c.get("match_reasons") or []),
                "workflow": workflow, "raw": json.dumps(c)
            })
            c["candidate_id"] = cid
            c["workflow_status"] = workflow
            if old:
                c["duplicate_candidate_id"] = old[0]
            saved.append(c)
    return saved

def get_candidate(engine, cid):
    with engine.connect() as c:
        row = c.execute(text(
            "SELECT * FROM pi_discovery_candidates WHERE candidate_id=:cid LIMIT 1"
        ), {"cid": cid}).first()
    return dict(row._mapping) if row else None

def _page():
    return r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Find Property by Demand</title>
<style>
:root{--ink:#172033;--muted:#687386;--line:#dfe4ec;--bg:#f5f7fb;--green:#087443;--amber:#9a6700;--red:#b42318}
*{box-sizing:border-box}body{font-family:Arial,sans-serif;background:var(--bg);color:var(--ink);margin:0}.wrap{max-width:1380px;margin:auto;padding:24px}
.card{background:#fff;border:1px solid var(--line);border-radius:15px;padding:17px;margin-bottom:14px}.top{display:flex;justify-content:space-between;gap:12px;align-items:center}.muted{color:var(--muted)}
textarea{width:100%;min-height:95px;padding:13px;font-size:16px;border:1px solid var(--line);border-radius:10px}button{padding:9px 13px;border:0;border-radius:9px;background:var(--ink);color:#fff;font-weight:700;cursor:pointer;margin:3px}button.red{background:var(--red)}button.green{background:var(--green)}button.light{background:#edf1f5;color:var(--ink)}
.summary{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}.metric{padding:10px;border:1px solid var(--line);border-radius:10px;background:#fbfcfe}.metric b{display:block;margin-top:3px}
.section-title{display:flex;justify-content:space-between;align-items:center;margin:22px 0 10px}.count{background:#edf1f5;border-radius:999px;padding:4px 9px;font-size:12px;font-weight:700}
.results{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}.result{background:#fff;border:1px solid var(--line);border-radius:14px;padding:15px}.result.best{border-left:5px solid var(--green)}.result.possible{border-left:5px solid #d49b00}.result.review{border-left:5px solid #aab2c0}
.badge{display:inline-block;padding:5px 8px;border-radius:999px;background:#edf1f5;margin:2px 4px 2px 0;font-size:12px;font-weight:700}.score.best{background:#e8f7ee;color:var(--green)}.score.possible{background:#fff4d6;color:var(--amber)}.score.review{background:#f0f1f3;color:#555}
.fields{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin:10px 0}.field{background:#f8fafc;border-radius:8px;padding:8px}.field small{display:block;color:var(--muted);margin-bottom:2px}.warn{color:var(--red);font-weight:700;font-size:13px}.why{font-size:13px;color:#465166}.snippet{font-size:13px;line-height:1.4;color:#4c566a;max-height:58px;overflow:hidden}.actions{display:flex;gap:5px;flex-wrap:wrap;margin-top:10px}a{color:#1647c8;font-weight:700;text-decoration:none}
.source-group{font-size:12px;color:var(--muted);margin-top:4px}@media(max-width:900px){.summary{grid-template-columns:repeat(2,1fr)}.results{grid-template-columns:1fr}.fields{grid-template-columns:repeat(2,1fr)}}
</style></head><body><div class="wrap">
<div class="top"><div><h1 style="margin-bottom:4px">🔍 Find Property by Demand</h1><div class="muted">Organized results · Internet findings remain UNVERIFIED until your team confirms availability.</div></div><a href="/final-dashboard-v3">← Dashboard</a></div>
<div class="card"><textarea id="q">Require retail space on lease in South Delhi, 4000 sqft, ground floor.</textarea><div style="margin-top:10px"><label><input id="deep" type="checkbox"> Deep Search</label><button onclick="runSearch()">Find Properties</button><span id="status" class="muted"></span></div></div>
<div id="summary"></div><div id="best"></div><div id="possible"></div><div id="review"></div>
<script>
const e=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]));
function card(c){const cls=c.match_band==='BEST MATCH'?'best':c.match_band==='POSSIBLE MATCH'?'possible':'review';const mism=(c.hard_mismatches||[]).map(x=>`<div class="warn">⚠ ${e(x)}</div>`).join('');const miss=(c.missing_fields||[]).map(x=>`<div class="why">△ ${e(x)}</div>`).join('');return `<div class="result ${cls}">
<div><span class="badge score ${cls}">${e(c.match_score)}% · ${e(c.match_band)}</span><span class="badge">${e(c.source_platform)}</span><span class="badge">${e(c.listing_quality||'UNKNOWN')} evidence</span><span class="badge">${e(c.workflow_status)}</span></div>
<h3 style="margin:9px 0 5px">${e(c.title)}</h3><div class="source-group">Source engine: ${e(c.source_provider)}${c.posted_date?' · Posted '+e(c.posted_date):''}</div>
<div class="fields"><div class="field"><small>Location</small><b>${e(c.location||'Not extracted')}</b></div><div class="field"><small>Area</small><b>${e(c.available_area_sqft?Math.round(c.available_area_sqft).toLocaleString()+' sqft':'Not extracted')}</b></div><div class="field"><small>Floor</small><b>${e(c.floor||'Not extracted')}</b></div><div class="field"><small>Rent / Price</small><b>${e(c.rent_display||'Not extracted')}</b></div><div class="field"><small>Contact</small><b>${e(c.contact_phone||'Not found')}</b></div><div class="field"><small>Status</small><b>UNVERIFIED</b></div></div>
${mism}${miss}<div class="why"><b>Matched:</b> ${e((c.match_reasons||[]).join(' · ')||'Limited structured evidence')}</div><p class="snippet">${e(c.snippet||'')}</p>
<div class="actions">${c.source_url?`<a href="${e(c.source_url)}" target="_blank" rel="noopener"><button class="light">View Source ↗</button></a>`:''}<button class="light" onclick="act('${e(c.candidate_id)}','select')">Select</button><button class="red" onclick="act('${e(c.candidate_id)}','reject')">Reject</button><button class="green" onclick="verify('${e(c.candidate_id)}')">Mark Verified</button><button onclick="act('${e(c.candidate_id)}','add-to-database')">Add to Database</button></div></div>`}
function section(id,title,rows){const el=document.getElementById(id);if(!rows.length){el.innerHTML='';return}el.innerHTML=`<div class="section-title"><h2>${title}</h2><span class="count">${rows.length}</span></div><div class="results">${rows.map(card).join('')}</div>`}
async function runSearch(){const status=document.getElementById('status');status.textContent=' Searching and organizing...';['summary','best','possible','review'].forEach(id=>document.getElementById(id).innerHTML='');const r=await fetch('/api/discovery/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({requirement:document.getElementById('q').value,deep_search:document.getElementById('deep').checked})});const d=await r.json();if(!r.ok){status.textContent=' '+(d.detail||'Search failed');return}const p=d.parsed_requirement||{};const best=d.candidates.filter(x=>x.match_band==='BEST MATCH');const possible=d.candidates.filter(x=>x.match_band==='POSSIBLE MATCH');const review=d.candidates.filter(x=>x.match_band==='NEEDS REVIEW');status.textContent=` ${d.candidates.length} organized candidates found`;
document.getElementById('summary').innerHTML=`<div class="card"><div class="summary"><div class="metric">Property Type<b>${e(p.property_type||'Any')}</b></div><div class="metric">Transaction<b>${e(p.transaction_type||'Any')}</b></div><div class="metric">Location<b>${e(p.location||p.city||'Any')}</b></div><div class="metric">Area<b>${p.minimum_area_sqft?Math.round(p.minimum_area_sqft).toLocaleString()+'–'+Math.round(p.maximum_area_sqft).toLocaleString()+' sqft':'Any'}</b></div><div class="metric">Floor<b>${e(p.floor||'Any')}</b></div><div class="metric">Results<b>${d.candidates.length}</b></div></div></div>`;
section('best','✅ Best Matches',best);section('possible','🟡 Possible Matches',possible);section('review','⚪ Needs Review',review)}
async function act(id,a){const r=await fetch(`/api/discovery/candidates/${encodeURIComponent(id)}/${a}`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const d=await r.json();alert(d.message||d.detail||JSON.stringify(d))}
async function verify(id){const by=prompt('Verified by:','');if(by===null)return;const notes=prompt('Availability / verification notes:','');const r=await fetch(`/api/discovery/candidates/${encodeURIComponent(id)}/verify`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({verified_by:by,notes:notes||''})});const d=await r.json();alert(d.message||d.detail||JSON.stringify(d))}
</script></div></body></html>'''

def install_property_discovery(app, engine, need_login, save_property, actor_name=None):
    @app.on_event("startup")
    def _discovery_startup():
        init_db(engine)

    @app.get("/property-discovery", response_class=HTMLResponse)
    def property_discovery_page(req: Request):
        need_login(req)
        return HTMLResponse(_page())

    @app.get("/api/discovery/providers")
    def discovery_providers(req: Request):
        need_login(req)
        now = time.time()
        data = {}
        for name, configured in configured_providers().items():
            s = dict(_state(name))
            s["configured"] = configured
            s["cooldown_seconds"] = max(0, int(s.pop("cooldown_until", 0) - now))
            data[name] = s
        return {"status": "ok", "version": DISCOVERY_VERSION, "providers": data}

    @app.post("/api/discovery/search")
    def discovery_search(payload: DiscoverySearchInput, req: Request):
        need_login(req)
        p = parse_requirement(payload.requirement)
        queries = build_queries(p, payload.deep_search)
        raw, logs = search_waterfall(queries, payload.deep_search)
        candidates = []
        expanded_rows = []
        suppressed_collections = 0
        for row in raw:
            pieces = _split_portal_result(row)
            if _is_collection_page(row) and not pieces:
                suppressed_collections += 1
            expanded_rows.extend(pieces)
        for row in expanded_rows:
            c = score_candidate(extract_candidate(row, p), p)
            if c.get("listing_quality") == "LOW" and not c.get("available_area_sqft") and not c.get("contact_phone"):
                continue
            if c["match_score"] >= DISCOVERY_MIN_SCORE:
                candidates.append(c)
        unique = {}
        for c in candidates:
            area_bucket = int(round((c.get("available_area_sqft") or 0) / 50.0) * 50)
            key = (
                _space(c.get("location")).lower(), area_bucket,
                _space(c.get("floor")).lower(), _space(c.get("contact_phone")),
                _space(c.get("title")).lower()[:80]
            )
            if key not in unique or c.get("match_score",0) > unique[key].get("match_score",0):
                unique[key] = c
        candidates = list(unique.values())
        candidates.sort(key=lambda x: (x.get("match_band") == "BEST MATCH", x.get("match_score", 0)), reverse=True)

        if not payload.created_by and actor_name:
            try:
                payload.created_by = actor_name(req)
            except Exception:
                pass

        sid = save_search(engine, payload, p, queries, len(candidates))
        candidates = save_candidates(engine, sid, candidates)
        return {
            "status": "ok", "search_id": sid,
            "search_mode": "DEEP" if payload.deep_search else "QUICK",
            "parsed_requirement": p, "queries": queries,
            "provider_logs": logs, "candidates": candidates
        }

    @app.get("/api/discovery/candidates")
    def discovery_candidates(req: Request, search_id: Optional[str] = None, workflow_status: Optional[str] = None, limit: int = 200):
        need_login(req)
        limit = max(1, min(limit, 1000))
        wh, params = [], {"n": limit}
        if search_id:
            wh.append("search_id=:sid"); params["sid"] = search_id
        if workflow_status:
            wh.append("workflow_status=:ws"); params["ws"] = workflow_status
        where = "WHERE " + " AND ".join(wh) if wh else ""
        with engine.connect() as c:
            rows = c.execute(text(f"SELECT * FROM pi_discovery_candidates {where} ORDER BY match_score DESC,id DESC LIMIT :n"), params).fetchall()
        out = []
        for r in rows:
            d = dict(r._mapping)
            for k,v in list(d.items()):
                if hasattr(v, "isoformat"):
                    d[k] = v.isoformat()
            out.append(d)
        return {"status": "ok", "rows": out}

    @app.post("/api/discovery/candidates/{candidate_id}/select")
    def discovery_select(candidate_id: str, payload: DiscoveryStatusInput, req: Request):
        need_login(req)
        with engine.begin() as c:
            r = c.execute(text("UPDATE pi_discovery_candidates SET workflow_status='SELECTED',verification_notes=COALESCE(:n,verification_notes),updated_at=NOW() WHERE candidate_id=:cid"), {"cid": candidate_id, "n": payload.notes})
            if not r.rowcount:
                raise HTTPException(404, "Candidate not found")
        return {"status": "ok", "message": "Selected for verification."}

    @app.post("/api/discovery/candidates/{candidate_id}/reject")
    def discovery_reject(candidate_id: str, payload: DiscoveryStatusInput, req: Request):
        need_login(req)
        with engine.begin() as c:
            r = c.execute(text("UPDATE pi_discovery_candidates SET workflow_status='REJECTED',verification_notes=COALESCE(:n,verification_notes),updated_at=NOW() WHERE candidate_id=:cid"), {"cid": candidate_id, "n": payload.notes})
            if not r.rowcount:
                raise HTTPException(404, "Candidate not found")
        return {"status": "ok", "message": "Candidate rejected."}

    @app.post("/api/discovery/candidates/{candidate_id}/verify")
    def discovery_verify(candidate_id: str, payload: VerifyInput, req: Request):
        need_login(req)
        who = payload.verified_by
        if not who and actor_name:
            try:
                who = actor_name(req)
            except Exception:
                pass
        who = who or "team"
        with engine.begin() as c:
            r = c.execute(text('''
                UPDATE pi_discovery_candidates SET
                  verification_status='VERIFIED',workflow_status='VERIFIED',
                  contact_name=COALESCE(NULLIF(:cn,''),contact_name),
                  contact_phone=COALESCE(NULLIF(:cp,''),contact_phone),
                  owner_name=COALESCE(NULLIF(:on,''),owner_name),
                  owner_contact=COALESCE(NULLIF(:oc,''),owner_contact),
                  broker_name=COALESCE(NULLIF(:bn,''),broker_name),
                  broker_contact=COALESCE(NULLIF(:bc,''),broker_contact),
                  verification_notes=:notes,updated_at=NOW()
                WHERE candidate_id=:cid
            '''), {
                "cid": candidate_id, "cn": payload.contact_name,
                "cp": _phone(payload.contact_phone) if payload.contact_phone else None,
                "on": payload.owner_name, "oc": _phone(payload.owner_contact) if payload.owner_contact else None,
                "bn": payload.broker_name, "bc": _phone(payload.broker_contact) if payload.broker_contact else None,
                "notes": f"Verified by {who}. {payload.notes or ''}".strip()
            })
            if not r.rowcount:
                raise HTTPException(404, "Candidate not found")
        return {"status": "ok", "message": "Candidate marked VERIFIED."}

    @app.post("/api/discovery/candidates/{candidate_id}/add-to-database")
    def discovery_add(candidate_id: str, req: Request):
        need_login(req)
        c = get_candidate(engine, candidate_id)
        if not c:
            raise HTTPException(404, "Candidate not found")
        if c.get("verification_status") != "VERIFIED":
            raise HTTPException(400, "Verify current availability before adding this property to the master database.")

        payload = {
            "property_name": c.get("title"), "property_type": c.get("property_type") or "NA",
            "city": c.get("city") or "NA", "location": c.get("location") or "NA",
            "available_area_sqft": float(c["available_area_sqft"]) if c.get("available_area_sqft") is not None else None,
            "minimum_area_sqft": None, "maximum_area_sqft": None, "floor": c.get("floor"),
            "rent_or_sale": c.get("transaction_type"), "nearby_brands": None,
            "suitable_category": None, "parking": None, "owner_name": c.get("owner_name"),
            "owner_contact": c.get("owner_contact"), "broker_name": c.get("broker_name"),
            "broker_contact": c.get("broker_contact") or c.get("contact_phone"),
            "remarks": f"AI Discovery. Source: {c.get('source_platform')}. Match score: {c.get('match_score')}. {c.get('verification_notes') or ''}",
            "image_urls": None, "video_urls": None, "brochure_url": None,
            "source": c.get("source_url") or f"AI Discovery / {c.get('source_platform')}",
            "extraction_confidence": float(c.get("match_score") or 0)
        }
        result = save_property(payload)
        pid = result.get("property_id") if isinstance(result, dict) else None
        duplicate = isinstance(result, dict) and result.get("status") == "duplicate"

        with engine.begin() as conn:
            conn.execute(text("UPDATE pi_discovery_candidates SET workflow_status=:ws,property_id=:pid,updated_at=NOW() WHERE candidate_id=:cid"), {
                "ws": "DUPLICATE_IN_DATABASE" if duplicate else "ADDED_TO_DATABASE",
                "pid": pid, "cid": candidate_id
            })
        if duplicate:
            return {"status": "duplicate", "property_id": pid, "message": f"Existing property {pid} matched. No duplicate master record created."}
        return {"status": "ok", "property_id": pid, "message": f"Added to master Property Database as {pid}."}

    @app.get("/api/discovery/stats")
    def discovery_stats(req: Request):
        need_login(req)
        with engine.connect() as c:
            r = c.execute(text('''
                SELECT COUNT(*) total,
                COUNT(*) FILTER(WHERE workflow_status='DISCOVERED') discovered,
                COUNT(*) FILTER(WHERE workflow_status='SELECTED') selected,
                COUNT(*) FILTER(WHERE workflow_status='VERIFIED') verified,
                COUNT(*) FILTER(WHERE workflow_status='ADDED_TO_DATABASE') added,
                COUNT(*) FILTER(WHERE workflow_status='REJECTED') rejected
                FROM pi_discovery_candidates
            ''')).first()
        return {"status": "ok", **dict(r._mapping)}

    return app

# ============================================================================
# V17.3 STRUCTURED RESULT OVERRIDES
# One dashboard card = one property record. Collection pages never appear as
# properties. They must be split into self-contained listing segments or drop.
# ============================================================================
DISCOVERY_VERSION = "17.3-STRUCTURED-RESULTS"
DISCOVERY_MIN_SCORE = max(int(os.getenv("DISCOVERY_MIN_SCORE", "45")), 45)

STRICT_LISTING_START_RE = re.compile(
    r"(?:^|(?<=\s))"
    r"(?:(?:featured\s+dealer|dealer|owner)\s*[·:\-]?\s*)?"
    r"(?:[a-z0-9 .&'()/-]{0,70}\s+)?"
    r"(?:shop|showroom|office\s+space|ready\s+to\s+move\s+office\s+space|bare\s+shell\s+office\s+space|"
    r"commercial\s+space|commercial\s+property|retail\s+space|warehouse|land|plot|villa|farmhouse|hotel|guest\s+house)"
    r"\s+(?:for\s+(?:rent|lease|sale)|in)\b",
    re.I,
)

RATE_PER_SQFT_RE = re.compile(r"(?:₹|rs\.?)\s*([\d,.]+)\s*/\s*(?:sq\.?\s*ft|sqft)", re.I)
POSTED_DATE_RE = re.compile(
    r"(?:posted\s*:?[ ]*|posted\s+on\s+)?"
    r"((?:today|yesterday|\d+\s*(?:h|d|w|mo)\s*ago)|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:\s*,?\s*['’]?\d{2,4})?)",
    re.I,
)


def _rent_value_and_display(blob):
    # Prefer values explicitly tied to month/rent/lease to avoid security deposits.
    patterns = [
        r"(?:rent(?:al)?|lease\s+amount|asking\s+rent)\s*[:\-]?\s*(?:₹|rs\.?)\s*([\d,.]+)\s*(lac|lakh|l|crore|cr)?",
        r"(?:₹|rs\.?)\s*([\d,.]+)\s*(lac|lakh|l|crore|cr)?\s*(?:/\s*month|per\s*month)",
    ]
    for pat in patterns:
        m = re.search(pat, blob, re.I)
        if not m:
            continue
        value = _num(m.group(1))
        if value is None:
            continue
        unit = (m.group(2) or "").lower()
        if unit in {"lac", "lakh", "l"}:
            value *= 100000
        elif unit in {"crore", "cr"}:
            value *= 10000000
        return round(value, 2), f"₹{value:,.0f}/month"
    return None, None


def _extract_rate_per_sqft(blob):
    m = RATE_PER_SQFT_RE.search(blob)
    return _num(m.group(1)) if m else None


def _extract_structured_posted(blob):
    m = POSTED_DATE_RE.search(blob)
    return _space(m.group(1)) if m else None


def _extract_structured_floor(blob):
    low = blob.lower()
    if "ground floor" in low or "ground-floor" in low or "floor ground" in low:
        return "Ground Floor"
    if "upper basement" in low:
        return "Upper Basement"
    if "lower ground" in low:
        return "Lower Ground"
    m = re.search(r"\bfloor\s+(\d+)\b|\b(\d+)(?:st|nd|rd|th)?\s+floor\b", low)
    if m:
        return f"{m.group(1) or m.group(2)} Floor"
    return None


def _extract_structured_location(blob, parsed):
    low = blob.lower()
    candidates = LOCATION_EXPANSIONS.get((parsed.get("location") or "").lower(), [])
    # Longer locality names first so South Extension 2 wins over South Extension.
    for place in sorted(candidates, key=len, reverse=True):
        if place.lower() in low:
            return place
    explicit = [
        "Greater Kailash 2", "Greater Kailash 1", "GK 2", "GK 1",
        "South Extension 2", "South Extension 1", "South Extension",
        "Defence Colony", "Green Park", "Hauz Khas", "Kailash Colony",
        "Lajpat Nagar 4", "Lajpat Nagar 3", "Lajpat Nagar 2", "Lajpat Nagar",
        "Nehru Place", "Saket", "Vasant Kunj", "Jasola", "Okhla Phase 2",
        "New Friends Colony", "Meharchand Market", "SDA", "Chattarpur",
        "Siolim", "Assagao", "Anjuna", "Vagator", "Morjim", "Porvorim",
    ]
    for place in explicit:
        if place.lower() in low:
            return place
    return parsed.get("location")


def _extract_structured_property_type(blob, fallback):
    low = blob.lower()
    if any(x in low for x in ["shop", "showroom", "retail space", "high street retail"]):
        return "Retail"
    if "office" in low:
        return "Office"
    if any(x in low for x in ["land", "plot", "parcel"]):
        return "Land"
    if any(x in low for x in ["warehouse", "godown", "industrial shed"]):
        return "Warehouse"
    if any(x in low for x in ["hotel", "guest house", "restaurant", "banquet"]):
        return "Hospitality"
    return fallback


def _extract_availability(blob):
    low = blob.lower()
    if any(x in low for x in ["immediately available", "availability immediate", "available immediately", "ready to move"]):
        return "Immediate / Ready"
    return None


def _extract_furnishing(blob):
    low = blob.lower()
    if "unfurnished" in low:
        return "Unfurnished"
    if "semi furnished" in low or "semi-furnished" in low:
        return "Semi Furnished"
    if "furnished" in low:
        return "Furnished"
    if "bare shell" in low:
        return "Bare Shell"
    return None


def _strict_collection_page(row):
    title = _space(row.get("title"))
    url = _space(row.get("url"))
    snippet = _space(row.get("snippet"))
    if _is_collection_page(row):
        return True
    if re.search(r"\b\d[\d,]*\+\s*(?:shops?|showrooms?|commercial|properties|office)", title, re.I):
        return True
    if len(list(STRICT_LISTING_START_RE.finditer(snippet))) >= 3:
        return True
    return False


def _split_portal_result(row):
    """Return only self-contained property records. Never return a collection page."""
    if not _strict_collection_page(row):
        x = dict(row)
        x["candidate_kind"] = "INDIVIDUAL"
        return [x]

    snippet = _space(row.get("snippet"))
    matches = list(STRICT_LISTING_START_RE.finditer(snippet))
    if not matches:
        return []

    out = []
    for i, match in enumerate(matches[:24]):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else min(len(snippet), start + 1800)
        segment = _space(snippet[start:end])
        if len(segment) < 90:
            continue

        area = _extract_area(segment)
        floor = _extract_structured_floor(segment)
        rent_value, _ = _rent_value_and_display(segment)
        phone_match = PHONE_RE.search(segment)

        # A segment needs at least two concrete property facts. This prevents text
        # from one listing inheriting the numbers of the next listing.
        concrete = sum(bool(v) for v in [area, floor, rent_value, phone_match])
        if concrete < 2:
            continue

        # Trim the title before the first obvious field/value marker.
        tm = re.split(
            r"\s+(?=(?:₹|rs\.?|carpet\s+area|super\s+area|built[- ]?up\s+area|area\s*:|floor\s+|highlights\s*:|availability\s*:|posted\s*:))",
            segment,
            maxsplit=1,
            flags=re.I,
        )
        title = _space(tm[0])[:180]
        if not title:
            title = "Property listing"

        out.append({
            "title": title,
            "url": row.get("url") or "",
            "snippet": segment[:1800],
            "source_provider": row.get("source_provider") or "Web",
            "candidate_kind": "EXTRACTED_LISTING",
            "parent_collection_title": row.get("title") or "",
        })
    return out


def extract_candidate(row, parsed):
    # IMPORTANT: everything is extracted only from this listing segment.
    blob = _space(f'{row.get("title", "")} {row.get("snippet", "")}')
    phone_match = PHONE_RE.search(blob)
    phone = _phone(phone_match.group(0)) if phone_match else None
    rent_value, rent_display = _rent_value_and_display(blob)
    rate = _extract_rate_per_sqft(blob)
    snippet = _space(row.get("snippet"))

    area = _extract_area(blob)
    floor = _extract_structured_floor(blob)
    location = _extract_structured_location(blob, parsed)
    ptype = _extract_structured_property_type(blob, parsed.get("property_type"))

    candidate = {
        # Existing keys retained for database compatibility.
        "title": _space(row.get("title"))[:180] or "Property listing",
        "source_provider": row.get("source_provider") or "Web",
        "source_platform": _platform(row.get("url")),
        "source_url": row.get("url") or "",
        "snippet": snippet[:260] + ("…" if len(snippet) > 260 else ""),
        "full_snippet": snippet[:1800],
        "property_type": ptype,
        "transaction_type": parsed.get("transaction_type"),
        "city": parsed.get("city"),
        "location": location,
        "available_area_sqft": area,
        "floor": floor,
        "contact_phone": phone,
        "verification_status": "UNVERIFIED",
        "candidate_kind": row.get("candidate_kind") or "INDIVIDUAL",
        "parent_collection_title": row.get("parent_collection_title"),
        # New structured fields.
        "rent_amount": rent_value,
        "rent_display": rent_display,
        "rate_per_sqft": rate,
        "availability": _extract_availability(blob),
        "furnishing": _extract_furnishing(blob),
        "posted_date": _extract_structured_posted(blob),
    }

    concrete = sum(bool(candidate.get(k)) for k in [
        "available_area_sqft", "floor", "rent_amount", "contact_phone", "posted_date"
    ])
    candidate["listing_quality"] = "HIGH" if concrete >= 4 else "MEDIUM" if concrete >= 2 else "LOW"
    return candidate


def score_candidate(c, p):
    score = 0
    reasons = []
    mismatches = []
    missing = []
    hay = f'{c.get("title", "")} {c.get("full_snippet", c.get("snippet", ""))}'.lower()

    req_loc = (p.get("location") or "").lower()
    city = (p.get("city") or "").lower()
    extracted_loc = (c.get("location") or "").lower()
    expanded = [x.lower() for x in LOCATION_EXPANSIONS.get(req_loc, [])]
    if req_loc and req_loc in hay:
        score += 20; reasons.append("Location matched")
    elif extracted_loc and extracted_loc in expanded:
        score += 18; reasons.append("Micro-market matched")
    elif city and city in hay:
        score += 10; reasons.append("City matched")
    else:
        missing.append("Location evidence")

    req_type = p.get("property_type")
    if req_type:
        if c.get("property_type") == req_type:
            score += 15; reasons.append("Property type matched")
        elif c.get("property_type"):
            mismatches.append(f"Property type {c.get('property_type')} vs required {req_type}")
        else:
            missing.append("Property type")

    tx = p.get("transaction_type")
    if tx == "Lease":
        if any(x in hay for x in ["rent", "lease"]):
            score += 10; reasons.append("Lease matched")
        elif "for sale" in hay:
            mismatches.append("Transaction is sale, not lease")
        else:
            missing.append("Lease evidence")
    elif tx == "Sale":
        if any(x in hay for x in ["for sale", "sale", "sell"]):
            score += 10; reasons.append("Sale matched")
        elif any(x in hay for x in ["for rent", "lease"]):
            mismatches.append("Transaction is lease, not sale")

    req_min, req_max = p.get("minimum_area_sqft"), p.get("maximum_area_sqft")
    area = c.get("available_area_sqft")
    area_exact = False
    if req_min and req_max:
        if area is None:
            missing.append("Area")
        elif req_min <= area <= req_max:
            score += 30; reasons.append("Area within requirement"); area_exact = True
        else:
            mid = (req_min + req_max) / 2
            deviation = abs(area - mid) / max(mid, 1)
            if deviation <= 0.15:
                score += 16; reasons.append("Area close to requirement")
            else:
                mismatches.append(f"Area {area:,.0f} sqft outside {req_min:,.0f}-{req_max:,.0f} sqft")

    req_floor = p.get("floor")
    floor_exact = False
    if req_floor:
        if c.get("floor") == req_floor:
            score += 15; reasons.append("Floor matched"); floor_exact = True
        elif c.get("floor"):
            mismatches.append(f"Floor {c.get('floor')} vs required {req_floor}")
        else:
            missing.append("Floor")

    if p.get("title_preference"):
        if "clear title" in hay:
            score += 5; reasons.append("Clear title mentioned")
        else:
            missing.append("Clear title evidence")

    if c.get("contact_phone"):
        score += 4; reasons.append("Contact found")
    if c.get("rent_amount"):
        score += 3; reasons.append("Rent extracted")
    if c.get("posted_date"):
        score += 2; reasons.append("Posted date found")
    if c.get("listing_quality") == "HIGH":
        score += 4; reasons.append("Strong listing evidence")

    # Explicit demand fields are hard gates. Wrong area/floor/type/transaction
    # can never appear as a Best/Possible match just because location matches.
    if mismatches:
        score = min(score, 59)
    if req_min and req_max and not area_exact:
        score = min(score, 79)
    if req_floor and not floor_exact:
        score = min(score, 79)

    score = max(0, min(int(round(score)), 100))
    if not mismatches and score >= 85 and (not req_min or area_exact) and (not req_floor or floor_exact):
        band = "BEST MATCH"
    elif not mismatches and score >= 65:
        band = "POSSIBLE MATCH"
    else:
        band = "NEEDS REVIEW"

    evidence_count = sum(bool(c.get(k)) for k in [
        "location", "available_area_sqft", "floor", "rent_amount", "contact_phone", "posted_date"
    ])
    c["match_score"] = score
    c["match_band"] = band
    c["match_reasons"] = reasons
    c["hard_mismatches"] = mismatches
    c["missing_fields"] = missing
    c["evidence_quality"] = "HIGH" if evidence_count >= 5 else "MEDIUM" if evidence_count >= 3 else "LOW"
    return c


def _page():
    # Raw source text is deliberately collapsed. Every visible card has the
    # same fixed fields, preventing one property's content spilling into another.
    return r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Find Property by Demand</title>
<style>
:root{--bg:#f5f7fb;--card:#fff;--ink:#172033;--muted:#687386;--line:#dfe4ec;--green:#087443;--amber:#8a5a00;--red:#b42318}
*{box-sizing:border-box}body{font-family:Arial,sans-serif;background:var(--bg);color:var(--ink);margin:0}.wrap{max-width:1380px;margin:auto;padding:26px}.card{background:#fff;border:1px solid var(--line);border-radius:15px;padding:17px;margin-bottom:14px}textarea{width:100%;min-height:100px;padding:13px;font-size:16px;border:1px solid var(--line);border-radius:10px}button{padding:9px 13px;border:0;border-radius:9px;background:var(--ink);color:#fff;font-weight:700;cursor:pointer;margin:3px}button.light{background:#edf1f5;color:var(--ink)}button.green{background:var(--green)}button.red{background:var(--red)}.muted{color:var(--muted)}.summary{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}.metric{padding:10px;border:1px solid var(--line);border-radius:10px;background:#fbfcfe}.metric b{display:block;margin-top:4px}.section{margin:22px 0 10px;display:flex;justify-content:space-between;align-items:center}.count{background:#edf1f5;border-radius:999px;padding:4px 9px;font-size:12px;font-weight:700}.results{display:grid;grid-template-columns:repeat(2,1fr);gap:13px}.result{background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px}.result.best{border-left:5px solid var(--green)}.result.possible{border-left:5px solid #d49b00}.result.review{border-left:5px solid #aab2c0}.badge{display:inline-block;padding:5px 8px;border-radius:999px;background:#edf1f5;margin:2px 4px 2px 0;font-size:12px;font-weight:700}.bestscore{background:#e8f7ee;color:var(--green)}.possiblescore{background:#fff4d6;color:var(--amber)}.reviewscore{background:#f0f1f3;color:#555}.fields{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:12px 0}.field{background:#f8fafc;border-radius:8px;padding:9px;min-height:58px}.field small{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;margin-bottom:4px}.warning{background:#fff1f0;color:var(--red);padding:8px;border-radius:8px;margin:7px 0;font-size:13px}.missing{color:var(--muted);font-size:13px;margin:7px 0}.why{font-size:13px;color:#465166;margin:7px 0}.actions{display:flex;flex-wrap:wrap;gap:5px;margin-top:10px}details{margin-top:10px;background:#fafbfc;border-radius:8px;padding:8px;font-size:13px;color:#566176}details summary{cursor:pointer;font-weight:700}a{text-decoration:none}@media(max-width:900px){.summary{grid-template-columns:repeat(2,1fr)}.results{grid-template-columns:1fr}.fields{grid-template-columns:repeat(2,1fr)}}
</style></head><body><div class="wrap">
<div style="display:flex;justify-content:space-between;align-items:center;gap:12px"><div><h1 style="margin-bottom:4px">🔍 Find Property by Demand</h1><div class="muted">One card = one property record. No overlapping portal data.</div></div><a href="/final-dashboard-v3">← Dashboard</a></div>
<div class="card"><textarea id="q">Require retail space on lease in South Delhi, 4000 sqft, ground floor.</textarea><div style="margin-top:10px"><label><input id="deep" type="checkbox"> Deep Search</label><button onclick="runSearch()">Find Properties</button><span id="status" class="muted"></span></div></div>
<div id="summary"></div><div id="best"></div><div id="possible"></div><div id="review"></div>
<script>
const e=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]));
function f(label,value){return `<div class="field"><small>${e(label)}</small><b>${e(value||'Not found')}</b></div>`}
function card(c){const cls=c.match_band==='BEST MATCH'?'best':c.match_band==='POSSIBLE MATCH'?'possible':'review';const scorecls=cls==='best'?'bestscore':cls==='possible'?'possiblescore':'reviewscore';return `<div class="result ${cls}"><div><span class="badge ${scorecls}">${e(c.match_score)}% · ${e(c.match_band)}</span><span class="badge">${e(c.source_platform)}</span><span class="badge">${e(c.evidence_quality||c.listing_quality||'LOW')} evidence</span><span class="badge">${e(c.workflow_status)}</span></div><h3 style="margin:9px 0 4px">${e(c.title)}</h3><div class="fields">${f('Property Type',c.property_type)}${f('Transaction',c.transaction_type)}${f('Location',c.location)}${f('Area',c.available_area_sqft?Math.round(c.available_area_sqft).toLocaleString()+' sqft':null)}${f('Floor',c.floor)}${f('Rent',c.rent_display)}${f('Rate / sqft',c.rate_per_sqft?'₹'+Number(c.rate_per_sqft).toLocaleString('en-IN'):null)}${f('Availability',c.availability)}${f('Furnishing',c.furnishing)}${f('Contact',c.contact_phone)}${f('Posted',c.posted_date)}${f('Verification','UNVERIFIED')}</div>${(c.hard_mismatches||[]).length?`<div class="warning"><b>Mismatch:</b> ${e(c.hard_mismatches.join(' · '))}</div>`:''}${(c.missing_fields||[]).length?`<div class="missing"><b>Missing:</b> ${e(c.missing_fields.join(', '))}</div>`:''}<div class="why"><b>Matched:</b> ${e((c.match_reasons||[]).join(' · ')||'Limited evidence')}</div><details><summary>Source evidence</summary><p>${e(c.full_snippet||c.snippet||'')}</p></details><div class="actions">${c.source_url?`<a href="${e(c.source_url)}" target="_blank" rel="noopener"><button class="light">View Source ↗</button></a>`:''}<button class="light" onclick="act('${e(c.candidate_id)}','select')">Select</button><button class="red" onclick="act('${e(c.candidate_id)}','reject')">Reject</button><button class="green" onclick="verify('${e(c.candidate_id)}')">Mark Verified</button><button onclick="act('${e(c.candidate_id)}','add-to-database')">Add to Database</button></div></div>`}
function section(id,title,rows){const el=document.getElementById(id);if(!rows.length){el.innerHTML='';return}el.innerHTML=`<div class="section"><h2>${title}</h2><span class="count">${rows.length}</span></div><div class="results">${rows.map(card).join('')}</div>`}
async function runSearch(){const status=document.getElementById('status');status.textContent=' Searching and structuring individual properties...';['summary','best','possible','review'].forEach(id=>document.getElementById(id).innerHTML='');const r=await fetch('/api/discovery/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({requirement:document.getElementById('q').value,deep_search:document.getElementById('deep').checked})});const d=await r.json();if(!r.ok){status.textContent=' '+(d.detail||'Search failed');return}const p=d.parsed_requirement||{};const best=d.candidates.filter(x=>x.match_band==='BEST MATCH');const possible=d.candidates.filter(x=>x.match_band==='POSSIBLE MATCH');const review=d.candidates.filter(x=>x.match_band==='NEEDS REVIEW');status.textContent=` ${d.candidates.length} individual property records found`;document.getElementById('summary').innerHTML=`<div class="card"><div class="summary"><div class="metric">Type<b>${e(p.property_type||'Any')}</b></div><div class="metric">Transaction<b>${e(p.transaction_type||'Any')}</b></div><div class="metric">Location<b>${e(p.location||p.city||'Any')}</b></div><div class="metric">Area<b>${p.minimum_area_sqft?Math.round(p.minimum_area_sqft).toLocaleString()+'–'+Math.round(p.maximum_area_sqft).toLocaleString()+' sqft':'Any'}</b></div><div class="metric">Floor<b>${e(p.floor||'Any')}</b></div><div class="metric">Results<b>${d.candidates.length}</b></div></div></div>`;section('best','✅ Best Matches',best);section('possible','🟡 Possible Matches',possible);section('review','⚪ Needs Review',review)}
async function act(id,a){const r=await fetch(`/api/discovery/candidates/${encodeURIComponent(id)}/${a}`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const d=await r.json();alert(d.message||d.detail||JSON.stringify(d))}
async function verify(id){const by=prompt('Verified by:','');if(by===null)return;const notes=prompt('Availability / verification notes:','');const r=await fetch(`/api/discovery/candidates/${encodeURIComponent(id)}/verify`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({verified_by:by,notes:notes||''})});const d=await r.json();alert(d.message||d.detail||JSON.stringify(d))}
</script></div></body></html>'''

# === V17.4 FINAL STRUCTURED EXTRACTION OVERRIDES ===
# One visible candidate must represent exactly one property.
DISCOVERY_VERSION = "17.4-FINAL-STRUCTURED"
DISCOVERY_MIN_SCORE = 50

V174_LISTING_ANCHOR_RE = re.compile(
    r"\b(?:shop|showroom|office\s+space|commercial\s+(?:space|property)|retail\s+space|"
    r"land|plot|warehouse|godown|villa|farmhouse|hotel|guest\s+house)\s+"
    r"(?:for\s+(?:rent|lease|sale)|in)\b",
    re.I,
)

V174_GENERIC_SOURCE_PATTERNS = [
    r"we\s+have\s+multiple\s+(?:commercial\s+)?properties",
    r"we(?:'re| are)\s+actively\s+working\s+with\s+multiple\s+commercial\s+options",
    r"share\s+options\s+based\s+on\s+your\s+requirement",
    r"looking\s+for\s+something\s+specific\?.*share\s+your\s+property\s+requirements",
]


def _v174_distinct_areas(text_blob):
    vals = []
    for m in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|square\s*feet)", text_blob, re.I):
        v = _num(m.group(1))
        if v and 50 <= v <= 1000000:
            vals.append(int(round(v)))
    return sorted(set(vals))


def _v174_is_generic_source_post(row):
    blob = _space(f'{row.get("title", "")} {row.get("snippet", "")}')
    low = blob.lower()
    areas = _v174_distinct_areas(blob)
    # Multiple independent sizes in a social post normally means several properties.
    if len(areas) >= 2 and any(x in low for x in ["options", "showcasing", "different options", "among many"]):
        return True
    for pat in V174_GENERIC_SOURCE_PATTERNS:
        if re.search(pat, low, re.I | re.S):
            # Keep it only if it clearly describes one property with one area.
            if len(areas) != 1:
                return True
    return False


def _v174_collection_page(row):
    title = _space(row.get("title"))
    snippet = _space(row.get("snippet"))
    low = f"{title} {snippet}".lower()
    if _v174_is_generic_source_post(row):
        return True
    if re.search(r"\b\d+\+?\s+(?:shops?|showrooms?|commercial\s+propert(?:y|ies)|properties|offices?)\b", title, re.I):
        return True
    if re.search(r"\b\d+\s+results\b|\bresults\s*\|", snippet, re.I):
        return True
    if any(x in low for x in ["1 to 9 out of 9 properties", "sort by:", "property archives"]):
        return True
    if len(list(V174_LISTING_ANCHOR_RE.finditer(snippet))) >= 2:
        return True
    return False


def _v174_clean_segment_title(segment):
    m = V174_LISTING_ANCHOR_RE.search(segment)
    start = m.start() if m else 0
    text_seg = _space(segment[start:])
    # Stop before structured fields to keep a short, property-specific title.
    pieces = re.split(
        r"\s+(?=(?:₹|rs\.?|carpet\s+area|super\s+area|built[- ]?up\s+area|"
        r"floor\s+|highlights\s*:|availability\s*:|property\s+age|furnishing\s+status))",
        text_seg,
        maxsplit=1,
        flags=re.I,
    )
    title = _space(pieces[0])[:170]
    return title or "Property listing"


def _v174_segment_is_self_contained(segment):
    area = _extract_area(segment)
    floor = _extract_structured_floor(segment)
    rent_value, _ = _rent_value_and_display(segment)
    phone = PHONE_RE.search(segment)
    prop_anchor = V174_LISTING_ANCHOR_RE.search(segment)
    location_clue = bool(re.search(
        r"\b(?:greater\s+kailash|gk\s*[- ]?[12]|south\s+extension|defence\s+colony|"
        r"lajpat\s+nagar|green\s+park|hauz\s+khas|saket|nehru\s+place|jasola|okhla|"
        r"goa|siolim|assagao|anjuna|vagator|morjim|gurgaon|gurugram|noida)\b",
        segment,
        re.I,
    ))
    concrete = sum(bool(v) for v in [area, floor, rent_value, phone, location_clue])
    # Area + one other concrete field is the minimum safe property record.
    return bool(prop_anchor and area and concrete >= 2)


def _split_portal_result(row):
    """V17.4: return zero or more rows, each representing exactly one property."""
    x = dict(row)
    snippet = _space(row.get("snippet"))

    # Generic broker/source posts are leads, not property inventory.
    if _v174_is_generic_source_post(row):
        return []

    if not _v174_collection_page(row):
        # Even a non-collection result with several independent sizes is ambiguous.
        if len(_v174_distinct_areas(snippet)) >= 3:
            return []
        x["candidate_kind"] = "INDIVIDUAL"
        return [x] if _v174_segment_is_self_contained(_space(f'{row.get("title","")} {snippet}')) else []

    matches = list(V174_LISTING_ANCHOR_RE.finditer(snippet))
    if not matches:
        # Never convert an unsplittable archive/category page into a property.
        return []

    out = []
    for i, match in enumerate(matches[:40]):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(snippet)
        segment = _space(snippet[start:end])
        if len(segment) < 60 or not _v174_segment_is_self_contained(segment):
            continue

        # Guard against a segment accidentally containing several properties.
        nested = list(V174_LISTING_ANCHOR_RE.finditer(segment))
        if len(nested) > 1:
            continue

        out.append({
            "title": _v174_clean_segment_title(segment),
            "url": row.get("url") or "",
            "snippet": segment[:2200],
            "source_provider": row.get("source_provider") or "Web",
            "candidate_kind": "EXTRACTED_LISTING",
            "parent_collection_title": row.get("title") or "",
        })
    return out


def extract_candidate(row, parsed):
    # All fields are extracted only from this single property segment.
    snippet = _space(row.get("snippet"))
    blob = _space(f'{row.get("title", "")} {snippet}')
    phone_match = PHONE_RE.search(blob)
    rent_value, rent_display = _rent_value_and_display(blob)
    area = _extract_area(blob)
    floor = _extract_structured_floor(blob)
    location = _extract_structured_location(blob, parsed)
    ptype = _extract_structured_property_type(blob, parsed.get("property_type"))

    # If a segment still has several materially different sizes, treat it as ambiguous.
    areas = _v174_distinct_areas(blob)
    ambiguous = len(areas) >= 3

    candidate = {
        "title": _v174_clean_segment_title(blob),
        "source_provider": row.get("source_provider") or "Web",
        "source_platform": _platform(row.get("url")),
        "source_url": row.get("url") or "",
        "snippet": snippet[:320] + ("…" if len(snippet) > 320 else ""),
        "full_snippet": snippet[:2200],
        "property_type": ptype,
        "transaction_type": parsed.get("transaction_type"),
        "city": parsed.get("city"),
        "location": location,
        "available_area_sqft": area,
        "floor": floor,
        "contact_phone": _phone(phone_match.group(0)) if phone_match else None,
        "verification_status": "UNVERIFIED",
        "candidate_kind": row.get("candidate_kind") or "INDIVIDUAL",
        "parent_collection_title": row.get("parent_collection_title"),
        "rent_amount": rent_value,
        "rent_display": rent_display,
        "rate_per_sqft": _extract_rate_per_sqft(blob),
        "availability": _extract_availability(blob),
        "furnishing": _extract_furnishing(blob),
        "posted_date": _extract_structured_posted(blob),
        "ambiguous_multi_property": ambiguous,
    }
    concrete = sum(bool(candidate.get(k)) for k in [
        "available_area_sqft", "floor", "rent_amount", "contact_phone", "posted_date", "location"
    ])
    candidate["listing_quality"] = "HIGH" if concrete >= 5 else "MEDIUM" if concrete >= 3 else "LOW"
    return candidate


def score_candidate(c, p):
    score = 0
    reasons, mismatches, missing = [], [], []
    hay = f'{c.get("title", "")} {c.get("full_snippet", c.get("snippet", ""))}'.lower()

    if c.get("ambiguous_multi_property"):
        c.update({
            "match_score": 0, "match_band": "SUPPRESSED", "match_reasons": [],
            "hard_mismatches": ["Source contains multiple properties in one segment"],
            "missing_fields": [], "evidence_quality": "LOW"
        })
        return c

    req_loc = (p.get("location") or "").lower()
    city = (p.get("city") or "").lower()
    extracted_loc = (c.get("location") or "").lower()
    expanded = [x.lower() for x in LOCATION_EXPANSIONS.get(req_loc, [])]
    if req_loc and req_loc in hay:
        score += 18; reasons.append("Location matched")
    elif extracted_loc and extracted_loc in expanded:
        score += 18; reasons.append("Micro-market matched")
    elif city and city in hay:
        score += 8; reasons.append("City matched")
    else:
        missing.append("Location")

    req_type = p.get("property_type")
    if req_type:
        if c.get("property_type") == req_type:
            score += 14; reasons.append("Property type matched")
        elif c.get("property_type"):
            mismatches.append(f"Type {c.get('property_type')} vs required {req_type}")
        else:
            missing.append("Property type")

    tx = p.get("transaction_type")
    if tx == "Lease":
        if any(x in hay for x in ["for rent", "for lease", "available for rent", "lease"]):
            score += 8; reasons.append("Lease matched")
        elif "for sale" in hay:
            mismatches.append("Sale listing, not lease")
        else:
            missing.append("Lease evidence")
    elif tx == "Sale":
        if any(x in hay for x in ["for sale", "sale"]):
            score += 8; reasons.append("Sale matched")
        elif any(x in hay for x in ["for rent", "for lease"]):
            mismatches.append("Lease listing, not sale")

    req_min, req_max = p.get("minimum_area_sqft"), p.get("maximum_area_sqft")
    area = c.get("available_area_sqft")
    area_exact = False
    if req_min and req_max:
        if area is None:
            missing.append("Area")
        elif req_min <= area <= req_max:
            score += 34; reasons.append("Area within requirement"); area_exact = True
        else:
            mismatches.append(f"Area {area:,.0f} sqft outside {req_min:,.0f}-{req_max:,.0f} sqft")
    elif area:
        score += 10; reasons.append("Area extracted")

    req_floor = p.get("floor")
    floor_exact = False
    if req_floor:
        if c.get("floor") == req_floor:
            score += 18; reasons.append("Floor matched"); floor_exact = True
        elif c.get("floor"):
            mismatches.append(f"Floor {c.get('floor')} vs required {req_floor}")
        else:
            missing.append("Floor")

    if p.get("title_preference"):
        if "clear title" in hay:
            score += 5; reasons.append("Clear title mentioned")
        else:
            missing.append("Clear title")

    if c.get("contact_phone"):
        score += 3; reasons.append("Contact found")
    if c.get("rent_amount"):
        score += 3; reasons.append("Rent extracted")
    if c.get("posted_date"):
        score += 2; reasons.append("Posted date found")

    # HARD FILTERS: explicit area/type/transaction/floor mismatch is not a result.
    if mismatches:
        score = min(score, 44)

    score = max(0, min(int(round(score)), 100))
    if not mismatches and score >= 88 and (not req_min or area_exact) and (not req_floor or floor_exact):
        band = "BEST MATCH"
    elif not mismatches and score >= 65:
        band = "POSSIBLE MATCH"
    else:
        band = "NEEDS REVIEW"

    evidence_count = sum(bool(c.get(k)) for k in [
        "location", "available_area_sqft", "floor", "rent_amount", "contact_phone", "posted_date"
    ])
    c.update({
        "match_score": score,
        "match_band": band,
        "match_reasons": reasons,
        "hard_mismatches": mismatches,
        "missing_fields": missing,
        "evidence_quality": "HIGH" if evidence_count >= 5 else "MEDIUM" if evidence_count >= 3 else "LOW",
    })
    return c


# Cleaner fixed-field UI. Raw evidence is collapsed and never overlaps cards.
def _page():
    return r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Find Property by Demand</title>
<style>
*{box-sizing:border-box}body{font-family:Arial,sans-serif;background:#f5f7fb;color:#172033;margin:0}.wrap{max-width:1400px;margin:auto;padding:26px}.panel,.result{background:#fff;border:1px solid #dfe4ec;border-radius:14px;padding:16px}.panel{margin-bottom:14px}textarea{width:100%;min-height:100px;border:1px solid #dfe4ec;border-radius:10px;padding:13px;font-size:16px}button{border:0;border-radius:9px;padding:9px 13px;font-weight:700;cursor:pointer;background:#172033;color:white;margin:2px}.light{background:#edf1f5;color:#172033}.green{background:#087443}.red{background:#b42318}.muted{color:#687386}.summary{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}.metric{background:#f8fafc;border-radius:9px;padding:9px}.metric small{display:block;color:#687386;text-transform:uppercase;font-size:10px}.section{display:flex;justify-content:space-between;align-items:center;margin:22px 0 9px}.results{display:grid;grid-template-columns:repeat(2,1fr);gap:13px}.result{overflow:hidden}.best{border-left:5px solid #087443}.possible{border-left:5px solid #d49b00}.review{border-left:5px solid #98a2b3}.badge{display:inline-block;background:#edf1f5;padding:5px 8px;border-radius:999px;font-size:12px;font-weight:700;margin:2px}.scorebest{background:#e8f7ee;color:#087443}.scorepossible{background:#fff3d6;color:#8a5a00}.scorereview{background:#f0f1f3;color:#555}.fields{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:12px 0}.field{background:#f8fafc;border-radius:8px;padding:9px;min-height:58px}.field small{display:block;color:#687386;font-size:10px;text-transform:uppercase;margin-bottom:4px}.warning{background:#fff0ef;color:#b42318;padding:8px;border-radius:8px;font-size:13px}.why,.missing{font-size:13px;color:#566176;margin:7px 0}details{margin-top:9px;background:#fafbfc;border-radius:8px;padding:8px;font-size:12px;color:#5d6675}details summary{cursor:pointer;font-weight:700}.actions{display:flex;flex-wrap:wrap;gap:4px;margin-top:9px}a{text-decoration:none}@media(max-width:900px){.results{grid-template-columns:1fr}.fields{grid-template-columns:repeat(2,1fr)}.summary{grid-template-columns:repeat(2,1fr)}}
</style></head><body><div class="wrap">
<div style="display:flex;justify-content:space-between;align-items:center"><div><h1 style="margin-bottom:4px">🔍 Find Property by Demand</h1><div class="muted">Refined shortlist · one card = one property · collection pages suppressed</div></div><a href="/final-dashboard-v3">← Dashboard</a></div>
<div class="panel"><textarea id="q">Require retail space on lease in South Delhi, 4000 sqft, ground floor.</textarea><div style="margin-top:10px"><label><input id="deep" type="checkbox"> Deep Search</label><button onclick="runSearch()">Find Properties</button><span id="status" class="muted"></span></div></div>
<div id="summary"></div><div id="best"></div><div id="possible"></div><div id="review"></div>
<script>
const e=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]));
function f(l,v){return `<div class="field"><small>${e(l)}</small><b>${e(v||'Not found')}</b></div>`}
function card(c){const cls=c.match_band==='BEST MATCH'?'best':c.match_band==='POSSIBLE MATCH'?'possible':'review';const sc=cls==='best'?'scorebest':cls==='possible'?'scorepossible':'scorereview';return `<div class="result ${cls}"><div><span class="badge ${sc}">${e(c.match_score)}% · ${e(c.match_band)}</span><span class="badge">${e(c.source_platform)}</span><span class="badge">${e(c.evidence_quality)} evidence</span></div><h3>${e(c.title)}</h3><div class="fields">${f('Property Type',c.property_type)}${f('Transaction',c.transaction_type)}${f('Location',c.location)}${f('Area',c.available_area_sqft?Math.round(c.available_area_sqft).toLocaleString()+' sqft':null)}${f('Floor',c.floor)}${f('Rent',c.rent_display)}${f('Rate / sqft',c.rate_per_sqft?'₹'+Number(c.rate_per_sqft).toLocaleString('en-IN'):null)}${f('Availability',c.availability)}${f('Furnishing',c.furnishing)}${f('Contact',c.contact_phone)}${f('Posted',c.posted_date)}${f('Verification',c.verification_status||'UNVERIFIED')}</div>${(c.hard_mismatches||[]).length?`<div class="warning"><b>Excluded mismatch:</b> ${e(c.hard_mismatches.join(' · '))}</div>`:''}${(c.missing_fields||[]).length?`<div class="missing"><b>Missing:</b> ${e(c.missing_fields.join(', '))}</div>`:''}<div class="why"><b>Matched:</b> ${e((c.match_reasons||[]).join(' · ')||'Limited evidence')}</div><details><summary>Source evidence</summary>${e(c.full_snippet||c.snippet||'')}</details><div class="actions">${c.source_url?`<a href="${e(c.source_url)}" target="_blank"><button class="light">View Source ↗</button></a>`:''}<button class="light" onclick="act('${e(c.candidate_id)}','select')">Select</button><button class="red" onclick="act('${e(c.candidate_id)}','reject')">Reject</button><button class="green" onclick="verify('${e(c.candidate_id)}')">Mark Verified</button><button onclick="act('${e(c.candidate_id)}','add-to-database')">Add to Database</button></div></div>`}
function section(id,title,rows){const el=document.getElementById(id);if(!rows.length){el.innerHTML='';return}el.innerHTML=`<div class="section"><h2>${title}</h2><span class="badge">${rows.length}</span></div><div class="results">${rows.map(card).join('')}</div>`}
async function runSearch(){const status=document.getElementById('status');status.textContent=' Searching and extracting individual properties...';['summary','best','possible','review'].forEach(x=>document.getElementById(x).innerHTML='');const r=await fetch('/api/discovery/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({requirement:document.getElementById('q').value,deep_search:document.getElementById('deep').checked})});const d=await r.json();if(!r.ok){status.textContent=' '+(d.detail||'Search failed');return}const p=d.parsed_requirement||{};const best=d.candidates.filter(x=>x.match_band==='BEST MATCH');const possible=d.candidates.filter(x=>x.match_band==='POSSIBLE MATCH');const review=d.candidates.filter(x=>x.match_band==='NEEDS REVIEW');status.textContent=` ${d.candidates.length} refined property records`;document.getElementById('summary').innerHTML=`<div class="panel"><div class="summary"><div class="metric"><small>Type</small><b>${e(p.property_type||'Any')}</b></div><div class="metric"><small>Transaction</small><b>${e(p.transaction_type||'Any')}</b></div><div class="metric"><small>Location</small><b>${e(p.location||p.city||'Any')}</b></div><div class="metric"><small>Area</small><b>${p.minimum_area_sqft?Math.round(p.minimum_area_sqft).toLocaleString()+'–'+Math.round(p.maximum_area_sqft).toLocaleString()+' sqft':'Any'}</b></div><div class="metric"><small>Floor</small><b>${e(p.floor||'Any')}</b></div><div class="metric"><small>Results</small><b>${d.candidates.length}</b></div></div></div>`;section('best','✅ Best Matches',best);section('possible','🟡 Possible Matches',possible);section('review','⚪ Needs More Evidence',review)}
async function act(id,a){const r=await fetch(`/api/discovery/candidates/${encodeURIComponent(id)}/${a}`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const d=await r.json();alert(d.message||d.detail||JSON.stringify(d))}
async function verify(id){const by=prompt('Verified by:','');if(by===null)return;const notes=prompt('Availability / verification notes:','');const r=await fetch(`/api/discovery/candidates/${encodeURIComponent(id)}/verify`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({verified_by:by,notes:notes||''})});const d=await r.json();alert(d.message||d.detail||JSON.stringify(d))}
</script></div></body></html>'''
# === END V17.4 OVERRIDES ===

# === V17.4.1 PRECISION TUNING ===
def _v174_rent_value_and_display(blob):
    normalized = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", blob)
    # Prefer a monthly/rent-context amount, then fall back to the first headline amount.
    patterns = [
        r"(?:rent(?:al)?(?:\s+expectation)?(?:\s+is|\s*:)?\s*)(?:₹|rs\.?)?\s*([\d,.]+)\s*(lac|lakh|l|crore|cr|thousand|k)?",
        r"(?:₹|rs\.?)\s*([\d,.]+)\s*(lac|lakh|l|crore|cr|thousand|k)\b",
    ]
    for pat in patterns:
        m = re.search(pat, normalized, re.I)
        if not m:
            continue
        value = _num(m.group(1))
        if value is None:
            continue
        unit = (m.group(2) or "").lower()
        if unit in {"lac", "lakh", "l"}: value *= 100000
        elif unit in {"crore", "cr"}: value *= 10000000
        elif unit in {"thousand", "k"}: value *= 1000
        # Ignore tiny numbers that are clearly rates rather than monthly rent.
        if value < 10000:
            continue
        return round(value, 2), f"₹{value:,.0f}/month"
    return None, None


def extract_candidate(row, parsed):
    snippet = _space(row.get("snippet"))
    blob = _space(f'{row.get("title", "")} {snippet}')
    phone_match = PHONE_RE.search(blob)
    rent_value, rent_display = _v174_rent_value_and_display(blob)
    area = _extract_area(blob)
    floor = _extract_structured_floor(blob)
    location = _extract_structured_location(blob, parsed)
    ptype = _extract_structured_property_type(blob, parsed.get("property_type"))
    areas = _v174_distinct_areas(blob)
    ambiguous = len(areas) >= 3

    if row.get("candidate_kind") == "EXTRACTED_LISTING":
        title = _space(row.get("title"))[:170]
    else:
        title = _v174_clean_segment_title(snippet or row.get("title", ""))

    candidate = {
        "title": title or "Property listing",
        "source_provider": row.get("source_provider") or "Web",
        "source_platform": _platform(row.get("url")),
        "source_url": row.get("url") or "",
        "snippet": snippet[:320] + ("…" if len(snippet) > 320 else ""),
        "full_snippet": snippet[:2200],
        "property_type": ptype,
        "transaction_type": parsed.get("transaction_type"),
        "city": parsed.get("city"),
        "location": location,
        "available_area_sqft": area,
        "floor": floor,
        "contact_phone": _phone(phone_match.group(0)) if phone_match else None,
        "verification_status": "UNVERIFIED",
        "candidate_kind": row.get("candidate_kind") or "INDIVIDUAL",
        "parent_collection_title": row.get("parent_collection_title"),
        "rent_amount": rent_value,
        "rent_display": rent_display,
        "rate_per_sqft": _extract_rate_per_sqft(blob),
        "availability": _extract_availability(blob),
        "furnishing": _extract_furnishing(blob),
        "posted_date": _extract_structured_posted(blob),
        "ambiguous_multi_property": ambiguous,
    }
    concrete = sum(bool(candidate.get(k)) for k in [
        "available_area_sqft", "floor", "rent_amount", "contact_phone", "posted_date", "location"
    ])
    candidate["listing_quality"] = "HIGH" if concrete >= 5 else "MEDIUM" if concrete >= 3 else "LOW"
    return candidate

# Exact explicit-demand matches should be surfaced as Best even when contact is absent.
_v174_score_candidate_base = score_candidate
def score_candidate(c, p):
    c = _v174_score_candidate_base(c, p)
    if c.get("hard_mismatches"):
        return c
    req_min, req_max = p.get("minimum_area_sqft"), p.get("maximum_area_sqft")
    area = c.get("available_area_sqft")
    area_ok = not (req_min and req_max) or (area is not None and req_min <= area <= req_max)
    floor_ok = not p.get("floor") or c.get("floor") == p.get("floor")
    type_ok = not p.get("property_type") or c.get("property_type") == p.get("property_type")
    tx_ok = not p.get("transaction_type") or any(x in (c.get("full_snippet") or "").lower() for x in (["rent","lease"] if p.get("transaction_type")=="Lease" else ["sale"]))
    if area_ok and floor_ok and type_ok and tx_ok and c.get("match_score", 0) >= 80:
        c["match_band"] = "BEST MATCH"
    return c
# === END V17.4.1 ===
