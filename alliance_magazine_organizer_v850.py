from __future__ import annotations
import html, json, re, fitz
from fastapi import Body, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

VERSION="8.5.3-CATEGORY-LOCATION-CONTEXT"
MOBILE_RE=re.compile(r"(?<!\d)([6-9]\d{9})(?!\d)")
LANDLINE_RE=re.compile(r"(?<!\d)(0?11[-\s]?\d{7,8}(?:/\d(?:/\d)*)?)(?!\d)")
PHONE_ANY_RE=re.compile(r"(?<!\d)(?:[6-9]\d{9}|0?11[-\s]?\d{7,8}(?:/\d(?:/\d)*)?)(?!\d)")
ROLE_RE=re.compile(r"(?i)\b(BUILDER|BROKER|OWNER|DEVELOPER|REALTOR|AGENT)\b")
URL_RE=re.compile(r"(?i)https?://\S+|www\.\S+")
EMAIL_RE=re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
PROPERTY_SIGNAL_RE=re.compile(r"(?i)\b(?:\d{2,7}\s*(?:Y|YD|SQYD|FT|SQFT|MTR|SQM)|GF|FF|SF|TF|BMT|BASEMENT|\d+\s*(?:BHK|BR)|APT|APARTMENT|FLAT|KOTHI|VILLA|PLOT|OFFICE|SHOP|SHOWROOM|WAREHOUSE|GODOWN|BUILDING)\b")

TRUSTED_LOCATIONS=[
"ARADHNA ENCLAVE","ARADHANA ENCLAVE","ALAKNANDA","ANAND LOK","ANAND NIKETAN","CHANAKYAPURI",
"CHITRANJAN PARK","CR PARK","C R PARK","CHIRAG DELHI","CONNAUGHT PLACE","VASANT VIHAR","VASANT KUNJ",
"MALCHA MARG","PRITHVIRAJ ROAD","PRITHVIRAJ LANE","FEROZE SHAH ROAD","HANUMAN ROAD","RAJDOOT MARG",
"ASIAD VILLAGE","TUGHLAKABAD EXTN","CHATTERPUR ENCLAVE","CHHATARPUR ENCLAVE","LAJPAT NAGAR",
"GREATER KAILASH","KAILASH COLONY","DEFENCE COLONY","SOUTH EXTENSION","NEW FRIENDS COLONY",
"PANCHSHEEL PARK","HAUZ KHAS","GREEN PARK","SAFDARJUNG ENCLAVE","JOR BAGH","SUNDER NAGAR","GOLF LINKS",
"MAHARANI BAGH","FRIENDS COLONY","NIZAMUDDIN","SAKET","PITAMPURA","ROHINI","DWARKA","GURGAON","GURUGRAM","NOIDA"
]
BAD_SECTION_EXACT={"TARA","ESTATE","ESTATES","ESTATES PVT. LTD.","ESTATES PVT LTD"}
PROMO_SECTION_RE=re.compile(r"(?i)\b(?:ESTATES?\s+PVT\.?\s*LTD\.?|PVT\.?\s*LTD\.?|INTERIOR|RENOVATION|COLLABORATION|REALTORS?|REALTY|PROPERTIES|PROPERTY\s+DEALER|BUILDERS?|DEVELOPERS?)\b")

def _canon_location(v):
    u=re.sub(r"\s+"," ",(v or "").upper().strip(" -:"))
    return {"ARADHANA ENCLAVE":"ARADHNA ENCLAVE","CR PARK":"CHITRANJAN PARK","C R PARK":"CHITRANJAN PARK",
            "GURGAON":"GURUGRAM","CHHATARPUR ENCLAVE":"CHATTERPUR ENCLAVE"}.get(u,u)

def _explicit_location(v):
    u=(v or "").upper()
    for loc in sorted(TRUSTED_LOCATIONS,key=len,reverse=True):
        if re.search(r"(?<![A-Z])"+re.escape(loc)+r"(?![A-Z])",u):
            return _canon_location(loc)
    return None

def _trusted_section(v):
    s=_canon_location(v)
    if not s or s in BAD_SECTION_EXACT or PROMO_SECTION_RE.search(s): return None
    return s if any(_canon_location(x)==s for x in TRUSTED_LOCATIONS) else None


CATEGORY_PATTERNS=[
    (re.compile(r"(?i)\bRESIDENTIAL\s*[-–| ]\s*SALE\b"),"Residential Sale"),
    (re.compile(r"(?i)\bRESIDENTIAL\s*[-–| ]\s*RENT(?:ING)?\b"),"Residential Rent"),
    (re.compile(r"(?i)\bCOMMERCIAL\s*[-–| ]\s*SALE\b"),"Commercial Sale"),
    (re.compile(r"(?i)\bCOMMERCIAL\s*[-–| ]\s*RENT(?:ING)?\b"),"Commercial Rent"),
    (re.compile(r"(?i)\bINDUSTRIAL\s*[-–| ]\s*SALE\b"),"Industrial Sale"),
    (re.compile(r"(?i)\bINDUSTRIAL\s*[-–| ]\s*RENT(?:ING)?\b"),"Industrial Rent"),
    (re.compile(r"(?i)\bFARM\s*HOUSES?\s*[-–| ]\s*SALE\b"),"Farmhouse Sale"),
    (re.compile(r"(?i)\bFARM\s*HOUSES?\s*[-–| ]\s*RENT(?:ING)?\b"),"Farmhouse Rent"),
]
FARM_RE=re.compile(r"(?i)\b(FARM\s*HOUSE|FARMHOUSE|SAINIK\s+FARM|DERA\s+MANDI|GADIPUR\s+FARMS?|SULTANPUR\s+FARMS?|FARMALAND)\b")
COMMERCIAL_RE=re.compile(r"(?i)\b(OFFICE|SHOP|SHOWROOM|COMMERCIAL|RETAIL|MALL|MARKET|WAREHOUSE|GODOWN|SPACE)\b")
INDUSTRIAL_RE=re.compile(r"(?i)\b(INDUSTRIAL|FACTORY|WAREHOUSE|GODOWN|SHED)\b")
RENT_RE=re.compile(r"(?i)\b(RENT|RENTED|LEASE|LEASING|RENT\s+ALSO)\b")
SALE_RE=re.compile(r"(?i)\b(SALE|SELL|RESALE|BOOKING|BKG|U/C|NEW)\b")

def _category_from_text(v):
    s=v or ""
    for rx,label in CATEGORY_PATTERNS:
        if rx.search(s): return label
    return None

def _page_category_headings(pdf_bytes,page_no):
    out=[]
    if not pdf_bytes:return out
    doc=fitz.open(stream=bytes(pdf_bytes),filetype="pdf")
    if page_no<1 or page_no>len(doc):
        doc.close();return out
    page=doc.load_page(page_no-1)
    d=page.get_text("dict",sort=True)
    width=float(page.rect.width or 1)
    for block in d.get("blocks",[]):
        if block.get("type")!=0:continue
        for line in block.get("lines",[]):
            raw="".join(span.get("text","") for span in line.get("spans",[]) if span.get("text","")).strip()
            cat=_category_from_text(raw)
            if not cat: continue
            bbox=line.get("bbox") or block.get("bbox") or [0,0,0,0]
            x0=float(bbox[0]); y0=float(bbox[1])
            col=0 if x0/width<.34 else (1 if x0/width<.67 else 2)
            out.append({"y0":y0,"x0":x0,"col":col,"category":cat,"text":raw})
    doc.close()
    return out

def _category_for_row(row,headings):
    original=row["original_description"] or ""
    explicit=_category_from_text(original)
    if explicit:return explicit,"DESCRIPTION",100
    bbox=row.get("bbox") or []
    x0=float(bbox[0]) if isinstance(bbox,(list,tuple)) and len(bbox)>=2 else 0.0
    y0=float(bbox[1]) if isinstance(bbox,(list,tuple)) and len(bbox)>=2 else 999999.0
    # Use nearest preceding magazine category heading.
    candidates=[h for h in headings if h["y0"]<=y0+2]
    if candidates:
        h=max(candidates,key=lambda z:z["y0"])
        return h["category"],"MAGAZINE_HEADING",95
    # Safe description inference only when heading was not recoverable.
    if FARM_RE.search(original):
        return ("Farmhouse Rent" if RENT_RE.search(original) else "Farmhouse Sale"),"DESCRIPTION_FARMHOUSE",85
    if INDUSTRIAL_RE.search(original):
        return ("Industrial Rent" if RENT_RE.search(original) else "Industrial Sale"),"DESCRIPTION_ASSET",80
    if COMMERCIAL_RE.search(original):
        return ("Commercial Rent" if RENT_RE.search(original) else "Commercial Sale"),"DESCRIPTION_ASSET",80
    if RENT_RE.search(original):
        return "Residential Rent","DESCRIPTION_TRANSACTION",75
    return None,"UNKNOWN",0

def _promo_record(section,clean,original):
    s=(section or "").upper().strip()
    if s in BAD_SECTION_EXACT or PROMO_SECTION_RE.search(s):
        u=(clean or "").upper()
        if re.search(r"\bBUY\b.*\bSELL\b|\bCOLLABORATION\b|\bINTERIOR\b|\bRENOVATION\b",u):
            return True,"PROMOTIONAL_COPY"
        if re.search(r"\bPVT\.?\s*LTD\.?\b|\bOFFICE\s+ADDRESS\b",u):
            return True,"PROMOTIONAL_COMPANY"
        # Office/business card style rows with no property inventory signal
        if not re.search(r"(?i)\b\d+\s*(?:BHK|BR)\b|\b\d{2,7}\s*(?:Y|YD|SQYD|FT|SQFT|MTR|SQM)\b",clean or ""):
            return True,"PROMOTIONAL_SECTION_NO_PROPERTY"
    return False,None


def _app(core): return getattr(core,"app",None) or core
def _engine(core): return getattr(core,"engine",None)
def _login(core,req):
    fn=getattr(core,"need_login",None)
    return fn(req) if fn else "team"
def _esc(v): return html.escape("" if v is None else str(v))

def _setup(e):
    with e.begin() as c:
        c.execute(text("""CREATE TABLE IF NOT EXISTS pi_magazine_organized_v850(
          id BIGSERIAL PRIMARY KEY,source_record_id TEXT UNIQUE NOT NULL,upload_id UUID NOT NULL,page_number INTEGER NOT NULL,
          section_heading TEXT,original_description TEXT NOT NULL,clean_description TEXT NOT NULL,contact_name TEXT,
          contact_numbers JSONB NOT NULL DEFAULT '[]'::jsonb,mobile_numbers JSONB NOT NULL DEFAULT '[]'::jsonb,
          landline_numbers JSONB NOT NULL DEFAULT '[]'::jsonb,contact_role TEXT,transaction_type TEXT,property_type TEXT,
          area_value TEXT,area_unit TEXT,floor TEXT,amount_raw TEXT,organizer_status TEXT NOT NULL,reject_reason TEXT,
          duplicate_key TEXT,needs_review BOOLEAN NOT NULL DEFAULT TRUE,created_at TIMESTAMPTZ DEFAULT NOW(),updated_at TIMESTAMPTZ DEFAULT NOW())"""))
        for ddl in [
            "ALTER TABLE pi_magazine_organized_v850 ADD COLUMN IF NOT EXISTS assigned_to TEXT",
            "ALTER TABLE pi_magazine_organized_v850 ADD COLUMN IF NOT EXISTS verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED'",
            "ALTER TABLE pi_magazine_organized_v850 ADD COLUMN IF NOT EXISTS verified_by TEXT",
            "ALTER TABLE pi_magazine_organized_v850 ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ",
            "ALTER TABLE pi_magazine_organized_v850 ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ",
            "ALTER TABLE pi_magazine_organized_v850 ADD COLUMN IF NOT EXISTS archived_by TEXT",
            "ALTER TABLE pi_magazine_organized_v850 ADD COLUMN IF NOT EXISTS source_name TEXT DEFAULT 'Magazine'",
            "ALTER TABLE pi_magazine_organized_v850 ADD COLUMN IF NOT EXISTS location TEXT",
            "ALTER TABLE pi_magazine_organized_v850 ADD COLUMN IF NOT EXISTS location_source TEXT",
            "ALTER TABLE pi_magazine_organized_v850 ADD COLUMN IF NOT EXISTS location_confidence INTEGER",
            "ALTER TABLE pi_magazine_organized_v850 ADD COLUMN IF NOT EXISTS property_category TEXT",
            "ALTER TABLE pi_magazine_organized_v850 ADD COLUMN IF NOT EXISTS category_source TEXT",
            "ALTER TABLE pi_magazine_organized_v850 ADD COLUMN IF NOT EXISTS category_confidence INTEGER",
        ]: c.execute(text(ddl))
        c.execute(text("""CREATE TABLE IF NOT EXISTS pi_magazine_organized_history_v851(
          id BIGSERIAL PRIMARY KEY,source_record_id TEXT NOT NULL,action TEXT NOT NULL,actor TEXT,
          before_json JSONB,after_json JSONB,note TEXT,created_at TIMESTAMPTZ DEFAULT NOW())"""))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_magorg_upload_page ON pi_magazine_organized_v850(upload_id,page_number)"))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_magorg_status ON pi_magazine_organized_v850(organizer_status)"))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_magorg_verify ON pi_magazine_organized_v850(verification_status)"))

def _phones(s):
    mobiles=list(dict.fromkeys(MOBILE_RE.findall(s or "")))
    lands=list(dict.fromkeys(m.group(1).strip() for m in LANDLINE_RE.finditer(s or "")))
    return mobiles,lands

def _contact_block(s):
    s=s or ""; phones=list(PHONE_ANY_RE.finditer(s))
    if not phones:return None
    pm=phones[-1]; left=s.rfind("(",0,pm.start())
    if left<0:return None
    right=s.find(")",pm.end())
    if right<0:return None
    return left,right+1,s[left+1:right]

def _contact_name_role(original):
    b=_contact_block(original)
    if not b:return None,None
    block=b[2]; rm=ROLE_RE.search(block); role=rm.group(1).upper() if rm else None
    name=PHONE_ANY_RE.sub(" ",block); name=ROLE_RE.sub(" ",name)
    name=re.sub(r"[()/,:;|]+"," ",name); name=re.sub(r"\s+"," ",name).strip(" -")
    return (name or None),role

def _clean(original):
    s=(original or "").strip(); b=_contact_block(s)
    if b:s=(s[:b[0]]+" "+s[b[1]:]).strip()
    s=PHONE_ANY_RE.sub(" ",s); s=URL_RE.sub(" ",s); s=EMAIL_RE.sub(" ",s)
    s=re.sub(r"\(\s*\)"," ",s); s=re.sub(r"\s+"," ",s).strip(" ,;|-")
    return s

def _noise(original,clean,mobiles,lands,score):
    compact=re.sub(r"[\s,./()\-:]+","",original or "")
    if compact and compact.isdigit():return True,"PHONE_OR_NUMBER_ONLY"
    if not PROPERTY_SIGNAL_RE.search(clean or "") and (mobiles or lands) and int(score or 0)<=5:
        return True,"CONTACT_OR_AD_NO_PROPERTY_SIGNAL"
    if len((clean or "").strip())<5:return True,"NO_PROPERTY_DESCRIPTION"
    return False,None

def _dup(section,clean,area,unit,floor):
    vals=[section or "",clean or "",str(area or ""),str(unit or ""),str(floor or "")]
    return "|".join(re.sub(r"[^A-Z0-9]","",v.upper()) for v in vals)[:240]

def _organize(r,location=None,location_source=None,location_confidence=None,property_category=None,category_source=None,category_confidence=None):
    original=r["original_description"] or ""; clean=_clean(original)
    mobiles,lands=_phones(original); name,role=_contact_name_role(original)
    noise,reason=_noise(original,clean,mobiles,lands,r["signal_score"])
    promo,promo_reason=_promo_record(r["section_heading"],clean,original)
    if promo: noise,reason=True,promo_reason
    return dict(source_record_id=r["record_id"],upload_id=str(r["upload_id"]),page_number=r["page_number"],
      section_heading=r["section_heading"],location=location,location_source=location_source,location_confidence=location_confidence,property_category=property_category,category_source=category_source,category_confidence=category_confidence,original_description=original,clean_description=clean,contact_name=name,
      contact_numbers=list(dict.fromkeys(mobiles+lands)),mobile_numbers=mobiles,landline_numbers=lands,contact_role=role,
      transaction_type=r["transaction_type"],property_type=r["property_type"],area_value=r["area_value"],area_unit=r["area_unit"],
      floor=r["floor"],amount_raw=r["amount_raw"],organizer_status="REJECTED_NOISE" if noise else "CLEAN",
      reject_reason=reason,duplicate_key=_dup(location or r["section_heading"],clean,r["area_value"],r["area_unit"],r["floor"]),
      needs_review=bool(r["needs_review"]) or noise)

def _run(e,upload_id=None):
    q="""SELECT record_id,upload_id,page_number,section_heading,original_description,transaction_type,property_type,
    area_value,area_unit,floor,amount_raw,signal_score,needs_review,bbox FROM pi_magazine_fastlane_records"""
    p={}
    if upload_id:q+=" WHERE upload_id=CAST(:u AS UUID)";p["u"]=upload_id
    q+=" ORDER BY page_number,id"
    with e.connect() as c:
        rows=c.execute(text(q),p).mappings().all()
        pdfrow=c.execute(text("SELECT pdf_content FROM pi_magazine_fresh_uploads ORDER BY created_at DESC LIMIT 1")).first()
    pdf_bytes=bytes(pdfrow[0]) if pdfrow and pdfrow[0] is not None else None
    page_headings={}
    for pg in sorted({int(r["page_number"]) for r in rows}):
        page_headings[pg]=_page_category_headings(pdf_bytes,pg) if pdf_bytes else []
    clean=rejected=review=0
    last_location=None
    with e.begin() as c:
        for r in rows:
            cdesc=_clean(r["original_description"] or "")
            explicit=_explicit_location(cdesc)
            trusted=_trusted_section(r["section_heading"])
            if explicit:
                location,location_source,location_confidence=explicit,"DESCRIPTION",100
                last_location=location
            elif trusted:
                location,location_source,location_confidence=trusted,"TRUSTED_HEADING",95
                last_location=location
            elif last_location:
                location,location_source,location_confidence=last_location,"CARRY_FORWARD_AFTER_NOISE",80
            else:
                location,location_source,location_confidence=None,"UNKNOWN",0
            property_category,category_source,category_confidence=_category_for_row(r,page_headings.get(int(r["page_number"]),[]))
            x=_organize(r,location,location_source,location_confidence,property_category,category_source,category_confidence); db=dict(x)
            db["contacts"]=json.dumps(x["contact_numbers"]);db["mobiles"]=json.dumps(x["mobile_numbers"]);db["lands"]=json.dumps(x["landline_numbers"])
            c.execute(text("""INSERT INTO pi_magazine_organized_v850(source_record_id,upload_id,page_number,section_heading,original_description,
            clean_description,location,location_source,location_confidence,property_category,category_source,category_confidence,contact_name,contact_numbers,mobile_numbers,landline_numbers,contact_role,transaction_type,property_type,
            area_value,area_unit,floor,amount_raw,organizer_status,reject_reason,duplicate_key,needs_review)
            VALUES(:source_record_id,CAST(:upload_id AS UUID),:page_number,:section_heading,:original_description,:clean_description,:location,:location_source,:location_confidence,:property_category,:category_source,:category_confidence,:contact_name,
            CAST(:contacts AS JSONB),CAST(:mobiles AS JSONB),CAST(:lands AS JSONB),:contact_role,:transaction_type,:property_type,:area_value,
            :area_unit,:floor,:amount_raw,:organizer_status,:reject_reason,:duplicate_key,:needs_review)
            ON CONFLICT(source_record_id) DO UPDATE SET section_heading=EXCLUDED.section_heading,original_description=EXCLUDED.original_description,
            clean_description=EXCLUDED.clean_description,location=EXCLUDED.location,location_source=EXCLUDED.location_source,location_confidence=EXCLUDED.location_confidence,property_category=EXCLUDED.property_category,category_source=EXCLUDED.category_source,category_confidence=EXCLUDED.category_confidence,contact_name=EXCLUDED.contact_name,contact_numbers=EXCLUDED.contact_numbers,
            mobile_numbers=EXCLUDED.mobile_numbers,landline_numbers=EXCLUDED.landline_numbers,contact_role=EXCLUDED.contact_role,
            transaction_type=EXCLUDED.transaction_type,property_type=EXCLUDED.property_type,area_value=EXCLUDED.area_value,area_unit=EXCLUDED.area_unit,
            floor=EXCLUDED.floor,amount_raw=EXCLUDED.amount_raw,organizer_status=EXCLUDED.organizer_status,reject_reason=EXCLUDED.reject_reason,
            duplicate_key=EXCLUDED.duplicate_key,needs_review=EXCLUDED.needs_review,updated_at=NOW()"""),db)
            clean+=x["organizer_status"]=="CLEAN";rejected+=x["organizer_status"]!="CLEAN";review+=x["needs_review"]
        c.execute(text("""WITH ranked AS (SELECT id,ROW_NUMBER() OVER(PARTITION BY upload_id,duplicate_key ORDER BY page_number,id) rn
        FROM pi_magazine_organized_v850 WHERE organizer_status='CLEAN' AND archived_at IS NULL AND duplicate_key IS NOT NULL AND duplicate_key<>'')
        UPDATE pi_magazine_organized_v850 o SET organizer_status='DUPLICATE_EXACT',needs_review=TRUE,updated_at=NOW()
        FROM ranked r WHERE o.id=r.id AND r.rn>1"""))
    return {"processed":len(rows),"clean_before_duplicate_mark":int(clean),"rejected_noise":int(rejected),"needs_review":int(review)}

def _actor(core,req):
    try:return str(_login(core,req) or "team")
    except Exception:return "team"

def register(core):
    app=_app(core);e=_engine(core)
    if app is None or e is None:raise RuntimeError("Organizer requires app + engine")
    _setup(e)

    @app.post("/api/magazine-organizer/run")
    def run(req:Request,upload_id:str|None=Query(None)):
        _login(core,req);return {"status":"ORGANIZED","version":VERSION,"cost":0,"external_api_calls":0,**_run(e,upload_id)}

    @app.post("/api/magazine-organizer/edit/{record_id}")
    def edit(record_id:str,req:Request,payload:dict=Body(...)):
        actor=_actor(core,req)
        allowed={"clean_description","location","property_category","contact_name","contact_numbers","transaction_type","amount_raw","assigned_to","verification_status","needs_review"}
        changes={k:payload[k] for k in allowed if k in payload}
        if not changes:raise HTTPException(400,"No editable fields supplied")
        with e.begin() as c:
            before=c.execute(text("SELECT * FROM pi_magazine_organized_v850 WHERE source_record_id=:r AND archived_at IS NULL"),{"r":record_id}).mappings().first()
            if not before:raise HTTPException(404,"Record not found")
            sets=[];params={"r":record_id}
            for i,(k,v) in enumerate(changes.items()):
                key=f"v{i}"
                if k=="contact_numbers":
                    sets.append(f"{k}=CAST(:{key} AS JSONB)");params[key]=json.dumps(v if isinstance(v,list) else [x.strip() for x in str(v).split(",") if x.strip()])
                else:
                    sets.append(f"{k}=:{key}");params[key]=v
            sets.append("updated_at=NOW()")
            c.execute(text("UPDATE pi_magazine_organized_v850 SET "+",".join(sets)+" WHERE source_record_id=:r"),params)
            after=c.execute(text("SELECT * FROM pi_magazine_organized_v850 WHERE source_record_id=:r"),{"r":record_id}).mappings().first()
            c.execute(text("""INSERT INTO pi_magazine_organized_history_v851(source_record_id,action,actor,before_json,after_json)
            VALUES(:r,'EDIT',:a,CAST(:b AS JSONB),CAST(:n AS JSONB))"""),{"r":record_id,"a":actor,"b":json.dumps(dict(before),default=str),"n":json.dumps(dict(after),default=str)})
        return {"status":"UPDATED","record_id":record_id}

    @app.post("/api/magazine-organizer/verify/{record_id}")
    def verify(record_id:str,req:Request,payload:dict=Body(...)):
        actor=_actor(core,req); status=str(payload.get("status","UNVERIFIED")).upper()
        if status not in {"UNVERIFIED","VERIFICATION DUE","AVAILABLE","NOT AVAILABLE","FOLLOW-UP","CLOSED/REMOVED"}:
            raise HTTPException(400,"Invalid verification status")
        with e.begin() as c:
            before=c.execute(text("SELECT * FROM pi_magazine_organized_v850 WHERE source_record_id=:r AND archived_at IS NULL"),{"r":record_id}).mappings().first()
            if not before:raise HTTPException(404,"Record not found")
            c.execute(text("""UPDATE pi_magazine_organized_v850 SET verification_status=:s,verified_by=:a,
            verified_at=CASE WHEN :s='AVAILABLE' THEN NOW() ELSE verified_at END,updated_at=NOW() WHERE source_record_id=:r"""),
            {"s":status,"a":actor,"r":record_id})
            after=c.execute(text("SELECT * FROM pi_magazine_organized_v850 WHERE source_record_id=:r"),{"r":record_id}).mappings().first()
            c.execute(text("""INSERT INTO pi_magazine_organized_history_v851(source_record_id,action,actor,before_json,after_json,note)
            VALUES(:r,'VERIFY',:a,CAST(:b AS JSONB),CAST(:n AS JSONB),:note)"""),
            {"r":record_id,"a":actor,"b":json.dumps(dict(before),default=str),"n":json.dumps(dict(after),default=str),"note":status})
        return {"status":"UPDATED","record_id":record_id,"verification_status":status}

    @app.post("/api/magazine-organizer/delete/{record_id}")
    def delete(record_id:str,req:Request):
        actor=_actor(core,req)
        with e.begin() as c:
            before=c.execute(text("SELECT * FROM pi_magazine_organized_v850 WHERE source_record_id=:r AND archived_at IS NULL"),{"r":record_id}).mappings().first()
            if not before:raise HTTPException(404,"Record not found")
            c.execute(text("""UPDATE pi_magazine_organized_v850 SET archived_at=NOW(),archived_by=:a,organizer_status='ARCHIVED',
            updated_at=NOW() WHERE source_record_id=:r"""),{"a":actor,"r":record_id})
            c.execute(text("""INSERT INTO pi_magazine_organized_history_v851(source_record_id,action,actor,before_json,note)
            VALUES(:r,'SOFT_DELETE',:a,CAST(:b AS JSONB),'Archived, not hard deleted')"""),
            {"r":record_id,"a":actor,"b":json.dumps(dict(before),default=str)})
        return {"status":"ARCHIVED","record_id":record_id}

    @app.get("/api/magazine-organizer/history/{record_id}")
    def history(record_id:str,req:Request):
        _login(core,req)
        with e.connect() as c:rows=c.execute(text("""SELECT action,actor,note,created_at FROM pi_magazine_organized_history_v851
        WHERE source_record_id=:r ORDER BY id DESC LIMIT 100"""),{"r":record_id}).mappings().all()
        return {"record_id":record_id,"history":[dict(x) for x in rows]}

    @app.get("/api/magazine-organizer/status")
    def status(req:Request):
        _login(core,req)
        with e.connect() as c:r=c.execute(text("""SELECT COUNT(*) FILTER(WHERE archived_at IS NULL) total,
        COUNT(*) FILTER(WHERE organizer_status='CLEAN' AND archived_at IS NULL) clean,
        COUNT(*) FILTER(WHERE organizer_status='REJECTED_NOISE' AND archived_at IS NULL) rejected,
        COUNT(*) FILTER(WHERE organizer_status='DUPLICATE_EXACT' AND archived_at IS NULL) duplicates,
        COUNT(*) FILTER(WHERE needs_review AND archived_at IS NULL) review,
        COUNT(*) FILTER(WHERE verification_status='AVAILABLE' AND archived_at IS NULL) available,
        COUNT(*) FILTER(WHERE property_category='Residential Sale' AND archived_at IS NULL) residential_sale,
        COUNT(*) FILTER(WHERE property_category='Residential Rent' AND archived_at IS NULL) residential_rent,
        COUNT(*) FILTER(WHERE property_category='Commercial Sale' AND archived_at IS NULL) commercial_sale,
        COUNT(*) FILTER(WHERE property_category='Commercial Rent' AND archived_at IS NULL) commercial_rent,
        COUNT(*) FILTER(WHERE property_category='Industrial Sale' AND archived_at IS NULL) industrial_sale,
        COUNT(*) FILTER(WHERE property_category='Industrial Rent' AND archived_at IS NULL) industrial_rent,
        COUNT(*) FILTER(WHERE property_category LIKE 'Farmhouse %%' AND archived_at IS NULL) farmhouse
        FROM pi_magazine_organized_v850""")).mappings().first()
        return {"status":"OK","version":VERSION,"cost":0,"external_api_calls":0,**dict(r)}

    @app.get("/magazine-organizer",response_class=HTMLResponse)
    def page(req:Request,limit:int=Query(1500,ge=1,le=5000)):
        _login(core,req)
        with e.connect() as c:rows=c.execute(text("""SELECT source_record_id,page_number,location,location_source,location_confidence,property_category,category_source,category_confidence,clean_description,contact_name,contact_numbers,
        transaction_type,area_value,area_unit,amount_raw,verification_status,assigned_to,source_name,created_at,updated_at,organizer_status,
        needs_review FROM pi_magazine_organized_v850 WHERE archived_at IS NULL AND organizer_status NOT IN ('REJECTED_NOISE') ORDER BY page_number,id LIMIT :n"""),{"n":limit}).mappings().all()
        heads=["Property ID","Location","Description / Address","Property Category","Area","Amount","Contact Name","Contact No.","Date & Time","Status","Verify","History","Assigned To","Source","Edit","Delete"]
        body=[]
        for r in rows:
            area=" ".join(x for x in [str(r["area_value"] or ""),str(r["area_unit"] or "")] if x)
            dt=(str(r["created_at"])[:19] if r["created_at"] else "")
            rid=_esc(r["source_record_id"])
            status=r["verification_status"] or ("Needs Review" if r["needs_review"] else r["organizer_status"])
            vals=[
                rid,_esc(r["location"]),_esc(r["clean_description"]),_esc(r["property_category"]),_esc(area),_esc(r["amount_raw"]),
                _esc(r["contact_name"]),_esc(", ".join(r["contact_numbers"] or [])),_esc(dt),_esc(status)
            ]
            tr="<tr>"+"".join("<td>"+v+"</td>" for v in vals)
            tr+=f"""<td><button class='mini' onclick="verifyRec('{rid}')">Verify</button></td>
            <td><button class='mini' onclick="historyRec('{rid}')">History</button></td>
            <td>{_esc(r["assigned_to"])}</td><td>Magazine · p.{_esc(r["page_number"])}</td>
            <td><button class='mini' onclick="editRec('{rid}')">Edit</button></td>
            <td><button class='del' onclick="deleteRec('{rid}')">Delete</button></td></tr>"""
            body.append(tr)
        table="<table><tr>"+"".join("<th>"+h+"</th>" for h in heads)+"</tr>"+("".join(body) if body else "<tr><td colspan=16>No organized records yet.</td></tr>")+"</table>"
        page_html="""<!doctype html><html><head><meta charset='utf-8'><style>
        body{font-family:Arial;padding:20px;background:#f6f7f9;color:#17212b}.top{background:white;padding:16px;border-radius:12px;margin-bottom:14px}
        button{padding:9px 13px;background:#125bc5;color:white;border:0;border-radius:7px;font-weight:bold}.mini{padding:5px 8px}.del{padding:5px 8px;background:#a21d1d}
        table{width:100%;border-collapse:collapse;background:white;font-size:12px}th,td{padding:7px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top}
        th{background:#eef2f6;position:sticky;top:0}td:nth-child(3){min-width:280px}</style></head><body>
        <div class='top'><h2>Alliance Magazine Database · 8.5.3</h2>
        <p><b>8.5.3:</b> Section is hidden from the team database. Location is business-facing. TARA/ESTATES promotional headings are ignored contextually, genuine TARA APT and Farmhouse records are preserved. Property Category is derived from the magazine heading and description. Original evidence remains immutable.</p>
        <button onclick="organize()">Organize FastLane Data — Free</button> <span id='msg'></span></div>"""+table+"""
        <script>
        async function organize(){msg.textContent=' Organizing...';let d=await (await fetch('/api/magazine-organizer/run',{method:'POST'})).json();msg.textContent=' '+JSON.stringify(d);setTimeout(()=>location.reload(),700)}
        async function verifyRec(id){let s=prompt('Status: UNVERIFIED, VERIFICATION DUE, AVAILABLE, NOT AVAILABLE, FOLLOW-UP, CLOSED/REMOVED','AVAILABLE');if(!s)return;await fetch('/api/magazine-organizer/verify/'+id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:s})});location.reload()}
        async function editRec(id){let loc=prompt('Location (optional)');let cat=prompt('Property Category: Residential Sale, Residential Rent, Commercial Sale, Commercial Rent, Industrial Sale, Industrial Rent, Farmhouse Sale, Farmhouse Rent (optional)');let desc=prompt('New Clean Description (Original Description will NOT change)');if(desc===null)return;let name=prompt('Contact Name (optional)');let phones=prompt('Contact No(s), comma separated (optional)');let assigned=prompt('Assigned To (optional)');let body={clean_description:desc};if(loc!==null)body.location=loc;if(cat!==null)body.property_category=cat;if(name!==null)body.contact_name=name;if(phones!==null)body.contact_numbers=phones;if(assigned!==null)body.assigned_to=assigned;await fetch('/api/magazine-organizer/edit/'+id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});location.reload()}
        async function deleteRec(id){if(!confirm('Archive this property? It will NOT be hard deleted.'))return;await fetch('/api/magazine-organizer/delete/'+id,{method:'POST'});location.reload()}
        async function historyRec(id){let d=await (await fetch('/api/magazine-organizer/history/'+id)).json();alert(JSON.stringify(d.history,null,2))}
        </script></body></html>"""
        return HTMLResponse(page_html,headers={"Cache-Control":"no-store"})
    return {"status":"REGISTERED","version":VERSION,"routes":["/magazine-organizer","/api/magazine-organizer/run","/api/magazine-organizer/edit/{record_id}","/api/magazine-organizer/verify/{record_id}","/api/magazine-organizer/delete/{record_id}","/api/magazine-organizer/history/{record_id}"]}
