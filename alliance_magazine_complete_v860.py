from __future__ import annotations
import html, json, re
from fastapi import Body, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

VERSION="8.6.0-COMPLETE-MAGAZINE-DATABASE"

MOBILE_RE=re.compile(r"(?<!\d)([6-9]\d{9})(?!\d)")
LANDLINE_RE=re.compile(r"(?<!\d)(0?11[-\s]?\d{7,8}(?:/\d(?:/\d)*)?)(?!\d)")
PHONE_RE=re.compile(r"(?<!\d)(?:[6-9]\d{9}|0?11[-\s]?\d{7,8}(?:/\d(?:/\d)*)?)(?!\d)")
URL_RE=re.compile(r"(?i)https?://\S+|www\.\S+")
EMAIL_RE=re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
ROLE_RE=re.compile(r"(?i)\b(BUILDER|BROKER|OWNER|DEVELOPER|REALTOR|AGENT)\b")
PROP_RE=re.compile(r"(?i)(?:\d{2,7}\s*(?:Y|YD|SQYD|FT|SQFT|MTR|SQM|ACRE)|\d+\s*(?:BHK|BR)|GF|FF|SF|TF|BASEMENT|BMT|APT|APARTMENT|FLAT|KOTHI|VILLA|PLOT|OFFICE|SHOP|SHOWROOM|WAREHOUSE|GODOWN|FACTORY|FARM)")
PROMO_RE=re.compile(r"(?i)\b(?:ESTATES?\s+PVT|PVT\.?\s*LTD|INTERIOR|RENOVATION|COLLABORATION|REALTORS?|REALTY|PROPERTY\s+DEALER|BUILDERS?|DEVELOPERS?)\b")

# Broad Delhi/NCR + common CRE locality dictionary. Unknown values remain reviewable, never invented.
LOCATIONS=[
"ARADHNA ENCLAVE","ARADHANA ENCLAVE","ALAKNANDA","ANAND LOK","ANAND NIKETAN","ASIAD VILLAGE",
"BHIKAJI CAMA PLACE","CHANAKYAPURI","CHATTERPUR ENCLAVE","CHHATARPUR ENCLAVE","CHIRAG DELHI",
"CHITRANJAN PARK","CR PARK","C R PARK","CONNAUGHT PLACE","DEFENCE COLONY","DERA MANDI","DWARKA",
"EAST OF KAILASH","FEROZE SHAH ROAD","FRIENDS COLONY","GADIPUR","GADIPUR FARMS","GAUTAM NAGAR",
"GOLF LINKS","GREATER KAILASH-1","GREATER KAILASH-2","GREATER KAILASH I","GREATER KAILASH II",
"GREATER KAILASH","GREEN PARK EXTN","GREEN PARK","GURGAON","GURUGRAM","HANUMAN ROAD","HAUZ KHAS",
"JASOLA","JOR BAGH","KAILASH COLONY","LAJPAT NAGAR","MAHARANI BAGH","MALCHA MARG","MALVIYA NAGAR",
"MOHAN CO-OPERATIVE","NEW FRIENDS COLONY","NITI BAGH","NIZAMUDDIN EAST","NIZAMUDDIN WEST","NIZAMUDDIN",
"NOIDA","OKHLA -2","OKHLA-2","PANCHSHEEL ENCLAVE","PANCHSHEEL PARK","PITAMPURA","PRITHVIRAJ LANE",
"PRITHVIRAJ ROAD","RAJDOOT MARG","ROHINI","SAFDARJUNG DEVELOPMENT AREA","SAFDARJUNG ENCLAVE",
"SAINIK FARM","SAKET","SARVODAYA ENCLAVE","SHANTI NIKETAN","SOUTH EXTENSION","SULTANPUR FARMS",
"SUNDER NAGAR","TUGHLAKABAD EXTN","VASANT KUNJ","VASANT VIHAR"
]
BAD_HEADINGS={"TARA","ESTATE","ESTATES","ESTATES PVT LTD","ESTATES PVT. LTD."}
ALIASES={"ARADHANA ENCLAVE":"ARADHNA ENCLAVE","CR PARK":"CHITRANJAN PARK","C R PARK":"CHITRANJAN PARK",
"GURGAON":"GURUGRAM","CHHATARPUR ENCLAVE":"CHATTERPUR ENCLAVE","OKHLA -2":"OKHLA-2"}

def _app(core): return getattr(core,"app",None) or core
def _engine(core): return getattr(core,"engine",None)
def _login(core,req):
    fn=getattr(core,"need_login",None); return fn(req) if fn else "team"
def _actor(core,req):
    try:return str(_login(core,req) or "team")
    except Exception:return "team"
def _esc(v):return html.escape("" if v is None else str(v))
def _canon(v):
    u=re.sub(r"\s+"," ",(v or "").upper().strip(" -:|"))
    return ALIASES.get(u,u)

def _location_from_text(v):
    u=(v or "").upper()
    for loc in sorted(LOCATIONS,key=len,reverse=True):
        if re.search(r"(?<![A-Z])"+re.escape(loc)+r"(?![A-Z])",u):
            return _canon(loc)
    return None

def _valid_heading(v):
    s=_canon(v)
    if not s or s in BAD_HEADINGS or PROMO_RE.search(s):return None
    if s.startswith(("RESIDENTIAL","COMMERCIAL","INDUSTRIAL","FARMHOUSE","FARM HOUSES")):return None
    return s if any(_canon(x)==s for x in LOCATIONS) else None

def _phones(s):
    mobiles=list(dict.fromkeys(MOBILE_RE.findall(s or "")))
    lands=list(dict.fromkeys(m.group(1).strip() for m in LANDLINE_RE.finditer(s or "")))
    return mobiles,lands

def _contact_block(s):
    phones=list(PHONE_RE.finditer(s or ""))
    if not phones:return None
    p=phones[-1]; l=(s or "").rfind("(",0,p.start()); r=(s or "").find(")",p.end())
    return (l,r+1,(s or "")[l+1:r]) if l>=0 and r>=0 else None

def _contact(s):
    b=_contact_block(s)
    if not b:return None,None
    t=PHONE_RE.sub(" ",b[2]); role=ROLE_RE.search(t)
    role=role.group(1).upper() if role else None
    t=ROLE_RE.sub(" ",t);t=re.sub(r"[()/,:;|]+"," ",t);t=re.sub(r"\s+"," ",t).strip(" -")
    return t or None,role

def _clean(s):
    x=(s or "").strip();b=_contact_block(x)
    if b:x=(x[:b[0]]+" "+x[b[1]:]).strip()
    x=PHONE_RE.sub(" ",x);x=URL_RE.sub(" ",x);x=EMAIL_RE.sub(" ",x)
    x=re.sub(r"\(\s*\)"," ",x);x=re.sub(r"\s+"," ",x).strip(" ,;|-")
    return x

def _category_context(section,original,existing_tx):
    # Highest confidence: retained source section/category text.
    blob=" | ".join(str(x or "") for x in [section,original])
    u=blob.upper()
    asset=None
    if re.search(r"\bFARM\s*HOUSES?\b|\bFARMHOUSE\b|\bSAINIK FARM\b|\bDERA MANDI\b|\bGADIPUR FARMS?\b|\bSULTANPUR FARMS?\b",u):asset="Farmhouse"
    elif re.search(r"\bINDUSTRIAL\b|\bFACTORY\b|\bINDUSTRIAL AREA\b",u):asset="Industrial"
    elif re.search(r"\bCOMMERCIAL\b|\bOFFICE\b|\bSHOWROOM\b|\bSHOP\b|\bRETAIL\b|\bMALL\b",u):asset="Commercial"
    elif re.search(r"\bRESIDENTIAL\b|\bBHK\b|\bAPARTMENT\b|\bAPT\b|\bFLAT\b|\bKOTHI\b|\bVILLA\b",u):asset="Residential"

    tx=None
    if re.search(r"\bRENT\b|\bLEASE\b|\bLEASING\b",u) or str(existing_tx or "").upper() in {"RENT","LEASE"}:tx="Rent"
    elif re.search(r"\bSALE\b|\bRESALE\b|\bSELL\b",u) or str(existing_tx or "").upper()=="SALE":tx="Sale"

    # Explicit combined source text, including historical form "Rent | Commercial | locality".
    for a in ["Residential","Commercial","Industrial","Farmhouse"]:
        if re.search(r"(?i)\bRENT\b\s*\|\s*"+a+r"\b|\b"+a+r"\b\s*[-| ]+\s*\bRENT\b",blob):return a+" Rent","SOURCE_CONTEXT",100
        if re.search(r"(?i)\bSALE\b\s*\|\s*"+a+r"\b|\b"+a+r"\b\s*[-| ]+\s*\bSALE\b",blob):return a+" Sale","SOURCE_CONTEXT",100
    if asset and tx:return asset+" "+tx,"DERIVED_CONTEXT",90
    return None,"UNKNOWN",0

def _setup(e):
    with e.begin() as c:
        c.execute(text("""CREATE TABLE IF NOT EXISTS pi_magazine_complete_v860(
        id BIGSERIAL PRIMARY KEY,property_id TEXT UNIQUE NOT NULL,source_record_id TEXT UNIQUE NOT NULL,upload_id UUID,page_number INTEGER,
        location TEXT,description TEXT NOT NULL,property_category TEXT,property_type TEXT,area_value TEXT,area_unit TEXT,floor TEXT,
        amount TEXT,contact_name TEXT,contact_numbers JSONB NOT NULL DEFAULT '[]'::jsonb,contact_role TEXT,
        entry_datetime TIMESTAMPTZ DEFAULT NOW(),source_datetime TIMESTAMPTZ,verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
        verified_by TEXT,verified_at TIMESTAMPTZ,assigned_to TEXT,source TEXT DEFAULT 'Magazine',source_name TEXT,
        original_description TEXT NOT NULL,original_section TEXT,location_source TEXT,category_source TEXT,
        needs_review BOOLEAN NOT NULL DEFAULT TRUE,review_reason TEXT,record_status TEXT NOT NULL DEFAULT 'ACTIVE',
        archived_at TIMESTAMPTZ,archived_by TEXT,created_at TIMESTAMPTZ DEFAULT NOW(),updated_at TIMESTAMPTZ DEFAULT NOW())"""))
        c.execute(text("""CREATE TABLE IF NOT EXISTS pi_magazine_complete_history_v860(
        id BIGSERIAL PRIMARY KEY,property_id TEXT NOT NULL,action TEXT NOT NULL,actor TEXT,before_json JSONB,after_json JSONB,
        note TEXT,created_at TIMESTAMPTZ DEFAULT NOW())"""))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_magcomplete_loc ON pi_magazine_complete_v860(location)"))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_magcomplete_cat ON pi_magazine_complete_v860(property_category)"))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_magcomplete_verify ON pi_magazine_complete_v860(verification_status)"))

def _build(e,upload_id=None):
    q="""SELECT record_id,upload_id,page_number,section_heading,original_description,transaction_type,property_type,
    area_value,area_unit,floor,amount_raw,signal_score,needs_review FROM pi_magazine_fastlane_records"""
    p={}
    if upload_id:q+=" WHERE upload_id=CAST(:u AS UUID)";p["u"]=upload_id
    q+=" ORDER BY page_number,id"
    with e.connect() as c: rows=c.execute(text(q),p).mappings().all()

    last_loc=None; built=review=noise=0
    with e.begin() as c:
        for r in rows:
            original=r["original_description"] or ""; clean=_clean(original)
            explicit=_location_from_text(clean); heading=_valid_heading(r["section_heading"])
            if explicit: loc,ls=explicit,"DESCRIPTION";last_loc=loc
            elif heading: loc,ls=heading,"MAGAZINE_LOCATION_HEADING";last_loc=loc
            elif last_loc: loc,ls=last_loc,"CONTEXT_CARRY_FORWARD"
            else: loc,ls=None,"UNKNOWN"

            cat,cs,cc=_category_context(r["section_heading"],original,r["transaction_type"])
            mobiles,lands=_phones(original);name,role=_contact(original)
            contacts=list(dict.fromkeys(mobiles+lands))
            promo=(PROMO_RE.search((r["section_heading"] or "")) and not PROP_RE.search(clean))
            phoneonly=bool(re.fullmatch(r"[\s\d+()/.,-]+",original or ""))
            reasons=[]
            if not loc:reasons.append("MISSING_LOCATION")
            if not cat:reasons.append("MISSING_PROPERTY_CATEGORY")
            if bool(r["needs_review"]):reasons.append("SOURCE_NEEDS_REVIEW")
            if promo:reasons.append("PROMOTIONAL_NOISE")
            if phoneonly:reasons.append("CONTACT_ONLY_NOISE")
            if re.search(r"(?i)\bNOT CONFIRM(?:ED)?\b",original):reasons.append("NOT_CONFIRMED")
            status="NOISE" if promo or phoneonly else "ACTIVE"
            needs=bool(reasons)
            pid="MAG-"+str(r["record_id"])
            db=dict(pid=pid,rid=r["record_id"],uid=str(r["upload_id"]),pg=r["page_number"],loc=loc,desc=clean,cat=cat,
              ptype=r["property_type"],area=r["area_value"],unit=r["area_unit"],floor=r["floor"],amount=r["amount_raw"],name=name,
              contacts=json.dumps(contacts),role=role,orig=original,section=r["section_heading"],ls=ls,cs=cs,needs=needs,
              reason=", ".join(reasons) if reasons else None,status=status)
            c.execute(text("""INSERT INTO pi_magazine_complete_v860(property_id,source_record_id,upload_id,page_number,location,description,
            property_category,property_type,area_value,area_unit,floor,amount,contact_name,contact_numbers,contact_role,source_name,
            original_description,original_section,location_source,category_source,needs_review,review_reason,record_status)
            VALUES(:pid,:rid,CAST(:uid AS UUID),:pg,:loc,:desc,:cat,:ptype,:area,:unit,:floor,:amount,:name,CAST(:contacts AS JSONB),:role,
            'DELHI SEP-2026 Magazine',:orig,:section,:ls,:cs,:needs,:reason,:status)
            ON CONFLICT(source_record_id) DO UPDATE SET location=EXCLUDED.location,description=EXCLUDED.description,
            property_category=EXCLUDED.property_category,property_type=EXCLUDED.property_type,area_value=EXCLUDED.area_value,
            area_unit=EXCLUDED.area_unit,floor=EXCLUDED.floor,amount=EXCLUDED.amount,contact_name=EXCLUDED.contact_name,
            contact_numbers=EXCLUDED.contact_numbers,contact_role=EXCLUDED.contact_role,original_description=EXCLUDED.original_description,
            original_section=EXCLUDED.original_section,location_source=EXCLUDED.location_source,category_source=EXCLUDED.category_source,
            needs_review=EXCLUDED.needs_review,review_reason=EXCLUDED.review_reason,record_status=EXCLUDED.record_status,updated_at=NOW()"""),db)
            built+=status=="ACTIVE";noise+=status=="NOISE";review+=needs
    return {"processed":len(rows),"active_properties":int(built),"noise_kept_out_of_view":int(noise),"needs_review":int(review)}

def register(core):
    app=_app(core);e=_engine(core)
    if app is None or e is None:raise RuntimeError("8.6 requires app + engine")
    _setup(e)

    @app.post("/api/magazine-complete/build")
    def build(req:Request,upload_id:str|None=Query(None)):
        _login(core,req);return {"status":"BUILT","version":VERSION,"cost":0,"external_api_calls":0,**_build(e,upload_id)}

    @app.post("/api/magazine-complete/edit/{pid}")
    def edit(pid:str,req:Request,payload:dict=Body(...)):
        actor=_actor(core,req)
        allowed={"location","description","property_category","property_type","area_value","area_unit","floor","amount","contact_name","contact_numbers","verification_status","assigned_to","needs_review","review_reason"}
        ch={k:payload[k] for k in allowed if k in payload}
        if not ch:raise HTTPException(400,"No editable fields")
        with e.begin() as c:
            before=c.execute(text("SELECT * FROM pi_magazine_complete_v860 WHERE property_id=:p AND archived_at IS NULL"),{"p":pid}).mappings().first()
            if not before:raise HTTPException(404,"Property not found")
            sets=[];params={"p":pid}
            for i,(k,v) in enumerate(ch.items()):
                key=f"v{i}"
                if k=="contact_numbers":
                    sets.append(f"{k}=CAST(:{key} AS JSONB)");params[key]=json.dumps(v if isinstance(v,list) else [x.strip() for x in str(v).split(",") if x.strip()])
                else:sets.append(f"{k}=:{key}");params[key]=v
            sets.append("updated_at=NOW()")
            c.execute(text("UPDATE pi_magazine_complete_v860 SET "+",".join(sets)+" WHERE property_id=:p"),params)
            after=c.execute(text("SELECT * FROM pi_magazine_complete_v860 WHERE property_id=:p"),{"p":pid}).mappings().first()
            c.execute(text("""INSERT INTO pi_magazine_complete_history_v860(property_id,action,actor,before_json,after_json)
            VALUES(:p,'EDIT',:a,CAST(:b AS JSONB),CAST(:n AS JSONB))"""),{"p":pid,"a":actor,"b":json.dumps(dict(before),default=str),"n":json.dumps(dict(after),default=str)})
        return {"status":"UPDATED","property_id":pid}

    @app.post("/api/magazine-complete/verify/{pid}")
    def verify(pid:str,req:Request,payload:dict=Body(...)):
        actor=_actor(core,req);s=str(payload.get("status","UNVERIFIED")).upper()
        allowed={"UNVERIFIED","VERIFICATION DUE","AVAILABLE","NOT AVAILABLE","FOLLOW-UP","CLOSED/REMOVED"}
        if s not in allowed:raise HTTPException(400,"Invalid status")
        with e.begin() as c:
            before=c.execute(text("SELECT * FROM pi_magazine_complete_v860 WHERE property_id=:p AND archived_at IS NULL"),{"p":pid}).mappings().first()
            if not before:raise HTTPException(404,"Property not found")
            c.execute(text("""UPDATE pi_magazine_complete_v860 SET verification_status=:s,verified_by=:a,
            verified_at=CASE WHEN :s='AVAILABLE' THEN NOW() ELSE verified_at END,updated_at=NOW() WHERE property_id=:p"""),{"s":s,"a":actor,"p":pid})
            after=c.execute(text("SELECT * FROM pi_magazine_complete_v860 WHERE property_id=:p"),{"p":pid}).mappings().first()
            c.execute(text("""INSERT INTO pi_magazine_complete_history_v860(property_id,action,actor,before_json,after_json,note)
            VALUES(:p,'VERIFY',:a,CAST(:b AS JSONB),CAST(:n AS JSONB),:s)"""),{"p":pid,"a":actor,"b":json.dumps(dict(before),default=str),"n":json.dumps(dict(after),default=str),"s":s})
        return {"status":"UPDATED","property_id":pid,"verification_status":s}

    @app.post("/api/magazine-complete/delete/{pid}")
    def delete(pid:str,req:Request):
        actor=_actor(core,req)
        with e.begin() as c:
            before=c.execute(text("SELECT * FROM pi_magazine_complete_v860 WHERE property_id=:p AND archived_at IS NULL"),{"p":pid}).mappings().first()
            if not before:raise HTTPException(404,"Property not found")
            c.execute(text("UPDATE pi_magazine_complete_v860 SET archived_at=NOW(),archived_by=:a,updated_at=NOW() WHERE property_id=:p"),{"a":actor,"p":pid})
            c.execute(text("""INSERT INTO pi_magazine_complete_history_v860(property_id,action,actor,before_json,note)
            VALUES(:p,'SOFT_DELETE',:a,CAST(:b AS JSONB),'Archived, original evidence retained')"""),{"p":pid,"a":actor,"b":json.dumps(dict(before),default=str)})
        return {"status":"ARCHIVED","property_id":pid}

    @app.get("/api/magazine-complete/history/{pid}")
    def history(pid:str,req:Request):
        _login(core,req)
        with e.connect() as c:rows=c.execute(text("SELECT action,actor,note,created_at FROM pi_magazine_complete_history_v860 WHERE property_id=:p ORDER BY id DESC LIMIT 100"),{"p":pid}).mappings().all()
        return {"property_id":pid,"history":[dict(x) for x in rows]}

    @app.get("/magazine-organizer",response_class=HTMLResponse)
    def page(req:Request,limit:int=Query(2500,ge=1,le=5000)):
        _login(core,req)
        with e.connect() as c:
            rows=c.execute(text("""SELECT property_id,location,description,property_category,property_type,area_value,area_unit,floor,amount,
            contact_name,contact_numbers,entry_datetime,verification_status,needs_review,review_reason,assigned_to,source,page_number
            FROM pi_magazine_complete_v860 WHERE archived_at IS NULL AND record_status='ACTIVE' ORDER BY page_number,id LIMIT :n"""),{"n":limit}).mappings().all()
        heads=["Property ID","Location","Description / Address","Property Category","Property Type","Area","Floor","Amount","Contact Name","Contact No.","Date & Time","Status","Verify","History","Assigned To","Source","Edit","Delete"]
        body=[]
        for r in rows:
            pid=_esc(r["property_id"]);area=" ".join(x for x in [str(r["area_value"] or ""),str(r["area_unit"] or "")] if x)
            dt=str(r["entry_datetime"])[:19] if r["entry_datetime"] else ""
            status=r["verification_status"]+(" · NEEDS REVIEW" if r["needs_review"] else "")
            vals=[pid,_esc(r["location"]),_esc(r["description"]),_esc(r["property_category"]),_esc(r["property_type"]),_esc(area),_esc(r["floor"]),
                  _esc(r["amount"]),_esc(r["contact_name"]),_esc(", ".join(r["contact_numbers"] or [])),_esc(dt),_esc(status)]
            tr="<tr>"+"".join("<td>"+x+"</td>" for x in vals)
            tr+=f"""<td><button class='mini' onclick="verifyRec('{pid}')">Verify</button></td><td><button class='mini' onclick="historyRec('{pid}')">History</button></td>
            <td>{_esc(r["assigned_to"])}</td><td>Magazine · p.{_esc(r["page_number"])}</td><td><button class='mini' onclick="editRec('{pid}')">Edit</button></td>
            <td><button class='del' onclick="deleteRec('{pid}')">Delete</button></td></tr>"""
            body.append(tr)
        table="<table><tr>"+"".join("<th>"+h+"</th>" for h in heads)+"</tr>"+("".join(body) if body else "<tr><td colspan=18>No records yet. Click Build Complete Database.</td></tr>")+"</table>"
        return HTMLResponse("""<!doctype html><html><head><meta charset='utf-8'><style>
        body{font-family:Arial;padding:18px;background:#f6f7f9;color:#17212b}.top{background:#fff;padding:16px;border-radius:12px;margin-bottom:12px}
        button{padding:8px 12px;background:#125bc5;color:white;border:0;border-radius:7px;font-weight:bold}.mini{padding:5px 8px}.del{padding:5px 8px;background:#a21d1d}
        table{width:100%;border-collapse:collapse;background:#fff;font-size:12px}th,td{padding:7px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top}
        th{background:#eef2f6;position:sticky;top:0}td:nth-child(3){min-width:270px}</style></head><body>
        <div class='top'><h2>Alliance Magazine Database · 8.6 Complete</h2>
        <p><b>All approved fields on one page.</b> Blank Location or Property Category is automatically Needs Review. Promotional noise stays out of the operational view. Original Description is immutable evidence.</p>
        <button onclick="build()">Build / Refresh Complete Database — Free</button> <span id='msg'></span></div>"""+table+"""
        <script>
        async function build(){msg.textContent=' Building...';let d=await (await fetch('/api/magazine-complete/build',{method:'POST'})).json();msg.textContent=' '+JSON.stringify(d);setTimeout(()=>location.reload(),800)}
        async function verifyRec(id){let s=prompt('UNVERIFIED, VERIFICATION DUE, AVAILABLE, NOT AVAILABLE, FOLLOW-UP, CLOSED/REMOVED','AVAILABLE');if(!s)return;await fetch('/api/magazine-complete/verify/'+id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:s})});location.reload()}
        async function editRec(id){let loc=prompt('Location');if(loc===null)return;let cat=prompt('Property Category: Residential Sale/Rent, Commercial Sale/Rent, Industrial Sale/Rent, Farmhouse Sale/Rent');if(cat===null)return;let desc=prompt('Description / Address (Original evidence will NOT change)');if(desc===null)return;let amount=prompt('Amount');let name=prompt('Contact Name');let phones=prompt('Contact No(s), comma separated');let assigned=prompt('Assigned To');let b={location:loc,property_category:cat,description:desc};if(amount!==null)b.amount=amount;if(name!==null)b.contact_name=name;if(phones!==null)b.contact_numbers=phones;if(assigned!==null)b.assigned_to=assigned;await fetch('/api/magazine-complete/edit/'+id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});location.reload()}
        async function deleteRec(id){if(!confirm('Archive this property? Original evidence will remain.'))return;await fetch('/api/magazine-complete/delete/'+id,{method:'POST'});location.reload()}
        async function historyRec(id){let d=await (await fetch('/api/magazine-complete/history/'+id)).json();alert(JSON.stringify(d.history,null,2))}
        </script></body></html>""",headers={"Cache-Control":"no-store"})

    return {"status":"REGISTERED","version":VERSION,"page":"/magazine-organizer"}
