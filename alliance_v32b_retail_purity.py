from __future__ import annotations
import re, hashlib
from datetime import datetime, timezone, timedelta

MODULE_VERSION="3.2B2-REGISTRATION-SIGNATURE-FIX"

TARGET_ROLES=[
"head of expansion","expansion head","expansion manager","head of real estate",
"real estate head","real estate manager","head of leasing","leasing head",
"leasing manager","head of business development","business development head",
"business development manager","business development officer",
"store development head","store development manager"
]
EXPANSION_PHRASES=[
"plans to open","plan to open","targets","targeting","new stores","new outlets",
"expand footprint","expanding footprint","expansion","entering delhi","entering india",
"seeking retail space","looking for retail space","looking to expand",
"store rollout","outlet rollout","open stores","open outlets"
]
STALE_MARKERS=["magazine","special issue","sample pages","wp-content/uploads/2024",
"wp-content/uploads/2023","wp-content/uploads/2022"]
PROFILE_RE=re.compile(r"^https?://([a-z]{2,3}\.)?linkedin\.com/in/[^/?#]+/?",re.I)

def _norm(s): return re.sub(r"\s+"," ",str(s or "")).strip()
def _year(text):
    m=re.search(r"\b(20\d{2})\b",_norm(text))
    return int(m.group(1)) if m else None

def classify_signal(headline="",evidence="",source_url="",published_at=None,now=None):
    now=now or datetime.now(timezone.utc)
    blob=_norm(f"{headline} {evidence} {source_url}").lower()
    if any(x in blob for x in STALE_MARKERS):
        return {"intent_score":0,"intent_status":"STALE_CONTENT","requirement_candidate":False,
                "reasons":["Magazine/archive/sample content"]}
    yr=_year(blob)
    if yr and yr < now.year-1:
        return {"intent_score":0,"intent_status":"STALE_CONTENT","requirement_candidate":False,
                "reasons":[f"Old content year {yr}"]}
    dated=False
    if published_at:
        try:
            dt=datetime.fromisoformat(str(published_at).replace("Z","+00:00"))
            if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
            if now-dt > timedelta(days=180):
                return {"intent_score":10,"intent_status":"STALE_CONTENT","requirement_candidate":False,
                        "reasons":["Published more than 180 days ago"]}
            dated=True
        except Exception: pass
    hits=[p for p in EXPANSION_PHRASES if p in blob]
    reasons=[]
    score=min(70,len(set(hits))*12)
    if re.search(r"\b\d+\s+(new\s+)?(stores|outlets|locations)\b",blob):
        score+=25; reasons.append("Store-count evidence")
    if any(x in blob for x in ["delhi","ncr","gurugram","noida","india"]):
        score+=8; reasons.append("Market/location evidence")
    if dated: score+=7; reasons.append("Dated source")
    else: reasons.append("Publication date missing")
    score=min(score,100)
    status="HIGH_INTENT" if score>=70 and dated else ("REVIEW" if score>=35 else "LOW_SIGNAL")
    reasons += [f"Expansion phrase: {x}" for x in hits[:4]]
    return {"intent_score":score,"intent_status":status,
            "requirement_candidate":status=="HIGH_INTENT","reasons":reasons}

def valid_public_linkedin_profile(url): return bool(PROFILE_RE.match(_norm(url)))

def profile_confidence(title="",snippet="",url=""):
    if not valid_public_linkedin_profile(url): return 0
    blob=_norm(f"{title} {snippet}").lower()
    score=45
    if any(r in blob for r in TARGET_ROLES): score+=35
    if any(x in blob for x in ["retail","jewellery","jewelry","restaurant","qsr","hospitality","banquet"]): score+=10
    if "linkedin" in blob: score+=5
    return min(score,100)

def build_profile_queries(company,category="",location="India"):
    roles=['"Head of Expansion"','"Expansion Manager"','"Real Estate Head"',
           '"Leasing Head"','"Business Development Head"','"Store Development Manager"']
    return [f'site:linkedin.com/in {r} "{_norm(company)}" {_norm(category)} {_norm(location)}'.strip()
            for r in roles]

def canonical_key(company,url):
    return hashlib.sha256((_norm(company).lower()+"|"+_norm(url).lower()).encode()).hexdigest()[:32]

def register(core):
    from fastapi import Request
    from sqlalchemy import text
    app=core.app
    engine=core.engine

    @app.get("/api/v3/retail/v32b/status")
    def status(req:Request):
        if hasattr(core,"need_login"): core.need_login(req)
        return {"version":MODULE_VERSION,"status":"OK","db_access":False,
                "non_destructive":True,"direct_linkedin_scraping":False,
                "public_profile_discovery":True,"purity_filter":True}

    @app.get("/api/v3/retail/v32b/purity-preview")
    def purity_preview(req:Request,limit:int=50):
        if hasattr(core,"need_login"): core.need_login(req)
        with engine.connect() as c:
            rows=c.execute(text("""SELECT signal_id,company_name,category,headline,source_url,
                published_at,evidence_text FROM ai_retail_expansion_signal
                ORDER BY signal_id DESC LIMIT :lim"""),
                {"lim":max(1,min(limit,500))}).mappings().all()
        out=[]
        for r in rows:
            d=classify_signal(r["headline"],r["evidence_text"],r["source_url"],r["published_at"])
            out.append({"signal_id":r["signal_id"],"company_name":r["company_name"],
                        "category":r["category"],"headline":r["headline"],
                        "source_url":r["source_url"],**d})
        return {"version":MODULE_VERSION,"count":len(out),"signals":out}

    @app.post("/api/v3/retail/v32b/reclassify")
    def reclassify(req:Request,limit:int=500):
        if hasattr(core,"need_login"): core.need_login(req)
        with engine.begin() as c:
            rows=c.execute(text("""SELECT signal_id,headline,evidence_text,source_url,published_at
                FROM ai_retail_expansion_signal ORDER BY signal_id DESC LIMIT :lim"""),
                {"lim":max(1,min(limit,5000))}).mappings().all()
            cols={r[0] for r in c.execute(text("""SELECT column_name FROM information_schema.columns
                WHERE table_name='ai_retail_expansion_signal'""")).all()}
            changed=high=stale=review=0
            for r in rows:
                d=classify_signal(r["headline"],r["evidence_text"],r["source_url"],r["published_at"])
                sets=[]; p={"id":r["signal_id"]}
                if "intent_score" in cols: sets.append("intent_score=:sc"); p["sc"]=d["intent_score"]
                if "intent_status" in cols: sets.append("intent_status=:st"); p["st"]=d["intent_status"]
                if sets:
                    c.execute(text("UPDATE ai_retail_expansion_signal SET "+",".join(sets)+" WHERE signal_id=:id"),p)
                    changed+=1
                high+=d["intent_status"]=="HIGH_INTENT"
                stale+=d["intent_status"]=="STALE_CONTENT"
                review+=d["intent_status"]=="REVIEW"
        return {"version":MODULE_VERSION,"evaluated":len(rows),
                "updated_non_destructively":changed,"HIGH_INTENT":high,
                "STALE_CONTENT":stale,"REVIEW":review,"source_rows_deleted":0}

    @app.get("/api/v3/retail/v32b/profile-queries")
    def profile_queries(req:Request,company:str,category:str="",location:str="India"):
        if hasattr(core,"need_login"): core.need_login(req)
        return {"version":MODULE_VERSION,"company":company,
                "queries":build_profile_queries(company,category,location),
                "rule":"Accept only publicly indexed linkedin.com/in profile URLs"}
