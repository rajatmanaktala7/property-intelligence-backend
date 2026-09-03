from __future__ import annotations
import hashlib, os, tempfile
from fastapi import BackgroundTasks, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
import alliance_newspaper_live_bridge_v196 as newspaper_live_v196

VERSION="19.2-PERSISTENT-NEWSPAPER-SOURCE"
DDL="CREATE TABLE IF NOT EXISTS pi_source_files(source_id BIGINT PRIMARY KEY, original_filename TEXT NOT NULL, mime_type TEXT, file_size BIGINT NOT NULL, sha256 TEXT NOT NULL, content BYTEA NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW())"

def _auth(core,req):
    try:
        core.need_login(req)
        return None
    except Exception:
        return RedirectResponse("/login",303)

def _setup(core):
    with core.engine.begin() as c:
        c.execute(text(DDL))

def _remove(app):
    targets={"/capture-intelligence","/newspaper","/newspaper-upload","/magazine-capture"}
    kept=[];removed=[]
    for r in app.router.routes:
        p=getattr(r,"path",None);m=set(getattr(r,"methods",set()) or set())
        if p in targets and "GET" in m:
            removed.append(p)
        else:
            kept.append(r)
    app.router.routes[:]=kept
    return removed

def _materialize_and_run(core,sid,jid):
    with core.engine.connect() as c:
        row=c.execute(text("SELECT original_filename,mime_type,content FROM pi_source_files WHERE source_id=:s"),{"s":sid}).mappings().first()
    if not row:
        with core.engine.begin() as c:
            c.execute(text("UPDATE pi_ai_jobs SET status='FAILED',error_message='Original source file missing',completed_at=NOW() WHERE id=:j"),{"j":jid})
        return
    ext=os.path.splitext(row["original_filename"] or "")[1].lower() or ".bin"
    fd,path=tempfile.mkstemp(suffix=ext);os.close(fd)
    try:
        with open(path,"wb") as f:
            f.write(bytes(row["content"]))
        core.run_file_job(sid,jid,path,row["mime_type"] or "application/octet-stream")
        try:
            sync_result=newspaper_live_v196.sync_source(core,sid)
            with core.engine.begin() as c:
                c.execute(text("UPDATE pi_ai_jobs SET output_summary=COALESCE(output_summary,'') || :x WHERE id=:j"), {"x":f" | Newspaper Live sync: {sync_result.get('inserted',0)} new, {sync_result.get('duplicates',0)} duplicates","j":jid})
        except Exception as sync_exc:
            with core.engine.begin() as c:
                c.execute(text("UPDATE pi_ai_jobs SET error_message=CONCAT_WS(' | ',NULLIF(error_message,''),:e) WHERE id=:j"), {"e":f"NEWSPAPER_LIVE_SYNC: {type(sync_exc).__name__}: {sync_exc}"[:4000],"j":jid})
    except Exception as exc:
        with core.engine.begin() as c:
            c.execute(text("UPDATE pi_sources SET ingestion_status='FAILED',error_message=:e WHERE id=:s"),{"e":str(exc),"s":sid})
            c.execute(text("UPDATE pi_ai_jobs SET status='FAILED',error_message=:e,completed_at=NOW() WHERE id=:j"),{"e":str(exc),"j":jid})
        try: os.unlink(path)
        except Exception: pass

UI=r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Newspaper Upload</title><style>
*{box-sizing:border-box}body{font-family:Arial;margin:0;background:#f4f7fb;color:#172437}header{background:#102235;color:#fff;padding:18px 22px}
.w{max-width:1100px;margin:auto;padding:18px}.card{background:#fff;border:1px solid #dfe7f0;border-radius:12px;padding:16px;margin:12px 0}
.btn,button{display:inline-block;border:0;border-radius:8px;padding:10px 13px;background:#1677ff;color:#fff;text-decoration:none;font-weight:700;cursor:pointer}.gray{background:#edf2f7;color:#24364b}
input,select{width:100%;padding:10px;border:1px solid #cbd6e2;border-radius:8px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.drop{border:2px dashed #9fb1c5;border-radius:12px;padding:28px;text-align:center;background:#fafcff;cursor:pointer;min-height:150px}.drop.over{outline:3px solid #b6d0ff}.drop input{display:none}
.stage{padding:10px 12px;border-radius:8px;background:#f7f9fc;margin:8px 0}.ok{color:#08734b;font-weight:700}.err{color:#a11;font-weight:700}.muted{font-size:12px;color:#62748a}
@media(max-width:760px){.grid{grid-template-columns:1fr}}</style></head><body>
<header><b>Newspaper / Magazine Upload</b><br><small>Original full page is saved permanently before AI extraction</small></header><div class="w">
<div class="card"><a class="btn gray" href="/workspace">Dashboard</a> <a class="btn gray" href="/newspaper-v83#newspaper-database">Newspaper Live Database</a> <a class="btn gray" href="/manual-property-database-v178">Manual Property Database</a></div>
<form id="f" class="card"><div class="grid">
<label>Source Type<select name="source_type" id="st"><option>NEWSPAPER</option><option>MAGAZINE</option></select></label>
<label>Optional Note<input name="note" placeholder="e.g. Property Informer Sep 2026 / Page 12"></label></div>
<div id="drop" class="drop" tabindex="0"><b>Upload the FULL newspaper page</b><p>Choose file, drag & drop, or copy image and press Ctrl+V here</p>
<div class="muted">The original image/PDF is saved before AI extraction starts.</div><input id="file" name="file" type="file" accept=".pdf,application/pdf,image/jpeg,image/png,image/webp,image/*"><div id="pv"></div></div>
<p><button id="go">Save Original & Extract</button></p><div id="msg" class="stage">Ready.</div></form>
<div class="card"><h3>Processing Status</h3><div id="jobs">Loading...</div></div></div>
<script>
function E(v){return String(v??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
const f=document.getElementById('f'),drop=document.getElementById('drop'),file=document.getElementById('file'),pv=document.getElementById('pv'),msg=document.getElementById('msg'),go=document.getElementById('go'),jobs=document.getElementById('jobs'),st=document.getElementById('st');
function setFile(x){if(!x)return;const dt=new DataTransfer();dt.items.add(x);file.files=dt.files;pv.textContent=x.name+' | '+Math.round(x.size/1024)+' KB';}
file.onchange=()=>setFile(file.files[0]);drop.onclick=e=>{if(e.target!==file)file.click()};
['dragenter','dragover'].forEach(n=>drop.addEventListener(n,e=>{e.preventDefault();drop.classList.add('over')}));
['dragleave','drop'].forEach(n=>drop.addEventListener(n,e=>{e.preventDefault();drop.classList.remove('over')}));
drop.addEventListener('drop',e=>setFile(e.dataTransfer.files?.[0]));
drop.addEventListener('paste',e=>{const it=[...(e.clipboardData?.items||[])].find(x=>x.kind==='file'&&x.type.startsWith('image/'));if(!it){msg.className='stage err';msg.textContent='Clipboard has no image.';return}e.preventDefault();let x=it.getAsFile();const ext=(x.type.split('/')[1]||'png').replace('jpeg','jpg');x=new File([x],'clipboard-newspaper-'+Date.now()+'.'+ext,{type:x.type});setFile(x);});
function label(x){const s=String(x.job_status||x.ingestion_status||'').toUpperCase();if(!x.original_saved)return 'Original not saved';if(['RUNNING','PROCESSING','RECEIVED','PENDING'].includes(s))return 'AI Extracting';if(['COMPLETED','PROCESSED','PROCESSED_WITH_ERRORS'].includes(s))return 'Completed';if(['FAILED','ERROR'].includes(s))return 'Failed';return s||'Queued';}
async function load(){try{const r=await fetch('/api/v19-2/newspaper-status');const d=await r.json();if(!r.ok)throw new Error(d.detail||'Status failed');const a=d.rows||[];jobs.innerHTML=a.length?a.map(x=>`<div class="stage"><b>${E(label(x))}</b> | ${E(x.original_filename||'')}<br>Original saved: <b>${x.original_saved?'YES':'NO'}</b> | New records: <b>${E(x.processed_records||0)}</b> | Duplicates: ${E(x.duplicate_records||0)}${x.output_summary?'<br>'+E(x.output_summary):''}${x.error_message?'<br><span class="err">'+E(x.error_message)+'</span>':''}${x.source_id?'<br><button type="button" onclick="retry('+x.source_id+')">Retry AI from saved original</button>':''}</div>`).join(''):'No newspaper uploads yet.'}catch(e){jobs.innerHTML='<span class="err">'+E(e.message)+'</span>'}}
async function retry(id){try{const r=await fetch('/api/v19-2/newspaper-retry/'+id,{method:'POST'});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Retry failed');await load()}catch(e){alert(e.message)}}
f.onsubmit=async e=>{e.preventDefault();if(!file.files.length){msg.className='stage err';msg.textContent='Choose, drop or paste a full newspaper page first.';return}go.disabled=true;msg.className='stage';msg.textContent='Saving original page...';try{const r=await fetch('/api/v19-2/newspaper-upload',{method:'POST',body:new FormData(f)});const raw=await r.text();let d={};try{d=JSON.parse(raw)}catch(_){}if(!r.ok)throw new Error(d.detail||d.message||raw||('HTTP '+r.status));msg.className='stage ok';msg.textContent='Original saved. AI extraction started. Source ID: '+d.source_id+' | Job ID: '+d.job_id;await load()}catch(e){msg.className='stage err';msg.textContent='UPLOAD FAILED: '+e.message}finally{go.disabled=false}};
const q=new URLSearchParams(location.search).get('source_type');if(q)st.value=q.toUpperCase();load();setInterval(load,5000);
</script></body></html>'''

def register(wrapped):
    core=wrapped.core;app=wrapped.app
    _setup(core)
    removed=_remove(app)
    try:
        with core.engine.connect() as c:
            latest=c.execute(text("SELECT s.id FROM pi_sources s JOIN pi_source_files sf ON sf.source_id=s.id WHERE upper(coalesce(s.source_type,''))='NEWSPAPER' AND upper(coalesce(s.ingestion_status,'')) IN ('PROCESSED','PROCESSED_WITH_ERRORS','COMPLETED') ORDER BY s.uploaded_at DESC LIMIT 1")).first()
        if latest:
            newspaper_live_v196.sync_source(core,int(latest[0]))
    except Exception as exc:
        print("[V19.6 startup newspaper sync]",type(exc).__name__,str(exc))

    @app.get("/capture-intelligence",response_class=HTMLResponse)
    def capture(req:Request):
        r=_auth(core,req);return r or HTMLResponse(UI)

    @app.get("/newspaper")
    def newspaper(req:Request):
        r=_auth(core,req);return r or RedirectResponse("/capture-intelligence?source_type=NEWSPAPER",307)

    @app.get("/newspaper-upload")
    def newspaper_upload(req:Request):
        r=_auth(core,req);return r or RedirectResponse("/capture-intelligence?source_type=NEWSPAPER",307)

    @app.get("/magazine-capture")
    def magazine_capture(req:Request):
        r=_auth(core,req);return r or RedirectResponse("/capture-intelligence?source_type=MAGAZINE",307)

    @app.post("/api/v19-2/newspaper-upload")
    async def upload(bg:BackgroundTasks,req:Request,file:UploadFile=File(...),source_type:str=Form("NEWSPAPER"),note:str=Form("")):
        core.need_login(req)
        if not callable(getattr(core,"run_file_job",None)):
            raise HTTPException(500,"Core exhaustive AI file worker is unavailable.")
        filename=file.filename or "newspaper-upload"
        ext=os.path.splitext(filename)[1].lower()
        mime=(file.content_type or "").lower()
        if ext not in {".pdf",".jpg",".jpeg",".png",".webp"} and mime not in {"application/pdf","image/jpeg","image/png","image/webp"}:
            raise HTTPException(400,"Upload PDF, JPG, JPEG, PNG or WEBP only.")
        max_mb=int(getattr(core,"MAX_UPLOAD_MB",100) or 100)
        data=await file.read()
        if not data:raise HTTPException(400,"Uploaded file is empty.")
        if len(data)>max_mb*1024*1024:raise HTTPException(413,f"File is larger than {max_mb} MB.")
        st=str(source_type or "NEWSPAPER").upper()
        sid=core.source_row(st,filename,filename,mime,reference=note)
        digest=hashlib.sha256(data).hexdigest()
        sql=("INSERT INTO pi_source_files(source_id,original_filename,mime_type,file_size,sha256,content) "
             "VALUES(:s,:f,:m,:z,:h,:b) "
             "ON CONFLICT(source_id) DO UPDATE SET original_filename=EXCLUDED.original_filename,mime_type=EXCLUDED.mime_type,"
             "file_size=EXCLUDED.file_size,sha256=EXCLUDED.sha256,content=EXCLUDED.content")
        with core.engine.begin() as c:
            c.execute(text(sql),{"s":sid,"f":filename,"m":mime,"z":len(data),"h":digest,"b":data})
        jid=core.create_job(sid,"NEWSPAPER_FULL_PAGE_EXTRACTION",f"{st} | {filename} | original saved")
        bg.add_task(_materialize_and_run,core,sid,jid)
        return {"status":"ACCEPTED","source_id":sid,"job_id":jid,"original_saved":True,"sha256":digest,"file_size":len(data)}

    @app.post("/api/v19-2/newspaper-retry/{source_id}")
    def retry(source_id:int,bg:BackgroundTasks,req:Request):
        core.need_login(req)
        with core.engine.connect() as c:
            row=c.execute(text("SELECT original_filename FROM pi_source_files WHERE source_id=:s"),{"s":source_id}).first()
        if not row:raise HTTPException(404,"Saved original source file not found.")
        jid=core.create_job(source_id,"NEWSPAPER_RETRY",f"Retry from saved original | {row[0]}")
        with core.engine.begin() as c:
            c.execute(text("UPDATE pi_sources SET ingestion_status='RECEIVED',error_message=NULL WHERE id=:s"),{"s":source_id})
        bg.add_task(_materialize_and_run,core,source_id,jid)
        return {"status":"ACCEPTED","source_id":source_id,"job_id":jid,"original_saved":True}

    @app.get("/api/v19-2/newspaper-status")
    def status(req:Request):
        core.need_login(req)
        sql=("SELECT s.id source_id,s.source_type,s.original_filename,s.ingestion_status,"
             "COALESCE(s.processed_records,0) processed_records,COALESCE(s.duplicate_records,0) duplicate_records,"
             "sf.source_id IS NOT NULL original_saved,s.error_message source_error,"
             "j.status job_status,j.output_summary,j.error_message job_error "
             "FROM pi_sources s LEFT JOIN pi_source_files sf ON sf.source_id=s.id "
             "LEFT JOIN LATERAL (SELECT status,output_summary,error_message FROM pi_ai_jobs j2 WHERE j2.source_id=s.id ORDER BY j2.created_at DESC LIMIT 1) j ON TRUE "
             "WHERE upper(coalesce(s.source_type,'')) IN ('NEWSPAPER','MAGAZINE') ORDER BY s.uploaded_at DESC LIMIT 20")
        with core.engine.connect() as c:rows=c.execute(text(sql)).mappings().all()
        out=[]
        for row in rows:
            x=dict(row);x["error_message"]=x.pop("job_error",None) or x.pop("source_error",None);out.append(x)
        return {"status":"ok","version":VERSION,"rows":out}

    return {"status":"REGISTERED","version":VERSION,"removed_capture_routes":removed}
