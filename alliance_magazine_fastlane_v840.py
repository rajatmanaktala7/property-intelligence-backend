from __future__ import annotations
import hashlib, html, json, re, uuid
from typing import Optional
import fitz
from fastapi import BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

VERSION='8.4-FASTLANE-ZERO-COST-TEXT-FIRST'
PHONE_RE=re.compile(r'(?<!\d)([6-9]\d{9})(?!\d)')
AREA_RE=re.compile(r'(?i)(\d{2,7}(?:\.\d+)?)\s*(SQ\.?\s*FT|SQFT|FT|SQ\.?\s*YD|SQYD|YD|SQ\.?\s*M|SQM|ACRE|Y)\b')
FLOOR_RE=re.compile(r'(?i)\b(GF|GROUND\s*FLOOR|FF|FIRST\s*FLOOR|SF|SECOND\s*FLOOR|TF|THIRD\s*FLOOR|BASEMENT|BMT|\d+(?:ST|ND|RD|TH)?\s*FLOOR)\b')
BHK_RE=re.compile(r'(?i)\b(\d+\s*BHK|\d+\s*BR)\b')
AMOUNT_RE=re.compile(r'(?i)(?:₹|RS\.?|INR)?\s*(\d+(?:\.\d+)?)\s*(CR|CRORE|L|LAC|LAKH|K)\b')
PTYPE_RE=re.compile(r'(?i)\b(APARTMENT|APT|FLAT|KOTHI|VILLA|PLOT|FLOOR|HOUSE|OFFICE|SHOP|SHOWROOM|WAREHOUSE|GODOWN|SPACE|BUILDING|FARMHOUSE)\b')
TX_RE=re.compile(r'(?i)\b(SALE|SELL|RENT|LEASE|LEASING|RENTING)\b')
AGENCY_RE=re.compile(r'(?i)\b(REALTORS?|PROPERTY\s+DEALER|REAL\s+ESTATE\s+CONSULTANT|REALTY)\b')
EMAIL_RE=re.compile(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}')
WEB_RE=re.compile(r'(?i)\b(?:www\.|https?://)')

def _app(core): return getattr(core,'app',None) or core
def _engine(core): return getattr(core,'engine',None)
def _login(core,req):
    fn=getattr(core,'need_login',None)
    return fn(req) if fn else 'team'
def _esc(v): return html.escape('' if v is None else str(v))

def _setup(e):
    with e.begin() as c:
        c.execute(text("""CREATE TABLE IF NOT EXISTS pi_magazine_fastlane_jobs(
          upload_id UUID PRIMARY KEY,status TEXT NOT NULL DEFAULT 'READY',start_page INTEGER NOT NULL DEFAULT 23,
          end_page INTEGER,current_page INTEGER NOT NULL DEFAULT 0,pages_processed INTEGER NOT NULL DEFAULT 0,
          records_created INTEGER NOT NULL DEFAULT 0,pages_without_text INTEGER NOT NULL DEFAULT 0,error_message TEXT,
          started_at TIMESTAMPTZ,updated_at TIMESTAMPTZ DEFAULT NOW(),completed_at TIMESTAMPTZ)"""))
        c.execute(text("""CREATE TABLE IF NOT EXISTS pi_magazine_fastlane_records(
          id BIGSERIAL PRIMARY KEY,record_id TEXT UNIQUE NOT NULL,upload_id UUID NOT NULL,page_number INTEGER NOT NULL,
          source_method TEXT NOT NULL,section_heading TEXT,original_description TEXT NOT NULL,transaction_type TEXT,
          property_type TEXT,area_value TEXT,area_unit TEXT,floor TEXT,amount_raw TEXT,
          contact_numbers JSONB NOT NULL DEFAULT '[]'::jsonb,signal_score INTEGER NOT NULL DEFAULT 0,
          needs_review BOOLEAN NOT NULL DEFAULT TRUE,bbox JSONB,evidence_hash TEXT NOT NULL,
          raw_json JSONB NOT NULL DEFAULT '{}'::jsonb,created_at TIMESTAMPTZ DEFAULT NOW(),
          UNIQUE(upload_id,page_number,evidence_hash))"""))
        c.execute(text('CREATE INDEX IF NOT EXISTS idx_magfast_upload_page ON pi_magazine_fastlane_records(upload_id,page_number)'))

def _unit(raw):
    if not raw:return None
    u=raw.upper().replace('.','').replace(' ','')
    if u in ('SQFT','FT'):return 'SQFT'
    if u in ('SQYD','YD','Y'):return 'SQYD'
    if u in ('SQM','M'):return 'SQM'
    if u=='ACRE':return 'ACRE'
    return None

def _tx(s,section=None):
    m=TX_RE.search(s or '')
    if m:
        q=m.group(1).upper()
        if q in ('SALE','SELL'):return 'SALE'
        if 'LEASE' in q:return 'LEASE'
        return 'RENT'
    u=(section or '').upper()
    if 'SALE' in u:return 'SALE'
    if 'LEASE' in u:return 'LEASE'
    if 'RENT' in u:return 'RENT'
    return None

def _signal(s):
    score=0
    if PHONE_RE.search(s):score+=3
    if AREA_RE.search(s):score+=3
    if FLOOR_RE.search(s):score+=2
    if BHK_RE.search(s):score+=2
    if AMOUNT_RE.search(s):score+=2
    if PTYPE_RE.search(s):score+=2
    if TX_RE.search(s):score+=1
    return score

def _is_heading(s):
    t=(s or '').strip()
    if not t or len(t)>70:return False
    if PHONE_RE.search(t) or AREA_RE.search(t) or AMOUNT_RE.search(t):return False
    u=t.upper()
    if re.search(r'\b(RESIDENTIAL|COMMERCIAL|INDUSTRIAL)\b',u) and re.search(r'\b(SALE|RENT|LEASE|RENTING)\b',u):return True
    return t==u and sum(ch.isalpha() for ch in t)>=4 and len(t.split())<=6

def _column_id(x0,width):
    if width<=0:return 0
    f=x0/width
    return 0 if f<.34 else (1 if f<.67 else 2)

def _native_lines(page):
    d=page.get_text('dict',sort=True)
    out=[]
    for block in d.get('blocks',[]):
        if block.get('type')!=0:continue
        for line in block.get('lines',[]):
            raw=''.join(span.get('text','') for span in line.get('spans',[]) if span.get('text',''))
            if not raw.strip():continue
            bbox=line.get('bbox') or block.get('bbox') or [0,0,0,0]
            out.append({'text':raw.rstrip('\r\n'),'bbox':[float(x) for x in bbox],'x0':float(bbox[0]),'y0':float(bbox[1])})
    out.sort(key=lambda z:(z['y0'],z['x0']))
    return out

def _extract_candidates(page):
    lines=_native_lines(page)
    chars=sum(len(x['text']) for x in lines)
    width=float(page.rect.width)
    headings={0:None,1:None,2:None}
    out=[]
    for row in lines:
        raw=row['text']; col=_column_id(row['x0'],width)
        if _is_heading(raw):
            headings[col]=raw.strip(); continue
        sig=_signal(raw)
        if sig<3:continue
        phones=PHONE_RE.findall(raw)
        property_evidence=bool(AREA_RE.search(raw) or FLOOR_RE.search(raw) or BHK_RE.search(raw) or PTYPE_RE.search(raw) or AMOUNT_RE.search(raw))
        if AGENCY_RE.search(raw) and (WEB_RE.search(raw) or EMAIL_RE.search(raw)) and phones and not property_evidence:continue
        am=AREA_RE.search(raw); fl=FLOOR_RE.search(raw); amt=AMOUNT_RE.search(raw); pt=PTYPE_RE.search(raw); section=headings.get(col)
        out.append({'source_method':'NATIVE_PDF_TEXT','section_heading':section,'original_description':raw,
          'transaction_type':_tx(raw,section),'property_type':pt.group(1).upper() if pt else None,
          'area_value':am.group(1) if am else None,'area_unit':_unit(am.group(2)) if am else None,
          'floor':fl.group(1).upper() if fl else None,'amount_raw':amt.group(0).strip() if amt else None,
          'contact_numbers':list(dict.fromkeys(phones)),'signal_score':sig,'needs_review':sig<7 or not phones,
          'bbox':row['bbox'],'raw_json':{'column':col,'text_chars_on_page':chars}})
    return out,{'method':'NATIVE_PDF_TEXT','text_chars':chars,'line_count':len(lines)}

def _save(e,uid,page_no,rows):
    made=0
    with e.begin() as c:
        for x in rows:
            h=hashlib.sha256(f'{page_no}|{x["bbox"]}|{x["original_description"]}'.encode('utf-8','ignore')).hexdigest()
            p={'rid':'MAGFAST-'+uuid.uuid4().hex[:16].upper(),'uid':uid,'page':page_no,'method':x['source_method'],
               'section':x['section_heading'],'original':x['original_description'],'tx':x['transaction_type'],'ptype':x['property_type'],
               'area':x['area_value'],'unit':x['area_unit'],'floor':x['floor'],'amount':x['amount_raw'],'phones':json.dumps(x['contact_numbers']),
               'score':x['signal_score'],'review':x['needs_review'],'bbox':json.dumps(x['bbox']),'h':h,'raw':json.dumps(x['raw_json'],ensure_ascii=False)}
            z=c.execute(text("""INSERT INTO pi_magazine_fastlane_records(record_id,upload_id,page_number,source_method,section_heading,original_description,
              transaction_type,property_type,area_value,area_unit,floor,amount_raw,contact_numbers,signal_score,needs_review,bbox,evidence_hash,raw_json)
              VALUES(:rid,CAST(:uid AS UUID),:page,:method,:section,:original,:tx,:ptype,:area,:unit,:floor,:amount,CAST(:phones AS JSONB),:score,:review,
              CAST(:bbox AS JSONB),:h,CAST(:raw AS JSONB)) ON CONFLICT(upload_id,page_number,evidence_hash) DO NOTHING"""),p)
            if z.rowcount:made+=1
    return made

def _run(core,uid,start_page,end_page):
    e=_engine(core)
    try:
        with e.connect() as c:r=c.execute(text('SELECT pdf_content FROM pi_magazine_fresh_uploads WHERE upload_id=CAST(:u AS UUID)'),{'u':uid}).first()
        if not r or r[0] is None:raise RuntimeError('Stored Magazine PDF not found')
        doc=fitz.open(stream=bytes(r[0]),filetype='pdf'); sp=max(1,int(start_page or 23)); ep=min(len(doc),int(end_page or len(doc)))
        with e.begin() as c:c.execute(text("""INSERT INTO pi_magazine_fastlane_jobs(upload_id,status,start_page,end_page,current_page,pages_processed,records_created,pages_without_text,error_message,started_at,updated_at,completed_at)
          VALUES(CAST(:u AS UUID),'RUNNING',:s,:ep,:s,0,0,0,NULL,NOW(),NOW(),NULL)
          ON CONFLICT(upload_id) DO UPDATE SET status='RUNNING',start_page=:s,end_page=:ep,current_page=:s,pages_processed=0,error_message=NULL,started_at=NOW(),updated_at=NOW(),completed_at=NULL"""),{'u':uid,'s':sp,'ep':ep})
        created=0; no_text=0; done=0
        for page_no in range(sp,ep+1):
            rows,meta=_extract_candidates(doc.load_page(page_no-1))
            if meta['text_chars']<120:no_text+=1
            created+=_save(e,uid,page_no,rows); done+=1
            with e.begin() as c:c.execute(text('UPDATE pi_magazine_fastlane_jobs SET current_page=:p,pages_processed=:d,records_created=:r,pages_without_text=:nt,updated_at=NOW() WHERE upload_id=CAST(:u AS UUID)'),{'p':page_no,'d':done,'r':created,'nt':no_text,'u':uid})
        doc.close()
        with e.begin() as c:c.execute(text("UPDATE pi_magazine_fastlane_jobs SET status='READY_FOR_REVIEW',completed_at=NOW(),updated_at=NOW() WHERE upload_id=CAST(:u AS UUID)"),{'u':uid})
    except Exception as exc:
        with e.begin() as c:c.execute(text("""INSERT INTO pi_magazine_fastlane_jobs(upload_id,status,error_message,updated_at) VALUES(CAST(:u AS UUID),'ERROR',:x,NOW())
          ON CONFLICT(upload_id) DO UPDATE SET status='ERROR',error_message=:x,updated_at=NOW()"""),{'u':uid,'x':f'{type(exc).__name__}: {exc}'[:3000]})

def register(core):
    app=_app(core); e=_engine(core)
    if app is None or e is None:raise RuntimeError('FastLane requires app + engine')
    _setup(e)

    @app.get('/magazine-fastlane',response_class=HTMLResponse)
    def page(req:Request):
        _login(core,req)
        return HTMLResponse("""<!doctype html><html><body style='font-family:Arial;padding:20px'><h2>Alliance Magazine FastLane · CRE OS 8.4</h2>
        <p><b>Free first:</b> stored PDF text → deterministic parser → review queue. No Gemini, Groq or OpenRouter.</p>
        <p><button onclick='test23()'>Test Page 23 Free</button> <button onclick='runall()'>Run From Page 23 Free</button></p>
        <pre id='o'>Loading latest magazine...</pre><p><a href='/magazine-fastlane/records'>Open FastLane Records</a></p><script>
        let u=null;async function init(){let d=await (await fetch('/api/magazine-fresh/latest')).json();u=d.latest&&d.latest.upload_id;o.textContent=d.latest?d.latest.filename:'No stored PDF'}
        async function test23(){o.textContent='Testing...';let d=await (await fetch('/api/magazine-fastlane/test/'+u+'?page=23')).json();o.textContent=JSON.stringify(d,null,2)}
        async function runall(){let d=await (await fetch('/api/magazine-fastlane/start/'+u+'?start_page=23',{method:'POST'})).json();o.textContent=JSON.stringify(d,null,2)}init();</script></body></html>""",headers={'Cache-Control':'no-store'})

    @app.get('/api/magazine-fastlane/test/{upload_id}')
    def test(upload_id:str,req:Request,page:int=Query(23,ge=1)):
        _login(core,req)
        with e.connect() as c:r=c.execute(text('SELECT pdf_content,filename FROM pi_magazine_fresh_uploads WHERE upload_id=CAST(:u AS UUID)'),{'u':upload_id}).first()
        if not r or r[0] is None:raise HTTPException(404,'Stored Magazine PDF not found')
        doc=fitz.open(stream=bytes(r[0]),filetype='pdf')
        if page>len(doc):doc.close();raise HTTPException(400,'Page outside PDF')
        rows,meta=_extract_candidates(doc.load_page(page-1));doc.close()
        return {'status':'PASS_NATIVE_TEXT' if meta['text_chars']>=120 else 'TEXT_LAYER_WEAK','version':VERSION,'filename':r[1],'page':page,
          'cost':0,'external_api_calls':0,'text_chars':meta['text_chars'],'line_count':meta['line_count'],'candidate_count':len(rows),'preview':rows[:80],
          'recommendation':'RUN_FASTLANE' if meta['text_chars']>=120 and len(rows)>=5 else 'NEEDS_LOCAL_OCR_OR_MANUAL_REVIEW'}

    @app.post('/api/magazine-fastlane/start/{upload_id}')
    def start(upload_id:str,req:Request,bg:BackgroundTasks,start_page:int=Query(23,ge=1),end_page:Optional[int]=Query(None,ge=1)):
        _login(core,req); bg.add_task(_run,core,upload_id,start_page,end_page)
        return {'status':'STARTED','version':VERSION,'upload_id':upload_id,'start_page':start_page,'end_page':end_page,'cost':0,'external_api_calls':0}

    @app.get('/api/magazine-fastlane/status/{upload_id}')
    def status(upload_id:str,req:Request):
        _login(core,req)
        with e.connect() as c:r=c.execute(text('SELECT upload_id::text,status,start_page,end_page,current_page,pages_processed,records_created,pages_without_text,error_message,started_at,updated_at,completed_at FROM pi_magazine_fastlane_jobs WHERE upload_id=CAST(:u AS UUID)'),{'u':upload_id}).mappings().first()
        return {'status':'READY','version':VERSION,'upload_id':upload_id} if not r else {'version':VERSION,**dict(r)}

    @app.get('/magazine-fastlane/records',response_class=HTMLResponse)
    def records(req:Request,limit:int=Query(1500,ge=1,le=5000)):
        _login(core,req)
        with e.connect() as c:rows=c.execute(text('SELECT record_id,page_number,source_method,section_heading,original_description,transaction_type,property_type,area_value,area_unit,floor,amount_raw,contact_numbers,signal_score,needs_review FROM pi_magazine_fastlane_records ORDER BY page_number,id LIMIT :n'),{'n':limit}).mappings().all()
        heads=['ID','Page','Method','Section','Original Description','Rent/Sale','Type','Area','Unit','Floor','Amount','Contacts','Score','Review']; body=[]
        for r in rows:
            vals=[r['record_id'],r['page_number'],r['source_method'],r['section_heading'],r['original_description'],r['transaction_type'],r['property_type'],r['area_value'],r['area_unit'],r['floor'],r['amount_raw'],', '.join(r['contact_numbers'] or []),r['signal_score'],'YES' if r['needs_review'] else 'NO']
            body.append('<tr>'+''.join('<td>'+_esc(v)+'</td>' for v in vals)+'</tr>')
        table="<table border=1 cellpadding=6 cellspacing=0 style='border-collapse:collapse;font-size:12px'><tr>"+''.join('<th>'+h+'</th>' for h in heads)+'</tr>'+(''.join(body) if body else '<tr><td colspan=14>No FastLane records yet.</td></tr>')+'</table>'
        return HTMLResponse("<!doctype html><html><body style='font-family:Arial;padding:20px'><p><a href='/magazine-fastlane'>← FastLane</a></p><h2>FastLane Review Queue</h2>"+table+'</body></html>')

    return {'status':'REGISTERED','version':VERSION,'routes':['/magazine-fastlane','/api/magazine-fastlane/test/{upload_id}','/api/magazine-fastlane/start/{upload_id}','/api/magazine-fastlane/status/{upload_id}','/magazine-fastlane/records']}
