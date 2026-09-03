from __future__ import annotations
import json, uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html import escape
from fastapi import File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION='18.1-PROPERTY-EDIT-MEDIA-AREA-AMOUNTS'
FACTORS={'SQFT':Decimal('1'),'SQYD':Decimal('9'),'SQMTR':Decimal('10.7639104167'),'ACRE':Decimal('43560')}
LABELS={'SQFT':'sq ft','SQYD':'sq yd','SQMTR':'sq mtr','ACRE':'acre'}
TARGETS={('/edit-property/{property_code}','GET'),('/api/v17-8/property/{property_code}/edit','POST'),('/manual-property-database-v178','GET'),('/manual-property-v18','GET')}

CSS='''*{box-sizing:border-box}body{font-family:Arial;margin:0;background:#f4f7fb;color:#172437}header{background:#102235;color:white;padding:18px 22px}.w{max-width:1500px;margin:auto;padding:18px}.box{background:white;border:1px solid #dfe7f0;border-radius:12px;padding:16px;margin:14px 0}.grid{display:grid;grid-template-columns:repeat(3,minmax(210px,1fr));gap:12px}label small{display:block;font-weight:700;margin-bottom:5px}input,select,textarea{width:100%;padding:10px;border:1px solid #cbd6e2;border-radius:8px}textarea{min-height:110px}.types{display:grid;grid-template-columns:repeat(3,minmax(180px,1fr));gap:7px}.types label{padding:7px;background:#f8fafc;border-radius:7px}.types input{width:auto}.btn,button{display:inline-block;border:0;border-radius:8px;padding:10px 13px;background:#1677ff;color:white;text-decoration:none;font-weight:700;cursor:pointer}.gray{background:#edf2f7!important;color:#24364b!important}.drop{border:2px dashed #9fb1c5;border-radius:12px;padding:18px;text-align:center;background:#fafcff;cursor:pointer;min-height:105px}.drop.over{outline:3px solid #9bbcff}.drop input{display:none}.chip{display:inline-block;background:#eef4fb;border-radius:999px;padding:6px 8px;margin:4px;font-size:12px}.chip button{background:transparent;color:#a31d1d;padding:0 2px}.media-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}.media-card{border:1px solid #dfe7f0;border-radius:10px;padding:8px}.media-card img,.media-card video{width:100%;max-height:180px;object-fit:contain}.error{color:#a11}.ok{color:#08734b}.tablewrap{overflow:auto;max-height:75vh;background:white;border-radius:12px;border:1px solid #dfe7f0}table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:9px;border-bottom:1px solid #edf1f5;text-align:left;vertical-align:top;white-space:nowrap}th{position:sticky;top:0;background:#f8fafc;z-index:1}@media(max-width:850px){.grid,.types{grid-template-columns:1fr}}'''

JS='''function esc(v){return String(v??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));}
function wireDrop(zid,iid,pid){const z=document.getElementById(zid),i=document.getElementById(iid),p=document.getElementById(pid);if(!z||!i)return;const bag=new DataTransfer();window.__bags=window.__bags||{};const render=()=>{i.files=bag.files;if(p)p.innerHTML=[...bag.files].map((f,n)=>`<span class=\"chip\">${esc(f.name)} <button type=\"button\" onclick=\"removeQueued('${iid}',${n})\">x</button></span>`).join('')};window.__bags[iid]={bag,render};const add=fs=>{for(const f of fs||[])if(f instanceof File)bag.items.add(f);render()};i.onchange=()=>{const a=[...i.files];bag.items.clear();add(a)};['dragenter','dragover'].forEach(e=>z.addEventListener(e,x=>{x.preventDefault();z.classList.add('over')}));['dragleave','drop'].forEach(e=>z.addEventListener(e,x=>{x.preventDefault();z.classList.remove('over')}));z.addEventListener('drop',e=>add(e.dataTransfer.files));z.addEventListener('paste',e=>{const a=[...(e.clipboardData?.files||[])];if(a.length){e.preventDefault();add(a)}});z.addEventListener('click',e=>{if(!e.target.closest('button'))i.click()})}
function removeQueued(iid,n){const x=window.__bags?.[iid];if(!x)return;const a=[...x.bag.files].filter((_,j)=>j!==n);x.bag.items.clear();a.forEach(f=>x.bag.items.add(f));x.render()}
'''

def num(v):
    s=str(v or '').replace(',','').replace('₹','').strip()
    if not s:return None
    try:d=Decimal(s)
    except InvalidOperation:raise HTTPException(400,f'Invalid number: {v}')
    if d<0:raise HTTPException(400,'Negative values are not allowed.')
    return d

def area(v,u):
    u=str(u or 'SQFT').upper();u=u if u in FACTORS else 'SQFT';d=num(v)
    if d is None or d<=0:raise HTTPException(400,'Area is required and must be greater than zero.')
    return (d*FACTORS[u]).quantize(Decimal('0.01')),d,u

def money(v):
    if v in (None,''):return ''
    try:n=int(Decimal(str(v)).quantize(Decimal('1')))
    except:return str(v)
    s=str(abs(n));last=s[-3:];head=s[:-3];parts=[]
    while head:parts.insert(0,head[-2:]);head=head[:-2]
    return '₹'+('-' if n<0 else '')+((','.join(parts)+',') if parts else '')+last

def setup(core):
    with core.engine.begin() as c:
        c.execute(text('ALTER TABLE pi_operational_properties ADD COLUMN IF NOT EXISTS area_value NUMERIC(14,2)'))
        c.execute(text('ALTER TABLE pi_operational_properties ADD COLUMN IF NOT EXISTS area_unit TEXT'))
        c.execute(text('ALTER TABLE pi_operational_properties ADD COLUMN IF NOT EXISTS sale_amount NUMERIC(18,2)'))
        c.execute(text('ALTER TABLE pi_operational_properties ALTER COLUMN rent_amount DROP NOT NULL'))
        c.execute(text("UPDATE pi_operational_properties SET area_value=area_sqft,area_unit='SQFT' WHERE area_value IS NULL AND area_sqft IS NOT NULL"))
        c.execute(text("UPDATE pi_operational_properties SET sale_amount=rent_amount,rent_amount=NULL WHERE upper(coalesce(transaction_type,''))='SALE' AND sale_amount IS NULL AND rent_amount IS NOT NULL"))

def rm_routes(app):
    kept=[];removed=[]
    for r in app.router.routes:
        p=getattr(r,'path',None);m=set(getattr(r,'methods',set()) or set())
        if any(p==tp and tm in m for tp,tm in TARGETS):removed.append(p)
        else:kept.append(r)
    app.router.routes[:]=kept
    return removed

def types(core):
    try:return list(core._v17_types())
    except:return ['Retail Shop','High Street Retail','Mall Retail','Office','Restaurant','Cafe','Banquet / Wedding Venue','Hotel','Guest House','Lounge','Club','Bar','Farmhouse','Warehouse','Industrial','Land','Mixed Use','Residential / Villa']

def auth(core,req):return None if core.page_role_or_redirect(req) else RedirectResponse('/login',303)

def safe_types(v):
    if isinstance(v,list):return v
    try:x=json.loads(v or '[]');return x if isinstance(x,list) else []
    except:return []

async def save_media(core,code,files,kind,max_mb):
    saved=0;errors=[]
    for f in files or []:
        if not f or not f.filename:continue
        try:
            b=await f.read()
            if len(b)>max_mb*1024*1024:errors.append(f'{f.filename}: exceeds {max_mb} MB');continue
            mime=f.content_type or 'application/octet-stream'
            if kind=='IMAGE' and not mime.startswith('image/'):errors.append(f'{f.filename}: not an image');continue
            if kind=='VIDEO' and not mime.startswith('video/'):errors.append(f'{f.filename}: not a video');continue
            if kind=='BROCHURE' and mime!='application/pdf' and not f.filename.lower().endswith('.pdf'):errors.append(f'{f.filename}: brochure must be PDF');continue
            with core.engine.begin() as c:c.execute(text('INSERT INTO pi_operational_property_media(property_code,media_type,filename,mime_type,file_size,content) VALUES(:p,:t,:f,:m,:s,:b)'),{'p':code,'t':kind,'f':f.filename,'m':mime,'s':len(b),'b':b})
            saved+=1
        except Exception as e:errors.append(f'{f.filename}: {type(e).__name__}: {e}')
    return saved,errors

def fields_html(p=None):
    p=p or {};v=lambda k:escape('' if p.get(k) is None else str(p.get(k)),quote=True);u=str(p.get('area_unit') or 'SQFT').upper();av=p.get('area_value') if p.get('area_value') is not None else p.get('area_sqft');tx=str(p.get('transaction_type') or 'LEASE').upper();tx=tx if tx in {'LEASE','SALE','BOTH'} else 'LEASE'
    opts=''.join(f'<option value="{x}" {"selected" if x==u else ""}>{LABELS[x]}</option>' for x in LABELS)
    return f'''<div class="grid"><label><small>Property Name</small><input name="property_name" value="{v('property_name')}"></label><label><small>City</small><input name="city" value="{v('city')}"></label><label><small>Location *</small><input name="location" required value="{v('location')}"></label><label><small>Google Location</small><input name="google_location" value="{v('google_location')}"></label><label><small>Area Value *</small><input name="area_value" required inputmode="decimal" value="{escape(str(av or ''))}"></label><label><small>Area Unit *</small><select name="area_unit">{opts}</select></label><label><small>Transaction *</small><select name="transaction_type" id="tx"><option value="LEASE" {"selected" if tx=='LEASE' else ''}>LEASE</option><option value="SALE" {"selected" if tx=='SALE' else ''}>SALE</option><option value="BOTH" {"selected" if tx=='BOTH' else ''}>SALE + LEASE</option></select></label><label id="rentBox"><small>Monthly Rent *</small><input name="rent_amount" id="rentAmount" inputmode="decimal" value="{v('rent_amount')}"></label><label id="saleBox"><small>Sale Amount *</small><input name="sale_amount" id="saleAmount" inputmode="decimal" value="{v('sale_amount')}"></label><label><small>Floor</small><input name="floor" value="{v('floor')}"></label><label><small>Frontage</small><input name="frontage" value="{v('frontage')}"></label><label><small>Parking</small><input name="parking" value="{v('parking')}"></label><label><small>Possession</small><input name="possession" value="{v('possession')}"></label><label><small>Suitable For</small><input name="suitable_for" value="{v('suitable_for')}"></label><label><small>Nearby Brands</small><input name="nearby_brands" value="{v('nearby_brands')}"></label><label><small>Owner / Broker Name</small><input name="owner_broker_name" value="{v('owner_broker_name')}"></label><label><small>Contact Number</small><input name="contact_number" value="{v('contact_number')}"></label><label><small>Contact Role</small><input name="contact_role" value="{v('contact_role')}"></label><label><small>Verification</small><select name="verification_status"><option {"selected" if str(p.get('verification_status') or '').upper()=='UNVERIFIED' else ''}>UNVERIFIED</option><option {"selected" if str(p.get('verification_status') or '').upper()=='VERIFIED' else ''}>VERIFIED</option></select></label><label><small>Updated By</small><input name="entered_by"></label></div>'''

def media_boxes():return '''<h3>Add Media</h3><div class="grid"><div><small><b>Photos</b></small><div class="drop" id="dzImages" tabindex="0">Drop photos here, paste from clipboard, or click<input id="images" type="file" name="images" accept="image/*" multiple><div id="prevImages"></div></div></div><div><small><b>Videos</b></small><div class="drop" id="dzVideos" tabindex="0">Drop videos here or click<input id="videos" type="file" name="videos" accept="video/*" multiple><div id="prevVideos"></div></div></div><div><small><b>Brochure</b></small><div class="drop" id="dzBrochure" tabindex="0">Drop PDF here or click<input id="brochure" type="file" name="brochure" accept=".pdf,application/pdf"><div id="prevBrochure"></div></div></div></div>'''

def tx_js():return "function txUI(){const v=tx.value;rentBox.style.display=(v==='LEASE'||v==='BOTH')?'':'none';saleBox.style.display=(v==='SALE'||v==='BOTH')?'':'none';rentAmount.required=(v==='LEASE'||v==='BOTH');saleAmount.required=(v==='SALE'||v==='BOTH')}tx.onchange=txUI;txUI();"

def register(wrapped):
    core=wrapped.core;app=wrapped.app;setup(core);removed=rm_routes(app)

    @app.get('/manual-property-v18',response_class=HTMLResponse)
    def add_form(req:Request,division:str=Query('DELHI_NCR')):
        r=auth(core,req)
        if r:return r
        checks=''.join(f'<label><input type="checkbox" name="property_types" value="{escape(x)}"> {escape(x)}</label>' for x in types(core))
        return HTMLResponse(f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Add Property</title><style>{CSS}</style></head><body><header><b>Add Property</b><br><small>Sq Ft / Sq Yd / Sq Mtr / Acre + separate Rent and Sale amounts</small></header><div class="w"><form id="f" class="box"><input type="hidden" name="division" value="{escape(division.upper())}">{fields_html()}<h3>Property Types *</h3><div class="types">{checks}</div><h3>Remarks</h3><textarea name="remarks"></textarea>{media_boxes()}<p><button>Save Property</button> <a class="btn gray" href="/manual-property-database-v178">Database</a> <b id="msg"></b></p></form></div><script>{JS}wireDrop('dzImages','images','prevImages');wireDrop('dzVideos','videos','prevVideos');wireDrop('dzBrochure','brochure','prevBrochure');{tx_js()}f.onsubmit=async e=>{{e.preventDefault();msg.textContent='Saving...';try{{const r=await fetch('/api/v18-1/property',{{method:'POST',body:new FormData(f)}}),d=await r.json();if(!r.ok)throw new Error(d.detail||'Save failed');location.href='/property-detail-final/'+encodeURIComponent(d.property_code)}}catch(x){{msg.className='error';msg.textContent='ERROR: '+x.message}}}};</script></body></html>''')

    @app.post('/api/v18-1/property')
    async def add(req:Request,division:str=Form('DELHI_NCR'),property_name:str=Form(''),property_types:list[str]=Form([]),city:str=Form(''),location:str=Form(''),google_location:str=Form(''),area_value:str=Form(''),area_unit:str=Form('SQFT'),transaction_type:str=Form('LEASE'),rent_amount:str=Form(''),sale_amount:str=Form(''),floor:str=Form(''),frontage:str=Form(''),parking:str=Form(''),possession:str=Form(''),suitable_for:str=Form(''),nearby_brands:str=Form(''),owner_broker_name:str=Form(''),contact_number:str=Form(''),contact_role:str=Form('BROKER'),verification_status:str=Form('UNVERIFIED'),remarks:str=Form(''),entered_by:str=Form(''),images:list[UploadFile]=File([]),videos:list[UploadFile]=File([]),brochure:UploadFile|None=File(None)):
        core.need_login(req);pts=[x.strip() for x in property_types if x.strip()]
        if not location.strip():raise HTTPException(400,'Location is required.')
        if not pts:raise HTTPException(400,'Select at least one Property Type.')
        sqft,av,au=area(area_value,area_unit);tx=str(transaction_type).upper();rent=num(rent_amount);sale=num(sale_amount)
        if tx not in {'LEASE','SALE','BOTH'}:raise HTTPException(400,'Invalid transaction.')
        if tx in {'LEASE','BOTH'} and (rent is None or rent<=0):raise HTTPException(400,'Monthly Rent is required.')
        if tx in {'SALE','BOTH'} and (sale is None or sale<=0):raise HTTPException(400,'Sale Amount is required.')
        if tx=='LEASE':sale=None
        if tx=='SALE':rent=None
        with core.engine.connect() as c:
            dup=c.execute(text("SELECT property_code FROM pi_operational_properties WHERE lower(trim(coalesce(city,'')))=lower(trim(:c)) AND lower(trim(coalesce(location,'')))=lower(trim(:l)) AND abs(coalesce(area_sqft,0)-:a)<0.5 AND lower(trim(coalesce(floor,'')))=lower(trim(:f)) ORDER BY id DESC LIMIT 1"),{'c':city,'l':location,'a':sqft,'f':floor}).first()
        if dup:return {'status':'ok','duplicate':True,'property_code':dup[0]}
        code=f"PROP-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}";who=entered_by.strip() or core.actor_name(req)
        with core.engine.begin() as c:c.execute(text("""INSERT INTO pi_operational_properties(property_code,division,property_name,property_types,city,location,google_location,area_sqft,area_value,area_unit,rent_amount,sale_amount,rent_unit,transaction_type,floor,frontage,parking,possession,suitable_for,nearby_brands,owner_broker_name,contact_number,contact_role,verification_status,remarks,created_by,updated_at) VALUES(:code,:div,:name,CAST(:pts AS jsonb),:city,:loc,:google,:sqft,:av,:au,:rent,:sale,'MONTH',:tx,:floor,:front,:park,:poss,:sf,:nb,:person,:phone,:role,:ver,:remarks,:who,NOW())"""),{'code':code,'div':division.upper(),'name':property_name,'pts':json.dumps(pts),'city':city,'loc':location,'google':google_location,'sqft':sqft,'av':av,'au':au,'rent':rent,'sale':sale,'tx':tx,'floor':floor,'front':frontage,'park':parking,'poss':possession,'sf':suitable_for,'nb':nearby_brands,'person':owner_broker_name,'phone':contact_number,'role':contact_role,'ver':verification_status,'remarks':remarks,'who':who})
        errs=[];im,e=await save_media(core,code,images,'IMAGE',12);errs+=e;vi,e=await save_media(core,code,videos,'VIDEO',100);errs+=e;br,e=await save_media(core,code,[brochure] if brochure else [],'BROCHURE',40);errs+=e
        return {'status':'ok','property_code':code,'media_errors':errs,'images_saved':im,'videos_saved':vi,'brochures_saved':br}

    @app.get('/edit-property/{property_code}',response_class=HTMLResponse)
    def edit_form(property_code:str,req:Request):
        r=auth(core,req)
        if r:return r
        with core.engine.connect() as c:
            row=c.execute(text('SELECT * FROM pi_operational_properties WHERE property_code=:p'),{'p':property_code}).first();media=c.execute(text('SELECT id,media_type,filename FROM pi_operational_property_media WHERE property_code=:p ORDER BY id'),{'p':property_code}).fetchall()
        if not row:return HTMLResponse('<h2>Property not found.</h2>',404)
        p=dict(row._mapping);sel=set(safe_types(p.get('property_types')));checks=''.join(f'<label><input type="checkbox" name="property_types" value="{escape(x)}" {"checked" if x in sel else ""}> {escape(x)}</label>' for x in types(core));cards=[]
        for m in media:
            x=dict(m._mapping);mid=x['id'];kind=str(x.get('media_type') or '').upper();fn=escape(str(x.get('filename') or 'media'));url=f'/api/v17-2/property-media/{mid}';pv=f'<img src="{url}">' if kind=='IMAGE' else (f'<video controls src="{url}"></video>' if kind=='VIDEO' else f'<a class="btn" target="_blank" href="{url}">Open PDF</a>');cards.append(f'<div class="media-card">{pv}<div>{fn}</div><button type="button" onclick="rm({mid})">Remove</button></div>')
        return HTMLResponse(f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Edit Property</title><style>{CSS}</style></head><body><header><b>Edit Property · {escape(property_code)}</b><br><small>Edits update this Property ID in place; no duplicate record is created.</small></header><div class="w"><form id="f" class="box">{fields_html(p)}<h3>Property Types *</h3><div class="types">{checks}</div><h3>Remarks</h3><textarea name="remarks">{escape(str(p.get('remarks') or ''))}</textarea>{media_boxes()}<p><button>Save Changes</button> <a class="btn gray" href="/property-detail-final/{escape(property_code)}">Cancel</a> <b id="msg"></b></p></form><div class="box"><h2>Existing Media</h2><div class="media-grid">{''.join(cards) or '<p>No existing media.</p>'}</div></div></div><script>{JS}const code={json.dumps(property_code)};wireDrop('dzImages','images','prevImages');wireDrop('dzVideos','videos','prevVideos');wireDrop('dzBrochure','brochure','prevBrochure');{tx_js()}f.onsubmit=async e=>{{e.preventDefault();msg.textContent='Saving...';try{{const r=await fetch('/api/v17-8/property/'+encodeURIComponent(code)+'/edit',{{method:'POST',body:new FormData(f)}}),d=await r.json();if(!r.ok)throw new Error(d.detail||'Save failed');if((d.media_errors||[]).length){{msg.className='error';msg.textContent='Property saved. Some media failed: '+d.media_errors.join('; ');return}}location.href='/property-detail-final/'+encodeURIComponent(code)}}catch(x){{msg.className='error';msg.textContent='ERROR: '+x.message}}}};async function rm(id){{if(!confirm('Remove this media?'))return;const r=await fetch('/api/v17-8/property/'+encodeURIComponent(code)+'/media/'+id,{{method:'DELETE'}});if(r.ok)location.reload();else alert('Unable to remove media')}};</script></body></html>''')

    @app.post('/api/v17-8/property/{property_code}/edit')
    async def edit(property_code:str,req:Request,property_name:str=Form(''),property_types:list[str]=Form([]),city:str=Form(''),location:str=Form(''),google_location:str=Form(''),area_value:str=Form(''),area_unit:str=Form('SQFT'),transaction_type:str=Form('LEASE'),rent_amount:str=Form(''),sale_amount:str=Form(''),floor:str=Form(''),frontage:str=Form(''),parking:str=Form(''),possession:str=Form(''),suitable_for:str=Form(''),nearby_brands:str=Form(''),owner_broker_name:str=Form(''),contact_number:str=Form(''),contact_role:str=Form(''),verification_status:str=Form('UNVERIFIED'),remarks:str=Form(''),entered_by:str=Form(''),images:list[UploadFile]=File([]),videos:list[UploadFile]=File([]),brochure:UploadFile|None=File(None)):
        core.need_login(req);pts=[x.strip() for x in property_types if x.strip()]
        if not location.strip():raise HTTPException(400,'Location is required.')
        if not pts:raise HTTPException(400,'Select at least one Property Type.')
        sqft,av,au=area(area_value,area_unit);tx=str(transaction_type).upper();rent=num(rent_amount);sale=num(sale_amount)
        if tx not in {'LEASE','SALE','BOTH'}:raise HTTPException(400,'Invalid transaction.')
        if tx in {'LEASE','BOTH'} and (rent is None or rent<=0):raise HTTPException(400,'Monthly Rent is required.')
        if tx in {'SALE','BOTH'} and (sale is None or sale<=0):raise HTTPException(400,'Sale Amount is required.')
        if tx=='LEASE':sale=None
        if tx=='SALE':rent=None
        who=entered_by.strip() or core.actor_name(req)
        with core.engine.begin() as c:
            if not c.execute(text('SELECT 1 FROM pi_operational_properties WHERE property_code=:p'),{'p':property_code}).first():raise HTTPException(404,'Property not found.')
            c.execute(text("""UPDATE pi_operational_properties SET property_name=:name,property_types=CAST(:pts AS jsonb),city=:city,location=:loc,google_location=:google,area_sqft=:sqft,area_value=:av,area_unit=:au,rent_amount=:rent,sale_amount=:sale,rent_unit='MONTH',transaction_type=:tx,floor=:floor,frontage=:front,parking=:park,possession=:poss,suitable_for=:sf,nearby_brands=:nb,owner_broker_name=:person,contact_number=:phone,contact_role=:role,verification_status=:ver,remarks=:remarks,created_by=COALESCE(NULLIF(:who,''),created_by),updated_at=NOW() WHERE property_code=:code"""),{'name':property_name,'pts':json.dumps(pts),'city':city,'loc':location,'google':google_location,'sqft':sqft,'av':av,'au':au,'rent':rent,'sale':sale,'tx':tx,'floor':floor,'front':frontage,'park':parking,'poss':possession,'sf':suitable_for,'nb':nearby_brands,'person':owner_broker_name,'phone':contact_number,'role':contact_role,'ver':verification_status,'remarks':remarks,'who':who,'code':property_code})
        errs=[];im,e=await save_media(core,property_code,images,'IMAGE',12);errs+=e;vi,e=await save_media(core,property_code,videos,'VIDEO',100);errs+=e;br,e=await save_media(core,property_code,[brochure] if brochure else [],'BROCHURE',40);errs+=e
        return {'status':'ok','property_code':property_code,'media_errors':errs,'images_saved':im,'videos_saved':vi,'brochures_saved':br}

    @app.get('/manual-property-database-v178',response_class=HTMLResponse)
    def db(req:Request,division:str=Query('ALL')):
        r=auth(core,req)
        if r:return r
        with core.engine.connect() as c:rows=c.execute(text("""SELECT p.*,COUNT(m.id) FILTER(WHERE m.media_type='IMAGE') image_count,COUNT(m.id) FILTER(WHERE m.media_type='VIDEO') video_count,COUNT(m.id) FILTER(WHERE m.media_type='BROCHURE') brochure_count FROM pi_operational_properties p LEFT JOIN pi_operational_property_media m ON m.property_code=p.property_code GROUP BY p.id ORDER BY p.updated_at DESC NULLS LAST,p.id DESC LIMIT 5000""")).fetchall()
        data=[dict(x._mapping) for x in rows];payload=json.dumps(data,default=str).replace('</','<\\/')
        return HTMLResponse(f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Manual Property Database</title><style>{CSS}.bar{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}}.bar input,.bar select{{width:auto;min-width:180px}}</style></head><body><header><b>Manual Property Database</b><br><small>Transaction + Rent Amount + Sale Amount shown separately</small></header><div class="w"><div class="bar"><a class="btn gray" href="/final-dashboard-v12">Dashboard</a><a class="btn" href="/manual-property-final?division=DELHI_NCR">Add Delhi NCR</a><a class="btn" href="/manual-property-final?division=GOA">Add Goa</a><select id="division"><option value="ALL">ALL</option><option value="DELHI_NCR">DELHI NCR</option><option value="GOA">GOA</option></select><input id="q" placeholder="Search"></div><div class="tablewrap"><table><thead><tr><th>No.</th><th>Property</th><th>City</th><th>Location</th><th>Area</th><th>Transaction</th><th>Rent Amount</th><th>Sale Amount</th><th>Contact</th><th>Media</th><th>Actions</th></tr></thead><tbody id="rows"></tbody></table></div></div><script>{JS}const data={payload},init={json.dumps(division.upper())};division.value=['ALL','DELHI_NCR','GOA'].includes(init)?init:'ALL';function inr(v){{if(v===null||v===undefined||v==='')return '';let n=Math.round(Number(v));if(!Number.isFinite(n))return esc(v);let s=String(Math.abs(n)),last=s.slice(-3),head=s.slice(0,-3),g=[];while(head){{g.unshift(head.slice(-2));head=head.slice(0,-2)}}return '₹'+(n<0?'-':'')+(g.length?g.join(',')+',':'')+last}}function ar(x){{const l={{SQFT:'sq ft',SQYD:'sq yd',SQMTR:'sq mtr',ACRE:'acre'}},u=(x.area_unit||'SQFT').toUpperCase(),v=x.area_value??x.area_sqft??'';return esc(v)+' '+(l[u]||'sq ft')+(u!=='SQFT'&&x.area_sqft?'<br><small>'+esc(x.area_sqft)+' sq ft eq.</small>':'')}}function render(){{const n=(q.value||'').toLowerCase(),d=division.value,a=data.filter(x=>(d==='ALL'||x.division===d)&&(!n||JSON.stringify(x).toLowerCase().includes(n)));rows.innerHTML=a.map((x,i)=>`<tr><td>${{i+1}}</td><td><b>${{esc(x.property_name||x.property_code)}}</b><br><small>${{esc(x.property_code)}}</small></td><td>${{esc(x.city)}}</td><td>${{esc(x.location)}}</td><td>${{ar(x)}}</td><td>${{esc(x.transaction_type)}}</td><td>${{inr(x.rent_amount)}}</td><td>${{inr(x.sale_amount)}}</td><td>${{esc(x.owner_broker_name)}}<br>${{esc(x.contact_number)}}</td><td>Photos ${{x.image_count||0}} | Videos ${{x.video_count||0}} | Brochure ${{x.brochure_count||0}}</td><td><a class="btn" href="/property-detail-final/${{encodeURIComponent(x.property_code)}}">View</a> <a class="btn" href="/edit-property/${{encodeURIComponent(x.property_code)}}">Edit</a></td></tr>`).join('')||'<tr><td colspan="11">No properties found.</td></tr>'}}division.onchange=render;q.oninput=render;render();</script></body></html>''')

    return {'status':'REGISTERED','version':VERSION,'removed_routes':removed}
