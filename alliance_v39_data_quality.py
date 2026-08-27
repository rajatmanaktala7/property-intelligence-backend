
from __future__ import annotations
import os,re,hashlib,json
from fastapi import APIRouter, Request, Query, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import create_engine, text

VERSION="3.9.0-DATA-QUALITY-CLEAN-SOURCES"

def _db_url(u):
    u=(u or "").strip()
    if u.startswith("postgres://"): return u.replace("postgres://","postgresql+psycopg://",1)
    if u.startswith("postgresql://"): return u.replace("postgresql://","postgresql+psycopg://",1)
    return u

def _wa_engine():
    u=os.getenv("WHATSAPP_DATABASE_URL","").strip()
    return create_engine(_db_url(u),pool_pre_ping=True,pool_recycle=300,connect_args={"connect_timeout":5}) if u else None

def _norm(v):
    return re.sub(r"\s+"," ",re.sub(r"[^A-Z0-9]+"," ",str(v or "").upper())).strip()

def _phone(textv):
    m=re.search(r"(?<!\d)(?:\+?91[\s-]?)?([6-9]\d{9})(?!\d)",textv or "")
    return m.group(1) if m else None

def _money(v):
    if v in (None,""): return None
    if isinstance(v,(int,float)): return float(v)
    s=str(v).lower().replace(",","").replace("₹","").strip()
    m=re.search(r"(\d+(?:\.\d+)?)",s)
    if not m:return None
    n=float(m.group(1))
    if "crore" in s or re.search(r"\bcr\b",s): n*=10_000_000
    elif "lakh" in s or "lac" in s or re.search(r"\bl\b",s): n*=100_000
    elif re.search(r"\bk\b",s): n*=1_000
    return n

def _area_sqft(line):
    m=re.search(r"(\d{3,6}(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|sft)\b",line,re.I)
    return float(m.group(1)) if m else None

def _yards(line):
    m=re.search(r"(\d{2,4})\s*(?:syds|sq\s*yds|sqyds|yards|yds)\b",line,re.I)
    return float(m.group(1)) if m else None

def _contact(raw):
    phone=_phone(raw)
    name=None
    for p in [
        r"(?is)(?:please\s+contact|plz\.?\s*cont\.?|more\s+details\s+call|contact)\s*[:\-]?\s*\*?([A-Za-z][A-Za-z .&]{2,50})\*?\s*(?:mob|mobile|ph|phone)?\s*[:\-]?\s*(?:\+?91[\s-]?)?[6-9]\d{9}",
        r"(?is)\*?([A-Za-z][A-Za-z .&]{2,50})\*?\s*(?:mob|mobile|ph|phone)\s*[:\-]?\s*(?:\+?91[\s-]?)?[6-9]\d{9}",
    ]:
        m=re.search(p,raw or "")
        if m:
            name=re.sub(r"[\*_]+","",m.group(1)).strip()
            break
    return name,phone

def _fingerprint(parts):
    basis="|".join(_norm(x) for x in parts if x not in (None,""))
    return hashlib.sha256(basis.encode()).hexdigest()

def _heading(line):
    raw=(line or "").strip()
    x=re.sub(r"[\*_`]+","",raw).strip()
    if not x:return None
    up=x.upper()
    if any(g in up for g in [
        "INVENTORY FOR SALE","AVAILABLE FOR RENT","AVAIL FOR RENT",
        "NEW FLOORS IN RESALE","LEASE TERMS","RENTALS",
        "MORE DETAILS CALL","PLEASE CONTACT WITH SERIOUS BUYER"
    ]): return None
    if re.search(r"\b(BHK|SQFT|SQ FT|SYDS|RENT|DEMAND|ASKING|TENANT|SIZE)\b",up): return None
    if len(x)>80:return None
    if raw.startswith("*") and raw.endswith("*"): return x
    if re.search(r"\b(?:DLF\s*PHASE\s*[124]|SHUSHANT\s*LOK\s*1|SUSHANT\s*LOK\s*1|SECTOR\s*\d+|SEC-\d+)\b",up):
        return x
    return None

def _sale_entity(project,line,contact_name,phone):
    area=_area_sqft(line)
    if area is None:return None
    cfgm=re.search(r"(?i)\b(\d+(?:/\d+)?\s*BHK(?:\s*\+\s*SER)?)\b",line)
    cfg=cfgm.group(1).upper() if cfgm else None
    pm=re.search(r"@\s*([\d.]+\s*(?:cr|crore|lakh|lac))",line,re.I)
    price=_money(pm.group(1)) if pm else None
    if not (cfg or price):return None
    notes=" | ".join(x for x in [project,cfg,f"{int(area)} sqft",f"Sale ₹{price:,.0f}" if price else None] if x)
    return {
        "fingerprint":_fingerprint(["WHATSAPP",project,cfg,area,"SALE",price,phone]),
        "lead_type":"SALE","locality":project,"area":str(int(area)),
        "configuration_details":cfg or "","price":price,
        "agency_brand":contact_name or "","contact_person":contact_name or "",
        "phone_numbers":phone or "","notes":notes,"source":"WhatsApp",
        "completeness":90,"verification":"Unverified","raw_text":line,"source_group":None
    }

def _rent_entities(location,lines,contact_name,phone):
    joined=" ".join(x.strip() for x in lines if x.strip())
    yards=_yards(joined)
    if yards is None:return []
    cfgm=re.search(r"(?i)\b(\d+(?:/\d+)?\s*BHK(?:\s*\+\s*SER)?)\b",joined)
    cfg=cfgm.group(1).upper() if cfgm else None
    furnished="FULLY FURNISHED" in joined.upper()
    semi="SEMI FURNISHED" in joined.upper()

    rents=[]
    for m in re.finditer(r"(?i)(?:rent\s*)?(\d+(?:\.\d+)?)\s*(LAC|LAKH|L|K)\b",joined):
        val=_money(m.group(1)+" "+m.group(2))
        if val and val not in rents:rents.append(val)
    if not rents:
        m=re.search(r"(?i)rent\s*(\d{4,6})\b",joined)
        if m:rents=[float(m.group(1))]
    if not rents:return []

    out=[]
    for idx,rent in enumerate(rents):
        variant=""
        if len(rents)>1:
            variant="Fully Furnished" if idx==len(rents)-1 and furnished else ("Semi Furnished" if idx==0 and semi else "")
        elif furnished: variant="Fully Furnished"
        elif semi: variant="Semi Furnished"
        notes=" | ".join(x for x in [location,cfg,f"{int(yards)} sq yd",variant,f"Rent ₹{rent:,.0f}"] if x)
        out.append({
            "fingerprint":_fingerprint(["WHATSAPP",location,cfg,yards,"LEASE",rent,variant,phone]),
            "lead_type":"LEASE","locality":location,"area":f"{int(yards)} sq yd",
            "configuration_details":" | ".join(x for x in [cfg,variant] if x),
            "price":rent,"agency_brand":contact_name or "","contact_person":contact_name or "",
            "phone_numbers":phone or "","notes":notes,"source":"WhatsApp",
            "completeness":88,"verification":"Unverified","raw_text":"\n".join(lines),"source_group":None
        })
    return out

def split_whatsapp_message(raw,source_group=None,sender_name=None,sender_phone=None):
    raw=(raw or "").replace("\r","\n")
    contact_name,phone=_contact(raw)
    contact_name=contact_name or sender_name
    phone=phone or sender_phone
    lines=[x.rstrip() for x in raw.splitlines()]
    entities=[]
    current=None
    i=0
    while i<len(lines):
        line=lines[i].strip()
        h=_heading(line)
        if h:
            current=h
            i+=1
            continue

        if current:
            sale=_sale_entity(current,line,contact_name,phone)
            if sale:
                sale["source_group"]=source_group
                entities.append(sale)
                i+=1
                continue

        if current and re.search(r"(?i)\b\d{2,4}\s*(?:syds|sq\s*yds|sqyds|yards|yds)\b",line) and re.search(r"(?i)\b\d+(?:/\d+)?\s*BHK\b",line):
            block=[line]
            j=i+1
            while j<len(lines):
                nxt=lines[j].strip()
                if _heading(nxt):break
                if re.search(r"(?i)\b\d{2,4}\s*(?:syds|sq\s*yds|sqyds|yards|yds)\b",nxt) and re.search(r"(?i)\b\d+(?:/\d+)?\s*BHK\b",nxt):break
                if not nxt and len(block)>=3:break
                block.append(nxt)
                j+=1
            for e in _rent_entities(current,block,contact_name,phone):
                e["source_group"]=source_group
                entities.append(e)
            i=max(j,i+1)
            continue
        i+=1

    uniq={}
    for e in entities: uniq.setdefault(e["fingerprint"],e)
    return list(uniq.values())

def register(core):
    app=core.app
    engine=core.engine
    need_login=core.need_login
    page_role_or_redirect=core.page_role_or_redirect
    router=APIRouter()

    def ensure_aux():
        with engine.begin() as c:
            c.execute(text("""
            CREATE TABLE IF NOT EXISTS pi_duplicate_archive(
              id BIGSERIAL PRIMARY KEY,
              source_type TEXT NOT NULL,
              source_table TEXT NOT NULL,
              source_record_id TEXT,
              fingerprint TEXT,
              archived_row JSONB NOT NULL,
              archived_at TIMESTAMPTZ DEFAULT NOW()
            )"""))
            c.execute(text("""
            CREATE TABLE IF NOT EXISTS pi_whatsapp_clean_properties(
              id BIGSERIAL PRIMARY KEY,
              record_id TEXT UNIQUE NOT NULL,
              message_id TEXT,
              source_group TEXT,
              fingerprint TEXT UNIQUE NOT NULL,
              lead_type TEXT,
              locality TEXT,
              area TEXT,
              configuration_details TEXT,
              price NUMERIC(16,2),
              agency_brand TEXT,
              contact_person TEXT,
              phone_numbers TEXT,
              notes TEXT,
              source TEXT DEFAULT 'WhatsApp',
              completeness INTEGER DEFAULT 0,
              verification TEXT DEFAULT 'Unverified',
              raw_text TEXT,
              created_at TIMESTAMPTZ DEFAULT NOW(),
              updated_at TIMESTAMPTZ DEFAULT NOW()
            )"""))

    @router.get("/api/v39/status")
    def status(req:Request):
        need_login(req)
        return {"version":VERSION,"status":"OK","startup_db_work":False,
                "newspaper_clean_database":"/newspaper-database-clean",
                "whatsapp_clean_database":"/whatsapp-database-clean"}

    @router.post("/api/v39/setup")
    def setup(req:Request):
        need_login(req);ensure_aux()
        return {"status":"READY","version":VERSION}

    @router.post("/api/v39/newspaper/dedupe")
    def newspaper_dedupe(req:Request):
        need_login(req);ensure_aux()
        with engine.begin() as c:
            dupes=c.execute(text("""
            WITH ranked AS (
              SELECT *,
                ROW_NUMBER() OVER(
                  PARTITION BY fingerprint
                  ORDER BY CASE WHEN UPPER(COALESCE(verification,''))='VERIFIED' THEN 0 ELSE 1 END,id DESC
                ) rn
              FROM pi_newspaper_properties
              WHERE fingerprint IS NOT NULL AND fingerprint<>''
            )
            SELECT * FROM ranked WHERE rn>1
            """)).mappings().all()

            for d in dupes:
                row=dict(d);row.pop("rn",None)
                serial={k:(v.isoformat() if hasattr(v,"isoformat") else v) for k,v in row.items()}
                c.execute(text("""
                  INSERT INTO pi_duplicate_archive(source_type,source_table,source_record_id,fingerprint,archived_row)
                  VALUES('NEWSPAPER','pi_newspaper_properties',:rid,:fp,CAST(:row AS JSONB))
                """),{"rid":str(row.get("record_id") or row.get("id")),"fp":row.get("fingerprint"),
                      "row":json.dumps(serial,default=str)})

            if dupes:
                c.execute(text("DELETE FROM pi_newspaper_properties WHERE id = ANY(:ids)"),
                          {"ids":[int(d["id"]) for d in dupes]})

            c.execute(text("""
              CREATE UNIQUE INDEX IF NOT EXISTS uq_pi_newspaper_properties_fingerprint
              ON pi_newspaper_properties(fingerprint)
              WHERE fingerprint IS NOT NULL AND fingerprint<>''
            """))
        return {"status":"OK","duplicates_archived_and_removed":len(dupes)}

    @router.post("/api/v39/whatsapp/rebuild-clean")
    def rebuild_whatsapp(req:Request,limit:int=Query(2000,ge=1,le=5000)):
        need_login(req);ensure_aux()
        w=_wa_engine()
        if w is None:raise HTTPException(503,"WHATSAPP_DATABASE_URL not configured")
        with w.connect() as c:
            rows=c.execute(text("""
              SELECT m.message_id,m.raw_text,m.created_at,m.sender_name,m.sender_phone,s.group_name
              FROM wa_messages m LEFT JOIN wa_sources s ON s.source_id=m.source_id
              WHERE m.classification='PROPERTY_INVENTORY'
              ORDER BY m.id DESC LIMIT :lim
            """),{"lim":limit}).mappings().all()

        created=0;merged=0
        with engine.begin() as c:
            for r in rows:
                for e in split_whatsapp_message(r["raw_text"],r.get("group_name"),r.get("sender_name"),r.get("sender_phone")):
                    rid="WA-"+hashlib.sha1((str(r["message_id"])+"|"+e["fingerprint"]).encode()).hexdigest()[:12].upper()
                    inserted=c.execute(text("""
                      INSERT INTO pi_whatsapp_clean_properties(
                        record_id,message_id,source_group,fingerprint,lead_type,locality,area,
                        configuration_details,price,agency_brand,contact_person,phone_numbers,notes,
                        source,completeness,verification,raw_text)
                      VALUES(:rid,:mid,:grp,:fp,:lead_type,:locality,:area,:configuration_details,:price,
                        :agency_brand,:contact_person,:phone_numbers,:notes,'WhatsApp',:completeness,:verification,:raw_text)
                      ON CONFLICT(fingerprint) DO UPDATE SET
                        source_group=EXCLUDED.source_group,
                        contact_person=COALESCE(NULLIF(EXCLUDED.contact_person,''),pi_whatsapp_clean_properties.contact_person),
                        phone_numbers=COALESCE(NULLIF(EXCLUDED.phone_numbers,''),pi_whatsapp_clean_properties.phone_numbers),
                        notes=EXCLUDED.notes,raw_text=EXCLUDED.raw_text,updated_at=NOW()
                      RETURNING (xmax = 0)
                    """),{"rid":rid,"mid":str(r["message_id"]),"grp":e["source_group"],"fp":e["fingerprint"],
                          "lead_type":e["lead_type"],"locality":e["locality"],"area":e["area"],
                          "configuration_details":e["configuration_details"],"price":e["price"],
                          "agency_brand":e["agency_brand"],"contact_person":e["contact_person"],
                          "phone_numbers":e["phone_numbers"],"notes":e["notes"],
                          "completeness":e["completeness"],"verification":e["verification"],"raw_text":e["raw_text"]}).scalar()
                    if inserted:created+=1
                    else:merged+=1
        return {"status":"OK","clean_single_property_rows":created,"duplicates_merged":merged}

    def _rows(table,q,limit):
        where="";p={"lim":limit}
        if q.strip():
            where="""WHERE COALESCE(locality,'') ILIKE :q OR COALESCE(configuration_details,'') ILIKE :q
                     OR COALESCE(contact_person,'') ILIKE :q OR COALESCE(phone_numbers,'') ILIKE :q
                     OR COALESCE(notes,'') ILIKE :q"""
            p["q"]="%"+q.strip()+"%"
        with engine.connect() as c:
            rr=c.execute(text(f"SELECT * FROM {table} {where} ORDER BY id DESC LIMIT :lim"),p).mappings().all()
        out=[];seen=set()
        for r in rr:
            d=dict(r);fp=d.get("fingerprint") or _fingerprint([d.get("locality"),d.get("area"),d.get("configuration_details"),d.get("price"),d.get("phone_numbers")])
            if fp in seen:continue
            seen.add(fp)
            for k,v in list(d.items()):
                if hasattr(v,"isoformat"):d[k]=v.isoformat()
            out.append(d)
        return out

    @router.get("/api/v39/newspaper/rows")
    def newspaper_rows(req:Request,q:str="",limit:int=Query(500,ge=1,le=2000)):
        need_login(req);r=_rows("pi_newspaper_properties",q,limit)
        return {"source":"NEWSPAPER","count":len(r),"rows":r}

    @router.get("/api/v39/whatsapp/rows")
    def whatsapp_rows(req:Request,q:str="",limit:int=Query(500,ge=1,le=2000)):
        need_login(req);ensure_aux();r=_rows("pi_whatsapp_clean_properties",q,limit)
        return {"source":"WHATSAPP","count":len(r),"rows":r}

    @router.get("/newspaper-database-clean",response_class=HTMLResponse)
    def newspaper_page(req:Request):
        if not page_role_or_redirect(req):return RedirectResponse("/login",303)
        return HTMLResponse(PAGE.replace("__SOURCE__","NEWSPAPER").replace("__TITLE__","Newspaper Property Database"))

    @router.get("/whatsapp-database-clean",response_class=HTMLResponse)
    def whatsapp_page(req:Request):
        if not page_role_or_redirect(req):return RedirectResponse("/login",303)
        return HTMLResponse(PAGE.replace("__SOURCE__","WHATSAPP").replace("__TITLE__","WhatsApp Group Property Database"))

    app.include_router(router)
    return router

PAGE=r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title><style>
body{margin:0;font-family:Arial;background:#efe4d2;color:#2d261f}header{background:#5d4937;color:#fff;padding:15px 20px}
.wrap{max-width:1600px;margin:auto;padding:18px}.card{background:#fffdf9;border:1px solid #dccdbb;border-radius:14px;padding:16px;margin-bottom:14px}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}.btn,button{padding:9px 12px;border:0;border-radius:8px;background:#6c543f;color:#fff;font-weight:800;text-decoration:none;cursor:pointer}
input{padding:9px;border:1px solid #d8c8b4;border-radius:8px;min-width:300px}.tablewrap{overflow:auto;max-height:72vh;border:1px solid #ddcfbd;border-radius:10px}
table{width:100%;border-collapse:collapse;min-width:1450px;background:#fff}th,td{padding:9px;border-bottom:1px solid #eee0ce;text-align:left;font-size:12px;vertical-align:top}
th{background:#f7ecdf;position:sticky;top:0}.notes{max-width:420px;white-space:pre-wrap}.phone{font-weight:900}
</style></head><body><header><b>__TITLE__</b> · Clean Single-Property Records</header><div class=wrap>
<div class=card><div class=toolbar><a class=btn href="/workspace">← Dashboard</a><a class=btn href="/newspaper-v83">Newspaper Upload</a><a class=btn href="/whatsapp-live">WhatsApp Live</a></div></div>
<div class=card><div class=toolbar><input id=q placeholder="Search location, configuration, contact, phone, description"><button onclick=load()>Search</button><button onclick=cleanNow()>Clean / Rebuild</button></div><div id=count></div>
<div class=tablewrap><table><thead><tr><th>Record</th><th>Type</th><th>Location / Project</th><th>Area</th><th>Configuration</th><th>Price / Rent</th><th>Agency / Broker</th><th>Contact</th><th>Phone</th><th>Description</th><th>Verification</th><th>Source / Group</th></tr></thead><tbody id=rows></tbody></table></div></div></div>
<script>
const SOURCE="__SOURCE__";const E=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
async function J(u,o={}){let r=await fetch(u,{credentials:'include',...o});let t=await r.text();let d;try{d=JSON.parse(t)}catch(e){d={detail:t}};if(!r.ok)throw Error(d.detail||t);return d}
async function load(){let d=await J('/api/v39/'+SOURCE.toLowerCase()+'/rows?q='+encodeURIComponent(q.value||'')+'&limit=1000');count.textContent=d.count+' clean unique records';rows.innerHTML=(d.rows||[]).map(x=>`<tr><td>${E(x.record_id||x.id)}</td><td>${E(x.lead_type)}</td><td><b>${E(x.locality)}</b></td><td>${E(x.area)}</td><td>${E(x.configuration_details)}</td><td>${E(x.price)}</td><td>${E(x.agency_brand)}</td><td>${E(x.contact_person)}</td><td class=phone>${E(x.phone_numbers)}</td><td class=notes>${E(x.notes)}</td><td>${E(x.verification)}</td><td>${E(x.source_group||x.source)}</td></tr>`).join('')||'<tr><td colspan=12>No records.</td></tr>'}
async function cleanNow(){let u=SOURCE==='NEWSPAPER'?'/api/v39/newspaper/dedupe':'/api/v39/whatsapp/rebuild-clean';let d=await J(u,{method:'POST'});alert(JSON.stringify(d,null,2));load()}load();
</script></body></html>"""
