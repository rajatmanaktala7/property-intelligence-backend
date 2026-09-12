from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import HTTPException
from sqlalchemy import bindparam, text

VERSION = "1.1.0-READ-ONLY-FAIL-SAFE-WHATSAPP-RECONCILIATION"
ROUTE = "/api/alliance/whatsapp-reconciliation-v1"
PAGE = "/alliance/admin/whatsapp-reconciliation-v1"
WA_TABLES = ("wa_sources","wa_messages","wa_properties","wa_requirements","wa_contacts","wa_review_queue","wa_rejected")
MAIN_TABLES = ("pi_master_properties_v711","pi_master_source_links_v711","pi_master_workflow_v720","pi_whatsapp_live_clean_ledger")

def _table_exists(engine, name):
    with engine.connect() as c:
        return bool(c.execute(text("""SELECT EXISTS(
          SELECT 1 FROM information_schema.tables
          WHERE table_schema=current_schema() AND table_name=:t)"""), {"t":name}).scalar())

def _columns(engine, name):
    with engine.connect() as c:
        return {str(r[0]) for r in c.execute(text("""SELECT column_name FROM information_schema.columns
          WHERE table_schema=current_schema() AND table_name=:t"""), {"t":name})}

def _safe_scalar(conn, sql, params=None, default=0):
    try:
        v = conn.execute(text(sql), params or {}).scalar()
        return default if v is None else v
    except Exception:
        return default

def _chunks(items, n=800):
    for i in range(0, len(items), n):
        yield items[i:i+n]

def _wa_engine():
    for modname in ("whatsapp_intelligence","whatsapp_intelligence_property_purity"):
        try:
            mod = __import__(modname)
            eng = getattr(mod, "wa_engine", None)
            if eng is not None:
                return eng
        except Exception:
            pass
    return None

def _master_promotions(main_engine, property_ids):
    if not property_ids or not _table_exists(main_engine,"pi_master_source_links_v711"):
        return {"promoted":0,"matcher_eligible":0}
    promoted=set(); eligible=set()
    wf_ok=_table_exists(main_engine,"pi_master_workflow_v720")
    wf_cols=_columns(main_engine,"pi_master_workflow_v720") if wf_ok else set()
    with main_engine.connect() as c:
        for chunk in _chunks([str(x) for x in property_ids]):
            q=text("""SELECT DISTINCT canonical_id FROM pi_master_source_links_v711
                      WHERE source_table='wa_properties' AND source_pk IN :ids""").bindparams(bindparam("ids",expanding=True))
            for r in c.execute(q,{"ids":chunk}):
                if r[0]: promoted.add(str(r[0]))
        if promoted and wf_ok and {"canonical_id","verification_status"} <= wf_cols:
            for chunk in _chunks(sorted(promoted)):
                filters=["canonical_id IN :ids","UPPER(COALESCE(verification_status,''))='VERIFIED'"]
                if "availability_status" in wf_cols:
                    filters.append("UPPER(COALESCE(availability_status,'')) IN ('READY','AVAILABLE','ACTIVE','VERIFIED_AVAILABLE')")
                q=text("SELECT canonical_id FROM pi_master_workflow_v720 WHERE "+" AND ".join(filters)).bindparams(bindparam("ids",expanding=True))
                for r in c.execute(q,{"ids":chunk}):
                    eligible.add(str(r[0]))
    return {"promoted":len(promoted),"matcher_eligible":len(eligible)}

def _sources(wa_engine):
    if not _table_exists(wa_engine,"wa_sources"): return []
    cols=_columns(wa_engine,"wa_sources")
    wanted=["source_id","source_name","group_name","ingestion_status","total_messages","created_at","processed_at","error_message"]
    sel=[x for x in wanted if x in cols]
    if "source_id" not in sel: return []
    order="created_at" if "created_at" in cols else "source_id"
    with wa_engine.connect() as c:
        return [dict(r) for r in c.execute(text(f"SELECT {','.join(sel)} FROM wa_sources ORDER BY {order} DESC")).mappings()]

def _count(conn, table, sid, extra=""):
    return int(_safe_scalar(conn,f"SELECT COUNT(*) FROM {table} WHERE source_id=:sid {extra}",{"sid":sid},0))

def _distinct_messages(conn, table, sid):
    try:
        return int(conn.execute(text(f"SELECT COUNT(DISTINCT message_id) FROM {table} WHERE source_id=:sid"),{"sid":sid}).scalar() or 0)
    except Exception:
        return 0

def audit(main_engine):
    wa_engine=_wa_engine()
    if wa_engine is None:
        return {"status":"ERROR","version":VERSION,"read_only":True,"groups":[],"failure_codes":{"WA_ENGINE_UNAVAILABLE":1}}

    wa_schema={t:_table_exists(wa_engine,t) for t in WA_TABLES}
    main_schema={t:_table_exists(main_engine,t) for t in MAIN_TABLES}
    groups=[]; failure_codes=Counter()

    with wa_engine.connect() as c:
        msg_cols=_columns(wa_engine,"wa_messages") if wa_schema["wa_messages"] else set()
        prop_cols=_columns(wa_engine,"wa_properties") if wa_schema["wa_properties"] else set()

        for s in _sources(wa_engine):
            sid=s.get("source_id")
            raw=_count(c,"wa_messages",sid) if wa_schema["wa_messages"] else 0
            last=None
            if raw and "message_timestamp" in msg_cols:
                try: last=c.execute(text("SELECT MAX(message_timestamp) FROM wa_messages WHERE source_id=:sid"),{"sid":sid}).scalar()
                except Exception: pass

            classes={}
            if wa_schema["wa_messages"] and "classification" in msg_cols:
                try:
                    for r in c.execute(text("""SELECT COALESCE(classification,'UNCLASSIFIED') k,COUNT(*) n
                                              FROM wa_messages WHERE source_id=:sid GROUP BY 1"""),{"sid":sid}).mappings():
                        classes[str(r["k"])]=int(r["n"])
                except Exception: pass

            rejected=_distinct_messages(c,"wa_rejected",sid) if wa_schema["wa_rejected"] else 0
            review=_distinct_messages(c,"wa_review_queue",sid) if wa_schema["wa_review_queue"] else 0
            requirements=_distinct_messages(c,"wa_requirements",sid) if wa_schema["wa_requirements"] else 0
            contacts=_distinct_messages(c,"wa_contacts",sid) if wa_schema["wa_contacts"] else 0
            prop_rows=_count(c,"wa_properties",sid) if wa_schema["wa_properties"] else 0
            prop_messages=_distinct_messages(c,"wa_properties",sid) if wa_schema["wa_properties"] else 0
            duplicates=0; active_unique=prop_rows; prop_ids=[]

            if wa_schema["wa_properties"]:
                if "duplicate_status" in prop_cols:
                    duplicates=_count(c,"wa_properties",sid,"AND COALESCE(duplicate_status,'UNIQUE')<>'UNIQUE'")
                filters=["source_id=:sid"]
                if "record_status" in prop_cols: filters.append("COALESCE(record_status,'ACTIVE')='ACTIVE'")
                if "duplicate_status" in prop_cols: filters.append("COALESCE(duplicate_status,'UNIQUE')='UNIQUE'")
                try:
                    active_unique=int(c.execute(text("SELECT COUNT(*) FROM wa_properties WHERE "+" AND ".join(filters)),{"sid":sid}).scalar() or 0)
                except Exception: active_unique=0
                if "wa_property_id" in prop_cols:
                    try:
                        prop_ids=[str(r[0]) for r in c.execute(text("SELECT wa_property_id FROM wa_properties WHERE source_id=:sid"),{"sid":sid}) if r[0] is not None]
                    except Exception: prop_ids=[]

            master=_master_promotions(main_engine,prop_ids)
            declared=int(s.get("total_messages") or 0)
            gaps=[]
            if declared and declared != raw:
                gaps.append({"code":"SOURCE_MESSAGE_COUNT_MISMATCH","declared":declared,"actual":raw})
            if raw and classes and sum(classes.values()) != raw:
                gaps.append({"code":"MESSAGE_CLASSIFICATION_GAP","actual":raw,"classified":sum(classes.values())})
            if str(s.get("ingestion_status") or "").upper() in {"FAILED","ERROR"}:
                gaps.append({"code":"SOURCE_INGESTION_FAILED","status":s.get("ingestion_status")})
            if active_unique and master["promoted"] < active_unique:
                gaps.append({"code":"MASTER_PROMOTION_GAP","active_unique_properties":active_unique,
                             "promoted_from_wa_properties":master["promoted"],"gap":active_unique-master["promoted"]})
            if raw and last is None:
                gaps.append({"code":"MESSAGE_TIMESTAMP_MISSING","messages":raw})
            for g in gaps: failure_codes[g["code"]]+=1

            groups.append({
                "source_id":str(sid),"group":s.get("group_name") or s.get("source_name") or str(sid),
                "ingestion_status":s.get("ingestion_status"),"declared_messages":declared,"raw_messages":raw,
                "last_message_at":last,"message_classifications":classes,"rejected_messages":rejected,
                "review_messages":review,"contact_messages":contacts,"property_messages":prop_messages,
                "property_rows":prop_rows,"active_unique_properties":active_unique,"duplicates":duplicates,
                "requirements":requirements,"master_promotions":master["promoted"],
                "matcher_eligible_verified":master["matcher_eligible"],"gaps":gaps,
                "status":"PASS" if not gaps else "FAIL"
            })

    totals={k:sum(x[k] for x in groups) for k in (
        "raw_messages","property_rows","active_unique_properties","duplicates","requirements",
        "review_messages","rejected_messages","master_promotions","matcher_eligible_verified")}
    totals.update({"configured_sources":len(groups),"groups_pass":sum(x["status"]=="PASS" for x in groups),
                   "groups_fail":sum(x["status"]=="FAIL" for x in groups)})
    return {"status":"PASS" if not failure_codes else "FAIL","version":VERSION,"mode":"READ_ONLY_RECONCILIATION",
            "read_only":True,"source_mutations":0,"master_mutations":0,"matcher_mutations":0,
            "wa_schema":wa_schema,"main_schema":main_schema,"totals":totals,
            "failure_codes":dict(failure_codes),"groups":groups,
            "policy":{"message_partition":"wa_messages.classification is authoritative",
                      "master_promotion":"pi_master_source_links_v711 source_table=wa_properties",
                      "matcher_eligible_verified":"conservative VERIFIED plus available/active when availability_status exists",
                      "master_only_matcher_preserved":True},
            "generated_at":datetime.now(timezone.utc).isoformat()}

def register(core):
    app=core.app; engine=core.engine
    paths={getattr(r,"path",None) for r in app.router.routes}
    if ROUTE not in paths:
        @app.get(ROUTE)
        def api_reconciliation():
            return audit(engine)
    if PAGE not in paths:
        from fastapi.responses import HTMLResponse
        @app.get(PAGE,response_class=HTMLResponse)
        def page_reconciliation():
            snap=audit(engine)
            rows=[]
            for g in snap.get("groups",[]):
                gaps=", ".join(x.get("code","") for x in g.get("gaps",[])) or "—"
                rows.append("<tr>"+f"<td>{g['status']}</td><td>{g['group']}</td><td>{g['raw_messages']}</td>"
                            f"<td>{g.get('last_message_at') or '—'}</td><td>{g['active_unique_properties']}</td>"
                            f"<td>{g['duplicates']}</td><td>{g['requirements']}</td><td>{g['review_messages']}</td>"
                            f"<td>{g['rejected_messages']}</td><td>{g['master_promotions']}</td>"
                            f"<td>{g['matcher_eligible_verified']}</td><td>{gaps}</td></tr>")
            return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'><title>Alliance WhatsApp Reconciliation V1.1</title>
<style>body{{font-family:Arial;margin:20px;background:#f6f4f1}}table{{border-collapse:collapse;width:100%;background:white}}
th,td{{padding:8px;border:1px solid #ddd;text-align:left;font-size:13px}}th{{background:#eee}}</style></head><body>
<h2>Alliance WhatsApp Ingestion Reconciliation V1.1</h2><p><b>Status:</b> {snap.get('status')} · READ ONLY ·
<b>Groups:</b> {snap.get('totals',{}).get('configured_sources',0)}</p>
<table><tr><th>Status</th><th>Group</th><th>Raw</th><th>Last message</th><th>Unique properties</th><th>Duplicates</th>
<th>Requirements</th><th>Review</th><th>Rejected</th><th>Master promoted</th><th>Matcher eligible</th><th>Gaps</th></tr>
{''.join(rows)}</table></body></html>""")
    return {"status":"REGISTERED","version":VERSION,"mode":"READ_ONLY","routes":[ROUTE,PAGE],
            "source_mutations":0,"master_mutations":0,"matcher_mutations":0}
