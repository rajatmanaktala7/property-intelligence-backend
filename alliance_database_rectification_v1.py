from __future__ import annotations
import hashlib, html, json, re, threading, time, unicodedata, uuid
from datetime import datetime, timezone
from typing import Any
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import inspect, text

VERSION = "1.0.0-CANONICAL-RECTIFICATION-SAFE-AUTO"
RUN_EVERY_SECONDS = 900
BOOT_DELAY_SECONDS = 90
_LOCK = threading.Lock()
_LAST = {"status":"IDLE","version":VERSION,"last_run":None,"last_error":None,"mode":"SAFE_CANONICAL_ONLY"}

def _now():
    return datetime.now(timezone.utc)

def _auth(core, request: Request):
    try:
        core.need_login(request)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Login required") from exc

def _wa_engine():
    import alliance_whatsapp_source_reconciliation_v2 as recon
    return recon._wa_engine()

def _cols(engine, table):
    try:
        return {c["name"] for c in inspect(engine).get_columns(table)}
    except Exception:
        return set()

def _exists(engine, table):
    try:
        return bool(inspect(engine).has_table(table))
    except Exception:
        return False

def _canon_name(value: Any) -> str:
    s = unicodedata.normalize("NFKC", str(value or "")).strip()
    s = re.sub(r"(?i)^\s*whatsapp\s+chat\s+with\s+", "", s)
    s = re.sub(r"(?i)\.txt\s*$", "", s)
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[^\w\s]+", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip().casefold()
    return s or "unknown"

def _hash(*parts: Any) -> str:
    raw = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()

def _ensure_schema(engine):
    stmts = [
        "CREATE TABLE IF NOT EXISTS alliance_rectification_runs_v1(run_id TEXT PRIMARY KEY,started_at TIMESTAMPTZ NOT NULL,finished_at TIMESTAMPTZ,status TEXT NOT NULL,mode TEXT NOT NULL,sources_seen BIGINT NOT NULL DEFAULT 0,canonical_sources BIGINT NOT NULL DEFAULT 0,aliases_seen BIGINT NOT NULL DEFAULT 0,messages_seen BIGINT NOT NULL DEFAULT 0,canonical_messages BIGINT NOT NULL DEFAULT 0,duplicate_messages BIGINT NOT NULL DEFAULT 0,source_counter_drift BIGINT NOT NULL DEFAULT 0,raw_wai_gap BIGINT NOT NULL DEFAULT 0,notes JSONB NOT NULL DEFAULT '{}'::jsonb)",
        "CREATE TABLE IF NOT EXISTS alliance_wa_source_canonical_v1(canonical_source_id TEXT PRIMARY KEY,canonical_key TEXT UNIQUE NOT NULL,display_name TEXT NOT NULL,first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),alias_count BIGINT NOT NULL DEFAULT 0,declared_messages BIGINT NOT NULL DEFAULT 0,actual_messages BIGINT NOT NULL DEFAULT 0,wai_messages BIGINT NOT NULL DEFAULT 0,counter_drift BIGINT NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'ACTIVE')",
        "CREATE TABLE IF NOT EXISTS alliance_wa_source_alias_v1(alias_id TEXT PRIMARY KEY,canonical_source_id TEXT NOT NULL,source_id TEXT,source_name TEXT,normalized_name TEXT NOT NULL,source_kind TEXT NOT NULL DEFAULT 'OBSERVED',declared_messages BIGINT NOT NULL DEFAULT 0,actual_messages BIGINT NOT NULL DEFAULT 0,wai_messages BIGINT NOT NULL DEFAULT 0,last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
        "CREATE TABLE IF NOT EXISTS alliance_wa_message_identity_v1(message_identity TEXT PRIMARY KEY,canonical_source_id TEXT NOT NULL,source_id TEXT,message_id TEXT,sender_phone TEXT,sent_at TEXT,message_hash TEXT NOT NULL,first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),duplicate_count BIGINT NOT NULL DEFAULT 0)",
        "CREATE INDEX IF NOT EXISTS ix_alliance_wa_alias_canonical ON alliance_wa_source_alias_v1(canonical_source_id)",
        "CREATE INDEX IF NOT EXISTS ix_alliance_wa_message_source ON alliance_wa_message_identity_v1(canonical_source_id)",
    ]
    with engine.begin() as cx:
        for stmt in stmts:
            cx.execute(text(stmt))

def _source_rows(wa):
    if not _exists(wa, "wa_sources"):
        return []
    c = _cols(wa, "wa_sources")
    sid = "source_id" if "source_id" in c else ("id" if "id" in c else None)
    name = "group_name" if "group_name" in c else ("source_name" if "source_name" in c else ("name" if "name" in c else None))
    total = "total_messages" if "total_messages" in c else None
    if not sid or not name:
        return []
    select = 'SELECT "' + sid + '" AS source_id, "' + name + '" AS source_name'
    select += (', COALESCE("' + total + '",0) AS declared' if total else ', 0 AS declared')
    select += ' FROM wa_sources'
    with wa.connect() as cx:
        return [dict(r._mapping) for r in cx.execute(text(select))]

def _message_rows(wa):
    if not _exists(wa, "wa_messages"):
        return []
    c = _cols(wa, "wa_messages")
    wanted = {
        "source_id": ["source_id"],
        "message_id": ["message_id","id"],
        "sender_phone": ["sender_phone","phone","sender_number"],
        "sent_at": ["timestamp","sent_at","created_at"],
        "raw_text": ["raw_text","message_text","text","body"],
    }
    sel = []
    for alias, opts in wanted.items():
        col = next((x for x in opts if x in c), None)
        sel.append(('"' + col + '"' if col else "NULL") + " AS " + alias)
    with wa.connect() as cx:
        return [dict(r._mapping) for r in cx.execute(text("SELECT " + ", ".join(sel) + " FROM wa_messages"))]

def _wai_counts(wa):
    if not (_exists(wa, "wai_groups") and _exists(wa, "wai_raw_messages")):
        return {}
    gc, rc = _cols(wa, "wai_groups"), _cols(wa, "wai_raw_messages")
    gid = "id" if "id" in gc else ("group_id" if "group_id" in gc else None)
    gname = "name" if "name" in gc else ("group_name" if "group_name" in gc else None)
    rgid = "group_id" if "group_id" in rc else None
    if not gid or not gname or not rgid:
        return {}
    q = 'SELECT g."' + gname + '" AS n, COUNT(*) AS c FROM wai_groups g JOIN wai_raw_messages r ON r."' + rgid + '"=g."' + gid + '" GROUP BY g."' + gname + '"'
    out = {}
    with wa.connect() as cx:
        for r in cx.execute(text(q)):
            key = _canon_name(r._mapping["n"])
            out[key] = out.get(key, 0) + int(r._mapping["c"] or 0)
    return out

def _run(core, mode="SAFE_CANONICAL_ONLY"):
    if not _LOCK.acquire(blocking=False):
        return {"status":"BUSY","version":VERSION}
    run_id = str(uuid.uuid4())
    started = _now()
    try:
        main, wa = core.engine, _wa_engine()
        _ensure_schema(main)
        sources, messages, wai = _source_rows(wa), _message_rows(wa), _wai_counts(wa)
        source_by_id, groups = {}, {}
        for s in sources:
            sid = str(s.get("source_id") or "")
            name = str(s.get("source_name") or "Unknown WhatsApp Group")
            key = _canon_name(name)
            source_by_id[sid] = (key, name)
            g = groups.setdefault(key, {"names":[],"source_ids":[],"declared":0,"actual":0,"wai":int(wai.get(key,0))})
            g["names"].append(name); g["source_ids"].append(sid); g["declared"] += int(s.get("declared") or 0)

        per_source_actual, identities = {}, {}
        for m in messages:
            sid = str(m.get("source_id") or "")
            per_source_actual[sid] = per_source_actual.get(sid, 0) + 1
            key, _ = source_by_id.get(sid, ("unknown","Unknown WhatsApp Group"))
            mid = str(m.get("message_id") or "")
            raw = str(m.get("raw_text") or "")
            ph = str(m.get("sender_phone") or "")
            ts = str(m.get("sent_at") or "")
            ident = _hash("MID", mid) if mid else _hash("FALLBACK", key, ph, ts, re.sub(r"\s+"," ",raw).strip())
            rec = identities.setdefault(ident, {"key":key,"source_id":sid,"message_id":mid,"sender_phone":ph,"sent_at":ts,"hash":_hash(raw),"count":0})
            rec["count"] += 1
        for key, g in groups.items():
            g["actual"] = sum(per_source_actual.get(s,0) for s in g["source_ids"])

        with main.begin() as cx:
            cx.execute(text("INSERT INTO alliance_rectification_runs_v1(run_id,started_at,status,mode) VALUES(:r,:s,'RUNNING',:m)"),
                       {"r":run_id,"s":started,"m":mode})
            canonical_ids = {}
            for key, g in groups.items():
                cid = "WASRC-" + _hash(key)[:20].upper()
                canonical_ids[key] = cid
                display = sorted(g["names"], key=lambda x:(x.lower().startswith("whatsapp chat with "),len(x)))[0]
                drift = int(g["actual"]) - int(g["declared"])
                cx.execute(text("INSERT INTO alliance_wa_source_canonical_v1(canonical_source_id,canonical_key,display_name,last_seen_at,alias_count,declared_messages,actual_messages,wai_messages,counter_drift,status) VALUES(:id,:k,:d,NOW(),:a,:decl,:act,:wai,:dr,'ACTIVE') ON CONFLICT(canonical_source_id) DO UPDATE SET canonical_key=EXCLUDED.canonical_key,display_name=EXCLUDED.display_name,last_seen_at=NOW(),alias_count=EXCLUDED.alias_count,declared_messages=EXCLUDED.declared_messages,actual_messages=EXCLUDED.actual_messages,wai_messages=EXCLUDED.wai_messages,counter_drift=EXCLUDED.counter_drift,status='ACTIVE'"),
                           {"id":cid,"k":key,"d":display,"a":len(set(g["source_ids"])),"decl":g["declared"],"act":g["actual"],"wai":g["wai"],"dr":drift})
            for s in sources:
                sid = str(s.get("source_id") or ""); name = str(s.get("source_name") or "")
                key = _canon_name(name); cid = canonical_ids.get(key, "WASRC-" + _hash(key)[:20].upper())
                alias_id = "WAALIAS-" + _hash(cid,sid,name)[:24].upper()
                cx.execute(text("INSERT INTO alliance_wa_source_alias_v1(alias_id,canonical_source_id,source_id,source_name,normalized_name,declared_messages,actual_messages,wai_messages,last_seen_at) VALUES(:aid,:cid,:sid,:sn,:nn,:decl,:act,:wai,NOW()) ON CONFLICT(alias_id) DO UPDATE SET declared_messages=EXCLUDED.declared_messages,actual_messages=EXCLUDED.actual_messages,wai_messages=EXCLUDED.wai_messages,last_seen_at=NOW()"),
                           {"aid":alias_id,"cid":cid,"sid":sid,"sn":name,"nn":key,"decl":int(s.get("declared") or 0),"act":per_source_actual.get(sid,0),"wai":int(wai.get(key,0))})
            for ident, r in identities.items():
                cid = canonical_ids.get(r["key"], "WASRC-" + _hash(r["key"])[:20].upper())
                cx.execute(text("INSERT INTO alliance_wa_message_identity_v1(message_identity,canonical_source_id,source_id,message_id,sender_phone,sent_at,message_hash,duplicate_count) VALUES(:i,:cid,:sid,:mid,:ph,:ts,:mh,:dup) ON CONFLICT(message_identity) DO UPDATE SET duplicate_count=EXCLUDED.duplicate_count"),
                           {"i":ident,"cid":cid,"sid":r["source_id"],"mid":r["message_id"],"ph":r["sender_phone"],"ts":r["sent_at"],"mh":r["hash"],"dup":max(0,r["count"]-1)})
            drift = sum(1 for g in groups.values() if int(g["declared"]) != int(g["actual"]))
            raw_wai = sum(1 for g in groups.values() if int(g["wai"]) and int(g["wai"]) != int(g["actual"]))
            dups = sum(max(0,r["count"]-1) for r in identities.values())
            notes = {"raw_source_tables_mutated":False,"master_tables_mutated":False,"matcher_mutated":False,"policy":"raw evidence immutable; canonical rectification metadata only"}
            cx.execute(text("UPDATE alliance_rectification_runs_v1 SET finished_at=NOW(),status='PASS',sources_seen=:ss,canonical_sources=:cs,aliases_seen=:as_,messages_seen=:ms,canonical_messages=:cm,duplicate_messages=:dm,source_counter_drift=:sd,raw_wai_gap=:rg,notes=CAST(:notes AS JSONB) WHERE run_id=:r"),
                       {"ss":len(sources),"cs":len(groups),"as_":len(sources),"ms":len(messages),"cm":len(identities),"dm":dups,"sd":drift,"rg":raw_wai,"notes":json.dumps(notes),"r":run_id})
        result = {"status":"PASS","version":VERSION,"run_id":run_id,"mode":mode,"sources_seen":len(sources),"canonical_sources":len(groups),"messages_seen":len(messages),"canonical_messages":len(identities),"duplicate_messages":dups,"source_counter_drift_groups":drift,"raw_wai_gap_groups":raw_wai,"raw_source_tables_mutated":False,"master_tables_mutated":False,"matcher_mutated":False}
        _LAST.update({"status":"PASS","last_run":_now().isoformat(),"last_error":None,"result":result})
        return result
    except Exception as exc:
        _LAST.update({"status":"ERROR","last_run":_now().isoformat(),"last_error":f"{type(exc).__name__}: {exc}"})
        return {"status":"ERROR","version":VERSION,"error":_LAST["last_error"]}
    finally:
        _LOCK.release()

def _status(core):
    out = dict(_LAST)
    try:
        if _exists(core.engine, "alliance_rectification_runs_v1"):
            with core.engine.connect() as cx:
                r = cx.execute(text("SELECT run_id,started_at,finished_at,status,mode,sources_seen,canonical_sources,aliases_seen,messages_seen,canonical_messages,duplicate_messages,source_counter_drift,raw_wai_gap FROM alliance_rectification_runs_v1 ORDER BY started_at DESC LIMIT 1")).mappings().first()
                if r:
                    out["database_last_run"] = {k:(v.isoformat() if hasattr(v,"isoformat") else v) for k,v in dict(r).items()}
    except Exception as exc:
        out["status_read_error"] = f"{type(exc).__name__}: {exc}"
    return out

def _page(core):
    s = _status(core)
    esc = lambda x: html.escape(str(x))
    last = s.get("database_last_run") or s.get("result") or {}
    rows = "".join("<tr><th>"+esc(k)+"</th><td>"+esc(v)+"</td></tr>" for k,v in last.items())
    return "<!doctype html><html><head><title>Alliance Database Rectification V1</title><style>body{font-family:Arial;margin:24px}table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:8px;text-align:left}</style></head><body><h1>Alliance Database Rectification V1</h1><p>Canonical, non-destructive rectification layer. Raw WhatsApp and Master tables remain preserved.</p><table>"+rows+"</table><p>Automatic interval: 15 minutes. Safe mode writes only rectification metadata.</p></body></html>"

def _worker(core):
    time.sleep(BOOT_DELAY_SECONDS)
    while True:
        try:
            _run(core)
        except Exception as exc:
            _LAST.update({"status":"ERROR","last_error":f"{type(exc).__name__}: {exc}"})
        time.sleep(RUN_EVERY_SECONDS)

def register(core):
    routes = {getattr(r,"path",None) for r in getattr(core,"routes",[])}
    added = []
    if "/api/alliance/database-rectification-v1/status" not in routes:
        @core.get("/api/alliance/database-rectification-v1/status")
        async def rect_status(request: Request):
            _auth(core,request); return JSONResponse(_status(core))
        added.append("/api/alliance/database-rectification-v1/status")
    if "/api/alliance/database-rectification-v1/run" not in routes:
        @core.post("/api/alliance/database-rectification-v1/run")
        async def rect_run(request: Request):
            _auth(core,request); return JSONResponse(_run(core))
        added.append("/api/alliance/database-rectification-v1/run")
    if "/alliance/admin/database-rectification-v1" not in routes:
        @core.get("/alliance/admin/database-rectification-v1",response_class=HTMLResponse)
        async def rect_page(request: Request):
            _auth(core,request); return HTMLResponse(_page(core))
        added.append("/alliance/admin/database-rectification-v1")
    if not getattr(core.state,"alliance_rectification_worker_v1",False):
        core.state.alliance_rectification_worker_v1=True
        threading.Thread(target=_worker,args=(core,),daemon=True,name="alliance-rectification-v1").start()
    return {"status":"REGISTERED","version":VERSION,"mode":"SAFE_CANONICAL_ONLY","routes":added,"automatic_interval_seconds":RUN_EVERY_SECONDS,"raw_source_mutations":0,"master_mutations":0,"matcher_mutations":0}
