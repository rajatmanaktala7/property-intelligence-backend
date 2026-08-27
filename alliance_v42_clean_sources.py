from __future__ import annotations
import os,re,hashlib,uuid
from fastapi import APIRouter,Request,Query,HTTPException
from fastapi.responses import HTMLResponse,RedirectResponse
from sqlalchemy import create_engine,text
import alliance_v41b_whatsapp_splitter as base

VERSION="4.2.0-NEWSPAPER-FORMAT-WHATSAPP-DEDUPE"

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

def _canonical_key(lead_type,locality,area,config,price,phone):
    # Phone intentionally excluded from the property identity so the same property
    # from multiple brokers/groups merges into one property while contacts aggregate.
    parts=[lead_type,locality,area,config,price]
    return hashlib.sha256("|".join(_norm(x) for x in parts).encode()).hexdigest()

def _money_label(v,txn):
    if v in (None,""): return None
    try:n=float(v)
    except:return str(v)
    if n>=10_000_000:return f"₹{n/10_000_000:.2f} Cr"
    if n>=100_000:return f"₹{n/100_000:.2f} Lakh" if txn=="Sale" else f"₹{n/100_000:.2f} Lakh/month"
    return f"₹{n:,.0f}" if txn=="Sale" else f"₹{n:,.0f}/month"

def _ensure(engine):
    with engine.begin() as c:
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS pi_whatsapp_newspaper_format_generation(
          id BIGSERIAL PRIMARY KEY,
          generation_id UUID UNIQUE NOT NULL,
          started_at TIMESTAMPTZ DEFAULT NOW(),
          completed_at TIMESTAMPTZ,
          raw_messages INTEGER DEFAULT 0,
          bursts INTEGER DEFAULT 0,
          extracted_children INTEGER DEFAULT 0,
          canonical_rows INTEGER DEFAULT 0,
          requirements_filtered INTEGER DEFAULT 0,
          duplicates_merged INTEGER DEFAULT 0,
          skipped_non_specific INTEGER DEFAULT 0,
          status TEXT DEFAULT 'RUNNING'
        )"""))
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS pi_whatsapp_newspaper_format(
          id BIGSERIAL PRIMARY KEY,
          generation_id UUID NOT NULL,
          canonical_key TEXT NOT NULL,
          record_id TEXT NOT NULL,
          lead_type TEXT,
          locality TEXT,
          area TEXT,
          configuration_details TEXT,
          price TEXT,
          agency_brand TEXT,
          contact_person TEXT,
          phone_numbers TEXT,
          notes TEXT,
          source TEXT,
          completeness INTEGER,
          verification TEXT DEFAULT 'Unverified',
          team_member TEXT DEFAULT '',
          raw_message TEXT,
          source_count INTEGER DEFAULT 1,
          created_at TIMESTAMPTZ DEFAULT NOW(),
          UNIQUE(generation_id,canonical_key)
        )"""))

def _is_specific(rec):
    # Reject broad inventory summaries / partial scraps.
    locality=_norm(rec.get("project_name") or rec.get("locality"))
    if any(x in locality for x in ["NEW FLOORS IN RESALE","4 5 6 BHK KOTHI","INVENTORY FOR SALE"]):
        return False
    if not (rec.get("project_name") or rec.get("locality")):
        return False
    if not rec.get("area_value"):
        return False
    if rec.get("transaction_type") not in ("Sale","Rent"):
        return False
    if rec.get("transaction_type")=="Sale" and not rec.get("price_value"):
        return False
    if rec.get("transaction_type")=="Rent" and not rec.get("rent_value"):
        return False
    return True

def _to_newspaper_row(rec):
    txn=rec.get("transaction_type")
    locality=rec.get("project_name") or rec.get("locality") or rec.get("city") or ""
    area=""
    if rec.get("area_value") not in (None,""):
        val=rec.get("area_value")
        try:val=str(int(float(val)))
        except:val=str(val)
        area=(val+" "+str(rec.get("area_unit") or "")).strip()
    price_num=rec.get("price_value") if txn=="Sale" else rec.get("rent_value")
    price=_money_label(price_num,txn)
    phone=rec.get("broker_phone") or ""
    key=_canonical_key(txn,locality,area,rec.get("configuration"),price_num,phone)
    source=rec.get("source_group") or "WhatsApp Group"
    notes=" | ".join(x for x in [
        locality,
        rec.get("property_type"),
        rec.get("configuration"),
        area,
        price,
        rec.get("floor"),
    ] if x)
    return {
        "canonical_key":key,
        "record_id":"WA-"+key[:10].upper(),
        "lead_type":txn.upper() if txn else "",
        "locality":locality,
        "area":area,
        "configuration_details":rec.get("configuration") or rec.get("property_type") or "",
        "price":price or "",
        "agency_brand":rec.get("broker_name") or "",
        "contact_person":rec.get("broker_name") or "",
        "phone_numbers":phone,
        "notes":notes,
        "source":source,
        "completeness":int(rec.get("confidence") or 0),
        "verification":"Unverified",
        "team_member":"",
        "raw_message":rec.get("raw_message") or "",
    }

def _serialize(rows):
    out=[]
    for r in rows:
        d=dict(r)
        for k,v in list(d.items()):
            if isinstance(v,uuid.UUID):d[k]=str(v)
            elif hasattr(v,"isoformat"):d[k]=v.isoformat()
        out.append(d)
    return out

def register(core):
    app=core.app;engine=core.engine;need_login=core.need_login;page_role_or_redirect=core.page_role_or_redirect
    router=APIRouter()

    @router.get("/api/v42/status")
    def status(req:Request):
        need_login(req)
        return {
          "version":VERSION,"status":"OK","startup_db_work":False,
          "whatsapp_database":"/whatsapp-database-v42",
          "newspaper_database":"/newspaper-database-v42",
          "whatsapp_columns":"NEWSPAPER_COMPATIBLE",
          "newspaper_dedupe":"NON_DESTRUCTIVE_CANONICAL"
        }

    @router.post("/api/v42/setup")
    def setup(req:Request):
        need_login(req);_ensure(engine)
        return {"version":VERSION,"status":"READY"}

    @router.post("/api/v42/whatsapp/rebuild")
    def rebuild(req:Request,limit:int=Query(1000,ge=1,le=10000)):
        need_login(req);_ensure(engine)
        w=_wa_engine()
        if w is None:raise HTTPException(503,"WHATSAPP_DATABASE_URL not configured")
        with w.connect() as c:
            rows=c.execute(text("""
              SELECT m.message_id,m.raw_text,m.created_at,m.sender_name,m.sender_phone,
                     m.source_id,s.group_name
              FROM wa_messages m LEFT JOIN wa_sources s ON s.source_id=m.source_id
              ORDER BY m.created_at ASC NULLS LAST,m.id ASC LIMIT :lim
            """),{"lim":limit}).mappings().all()

        bursts=base.group_message_bursts(rows,180)
        gen=uuid.uuid4()
        with engine.begin() as c:
            c.execute(text("INSERT INTO pi_whatsapp_newspaper_format_generation(generation_id,status) VALUES(:g,'RUNNING')"),{"g":gen})

        canonical={}; requirements=0; child_count=0; skipped=0
        for burst in bursts:
            parent="\n".join(str(x.get("raw_text") or "") for x in burst["rows"] if str(x.get("raw_text") or "").strip())
            if not parent.strip():continue
            if base.classify_listing_vs_requirement(parent)=="REQUIREMENT":
                requirements+=1;continue
            meta=burst["rows"][-1]
            base_children=base.split_multi_listing(parent)
            children=[]
            for b in base_children:
                children.extend(base.expand_specific_rent_variants(b))
            child_count+=len(children)
            for child in children:
                rec=base.normalize_listing(child,parent,meta)
                if not rec or not _is_specific(rec):
                    skipped+=1;continue
                row=_to_newspaper_row(rec)
                key=row["canonical_key"]
                if key not in canonical:
                    row["phones"]=set([p for p in re.findall(r"[6-9]\d{9}",row["phone_numbers"])])
                    row["sources"]=set([row["source"]] if row["source"] else [])
                    row["source_count"]=1
                    canonical[key]=row
                else:
                    x=canonical[key]
                    x["phones"].update(re.findall(r"[6-9]\d{9}",row["phone_numbers"]))
                    if row["source"]:x["sources"].add(row["source"])
                    if not x["contact_person"] and row["contact_person"]:
                        x["contact_person"]=row["contact_person"];x["agency_brand"]=row["agency_brand"]
                    x["source_count"]+=1
                    x["completeness"]=max(x["completeness"],row["completeness"])

        with engine.begin() as c:
            for key,row in canonical.items():
                c.execute(text("""
                  INSERT INTO pi_whatsapp_newspaper_format(
                    generation_id,canonical_key,record_id,lead_type,locality,area,configuration_details,
                    price,agency_brand,contact_person,phone_numbers,notes,source,completeness,
                    verification,team_member,raw_message,source_count)
                  VALUES(:g,:canonical_key,:record_id,:lead_type,:locality,:area,:configuration_details,
                    :price,:agency_brand,:contact_person,:phone_numbers,:notes,:source,:completeness,
                    :verification,:team_member,:raw_message,:source_count)
                """),{
                  "g":gen,**{k:v for k,v in row.items() if k not in {"phones","sources"}},
                  "phone_numbers":" | ".join(sorted(row["phones"])),
                  "source":" | ".join(sorted(row["sources"])),
                })
            c.execute(text("""
              UPDATE pi_whatsapp_newspaper_format_generation
              SET completed_at=NOW(),raw_messages=:raw,bursts=:bursts,extracted_children=:children,
                  canonical_rows=:can,requirements_filtered=:req,duplicates_merged=:dup,
                  skipped_non_specific=:skip,status='COMPLETED' WHERE generation_id=:g
            """),{
              "raw":len(rows),"bursts":len(bursts),"children":child_count,"can":len(canonical),
              "req":requirements,"dup":max(child_count-len(canonical)-skipped,0),"skip":skipped,"g":gen
            })
        return {
          "status":"OK","version":VERSION,"generation_id":str(gen),"raw_messages":len(rows),
          "bursts":len(bursts),"extracted_children":child_count,"canonical_rows":len(canonical),
          "requirements_filtered":requirements,
          "duplicates_merged":max(child_count-len(canonical)-skipped,0),
          "skipped_non_specific":skipped
        }

    @router.get("/api/v42/whatsapp/rows")
    def whatsapp_rows(req:Request,q:str="",limit:int=Query(1000,ge=1,le=3000)):
        need_login(req);_ensure(engine)
        p={"lim":limit};where=""
        if q.strip():
            where="""AND (COALESCE(locality,'') ILIKE :q OR COALESCE(configuration_details,'') ILIKE :q
                     OR COALESCE(contact_person,'') ILIKE :q OR COALESCE(phone_numbers,'') ILIKE :q
                     OR COALESCE(notes,'') ILIKE :q OR COALESCE(source,'') ILIKE :q)"""
            p["q"]="%"+q.strip()+"%"
        with engine.connect() as c:
            gen=c.execute(text("""SELECT generation_id FROM pi_whatsapp_newspaper_format_generation
                WHERE status='COMPLETED' ORDER BY completed_at DESC NULLS LAST,id DESC LIMIT 1""")).scalar()
            if not gen:return {"status":"REBUILD_REQUIRED","count":0,"rows":[]}
            p["g"]=gen
            rows=c.execute(text(f"""SELECT record_id,lead_type,locality,area,configuration_details,price,
                agency_brand,contact_person,phone_numbers,notes,source,completeness,verification,team_member,source_count
                FROM pi_whatsapp_newspaper_format WHERE generation_id=:g {where}
                ORDER BY id DESC LIMIT :lim"""),p).mappings().all()
        return {"status":"OK","generation_id":str(gen),"count":len(rows),"rows":_serialize(rows)}

    @router.get("/api/v42/newspaper/dedupe-status")
    def newspaper_dedupe_status(req:Request):
        need_login(req)
        with engine.connect() as c:
            src=c.execute(text("SELECT COUNT(*) FROM pi_newspaper_properties")).scalar() or 0
            can=c.execute(text("""
              WITH n AS (
                SELECT id,
                 regexp_replace(upper(COALESCE(lead_type,'')),'[^A-Z0-9]+','','g') k_type,
                 regexp_replace(upper(COALESCE(locality,'')),'[^A-Z0-9]+','','g') k_loc,
                 regexp_replace(upper(COALESCE(area,'')),'[^A-Z0-9]+','','g') k_area,
                 regexp_replace(upper(COALESCE(configuration_details,'')),'[^A-Z0-9]+','','g') k_cfg,
                 regexp_replace(upper(COALESCE(price,'')),'[^A-Z0-9]+','','g') k_price,
                 regexp_replace(COALESCE(phone_numbers,''),'[^0-9]+','','g') k_phone
                FROM pi_newspaper_properties
              )
              SELECT COUNT(*) FROM (
                SELECT 1 FROM n GROUP BY k_type,k_loc,k_area,k_cfg,k_price,k_phone
              ) x
            """)).scalar() or 0
        return {"status":"OK","source_rows":int(src),"canonical_unique_rows":int(can),"duplicates_hidden":int(src-can),"source_rows_deleted":0}

    @router.get("/api/v42/newspaper/rows")
    def newspaper_rows(req:Request,q:str="",limit:int=Query(1000,ge=1,le=3000)):
        need_login(req)
        p={"lim":limit};where=""
        if q.strip():
            where="""AND (COALESCE(locality,'') ILIKE :q OR COALESCE(configuration_details,'') ILIKE :q
                       OR COALESCE(contact_person,'') ILIKE :q OR COALESCE(phone_numbers,'') ILIKE :q
                       OR COALESCE(notes,'') ILIKE :q OR COALESCE(source,'') ILIKE :q)"""
            p["q"]="%"+q.strip()+"%"
        with engine.connect() as c:
            rows=c.execute(text(f"""
              WITH n AS (
                SELECT p.*,
                 regexp_replace(upper(COALESCE(lead_type,'')),'[^A-Z0-9]+','','g') k_type,
                 regexp_replace(upper(COALESCE(locality,'')),'[^A-Z0-9]+','','g') k_loc,
                 regexp_replace(upper(COALESCE(area,'')),'[^A-Z0-9]+','','g') k_area,
                 regexp_replace(upper(COALESCE(configuration_details,'')),'[^A-Z0-9]+','','g') k_cfg,
                 regexp_replace(upper(COALESCE(price,'')),'[^A-Z0-9]+','','g') k_price,
                 regexp_replace(COALESCE(phone_numbers,''),'[^0-9]+','','g') k_phone
                FROM pi_newspaper_properties p
              ), ranked AS (
                SELECT n.*,ROW_NUMBER() OVER(
                  PARTITION BY k_type,k_loc,k_area,k_cfg,k_price,k_phone
                  ORDER BY CASE WHEN UPPER(COALESCE(verification,''))='VERIFIED' THEN 0 ELSE 1 END,id DESC
                ) rn FROM n
              )
              SELECT record_id,lead_type,locality,area,configuration_details,price,agency_brand,
                     contact_person,phone_numbers,notes,source,completeness,verification,team_member
              FROM ranked WHERE rn=1 {where} ORDER BY id DESC LIMIT :lim
            """),p).mappings().all()
        return {"status":"OK","count":len(rows),"rows":_serialize(rows),"dedupe":"NON_DESTRUCTIVE_CANONICAL"}

    @router.get("/whatsapp-database-v42",response_class=HTMLResponse)
    def wa_page(req:Request):
        if not page_role_or_redirect(req):return RedirectResponse("/login",303)
        return HTMLResponse(PAGE.replace("__SRC__","whatsapp").replace("__TITLE__","WhatsApp Group Property Database"))

    @router.get("/newspaper-database-v42",response_class=HTMLResponse)
    def news_page(req:Request):
        if not page_role_or_redirect(req):return RedirectResponse("/login",303)
        return HTMLResponse(PAGE.replace("__SRC__","newspaper").replace("__TITLE__","Newspaper Property Database"))

    app.include_router(router)
    return router

PAGE=r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title><style>
body{margin:0;font-family:Arial;background:#efe4d2;color:#2d261f}header{background:#5d4937;color:white;padding:16px 20px}.wrap{max-width:1650px;margin:auto;padding:18px}
.card{background:#fffdf9;border:1px solid #dccdbb;border-radius:14px;padding:16px;margin-bottom:14px}.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}
.btn,button{padding:9px 12px;border:0;border-radius:8px;background:#6c543f;color:white;font-weight:800;text-decoration:none;cursor:pointer}input{padding:9px;border:1px solid #d8c8b4;border-radius:8px;min-width:340px}
.tablewrap{overflow:auto;max-height:72vh;border:1px solid #ddcfbd;border-radius:10px}table{width:100%;border-collapse:collapse;min-width:1500px;background:white}
th,td{padding:9px;border-bottom:1px solid #eee0ce;text-align:left;font-size:12px;vertical-align:top}th{background:#f7ecdf;position:sticky;top:0}.phone{font-weight:900}.notes{max-width:420px;white-space:pre-wrap}.badge{background:#e8dccb;padding:5px 9px;border-radius:999px;font-weight:900}
</style></head><body><header><b>__TITLE__</b> · <span class=badge>V4.2 Newspaper Format</span></header><div class=wrap>
<div class=card><div class=toolbar><a class=btn href="/workspace">← Dashboard</a><a class=btn href="/newspaper-v83">Newspaper Upload</a><a class=btn href="/whatsapp-live">WhatsApp Live</a></div></div>
<div class=card><div class=toolbar><input id=q placeholder="Search locality, configuration, contact, phone, source"><button onclick=load()>Search</button><button id=rebuild onclick=rebuildNow()>Rebuild WhatsApp Clean Database</button></div><div id=summary></div>
<div class=tablewrap><table><thead><tr><th>Record ID</th><th>Lead Type</th><th>Locality / Project</th><th>Area</th><th>Configuration</th><th>Price / Rent</th><th>Agency / Broker</th><th>Contact Person</th><th>Phone Numbers</th><th>Notes / Description</th><th>Source</th><th>Completeness</th><th>Verification</th><th>Team Member</th></tr></thead><tbody id=rows></tbody></table></div></div></div>
<script>
const SRC="__SRC__";if(SRC==="newspaper")document.getElementById("rebuild").style.display="none";
const E=v=>String(v??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m]));
async function J(u,o={}){let r=await fetch(u,{credentials:"include",...o});let t=await r.text();let d;try{d=JSON.parse(t)}catch(e){d={detail:t}};if(!r.ok)throw Error(d.detail||t);return d}
async function rebuildNow(){let d=await J("/api/v42/whatsapp/rebuild?limit=1000",{method:"POST"});alert(JSON.stringify(d,null,2));load()}
async function load(){let d=await J("/api/v42/"+SRC+"/rows?q="+encodeURIComponent(q.value||"")+"&limit=1500");summary.textContent=(d.count||0)+" canonical unique records";rows.innerHTML=(d.rows||[]).map(x=>`<tr><td>${E(x.record_id)}</td><td>${E(x.lead_type)}</td><td><b>${E(x.locality)}</b></td><td>${E(x.area)}</td><td>${E(x.configuration_details)}</td><td>${E(x.price)}</td><td>${E(x.agency_brand)}</td><td>${E(x.contact_person)}</td><td class=phone>${E(x.phone_numbers)}</td><td class=notes>${E(x.notes)}</td><td>${E(x.source)}</td><td>${E(x.completeness)}</td><td>${E(x.verification)}</td><td>${E(x.team_member)}</td></tr>`).join("")||'<tr><td colspan=14>No clean records.</td></tr>'}load();
</script></body></html>'''
