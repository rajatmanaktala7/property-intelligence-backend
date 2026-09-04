from __future__ import annotations
import hashlib, html, json, math, os, re, uuid
from typing import Optional
import fitz
from fastapi import BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from google.genai import types
from pydantic import BaseModel, Field
from sqlalchemy import text
import alliance_magazine_safe_gateway_v660 as safe_gateway

VERSION='8.2.7-MULTIMODEL-OPENROUTER-FALLBACK'
CHUNK_SIZE=4*1024*1024
MAX_UPLOAD_MB=int(os.getenv('MAX_UPLOAD_MB','100'))
PDF_RENDER_DPI=int(os.getenv('PDF_RENDER_DPI','220'))
GEMINI_MODEL=os.getenv('GEMINI_MODEL','gemini-3.1-flash-lite')

class FreshProperty(BaseModel):
    section_heading: Optional[str]=None
    original_description: str
    exact_address: Optional[str]=None
    locality: Optional[str]=None
    city: Optional[str]=None
    property_type: Optional[str]=None
    transaction_type: Optional[str]=None
    area_value: Optional[float]=None
    area_unit: Optional[str]=None
    floor: Optional[str]=None
    amount_raw: Optional[str]=None
    contact_name: Optional[str]=None
    contact_number: Optional[str]=None
    extraction_confidence: Optional[float]=None

class FreshEnvelope(BaseModel):
    properties:list[FreshProperty]=Field(default_factory=list)

PROMPT='''You are extracting a real-estate classified magazine page for Alliance Infrastructure.
Return EVERY visible property listing as a separate property record. Do not merge neighboring listings.
STRICT RULES:
1. original_description must copy the exact printed property listing text visible for that property. Preserve digits, address tokens, area, floor codes, amount and phone numbers. Never rewrite it into a summary.
2. Never invent missing address, locality, area, floor, amount, contact name or phone.
3. One physical listing row = one record even when several rows share the same contact.
4. If a visible section/category/locality heading applies to rows below, capture it in section_heading and inherit only what the page visibly supports.
5. Ignore editorial content, headers, footers and unrelated ads.
6. transaction_type should be RENT/LEASE/SALE only when visible or clearly inherited from a visible heading.
7. area_unit should be SQFT, SQYD, SQM or ACRE only when visible.
8. exact_address must be an actual property/building/unit/address reference, not merely the locality heading.
9. If unclear, return null rather than guessing.
10. extraction_confidence is 0-100.'''

def _app(core): return getattr(core,'app',None) or core
def _engine(core): return getattr(core,'engine',None)
def _client(core): return getattr(core,'client',None)
def _login(core,req):
    fn=getattr(core,'need_login',None)
    return fn(req) if fn else 'team'
def _e(v): return html.escape('' if v is None else str(v))

def _remove(app,path,method):
    app.router.routes[:]=[r for r in list(app.routes) if not (getattr(r,'path',None)==path and method.upper() in set(getattr(r,'methods',set()) or set()))]

def _setup(e):
    ddls=[
    '''CREATE TABLE IF NOT EXISTS pi_magazine_fresh_uploads(
       upload_id UUID PRIMARY KEY, filename TEXT NOT NULL, file_size BIGINT NOT NULL,
       chunk_size INTEGER NOT NULL, total_chunks INTEGER NOT NULL, received_chunks INTEGER NOT NULL DEFAULT 0,
       status TEXT NOT NULL DEFAULT 'UPLOADING', pdf_content BYTEA, sha256 TEXT,
       page_count INTEGER NOT NULL DEFAULT 0, processed_pages INTEGER NOT NULL DEFAULT 0,
       created_records INTEGER NOT NULL DEFAULT 0, review_records INTEGER NOT NULL DEFAULT 0,
       error_message TEXT, created_at TIMESTAMPTZ DEFAULT NOW(), completed_at TIMESTAMPTZ)''',
    '''CREATE TABLE IF NOT EXISTS pi_magazine_fresh_chunks(
       upload_id UUID NOT NULL, chunk_index INTEGER NOT NULL, content BYTEA NOT NULL,
       byte_count INTEGER NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW(), PRIMARY KEY(upload_id,chunk_index))''',
    '''CREATE TABLE IF NOT EXISTS pi_magazine_fresh_records(
       id BIGSERIAL PRIMARY KEY, record_id TEXT UNIQUE NOT NULL, upload_id UUID NOT NULL,
       page_number INTEGER NOT NULL, section_heading TEXT, original_description TEXT NOT NULL,
       exact_address TEXT, locality TEXT, city TEXT, property_type TEXT, transaction_type TEXT,
       area_value NUMERIC(14,2), area_unit TEXT, area_sqft NUMERIC(14,2), floor TEXT,
       amount_raw TEXT, contact_name TEXT, contact_number TEXT, extraction_confidence NUMERIC(5,2),
       needs_review BOOLEAN DEFAULT FALSE, evidence_hash TEXT NOT NULL,
       raw_json JSONB NOT NULL DEFAULT '{}'::jsonb, created_at TIMESTAMPTZ DEFAULT NOW(),
       UNIQUE(upload_id,page_number,evidence_hash))''',
    'CREATE INDEX IF NOT EXISTS idx_magfresh_upload ON pi_magazine_fresh_records(upload_id)',
    'CREATE INDEX IF NOT EXISTS idx_magfresh_page ON pi_magazine_fresh_records(upload_id,page_number)']
    with e.begin() as c:
        for q in ddls: c.execute(text(q))

def _sqft(value,unit):
    if value is None: return None
    try: v=float(value)
    except: return None
    u=str(unit or '').upper().replace(' ','')
    return {'SQFT':v,'SQYD':v*9,'SQM':v*10.7639104167,'ACRE':v*43560}.get(u)

def _extract_page(client,jpg):
    r=client.models.generate_content(model=GEMINI_MODEL,
      contents=[PROMPT,types.Part.from_bytes(data=jpg,mime_type='image/jpeg')],
      config=types.GenerateContentConfig(response_mime_type='application/json',response_schema=FreshEnvelope,temperature=0.0))
    env=FreshEnvelope.model_validate(r.parsed) if getattr(r,'parsed',None) is not None else FreshEnvelope.model_validate_json(r.text)
    return env.properties

def _save(e,uid,page,rows):
    made=review=0
    with e.begin() as c:
        for x in rows:
            original=re.sub(r'\s+',' ',x.original_description or '').strip()
            if not original: continue
            h=hashlib.sha256(original.lower().encode()).hexdigest()
            nr=not x.exact_address or not x.contact_number or x.extraction_confidence is None or float(x.extraction_confidence)<80
            p=dict(record_id='MAGNEW-'+uuid.uuid4().hex[:16].upper(),uid=uid,page=page,section=x.section_heading,
              original=original,address=x.exact_address,locality=x.locality,city=x.city,ptype=x.property_type,tx=x.transaction_type,
              area=x.area_value,unit=x.area_unit,sqft=_sqft(x.area_value,x.area_unit),floor=x.floor,amount=x.amount_raw,
              cname=x.contact_name,cphone=x.contact_number,confidence=x.extraction_confidence,review=nr,h=h,
              raw=json.dumps(x.model_dump(),ensure_ascii=False))
            z=c.execute(text('''INSERT INTO pi_magazine_fresh_records(record_id,upload_id,page_number,section_heading,original_description,
              exact_address,locality,city,property_type,transaction_type,area_value,area_unit,area_sqft,floor,amount_raw,
              contact_name,contact_number,extraction_confidence,needs_review,evidence_hash,raw_json)
              VALUES(:record_id,CAST(:uid AS UUID),:page,:section,:original,:address,:locality,:city,:ptype,:tx,:area,:unit,:sqft,
              :floor,:amount,:cname,:cphone,:confidence,:review,:h,CAST(:raw AS JSONB))
              ON CONFLICT(upload_id,page_number,evidence_hash) DO NOTHING'''),p)
            if z.rowcount:
                made+=1; review+=1 if nr else 0
    return made,review


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

def _process(core,uid):
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
                retry=gw.next_retry();reason=meta.get("status","VISION_PROVIDER_UNAVAILABLE")
                msg="AI provider unavailable. The PDF is safe and extraction can resume from page "+str(i)+"."
                if retry:msg+=" Next provider retry after "+retry.isoformat()+"."
                msg+=" Provider status: "+str(reason)+"."
                with e.begin() as c:c.execute(text("UPDATE pi_magazine_fresh_uploads SET status='WAITING_FOR_PROVIDER',error_message=:x WHERE upload_id=CAST(:u AS UUID)"),{'x':msg[:1000],'u':uid})
                doc.close();return
            m,r=_save(e,uid,i+1,rows);tm+=m;tr+=r
            with e.begin() as c:c.execute(text("UPDATE pi_magazine_fresh_uploads SET processed_pages=:d,created_records=:m,review_records=:r,error_message=:x WHERE upload_id=CAST(:u AS UUID)"),{'d':i+1,'m':tm,'r':tr,'x':("provider="+str(meta.get("provider","UNKNOWN")))[:4000],'u':uid})
        doc.close()
        with e.begin() as c:c.execute(text("UPDATE pi_magazine_fresh_uploads SET status='READY_FOR_REVIEW',error_message=NULL,completed_at=NOW() WHERE upload_id=CAST(:u AS UUID)"),{'u':uid})
    except Exception as exc:
        with e.begin() as c:c.execute(text("UPDATE pi_magazine_fresh_uploads SET status='PAUSED_ERROR',error_message=:x WHERE upload_id=CAST(:u AS UUID)"),{'x':f'{type(exc).__name__}: {exc}'[:4000],'u':uid})

def _page():
    return '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">\n<title>Alliance Magazine Resume</title><style>body{font-family:Arial;background:#f4f7fb;margin:0;color:#172437}.top{background:#102235;color:white;padding:20px}.wrap{max-width:1180px;margin:auto;padding:20px}.card{background:white;padding:18px;border-radius:14px;margin-bottom:14px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.num{font-size:28px;font-weight:800}.muted{color:#66758a}.btn{background:#1266f1;color:white;border:0;border-radius:9px;padding:11px 18px;font-weight:700;cursor:pointer}.good{color:#16833c}.bad{color:#bd2f2f}a{color:#1266f1;text-decoration:none}table{width:100%;border-collapse:collapse}td,th{padding:8px;border-bottom:1px solid #e5eaf0;text-align:left}</style></head>\n<body><div class="top"><b>Fresh Magazine PDF Database · CRE OS 8.2.7</b><br><small>Multi-model Gemini · OpenRouter vision fallback · safe checkpoint resume</small></div>\n<div class="wrap"><div class="card"><a href="/workspace">← Dashboard</a> · <a href="/magazine-fresh/records">New Magazine Records</a></div>\n<div class="card"><h2>Current / Previous Magazine</h2><p id="name" class="muted">Checking stored jobs...</p><div id="stats" class="grid"></div><p id="state" class="muted"></p><button id="resume" class="btn" style="display:none">Resume Extraction</button></div>\n<div class="card"><h3>Recent Magazine Jobs</h3><div id="jobs">Loading...</div></div>\n<div class="card"><h3>New Magazine</h3><p class="muted">For a genuinely new magazine, use the existing upload page after the current database is validated.</p></div></div>\n<script>\nlet active=null,timer=null;\nfunction esc(x){return String(x??\'\').replace(/[&<>"\']/g,m=>({\'&\':\'&amp;\',\'<\':\'&lt;\',\'>\':\'&gt;\',\'"\':\'&quot;\',"\'":\'&#39;\'}[m]))}\nfunction render(d){active=d.upload_id;name.innerHTML=\'<b>\'+esc(d.filename)+\'</b> · \'+esc(d.status);stats.innerHTML=\'<div class="card"><div class="muted">Pages</div><div class="num">\'+d.processed_pages+\'/\'+d.page_count+\'</div></div><div class="card"><div class="muted">Records</div><div class="num">\'+d.created_records+\'</div></div><div class="card"><div class="muted">Needs review</div><div class="num">\'+d.review_records+\'</div></div><div class="card"><div class="muted">Status</div><b>\'+esc(d.status)+\'</b></div>\';state.textContent=d.error_message||\'Ready.\';resume.style.display=[\'ERROR\',\'PAUSED_ERROR\',\'WAITING_FOR_PROVIDER\',\'STORED\'].includes(d.status)?\'inline-block\':\'none\'}\nasync function load(){let r=await fetch(\'/api/magazine-fresh/latest\');if(!r.ok){state.textContent=\'Unable to read stored jobs.\';return}let d=await r.json();if(d.latest)render(d.latest);else{name.textContent=\'No stored Magazine PDF found.\'}jobs.innerHTML=(d.uploads||[]).length?\'<table><tr><th>PDF</th><th>Status</th><th>Pages</th><th>Records</th></tr>\'+d.uploads.map(x=>\'<tr><td>\'+esc(x.filename)+\'</td><td>\'+esc(x.status)+\'</td><td>\'+x.processed_pages+\'/\'+x.page_count+\'</td><td>\'+x.created_records+\'</td></tr>\').join(\'\')+\'</table>\':\'No previous jobs.\'}\nresume.onclick=async()=>{if(!active)return;resume.disabled=true;let r=await fetch(\'/api/magazine-fresh/resume/\'+active,{method:\'POST\'});let d=await r.json();state.textContent=d.status||d.detail||\'Resume requested\';resume.disabled=false;setTimeout(load,1000)}\nload();timer=setInterval(load,4000);\n</script></body></html>'

def register(core):
    app=_app(core); e=_engine(core)
    if app is None or e is None: raise RuntimeError('Fresh Magazine 8.2.2 requires app + engine')
    _setup(e); _remove(app,'/magazine-master-import','GET')

    @app.get('/magazine-master-import',response_class=HTMLResponse)
    def page(req:Request): _login(core,req); return HTMLResponse(_page())

    @app.get('/magazine-fresh',response_class=HTMLResponse)
    def alias(req:Request): _login(core,req); return HTMLResponse(_page())

    @app.post('/api/magazine-fresh/init')
    async def init(req:Request):
        _login(core,req); b=await req.json(); fn=str(b.get('filename') or 'magazine.pdf'); size=int(b.get('file_size') or 0)
        if not fn.lower().endswith('.pdf'): raise HTTPException(400,'PDF only')
        if size<=0: raise HTTPException(400,'Empty file')
        if size>MAX_UPLOAD_MB*1024*1024: raise HTTPException(413,f'PDF exceeds {MAX_UPLOAD_MB} MB limit')
        uid=str(uuid.uuid4()); total=math.ceil(size/CHUNK_SIZE)
        with e.begin() as c:c.execute(text('INSERT INTO pi_magazine_fresh_uploads(upload_id,filename,file_size,chunk_size,total_chunks) VALUES(CAST(:u AS UUID),:f,:s,:cs,:t)'),{'u':uid,'f':fn,'s':size,'cs':CHUNK_SIZE,'t':total})
        return {'status':'OK','upload_id':uid,'total_chunks':total,'chunk_size':CHUNK_SIZE}

    @app.post('/api/magazine-fresh/chunk/{upload_id}')
    async def chunk(upload_id:str,req:Request,index:int=Query(...,ge=0)):
        _login(core,req); data=await req.body()
        if not data or len(data)>CHUNK_SIZE: raise HTTPException(400,'Invalid chunk size')
        with e.begin() as c:
            meta=c.execute(text('SELECT total_chunks FROM pi_magazine_fresh_uploads WHERE upload_id=CAST(:u AS UUID)'),{'u':upload_id}).first()
            if not meta: raise HTTPException(404,'Upload not found')
            if index>=int(meta[0]): raise HTTPException(400,'Chunk index out of range')
            c.execute(text('INSERT INTO pi_magazine_fresh_chunks(upload_id,chunk_index,content,byte_count) VALUES(CAST(:u AS UUID),:i,:b,:n) ON CONFLICT(upload_id,chunk_index) DO UPDATE SET content=EXCLUDED.content,byte_count=EXCLUDED.byte_count'),{'u':upload_id,'i':index,'b':data,'n':len(data)})
            n=c.execute(text('SELECT COUNT(*) FROM pi_magazine_fresh_chunks WHERE upload_id=CAST(:u AS UUID)'),{'u':upload_id}).scalar_one(); c.execute(text('UPDATE pi_magazine_fresh_uploads SET received_chunks=:n WHERE upload_id=CAST(:u AS UUID)'),{'n':n,'u':upload_id})
        return {'status':'OK','received_chunks':int(n)}

    @app.post('/api/magazine-fresh/complete/{upload_id}')
    def complete(upload_id:str,req:Request,bg:BackgroundTasks):
        _login(core,req)
        with e.begin() as c:
            m=c.execute(text('SELECT file_size,total_chunks FROM pi_magazine_fresh_uploads WHERE upload_id=CAST(:u AS UUID) FOR UPDATE'),{'u':upload_id}).first()
            if not m: raise HTTPException(404,'Upload not found')
            rows=c.execute(text('SELECT chunk_index,content FROM pi_magazine_fresh_chunks WHERE upload_id=CAST(:u AS UUID) ORDER BY chunk_index'),{'u':upload_id}).fetchall()
            if len(rows)!=int(m[1]): raise HTTPException(409,f'Upload incomplete: {len(rows)}/{m[1]} chunks')
            pdf=b''.join(bytes(r[1]) for r in rows)
            if len(pdf)!=int(m[0]): raise HTTPException(409,'Uploaded byte count mismatch')
            if not pdf.startswith(b'%PDF'): raise HTTPException(400,'Not a valid PDF')
            sha=hashlib.sha256(pdf).hexdigest(); c.execute(text("UPDATE pi_magazine_fresh_uploads SET pdf_content=:p,sha256=:s,status='STORED',received_chunks=total_chunks WHERE upload_id=CAST(:u AS UUID)"),{'p':pdf,'s':sha,'u':upload_id}); c.execute(text('DELETE FROM pi_magazine_fresh_chunks WHERE upload_id=CAST(:u AS UUID)'),{'u':upload_id})
        bg.add_task(_process,core,upload_id); return {'status':'STORED','upload_id':upload_id,'sha256':sha,'processing':'STARTED'}


    @app.post('/api/magazine-fresh/resume/{upload_id}')
    def resume(upload_id:str,req:Request,bg:BackgroundTasks):
        _login(core,req)
        with e.connect() as c:row=c.execute(text("SELECT pdf_content,status FROM pi_magazine_fresh_uploads WHERE upload_id=CAST(:u AS UUID)"),{'u':upload_id}).first()
        if not row:raise HTTPException(404,'Upload not found')
        if row[0] is None:raise HTTPException(409,'Stored PDF not found')
        if row[1]=='PROCESSING':return {'status':'ALREADY_PROCESSING','upload_id':upload_id}
        with e.begin() as c:
            c.execute(text("UPDATE pi_magazine_fresh_uploads SET status='QUEUED',error_message='Checking available AI providers...' WHERE upload_id=CAST(:u AS UUID)"),{'u':upload_id})
        bg.add_task(_process,core,upload_id)
        return {'status':'RESUME_STARTED','upload_id':upload_id,'version':'8.2.5','message':'Provider waterfall started'}

    @app.get('/api/magazine-fresh/latest')
    def latest(req:Request):
        _login(core,req)
        try:
            with e.connect() as c:
                rows=c.execute(text("""
                    SELECT upload_id::text AS upload_id,
                           filename,
                           COALESCE(status,'UNKNOWN') AS status,
                           COALESCE(page_count,0) AS page_count,
                           COALESCE(processed_pages,0) AS processed_pages,
                           COALESCE(created_records,0) AS created_records,
                           COALESCE(review_records,0) AS review_records,
                           error_message,
                           created_at
                    FROM pi_magazine_fresh_uploads
                    ORDER BY created_at DESC NULLS LAST
                    LIMIT 10
                """)).mappings().all()
            items=[]
            for x in rows:
                d=dict(x)
                if d.get('created_at') is not None:
                    d['created_at']=d['created_at'].isoformat()
                items.append(d)
            return {'status':'OK','version':'8.2.4.1','latest':items[0] if items else None,'uploads':items}
        except Exception as exc:
            raise HTTPException(500,f'Latest Magazine lookup failed: {type(exc).__name__}: {exc}')

    @app.post('/api/magazine-fresh/provider-test')
    def provider_test(req:Request):
        _login(core,req)
        gw=safe_gateway.ProviderGateway()
        results=[]
        try:
            from PIL import Image
            import io
            im=Image.new("RGB",(24,24),"white")
            b=io.BytesIO(); im.save(b,format="JPEG",quality=60); img=b.getvalue()
        except Exception as exc:
            raise HTTPException(500,f"Diagnostic image creation failed: {type(exc).__name__}: {exc}")
        prompt='Return JSON only: {"diagnostic":"OK"}. This is a provider connectivity test.'
        for p in gw.providers:
            label=p.get("label","UNKNOWN"); kind=p.get("kind","unknown")
            try:
                data=gw._call_gemini(p,img,prompt) if kind=="gemini" else gw._call_openrouter(p,img,prompt)
                results.append({"provider":label,"kind":kind,"result":"OK","detail":"Provider accepted a live vision request."})
            except Exception as exc:
                raw=str(exc); upper=raw.upper()
                if safe_gateway._is_daily_quota(exc): result="DAILY_QUOTA_EXHAUSTED"
                elif safe_gateway._is_quota(exc): result="RATE_LIMIT_OR_QUOTA"
                elif "API_KEY" in upper or "API KEY" in upper or "UNAUTHENTICATED" in upper or "401" in upper: result="AUTHENTICATION_ERROR"
                elif "NOT_FOUND" in upper or "404" in upper: result="MODEL_ACCESS_ERROR"
                else: result="PROVIDER_ERROR"
                detail=re.sub(r'AIza[0-9A-Za-z_-]{20,}', '[REDACTED_KEY]', raw)
                detail=re.sub(r'(?i)(api[_ -]?key["=: ]+)[^ ,;}\]]+', r'\1[REDACTED]', detail)
                results.append({"provider":label,"kind":kind,"result":result,"detail":detail[:1200]})
        return {"status":"OK","version":"8.2.7","tested":len(results),"results":results,
                "note":"One tiny live vision request was attempted per configured model/provider route. No magazine pages were processed."}

    @app.get('/api/magazine-fresh/providers')
    def providers(req:Request):
        _login(core,req)
        gw=safe_gateway.ProviderGateway()
        configured=[p.get('label') for p in gw.providers]
        return {'status':'OK','version':'8.2.5','configured_count':len(configured),
                'providers':configured,
                'gemini_keys':len({id(p.get('client')) for p in gw.providers if p.get('kind')=='gemini'}),
                'openrouter_configured':any(p.get('kind')=='openrouter' for p in gw.providers),
                'message':'Multi-model provider waterfall ready' if configured else 'No vision provider configured'}

    @app.get('/api/magazine-fresh/status/{upload_id}')
    def status(upload_id:str,req:Request):
        _login(core,req)
        with e.connect() as c:r=c.execute(text('SELECT upload_id::text,filename,file_size,total_chunks,received_chunks,status,page_count,processed_pages,created_records,review_records,error_message,created_at,completed_at FROM pi_magazine_fresh_uploads WHERE upload_id=CAST(:u AS UUID)'),{'u':upload_id}).mappings().first()
        if not r: raise HTTPException(404,'Upload not found')
        return dict(r)

    @app.get('/magazine-fresh/records',response_class=HTMLResponse)
    def records(req:Request,limit:int=Query(1000,ge=1,le=5000)):
        _login(core,req)
        with e.connect() as c: rows=c.execute(text('SELECT r.record_id,u.filename,r.page_number,r.section_heading,r.original_description,r.exact_address,r.locality,r.city,r.property_type,r.transaction_type,r.area_value,r.area_unit,r.area_sqft,r.floor,r.amount_raw,r.contact_name,r.contact_number,r.extraction_confidence,r.needs_review FROM pi_magazine_fresh_records r JOIN pi_magazine_fresh_uploads u ON u.upload_id=r.upload_id ORDER BY r.created_at DESC,r.page_number,r.id LIMIT :n'),{'n':limit}).mappings().all()
        heads=['Record ID','PDF','Page','Section','Original Description','Exact Address','Locality','City','Property Type','Rent/Sale','Area','Unit','Area Sq Ft','Floor','Amount','Contact Name','Contact No.','Confidence','Review']
        trs=[]
        for r in rows:
            vals=[r['record_id'],r['filename'],r['page_number'],r['section_heading'],r['original_description'],r['exact_address'],r['locality'],r['city'],r['property_type'],r['transaction_type'],r['area_value'],r['area_unit'],r['area_sqft'],r['floor'],r['amount_raw'],r['contact_name'],r['contact_number'],r['extraction_confidence'],'YES' if r['needs_review'] else 'NO']; trs.append('<tr>'+''.join('<td>'+_e(v)+'</td>' for v in vals)+'</tr>')
        table='<table border=1 cellpadding=7 cellspacing=0 style="border-collapse:collapse;font-size:12px"><thead><tr>'+''.join('<th>'+h+'</th>' for h in heads)+'</tr></thead><tbody>'+(''.join(trs) if trs else '<tr><td colspan=19>No fresh magazine records yet.</td></tr>')+'</tbody></table>'
        return HTMLResponse('<!doctype html><html><body style="font-family:Arial;padding:20px"><p><a href="/magazine-master-import">← Upload Magazine</a></p><h2>Fresh Magazine Records</h2><p>New database only. Historical magazine data is untouched.</p><div style="overflow:auto">'+table+'</div></body></html>')

    return {'status':'REGISTERED','version':VERSION,'routes':['/magazine-master-import','/magazine-fresh','/magazine-fresh/records','/api/magazine-fresh/init','/api/magazine-fresh/chunk/{upload_id}','/api/magazine-fresh/complete/{upload_id}','/api/magazine-fresh/status/{upload_id}']}

if __name__=='__main__': print(VERSION)
