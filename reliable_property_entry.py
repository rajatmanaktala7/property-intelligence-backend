import re, uuid, hashlib
from datetime import datetime, timezone, date
from typing import Optional
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import text

V18_VERSION="18.2-FLEXIBLE-MANUAL-FIELDS"

class V18PropertyEntry(BaseModel):
    request_id:str
    property_name:Optional[str]=None
    property_type:str
    entry_status:str="UNVERIFIED"
    city:str
    location:str
    availability_status:str="Available"
    area_text:str
    rent_text:str
    floor:Optional[str]=None
    rent_or_sale:Optional[str]=None
    frontage:Optional[str]=None
    address:Optional[str]=None
    owner_name:Optional[str]=None
    owner_contact:Optional[str]=None
    broker_name:Optional[str]=None
    broker_contact:Optional[str]=None
    team_member_name:Optional[str]=None
    parking:Optional[str]=None
    possession:Optional[str]=None
    nearby_brands:Optional[str]=None
    suitable_category:Optional[str]=None
    remarks:Optional[str]=None

def _clean_phone(v):
    d=re.sub(r"\D","",str(v or ""))
    if d.startswith("91") and len(d)>=12:d=d[-10:]
    return d if len(d)==10 else (str(v or "").strip() or None)

def _parse_area_sqft(s):
    s=str(s or "").lower().replace(","," ")
    m=re.search(r"(\d+(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|square\s*feet)",s)
    if m:return float(m.group(1))
    m=re.search(r"(\d+(?:\.\d+)?)\s*(?:sq\.?\s*m|sqm|sqmt|square\s*met(?:er|re)s?)",s)
    if m:return round(float(m.group(1))*10.7639104167,2)
    m=re.search(r"(\d+(?:\.\d+)?)",s)
    return float(m.group(1)) if m else None

def _parse_rent_amount(s):
    s=str(s or "").lower().replace(",","")
    m=re.search(r"(\d+(?:\.\d+)?)\s*(crore|cr|lakhs?|lacs?|lac|lakh|k|thousand)?",s)
    if not m:return None
    n=float(m.group(1));u=(m.group(2) or "").lower()
    if u in {"crore","cr"}:n*=10000000
    elif u in {"lakh","lakhs","lac","lacs"}:n*=100000
    elif u in {"k","thousand"}:n*=1000
    return round(n,2)

def _fingerprint(p,oc,bc,a):
    raw="|".join(str(x or "").strip().lower() for x in [p.property_name,p.city,p.location,p.property_type,a,p.area_text,p.floor,p.rent_or_sale,p.rent_text,oc,bc])
    return hashlib.sha256(raw.encode()).hexdigest()

HTML="""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Reliable Property Entry V18.2</title>
<style>*{box-sizing:border-box}body{font-family:Arial;margin:0;background:#f4f7fb;color:#172437}header{background:#102235;color:#fff;padding:20px}.w{max-width:1200px;margin:auto;padding:20px}.card{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:18px}.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:13px}label{display:block;font-size:12px;font-weight:bold;margin-bottom:5px}input,select,textarea{width:100%;padding:11px;border:1px solid #ccd6e2;border-radius:8px}.full{grid-column:1/-1}.btn{padding:12px 18px;border:0;border-radius:9px;background:#08734b;color:#fff;font-weight:bold}.msg{margin-top:14px;padding:12px;border-radius:9px;background:#eef6ff;border:1px solid #bfd8ff}.ok{background:#eaf8ef}.err{background:#fff0f0}.hint{font-size:11px;color:#7b8797}.req{color:#b42318}@media(max-width:760px){.grid{grid-template-columns:1fr}.full{grid-column:auto}}</style></head>
<body><header><b>Reliable Manual Property Entry V18.2</b><br><small>Flexible mandatory Area and Rent fields</small></header><div class='w'><div class='card'><form id='f'><div class='grid'>
<div><label>Property Name</label><input name='property_name'></div>
<div><label>Property Type <span class='req'>*</span></label><select name='property_type' required><option>Retail</option><option>Office</option><option>Land</option><option>Warehouse</option><option>Industrial</option><option>Hospitality</option><option>Residential</option><option>Villa</option><option>Farmhouse</option><option>Commercial</option></select></div>
<div><label>City <span class='req'>*</span></label><input name='city' required></div><div><label>Location <span class='req'>*</span></label><input name='location' required></div>
<div class='full'><label>Area Details <span class='req'>*</span></label><input name='area_text' required placeholder='5000 sqft OR 500 sqmt (1000 sqft each on 5 floor)'><div class='hint'>Type exactly as your team understands the property.</div></div>
<div class='full'><label>Rent Details <span class='req'>*</span></label><input name='rent_text' required placeholder='5 lakhs OR 4.5 lakh per month OR Rs 650000'><div class='hint'>Normal language is allowed.</div></div>
<div><label>Floor</label><input name='floor'></div><div><label>Rent / Sale</label><select name='rent_or_sale'><option value=''>Select</option><option>Lease</option><option>Sale</option></select></div>
<div><label>Verification *</label><select name='entry_status'><option>UNVERIFIED</option><option>VERIFIED</option></select></div><div><label>Availability</label><select name='availability_status'><option>Available</option><option>Not Available</option><option>On Hold</option></select></div>
<div><label>Owner Name</label><input name='owner_name'></div><div><label>Owner Contact</label><input name='owner_contact'></div>
<div><label>Broker Name</label><input name='broker_name'></div><div><label>Broker Contact</label><input name='broker_contact'></div>
<div><label>Team Member</label><input name='team_member_name'></div><div><label>Frontage</label><input name='frontage'></div>
<div class='full'><label>Address</label><input name='address'></div><div><label>Parking</label><input name='parking'></div><div><label>Possession</label><input name='possession'></div>
<div class='full'><label>Nearby Brands</label><input name='nearby_brands'></div><div class='full'><label>Suitable Category</label><input name='suitable_category'></div><div class='full'><label>Remarks</label><textarea name='remarks'></textarea></div>
<div class='full'><button id='save' class='btn' type='submit'>Save Property</button></div></div></form><div id='msg' class='msg'>Ready.</div></div></div>
<script>let submitting=false;f.onsubmit=async e=>{e.preventDefault();if(submitting)return;const fd=new FormData(f),body={request_id:(crypto.randomUUID?crypto.randomUUID():String(Date.now()))};for(const[k,v]of fd.entries())body[k]=String(v).trim()||null;if(!body.property_type||!body.city||!body.location||!body.area_text||!body.rent_text||!body.entry_status){msg.className='msg err';msg.textContent='Please fill all mandatory fields.';return}submitting=true;save.disabled=true;save.textContent='Saving...';const ctrl=new AbortController(),timer=setTimeout(()=>ctrl.abort(),12000);try{const r=await fetch('/api/v18/property-entry',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:ctrl.signal});const d=await r.json();if(!r.ok)throw Error(d.detail||'Save failed');msg.className='msg ok';msg.textContent=(d.message||'Saved')+' Property ID: '+(d.property_id||'');if(d.status==='created')f.reset()}catch(err){msg.className='msg err';msg.textContent=err.name==='AbortError'?'Stopped waiting after 12 seconds. Check inventory before retrying.':'ERROR: '+err.message}finally{clearTimeout(timer);submitting=false;save.disabled=false;save.textContent='Save Property'}};</script></body></html>"""

def install_reliable_property_entry(app,engine,need_login,page_role_or_redirect,actor_name):
    @app.on_event("startup")
    def _setup():
        with engine.begin() as c:
            for sql in ["ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS rent_amount NUMERIC(14,2)","ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS rent_text TEXT","ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS area_text TEXT","ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS frontage TEXT","ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS address TEXT","ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS team_member_name TEXT","ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS manual_request_id TEXT"]:
                c.execute(text(sql))
    @app.get("/property-entry-reliable",response_class=HTMLResponse)
    def page(req:Request):
        if not page_role_or_redirect(req):return RedirectResponse("/login",303)
        return HTMLResponse(HTML)
    @app.post("/api/v18/property-entry")
    def save(payload:V18PropertyEntry,req:Request):
        need_login(req)
        if not payload.area_text.strip() or not payload.rent_text.strip():raise HTTPException(400,"Area Details and Rent Details are required")
        oc=_clean_phone(payload.owner_contact);bc=_clean_phone(payload.broker_contact);a=_parse_area_sqft(payload.area_text);r=_parse_rent_amount(payload.rent_text);fp=_fingerprint(payload,oc,bc,a)
        try:
            with engine.begin() as c:
                c.execute(text("SET LOCAL lock_timeout='3s'"));c.execute(text("SET LOCAL statement_timeout='8s'"))
                row=c.execute(text("SELECT property_id FROM pi_properties WHERE manual_request_id=:rid LIMIT 1"),{"rid":payload.request_id}).first()
                if row:return {"status":"already_saved","property_id":row[0],"message":"Already saved"}
                row=c.execute(text("SELECT property_id FROM pi_properties WHERE fingerprint=:fp ORDER BY id DESC LIMIT 1"),{"fp":fp}).first()
                if row:return {"status":"duplicate","property_id":row[0],"message":"Possible duplicate found"}
                pid="PROP-MAN-"+datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")+"-"+uuid.uuid4().hex[:6].upper()
                sql="INSERT INTO pi_properties(property_id,fingerprint,manual_request_id,property_name,entry_status,availability_status,property_type,city,location,available_area_sqft,area_text,floor,rent_or_sale,rent_amount,rent_text,frontage,address,owner_name,owner_contact,broker_name,broker_contact,team_member_name,parking,possession,nearby_brands,suitable_category,remarks,verification_status,verified_by,verified_date,source,created_at,updated_at) VALUES(:pid,:fp,:rid,:pn,'Active',:av,:pt,:city,:loc,:area,:area_text,:floor,:ros,:rent,:rent_text,:frontage,:address,:on,:oc,:bn,:bc,:tm,:parking,:possession,:nearby,:cat,:remarks,:vs,:vb,:vd,'MANUAL_V18_2',NOW(),NOW())"
                c.execute(text(sql),{"pid":pid,"fp":fp,"rid":payload.request_id,"pn":payload.property_name,"av":payload.availability_status,"pt":payload.property_type,"city":payload.city.strip(),"loc":payload.location.strip(),"area":a,"area_text":payload.area_text.strip(),"floor":payload.floor,"ros":payload.rent_or_sale,"rent":r,"rent_text":payload.rent_text.strip(),"frontage":payload.frontage,"address":payload.address,"on":payload.owner_name,"oc":oc,"bn":payload.broker_name,"bc":bc,"tm":payload.team_member_name,"parking":payload.parking,"possession":payload.possession,"nearby":payload.nearby_brands,"cat":payload.suitable_category,"remarks":payload.remarks,"vs":payload.entry_status.upper(),"vb":actor_name(req) if payload.entry_status.upper()=="VERIFIED" else None,"vd":date.today() if payload.entry_status.upper()=="VERIFIED" else None})
            return {"status":"created","property_id":pid,"message":"Property saved successfully.","area_sqft":a,"rent_amount":r}
        except Exception as ex:
            raise HTTPException(500,f"Property save failed: {type(ex).__name__}: {ex}")
    return app
