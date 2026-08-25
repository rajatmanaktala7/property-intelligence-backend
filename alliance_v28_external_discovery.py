
import os
import re
import json
import urllib.parse
import urllib.request
from sqlalchemy import text
from fastapi import Request
from fastapi.responses import HTMLResponse

MODULE_VERSION = "2.8.1-TIMEOUT-SAFE-EXTERNAL-DISCOVERY"

LANGSEARCH_URL = "https://api.langsearch.com/v1/web-search"
JINA_SEARCH_URL = "https://s.jina.ai/"

def _norm(v):
    return re.sub(r"\s+", " ", str(v or "").strip().lower())

def _tokens(v):
    return {x for x in re.split(r"[^a-z0-9]+", _norm(v)) if len(x) >= 2}

def _ensure_schema(engine):
    with engine.begin() as c:
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_v28_external_discovery(
          discovery_id BIGSERIAL PRIMARY KEY,
          action_id BIGINT NOT NULL,
          requirement_code TEXT NOT NULL,
          provider TEXT NOT NULL,
          search_query TEXT NOT NULL,
          source_url TEXT NOT NULL,
          title TEXT,
          snippet TEXT,
          published_at TEXT,
          evidence_score NUMERIC(6,2) DEFAULT 0,
          evidence_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
          review_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
          source_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
          discovered_at TIMESTAMPTZ DEFAULT NOW(),
          updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """))
        c.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_v28_action_url
        ON ai_v28_external_discovery(action_id,source_url)
        """))

def _http_json(url, method="GET", headers=None, body=None, timeout=4):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except Exception:
            return {"raw_text": raw}

def _langsearch(query, count=8):
    key = os.getenv("LANGSEARCH_API_KEY", "").strip()
    if not key:
        return {"provider":"LANGSEARCH","status":"DISABLED_NO_KEY","results":[]}

    try:
        data = _http_json(
            LANGSEARCH_URL,
            method="POST",
            headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},
            body={"query":query,"freshness":"noLimit","summary":True,"count":max(1,min(int(count),8))},
            timeout=4,
        )
        values = data.get("data",{}).get("webPages",{}).get("value",[])
        results = [{
            "title":x.get("name"),
            "url":x.get("url"),
            "snippet":x.get("summary") or x.get("snippet"),
            "published_at":x.get("datePublished"),
            "raw":x,
        } for x in values if x.get("url")]
        return {"provider":"LANGSEARCH","status":"OK","results":results}
    except Exception as exc:
        return {"provider":"LANGSEARCH","status":"ERROR","error":str(exc),"results":[]}

def _jina(query, count=8):
    key = os.getenv("JINA_API_KEY", "").strip()
    if not key:
        return {"provider":"JINA","status":"DISABLED_NO_KEY","results":[]}

    try:
        url = JINA_SEARCH_URL + "?" + urllib.parse.urlencode({"q":query})
        data = _http_json(
            url,
            method="GET",
            headers={"Authorization":f"Bearer {key}","Accept":"application/json"},
            timeout=4,
        )
        candidates=[]
        if isinstance(data,dict):
            if isinstance(data.get("data"),list):
                candidates=data["data"]
            elif isinstance(data.get("results"),list):
                candidates=data["results"]
            elif isinstance(data.get("data"),dict) and isinstance(data["data"].get("results"),list):
                candidates=data["data"]["results"]

        results=[]
        for x in candidates[:max(1,min(int(count),8))]:
            if not isinstance(x,dict):
                continue
            u=x.get("url") or x.get("link")
            if not u:
                continue
            results.append({
                "title":x.get("title") or x.get("name"),
                "url":u,
                "snippet":x.get("description") or x.get("snippet") or x.get("content"),
                "published_at":x.get("publishedDate") or x.get("date"),
                "raw":x,
            })
        return {"provider":"JINA","status":"OK","results":results}
    except Exception as exc:
        return {"provider":"JINA","status":"ERROR","error":str(exc),"results":[]}

def _choose_provider():
    # Prefer LangSearch if configured. Never chain providers in same request.
    if os.getenv("LANGSEARCH_API_KEY","").strip():
        return "LANGSEARCH"
    if os.getenv("JINA_API_KEY","").strip():
        return "JINA"
    return None

def build_query(req):
    loc=req.get("locations") or ""
    tx=req.get("transaction_type") or ""
    amin=req.get("minimum_area_sqft")
    amax=req.get("maximum_area_sqft")
    front=req.get("minimum_frontage_ft")
    suit=req.get("suitable_for") or ""

    area=""
    if amin is not None and amax is not None:
        area=f"{float(amin):g}-{float(amax):g} sqft"
    frontage=f"{float(front):g} ft frontage" if front is not None else ""

    return " ".join(x for x in [
        loc,tx,area,suit,frontage,
        "commercial property available restaurant space lease rent"
    ] if x)

def score_evidence(req,item):
    blob=_norm(" ".join([item.get("title") or "",item.get("snippet") or "",item.get("url") or ""]))
    score=0
    reasons=[]

    tx=_norm(req.get("transaction_type"))
    if tx=="lease" and any(x in blob for x in ["lease","rent","rental","to let"]):
        score+=20;reasons.append("Lease/rent signal")
    elif tx=="sale" and any(x in blob for x in ["sale","sell","buy","outright"]):
        score+=20;reasons.append("Sale signal")

    loc_tokens=[t for t in _tokens(req.get("locations")) if t not in {"place","road","delhi","ncr"}]
    if loc_tokens:
        hits=sum(1 for t in loc_tokens if t in blob)
        ratio=hits/len(loc_tokens)
        if ratio>=0.75:
            score+=30;reasons.append("Strong location evidence")
        elif ratio>=0.5:
            score+=22;reasons.append("Good location evidence")
        elif ratio>0:
            score+=10;reasons.append("Partial location evidence")

    try:
        amin=float(req.get("minimum_area_sqft")) if req.get("minimum_area_sqft") is not None else None
        amax=float(req.get("maximum_area_sqft")) if req.get("maximum_area_sqft") is not None else None
    except Exception:
        amin=amax=None

    nums=[]
    for m in re.finditer(r"\b(\d{3,5})\s*(?:sq\.?\s*ft|sqft|square\s*feet)\b",blob):
        try: nums.append(float(m.group(1)))
        except Exception: pass

    if nums and (amin is not None or amax is not None):
        low=amin if amin is not None else amax
        high=amax if amax is not None else amin
        if any(low*0.9<=n<=high*1.1 for n in nums):
            score+=25;reasons.append("Area evidence near requirement")
        else:
            reasons.append("Area evidence outside preferred range")
    elif amin is not None or amax is not None:
        reasons.append("Area not evidenced")

    suit_tokens=[t for t in _tokens(req.get("suitable_for")) if len(t)>=4]
    if suit_tokens and any(t in blob for t in suit_tokens):
        score+=15;reasons.append("Use/suitability evidence")
    elif any(x in blob for x in ["restaurant","cafe","food","retail","shop","commercial"]):
        score+=7;reasons.append("Broad commercial-use evidence")

    if str(item.get("url") or "").startswith("https://"):
        score+=5;reasons.append("HTTPS source")

    return max(0,min(100,round(score,2))),reasons

def run_external_discovery(engine,action_id,count=8):
    _ensure_schema(engine)

    with engine.connect() as c:
        action=c.execute(text("""
          SELECT *
          FROM ai_v26_team_action
          WHERE action_id=:id
          LIMIT 1
        """),{"id":int(action_id)}).mappings().first()

    if not action:
        return {"version":MODULE_VERSION,"status":"NOT_FOUND","detail":"V2.6 action not found"}

    if action["workflow_status"]!="INVENTORY_SEARCH":
        return {
            "version":MODULE_VERSION,
            "status":"BLOCKED",
            "detail":f"Action must be INVENTORY_SEARCH, found {action['workflow_status']}",
        }

    with engine.connect() as c:
        req=c.execute(text("""
          SELECT
            requirement_code,company_name,preferred_locations_raw AS locations,
            transaction_type,minimum_area_sqft,maximum_area_sqft,
            minimum_frontage_ft,required_floor,suitable_for
          FROM ai_requirement_index
          WHERE requirement_code=:code
          ORDER BY requirement_index_id DESC
          LIMIT 1
        """),{"code":action["requirement_code"]}).mappings().first()

    if not req:
        return {"version":MODULE_VERSION,"status":"NOT_FOUND","detail":"Requirement not indexed"}

    provider=_choose_provider()
    query=build_query(req)

    if not provider:
        return {
            "version":MODULE_VERSION,
            "status":"NO_PROVIDER_CONFIGURED",
            "requirement_code":action["requirement_code"],
            "query":query,
            "required_env":["LANGSEARCH_API_KEY","JINA_API_KEY"],
            "message":"Add at least one provider API key in Railway.",
        }

    result=_langsearch(query,count) if provider=="LANGSEARCH" else _jina(query,count)

    # Never fallback in same request. Return provider error immediately.
    if result["status"]!="OK":
        return {
            "version":MODULE_VERSION,
            "status":"PROVIDER_ERROR",
            "provider":provider,
            "provider_status":result["status"],
            "message":result.get("error") or result["status"],
            "query":query,
            "retry_safe":True,
            "fallback_not_run_in_same_request":True,
        }

    discoveries=[]
    seen=set()

    for item in result["results"]:
        url=str(item.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        score,reasons=score_evidence(req,item)
        x={**item,"evidence_score":score,"evidence_reasons":reasons}
        discoveries.append(x)

    discoveries.sort(key=lambda x:x["evidence_score"],reverse=True)

    for x in discoveries:
        with engine.begin() as c:
            c.execute(text("""
              INSERT INTO ai_v28_external_discovery(
                action_id,requirement_code,provider,search_query,source_url,title,snippet,
                published_at,evidence_score,evidence_reasons,review_status,source_payload,
                discovered_at,updated_at
              )
              VALUES(
                :action_id,:requirement_code,:provider,:search_query,:source_url,:title,:snippet,
                :published_at,:evidence_score,CAST(:reasons AS jsonb),'UNVERIFIED',
                CAST(:payload AS jsonb),NOW(),NOW()
              )
              ON CONFLICT(action_id,source_url) DO UPDATE SET
                provider=EXCLUDED.provider,
                search_query=EXCLUDED.search_query,
                title=EXCLUDED.title,
                snippet=EXCLUDED.snippet,
                published_at=EXCLUDED.published_at,
                evidence_score=EXCLUDED.evidence_score,
                evidence_reasons=EXCLUDED.evidence_reasons,
                source_payload=EXCLUDED.source_payload,
                updated_at=NOW()
            """),{
                "action_id":int(action_id),
                "requirement_code":action["requirement_code"],
                "provider":provider,
                "search_query":query,
                "source_url":x["url"],
                "title":x.get("title"),
                "snippet":(x.get("snippet") or "")[:12000],
                "published_at":x.get("published_at"),
                "evidence_score":x["evidence_score"],
                "reasons":json.dumps(x["evidence_reasons"]),
                "payload":json.dumps(x.get("raw") or {}),
            })

    review=[x for x in discoveries if x["evidence_score"]>=55]

    return {
        "version":MODULE_VERSION,
        "status":"OK",
        "action_id":int(action_id),
        "requirement_code":action["requirement_code"],
        "execution_mode":"SINGLE_PROVIDER_TIMEOUT_SAFE",
        "provider":provider,
        "provider_status":result["status"],
        "query":query,
        "http_timeout_seconds":4,
        "fallback_not_run_in_same_request":True,
        "unique_discoveries":len(discoveries),
        "verification_candidates":len(review),
        "discoveries":[{
            "provider":provider,
            "title":x.get("title"),
            "url":x["url"],
            "published_at":x.get("published_at"),
            "evidence_score":x["evidence_score"],
            "evidence_reasons":x["evidence_reasons"],
            "review_status":"UNVERIFIED",
        } for x in discoveries],
        "external_inventory_confirmed":False,
        "next_step":"VERIFY_EXTERNAL_DISCOVERIES" if review else "NO_EXTERNAL_CANDIDATE_YET",
    }

def register_v28_routes(core):
    app,engine=core.app,core.engine
    _ensure_schema(engine)

    @app.post("/api/v2/intelligence/v28/discover/{action_id}")
    def discover(action_id:int,req:Request,count:int=8):
        if hasattr(core,"need_login"):
            core.need_login(req)
        try:
            return run_external_discovery(engine,action_id,count)
        except Exception as exc:
            return {
                "version":MODULE_VERSION,
                "status":"ERROR",
                "message":str(exc),
            }

    @app.get("/api/v2/intelligence/v28/discoveries/{action_id}")
    def list_discoveries(action_id:int,req:Request,limit:int=100):
        if hasattr(core,"need_login"):
            core.need_login(req)
        limit=max(1,min(int(limit or 100),200))
        with engine.connect() as c:
            rows=c.execute(text("""
              SELECT discovery_id,action_id,requirement_code,provider,search_query,
                     source_url,title,snippet,published_at,evidence_score,
                     evidence_reasons,review_status,discovered_at,updated_at
              FROM ai_v28_external_discovery
              WHERE action_id=:id
              ORDER BY evidence_score DESC,updated_at DESC
              LIMIT :lim
            """),{"id":int(action_id),"lim":limit}).mappings().all()
        return {"version":MODULE_VERSION,"count":len(rows),"discoveries":[dict(x) for x in rows]}

    @app.get("/v2/external-inventory-discovery",response_class=HTMLResponse)
    def dashboard(req:Request):
        if hasattr(core,"need_login"):
            core.need_login(req)
        return HTMLResponse("""<!doctype html>
<html><head><meta charset="utf-8"><title>V2.8A External Discovery</title></head>
<body style="font-family:Arial;background:#f5f7fa">
<div style="max-width:1000px;margin:30px auto;background:white;padding:28px;border-radius:14px">
<h1>V2.8A Timeout-Safe External Discovery</h1>
<p>One provider per request. Hard HTTP timeout: 4 seconds.</p>
<p>No provider fallback chaining inside the same browser request.</p>
<p>All external results remain UNVERIFIED.</p>
</div></body></html>""")
    return app
