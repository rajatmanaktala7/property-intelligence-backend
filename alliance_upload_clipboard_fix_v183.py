from __future__ import annotations
from fastapi import Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse

VERSION="18.3-NEWSPAPER-UPLOAD-CLIPBOARD-FIX"
OWNED={"/capture-intelligence","/newspaper","/newspaper-upload","/magazine-capture","/fast-property-entry"}

def _remove(app):
    kept=[]; removed=[]
    for r in app.router.routes:
        p=getattr(r,"path",None); m=set(getattr(r,"methods",set()) or set())
        if p in OWNED and "GET" in m: removed.append(p)
        else: kept.append(r)
    app.router.routes[:]=kept
    return removed

def _ok(core,req):
    try:return bool(core.page_role_or_redirect(req))
    except:return True

UI=r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Newspaper / Magazine Upload</title>
<style>*{box-sizing:border-box}body{font-family:Arial;margin:0;background:#f4f7fb;color:#172437}header{background:#102235;color:#fff;padding:18px 22px}.w{max-width:1050px;margin:auto;padding:18px}.card{background:#fff;border:1px solid #dfe7f0;border-radius:12px;padding:18px;margin:14px 0}.drop{border:2px dashed #8da8c2;border-radius:12px;padding:28px;text-align:center;background:#fafcff;cursor:pointer}.drop.over{outline:3px solid #b4d1ff}.drop input{display:none}.btn,button{padding:11px 14px;border:0;border-radius:8px;background:#1677ff;color:#fff;font-weight:700;text-decoration:none;cursor:pointer}.gray{background:#edf2f7;color:#24364b}select,input{width:100%;padding:10px;border:1px solid #ccd7e4;border-radius:8px;margin:5px 0 12px}.status{padding:12px;background:#f7f9fc;border-radius:8px;white-space:pre-wrap}.ok{color:#08734b}.err{color:#b42318}</style></head><body>
<header><b>Newspaper / Magazine Property Upload</b><br><small>Choose File · Drag & Drop · Copy/Paste Image</small></header>
<div class="w"><div class="card"><a class="btn gray" href="/final-dashboard-v12">← Dashboard</a></div>
<form id="f" class="card"><label><b>Source Type</b></label><select name="source_type" id="st"><option>NEWSPAPER</option><option>MAGAZINE</option><option>PHOTO</option></select>
<label><b>Optional Note</b></label><input name="note" placeholder="e.g. Property Informer Sep 2026 / Page 12">
<input type="hidden" name="capture_mode" value="UPLOAD_DRAG_DROP_PASTE">
<div id="drop" class="drop" tabindex="0"><b>Drop newspaper / magazine here</b><p>or click to choose PDF/image</p><p>For copied image: click here and press <b>Ctrl+V</b></p><input id="file" name="file" type="file" accept=".pdf,application/pdf,image/*"><div id="picked"></div></div>
<p><button id="go">Upload & Extract</button></p><div id="msg" class="status">Ready.</div></form>
<div class="card"><h3>Recent Intake</h3><button type="button" class="btn gray" onclick="loadStatus()">Refresh</button><div id="jobs" class="status">Loading...</div></div></div>
<script>
const f=document.getElementById('f'),drop=document.getElementById('drop'),file=document.getElementById('file'),picked=document.getElementById('picked'),msg=document.getElementById('msg'),go=document.getElementById('go');
function setFile(x){if(!x)return;const dt=new DataTransfer();dt.items.add(x);file.files=dt.files;picked.textContent=x.name+' · '+Math.round(x.size/1024)+' KB';msg.className='status';msg.textContent='Ready to upload.'}
file.onchange=()=>setFile(file.files[0]);drop.onclick=e=>{if(e.target!==file)file.click()};
['dragenter','dragover'].forEach(n=>drop.addEventListener(n,e=>{e.preventDefault();drop.classList.add('over')}));
['dragleave','drop'].forEach(n=>drop.addEventListener(n,e=>{e.preventDefault();drop.classList.remove('over')}));
drop.addEventListener('drop',e=>setFile(e.dataTransfer.files[0]));
drop.addEventListener('paste',e=>{const it=[...(e.clipboardData?.items||[])].find(i=>i.kind==='file'&&i.type.startsWith('image/'));if(!it){msg.className='status err';msg.textContent='Clipboard has no image. Copy the actual image, then Ctrl+V here.';return}e.preventDefault();let x=it.getAsFile();const ext=(x.type.split('/')[1]||'png').replace('jpeg','jpg');x=new File([x],'clipboard-'+Date.now()+'.'+ext,{type:x.type});setFile(x)});
f.onsubmit=async e=>{e.preventDefault();if(!file.files.length){msg.className='status err';msg.textContent='Choose, drop or paste a file first.';return}go.disabled=true;msg.className='status';msg.textContent='Uploading...';try{const r=await fetch('/api/v10/intake/file',{method:'POST',body:new FormData(f)});const raw=await r.text();let d={};try{d=JSON.parse(raw)}catch(_){}if(!r.ok)throw new Error(d.detail||d.message||raw||('HTTP '+r.status));msg.className='status ok';msg.textContent='Upload accepted. AI extraction started. Job ID: '+(d.job_id||'created');file.value='';picked.textContent='';loadStatus()}catch(x){msg.className='status err';msg.textContent='UPLOAD FAILED: '+x.message}finally{go.disabled=false}};
async function loadStatus(){try{const r=await fetch('/api/v10/intake/status'),d=await r.json();const a=(d.rows||[]).filter(x=>['NEWSPAPER','MAGAZINE'].includes(String(x.source_type||'').toUpperCase())).slice(0,10);jobs.textContent=a.length?a.map(x=>(x.source_type||'')+' | '+(x.original_filename||'')+' | '+(x.status||'')+' | New '+(x.processed_records||0)+(x.error_message?' | ERROR '+x.error_message:'')).join('\\n'):'No newspaper/magazine jobs yet.'}catch(x){jobs.textContent='Status failed: '+x.message}}
const q=new URLSearchParams(location.search).get('source_type');if(q)st.value=q.toUpperCase();loadStatus();
</script></body></html>'''

def register(wrapped):
    core=wrapped.core; app=wrapped.app; removed=_remove(app)
    @app.get("/fast-property-entry")
    def fast(req:Request,division:str=Query("DELHI_NCR")):
        if not _ok(core,req): return RedirectResponse("/login",303)
        return RedirectResponse("/manual-property-v18?division="+str(division or "DELHI_NCR").upper(),307)
    @app.get("/capture-intelligence",response_class=HTMLResponse)
    def cap(req:Request):
        if not _ok(core,req): return RedirectResponse("/login",303)
        return HTMLResponse(UI)
    @app.get("/newspaper")
    def news(req:Request):
        if not _ok(core,req): return RedirectResponse("/login",303)
        return RedirectResponse("/capture-intelligence?source_type=NEWSPAPER",307)
    @app.get("/newspaper-upload")
    def news2(req:Request):
        if not _ok(core,req): return RedirectResponse("/login",303)
        return RedirectResponse("/capture-intelligence?source_type=NEWSPAPER",307)
    @app.get("/magazine-capture")
    def mag(req:Request):
        if not _ok(core,req): return RedirectResponse("/login",303)
        return RedirectResponse("/capture-intelligence?source_type=MAGAZINE",307)
    @app.get("/api/v18-3/upload-fix/status")
    def status():
        return {"status":"READY","version":VERSION,"removed":removed,"clipboard_paste":True,"newspaper_backend":"/api/v10/intake/file"}
    return {"status":"REGISTERED","version":VERSION}
