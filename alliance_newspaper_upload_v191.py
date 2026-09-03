from __future__ import annotations
import os, tempfile, uuid
from fastapi import BackgroundTasks, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION = "19.1-NEWSPAPER-UPLOAD-RELIABILITY"

def _auth(core, req):
    try:
        core.need_login(req)
        return None
    except Exception:
        return RedirectResponse("/login", 303)

def _caps(core):
    return {
        "source_row": callable(getattr(core, "source_row", None)),
        "create_job": callable(getattr(core, "create_job", None)),
        "v10_tables": callable(getattr(core, "_ensure_v10_tables", None)),
        "v10_worker": callable(getattr(core, "_v10_file_worker", None)),
        "generic_worker": callable(getattr(core, "run_file_job", None)),
    }

def _remove_capture_routes(app):
    targets = {"/capture-intelligence", "/newspaper", "/newspaper-upload", "/magazine-capture"}
    kept, removed = [], []
    for route in app.router.routes:
        path = getattr(route, "path", None)
        methods = set(getattr(route, "methods", set()) or set())
        if path in targets and "GET" in methods:
            removed.append(path)
        else:
            kept.append(route)
    app.router.routes[:] = kept
    return removed

UI = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Newspaper Upload</title>
<style>
*{box-sizing:border-box}body{font-family:Arial;margin:0;background:#f4f7fb;color:#172437}
header{background:#102235;color:#fff;padding:18px 22px}.w{max-width:1050px;margin:auto;padding:18px}
.card{background:#fff;border:1px solid #dfe7f0;border-radius:12px;padding:16px;margin:12px 0}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.btn,button{display:inline-block;border:0;border-radius:8px;padding:10px 13px;background:#1677ff;color:#fff;text-decoration:none;font-weight:700;cursor:pointer}
.gray{background:#edf2f7;color:#24364b}input,select{width:100%;padding:10px;border:1px solid #cbd6e2;border-radius:8px}
.drop{border:2px dashed #9fb1c5;border-radius:12px;padding:26px;text-align:center;background:#fafcff;cursor:pointer;min-height:145px}
.drop.over{outline:3px solid #b6d0ff}.drop input{display:none}.stage{padding:10px 12px;border-radius:8px;background:#f7f9fc;margin:8px 0}
.ok{color:#08734b;font-weight:700}.err{color:#a11;font-weight:700}.muted{color:#62748a;font-size:12px}
@media(max-width:760px){.grid{grid-template-columns:1fr}}
</style></head><body>
<header><b>Newspaper / Magazine Upload</b><br><small>Reliable V19.1 uploader</small></header>
<div class="w">
<div class="card"><a class="btn gray" href="/workspace">Dashboard</a> <a class="btn gray" href="/manual-property-database-v178">Manual Property Database</a></div>
<form id="form" class="card">
<div class="grid">
<label>Source Type<select name="source_type" id="source_type"><option>NEWSPAPER</option><option>MAGAZINE</option></select></label>
<label>Optional Note<input name="note" placeholder="e.g. Property Informer Sep 2026 / Page 12"></label>
</div>
<input type="hidden" name="capture_mode" value="V19_1_RELIABLE_UPLOAD">
<div id="drop" class="drop" tabindex="0"><b>Choose / Drop newspaper or magazine</b><p>Copied image: click here and press Ctrl+V</p><div class="muted">PDF, JPG, JPEG, PNG, WEBP</div><input id="file" name="file" type="file" accept=".pdf,application/pdf,image/jpeg,image/png,image/webp,image/*"><div id="preview"></div></div>
<p><button id="go">Upload & Extract</button></p><div id="msg" class="stage">Ready.</div>
</form>
<div class="card"><h3>AI Extraction Status</h3><div id="jobs">Loading...</div></div>
</div>
<script>
function E(v){return String(v??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
const form=document.getElementById('form'),zone=document.getElementById('drop'),input=document.getElementById('file'),preview=document.getElementById('preview'),msg=document.getElementById('msg'),go=document.getElementById('go'),jobs=document.getElementById('jobs'),sourceType=document.getElementById('source_type');
function setFile(f){if(!f)return;const dt=new DataTransfer();dt.items.add(f);input.files=dt.files;preview.textContent=f.name+' | '+Math.round(f.size/1024)+' KB';}
input.onchange=()=>setFile(input.files[0]);
zone.onclick=e=>{if(e.target!==input)input.click()};
['dragenter','dragover'].forEach(n=>zone.addEventListener(n,e=>{e.preventDefault();zone.classList.add('over')}));
['dragleave','drop'].forEach(n=>zone.addEventListener(n,e=>{e.preventDefault();zone.classList.remove('over')}));
zone.addEventListener('drop',e=>setFile(e.dataTransfer.files?.[0]));
zone.addEventListener('paste',e=>{const it=[...(e.clipboardData?.items||[])].find(x=>x.kind==='file'&&x.type.startsWith('image/'));if(!it){msg.className='stage err';msg.textContent='Clipboard has no image.';return}e.preventDefault();let f=it.getAsFile();const ext=(f.type.split('/')[1]||'png').replace('jpeg','jpg');f=new File([f],'clipboard-newspaper-'+Date.now()+'.'+ext,{type:f.type});setFile(f);});
function stage(x){const s=String(x.job_status||x.ingestion_status||'').toUpperCase();if(['PENDING','ACCEPTED','RECEIVED','QUEUED'].includes(s))return '1. Uploaded / Queued';if(['RUNNING','PROCESSING','EXTRACTING'].includes(s))return '2. AI Extracting';if(['COMPLETED','DONE','SUCCESS','PROCESSED'].includes(s))return '4. Completed';if(['FAILED','ERROR'].includes(s))return 'Failed';return s||'Processing';}
async function refreshJobs(){try{const r=await fetch('/api/v19-1/newspaper-status');const d=await r.json();if(!r.ok)throw new Error(d.detail||d.message||'Status failed');const a=d.rows||[];jobs.innerHTML=a.length?a.map(x=>`<div class="stage"><b>${E(stage(x))}</b> | ${E(x.original_filename||'')}<br>New records: <b>${E(x.processed_records||0)}</b> | Duplicates: ${E(x.duplicate_records||0)}${x.output_summary?'<br>'+E(x.output_summary):''}${x.error_message?'<br><span class="err">'+E(x.error_message)+'</span>':''}</div>`).join(''):'No uploads yet.'}catch(e){jobs.innerHTML='<span class="err">'+E(e.message)+'</span>'}}
form.onsubmit=async e=>{e.preventDefault();if(!input.files.length){msg.className='stage err';msg.textContent='Choose, drop or paste a file first.';return}go.disabled=true;msg.className='stage';msg.textContent='Uploading...';try{const r=await fetch('/api/v19-1/newspaper-upload',{method:'POST',body:new FormData(form)});const raw=await r.text();let d={};try{d=JSON.parse(raw)}catch(_){}if(!r.ok)throw new Error(d.detail||d.message||raw||('HTTP '+r.status));msg.className='stage ok';msg.textContent='Upload accepted. AI extraction started. Job ID: '+(d.job_id||'created');await refreshJobs()}catch(e){msg.className='stage err';msg.textContent='UPLOAD FAILED: '+e.message}finally{go.disabled=false}};
const src=new URLSearchParams(location.search).get('source_type');if(src)sourceType.value=src.toUpperCase();
refreshJobs();setInterval(refreshJobs,5000);
</script></body></html>'''

def register(wrapped):
    core = wrapped.core
    app = wrapped.app
    removed = _remove_capture_routes(app)

    @app.get("/capture-intelligence", response_class=HTMLResponse)
    def capture(req: Request):
        r = _auth(core, req)
        return r or HTMLResponse(UI)

    @app.get("/newspaper")
    def newspaper(req: Request):
        r = _auth(core, req)
        return r or RedirectResponse("/capture-intelligence?source_type=NEWSPAPER", 307)

    @app.get("/newspaper-upload")
    def newspaper_upload(req: Request):
        r = _auth(core, req)
        return r or RedirectResponse("/capture-intelligence?source_type=NEWSPAPER", 307)

    @app.get("/magazine-capture")
    def magazine_capture(req: Request):
        r = _auth(core, req)
        return r or RedirectResponse("/capture-intelligence?source_type=MAGAZINE", 307)

    @app.post("/api/v19-1/newspaper-upload")
    async def upload(bg: BackgroundTasks, req: Request, file: UploadFile = File(...), source_type: str = Form("NEWSPAPER"), capture_mode: str = Form("V19_1_RELIABLE_UPLOAD"), note: str = Form("")):
        core.need_login(req)
        caps = _caps(core)
        if not caps["source_row"] or not caps["create_job"]:
            raise HTTPException(500, "Core ingestion functions are unavailable.")

        filename = file.filename or "newspaper-upload"
        ext = os.path.splitext(filename)[1].lower()
        mime = (file.content_type or "").lower()
        allowed_ext = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
        allowed_mime = {"application/pdf", "image/jpeg", "image/png", "image/webp"}
        if ext not in allowed_ext and mime not in allowed_mime:
            raise HTTPException(400, "Upload PDF, JPG, JPEG, PNG or WEBP only.")

        max_mb = int(getattr(core, "MAX_UPLOAD_MB", 100) or 100)
        fd, path = tempfile.mkstemp(suffix=ext if ext in allowed_ext else ".bin")
        os.close(fd)
        total = 0
        try:
            with open(path, "wb") as out:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_mb * 1024 * 1024:
                        raise HTTPException(413, f"File is larger than {max_mb} MB.")
                    out.write(chunk)
            if total == 0:
                raise HTTPException(400, "Uploaded file is empty.")

            st = str(source_type or "NEWSPAPER").upper()
            sid = core.source_row(st, filename, filename, mime, reference=note)
            jid = core.create_job(sid, "V19_1_NEWSPAPER_EXTRACTION", f"{st} | {filename}")
            iid = str(uuid.uuid4())

            if caps["v10_tables"]:
                try:
                    core._ensure_v10_tables()
                    sql = (
                        "INSERT INTO pi_v10_intake_log("
                        "intake_id,source_id,job_id,source_type,original_filename,capture_mode,status,file_size,note,created_by"
                        ") VALUES(CAST(:iid AS UUID),:sid,:jid,:st,:fn,:cm,'ACCEPTED',:sz,:note,:by)"
                    )
                    with core.engine.begin() as c:
                        c.execute(text(sql), {
                            "iid": iid, "sid": sid, "jid": jid, "st": st, "fn": filename,
                            "cm": capture_mode, "sz": total, "note": note,
                            "by": getattr(core, "actor_name", lambda r: "team")(req),
                        })
                except Exception as exc:
                    print("[v19.1 intake log]", type(exc).__name__, str(exc))

            if caps["v10_worker"]:
                bg.add_task(core._v10_file_worker, sid, jid, path, mime, iid, capture_mode)
                worker = "V10_MULTIMODAL"
            elif caps["generic_worker"]:
                bg.add_task(core.run_file_job, sid, jid, path, mime)
                worker = "GENERIC_FILE_EXTRACTION"
            else:
                raise HTTPException(500, "No AI file-extraction worker is available.")

            return {"status": "ACCEPTED", "source_id": sid, "job_id": jid, "intake_id": iid, "worker": worker, "file_size": total}
        except Exception:
            try:
                os.unlink(path)
            except Exception:
                pass
            raise

    @app.get("/api/v19-1/newspaper-status")
    def status(req: Request):
        core.need_login(req)
        sql = (
            "SELECT s.id source_id,s.source_type,s.original_filename,s.ingestion_status,"
            "COALESCE(s.processed_records,0) processed_records,"
            "COALESCE(s.duplicate_records,0) duplicate_records,"
            "s.error_message source_error,s.uploaded_at,"
            "j.status job_status,j.output_summary,j.error_message job_error "
            "FROM pi_sources s LEFT JOIN LATERAL ("
            "SELECT status,output_summary,error_message FROM pi_ai_jobs j2 "
            "WHERE j2.source_id=s.id ORDER BY j2.created_at DESC LIMIT 1"
            ") j ON TRUE "
            "WHERE upper(coalesce(s.source_type,'')) IN ('NEWSPAPER','MAGAZINE') "
            "ORDER BY s.uploaded_at DESC LIMIT 20"
        )
        with core.engine.connect() as c:
            rows = c.execute(text(sql)).mappings().all()
        out = []
        for row in rows:
            x = dict(row)
            x["error_message"] = x.pop("job_error", None) or x.pop("source_error", None)
            for k, v in list(x.items()):
                if hasattr(v, "isoformat"):
                    x[k] = v.isoformat()
            out.append(x)
        return {"status": "ok", "rows": out, "capabilities": _caps(core)}

    @app.get("/api/v19-1/newspaper-diagnostic")
    def diagnostic(req: Request):
        core.need_login(req)
        return {"status": "READY", "version": VERSION, "capabilities": _caps(core), "removed_capture_routes": removed}

    return {"status": "REGISTERED", "version": VERSION, "removed_capture_routes": removed}
