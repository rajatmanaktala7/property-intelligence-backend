
from __future__ import annotations
import os
from fastapi import Request,Body,Query,HTTPException
from sqlalchemy import text,create_engine
MODULE_VERSION="3.7.4-WHATSAPP-SYNC-VERIFICATION-CONTACTS"
def _db_url(u):
    u=(u or "").strip()
    if u.startswith("postgres://"):return u.replace("postgres://","postgresql+psycopg://",1)
    if u.startswith("postgresql://"):return u.replace("postgresql://","postgresql+psycopg://",1)
    return u
def _wa_engine():
    u=(os.getenv("WHATSAPP_DATABASE_URL") or "").strip()
    return create_engine(_db_url(u),pool_pre_ping=True,pool_recycle=300) if u else None
def _ser(rows):
    out=[]
    for r in rows:
        d=dict(r)
        for k,v in list(d.items()):
            if hasattr(v,"isoformat"):d[k]=v.isoformat()
        out.append(d)
    return out
def _sync_whatsapp(engine):
    from alliance_v2_whatsapp_adapter import rebuild_whatsapp
    return rebuild_whatsapp(engine)
def _sync_contacts(engine):
    from alliance_v33_contact_vault import ensure_schema,adopt_hospitality,adopt_source_tables,upsert
    ensure_schema(engine);h=adopt_hospitality(engine);s,details=adopt_source_tables(engine);retail=0
    try:
        with engine.connect() as c:
            ex=c.execute(text("SELECT to_regclass('public.ai_retail_contact') IS NOT NULL")).scalar()
            rows=c.execute(text("SELECT * FROM ai_retail_contact ORDER BY retail_contact_id DESC LIMIT 10000")).mappings().all() if ex else []
        for r in rows:
            if upsert(engine,{"business_name":r.get("company_name"),"category":r.get("category") or "RETAIL","contact_name":r.get("person_name"),"role_title":r.get("designation"),"contact_phone":r.get("contact_phone"),"email":r.get("email"),"website":r.get("website"),"city":r.get("city"),"verification_status":r.get("verification_status") or "UNVERIFIED"},{"source_type":"RETAIL_EXPANSION","source_name":"Retail Expansion Bot","source_record_id":str(r.get("retail_contact_id")),"source_url":r.get("linkedin_profile_url"),"evidence_text":"Retail expansion decision-maker contact"}):retail+=1
    except Exception:pass
    return {"hospitality":h,"source_tables":s,"retail":retail,"details":details}
def register(app,engine,need_login):
    @app.get("/api/team-dashboard-v373/status")
    def status(req:Request):
        need_login(req);return {"version":MODULE_VERSION,"status":"OK","post_ingest_sync":True,"capture_date":True,"verification_controls":True,"contact_vault_growth":True}
    @app.post("/api/team-dashboard-v373/whatsapp-sync")
    def whatsapp_sync(req:Request):
        need_login(req)
        try:return {"version":MODULE_VERSION,"status":"OK","matcher_sync":_sync_whatsapp(engine),"contact_sync":_sync_contacts(engine)}
        except Exception as e:return {"version":MODULE_VERSION,"status":"ERROR","message":str(e)}
    @app.get("/api/team-dashboard-v373/whatsapp-capture")
    def whatsapp_capture(req:Request,q:str="",kind:str="ALL",limit:int=Query(300,ge=1,le=1000)):
        need_login(req);w=_wa_engine()
        if w is None:return {"status":"OFFLINE","count":0,"rows":[]}
        with w.connect() as c:
            wh=[];p={"lim":limit}
            if q.strip():wh.append("(COALESCE(g.group_name,'') ILIKE :q OR COALESCE(e.sender_name,'') ILIKE :q OR COALESCE(e.sender_phone,'') ILIKE :q OR COALESCE(e.raw_text,'') ILIKE :q OR COALESCE(e.entity_id,'') ILIKE :q)");p["q"]="%"+q.strip()+"%"
            if kind.upper()!="ALL":wh.append("e.classification=:k");p["k"]=kind.upper()
            where="WHERE "+" AND ".join(wh) if wh else ""
            rows=c.execute(text(f"""SELECT e.id,e.created_at capture_date,e.processed_at,e.message_timestamp,g.group_name,e.sender_name,e.sender_phone,e.raw_text,e.classification,e.entity_id,e.status processing_status,
            CASE WHEN e.entity_id LIKE 'WAP-%' THEN COALESCE((SELECT verification_status FROM wa_properties p WHERE p.wa_property_id=e.entity_id),'UNVERIFIED')
                 WHEN e.entity_id LIKE 'WAR-%' THEN COALESCE((SELECT verification_status FROM wa_requirements r WHERE r.wa_requirement_id=e.entity_id),'UNVERIFIED')
                 ELSE 'REVIEW_REQUIRED' END verification_status
            FROM wa_bridge_events e JOIN wa_bridge_groups g ON g.group_id=e.group_id {where} ORDER BY e.id DESC LIMIT :lim"""),p).mappings().all()
            last=c.execute(text("SELECT MAX(processed_at) FROM wa_bridge_events WHERE status='PROCESSED'")).scalar()
        return {"status":"OK","count":len(rows),"rows":_ser(rows),"last_sync":last.isoformat() if hasattr(last,"isoformat") else last}
    @app.post("/api/team-dashboard-v373/whatsapp-verify/{entity_id}")
    def whatsapp_verify(entity_id:str,req:Request,status:str="VERIFIED"):
        need_login(req);st=status.upper()
        if st not in {"VERIFIED","UNVERIFIED","REJECTED"}:raise HTTPException(400,"Invalid status")
        w=_wa_engine()
        if w is None:raise HTTPException(503,"WhatsApp DB not configured")
        with w.begin() as c:
            if entity_id.startswith("WAP-"):n=c.execute(text("UPDATE wa_properties SET verification_status=:s,updated_at=NOW() WHERE wa_property_id=:id"),{"s":st,"id":entity_id}).rowcount
            elif entity_id.startswith("WAR-"):n=c.execute(text("UPDATE wa_requirements SET verification_status=:s,updated_at=NOW() WHERE wa_requirement_id=:id"),{"s":st,"id":entity_id}).rowcount
            else:raise HTTPException(400,"Unsupported entity")
        try:_sync_whatsapp(engine)
        except Exception:pass
        return {"status":"UPDATED","entity_id":entity_id,"verification_status":st,"rows":n}
    @app.post("/api/team-dashboard-v373/marketing-sync")
    def marketing_sync(req:Request):
        need_login(req)
        try:return {"version":MODULE_VERSION,"status":"OK",**_sync_contacts(engine)}
        except Exception as e:return {"version":MODULE_VERSION,"status":"ERROR","message":str(e)}
    @app.get("/api/team-dashboard-v373/marketing-contacts")
    def contacts(req:Request,bucket:str="ALL",category:str="ALL",q:str="",limit:int=Query(500,ge=1,le=2000)):
        need_login(req)
        from alliance_v33_contact_vault import ensure_schema
        ensure_schema(engine);wh=[];p={"lim":limit}
        if bucket.upper()!="ALL":wh.append("source_bucket=:b");p["b"]=bucket.upper()
        if category.upper()!="ALL":wh.append("category=:cat");p["cat"]=category.upper()
        if q.strip():wh.append("(COALESCE(business_name,'') ILIKE :q OR COALESCE(contact_name,'') ILIKE :q OR COALESCE(phone,'') ILIKE :q OR COALESCE(whatsapp_phone,'') ILIKE :q OR COALESCE(email,'') ILIKE :q OR COALESCE(location,'') ILIKE :q OR COALESCE(city,'') ILIKE :q)");p["q"]="%"+q.strip()+"%"
        where="WHERE "+" AND ".join(wh) if wh else ""
        with engine.connect() as c:rows=c.execute(text(f"SELECT * FROM ai_marketing_contact_vault {where} ORDER BY updated_at DESC LIMIT :lim"),p).mappings().all()
        return {"status":"OK","count":len(rows),"contacts":_ser(rows)}
    @app.post("/api/team-dashboard-v373/contact/{contact_id}/verify")
    def verify_contact(contact_id:int,req:Request):
        need_login(req)
        with engine.begin() as c:n=c.execute(text("UPDATE ai_marketing_contact_vault SET verification_status='VERIFIED',updated_at=NOW() WHERE contact_id=:id"),{"id":contact_id}).rowcount
        return {"status":"VERIFIED","contact_id":contact_id,"rows":n}
    @app.post("/api/team-dashboard-v373/contact/{contact_id}")
    def edit_contact(contact_id:int,req:Request,payload:dict=Body(...)):
        need_login(req);allowed={"business_name","category","contact_name","role_title","phone","whatsapp_phone","email","website","location","city","verification_status","whatsapp_status","marketing_status","dnd"};vals={k:v for k,v in payload.items() if k in allowed}
        if not vals:return {"status":"NO_CHANGES"}
        sets=[];p={"id":contact_id}
        for i,(k,v) in enumerate(vals.items()):key=f"v{i}";sets.append(f'"{k}"=:{key}');p[key]=v
        with engine.begin() as c:n=c.execute(text(f"UPDATE ai_marketing_contact_vault SET {','.join(sets)},updated_at=NOW() WHERE contact_id=:id"),p).rowcount
        return {"status":"UPDATED","contact_id":contact_id,"rows":n}
    @app.delete("/api/team-dashboard-v373/contact/{contact_id}")
    def delete_contact(contact_id:int,req:Request):
        need_login(req)
        with engine.begin() as c:
            c.execute(text("DELETE FROM ai_marketing_contact_source_history WHERE contact_id=:id"),{"id":contact_id})
            n=c.execute(text("DELETE FROM ai_marketing_contact_vault WHERE contact_id=:id"),{"id":contact_id}).rowcount
        return {"status":"DELETED","contact_id":contact_id,"rows":n}
    @app.middleware("http")
    async def post_whatsapp_ingest_sync(request,call_next):
        response=await call_next(request)
        if request.method=="POST" and request.url.path=="/whatsapp-live/api/ingest" and response.status_code<300:
            try:_sync_whatsapp(engine);_sync_contacts(engine)
            except Exception as e:print("V3.7.3 post-ingest sync warning:",repr(e))
        return response
