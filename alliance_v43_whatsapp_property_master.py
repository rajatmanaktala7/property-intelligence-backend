
from __future__ import annotations
import os,re,hashlib,uuid
from fastapi import APIRouter,Request,Query,HTTPException
from fastapi.responses import HTMLResponse,RedirectResponse
from sqlalchemy import create_engine,text
import alliance_v41b_whatsapp_splitter as base

VERSION="4.3.0-CLEAN-WHATSAPP-PROPERTY-MASTER"

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

def _normalize_locality(v):
    s=_norm(v)
    replacements={
        "DLFPHASE1":"DLF PHASE 1",
        "DLFPHASE2":"DLF PHASE 2",
        "DLFPHASE4":"DLF PHASE 4",
        "SHUSHANTLOK1":"SHUSHANT LOK 1",
        "SUSHANTLOK1":"SUSHANT LOK 1",
        "DLF PHASE I":"DLF PHASE 1",
        "DLF PHASE II":"DLF PHASE 2",
        "DLF PHASE IV":"DLF PHASE 4",
    }
    compact=s.replace(" ","")
    if compact in replacements:
        return replacements[compact]
    if s in replacements:
        return replacements[s]
    return s.title() if s else ""

def _normalize_area(area):
    s=str(area or "").strip()
    if not s:return ""
    m=re.search(r"(?i)(\d+(?:\.\d+)?)\s*(sq\.?\s*yds?|sqyds?|syds|yards|yds)",s)
    if m:return f"{int(float(m.group(1)))} sq yd"
    m=re.search(r"(?i)(\d+(?:\.\d+)?)\s*(sq\.?\s*ft|sqft|sft)",s)
    if m:return f"{int(float(m.group(1)))} sqft"
    return re.sub(r"\s+"," ",s).strip()

def _normalize_config(v):
    raw=str(v or "").upper()
    had_plus="+" in raw
    had_servant=("SERVANT" in raw or " SER" in (" "+raw))
    s=_norm(raw)
    s=s.replace("SERVANT","SER")
    # Standardize 3BHK / 3 BHK / 3/4BHK forms.
    s=re.sub(r"\b(\d+(?:/\d+)?)\s*BHK\b",r"\1 BHK",s)
    s=re.sub(r"\s+"," ",s).strip()
    if re.search(r"\b\d+(?:/\d+)?\s+BHK\s+SER\b",s) and (had_plus or had_servant):
        s=re.sub(r"(\b\d+(?:/\d+)?\s+BHK)\s+SER\b",r"\1 + SER",s)
    return s

def _furnishing(raw):
    up=_norm(raw)
    if "FULLY FURNISHED" in up:return "Fully Furnished"
    if "SEMI FURNISHED" in up:return "Semi Furnished"
    if "FURNISHED" in up:return "Furnished"
    return ""

def _money_label(v,txn):
    if v in (None,""):return ""
    try:n=float(v)
    except:return str(v)
    if n>=10_000_000:return f"₹{n/10_000_000:.2f} Cr"
    if n>=100_000:return f"₹{n/100_000:.2f} Lakh" + ("/month" if txn=="Rent" else "")
    return f"₹{n:,.0f}" + ("/month" if txn=="Rent" else "")

def _canonical_key(txn,locality,area,config,furnishing,price,floor):
    # Broker phone intentionally excluded.
    # Same property from different brokers/groups becomes one canonical property.
    parts=[
        txn,
        _normalize_locality(locality),
        _normalize_area(area),
        _normalize_config(config),
        furnishing or "",
        floor or "",
        str(round(float(price),2)) if price not in (None,"") else "",
    ]
    return hashlib.sha256("|".join(_norm(x) for x in parts).encode()).hexdigest()

def _phone_list(*vals):
    out=[]
    for v in vals:
        for p in re.findall(r"(?<!\d)(?:\+?91[\s-]?)?([6-9]\d{9})(?!\d)",str(v or "")):
            if p not in out:out.append(p)
    return out

def _contact_label(name,phones):
    name=str(name or "").strip()
    if name and phones:
        return name+" · "+" | ".join(phones)
    if phones:
        return " | ".join(phones)
    return name

def _specific(rec):
    locality=_norm(rec.get("project_name") or rec.get("locality"))
    if not locality:return False
    if any(x in locality for x in [
        "NEW FLOORS IN RESALE","INVENTORY FOR SALE","4 5 6 BHK KOTHI"
    ]):return False
    if not rec.get("area_value"):return False
    if rec.get("transaction_type") not in ("Sale","Rent"):return False
    if rec.get("transaction_type")=="Sale" and not rec.get("price_value"):return False
    if rec.get("transaction_type")=="Rent" and not rec.get("rent_value"):return False
    return True

def _to_master_row(rec,parent_raw):
    txn=rec.get("transaction_type")
    locality=_normalize_locality(rec.get("project_name") or rec.get("locality") or rec.get("city") or "")
    area=""
    if rec.get("area_value") not in (None,""):
        try:v=str(int(float(rec.get("area_value"))))
        except:v=str(rec.get("area_value"))
        unit=str(rec.get("area_unit") or "")
        area=_normalize_area((v+" "+unit).strip())

    config=_normalize_config(rec.get("configuration") or rec.get("property_type") or "")
    furnishing=_furnishing(parent_raw)
    floor=rec.get("floor") or ""
    price_num=rec.get("price_value") if txn=="Sale" else rec.get("rent_value")
    price=_money_label(price_num,txn)

    phones=_phone_list(rec.get("broker_phone"),parent_raw)
    broker_name=str(rec.get("broker_name") or "").strip()
    contact=_contact_label(broker_name,phones)

    details=[
        locality,
        config,
        area,
        furnishing,
        floor,
        ("Sale "+price if txn=="Sale" and price else None),
        ("Rent "+price if txn=="Rent" and price else None),
    ]
    # Keep useful operational keywords from raw evidence.
    raw_up=_norm(parent_raw)
    for token,label in [
        ("LIFT","Lift"),
        ("STILT","Stilt Parking"),
        ("MAINT","Maintenance Extra"),
        ("RENOVATED","Renovated"),
        ("PARKING","Parking"),
        ("TERRACE","Terrace"),
        ("CORNER","Corner"),
        ("FRONT FACING","Front Facing"),
        ("DOUBLE HEIGHT","Double Height"),
    ]:
        if token in raw_up and label not in details:
            details.append(label)

    description=" | ".join(x for x in details if x)

    key=_canonical_key(txn,locality,area,config,furnishing,price_num,floor)
    return {
        "canonical_key":key,
        "record_id":"WA-"+key[:10].upper(),
        "lead_type":txn.upper() if txn else "",
        "description":description,
        "area":area,
        "configuration_details":config,
        "price":price,
        "contact_name_number":contact,
        "phone_numbers":" | ".join(phones),
        "contact_name":broker_name,
        "source":rec.get("source_group") or "WhatsApp Group",
        "captured_on":rec.get("captured_on"),
        "verification":"Unverified",
        "raw_message":parent_raw,
        "furnishing":furnishing,
        "floor":floor,
        "price_numeric":price_num,
    }

def _ensure(engine):
    with engine.begin() as c:
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS pi_whatsapp_property_master_generation(
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
        CREATE TABLE IF NOT EXISTS pi_whatsapp_property_master(
          id BIGSERIAL PRIMARY KEY,
          generation_id UUID NOT NULL,
          canonical_key TEXT NOT NULL,
          record_id TEXT NOT NULL,
          lead_type TEXT,
          description TEXT,
          area TEXT,
          configuration_details TEXT,
          price TEXT,
          contact_name_number TEXT,
          contact_name TEXT,
          phone_numbers TEXT,
          source TEXT,
          source_count INTEGER DEFAULT 1,
          all_contacts TEXT,
          all_sources TEXT,
          captured_on TIMESTAMPTZ,
          verification TEXT DEFAULT 'Unverified',
          raw_message TEXT,
          furnishing TEXT,
          floor TEXT,
          created_at TIMESTAMPTZ DEFAULT NOW(),
          UNIQUE(generation_id,canonical_key)
        )"""))

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
    app=core.app
    engine=core.engine
    need_login=core.need_login
    page_role_or_redirect=core.page_role_or_redirect
    router=APIRouter()

    @router.get("/api/v43/status")
    def status(req:Request):
        need_login(req)
        return {
            "version":VERSION,
            "status":"OK",
            "startup_db_work":False,
            "database":"/whatsapp-property-master-v43",
            "duplicate_strategy":"CANONICAL_PROPERTY_WITH_AGGREGATED_CONTACTS",
            "source_data_deleted":False
        }

    @router.post("/api/v43/setup")
    def setup(req:Request):
        need_login(req)
        _ensure(engine)
        return {"version":VERSION,"status":"READY"}

    @router.post("/api/v43/whatsapp/rebuild")
    def rebuild(req:Request,limit:int=Query(1000,ge=1,le=10000)):
        need_login(req)
        _ensure(engine)
        w=_wa_engine()
        if w is None:
            raise HTTPException(503,"WHATSAPP_DATABASE_URL not configured")

        with w.connect() as c:
            rows=c.execute(text("""
              SELECT m.message_id,m.raw_text,m.created_at,m.sender_name,m.sender_phone,
                     m.source_id,s.group_name
              FROM wa_messages m
              LEFT JOIN wa_sources s ON s.source_id=m.source_id
              ORDER BY m.created_at ASC NULLS LAST,m.id ASC
              LIMIT :lim
            """),{"lim":limit}).mappings().all()

        bursts=base.group_message_bursts(rows,180)
        gen=uuid.uuid4()

        with engine.begin() as c:
            c.execute(text("""
              INSERT INTO pi_whatsapp_property_master_generation(generation_id,status)
              VALUES(:g,'RUNNING')
            """),{"g":gen})

        canonical={}
        requirements=0
        children_total=0
        skipped=0

        for burst in bursts:
            parent="\n".join(str(x.get("raw_text") or "") for x in burst["rows"] if str(x.get("raw_text") or "").strip())
            if not parent.strip():
                continue

            if base.classify_listing_vs_requirement(parent)=="REQUIREMENT":
                requirements+=1
                continue

            meta=burst["rows"][-1]
            base_children=base.split_multi_listing(parent)
            children=[]
            for b in base_children:
                children.extend(base.expand_specific_rent_variants(b))

            children_total+=len(children)

            for child in children:
                rec=base.normalize_listing(child,parent,meta)
                if not rec or not _specific(rec):
                    skipped+=1
                    continue

                rec["source_group"]=meta.get("group_name")
                rec["captured_on"]=meta.get("created_at")
                row=_to_master_row(rec,parent)
                key=row["canonical_key"]

                phones=set(_phone_list(row["phone_numbers"]))
                source=row["source"]
                contact_name=row["contact_name"]

                if key not in canonical:
                    row["phones"]=phones
                    row["sources"]=set([source] if source else [])
                    row["contact_names"]=set([contact_name] if contact_name else [])
                    row["source_count"]=1
                    canonical[key]=row
                else:
                    x=canonical[key]
                    x["phones"].update(phones)
                    if source:x["sources"].add(source)
                    if contact_name:x["contact_names"].add(contact_name)
                    x["source_count"]+=1

                    # Prefer richer description.
                    if len(row["description"])>len(x["description"]):
                        x["description"]=row["description"]
                        x["raw_message"]=row["raw_message"]

        with engine.begin() as c:
            for key,row in canonical.items():
                contact_pairs=[]
                names=sorted(row["contact_names"])
                phones=sorted(row["phones"])

                if names and phones:
                    # Keep all available names and phones without duplicating them in two columns.
                    if len(names)==1:
                        contact_pairs=[names[0]+" · "+" | ".join(phones)]
                    else:
                        contact_pairs=[" / ".join(names)+" · "+" | ".join(phones)]
                elif phones:
                    contact_pairs=[" | ".join(phones)]
                elif names:
                    contact_pairs=[" / ".join(names)]

                c.execute(text("""
                  INSERT INTO pi_whatsapp_property_master(
                    generation_id,canonical_key,record_id,lead_type,description,area,
                    configuration_details,price,contact_name_number,contact_name,phone_numbers,
                    source,source_count,all_contacts,all_sources,captured_on,verification,
                    raw_message,furnishing,floor)
                  VALUES(
                    :g,:canonical_key,:record_id,:lead_type,:description,:area,
                    :configuration_details,:price,:contact_name_number,:contact_name,:phone_numbers,
                    :source,:source_count,:all_contacts,:all_sources,:captured_on,:verification,
                    :raw_message,:furnishing,:floor)
                """),{
                    "g":gen,
                    "canonical_key":key,
                    "record_id":row["record_id"],
                    "lead_type":row["lead_type"],
                    "description":row["description"],
                    "area":row["area"],
                    "configuration_details":row["configuration_details"],
                    "price":row["price"],
                    "contact_name_number":contact_pairs[0] if contact_pairs else "",
                    "contact_name":" / ".join(names),
                    "phone_numbers":" | ".join(phones),
                    "source":" | ".join(sorted(row["sources"])),
                    "source_count":row["source_count"],
                    "all_contacts":contact_pairs[0] if contact_pairs else "",
                    "all_sources":" | ".join(sorted(row["sources"])),
                    "captured_on":row["captured_on"],
                    "verification":row["verification"],
                    "raw_message":row["raw_message"],
                    "furnishing":row["furnishing"],
                    "floor":row["floor"],
                })

            dup=max(children_total-len(canonical)-skipped,0)
            c.execute(text("""
              UPDATE pi_whatsapp_property_master_generation
              SET completed_at=NOW(),
                  raw_messages=:raw,
                  bursts=:bursts,
                  extracted_children=:children,
                  canonical_rows=:can,
                  requirements_filtered=:req,
                  duplicates_merged=:dup,
                  skipped_non_specific=:skip,
                  status='COMPLETED'
              WHERE generation_id=:g
            """),{
                "raw":len(rows),
                "bursts":len(bursts),
                "children":children_total,
                "can":len(canonical),
                "req":requirements,
                "dup":dup,
                "skip":skipped,
                "g":gen
            })

        return {
            "status":"OK",
            "version":VERSION,
            "generation_id":str(gen),
            "raw_messages":len(rows),
            "bursts":len(bursts),
            "extracted_children":children_total,
            "canonical_rows":len(canonical),
            "requirements_filtered":requirements,
            "duplicates_merged":max(children_total-len(canonical)-skipped,0),
            "skipped_non_specific":skipped
        }

    @router.get("/api/v43/whatsapp/rows")
    def rows(req:Request,q:str="",limit:int=Query(1000,ge=1,le=3000)):
        need_login(req)
        _ensure(engine)
        p={"lim":limit}
        where=""
        if q.strip():
            where="""AND (
              COALESCE(description,'') ILIKE :q OR
              COALESCE(configuration_details,'') ILIKE :q OR
              COALESCE(contact_name_number,'') ILIKE :q OR
              COALESCE(phone_numbers,'') ILIKE :q OR
              COALESCE(source,'') ILIKE :q
            )"""
            p["q"]="%"+q.strip()+"%"

        with engine.connect() as c:
            gen=c.execute(text("""
              SELECT generation_id
              FROM pi_whatsapp_property_master_generation
              WHERE status='COMPLETED'
              ORDER BY completed_at DESC NULLS LAST,id DESC
              LIMIT 1
            """)).scalar()

            if not gen:
                return {"status":"REBUILD_REQUIRED","count":0,"rows":[]}

            p["g"]=gen
            rr=c.execute(text(f"""
              SELECT record_id,lead_type,description,area,configuration_details,price,
                     contact_name_number,source,captured_on,verification,source_count
              FROM pi_whatsapp_property_master
              WHERE generation_id=:g {where}
              ORDER BY id DESC
              LIMIT :lim
            """),p).mappings().all()

        return {
            "status":"OK",
            "generation_id":str(gen),
            "count":len(rr),
            "rows":_serialize(rr)
        }

    @router.get("/api/v43/whatsapp/dedupe-status")
    def dedupe_status(req:Request):
        need_login(req)
        _ensure(engine)
        with engine.connect() as c:
            gen=c.execute(text("""
              SELECT generation_id
              FROM pi_whatsapp_property_master_generation
              WHERE status='COMPLETED'
              ORDER BY completed_at DESC NULLS LAST,id DESC
              LIMIT 1
            """)).scalar()
            if not gen:
                return {"status":"REBUILD_REQUIRED"}

            stats=c.execute(text("""
              SELECT raw_messages,bursts,extracted_children,canonical_rows,
                     requirements_filtered,duplicates_merged,skipped_non_specific
              FROM pi_whatsapp_property_master_generation
              WHERE generation_id=:g
            """),{"g":gen}).mappings().one()

        return {"status":"OK","generation_id":str(gen),**dict(stats)}

    @router.get("/whatsapp-property-master-v43",response_class=HTMLResponse)
    def page(req:Request):
        if not page_role_or_redirect(req):
            return RedirectResponse("/login",303)
        return HTMLResponse(PAGE)

    app.include_router(router)
    return router

PAGE=r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WhatsApp Property Master V4.3</title>
<style>
body{margin:0;font-family:Arial;background:#efe4d2;color:#2d261f}
header{background:#5d4937;color:#fff;padding:16px 20px}
.wrap{max-width:1700px;margin:auto;padding:18px}
.card{background:#fffdf9;border:1px solid #dccdbb;border-radius:14px;padding:16px;margin-bottom:14px}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}
.btn,button{padding:9px 12px;border:0;border-radius:8px;background:#6c543f;color:#fff;font-weight:800;text-decoration:none;cursor:pointer}
input{padding:9px;border:1px solid #d8c8b4;border-radius:8px;min-width:360px}
.tablewrap{overflow:auto;max-height:74vh;border:1px solid #ddcfbd;border-radius:10px}
table{width:100%;border-collapse:collapse;min-width:1500px;background:#fff}
th,td{padding:10px;border-bottom:1px solid #eee0ce;text-align:left;font-size:12px;vertical-align:top}
th{background:#f7ecdf;position:sticky;top:0}
.description{max-width:560px;white-space:normal;line-height:1.45}
.contact{font-weight:800;min-width:220px}
.badge{display:inline-block;background:#e8dccd;padding:4px 8px;border-radius:999px;font-weight:800}
</style>
</head>
<body>
<header><b>WhatsApp Property Master</b> · V4.3 Clean Canonical Database</header>
<div class="wrap">
<div class="card">
<div class="toolbar">
<a class="btn" href="/workspace">← Dashboard</a>
<a class="btn" href="/whatsapp-live">WhatsApp Live</a>
<a class="btn" href="/newspaper-database-v42">Newspaper Database</a>
<a class="btn" href="/api/v43/whatsapp/dedupe-status" target="_blank">Dedupe Status</a>
</div>
</div>

<div class="card">
<div class="toolbar">
<input id="q" placeholder="Search description, area, configuration, contact, phone or source">
<button onclick="load()">Search</button>
<button onclick="rebuild()">Rebuild Clean Database</button>
</div>
<div id="summary"></div>

<div class="tablewrap">
<table>
<thead>
<tr>
<th>Record ID</th>
<th>Type</th>
<th>Description</th>
<th>Area</th>
<th>Configuration</th>
<th>Price / Rent</th>
<th>Contact Name / Number</th>
<th>Source</th>
<th>Captured On</th>
<th>Verification</th>
<th>Sources Merged</th>
</tr>
</thead>
<tbody id="rows"></tbody>
</table>
</div>
</div>
</div>

<script>
const E=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));

async function J(u,o={}){
 let r=await fetch(u,{credentials:'include',...o});
 let t=await r.text();
 let d;
 try{d=JSON.parse(t)}catch(e){d={detail:t}}
 if(!r.ok)throw Error(d.detail||t);
 return d
}

async function load(){
 let d=await J('/api/v43/whatsapp/rows?q='+encodeURIComponent(q.value||'')+'&limit=1500');
 summary.textContent=(d.count||0)+' unique canonical properties';
 rows.innerHTML=(d.rows||[]).map(x=>`<tr>
 <td>${E(x.record_id)}</td>
 <td><span class="badge">${E(x.lead_type)}</span></td>
 <td class="description"><b>${E(x.description)}</b></td>
 <td>${E(x.area)}</td>
 <td>${E(x.configuration_details)}</td>
 <td><b>${E(x.price)}</b></td>
 <td class="contact">${E(x.contact_name_number)}</td>
 <td>${E(x.source)}</td>
 <td>${E(x.captured_on)}</td>
 <td>${E(x.verification)}</td>
 <td>${E(x.source_count)}</td>
 </tr>`).join('') || '<tr><td colspan="11">No records. Run rebuild.</td></tr>';
}

async function rebuild(){
 let d=await J('/api/v43/whatsapp/rebuild?limit=1000',{method:'POST'});
 alert(JSON.stringify(d,null,2));
 load();
}
load();
</script>
</body>
</html>"""
