from __future__ import annotations
import hashlib, html, json, threading, time
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from fastapi.responses import HTMLResponse
from sqlalchemy import text

VERSION="7.1.1-ALLIANCE-PROMOTION-INTEGRITY-CLOSURE"
MODE="WAIT_PARENT_COMPLETE_REPEATABLE_READ_FROZEN_SNAPSHOT_RECONCILED_COUNTS_NONEMPTY_GATE_IDEMPOTENT_NO_SOURCE_MUTATION"

STATE={"status":"NOT_STARTED","phase":"WAITING","started_at":None,"finished_at":None,"processed":0,"result":None,"last_error":None}
_LOCK=threading.Lock();_STARTED=False

DDL=[
"""CREATE TABLE IF NOT EXISTS pi_promotion_snapshot_clean_v711 AS SELECT * FROM pi_source_aware_clean_v705 WITH NO DATA""",
"""CREATE TABLE IF NOT EXISTS pi_promotion_snapshot_canonical_v711 AS SELECT * FROM pi_source_aware_canonical_v705 WITH NO DATA""",
"""CREATE TABLE IF NOT EXISTS pi_promotion_snapshot_links_v711 AS SELECT * FROM pi_source_aware_links_v705 WITH NO DATA""",
"""CREATE TABLE IF NOT EXISTS pi_master_properties_v711(
 master_property_id TEXT PRIMARY KEY, canonical_id TEXT NOT NULL UNIQUE, source_type TEXT, transaction_type TEXT,
 locality TEXT, city TEXT, area_value NUMERIC(18,4), area_unit TEXT, area_sqft NUMERIC(18,4), price_raw TEXT,
 price_kind TEXT, phones JSONB DEFAULT '[]'::jsonb, clean_record JSONB NOT NULL, source_count INTEGER DEFAULT 1,
 promotion_status TEXT NOT NULL DEFAULT 'PROMOTED_VALIDATED', source_version TEXT NOT NULL,
 created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW())""",
"""CREATE TABLE IF NOT EXISTS pi_master_requirements_v711(
 master_requirement_id TEXT PRIMARY KEY, canonical_id TEXT NOT NULL UNIQUE, source_type TEXT, transaction_type TEXT,
 locality TEXT, city TEXT, area_value NUMERIC(18,4), area_unit TEXT, area_sqft NUMERIC(18,4), budget_raw TEXT,
 budget_kind TEXT, phones JSONB DEFAULT '[]'::jsonb, clean_record JSONB NOT NULL, source_count INTEGER DEFAULT 1,
 promotion_status TEXT NOT NULL DEFAULT 'PROMOTED_VALIDATED', source_version TEXT NOT NULL,
 created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW())""",
"""CREATE TABLE IF NOT EXISTS pi_master_source_links_v711(
 id BIGSERIAL PRIMARY KEY, master_entity_type TEXT NOT NULL, master_id TEXT NOT NULL, canonical_id TEXT NOT NULL,
 source_type TEXT NOT NULL, source_table TEXT NOT NULL, source_pk TEXT NOT NULL, source_row_hash TEXT NOT NULL,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 UNIQUE(master_entity_type,master_id,source_table,source_pk,source_row_hash))""",
"""CREATE TABLE IF NOT EXISTS pi_master_promotion_runs_v711(
 run_id BIGSERIAL PRIMARY KEY, version TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL,
 result JSONB NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW())"""
]

def _engine(core):return getattr(core,"engine",None)
def _app(core):return getattr(core,"app",None) or core
def _route_exists(a,p):
    try:return any(getattr(r,"path",None)==p for r in a.routes)
    except:return False
def _safe(v):
    if v is None or isinstance(v,(str,int,float,bool)):return v
    if isinstance(v,Decimal):return float(v)
    if isinstance(v,dict):return {str(k):_safe(x) for k,x in v.items()}
    if isinstance(v,(list,tuple,set)):return [_safe(x) for x in v]
    return str(v)

def _parent_runtime():
    try:
        import alliance_contact_provenance_v705 as p
        return p.STATE
    except Exception:return {}

def _wait_parent_complete(timeout=900):
    start=time.time()
    while time.time()-start<timeout:
        ps=_parent_runtime()
        if ps.get("status")=="COMPLETE" and isinstance(ps.get("result"),dict):
            return ps["result"]
        if ps.get("status")=="ERROR":
            raise RuntimeError("7.0.5 parent is ERROR: "+str(ps.get("last_error")))
        STATE["phase"]="WAIT_PARENT_COMPLETE"
        time.sleep(10)
    raise RuntimeError("Timed out waiting for 7.0.5 COMPLETE")

def _snapshot(engine,parent_result):
    """One repeatable-read transaction: reconcile counts and freeze all three parent tables atomically."""
    expected=int(((parent_result or {}).get("counts") or {}).get("clean_rows") or 0)
    if expected<=0:raise RuntimeError("Parent COMPLETE result has no valid clean_rows count")

    conn=engine.connect().execution_options(isolation_level="REPEATABLE READ")
    trans=conn.begin()
    try:
        total=conn.execute(text("SELECT COUNT(*) FROM pi_source_aware_clean_v705")).scalar_one()
        ready=conn.execute(text("SELECT COUNT(*) FROM pi_source_aware_clean_v705 WHERE quality_status='PROMOTION_READY'")).scalar_one()
        props=conn.execute(text("""SELECT COUNT(*) FROM pi_source_aware_clean_v705
          WHERE quality_status='PROMOTION_READY' AND entity_type='PROPERTY_AVAILABILITY'""")).scalar_one()
        reqs=conn.execute(text("""SELECT COUNT(*) FROM pi_source_aware_clean_v705
          WHERE quality_status='PROMOTION_READY' AND entity_type='REQUIREMENT'""")).scalar_one()
        other=conn.execute(text("""SELECT COUNT(*) FROM pi_source_aware_clean_v705
          WHERE quality_status='PROMOTION_READY' AND entity_type NOT IN ('PROPERTY_AVAILABILITY','REQUIREMENT')""")).scalar_one()
        invalid=conn.execute(text("""SELECT COUNT(*) FROM pi_source_aware_clean_v705
          WHERE quality_status='PROMOTION_READY' AND
          (entity_type NOT IN ('PROPERTY_AVAILABILITY','REQUIREMENT') OR canonical_transaction='UNKNOWN'
           OR locality_clean IS NULL OR jsonb_array_length(phones)=0)""")).scalar_one()
        can=conn.execute(text("SELECT COUNT(*) FROM pi_source_aware_canonical_v705")).scalar_one()
        links=conn.execute(text("SELECT COUNT(*) FROM pi_source_aware_links_v705")).scalar_one()

        if total!=expected:
            raise RuntimeError(f"Parent snapshot mismatch: DB clean={total}, parent COMPLETE clean={expected}")
        if ready != props+reqs+other:
            raise RuntimeError(f"Parent count reconciliation failed: ready={ready}, properties={props}, requirements={reqs}, other={other}")
        if invalid!=0:
            raise RuntimeError(f"Parent promotion gate has {invalid} invalid ready rows")
        if ready<=0 or can<=0 or links<=0:
            raise RuntimeError(f"Parent nonempty gate failed: ready={ready}, canonical={can}, links={links}")

        for t in ["pi_promotion_snapshot_clean_v711","pi_promotion_snapshot_canonical_v711","pi_promotion_snapshot_links_v711"]:
            conn.execute(text(f'TRUNCATE "{t}"'))
        conn.execute(text("INSERT INTO pi_promotion_snapshot_clean_v711 SELECT * FROM pi_source_aware_clean_v705"))
        conn.execute(text("INSERT INTO pi_promotion_snapshot_canonical_v711 SELECT * FROM pi_source_aware_canonical_v705"))
        conn.execute(text("INSERT INTO pi_promotion_snapshot_links_v711 SELECT * FROM pi_source_aware_links_v705"))
        trans.commit()
        return {"expected_parent_clean":expected,"snapshot_clean":total,"ready":ready,"ready_properties":props,
                "ready_requirements":reqs,"ready_other":other,"invalid_ready_rows":invalid,
                "snapshot_canonical":can,"snapshot_links":links,"reconciled":True}
    except:
        trans.rollback();raise
    finally:conn.close()

def _candidate_rows(engine):
    with engine.connect() as c:
        rows=c.execute(text("""
          SELECT DISTINCT ON (can.canonical_id)
            can.canonical_id,can.entity_type,can.canonical_transaction,can.locality_clean,can.city_clean,
            can.area_value,can.area_unit,can.area_sqft,can.price_raw,can.price_kind,can.phones,can.clean_record,
            can.source_count,cl.source_type,cl.confidence
          FROM pi_promotion_snapshot_canonical_v711 can
          JOIN pi_promotion_snapshot_links_v711 l ON l.canonical_id=can.canonical_id
          JOIN pi_promotion_snapshot_clean_v711 cl
            ON cl.source_table=l.source_table AND cl.source_pk=l.source_pk AND cl.source_row_hash=l.source_row_hash
          WHERE cl.quality_status='PROMOTION_READY'
            AND can.entity_type IN ('PROPERTY_AVAILABILITY','REQUIREMENT')
            AND can.canonical_transaction<>'UNKNOWN'
            AND can.locality_clean IS NOT NULL
            AND jsonb_array_length(can.phones)>0
          ORDER BY can.canonical_id,cl.confidence DESC,cl.id ASC
        """)).mappings().all()
    return rows

def run_once(core):
    if not _LOCK.acquire(False):return STATE.get("result") or dict(STATE)
    try:
        STATE.update(status="RUNNING",phase="WAIT_PARENT_COMPLETE",started_at=datetime.now(timezone.utc).isoformat(),
                     finished_at=None,processed=0,result=None,last_error=None)
        engine=_engine(core)
        if engine is None:raise RuntimeError("Database engine unavailable")
        parent=_wait_parent_complete()
        with engine.begin() as c:
            for ddl in DDL:c.execute(text(ddl))
        STATE["phase"]="FREEZE_REPEATABLE_READ_SNAPSHOT"
        snap=_snapshot(engine,parent)

        STATE["phase"]="SELECT_FROZEN_CANONICAL_SET"
        rows=_candidate_rows(engine)
        if snap["ready"]>0 and len(rows)==0:
            raise RuntimeError(f"FALSE-PASS BLOCKED: {snap['ready']} ready rows but 0 canonical candidates")

        # Every candidate must be backed by at least one ready row; not every ready row must be a unique canonical.
        if len(rows)>snap["ready"]:
            raise RuntimeError(f"Candidate reconciliation failed: candidates={len(rows)} > ready={snap['ready']}")

        STATE["phase"]="PROMOTE_FROZEN_SNAPSHOT"
        counts=Counter();source_counts=Counter()
        for r in rows:
            d=dict(r);cid=str(d["canonical_id"]);etype=d["entity_type"]
            mid=("MP-" if etype=="PROPERTY_AVAILABILITY" else "MR-")+hashlib.sha256(cid.encode()).hexdigest()[:16].upper()
            params={"mid":mid,"cid":cid,"st":d.get("source_type"),"tx":d.get("canonical_transaction"),
                    "loc":d.get("locality_clean"),"city":d.get("city_clean"),"av":d.get("area_value"),
                    "au":d.get("area_unit"),"asq":d.get("area_sqft"),"pr":d.get("price_raw"),"pk":d.get("price_kind"),
                    "ph":json.dumps(_safe(d.get("phones") or []),ensure_ascii=False),
                    "clean":json.dumps(_safe(d.get("clean_record") or {}),ensure_ascii=False),
                    "sc":d.get("source_count") or 1,"sv":VERSION}
            if etype=="PROPERTY_AVAILABILITY":
                q="""INSERT INTO pi_master_properties_v711(master_property_id,canonical_id,source_type,transaction_type,locality,city,
                area_value,area_unit,area_sqft,price_raw,price_kind,phones,clean_record,source_count,promotion_status,source_version)
                VALUES(:mid,:cid,:st,:tx,:loc,:city,:av,:au,:asq,:pr,:pk,CAST(:ph AS JSONB),CAST(:clean AS JSONB),:sc,'PROMOTED_VALIDATED',:sv)
                ON CONFLICT(canonical_id) DO UPDATE SET source_type=EXCLUDED.source_type,transaction_type=EXCLUDED.transaction_type,
                locality=EXCLUDED.locality,city=EXCLUDED.city,area_value=EXCLUDED.area_value,area_unit=EXCLUDED.area_unit,
                area_sqft=EXCLUDED.area_sqft,price_raw=EXCLUDED.price_raw,price_kind=EXCLUDED.price_kind,phones=EXCLUDED.phones,
                clean_record=EXCLUDED.clean_record,source_count=EXCLUDED.source_count,source_version=EXCLUDED.source_version,updated_at=NOW()"""
                counts["properties"]+=1
            else:
                q="""INSERT INTO pi_master_requirements_v711(master_requirement_id,canonical_id,source_type,transaction_type,locality,city,
                area_value,area_unit,area_sqft,budget_raw,budget_kind,phones,clean_record,source_count,promotion_status,source_version)
                VALUES(:mid,:cid,:st,:tx,:loc,:city,:av,:au,:asq,:pr,:pk,CAST(:ph AS JSONB),CAST(:clean AS JSONB),:sc,'PROMOTED_VALIDATED',:sv)
                ON CONFLICT(canonical_id) DO UPDATE SET source_type=EXCLUDED.source_type,transaction_type=EXCLUDED.transaction_type,
                locality=EXCLUDED.locality,city=EXCLUDED.city,area_value=EXCLUDED.area_value,area_unit=EXCLUDED.area_unit,
                area_sqft=EXCLUDED.area_sqft,budget_raw=EXCLUDED.budget_raw,budget_kind=EXCLUDED.budget_kind,phones=EXCLUDED.phones,
                clean_record=EXCLUDED.clean_record,source_count=EXCLUDED.source_count,source_version=EXCLUDED.source_version,updated_at=NOW()"""
                counts["requirements"]+=1
            with engine.begin() as c:c.execute(text(q),params)

            with engine.begin() as c:
                links=c.execute(text("""SELECT source_type,source_table,source_pk,source_row_hash
                  FROM pi_promotion_snapshot_links_v711 WHERE canonical_id=:cid"""),{"cid":cid}).mappings().all()
                for ln in links:
                    c.execute(text("""INSERT INTO pi_master_source_links_v711(master_entity_type,master_id,canonical_id,source_type,source_table,source_pk,source_row_hash)
                      VALUES(:et,:mid,:cid,:st,:tb,:pk,:rh) ON CONFLICT DO NOTHING"""),
                      {"et":"PROPERTY" if etype=="PROPERTY_AVAILABILITY" else "REQUIREMENT","mid":mid,"cid":cid,
                       "st":ln["source_type"],"tb":ln["source_table"],"pk":ln["source_pk"],"rh":ln["source_row_hash"]})
                    source_counts[ln["source_type"]]+=1
            STATE["processed"]+=1

        STATE["phase"]="INTEGRITY_AUDIT"
        with engine.connect() as c:
            mp=c.execute(text("SELECT COUNT(*) FROM pi_master_properties_v711")).scalar_one()
            mr=c.execute(text("SELECT COUNT(*) FROM pi_master_requirements_v711")).scalar_one()
            ml=c.execute(text("SELECT COUNT(*) FROM pi_master_source_links_v711")).scalar_one()
            badp=c.execute(text("""SELECT COUNT(*) FROM pi_master_properties_v711
              WHERE transaction_type='UNKNOWN' OR locality IS NULL OR jsonb_array_length(phones)=0""")).scalar_one()
            badr=c.execute(text("""SELECT COUNT(*) FROM pi_master_requirements_v711
              WHERE transaction_type='UNKNOWN' OR locality IS NULL OR jsonb_array_length(phones)=0""")).scalar_one()
        promoted=mp+mr
        gate=(len(rows)>0 and promoted>0 and badp==0 and badr==0 and promoted==len(rows))
        if not gate:
            raise RuntimeError(f"Final integrity gate failed: candidates={len(rows)}, master={promoted}, bad_properties={badp}, bad_requirements={badr}")

        result={"version":VERSION,"mode":MODE,"status":"COMPLETE",
          "snapshot_gate":snap,
          "promotion":{"canonical_candidates":len(rows),"properties_promoted":counts["properties"],
                       "requirements_promoted":counts["requirements"],"master_properties_total":mp,
                       "master_requirements_total":mr,"master_source_links_total":ml,
                       "source_link_counts":dict(source_counts)},
          "integrity":{"false_empty_pass_impossible":True,"ready_reconciled":True,"repeatable_read_snapshot":True,
                       "parent_complete_required":True,"invalid_master_properties":badp,"invalid_master_requirements":badr,
                       "gate":"PASS"},
          "safety":{"raw_source_mutations":0,"gold_mutations":0,"champion_mutations":0,"legacy_master_mutations":0,
                    "ai_calls_used":0},
          "next_step":"AUDIT_V711_MASTER_COUNTS_THEN_CONNECT_SEARCH_MATCH"}
        with engine.begin() as c:
            c.execute(text("INSERT INTO pi_master_promotion_runs_v711(version,mode,status,result) VALUES(:v,:m,'COMPLETE',CAST(:r AS JSONB))"),
                      {"v":VERSION,"m":MODE,"r":json.dumps(result,ensure_ascii=False)})
        STATE.update(status="COMPLETE",phase="COMPLETE",finished_at=datetime.now(timezone.utc).isoformat(),result=result)
        return result
    except Exception as exc:
        STATE.update(status="ERROR",phase="ERROR",finished_at=datetime.now(timezone.utc).isoformat(),
                     last_error=f"{type(exc).__name__}: {exc}")
        return dict(STATE)
    finally:_LOCK.release()

def status(core):return STATE.get("result") or dict(STATE)
def dashboard(core):
    s=status(core);p=s.get("promotion") or {};g=s.get("integrity") or {};sn=s.get("snapshot_gate") or {}
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta http-equiv='refresh' content='10'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>Promotion Integrity 7.1.1</title>
<style>body{{font-family:Arial;background:#f5f7fb;color:#172033;margin:0}}header{{background:#102235;color:white;padding:18px}}
.wrap{{max-width:1450px;margin:auto;padding:18px}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(170px,1fr));gap:10px}}
.card{{background:white;border:1px solid #e2e8f0;border-radius:12px;padding:14px;margin-bottom:12px}}b.num{{font-size:24px;display:block}}
pre{{white-space:pre-wrap;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px;overflow:auto}}</style></head>
<body><header><b>Alliance Promotion Integrity Closure 7.1.1</b><br><small>Parent COMPLETE · frozen repeatable-read snapshot · reconciled counts · false empty PASS blocked</small></header>
<div class='wrap'><div class='card'><b>{html.escape(str(s.get("status")))}</b> · Phase {html.escape(str(s.get("phase")))}</div>
<div class='grid'><div class='card'>Frozen clean<b class='num'>{sn.get("snapshot_clean","-")}</b></div>
<div class='card'>Ready<b class='num'>{sn.get("ready","-")}</b></div><div class='card'>Candidates<b class='num'>{p.get("canonical_candidates","-")}</b></div>
<div class='card'>Master properties<b class='num'>{p.get("master_properties_total","-")}</b></div>
<div class='card'>Master requirements<b class='num'>{p.get("master_requirements_total","-")}</b></div>
<div class='card'>Evidence links<b class='num'>{p.get("master_source_links_total","-")}</b></div><div class='card'>Gate<b class='num'>{g.get("gate","-")}</b></div></div>
<pre>{html.escape(json.dumps(s,ensure_ascii=False,indent=2))}</pre></div></body></html>"""
def register(core):
    app=_app(core)
    if not _route_exists(app,"/api/property-brain/promotion-integrity-v711/status"):
        @app.get("/api/property-brain/promotion-integrity-v711/status")
        def _s():return status(core)
    if not _route_exists(app,"/property-brain/promotion-integrity-v711"):
        @app.get("/property-brain/promotion-integrity-v711",response_class=HTMLResponse)
        def _p():return HTMLResponse(dashboard(core))
def _runner(core):
    time.sleep(75);run_once(core)
def start(core):
    global _STARTED
    register(core)
    if not _STARTED:
        _STARTED=True;threading.Thread(target=_runner,args=(core,),daemon=True,name="promotion-integrity-v711").start()
    return STATE
