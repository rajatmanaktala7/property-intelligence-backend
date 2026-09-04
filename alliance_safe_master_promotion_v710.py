from __future__ import annotations
import hashlib, html, json, threading, time
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal

from fastapi.responses import HTMLResponse
from sqlalchemy import text

VERSION="7.1.0-ALLIANCE-SAFE-MASTER-PROMOTION"
MODE="PROMOTION_READY_ONLY_SPLIT_PROPERTY_REQUIREMENT_IDEMPOTENT_CANONICAL_SOURCE_LINKED_NO_RAW_MUTATION"

STATE={"status":"NOT_STARTED","phase":"WAITING","started_at":None,"finished_at":None,
       "processed":0,"result":None,"last_error":None}
_LOCK=threading.Lock();_STARTED=False

DDL=[
"""CREATE TABLE IF NOT EXISTS pi_master_properties_v710(
 master_property_id TEXT PRIMARY KEY,
 canonical_id TEXT NOT NULL UNIQUE,
 source_type TEXT,
 transaction_type TEXT,
 locality TEXT,
 city TEXT,
 area_value NUMERIC(18,4),
 area_unit TEXT,
 area_sqft NUMERIC(18,4),
 price_raw TEXT,
 price_kind TEXT,
 phones JSONB DEFAULT '[]'::jsonb,
 clean_record JSONB NOT NULL,
 source_count INTEGER DEFAULT 1,
 promotion_status TEXT NOT NULL DEFAULT 'PROMOTED_VALIDATED',
 source_version TEXT NOT NULL,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 updated_at TIMESTAMPTZ DEFAULT NOW()
)""",
"""CREATE INDEX IF NOT EXISTS idx_master_property_v710_loc ON pi_master_properties_v710(locality)""",
"""CREATE INDEX IF NOT EXISTS idx_master_property_v710_tx ON pi_master_properties_v710(transaction_type)""",

"""CREATE TABLE IF NOT EXISTS pi_master_requirements_v710(
 master_requirement_id TEXT PRIMARY KEY,
 canonical_id TEXT NOT NULL UNIQUE,
 source_type TEXT,
 transaction_type TEXT,
 locality TEXT,
 city TEXT,
 area_value NUMERIC(18,4),
 area_unit TEXT,
 area_sqft NUMERIC(18,4),
 budget_raw TEXT,
 budget_kind TEXT,
 phones JSONB DEFAULT '[]'::jsonb,
 clean_record JSONB NOT NULL,
 source_count INTEGER DEFAULT 1,
 promotion_status TEXT NOT NULL DEFAULT 'PROMOTED_VALIDATED',
 source_version TEXT NOT NULL,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 updated_at TIMESTAMPTZ DEFAULT NOW()
)""",
"""CREATE INDEX IF NOT EXISTS idx_master_requirement_v710_loc ON pi_master_requirements_v710(locality)""",
"""CREATE INDEX IF NOT EXISTS idx_master_requirement_v710_tx ON pi_master_requirements_v710(transaction_type)""",

"""CREATE TABLE IF NOT EXISTS pi_master_source_links_v710(
 id BIGSERIAL PRIMARY KEY,
 master_entity_type TEXT NOT NULL,
 master_id TEXT NOT NULL,
 canonical_id TEXT NOT NULL,
 source_type TEXT NOT NULL,
 source_table TEXT NOT NULL,
 source_pk TEXT NOT NULL,
 source_row_hash TEXT NOT NULL,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 UNIQUE(master_entity_type,master_id,source_table,source_pk,source_row_hash)
)""",

"""CREATE TABLE IF NOT EXISTS pi_master_promotion_runs_v710(
 run_id BIGSERIAL PRIMARY KEY,
 version TEXT NOT NULL,
 mode TEXT NOT NULL,
 status TEXT NOT NULL,
 result JSONB NOT NULL,
 created_at TIMESTAMPTZ DEFAULT NOW()
)"""
]

def _engine(core): return getattr(core,"engine",None)
def _app(core): return getattr(core,"app",None) or core
def _route_exists(a,p):
    try:return any(getattr(r,"path",None)==p for r in a.routes)
    except:return False

def _json(v):
    if v is None:return None
    if isinstance(v,(str,int,float,bool)):return v
    if isinstance(v,Decimal):return float(v)
    if isinstance(v,dict):return {str(k):_json(x) for k,x in v.items()}
    if isinstance(v,(list,tuple,set)):return [_json(x) for x in v]
    return str(v)

def _require_parent(engine):
    with engine.connect() as c:
        present=c.execute(text("SELECT to_regclass('pi_source_aware_clean_v705') IS NOT NULL")).scalar()
        present2=c.execute(text("SELECT to_regclass('pi_source_aware_canonical_v705') IS NOT NULL")).scalar()
        present3=c.execute(text("SELECT to_regclass('pi_source_aware_links_v705') IS NOT NULL")).scalar()
    if not (present and present2 and present3):
        raise RuntimeError("7.0.5 derived tables are missing")

def _parent_counts(engine):
    with engine.connect() as c:
        total=c.execute(text("SELECT COUNT(*) FROM pi_source_aware_clean_v705")).scalar_one()
        ready=c.execute(text("SELECT COUNT(*) FROM pi_source_aware_clean_v705 WHERE quality_status='PROMOTION_READY'")).scalar_one()
        props=c.execute(text("""SELECT COUNT(*) FROM pi_source_aware_clean_v705
          WHERE quality_status='PROMOTION_READY' AND entity_type='PROPERTY_AVAILABILITY'""")).scalar_one()
        reqs=c.execute(text("""SELECT COUNT(*) FROM pi_source_aware_clean_v705
          WHERE quality_status='PROMOTION_READY' AND entity_type='REQUIREMENT'""")).scalar_one()
        bad=c.execute(text("""SELECT COUNT(*) FROM pi_source_aware_clean_v705
          WHERE quality_status='PROMOTION_READY'
          AND (entity_type NOT IN ('PROPERTY_AVAILABILITY','REQUIREMENT')
               OR canonical_transaction='UNKNOWN'
               OR locality_clean IS NULL
               OR jsonb_array_length(phones)=0)""")).scalar_one()
    return {"total":total,"ready":ready,"ready_properties":props,"ready_requirements":reqs,"invalid_ready_rows":bad}

def run_once(core):
    if not _LOCK.acquire(False):return STATE.get("result") or dict(STATE)
    try:
        STATE.update(status="RUNNING",phase="VALIDATE_PARENT",started_at=datetime.now(timezone.utc).isoformat(),
                     finished_at=None,processed=0,result=None,last_error=None)
        engine=_engine(core)
        if engine is None:raise RuntimeError("Database engine unavailable")
        _require_parent(engine)
        pc=_parent_counts(engine)
        if pc["ready"]<=0:raise RuntimeError("No PROMOTION_READY rows in 7.0.5")
        if pc["invalid_ready_rows"]!=0:
            raise RuntimeError(f"Promotion gate failed: {pc['invalid_ready_rows']} invalid PROMOTION_READY rows")

        with engine.begin() as c:
            for ddl in DDL:c.execute(text(ddl))

        STATE["phase"]="SELECT_CANONICAL_PROMOTION_SET"

        # Only canonical entities having at least one PROMOTION_READY clean source row.
        with engine.connect() as c:
            rows=c.execute(text("""
              SELECT DISTINCT ON (can.canonical_id)
                can.canonical_id,
                can.entity_type,
                can.canonical_transaction,
                can.locality_clean,
                can.city_clean,
                can.area_value,
                can.area_unit,
                can.area_sqft,
                can.price_raw,
                can.price_kind,
                can.phones,
                can.clean_record,
                can.source_count,
                cl.source_type
              FROM pi_source_aware_canonical_v705 can
              JOIN pi_source_aware_links_v705 l ON l.canonical_id=can.canonical_id
              JOIN pi_source_aware_clean_v705 cl
                ON cl.source_table=l.source_table
               AND cl.source_pk=l.source_pk
               AND cl.source_row_hash=l.source_row_hash
              WHERE cl.quality_status='PROMOTION_READY'
                AND can.entity_type IN ('PROPERTY_AVAILABILITY','REQUIREMENT')
                AND can.canonical_transaction <> 'UNKNOWN'
                AND can.locality_clean IS NOT NULL
                AND jsonb_array_length(can.phones)>0
              ORDER BY can.canonical_id, cl.confidence DESC, cl.id ASC
            """)).mappings().all()

        counts=Counter();source_counts=Counter();promoted_ids=[]
        STATE["phase"]="PROMOTE_IDEMPOTENT"

        for r in rows:
            d=dict(r);cid=str(d["canonical_id"]);etype=d["entity_type"]
            mid_prefix="MP" if etype=="PROPERTY_AVAILABILITY" else "MR"
            mid=f"{mid_prefix}-{hashlib.sha256(cid.encode()).hexdigest()[:16].upper()}"
            phones=json.dumps(_json(d.get("phones") or []),ensure_ascii=False)
            clean=json.dumps(_json(d.get("clean_record") or {}),ensure_ascii=False)
            if etype=="PROPERTY_AVAILABILITY":
                with engine.begin() as c:
                    c.execute(text("""
                      INSERT INTO pi_master_properties_v710(
                        master_property_id,canonical_id,source_type,transaction_type,locality,city,
                        area_value,area_unit,area_sqft,price_raw,price_kind,phones,clean_record,
                        source_count,promotion_status,source_version)
                      VALUES(:mid,:cid,:st,:tx,:loc,:city,:av,:au,:asq,:pr,:pk,CAST(:ph AS JSONB),
                             CAST(:clean AS JSONB),:sc,'PROMOTED_VALIDATED',:sv)
                      ON CONFLICT(canonical_id) DO UPDATE SET
                        source_type=EXCLUDED.source_type,
                        transaction_type=EXCLUDED.transaction_type,
                        locality=EXCLUDED.locality,
                        city=EXCLUDED.city,
                        area_value=EXCLUDED.area_value,
                        area_unit=EXCLUDED.area_unit,
                        area_sqft=EXCLUDED.area_sqft,
                        price_raw=EXCLUDED.price_raw,
                        price_kind=EXCLUDED.price_kind,
                        phones=EXCLUDED.phones,
                        clean_record=EXCLUDED.clean_record,
                        source_count=EXCLUDED.source_count,
                        promotion_status='PROMOTED_VALIDATED',
                        source_version=EXCLUDED.source_version,
                        updated_at=NOW()
                    """),{"mid":mid,"cid":cid,"st":d.get("source_type"),"tx":d.get("canonical_transaction"),
                           "loc":d.get("locality_clean"),"city":d.get("city_clean"),"av":d.get("area_value"),
                           "au":d.get("area_unit"),"asq":d.get("area_sqft"),"pr":d.get("price_raw"),
                           "pk":d.get("price_kind"),"ph":phones,"clean":clean,"sc":d.get("source_count") or 1,"sv":VERSION})
                counts["properties_promoted"]+=1
            else:
                with engine.begin() as c:
                    c.execute(text("""
                      INSERT INTO pi_master_requirements_v710(
                        master_requirement_id,canonical_id,source_type,transaction_type,locality,city,
                        area_value,area_unit,area_sqft,budget_raw,budget_kind,phones,clean_record,
                        source_count,promotion_status,source_version)
                      VALUES(:mid,:cid,:st,:tx,:loc,:city,:av,:au,:asq,:pr,:pk,CAST(:ph AS JSONB),
                             CAST(:clean AS JSONB),:sc,'PROMOTED_VALIDATED',:sv)
                      ON CONFLICT(canonical_id) DO UPDATE SET
                        source_type=EXCLUDED.source_type,
                        transaction_type=EXCLUDED.transaction_type,
                        locality=EXCLUDED.locality,
                        city=EXCLUDED.city,
                        area_value=EXCLUDED.area_value,
                        area_unit=EXCLUDED.area_unit,
                        area_sqft=EXCLUDED.area_sqft,
                        budget_raw=EXCLUDED.budget_raw,
                        budget_kind=EXCLUDED.budget_kind,
                        phones=EXCLUDED.phones,
                        clean_record=EXCLUDED.clean_record,
                        source_count=EXCLUDED.source_count,
                        promotion_status='PROMOTED_VALIDATED',
                        source_version=EXCLUDED.source_version,
                        updated_at=NOW()
                    """),{"mid":mid,"cid":cid,"st":d.get("source_type"),"tx":d.get("canonical_transaction"),
                           "loc":d.get("locality_clean"),"city":d.get("city_clean"),"av":d.get("area_value"),
                           "au":d.get("area_unit"),"asq":d.get("area_sqft"),"pr":d.get("price_raw"),
                           "pk":d.get("price_kind"),"ph":phones,"clean":clean,"sc":d.get("source_count") or 1,"sv":VERSION})
                counts["requirements_promoted"]+=1

            # Rebuild source evidence links for this promoted canonical entity.
            with engine.begin() as c:
                links=c.execute(text("""
                  SELECT source_type,source_table,source_pk,source_row_hash
                  FROM pi_source_aware_links_v705 WHERE canonical_id=:cid
                """),{"cid":cid}).mappings().all()
                for ln in links:
                    c.execute(text("""
                      INSERT INTO pi_master_source_links_v710(
                        master_entity_type,master_id,canonical_id,source_type,source_table,source_pk,source_row_hash)
                      VALUES(:et,:mid,:cid,:st,:tb,:pk,:rh)
                      ON CONFLICT DO NOTHING
                    """),{"et":"PROPERTY" if etype=="PROPERTY_AVAILABILITY" else "REQUIREMENT",
                           "mid":mid,"cid":cid,"st":ln["source_type"],"tb":ln["source_table"],
                           "pk":ln["source_pk"],"rh":ln["source_row_hash"]})
                    source_counts[ln["source_type"]]+=1
            promoted_ids.append(cid)
            STATE["processed"]=len(promoted_ids)

        STATE["phase"]="POST_PROMOTION_AUDIT"
        with engine.connect() as c:
            mp=c.execute(text("SELECT COUNT(*) FROM pi_master_properties_v710")).scalar_one()
            mr=c.execute(text("SELECT COUNT(*) FROM pi_master_requirements_v710")).scalar_one()
            links=c.execute(text("SELECT COUNT(*) FROM pi_master_source_links_v710")).scalar_one()
            bad_master_props=c.execute(text("""SELECT COUNT(*) FROM pi_master_properties_v710
              WHERE transaction_type='UNKNOWN' OR locality IS NULL OR jsonb_array_length(phones)=0""")).scalar_one()
            bad_master_reqs=c.execute(text("""SELECT COUNT(*) FROM pi_master_requirements_v710
              WHERE transaction_type='UNKNOWN' OR locality IS NULL OR jsonb_array_length(phones)=0""")).scalar_one()

        result={
          "version":VERSION,"mode":MODE,"status":"COMPLETE",
          "parent_gate":pc,
          "promotion":{
            "canonical_candidates":len(rows),
            "properties_promoted_this_run":counts["properties_promoted"],
            "requirements_promoted_this_run":counts["requirements_promoted"],
            "master_properties_total":mp,
            "master_requirements_total":mr,
            "master_source_links_total":links,
            "source_link_counts_this_run":dict(source_counts),
          },
          "post_promotion_audit":{
            "invalid_master_properties":bad_master_props,
            "invalid_master_requirements":bad_master_reqs,
            "gate":"PASS" if bad_master_props==0 and bad_master_reqs==0 else "FAIL"
          },
          "architecture":{
            "raw_source_mutations":0,
            "gold_mutations":0,
            "champion_mutations":0,
            "pi_properties_mutations":0,
            "pi_newspaper_properties_mutations":0,
            "pi_magazine_master_mutations":0,
            "only_promotion_ready":True,
            "requirements_separate_from_properties":True,
            "canonical_dedupe_before_promotion":True,
            "source_evidence_links_preserved":True,
            "idempotent_upsert":True,
            "ai_calls_used":0
          },
          "next_step":"CONNECT_MASTER_V710_TO_SEARCH_MATCH_DASHBOARD_AFTER_AUDIT_PASS"
        }
        with engine.begin() as c:
            c.execute(text("""INSERT INTO pi_master_promotion_runs_v710(version,mode,status,result)
              VALUES(:v,:m,'COMPLETE',CAST(:r AS JSONB))"""),
              {"v":VERSION,"m":MODE,"r":json.dumps(result,ensure_ascii=False)})
        STATE.update(status="COMPLETE",phase="COMPLETE",finished_at=datetime.now(timezone.utc).isoformat(),result=result)
        return result
    except Exception as exc:
        STATE.update(status="ERROR",phase="ERROR",finished_at=datetime.now(timezone.utc).isoformat(),
                     last_error=f"{type(exc).__name__}: {exc}")
        return dict(STATE)
    finally:
        _LOCK.release()

def status(core):return STATE.get("result") or dict(STATE)

def dashboard(core):
    s=status(core);p=s.get("promotion") or {};a=s.get("post_promotion_audit") or {}
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta http-equiv='refresh' content='10'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>Safe Master Promotion 7.1</title>
<style>body{{font-family:Arial;background:#f5f7fb;color:#172033;margin:0}}header{{background:#102235;color:white;padding:18px}}
.wrap{{max-width:1450px;margin:auto;padding:18px}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(170px,1fr));gap:10px}}
.card{{background:white;border:1px solid #e2e8f0;border-radius:12px;padding:14px;margin-bottom:12px}}b.num{{font-size:24px;display:block}}
pre{{white-space:pre-wrap;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px;overflow:auto}}</style></head>
<body><header><b>Alliance Safe Master Promotion 7.1</b><br><small>Promotion-ready only · canonical dedupe · Property and Requirement masters separated</small></header>
<div class='wrap'><div class='card'><b>{html.escape(str(s.get("status")))}</b> · Phase {html.escape(str(s.get("phase")))}</div>
<div class='grid'>
<div class='card'>Canonical promoted<b class='num'>{p.get("canonical_candidates","-")}</b></div>
<div class='card'>Master properties<b class='num'>{p.get("master_properties_total","-")}</b></div>
<div class='card'>Master requirements<b class='num'>{p.get("master_requirements_total","-")}</b></div>
<div class='card'>Evidence links<b class='num'>{p.get("master_source_links_total","-")}</b></div>
<div class='card'>Invalid properties<b class='num'>{a.get("invalid_master_properties","-")}</b></div>
<div class='card'>Invalid requirements<b class='num'>{a.get("invalid_master_requirements","-")}</b></div>
<div class='card'>Audit gate<b class='num'>{a.get("gate","-")}</b></div>
</div><pre>{html.escape(json.dumps(s,ensure_ascii=False,indent=2))}</pre></div></body></html>"""

def register(core):
    app=_app(core)
    if not _route_exists(app,"/api/property-brain/master-promotion-v710/status"):
        @app.get("/api/property-brain/master-promotion-v710/status")
        def _s():return status(core)
    if not _route_exists(app,"/property-brain/master-promotion-v710"):
        @app.get("/property-brain/master-promotion-v710",response_class=HTMLResponse)
        def _p():return HTMLResponse(dashboard(core))

def _runner(core):
    time.sleep(60);run_once(core)

def start(core):
    global _STARTED
    register(core)
    if not _STARTED:
        _STARTED=True
        threading.Thread(target=_runner,args=(core,),daemon=True,name="master-promotion-v710").start()
    return STATE
