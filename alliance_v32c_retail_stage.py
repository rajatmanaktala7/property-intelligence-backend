
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from fastapi import Request, Body
from sqlalchemy import text

MODULE_VERSION = "3.2C-DATE-RECOVERY-EXPANSION-STAGE-INTELLIGENCE"

STAGES = {
    "FUTURE_EXPANSION",
    "ACTIVE_ROLLOUT",
    "STORE_ALREADY_OPENED",
    "GENERIC_STRATEGY",
    "STALE_CONTENT",
    "NEEDS_DATE_REVIEW",
}

FUTURE_TERMS = [
    "plans to open","plan to open","will open","targets","targeting",
    "aims to open","looking to expand","plans expansion","to add stores",
    "to add outlets","new stores planned","new outlets planned",
    "store rollout","outlet rollout","expansion plan","expansion plans",
]

ROLLOUT_TERMS = [
    "expanding footprint","expands footprint","expanding presence",
    "expands presence","rollout","rolling out","accelerates expansion",
    "store network expansion","rapid expansion","scaling stores",
]

OPENED_TERMS = [
    "opens new store","opened new store","opens its","opened its",
    "launches new store","launches first","store launch","new store launch",
    "new outlet opens","inaugurates","flagship store in",
]

GENERIC_TERMS = [
    "industry outlook","market outlook","strategy","interview","analysis",
    "growth story","lifestyle powerhouse","retail trends","how jewellers",
]

STALE_MARKERS = [
    "magazine","special issue","sample pages","contents march 2024",
    "wp-content/uploads/2024","wp-content/uploads/2023","wp-content/uploads/2022",
]

def _norm(v):
    return re.sub(r"\s+"," ",str(v or "")).strip()

def _parse_date(value):
    if not value:
        return None
    s=_norm(value)
    # ISO and common yyyy-mm-dd
    for candidate in [s, s.replace("Z","+00:00")]:
        try:
            dt=datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt=dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass

    m=re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b",s)
    if m:
        try:
            return datetime(int(m.group(1)),int(m.group(2)),int(m.group(3)),tzinfo=timezone.utc)
        except Exception:
            return None
    return None

def recover_date_from_text(headline="", evidence="", source_url=""):
    blob=_norm(f"{headline} {evidence} {source_url}")

    # YYYY-MM-DD / YYYY/MM/DD
    m=re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b",blob)
    if m:
        try:
            dt=datetime(int(m.group(1)),int(m.group(2)),int(m.group(3)),tzinfo=timezone.utc)
            return {"published_at_recovered":dt.isoformat(),"date_confidence":85,"date_source":"TEXT_EXACT"}
        except Exception:
            pass

    # Month YYYY only. Store first day as coarse date, never treat as precise.
    months = {
        "january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
        "july":7,"august":8,"september":9,"october":10,"november":11,"december":12
    }
    m=re.search(r"\b("+"|".join(months.keys())+r")\s+(20\d{2})\b",blob,re.I)
    if m:
        dt=datetime(int(m.group(2)),months[m.group(1).lower()],1,tzinfo=timezone.utc)
        return {"published_at_recovered":dt.isoformat(),"date_confidence":45,"date_source":"TEXT_MONTH_YEAR"}

    # URL year/month path e.g. /2026/08/
    p=urlparse(source_url or "").path
    m=re.search(r"/(20\d{2})/(\d{1,2})/",p)
    if m:
        try:
            dt=datetime(int(m.group(1)),int(m.group(2)),1,tzinfo=timezone.utc)
            return {"published_at_recovered":dt.isoformat(),"date_confidence":40,"date_source":"URL_YEAR_MONTH"}
        except Exception:
            pass

    return {"published_at_recovered":None,"date_confidence":0,"date_source":"MISSING"}

def classify_stage(headline="", evidence="", source_url="", published_at=None, now=None):
    now=now or datetime.now(timezone.utc)
    blob=_norm(f"{headline} {evidence} {source_url}").lower()

    # Hard stale filters first.
    if any(x in blob for x in STALE_MARKERS):
        return {
            "expansion_stage":"STALE_CONTENT",
            "stage_confidence":98,
            "action":"ARCHIVE_ONLY",
            "reasons":["Archive/magazine/sample content"],
        }

    dt=_parse_date(published_at)
    if dt and now-dt > timedelta(days=365):
        return {
            "expansion_stage":"STALE_CONTENT",
            "stage_confidence":95,
            "action":"ARCHIVE_ONLY",
            "reasons":["Published more than 365 days ago"],
        }

    future_hits=[x for x in FUTURE_TERMS if x in blob]
    rollout_hits=[x for x in ROLLOUT_TERMS if x in blob]
    opened_hits=[x for x in OPENED_TERMS if x in blob]
    generic_hits=[x for x in GENERIC_TERMS if x in blob]

    # Future expansion outranks generic language.
    if future_hits:
        conf=72 + min(18, len(future_hits)*5)
        if re.search(r"\b\d+\s+(new\s+)?(stores|outlets|showrooms|locations)\b",blob):
            conf=min(98,conf+8)
        return {
            "expansion_stage":"FUTURE_EXPANSION",
            "stage_confidence":conf,
            "action":"FIND_DECISION_MAKERS",
            "reasons":[f"Future signal: {x}" for x in future_hits[:4]],
        }

    if rollout_hits:
        conf=70 + min(20,len(rollout_hits)*5)
        return {
            "expansion_stage":"ACTIVE_ROLLOUT",
            "stage_confidence":conf,
            "action":"FIND_DECISION_MAKERS",
            "reasons":[f"Rollout signal: {x}" for x in rollout_hits[:4]],
        }

    if opened_hits:
        return {
            "expansion_stage":"STORE_ALREADY_OPENED",
            "stage_confidence":88,
            "action":"SEARCH_FOR_FUTURE_ROLLOUT",
            "reasons":[f"Opened-store signal: {x}" for x in opened_hits[:3]],
        }

    if generic_hits:
        return {
            "expansion_stage":"GENERIC_STRATEGY",
            "stage_confidence":75,
            "action":"RESEARCH_ONLY",
            "reasons":[f"Generic strategy signal: {x}" for x in generic_hits[:3]],
        }

    if not dt:
        return {
            "expansion_stage":"NEEDS_DATE_REVIEW",
            "stage_confidence":45,
            "action":"RECOVER_DATE",
            "reasons":["No publication date and no decisive expansion-stage phrase"],
        }

    return {
        "expansion_stage":"GENERIC_STRATEGY",
        "stage_confidence":50,
        "action":"RESEARCH_ONLY",
        "reasons":["No decisive future/rollout/opening signal"],
    }

def _table_columns(c, table_name):
    return {
        r[0] for r in c.execute(text("""
          SELECT column_name
          FROM information_schema.columns
          WHERE table_schema='public' AND table_name=:t
        """),{"t":table_name}).all()
    }

def ensure_stage_columns(engine):
    with engine.begin() as c:
        c.execute(text("SET LOCAL lock_timeout='2s'"))
        c.execute(text("SET LOCAL statement_timeout='5s'"))
        cols=_table_columns(c,"ai_retail_expansion_signal")
        if "published_at_recovered" not in cols:
            c.execute(text("ALTER TABLE ai_retail_expansion_signal ADD COLUMN published_at_recovered TEXT"))
        if "date_confidence" not in cols:
            c.execute(text("ALTER TABLE ai_retail_expansion_signal ADD COLUMN date_confidence NUMERIC(6,2) DEFAULT 0"))
        if "date_source" not in cols:
            c.execute(text("ALTER TABLE ai_retail_expansion_signal ADD COLUMN date_source TEXT"))
        if "expansion_stage" not in cols:
            c.execute(text("ALTER TABLE ai_retail_expansion_signal ADD COLUMN expansion_stage TEXT"))
        if "stage_confidence" not in cols:
            c.execute(text("ALTER TABLE ai_retail_expansion_signal ADD COLUMN stage_confidence NUMERIC(6,2) DEFAULT 0"))
        if "stage_action" not in cols:
            c.execute(text("ALTER TABLE ai_retail_expansion_signal ADD COLUMN stage_action TEXT"))
    return {"status":"READY"}

def register(core):
    app=core.app
    engine=core.engine

    @app.get("/api/v3/retail/v32c/status")
    def status(req:Request):
        if hasattr(core,"need_login"):
            core.need_login(req)
        return {
            "version":MODULE_VERSION,
            "status":"OK",
            "db_access":False,
            "non_destructive":True,
            "date_recovery":True,
            "stage_intelligence":True,
            "auto_requirement_promotion":False,
        }

    @app.post("/api/v3/retail/v32c/setup")
    def setup(req:Request):
        if hasattr(core,"need_login"):
            core.need_login(req)
        try:
            return {"version":MODULE_VERSION,**ensure_stage_columns(engine)}
        except Exception as exc:
            return {"version":MODULE_VERSION,"status":"SCHEMA_BUSY","message":str(exc)}

    @app.get("/api/v3/retail/v32c/preview")
    def preview(req:Request,limit:int=50):
        if hasattr(core,"need_login"):
            core.need_login(req)
        with engine.connect() as c:
            rows=c.execute(text("""
              SELECT signal_id,company_name,category,headline,source_url,published_at,evidence_text
              FROM ai_retail_expansion_signal
              ORDER BY signal_id DESC
              LIMIT :lim
            """),{"lim":max(1,min(int(limit or 50),500))}).mappings().all()

        out=[]
        for r in rows:
            recovered=recover_date_from_text(
                r["headline"],r["evidence_text"],r["source_url"]
            )
            effective_date=r["published_at"] or recovered["published_at_recovered"]
            stage=classify_stage(
                r["headline"],r["evidence_text"],r["source_url"],effective_date
            )
            out.append({
                "signal_id":r["signal_id"],
                "company_name":r["company_name"],
                "category":r["category"],
                "headline":r["headline"],
                "source_url":r["source_url"],
                "published_at_original":r["published_at"],
                **recovered,
                **stage,
                "eligible_for_decision_maker_search":
                    stage["expansion_stage"] in {"FUTURE_EXPANSION","ACTIVE_ROLLOUT"},
            })

        return {"version":MODULE_VERSION,"count":len(out),"signals":out}

    @app.post("/api/v3/retail/v32c/reclassify")
    def reclassify(req:Request,limit:int=500):
        if hasattr(core,"need_login"):
            core.need_login(req)

        ensure_stage_columns(engine)

        counts={s:0 for s in STAGES}
        updated=0

        with engine.begin() as c:
            rows=c.execute(text("""
              SELECT signal_id,headline,source_url,published_at,evidence_text
              FROM ai_retail_expansion_signal
              ORDER BY signal_id DESC
              LIMIT :lim
            """),{"lim":max(1,min(int(limit or 500),5000))}).mappings().all()

            for r in rows:
                recovered=recover_date_from_text(
                    r["headline"],r["evidence_text"],r["source_url"]
                )
                effective_date=r["published_at"] or recovered["published_at_recovered"]
                stage=classify_stage(
                    r["headline"],r["evidence_text"],r["source_url"],effective_date
                )

                c.execute(text("""
                  UPDATE ai_retail_expansion_signal
                  SET published_at_recovered=:pr,
                      date_confidence=:dc,
                      date_source=:ds,
                      expansion_stage=:es,
                      stage_confidence=:sc,
                      stage_action=:sa,
                      last_seen_at=NOW()
                  WHERE signal_id=:id
                """),{
                    "pr":recovered["published_at_recovered"],
                    "dc":recovered["date_confidence"],
                    "ds":recovered["date_source"],
                    "es":stage["expansion_stage"],
                    "sc":stage["stage_confidence"],
                    "sa":stage["action"],
                    "id":r["signal_id"],
                })
                counts[stage["expansion_stage"]]+=1
                updated+=1

        return {
            "version":MODULE_VERSION,
            "evaluated":len(rows),
            "updated_non_destructively":updated,
            "stage_counts":counts,
            "source_rows_deleted":0,
            "requirement_rows_created":0,
            "next_step":"SEARCH_DECISION_MAKERS_FOR_FUTURE_AND_ACTIVE",
        }

    @app.get("/api/v3/retail/v32c/decision-maker-queue")
    def decision_maker_queue(req:Request,limit:int=100):
        if hasattr(core,"need_login"):
            core.need_login(req)

        with engine.connect() as c:
            cols=_table_columns(c,"ai_retail_expansion_signal")
            if "expansion_stage" not in cols:
                return {
                    "version":MODULE_VERSION,
                    "count":0,
                    "queue":[],
                    "next_step":"POST /api/v3/retail/v32c/setup then reclassify",
                }

            rows=c.execute(text("""
              SELECT signal_id,company_name,category,headline,source_url,
                     COALESCE(published_at,published_at_recovered) AS effective_published_at,
                     expansion_stage,stage_confidence,stage_action
              FROM ai_retail_expansion_signal
              WHERE expansion_stage IN ('FUTURE_EXPANSION','ACTIVE_ROLLOUT')
              ORDER BY stage_confidence DESC,signal_id DESC
              LIMIT :lim
            """),{"lim":max(1,min(int(limit or 100),500))}).mappings().all()

        return {
            "version":MODULE_VERSION,
            "count":len(rows),
            "queue":[dict(x) for x in rows],
        }
