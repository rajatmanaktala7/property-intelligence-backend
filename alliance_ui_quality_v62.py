from __future__ import annotations
from fastapi.responses import HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware

VERSION="6.2-UI-QUALITY-UPLOAD-PROGRESS"

MAGAZINE_HTML=r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Refined Magazine Master Import</title><style>
*{box-sizing:border-box}body{font-family:Arial;background:#efe4d2;margin:0;color:#2d261f}header{background:#5d4937;color:#fff;padding:18px 22px}.wrap{max-width:1300px;margin:auto;padding:18px}
.card{background:#fff;border:1px solid #d8cab8;border-radius:14px;padding:18px;margin-bottom:14px}.top{display:flex;gap:8px;flex-wrap:wrap}.btn,button{background:#6c543f;color:#fff;text-decoration:none;border:0;border-radius:8px;padding:10px 13px;font-weight:800;cursor:pointer}
.green{background:#16845b}.blue{background:#315f8d}input[type=file]{width:100%;padding:12px;border:2px dashed #c9b49f;border-radius:10px;background:#fffaf4}
.progress{height:18px;background:#eadfd2;border-radius:999px;overflow:hidden;margin-top:8px}.bar{height:100%;width:0%;background:#16845b;transition:width .12s}.pct{font-size:18px;font-weight:900}
.status{white-space:pre-wrap;background:#f7f1e8;padding:12px;border-radius:8px;margin-top:10px}.muted{color:#756757}
</style></head><body><header><b>Refined Magazine Master Import</b><br><small>Heavy file upload with live percentage · safe reconciliation</small></header>
<div class=wrap><div class="card top"><a class=btn href="/workspace">← Dashboard</a><a class=btn href="/property-database">Property Database</a><a class=btn href="/inventory-activation">Inventory Activation</a></div>
<div class=card><h2>Upload Refined Magazine Excel</h2><p class=muted>Choose the refined magazine XLSX. Upload percentage is based on actual bytes sent to the server.</p>
<form id=f><input id=file name=file type=file accept=".xlsx" required><br><br><button id=uploadBtn class=green>Upload Magazine</button></form>
<div id=progressBox style="display:none;margin-top:16px"><div style="display:flex;justify-content:space-between"><span id=label>Preparing...</span><span class=pct id=pct>0%</span></div><div class=progress><div class=bar id=bar></div></div></div>
<div id=msg class=status>Ready.</div></div>
<div class=card><h3>Magazine Import Status</h3><div id=summary>Loading...</div><br><button class=blue id=reconcile>Reconcile Existing Staged Data</button></div></div>
<script>
function setP(p,t){progressBox.style.display='block';pct.textContent=p+'%';bar.style.width=p+'%';if(t)label.textContent=t}
async function load(){
 try{let r=await fetch('/api/v13-2/magazine/summary',{credentials:'include'}),d=await r.json();
 summary.innerHTML=`Master rows <b>${d.master_rows||0}</b> · Match ready <b>${d.match_ready||0}</b> · Data review <b>${d.data_review||0}</b> · Excluded <b>${d.excluded||0}</b> · Contact links <b>${d.contact_links||0}</b> · Auto mapped <b>${d.auto_mapped||0}</b> · Review <b>${d.review||0}</b> · Unmatched <b>${d.unmatched||0}</b>`}
 catch(e){summary.textContent='Status unavailable: '+e.message}
}
f.onsubmit=e=>{
 e.preventDefault();const fl=file.files[0];if(!fl){msg.textContent='Choose an XLSX file.';return}
 uploadBtn.disabled=true;setP(0,'Uploading '+fl.name);msg.textContent='Starting upload...';
 const fd=new FormData();fd.append('file',fl);
 const x=new XMLHttpRequest();x.open('POST','/api/v13-2/magazine/import',true);x.withCredentials=true;
 x.upload.onprogress=ev=>{if(ev.lengthComputable){let p=Math.min(100,Math.round(ev.loaded/ev.total*100));setP(p,'Uploading '+fl.name);msg.textContent=`UPLOAD ${p}% · ${(ev.loaded/1048576).toFixed(1)} MB of ${(ev.total/1048576).toFixed(1)} MB`}};
 x.upload.onload=()=>{setP(100,'Upload complete · importing and reconciling');msg.textContent='UPLOAD 100%\\nFile is now being imported and reconciled. Please do not upload it again.'};
 x.onerror=()=>{uploadBtn.disabled=false;msg.textContent='UPLOAD ERROR: Network connection failed.'};
 x.onload=()=>{uploadBtn.disabled=false;let d;try{d=JSON.parse(x.responseText)}catch(_){d={detail:x.responseText}}
   if(x.status<200||x.status>=300){msg.textContent='IMPORT ERROR: '+(d.detail||d.message||x.responseText);return}
   setP(100,'Completed');msg.textContent=`COMPLETED\\nImported ${d.master_rows||0} rows\\nContact links ${d.contact_links||0}\\nAuto mapped ${d.auto_mapped||0}\\nReview ${d.review||0}\\nUnmatched ${d.unmatched||0}`;load()
 };
 x.send(fd)
};
reconcile.onclick=async()=>{reconcile.disabled=true;msg.textContent='Reconciling staged data...';try{let r=await fetch('/api/v13-2/magazine/reconcile-existing',{method:'POST',credentials:'include'}),d=await r.json();if(!r.ok)throw Error(d.detail||'Unknown error');msg.textContent=`Reconciled. Auto mapped ${d.auto_mapped||0}, review ${d.review||0}, unmatched ${d.unmatched||0}.`;load()}catch(e){msg.textContent='RECONCILE ERROR: '+e.message}finally{reconcile.disabled=false}};
load();
</script></body></html>"""

class MagazineProgressMiddleware(BaseHTTPMiddleware):
    async def dispatch(self,request,call_next):
        if request.method=="GET" and request.url.path.rstrip("/")=="/magazine-master-import":
            return HTMLResponse(MAGAZINE_HTML)
        return await call_next(request)

def register(core):
    app=core.app
    if not getattr(app.state,"alliance_v62_magazine_progress",False):
        app.add_middleware(MagazineProgressMiddleware)
        app.state.alliance_v62_magazine_progress=True

    @app.get("/api/v62/ui-quality/status")
    def status():
        return {
            "status":"OK",
            "version":VERSION,
            "magazine_upload_progress":"REAL_BYTE_PERCENTAGE",
            "magazine_processing_stage_separate":True,
            "newspaper_upload_progress_expected":"REAL_BYTE_PERCENTAGE",
            "single_matcher":"/deal-match-ai-v60"
        }

    return {"status":"REGISTERED","version":VERSION}
