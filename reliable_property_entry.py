import re, uuid, hashlib
from datetime import datetime, timezone, date
from typing import Optional
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import text

V18_VERSION = "18.0-RELIABLE-MANUAL-ENTRY"

class V18PropertyEntry(BaseModel):
    request_id: str
    property_name: Optional[str] = None
    property_type: str
    entry_status: str = "UNVERIFIED"
    city: str
    location: str
    availability_status: str = "Available"
    available_area_sqft: float
    floor: Optional[str] = None
    rent_or_sale: Optional[str] = None
    rent_amount: Optional[float] = None
    frontage: Optional[str] = None
    address: Optional[str] = None
    owner_name: Optional[str] = None
    owner_contact: Optional[str] = None
    broker_name: Optional[str] = None
    broker_contact: Optional[str] = None
    team_member_name: Optional[str] = None
    parking: Optional[str] = None
    possession: Optional[str] = None
    nearby_brands: Optional[str] = None
    suitable_category: Optional[str] = None
    remarks: Optional[str] = None

def _clean_phone(v):
    d = re.sub(r"\D", "", str(v or ""))
    if d.startswith("91") and len(d) >= 12:
        d = d[-10:]
    return d if len(d) == 10 else (str(v or "").strip() or None)

def _fingerprint(p, owner_contact, broker_contact):
    raw = "|".join(str(x or "").strip().lower() for x in [
        p.property_name,p.city,p.location,p.property_type,p.available_area_sqft,
        p.floor,p.rent_or_sale,owner_contact,broker_contact
    ])
    return hashlib.sha256(raw.encode()).hexdigest()

HTML = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reliable Property Entry V18</title>
<style>
*{box-sizing:border-box}body{font-family:Arial;margin:0;background:#f4f7fb;color:#172437}
header{background:#102235;color:#fff;padding:20px}.w{max-width:1200px;margin:auto;padding:20px}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:18px}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:13px}
label{display:block;font-size:12px;font-weight:bold;margin-bottom:5px}
input,select,textarea{width:100%;padding:11px;border:1px solid #ccd6e2;border-radius:8px}
textarea{min-height:80px}.full{grid-column:1/-1}.btn{padding:12px 18px;border:0;border-radius:9px;background:#08734b;color:#fff;font-weight:bold}
.btn:disabled{opacity:.55}.back{display:inline-block;background:#e9eef5;color:#203247;text-decoration:none;padding:9px 11px;border-radius:8px;font-weight:bold}
.msg{margin-top:14px;padding:12px;border-radius:9px;background:#eef6ff;border:1px solid #bfd8ff}.ok{background:#eaf8ef;border-color:#9fd5b4}.err{background:#fff0f0;border-color:#efb4b4}
.note{font-size:12px;color:#687789;margin-bottom:14px}@media(max-width:760px){.grid{grid-template-columns:1fr}.full{grid-column:auto}}
</style></head>
<body><header><b>Reliable Manual Property Entry V18</b><br><small>Fast record-first save. Media never blocks the property record.</small></header>
<div class="w"><p><a class="back" href="/final-dashboard-v3">← Dashboard</a></p><div class="card">
<div class="note"><b>Important:</b> Save property details first. Add images/videos after save. This prevents large files from freezing manual entry.</div>
<form id="f"><div class="grid">
<div><label>Property Name</label><input name="property_name"></div>
<div><label>Property Type *</label><select name="property_type" required><option>Retail</option><option>Office</option><option>Land</option><option>Warehouse</option><option>Industrial</option><option>Hospitality</option><option>Residential</option><option>Villa</option><option>Farmhouse</option><option>Commercial</option></select></div>
<div><label>City *</label><input name="city" required></div><div><label>Location *</label><input name="location" required></div>
<div><label>Available Area (sqft) *</label><input name="available_area_sqft" type="number" min="0.01" step="0.01" required></div>
<div><label>Floor</label><input name="floor" placeholder="Ground Floor"></div>
<div><label>Rent / Sale</label><select name="rent_or_sale"><option value="">Select</option><option>Lease</option><option>Sale</option></select></div>
<div><label>Rent Amount</label><input name="rent_amount" type="number" min="0" step="0.01"></div>
<div><label>Verification *</label><select name="entry_status"><option>UNVERIFIED</option><option>VERIFIED</option></select></div>
<div><label>Availability</label><select name="availability_status"><option>Available</option><option>Not Available</option><option>On Hold</option></select></div>
<div><label>Owner Name</label><input name="owner_name"></div><div><label>Owner Contact</label><input name="owner_contact"></div>
<div><label>Broker Name</label><input name="broker_name"></div><div><label>Broker Contact</label><input name="broker_contact"></div>
<div><label>Team Member / Follow-up Owner</label><input name="team_member_name"></div><div><label>Frontage</label><input name="frontage"></div>
<div class="full"><label>Address</label><input name="address"></div><div><label>Parking</label><input name="parking"></div>
<div><label>Possession</label><input name="possession"></div><div class="full"><label>Nearby Brands</label><input name="nearby_brands"></div>
<div class="full"><label>Suitable Category</label><input name="suitable_category"></div><div class="full"><label>Remarks</label><textarea name="remarks"></textarea></div>
<div class="full"><button id="save" class="btn" type="submit">Save Property</button></div>
</div></form><div id="msg" class="msg">Ready.</div></div></div>
<script>
let submitting=false;
f.onsubmit=async e=>{
 e.preventDefault(); if(submitting)return;
 const fd=new FormData(f), body={request_id:(crypto.randomUUID?crypto.randomUUID():String(Date.now()))};
 for(const [k,v] of fd.entries())body[k]=String(v).trim()||null;
 body.available_area_sqft=Number(body.available_area_sqft); if(body.rent_amount!==null)body.rent_amount=Number(body.rent_amount);
 submitting=true;save.disabled=true;save.textContent='Saving...';msg.className='msg';msg.textContent='Saving property...';
 const ctrl=new AbortController(), timer=setTimeout(()=>ctrl.abort(),12000);
 try{
  const r=await fetch('/api/v18/property-entry',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:ctrl.signal});
  const d=await r.json(); if(!r.ok)throw Error(d.detail||d.message||'Save failed');
  msg.className='msg ok';msg.textContent=(d.message||'Saved')+' Property ID: '+(d.property_id||'');
  if(d.status==='created')f.reset();
 }catch(err){
  msg.className='msg err';msg.textContent=err.name==='AbortError'?'Stopped waiting after 12 seconds. Check Fresh Inventory before retrying; duplicate protection is enabled.':'ERROR: '+err.message;
 }finally{clearTimeout(timer);submitting=false;save.disabled=false;save.textContent='Save Property'}
};
</script></body></html>"""

def install_reliable_property_entry(app, engine, need_login, page_role_or_redirect, actor_name):
    @app.on_event("startup")
    def _setup():
        with engine.begin() as c:
            for sql in [
                "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS rent_amount NUMERIC(14,2)",
                "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS frontage TEXT",
                "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS address TEXT",
                "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS team_member_name TEXT",
                "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS manual_request_id TEXT",
                "CREATE INDEX IF NOT EXISTS idx_pi_properties_manual_request_id ON pi_properties(manual_request_id)",
                "CREATE INDEX IF NOT EXISTS idx_pi_properties_fingerprint_v18 ON pi_properties(fingerprint)"
            ]:
                c.execute(text(sql))

    @app.get("/property-entry-reliable", response_class=HTMLResponse)
    def page(req: Request):
        if not page_role_or_redirect(req):
            return RedirectResponse("/login", status_code=303)
        return HTMLResponse(HTML)

    @app.post("/api/v18/property-entry")
    def save(payload: V18PropertyEntry, req: Request):
        need_login(req)
        if not payload.city.strip() or not payload.location.strip():
            raise HTTPException(400,"City and location are required")
        if payload.available_area_sqft <= 0:
            raise HTTPException(400,"Available area must be greater than zero")
        if payload.entry_status.upper() not in {"VERIFIED","UNVERIFIED"}:
            raise HTTPException(400,"Verification must be VERIFIED or UNVERIFIED")

        owner_contact=_clean_phone(payload.owner_contact)
        broker_contact=_clean_phone(payload.broker_contact)
        fp=_fingerprint(payload,owner_contact,broker_contact)

        try:
            with engine.begin() as c:
                c.execute(text("SET LOCAL lock_timeout = '3s'"))
                c.execute(text("SET LOCAL statement_timeout = '8s'"))

                row=c.execute(text("SELECT property_id FROM pi_properties WHERE manual_request_id=:rid LIMIT 1"),{"rid":payload.request_id}).first()
                if row:
                    return {"status":"already_saved","property_id":row[0],"message":"This property was already saved successfully."}

                row=c.execute(text("SELECT property_id FROM pi_properties WHERE fingerprint=:fp ORDER BY id DESC LIMIT 1"),{"fp":fp}).first()
                if row:
                    return {"status":"duplicate","property_id":row[0],"message":"Possible duplicate found. No second property was created."}

                pid="PROP-MAN-"+datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")+"-"+uuid.uuid4().hex[:6].upper()
                sql="INSERT INTO pi_properties(property_id,fingerprint,manual_request_id,property_name,entry_status,availability_status,property_type,city,location,available_area_sqft,floor,rent_or_sale,rent_amount,frontage,address,owner_name,owner_contact,broker_name,broker_contact,team_member_name,parking,possession,nearby_brands,suitable_category,remarks,verification_status,verified_by,verified_date,source,created_at,updated_at) VALUES(:pid,:fp,:rid,:property_name,'Active',:availability_status,:property_type,:city,:location,:available_area,:floor,:rent_or_sale,:rent_amount,:frontage,:address,:owner_name,:owner_contact,:broker_name,:broker_contact,:team_member_name,:parking,:possession,:nearby_brands,:suitable_category,:remarks,:verification_status,:verified_by,:verified_date,'MANUAL_V18',NOW(),NOW())"
                c.execute(text(sql),{
                    "pid":pid,"fp":fp,"rid":payload.request_id,"property_name":payload.property_name,
                    "availability_status":payload.availability_status,"property_type":payload.property_type,
                    "city":payload.city.strip(),"location":payload.location.strip(),"available_area":payload.available_area_sqft,
                    "floor":payload.floor,"rent_or_sale":payload.rent_or_sale,"rent_amount":payload.rent_amount,
                    "frontage":payload.frontage,"address":payload.address,"owner_name":payload.owner_name,
                    "owner_contact":owner_contact,"broker_name":payload.broker_name,"broker_contact":broker_contact,
                    "team_member_name":payload.team_member_name,"parking":payload.parking,"possession":payload.possession,
                    "nearby_brands":payload.nearby_brands,"suitable_category":payload.suitable_category,"remarks":payload.remarks,
                    "verification_status":payload.entry_status.upper(),
                    "verified_by":actor_name(req) if payload.entry_status.upper()=="VERIFIED" else None,
                    "verified_date":date.today() if payload.entry_status.upper()=="VERIFIED" else None
                })
            return {"status":"created","property_id":pid,"message":"Property saved successfully."}
        except Exception as ex:
            s=str(ex).lower()
            if "lock timeout" in s or "statement timeout" in s or "canceling statement" in s:
                raise HTTPException(503,"Database is busy. Nothing was lost. Please press Save again.")
            raise HTTPException(500,f"Property save failed: {type(ex).__name__}: {ex}")

    @app.middleware("http")
    async def no_cache(request, call_next):
        response=await call_next(request)
        if request.url.path.startswith(("/property-entry-reliable","/api/v18/property-entry")):
            response.headers["Cache-Control"]="no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"]="no-cache"; response.headers["Expires"]="0"
        return response
    return app
