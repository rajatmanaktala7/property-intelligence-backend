from pathlib import Path
from datetime import datetime
import shutil

ROOT=Path(__file__).resolve().parent
APP=ROOT/"app.py"
MOD=ROOT/"alliance_magazine_image_v560.py"
MARKER="FOUNDATION_5_6_MAGAZINE_IMAGE_EVIDENCE_VAULT"
CAPTURE_MARKER="# FOUNDATION_5_6_CAPTURE_SOURCE_PIXELS"
MOD_CONTENT='from __future__ import annotations\n\nimport base64\nimport hashlib\nimport html\nimport json\nimport os\nimport threading\nimport time\nimport urllib.request\nfrom pathlib import Path\n\nfrom fastapi.responses import HTMLResponse\nfrom sqlalchemy import text\n\nimport alliance_magazine_challenger_v514 as student\n\nVERSION = "5.6.0-ALLIANCE-MAGAZINE-IMAGE-EVIDENCE-VAULT-READINESS"\nMODE = "IMMUTABLE_PIXEL_EVIDENCE_CAPTURE_AUTO_BACKFILL_CERTIFICATION_READINESS_NO_CANONICAL_WRITES"\nEXPECTED_STUDENT_VERSION = "5.1.4-ALLIANCE-MAGAZINE-CHALLENGER-V3-FAILURE-CLOSURE"\nEXPECTED_V4_EXAM_ID = "MAGAZINE_FRESH_BLIND_V4_550_2026_09_03"\nEXPECTED_V4_STATUS = "AUTOMATED_INDEPENDENT_MAGAZINE_V4_PASS"\n\nSTATE = {"status":"NOT_STARTED","result":None,"last_error":None}\n_STARTED = False\n_LOCK = threading.Lock()\n\nDDL = [\n"""CREATE TABLE IF NOT EXISTS pi_source_evidence_vault(\n    evidence_id BIGSERIAL PRIMARY KEY,\n    source_id BIGINT,\n    source_type TEXT,\n    original_filename TEXT,\n    mime_type TEXT,\n    byte_size BIGINT NOT NULL,\n    sha256 TEXT NOT NULL UNIQUE,\n    content BYTEA NOT NULL,\n    origin_kind TEXT NOT NULL DEFAULT \'UPLOAD\',\n    immutable BOOLEAN NOT NULL DEFAULT TRUE,\n    captured_at TIMESTAMPTZ DEFAULT NOW()\n)""",\n"""CREATE INDEX IF NOT EXISTS idx_pi_source_evidence_vault_source_id\n   ON pi_source_evidence_vault(source_id)""",\n"""CREATE TABLE IF NOT EXISTS alliance_magazine_image_readiness_runs(\n    run_id BIGSERIAL PRIMARY KEY,\n    version TEXT NOT NULL,\n    student_version TEXT NOT NULL,\n    v4_exam_id TEXT NOT NULL,\n    v4_status TEXT,\n    retained_evidence_files INTEGER DEFAULT 0,\n    magazine_candidate_files INTEGER DEFAULT 0,\n    image_files INTEGER DEFAULT 0,\n    pdf_files INTEGER DEFAULT 0,\n    backfilled_files INTEGER DEFAULT 0,\n    status TEXT NOT NULL,\n    result JSONB NOT NULL,\n    created_at TIMESTAMPTZ DEFAULT NOW()\n)"""\n]\n\nALLOWED_MIME = {"image/jpeg","image/png","image/webp","application/pdf"}\n\ndef _app(core):\n    return getattr(core,"app",None) or core\n\ndef _engine(core):\n    return getattr(core,"engine",None)\n\ndef _route_exists(app,path):\n    try:\n        return any(getattr(r,"path",None)==path for r in app.routes)\n    except Exception:\n        return False\n\ndef _install(engine):\n    with engine.begin() as c:\n        for stmt in DDL:\n            c.execute(text(stmt))\n\ndef _sha(data: bytes):\n    return hashlib.sha256(data).hexdigest()\n\ndef _guess_mime(filename, mime):\n    m=(mime or "").lower().strip()\n    if m in ALLOWED_MIME:\n        return m\n    ext=Path(filename or "").suffix.lower()\n    return {\n        ".jpg":"image/jpeg",".jpeg":"image/jpeg",".png":"image/png",\n        ".webp":"image/webp",".pdf":"application/pdf"\n    }.get(ext,m or "application/octet-stream")\n\ndef _is_magazine(source_type="", filename="", source_name=""):\n    n=" ".join([str(source_type or ""),str(filename or ""),str(source_name or "")]).lower()\n    return "magazine" in n or "classified" in n\n\ndef capture_bytes(engine, source_id, data: bytes, source_type="", filename="", mime="", origin_kind="UPLOAD"):\n    if not data:\n        return {"status":"SKIPPED","reason":"EMPTY"}\n    mime=_guess_mime(filename,mime)\n    if mime not in ALLOWED_MIME:\n        return {"status":"SKIPPED","reason":"UNSUPPORTED_MIME","mime":mime}\n    digest=_sha(data)\n    _install(engine)\n    with engine.begin() as c:\n        old=c.execute(text("SELECT evidence_id FROM pi_source_evidence_vault WHERE sha256=:s"),{"s":digest}).scalar()\n        if old:\n            return {"status":"EXISTS","evidence_id":int(old),"sha256":digest}\n        eid=c.execute(text("""\n            INSERT INTO pi_source_evidence_vault(\n                source_id,source_type,original_filename,mime_type,byte_size,sha256,content,origin_kind,immutable\n            ) VALUES(:sid,:st,:fn,:m,:n,:s,:b,:o,TRUE)\n            RETURNING evidence_id\n        """),{"sid":source_id,"st":source_type,"fn":filename,"m":mime,"n":len(data),"s":digest,"b":data,"o":origin_kind}).scalar_one()\n    return {"status":"CAPTURED","evidence_id":int(eid),"sha256":digest,"byte_size":len(data)}\n\ndef capture_file(engine, source_id, path, source_type="", filename="", mime=""):\n    try:\n        p=Path(path)\n        if not p.exists() or not p.is_file():\n            return {"status":"SKIPPED","reason":"FILE_NOT_FOUND"}\n        return capture_bytes(engine,source_id,p.read_bytes(),source_type,filename or p.name,mime,"UPLOAD")\n    except Exception as exc:\n        return {"status":"ERROR","error":f"{type(exc).__name__}: {exc}"}\n\ndef _table_exists(engine, name):\n    with engine.connect() as c:\n        return bool(c.execute(text("""\n          SELECT EXISTS(\n            SELECT 1 FROM information_schema.tables\n            WHERE table_schema=current_schema() AND table_name=:t\n          )\n        """),{"t":name}).scalar())\n\ndef _v4_result(engine):\n    if not _table_exists(engine,"alliance_magazine_fresh_v4_exams"):\n        return None\n    with engine.connect() as c:\n        row=c.execute(text("""\n          SELECT exam_id,status,student_version,student_source_sha256,result\n          FROM alliance_magazine_fresh_v4_exams\n          WHERE exam_id=:e LIMIT 1\n        """),{"e":EXPECTED_V4_EXAM_ID}).first()\n    return dict(row._mapping) if row else None\n\ndef _source_rows(engine):\n    if not _table_exists(engine,"pi_sources"):\n        return []\n    with engine.connect() as c:\n        rows=c.execute(text("""\n          SELECT id,source_type,source_name,source_reference,original_filename,mime_type\n          FROM pi_sources\n          ORDER BY id DESC\n          LIMIT 5000\n        """)).all()\n    return [dict(x._mapping) for x in rows]\n\ndef _download_reference(ref, max_bytes=55*1024*1024):\n    ref=str(ref or "").strip()\n    if not ref:\n        return None\n    if ref.startswith("data:"):\n        try:\n            head,payload=ref.split(",",1)\n            if ";base64" not in head:\n                return None\n            data=base64.b64decode(payload)\n            return data if len(data)<=max_bytes else None\n        except Exception:\n            return None\n    if ref.startswith("http://") or ref.startswith("https://"):\n        try:\n            req=urllib.request.Request(ref,headers={"User-Agent":"AllianceEvidenceVault/5.6"})\n            with urllib.request.urlopen(req,timeout=20) as resp:\n                data=resp.read(max_bytes+1)\n                if len(data)>max_bytes:\n                    return None\n                return data\n        except Exception:\n            return None\n    if ref.startswith("file://"):\n        ref=ref[7:]\n    try:\n        p=Path(ref)\n        if p.exists() and p.is_file() and p.stat().st_size<=max_bytes:\n            return p.read_bytes()\n    except Exception:\n        pass\n    return None\n\ndef auto_backfill(engine):\n    captured=0\n    attempted=0\n    details=[]\n    for s in _source_rows(engine):\n        ref=s.get("source_reference")\n        if not ref:\n            continue\n        mime=_guess_mime(s.get("original_filename"),s.get("mime_type"))\n        if mime not in ALLOWED_MIME:\n            continue\n        txt=str(ref).strip()\n        if not (txt.startswith(("http://","https://","file://","data:")) or Path(txt).suffix.lower() in {".jpg",".jpeg",".png",".webp",".pdf"}):\n            continue\n        attempted += 1\n        data=_download_reference(txt)\n        if not data:\n            details.append({"source_id":s["id"],"status":"UNAVAILABLE_REFERENCE"})\n            continue\n        r=capture_bytes(engine,s["id"],data,s.get("source_type"),s.get("original_filename"),mime,"REFERENCE_BACKFILL")\n        details.append({"source_id":s["id"],**r})\n        if r.get("status")=="CAPTURED":\n            captured += 1\n    return {"attempted":attempted,"captured":captured,"details":details[:30]}\n\ndef _vault_inventory(engine):\n    with engine.connect() as c:\n        rows=c.execute(text("""\n          SELECT v.evidence_id,v.source_id,v.source_type,v.original_filename,v.mime_type,\n                 v.byte_size,v.sha256,v.origin_kind,\n                 COALESCE(s.source_name,\'\') AS source_name\n          FROM pi_source_evidence_vault v\n          LEFT JOIN pi_sources s ON s.id=v.source_id\n          ORDER BY v.evidence_id DESC\n        """)).all()\n    result=[dict(x._mapping) for x in rows]\n    candidates=[r for r in result if _is_magazine(r.get("source_type"),r.get("original_filename"),r.get("source_name"))]\n    return result,candidates\n\ndef run_once(core):\n    if not _LOCK.acquire(blocking=False):\n        return {"status":"SKIPPED","reason":"READINESS_ALREADY_RUNNING"}\n    try:\n        engine=_engine(core)\n        if engine is None:\n            raise RuntimeError("Core engine unavailable")\n        _install(engine)\n\n        if student.VERSION != EXPECTED_STUDENT_VERSION:\n            raise RuntimeError(f"Certified magazine student version changed: {student.VERSION}")\n\n        v4=_v4_result(engine)\n        if not v4:\n            result={"version":VERSION,"mode":MODE,"status":"BLOCKED_V4_CERTIFICATION_NOT_FOUND","next_gate":"RESTORE_V4_CERTIFICATION"}\n        elif v4.get("status") != EXPECTED_V4_STATUS:\n            result={"version":VERSION,"mode":MODE,"status":"BLOCKED_V4_NOT_PASS",\n                    "v4":{"exam_id":v4.get("exam_id"),"status":v4.get("status"),"student_version":v4.get("student_version")},\n                    "next_gate":"V4_MUST_PASS_FIRST"}\n        else:\n            backfill=auto_backfill(engine)\n            all_files,candidates=_vault_inventory(engine)\n            images=sum(1 for x in candidates if str(x.get("mime_type","")).startswith("image/"))\n            pdfs=sum(1 for x in candidates if x.get("mime_type")=="application/pdf")\n\n            if candidates:\n                status="IMAGE_EVIDENCE_READY_FOR_FRESH_PIXEL_EXAM"\n                next_gate="RUN_FRESH_MAGAZINE_PIXEL_EXAM"\n            else:\n                status="BLOCKED_NO_RETAINED_MAGAZINE_PIXELS"\n                next_gate="AUTOMATICALLY_CAPTURE_NEXT_MAGAZINE_UPLOAD_THEN_RUN_PIXEL_EXAM"\n\n            result={\n              "version":VERSION,\n              "mode":MODE,\n              "status":status,\n              "certified_semantic_student":{"version":student.VERSION,"frozen":True},\n              "v4_certification":{"exam_id":v4.get("exam_id"),"status":v4.get("status"),"student_version":v4.get("student_version")},\n              "evidence_vault":{\n                  "total_retained_files":len(all_files),\n                  "magazine_candidate_files":len(candidates),\n                  "image_files":images,\n                  "pdf_files":pdfs,\n                  "auto_backfill_attempted":backfill["attempted"],\n                  "auto_backfill_captured":backfill["captured"],\n                  "candidate_samples":[{k:r.get(k) for k in ("evidence_id","source_id","source_type","original_filename","mime_type","byte_size","sha256","origin_kind")} for r in candidates[:10]]\n              },\n              "why_blocked_if_zero":"Existing structured magazine rows cannot recreate original page pixels. Certification must use original immutable image/PDF bytes, never database text as substitute truth.",\n              "future_capture":"Enabled: /api/ingest/file now stores every uploaded image/PDF immutably in pi_source_evidence_vault before background AI processing.",\n              "next_gate":next_gate,\n              "safety":{"canonical_property_writes":0,"canonical_requirement_writes":0,"gold_mutations":0,"champion_mutations":0,"certified_student_mutations":0,"source_row_mutations":0,"evidence_vault_additive_writes":backfill["captured"]}\n            }\n\n        with engine.begin() as c:\n            c.execute(text("""\n              INSERT INTO alliance_magazine_image_readiness_runs(\n                version,student_version,v4_exam_id,v4_status,retained_evidence_files,\n                magazine_candidate_files,image_files,pdf_files,backfilled_files,status,result\n              ) VALUES(\n                :v,:sv,:eid,:vs,:rf,:mf,:im,:pdf,:bf,:st,CAST(:r AS JSONB)\n              )\n            """),{\n              "v":VERSION,"sv":student.VERSION,"eid":EXPECTED_V4_EXAM_ID,\n              "vs":(v4 or {}).get("status"),\n              "rf":result.get("evidence_vault",{}).get("total_retained_files",0),\n              "mf":result.get("evidence_vault",{}).get("magazine_candidate_files",0),\n              "im":result.get("evidence_vault",{}).get("image_files",0),\n              "pdf":result.get("evidence_vault",{}).get("pdf_files",0),\n              "bf":result.get("evidence_vault",{}).get("auto_backfill_captured",0),\n              "st":result["status"],"r":json.dumps(result,ensure_ascii=False)\n            })\n\n        STATE["status"]=result["status"];STATE["result"]=result;STATE["last_error"]=None\n        return result\n    except Exception as exc:\n        STATE["status"]="ERROR";STATE["last_error"]=f"{type(exc).__name__}: {exc}"\n        return {"version":VERSION,"status":"ERROR","error":STATE["last_error"]}\n    finally:\n        _LOCK.release()\n\ndef status(core):\n    return run_once(core)\n\ndef dashboard(core):\n    s=status(core)\n    ev=s.get("evidence_vault") or {}\n    return f"""<!doctype html><html><head><meta charset=\'utf-8\'><meta name=\'viewport\' content=\'width=device-width,initial-scale=1\'>\n<title>Magazine Image Evidence 5.6</title><style>\nbody{{font-family:Arial;background:#f5f7fb;color:#172033;margin:0}}header{{background:#102235;color:white;padding:18px}}\n.wrap{{max-width:1280px;margin:auto;padding:18px}}.card{{background:white;border:1px solid #e2e8f0;border-radius:12px;padding:14px;margin-bottom:12px}}\npre{{white-space:pre-wrap;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px;overflow:auto}}</style></head>\n<body><header><b>Alliance Magazine Image Evidence Gate 5.6</b><br><small>Original pixels are truth · immutable evidence vault · no fake certification from extracted text</small></header>\n<div class=\'wrap\'><div class=\'card\'><b>{html.escape(str(s.get("status")))}</b><br>\nRetained files: {html.escape(str(ev.get("total_retained_files",0)))} · Magazine candidates: {html.escape(str(ev.get("magazine_candidate_files",0)))} ·\nImages: {html.escape(str(ev.get("image_files",0)))} · PDFs: {html.escape(str(ev.get("pdf_files",0)))}</div>\n<pre>{html.escape(json.dumps(s,ensure_ascii=False,indent=2))}</pre></div></body></html>"""\n\ndef register(core):\n    app=_app(core)\n    if not _route_exists(app,"/api/property-brain/magazine-image-v560/status"):\n        @app.get("/api/property-brain/magazine-image-v560/status")\n        def _status():\n            return status(core)\n    if not _route_exists(app,"/property-brain/magazine-image-v560"):\n        @app.get("/property-brain/magazine-image-v560",response_class=HTMLResponse)\n        def _page():\n            return HTMLResponse(dashboard(core))\n    return {"status":"REGISTERED","version":VERSION,"route":"/property-brain/magazine-image-v560"}\n\ndef _runner(core):\n    time.sleep(int(os.getenv("ALLIANCE_MAGAZINE_IMAGE_READINESS_DELAY","50")))\n    run_once(core)\n\ndef start(core):\n    global _STARTED\n    register(core)\n    if _STARTED:\n        return STATE\n    _STARTED=True\n    threading.Thread(target=_runner,args=(core,),name="magazine-image-v560",daemon=True).start()\n    return STATE\n'

def check(src,name):
    compile(src,name,"exec")

def main():
    if not APP.exists():
        raise SystemExit("app.py not found")

    check(MOD_CONTENT,str(MOD))
    MOD.write_text(MOD_CONTENT,encoding="utf-8")

    app=APP.read_text(encoding="utf-8")
    changed=False
    backup=None

    if CAPTURE_MARKER not in app:
        needle = """        sid=source_row(
            source_type.upper(),
            source_name or filename,
            filename,
            mime
        )

        # CSV is processed directly after streamed upload.
"""
        replacement = """        sid=source_row(
            source_type.upper(),
            source_name or filename,
            filename,
            mime
        )

        # FOUNDATION_5_6_CAPTURE_SOURCE_PIXELS
        # Preserve original image/PDF bytes immutably BEFORE any AI processing.
        # This is additive evidence storage only; it does not mutate canonical records.
        if mime in {"image/jpeg","image/png","image/webp","application/pdf"} or filename.lower().endswith((".jpg",".jpeg",".png",".webp",".pdf")):
            try:
                import alliance_magazine_image_v560 as _source_evidence_v560
                _source_evidence_v560.capture_file(
                    engine, sid, path, source_type.upper(), filename, mime
                )
            except Exception as _evidence_exc:
                print("Source evidence capture warning:", _evidence_exc)

        # CSV is processed directly after streamed upload.
"""
        if needle not in app:
            raise SystemExit("SAFE PATCH ABORTED: exact ingest_file source_row block not found. app.py left unchanged.")
        backup=ROOT/f"app-before-magazine-image-v560-{datetime.now().strftime('%Y%m%d-%H%M%S')}.py"
        shutil.copy2(APP,backup)
        app=app.replace(needle,replacement,1)
        changed=True

    if MARKER not in app:
        if backup is None:
            backup=ROOT/f"app-before-magazine-image-v560-{datetime.now().strftime('%Y%m%d-%H%M%S')}.py"
            shutil.copy2(APP,backup)
        app=app.rstrip()+"""

# FOUNDATION_5_6_MAGAZINE_IMAGE_EVIDENCE_VAULT
try:
    import sys as _m560_sys
    import alliance_magazine_image_v560 as _mag_img_v560
    _mag_img_v560.start(_m560_sys.modules[__name__])
    print("Alliance Magazine Image Evidence Gate 5.6: registered")
except Exception as _m560_exc:
    print("Alliance Magazine Image Evidence Gate 5.6 registration warning:", _m560_exc)
"""
        changed=True

    check(app,str(APP))
    if changed:
        APP.write_text(app,encoding="utf-8")
        print("Backup:",backup)
    else:
        print("5.6 app integration already present.")

    for p in (APP,MOD):
        check(p.read_text(encoding="utf-8"),str(p))

    print("FOUNDATION 5.6 INSTALLED")
    print("Certified semantic Student 5.1.4 remains unchanged.")
    print("Every future uploaded JPG/PNG/WEBP/PDF is now retained immutably before AI processing.")
    print("The gate automatically attempts to recover any old source_reference files/URLs.")
    print("Dashboard: /property-brain/magazine-image-v560")
    print("If no original magazine pixels exist, status will honestly BLOCK rather than certify from database text.")

if __name__=="__main__":
    main()
