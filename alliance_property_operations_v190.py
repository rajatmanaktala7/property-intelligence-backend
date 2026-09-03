from __future__ import annotations

import json, re, uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html import escape
from fastapi import File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION = "19.0-STABLE-PROPERTY-OPERATIONS"
FACTORS = {"SQFT": Decimal("1"), "SQYD": Decimal("9"), "SQMTR": Decimal("10.7639104167"), "ACRE": Decimal("43560")}
LABELS = {"SQFT": "Sq Ft", "SQYD": "Sq Yd", "SQMTR": "Sq Mtr", "ACRE": "Acre"}
PROPERTY_TYPES = [
    "Retail Shop", "High Street Retail", "Mall Retail", "Office", "Restaurant", "Cafe",
    "Banquet / Wedding Venue", "Hotel", "Guest House", "Lounge", "Club", "Bar",
    "Farmhouse", "Warehouse", "Industrial", "Land", "Mixed Use", "Residential / Villa",
    "Pre-Rented Property",
]

CSS = """
*{box-sizing:border-box}body{font-family:Arial;margin:0;background:#f4f7fb;color:#172437}
header{background:#102235;color:white;padding:18px 22px}.w{max-width:1800px;margin:auto;padding:18px}
.card{background:white;border:1px solid #dfe7f0;border-radius:12px;padding:16px;margin:12px 0}
.grid{display:grid;grid-template-columns:repeat(3,minmax(210px,1fr));gap:12px}
.types{display:grid;grid-template-columns:repeat(3,minmax(180px,1fr));gap:7px}.types label{background:#f8fafc;border-radius:7px;padding:7px}.types input{width:auto}
.kpis{display:grid;grid-template-columns:repeat(6,minmax(130px,1fr));gap:10px}.kpi{background:white;border:1px solid #dfe7f0;border-radius:10px;padding:12px}.kpi b{font-size:24px;display:block}
label small{display:block;font-weight:700;margin-bottom:5px}input,select,textarea{width:100%;padding:10px;border:1px solid #cbd6e2;border-radius:8px}textarea{min-height:90px}
.btn,button{display:inline-block;border:0;border-radius:8px;padding:9px 12px;background:#1677ff;color:white;text-decoration:none;font-weight:700;cursor:pointer}.gray{background:#edf2f7!important;color:#24364b!important}.green{background:#08734b!important}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.tablewrap{overflow:auto;max-height:72vh;border:1px solid #dfe7f0;border-radius:10px;background:white}
table{border-collapse:collapse;width:100%;font-size:12px}th,td{padding:9px;border-bottom:1px solid #edf1f5;text-align:left;vertical-align:top;white-space:nowrap}th{position:sticky;top:0;background:#f8fafc;z-index:2}
.drop{border:2px dashed #9fb1c5;border-radius:12px;padding:20px;text-align:center;background:#fafcff;cursor:pointer;min-height:115px}.drop.over{outline:3px solid #b6d0ff}.drop input{display:none}
.chip{display:inline-block;background:#eef4fb;border-radius:999px;padding:5px 8px;margin:3px;font-size:11px}.pill{display:inline-block;border-radius:999px;padding:4px 7px;font-size:11px;font-weight:bold}.verified{background:#dcfce7;color:#166534}.unverified{background:#fef3c7;color:#92400e}
.ok{color:#08734b;font-weight:700}.err{color:#a11;font-weight:700}.hint{font-size:12px;color:#62748a}.stage{padding:8px 10px;border-radius:8px;background:#f7f9fc;margin:6px 0}
@media(max-width:950px){.grid,.types,.kpis{grid-template-columns:1fr}}
"""

DROP_JS = r"""
function esc(v){return String(v??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));}
function wireDrop(zoneId,inputId,previewId,pasteImages){
 const z=document.getElementById(zoneId),i=document.getElementById(inputId),p=document.getElementById(previewId);if(!z||!i)return;
 let files=[];
 const render=()=>{const dt=new DataTransfer();files.forEach(f=>dt.items.add(f));i.files=dt.files;if(p)p.innerHTML=files.map((f,n)=>`<span class="chip">${esc(f.name)} <button type="button" class="gray" onclick="removeQueued('${inputId}',${n})">x</button></span>`).join('')};
 window.__uploadBags=window.__uploadBags||{};window.__uploadBags[inputId]={get:()=>files,set:a=>{files=a;render()}};
 const add=arr=>{for(const f of (arr||[]))if(f instanceof File)files.push(f);render()};
 i.onchange=()=>{const a=[...i.files];files=[];add(a)};
 ['dragenter','dragover'].forEach(ev=>z.addEventListener(ev,e=>{e.preventDefault();z.classList.add('over')}));
 ['dragleave','drop'].forEach(ev=>z.addEventListener(ev,e=>{e.preventDefault();z.classList.remove('over')}));
 z.addEventListener('drop',e=>add([...e.dataTransfer.files]));
 if(pasteImages)z.addEventListener('paste',e=>{const arr=[];for(const it of [...(e.clipboardData?.items||[])]){if(it.kind==='file'&&it.type.startsWith('image/')){const f=it.getAsFile();if(f){const ext=(f.type.split('/')[1]||'png').replace('jpeg','jpg');arr.push(new File([f],`clipboard-${Date.now()}.${ext}`,{type:f.type}))}}}if(arr.length){e.preventDefault();add(arr)}});
 z.addEventListener('click',e=>{if(!e.target.closest('button'))i.click()});
}
function removeQueued(id,n){const q=window.__uploadBags?.[id];if(!q)return;q.set(q.get().filter((_,j)=>j!==n));}
"""

def h(v): return escape("" if v is None else str(v), quote=True)

def _auth(core, req):
    try:return None if core.page_role_or_redirect(req) else RedirectResponse("/login",303)
    except Exception:return None

def _actor(core, req):
    try:return core.actor_name(req)
    except Exception:return "team"

def _dec(v):
    s=str(v or "").replace(",","").replace("₹","").strip()
    if not s:return None
    try:return Decimal(s)
    except InvalidOperation:raise HTTPException(400,f"Invalid number: {v}")

def parse_area(value, unit):
    d=_dec(value);u=str(unit or "SQFT").upper()
    if d is None or d<=0:raise HTTPException(400,"Area must be greater than zero.")
    if u not in FACTORS:raise HTTPException(400,"Invalid area unit.")
    return d,u,(d*FACTORS[u]).quantize(Decimal("0.01"))

def parse_money(raw):
    original=str(raw or "").strip();s=original.lower().replace(",","").replace("₹"," ")
    if not s:return None,""
    m=re.search(r"(-?\d+(?:\.\d+)?)",s)
    if not m:raise HTTPException(400,f"Could not understand amount: {original}")
    n=Decimal(m.group(1))
    if n<0:raise HTTPException(400,"Negative amount is not allowed.")
    mult=Decimal("1")
    if re.search(r"\b(cr|crore|crores)\b",s):mult=Decimal("10000000")
    elif re.search(r"\b(l|lac|lakh|lakhs)\b",s):mult=Decimal("100000")
    elif re.search(r"\b(k|thousand)\b",s):mult=Decimal("1000")
    return (n*mult).quantize(Decimal("0.01")),original

def money(v):
    if v in (None,""):return "—"
    try:n=Decimal(str(v))
    except Exception:return h(v) or "—"
    if abs(n)>=Decimal("10000000"):return f"₹{(n/Decimal('10000000')).quantize(Decimal('0.01')).normalize():f} Cr"
    if abs(n)>=Decimal("100000"):return f"₹{(n/Decimal('100000')).quantize(Decimal('0.01')).normalize():f} Lakh"
    i=int(n.quantize(Decimal("1")));s=str(abs(i));last=s[-3:];head=s[:-3];parts=[]
    while head:parts.insert(0,head[-2:]);head=head[:-2]
    return "₹"+("-" if i<0 else "")+((",".join(parts)+",") if parts else "")+last

def safe_list(v):
    if v is None:return []
    if isinstance(v,(list,tuple,set)):return [str(x) for x in v if x is not None]
    if isinstance(v,dict):return [str(k) for k,val in v.items() if val]
    if isinstance(v,str):
        s=v.strip()
        if not s:return []
        try:return safe_list(json.loads(s))
        except Exception:return [x.strip() for x in s.split(",") if x.strip()]
    return [str(v)]

def area_options(sel="SQFT"):
    s=str(sel or "SQFT").upper();return "".join(f'<option value="{k}" {"selected" if k==s else ""}>{v}</option>' for k,v in LABELS.items())

def tx_options(sel="LEASE"):
    s=str(sel or "LEASE").upper().replace("SALE + LEASE","BOTH")
    return f'<option value="LEASE" {"selected" if s=="LEASE" else ""}>LEASE</option><option value="SALE" {"selected" if s=="SALE" else ""}>SALE</option><option value="BOTH" {"selected" if s=="BOTH" else ""}>SALE + LEASE</option>'

def type_checks(selected=()):
    sel=set(safe_list(selected));return "".join(f'<label><input type="checkbox" name="property_types" value="{h(x)}" {"checked" if x in sel else ""}> {h(x)}</label>' for x in PROPERTY_TYPES)

def _setup(core):
    for stmt in [
        "ALTER TABLE pi_operational_properties ADD COLUMN IF NOT EXISTS area_value NUMERIC(14,2)",
        "ALTER TABLE pi_operational_properties ADD COLUMN IF NOT EXISTS area_unit TEXT",
        "ALTER TABLE pi_operational_properties ADD COLUMN IF NOT EXISTS sale_amount NUMERIC(18,2)",
        "ALTER TABLE pi_operational_properties ADD COLUMN IF NOT EXISTS rent_input_text TEXT",
        "ALTER TABLE pi_operational_properties ADD COLUMN IF NOT EXISTS sale_input_text TEXT",
        "ALTER TABLE pi_operational_properties ALTER COLUMN rent_amount DROP NOT NULL",
        "UPDATE pi_operational_properties SET area_value=area_sqft,area_unit='SQFT' WHERE area_value IS NULL AND area_sqft IS NOT NULL",
    ]:
        try:
            with core.engine.begin() as c:c.execute(text(stmt))
        except Exception as exc:print("[v190 schema]",type(exc).__name__,str(exc))

def _remove_routes(app):
    owned={
      ("/manual-property-database-v178","GET"),("/manual-property-v18","GET"),("/edit-property/{property_code}","GET"),
      ("/fast-property-entry","GET"),("/capture-intelligence","GET"),("/newspaper","GET"),("/newspaper-upload","GET"),("/magazine-capture","GET"),
      ("/api/v19/property","POST"),("/api/v19/property/{property_code}/edit","POST"),("/api/v19/property-ops/status","GET")}
    kept=[];removed=[]
    for r in app.router.routes:
        p=getattr(r,"path",None);m=set(getattr(r,"methods",set()) or set())
        if any(p==path and method in m for path,method in owned):removed.append(p)
        else:kept.append(r)
    app.router.routes[:]=kept;return removed

async def _save_media(core,code,files,kind,max_mb):
    saved=0;errors=[]
    for f in files or []:
        if not f or not getattr(f,"filename",None):continue
        try:
            data=await f.read();mime=f.content_type or "application/octet-stream"
            if len(data)>max_mb*1024*1024:errors.append(f"{f.filename}: exceeds {max_mb} MB");continue
            if kind=="IMAGE" and not mime.startswith("image/"):errors.append(f"{f.filename}: not an image");continue
            if kind=="VIDEO" and not mime.startswith("video/"):errors.append(f"{f.filename}: not a video");continue
            if kind=="BROCHURE" and mime!="application/pdf" and not f.filename.lower().endswith(".pdf"):errors.append(f"{f.filename}: brochure must be PDF");continue
            with core.engine.begin() as c:c.execute(text("INSERT INTO pi_operational_property_media(property_code,media_type,filename,mime_type,file_size,content) VALUES(:p,:t,:f,:m,:s,:b)"),{"p":code,"t":kind,"f":f.filename,"m":mime,"s":len(data),"b":data})
            saved+=1
        except Exception as exc:errors.append(f"{f.filename}: {type(exc).__name__}: {exc}")
    return saved,errors

def _property_form(division,p=None,edit=False):
    p=dict(p or {});pts=safe_list(p.get("property_types"));av=p.get("area_value") if p.get("area_value") is not None else p.get("area_sqft");au=str(p.get("area_unit") or "SQFT").upper();tx=str(p.get("transaction_type") or "LEASE").upper()
    sale=p.get("sale_input_text") or (str(p.get("sale_amount")) if p.get("sale_amount") is not None else "");rent=p.get("rent_input_text") or (str(p.get("rent_amount")) if p.get("rent_amount") is not None else "")
    endpoint=f"/api/v19/property/{h(p.get('property_code'))}/edit" if edit else "/api/v19/property";title="Edit Property" if edit else "Add Manual Property"
    return f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>{title}</title><style>{CSS}</style></head><body><header><b>{title}</b><br><small>Sale/Rent | Area Units | Pre-Rented | Upload / Drag / Paste Photos</small></header><div class=w><form id=f class=card><input type=hidden name=division value='{h(division)}'><div class=grid>
<label><small>Property Name</small><input name=property_name value='{h(p.get('property_name'))}'></label><label><small>City</small><input name=city value='{h(p.get('city'))}'></label><label><small>Location *</small><input required name=location value='{h(p.get('location'))}'></label><label><small>Google Location</small><input name=google_location value='{h(p.get('google_location'))}'></label>
<label><small>Area Value *</small><input required name=area_value value='{h(av)}' placeholder='500'></label><label><small>Area Unit *</small><select name=area_unit>{area_options(au)}</select></label><label><small>Transaction *</small><select name=transaction_type id=tx>{tx_options(tx)}</select></label>
<label id=saleBox><small>Sale Amount</small><input name=sale_amount_text value='{h(sale)}' placeholder='8 lakh / 2.5 cr / 9500000'></label><label id=rentBox><small>Rent Amount</small><input name=rent_amount_text value='{h(rent)}' placeholder='8 lakh / 100 per sqft / 500000'></label><label><small>Rent Basis</small><select name=rent_basis><option value=PER_MONTH>Per Month</option><option value=PER_SQFT>Per Sq Ft</option><option value=PER_SQYD>Per Sq Yd</option><option value=PER_SQMTR>Per Sq Mtr</option></select></label>
<label><small>Floor</small><input name=floor value='{h(p.get('floor'))}'></label><label><small>Frontage</small><input name=frontage value='{h(p.get('frontage'))}'></label><label><small>Parking</small><input name=parking value='{h(p.get('parking'))}'></label><label><small>Possession</small><input name=possession value='{h(p.get('possession'))}'></label><label><small>Suitable For</small><input name=suitable_for value='{h(p.get('suitable_for'))}'></label><label><small>Nearby Brands</small><input name=nearby_brands value='{h(p.get('nearby_brands'))}'></label>
<label><small>Owner / Broker Name</small><input name=owner_broker_name value='{h(p.get('owner_broker_name'))}'></label><label><small>Contact Number</small><input name=contact_number value='{h(p.get('contact_number'))}'></label><label><small>Contact Role</small><select name=contact_role><option>OWNER</option><option>BROKER</option><option>BUILDER</option><option>UNVERIFIED</option></select></label><label><small>Verification</small><select name=verification_status><option {'selected' if str(p.get('verification_status') or '').upper()=='UNVERIFIED' else ''}>UNVERIFIED</option><option {'selected' if str(p.get('verification_status') or '').upper()=='VERIFIED' else ''}>VERIFIED</option></select></label></div>
<h3>Property Type *</h3><div class=types>{type_checks(pts)}</div><h3>Remarks</h3><textarea name=remarks>{h(p.get('remarks'))}</textarea><h3>Property Media</h3><p class=hint>Photos: click Upload, drag files, or click the box and press Ctrl+V after copying an image.</p><div class=grid><div><div class=drop id=dzImages tabindex=0><b>Photos</b><br>Upload | Drag & Drop | Ctrl+V<input id=images type=file name=images accept='image/*' multiple><div id=prevImages></div></div></div><div><div class=drop id=dzVideos tabindex=0><b>Videos</b><br>Upload | Drag & Drop<input id=videos type=file name=videos accept='video/*' multiple><div id=prevVideos></div></div></div><div><div class=drop id=dzBrochure tabindex=0><b>Brochure PDF</b><br>Upload | Drag & Drop<input id=brochure type=file name=brochure accept='.pdf,application/pdf'><div id=prevBrochure></div></div></div></div><p><button>{'Save Changes' if edit else 'Save Property'}</button> <a class='btn gray' href='/manual-property-database-v178'>Manual Database</a> <b id=msg></b></p></form></div><script>{DROP_JS}wireDrop('dzImages','images','prevImages',true);wireDrop('dzVideos','videos','prevVideos',false);wireDrop('dzBrochure','brochure','prevBrochure',false);function txUI(){{const v=tx.value;rentBox.style.display=(v==='LEASE'||v==='BOTH')?'':'none';saleBox.style.display=(v==='SALE'||v==='BOTH')?'':'none'}}tx.onchange=txUI;txUI();f.onsubmit=async e=>{{e.preventDefault();msg.className='';msg.textContent='Saving...';try{{const r=await fetch('{endpoint}',{{method:'POST',body:new FormData(f)}});const raw=await r.text();let d={{}};try{{d=JSON.parse(raw)}}catch(_){{}}if(!r.ok)throw new Error(d.detail||d.message||raw||'Save failed');if(d.duplicate){{msg.className='err';msg.textContent='Possible duplicate: '+d.property_code;return}}location.href='/manual-property-database-v178'}}catch(x){{msg.className='err';msg.textContent='ERROR: '+x.message}}}}</script></body></html>"""

async def _save_property(core,req,code,division,property_name,property_types,city,location,google_location,area_value,area_unit,transaction_type,sale_amount_text,rent_amount_text,rent_basis,floor,frontage,parking,possession,suitable_for,nearby_brands,owner_broker_name,contact_number,contact_role,verification_status,remarks,images,videos,brochure,edit):
    av,au,asq=parse_area(area_value,area_unit);sale,sale_raw=parse_money(sale_amount_text);rent,rent_raw=parse_money(rent_amount_text);tx=str(transaction_type or "LEASE").upper().replace("SALE + LEASE","BOTH")
    if tx not in {"LEASE","SALE","BOTH"}:raise HTTPException(400,"Invalid transaction.")
    if tx=="LEASE":sale=None;sale_raw=""
    if tx=="SALE":rent=None;rent_raw=""
    pts=[str(x).strip() for x in property_types if str(x).strip()]
    if not pts:raise HTTPException(400,"Select at least one property type.")
    if edit:
        with core.engine.begin() as c:
            if not c.execute(text("SELECT 1 FROM pi_operational_properties WHERE property_code=:p"),{"p":code}).first():raise HTTPException(404,"Property not found.")
            c.execute(text("""UPDATE pi_operational_properties SET division=:d,property_name=:n,property_types=CAST(:pt AS JSONB),city=:c,location=:l,google_location=:g,area_sqft=:asq,area_value=:av,area_unit=:au,rent_amount=:rent,sale_amount=:sale,rent_input_text=:rr,sale_input_text=:sr,rent_unit=:rb,transaction_type=:tx,floor=:fl,frontage=:fr,parking=:pa,possession=:po,suitable_for=:su,nearby_brands=:nb,owner_broker_name=:ob,contact_number=:cn,contact_role=:cr,verification_status=:vs,remarks=:rm,updated_at=NOW() WHERE property_code=:p"""),{"d":division,"n":property_name,"pt":json.dumps(pts),"c":city,"l":location,"g":google_location,"asq":asq,"av":av,"au":au,"rent":rent,"sale":sale,"rr":rent_raw,"sr":sale_raw,"rb":rent_basis,"tx":tx,"fl":floor,"fr":frontage,"pa":parking,"po":possession,"su":suitable_for,"nb":nearby_brands,"ob":owner_broker_name,"cn":contact_number,"cr":contact_role,"vs":verification_status,"rm":remarks,"p":code})
    else:
        with core.engine.connect() as c:dup=c.execute(text("""SELECT property_code FROM pi_operational_properties WHERE lower(trim(coalesce(city,'')))=lower(trim(:c)) AND lower(trim(coalesce(location,'')))=lower(trim(:l)) AND abs(coalesce(area_sqft,0)-:a)<=1 AND lower(trim(coalesce(property_name,'')))=lower(trim(:n)) LIMIT 1"""),{"c":city,"l":location,"a":asq,"n":property_name}).first()
        if dup:return {"status":"duplicate","duplicate":True,"property_code":dup[0]}
        code="PROP-"+datetime.now().strftime("%Y%m%d%H%M%S")+"-"+uuid.uuid4().hex[:6].upper()
        with core.engine.begin() as c:c.execute(text("""INSERT INTO pi_operational_properties(property_code,division,source_system,property_name,property_types,city,location,google_location,area_sqft,area_value,area_unit,rent_amount,sale_amount,rent_input_text,sale_input_text,rent_unit,transaction_type,floor,frontage,parking,possession,suitable_for,nearby_brands,owner_broker_name,contact_number,contact_role,verification_status,remarks,created_by) VALUES(:p,:d,'MANUAL',:n,CAST(:pt AS JSONB),:c,:l,:g,:asq,:av,:au,:rent,:sale,:rr,:sr,:rb,:tx,:fl,:fr,:pa,:po,:su,:nb,:ob,:cn,:cr,:vs,:rm,:by)"""),{"p":code,"d":division,"n":property_name,"pt":json.dumps(pts),"c":city,"l":location,"g":google_location,"asq":asq,"av":av,"au":au,"rent":rent,"sale":sale,"rr":rent_raw,"sr":sale_raw,"rb":rent_basis,"tx":tx,"fl":floor,"fr":frontage,"pa":parking,"po":possession,"su":suitable_for,"nb":nearby_brands,"ob":owner_broker_name,"cn":contact_number,"cr":contact_role,"vs":verification_status,"rm":remarks,"by":_actor(core,req)})
    im,ie=await _save_media(core,code,images,"IMAGE",12);vi,ve=await _save_media(core,code,videos,"VIDEO",100);br,be=await _save_media(core,code,[brochure] if brochure and brochure.filename else [],"BROCHURE",40)
    return {"status":"ok","property_code":code,"images_saved":im,"videos_saved":vi,"brochures_saved":br,"media_errors":ie+ve+be}

CAPTURE_UI = r"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Newspaper / Magazine Upload</title><style>""" + CSS + r"""</style></head><body><header><b>Newspaper / Magazine Upload</b><br><small>Uploaded -> AI Extracting -> Validating -> Completed</small></header><div class=w><div class=card><a class='btn gray' href='/workspace'>Back to Dashboard</a> <a class='btn gray' href='/manual-property-database-v178'>Manual Database</a></div><form id=cap class=card><div class=grid><label><small>Source Type</small><select name=source_type id=st><option>NEWSPAPER</option><option>MAGAZINE</option><option>PHOTO</option></select></label><label><small>Optional Note</small><input name=note placeholder='e.g. Property Informer Sep 2026 / Page 12'></label></div><input type=hidden name=capture_mode value='UPLOAD_DRAG_DROP_PASTE'><div id=capDrop class=drop tabindex=0><b>Choose / Drop Newspaper or Magazine</b><p>Copied image: click here and press Ctrl+V</p><p class=hint>PDF, JPG, PNG, WEBP</p><input id=capFile name=file type=file accept='.pdf,application/pdf,image/*'><div id=capPrev></div></div><p><button id=capGo>Upload & Extract</button></p><div id=capMsg class=stage>Ready.</div></form><div class=card><h3>AI Extraction Status</h3><div id=jobs>Loading...</div></div></div><script>""" + DROP_JS + r"""
wireDrop('capDrop','capFile','capPrev',true);
function stage(x){const s=String(x.status||'').toUpperCase();if(['ACCEPTED','PENDING','QUEUED'].includes(s))return '1. Uploaded / Queued';if(['RUNNING','PROCESSING','EXTRACTING'].includes(s))return '2. AI Extracting';if(['VALIDATING','REVIEW'].includes(s))return '3. Validating';if(['COMPLETED','DONE','SUCCESS','PROCESSED'].includes(s))return '4. Completed';if(['FAILED','ERROR'].includes(s))return 'Failed';return s||'Processing'}
async function loadJobs(){try{const r=await fetch('/api/v10/intake/status');const d=await r.json();const a=(d.rows||[]).filter(x=>['NEWSPAPER','MAGAZINE'].includes(String(x.source_type||'').toUpperCase())).slice(0,12);jobs.innerHTML=a.length?a.map(x=>`<div class="stage"><b>${esc(stage(x))}</b> | ${esc(x.original_filename||'')}<br>New properties: <b>${esc(x.processed_records||0)}</b> | Duplicates: ${esc(x.duplicate_records||0)}${x.output_summary?'<br>'+esc(x.output_summary):''}${x.error_message?'<br><span class="err">'+esc(x.error_message)+'</span>':''}</div>`).join(''):'No newspaper/magazine jobs yet.'}catch(x){jobs.innerHTML='<span class="err">Status failed: '+esc(x.message)+'</span>'}}
cap.onsubmit=async e=>{e.preventDefault();if(!capFile.files.length){capMsg.className='stage err';capMsg.textContent='Choose, drop or paste a file first.';return}capGo.disabled=true;capMsg.className='stage';capMsg.textContent='1. Uploading...';try{const r=await fetch('/api/v10/intake/file',{method:'POST',body:new FormData(cap)});const raw=await r.text();let d={};try{d=JSON.parse(raw)}catch(_){}if(!r.ok)throw new Error(d.detail||d.message||raw||('HTTP '+r.status));capMsg.className='stage ok';capMsg.textContent='Uploaded. AI extraction is now running. Job ID: '+(d.job_id||'created');await loadJobs()}catch(x){capMsg.className='stage err';capMsg.textContent='UPLOAD FAILED: '+x.message}finally{capGo.disabled=false}};
const src=new URLSearchParams(location.search).get('source_type');if(src)st.value=src.toUpperCase();loadJobs();setInterval(loadJobs,5000);
</script></body></html>"""

def register(wrapped):
    core=wrapped.core;app=wrapped.app;_setup(core);removed=_remove_routes(app)

    @app.get('/manual-property-v18',response_class=HTMLResponse)
    def add_property(req:Request,division:str=Query('DELHI_NCR')):
        r=_auth(core,req);return r or HTMLResponse(_property_form(division.upper()))

    @app.post('/api/v19/property')
    async def add_property_api(req:Request,division:str=Form('DELHI_NCR'),property_name:str=Form(''),property_types:list[str]=Form([]),city:str=Form(''),location:str=Form(''),google_location:str=Form(''),area_value:str=Form(''),area_unit:str=Form('SQFT'),transaction_type:str=Form('LEASE'),sale_amount_text:str=Form(''),rent_amount_text:str=Form(''),rent_basis:str=Form('PER_MONTH'),floor:str=Form(''),frontage:str=Form(''),parking:str=Form(''),possession:str=Form(''),suitable_for:str=Form(''),nearby_brands:str=Form(''),owner_broker_name:str=Form(''),contact_number:str=Form(''),contact_role:str=Form('UNVERIFIED'),verification_status:str=Form('UNVERIFIED'),remarks:str=Form(''),images:list[UploadFile]=File([]),videos:list[UploadFile]=File([]),brochure:UploadFile|None=File(None)):
        return await _save_property(core,req,None,division,property_name,property_types,city,location,google_location,area_value,area_unit,transaction_type,sale_amount_text,rent_amount_text,rent_basis,floor,frontage,parking,possession,suitable_for,nearby_brands,owner_broker_name,contact_number,contact_role,verification_status,remarks,images,videos,brochure,False)

    @app.get('/edit-property/{property_code}',response_class=HTMLResponse)
    def edit_property(property_code:str,req:Request):
        r=_auth(core,req)
        if r:return r
        with core.engine.connect() as c:row=c.execute(text('SELECT * FROM pi_operational_properties WHERE property_code=:p'),{'p':property_code}).mappings().first()
        if not row:raise HTTPException(404,'Property not found.')
        return HTMLResponse(_property_form(str(row.get('division') or 'DELHI_NCR'),row,True))

    @app.post('/api/v19/property/{property_code}/edit')
    async def edit_property_api(property_code:str,req:Request,division:str=Form('DELHI_NCR'),property_name:str=Form(''),property_types:list[str]=Form([]),city:str=Form(''),location:str=Form(''),google_location:str=Form(''),area_value:str=Form(''),area_unit:str=Form('SQFT'),transaction_type:str=Form('LEASE'),sale_amount_text:str=Form(''),rent_amount_text:str=Form(''),rent_basis:str=Form('PER_MONTH'),floor:str=Form(''),frontage:str=Form(''),parking:str=Form(''),possession:str=Form(''),suitable_for:str=Form(''),nearby_brands:str=Form(''),owner_broker_name:str=Form(''),contact_number:str=Form(''),contact_role:str=Form('UNVERIFIED'),verification_status:str=Form('UNVERIFIED'),remarks:str=Form(''),images:list[UploadFile]=File([]),videos:list[UploadFile]=File([]),brochure:UploadFile|None=File(None)):
        return await _save_property(core,req,property_code,division,property_name,property_types,city,location,google_location,area_value,area_unit,transaction_type,sale_amount_text,rent_amount_text,rent_basis,floor,frontage,parking,possession,suitable_for,nearby_brands,owner_broker_name,contact_number,contact_role,verification_status,remarks,images,videos,brochure,True)

    @app.get('/manual-property-database-v178',response_class=HTMLResponse)
    def manual_db(req:Request,division:str=Query('ALL')):
        r=_auth(core,req)
        if r:return r
        d=division.upper();params={} if d=='ALL' else {'d':d};where='' if d=='ALL' else 'WHERE p.division=:d'
        with core.engine.connect() as c:rows=c.execute(text(f"""SELECT p.*,(SELECT count(*) FROM pi_operational_property_media m WHERE m.property_code=p.property_code AND m.media_type='IMAGE') image_count,(SELECT count(*) FROM pi_operational_property_media m WHERE m.property_code=p.property_code AND m.media_type='VIDEO') video_count FROM pi_operational_properties p {where} ORDER BY p.created_at DESC NULLS LAST LIMIT 5000"""),params).mappings().all()
        data=[dict(x) for x in rows];total=len(data);verified=sum(str(x.get('verification_status') or '').upper()=='VERIFIED' for x in data);photos=sum(int(x.get('image_count') or 0) for x in data);videos=sum(int(x.get('video_count') or 0) for x in data);trs=[]
        for i,x in enumerate(data,1):
            try:
                av=x.get('area_value') if x.get('area_value') is not None else x.get('area_sqft');au=LABELS.get(str(x.get('area_unit') or 'SQFT').upper(),'Sq Ft');tx=str(x.get('transaction_type') or '').upper();pts=safe_list(x.get('property_types'));cls='verified' if str(x.get('verification_status') or '').upper()=='VERIFIED' else 'unverified'
                trs.append(f"""<tr><td>{i}</td><td><b>{h(x.get('property_name') or x.get('property_code'))}</b><br><small>{h(x.get('property_code'))}</small></td><td>{h(str(x.get('created_at') or '')[:16])}</td><td><span class='pill {cls}'>{h(x.get('verification_status'))}</span></td><td>{h(', '.join(pts))}</td><td>{h(x.get('city'))}</td><td><b>{h(x.get('location'))}</b></td><td>{h(av)}</td><td>{h(au)}</td><td><b>{h('SALE + LEASE' if tx=='BOTH' else tx)}</b></td><td>{money(x.get('sale_amount')) if tx in {'SALE','BOTH'} else '—'}</td><td>{money(x.get('rent_amount')) if tx in {'LEASE','BOTH'} else '—'}</td><td>{h(x.get('owner_broker_name'))}<br><b>{h(x.get('contact_number'))}</b></td><td>Photos {x.get('image_count') or 0} | Videos {x.get('video_count') or 0}</td><td><a class='btn green' href='/edit-property/{h(x.get('property_code'))}'>Edit</a></td></tr>""")
            except Exception as exc:trs.append(f"<tr><td>{i}</td><td colspan='14' class='err'>Display error for {h(x.get('property_code'))}: {h(type(exc).__name__)}: {h(exc)}</td></tr>")
        return HTMLResponse(f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Manual Property Database</title><style>{CSS}</style></head><body><header><b>Manual Property Database</b><br><small>Sale Amount | Rent Amount | Original Area Unit | Media</small></header><div class=w><div class=toolbar><a class='btn gray' href='/workspace'>Back to Dashboard</a><a class=btn href='/manual-property-v18?division=DELHI_NCR'>Add Delhi NCR Property</a><a class=btn href='/manual-property-v18?division=GOA'>Add Goa Property</a></div><div class=kpis><div class=kpi><b>{total}</b>Total</div><div class=kpi><b>{verified}</b>Verified</div><div class=kpi><b>{total-verified}</b>Unverified</div><div class=kpi><b>{photos}</b>Photos</div><div class=kpi><b>{videos}</b>Videos</div><div class=kpi><b>V19</b>Stable Surface</div></div><div class=tablewrap><table><thead><tr><th>#</th><th>Property / Code</th><th>Entry Date</th><th>Verification</th><th>Property Type</th><th>City</th><th>Location</th><th>Area</th><th>Unit</th><th>Transaction</th><th>Sale Amount</th><th>Rent Amount</th><th>Contact</th><th>Media</th><th>Action</th></tr></thead><tbody>{''.join(trs) or "<tr><td colspan='15'>No properties found.</td></tr>"}</tbody></table></div></div></body></html>""")

    @app.get('/capture-intelligence',response_class=HTMLResponse)
    def capture(req:Request):
        r=_auth(core,req);return r or HTMLResponse(CAPTURE_UI)

    @app.get('/newspaper')
    def newspaper(req:Request):
        r=_auth(core,req);return r or RedirectResponse('/capture-intelligence?source_type=NEWSPAPER',307)

    @app.get('/newspaper-upload')
    def newspaper_upload(req:Request):
        r=_auth(core,req);return r or RedirectResponse('/capture-intelligence?source_type=NEWSPAPER',307)

    @app.get('/magazine-capture')
    def magazine_capture(req:Request):
        r=_auth(core,req);return r or RedirectResponse('/capture-intelligence?source_type=MAGAZINE',307)

    @app.get('/fast-property-entry')
    def fast_property_entry(req:Request,division:str=Query('DELHI_NCR')):
        r=_auth(core,req);return r or RedirectResponse('/manual-property-v18?division='+division.upper(),307)

    @app.get('/api/v19/property-ops/status')
    def status():return {'status':'READY','version':VERSION,'removed_routes':removed,'sale_column':True,'photo_clipboard_paste':True,'newspaper_status_polling':True}

    return {'status':'REGISTERED','version':VERSION,'removed_routes':removed}
