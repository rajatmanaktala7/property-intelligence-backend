from __future__ import annotations
import hashlib, html, json, math, os, re, time, uuid
from typing import Optional
import fitz
from fastapi import BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from google.genai import types
from pydantic import BaseModel, Field
from sqlalchemy import text
import alliance_magazine_safe_gateway_v660 as safe_gateway

VERSION='8.3.5-LOSSLESS-REGION-CAPTURE'
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
    area_value: Optional[object]=None
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
5A. DO NOT return broker, realtor, agency or company profile advertisements as property records.
5B. A broker office address, agency office address, email, website, multiple agent names/phones, or generic SALE-PURCHASE-RENTING-COLLABORATION language describes the broker business, not a property.
5C. Return a record only when the page visibly describes a specific property offering. A contact card with no property-specific area, floor, price, unit or property description is NOT a property listing.
6. transaction_type should be RENT/LEASE/SALE only when visible or clearly inherited from a visible heading.
7. area_unit should be SQFT, SQYD, SQM or ACRE only when visible.
8. exact_address must be an actual property/building/unit/address reference, not merely the locality heading.
9. If unclear, return null rather than guessing.
9A. CAPTURE FIRST, PARSE SECOND: if a genuine property row has a complex area such as 2200FT+200FT GARAGE, preserve the complete expression in original_description and return area_value as the visible expression if a single numeric value is impossible.
9B. Never omit a genuine property row merely because area, amount, floor, contact, address or another structured field is missing or complex. Keep the row and use null for fields that cannot be safely normalized.
9C. A genuine classified property row may be short. Missing price or missing area does NOT make it an advertisement.
9D. LOSSLESS REGION RULE: original_description must contain the COMPLETE visible printed property row from its first character through its final visible character. Never shorten, summarize, ellipsize or stop after area/floor.
9E. Preserve every visible phone number, bracketed contact name, price/amount, floor code and trailing qualifier that belongs to that row.
9F. If the crop cuts a property row at its left or right edge and the full row is not visible, OMIT that edge-cut row from that crop. An overlapping neighboring crop will capture it completely.
9G. Do not duplicate the same row merely because it appears in an overlap.
10. extraction_confidence is 0-100.
OUTPUT CONTRACT:
Return JSON only. No markdown and no commentary.
The top-level object MUST contain a "properties" array.
Each property object MUST use these keys:
section_heading, original_description, exact_address, locality, city, property_type,
transaction_type, area_value, area_unit, floor, amount_raw, contact_name,
contact_number, extraction_confidence.
Use null for unknown fields. original_description must remain exact visible text.'''

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

def _area_number(value):
    if value is None: return None
    if isinstance(value,(int,float)): return float(value)
    s=str(value).strip().replace(',','')
    if re.fullmatch(r'\d+(?:\.\d+)?',s):
        try:return float(s)
        except:return None
    return None

def _sqft(value,unit):
    v=_area_number(value)
    if v is None: return None
    u=str(unit or '').upper().replace(' ','')
    return {'SQFT':v,'SQYD':v*9,'SQM':v*10.7639104167,'ACRE':v*43560}.get(u)

def _extract_page(client,jpg):
    r=client.models.generate_content(model=GEMINI_MODEL,
      contents=[PROMPT,types.Part.from_bytes(data=jpg,mime_type='image/jpeg')],
      config=types.GenerateContentConfig(response_mime_type='application/json',response_schema=FreshEnvelope,temperature=0.0))
    env=FreshEnvelope.model_validate(r.parsed) if getattr(r,'parsed',None) is not None else FreshEnvelope.model_validate_json(r.text)
    return env.properties

def _property_purity(x):
    raw=re.sub(r'\s+',' ',str(getattr(x,'original_description','') or '')).strip()
    if not raw:return False,'EMPTY_ROW'
    up=raw.upper()
    area=getattr(x,'area_value',None)
    floor=str(getattr(x,'floor',None) or '').strip()
    amount=str(getattr(x,'amount_raw',None) or '').strip()
    address=str(getattr(x,'exact_address',None) or '').strip()
    locality=str(getattr(x,'locality',None) or '').strip()
    ptype=str(getattr(x,'property_type',None) or '').strip()
    tx=str(getattr(x,'transaction_type',None) or '').strip()
    property_signal=bool(area is not None or floor or amount or ptype or tx)
    if re.search(r'\b(?:BHK|BR|GF|FF|SF|TF|BMT|BASEMENT|FLOOR|FLR|APT|APARTMENT|FLAT|KOTHI|PLOT|SHOP|OFFICE|SHOWROOM|SQFT|SQYD|SQM|ACRE|\d+\s*Y\b|\d+\s*FT\b)\b',up):
        property_signal=True
    if address and locality and address.upper()!=locality.upper(): property_signal=True
    agency_terms=['REALTORS','PROPERTY DEALER','REAL ESTATE CONSULTANT','REALTY','SALE-PURCHASE-RENTING','SALE | PURCHASE | RENTING','SALE PURCHASE RENTING','COLLABORATION DEALS','COLLABORATION IN','WE SPL. IN','WE SPECIALISE IN','WE SPECIALIZE IN']
    agency_hits=sum(1 for t in agency_terms if t in up)
    compact=re.sub(r'[\s-]','',raw)
    phones=len(set(re.findall(r'(?<!\d)[6-9]\d{9}(?!\d)',compact)))
    has_email=bool(re.search(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b',raw))
    has_web=bool(re.search(r'\b(?:WWW\.|HTTPS?://)',up))
    if not property_signal and (agency_hits>=2 or (agency_hits>=1 and (has_email or has_web or phones>=2))):
        return False,'BROKER_OR_AGENCY_AD'
    if not property_signal:return True,'PROPERTY_CANDIDATE_NEEDS_REVIEW'
    return True,'PROPERTY_LISTING'

def _save(e,uid,page,rows,meta_evidence=None):
    made=review=0
    with e.begin() as c:
        for x in rows:
            accepted,purity_reason=_property_purity(x)
            if not accepted: continue
            original=x.original_description if x.original_description is not None else ''
            if not isinstance(original,str): original=str(original)
            original=original.rstrip('\r\n')
            if not original: continue
            h=hashlib.sha256(original.lower().encode()).hexdigest()
            area_num=_area_number(x.area_value)
            complex_area=(x.area_value is not None and area_num is None)
            nr=complex_area or not x.exact_address or not x.contact_number or x.extraction_confidence is None or float(x.extraction_confidence)<80
            p=dict(record_id='MAGNEW-'+uuid.uuid4().hex[:16].upper(),uid=uid,page=page,section=x.section_heading,
              original=original,address=x.exact_address,locality=x.locality,city=x.city,ptype=x.property_type,tx=x.transaction_type,
              area=area_num,unit=x.area_unit,sqft=_sqft(x.area_value,x.area_unit),floor=x.floor,amount=x.amount_raw,
              cname=x.contact_name,cphone=x.contact_number,confidence=x.extraction_confidence,review=nr,h=h,
              raw=json.dumps(dict(x.model_dump(),_source_region=(meta_evidence or {}).get(_row_key(x))),ensure_ascii=False))
            z=c.execute(text('''INSERT INTO pi_magazine_fresh_records(record_id,upload_id,page_number,section_heading,original_description,
              exact_address,locality,city,property_type,transaction_type,area_value,area_unit,area_sqft,floor,amount_raw,
              contact_name,contact_number,extraction_confidence,needs_review,evidence_hash,raw_json)
              VALUES(:record_id,CAST(:uid AS UUID),:page,:section,:original,:address,:locality,:city,:ptype,:tx,:area,:unit,:sqft,
              :floor,:amount,:cname,:cphone,:confidence,:review,:h,CAST(:raw AS JSONB))
              ON CONFLICT(upload_id,page_number,evidence_hash) DO NOTHING'''),p)
            if z.rowcount:
                made+=1; review+=1 if nr else 0
    return made,review


def _lossless_regions(page):
    rect=page.rect
    w=float(rect.width); h=float(rect.height)
    xbands=[(0.00,0.42),(0.29,0.71),(0.58,1.00)]
    ybands=[(0.00,0.56),(0.44,1.00)]
    out=[]; scale=PDF_RENDER_DPI/72.0; n=0
    for yi,(y0f,y1f) in enumerate(ybands,1):
        for xi,(x0f,x1f) in enumerate(xbands,1):
            n+=1
            clip=fitz.Rect(w*x0f,h*y0f,w*x1f,h*y1f)
            jpg=page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=clip,alpha=False).tobytes('jpeg')
            out.append({
                'region':n,'column':xi,'band':yi,
                'bbox':[round(x0f,4),round(y0f,4),round(x1f,4),round(y1f,4)],
                'jpg':jpg
            })
    return out

def _row_key(x):
    raw=str(getattr(x,'original_description','') or '').upper()
    raw=re.sub(r'\s+',' ',raw).strip()
    return re.sub(r'[^A-Z0-9]+','',raw)

def _row_quality(x):
    raw=str(getattr(x,'original_description','') or '')
    score=len(raw)
    if re.search(r'(?<!\d)[6-9]\d{9}(?!\d)',re.sub(r'[\s-]','',raw)): score+=80
    if getattr(x,'contact_number',None): score+=60
    if getattr(x,'amount_raw',None): score+=30
    if getattr(x,'exact_address',None): score+=20
    return score

def _near_duplicate_key(x):
    raw=re.sub(r'\s+',' ',str(getattr(x,'original_description','') or '')).strip().upper()
    return re.sub(r'[^A-Z0-9]+','',raw)[:34]

def _merge_lossless_rows(region_groups):
    exact={}
    for region,rows in region_groups:
        for x in rows:
            k=_row_key(x)
            if not k: continue
            prev=exact.get(k)
            if prev is None or _row_quality(x)>_row_quality(prev[1]):
                exact[k]=(region,x)

    chosen={}
    for region,x in exact.values():
        k=_near_duplicate_key(x)
        if len(k)<18:
            k=_row_key(x)
        prev=chosen.get(k)
        if prev is None or _row_quality(x)>_row_quality(prev[1]):
            chosen[k]=(region,x)

    rows=[]; evidence={}
    for region,x in chosen.values():
        rows.append(x)
        evidence[_row_key(x)]={
            'region':region['region'],'column':region['column'],'band':region['band'],
            'bbox':region['bbox']
        }
    return rows,evidence

def _region_extract_with_retry(gw,region,max_attempts=3):
    last_meta={'status':'NOT_ATTEMPTED'}
    delays=[0,12,30]
    active_gw=gw
    for attempt in range(max_attempts):
        if delays[attempt]:
            time.sleep(delays[attempt])
        rows,meta=_gateway_extract(active_gw,region['jpg'])
        last_meta=meta or {}
        if rows is not None:
            return rows,last_meta,attempt+1
        if attempt+1<max_attempts:
            active_gw=safe_gateway.ProviderGateway()
            active_gw.max_calls=int(os.getenv("ALLIANCE_MAGAZINE_V823_MAX_CALLS","1000"))
    return None,last_meta,max_attempts

def _extract_lossless_page(gw,page):
    groups=[]; region_results=[]; failed=[]
    for region in _lossless_regions(page):
        rows,rmeta,attempts=_region_extract_with_retry(gw,region)
        item={
            'region':region['region'],'column':region['column'],'band':region['band'],
            'bbox':region['bbox'],'status':rmeta.get('status'),
            'provider':rmeta.get('provider'),'records':None if rows is None else len(rows),
            'attempts':attempts
        }
        region_results.append(item)
        if rows is None:
            failed.append(region['region'])
        else:
            groups.append((region,rows))

    if failed:
        return None,{
            'status':'REGION_INCOMPLETE',
            'failed_regions':failed,
            'regions':region_results,
            'provider':'REGION_WATERFALL'
        }

    merged,evidence=_merge_lossless_rows(groups)
    return merged,{
        'status':'OK','regions':region_results,'records':len(merged),
        'provider':'LOSSLESS_6_REGION','evidence':evidence
    }


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
        try:
            row=FreshProperty.model_validate(item)
            accepted,purity_reason=_property_purity(row)
            if accepted: rows.append(row)
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
        requested_start=max(1,int(os.getenv("MAGAZINE_START_PAGE","23")))
        if start < requested_start-1:
            start=requested_start-1
            with e.begin() as c:
                c.execute(text("UPDATE pi_magazine_fresh_uploads SET processed_pages=:d,error_message=:x WHERE upload_id=CAST(:u AS UUID)"),{'d':start,'x':"Skipped non-property front matter through page "+str(start),'u':uid})
        with e.connect() as c:counts=c.execute(text("SELECT COUNT(*),COUNT(*) FILTER(WHERE needs_review) FROM pi_magazine_fresh_records WHERE upload_id=CAST(:u AS UUID)"),{'u':uid}).first()
        tm=int(counts[0] or 0);tr=int(counts[1] or 0)
        with e.begin() as c:c.execute(text("UPDATE pi_magazine_fresh_uploads SET status='PROCESSING',page_count=:p,created_records=:m,review_records=:r,error_message=NULL WHERE upload_id=CAST(:u AS UUID)"),{'p':pages,'m':tm,'r':tr,'u':uid})
        for i in range(start,pages):
            page=doc.load_page(i)
            rows,meta=_extract_lossless_page(gw,page)
            if rows is None:
                retry=gw.next_retry();reason=meta.get("status","VISION_PROVIDER_UNAVAILABLE")
                msg="Magazine page "+str(i+1)+" is incomplete because one or more required regions failed. No records from this page were saved and the page checkpoint was not advanced."
                if retry:msg+=" Next provider retry after "+retry.isoformat()+"."
                msg+=" Provider status: "+str(reason)+"."
                with e.begin() as c:c.execute(text("UPDATE pi_magazine_fresh_uploads SET status='WAITING_FOR_PROVIDER',error_message=:x WHERE upload_id=CAST(:u AS UUID)"),{'x':msg[:1000],'u':uid})
                doc.close();return
            m,r=_save(e,uid,i+1,rows,meta.get('evidence'));tm+=m;tr+=r
            with e.begin() as c:c.execute(text("UPDATE pi_magazine_fresh_uploads SET processed_pages=:d,created_records=:m,review_records=:r,error_message=:x WHERE upload_id=CAST(:u AS UUID)"),{'d':i+1,'m':tm,'r':tr,'x':("provider="+str(meta.get("provider","UNKNOWN")))[:4000],'u':uid})
        doc.close()
        with e.begin() as c:c.execute(text("UPDATE pi_magazine_fresh_uploads SET status='READY_FOR_REVIEW',error_message=NULL,completed_at=NOW() WHERE upload_id=CAST(:u AS UUID)"),{'u':uid})
    except Exception as exc:
        with e.begin() as c:c.execute(text("UPDATE pi_magazine_fresh_uploads SET status='PAUSED_ERROR',error_message=:x WHERE upload_id=CAST(:u AS UUID)"),{'x':f'{type(exc).__name__}: {exc}'[:4000],'u':uid})

def _page():
    return '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">\n<title>Alliance Magazine Resume</title><style>body{font-family:Arial;background:#f4f7fb;margin:0;color:#172437}.top{background:#102235;color:white;padding:20px}.wrap{max-width:1180px;margin:auto;padding:20px}.card{background:white;padding:18px;border-radius:14px;margin-bottom:14px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.num{font-size:28px;font-weight:800}.muted{color:#66758a}.btn{background:#1266f1;color:white;border:0;border-radius:9px;padding:11px 18px;font-weight:700;cursor:pointer}.good{color:#16833c}.bad{color:#bd2f2f}a{color:#1266f1;text-decoration:none}table{width:100%;border-collapse:collapse}td,th{padding:8px;border-bottom:1px solid #e5eaf0;text-align:left}</style></head>\n<body><div class="top"><b>Fresh Magazine PDF Database · CRE OS 8.3.5</b><br><small>Gemini -> OpenRouter -> Groq Vision · real-page validation · checkpoint resume</small></div>\n<div class="wrap"><div class="card"><a href="/workspace">← Dashboard</a> · <a href="/magazine-fresh/records">New Magazine Records</a></div>\n<div class="card"><h2>Current / Previous Magazine</h2><p id="name" class="muted">Checking stored jobs...</p><div id="stats" class="grid"></div><p id="state" class="muted"></p><button id="resume" class="btn" style="display:none">Resume Extraction</button></div>\n<div class="card"><h3>Recent Magazine Jobs</h3><div id="jobs">Loading...</div></div>\n<div class="card"><h3>New Magazine</h3><p class="muted">For a genuinely new magazine, use the existing upload page after the current database is validated.</p></div></div>\n<script>\nlet active=null,timer=null;\nfunction esc(x){return String(x??\'\').replace(/[&<>"\']/g,m=>({\'&\':\'&amp;\',\'<\':\'&lt;\',\'>\':\'&gt;\',\'"\':\'&quot;\',"\'":\'&#39;\'}[m]))}\nfunction render(d){active=d.upload_id;name.innerHTML=\'<b>\'+esc(d.filename)+\'</b> · \'+esc(d.status);stats.innerHTML=\'<div class="card"><div class="muted">Pages</div><div class="num">\'+d.processed_pages+\'/\'+d.page_count+\'</div></div><div class="card"><div class="muted">Records</div><div class="num">\'+d.created_records+\'</div></div><div class="card"><div class="muted">Needs review</div><div class="num">\'+d.review_records+\'</div></div><div class="card"><div class="muted">Status</div><b>\'+esc(d.status)+\'</b></div>\';state.textContent=d.error_message||\'Ready.\';resume.style.display=[\'ERROR\',\'PAUSED_ERROR\',\'WAITING_FOR_PROVIDER\',\'STORED\'].includes(d.status)?\'inline-block\':\'none\'}\nasync function load(){let r=await fetch(\'/api/magazine-fresh/latest\');if(!r.ok){state.textContent=\'Unable to read stored jobs.\';return}let d=await r.json();if(d.latest)render(d.latest);else{name.textContent=\'No stored Magazine PDF found.\'}jobs.innerHTML=(d.uploads||[]).length?\'<table><tr><th>PDF</th><th>Status</th><th>Pages</th><th>Records</th></tr>\'+d.uploads.map(x=>\'<tr><td>\'+esc(x.filename)+\'</td><td>\'+esc(x.status)+\'</td><td>\'+x.processed_pages+\'/\'+x.page_count+\'</td><td>\'+x.created_records+\'</td></tr>\').join(\'\')+\'</table>\':\'No previous jobs.\'}\nresume.onclick=async()=>{if(!active)return;resume.disabled=true;let r=await fetch(\'/api/magazine-fresh/resume/\'+active,{method:\'POST\'});let d=await r.json();state.textContent=d.status||d.detail||\'Resume requested\';resume.disabled=false;setTimeout(load,1000)}\nload();timer=setInterval(load,4000);\n</script></body></html>'

def register(core):
    app=_app(core); e=_engine(core)
    if app is None or e is None: raise RuntimeError('Fresh Magazine 8.2.2 requires app + engine')
    _setup(e); _remove(app,'/magazine-master-import','GET')

    @app.get('/magazine-master-import',response_class=HTMLResponse)
    def page(req:Request): _login(core,req); return HTMLResponse(_page(),headers={'Cache-Control':'no-store, no-cache, must-revalidate, max-age=0','Pragma':'no-cache','Expires':'0'})

    @app.get('/magazine-fresh',response_class=HTMLResponse)
    def alias(req:Request): _login(core,req); return HTMLResponse(_page(),headers={'Cache-Control':'no-store, no-cache, must-revalidate, max-age=0','Pragma':'no-cache','Expires':'0'})

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


    @app.post('/api/magazine-fresh/real-page-test/{upload_id}')
    def real_page_test(upload_id:str,req:Request,page:int=Query(1,ge=1),dense:int=Query(0,ge=0,le=1)):
        _login(core,req)
        with e.connect() as c:
            row=c.execute(text("SELECT pdf_content,page_count FROM pi_magazine_fresh_uploads WHERE upload_id=CAST(:u AS UUID)"),{'u':upload_id}).first()
        if not row: raise HTTPException(404,'Upload not found')
        if row[0] is None: raise HTTPException(409,'Stored PDF not found')
        pdf=bytes(row[0])
        doc=fitz.open(stream=pdf,filetype='pdf')
        if page>len(doc):
            doc.close()
            raise HTTPException(400,f'Page out of range: {page}/{len(doc)}')
        p=doc.load_page(page-1)

        if dense:
            gw=safe_gateway.ProviderGateway()
            gw.max_calls=int(os.getenv("ALLIANCE_MAGAZINE_V823_MAX_CALLS","1000"))
            try:
                rows,meta=_extract_lossless_page(gw,p)
            finally:
                doc.close()
            preview=[]
            for x in (rows or [])[:80]:
                ok,reason=_property_purity(x)
                preview.append({'accepted':ok,'purity_reason':reason,'original_description':x.original_description,'exact_address':x.exact_address,'locality':x.locality,'property_type':x.property_type,'transaction_type':x.transaction_type,'area_value':x.area_value,'area_unit':x.area_unit,'floor':x.floor,'amount_raw':x.amount_raw,'contact_name':x.contact_name,'contact_number':x.contact_number})
            complete_rows=[x for x in (rows or []) if len(str(x.original_description or '').strip())>=35]
            with_phone=[x for x in (rows or []) if x.contact_number or re.search(r'(?<!\d)[6-9]\d{9}(?!\d)',re.sub(r'[\s-]','',str(x.original_description or '')))]
            return {'status':'OK' if rows is not None else 'REGION_INCOMPLETE','version':'8.3.5','mode':'LOSSLESS_6_REGION','page':page,'region_results':meta.get('regions',[]),'failed_regions':meta.get('failed_regions',[]),'merged_record_count':len(rows or []),'complete_line_35plus_count':len(complete_rows),'phone_preserved_count':len(with_phone),'preview':preview,'note':'8.3.5 lossless canary only. All required regions must succeed. No records written and no checkpoint advanced.'}

        try:
            scale=PDF_RENDER_DPI/72.0
            jpg=p.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False).tobytes('jpeg')
        finally:
            doc.close()

        gw=safe_gateway.ProviderGateway()
        results=[]
        for provider in gw.providers:
            label=provider.get('label','UNKNOWN')
            kind=provider.get('kind','unknown')
            item={'provider':label,'kind':kind,'image_bytes':len(jpg)}
            try:
                data=gw._call_provider(provider,jpg,PROMPT)
                item['transport']='OK'
                raw=None
                if isinstance(data,dict):
                    raw=data.get('properties')
                    if raw is None:
                        raw=data.get('records')
                item['has_properties_array']=isinstance(raw,list)
                item['record_count']=len(raw) if isinstance(raw,list) else 0
                item['top_level_keys']=sorted([str(k) for k in data.keys()])[:20] if isinstance(data,dict) else []
                preview=[]
                if isinstance(raw,list):
                    for rec in raw[:10]:
                        if isinstance(rec,dict):
                            preview.append({
                                'section_heading':rec.get('section_heading'),
                                'original_description':rec.get('original_description') or rec.get('raw_line'),
                                'exact_address':rec.get('exact_address') or rec.get('address'),
                                'locality':rec.get('locality'),
                                'city':rec.get('city'),
                                'property_type':rec.get('property_type'),
                                'transaction_type':rec.get('transaction_type'),
                                'area_value':rec.get('area_value'),
                                'area_unit':rec.get('area_unit'),
                                'floor':rec.get('floor'),
                                'amount_raw':rec.get('amount_raw'),
                                'contact_name':rec.get('contact_name'),
                                'contact_number':rec.get('contact_number'),
                                'extraction_confidence':rec.get('extraction_confidence')
                            })
                item['preview']=preview
                purity_preview=[]
                accepted_count=0
                if isinstance(raw,list):
                    for rec in raw[:10]:
                        if not isinstance(rec,dict): continue
                        try:
                            candidate=dict(rec)
                            if 'original_description' not in candidate and candidate.get('raw_line'):
                                candidate['original_description']=candidate.get('raw_line')
                            obj=FreshProperty.model_validate(candidate)
                            ok,reason=_property_purity(obj)
                            if ok: accepted_count+=1
                            purity_preview.append({'accepted':ok,'purity_reason':reason,'original_description':obj.original_description,'exact_address':obj.exact_address,'locality':obj.locality,'property_type':obj.property_type,'transaction_type':obj.transaction_type,'area_value':obj.area_value,'area_unit':obj.area_unit,'floor':obj.floor,'amount_raw':obj.amount_raw,'contact_name':obj.contact_name,'contact_number':obj.contact_number})
                        except Exception as exc:
                            purity_preview.append({'accepted':False,'purity_reason':'SCHEMA_REJECTED','detail':str(exc)[:300]})
                item['purity_preview']=purity_preview
                item['accepted_property_count']=accepted_count
                item['rejected_by_purity_count']=(len(raw)-accepted_count) if isinstance(raw,list) else 0
                item['result']='OK' if isinstance(raw,list) else 'JSON_SCHEMA_MISMATCH'
            except Exception as exc:
                raw=str(exc)
                upper=raw.upper()
                if safe_gateway._is_daily_quota(exc):
                    result='DAILY_QUOTA_EXHAUSTED'
                elif safe_gateway._is_quota(exc):
                    result='RATE_LIMIT_OR_QUOTA'
                elif '503' in raw and ('UNAVAILABLE' in upper or 'HIGH DEMAND' in upper):
                    result='TRANSIENT_503'
                elif 'API_KEY' in upper or 'API KEY' in upper or 'UNAUTHENTICATED' in upper or '401' in upper:
                    result='AUTHENTICATION_ERROR'
                elif 'NOT_FOUND' in upper or '404' in upper:
                    result='MODEL_ACCESS_ERROR'
                elif 'JSON' in upper or 'DECODE' in upper or 'EXPECTING VALUE' in upper:
                    result='JSON_PARSE_ERROR'
                else:
                    result='PROVIDER_ERROR'
                detail=re.sub(r'AIza[0-9A-Za-z_-]{20,}', '[REDACTED_KEY]', raw)
                detail=re.sub(r'(?i)(api[_ -]?key["=: ]+)[^ ,;}\]]+', r'\1[REDACTED]', detail)
                item.update({'result':result,'detail':detail[:1600]})
            results.append(item)
        return {
            'status':'OK',
            'version':'8.3.5',
            'page':page,
            'render_dpi':PDF_RENDER_DPI,
            'image_bytes':len(jpg),
            'tested':len(results),
            'results':results,
            'note':'Exact stored Magazine page tested with production prompt. No records written and no checkpoint advanced.'
        }

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
                data=gw._call_provider(p,img,prompt)
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
                'groq_configured':any(p.get('kind')=='groq' for p in gw.providers),
                'openrouter_configured':any(p.get('kind')=='openrouter' for p in gw.providers),
                'message':'Gemini -> Groq -> OpenRouter provider waterfall ready' if configured else 'No vision provider configured'}

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
