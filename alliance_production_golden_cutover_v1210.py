
from __future__ import annotations

import hashlib, html, json, re, threading, time, traceback
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

VERSION = "12.1.0.2-ROUTE-FIRST-DEPENDENCY-GUARD"
SETTLED = "pi_magazine_settled_v12009"
WORKABLE = "pi_magazine_workable_v12009"
GOLD = "pi_magazine_golden_master_v12009"
REVIEW = "pi_magazine_review_v12009"
BRIDGE = "pi_magazine_master_bridge_v1210"
RUNS = "pi_magazine_cutover_runs_v1210"
MATCHER_VIEW = "pi_master_properties_matcher_v1210"
LOCK_KEY = 121000001

STATE = {
    "status":"IDLE","phase":"WAITING","started_at":None,"completed_at":None,
    "raw_rows":0,"settled_rows":0,"workable_rows":0,"gold_rows":0,"review_rows":0,
    "bridge_candidates":0,"bridge_promoted":0,"bridge_skipped_no_transaction":0,
    "bridge_skipped_no_location":0,"matcher_magazine_rows":0,
    "source_page_cutover":False,"matcher_cutover":False,"stale":False,
    "error":None,"details":{}
}
LOCK = threading.Lock()
PHONE_RE = re.compile(r"(?<!\d)([6-9]\d{9}|0?11[-\s]?\d{7,8})(?!\d)")
NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")

def _now(): return datetime.now(timezone.utc).isoformat()
def _app(core): return getattr(core,"app",None) or core
def _engine(core): return getattr(core,"engine",None)
def _login(core,req):
    fn=getattr(core,"need_login",None)
    return fn(req) if fn else "team"
def _safe(v):
    if v is None or isinstance(v,(str,int,float,bool)): return v
    if isinstance(v,Decimal): return float(v)
    if isinstance(v,datetime): return v.isoformat()
    if isinstance(v,dict): return {str(k):_safe(x) for k,x in v.items()}
    if isinstance(v,(list,tuple,set)): return [_safe(x) for x in v]
    return str(v)
def _norm(v): return re.sub(r"\s+"," ",str(v or "")).strip()
def _table_exists(e,t):
    with e.connect() as c:
        return bool(c.execute(text("SELECT to_regclass(:t) IS NOT NULL"),{"t":t}).scalar())
def _txn(raw):
    vals=[]
    for k in ("transaction_type","listing_type","category","lead_type","configuration"):
        x=_norm(raw.get(k))
        if x: vals.append(x.upper())
    s=" | ".join(vals)
    if re.search(r"\b(RENT|LEASE|LEASING|TO LET)\b",s): return "RENT"
    if re.search(r"\b(SALE|SELL|SELLING|PURCHASE|BUY)\b",s): return "SALE"
    return None
def _number(v):
    if v is None:return None
    if isinstance(v,(int,float,Decimal)):return float(v)
    m=NUM_RE.search(str(v).replace(",",""))
    return float(m.group()) if m else None
def _area(raw):
    val=_number(raw.get("area") or raw.get("area_value") or raw.get("area_input"))
    unit=_norm(raw.get("area_unit") or raw.get("unit"))
    if val is None:
        desc=_norm(raw.get("original_raw_text"))
        m=re.search(r"(?i)\b(\d{2,7}(?:\.\d+)?)\s*(SQ\.?\s*FT|SQFT|FT|SQ\.?\s*YD|SQYD|YD|Y|SQ\.?\s*M|SQM|ACRE)\b",desc)
        if m:
            val=float(m.group(1));unit=m.group(2)
    if val is None:return None,None,None
    u=re.sub(r"[^A-Z]","",unit.upper())
    if u in ("FT","SQFT"): canon="Sq Ft";sqft=val
    elif u in ("Y","YD","SQYD"): canon="Sq Yd";sqft=val*9
    elif u in ("M","SQM"): canon="Sq Mtr";sqft=val*10.7639104167
    elif u=="ACRE": canon="Acre";sqft=val*43560
    else: canon=unit or None;sqft=None
    return val,canon,sqft
def _phones(raw):
    out=[]
    for k in ("valid_mobiles","valid_landlines","partial_contacts","contact_number","phone_numbers"):
        v=raw.get(k)
        if isinstance(v,list): vals=v
        elif isinstance(v,dict): vals=list(v.values())
        else: vals=[v]
        for item in vals:
            for p in PHONE_RE.findall(str(item or "")):
                p=re.sub(r"\s+","",p)
                if p not in out:out.append(p)
    if not out:
        for p in PHONE_RE.findall(_norm(raw.get("original_raw_text"))):
            p=re.sub(r"\s+","",p)
            if p not in out:out.append(p)
    return out
def _json(v):
    return json.dumps(_safe(v),ensure_ascii=False,default=str)

def _setup(e):
    with e.begin() as c:
        for t in (SETTLED,WORKABLE,GOLD,REVIEW,"pi_master_properties_v711","pi_master_source_links_v711","pi_master_workflow_v720"):
            if not c.execute(text("SELECT to_regclass(:t) IS NOT NULL"),{"t":t}).scalar():
                raise RuntimeError("Required production dependency missing: "+t)
        c.execute(text(f"""CREATE TABLE IF NOT EXISTS {BRIDGE}(
            source_id TEXT PRIMARY KEY,
            canonical_id TEXT NOT NULL UNIQUE,
            master_property_id TEXT NOT NULL UNIQUE,
            transaction_type TEXT,
            locality TEXT,
            area_sqft NUMERIC(18,4),
            promotion_status TEXT NOT NULL,
            skip_reason TEXT,
            promoted_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )"""))
        c.execute(text(f"""CREATE TABLE IF NOT EXISTS {RUNS}(
            id BIGSERIAL PRIMARY KEY,version TEXT NOT NULL,status TEXT NOT NULL,
            started_at TIMESTAMPTZ DEFAULT NOW(),completed_at TIMESTAMPTZ,
            result JSONB NOT NULL DEFAULT '{{}}'::jsonb
        )"""))

def _wait_dependencies(e, timeout=180):
    required=(SETTLED,WORKABLE,GOLD,REVIEW,"pi_master_properties_v711",
              "pi_master_source_links_v711","pi_master_workflow_v720")
    end=time.monotonic()+timeout
    last={}
    while time.monotonic()<end:
        missing=[]
        for t in required:
            try:
                if not _table_exists(e,t):
                    missing.append(t)
            except Exception:
                missing.append(t)
        last={"missing":missing}
        if not missing:
            return last
        STATE["phase"]="WAITING_FOR_DEPENDENCIES"
        STATE["details"]={"dependency_guard":True,"missing_dependencies":missing}
        time.sleep(2)
    raise RuntimeError("Production cutover dependencies not ready: "+json.dumps(last))

def _counts(e):
    with e.connect() as c:
        raw=int(c.execute(text("SELECT COUNT(*) FROM pi_magazine_master")).scalar() or 0)
        settled=int(c.execute(text(f"SELECT COUNT(*) FROM {SETTLED} WHERE version='12.0.9-WORKABLE-DATABASE-SETTLEMENT'")).scalar() or 0)
        workable=int(c.execute(text(f"SELECT COUNT(*) FROM {WORKABLE}")).scalar() or 0)
        gold=int(c.execute(text(f"SELECT COUNT(*) FROM {GOLD}")).scalar() or 0)
        review=int(c.execute(text(f"SELECT COUNT(*) FROM {REVIEW}")).scalar() or 0)
    return {"raw":raw,"settled":settled,"workable":workable,"gold":gold,"review":review}

def _wait(e,timeout=120):
    end=time.monotonic()+timeout;last={}
    while time.monotonic()<end:
        last=_counts(e)
        if last["raw"]>0 and last["settled"]==last["raw"] and last["gold"]>0:return last
        time.sleep(2)
    raise RuntimeError("12.0.9 is not fully ready: "+json.dumps(last))

def _promote_gold(e):
    with e.connect() as c:
        rows=[dict(r) for r in c.execute(text(f"""
            SELECT to_jsonb(g) AS raw
            FROM {GOLD} g
            ORDER BY source_id
        """)).mappings().all()]
    promoted=skip_tx=skip_loc=0
    for wrapper in rows:
        raw=wrapper["raw"]
        if isinstance(raw,str): raw=json.loads(raw)
        sid=_norm(raw.get("source_id"))
        loc=_norm(raw.get("settled_location"))
        tx=_txn(raw)
        if not sid: continue
        cid="MAG1210-"+hashlib.sha256(sid.encode()).hexdigest()[:24].upper()
        mid="MP-"+hashlib.sha256(cid.encode()).hexdigest()[:16].upper()
        if not loc:
            skip_loc+=1
            with e.begin() as c:
                c.execute(text(f"""INSERT INTO {BRIDGE}(source_id,canonical_id,master_property_id,promotion_status,skip_reason,updated_at)
                    VALUES(:sid,:cid,:mid,'SKIPPED','NO_SETTLED_LOCATION',NOW())
                    ON CONFLICT(source_id) DO UPDATE SET promotion_status='SKIPPED',skip_reason='NO_SETTLED_LOCATION',updated_at=NOW()"""),
                    {"sid":sid,"cid":cid,"mid":mid})
            continue
        if not tx:
            skip_tx+=1
            with e.begin() as c:
                c.execute(text(f"""INSERT INTO {BRIDGE}(source_id,canonical_id,master_property_id,locality,promotion_status,skip_reason,updated_at)
                    VALUES(:sid,:cid,:mid,:loc,'SKIPPED','NO_PROVEN_TRANSACTION',NOW())
                    ON CONFLICT(source_id) DO UPDATE SET locality=EXCLUDED.locality,promotion_status='SKIPPED',skip_reason='NO_PROVEN_TRANSACTION',updated_at=NOW()"""),
                    {"sid":sid,"cid":cid,"mid":mid,"loc":loc})
            continue
        av,au,asq=_area(raw)
        phones=_phones(raw)
        price=_norm(raw.get("price") or raw.get("amount_raw") or raw.get("rent_sale_amount"))
        pk="SALE_AMOUNT" if tx=="SALE" else "RENT_AMOUNT"
        desc=_norm(raw.get("original_raw_text"))
        clean={
            "source_id":sid,"source":"MAGAZINE_SETTLED_12.0.9","description":desc,
            "settled_location":loc,"settled_confidence":raw.get("settled_confidence"),
            "settlement_rule":raw.get("settlement_rule"),"settled_status":"GOLD",
            "category":raw.get("category"),"listing_type":raw.get("listing_type"),
            "property_type":raw.get("configuration") or raw.get("property_type"),
            "floor":raw.get("floor"),"contact_name":raw.get("contact_name_company"),
            "price":raw.get("price"),"area":raw.get("area"),"area_unit":raw.get("area_unit"),
            "raw_record_status":raw.get("record_status"),
            "availability_verification":"REQUIRES_LIVE_VERIFICATION_BEFORE_CLIENT_SEND"
        }
        params={"mid":mid,"cid":cid,"tx":tx,"loc":loc,"av":av,"au":au,"asq":asq,
                "pr":price or None,"pk":pk,"ph":_json(phones),"clean":_json(clean),
                "sid":sid,"hash":"SETTLED12009-"+hashlib.sha256(sid.encode()).hexdigest()}
        with e.begin() as c:
            c.execute(text("""INSERT INTO pi_master_properties_v711(
                master_property_id,canonical_id,source_type,transaction_type,locality,city,
                area_value,area_unit,area_sqft,price_raw,price_kind,phones,clean_record,
                source_count,promotion_status,source_version,created_at,updated_at)
                VALUES(:mid,:cid,'MAGAZINE',:tx,:loc,NULL,:av,:au,:asq,:pr,:pk,
                       CAST(:ph AS JSONB),CAST(:clean AS JSONB),1,'PROMOTED_VALIDATED',:ver,NOW(),NOW())
                ON CONFLICT(canonical_id) DO UPDATE SET
                  source_type='MAGAZINE',transaction_type=EXCLUDED.transaction_type,
                  locality=EXCLUDED.locality,area_value=EXCLUDED.area_value,area_unit=EXCLUDED.area_unit,
                  area_sqft=EXCLUDED.area_sqft,price_raw=EXCLUDED.price_raw,price_kind=EXCLUDED.price_kind,
                  phones=EXCLUDED.phones,clean_record=EXCLUDED.clean_record,
                  promotion_status='PROMOTED_VALIDATED',source_version=EXCLUDED.source_version,updated_at=NOW()
            """),{**params,"ver":VERSION})
            c.execute(text("""INSERT INTO pi_master_source_links_v711(
                master_entity_type,master_id,canonical_id,source_type,source_table,source_pk,source_row_hash)
                VALUES('PROPERTY',:mid,:cid,'MAGAZINE','pi_magazine_master',:sid,:hash)
                ON CONFLICT DO NOTHING"""),params)
            # Gold is certified extraction, NOT live availability. Keep availability UNKNOWN until team verifies.
            c.execute(text("""INSERT INTO pi_master_workflow_v720(
                canonical_id,entity_type,verification_status,availability_status,updated_at)
                VALUES(:cid,'PROPERTY','UNVERIFIED','UNKNOWN',NOW())
                ON CONFLICT(canonical_id) DO NOTHING"""),{"cid":cid})
            c.execute(text(f"""INSERT INTO {BRIDGE}(
                source_id,canonical_id,master_property_id,transaction_type,locality,area_sqft,
                promotion_status,skip_reason,promoted_at,updated_at)
                VALUES(:sid,:cid,:mid,:tx,:loc,:asq,'PROMOTED',NULL,NOW(),NOW())
                ON CONFLICT(source_id) DO UPDATE SET canonical_id=EXCLUDED.canonical_id,
                  master_property_id=EXCLUDED.master_property_id,transaction_type=EXCLUDED.transaction_type,
                  locality=EXCLUDED.locality,area_sqft=EXCLUDED.area_sqft,
                  promotion_status='PROMOTED',skip_reason=NULL,promoted_at=NOW(),updated_at=NOW()
            """),params)
        promoted+=1
    return {"candidates":len(rows),"promoted":promoted,"skip_tx":skip_tx,"skip_loc":skip_loc}

def _matcher_view(e):
    # PostgreSQL CREATE VIEW cannot use the runtime bind parameter used previously.
    # VERSION is a fixed application constant, so quote it once into the DDL.
    ver_sql = str(VERSION).replace("'", "''")
    with e.begin() as c:
        c.execute(text(f"DROP VIEW IF EXISTS {MATCHER_VIEW}"))
        c.execute(text(f"""CREATE VIEW {MATCHER_VIEW} AS
          SELECT p.*
          FROM pi_master_properties_v711 p
          WHERE p.source_version='{ver_sql}'
             OR NOT EXISTS (
                SELECT 1 FROM pi_master_source_links_v711 lm
                WHERE lm.canonical_id=p.canonical_id AND lm.source_table='pi_magazine_master'
             )
             OR EXISTS (
                SELECT 1 FROM pi_master_source_links_v711 lo
                WHERE lo.canonical_id=p.canonical_id AND lo.source_table<>'pi_magazine_master'
             )
        """))

def _install_matcher_patch(e):
    import alliance_master_integration_v720 as v720
    def search_properties(engine,q="",tx="",limit=500):
        wh=["1=1"];params={"n":limit}
        if q:
            wh.append("(COALESCE(p.locality,'') ILIKE :q OR COALESCE(p.city,'') ILIKE :q OR COALESCE(p.clean_record::text,'') ILIKE :q)")
            params["q"]="%"+q+"%"
        if tx:
            wh.append("p.transaction_type=:tx");params["tx"]=tx.upper()
        sql=f"""SELECT p.*,COALESCE(w.verification_status,'UNVERIFIED') verification_status,
          w.availability_status,w.assigned_to
          FROM {MATCHER_VIEW} p
          LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=p.canonical_id
          WHERE {' AND '.join(wh)}
          ORDER BY CASE WHEN p.source_version=:sv THEN 0 ELSE 1 END,p.updated_at DESC LIMIT :n"""
        params["sv"]=VERSION
        with engine.connect() as c:
            rows=c.execute(text(sql),params).mappings().all()
        out=[]
        for r in rows:
            d={k:_safe(v) for k,v in dict(r).items()}
            out.append(v720._decorate_property(d))
        return out
    v720._search_properties=search_properties
    # primary workspace imports v720 dynamically inside matcher, so this patch is authoritative.
    return True

def _remove_route(app,path,methods={"GET"}):
    app.router.routes[:]=[r for r in app.router.routes if not (
        getattr(r,"path",None)==path and set(getattr(r,"methods",set()) or set()) & set(methods)
    )]

def _install_magazine_page(core):
    app=_app(core);e=_engine(core)
    _remove_route(app,"/alliance/source/magazine",{"GET"})
    def page(req:Request,q:str="",page:int=1,per_page:int=100,status:str=""):
        _login(core,req)
        page=max(1,page);per_page=max(25,min(per_page,500));off=(page-1)*per_page
        wh=["1=1"];p={"lim":per_page,"off":off}
        if q.strip():
            wh.append("to_jsonb(x)::text ILIKE :q");p["q"]="%"+q.strip()+"%"
        if status.strip().upper() in {"GOLD","SILVER"}:
            wh.append("x.settled_status=:st");p["st"]=status.strip().upper()
        with e.connect() as c:
            total=int(c.execute(text(f"SELECT COUNT(*) FROM {WORKABLE} x WHERE {' AND '.join(wh)}"),p).scalar() or 0)
            rows=c.execute(text(f"""SELECT to_jsonb(x) FROM {WORKABLE} x
                WHERE {' AND '.join(wh)}
                ORDER BY x.source_id DESC LIMIT :lim OFFSET :off"""),p).scalars().all()
            raw_count=int(c.execute(text("SELECT COUNT(*) FROM pi_magazine_master")).scalar() or 0)
            settled_count=int(c.execute(text(f"SELECT COUNT(*) FROM {SETTLED}")).scalar() or 0)
        rows=[r if isinstance(r,dict) else json.loads(r) for r in rows]
        pages=max(1,(total+per_page-1)//per_page)
        def esc(v):return html.escape("" if v is None else str(v))
        trs=[]
        for d in rows:
            sid=str(d.get("source_id") or "")
            desc=d.get("original_raw_text") or ""
            phones=d.get("valid_mobiles") or d.get("valid_landlines") or d.get("partial_contacts") or ""
            if isinstance(phones,(list,dict)):phones=json.dumps(phones,ensure_ascii=False)
            amount=d.get("price") or ""
            trs.append("<tr>"+
                f"<td>{esc(sid)}</td><td><b>{esc(d.get('settled_location'))}</b></td>"+
                f"<td>{esc(d.get('settled_status'))}<br><small>{esc(d.get('settled_confidence'))}</small></td>"+
                f"<td class='desc'>{esc(desc)}</td><td>{esc(d.get('category'))}</td>"+
                f"<td>{esc(d.get('listing_type') or d.get('configuration'))}</td>"+
                f"<td>{esc(d.get('area'))} {esc(d.get('area_unit'))}</td><td>{esc(d.get('floor'))}</td>"+
                f"<td>{esc(amount)}</td><td>{esc(d.get('contact_name_company'))}</td><td>{esc(phones)}</td>"+
                f"<td><a class='btn' href='/alliance/property-edit/magazine/{quote(sid,safe='')}'>Raw Evidence</a></td></tr>")
        stale=raw_count!=settled_count
        warn=(f"<div class='warn'>Raw rows {raw_count:,} ≠ settled rows {settled_count:,}. Run the settlement rebuild before relying on new uploads.</div>" if stale else "")
        body=f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Alliance Workable Magazine Database</title><style>
        *{{box-sizing:border-box}}body{{font:12px Arial;margin:0;background:#f5f7fa;color:#172033}}
        header{{background:#102a43;color:#fff;padding:16px 20px}}nav{{background:#fff;padding:9px;border-bottom:1px solid #ccd4dd}}
        a,.btn,button{{background:#102a43;color:white;text-decoration:none;border:0;padding:8px 10px;margin:2px;display:inline-block}}
        .wrap{{padding:12px}}.kpi{{display:inline-flex;flex-direction:column;background:white;border:1px solid #98a2b3;padding:10px 18px;margin:3px}}
        .kpi b{{font-size:23px}}.warn{{background:#fff3cd;border:1px solid #d6b656;padding:10px;margin:8px 0}}
        .search{{display:flex;gap:5px;margin:8px 0}}input,select{{padding:8px;border:1px solid #98a2b3}}
        .tablebox{{overflow:auto;max-height:72vh}}table{{border-collapse:collapse;width:max-content;min-width:100%;background:white}}
        th,td{{border:1px solid #98a2b3;padding:6px;vertical-align:top}}th{{position:sticky;top:0;background:#e9eef5}}
        td.desc{{min-width:380px;max-width:560px;white-space:pre-wrap}}small{{color:#667085}}</style></head><body>
        <header><b>Alliance CRE Intelligence OS</b><br>Workable Magazine Property Database · Golden Cutover 12.1.0</header>
        <nav><a href='/alliance/primary'>Command Centre</a><a href='/alliance/source/manual'>Manual</a>
        <a href='/alliance/source/newspaper'>Newspaper</a><a href='/alliance/source/magazine'>Magazine</a>
        <a href='/alliance/source/whatsapp'>WhatsApp</a><a href='/alliance/admin/magazine-settlement'>Settlement Admin</a>
        <a href='/api/alliance/admin/production-cutover/status'>Cutover Status</a></nav>
        <div class='wrap'>{warn}
        <div class='kpi'><b>{total:,}</b><span>Workable unique properties</span></div>
        <div class='kpi'><b>GOLD + SILVER</b><span>Review/quarantine excluded</span></div>
        <form class='search'><input name='q' value='{esc(q)}' placeholder='Search workable Magazine database'>
        <select name='status'><option value=''>Gold + Silver</option><option {'selected' if status.upper()=='GOLD' else ''}>GOLD</option>
        <option {'selected' if status.upper()=='SILVER' else ''}>SILVER</option></select>
        <select name='per_page'>{''.join(f"<option {'selected' if per_page==x else ''}>{x}</option>" for x in (50,100,200,500))}</select>
        <button>Search</button></form><div>Page {page} of {pages}</div>
        <div class='tablebox'><table><thead><tr><th>ID</th><th>Settled Location</th><th>Quality</th><th>Description</th>
        <th>Category</th><th>Type</th><th>Area</th><th>Floor</th><th>Amount</th><th>Contact</th><th>Phone</th><th>Evidence</th>
        </tr></thead><tbody>{''.join(trs) if trs else "<tr><td colspan='12'>No workable records.</td></tr>"}</tbody></table></div>
        <p><a class='btn' href='?q={quote(q)}&status={quote(status)}&per_page={per_page}&page={max(1,page-1)}'>← Previous</a>
        <a class='btn' href='?q={quote(q)}&status={quote(status)}&per_page={per_page}&page={min(pages,page+1)}'>Next →</a></p>
        </div></body></html>"""
        return HTMLResponse(body,headers={"Cache-Control":"no-store","X-Alliance-Data-Source":"pi_magazine_workable_v12009"})
    app.add_api_route("/alliance/source/magazine",page,methods=["GET"],include_in_schema=False)
    chosen=[r for r in list(app.router.routes) if getattr(r,"path",None)=="/alliance/source/magazine"]
    for r in chosen:
        try:app.router.routes.remove(r)
        except ValueError:pass
    for r in reversed(chosen):app.router.routes.insert(0,r)
    return True

def _run(core):
    e=_engine(core)
    with LOCK:
        if STATE["status"]=="RUNNING":return
        STATE.update({"status":"RUNNING","phase":"WAITING_FOR_SETTLED_DATABASE","started_at":_now(),
            "completed_at":None,"error":None})
    lc=None;run_id=None
    try:
        _wait_dependencies(e)
        _setup(e)
        lc=e.connect()
        if not bool(lc.execute(text("SELECT pg_try_advisory_lock(:k)"),{"k":LOCK_KEY}).scalar()):
            STATE.update({"status":"SKIPPED","phase":"ANOTHER_CUTOVER_RUNNING","completed_at":_now()});return
        ready=_wait(e)
        STATE.update({"raw_rows":ready["raw"],"settled_rows":ready["settled"],
                      "workable_rows":ready["workable"],"gold_rows":ready["gold"],"review_rows":ready["review"]})
        with e.begin() as c:
            run_id=c.execute(text(f"INSERT INTO {RUNS}(version,status) VALUES(:v,'RUNNING') RETURNING id"),{"v":VERSION}).scalar()
        STATE["phase"]="PROMOTING_GOLD_TO_MASTER"
        br=_promote_gold(e)
        STATE.update({"bridge_candidates":br["candidates"],"bridge_promoted":br["promoted"],
                      "bridge_skipped_no_transaction":br["skip_tx"],"bridge_skipped_no_location":br["skip_loc"]})
        STATE["phase"]="BUILDING_MATCHER_READ_MODEL"
        _matcher_view(e)
        _install_matcher_patch(e);STATE["matcher_cutover"]=True
        STATE["phase"]="CUTTING_OVER_MAGAZINE_SCREEN"
        _install_magazine_page(core);STATE["source_page_cutover"]=True
        with e.connect() as c:
            mm=int(c.execute(text(f"SELECT COUNT(*) FROM {MATCHER_VIEW} WHERE source_version=:v"),{"v":VERSION}).scalar() or 0)
            current=_counts(e)
        STATE.update({"matcher_magazine_rows":mm,"stale":current["raw"]!=current["settled"],
            "status":"PASS","phase":"COMPLETE","completed_at":_now(),
            "details":{
                "research_architecture":"raw evidence -> governed settlement -> survivorship -> golden master -> operational consumers",
                "raw_magazine_mutation":"NONE",
                "settled_v12009_mutation":"NONE",
                "live_magazine_page":WORKABLE,
                "matcher_magazine_policy":"GOLD_ONLY",
                "matcher_read_model":MATCHER_VIEW,
                "live_availability_policy":"Gold extraction remains UNVERIFIED/UNKNOWN until team confirms availability",
                "legacy_magazine_matcher_policy":"magazine-only legacy master rows excluded; mixed-source master rows retained",
                "master_bridge_table":BRIDGE,
                "source_lineage":"pi_master_source_links_v711",
                "ready_counts":ready
            }})
        if run_id:
            with e.begin() as c:
                c.execute(text(f"UPDATE {RUNS} SET status='PASS',completed_at=NOW(),result=CAST(:r AS JSONB) WHERE id=:id"),
                          {"id":run_id,"r":_json(STATE)})
    except Exception as exc:
        STATE.update({"status":"ERROR","phase":"FAILED","completed_at":_now(),"error":f"{type(exc).__name__}: {exc}",
                      "details":{"trace":traceback.format_exc()[-8000:],"raw_magazine_mutation":"NONE"}})
        if run_id:
            try:
                with e.begin() as c:
                    c.execute(text(f"UPDATE {RUNS} SET status='ERROR',completed_at=NOW(),result=CAST(:r AS JSONB) WHERE id=:id"),
                              {"id":run_id,"r":_json(STATE)})
            except Exception:pass
    finally:
        if lc is not None:
            try:lc.execute(text("SELECT pg_advisory_unlock(:k)"),{"k":LOCK_KEY})
            except Exception:pass
            try:lc.close()
            except Exception:pass

def register(core):
    app=_app(core);e=_engine(core)
    if app is None or e is None:raise RuntimeError("12.1.0 requires app + engine")
    # ROUTE-FIRST: register diagnostics before transient database dependency checks.
    @app.get("/api/alliance/admin/production-cutover/status")
    def status():
        try:
            c=_counts(e);STATE["stale"]=c["raw"]!=c["settled"]
        except Exception:pass
        return JSONResponse(STATE)
    @app.post("/api/alliance/admin/production-cutover/rebuild")
    def rebuild():
        threading.Thread(target=_run,args=(core,),daemon=True,name="production-cutover-1210").start()
        return JSONResponse({"status":"STARTED","version":VERSION})
    @app.get("/alliance/admin/production-cutover",response_class=HTMLResponse)
    def admin(req:Request):
        _login(core,req)
        s=STATE
        return HTMLResponse(f"""<html><body style='font-family:Arial;margin:30px'>
        <h1>Alliance Production Golden Cutover · 12.1.0</h1>
        <p><b>Status:</b> {html.escape(str(s['status']))} · <b>Phase:</b> {html.escape(str(s['phase']))}</p>
        <p>Raw: {s['raw_rows']:,} · Settled: {s['settled_rows']:,} · Workable: {s['workable_rows']:,} · Gold: {s['gold_rows']:,}</p>
        <p>Gold promoted to Master/Matcher: <b>{s['bridge_promoted']:,}</b> · Matcher Magazine rows: <b>{s['matcher_magazine_rows']:,}</b></p>
        <p>Skipped no transaction: {s['bridge_skipped_no_transaction']:,} · Skipped no location: {s['bridge_skipped_no_location']:,}</p>
        <p>Magazine screen cutover: {s['source_page_cutover']} · Matcher cutover: {s['matcher_cutover']} · Stale: {s['stale']}</p>
        <p><a href='/alliance/source/magazine'>Open Workable Magazine DB</a> ·
        <a href='/alliance/primary/matcher'>Open Matcher</a> ·
        <a href='/api/alliance/admin/production-cutover/status'>Status JSON</a></p>
        <form method='post' action='/api/alliance/admin/production-cutover/rebuild'><button style='padding:10px'>Rebuild Cutover</button></form>
        <h3>Policy</h3><p>Raw evidence is preserved. Only unique settled GOLD enters the matcher. GOLD does not mean currently available:
        team verification remains required before client sending.</p></body></html>""")
    threading.Thread(target=_run,args=(core,),daemon=True,name="production-cutover-1210").start()
    return {"status":"REGISTERED","version":VERSION,
            "status_api":"/api/alliance/admin/production-cutover/status",
            "admin":"/alliance/admin/production-cutover",
            "magazine":"/alliance/source/magazine"}
