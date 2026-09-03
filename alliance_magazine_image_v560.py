from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import threading
import time
import urllib.request
from pathlib import Path

from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_magazine_challenger_v514 as student

VERSION = "5.6.0-ALLIANCE-MAGAZINE-IMAGE-EVIDENCE-VAULT-READINESS"
MODE = "IMMUTABLE_PIXEL_EVIDENCE_CAPTURE_AUTO_BACKFILL_CERTIFICATION_READINESS_NO_CANONICAL_WRITES"
EXPECTED_STUDENT_VERSION = "5.1.4-ALLIANCE-MAGAZINE-CHALLENGER-V3-FAILURE-CLOSURE"
EXPECTED_V4_EXAM_ID = "MAGAZINE_FRESH_BLIND_V4_550_2026_09_03"
EXPECTED_V4_STATUS = "AUTOMATED_INDEPENDENT_MAGAZINE_V4_PASS"

STATE = {"status":"NOT_STARTED","result":None,"last_error":None}
_STARTED = False
_LOCK = threading.Lock()

DDL = [
"""CREATE TABLE IF NOT EXISTS pi_source_evidence_vault(
    evidence_id BIGSERIAL PRIMARY KEY,
    source_id BIGINT,
    source_type TEXT,
    original_filename TEXT,
    mime_type TEXT,
    byte_size BIGINT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    content BYTEA NOT NULL,
    origin_kind TEXT NOT NULL DEFAULT 'UPLOAD',
    immutable BOOLEAN NOT NULL DEFAULT TRUE,
    captured_at TIMESTAMPTZ DEFAULT NOW()
)""",
"""CREATE INDEX IF NOT EXISTS idx_pi_source_evidence_vault_source_id
   ON pi_source_evidence_vault(source_id)""",
"""CREATE TABLE IF NOT EXISTS alliance_magazine_image_readiness_runs(
    run_id BIGSERIAL PRIMARY KEY,
    version TEXT NOT NULL,
    student_version TEXT NOT NULL,
    v4_exam_id TEXT NOT NULL,
    v4_status TEXT,
    retained_evidence_files INTEGER DEFAULT 0,
    magazine_candidate_files INTEGER DEFAULT 0,
    image_files INTEGER DEFAULT 0,
    pdf_files INTEGER DEFAULT 0,
    backfilled_files INTEGER DEFAULT 0,
    status TEXT NOT NULL,
    result JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
)"""
]

ALLOWED_MIME = {"image/jpeg","image/png","image/webp","application/pdf"}

def _app(core):
    return getattr(core,"app",None) or core

def _engine(core):
    return getattr(core,"engine",None)

def _route_exists(app,path):
    try:
        return any(getattr(r,"path",None)==path for r in app.routes)
    except Exception:
        return False

def _install(engine):
    with engine.begin() as c:
        for stmt in DDL:
            c.execute(text(stmt))

def _sha(data: bytes):
    return hashlib.sha256(data).hexdigest()

def _guess_mime(filename, mime):
    m=(mime or "").lower().strip()
    if m in ALLOWED_MIME:
        return m
    ext=Path(filename or "").suffix.lower()
    return {
        ".jpg":"image/jpeg",".jpeg":"image/jpeg",".png":"image/png",
        ".webp":"image/webp",".pdf":"application/pdf"
    }.get(ext,m or "application/octet-stream")

def _is_magazine(source_type="", filename="", source_name=""):
    n=" ".join([str(source_type or ""),str(filename or ""),str(source_name or "")]).lower()
    return "magazine" in n or "classified" in n

def capture_bytes(engine, source_id, data: bytes, source_type="", filename="", mime="", origin_kind="UPLOAD"):
    if not data:
        return {"status":"SKIPPED","reason":"EMPTY"}
    mime=_guess_mime(filename,mime)
    if mime not in ALLOWED_MIME:
        return {"status":"SKIPPED","reason":"UNSUPPORTED_MIME","mime":mime}
    digest=_sha(data)
    _install(engine)
    with engine.begin() as c:
        old=c.execute(text("SELECT evidence_id FROM pi_source_evidence_vault WHERE sha256=:s"),{"s":digest}).scalar()
        if old:
            return {"status":"EXISTS","evidence_id":int(old),"sha256":digest}
        eid=c.execute(text("""
            INSERT INTO pi_source_evidence_vault(
                source_id,source_type,original_filename,mime_type,byte_size,sha256,content,origin_kind,immutable
            ) VALUES(:sid,:st,:fn,:m,:n,:s,:b,:o,TRUE)
            RETURNING evidence_id
        """),{"sid":source_id,"st":source_type,"fn":filename,"m":mime,"n":len(data),"s":digest,"b":data,"o":origin_kind}).scalar_one()
    return {"status":"CAPTURED","evidence_id":int(eid),"sha256":digest,"byte_size":len(data)}

def capture_file(engine, source_id, path, source_type="", filename="", mime=""):
    try:
        p=Path(path)
        if not p.exists() or not p.is_file():
            return {"status":"SKIPPED","reason":"FILE_NOT_FOUND"}
        return capture_bytes(engine,source_id,p.read_bytes(),source_type,filename or p.name,mime,"UPLOAD")
    except Exception as exc:
        return {"status":"ERROR","error":f"{type(exc).__name__}: {exc}"}

def _table_exists(engine, name):
    with engine.connect() as c:
        return bool(c.execute(text("""
          SELECT EXISTS(
            SELECT 1 FROM information_schema.tables
            WHERE table_schema=current_schema() AND table_name=:t
          )
        """),{"t":name}).scalar())

def _v4_result(engine):
    if not _table_exists(engine,"alliance_magazine_fresh_v4_exams"):
        return None
    with engine.connect() as c:
        row=c.execute(text("""
          SELECT exam_id,status,student_version,student_source_sha256,result
          FROM alliance_magazine_fresh_v4_exams
          WHERE exam_id=:e LIMIT 1
        """),{"e":EXPECTED_V4_EXAM_ID}).first()
    return dict(row._mapping) if row else None

def _source_rows(engine):
    if not _table_exists(engine,"pi_sources"):
        return []
    with engine.connect() as c:
        rows=c.execute(text("""
          SELECT id,source_type,source_name,source_reference,original_filename,mime_type
          FROM pi_sources
          ORDER BY id DESC
          LIMIT 5000
        """)).all()
    return [dict(x._mapping) for x in rows]

def _download_reference(ref, max_bytes=55*1024*1024):
    ref=str(ref or "").strip()
    if not ref:
        return None
    if ref.startswith("data:"):
        try:
            head,payload=ref.split(",",1)
            if ";base64" not in head:
                return None
            data=base64.b64decode(payload)
            return data if len(data)<=max_bytes else None
        except Exception:
            return None
    if ref.startswith("http://") or ref.startswith("https://"):
        try:
            req=urllib.request.Request(ref,headers={"User-Agent":"AllianceEvidenceVault/5.6"})
            with urllib.request.urlopen(req,timeout=20) as resp:
                data=resp.read(max_bytes+1)
                if len(data)>max_bytes:
                    return None
                return data
        except Exception:
            return None
    if ref.startswith("file://"):
        ref=ref[7:]
    try:
        p=Path(ref)
        if p.exists() and p.is_file() and p.stat().st_size<=max_bytes:
            return p.read_bytes()
    except Exception:
        pass
    return None

def auto_backfill(engine):
    captured=0
    attempted=0
    details=[]
    for s in _source_rows(engine):
        ref=s.get("source_reference")
        if not ref:
            continue
        mime=_guess_mime(s.get("original_filename"),s.get("mime_type"))
        if mime not in ALLOWED_MIME:
            continue
        txt=str(ref).strip()
        if not (txt.startswith(("http://","https://","file://","data:")) or Path(txt).suffix.lower() in {".jpg",".jpeg",".png",".webp",".pdf"}):
            continue
        attempted += 1
        data=_download_reference(txt)
        if not data:
            details.append({"source_id":s["id"],"status":"UNAVAILABLE_REFERENCE"})
            continue
        r=capture_bytes(engine,s["id"],data,s.get("source_type"),s.get("original_filename"),mime,"REFERENCE_BACKFILL")
        details.append({"source_id":s["id"],**r})
        if r.get("status")=="CAPTURED":
            captured += 1
    return {"attempted":attempted,"captured":captured,"details":details[:30]}

def _vault_inventory(engine):
    with engine.connect() as c:
        rows=c.execute(text("""
          SELECT v.evidence_id,v.source_id,v.source_type,v.original_filename,v.mime_type,
                 v.byte_size,v.sha256,v.origin_kind,
                 COALESCE(s.source_name,'') AS source_name
          FROM pi_source_evidence_vault v
          LEFT JOIN pi_sources s ON s.id=v.source_id
          ORDER BY v.evidence_id DESC
        """)).all()
    result=[dict(x._mapping) for x in rows]
    candidates=[r for r in result if _is_magazine(r.get("source_type"),r.get("original_filename"),r.get("source_name"))]
    return result,candidates

def run_once(core):
    if not _LOCK.acquire(blocking=False):
        return {"status":"SKIPPED","reason":"READINESS_ALREADY_RUNNING"}
    try:
        engine=_engine(core)
        if engine is None:
            raise RuntimeError("Core engine unavailable")
        _install(engine)

        if student.VERSION != EXPECTED_STUDENT_VERSION:
            raise RuntimeError(f"Certified magazine student version changed: {student.VERSION}")

        v4=_v4_result(engine)
        if not v4:
            result={"version":VERSION,"mode":MODE,"status":"BLOCKED_V4_CERTIFICATION_NOT_FOUND","next_gate":"RESTORE_V4_CERTIFICATION"}
        elif v4.get("status") != EXPECTED_V4_STATUS:
            result={"version":VERSION,"mode":MODE,"status":"BLOCKED_V4_NOT_PASS",
                    "v4":{"exam_id":v4.get("exam_id"),"status":v4.get("status"),"student_version":v4.get("student_version")},
                    "next_gate":"V4_MUST_PASS_FIRST"}
        else:
            backfill=auto_backfill(engine)
            all_files,candidates=_vault_inventory(engine)
            images=sum(1 for x in candidates if str(x.get("mime_type","")).startswith("image/"))
            pdfs=sum(1 for x in candidates if x.get("mime_type")=="application/pdf")

            if candidates:
                status="IMAGE_EVIDENCE_READY_FOR_FRESH_PIXEL_EXAM"
                next_gate="RUN_FRESH_MAGAZINE_PIXEL_EXAM"
            else:
                status="BLOCKED_NO_RETAINED_MAGAZINE_PIXELS"
                next_gate="AUTOMATICALLY_CAPTURE_NEXT_MAGAZINE_UPLOAD_THEN_RUN_PIXEL_EXAM"

            result={
              "version":VERSION,
              "mode":MODE,
              "status":status,
              "certified_semantic_student":{"version":student.VERSION,"frozen":True},
              "v4_certification":{"exam_id":v4.get("exam_id"),"status":v4.get("status"),"student_version":v4.get("student_version")},
              "evidence_vault":{
                  "total_retained_files":len(all_files),
                  "magazine_candidate_files":len(candidates),
                  "image_files":images,
                  "pdf_files":pdfs,
                  "auto_backfill_attempted":backfill["attempted"],
                  "auto_backfill_captured":backfill["captured"],
                  "candidate_samples":[{k:r.get(k) for k in ("evidence_id","source_id","source_type","original_filename","mime_type","byte_size","sha256","origin_kind")} for r in candidates[:10]]
              },
              "why_blocked_if_zero":"Existing structured magazine rows cannot recreate original page pixels. Certification must use original immutable image/PDF bytes, never database text as substitute truth.",
              "future_capture":"Enabled: /api/ingest/file now stores every uploaded image/PDF immutably in pi_source_evidence_vault before background AI processing.",
              "next_gate":next_gate,
              "safety":{"canonical_property_writes":0,"canonical_requirement_writes":0,"gold_mutations":0,"champion_mutations":0,"certified_student_mutations":0,"source_row_mutations":0,"evidence_vault_additive_writes":backfill["captured"]}
            }

        with engine.begin() as c:
            c.execute(text("""
              INSERT INTO alliance_magazine_image_readiness_runs(
                version,student_version,v4_exam_id,v4_status,retained_evidence_files,
                magazine_candidate_files,image_files,pdf_files,backfilled_files,status,result
              ) VALUES(
                :v,:sv,:eid,:vs,:rf,:mf,:im,:pdf,:bf,:st,CAST(:r AS JSONB)
              )
            """),{
              "v":VERSION,"sv":student.VERSION,"eid":EXPECTED_V4_EXAM_ID,
              "vs":(v4 or {}).get("status"),
              "rf":result.get("evidence_vault",{}).get("total_retained_files",0),
              "mf":result.get("evidence_vault",{}).get("magazine_candidate_files",0),
              "im":result.get("evidence_vault",{}).get("image_files",0),
              "pdf":result.get("evidence_vault",{}).get("pdf_files",0),
              "bf":result.get("evidence_vault",{}).get("auto_backfill_captured",0),
              "st":result["status"],"r":json.dumps(result,ensure_ascii=False)
            })

        STATE["status"]=result["status"];STATE["result"]=result;STATE["last_error"]=None
        return result
    except Exception as exc:
        STATE["status"]="ERROR";STATE["last_error"]=f"{type(exc).__name__}: {exc}"
        return {"version":VERSION,"status":"ERROR","error":STATE["last_error"]}
    finally:
        _LOCK.release()

def status(core):
    return run_once(core)

def dashboard(core):
    s=status(core)
    ev=s.get("evidence_vault") or {}
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Magazine Image Evidence 5.6</title><style>
body{{font-family:Arial;background:#f5f7fb;color:#172033;margin:0}}header{{background:#102235;color:white;padding:18px}}
.wrap{{max-width:1280px;margin:auto;padding:18px}}.card{{background:white;border:1px solid #e2e8f0;border-radius:12px;padding:14px;margin-bottom:12px}}
pre{{white-space:pre-wrap;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px;overflow:auto}}</style></head>
<body><header><b>Alliance Magazine Image Evidence Gate 5.6</b><br><small>Original pixels are truth · immutable evidence vault · no fake certification from extracted text</small></header>
<div class='wrap'><div class='card'><b>{html.escape(str(s.get("status")))}</b><br>
Retained files: {html.escape(str(ev.get("total_retained_files",0)))} · Magazine candidates: {html.escape(str(ev.get("magazine_candidate_files",0)))} ·
Images: {html.escape(str(ev.get("image_files",0)))} · PDFs: {html.escape(str(ev.get("pdf_files",0)))}</div>
<pre>{html.escape(json.dumps(s,ensure_ascii=False,indent=2))}</pre></div></body></html>"""

def register(core):
    app=_app(core)
    if not _route_exists(app,"/api/property-brain/magazine-image-v560/status"):
        @app.get("/api/property-brain/magazine-image-v560/status")
        def _status():
            return status(core)
    if not _route_exists(app,"/property-brain/magazine-image-v560"):
        @app.get("/property-brain/magazine-image-v560",response_class=HTMLResponse)
        def _page():
            return HTMLResponse(dashboard(core))
    return {"status":"REGISTERED","version":VERSION,"route":"/property-brain/magazine-image-v560"}

def _runner(core):
    time.sleep(int(os.getenv("ALLIANCE_MAGAZINE_IMAGE_READINESS_DELAY","50")))
    run_once(core)

def start(core):
    global _STARTED
    register(core)
    if _STARTED:
        return STATE
    _STARTED=True
    threading.Thread(target=_runner,args=(core,),name="magazine-image-v560",daemon=True).start()
    return STATE
