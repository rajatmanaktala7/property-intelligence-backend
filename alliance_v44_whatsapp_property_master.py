from __future__ import annotations
import hashlib, os, re, uuid
from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import create_engine, text
import alliance_v41b_whatsapp_splitter as base

VERSION="4.4.3-PHASE4.1-PURITY-INCREMENTAL-MASTER"
LIVE_GENERATION_ID=uuid.uuid5(uuid.NAMESPACE_URL,"alliance://pi_whatsapp_property_master/live-v1")

def _db_url(u):
    u=(u or "").strip()
    if u.startswith("postgres://"):return u.replace("postgres://","postgresql+psycopg://",1)
    if u.startswith("postgresql://"):return u.replace("postgresql://","postgresql+psycopg://",1)
    return u
def _wa_engine():
    u=os.getenv("WHATSAPP_DATABASE_URL","").strip()
    return create_engine(_db_url(u),pool_pre_ping=True,pool_recycle=300,connect_args={"connect_timeout":5}) if u else None
def _norm(v):return re.sub(r"\s+"," ",re.sub(r"[^A-Z0-9]+"," ",str(v or "").upper())).strip()
def _canonical_key(txn,locality,area,config,furnishing,price,floor):
    # Price intentionally excluded from identity.
    parts=[txn,locality,area,config,furnishing or "",floor or ""]
    return hashlib.sha256("|".join(_norm(x) for x in parts).encode()).hexdigest()
def _phone_list(*vals):return base.purity.phones(*vals)
def _specific(rec):
    loc=_norm(rec.get("project_name") or rec.get("locality"))
    if not loc or loc in base.purity.CITY_ONLY:return False
    if rec.get("review_hold"):return False
    return rec.get("transaction_type") in {"Sale","Rent"} and bool(rec.get("area_value")) and bool(rec.get("area_unit"))
def _money_label(v,txn):
    if v in (None,""):return ""
    n=float(v)
    if n>=10_000_000:return f"₹{n/10_000_000:.2f} Cr"
    if n>=100_000:return f"₹{n/100_000:.2f} Lakh"+("/month" if txn=="Rent" else "")
    return f"₹{n:,.0f}"+("/month" if txn=="Rent" else "")
def _to_master_row(rec,parent):
    txn=rec["transaction_type"];loc=str(rec.get("project_name") or rec.get("locality") or "").strip()
    area=f"{float(rec['area_value']):g} {rec['area_unit']}" if rec.get("area_value") else ""
    cfg=str(rec.get("configuration") or rec.get("property_type") or "")
    price_num=rec.get("price_value") if txn=="Sale" else rec.get("rent_value");price=_money_label(price_num,txn)
    furn="";fl=rec.get("floor") or "";ph=_phone_list(rec.get("broker_phone"),parent);name=str(rec.get("broker_name") or "")
    key=_canonical_key(txn,loc,area,cfg,furn,price_num,fl)
    return {"canonical_key":key,"record_id":"WA-"+key[:10].upper(),"lead_type":txn.upper(),"description":" | ".join(x for x in [loc,cfg,area,("Sale "+price if txn=="Sale" and price else None),("Rent "+price if txn=="Rent" and price else None)] if x),
            "area":area,"configuration_details":cfg,"price":price,"contact_name_number":(name+" · " if name and ph else "")+" | ".join(ph),
            "phone_numbers":" | ".join(ph),"contact_name":name,"source":rec.get("source_group") or "WhatsApp Group","captured_on":rec.get("captured_on"),
            "verification":"Unverified","raw_message":parent,"furnishing":furn,"floor":fl}
def _ensure(engine):
    with engine.begin() as c:
        c.execute(text("""CREATE TABLE IF NOT EXISTS pi_whatsapp_property_master_generation(
          id BIGSERIAL PRIMARY KEY,generation_id UUID UNIQUE NOT NULL,started_at TIMESTAMPTZ DEFAULT NOW(),completed_at TIMESTAMPTZ,
          raw_messages INTEGER DEFAULT 0,bursts INTEGER DEFAULT 0,extracted_children INTEGER DEFAULT 0,canonical_rows INTEGER DEFAULT 0,
          requirements_filtered INTEGER DEFAULT 0,duplicates_merged INTEGER DEFAULT 0,skipped_non_specific INTEGER DEFAULT 0,status TEXT DEFAULT 'RUNNING')"""))
        c.execute(text("""CREATE TABLE IF NOT EXISTS pi_whatsapp_property_master(
          id BIGSERIAL PRIMARY KEY,generation_id UUID NOT NULL,canonical_key TEXT NOT NULL,record_id TEXT NOT NULL,lead_type TEXT,
          description TEXT,area TEXT,configuration_details TEXT,price TEXT,contact_name_number TEXT,contact_name TEXT,phone_numbers TEXT,
          source TEXT,source_count INTEGER DEFAULT 1,all_contacts TEXT,all_sources TEXT,captured_on TIMESTAMPTZ,
          verification TEXT DEFAULT 'Unverified',raw_message TEXT,furnishing TEXT,floor TEXT,created_at TIMESTAMPTZ DEFAULT NOW(),
          UNIQUE(generation_id,canonical_key))"""))
        c.execute(text("""INSERT INTO pi_whatsapp_property_master_generation(generation_id,status,started_at,completed_at)
          VALUES(:g,'COMPLETED',NOW(),NOW()) ON CONFLICT(generation_id) DO NOTHING"""),{"g":LIVE_GENERATION_ID})
def _build_canonical(rows):
    bursts=base.group_message_bursts(rows,180);canonical={};reqs=0;children=0;skipped=0;review=0
    for burst in bursts:
        parent="\n".join(str(x.get("raw_text") or "") for x in burst["rows"] if str(x.get("raw_text") or "").strip())
        if not parent.strip():continue
        cls=base.purity.classify_text(parent)
        if cls=="REQUIREMENT":reqs+=1;continue
        if cls in {"NOISE","SERVICE_AD"}:skipped+=1;continue
        meta=burst["rows"][-1]
        for child in base.split_multi_listing(parent):
            children+=1;rec=base.normalize_listing(child,parent,meta)
            if not rec:skipped+=1;continue
            if rec.get("review_hold"):review+=1;continue
            if not _specific(rec):skipped+=1;continue
            rec["source_group"]=meta.get("group_name");rec["captured_on"]=meta.get("created_at");row=_to_master_row(rec,parent);k=row["canonical_key"]
            if k not in canonical:
                row["phones"]=set(_phone_list(row["phone_numbers"]));row["sources"]=set([row["source"]] if row["source"] else []);row["names"]=set([row["contact_name"]] if row["contact_name"] else []);canonical[k]=row
            else:
                x=canonical[k];x["phones"].update(_phone_list(row["phone_numbers"]));x["sources"].add(row["source"]);x["names"].add(row["contact_name"])
                if row.get("captured_on") and (not x.get("captured_on") or row["captured_on"]>=x["captured_on"]):
                    x["captured_on"]=row["captured_on"];x["description"]=row["description"];x["price"]=row["price"];x["raw_message"]=row["raw_message"]
    return canonical,{"raw_messages":len(rows),"bursts":len(bursts),"extracted_children":children,"canonical_rows":len(canonical),
                      "requirements_filtered":reqs,"duplicates_merged":max(children-len(canonical)-skipped-review,0),
                      "skipped_non_specific":skipped,"review_held":review}
def _merge_and_upsert(engine,canonical):
    ins=upd=0
    with engine.begin() as c:
        for k,r in canonical.items():
            old=c.execute(text("SELECT verification FROM pi_whatsapp_property_master WHERE generation_id=:g AND canonical_key=:k FOR UPDATE"),{"g":LIVE_GENERATION_ID,"k":k}).mappings().first()
            ver=str(old.get("verification") if old else r["verification"] or "Unverified")
            ph=sorted(x for x in r["phones"] if x);src=sorted(x for x in r["sources"] if x);names=sorted(x for x in r["names"] if x)
            label=((" / ".join(names)+" · ") if names and ph else "")+(" | ".join(ph) if ph else " / ".join(names))
            p={"g":LIVE_GENERATION_ID,"k":k,"rid":r["record_id"],"lt":r["lead_type"],"d":r["description"],"a":r["area"],"cfg":r["configuration_details"],"pr":r["price"],
               "cl":label,"cn":" / ".join(names),"ph":" | ".join(ph),"src":" | ".join(src),"sc":max(len(src),1 if src else 0),"ac":label,"asrc":" | ".join(src),
               "cap":r["captured_on"],"ver":ver,"raw":r["raw_message"],"f":r["furnishing"],"fl":r["floor"]}
            c.execute(text("""INSERT INTO pi_whatsapp_property_master(generation_id,canonical_key,record_id,lead_type,description,area,configuration_details,price,
              contact_name_number,contact_name,phone_numbers,source,source_count,all_contacts,all_sources,captured_on,verification,raw_message,furnishing,floor)
              VALUES(:g,:k,:rid,:lt,:d,:a,:cfg,:pr,:cl,:cn,:ph,:src,:sc,:ac,:asrc,:cap,:ver,:raw,:f,:fl)
              ON CONFLICT(generation_id,canonical_key) DO UPDATE SET lead_type=EXCLUDED.lead_type,description=EXCLUDED.description,area=EXCLUDED.area,
              configuration_details=EXCLUDED.configuration_details,price=EXCLUDED.price,contact_name_number=EXCLUDED.contact_name_number,contact_name=EXCLUDED.contact_name,
              phone_numbers=EXCLUDED.phone_numbers,source=EXCLUDED.source,source_count=EXCLUDED.source_count,all_contacts=EXCLUDED.all_contacts,all_sources=EXCLUDED.all_sources,
              captured_on=EXCLUDED.captured_on,verification=EXCLUDED.verification,raw_message=EXCLUDED.raw_message,furnishing=EXCLUDED.furnishing,floor=EXCLUDED.floor"""),p)
            if old:upd+=1
            else:ins+=1
    return ins,upd
def _update_live_generation(engine,s):
    with engine.begin() as c:c.execute(text("""UPDATE pi_whatsapp_property_master_generation SET completed_at=NOW(),raw_messages=:raw,bursts=:b,extracted_children=:ch,
      canonical_rows=(SELECT COUNT(*) FROM pi_whatsapp_property_master WHERE generation_id=:g),requirements_filtered=:r,duplicates_merged=:d,skipped_non_specific=:sk,status='COMPLETED'
      WHERE generation_id=:g"""),{"raw":s["raw_messages"],"b":s["bursts"],"ch":s["extracted_children"],"r":s["requirements_filtered"],"d":s["duplicates_merged"],"sk":s["skipped_non_specific"],"g":LIVE_GENERATION_ID})
def register(core):
    app=core.app;engine=core.engine;need_login=core.need_login;router=APIRouter()
    @router.get("/api/v44/status")
    def status(req:Request):
        need_login(req);return {"version":VERSION,"status":"OK","generation_mode":"FIXED_LIVE_GENERATION","update_mode":"INCREMENTAL_BY_WA_MESSAGE_ID",
          "duplicate_strategy":"IDENTITY_UPSERT_PRICE_EXCLUDED","review_hold_enabled":True,"source_data_deleted":False}
    def fetch_incremental(after_id=0,upto_id=None,limit=5000):
        w=_wa_engine()
        if w is None:raise HTTPException(503,"WHATSAPP_DATABASE_URL not configured")
        p={"a":max(int(after_id or 0),0),"l":int(limit)};end=""
        if upto_id is not None:p["u"]=int(upto_id);end="AND m.id<=:u"
        with w.connect() as c:return c.execute(text(f"""SELECT m.message_id,m.raw_text,m.created_at,m.sender_name,m.sender_phone,m.source_id,s.group_name,m.id AS wa_id
          FROM wa_messages m LEFT JOIN wa_sources s ON s.source_id=m.source_id WHERE m.id>:a {end} ORDER BY m.id ASC LIMIT :l"""),p).mappings().all()
    def fetch_latest(limit=5000):
        w=_wa_engine()
        if w is None:raise HTTPException(503,"WHATSAPP_DATABASE_URL not configured")
        with w.connect() as c:return c.execute(text("""SELECT * FROM (SELECT m.message_id,m.raw_text,m.created_at,m.sender_name,m.sender_phone,m.source_id,s.group_name,m.id AS wa_id
          FROM wa_messages m LEFT JOIN wa_sources s ON s.source_id=m.source_id ORDER BY m.id DESC LIMIT :l)x ORDER BY wa_id ASC"""),{"l":int(limit)}).mappings().all()
    def apply_rows(rows,mode):
        _ensure(engine)
        if not rows:return {"status":"UP_TO_DATE","version":VERSION,"mode":mode,"processed_to_id":None,"inserted":0,"updated":0}
        can,stats=_build_canonical(rows);i,u=_merge_and_upsert(engine,can);_update_live_generation(engine,stats)
        return {"status":"OK","version":VERSION,"mode":mode,**stats,"inserted":i,"updated":u,"processed_to_id":max(int(x["wa_id"]) for x in rows)}
    core._v44_sync_whatsapp_master=lambda after_id=0,upto_id=None,limit=5000:apply_rows(fetch_incremental(after_id,upto_id,limit),"INCREMENTAL")
    core._v44_rebuild_whatsapp_master=lambda limit=5000:apply_rows(fetch_latest(limit),"RECONCILE_LATEST")
    app.include_router(router);return router
