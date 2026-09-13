from __future__ import annotations
import hashlib, html, json, re, threading, time, unicodedata, uuid
from datetime import datetime, timezone
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import inspect, text

VERSION = "4.0.0-FINAL-MESSAGE-CLEANUP-PLANNER"
RUN_EVERY_SECONDS = 900
BOOT_DELAY_SECONDS = 180
ADVISORY_LOCK_KEY = 41042026
_LOCAL_LOCK = threading.Lock()
_LAST = {"status":"IDLE","version":VERSION,"mode":"DETERMINISTIC_PLAN_ONLY"}

def _now(): return datetime.now(timezone.utc)

def _app(core): return getattr(core, "app", None) or core

def _auth(core, request: Request):
    try:
        core.need_login(request)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Login required") from exc

def _wa_engine():
    import alliance_whatsapp_source_reconciliation_v2 as r
    return r._wa_engine()

def _cols(engine, table):
    try: return {c["name"] for c in inspect(engine).get_columns(table)}
    except Exception: return set()

def _pick(cols,*names):
    for n in names:
        if n in cols: return n
    return None

def _ident(name):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*",str(name or "")):
        raise RuntimeError("Unsafe SQL identifier")
    return '"' + str(name) + '"'

def _norm(v):
    s=unicodedata.normalize("NFKC",str(v or "")).replace("\u00a0"," ")
    return re.sub(r"\s+"," ",s).strip()

def _norm_text(v):
    s=_norm(v).casefold()
    s=re.sub(r"https?://\S+"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def _norm_source(v):
    s=_norm(v).casefold()
    s=re.sub(r"^whatsapp\s+chat\s+with\s+","",s)
    s=re.sub(r"\.txt$","",s)
    s=re.sub(r"[^\w\s]+"," ",s,flags=re.UNICODE)
    return re.sub(r"\s+"," ",s).strip()

def _phone(v):
    d=re.sub(r"\D+","",str(v or ""))
    if not d: return ""
    if d.startswith("00"): d=d[2:]
    if len(d)==12 and d.startswith("91"): return d[-10:]
    if len(d)==11 and d.startswith("0"): return d[-10:]
    return d

def _ts(v):
    if v is None: return ""
    return v.isoformat() if hasattr(v,"isoformat") else _norm(v)

def _epoch(v):
    if v is None: return None
    try:
        if hasattr(v,"timestamp"): return float(v.timestamp())
        s=str(v).strip().replace("Z","+00:00")
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None

def _hash(*parts):
    h=hashlib.sha256()
    for p in parts:
        h.update(str(p or "").encode("utf-8","ignore")); h.update(b"\x1f")
    return h.hexdigest()

def _ensure_schema(engine):
    stmts=[
      """CREATE TABLE IF NOT EXISTS alliance_rectification_cleanup_v4(
        cleanup_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        wa_message_key TEXT NOT NULL,
        wa_message_id TEXT,
        gap_before TEXT NOT NULL,
        resolution TEXT NOT NULL,
        confidence TEXT NOT NULL,
        candidate_count INTEGER NOT NULL DEFAULT 0,
        selected_wai_id TEXT,
        downstream_trace TEXT,
        action TEXT NOT NULL,
        auto_apply_eligible BOOLEAN NOT NULL DEFAULT FALSE,
        applied BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
      )""",
      "CREATE INDEX IF NOT EXISTS ix_rect4_run ON alliance_rectification_cleanup_v4(run_id)",
      "CREATE INDEX IF NOT EXISTS ix_rect4_resolution ON alliance_rectification_cleanup_v4(resolution)"
    ]
    with engine.begin() as cx:
        for s in stmts: cx.execute(text(s))

def _load_joined(engine, message_table, source_table, mc, sc):
    mcols=_cols(engine,message_table); scols=_cols(engine,source_table)
    if not mcols: raise RuntimeError(message_table+" missing")
    mid=_pick(mcols,*mc["message_id"]); raw=_pick(mcols,*mc["raw_text"])
    sender=_pick(mcols,*mc["sender"]); sent=_pick(mcols,*mc["sent_at"])
    direct=_pick(mcols,*mc["source"]); fk=_pick(mcols,*mc["source_fk"])
    sid=_pick(scols,*sc["source_id"]) if scols else None
    sname=_pick(scols,*sc["source"]) if scols else None
    if not raw: raise RuntimeError(message_table+" missing text column")
    fields=[
      ("m."+_ident(mid) if mid else "NULL")+" AS message_id",
      "m."+_ident(raw)+" AS raw_text",
      ("m."+_ident(sender) if sender else "NULL")+" AS sender",
      ("m."+_ident(sent) if sent else "NULL")+" AS sent_at"
    ]
    if direct and sname and fk and sid:
        sexpr="COALESCE(NULLIF(CAST(m."+_ident(direct)+" AS TEXT),''),CAST(s."+_ident(sname)+" AS TEXT))"
    elif direct: sexpr="CAST(m."+_ident(direct)+" AS TEXT)"
    elif sname and fk and sid: sexpr="CAST(s."+_ident(sname)+" AS TEXT)"
    else: sexpr="''"
    fields.append(sexpr+" AS source")
    sql="SELECT "+", ".join(fields)+" FROM "+_ident(message_table)+" m"
    if sname and fk and sid:
        sql+=" LEFT JOIN "+_ident(source_table)+" s ON m."+_ident(fk)+"=s."+_ident(sid)
    with engine.connect() as cx:
        return [dict(r) for r in cx.execute(text(sql)).mappings()]

def _finger(r):
    mid=_norm(r.get("message_id")); src=_norm_source(r.get("source"))
    txt=_norm_text(r.get("raw_text")); snd=_phone(r.get("sender")); ts=_ts(r.get("sent_at"))
    exact=_hash(src,snd,ts,txt); relaxed=_hash(snd,txt)
    return {"mid":mid,"src":src,"txt":txt,"snd":snd,"ts":ts,"epoch":_epoch(r.get("sent_at")),
            "exact":exact,"relaxed":relaxed,"wa_key":_hash("MID",src,mid) if mid else exact}

def _load_ledgers():
    wa=_wa_engine()
    wa_rows=_load_joined(wa,"wa_messages","wa_sources",
      {"message_id":("message_id","id","source_message_id"),"source":("group_name","source_name","source","group","filename"),
       "source_fk":("source_id","group_id"),"raw_text":("raw_text","message_text","text","body"),
       "sender":("sender_phone","sender","author_phone","phone_number","sender_number","author","participant"),
       "sent_at":("timestamp","sent_at","captured_at","created_at","message_timestamp")},
      {"source_id":("source_id","id","group_id"),"source":("group_name","source_name","name","group","filename")})
    wai_rows=_load_joined(wa,"wai_raw_messages","wai_groups",
      {"message_id":("message_id","id","source_message_id"),"source":("group_name","source_name","source","group","filename"),
       "source_fk":("group_id","source_id"),"raw_text":("message_text","raw_text","text","body"),
       "sender":("sender_phone","sender","author_phone","phone_number","sender_number","author","participant"),
       "sent_at":("sent_at","timestamp","captured_at","created_at","message_timestamp")},
      {"source_id":("group_id","id","source_id"),"source":("group_name","source_name","name","group","filename")})
    return wa,wa_rows,wai_rows

def _latest_v3_unresolved(core):
    with core.engine.connect() as cx:
        run=cx.execute(text("SELECT run_id FROM alliance_rectification_message_recovery_v3 ORDER BY created_at DESC LIMIT 1")).scalar()
        if not run: return None,[]
        rows=[dict(r) for r in cx.execute(text("""
          SELECT wa_message_key,wa_message_id,gap_code
          FROM alliance_rectification_message_recovery_v3
          WHERE run_id=:r AND gap_code IN ('AMBIGUOUS_ALIAS_OR_DUPLICATE_IMPORT','WAI_MESSAGE_MISSING')
        """),{"r":run}).mappings()]
    return run,rows

def _trace_downstream(wa_engine, wf):
    hits=[]
    for table in ("wa_properties","wa_requirements","wa_contacts","wa_rejected","wa_review_queue"):
        cols=_cols(wa_engine,table)
        if not cols: continue
        midcol=_pick(cols,"message_id","source_message_id","wa_message_id")
        txtcol=_pick(cols,"raw_text","message_text","text","description")
        conditions=[]; params={}
        if midcol and wf["mid"]:
            conditions.append(_ident(midcol)+"=:mid"); params["mid"]=wf["mid"]
        if txtcol and wf["txt"]:
            conditions.append(_ident(txtcol)+"=:txt"); params["txt_raw"]=None
        found=0
        try:
            if midcol and wf["mid"]:
                with wa_engine.connect() as cx:
                    found=int(cx.execute(text("SELECT COUNT(*) FROM "+_ident(table)+" WHERE "+_ident(midcol)+"=:mid"),{"mid":wf["mid"]}).scalar() or 0)
            if not found and txtcol and wf["txt"]:
                # Exact raw-text tracing only; no fuzzy mutation decisions.
                with wa_engine.connect() as cx:
                    vals=cx.execute(text("SELECT "+_ident(txtcol)+" FROM "+_ident(table)+" WHERE "+_ident(txtcol)+" IS NOT NULL")).scalars()
                    found=sum(1 for v in vals if _norm_text(v)==wf["txt"])
        except Exception:
            found=0
        if found: hits.append({"table":table,"count":found})
    return hits

def _classify_one(wf,cands):
    if not cands:
        return ("TRUE_WAI_MISSING","HIGH",None,"PLAN_TRACE_ONLY",False)
    same_src=[c for c in cands if c["src"]==wf["src"]]
    if len(same_src)==1:
        return ("RESOLVED_UNIQUE_SOURCE_LINEAGE","HIGH",same_src[0]["id"],"PLAN_METADATA_LINK",True)
    near=[]
    for c in cands:
        if wf["epoch"] is not None and c["epoch"] is not None and abs(wf["epoch"]-c["epoch"])<=300:
            near.append(c)
    if len(near)==1:
        return ("RESOLVED_UNIQUE_NEAR_TIMESTAMP","HIGH",near[0]["id"],"PLAN_METADATA_LINK",True)
    if len(cands)==1:
        return ("RESOLVED_UNIQUE_CONTENT_SENDER","HIGH",cands[0]["id"],"PLAN_METADATA_LINK",True)
    return ("AMBIGUOUS_REMAINS","MEDIUM",None,"REVIEW",False)

def _run(core):
    if not _LOCAL_LOCK.acquire(blocking=False):
        return {"status":"BUSY","version":VERSION}
    run_id=str(uuid.uuid4())
    conn=None
    try:
        _ensure_schema(core.engine)
        conn=core.engine.connect()
        got=conn.execute(text("SELECT pg_try_advisory_lock(:k)"),{"k":ADVISORY_LOCK_KEY}).scalar()
        if not got: return {"status":"BUSY","version":VERSION,"reason":"database advisory lock held"}

        v3_run,unresolved=_latest_v3_unresolved(core)
        if v3_run is None: raise RuntimeError("No V3 recovery run found")
        wa_engine,wa_rows,wai_rows=_load_ledgers()

        wa_by_key={}
        for r in wa_rows:
            f=_finger(r); wa_by_key[f["wa_key"]]=f

        wai_relaxed={}
        for i,r in enumerate(wai_rows):
            f=_finger(r); f["id"]=f["mid"] or f["exact"] or str(i)
            wai_relaxed.setdefault(f["relaxed"],[]).append(f)

        counts={}; actions=[]; downstream={}
        for u in unresolved:
            wf=wa_by_key.get(u["wa_message_key"])
            if not wf:
                resolution,conf,sel,action,eligible=("WA_SOURCE_ROW_NOT_FOUND","HIGH",None,"REVIEW",False)
                trace=[]
            else:
                cands=wai_relaxed.get(wf["relaxed"],[])
                resolution,conf,sel,action,eligible=_classify_one(wf,cands)
                trace=_trace_downstream(wa_engine,wf) if resolution=="TRUE_WAI_MISSING" else []
                if resolution=="TRUE_WAI_MISSING" and trace:
                    resolution="WAI_MISSING_BUT_DOWNSTREAM_TRACE_FOUND"
                    action="PLAN_REBUILD_WAI_LINK_ONLY"
                    eligible=False
            counts[resolution]=counts.get(resolution,0)+1
            if trace: downstream[u["wa_message_key"]]=trace
            actions.append({
              "cleanup_id":"RECT4-"+uuid.uuid4().hex.upper(),"run_id":run_id,
              "wa_message_key":u["wa_message_key"],"wa_message_id":u.get("wa_message_id"),
              "gap_before":u["gap_code"],"resolution":resolution,"confidence":conf,
              "candidate_count":len(wai_relaxed.get(wf["relaxed"],[])) if wf else 0,
              "selected_wai_id":sel,"downstream_trace":json.dumps(trace) if trace else None,
              "action":action,"auto_apply_eligible":eligible,"applied":False
            })

        with core.engine.begin() as cx:
            for a in actions:
                cx.execute(text("""
                  INSERT INTO alliance_rectification_cleanup_v4
                  (cleanup_id,run_id,wa_message_key,wa_message_id,gap_before,resolution,confidence,
                   candidate_count,selected_wai_id,downstream_trace,action,auto_apply_eligible,applied)
                  VALUES(:cleanup_id,:run_id,:wa_message_key,:wa_message_id,:gap_before,:resolution,:confidence,
                         :candidate_count,:selected_wai_id,:downstream_trace,:action,:auto_apply_eligible,:applied)
                """),a)

        eligible=sum(1 for a in actions if a["auto_apply_eligible"])
        result={"status":"PASS","version":VERSION,"run_id":run_id,"source_v3_run":v3_run,
                "unresolved_input":len(unresolved),"classifications":counts,
                "deterministic_metadata_links_planned":eligible,
                "raw_whatsapp_mutations":0,"wai_raw_mutations":0,"master_property_mutations":0,"matcher_mutations":0,
                "mode":"DETERMINISTIC_PLAN_ONLY",
                "rule":"V4 plans only. No source, WAI, Master or Matcher record is changed."}
        _LAST.update({"status":"PASS","last_run":_now().isoformat(),"result":result})
        return result
    except Exception as exc:
        _LAST.update({"status":"ERROR","last_run":_now().isoformat(),"error":f"{type(exc).__name__}: {exc}"})
        return {"status":"ERROR","version":VERSION,"error":_LAST["error"]}
    finally:
        try:
            if conn is not None:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"),{"k":ADVISORY_LOCK_KEY})
                conn.close()
        except Exception: pass
        _LOCAL_LOCK.release()

def _status(core):
    out=dict(_LAST)
    try:
        with core.engine.connect() as cx:
            run=cx.execute(text("SELECT run_id FROM alliance_rectification_cleanup_v4 ORDER BY created_at DESC LIMIT 1")).scalar()
            if run:
                out["latest_breakdown"]=[dict(r) for r in cx.execute(text("""
                  SELECT resolution,confidence,action,auto_apply_eligible,COUNT(*) AS n
                  FROM alliance_rectification_cleanup_v4 WHERE run_id=:r
                  GROUP BY resolution,confidence,action,auto_apply_eligible ORDER BY n DESC
                """),{"r":run}).mappings()]
    except Exception as exc:
        out["status_read_error"]=f"{type(exc).__name__}: {exc}"
    return out

def _page(core):
    s=_status(core); esc=lambda x: html.escape(str(x))
    rows="".join("<tr><th>"+esc(k)+"</th><td>"+esc(v)+"</td></tr>" for k,v in (s.get("result") or {}).items())
    br=s.get("latest_breakdown") or []
    b="".join("<tr><td>"+esc(r.get("resolution"))+"</td><td>"+esc(r.get("confidence"))+"</td><td>"+esc(r.get("action"))+"</td><td>"+esc(r.get("auto_apply_eligible"))+"</td><td>"+esc(r.get("n"))+"</td></tr>" for r in br)
    return "<!doctype html><html><head><title>Alliance Database Rectification V4</title><style>body{font-family:Arial;margin:24px}table{border-collapse:collapse;margin-bottom:24px}td,th{border:1px solid #ddd;padding:8px;text-align:left}</style></head><body><h1>Alliance Database Rectification V4</h1><p>Final message cleanup planner. Deterministic resolution only; no source/master mutations.</p><table>"+rows+"</table><h2>Latest Breakdown</h2><table><tr><th>Resolution</th><th>Confidence</th><th>Action</th><th>Auto-apply eligible</th><th>Count</th></tr>"+b+"</table></body></html>"

def _worker(core):
    time.sleep(BOOT_DELAY_SECONDS)
    while True:
        try: _run(core)
        except Exception as exc: _LAST.update({"status":"ERROR","error":f"{type(exc).__name__}: {exc}"})
        time.sleep(RUN_EVERY_SECONDS)

def register(core):
    app=_app(core)
    paths={getattr(r,"path",None) for r in getattr(app,"routes",[])}
    added=[]
    if "/api/alliance/database-rectification-v4/status" not in paths:
        @app.get("/api/alliance/database-rectification-v4/status")
        async def s(request: Request):
            _auth(core,request); return JSONResponse(_status(core))
        added.append("/api/alliance/database-rectification-v4/status")
    if "/api/alliance/database-rectification-v4/run" not in paths:
        @app.post("/api/alliance/database-rectification-v4/run")
        async def r(request: Request):
            _auth(core,request); return JSONResponse(_run(core))
        added.append("/api/alliance/database-rectification-v4/run")
    if "/alliance/admin/database-rectification-v4" not in paths:
        @app.get("/alliance/admin/database-rectification-v4",response_class=HTMLResponse)
        async def p(request: Request):
            _auth(core,request); return HTMLResponse(_page(core))
        added.append("/alliance/admin/database-rectification-v4")
    state=getattr(app,"state",None)
    if state is not None and not getattr(state,"alliance_rectification_worker_v4",False):
        setattr(state,"alliance_rectification_worker_v4",True)
        threading.Thread(target=_worker,args=(core,),daemon=True,name="alliance-rectification-v4").start()
    return {"status":"REGISTERED","version":VERSION,"mode":"DETERMINISTIC_PLAN_ONLY","routes":added,
            "automatic_interval_seconds":RUN_EVERY_SECONDS,"advisory_lock":True,
            "raw_whatsapp_mutations":0,"wai_raw_mutations":0,"master_property_mutations":0,"matcher_mutations":0}
