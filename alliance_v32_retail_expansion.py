
import os
import re
import json
import hashlib
import urllib.request
from sqlalchemy import text
from fastapi import Request, Body
from fastapi.responses import HTMLResponse

MODULE_VERSION = "3.2.0-RETAIL-EXPANSION-INTENT-BOT"

TARGET_ROLES = [
    "business development manager","business development officer",
    "business development head","business development",
    "expansion manager","expansion head",
    "leasing manager","leasing head",
    "real estate manager","real estate head",
    "property manager","property head",
    "store development manager","store development head",
    "network expansion","retail expansion",
]

TARGET_CATEGORIES = {
    "JEWELLERY","FASHION","FOOTWEAR","BEAUTY","ELECTRONICS","GROCERY",
    "RESTAURANT","CAFE","QSR","BANQUET","HOSPITALITY","FITNESS",
    "HOME_DECOR","SPECIALITY_RETAIL","OTHER"
}

HIGH_INTENT_TERMS = [
    "plans to open","targets","targeting","expand","expansion","new stores",
    "new outlets","new outlet","new store","store network","offline expansion",
    "retail footprint","launching stores","opening stores","enters","entry into",
    "lease","leasing","space requirement","looking for space","seeking space",
]

def _norm(v):
    return re.sub(r"\s+"," ",str(v or "").strip())

def _safe_json(v):
    return json.dumps(v or {},default=str,ensure_ascii=False)

def _hash(*parts):
    raw="|".join(_norm(x).lower() for x in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _schema_ready(engine):
    try:
        with engine.connect() as c:
            return bool(c.execute(text("""
              SELECT to_regclass('public.ai_retail_contact') IS NOT NULL
                 AND to_regclass('public.ai_retail_expansion_signal') IS NOT NULL
                 AND to_regclass('public.ai_retail_requirement_candidate') IS NOT NULL
                 AND to_regclass('public.ai_retail_bot_run') IS NOT NULL
            """)).scalar())
    except Exception:
        return False

def ensure_schema_safe(engine):
    if _schema_ready(engine):
        return {"status":"READY","created":False}
    with engine.begin() as c:
        c.execute(text("SET LOCAL lock_timeout='2s'"))
        c.execute(text("SET LOCAL statement_timeout='6s'"))
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_retail_contact(
          retail_contact_id BIGSERIAL PRIMARY KEY,
          canonical_key TEXT NOT NULL UNIQUE,
          person_name TEXT,
          designation TEXT,
          company_name TEXT NOT NULL,
          category TEXT NOT NULL DEFAULT 'OTHER',
          linkedin_profile_url TEXT,
          public_profile_evidence TEXT,
          contact_phone TEXT,
          email TEXT,
          website TEXT,
          city TEXT,
          source_url TEXT,
          source_provider TEXT,
          verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
          first_seen_at TIMESTAMPTZ DEFAULT NOW(),
          last_seen_at TIMESTAMPTZ DEFAULT NOW(),
          active BOOLEAN NOT NULL DEFAULT TRUE,
          created_at TIMESTAMPTZ DEFAULT NOW(),
          updated_at TIMESTAMPTZ DEFAULT NOW()
        )"""))
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_retail_expansion_signal(
          signal_id BIGSERIAL PRIMARY KEY,
          signal_key TEXT NOT NULL UNIQUE,
          company_name TEXT NOT NULL,
          category TEXT NOT NULL DEFAULT 'OTHER',
          headline TEXT,
          source_name TEXT,
          source_url TEXT NOT NULL,
          published_at TEXT,
          evidence_text TEXT,
          intent_score NUMERIC(6,2) DEFAULT 0,
          intent_status TEXT NOT NULL DEFAULT 'REVIEW',
          location_signal TEXT,
          outlet_target TEXT,
          raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
          first_seen_at TIMESTAMPTZ DEFAULT NOW(),
          last_seen_at TIMESTAMPTZ DEFAULT NOW()
        )"""))
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_retail_requirement_candidate(
          candidate_id BIGSERIAL PRIMARY KEY,
          signal_id BIGINT REFERENCES ai_retail_expansion_signal(signal_id),
          retail_contact_id BIGINT REFERENCES ai_retail_contact(retail_contact_id),
          company_name TEXT NOT NULL,
          category TEXT NOT NULL DEFAULT 'OTHER',
          preferred_locations TEXT,
          transaction_type TEXT,
          minimum_area_sqft NUMERIC(14,2),
          maximum_area_sqft NUMERIC(14,2),
          minimum_frontage_ft NUMERIC(14,2),
          suitable_for TEXT,
          evidence_text TEXT,
          source_url TEXT,
          qualification_status TEXT NOT NULL DEFAULT 'NEEDS_REVIEW',
          confidence NUMERIC(6,2) DEFAULT 0,
          created_at TIMESTAMPTZ DEFAULT NOW(),
          updated_at TIMESTAMPTZ DEFAULT NOW()
        )"""))
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_retail_bot_run(
          run_id BIGSERIAL PRIMARY KEY,
          run_type TEXT NOT NULL,
          query_text TEXT,
          provider TEXT,
          status TEXT NOT NULL DEFAULT 'RUNNING',
          fetched_count INT NOT NULL DEFAULT 0,
          saved_count INT NOT NULL DEFAULT 0,
          error_message TEXT,
          started_at TIMESTAMPTZ DEFAULT NOW(),
          completed_at TIMESTAMPTZ
        )"""))
    return {"status":"READY","created":True}

def _langsearch(query,count=8):
    key=os.getenv("LANGSEARCH_API_KEY","").strip()
    if not key:
        return {"status":"NO_KEY","provider":"LANGSEARCH","results":[]}
    body=json.dumps({
        "query":query,"freshness":"noLimit","summary":True,
        "count":max(1,min(int(count),8)),
    }).encode("utf-8")
    req=urllib.request.Request(
        "https://api.langsearch.com/v1/web-search",
        data=body,method="POST",
        headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},
    )
    try:
        with urllib.request.urlopen(req,timeout=4) as r:
            data=json.loads(r.read().decode("utf-8","replace"))
        vals=data.get("data",{}).get("webPages",{}).get("value",[])
        return {"status":"OK","provider":"LANGSEARCH","results":vals}
    except Exception as exc:
        return {"status":"ERROR","provider":"LANGSEARCH","message":str(exc),"results":[]}

def _guess_category(text_blob):
    t=_norm(text_blob).lower()
    rules=[
        ("JEWELLERY",["jewellery","jewelry","diamond","gold"]),
        ("RESTAURANT",["restaurant","dining"]),
        ("CAFE",["cafe","coffee"]),
        ("QSR",["qsr","quick service"]),
        ("BANQUET",["banquet"]),
        ("HOSPITALITY",["hotel","hospitality"]),
        ("FASHION",["fashion","apparel","clothing"]),
        ("FOOTWEAR",["footwear","shoes"]),
        ("BEAUTY",["beauty","cosmetics","skincare"]),
        ("ELECTRONICS",["electronics","consumer durables"]),
        ("GROCERY",["grocery","supermarket"]),
        ("FITNESS",["fitness","gym"]),
        ("HOME_DECOR",["home decor","furniture"]),
    ]
    for cat,terms in rules:
        if any(x in t for x in terms):
            return cat
    return "OTHER"

def _extract_company_from_title(title):
    t=_norm(title)
    for sep in [" | "," - "," – "," — "," at "]:
        if sep in t:
            parts=[x.strip() for x in t.split(sep) if x.strip()]
            if len(parts)>=2:
                return parts[-1][:180]
    return t[:180]

def _extract_linkedin_role(title,snippet):
    blob=_norm(f"{title} {snippet}").lower()
    role=next((r for r in TARGET_ROLES if r in blob),None)
    return role.upper().replace(" ","_") if role else "BUSINESS_DEVELOPMENT"

def discover_linkedin_people(engine,category="OTHER",location="India",count=8):
    ensure_schema_safe(engine)
    category=str(category or "OTHER").upper()
    query=(
        'site:linkedin.com/in ("business development manager" OR '
        '"business development officer" OR "business development head" OR '
        '"expansion manager" OR "leasing manager" OR "real estate manager" OR '
        '"store development manager") '
        f'{category.replace("_"," ")} {location}'
    )
    with engine.begin() as c:
        run_id=c.execute(text("""
          INSERT INTO ai_retail_bot_run(run_type,query_text,provider,status)
          VALUES('LINKEDIN_PUBLIC_PROFILE_DISCOVERY',:q,'LANGSEARCH','RUNNING')
          RETURNING run_id
        """),{"q":query}).scalar_one()

    result=_langsearch(query,count)
    saved=0
    if result["status"]=="OK":
        for item in result["results"]:
            url=str(item.get("url") or "")
            if "linkedin.com/in/" not in url.lower():
                continue
            title=_norm(item.get("name"))
            snippet=_norm(item.get("summary") or item.get("snippet"))
            company=_extract_company_from_title(title)
            role=_extract_linkedin_role(title,snippet)
            key=_hash(title,company,url)
            with engine.begin() as c:
                c.execute(text("""
                  INSERT INTO ai_retail_contact(
                    canonical_key,person_name,designation,company_name,category,
                    linkedin_profile_url,public_profile_evidence,source_url,
                    source_provider,verification_status,first_seen_at,last_seen_at,
                    created_at,updated_at
                  )
                  VALUES(
                    :k,:person_name,:designation,:company_name,:category,
                    :linkedin,:evidence,:source_url,'LANGSEARCH','UNVERIFIED',
                    NOW(),NOW(),NOW(),NOW()
                  )
                  ON CONFLICT(canonical_key) DO UPDATE SET
                    designation=COALESCE(EXCLUDED.designation,ai_retail_contact.designation),
                    public_profile_evidence=COALESCE(EXCLUDED.public_profile_evidence,ai_retail_contact.public_profile_evidence),
                    last_seen_at=NOW(),updated_at=NOW()
                """),{
                    "k":key,"person_name":title[:180],"designation":role,
                    "company_name":company,
                    "category":category if category in TARGET_CATEGORIES else "OTHER",
                    "linkedin":url,"evidence":snippet,"source_url":url,
                })
            saved+=1

    with engine.begin() as c:
        c.execute(text("""
          UPDATE ai_retail_bot_run
          SET status=:status,fetched_count=:fetched,saved_count=:saved,
              error_message=:error,completed_at=NOW()
          WHERE run_id=:run_id
        """),{
            "status":result["status"],"fetched":len(result["results"]),
            "saved":saved,"error":result.get("message"),"run_id":run_id,
        })
    return {
        "version":MODULE_VERSION,"run_id":int(run_id),
        "mode":"PUBLICLY_INDEXED_LINKEDIN_PROFILE_DISCOVERY",
        "direct_linkedin_scraping":False,
        "provider_status":result["status"],
        "profiles_found":saved,"saved_permanently":saved,
    }

def _intent_score(headline,evidence):
    blob=_norm(f"{headline} {evidence}").lower()
    score=20
    reasons=[]
    for term in HIGH_INTENT_TERMS:
        if term in blob:
            score+=12
            reasons.append(term)
    if any(x in blob for x in ["delhi","gurugram","gurgaon","noida","ghaziabad","ncr"]):
        score+=15
        reasons.append("Delhi NCR signal")
    if any(x in blob for x in ["stores","outlets","showrooms","restaurants"]):
        score+=10
    score=min(100,score)
    status="HIGH_INTENT" if score>=70 else "REVIEW" if score>=45 else "LOW_SIGNAL"
    return score,status,reasons

def discover_indiaretailing_signals(engine,category="ALL",count=8):
    ensure_schema_safe(engine)
    category=str(category or "ALL").upper()
    cat_query="" if category=="ALL" else category.replace("_"," ")
    query=f'site:indiaretailing.com {cat_query} expansion new stores new outlets retail footprint India'
    with engine.begin() as c:
        run_id=c.execute(text("""
          INSERT INTO ai_retail_bot_run(run_type,query_text,provider,status)
          VALUES('INDIARETAILING_EXPANSION_NEWS',:q,'LANGSEARCH','RUNNING')
          RETURNING run_id
        """),{"q":query}).scalar_one()

    result=_langsearch(query,count)
    saved=0
    high_intent=0
    if result["status"]=="OK":
        for item in result["results"]:
            url=str(item.get("url") or "")
            if "indiaretailing.com" not in url.lower():
                continue
            headline=_norm(item.get("name"))
            evidence=_norm(item.get("summary") or item.get("snippet"))
            company=_extract_company_from_title(headline)
            detected_category=_guess_category(f"{headline} {evidence}")
            score,status,reasons=_intent_score(headline,evidence)
            sigkey=_hash(url,headline)
            with engine.begin() as c:
                c.execute(text("""
                  INSERT INTO ai_retail_expansion_signal(
                    signal_key,company_name,category,headline,source_name,source_url,
                    published_at,evidence_text,intent_score,intent_status,
                    location_signal,outlet_target,raw_payload,first_seen_at,last_seen_at
                  )
                  VALUES(
                    :signal_key,:company_name,:category,:headline,'IndiaRetailing',
                    :source_url,:published_at,:evidence,:score,:status,
                    :location_signal,:outlet_target,CAST(:raw AS jsonb),NOW(),NOW()
                  )
                  ON CONFLICT(signal_key) DO UPDATE SET
                    evidence_text=EXCLUDED.evidence_text,
                    intent_score=EXCLUDED.intent_score,
                    intent_status=EXCLUDED.intent_status,
                    last_seen_at=NOW()
                """),{
                    "signal_key":sigkey,"company_name":company,
                    "category":detected_category,"headline":headline,
                    "source_url":url,
                    "published_at":item.get("datePublished") or item.get("published_at"),
                    "evidence":evidence,"score":score,"status":status,
                    "location_signal":"DELHI_NCR" if any(
                        x in f"{headline} {evidence}".lower()
                        for x in ["delhi","gurugram","gurgaon","noida","ghaziabad","ncr"]
                    ) else None,
                    "outlet_target":None,
                    "raw":_safe_json({"item":item,"reasons":reasons}),
                })
            saved+=1
            if status=="HIGH_INTENT":
                high_intent+=1

    with engine.begin() as c:
        c.execute(text("""
          UPDATE ai_retail_bot_run
          SET status=:status,fetched_count=:fetched,saved_count=:saved,
              error_message=:error,completed_at=NOW()
          WHERE run_id=:run_id
        """),{
            "status":result["status"],"fetched":len(result["results"]),
            "saved":saved,"error":result.get("message"),"run_id":run_id,
        })
    return {
        "version":MODULE_VERSION,"run_id":int(run_id),
        "source":"IndiaRetailing","provider_status":result["status"],
        "signals_found":saved,"high_intent_signals":high_intent,
        "saved_permanently":saved,
        "next_step":"QUALIFY_HIGH_INTENT_SIGNALS" if high_intent else "REVIEW_SIGNALS",
    }

def create_requirement_candidate(engine,signal_id,payload):
    ensure_schema_safe(engine)
    with engine.connect() as c:
        sig=c.execute(text("""
          SELECT * FROM ai_retail_expansion_signal
          WHERE signal_id=:id LIMIT 1
        """),{"id":int(signal_id)}).mappings().first()
    if not sig:
        return {"version":MODULE_VERSION,"status":"NOT_FOUND"}

    company=payload.get("company_name") or sig["company_name"]
    confidence=float(payload.get("confidence") or sig["intent_score"] or 0)

    with engine.begin() as c:
        row=c.execute(text("""
          INSERT INTO ai_retail_requirement_candidate(
            signal_id,retail_contact_id,company_name,category,preferred_locations,
            transaction_type,minimum_area_sqft,maximum_area_sqft,minimum_frontage_ft,
            suitable_for,evidence_text,source_url,qualification_status,confidence,
            created_at,updated_at
          )
          VALUES(
            :signal_id,:contact_id,:company,:category,:locations,:transaction,
            :amin,:amax,:frontage,:suitable_for,:evidence,:source_url,
            :status,:confidence,NOW(),NOW()
          )
          RETURNING candidate_id
        """),{
            "signal_id":int(signal_id),
            "contact_id":payload.get("retail_contact_id"),
            "company":company,
            "category":payload.get("category") or sig["category"],
            "locations":payload.get("preferred_locations"),
            "transaction":payload.get("transaction_type") or "LEASE",
            "amin":payload.get("minimum_area_sqft"),
            "amax":payload.get("maximum_area_sqft"),
            "frontage":payload.get("minimum_frontage_ft"),
            "suitable_for":payload.get("suitable_for"),
            "evidence":payload.get("evidence_text") or sig["evidence_text"],
            "source_url":sig["source_url"],
            "status":payload.get("qualification_status") or "NEEDS_REVIEW",
            "confidence":confidence,
        }).mappings().one()
    return {
        "version":MODULE_VERSION,"status":"OK",
        "candidate_id":int(row["candidate_id"]),
        "saved_permanently":True,
        "promoted_to_requirement_index":False,
        "next_step":"HUMAN_VERIFY_REQUIREMENT",
    }

def register_v32_retail_routes(core):
    app,engine=core.app,core.engine

    @app.get("/api/v3/retail/status")
    def status(req:Request):
        if hasattr(core,"need_login"): core.need_login(req)
        ready=_schema_ready(engine)
        if not ready:
            return {"version":MODULE_VERSION,"status":"OK","schema_ready":False,
                    "startup_schema_ddl":False,"next_step":"POST /api/v3/retail/setup"}
        with engine.connect() as c:
            contacts=int(c.execute(text("SELECT COUNT(*) FROM ai_retail_contact")).scalar() or 0)
            signals=int(c.execute(text("SELECT COUNT(*) FROM ai_retail_expansion_signal")).scalar() or 0)
            candidates=int(c.execute(text("SELECT COUNT(*) FROM ai_retail_requirement_candidate")).scalar() or 0)
        return {"version":MODULE_VERSION,"status":"OK","schema_ready":True,
                "contacts":contacts,"expansion_signals":signals,
                "requirement_candidates":candidates,
                "startup_schema_ddl":False,
                "direct_linkedin_scraping":False,"persistent_storage":True}

    @app.post("/api/v3/retail/setup")
    def setup(req:Request):
        if hasattr(core,"need_login"): core.need_login(req)
        try: return {"version":MODULE_VERSION,**ensure_schema_safe(engine)}
        except Exception as exc:
            return {"version":MODULE_VERSION,"status":"SCHEMA_BUSY","message":str(exc)}

    @app.post("/api/v3/retail/discover/linkedin")
    def linkedin_discovery(req:Request,category:str="OTHER",location:str="India",count:int=8):
        if hasattr(core,"need_login"): core.need_login(req)
        try: return discover_linkedin_people(engine,category,location,count)
        except Exception as exc:
            return {"version":MODULE_VERSION,"status":"ERROR","message":str(exc)}

    @app.post("/api/v3/retail/discover/news")
    def news_discovery(req:Request,category:str="ALL",count:int=8):
        if hasattr(core,"need_login"): core.need_login(req)
        try: return discover_indiaretailing_signals(engine,category,count)
        except Exception as exc:
            return {"version":MODULE_VERSION,"status":"ERROR","message":str(exc)}

    @app.post("/api/v3/retail/signals/{signal_id}/requirement")
    def requirement(signal_id:int,req:Request,payload:dict=Body(default={})):
        if hasattr(core,"need_login"): core.need_login(req)
        try: return create_requirement_candidate(engine,signal_id,payload or {})
        except Exception as exc:
            return {"version":MODULE_VERSION,"status":"ERROR","message":str(exc)}

    @app.get("/api/v3/retail/contacts")
    def contacts(req:Request,limit:int=100):
        if hasattr(core,"need_login"): core.need_login(req)
        if not _schema_ready(engine):
            return {"version":MODULE_VERSION,"count":0,"contacts":[]}
        with engine.connect() as c:
            rows=c.execute(text("""
              SELECT retail_contact_id,person_name,designation,company_name,category,
                     linkedin_profile_url,public_profile_evidence,city,
                     verification_status,first_seen_at,last_seen_at
              FROM ai_retail_contact
              WHERE active=TRUE
              ORDER BY last_seen_at DESC,retail_contact_id DESC
              LIMIT :lim
            """),{"lim":max(1,min(int(limit or 100),500))}).mappings().all()
        return {"version":MODULE_VERSION,"count":len(rows),"contacts":[dict(x) for x in rows]}

    @app.get("/api/v3/retail/signals")
    def signals(req:Request,status:str="",limit:int=100):
        if hasattr(core,"need_login"): core.need_login(req)
        if not _schema_ready(engine):
            return {"version":MODULE_VERSION,"count":0,"signals":[]}
        clauses=["TRUE"]
        params={"lim":max(1,min(int(limit or 100),500))}
        if status:
            clauses.append("intent_status=:status")
            params["status"]=status.upper()
        with engine.connect() as c:
            rows=c.execute(text(f"""
              SELECT signal_id,company_name,category,headline,source_name,source_url,
                     published_at,evidence_text,intent_score,intent_status,
                     location_signal,first_seen_at,last_seen_at
              FROM ai_retail_expansion_signal
              WHERE {" AND ".join(clauses)}
              ORDER BY intent_score DESC,signal_id DESC
              LIMIT :lim
            """),params).mappings().all()
        return {"version":MODULE_VERSION,"count":len(rows),"signals":[dict(x) for x in rows]}

    @app.get("/api/v3/retail/runs")
    def runs(req:Request,limit:int=50):
        if hasattr(core,"need_login"): core.need_login(req)
        if not _schema_ready(engine):
            return {"version":MODULE_VERSION,"count":0,"runs":[]}
        with engine.connect() as c:
            rows=c.execute(text("""
              SELECT * FROM ai_retail_bot_run
              ORDER BY run_id DESC LIMIT :lim
            """),{"lim":max(1,min(int(limit or 50),100))}).mappings().all()
        return {"version":MODULE_VERSION,"count":len(rows),"runs":[dict(x) for x in rows]}

    @app.get("/v3/retail-expansion-intelligence",response_class=HTMLResponse)
    def dashboard(req:Request):
        if hasattr(core,"need_login"): core.need_login(req)
        return HTMLResponse("""<!doctype html><html><body style="font-family:Arial">
        <h1>V3.2 Retail Expansion Intent Bot</h1>
        <p>Public LinkedIn profile discovery + IndiaRetailing expansion signals.</p>
        <p>No unauthorized LinkedIn scraping. Permanent storage enabled.</p>
        </body></html>""")

    return app
