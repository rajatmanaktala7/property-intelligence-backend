from __future__ import annotations
import html, json, re
from fastapi import Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

VERSION="8.5-DATA-ORGANIZER-ZERO-COST"
MOBILE_RE=re.compile(r"(?<!\d)([6-9]\d{9})(?!\d)")
LANDLINE_RE=re.compile(r"(?<!\d)(0?11[-\s]?\d{7,8}(?:/\d(?:/\d)*)?)(?!\d)")
PHONE_ANY_RE=re.compile(r"(?<!\d)(?:[6-9]\d{9}|0?11[-\s]?\d{7,8}(?:/\d(?:/\d)*)?)(?!\d)")
ROLE_RE=re.compile(r"(?i)\b(BUILDER|BROKER|OWNER|DEVELOPER|REALTOR|AGENT)\b")
URL_RE=re.compile(r"(?i)https?://\S+|www\.\S+")
EMAIL_RE=re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
PROPERTY_SIGNAL_RE=re.compile(r"(?i)\b(?:\d{2,7}\s*(?:Y|YD|SQYD|FT|SQFT|MTR|SQM)|GF|FF|SF|TF|BMT|BASEMENT|\d+\s*(?:BHK|BR)|APT|APARTMENT|FLAT|KOTHI|VILLA|PLOT|OFFICE|SHOP|SHOWROOM|WAREHOUSE|GODOWN|BUILDING)\b")

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
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_magorg_upload_page ON pi_magazine_organized_v850(upload_id,page_number)"))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_magorg_status ON pi_magazine_organized_v850(organizer_status)"))

def _phones(s):
    mobiles=list(dict.fromkeys(MOBILE_RE.findall(s or "")))
    lands=list(dict.fromkeys(m.group(1).strip() for m in LANDLINE_RE.finditer(s or "")))
    return mobiles,lands

def _contact_block(s):
    s=s or ""
    phones=list(PHONE_ANY_RE.finditer(s))
    if not phones:return None
    pm=phones[-1]
    left=s.rfind("(",0,pm.start())
    if left<0:return None
    right=s.find(")",pm.end())
    if right<0:return None
    return left,right+1,s[left+1:right]

def _contact_name_role(original):
    b=_contact_block(original)
    if not b:return None,None
    block=b[2]
    rm=ROLE_RE.search(block)
    role=rm.group(1).upper() if rm else None
    name=PHONE_ANY_RE.sub(" ",block)
    name=ROLE_RE.sub(" ",name)
    name=re.sub(r"[()/,:;|]+"," ",name)
    name=re.sub(r"\s+"," ",name).strip(" -")
    return (name or None),role

def _clean(original):
    s=(original or "").strip()
    b=_contact_block(s)
    if b:s=(s[:b[0]]+" "+s[b[1]:]).strip()
    s=PHONE_ANY_RE.sub(" ",s)
    s=URL_RE.sub(" ",s)
    s=EMAIL_RE.sub(" ",s)
    s=re.sub(r"\(\s*\)"," ",s)
    s=re.sub(r"\s+"," ",s).strip(" ,;|-")
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

def _organize(r):
    original=r["original_description"] or ""
    clean=_clean(original)
    mobiles,lands=_phones(original)
    name,role=_contact_name_role(original)
    noise,reason=_noise(original,clean,mobiles,lands,r["signal_score"])
    return dict(source_record_id=r["record_id"],upload_id=str(r["upload_id"]),page_number=r["page_number"],
      section_heading=r["section_heading"],original_description=original,clean_description=clean,contact_name=name,
      contact_numbers=list(dict.fromkeys(mobiles+lands)),mobile_numbers=mobiles,landline_numbers=lands,contact_role=role,
      transaction_type=r["transaction_type"],property_type=r["property_type"],area_value=r["area_value"],area_unit=r["area_unit"],
      floor=r["floor"],amount_raw=r["amount_raw"],organizer_status="REJECTED_NOISE" if noise else "CLEAN",
      reject_reason=reason,duplicate_key=_dup(r["section_heading"],clean,r["area_value"],r["area_unit"],r["floor"]),
      needs_review=bool(r["needs_review"]) or noise)

def _run(e,upload_id=None):
    q="""SELECT record_id,upload_id,page_number,section_heading,original_description,transaction_type,property_type,
    area_value,area_unit,floor,amount_raw,signal_score,needs_review FROM pi_magazine_fastlane_records"""
    p={}
    if upload_id:q+=" WHERE upload_id=CAST(:u AS UUID)";p["u"]=upload_id
    q+=" ORDER BY page_number,id"
    with e.connect() as c: rows=c.execute(text(q),p).mappings().all()
    clean=rejected=review=0
    with e.begin() as c:
        for r in rows:
            x=_organize(r)
            db=dict(x);db["contacts"]=json.dumps(x["contact_numbers"]);db["mobiles"]=json.dumps(x["mobile_numbers"]);db["lands"]=json.dumps(x["landline_numbers"])
            c.execute(text("""INSERT INTO pi_magazine_organized_v850(source_record_id,upload_id,page_number,section_heading,original_description,
            clean_description,contact_name,contact_numbers,mobile_numbers,landline_numbers,contact_role,transaction_type,property_type,
            area_value,area_unit,floor,amount_raw,organizer_status,reject_reason,duplicate_key,needs_review)
            VALUES(:source_record_id,CAST(:upload_id AS UUID),:page_number,:section_heading,:original_description,:clean_description,:contact_name,
            CAST(:contacts AS JSONB),CAST(:mobiles AS JSONB),CAST(:lands AS JSONB),:contact_role,:transaction_type,:property_type,:area_value,
            :area_unit,:floor,:amount_raw,:organizer_status,:reject_reason,:duplicate_key,:needs_review)
            ON CONFLICT(source_record_id) DO UPDATE SET section_heading=EXCLUDED.section_heading,original_description=EXCLUDED.original_description,
            clean_description=EXCLUDED.clean_description,contact_name=EXCLUDED.contact_name,contact_numbers=EXCLUDED.contact_numbers,
            mobile_numbers=EXCLUDED.mobile_numbers,landline_numbers=EXCLUDED.landline_numbers,contact_role=EXCLUDED.contact_role,
            transaction_type=EXCLUDED.transaction_type,property_type=EXCLUDED.property_type,area_value=EXCLUDED.area_value,area_unit=EXCLUDED.area_unit,
            floor=EXCLUDED.floor,amount_raw=EXCLUDED.amount_raw,organizer_status=EXCLUDED.organizer_status,reject_reason=EXCLUDED.reject_reason,
            duplicate_key=EXCLUDED.duplicate_key,needs_review=EXCLUDED.needs_review,updated_at=NOW()"""),db)
            clean+=x["organizer_status"]=="CLEAN";rejected+=x["organizer_status"]!="CLEAN";review+=x["needs_review"]
        c.execute(text("""WITH ranked AS (SELECT id,ROW_NUMBER() OVER(PARTITION BY upload_id,duplicate_key ORDER BY page_number,id) rn
        FROM pi_magazine_organized_v850 WHERE organizer_status='CLEAN' AND duplicate_key IS NOT NULL AND duplicate_key<>'')
        UPDATE pi_magazine_organized_v850 o SET organizer_status='DUPLICATE_EXACT',needs_review=TRUE,updated_at=NOW()
        FROM ranked r WHERE o.id=r.id AND r.rn>1"""))
    return {"processed":len(rows),"clean_before_duplicate_mark":int(clean),"rejected_noise":int(rejected),"needs_review":int(review)}

def register(core):
    app=_app(core);e=_engine(core)
    if app is None or e is None:raise RuntimeError("Organizer requires app + engine")
    _setup(e)
    @app.post("/api/magazine-organizer/run")
    def run(req:Request,upload_id:str|None=Query(None)):
        _login(core,req);return {"status":"ORGANIZED","version":VERSION,"cost":0,"external_api_calls":0,**_run(e,upload_id)}
    @app.get("/api/magazine-organizer/status")
    def status(req:Request):
        _login(core,req)
        with e.connect() as c:r=c.execute(text("""SELECT COUNT(*) total,COUNT(*) FILTER(WHERE organizer_status='CLEAN') clean,
        COUNT(*) FILTER(WHERE organizer_status='REJECTED_NOISE') rejected,COUNT(*) FILTER(WHERE organizer_status='DUPLICATE_EXACT') duplicates,
        COUNT(*) FILTER(WHERE needs_review) review FROM pi_magazine_organized_v850""")).mappings().first()
        return {"status":"OK","version":VERSION,"cost":0,"external_api_calls":0,**dict(r)}
    @app.get("/magazine-organizer",response_class=HTMLResponse)
    def page(req:Request,limit:int=Query(1500,ge=1,le=5000)):
        _login(core,req)
        with e.connect() as c:rows=c.execute(text("""SELECT page_number,section_heading,clean_description,contact_name,contact_numbers,contact_role,
        area_value,area_unit,floor,organizer_status,needs_review,original_description FROM pi_magazine_organized_v850 ORDER BY page_number,id LIMIT :n"""),{"n":limit}).mappings().all()
        heads=["Page","Section","Clean Description","Contact Name","Contact No.","Role","Area","Floor","Status","Review","Original Evidence"]
        body=[]
        for r in rows:
            vals=[r["page_number"],r["section_heading"],r["clean_description"],r["contact_name"],", ".join(r["contact_numbers"] or []),r["contact_role"],
            " ".join(x for x in [str(r["area_value"] or ""),str(r["area_unit"] or "")] if x),r["floor"],r["organizer_status"],"YES" if r["needs_review"] else "NO",r["original_description"]]
            body.append("<tr>"+"".join("<td>"+_esc(v)+"</td>" for v in vals)+"</tr>")
        table="<table><tr>"+"".join("<th>"+h+"</th>" for h in heads)+"</tr>"+("".join(body) if body else "<tr><td colspan=11>No organized records yet.</td></tr>")+"</table>"
        page_html="""<!doctype html><html><head><meta charset='utf-8'><style>body{font-family:Arial;padding:20px;background:#f6f7f9}button{padding:11px 16px;background:#125bc5;color:white;border:0;border-radius:8px;font-weight:bold}table{width:100%;border-collapse:collapse;background:white;font-size:12px}th,td{padding:7px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top}th{background:#eef2f6;position:sticky;top:0}</style></head><body><h2>Alliance Magazine Data Organizer · 8.5</h2><p><b>Original Description remains untouched.</b> Clean Description removes the contact block and numbers. Contacts are separate. Noise/duplicates are flagged, never deleted.</p><button onclick="go()">Organize FastLane Data — Free</button> <span id="s"></span><p><a href="/magazine-fastlane/records">Raw FastLane Records</a></p>"""+table+"""<script>async function go(){s.textContent=' Organizing...';let d=await (await fetch('/api/magazine-organizer/run',{method:'POST'})).json();s.textContent=' '+JSON.stringify(d);setTimeout(()=>location.reload(),700)}</script></body></html>"""
        return HTMLResponse(page_html,headers={"Cache-Control":"no-store"})
    return {"status":"REGISTERED","version":VERSION,"routes":["/magazine-organizer","/api/magazine-organizer/run","/api/magazine-organizer/status"]}
