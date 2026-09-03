from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html import escape

from fastapi import File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION = "18.2-UNIFIED-DATA-ORGANIZER"
AREA_FACTORS = {"SQFT": Decimal("1"), "SQYD": Decimal("9"), "SQMTR": Decimal("10.7639104167"), "ACRE": Decimal("43560")}
AREA_LABELS = {"SQFT": "Sq Ft", "SQYD": "Sq Yd", "SQMTR": "Sq Mtr", "ACRE": "Acre"}
PROPERTY_TYPES = ["Retail Shop","High Street Retail","Mall Retail","Office","Restaurant","Cafe","Banquet / Wedding Venue","Hotel","Guest House","Lounge","Club","Bar","Farmhouse","Warehouse","Industrial","Land","Mixed Use","Residential / Villa","Pre-Rented Property"]

CSS = """
*{box-sizing:border-box}body{margin:0;background:#f4f7fb;color:#172437;font-family:Arial,sans-serif}header{background:#102235;color:#fff;padding:18px 22px}.w{max-width:1800px;margin:auto;padding:18px}.box,.card{background:#fff;border:1px solid #dfe7f0;border-radius:12px;padding:15px;margin:12px 0}.grid{display:grid;grid-template-columns:repeat(3,minmax(210px,1fr));gap:12px}.types{display:grid;grid-template-columns:repeat(3,minmax(180px,1fr));gap:7px}.types label{background:#f8fafc;border-radius:7px;padding:7px}.types input{width:auto}.kpis{display:grid;grid-template-columns:repeat(7,minmax(130px,1fr));gap:10px}.kpi{background:#fff;border:1px solid #dfe7f0;border-radius:10px;padding:12px}.kpi b{font-size:25px;display:block}label small{display:block;font-weight:700;margin-bottom:5px}input,select,textarea{width:100%;padding:10px;border:1px solid #cbd6e2;border-radius:8px}textarea{min-height:100px}.btn,button{display:inline-block;border:0;border-radius:8px;padding:9px 12px;background:#1677ff;color:#fff;text-decoration:none;font-weight:700;cursor:pointer}.gray{background:#edf2f7!important;color:#24364b!important}.green{background:#08734b!important}.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.toolbar input,.toolbar select{width:auto;min-width:180px}.tablewrap{overflow:auto;max-height:72vh;border:1px solid #dfe7f0;border-radius:10px;background:#fff}table{border-collapse:collapse;width:100%;font-size:12px}th,td{padding:9px;border-bottom:1px solid #edf1f5;text-align:left;vertical-align:top;white-space:nowrap}th{position:sticky;top:0;background:#f8fafc;z-index:2}.drop{border:2px dashed #9fb1c5;border-radius:12px;padding:18px;text-align:center;background:#fafcff;cursor:pointer;min-height:105px}.drop.over{outline:3px solid #a9c5ff}.drop input{display:none}.chip{display:inline-block;background:#eef4fb;border-radius:999px;padding:5px 8px;margin:3px;font-size:11px}.pill{display:inline-block;border-radius:999px;padding:4px 7px;font-size:11px;font-weight:bold}.verified{background:#dcfce7;color:#166534}.unverified{background:#fef3c7;color:#92400e}.err{color:#a11}.hint{font-size:12px;color:#62748a}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}.navcard{display:block;background:#fff;border:1px solid #dfe7f0;border-radius:12px;padding:17px;text-decoration:none;color:#172437}.navcard p{color:#62748a;font-size:12px}@media(max-width:950px){.grid,.types,.kpis{grid-template-columns:1fr}.toolbar input,.toolbar select{width:100%}}
"""

DROP_JS = r"""
function esc(v){return String(v??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));}
function wireDrop(zid,iid,pid,paste=true){const z=document.getElementById(zid),i=document.getElementById(iid),p=document.getElementById(pid);if(!z||!i)return;const bag=new DataTransfer();window.__bags=window.__bags||{};const render=()=>{i.files=bag.files;if(p)p.innerHTML=[...bag.files].map((f,n)=>`<span class=\"chip\">${esc(f.name)} <button type=\"button\" class=\"gray\" onclick=\"removeQueued('${iid}',${n})\">x</button></span>`).join('')};window.__bags[iid]={bag,render};const add=fs=>{for(const f of fs||[])if(f instanceof File)bag.items.add(f);render()};i.onchange=()=>{const a=[...i.files];bag.items.clear();add(a)};['dragenter','dragover'].forEach(e=>z.addEventListener(e,x=>{x.preventDefault();z.classList.add('over')}));['dragleave','drop'].forEach(e=>z.addEventListener(e,x=>{x.preventDefault();z.classList.remove('over')}));z.addEventListener('drop',e=>add(e.dataTransfer.files));if(paste)z.addEventListener('paste',e=>{const a=[...(e.clipboardData?.files||[])];if(a.length){e.preventDefault();add(a)}});z.addEventListener('click',e=>{if(!e.target.closest('button'))i.click()})}
function removeQueued(iid,n){const x=window.__bags?.[iid];if(!x)return;const a=[...x.bag.files].filter((_,j)=>j!==n);x.bag.items.clear();a.forEach(f=>x.bag.items.add(f));x.render()}
"""

def q(v): return escape("" if v is None else str(v), quote=True)
def auth(core, req): return None if core.page_role_or_redirect(req) else RedirectResponse('/login',303)
def actor(core, req):
    try:return core.actor_name(req)
    except Exception:return 'team'

def dec(v):
    s=str(v or '').replace(',','').replace('₹','').strip()
    if not s:return None
    try:return Decimal(s)
    except InvalidOperation:raise HTTPException(400,f'Invalid number: {v}')

def parse_area(value, unit):
    d=dec(value);u=str(unit or 'SQFT').upper()
    if d is None or d<=0:raise HTTPException(400,'Area must be greater than zero.')
    if u not in AREA_FACTORS:raise HTTPException(400,'Invalid area unit.')
    return d,u,(d*AREA_FACTORS[u]).quantize(Decimal('0.01'))

def parse_money(raw):
    s=str(raw or '').strip(); low=s.lower().replace(',','').replace('₹',' ')
    if not low:return None,''
    m=re.search(r'(-?\d+(?:\.\d+)?)',low)
    if not m:raise HTTPException(400,f'Could not understand amount: {raw}')
    n=Decimal(m.group(1))
    if n<0:raise HTTPException(400,'Negative amount is not allowed.')
    mult=Decimal('1')
    if re.search(r'\b(cr|crore|crores)\b',low):mult=Decimal('10000000')
    elif re.search(r'\b(l|lac|lakh|lakhs)\b',low):mult=Decimal('100000')
    elif re.search(r'\b(k|thousand)\b',low):mult=Decimal('1000')
    return (n*mult).quantize(Decimal('0.01')),s

def money(v):
    if v in (None,''):return '—'
    try:n=Decimal(str(v))
    except Exception:return str(v)
    if abs(n)>=Decimal('10000000'):return f"₹{(n/Decimal('10000000')).normalize():f} Cr"
    if abs(n)>=Decimal('100000'):return f"₹{(n/Decimal('100000')).normalize():f} Lakh"
    i=int(n.quantize(Decimal('1')));s=str(abs(i));last=s[-3:];head=s[:-3];parts=[]
    while head:parts.insert(0,head[-2:]);head=head[:-2]
    return '₹'+('-' if i<0 else '')+((','.join(parts)+',') if parts else '')+last

def area_options(sel='SQFT'):
    return ''.join(f'<option value="{k}" {"selected" if k==sel else ""}>{v}</option>' for k,v in AREA_LABELS.items())

def tx_options(sel='LEASE'):
    s=str(sel or 'LEASE').upper();return f'<option value="LEASE" {"selected" if s=="LEASE" else ""}>LEASE</option><option value="SALE" {"selected" if s=="SALE" else ""}>SALE</option><option value="BOTH" {"selected" if s in {"BOTH","SALE + LEASE"} else ""}>SALE + LEASE</option>'

def type_checks(selected=()):
    sel=set(selected or ());return ''.join(f'<label><input type="checkbox" name="property_types" value="{q(x)}" {"checked" if x in sel else ""}> {q(x)}</label>' for x in PROPERTY_TYPES)

def setup(core):
    with core.engine.begin() as c:
        stmts=[
            "ALTER TABLE pi_operational_properties ADD COLUMN IF NOT EXISTS area_value NUMERIC(14,2)","ALTER TABLE pi_operational_properties ADD COLUMN IF NOT EXISTS area_unit TEXT","ALTER TABLE pi_operational_properties ADD COLUMN IF NOT EXISTS sale_amount NUMERIC(18,2)","ALTER TABLE pi_operational_properties ADD COLUMN IF NOT EXISTS rent_input_text TEXT","ALTER TABLE pi_operational_properties ADD COLUMN IF NOT EXISTS sale_input_text TEXT","ALTER TABLE pi_operational_properties ALTER COLUMN rent_amount DROP NOT NULL","UPDATE pi_operational_properties SET area_value=area_sqft,area_unit='SQFT' WHERE area_value IS NULL AND area_sqft IS NOT NULL",
            "ALTER TABLE pi_operational_requirements ADD COLUMN IF NOT EXISTS entry_date TIMESTAMPTZ DEFAULT NOW()","ALTER TABLE pi_operational_requirements ADD COLUMN IF NOT EXISTS minimum_area_value NUMERIC(14,2)","ALTER TABLE pi_operational_requirements ADD COLUMN IF NOT EXISTS maximum_area_value NUMERIC(14,2)","ALTER TABLE pi_operational_requirements ADD COLUMN IF NOT EXISTS area_unit TEXT","ALTER TABLE pi_operational_requirements ADD COLUMN IF NOT EXISTS rent_input_text TEXT","ALTER TABLE pi_operational_requirements ADD COLUMN IF NOT EXISTS rent_basis TEXT DEFAULT 'PER_MONTH'","ALTER TABLE pi_operational_requirements ADD COLUMN IF NOT EXISTS sale_budget NUMERIC(18,2)","ALTER TABLE pi_operational_requirements ADD COLUMN IF NOT EXISTS sale_input_text TEXT","ALTER TABLE pi_operational_requirements ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW()","UPDATE pi_operational_requirements SET entry_date=COALESCE(created_at,NOW()) WHERE entry_date IS NULL","UPDATE pi_operational_requirements SET minimum_area_value=minimum_area_sqft,maximum_area_value=maximum_area_sqft,area_unit='SQFT' WHERE area_unit IS NULL",
            "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS area_value NUMERIC(14,2)","ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS area_unit TEXT","ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS sale_amount NUMERIC(18,2)","ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS rent_input_text TEXT","ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS sale_input_text TEXT"
        ]
        for s in stmts:
            try:c.execute(text(s))
            except Exception:pass

def remove_routes(app,targets):
    kept=[];removed=[]
    for r in app.router.routes:
        p=getattr(r,'path',None);m=set(getattr(r,'methods',set()) or set())
        if any(p==tp and tm in m for tp,tm in targets):removed.append(p)
        else:kept.append(r)
    app.router.routes[:]=kept;return removed

async def save_media(core,code,files,kind,max_mb):
    saved=0;errors=[]
    for f in files or []:
        if not f or not getattr(f,'filename',None):continue
        try:
            b=await f.read();mime=f.content_type or 'application/octet-stream'
            if len(b)>max_mb*1024*1024:errors.append(f'{f.filename}: exceeds {max_mb} MB');continue
            if kind=='IMAGE' and not mime.startswith('image/'):errors.append(f'{f.filename}: not an image');continue
            if kind=='VIDEO' and not mime.startswith('video/'):errors.append(f'{f.filename}: not a video');continue
            if kind=='BROCHURE' and mime!='application/pdf' and not f.filename.lower().endswith('.pdf'):errors.append(f'{f.filename}: brochure must be PDF');continue
            with core.engine.begin() as c:c.execute(text('INSERT INTO pi_operational_property_media(property_code,media_type,filename,mime_type,file_size,content) VALUES(:p,:t,:f,:m,:s,:b)'),{'p':code,'t':kind,'f':f.filename,'m':mime,'s':len(b),'b':b})
            saved+=1
        except Exception as e:errors.append(f'{f.filename}: {type(e).__name__}: {e}')
    return saved,errors

def property_form(division,p=None,edit=False):
    p=p or {};pts=p.get('property_types') or []
    if isinstance(pts,str):
        try:pts=json.loads(pts)
        except Exception:pts=[]
    av=p.get('area_value') if p.get('area_value') is not None else p.get('area_sqft');au=str(p.get('area_unit') or 'SQFT').upper();tx=str(p.get('transaction_type') or 'LEASE').upper();rent=p.get('rent_input_text') or (str(p.get('rent_amount')) if p.get('rent_amount') is not None else '');sale=p.get('sale_input_text') or (str(p.get('sale_amount')) if p.get('sale_amount') is not None else '')
    endpoint=f"/api/v18-2/property/{q(p.get('property_code'))}/edit" if edit else '/api/v18-2/property';title='Edit Property' if edit else 'Add Manual Property'
    return f'''<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>{title}</title><style>{CSS}</style></head><body><header><b>{title}</b><br><small>Area in Sq Ft / Sq Yd / Sq Mtr / Acre · write amounts as 8 lakh, 2.5 cr, 100 per sqft · paste/upload photos</small></header><div class=w><form id=f class=box><input type=hidden name=division value="{q(division)}"><div class=grid>
<label><small>Property Name</small><input name=property_name value="{q(p.get('property_name'))}"></label><label><small>City</small><input name=city value="{q(p.get('city'))}"></label><label><small>Location *</small><input required name=location value="{q(p.get('location'))}"></label><label><small>Google Location</small><input name=google_location value="{q(p.get('google_location'))}"></label><label><small>Area Value *</small><input required name=area_value value="{q(av)}" placeholder="500"></label><label><small>Area Unit *</small><select name=area_unit>{area_options(au)}</select></label><label><small>Transaction *</small><select name=transaction_type id=tx>{tx_options(tx)}</select></label><label id=saleBox><small>Sale Amount</small><input name=sale_amount_text value="{q(sale)}" placeholder="8 lakh / 2.5 cr / 9500000"></label><label id=rentBox><small>Rent Amount</small><input name=rent_amount_text value="{q(rent)}" placeholder="8 lakh / 100 per sqft / 500000"></label><label><small>Rent Basis</small><select name=rent_basis><option value=PER_MONTH>Per Month</option><option value=PER_SQFT>Per Sq Ft</option><option value=PER_SQYD>Per Sq Yd</option><option value=PER_SQMTR>Per Sq Mtr</option></select></label><label><small>Floor</small><input name=floor value="{q(p.get('floor'))}"></label><label><small>Frontage</small><input name=frontage value="{q(p.get('frontage'))}"></label><label><small>Parking</small><input name=parking value="{q(p.get('parking'))}"></label><label><small>Possession</small><input name=possession value="{q(p.get('possession'))}"></label><label><small>Suitable For</small><input name=suitable_for value="{q(p.get('suitable_for'))}"></label><label><small>Nearby Brands</small><input name=nearby_brands value="{q(p.get('nearby_brands'))}"></label><label><small>Owner / Broker Name</small><input name=owner_broker_name value="{q(p.get('owner_broker_name'))}"></label><label><small>Contact Number</small><input name=contact_number value="{q(p.get('contact_number'))}"></label><label><small>Contact Role</small><select name=contact_role><option>OWNER</option><option>BROKER</option><option>BUILDER</option><option>UNVERIFIED</option></select></label><label><small>Verification</small><select name=verification_status><option {"selected" if str(p.get('verification_status') or '').upper()=='UNVERIFIED' else ''}>UNVERIFIED</option><option {"selected" if str(p.get('verification_status') or '').upper()=='VERIFIED' else ''}>VERIFIED</option></select></label></div><h3>Property Type *</h3><div class=types>{type_checks(pts)}</div><h3>Remarks</h3><textarea name=remarks>{q(p.get('remarks'))}</textarea><h3>Media</h3><p class=hint>Photos: paste with Ctrl+V, drag & drop, or click Upload.</p><div class=grid><div><div class=drop id=dzImages tabindex=0>Paste / Drop / Upload Photos<input id=images type=file name=images accept="image/*" multiple><div id=prevImages></div></div></div><div><div class=drop id=dzVideos tabindex=0>Drop / Upload Videos<input id=videos type=file name=videos accept="video/*" multiple><div id=prevVideos></div></div></div><div><div class=drop id=dzBrochure tabindex=0>Drop / Upload PDF<input id=brochure type=file name=brochure accept=".pdf,application/pdf"><div id=prevBrochure></div></div></div></div><p><button>{'Save Changes' if edit else 'Save Property'}</button> <a class="btn gray" href="/manual-property-database-v178">Database</a> <b id=msg></b></p></form></div><script>{DROP_JS}wireDrop('dzImages','images','prevImages',true);wireDrop('dzVideos','videos','prevVideos',false);wireDrop('dzBrochure','brochure','prevBrochure',false);function txUI(){{let v=tx.value;rentBox.style.display=(v==='LEASE'||v==='BOTH')?'':'none';saleBox.style.display=(v==='SALE'||v==='BOTH')?'':'none'}}tx.onchange=txUI;txUI();f.onsubmit=async e=>{{e.preventDefault();msg.textContent='Saving...';try{{let r=await fetch('{endpoint}',{{method:'POST',body:new FormData(f)}});let d=await r.json();if(!r.ok)throw new Error(d.detail||'Save failed');if(d.duplicate){{msg.className='err';msg.textContent='Possible duplicate: '+d.property_code;return}}location.href='/property-detail-final/'+encodeURIComponent(d.property_code)}}catch(x){{msg.className='err';msg.textContent='ERROR: '+x.message}}}}</script></body></html>'''

def register(wrapped):
    core=wrapped.core;app=wrapped.app;setup(core)
    removed=remove_routes(app,{('/manual-property-v18','GET'),('/manual-property-database-v178','GET'),('/edit-property/{property_code}','GET'),('/requirements-center-v176','GET'),('/manual-requirement-final','GET'),('/final-dashboard-v12','GET')})

    @app.get('/manual-property-v18',response_class=HTMLResponse)
    def add_property(req:Request,division:str=Query('DELHI_NCR')):
        r=auth(core,req);return r or HTMLResponse(property_form(division.upper()))

    async def _save_prop_common(req,code,division,property_name,property_types,city,location,google_location,area_value,area_unit,transaction_type,sale_amount_text,rent_amount_text,rent_basis,floor,frontage,parking,possession,suitable_for,nearby_brands,owner_broker_name,contact_number,contact_role,verification_status,remarks,images,videos,brochure,editing):
        core.need_login(req);setup(core)
        if not location.strip():raise HTTPException(400,'Location is required.')
        if not property_types:raise HTTPException(400,'Select at least one Property Type.')
        av,au,asqft=parse_area(area_value,area_unit);tx=transaction_type.upper();sale,sraw=parse_money(sale_amount_text);rent,rraw=parse_money(rent_amount_text)
        if tx=='SALE' and sale is None:raise HTTPException(400,'Sale Amount is required for SALE.')
        if tx=='LEASE' and rent is None:raise HTTPException(400,'Rent Amount is required for LEASE.')
        if tx=='BOTH' and (sale is None or rent is None):raise HTTPException(400,'Both Sale and Rent amounts are required.')
        with core.engine.begin() as c:
            if not editing:
                dup=c.execute(text("SELECT property_code FROM pi_operational_properties WHERE lower(trim(coalesce(city,'')))=lower(trim(:city)) AND lower(trim(coalesce(location,'')))=lower(trim(:loc)) AND abs(coalesce(area_sqft,0)-:area)<=GREATEST(25,:area*0.01) AND upper(coalesce(transaction_type,''))=:tx ORDER BY created_at DESC LIMIT 1"),{'city':city,'loc':location,'area':asqft,'tx':tx}).first()
                if dup:return {'status':'duplicate','duplicate':True,'property_code':dup[0]}
                code=f"PROP-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{abs(hash((city,location,str(asqft),tx)))%0xFFFFFF:06X}"
                c.execute(text("""INSERT INTO pi_operational_properties(property_code,division,property_name,property_types,city,location,google_location,area_sqft,area_value,area_unit,rent_amount,sale_amount,rent_input_text,sale_input_text,rent_unit,transaction_type,floor,frontage,parking,possession,suitable_for,nearby_brands,owner_broker_name,contact_number,contact_role,verification_status,remarks,created_by,created_at,updated_at) VALUES(:code,:division,:pn,CAST(:pts AS jsonb),:city,:loc,:google,:area,:av,:au,:rent,:sale,:rraw,:sraw,:basis,:tx,:floor,:front,:park,:poss,:suitable,:nearby,:person,:phone,:role,:ver,:remarks,:who,NOW(),NOW())"""),{'code':code,'division':division.upper(),'pn':property_name,'pts':json.dumps(property_types),'city':city,'loc':location,'google':google_location,'area':asqft,'av':av,'au':au,'rent':rent,'sale':sale,'rraw':rraw,'sraw':sraw,'basis':rent_basis,'tx':tx,'floor':floor,'front':frontage,'park':parking,'poss':possession,'suitable':suitable_for,'nearby':nearby_brands,'person':owner_broker_name,'phone':contact_number,'role':contact_role,'ver':verification_status.upper(),'remarks':remarks,'who':actor(core,req)})
            else:
                c.execute(text("""UPDATE pi_operational_properties SET division=:division,property_name=:pn,property_types=CAST(:pts AS jsonb),city=:city,location=:loc,google_location=:google,area_sqft=:area,area_value=:av,area_unit=:au,rent_amount=:rent,sale_amount=:sale,rent_input_text=:rraw,sale_input_text=:sraw,rent_unit=:basis,transaction_type=:tx,floor=:floor,frontage=:front,parking=:park,possession=:poss,suitable_for=:suitable,nearby_brands=:nearby,owner_broker_name=:person,contact_number=:phone,contact_role=:role,verification_status=:ver,remarks=:remarks,updated_at=NOW() WHERE property_code=:code"""),{'code':code,'division':division.upper(),'pn':property_name,'pts':json.dumps(property_types),'city':city,'loc':location,'google':google_location,'area':asqft,'av':av,'au':au,'rent':rent,'sale':sale,'rraw':rraw,'sraw':sraw,'basis':rent_basis,'tx':tx,'floor':floor,'front':frontage,'park':parking,'poss':possession,'suitable':suitable_for,'nearby':nearby_brands,'person':owner_broker_name,'phone':contact_number,'role':contact_role,'ver':verification_status.upper(),'remarks':remarks})
        im,ie=await save_media(core,code,images,'IMAGE',12);vi,ve=await save_media(core,code,videos,'VIDEO',100);br,be=await save_media(core,code,[brochure] if brochure else [],'BROCHURE',40)
        return {'status':'ok','property_code':code,'images_saved':im,'videos_saved':vi,'brochures_saved':br,'media_errors':ie+ve+be}

    @app.post('/api/v18-2/property')
    async def save_property(req:Request,division:str=Form('DELHI_NCR'),property_name:str=Form(''),property_types:list[str]=Form([]),city:str=Form(''),location:str=Form(''),google_location:str=Form(''),area_value:str=Form(''),area_unit:str=Form('SQFT'),transaction_type:str=Form('LEASE'),sale_amount_text:str=Form(''),rent_amount_text:str=Form(''),rent_basis:str=Form('PER_MONTH'),floor:str=Form(''),frontage:str=Form(''),parking:str=Form(''),possession:str=Form(''),suitable_for:str=Form(''),nearby_brands:str=Form(''),owner_broker_name:str=Form(''),contact_number:str=Form(''),contact_role:str=Form('UNVERIFIED'),verification_status:str=Form('UNVERIFIED'),remarks:str=Form(''),images:list[UploadFile]=File([]),videos:list[UploadFile]=File([]),brochure:UploadFile|None=File(None)):
        return await _save_prop_common(req,None,division,property_name,property_types,city,location,google_location,area_value,area_unit,transaction_type,sale_amount_text,rent_amount_text,rent_basis,floor,frontage,parking,possession,suitable_for,nearby_brands,owner_broker_name,contact_number,contact_role,verification_status,remarks,images,videos,brochure,False)

    @app.get('/edit-property/{property_code}',response_class=HTMLResponse)
    def edit_property(property_code:str,req:Request):
        r=auth(core,req)
        if r:return r
        with core.engine.connect() as c:row=c.execute(text('SELECT * FROM pi_operational_properties WHERE property_code=:p'),{'p':property_code}).first()
        if not row:raise HTTPException(404,'Property not found.')
        p=dict(row._mapping);return HTMLResponse(property_form(p.get('division') or 'DELHI_NCR',p,True))

    @app.post('/api/v18-2/property/{property_code}/edit')
    async def save_edit(property_code:str,req:Request,division:str=Form('DELHI_NCR'),property_name:str=Form(''),property_types:list[str]=Form([]),city:str=Form(''),location:str=Form(''),google_location:str=Form(''),area_value:str=Form(''),area_unit:str=Form('SQFT'),transaction_type:str=Form('LEASE'),sale_amount_text:str=Form(''),rent_amount_text:str=Form(''),rent_basis:str=Form('PER_MONTH'),floor:str=Form(''),frontage:str=Form(''),parking:str=Form(''),possession:str=Form(''),suitable_for:str=Form(''),nearby_brands:str=Form(''),owner_broker_name:str=Form(''),contact_number:str=Form(''),contact_role:str=Form('UNVERIFIED'),verification_status:str=Form('UNVERIFIED'),remarks:str=Form(''),images:list[UploadFile]=File([]),videos:list[UploadFile]=File([]),brochure:UploadFile|None=File(None)):
        return await _save_prop_common(req,property_code,division,property_name,property_types,city,location,google_location,area_value,area_unit,transaction_type,sale_amount_text,rent_amount_text,rent_basis,floor,frontage,parking,possession,suitable_for,nearby_brands,owner_broker_name,contact_number,contact_role,verification_status,remarks,images,videos,brochure,True)

    @app.get('/manual-property-database-v178',response_class=HTMLResponse)
    def manual_db(req:Request,division:str=Query('ALL')):
        r=auth(core,req)
        if r:return r
        d=division.upper();params={} if d=='ALL' else {'d':d};where='' if d=='ALL' else 'WHERE division=:d'
        with core.engine.connect() as c:rows=c.execute(text(f"""SELECT p.*,(SELECT count(*) FROM pi_operational_property_media m WHERE m.property_code=p.property_code AND m.media_type='IMAGE') image_count,(SELECT count(*) FROM pi_operational_property_media m WHERE m.property_code=p.property_code AND m.media_type='VIDEO') video_count,(SELECT count(*) FROM pi_operational_property_media m WHERE m.property_code=p.property_code AND m.media_type='BROCHURE') brochure_count FROM pi_operational_properties p {where} ORDER BY created_at DESC LIMIT 5000"""),params).fetchall()
        data=[dict(x._mapping) for x in rows];total=len(data);verified=sum(str(x.get('verification_status') or '').upper()=='VERIFIED' for x in data);today=datetime.now().date().isoformat();added=sum(str(x.get('created_at') or '')[:10]==today for x in data);photos=sum(int(x.get('image_count') or 0) for x in data);videos=sum(int(x.get('video_count') or 0) for x in data);brochures=sum(int(x.get('brochure_count') or 0) for x in data);trs=[]
        for i,x in enumerate(data,1):
            av=x.get('area_value') if x.get('area_value') is not None else x.get('area_sqft');au=AREA_LABELS.get(str(x.get('area_unit') or 'SQFT').upper(),'Sq Ft');tx=str(x.get('transaction_type') or '').upper();pts=x.get('property_types') or []
            if isinstance(pts,str):
                try:pts=json.loads(pts)
                except Exception:pts=[pts]
            cls='verified' if str(x.get('verification_status') or '').upper()=='VERIFIED' else 'unverified'
            trs.append(f'''<tr><td>{i}</td><td><b>{q(x.get('property_name') or x.get('property_code'))}</b><br><small>{q(x.get('property_code'))}</small></td><td>MANUAL</td><td>{q(str(x.get('created_at') or '')[:16])}</td><td>{q(x.get('created_by'))}</td><td><span class="pill {cls}">{q(x.get('verification_status'))}</span></td><td>{q(', '.join(pts))}</td><td>{q(x.get('city'))}</td><td><b>{q(x.get('location'))}</b></td><td>{q(av)}</td><td>{q(au)}</td><td><b>{q('SALE + LEASE' if tx=='BOTH' else tx)}</b></td><td>{money(x.get('sale_amount')) if tx in {'SALE','BOTH'} else '—'}<br><small>{q(x.get('sale_input_text'))}</small></td><td>{money(x.get('rent_amount')) if tx in {'LEASE','BOTH'} else '—'}<br><small>{q(x.get('rent_input_text'))} {q(x.get('rent_unit'))}</small></td><td>{q(x.get('owner_broker_name'))}<br><b>{q(x.get('contact_number'))}</b></td><td>Photos {x.get('image_count') or 0} · Videos {x.get('video_count') or 0} · Brochure {x.get('brochure_count') or 0}</td><td><a class=btn href="/property-detail-final/{q(x.get('property_code'))}">View</a> <a class="btn green" href="/edit-property/{q(x.get('property_code'))}">Edit</a></td></tr>''')
        return HTMLResponse(f'''<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Manual Property Database</title><style>{CSS}</style></head><body><header><b>Manual Property Database</b><br><small>Original area unit preserved · separate Sale Amount / Rent Amount · Pre-Rented Property · paste/upload photos</small></header><div class=w><div class=toolbar><a class="btn gray" href="/final-dashboard-v12">← Dashboard</a><a class=btn href="/manual-property-v18?division=DELHI_NCR">Add Delhi NCR Property</a><a class=btn href="/manual-property-v18?division=GOA">Add Goa Property</a></div><div class=kpis><div class=kpi><b>{total}</b>Total</div><div class=kpi><b>{added}</b>Added Today</div><div class=kpi><b>{verified}</b>Verified</div><div class=kpi><b>{total-verified}</b>Unverified</div><div class=kpi><b>{photos}</b>Photos</div><div class=kpi><b>{videos}</b>Videos</div><div class=kpi><b>{brochures}</b>Brochures</div></div><div class=tablewrap><table><thead><tr><th>S.No.</th><th>Property / Code</th><th>Entry Source</th><th>Entry Date</th><th>Entered By</th><th>Verification</th><th>Property Type</th><th>City</th><th>Location</th><th>Area</th><th>Area Unit</th><th>Transaction</th><th>Sale Amount</th><th>Rent Amount</th><th>Contact</th><th>Media</th><th>Action</th></tr></thead><tbody>{''.join(trs) or '<tr><td colspan=17>No properties found.</td></tr>'}</tbody></table></div></div></body></html>''')

    @app.get('/manual-requirement-final',response_class=HTMLResponse)
    def requirement_form(req:Request,division:str=Query('DELHI_NCR')):
        r=auth(core,req)
        if r:return r
        return HTMLResponse(f'''<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Add Requirement</title><style>{CSS}</style></head><body><header><b>Add Requirement</b><br><small>Entry date automatic · same area units and Lakh/Crore rent/budget format as property database</small></header><div class=w><form id=rf class=box><input type=hidden name=division value="{q(division.upper())}"><div class=grid><label><small>Client Name</small><input name=client_name></label><label><small>Company / Brand</small><input name=company_name></label><label><small>Contact Number</small><input name=contact_number></label><label><small>City</small><input name=city></label><label><small>Preferred Locations *</small><input required name=preferred_locations></label><label><small>Minimum Area *</small><input required name=minimum_area_value placeholder=500></label><label><small>Maximum Area *</small><input required name=maximum_area_value placeholder=1000></label><label><small>Area Unit *</small><select name=area_unit>{area_options()}</select></label><label><small>Transaction *</small><select name=transaction_type id=rtx>{tx_options()}</select></label><label id=rrent><small>Maximum Rent / Budget</small><input name=rent_amount_text placeholder="8 lakh / 100 per sqft"></label><label><small>Rent Basis</small><select name=rent_basis><option value=PER_MONTH>Per Month</option><option value=PER_SQFT>Per Sq Ft</option><option value=PER_SQYD>Per Sq Yd</option><option value=PER_SQMTR>Per Sq Mtr</option></select></label><label id=rsale><small>Maximum Sale Budget</small><input name=sale_amount_text placeholder="2.5 cr / 80 lakh"></label><label><small>Verification</small><select name=verification_status><option>VERIFIED</option><option>UNVERIFIED</option></select></label></div><h3>Property Types</h3><div class=types>{type_checks()}</div><h3>Additional Points</h3><textarea name=additional_points></textarea><p><button>Save Requirement</button> <a class="btn gray" href="/requirements-center-v176?division={q(division.upper())}">Requirement Database</a> <b id=msg></b></p></form></div><script>function txUI(){{let v=rtx.value;rrent.style.display=(v==='LEASE'||v==='BOTH')?'':'none';rsale.style.display=(v==='SALE'||v==='BOTH')?'':'none'}}rtx.onchange=txUI;txUI();rf.onsubmit=async e=>{{e.preventDefault();msg.textContent='Saving...';try{{let r=await fetch('/api/v18-2/requirement',{{method:'POST',body:new FormData(rf)}});let d=await r.json();if(!r.ok)throw new Error(d.detail||'Save failed');location.href='/requirements-center-v176?division={q(division.upper())}'}}catch(x){{msg.className='err';msg.textContent='ERROR: '+x.message}}}}</script></body></html>''')

    @app.post('/api/v18-2/requirement')
    async def save_requirement(req:Request,division:str=Form('DELHI_NCR'),client_name:str=Form(''),company_name:str=Form(''),contact_number:str=Form(''),property_types:list[str]=Form([]),city:str=Form(''),preferred_locations:str=Form(''),minimum_area_value:str=Form(''),maximum_area_value:str=Form(''),area_unit:str=Form('SQFT'),transaction_type:str=Form('LEASE'),rent_amount_text:str=Form(''),rent_basis:str=Form('PER_MONTH'),sale_amount_text:str=Form(''),additional_points:str=Form(''),verification_status:str=Form('VERIFIED')):
        core.need_login(req);setup(core);mn,au,mns=parse_area(minimum_area_value,area_unit);mx,_,mxs=parse_area(maximum_area_value,area_unit)
        if mxs<mns:raise HTTPException(400,'Maximum Area cannot be smaller than Minimum Area.')
        rent,rraw=parse_money(rent_amount_text);sale,sraw=parse_money(sale_amount_text);tx=transaction_type.upper()
        code=f"REQ-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        with core.engine.begin() as c:c.execute(text("""INSERT INTO pi_operational_requirements(requirement_code,division,client_name,company_name,contact_number,requirement_types,city,preferred_locations,minimum_area_sqft,maximum_area_sqft,minimum_area_value,maximum_area_value,area_unit,maximum_rent,rent_input_text,rent_basis,sale_budget,sale_input_text,transaction_type,additional_points,verification_status,created_by,created_at,entry_date,updated_at) VALUES(:code,:division,:client,:company,:phone,CAST(:types AS jsonb),:city,:loc,:mins,:maxs,:minv,:maxv,:au,:rent,:rraw,:basis,:sale,:sraw,:tx,:points,:ver,:who,NOW(),NOW(),NOW())"""),{'code':code,'division':division.upper(),'client':client_name,'company':company_name,'phone':contact_number,'types':json.dumps(property_types),'city':city,'loc':preferred_locations,'mins':mns,'maxs':mxs,'minv':mn,'maxv':mx,'au':au,'rent':rent,'rraw':rraw,'basis':rent_basis,'sale':sale,'sraw':sraw,'tx':tx,'points':additional_points,'ver':verification_status.upper(),'who':actor(core,req)})
        return {'status':'ok','requirement_code':code}

    @app.get('/requirements-center-v176',response_class=HTMLResponse)
    def requirement_db(req:Request,division:str=Query('DELHI_NCR')):
        r=auth(core,req)
        if r:return r
        d=division.upper();setup(core)
        with core.engine.connect() as c:rows=c.execute(text("SELECT * FROM pi_operational_requirements WHERE (:d='ALL' OR division=:d) ORDER BY COALESCE(entry_date,created_at) DESC LIMIT 5000"),{'d':d}).fetchall()
        trs=[]
        for i,row in enumerate(rows,1):
            x=dict(row._mapping);au=AREA_LABELS.get(str(x.get('area_unit') or 'SQFT').upper(),'Sq Ft');tx=str(x.get('transaction_type') or 'LEASE').upper();pts=x.get('requirement_types') or []
            if isinstance(pts,str):
                try:pts=json.loads(pts)
                except Exception:pts=[pts]
            cls='verified' if str(x.get('verification_status') or '').upper()=='VERIFIED' else 'unverified';trs.append(f'''<tr><td>{i}</td><td><b>{q(x.get('requirement_code'))}</b></td><td>{q(str(x.get('entry_date') or x.get('created_at') or '')[:16])}</td><td>{q(x.get('client_name'))}<br><small>{q(x.get('company_name'))}</small></td><td>{q(x.get('contact_number'))}</td><td>{q(', '.join(pts))}</td><td>{q(x.get('city'))}</td><td>{q(x.get('preferred_locations'))}</td><td>{q(x.get('minimum_area_value') or x.get('minimum_area_sqft'))} - {q(x.get('maximum_area_value') or x.get('maximum_area_sqft'))}</td><td>{q(au)}</td><td>{q('SALE + LEASE' if tx=='BOTH' else tx)}</td><td>{money(x.get('sale_budget'))}<br><small>{q(x.get('sale_input_text'))}</small></td><td>{money(x.get('maximum_rent'))}<br><small>{q(x.get('rent_input_text'))} {q(x.get('rent_basis'))}</small></td><td><span class="pill {cls}">{q(x.get('verification_status'))}</span></td></tr>''')
        return HTMLResponse(f'''<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Requirement Database</title><style>{CSS}</style></head><body><header><b>Requirement Database</b><br><small>Entry Date · same area units · separate Sale Budget / Rent Budget</small></header><div class=w><div class=toolbar><a class="btn gray" href="/final-dashboard-v12">← Dashboard</a><a class=btn href="/manual-requirement-final?division={q(d)}">Add Requirement</a></div><div class=tablewrap><table><thead><tr><th>S.No.</th><th>Requirement Code</th><th>Entry Date</th><th>Client / Brand</th><th>Contact</th><th>Property Type</th><th>City</th><th>Preferred Locations</th><th>Area</th><th>Area Unit</th><th>Transaction</th><th>Sale Budget</th><th>Rent Budget</th><th>Verification</th></tr></thead><tbody>{''.join(trs) or '<tr><td colspan=14>No requirements found.</td></tr>'}</tbody></table></div></div></body></html>''')

    @app.get('/magazine-property-database-v182',response_class=HTMLResponse)
    def magazine_db(req:Request):
        r=auth(core,req)
        if r:return r
        with core.engine.connect() as c:rows=c.execute(text("""SELECT p.*,s.source_type,s.source_name,s.original_filename,s.uploaded_at FROM pi_properties p LEFT JOIN pi_sources s ON s.id=p.source_id WHERE upper(coalesce(p.source,'')) LIKE '%MAGAZINE%' OR upper(coalesce(s.source_type,'')) LIKE '%MAGAZINE%' OR upper(coalesce(s.source_type,'')) LIKE '%PRINT%' ORDER BY p.created_at DESC LIMIT 5000""")).fetchall()
        trs=[]
        for i,row in enumerate(rows,1):
            x=dict(row._mapping);av=x.get('area_value') or x.get('available_area_sqft');au=AREA_LABELS.get(str(x.get('area_unit') or 'SQFT').upper(),'Sq Ft');tx=str(x.get('rent_or_sale') or '').upper();sale=x.get('sale_amount') or x.get('asking_sale_price');rent=x.get('monthly_rent') or x.get('asking_rent_per_sqft');trs.append(f'''<tr><td>{i}</td><td><b>{q(x.get('property_name') or x.get('property_id'))}</b><br><small>{q(x.get('property_id'))}</small></td><td>{q(str(x.get('created_at') or x.get('uploaded_at') or '')[:16])}</td><td>{q(x.get('original_filename') or x.get('source_name') or x.get('source'))}</td><td>{q(x.get('city'))}</td><td><b>{q(x.get('location'))}</b></td><td>{q(x.get('property_type'))}</td><td>{q(av)}</td><td>{q(au)}</td><td>{q(tx)}</td><td>{money(sale)}</td><td>{money(rent)}</td><td>{q(x.get('broker_name'))}<br><b>{q(x.get('broker_contact'))}</b></td><td>{q(x.get('verification_status'))}</td><td>{q(x.get('remarks'))}</td></tr>''')
        return HTMLResponse(f'''<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Magazine Property Database</title><style>{CSS}</style></head><body><header><b>Magazine Property Database</b><br><small>Same display language as manual inventory. Magazine source evidence remains preserved.</small></header><div class=w><div class=toolbar><a class="btn gray" href="/final-dashboard-v12">← Dashboard</a><a class=btn href="/capture-intelligence">Upload / Process Magazine</a></div><div class=tablewrap><table><thead><tr><th>S.No.</th><th>Property / ID</th><th>Entry Date</th><th>Magazine / Source</th><th>City</th><th>Location</th><th>Property Type</th><th>Area</th><th>Area Unit</th><th>Transaction</th><th>Sale Amount</th><th>Rent Amount</th><th>Broker / Contact</th><th>Verification</th><th>Source / Remarks</th></tr></thead><tbody>{''.join(trs) or '<tr><td colspan=15>No magazine properties found yet.</td></tr>'}</tbody></table></div></div></body></html>''')

    @app.get('/final-dashboard-v12',response_class=HTMLResponse)
    def dashboard(req:Request):
        r=auth(core,req)
        if r:return r
        return HTMLResponse(f'''<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Alliance Data Dashboard</title><style>{CSS}</style></head><body><header><b>Alliance AI Deal Intelligence OS</b><br><small>Unified Property · Requirement · Magazine data organizer</small></header><div class=w><h2>Core Databases</h2><div class=cards><a class=navcard href="/manual-property-database-v178"><b>Manual Property Database</b><p>Area units, Sale/Rent, Pre-Rented Property, media and edit.</p></a><a class=navcard href="/requirements-center-v176?division=DELHI_NCR"><b>Requirement Database</b><p>Entry date, same area units, natural rent/budget formats.</p></a><a class=navcard href="/magazine-property-database-v182"><b>Magazine Property Database</b><p>Formatted magazine inventory with source evidence.</p></a></div><h2>Entry & Matching</h2><div class=cards><a class=navcard href="/manual-property-v18?division=DELHI_NCR"><b>Add Delhi NCR Property</b></a><a class=navcard href="/manual-property-v18?division=GOA"><b>Add Goa Property</b></a><a class=navcard href="/manual-requirement-final?division=DELHI_NCR"><b>Add Requirement</b></a><a class=navcard href="/matcher-final?division=DELHI_NCR"><b>Property Matcher</b></a><a class=navcard href="/capture-intelligence"><b>Capture / Magazine Upload</b></a></div><h2>Intelligence & Operations</h2><div class=cards><a class=navcard href="/whatsapp-live"><b>WhatsApp Group Dashboard</b></a><a class=navcard href="/property-discovery"><b>Property Discovery</b></a><a class=navcard href="/retail-expansion"><b>Retail Expansion</b></a><a class=navcard href="/ai-hospitality-master-final"><b>Hospitality Master</b></a><a class=navcard href="/marketing-contacts-final"><b>Marketing Contacts</b></a><a class=navcard href="/universal-recovery-doctor"><b>Recovery Doctor</b></a></div></div></body></html>''')

    @app.middleware('http')
    async def v182_router(request,call_next):
        p=request.url.path
        if request.method=='GET' and p in {'/manual-property-final','/property-form-final','/operational-property-form','/property-manual','/manual-property-v179','/manual-property-final-exec','/manual-property-v18-final'}:
            return RedirectResponse('/manual-property-v18'+(('?'+request.url.query) if request.url.query else ''),307)
        if request.method=='GET' and p in {'/manual-property-database','/manual-property-database-v179'}:
            return RedirectResponse('/manual-property-database-v178'+(('?'+request.url.query) if request.url.query else ''),307)
        if request.method=='GET' and p in {'/requirements-match-center','/requirements-center-secure'}:
            return RedirectResponse('/requirements-center-v176'+(('?'+request.url.query) if request.url.query else ''),307)
        resp=await call_next(request)
        if p.startswith(('/manual-property-v18','/manual-property-database-v178','/requirements-center-v176','/manual-requirement-final','/magazine-property-database-v182','/api/v18-2','/final-dashboard-v12')):resp.headers['Cache-Control']='no-store, no-cache, must-revalidate, max-age=0'
        return resp

    return {'status':'REGISTERED','version':VERSION,'removed_routes':removed}
