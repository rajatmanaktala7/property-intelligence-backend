from __future__ import annotations
import hashlib, html, json, re, threading, time, unicodedata, uuid
from datetime import datetime, timezone
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import inspect, text

VERSION = "3.0.0-EXACT-MESSAGE-RECOVERY-LEDGER"
RUN_EVERY_SECONDS = 900
BOOT_DELAY_SECONDS = 150
_LOCK = threading.Lock()
_LAST = {"status":"IDLE","version":VERSION,"mode":"EXACT_RECOVERY_METADATA_ONLY"}

def _now():
    return datetime.now(timezone.utc)

def _app(core):
    return getattr(core, "app", None) or core

def _auth(core, request: Request):
    try:
        core.need_login(request)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Login required") from exc

def _wa_engine():
    try:
        import alliance_whatsapp_source_reconciliation_v2 as r
        return r._wa_engine()
    except Exception:
        pass
    try:
        import alliance_whatsapp_live_clean_os_v1 as c
        for name in ("_wa_engine","wa_engine","engine"):
            obj=getattr(c,name,None)
            if callable(obj):
                return obj()
            if obj is not None:
                return obj
    except Exception:
        pass
    raise RuntimeError("WhatsApp database engine unavailable")

def _cols(engine, table):
    try:
        return {c["name"] for c in inspect(engine).get_columns(table)}
    except Exception:
        return set()

def _pick(cols, *names):
    for n in names:
        if n in cols:
            return n
    return None

def _norm(v):
    s=unicodedata.normalize("NFKC", str(v or "")).replace("\u00a0"," ")
    return re.sub(r"\s+"," ",s).strip()

def _norm_text(v):
    s=_norm(v).casefold()
    s=re.sub(r"https?://\S+"," ",s)
    s=re.sub(r"\s+"," ",s).strip()
    return s

def _norm_source(v):
    s=_norm(v).casefold()
    s=re.sub(r"^whatsapp\s+chat\s+with\s+","",s)
    s=re.sub(r"\.txt$","",s)
    s=re.sub(r"[^\w\s]+"," ",s,flags=re.UNICODE)
    return re.sub(r"\s+"," ",s).strip()

def _phone(v):
    d=re.sub(r"\D+","",str(v or ""))
    if not d:
        return ""
    if d.startswith("00"):
        d=d[2:]
    if len(d)==12 and d.startswith("91"):
        return d[-10:]
    if len(d)==11 and d.startswith("0"):
        return d[-10:]
    return d

def _ts(v):
    if v is None:
        return ""
    if hasattr(v,"isoformat"):
        return v.isoformat()
    return _norm(v)

def _hash(*parts):
    h=hashlib.sha256()
    for p in parts:
        h.update(str(p or "").encode("utf-8","ignore"))
        h.update(b"\x1f")
    return h.hexdigest()

def _ensure_schema(engine):
    stmts=[
        """CREATE TABLE IF NOT EXISTS alliance_rectification_message_recovery_v3(
            recovery_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            wa_message_key TEXT NOT NULL,
            wa_message_id TEXT,
            canonical_source_key TEXT,
            sender_key TEXT,
            sent_at_key TEXT,
            text_hash TEXT NOT NULL,
            wai_match_type TEXT NOT NULL,
            wai_match_id TEXT,
            gap_code TEXT NOT NULL,
            confidence TEXT NOT NULL,
            auto_linked BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        "CREATE INDEX IF NOT EXISTS ix_rect3_run ON alliance_rectification_message_recovery_v3(run_id)",
        "CREATE INDEX IF NOT EXISTS ix_rect3_wa_key ON alliance_rectification_message_recovery_v3(wa_message_key)",
        "CREATE INDEX IF NOT EXISTS ix_rect3_gap ON alliance_rectification_message_recovery_v3(gap_code)",
    ]
    with engine.begin() as cx:
        for s in stmts:
            cx.execute(text(s))

def _select_rows(engine, table, mapping):
    cols=_cols(engine,table)
    chosen={}
    for out,names in mapping.items():
        c=_pick(cols,*names)
        if c:
            chosen[out]=c
    required=("raw_text","source")
    if not all(k in chosen for k in required):
        return []
    select=", ".join(f'"{c}" AS "{o}"' for o,c in chosen.items())
    with engine.connect() as cx:
        return [dict(r) for r in cx.execute(text(f'SELECT {select} FROM "{table}"')).mappings()]

def _load():
    wa=_wa_engine()
    wa_map={
        "message_id":("message_id","id","source_message_id"),
        "source":("group_name","source_name","source","group","filename"),
        "raw_text":("raw_text","message_text","text","body"),
        "sender":("sender_phone","sender","author_phone","phone_number","sender_number","author","participant"),
        "sent_at":("timestamp","sent_at","captured_at","created_at","message_timestamp"),
    }
    wai_map={
        "message_id":("message_id","id","source_message_id"),
        "source":("group_name","source_name","source","group","filename"),
        "raw_text":("message_text","raw_text","text","body"),
        "sender":("sender_phone","sender","author_phone","phone_number","sender_number","author","participant"),
        "sent_at":("sent_at","timestamp","captured_at","created_at","message_timestamp"),
    }
    wa_rows=_select_rows(wa,"wa_messages",wa_map)
    wai_rows=_select_rows(wa,"wai_raw_messages",wai_map)
    return wa_rows,wai_rows

def _fingerprints(r):
    mid=_norm(r.get("message_id"))
    src=_norm_source(r.get("source"))
    txt=_norm_text(r.get("raw_text"))
    snd=_phone(r.get("sender"))
    ts=_ts(r.get("sent_at"))
    text_hash=_hash(txt)
    exact=_hash(src,snd,ts,txt)
    relaxed=_hash(snd,txt)
    mid_key=_hash("MID",src,mid) if mid else ""
    wa_key=mid_key or exact
    return {"mid":mid,"src":src,"txt":txt,"snd":snd,"ts":ts,"text_hash":text_hash,"exact":exact,"relaxed":relaxed,"mid_key":mid_key,"wa_key":wa_key}

def _run(core):
    if not _LOCK.acquire(blocking=False):
        return {"status":"BUSY","version":VERSION}
    run_id=str(uuid.uuid4())
    try:
        _ensure_schema(core.engine)
        wa_rows,wai_rows=_load()
        wai_mid={}
        wai_exact={}
        wai_relaxed={}
        for i,r in enumerate(wai_rows):
            f=_fingerprints(r)
            rid=f["mid"] or f["exact"] or str(i)
            if f["mid_key"]:
                wai_mid.setdefault(f["mid_key"],[]).append(rid)
            wai_exact.setdefault(f["exact"],[]).append(rid)
            wai_relaxed.setdefault(f["relaxed"],[]).append(rid)

        counts={}
        inserts=[]
        seen=set()
        for r in wa_rows:
            f=_fingerprints(r)
            if f["wa_key"] in seen:
                continue
            seen.add(f["wa_key"])
            match_type="NONE"; match_id=None; gap="WAI_MESSAGE_MISSING"; confidence="HIGH"; auto=False
            if f["mid_key"] and f["mid_key"] in wai_mid:
                match_type="MESSAGE_ID"; match_id=wai_mid[f["mid_key"]][0]; gap="RECONCILED"; confidence="EXACT"
            elif f["exact"] in wai_exact:
                match_type="EXACT_IDENTITY"; match_id=wai_exact[f["exact"]][0]; gap="RECONCILED"; confidence="EXACT"
            elif f["relaxed"] in wai_relaxed and len(wai_relaxed[f["relaxed"]])==1:
                match_type="CONTENT_SENDER"; match_id=wai_relaxed[f["relaxed"]][0]; gap="SOURCE_OR_TIMESTAMP_ALIAS"; confidence="HIGH"; auto=True
            elif f["relaxed"] in wai_relaxed:
                match_type="CONTENT_SENDER_MULTI"; match_id=wai_relaxed[f["relaxed"]][0]; gap="AMBIGUOUS_ALIAS_OR_DUPLICATE_IMPORT"; confidence="MEDIUM"
            counts[gap]=counts.get(gap,0)+1
            inserts.append({
                "recovery_id":"RECT3-"+uuid.uuid4().hex.upper(),
                "run_id":run_id,
                "wa_message_key":f["wa_key"],
                "wa_message_id":f["mid"] or None,
                "canonical_source_key":f["src"],
                "sender_key":f["snd"],
                "sent_at_key":f["ts"],
                "text_hash":f["text_hash"],
                "wai_match_type":match_type,
                "wai_match_id":match_id,
                "gap_code":gap,
                "confidence":confidence,
                "auto_linked":auto,
            })

        with core.engine.begin() as cx:
            for row in inserts:
                cx.execute(text("""
                    INSERT INTO alliance_rectification_message_recovery_v3
                    (recovery_id,run_id,wa_message_key,wa_message_id,canonical_source_key,sender_key,
                     sent_at_key,text_hash,wai_match_type,wai_match_id,gap_code,confidence,auto_linked)
                    VALUES(:recovery_id,:run_id,:wa_message_key,:wa_message_id,:canonical_source_key,:sender_key,
                           :sent_at_key,:text_hash,:wai_match_type,:wai_match_id,:gap_code,:confidence,:auto_linked)
                """),row)

        result={
            "status":"PASS","version":VERSION,"run_id":run_id,"mode":"EXACT_RECOVERY_METADATA_ONLY",
            "wa_rows_seen":len(wa_rows),"wai_rows_seen":len(wai_rows),"unique_wa_messages":len(seen),
            "classifications":counts,
            "auto_linked_metadata":counts.get("SOURCE_OR_TIMESTAMP_ALIAS",0),
            "unresolved_missing":counts.get("WAI_MESSAGE_MISSING",0),
            "ambiguous":counts.get("AMBIGUOUS_ALIAS_OR_DUPLICATE_IMPORT",0),
            "raw_whatsapp_mutations":0,"wai_raw_mutations":0,"master_property_mutations":0,"matcher_mutations":0,
            "rule":"Exact or unique content+sender evidence may create recovery metadata only; source evidence is never deleted or rewritten."
        }
        _LAST.update({"status":"PASS","last_run":_now().isoformat(),"result":result})
        return result
    except Exception as exc:
        _LAST.update({"status":"ERROR","last_run":_now().isoformat(),"error":f"{type(exc).__name__}: {exc}"})
        return {"status":"ERROR","version":VERSION,"error":_LAST["error"]}
    finally:
        _LOCK.release()

def _status(core):
    out=dict(_LAST)
    try:
        if inspect(core.engine).has_table("alliance_rectification_message_recovery_v3"):
            with core.engine.connect() as cx:
                rows=[dict(r) for r in cx.execute(text("""
                    SELECT gap_code, confidence, auto_linked, COUNT(*) AS n
                    FROM alliance_rectification_message_recovery_v3
                    WHERE run_id=(SELECT run_id FROM alliance_rectification_message_recovery_v3 ORDER BY created_at DESC LIMIT 1)
                    GROUP BY gap_code, confidence, auto_linked ORDER BY n DESC
                """)).mappings()]
            out["latest_breakdown"]=rows
    except Exception as exc:
        out["status_read_error"]=f"{type(exc).__name__}: {exc}"
    return out

def _page(core):
    s=_status(core); esc=lambda x: html.escape(str(x))
    result=s.get("result") or {}
    rows="".join("<tr><th>"+esc(k)+"</th><td>"+esc(v)+"</td></tr>" for k,v in result.items())
    br=s.get("latest_breakdown") or []
    b="".join("<tr><td>"+esc(r.get("gap_code"))+"</td><td>"+esc(r.get("confidence"))+"</td><td>"+esc(r.get("auto_linked"))+"</td><td>"+esc(r.get("n"))+"</td></tr>" for r in br)
    return "<!doctype html><html><head><title>Alliance Database Rectification V3</title><style>body{font-family:Arial;margin:24px}table{border-collapse:collapse;margin-bottom:24px}td,th{border:1px solid #ddd;padding:8px;text-align:left}</style></head><body><h1>Alliance Database Rectification V3</h1><p>Exact message recovery ledger. Raw WhatsApp, WAI raw, Master Properties and Matcher remain immutable.</p><table>"+rows+"</table><h2>Latest Breakdown</h2><table><tr><th>Gap</th><th>Confidence</th><th>Auto-linked metadata</th><th>Count</th></tr>"+b+"</table></body></html>"

def _worker(core):
    time.sleep(BOOT_DELAY_SECONDS)
    while True:
        try:
            _run(core)
        except Exception as exc:
            _LAST.update({"status":"ERROR","error":f"{type(exc).__name__}: {exc}"})
        time.sleep(RUN_EVERY_SECONDS)

def register(core):
    app=_app(core)
    routes={getattr(r,"path",None) for r in getattr(app,"routes",[])}
    if not routes and hasattr(app,"router"):
        routes={getattr(r,"path",None) for r in getattr(app.router,"routes",[])}
    added=[]
    if "/api/alliance/database-rectification-v3/status" not in routes:
        @app.get("/api/alliance/database-rectification-v3/status")
        async def rect3_status(request: Request):
            _auth(core,request); return JSONResponse(_status(core))
        added.append("/api/alliance/database-rectification-v3/status")
    if "/api/alliance/database-rectification-v3/run" not in routes:
        @app.post("/api/alliance/database-rectification-v3/run")
        async def rect3_run(request: Request):
            _auth(core,request); return JSONResponse(_run(core))
        added.append("/api/alliance/database-rectification-v3/run")
    if "/alliance/admin/database-rectification-v3" not in routes:
        @app.get("/alliance/admin/database-rectification-v3",response_class=HTMLResponse)
        async def rect3_page(request: Request):
            _auth(core,request); return HTMLResponse(_page(core))
        added.append("/alliance/admin/database-rectification-v3")
    state=getattr(app,"state",None)
    if state is not None and not getattr(state,"alliance_rectification_worker_v3",False):
        setattr(state,"alliance_rectification_worker_v3",True)
        threading.Thread(target=_worker,args=(core,),daemon=True,name="alliance-rectification-v3").start()
    return {"status":"REGISTERED","version":VERSION,"mode":"EXACT_RECOVERY_METADATA_ONLY","routes":added,
            "automatic_interval_seconds":RUN_EVERY_SECONDS,"raw_whatsapp_mutations":0,"wai_raw_mutations":0,
            "master_property_mutations":0,"matcher_mutations":0}
