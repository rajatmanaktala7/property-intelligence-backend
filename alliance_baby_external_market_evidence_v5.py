from __future__ import annotations
import hashlib,re,json
from urllib.parse import urlparse
from sqlalchemy import text
from fastapi import Request
from fastapi.responses import HTMLResponse,JSONResponse
from pydantic import BaseModel,Field

VERSION="5.0.0-ALLIANCE-BABY-AUTOMATIC-EXTERNAL-MARKET-EVIDENCE"
CACHE_HOURS=24
RAW_TABLE="ai_market_external_candidate_v5"
EVIDENCE_TABLE="ai_retail_hospitality_market_evidence_v5"

AGGREGATOR_DOMAINS={
 "zomato.com","swiggy.com","tripadvisor.in","tripadvisor.com","justdial.com","magicpin.in",
 "dineout.co.in","eazydiner.com","sulekha.com","nearbuy.com","yelp.com","yellowpages.in",
 "restaurant-guru.in","restaurantguru.com","wikipedia.org"
}
GENERIC_TITLE_RE=re.compile(r"\b(top|best|list of|restaurants? in|cafes? in|stores? in|shops? in|brands? in|near me|directory|guide|things to do)\b",re.I)

class FetchInput(BaseModel):
    requirement:str=Field(min_length=5)
    market:str=Field(min_length=2)
    force:bool=False

def _n(v): return re.sub(r"\s+"," ",str(v or "").strip()).upper()
def _entity_key(v): return re.sub(r"[^A-Z0-9&]+"," ",_n(v)).strip()
def _domain(url):
    try:return urlparse(str(url or "")).netloc.lower().removeprefix("www.")
    except Exception:return ""
def _hash(*parts): return hashlib.sha256("|".join(_n(x) for x in parts).encode()).hexdigest()
def _kind(req):
    import alliance_baby_use_case_market_intelligence_v4 as v4
    return v4.category(req)

def _keywords(kind):
    return {
      "FNB":("RESTAURANT","CAFE","CAFÉ","F&B","FNB","QSR","FOOD","BAR","LOUNGE","KITCHEN","DINING"),
      "FASHION":("FASHION","GARMENT","APPAREL","CLOTHING","CLOTHES","WEAR","BOUTIQUE","FOOTWEAR"),
      "JEWELLERY":("JEWELLERY","JEWELRY","GOLD","DIAMOND","JEWELLER"),
      "LUXURY":("LUXURY","PREMIUM","DESIGNER","BOUTIQUE"),
      "GROCERY":("GROCERY","SUPERMARKET","CONVENIENCE","DAILY NEED"),
      "OFFICE":("OFFICE","CORPORATE","WORKSPACE","BUSINESS CENTRE","BUSINESS CENTER"),
      "RETAIL":("RETAIL","SHOP","SHOWROOM","STORE","BRAND"),
    }.get(kind,("RETAIL","STORE","SHOP"))

def ensure_schema(engine):
    with engine.begin() as c:
        c.execute(text(f"""CREATE TABLE IF NOT EXISTS {RAW_TABLE}(
          id BIGSERIAL PRIMARY KEY,
          fingerprint VARCHAR(64) UNIQUE NOT NULL,
          requirement_text TEXT,
          market TEXT NOT NULL,
          category TEXT NOT NULL,
          entity_name TEXT,
          title TEXT,
          snippet TEXT,
          source_url TEXT,
          source_domain TEXT,
          source_provider TEXT,
          query_text TEXT,
          qualification_status VARCHAR(40) DEFAULT 'RAW',
          qualification_reason TEXT,
          fetched_at TIMESTAMPTZ DEFAULT NOW()
        )"""))
        c.execute(text(f"""CREATE TABLE IF NOT EXISTS {EVIDENCE_TABLE}(
          id BIGSERIAL PRIMARY KEY,
          fingerprint VARCHAR(64) UNIQUE NOT NULL,
          name TEXT NOT NULL,
          location TEXT NOT NULL,
          category TEXT NOT NULL,
          source_url TEXT NOT NULL,
          source_provider TEXT,
          evidence_reason TEXT,
          created_at TIMESTAMPTZ DEFAULT NOW()
        )"""))
        c.execute(text(f"CREATE INDEX IF NOT EXISTS idx_v5_raw_market ON {RAW_TABLE}(market,category,fetched_at)"))
        c.execute(text(f"CREATE INDEX IF NOT EXISTS idx_v5_ev_market ON {EVIDENCE_TABLE}(location,category,created_at)"))
    return True

def build_queries(req,market):
    kind=_kind(req)
    if kind=="FNB":
        qs=[f'restaurants in "{market}"',f'cafes restaurants "{market}"',f'food dining "{market}"']
    elif kind=="FASHION":
        qs=[f'fashion clothing stores "{market}"',f'apparel brands "{market}"',f'garment showroom "{market}"']
    elif kind=="JEWELLERY":
        qs=[f'jewellery stores "{market}"',f'jewellers brands "{market}"',f'gold diamond showroom "{market}"']
    elif kind=="LUXURY":
        qs=[f'luxury fashion stores "{market}"',f'designer brands "{market}"',f'premium retail "{market}"']
    elif kind=="GROCERY":
        qs=[f'supermarket grocery "{market}"',f'convenience store "{market}"']
    elif kind=="OFFICE":
        qs=[f'office business district "{market}"',f'corporate offices "{market}"']
    else:
        qs=[f'retail brands "{market}"',f'shops showrooms "{market}"']
    if kind in ("FNB","FASHION","JEWELLERY","LUXURY","RETAIL"):
        qs.append(f'site:instagram.com "{market}" {"restaurant cafe" if kind=="FNB" else "store brand"}')
    out=[];seen=set()
    for q in qs:
        q=re.sub(r"\s+"," ",q).strip()
        if q.lower() not in seen:
            seen.add(q.lower());out.append(q)
    return out[:4]

def _title_entity(title):
    t=re.sub(r"\s+"," ",str(title or "")).strip()
    if not t or GENERIC_TITLE_RE.search(t):return None
    t=re.split(r"\s+[|–—-]\s+|\s+:\s+",t,maxsplit=1)[0].strip()
    t=re.sub(r"^(official\s+site\s+of|welcome\s+to)\s+","",t,flags=re.I).strip()
    if len(t)<2 or len(t)>100 or len(t.split())>10:return None
    return t

def qualify_result(req,market,row):
    kind=_kind(req)
    title=str(row.get("title") or "")
    snippet=str(row.get("snippet") or "")
    url=str(row.get("url") or "")
    blob=_n(title+" "+snippet)
    domain=_domain(url)
    entity=_title_entity(title)
    if not url:return {"qualifies":False,"entity":entity,"reason":"missing source URL"}
    if not entity:return {"qualifies":False,"entity":None,"reason":"generic/listicle title"}
    if _n(market) not in blob:return {"qualifies":False,"entity":entity,"reason":"target market not evidenced"}
    if not any(k in blob for k in _keywords(kind)):return {"qualifies":False,"entity":entity,"reason":"category not evidenced"}
    if any(domain==d or domain.endswith("."+d) for d in AGGREGATOR_DOMAINS):
        return {"qualifies":False,"entity":entity,"reason":"aggregator/listing source excluded"}
    return {"qualifies":True,"entity":entity,"reason":"public non-aggregator market evidence"}

def _recent(engine,market,kind):
    try:
        with engine.connect() as c:
            return bool(c.execute(text(
                f"SELECT 1 FROM {RAW_TABLE} WHERE UPPER(market)=UPPER(:m) AND UPPER(category)=UPPER(:k) "
                f"AND fetched_at >= NOW() - INTERVAL '{CACHE_HOURS} hours' LIMIT 1"
            ),{"m":market,"k":kind}).first())
    except Exception:
        return False

def evidence_summary(engine,market,kind):
    ensure_schema(engine)
    with engine.connect() as c:
        rows=c.execute(text(
            f"SELECT name,source_url,source_provider,created_at FROM {EVIDENCE_TABLE} "
            f"WHERE UPPER(location)=UPPER(:m) AND UPPER(category)=UPPER(:k) "
            f"ORDER BY created_at DESC LIMIT 100"
        ),{"m":market,"k":kind}).mappings().all()
    return {"qualifying_count":len(rows),"entities":[dict(r) for r in rows]}

def fetch_for_market(engine,req,market,force=False):
    ensure_schema(engine)
    kind=_kind(req)
    if not force and _recent(engine,market,kind):
        return {"status":"CACHED","market":market,"category":kind,**evidence_summary(engine,market,kind)}

    import property_discovery as pd
    queries=build_queries(req,market)
    rows,logs=pd.search_waterfall(queries,deep=False)
    raw_saved=0;qualified_saved=0;seen_entities=set()

    with engine.begin() as c:
        for row in rows:
            qres=qualify_result(req,market,row)
            fp=_hash(market,kind,row.get("url"),row.get("title"))
            c.execute(text(f"""INSERT INTO {RAW_TABLE}
              (fingerprint,requirement_text,market,category,entity_name,title,snippet,source_url,source_domain,
               source_provider,query_text,qualification_status,qualification_reason,fetched_at)
              VALUES(:fp,:rq,:m,:k,:en,:ti,:sn,:url,:dom,:sp,:qq,:st,:rs,NOW())
              ON CONFLICT(fingerprint) DO UPDATE SET
                snippet=EXCLUDED.snippet,
                qualification_status=EXCLUDED.qualification_status,
                qualification_reason=EXCLUDED.qualification_reason,
                fetched_at=NOW()"""),{
                  "fp":fp,"rq":str(req.get("raw_text") or ""),"m":market,"k":kind,"en":qres.get("entity"),
                  "ti":str(row.get("title") or "")[:500],"sn":str(row.get("snippet") or "")[:2500],
                  "url":str(row.get("url") or "")[:2000],"dom":_domain(row.get("url")),
                  "sp":str(row.get("source_provider") or "Web")[:80],
                  "qq":" | ".join(queries)[:2000],
                  "st":"QUALIFYING" if qres["qualifies"] else "REJECTED","rs":qres["reason"][:800]
            })
            raw_saved+=1
            if not qres["qualifies"]:continue
            ek=_entity_key(qres["entity"])
            if not ek or ek in seen_entities:continue
            seen_entities.add(ek)
            efp=_hash(market,kind,ek)
            c.execute(text(f"""INSERT INTO {EVIDENCE_TABLE}
              (fingerprint,name,location,category,source_url,source_provider,evidence_reason,created_at)
              VALUES(:fp,:n,:m,:k,:url,:sp,:rs,NOW())
              ON CONFLICT(fingerprint) DO UPDATE SET
                source_url=EXCLUDED.source_url,
                source_provider=EXCLUDED.source_provider,
                evidence_reason=EXCLUDED.evidence_reason,
                created_at=NOW()"""),{
                  "fp":efp,"n":qres["entity"],"m":market,"k":kind,
                  "url":str(row.get("url") or "")[:2000],
                  "sp":str(row.get("source_provider") or "Web")[:80],
                  "rs":qres["reason"][:800]
            })
            qualified_saved+=1

    return {"status":"FETCHED","market":market,"category":kind,"queries":queries,
            "raw_results":len(rows),"raw_saved":raw_saved,"qualified_saved":qualified_saved,
            "provider_logs":logs,**evidence_summary(engine,market,kind),
            "truth_rule":"External evidence never creates or verifies Master property."}

def automatic_escalate(engine,req,market,current_evidence):
    if str((current_evidence or {}).get("status") or "").upper()=="STRONG":
        return {"status":"SKIPPED_STRONG_INTERNAL"}
    return fetch_for_market(engine,req,market,force=False)

def exam():
    tests=[]
    def add(n,ok):tests.append({"name":n,"pass":bool(ok)})
    add("fashion query",any("fashion" in q.lower() for q in build_queries({"raw_text":"garment","location":"Saket"},"Lajpat Nagar")))
    add("fnb query",any("restaurant" in q.lower() for q in build_queries({"raw_text":"restaurant","location":"Saket"},"Hauz Khas")))
    add("generic title rejected",not qualify_result({"raw_text":"restaurant"},"Hauz Khas",{"title":"Top 10 restaurants in Hauz Khas","snippet":"restaurants Hauz Khas","url":"https://example.com/x"})["qualifies"])
    add("aggregator rejected",not qualify_result({"raw_text":"restaurant"},"Hauz Khas",{"title":"Cafe X - Hauz Khas","snippet":"restaurant Hauz Khas","url":"https://www.zomato.com/x"})["qualifies"])
    add("market mismatch rejected",not qualify_result({"raw_text":"restaurant"},"Hauz Khas",{"title":"Cafe X","snippet":"restaurant Saket","url":"https://cafex.example"})["qualifies"])
    add("official evidence qualifies",qualify_result({"raw_text":"restaurant"},"Hauz Khas",{"title":"Cafe X - Hauz Khas","snippet":"Cafe X restaurant in Hauz Khas Delhi","url":"https://cafex.example/hauz-khas"})["qualifies"])
    add("entity normalization",_entity_key("Cafe X!")=="CAFE X")
    add("fingerprint stable",_hash("A","B")==_hash("a","b"))
    p=sum(1 for x in tests if x["pass"])
    return {"status":"PASS" if p==len(tests) else "FAIL","tested":len(tests),"passed":p,"failed":len(tests)-p,"tests":tests}

def _app(core):return getattr(core,"app",None) or core
def _engine(core):return getattr(core,"engine",None)

def register(core):
    app=_app(core);eng=_engine(core);ensure_schema(eng)
    owned={"/alliance/baby/v5-external-evidence","/api/alliance/baby/v5-external-evidence/fetch"}
    app.router.routes[:]=[r for r in app.router.routes if getattr(r,"path",None) not in owned]

    @app.get("/alliance/baby/v5-external-evidence",response_class=HTMLResponse)
    async def page(req:Request):
        import property_discovery as pd
        body=f"<h2>Alliance Baby V5 · Automatic External Market Evidence</h2><p><b>Providers:</b> {json.dumps(pd.configured_providers())}</p><p>Cache: {CACHE_HOURS} hours. External evidence never creates Master inventory.</p>"
        return HTMLResponse("<!doctype html><html><head><meta charset=utf-8><title>Baby V5</title></head><body>"+body+"</body></html>")

    @app.post("/api/alliance/baby/v5-external-evidence/fetch")
    async def fetch(inp:FetchInput):
        import alliance_baby_cre_copilot_v1 as baby
        req=baby._parse_free_text(inp.requirement)
        return JSONResponse(fetch_for_market(eng,req,inp.market,inp.force))

    return {"status":"REGISTERED","version":VERSION,"routes":sorted(owned),"exam":exam(),"schema":"READY"}
