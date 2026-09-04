from __future__ import annotations
import html,json,threading,time
from datetime import datetime,timezone
from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

VERSION="7.2.1-ALLIANCE-AUTOMATED-END-TO-END-ACCEPTANCE"
MODE="READ_ONLY_REAL_DATA_PLUS_ROLLBACK_WRITE_PROBES_ROUTE_DB_AREA_MONEY_MATCH_VERIFY_PRIVACY_NO_PERSISTENT_TEST_MUTATION"
STATE={"status":"NOT_STARTED","phase":"WAITING","started_at":None,"finished_at":None,"result":None,"last_error":None}
_LOCK=threading.Lock();_STARTED=False

DDL=[r"""CREATE TABLE IF NOT EXISTS pi_acceptance_runs_v721(
 run_id BIGSERIAL PRIMARY KEY,version TEXT NOT NULL,status TEXT NOT NULL,passed INTEGER NOT NULL,failed INTEGER NOT NULL,
 result JSONB NOT NULL,created_at TIMESTAMPTZ DEFAULT NOW())"""]

def _engine(core):return getattr(core,"engine",None)
def _app(core):return getattr(core,"app",None) or core
def _role(core,req):
    fn=getattr(core,"need_login",None);return fn(req) if fn else "team"
def _route_exists(app,path,method=None):
    base=path.split("?",1)[0]
    for r in getattr(app,"routes",[]):
        if getattr(r,"path",None)==base:
            if method is None or method.upper() in (getattr(r,"methods",set()) or set()):return True
    return False
def _case(name,ok,detail,category="GENERAL",critical=True):
    return {"name":name,"category":category,"critical":critical,"status":"PASS" if ok else "FAIL","detail":detail}

def _table_exists(c,t):return bool(c.execute(text("SELECT to_regclass(:t) IS NOT NULL"),{"t":t}).scalar())
def _cols(c,t):
    return {r[0] for r in c.execute(text("""SELECT column_name FROM information_schema.columns
      WHERE table_schema=current_schema() AND table_name=:t"""),{"t":t}).all()}

def run_once(core):
    if not _LOCK.acquire(False):return STATE.get("result") or dict(STATE)
    tests=[]
    try:
        STATE.update(status="RUNNING",phase="FOUNDATION",started_at=datetime.now(timezone.utc).isoformat(),finished_at=None,result=None,last_error=None)
        engine=_engine(core);app=_app(core)
        if engine is None:raise RuntimeError("Database engine unavailable")
        with engine.begin() as c:
            for ddl in DDL:c.execute(text(ddl))

        # 1. Route contract: actual registration, including APIs used by workflow.
        STATE["phase"]="ROUTES"
        routes=[
          ("Command Centre","/alliance","GET"),("Master Properties","/alliance/properties","GET"),
          ("Master Requirements","/alliance/requirements","GET"),("Matcher","/alliance/matcher","GET"),
          ("Health API","/api/v7.2/health","GET"),("Properties API","/api/v7.2/properties","GET"),
          ("Requirements API","/api/v7.2/requirements","GET"),("Verify API","/api/v7.2/verify/{entity_type}/{canonical_id}","POST"),
          ("Match API","/api/v7.2/match/{requirement_id}","POST"),("Client Draft API","/api/v7.2/client-draft/{requirement_id}","GET"),
          ("Add Property","/property-manual","GET"),("Workspace","/workspace","GET"),("Status","/status-page","GET"),
          ("7.1.1 Integrity","/property-brain/promotion-integrity-v711","GET")]
        for label,path,method in routes:
            ok=_route_exists(app,path,method)
            tests.append(_case("route:"+label,ok,f"{method} {path} registered={ok}","ROUTES"))

        # 2. Database/table/column contract.
        STATE["phase"]="DATABASE"
        with engine.connect() as c:
            expected={
              "pi_master_properties_v711":{"canonical_id","transaction_type","locality","area_sqft","price_raw","price_kind","phones","clean_record"},
              "pi_master_requirements_v711":{"canonical_id","transaction_type","locality","area_sqft","budget_raw","budget_kind","phones","clean_record"},
              "pi_master_source_links_v711":{"canonical_id","source_type","source_table","source_pk","source_row_hash"},
              "pi_master_workflow_v720":{"canonical_id","entity_type","verification_status","availability_status"},
              "pi_master_matches_v720":{"requirement_canonical_id","property_canonical_id","match_score","match_reasons"}
            }
            for t,need in expected.items():
                exists=_table_exists(c,t);got=_cols(c,t) if exists else set();missing=sorted(need-got)
                tests.append(_case("schema:"+t,exists and not missing,f"exists={exists}; missing={missing}","DATABASE"))
            pc=c.execute(text("SELECT COUNT(*) FROM pi_master_properties_v711")).scalar_one()
            rc=c.execute(text("SELECT COUNT(*) FROM pi_master_requirements_v711")).scalar_one()
            lc=c.execute(text("SELECT COUNT(*) FROM pi_master_source_links_v711")).scalar_one()
            tests.append(_case("master properties nonempty",pc>0,f"properties={pc}","DATABASE"))
            tests.append(_case("master requirements nonempty",rc>0,f"requirements={rc}","DATABASE"))
            tests.append(_case("source evidence nonempty",lc>=pc+rc,f"source_links={lc}; canonical={pc+rc}","DATABASE"))

        # 3. Import and execute 7.2 business functions against real master data.
        STATE["phase"]="BUSINESS_LOGIC"
        import alliance_master_integration_v720 as v720
        counts=v720._counts(engine)
        tests.append(_case("7.2 counts reconcile",counts["properties"]==pc and counts["requirements"]==rc,
                           f"7.2={counts}; DB properties={pc}, requirements={rc}","BUSINESS_LOGIC"))
        props=v720._search_properties(engine,limit=min(pc,200))
        reqs=v720._search_requirements(engine,limit=min(rc,100))
        tests.append(_case("property search returns real data",len(props)>0,f"returned={len(props)}","SEARCH"))
        tests.append(_case("requirement search returns real data",len(reqs)>0,f"returned={len(reqs)}","SEARCH"))

        # 4. Area conversion exact deterministic checks + real-data coverage.
        a=v720._area_views(43560)
        area_ok=abs(a["sqft"]-43560)<.01 and abs(a["sqyd"]-4840)<.01 and abs(a["acre"]-1)<.0001 and abs(a["sqm"]-4046.8564)<.1
        tests.append(_case("area conversion mathematics",area_ok,f"43560 sqft => {a}","AREA"))
        area_real=next((p for p in props if p.get("area_sqft")),None)
        tests.append(_case("real property area decoration",bool(area_real and area_real.get("area_sqyd") is not None and area_real.get("area_sqm") is not None and area_real.get("area_acre") is not None),
                           "sample="+json.dumps({k:area_real.get(k) for k in ["canonical_id","area_sqft_display","area_sqyd","area_sqm","area_acre"]},default=str) if area_real else "no area-bearing sample","AREA",False))

        # 5. Money separation rules.
        sale=v720._money("5 Cr","SALE_AMOUNT","SALE");rent=v720._money("2 Lac/month","RENT_AMOUNT","RENT")
        tests.append(_case("sale/rent separation",sale==("5 Cr",None) and rent==(None,"2 Lac/month"),f"sale={sale}; rent={rent}","MONEY"))
        bad_money=sum(1 for p in props if p.get("sale_amount") and p.get("rent_amount"))
        tests.append(_case("no dual sale+rent display in sample",bad_money==0,f"dual_money_rows={bad_money}/{len(props)}","MONEY",False))

        # 6. Privacy: generated client text must never contain source phones.
        sample_prop=next((p for p in props if p.get("phones")),None)
        privacy_ok=False;privacy_detail="no phone-bearing property sample"
        if sample_prop:
            msg=v720._client_message({},[{"property":sample_prop}])
            phone_tokens=[str(x) for x in (sample_prop.get("phones") or []) if str(x)]
            privacy_ok=all(x not in msg for x in phone_tokens)
            privacy_detail=f"phones_tested={len(phone_tokens)}; leaked={not privacy_ok}"
        tests.append(_case("outbound client message strips contacts",privacy_ok,privacy_detail,"PRIVACY"))

        # 7. Matcher executes on a real requirement without persisting test matches: transaction rollback.
        match_count=0;match_error=None
        if reqs:
            rid=reqs[0]["canonical_id"]
            # Score engine itself is pure; run across real property sample first.
            scored=[]
            for p in props:
                s,reasons=v720._score(reqs[0],p)
                if s>=35:scored.append((s,reasons,p["canonical_id"]))
            match_count=len(scored)
            tests.append(_case("matcher scoring executes",True,f"requirement={rid}; qualifying sample matches={match_count}","MATCHER"))
        else:
            tests.append(_case("matcher scoring executes",False,"no master requirement available","MATCHER"))

        # 8. Verification write SQL tested in a rollback transaction. No real workflow mutation.
        STATE["phase"]="ROLLBACK_WRITE_PROBES"
        write_probe=False
        if props:
            cid=props[0]["canonical_id"]
            conn=engine.connect();tx=conn.begin()
            try:
                conn.execute(text("""INSERT INTO pi_master_workflow_v720(canonical_id,entity_type,verification_status,verified_at,verified_by,availability_status)
                  VALUES(:id,'PROPERTY','VERIFIED',NOW(),'V721_ACCEPTANCE_TEST','AVAILABLE')
                  ON CONFLICT(canonical_id) DO UPDATE SET verification_status='VERIFIED',verified_at=NOW(),verified_by='V721_ACCEPTANCE_TEST',updated_at=NOW()"""),{"id":cid})
                got=conn.execute(text("SELECT verification_status FROM pi_master_workflow_v720 WHERE canonical_id=:id"),{"id":cid}).scalar()
                write_probe=(got=="VERIFIED")
            finally:
                tx.rollback();conn.close()
        tests.append(_case("verification write path rollback probe",write_probe,"transaction executed then rolled back; persistent mutation=0","WORKFLOW"))

        # 9. Match persistence SQL tested in rollback transaction using real IDs.
        match_probe=False
        if reqs and props:
            rid=reqs[0]["canonical_id"];pid=props[0]["canonical_id"]
            conn=engine.connect();tx=conn.begin()
            try:
                conn.execute(text("""INSERT INTO pi_master_matches_v720(requirement_canonical_id,property_canonical_id,match_score,match_reasons,status,updated_at)
                  VALUES(:r,:p,50,CAST(:why AS JSONB),'READY_FOR_REVIEW',NOW())
                  ON CONFLICT(requirement_canonical_id,property_canonical_id) DO UPDATE SET match_score=50,match_reasons=EXCLUDED.match_reasons,updated_at=NOW()"""),
                  {"r":rid,"p":pid,"why":json.dumps(["V721_ROLLBACK_PROBE"])})
                got=conn.execute(text("""SELECT match_score FROM pi_master_matches_v720 WHERE requirement_canonical_id=:r AND property_canonical_id=:p"""),
                                 {"r":rid,"p":pid}).scalar()
                match_probe=(got is not None)
            finally:tx.rollback();conn.close()
        tests.append(_case("match write path rollback probe",match_probe,"transaction executed then rolled back; persistent mutation=0","MATCHER"))

        # 10. Canonical/source integrity and privacy columns.
        STATE["phase"]="INTEGRITY"
        with engine.connect() as c:
            dup_p=c.execute(text("SELECT COUNT(*)-COUNT(DISTINCT canonical_id) FROM pi_master_properties_v711")).scalar_one()
            dup_r=c.execute(text("SELECT COUNT(*)-COUNT(DISTINCT canonical_id) FROM pi_master_requirements_v711")).scalar_one()
            orphan=c.execute(text("""SELECT COUNT(*) FROM pi_master_source_links_v711 l
              WHERE NOT EXISTS(SELECT 1 FROM pi_master_properties_v711 p WHERE p.canonical_id=l.canonical_id)
                AND NOT EXISTS(SELECT 1 FROM pi_master_requirements_v711 r WHERE r.canonical_id=l.canonical_id)""")).scalar_one()
        tests.append(_case("canonical property uniqueness",dup_p==0,f"duplicate canonical property ids={dup_p}","INTEGRITY"))
        tests.append(_case("canonical requirement uniqueness",dup_r==0,f"duplicate canonical requirement ids={dup_r}","INTEGRITY"))
        tests.append(_case("source links have master parent",orphan==0,f"orphan source links={orphan}","INTEGRITY"))

        critical_fail=[x for x in tests if x["critical"] and x["status"]=="FAIL"]
        passed=sum(x["status"]=="PASS" for x in tests);failed=len(tests)-passed
        result={"version":VERSION,"mode":MODE,"status":"PASS" if not critical_fail else "FAIL",
                "summary":{"tests":len(tests),"passed":passed,"failed":failed,"critical_failed":len(critical_fail),
                           "master_properties":pc,"master_requirements":rc,"source_links":lc},
                "categories":dict(__import__("collections").Counter(x["category"] for x in tests if x["status"]=="PASS")),
                "tests":tests,
                "safety":{"persistent_test_property_writes":0,"persistent_test_requirement_writes":0,"persistent_verification_test_writes":0,
                          "persistent_match_test_writes":0,"raw_source_mutations":0,"gold_mutations":0,"champion_mutations":0,"ai_calls_used":0},
                "certification":"V7_2_OPERATIONAL_ACCEPTANCE_PASS" if not critical_fail else "V7_2_OPERATIONAL_ACCEPTANCE_HOLD",
                "next_step":"MAKE_ALLIANCE_PRIMARY_WORKSPACE_AND_ADD_ACTION_CONTROLS" if not critical_fail else "REPAIR_ONLY_EXACT_FAILED_TESTS"}
        with engine.begin() as c:
            c.execute(text("""INSERT INTO pi_acceptance_runs_v721(version,status,passed,failed,result)
              VALUES(:v,:s,:p,:f,CAST(:r AS JSONB))"""),{"v":VERSION,"s":result["status"],"p":passed,"f":failed,"r":json.dumps(result,ensure_ascii=False)})
        STATE.update(status=result["status"],phase="COMPLETE",finished_at=datetime.now(timezone.utc).isoformat(),result=result)
        return result
    except Exception as exc:
        STATE.update(status="ERROR",phase="ERROR",finished_at=datetime.now(timezone.utc).isoformat(),last_error=f"{type(exc).__name__}: {exc}")
        return dict(STATE)
    finally:_LOCK.release()

def dashboard(core):
    s=STATE.get("result") or STATE;summary=s.get("summary") or {};tests=s.get("tests") or []
    rows="".join(f"<tr><td>{html.escape(x['category'])}</td><td>{html.escape(x['name'])}</td><td class='{'ok' if x['status']=='PASS' else 'bad'}'>{x['status']}</td><td>{html.escape(str(x['detail']))}</td></tr>" for x in tests)
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta http-equiv='refresh' content='15'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Alliance 7.2.1 Acceptance</title><style>body{{font-family:Arial;background:#f5f7fb;color:#172033;margin:0}}header{{background:#102235;color:white;padding:18px}}
.wrap{{max-width:1500px;margin:auto;padding:18px}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(170px,1fr));gap:10px}}.card{{background:white;border:1px solid #e2e8f0;border-radius:12px;padding:14px;margin-bottom:12px}}
.num{{font-size:26px;font-weight:800}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{padding:8px;border-bottom:1px solid #edf0f4;text-align:left}}.ok{{color:#08783e;font-weight:800}}.bad{{color:#b42318;font-weight:800}}pre{{white-space:pre-wrap;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px}}</style></head>
<body><header><b>Alliance Automated End-to-End Acceptance · 7.2.1</b><br><small>Real master data · rollback write probes · no persistent test mutations</small></header><div class='wrap'>
<div class='grid'><div class='card'>Status<div class='num'>{html.escape(str(s.get('status')))}</div></div><div class='card'>Passed<div class='num'>{summary.get('passed','-')}</div></div>
<div class='card'>Failed<div class='num'>{summary.get('failed','-')}</div></div><div class='card'>Critical Failed<div class='num'>{summary.get('critical_failed','-')}</div></div></div>
<div class='card'><table><tr><th>Category</th><th>Test</th><th>Result</th><th>Evidence</th></tr>{rows}</table></div>
<pre>{html.escape(json.dumps(s,ensure_ascii=False,indent=2))}</pre></div></body></html>"""

def register(core):
    app=_app(core)
    if not _route_exists(app,"/api/property-brain/acceptance-v721/status"):
        @app.get("/api/property-brain/acceptance-v721/status")
        def _status(req:Request):
            _role(core,req);return STATE.get("result") or STATE
    if not _route_exists(app,"/property-brain/acceptance-v721"):
        @app.get("/property-brain/acceptance-v721",response_class=HTMLResponse)
        def _page(req:Request):
            _role(core,req);return HTMLResponse(dashboard(core))
def _runner(core):
    time.sleep(45);run_once(core)
def start(core):
    global _STARTED
    register(core)
    if not _STARTED:
        _STARTED=True;threading.Thread(target=_runner,args=(core,),daemon=True,name="acceptance-v721").start()
    return STATE
