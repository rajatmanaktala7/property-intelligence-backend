from pathlib import Path
import re, shutil, time, py_compile
ROOT=Path(__file__).resolve().parent
f=ROOT/"alliance_magazine_fresh_v822.py"
if not f.exists(): raise SystemExit("alliance_magazine_fresh_v822.py not found")
stamp=time.strftime("%Y%m%d-%H%M%S"); bak=ROOT/f"alliance_magazine_fresh_v822.py.before-v823-{stamp}.bak"
shutil.copy2(f,bak); s=f.read_text(encoding="utf-8")
if "alliance_magazine_safe_gateway_v660" not in s:
    s=s.replace("from sqlalchemy import text\n","from sqlalchemy import text\nimport alliance_magazine_safe_gateway_v660 as safe_gateway\n",1)
helper=r'''
def _gateway_extract(gw,jpg):
    data,meta=gw.ask(jpg,PROMPT)
    if data is None:return None,meta
    raw=data.get("properties") if isinstance(data,dict) else None
    if raw is None and isinstance(data,dict):raw=data.get("records")
    rows=[]
    for item in (raw or []):
        if not isinstance(item,dict):continue
        if "original_description" not in item and item.get("raw_line"):
            item=dict(item);item["original_description"]=item.get("raw_line")
        try:rows.append(FreshProperty.model_validate(item))
        except Exception:continue
    return rows,meta

'''
if "def _gateway_extract(" not in s:s=s.replace("def _process(core,uid):",helper+"def _process(core,uid):",1)
new_process=r'''def _process(core,uid):
    e=_engine(core)
    try:
        gw=safe_gateway.ProviderGateway()
        gw.max_calls=int(os.getenv("ALLIANCE_MAGAZINE_V823_MAX_CALLS","1000"))
        if not gw.providers:
            with e.begin() as c:c.execute(text("UPDATE pi_magazine_fresh_uploads SET status='WAITING_FOR_PROVIDER',error_message='No configured vision provider' WHERE upload_id=CAST(:u AS UUID)"),{'u':uid})
            return
        with e.connect() as c:row=c.execute(text("SELECT pdf_content,processed_pages FROM pi_magazine_fresh_uploads WHERE upload_id=CAST(:u AS UUID)"),{'u':uid}).first()
        if not row or row[0] is None:raise RuntimeError("Stored PDF not found")
        pdf=bytes(row[0]);start=int(row[1] or 0);doc=fitz.open(stream=pdf,filetype='pdf');pages=len(doc)
        with e.connect() as c:counts=c.execute(text("SELECT COUNT(*),COUNT(*) FILTER(WHERE needs_review) FROM pi_magazine_fresh_records WHERE upload_id=CAST(:u AS UUID)"),{'u':uid}).first()
        tm=int(counts[0] or 0);tr=int(counts[1] or 0)
        with e.begin() as c:c.execute(text("UPDATE pi_magazine_fresh_uploads SET status='PROCESSING',page_count=:p,created_records=:m,review_records=:r,error_message=NULL WHERE upload_id=CAST(:u AS UUID)"),{'p':pages,'m':tm,'r':tr,'u':uid})
        for i in range(start,pages):
            page=doc.load_page(i);scale=PDF_RENDER_DPI/72.0
            jpg=page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False).tobytes('jpeg')
            rows,meta=_gateway_extract(gw,jpg)
            if rows is None:
                retry=gw.next_retry();msg=meta.get("status","VISION_PROVIDER_UNAVAILABLE")
                if retry:msg+=" | retry_after="+retry.isoformat()
                with e.begin() as c:c.execute(text("UPDATE pi_magazine_fresh_uploads SET status='WAITING_FOR_PROVIDER',error_message=:x WHERE upload_id=CAST(:u AS UUID)"),{'x':msg[:4000],'u':uid})
                doc.close();return
            m,r=_save(e,uid,i+1,rows);tm+=m;tr+=r
            with e.begin() as c:c.execute(text("UPDATE pi_magazine_fresh_uploads SET processed_pages=:d,created_records=:m,review_records=:r,error_message=:x WHERE upload_id=CAST(:u AS UUID)"),{'d':i+1,'m':tm,'r':tr,'x':("provider="+str(meta.get("provider","UNKNOWN")))[:4000],'u':uid})
        doc.close()
        with e.begin() as c:c.execute(text("UPDATE pi_magazine_fresh_uploads SET status='READY_FOR_REVIEW',error_message=NULL,completed_at=NOW() WHERE upload_id=CAST(:u AS UUID)"),{'u':uid})
    except Exception as exc:
        with e.begin() as c:c.execute(text("UPDATE pi_magazine_fresh_uploads SET status='PAUSED_ERROR',error_message=:x WHERE upload_id=CAST(:u AS UUID)"),{'x':f'{type(exc).__name__}: {exc}'[:4000],'u':uid})

'''
pat=r"(?ms)^def _process\(core,uid\):.*?(?=^def _page\(\):)"
if not re.search(pat,s):shutil.copy2(bak,f);raise SystemExit("SAFETY STOP: _process block not found")
s=re.sub(pat,new_process,s,count=1)
resume=r'''
    @app.post('/api/magazine-fresh/resume/{upload_id}')
    def resume(upload_id:str,req:Request,bg:BackgroundTasks):
        _login(core,req)
        with e.connect() as c:row=c.execute(text("SELECT pdf_content,status FROM pi_magazine_fresh_uploads WHERE upload_id=CAST(:u AS UUID)"),{'u':upload_id}).first()
        if not row:raise HTTPException(404,'Upload not found')
        if row[0] is None:raise HTTPException(409,'Stored PDF not found')
        if row[1]=='PROCESSING':return {'status':'ALREADY_PROCESSING','upload_id':upload_id}
        bg.add_task(_process,core,upload_id)
        return {'status':'RESUME_STARTED','upload_id':upload_id,'version':'8.2.3'}

'''
needle="    @app.get('/api/magazine-fresh/status/{upload_id}')"
if "/api/magazine-fresh/resume/{upload_id}" not in s:
    if needle not in s:shutil.copy2(bak,f);raise SystemExit("SAFETY STOP: status route marker not found")
    s=s.replace(needle,resume+needle,1)
s=s.replace("VERSION='8.2.2-FRESH-MAGAZINE-PDF'","VERSION='8.2.3-RESILIENT-MAGAZINE-PDF'",1)
s=s.replace("Fresh Magazine PDF Database · CRE OS 8.2.2","Fresh Magazine PDF Database · CRE OS 8.2.3",1)
f.write_text(s,encoding="utf-8")
try:
    py_compile.compile(str(f),doraise=True);py_compile.compile(str(ROOT/"alliance_magazine_safe_gateway_v660.py"),doraise=True);py_compile.compile(str(ROOT/"production_entrypoint.py"),doraise=True)
except Exception:
    shutil.copy2(bak,f);raise
print("PASS: Alliance CRE OS 8.2.3 resilient Magazine fix installed")
print("Backup:",bak)
print("Existing stored PDF can resume without re-upload.")
