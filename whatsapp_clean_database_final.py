import re, io, json, uuid, hashlib, html
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import text
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from openpyxl.utils import get_column_letter
from whatsapp_property_intelligence_final import engine, require_db, esc

router = APIRouter(prefix="/clean", tags=["WhatsApp Clean Database Final"])

PROPERTY_TYPES = [
    ("Warehouse / Industrial", ["warehouse","industrial","factory","godown","udyog"]),
    ("Commercial Showroom", ["showroom"]),("Commercial Shop", ["shop","retail outlet"]),
    ("Office", ["office","workspace"]),("Farmhouse", ["farmhouse","farm house","farm land"]),
    ("Banquet", ["banquet"]),("Hotel", ["hotel","resort"]),("Guest House", ["guest house","guesthouse"]),
    ("Restaurant", ["restaurant","restro","resto bar","restobar"]),("Cafe", ["cafe","café"]),
    ("Club", ["club","lounge"]),("Independent House / Villa", ["villa","kothi","independent house","bungalow","bunglow","row house"]),
    ("Apartment", ["apartment","flat","bhk","builder floor","floor with terrace"]),
    ("Plot / Land", ["plot","land","acre","sqyd","gaj"]),("Commercial Space", ["commercial space","commercial building","commercial","retail space"])
]
REQ_WORDS=["requirement","required","require ","wanted","looking for","need ","buyer looking","tenant looking","client looking","want ","wanted on","required for","need on"]
SUPPLY_SALE=["for sale","available for sale","sale option","selling","sell ","auction","preleased","pre-leased"]
SUPPLY_RENT=["for rent","available on rent","for lease","available on lease","lease option","rent -","rent:"]
NOISE_WORDS=["high court","supreme court","breaking news","we're hiring","we are hiring","join our team","send us your cv","job opening","vacancy","follow our","facebook.com","instagram.com","group chat invite","view channel","subscribe","likes and follow"]
PHONE_RE=re.compile(r"(?<!\d)(?:(?:\+?91)[\s-]?)?([6-9](?:[\s-]?\d){9})(?!\d)")
AREA_RE=re.compile(r"(?P<a>\d[\d,]*(?:\.\d+)?)\s*(?:-|to|–)?\s*(?P<b>\d[\d,]*(?:\.\d+)?)?\s*(?P<u>sq\.?\s*ft|sqft|sft|sq\.?\s*yds?|sqyds?|sqyrd|sq\.?\s*m(?:tr|trs)?|sqm(?:tr|trs)?|gaj|yards?|yds?|acre?s?|bigha|mtr?s?)",re.I)
BUDGET_RE=re.compile(r"(?:(?:budget|price|demand|asking|rent|reserve price|amount|range)\s*(?:is|@|:|-|=)?\s*)(?:₹|rs\.?|inr)?\s*(?P<a>\d[\d,]*(?:\.\d+)?)(?:\s*(?:-|to|–)\s*(?P<b>\d[\d,]*(?:\.\d+)?))?\s*(?P<u>cr|crore?s?|lac?s?|lakh?s?|l|k|thousand)?",re.I)

KNOWN_LOCATIONS=["Siolim","Assagao","Anjuna","Vagator","Morjim","Mandrem","Parra","Arpora","Calangute","Candolim","Baga","Porvorim","Panjim","Panaji","Miramar","Caranzalem","Taleigao","Dona Paula","Bambolim","Saligao","Sangolda","Guirim","Old Goa","Campal","Mapusa","Margao","Colva","Nerul","Reis Magos","Pilerne","Moira","Ribandar","Kadamba Plateau","St. Inez","Fontainhas","Socorro","Merces","Chimbel","Betim","Corlim","Carambolim","Delhi","South Delhi","Defence Colony","Greater Kailash","GK-1","GK-2","Vasant Kunj","Vasant Vihar","Saket","Green Park","Hauz Khas","Janakpuri","Dwarka","Karol Bagh","Rohini","Rajouri Garden","Punjabi Bagh","Paschim Vihar","Pitampura","Vikaspuri","Tilak Nagar","Subhash Nagar","Moti Nagar","Friends Colony","Maharani Bagh","South Extension","Lajpat Nagar","Jor Bagh","Anand Lok","Niti Bagh","Panchsheel Park","Shanti Niketan","Sundar Nagar","Golf Links","Gulmohar Park","Connaught Place","East of Kailash","SDA","Safdarjung Enclave","Gurugram","Golf Course Road","Sohna Road","DLF Phase-1","DLF Phase-2","DLF Phase-4","Palam Vihar","Udyog Vihar","Noida","Faridabad","Ghaziabad","Vaishali","Indirapuram","Hapur"]

SCHEMA="""
CREATE TABLE IF NOT EXISTS wai_clean_records(
 id UUID PRIMARY KEY, source_message_id UUID, source_group TEXT, record_type TEXT NOT NULL, transaction TEXT,
 raw_details TEXT NOT NULL, contact_no TEXT, all_contacts JSONB DEFAULT '[]'::jsonb,
 budget_text TEXT, budget_min NUMERIC, budget_max NUMERIC, budget_period TEXT,
 area_text TEXT, area_min NUMERIC, area_max NUMERIC, area_unit TEXT, area_sqft_min NUMERIC, area_sqft_max NUMERIC,
 location TEXT, all_locations JSONB DEFAULT '[]'::jsonb, property_type TEXT, person_name TEXT, firm_name TEXT,
 confidence NUMERIC, status TEXT DEFAULT 'unverified', rejection_reason TEXT, source_fingerprint TEXT UNIQUE,
 source_created_at TIMESTAMPTZ, processed_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_wcr_type ON wai_clean_records(record_type);
CREATE INDEX IF NOT EXISTS idx_wcr_location ON wai_clean_records(location);
CREATE INDEX IF NOT EXISTS idx_wcr_phone ON wai_clean_records(contact_no);
CREATE TABLE IF NOT EXISTS wai_clean_runs(
 id UUID PRIMARY KEY, started_at TIMESTAMPTZ DEFAULT NOW(), completed_at TIMESTAMPTZ,
 source_messages INT DEFAULT 0, inventory_rows INT DEFAULT 0, requirement_rows INT DEFAULT 0,
 rejected_rows INT DEFAULT 0, failed INT DEFAULT 0, notes TEXT
);
"""

def init_clean_db():
    require_db()
    with engine.begin() as c:
        for stmt in [x.strip() for x in SCHEMA.split(";") if x.strip()]:
            c.execute(text(stmt))

def norm(v): return re.sub(r"\s+"," ",str(v or "").replace("\u00a0"," ")).strip()

def clean_phone(v):
    d=re.sub(r"\D","",str(v or ""))
    if len(d)==12 and d.startswith("91"): d=d[2:]
    if len(d)==11 and d.startswith("0"): d=d[1:]
    return "+91 "+d[:5]+" "+d[5:] if len(d)==10 and d[0] in "6789" else ""

def phones(txt):
    out=[]
    for m in PHONE_RE.finditer(txt or ""):
        p=clean_phone(m.group(0))
        if p and p not in out: out.append(p)
    return out

def detect_type(txt):
    low=(txt or "").lower()
    for out,keys in PROPERTY_TYPES:
        if any(k in low for k in keys): return out
    return None

def detect_record_type(txt):
    low=(txt or "").lower()
    if any(x in low for x in REQ_WORDS): return "REQUIREMENT"
    if any(x in low for x in SUPPLY_SALE+SUPPLY_RENT): return "INVENTORY"
    return None

def detect_transaction(txt,rtype):
    low=(txt or "").lower()
    if rtype=="REQUIREMENT":
        if any(x in low for x in ["rent","lease","tenant","on rent"]): return "RENT"
        if any(x in low for x in ["purchase","buy","buyer","outright","sale"]): return "SALE"
        return "REQUIREMENT"
    if any(x in low for x in SUPPLY_RENT): return "RENT"
    if any(x in low for x in SUPPLY_SALE): return "SALE"
    return None

def locations(txt):
    low=(txt or "").lower();out=[]
    aliases={"gurgaon":"Gurugram","gk1":"GK-1","gk2":"GK-2","donapaula":"Dona Paula","donapula":"Dona Paula","provorim":"Porvorim","caranzhalem":"Caranzalem","caranzalim":"Caranzalem","taligao":"Taleigao","stinez":"St. Inez","kadamba platue":"Kadamba Plateau"}
    for a,c in aliases.items():
        if re.search(r"(?<!\w)"+re.escape(a)+r"(?!\w)",low) and c not in out: out.append(c)
    for loc in sorted(KNOWN_LOCATIONS,key=len,reverse=True):
        if re.search(r"(?<!\w)"+re.escape(loc.lower())+r"(?!\w)",low) and loc not in out: out.append(loc)
    for sec in re.findall(r"\b(?:sector|sec)[\s\-]*([0-9]{1,3}[a-z]?)\b",txt or "",re.I):
        s="Sector "+sec.upper()
        if s not in out: out.append(s)
    return out[:12]

def area_to_sqft(v,u):
    if v is None:return None
    u=(u or "").lower().replace(".","").replace(" ","")
    if u in ("sqyd","sqyds","sqyrd","yards","yard","yds","gaj"): return v*9
    if u.startswith("sqm") or u.startswith("sqmt") or u in ("mtr","mtrs"): return v*10.7639
    if u.startswith("acre"): return v*43560
    if u=="bigha": return v*27000
    return v

def extract_area(txt):
    m=AREA_RE.search(txt or "")
    if not m:return (None,None,None,None,None,None)
    a=float(m.group("a").replace(",",""));b=float((m.group("b") or m.group("a")).replace(",",""));u=m.group("u")
    return (m.group(0),a,b,u,round(area_to_sqft(a,u),2),round(area_to_sqft(b,u),2))

def money_value(n,u):
    v=float(str(n).replace(",",""));u=(u or "").lower()
    if u in ("cr","crore","crores"):v*=10000000
    elif u in ("lac","lacs","lakh","lakhs","l"):v*=100000
    elif u in ("k","thousand"):v*=1000
    return v

def extract_budget(txt):
    m=BUDGET_RE.search(txt or "")
    if not m:
        m=re.search(r"(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?)\s*(?:-|to|–)?\s*(\d[\d,]*(?:\.\d+)?)?\s*(cr|crore?s?|lac?s?|lakh?s?|l|k)\b",txt or "",re.I)
        if not m:return (None,None,None,None)
        raw=m.group(0);a=money_value(m.group(1),m.group(3));b=money_value(m.group(2) or m.group(1),m.group(3))
    else:
        raw=m.group(0);a=money_value(m.group("a"),m.group("u"));b=money_value(m.group("b") or m.group("a"),m.group("u"))
    low=(txt or "").lower();period="per_month" if any(x in low for x in ["per month","/month","monthly","rent","lease"]) else "total"
    if any(x in low for x in ["psf","per sq ft","/sqft"]):period="per_sqft"
    return (raw,a,b,period)

def atomic_segments(raw):
    text=str(raw or "").replace("\r\n","\n").replace("\r","\n")
    strong=re.compile(r"(?i)(?=(?:\*{0,2}\s*)?(?:IMMEDIATELY\s+REQUIRE!?|URGENT(?:LY)?\s+REQUIRE(?:MENT|D)?|REQUIREMENT\s*[A-Z0-9]*|REQUIRED\s+FOR|WANTED\s+FOR|NEED\s+ON|FOR\s+SALE\s*[:-]|FOR\s+LEASE\s*[:-]|FOR\s+RENT\s*[:-]|OPTION\s*\d+\b))")
    parts=[norm(x.strip("* \t")) for x in strong.split(text) if norm(x.strip("* \t"))]
    if len(parts)<2:
        for p in [r"(?m)(?=^\s*(?:\d{1,3}|[A-Z])\s*[\)\.\-:]\s+)",r"(?m)(?=^\s*[0-9]\ufe0f?\u20e3\s*)",r"(?m)(?=^\s*[①②③④⑤⑥⑦⑧⑨⑩]\s*)"]:
            cand=[norm(x.strip("* \t")) for x in re.split(p,text) if norm(x.strip("* \t"))]
            if len([x for x in cand if len(x)>=18])>=2:
                parts=[x for x in cand if len(x)>=18];break
    return [x for x in (parts or [norm(text)]) if len(x)>=12]

def parse_message(row):
    full=row["message_text"] or ""; inherited=phones(full); out=[]
    for idx,seg in enumerate(atomic_segments(full),1):
        low=seg.lower()
        if any(x in low for x in NOISE_WORDS):
            out.append({"rtype":"REJECTED","raw":seg,"phone":inherited[0] if inherited else "","phones":inherited,"tx":None,"ptype":None,"locs":[],"area":(None,)*6,"budget":(None,)*4,"confidence":0,"status":"rejected","reason":"News / jobs / social promotion","idx":idx});continue
        ptype=detect_type(seg);rtype=detect_record_type(seg);ar=extract_area(seg);bu=extract_budget(seg)
        if not rtype and ptype and (ar[0] or bu[0]):rtype="INVENTORY"
        if not rtype or not ptype: continue
        ph=phones(seg) or inherited;locs=locations(seg);conf=25+20+(15 if locs else 0)+(15 if ph else 0)+(10 if ar[0] else 0)+(10 if bu[0] else 0)+5
        out.append({"rtype":rtype,"raw":seg,"phone":ph[0] if ph else "","phones":ph,"tx":detect_transaction(seg,rtype),"ptype":ptype,"locs":locs,"area":ar,"budget":bu,"confidence":min(conf,100),"status":"unverified","reason":None,"idx":idx})
    return out

def refresh_clean_database(full_rebuild=False):
    init_clean_db();run_id=uuid.uuid4();stats={"source_messages":0,"inventory_rows":0,"requirement_rows":0,"rejected_rows":0,"failed":0}
    with engine.begin() as c:
        c.execute(text("INSERT INTO wai_clean_runs(id,notes) VALUES(:id,:n)"),{"id":run_id,"n":"full rebuild" if full_rebuild else "incremental refresh"})
        if full_rebuild:c.execute(text("DELETE FROM wai_clean_records"))
        existing=set() if full_rebuild else {r[0] for r in c.execute(text("SELECT DISTINCT source_message_id FROM wai_clean_records")).all()}
        src=c.execute(text("""SELECT r.id source_message_id,r.message_text,r.sender_display_name,r.sent_at,g.name source_group FROM wai_raw_messages r LEFT JOIN wai_groups g ON g.id=r.group_id WHERE COALESCE(r.message_text,'')<>'' ORDER BY r.ingested_at,r.id""")).mappings().all()
        for row in src:
            stats["source_messages"]+=1
            if row["source_message_id"] in existing:continue
            try:
                for rec in parse_message(row):
                    fp=hashlib.sha256(f"{row['source_message_id']}|{rec['idx']}|{norm(rec['raw']).lower()}".encode()).hexdigest()
                    ar=rec["area"];bu=rec["budget"];locs=rec["locs"]
                    c.execute(text("""INSERT INTO wai_clean_records(id,source_message_id,source_group,record_type,transaction,raw_details,contact_no,all_contacts,budget_text,budget_min,budget_max,budget_period,area_text,area_min,area_max,area_unit,area_sqft_min,area_sqft_max,location,all_locations,property_type,person_name,confidence,status,rejection_reason,source_fingerprint,source_created_at) VALUES(:id,:mid,:grp,:rtype,:tx,:raw,:phone,CAST(:phones AS jsonb),:btxt,:bmin,:bmax,:bperiod,:atxt,:amin,:amax,:aunit,:asmin,:asmax,:loc,CAST(:locs AS jsonb),:ptype,:person,:conf,:status,:reason,:fp,:sent) ON CONFLICT(source_fingerprint) DO NOTHING"""),
                    {"id":uuid.uuid4(),"mid":row["source_message_id"],"grp":row["source_group"] or "","rtype":rec["rtype"],"tx":rec["tx"],"raw":rec["raw"],"phone":rec["phone"],"phones":json.dumps(rec["phones"]),"btxt":bu[0],"bmin":bu[1],"bmax":bu[2],"bperiod":bu[3],"atxt":ar[0],"amin":ar[1],"amax":ar[2],"aunit":ar[3],"asmin":ar[4],"asmax":ar[5],"loc":locs[0] if locs else None,"locs":json.dumps(locs),"ptype":rec["ptype"],"person":row["sender_display_name"] or "","conf":rec["confidence"],"status":rec["status"],"reason":rec["reason"],"fp":fp,"sent":row["sent_at"]})
                    if rec["rtype"]=="INVENTORY":stats["inventory_rows"]+=1
                    elif rec["rtype"]=="REQUIREMENT":stats["requirement_rows"]+=1
                    else:stats["rejected_rows"]+=1
            except Exception:stats["failed"]+=1
        c.execute(text("""UPDATE wai_clean_runs SET completed_at=NOW(),source_messages=:s,inventory_rows=:i,requirement_rows=:r,rejected_rows=:x,failed=:f WHERE id=:id"""),{"s":stats["source_messages"],"i":stats["inventory_rows"],"r":stats["requirement_rows"],"x":stats["rejected_rows"],"f":stats["failed"],"id":run_id})
    return stats

def shell(title,body):
    links=[("Dashboard","/whatsapp-capture/intelligence/clean"),("Inventory","/whatsapp-capture/intelligence/clean/properties"),("Requirements","/whatsapp-capture/intelligence/clean/requirements"),("Contacts","/whatsapp-capture/intelligence/clean/contacts"),("Rejected","/whatsapp-capture/intelligence/clean/rejected"),("Excel","/whatsapp-capture/intelligence/clean/export"),("← Sources","/whatsapp-capture/intelligence/accounts")]
    nav=" ".join(f"<a href='{u}'>{html.escape(n)}</a>" for n,u in links)
    return f"""<!doctype html><html><head><meta charset=utf-8><style>body{{font-family:Arial;margin:0;background:#f5f7fb}}header{{background:#fff;padding:18px 22px;border-bottom:1px solid #ddd}}nav{{display:flex;gap:12px;flex-wrap:wrap}}nav a{{text-decoration:none;font-weight:700}}main{{padding:20px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}}.card{{background:#fff;padding:14px;border:1px solid #ddd;border-radius:9px}}.num{{font-size:28px;font-weight:800}}.scroll{{overflow:auto;background:#fff;border:1px solid #ddd}}table{{border-collapse:collapse;width:100%;min-width:1300px}}th,td{{padding:9px;border-bottom:1px solid #ddd;vertical-align:top;text-align:left}}th{{background:#fafafa}}td.raw{{min-width:420px}}.btn{{display:inline-block;padding:9px 12px;background:#155eef;color:#fff;text-decoration:none;border-radius:7px;font-weight:700}}.green{{background:#067647}}.muted{{color:#667085}}</style></head><body><header><h2>WhatsApp Clean Property Database</h2><div class=muted>Structured · Searchable · One property / one requirement per row</div><nav>{nav}</nav></header><main>{body}</main></body></html>"""

@router.get("",response_class=HTMLResponse)
def dashboard():
    init_clean_db()
    with engine.begin() as c:
        r=c.execute(text("""SELECT COUNT(*) FILTER(WHERE record_type='INVENTORY') inv,COUNT(*) FILTER(WHERE record_type='REQUIREMENT') req,COUNT(*) FILTER(WHERE record_type='REJECTED') rej,COUNT(DISTINCT NULLIF(contact_no,'')) contacts,MAX(processed_at) last_processed FROM wai_clean_records""")).mappings().first()
    body=f"""<div class=grid><div class=card><div>Inventory</div><div class=num>{r['inv']}</div></div><div class=card><div>Requirements</div><div class=num>{r['req']}</div></div><div class=card><div>Rejected Noise</div><div class=num>{r['rej']}</div></div><div class=card><div>Contacts</div><div class=num>{r['contacts']}</div></div></div><br><div class=card><b>Rule:</b> one entity per row. Mixed WhatsApp messages are split. News/jobs/social posts stay out.</div><br><a class='btn green' href='/whatsapp-capture/intelligence/clean/rebuild'>FULL REBUILD FROM RAW WHATSAPP</a> <a class=btn href='/whatsapp-capture/intelligence/clean/refresh'>PROCESS NEW ONLY</a><p class=muted>Last processed: {esc(r['last_processed'])}</p>"""
    return HTMLResponse(shell("Dashboard",body))

@router.get("/refresh")
def refresh():
    refresh_clean_database(False);return RedirectResponse("/whatsapp-capture/intelligence/clean",303)

@router.get("/rebuild")
def rebuild():
    refresh_clean_database(True);return RedirectResponse("/whatsapp-capture/intelligence/clean",303)

def get_rows(rtype):
    with engine.begin() as c:return c.execute(text("SELECT * FROM wai_clean_records WHERE record_type=:r ORDER BY source_created_at DESC NULLS LAST LIMIT 5000"),{"r":rtype}).mappings().all()

@router.get("/properties",response_class=HTMLResponse)
def properties():
    init_clean_db();rows=get_rows("INVENTORY")
    trs="".join(f"<tr><td class=raw>{esc(r['raw_details'])}</td><td>{esc(r['contact_no'])}</td><td>{esc(r['budget_text'])}</td><td>{esc(r['area_text'])}</td><td>{esc(r['source_group'])}</td><td>{esc(', '.join(r['all_locations'] or []))}</td><td>{esc(r['property_type'])}</td><td>{esc(r['transaction'])}</td><td>{esc(r['person_name'])}</td><td>{float(r['confidence'] or 0):.0f}%</td><td>{esc(r['status'])}</td></tr>" for r in rows)
    body=f"""<h2>Property Database</h2><div class=scroll><table><tr><th>Raw Property Details</th><th>Contact No.</th><th>Price / Rent</th><th>Area</th><th>Source Group</th><th>Location</th><th>Property Type</th><th>Transaction</th><th>Poster / Broker</th><th>Confidence</th><th>Status</th></tr>{trs}</table></div>"""
    return HTMLResponse(shell("Property Database",body))

@router.get("/requirements",response_class=HTMLResponse)
def requirements():
    init_clean_db();rows=get_rows("REQUIREMENT")
    trs="".join(f"<tr><td class=raw>{esc(r['raw_details'])}</td><td>{esc(r['contact_no'])}</td><td>{esc(r['budget_text'])}</td><td>{esc(r['area_text'])}</td><td>{esc(r['source_group'])}</td><td>{esc(', '.join(r['all_locations'] or []))}</td><td>{esc(r['property_type'])}</td><td>{esc(r['person_name'])}</td><td>{float(r['confidence'] or 0):.0f}%</td><td>{esc(r['status'])}</td></tr>" for r in rows)
    body=f"""<h2>Requirements</h2><div class=scroll><table><tr><th>Raw Requirement Details</th><th>Contact No.</th><th>Budget</th><th>Area</th><th>Source Group</th><th>Location</th><th>Property Type</th><th>Client / Broker</th><th>Confidence</th><th>Status</th></tr>{trs}</table></div>"""
    return HTMLResponse(shell("Requirements",body))

@router.get("/contacts",response_class=HTMLResponse)
def contacts():
    init_clean_db()
    with engine.begin() as c:rows=c.execute(text("""SELECT contact_no,MAX(NULLIF(person_name,'')) person_name,STRING_AGG(DISTINCT source_group,' | ') source_groups,COUNT(*) FILTER(WHERE record_type='INVENTORY') inventory_count,COUNT(*) FILTER(WHERE record_type='REQUIREMENT') requirement_count,MAX(source_created_at) last_seen,ROUND(AVG(confidence),0) confidence FROM wai_clean_records WHERE COALESCE(contact_no,'')<>'' GROUP BY contact_no ORDER BY last_seen DESC""")).mappings().all()
    trs="".join(f"<tr><td>{esc(r['contact_no'])}</td><td>{esc(r['person_name'])}</td><td>{esc(r['source_groups'])}</td><td>{r['inventory_count']}</td><td>{r['requirement_count']}</td><td>{esc(r['last_seen'])}</td><td>{float(r['confidence'] or 0):.0f}%</td></tr>" for r in rows)
    return HTMLResponse(shell("Contacts",f"<h2>Contacts Database</h2><div class=scroll><table><tr><th>Contact No.</th><th>Name</th><th>Source Groups</th><th>Inventory</th><th>Requirements</th><th>Last Seen</th><th>Confidence</th></tr>{trs}</table></div>"))

@router.get("/rejected",response_class=HTMLResponse)
def rejected():
    init_clean_db();rows=get_rows("REJECTED")
    trs="".join(f"<tr><td class=raw>{esc(r['raw_details'])}</td><td>{esc(r['rejection_reason'])}</td><td>{esc(r['source_group'])}</td><td>{esc(r['contact_no'])}</td></tr>" for r in rows)
    return HTMLResponse(shell("Rejected",f"<h2>Rejected / Noise</h2><div class=scroll><table><tr><th>Raw Message</th><th>Reason</th><th>Source Group</th><th>Contact</th></tr>{trs}</table></div>"))

@router.get("/export")
def export():
    init_clean_db();inv=get_rows("INVENTORY");req=get_rows("REQUIREMENT")
    wb=Workbook();ws=wb.active;ws.title="Inventory";ws.append(["Raw Property Details","Contact No.","Price / Rent","Area","Source Group","Location","Property Type","Transaction","Poster / Broker","Confidence %","Status"])
    for r in inv:ws.append([r["raw_details"],r["contact_no"],r["budget_text"],r["area_text"],r["source_group"],", ".join(r["all_locations"] or []),r["property_type"],r["transaction"],r["person_name"],r["confidence"],r["status"]])
    wr=wb.create_sheet("Requirements");wr.append(["Raw Requirement Details","Contact No.","Budget","Area","Source Group","Location","Property Type","Client / Broker","Confidence %","Status"])
    for r in req:wr.append([r["raw_details"],r["contact_no"],r["budget_text"],r["area_text"],r["source_group"],", ".join(r["all_locations"] or []),r["property_type"],r["person_name"],r["confidence"],r["status"]])
    for sh in wb.worksheets:
        for c in sh[1]:c.font=Font(bold=True);c.alignment=Alignment(wrap_text=True)
        for col in range(1,sh.max_column+1):sh.column_dimensions[get_column_letter(col)].width=24
    bio=io.BytesIO();wb.save(bio);bio.seek(0)
    return StreamingResponse(bio,media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":"attachment; filename=Alliance_WhatsApp_Clean_Database.xlsx"})

try:init_clean_db()
except Exception as e:print("WhatsApp clean DB init warning:",e)
