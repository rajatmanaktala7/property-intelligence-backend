
from __future__ import annotations
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from fastapi import Request
from sqlalchemy import text

MODULE_VERSION="3.2C1-LAZY-ACTIVATION-STARTUP-SAFE"
_MOUNTED=False

STALE_MARKERS=[
    "magazine","special issue","sample pages","contents march 2024",
    "wp-content/uploads/2024","wp-content/uploads/2023","wp-content/uploads/2022",
]
FUTURE_TERMS=[
    "plans to open","plan to open","will open","targets","targeting",
    "aims to open","looking to expand","plans expansion","to add stores",
    "to add outlets","new stores planned","new outlets planned",
    "store rollout","outlet rollout","expansion plan","expansion plans",
]
ROLLOUT_TERMS=[
    "expanding footprint","expands footprint","expanding presence",
    "expands presence","rollout","rolling out","accelerates expansion",
    "store network expansion","rapid expansion","scaling stores",
]
OPENED_TERMS=[
    "opens new store","opened new store","opens its","opened its",
    "launches new store","launches first","store launch","new store launch",
    "new outlet opens","inaugurates","flagship store in",
]

def _norm(v): return re.sub(r"\s+"," ",str(v or "")).strip()

def _parse_date(v):
    if not v: return None
    s=_norm(v).replace("Z","+00:00")
    try:
        d=datetime.fromisoformat(s)
        if d.tzinfo is None: d=d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None

def recover_date_from_text(headline="",evidence="",source_url=""):
    blob=_norm(f"{headline} {evidence} {source_url}")
    m=re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b",blob)
    if m:
        try:
            d=datetime(int(m.group(1)),int(m.group(2)),int(m.group(3)),tzinfo=timezone.utc)
            return {"published_at_recovered":d.isoformat(),"date_confidence":85,"date_source":"TEXT_EXACT"}
        except Exception: pass
    months={"january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
            "july":7,"august":8,"september":9,"october":10,"november":11,"december":12}
    m=re.search(r"\b("+"|".join(months)+r")\s+(20\d{2})\b",blob,re.I)
    if m:
        d=datetime(int(m.group(2)),months[m.group(1).lower()],1,tzinfo=timezone.utc)
        return {"published_at_recovered":d.isoformat(),"date_confidence":45,"date_source":"TEXT_MONTH_YEAR"}
    p=urlparse(source_url or "").path
    m=re.search(r"/(20\d{2})/(\d{1,2})/",p)
    if m:
        d=datetime(int(m.group(1)),int(m.group(2)),1,tzinfo=timezone.utc)
        return {"published_at_recovered":d.isoformat(),"date_confidence":40,"date_source":"URL_YEAR_MONTH"}
    return {"published_at_recovered":None,"date_confidence":0,"date_source":"MISSING"}

def classify_stage(headline="",evidence="",source_url="",published_at=None,now=None):
    now=now or datetime.now(timezone.utc)
    blob=_norm(f"{headline} {evidence} {source_url}").lower()
    if any(x in blob for x in STALE_MARKERS):
        return {"expansion_stage":"STALE_CONTENT","stage_confidence":98,
                "action":"ARCHIVE_ONLY","reasons":["Archive/magazine/sample content"]}
    dt=_parse_date(published_at)
    if dt and now-dt>timedelta(days=365):
        return {"expansion_stage":"STALE_CONTENT","stage_confidence":95,
                "action":"ARCHIVE_ONLY","reasons":["Published more than 365 days ago"]}
    fh=[x for x in FUTURE_TERMS if x in blob]
    if fh:
        return {"expansion_stage":"FUTURE_EXPANSION","stage_confidence":85,
                "action":"FIND_DECISION_MAKERS","reasons":[f"Future signal: {x}" for x in fh[:4]]}
    rh=[x for x in ROLLOUT_TERMS if x in blob]
    if rh:
        return {"expansion_stage":"ACTIVE_ROLLOUT","stage_confidence":82,
                "action":"FIND_DECISION_MAKERS","reasons":[f"Rollout signal: {x}" for x in rh[:4]]}
    oh=[x for x in OPENED_TERMS if x in blob]
    if oh:
        return {"expansion_stage":"STORE_ALREADY_OPENED","stage_confidence":88,
                "action":"SEARCH_FOR_FUTURE_ROLLOUT","reasons":[f"Opened signal: {x}" for x in oh[:3]]}
    if not dt:
        return {"expansion_stage":"NEEDS_DATE_REVIEW","stage_confidence":45,
                "action":"RECOVER_DATE","reasons":["No decisive dated expansion signal"]}
    return {"expansion_stage":"GENERIC_STRATEGY","stage_confidence":50,
            "action":"RESEARCH_ONLY","reasons":["No decisive expansion-stage signal"]}

def _columns(c):
    return {r[0] for r in c.execute(text("""
      SELECT column_name FROM information_schema.columns
      WHERE table_schema='public' AND table_name='ai_retail_expansion_signal'
    """)).all()}

def ensure_columns(engine):
    with engine.begin() as c:
        c.execute(text("SET LOCAL lock_timeout='2s'"))
        c.execute(text("SET LOCAL statement_timeout='5s'"))
        cols=_columns(c)
        additions=[
          ("published_at_recovered","TEXT"),
          ("date_confidence","NUMERIC(6,2) DEFAULT 0"),
          ("date_source","TEXT"),
          ("expansion_stage","TEXT"),
          ("stage_confidence","NUMERIC(6,2) DEFAULT 0"),
          ("stage_action","TEXT"),
        ]
        for name,typ in additions:
            if name not in cols:
                c.execute(text(f"ALTER TABLE ai_retail_expansion_signal ADD COLUMN {name} {typ}"))
    return {"status":"READY"}

def _mount_db_routes(app,core):
    global _MOUNTED
    if _MOUNTED: return False
    engine=core.engine

    @app.post("/api/v3/retail/v32c1/setup")
    def setup(req:Request):
        if hasattr(core,"need_login"): core.need_login(req)
        try: return {"version":MODULE_VERSION,**ensure_columns(engine)}
        except Exception as exc: return {"version":MODULE_VERSION,"status":"SCHEMA_BUSY","message":str(exc)}

    @app.get("/api/v3/retail/v32c1/preview")
    def preview(req:Request,limit:int=50):
        if hasattr(core,"need_login"): core.need_login(req)
        with engine.connect() as c:
            rows=c.execute(text("""
              SELECT signal_id,company_name,category,headline,source_url,published_at,evidence_text
              FROM ai_retail_expansion_signal ORDER BY signal_id DESC LIMIT :lim
            """),{"lim":max(1,min(limit,500))}).mappings().all()
        out=[]
        for r in rows:
            rec=recover_date_from_text(r["headline"],r["evidence_text"],r["source_url"])
            eff=r["published_at"] or rec["published_at_recovered"]
            st=classify_stage(r["headline"],r["evidence_text"],r["source_url"],eff)
            out.append({"signal_id":r["signal_id"],"company_name":r["company_name"],
                        "category":r["category"],"headline":r["headline"],
                        "source_url":r["source_url"],"published_at_original":r["published_at"],
                        **rec,**st,
                        "eligible_for_decision_maker_search":
                            st["expansion_stage"] in {"FUTURE_EXPANSION","ACTIVE_ROLLOUT"}})
        return {"version":MODULE_VERSION,"count":len(out),"signals":out}

    @app.post("/api/v3/retail/v32c1/reclassify")
    def reclassify(req:Request,limit:int=500):
        if hasattr(core,"need_login"): core.need_login(req)
        ensure_columns(engine)
        counts={}
        with engine.begin() as c:
            rows=c.execute(text("""
              SELECT signal_id,headline,source_url,published_at,evidence_text
              FROM ai_retail_expansion_signal ORDER BY signal_id DESC LIMIT :lim
            """),{"lim":max(1,min(limit,5000))}).mappings().all()
            for r in rows:
                rec=recover_date_from_text(r["headline"],r["evidence_text"],r["source_url"])
                eff=r["published_at"] or rec["published_at_recovered"]
                st=classify_stage(r["headline"],r["evidence_text"],r["source_url"],eff)
                c.execute(text("""
                  UPDATE ai_retail_expansion_signal
                  SET published_at_recovered=:pr,date_confidence=:dc,date_source=:ds,
                      expansion_stage=:es,stage_confidence=:sc,stage_action=:sa,last_seen_at=NOW()
                  WHERE signal_id=:id
                """),{"pr":rec["published_at_recovered"],"dc":rec["date_confidence"],
                      "ds":rec["date_source"],"es":st["expansion_stage"],
                      "sc":st["stage_confidence"],"sa":st["action"],"id":r["signal_id"]})
                counts[st["expansion_stage"]]=counts.get(st["expansion_stage"],0)+1
        return {"version":MODULE_VERSION,"evaluated":len(rows),
                "updated_non_destructively":len(rows),"stage_counts":counts,
                "source_rows_deleted":0}

    _MOUNTED=True
    return True

def register(core):
    app=core.app

    @app.get("/api/v3/retail/v32c1/status")
    def status(req:Request):
        if hasattr(core,"need_login"): core.need_login(req)
        return {"version":MODULE_VERSION,"status":"OK","db_access":False,
                "lazy_activation":True,"db_routes_mounted":_MOUNTED,
                "non_destructive":True}

    @app.post("/api/v3/retail/v32c1/activate")
    def activate(req:Request):
        if hasattr(core,"need_login"): core.need_login(req)
        try:
            mounted=_mount_db_routes(app,core)
            return {"version":MODULE_VERSION,"status":"ACTIVE",
                    "mounted_now":mounted,"db_routes_mounted":True}
        except Exception as exc:
            return {"version":MODULE_VERSION,"status":"ACTIVATION_ERROR","message":str(exc)}
