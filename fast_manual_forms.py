import re, uuid, json
from datetime import datetime, timezone
from typing import Optional
from fastapi import Request, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import text

V19_VERSION='19.0-FAST-ENTRY-EDIT-DELETE'
PROPERTY_TYPES=['Retail Shop','High Street Retail','Mall Retail','Office','Restaurant','Cafe','Banquet / Wedding Venue','Hotel','Guest House','Lounge','Club','Bar','Farmhouse','Warehouse','Industrial','Land','Mixed Use','Residential / Villa']

class FastProperty(BaseModel):
    property_name:Optional[str]=None
    property_types:list[str]
    city:Optional[str]=None
    location:str
    google_location:Optional[str]=None
    area_text:str
    rent_text:str
    transaction_type:str='LEASE'
    floor:Optional[str]=None
    frontage:Optional[str]=None
    parking:Optional[str]=None
    possession:Optional[str]=None
    suitable_for:Optional[str]=None
    nearby_brands:Optional[str]=None
    owner_broker_name:Optional[str]=None
    contact_number:Optional[str]=None
    contact_role:str='UNVERIFIED'
    verification_status:str='UNVERIFIED'
    remarks:Optional[str]=None

class FastRequirement(BaseModel):
    client_name:Optional[str]=None
    company_name:Optional[str]=None
    contact_number:Optional[str]=None
    requirement_types:list[str]
    city:Optional[str]=None
    preferred_locations:str
    minimum_area_text:str
    maximum_area_text:str
    maximum_rent_text:str
    transaction_type:str='LEASE'
    additional_points:Optional[str]=None
    verification_status:str='VERIFIED'

def parse_area(v):
    s=str(v or '').lower().replace(',',' ')
    m=re.search(r'(\d+(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|square\s*feet)',s)
    if m:return float(m.group(1))
    m=re.search(r'(\d+(?:\.\d+)?)\s*(?:sq\.?\s*m|sqm|sqmt|square\s*met(?:er|re)s?)',s)
    if m:return round(float(m.group(1))*10.7639104167,2)
    m=re.search(r'(\d+(?:\.\d+)?)',s)
    return float(m.group(1)) if m else None

def parse_money(v):
    s=str(v or '').lower().replace(',','')
    m=re.search(r'(\d+(?:\.\d+)?)\s*(crore|cr|lakhs?|lacs?|lac|lakh|k|thousand)?',s)
    if not m:return None
    n=float(m.group(1));u=(m.group(2) or '').lower()
    if u in {'crore','cr'}:n*=10000000
    elif u in {'lakh','lakhs','lac','lacs'}:n*=100000
    elif u in {'k','thousand'}:n*=1000
    return round(n,2)

def code(prefix):
    return prefix+'-'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')+'-'+uuid.uuid4().hex[:6].upper()

def _property_page(d):
    checks=''.join(f"<label><input type=checkbox name=ptype value='{x}'> {x}</label>" for x in PROPERTY_TYPES)
    return f'''<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Fast Property Entry</title>
<style>body{{font-family:Arial;background:#f4f7fb;margin:0;color:#172437}}header{{background:#102235;color:#fff;padding:18px}}.w{{max-width:1250px;margin:auto;padding:18px}}.card{{background:#fff;padding:15px;border-radius:12px;margin-bottom:12px}}.g{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}input,select,textarea{{width:100%;padding:9px;border:1px solid #ccd6e2;border-radius:7px;box-sizing:border-box}}.checks{{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}}.checks input{{width:auto}}.btn{{padding:9px 12px;background:#1677ff;color:#fff;border:0;border-radius:8px;cursor:pointer}}.red{{background:#b42318}}.gray{{background:#e9eef5;color:#203247}}table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{padding:8px;border-bottom:1px solid #eee;text-align:left}}.hidden{{display:none}}.msg{{margin-top:10px;background:#fff8e8;padding:9px}}@media(max-width:800px){{.g,.checks{{grid-template-columns:1fr}}}}</style></head>
<body><header><b>{'Goa' if d=='GOA' else 'Delhi NCR'} Fast Property Entry V19</b><br><small>Fast save · Edit/Delete · Images after save</small></header><div class=w>
<div class=card><form id=f><input id=editcode type=hidden><div class=g>
<div><b>Property Name</b><input name=property_name></div><div><b>City</b><input name=city></div>
<div style="grid-column:1/-1"><b>Property Types *</b><div class=checks>{checks}</div></div>
<div><b>Location *</b><input name=location required></div><div><b>Google Location</b><input name=google_location></div>
<div><b>Area Details *</b><input name=area_text placeholder="5000 sqft OR 500 sqmt" required></div><div><b>Rent Details *</b><input name=rent_text placeholder="5 lakhs" required></div>
<div><b>Transaction</b><select name=transaction_type><option>LEASE</option><option>SALE</option></select></div><div><b>Floor</b><input name=floor></div>
<div><b>Frontage</b><input name=frontage></div><div><b>Parking</b><input name=parking></div><div><b>Possession</b><input name=possession></div><div><b>Suitable For</b><input name=suitable_for></div>
<div><b>Nearby Brands</b><input name=nearby_brands></div><div><b>Owner/Broker Name</b><input name=owner_broker_name></div><div><b>Contact Number</b><input name=contact_number></div>
<div><b>Contact Role</b><select name=contact_role><option>UNVERIFIED</option><option>OWNER</option><option>BROKER</option></select></div>
<div><b>Verification</b><select name=verification_status><option>UNVERIFIED</option><option>VERIFIED</option></select></div>
<div style="grid-column:1/-1"><b>Remarks</b><textarea name=remarks></textarea></div></div><br>
<button id=save class=btn>Save Property</button> <button id=cancel type=button class="btn gray hidden" onclick=resetForm()>Cancel Edit</button><div id=msg class=msg>Ready.</div></form></div>
<div id=media class="card hidden"><b>Upload Images After Save</b><p id=mediaPid></p><input id=imgs type=file accept="image/*" multiple><br><br><button class=btn onclick=uploadMedia()>Upload Images</button> <button class="btn gray" onclick='media.classList.add("hidden")'>Skip / Done</button><div id=mmsg class=msg>Property is already saved.</div></div>
<div class=card><h3>Recent Manual Properties</h3><table><thead><tr><th>Code</th><th>Property</th><th>Location</th><th>Area</th><th>Rent</th><th>Actions</th></tr></thead><tbody id=rows></tbody></table></div></div>
<script>
const DIV='{d}';let currentMedia=null,submitting=false;
function bodyFromForm(){{let fd=new FormData(f),b={{property_types:[...document.querySelectorAll('[name=ptype]:checked')].map(x=>x.value)}};for(let [k,v] of fd.entries())if(k!=='ptype')b[k]=String(v).trim()||null;return b}}
function resetForm(){{f.reset();editcode.value='';cancel.classList.add('hidden');save.textContent='Save Property';msg.textContent='Ready for next property.'}}
f.onsubmit=async e=>{{e.preventDefault();if(submitting)return;let b=bodyFromForm();if(!b.property_types.length||!b.location||!b.area_text||!b.rent_text){{msg.textContent='Property Type, Location, Area and Rent are mandatory.';return}}submitting=true;save.disabled=true;let ec=editcode.value,t=performance.now();try{{let r=await fetch(ec?'/api/v19/property/'+encodeURIComponent(ec):'/api/v19/property?division='+DIV,{{method:ec?'PUT':'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(b)}}),x=await r.json();if(!r.ok)throw Error(x.detail||'Save failed');let ms=Math.round(performance.now()-t),pid=x.property_code||ec;resetForm();msg.textContent=(ec?'Updated ':'Saved ')+pid+' in '+ms+' ms. Form cleared.';if(!ec){{currentMedia=pid;mediaPid.textContent='Property ID: '+pid;media.classList.remove('hidden');imgs.value=''}}await load()}}catch(err){{msg.textContent='ERROR: '+err.message}}finally{{submitting=false;save.disabled=false}}}};
async function load(){{let d=await(await fetch('/api/v19/properties?division='+DIV)).json();rows.innerHTML=(d.rows||[]).map(x=>`<tr><td>${{x.property_code}}</td><td>${{x.property_name||''}}</td><td>${{x.location||''}}</td><td>${{x.area_text||x.area_sqft||''}}</td><td>${{x.rent_text||x.rent_amount||''}}</td><td><button class=btn onclick='editP("${{x.property_code}}")'>Edit</button> <button class="btn red" onclick='delP("${{x.property_code}}")'>Delete</button></td></tr>`).join('')}}
async function editP(c){{let d=await(await fetch('/api/v19/property/'+encodeURIComponent(c))).json(),x=d.property||{{}};f.reset();for(let el of f.elements)if(el.name&&el.name!=='ptype'&&x[el.name]!=null)el.value=x[el.name];document.querySelectorAll('[name=ptype]').forEach(z=>z.checked=(x.property_types||[]).includes(z.value));editcode.value=c;cancel.classList.remove('hidden');save.textContent='Save Changes';scrollTo(0,0)}}
async function delP(c){{if(!confirm('Delete accidental/duplicate property '+c+'?'))return;let r=await fetch('/api/v19/property/'+encodeURIComponent(c),{{method:'DELETE'}}),x=await r.json();if(!r.ok)alert(x.detail||'Delete failed');else load()}}
async function uploadMedia(){{if(!currentMedia||!imgs.files.length)return;let fd=new FormData();for(let x of imgs.files)fd.append('files',x);mmsg.textContent='Uploading...';let r=await fetch('/api/v19/property/'+encodeURIComponent(currentMedia)+'/images',{{method:'POST',body:fd}}),x=await r.json();mmsg.textContent=r.ok?x.uploaded+' image(s) uploaded.':'ERROR: '+(x.detail||'Upload failed')}}
load();
</script></body></html>'''

def _requirement_page(d):
    checks=''.join(f"<label><input type=checkbox name=rtype value='{x}'> {x}</label>" for x in PROPERTY_TYPES)
    return f'''<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Fast Requirement Entry</title>
<style>body{{font-family:Arial;background:#f4f7fb;margin:0;color:#172437}}header{{background:#102235;color:#fff;padding:18px}}.w{{max-width:1150px;margin:auto;padding:18px}}.card{{background:#fff;padding:15px;border-radius:12px;margin-bottom:12px}}.g{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}input,select,textarea{{width:100%;padding:9px;border:1px solid #ccd6e2;border-radius:7px;box-sizing:border-box}}.checks{{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}}.checks input{{width:auto}}.btn{{padding:9px 12px;background:#1677ff;color:#fff;border:0;border-radius:8px;cursor:pointer}}.red{{background:#b42318}}.gray{{background:#e9eef5;color:#203247}}table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{padding:8px;border-bottom:1px solid #eee;text-align:left}}.hidden{{display:none}}.msg{{margin-top:10px;background:#fff8e8;padding:9px}}@media(max-width:800px){{.g,.checks{{grid-template-columns:1fr}}}}</style></head>
<body><header><b>{'Goa' if d=='GOA' else 'Delhi NCR'} Fast Requirement Entry V19</b><br><small>Fast save · Edit/Delete · Form clears after save</small></header><div class=w>
<div class=card><form id=f><input id=editcode type=hidden><div class=g><div><b>Client Name</b><input name=client_name></div><div><b>Company Name</b><input name=company_name></div><div><b>Contact Number</b><input name=contact_number></div><div><b>City</b><input name=city></div>
<div style="grid-column:1/-1"><b>Requirement Types *</b><div class=checks>{checks}</div></div><div style="grid-column:1/-1"><b>Preferred Locations *</b><input name=preferred_locations required></div>
<div><b>Minimum Area *</b><input name=minimum_area_text placeholder="4000 sqft" required></div><div><b>Maximum Area *</b><input name=maximum_area_text placeholder="5000 sqft" required></div>
<div><b>Maximum Rent *</b><input name=maximum_rent_text placeholder="5 lakhs" required></div><div><b>Transaction</b><select name=transaction_type><option>LEASE</option><option>SALE</option></select></div>
<div><b>Verification</b><select name=verification_status><option>VERIFIED</option><option>UNVERIFIED</option></select></div><div style="grid-column:1/-1"><b>Additional Points</b><textarea name=additional_points></textarea></div></div><br>
<button id=save class=btn>Save Requirement</button> <button id=cancel type=button class="btn gray hidden" onclick=resetForm()>Cancel Edit</button><div id=msg class=msg>Ready.</div></form></div>
<div class=card><h3>Recent Manual Requirements</h3><table><thead><tr><th>Code</th><th>Company</th><th>Location</th><th>Area</th><th>Rent</th><th>Actions</th></tr></thead><tbody id=rows></tbody></table></div></div>
<script>
const DIV='{d}';let submitting=false;
function bodyFromForm(){{let fd=new FormData(f),b={{requirement_types:[...document.querySelectorAll('[name=rtype]:checked')].map(x=>x.value)}};for(let [k,v] of fd.entries())if(k!=='rtype')b[k]=String(v).trim()||null;return b}}
function resetForm(){{f.reset();editcode.value='';cancel.classList.add('hidden');save.textContent='Save Requirement';msg.textContent='Ready for next requirement.'}}
f.onsubmit=async e=>{{e.preventDefault();if(submitting)return;let b=bodyFromForm();if(!b.requirement_types.length||!b.preferred_locations||!b.minimum_area_text||!b.maximum_area_text||!b.maximum_rent_text){{msg.textContent='Type, Location, Min Area, Max Area and Max Rent are mandatory.';return}}submitting=true;save.disabled=true;let ec=editcode.value,t=performance.now();try{{let r=await fetch(ec?'/api/v19/requirement/'+encodeURIComponent(ec):'/api/v19/requirement?division='+DIV,{{method:ec?'PUT':'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(b)}}),x=await r.json();if(!r.ok)throw Error(x.detail||'Save failed');let ms=Math.round(performance.now()-t),id=x.requirement_code||ec;resetForm();msg.textContent=(ec?'Updated ':'Saved ')+id+' in '+ms+' ms. Form cleared.';await load()}}catch(err){{msg.textContent='ERROR: '+err.message}}finally{{submitting=false;save.disabled=false}}}};
async function load(){{let d=await(await fetch('/api/v19/requirements?division='+DIV)).json();rows.innerHTML=(d.rows||[]).map(x=>`<tr><td>${{x.requirement_code}}</td><td>${{x.company_name||x.client_name||''}}</td><td>${{x.preferred_locations||''}}</td><td>${{x.minimum_area_text||x.minimum_area_sqft||''}} - ${{x.maximum_area_text||x.maximum_area_sqft||''}}</td><td>${{x.maximum_rent_text||x.maximum_rent||''}}</td><td><button class=btn onclick='editR("${{x.requirement_code}}")'>Edit</button> <button class="btn red" onclick='delR("${{x.requirement_code}}")'>Delete</button></td></tr>`).join('')}}
async function editR(c){{let d=await(await fetch('/api/v19/requirement/'+encodeURIComponent(c))).json(),x=d.requirement||{{}};f.reset();for(let el of f.elements)if(el.name&&el.name!=='rtype'&&x[el.name]!=null)el.value=x[el.name];document.querySelectorAll('[name=rtype]').forEach(z=>z.checked=(x.requirement_types||[]).includes(z.value));editcode.value=c;cancel.classList.remove('hidden');save.textContent='Save Changes';scrollTo(0,0)}}
async function delR(c){{if(!confirm('Delete accidental/duplicate requirement '+c+'?'))return;let r=await fetch('/api/v19/requirement/'+encodeURIComponent(c),{{method:'DELETE'}}),x=await r.json();if(!r.ok)alert(x.detail||'Delete failed');else load()}}
load();
</script></body></html>'''

def install_fast_forms(app,engine,need_login,page_role_or_redirect,actor_name):
    @app.on_event('startup')
    def setup():
        with engine.begin() as c:
            for s in [
                "ALTER TABLE pi_operational_properties ADD COLUMN IF NOT EXISTS area_text TEXT",
                "ALTER TABLE pi_operational_properties ADD COLUMN IF NOT EXISTS rent_text TEXT",
                "ALTER TABLE pi_operational_properties ADD COLUMN IF NOT EXISTS entry_source TEXT DEFAULT 'MANUAL'",
                "ALTER TABLE pi_operational_requirements ADD COLUMN IF NOT EXISTS minimum_area_text TEXT",
                "ALTER TABLE pi_operational_requirements ADD COLUMN IF NOT EXISTS maximum_area_text TEXT",
                "ALTER TABLE pi_operational_requirements ADD COLUMN IF NOT EXISTS maximum_rent_text TEXT",
                "ALTER TABLE pi_operational_requirements ADD COLUMN IF NOT EXISTS entry_source TEXT DEFAULT 'MANUAL'"
            ]: c.execute(text(s))

    @app.get('/fast-property-entry',response_class=HTMLResponse)
    def property_page(req:Request,division:str='DELHI_NCR'):
        if not page_role_or_redirect(req):return RedirectResponse('/login',303)
        d='GOA' if division.upper()=='GOA' else 'DELHI_NCR'
        return HTMLResponse(_property_page(d))

    @app.get('/fast-requirement-entry',response_class=HTMLResponse)
    def requirement_page(req:Request,division:str='DELHI_NCR'):
        if not page_role_or_redirect(req):return RedirectResponse('/login',303)
        d='GOA' if division.upper()=='GOA' else 'DELHI_NCR'
        return HTMLResponse(_requirement_page(d))

    @app.post('/api/v19/property')
    def add_property(payload:FastProperty,req:Request,division:str='DELHI_NCR'):
        need_login(req);d='GOA' if division.upper()=='GOA' else 'DELHI_NCR';a=parse_area(payload.area_text);r=parse_money(payload.rent_text)
        if not a or not r:raise HTTPException(400,'Area and Rent must contain recognizable values.')
        pc=code('GOA-PROP' if d=='GOA' else 'PROP')
        with engine.begin() as c:
            c.execute(text("SET LOCAL lock_timeout='1500ms'"));c.execute(text("SET LOCAL statement_timeout='3500ms'"))
            dup=c.execute(text("SELECT property_code FROM pi_operational_properties WHERE division=:d AND lower(location)=lower(:loc) AND area_sqft=:a AND rent_amount=:r AND COALESCE(contact_number,'')=COALESCE(:ph,'') LIMIT 1"),{'d':d,'loc':payload.location,'a':a,'r':r,'ph':payload.contact_number}).first()
            if dup:raise HTTPException(409,f'Possible duplicate already exists: {dup[0]}')
            c.execute(text("INSERT INTO pi_operational_properties(property_code,division,property_name,property_types,city,location,google_location,area_sqft,area_text,rent_amount,rent_text,transaction_type,floor,frontage,parking,possession,suitable_for,nearby_brands,owner_broker_name,contact_number,contact_role,verification_status,remarks,created_by,entry_source,created_at,updated_at) VALUES(:pc,:d,:pn,CAST(:types AS jsonb),:city,:loc,:gl,:a,:at,:r,:rt,:tt,:floor,:front,:park,:poss,:suit,:near,:name,:phone,:role,:verify,:remarks,:by,'MANUAL',NOW(),NOW())"),{'pc':pc,'d':d,'pn':payload.property_name,'types':json.dumps(payload.property_types),'city':payload.city,'loc':payload.location,'gl':payload.google_location,'a':a,'at':payload.area_text,'r':r,'rt':payload.rent_text,'tt':payload.transaction_type,'floor':payload.floor,'front':payload.frontage,'park':payload.parking,'poss':payload.possession,'suit':payload.suitable_for,'near':payload.nearby_brands,'name':payload.owner_broker_name,'phone':payload.contact_number,'role':payload.contact_role,'verify':payload.verification_status,'remarks':payload.remarks,'by':actor_name(req)})
        return {'status':'created','property_code':pc}

    @app.get('/api/v19/properties')
    def list_properties(req:Request,division:str='DELHI_NCR'):
        need_login(req);d='GOA' if division.upper()=='GOA' else 'DELHI_NCR'
        with engine.connect() as c:rows=[dict(x._mapping) for x in c.execute(text("SELECT * FROM pi_operational_properties WHERE division=:d AND COALESCE(entry_source,'MANUAL')='MANUAL' ORDER BY id DESC LIMIT 100"),{'d':d}).fetchall()]
        return {'rows':rows}

    @app.get('/api/v19/property/{pc}')
    def get_property(pc:str,req:Request):
        need_login(req)
        with engine.connect() as c:r=c.execute(text("SELECT * FROM pi_operational_properties WHERE property_code=:pc"),{'pc':pc}).first()
        if not r:raise HTTPException(404,'Property not found')
        d=dict(r._mapping);d['property_types']=d.get('property_types') or []
        return {'property':d}

    @app.put('/api/v19/property/{pc}')
    def edit_property(pc:str,payload:FastProperty,req:Request):
        need_login(req);a=parse_area(payload.area_text);r=parse_money(payload.rent_text)
        if not a or not r:raise HTTPException(400,'Area and Rent must contain recognizable values.')
        with engine.begin() as c:
            c.execute(text("SET LOCAL lock_timeout='1500ms'"));c.execute(text("SET LOCAL statement_timeout='3500ms'"))
            x=c.execute(text("UPDATE pi_operational_properties SET property_name=:pn,property_types=CAST(:types AS jsonb),city=:city,location=:loc,google_location=:gl,area_sqft=:a,area_text=:at,rent_amount=:r,rent_text=:rt,transaction_type=:tt,floor=:floor,frontage=:front,parking=:park,possession=:poss,suitable_for=:suit,nearby_brands=:near,owner_broker_name=:name,contact_number=:phone,contact_role=:role,verification_status=:verify,remarks=:remarks,updated_at=NOW() WHERE property_code=:pc"),{'pc':pc,'pn':payload.property_name,'types':json.dumps(payload.property_types),'city':payload.city,'loc':payload.location,'gl':payload.google_location,'a':a,'at':payload.area_text,'r':r,'rt':payload.rent_text,'tt':payload.transaction_type,'floor':payload.floor,'front':payload.frontage,'park':payload.parking,'poss':payload.possession,'suit':payload.suitable_for,'near':payload.nearby_brands,'name':payload.owner_broker_name,'phone':payload.contact_number,'role':payload.contact_role,'verify':payload.verification_status,'remarks':payload.remarks})
            if not x.rowcount:raise HTTPException(404,'Property not found')
        return {'status':'updated','property_code':pc}

    @app.delete('/api/v19/property/{pc}')
    def delete_property(pc:str,req:Request):
        need_login(req)
        with engine.begin() as c:
            c.execute(text("SET LOCAL lock_timeout='1500ms'"));c.execute(text("DELETE FROM pi_operational_matches WHERE property_code=:pc"),{'pc':pc});c.execute(text("DELETE FROM pi_operational_property_media WHERE property_code=:pc"),{'pc':pc})
            x=c.execute(text("DELETE FROM pi_operational_properties WHERE property_code=:pc"),{'pc':pc})
            if not x.rowcount:raise HTTPException(404,'Property not found')
        return {'status':'deleted','property_code':pc}

    @app.post('/api/v19/property/{pc}/images')
    async def upload_images(pc:str,req:Request,files:list[UploadFile]=File(...)):
        need_login(req)
        if len(files)>12:raise HTTPException(400,'Maximum 12 images')
        with engine.connect() as c:
            if not c.execute(text("SELECT 1 FROM pi_operational_properties WHERE property_code=:pc"),{'pc':pc}).first():raise HTTPException(404,'Property not found')
        n=0
        with engine.begin() as c:
            for f in files:
                if not (f.content_type or '').startswith('image/'):continue
                b=await f.read()
                if len(b)>10*1024*1024:raise HTTPException(413,f'{f.filename} exceeds 10 MB')
                c.execute(text("INSERT INTO pi_operational_property_media(property_code,media_type,filename,mime_type,file_size,content,created_at) VALUES(:pc,'IMAGE',:fn,:mt,:sz,:b,NOW())"),{'pc':pc,'fn':f.filename,'mt':f.content_type,'sz':len(b),'b':b});n+=1
        return {'uploaded':n}

    @app.post('/api/v19/requirement')
    def add_requirement(payload:FastRequirement,req:Request,division:str='DELHI_NCR'):
        need_login(req);d='GOA' if division.upper()=='GOA' else 'DELHI_NCR';mina=parse_area(payload.minimum_area_text);maxa=parse_area(payload.maximum_area_text);rent=parse_money(payload.maximum_rent_text)
        if not mina or not maxa or not rent:raise HTTPException(400,'Area and maximum rent must contain recognizable values.')
        rc=code('GOA-REQ' if d=='GOA' else 'REQ')
        with engine.begin() as c:
            c.execute(text("SET LOCAL lock_timeout='1500ms'"));c.execute(text("SET LOCAL statement_timeout='3500ms'"))
            dup=c.execute(text("SELECT requirement_code FROM pi_operational_requirements WHERE division=:d AND lower(preferred_locations)=lower(:loc) AND minimum_area_sqft=:mina AND maximum_area_sqft=:maxa AND COALESCE(contact_number,'')=COALESCE(:ph,'') LIMIT 1"),{'d':d,'loc':payload.preferred_locations,'mina':mina,'maxa':maxa,'ph':payload.contact_number}).first()
            if dup:raise HTTPException(409,f'Possible duplicate already exists: {dup[0]}')
            c.execute(text("INSERT INTO pi_operational_requirements(requirement_code,division,client_name,company_name,contact_number,requirement_types,city,preferred_locations,minimum_area_sqft,minimum_area_text,maximum_area_sqft,maximum_area_text,maximum_rent,maximum_rent_text,transaction_type,additional_points,verification_status,created_by,entry_source,created_at,updated_at) VALUES(:rc,:d,:client,:company,:phone,CAST(:types AS jsonb),:city,:loc,:mina,:minat,:maxa,:maxat,:rent,:rentt,:tt,:points,:verify,:by,'MANUAL',NOW(),NOW())"),{'rc':rc,'d':d,'client':payload.client_name,'company':payload.company_name,'phone':payload.contact_number,'types':json.dumps(payload.requirement_types),'city':payload.city,'loc':payload.preferred_locations,'mina':mina,'minat':payload.minimum_area_text,'maxa':maxa,'maxat':payload.maximum_area_text,'rent':rent,'rentt':payload.maximum_rent_text,'tt':payload.transaction_type,'points':payload.additional_points,'verify':payload.verification_status,'by':actor_name(req)})
        return {'status':'created','requirement_code':rc}

    @app.get('/api/v19/requirements')
    def list_requirements(req:Request,division:str='DELHI_NCR'):
        need_login(req);d='GOA' if division.upper()=='GOA' else 'DELHI_NCR'
        with engine.connect() as c:rows=[dict(x._mapping) for x in c.execute(text("SELECT * FROM pi_operational_requirements WHERE division=:d AND COALESCE(entry_source,'MANUAL')='MANUAL' ORDER BY id DESC LIMIT 100"),{'d':d}).fetchall()]
        return {'rows':rows}

    @app.get('/api/v19/requirement/{rc}')
    def get_requirement(rc:str,req:Request):
        need_login(req)
        with engine.connect() as c:r=c.execute(text("SELECT * FROM pi_operational_requirements WHERE requirement_code=:rc"),{'rc':rc}).first()
        if not r:raise HTTPException(404,'Requirement not found')
        d=dict(r._mapping);d['requirement_types']=d.get('requirement_types') or []
        return {'requirement':d}

    @app.put('/api/v19/requirement/{rc}')
    def edit_requirement(rc:str,payload:FastRequirement,req:Request):
        need_login(req);mina=parse_area(payload.minimum_area_text);maxa=parse_area(payload.maximum_area_text);rent=parse_money(payload.maximum_rent_text)
        if not mina or not maxa or not rent:raise HTTPException(400,'Area and maximum rent must contain recognizable values.')
        with engine.begin() as c:
            c.execute(text("SET LOCAL lock_timeout='1500ms'"));c.execute(text("SET LOCAL statement_timeout='3500ms'"))
            x=c.execute(text("UPDATE pi_operational_requirements SET client_name=:client,company_name=:company,contact_number=:phone,requirement_types=CAST(:types AS jsonb),city=:city,preferred_locations=:loc,minimum_area_sqft=:mina,minimum_area_text=:minat,maximum_area_sqft=:maxa,maximum_area_text=:maxat,maximum_rent=:rent,maximum_rent_text=:rentt,transaction_type=:tt,additional_points=:points,verification_status=:verify,updated_at=NOW() WHERE requirement_code=:rc"),{'rc':rc,'client':payload.client_name,'company':payload.company_name,'phone':payload.contact_number,'types':json.dumps(payload.requirement_types),'city':payload.city,'loc':payload.preferred_locations,'mina':mina,'minat':payload.minimum_area_text,'maxa':maxa,'maxat':payload.maximum_area_text,'rent':rent,'rentt':payload.maximum_rent_text,'tt':payload.transaction_type,'points':payload.additional_points,'verify':payload.verification_status})
            if not x.rowcount:raise HTTPException(404,'Requirement not found')
            c.execute(text("DELETE FROM pi_operational_matches WHERE requirement_code=:rc"),{'rc':rc})
        return {'status':'updated','requirement_code':rc}

    @app.delete('/api/v19/requirement/{rc}')
    def delete_requirement(rc:str,req:Request):
        need_login(req)
        with engine.begin() as c:
            c.execute(text("SET LOCAL lock_timeout='1500ms'"));c.execute(text("DELETE FROM pi_operational_matches WHERE requirement_code=:rc"),{'rc':rc})
            x=c.execute(text("DELETE FROM pi_operational_requirements WHERE requirement_code=:rc"),{'rc':rc})
            if not x.rowcount:raise HTTPException(404,'Requirement not found')
        return {'status':'deleted','requirement_code':rc}

    @app.middleware('http')
    async def router(request,call_next):
        p=request.url.path;q=request.url.query.upper();div='GOA' if 'DIVISION=GOA' in q else 'DELHI_NCR'
        if request.method=='GET' and p in {'/manual-property-final','/manual-property-v18','/manual-property-final-exec','/operational-property-form','/property-form-final','/property-manual','/goa-property-form','/v14-property-form'}:
            if p=='/goa-property-form':div='GOA'
            return RedirectResponse(f'/fast-property-entry?division={div}',307)
        if request.method=='GET' and p in {'/manual-requirement-final','/operational-requirement-form','/goa-requirement-form','/v14-requirement-form'}:
            if p=='/goa-requirement-form':div='GOA'
            return RedirectResponse(f'/fast-requirement-entry?division={div}',307)
        response=await call_next(request)
        if p.startswith(('/fast-property-entry','/fast-requirement-entry','/api/v19')):
            response.headers['Cache-Control']='no-store, no-cache, must-revalidate, max-age=0';response.headers['Pragma']='no-cache';response.headers['Expires']='0'
        return response
    return app
