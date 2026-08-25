
import os
import re
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from sqlalchemy import text
from fastapi import Request
from fastapi.responses import HTMLResponse

MODULE_VERSION = "2.8.0-EXTERNAL-INVENTORY-DISCOVERY"

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
        c.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_ai_v28_action_score
        ON ai_v28_external_discovery(action_id,evidence_score DESC)
        """))

def _http_json(url, method="GET", headers=None, body=None, timeout=8):
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers=headers or {},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except Exception:
            return {"raw_text": raw}

def _langsearch(query, count=10):
    key = os.getenv("LANGSEARCH_API_KEY", "").strip()
    if not key:
        return {"provider": "LANGSEARCH", "status": "DISABLED_NO_KEY", "results": []}

    payload = {
        "query": query,
        "freshness": "noLimit",
        "summary": True,
        "count": max(1, min(int(count), 10)),
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    try:
        data = _http_json(
            LANGSEARCH_URL,
            method="POST",
            headers=headers,
            body=payload,
            timeout=8,
        )
        values = (
            data.get("data", {})
                .get("webPages", {})
                .get("value", [])
        )
        out = []
        for x in values:
            out.append({
                "title": x.get("name"),
                "url": x.get("url"),
                "snippet": x.get("summary") or x.get("snippet"),
                "published_at": x.get("datePublished"),
                "raw": x,
            })
        return {"provider": "LANGSEARCH", "status": "OK", "results": out}
    except Exception as exc:
        return {"provider": "LANGSEARCH", "status": "ERROR", "error": str(exc), "results": []}

def _jina(query, count=10):
    key = os.getenv("JINA_API_KEY", "").strip()
    if not key:
        return {"provider": "JINA", "status": "DISABLED_NO_KEY", "results": []}

    url = JINA_SEARCH_URL + "?" + urllib.parse.urlencode({"q": query})
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }

    try:
        data = _http_json(url, method="GET", headers=headers, timeout=8)

        # Jina response shapes have changed over time; accept common shapes.
        candidates = []
        if isinstance(data, dict):
            if isinstance(data.get("data"), list):
                candidates = data["data"]
            elif isinstance(data.get("results"), list):
                candidates = data["results"]
            elif isinstance(data.get("data"), dict) and isinstance(data["data"].get("results"), list):
                candidates = data["data"]["results"]

        out = []
        for x in candidates[:max(1, min(int(count), 10))]:
            if not isinstance(x, dict):
                continue
            out.append({
                "title": x.get("title") or x.get("name"),
                "url": x.get("url") or x.get("link"),
                "snippet": x.get("description") or x.get("snippet") or x.get("content"),
                "published_at": x.get("publishedDate") or x.get("date"),
                "raw": x,
            })
        return {"provider": "JINA", "status": "OK", "results": out}
    except Exception as exc:
        return {"provider": "JINA", "status": "ERROR", "error": str(exc), "results": []}

def provider_waterfall(query, count=10):
    attempts = []

    a = _langsearch(query, count)
    attempts.append({k:v for k,v in a.items() if k != "results"})
    if a["results"]:
        return a["results"], attempts, "LANGSEARCH"

    b = _jina(query, count)
    attempts.append({k:v for k,v in b.items() if k != "results"})
    if b["results"]:
        return b["results"], attempts, "JINA"

    return [], attempts, None

def build_queries(req):
    loc = req.get("locations") or ""
    tx = req.get("transaction_type") or ""
    amin = req.get("minimum_area_sqft")
    amax = req.get("maximum_area_sqft")
    front = req.get("minimum_frontage_ft")
    suit = req.get("suitable_for") or ""

    area = ""
    if amin is not None and amax is not None:
        area = f"{float(amin):g}-{float(amax):g} sqft"
    elif amin is not None:
        area = f"{float(amin):g}+ sqft"

    frontage = f"{float(front):g} ft frontage" if front is not None else ""

    base = " ".join(x for x in [loc, tx, area, suit, frontage] if x)
    return [
        f"{base} commercial property available",
        f"{base} restaurant space lease rent",
        f"{base} shop showroom property broker owner",
    ]

def score_evidence(req, item):
    text_blob = _norm(" ".join([
        item.get("title") or "",
        item.get("snippet") or "",
        item.get("url") or "",
    ]))

    score = 0
    reasons = []

    req_tx = _norm(req.get("transaction_type"))
    if req_tx == "lease" and any(x in text_blob for x in ["lease", "rent", "rental", "to let"]):
        score += 20
        reasons.append("Lease/rent signal")
    elif req_tx == "sale" and any(x in text_blob for x in ["sale", "sell", "buy", "outright"]):
        score += 20
        reasons.append("Sale signal")

    loc_tokens = [t for t in _tokens(req.get("locations")) if t not in {"place","road","delhi","ncr"}]
    loc_hits = sum(1 for t in loc_tokens if t in text_blob)
    if loc_tokens:
        loc_ratio = loc_hits / len(loc_tokens)
        if loc_ratio >= 0.75:
            score += 30
            reasons.append("Strong location evidence")
        elif loc_ratio >= 0.5:
            score += 22
            reasons.append("Good location evidence")
        elif loc_ratio > 0:
            score += 10
            reasons.append("Partial location evidence")

    try:
        amin = float(req.get("minimum_area_sqft")) if req.get("minimum_area_sqft") is not None else None
        amax = float(req.get("maximum_area_sqft")) if req.get("maximum_area_sqft") is not None else None
    except Exception:
        amin = amax = None

    nums = []
    for m in re.finditer(r"\b(\d{3,5})\s*(?:sq\.?\s*ft|sqft|square\s*feet)\b", text_blob):
        try:
            nums.append(float(m.group(1)))
        except Exception:
            pass

    if nums and (amin is not None or amax is not None):
        low = amin if amin is not None else amax
        high = amax if amax is not None else amin
        if any(low*0.9 <= n <= high*1.1 for n in nums):
            score += 25
            reasons.append("Area evidence near requirement")
        else:
            reasons.append("Area evidence outside preferred range")
    elif amin is None and amax is None:
        score += 10
    else:
        reasons.append("Area not evidenced")

    suit_tokens = [t for t in _tokens(req.get("suitable_for")) if len(t) >= 4]
    if suit_tokens and any(t in text_blob for t in suit_tokens):
        score += 15
        reasons.append("Use/suitability evidence")
    elif any(x in text_blob for x in ["restaurant", "cafe", "food", "retail", "shop", "commercial"]):
        score += 7
        reasons.append("Broad commercial-use evidence")

    url = item.get("url") or ""
    if url.startswith("https://"):
        score += 5
        reasons.append("HTTPS source")

    return max(0, min(100, round(score,2))), reasons

def run_external_discovery(engine, action_id, max_queries=2, count=10):
    _ensure_schema(engine)

    with engine.connect() as c:
        action = c.execute(text("""
          SELECT *
          FROM ai_v26_team_action
          WHERE action_id=:id
          LIMIT 1
        """), {"id": int(action_id)}).mappings().first()

    if not action:
        return {"version": MODULE_VERSION, "status": "NOT_FOUND", "detail": "V2.6 action not found"}

    if action["workflow_status"] != "INVENTORY_SEARCH":
        return {
            "version": MODULE_VERSION,
            "status": "BLOCKED",
            "detail": f"Action must be INVENTORY_SEARCH, found {action['workflow_status']}",
        }

    with engine.connect() as c:
        req = c.execute(text("""
          SELECT
            requirement_code,
            company_name,
            preferred_locations_raw AS locations,
            transaction_type,
            minimum_area_sqft,
            maximum_area_sqft,
            minimum_frontage_ft,
            required_floor,
            suitable_for
          FROM ai_requirement_index
          WHERE requirement_code=:code
          ORDER BY requirement_index_id DESC
          LIMIT 1
        """), {"code": action["requirement_code"]}).mappings().first()

    if not req:
        return {"version": MODULE_VERSION, "status": "NOT_FOUND", "detail": "Requirement not indexed"}

    max_queries = max(1, min(int(max_queries or 2), 3))
    count = max(1, min(int(count or 10), 10))
    queries = build_queries(req)[:max_queries]

    all_results = []
    provider_attempts = []

    for q in queries:
        results, attempts, chosen = provider_waterfall(q, count)
        provider_attempts.append({
            "query": q,
            "attempts": attempts,
            "chosen_provider": chosen,
        })
        for item in results:
            if not item.get("url"):
                continue
            score, reasons = score_evidence(req, item)
            row = {
                **item,
                "provider": chosen or "UNKNOWN",
                "query": q,
                "evidence_score": score,
                "evidence_reasons": reasons,
            }
            all_results.append(row)

    # Deduplicate by URL and retain the highest evidence score.
    dedup = {}
    for x in all_results:
        url = x["url"].strip()
        old = dedup.get(url)
        if old is None or x["evidence_score"] > old["evidence_score"]:
            dedup[url] = x

    discoveries = sorted(
        dedup.values(),
        key=lambda x: x["evidence_score"],
        reverse=True,
    )[:30]

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
            """), {
                "action_id": int(action_id),
                "requirement_code": action["requirement_code"],
                "provider": x["provider"],
                "search_query": x["query"],
                "source_url": x["url"],
                "title": x.get("title"),
                "snippet": (x.get("snippet") or "")[:12000],
                "published_at": x.get("published_at"),
                "evidence_score": x["evidence_score"],
                "reasons": json.dumps(x["evidence_reasons"]),
                "payload": json.dumps(x.get("raw") or {}),
            })

    review_candidates = [x for x in discoveries if x["evidence_score"] >= 55]

    # Update original V2.6 task only with status information.
    with engine.begin() as c:
        note = (
            f"V2.8 external discovery found {len(discoveries)} unique result(s); "
            f"{len(review_candidates)} require verification. "
            "All external discoveries remain UNVERIFIED."
        )
        c.execute(text("""
          UPDATE ai_v26_team_action
          SET notes=:note, updated_at=NOW()
          WHERE action_id=:id
        """), {"note": note, "id": int(action_id)})

    return {
        "version": MODULE_VERSION,
        "action_id": int(action_id),
        "requirement_code": action["requirement_code"],
        "execution_mode": "EXTERNAL_PROVIDER_WATERFALL",
        "core_matcher_untouched": True,
        "v27_untouched": True,
        "provider_attempts": provider_attempts,
        "queries_run": len(queries),
        "unique_discoveries": len(discoveries),
        "verification_candidates": len(review_candidates),
        "discoveries": [
            {
                "provider": x["provider"],
                "title": x.get("title"),
                "url": x["url"],
                "published_at": x.get("published_at"),
                "evidence_score": x["evidence_score"],
                "evidence_reasons": x["evidence_reasons"],
                "review_status": "UNVERIFIED",
            }
            for x in discoveries
        ],
        "next_step": "VERIFY_EXTERNAL_DISCOVERIES" if review_candidates else "NO_EXTERNAL_CANDIDATE_YET",
        "external_inventory_confirmed": False,
    }

def register_v28_routes(core):
    app, engine = core.app, core.engine
    _ensure_schema(engine)

    @app.post("/api/v2/intelligence/v28/discover/{action_id}")
    def discover(
        action_id: int,
        req: Request,
        max_queries: int = 2,
        count: int = 10,
    ):
        if hasattr(core, "need_login"):
            core.need_login(req)
        try:
            return run_external_discovery(
                engine,
                action_id,
                max_queries=max_queries,
                count=count,
            )
        except Exception as exc:
            return {
                "version": MODULE_VERSION,
                "status": "ERROR",
                "message": str(exc),
            }

    @app.get("/api/v2/intelligence/v28/discoveries/{action_id}")
    def list_discoveries(
        action_id: int,
        req: Request,
        limit: int = 100,
    ):
        if hasattr(core, "need_login"):
            core.need_login(req)

        limit = max(1, min(int(limit or 100), 200))
        with engine.connect() as c:
            rows = c.execute(text("""
              SELECT
                discovery_id,action_id,requirement_code,provider,search_query,
                source_url,title,snippet,published_at,evidence_score,
                evidence_reasons,review_status,discovered_at,updated_at
              FROM ai_v28_external_discovery
              WHERE action_id=:id
              ORDER BY evidence_score DESC,updated_at DESC
              LIMIT :lim
            """), {"id": int(action_id), "lim": limit}).mappings().all()

        return {
            "version": MODULE_VERSION,
            "count": len(rows),
            "discoveries": [dict(x) for x in rows],
        }

    @app.post("/api/v2/intelligence/v28/discoveries/{discovery_id}/review/{status}")
    def review_discovery(
        discovery_id: int,
        status: str,
        req: Request,
    ):
        if hasattr(core, "need_login"):
            core.need_login(req)

        status = status.upper()
        if status not in {"UNVERIFIED", "VERIFYING", "VERIFIED_CANDIDATE", "REJECTED"}:
            return {
                "version": MODULE_VERSION,
                "status": "ERROR",
                "message": "Invalid review status",
            }

        with engine.begin() as c:
            row = c.execute(text("""
              UPDATE ai_v28_external_discovery
              SET review_status=:status,updated_at=NOW()
              WHERE discovery_id=:id
              RETURNING discovery_id,action_id,requirement_code,source_url,evidence_score,review_status
            """), {"status": status, "id": int(discovery_id)}).mappings().first()

        return {
            "version": MODULE_VERSION,
            "discovery": dict(row) if row else None,
            "external_inventory_confirmed": False,
        }

    @app.get("/v2/external-inventory-discovery", response_class=HTMLResponse)
    def dashboard(req: Request):
        if hasattr(core, "need_login"):
            core.need_login(req)

        return HTMLResponse("""<!doctype html>
<html>
<head><meta charset="utf-8"><title>V2.8 External Inventory Discovery</title></head>
<body style="font-family:Arial;background:#f5f7fa">
<div style="max-width:1050px;margin:30px auto;background:white;padding:28px;border-radius:14px">
<h1>V2.8 External Inventory Discovery Bot</h1>
<p>Fallback after V2.7 finds no indexed inventory.</p>
<p>Provider waterfall: LangSearch → Jina.</p>
<p>Every result is stored with URL, provider, query and evidence score.</p>
<p><b>All web results remain UNVERIFIED until team review.</b></p>
</div>
</body>
</html>""")

    return app
