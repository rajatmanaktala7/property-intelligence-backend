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

DISCOVERY_VERSION = "17.0-PROPERTY-DISCOVERY"
SQM_TO_SQFT = 10.7639104167

LANGSEARCH_API_KEY = os.getenv("LANGSEARCH_API_KEY", "").strip()
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip()
JINA_API_KEY = os.getenv("JINA_API_KEY", "").strip()
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "").strip()
GOOGLE_CSE_API_KEY = os.getenv("GOOGLE_CSE_API_KEY", "").strip()
GOOGLE_CSE_CX = os.getenv("GOOGLE_CSE_CX", "").strip()

DISCOVERY_TIMEOUT = float(os.getenv("DISCOVERY_TIMEOUT", "18"))
DISCOVERY_MIN_SCORE = int(os.getenv("DISCOVERY_MIN_SCORE", "30"))
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

def _extract_area(blob):
    m = AREA_SQFT.search(blob)
    if m:
        return _num(m.group(1))
    m = AREA_SQM.search(blob)
    if m:
        v = _num(m.group(1))
        return round(v * SQM_TO_SQFT, 2) if v else None
    return None

def _platform(url):
    u = (url or "").lower()
    for needle, label in [
        ("linkedin.com", "LinkedIn"), ("instagram.com", "Instagram"),
        ("facebook.com", "Facebook"), ("99acres.com", "99acres"),
        ("magicbricks.com", "MagicBricks"), ("housing.com", "Housing"),
        ("makaan.com", "Makaan")
    ]:
        if needle in u:
            return label
    return "Public Web"

def extract_candidate(row, parsed):
    blob = _space(f'{row.get("title","")} {row.get("snippet","")}')
    pm = PHONE_RE.search(blob)
    phone = _phone(pm.group(0)) if pm else None
    floor = "Ground Floor" if "ground floor" in blob.lower() or "ground-floor" in blob.lower() else None
    return {
        "title": _space(row.get("title")) or "Property result",
        "source_provider": row.get("source_provider") or "Web",
        "source_platform": _platform(row.get("url")),
        "source_url": row.get("url") or "",
        "snippet": _space(row.get("snippet")),
        "property_type": parsed.get("property_type"),
        "transaction_type": parsed.get("transaction_type"),
        "city": parsed.get("city"),
        "location": parsed.get("location"),
        "available_area_sqft": _extract_area(blob),
        "floor": floor,
        "contact_phone": phone,
        "verification_status": "UNVERIFIED"
    }

def score_candidate(c, p):
    score, reasons = 0, []
    hay = f'{c.get("title","")} {c.get("snippet","")}'.lower()
    loc = (p.get("location") or "").lower()
    city = (p.get("city") or "").lower()

    if loc and loc in hay:
        score += 25; reasons.append("Primary location match")
    elif any(x.lower() in hay for x in LOCATION_EXPANSIONS.get(loc, [])):
        score += 20; reasons.append("Expanded micro-market match")
    elif city and city in hay:
        score += 14; reasons.append("City match")

    ptype = p.get("property_type")
    terms = {
        "Land": ["land", "plot", "parcel"],
        "Retail": ["retail", "showroom", "shop", "commercial space"],
        "Office": ["office"], "Warehouse": ["warehouse", "godown", "shed"],
        "Industrial": ["industrial", "factory"],
        "Hospitality": ["hotel", "guest house", "restaurant", "banquet"],
        "Villa": ["villa"], "Residential": ["apartment", "flat", "residential"],
        "Farmhouse": ["farmhouse", "farm house"]
    }.get(ptype, [ptype.lower()] if ptype else [])
    if terms and any(t in hay for t in terms):
        score += 15; reasons.append("Property type match")

    tx = p.get("transaction_type")
    if tx == "Lease" and any(x in hay for x in ["lease", "rent", "rental"]):
        score += 10; reasons.append("Lease/rent match")
    elif tx == "Sale" and any(x in hay for x in ["sale", "sell", "buy", "purchase"]):
        score += 10; reasons.append("Sale match")

    req_min, req_max, area = p.get("minimum_area_sqft"), p.get("maximum_area_sqft"), c.get("available_area_sqft")
    if req_min and req_max and area:
        if req_min <= area <= req_max:
            score += 20; reasons.append("Area within requirement")
        else:
            mid = (req_min + req_max) / 2
            if abs(area - mid) / max(mid, 1) <= .20:
                score += 10; reasons.append("Area close to requirement")

    if p.get("floor") and p["floor"].lower() in hay:
        score += 10; reasons.append("Floor match")
    if p.get("title_preference") and "clear title" in hay:
        score += 5; reasons.append("Clear title mentioned")
    if c.get("contact_phone"):
        score += 5; reasons.append("Contact found")
    if c.get("source_url"):
        score += 5; reasons.append("Source URL available")

    c["match_score"] = min(score, 100)
    c["match_reasons"] = reasons
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
body{font-family:Arial,sans-serif;background:#f5f7fb;color:#172033;margin:0}.wrap{max-width:1250px;margin:auto;padding:26px}
.card,.result{background:#fff;border:1px solid #dfe4ec;border-radius:15px;padding:17px;margin-bottom:14px}
textarea{width:100%;min-height:110px;padding:13px;font-size:16px;border:1px solid #dfe4ec;border-radius:10px;box-sizing:border-box}
button{padding:10px 14px;border:0;border-radius:9px;background:#172033;color:#fff;font-weight:700;cursor:pointer;margin:4px}
button.red{background:#b42318}button.green{background:#087443}button.light{background:#edf1f5;color:#172033}
.results{display:grid;grid-template-columns:1fr 1fr;gap:14px}.badge{display:inline-block;padding:5px 8px;border-radius:999px;background:#edf1f5;margin:2px;font-size:12px;font-weight:700}
.score{background:#e8f7ee;color:#087443}.muted{color:#687386}a{color:#1647c8;font-weight:700}@media(max-width:800px){.results{grid-template-columns:1fr}}
</style></head><body><div class="wrap">
<div style="display:flex;justify-content:space-between;gap:12px;align-items:center"><div><h1>🔍 Find Property by Demand</h1><div class="muted">Property Intelligence Agent · Internet results stay UNVERIFIED until your team checks them.</div></div><a href="/">← Dashboard</a></div>
<div class="card"><textarea id="q">Require retail space on lease in South Delhi, 4000 sqft, ground floor.</textarea>
<div style="margin-top:10px"><label><input id="deep" type="checkbox"> Deep Search</label><button onclick="runSearch()">Find Properties</button><span id="status" class="muted"></span></div></div>
<div id="results" class="results"></div>
<script>
const e=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]));
async function runSearch(){
 const s=document.getElementById('status');s.textContent=' Searching...';document.getElementById('results').innerHTML='';
 const r=await fetch('/api/discovery/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({requirement:document.getElementById('q').value,deep_search:document.getElementById('deep').checked})});
 const d=await r.json();if(!r.ok){s.textContent=' '+(d.detail||'Search failed');return} s.textContent=` ${d.candidates.length} candidates found`;
 for(const c of d.candidates){const x=document.createElement('div');x.className='result';x.innerHTML=`
 <div><span class="badge score">${e(c.match_score)}% MATCH</span><span class="badge">${e(c.source_platform)}</span><span class="badge">${e(c.workflow_status)}</span></div>
 <h3>${e(c.title)}</h3><div class="muted">${e(c.location||'Location not extracted')} · ${e(c.available_area_sqft?Math.round(c.available_area_sqft)+' sqft':'Area not extracted')} · ${e(c.floor||'Floor not extracted')}</div>
 <p>${e(c.snippet||'')}</p><p><b>Contact:</b> ${e(c.contact_phone||'Not found')}</p><p><b>Why matched:</b> ${e((c.match_reasons||[]).join(', '))}</p>
 <p>${c.source_url?`<a href="${e(c.source_url)}" target="_blank" rel="noopener">View Source ↗</a>`:''}</p>
 <button class="light" onclick="act('${e(c.candidate_id)}','select')">Select</button><button class="red" onclick="act('${e(c.candidate_id)}','reject')">Reject</button>
 <button class="green" onclick="verify('${e(c.candidate_id)}')">Mark Verified</button><button onclick="act('${e(c.candidate_id)}','add-to-database')">Add to Property Database</button>`;
 document.getElementById('results').appendChild(x)}
}
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
        for row in raw:
            c = score_candidate(extract_candidate(row, p), p)
            if c["match_score"] >= DISCOVERY_MIN_SCORE:
                candidates.append(c)
        candidates.sort(key=lambda x: x["match_score"], reverse=True)

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
