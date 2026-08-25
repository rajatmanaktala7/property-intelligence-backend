
from __future__ import annotations
import json, re
from pathlib import Path
from fastapi import Request, Body, UploadFile, File, Form, Query, HTTPException
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import text

MODULE_VERSION="3.6.0-LIVE-TEAM-OPERATIONS-DASHBOARD"
HTML_FILE=Path(__file__).with_name("alliance_v36_dashboard.html")

DATASETS={
 "manual":{
   "table":"ai_team_live_record","id":"record_id",
   "fields":{"source_channel","record_type","business_name","contact_name","phone","email","location","details","status","assigned_to"}
 },
 "whatsapp":{
   "table":"wai_clean_records","id":"id",
   "fields":{"record_type","transaction","raw_details","contact_no","budget_text","area_text","location","property_type","person_name","firm_name","status","rejection_reason"}
 },
 "newspaper":{
   "table":"pi_newspaper_properties","id":"id",
   "fields":{"lead_type","locality","area","configuration_details","price","agency_brand","contact_person","phone_numbers","notes","completeness","verification","team_member"}
 },
 "hospitality":{
   "table":"ai_hospitality_entity","id":"hospitality_id",
   "fields":{"business_name","category","location","city","contact_name","contact_phone","whatsapp_phone","email","website","verification_status","outreach_status","assigned_to","notes","active"}
 },
 "retail_signal":{
   "table":"ai_retail_expansion_signal","id":"signal_id",
   "fields":{"company_name","category","headline","published_at","evidence_text","intent_score","intent_status","location_signal","outlet_target"}
 },
 "retail_contact":{
   "table":"ai_retail_contact","id":"retail_contact_id",
   "fields":{"person_name","designation","company_name","category","linkedin_profile_url","contact_phone","email","website","city","verification_status","active"}
 },
}

def _table_exists(c,t):
    return bool(c.execute(text("SELECT to_regclass(:n) IS NOT NULL"),{"n":"public."+t}).scalar())

def _safe_limit(v,hi=500):
    try:return max(1,min(int(v),hi))
    except:return 100

def ensure_live_schema(engine):
    with engine.begin() as c:
        c.execute(text("SET LOCAL lock_timeout='2s'"))
        c.execute(text("SET LOCAL statement_timeout='6s'"))
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_team_live_record(
          record_id BIGSERIAL PRIMARY KEY,
          source_channel TEXT NOT NULL,
          record_type TEXT,
          business_name TEXT,
          contact_name TEXT,
          phone TEXT,
          email TEXT,
          location TEXT,
          details TEXT,
          status TEXT NOT NULL DEFAULT 'UNVERIFIED',
          assigned_to TEXT,
          created_by TEXT,
          created_at TIMESTAMPTZ DEFAULT NOW(),
          updated_at TIMESTAMPTZ DEFAULT NOW()
        )"""))
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_team_live_media(
          media_id BIGSERIAL PRIMARY KEY,
          record_id BIGINT REFERENCES ai_team_live_record(record_id) ON DELETE CASCADE,
          filename TEXT,
          mime_type TEXT,
          content BYTEA NOT NULL,
          created_at TIMESTAMPTZ DEFAULT NOW()
        )"""))
    return {"status":"READY"}

def _serialize(rows):
    out=[]
    for r in rows:
        d=dict(r)
        for k,v in list(d.items()):
            if hasattr(v,"isoformat"):
                d[k]=v.isoformat()
            elif isinstance(v,(bytes,bytearray)):
                d[k]=None
        out.append(d)
    return out

def _dataset_rows(engine,dataset,limit=100,source_channel=""):
    limit=_safe_limit(limit,500)
    with engine.connect() as c:
        if dataset=="manual":
            if not _table_exists(c,"ai_team_live_record"): return []
            q="SELECT * FROM ai_team_live_record"
            p={"lim":limit}
            if source_channel:
                q+=" WHERE source_channel=:src";p["src"]=source_channel.upper()
            q+=" ORDER BY record_id DESC LIMIT :lim"
            return _serialize(c.execute(text(q),p).mappings().all())
        cfg=DATASETS[dataset]
        if not _table_exists(c,cfg["table"]): return []
        tbl=cfg["table"];pk=cfg["id"]
        return _serialize(c.execute(text(f'SELECT * FROM "{tbl}" ORDER BY "{pk}" DESC LIMIT :lim'),
                                    {"lim":limit}).mappings().all())

def register(core):
    app,engine=core.app,core.engine

    @app.get("/api/v3/live/status")
    def status(req:Request):
        if hasattr(core,"need_login"):core.need_login(req)
        with engine.connect() as c:
            states={k:_table_exists(c,v["table"]) for k,v in DATASETS.items() if k!="manual"}
            states["manual"]=_table_exists(c,"ai_team_live_record")
        return {"version":MODULE_VERSION,"status":"OK","datasets":states,
                "dashboard":"/v3/control-centre","same_app":True,"startup_ddl":False}

    @app.post("/api/v3/live/setup")
    def setup(req:Request):
        if hasattr(core,"need_login"):core.need_login(req)
        try:return {"version":MODULE_VERSION,**ensure_live_schema(engine)}
        except Exception as e:return {"version":MODULE_VERSION,"status":"SCHEMA_BUSY","message":str(e)}

    @app.get("/api/v3/live/data/{dataset}")
    def data(dataset:str,req:Request,limit:int=100,source_channel:str=""):
        if hasattr(core,"need_login"):core.need_login(req)
        if dataset not in DATASETS: raise HTTPException(404,"Unknown dataset")
        try:
            rows=_dataset_rows(engine,dataset,limit,source_channel)
            return {"version":MODULE_VERSION,"dataset":dataset,"count":len(rows),"rows":rows}
        except Exception as e:
            return {"version":MODULE_VERSION,"dataset":dataset,"status":"ERROR","message":str(e),"rows":[]}

    @app.post("/api/v3/live/manual")
    def add_manual(req:Request,payload:dict=Body(...)):
        if hasattr(core,"need_login"):core.need_login(req)
        ensure_live_schema(engine)
        src=str(payload.get("source_channel") or "MANUAL").upper()
        allowed={"MANUAL","WHATSAPP_GROUP","NEWSPAPER","MAGAZINE","PHONE_IMPORT","OTHER"}
        if src not in allowed: src="MANUAL"
        with engine.begin() as c:
            rid=c.execute(text("""
              INSERT INTO ai_team_live_record(
                source_channel,record_type,business_name,contact_name,phone,email,
                location,details,status,assigned_to,created_by,created_at,updated_at)
              VALUES(:src,:rt,:bn,:cn,:ph,:em,:loc,:det,:st,:asgn,:by,NOW(),NOW())
              RETURNING record_id
            """),{"src":src,"rt":payload.get("record_type"),"bn":payload.get("business_name"),
                 "cn":payload.get("contact_name"),"ph":payload.get("phone"),"em":payload.get("email"),
                 "loc":payload.get("location"),"det":payload.get("details"),
                 "st":str(payload.get("status") or "UNVERIFIED").upper(),
                 "asgn":payload.get("assigned_to"),"by":payload.get("created_by")}).scalar_one()
        return {"version":MODULE_VERSION,"status":"OK","record_id":int(rid),"saved":True}

    @app.post("/api/v3/live/upload")
    async def upload(req:Request,file:UploadFile=File(...),
                     source_channel:str=Form("NEWSPAPER"),record_type:str=Form("IMAGE_UPLOAD"),
                     business_name:str=Form(""),contact_name:str=Form(""),phone:str=Form(""),
                     email:str=Form(""),location:str=Form(""),details:str=Form(""),
                     assigned_to:str=Form("")):
        if hasattr(core,"need_login"):core.need_login(req)
        ensure_live_schema(engine)
        src=source_channel.upper()
        if src not in {"NEWSPAPER","MAGAZINE","WHATSAPP_GROUP","MANUAL","OTHER"}:src="OTHER"
        content=await file.read()
        if not content:raise HTTPException(400,"Empty file")
        if len(content)>20*1024*1024:raise HTTPException(413,"File too large (20 MB max)")
        with engine.begin() as c:
            rid=c.execute(text("""
              INSERT INTO ai_team_live_record(source_channel,record_type,business_name,contact_name,
                phone,email,location,details,status,assigned_to,created_at,updated_at)
              VALUES(:src,:rt,:bn,:cn,:ph,:em,:loc,:det,'UNVERIFIED',:asgn,NOW(),NOW())
              RETURNING record_id
            """),{"src":src,"rt":record_type,"bn":business_name,"cn":contact_name,"ph":phone,
                 "em":email,"loc":location,"det":details,"asgn":assigned_to}).scalar_one()
            mid=c.execute(text("""
              INSERT INTO ai_team_live_media(record_id,filename,mime_type,content,created_at)
              VALUES(:rid,:fn,:mt,:ct,NOW()) RETURNING media_id
            """),{"rid":rid,"fn":file.filename,"mt":file.content_type or "application/octet-stream",
                 "ct":content}).scalar_one()
        return {"version":MODULE_VERSION,"status":"OK","record_id":int(rid),"media_id":int(mid),
                "source_channel":src,"saved_permanently":True}

    @app.get("/api/v3/live/media/{media_id}")
    def media(media_id:int,req:Request):
        if hasattr(core,"need_login"):core.need_login(req)
        with engine.connect() as c:
            r=c.execute(text("SELECT filename,mime_type,content FROM ai_team_live_media WHERE media_id=:id"),
                        {"id":media_id}).mappings().first()
        if not r:raise HTTPException(404,"Media not found")
        return Response(content=bytes(r["content"]),media_type=r["mime_type"] or "application/octet-stream",
                        headers={"Content-Disposition":f'inline; filename="{r["filename"] or "file"}"'})

    @app.post("/api/v3/live/update/{dataset}/{row_id}")
    def update(dataset:str,row_id:str,req:Request,payload:dict=Body(...)):
        if hasattr(core,"need_login"):core.need_login(req)
        if dataset not in DATASETS:raise HTTPException(404,"Unknown dataset")
        cfg=DATASETS[dataset];allowed=cfg["fields"]
        vals={k:v for k,v in payload.items() if k in allowed}
        if not vals:return {"version":MODULE_VERSION,"status":"NO_CHANGES"}
        sets=[];params={"id":row_id}
        for i,(k,v) in enumerate(vals.items()):
            key=f"v{i}";sets.append(f'"{k}"=:{key}');params[key]=v
        # timestamps only where known
        if dataset in {"manual","newspaper","hospitality","retail_contact"}:
            sets.append("updated_at=NOW()")
        with engine.begin() as c:
            c.execute(text(f'UPDATE "{cfg["table"]}" SET {",".join(sets)} WHERE "{cfg["id"]}"=:id'),params)
        return {"version":MODULE_VERSION,"status":"UPDATED","dataset":dataset,"id":row_id}

    @app.delete("/api/v3/live/delete/{dataset}/{row_id}")
    def delete(dataset:str,row_id:str,req:Request):
        if hasattr(core,"need_login"):core.need_login(req)
        if dataset not in DATASETS:raise HTTPException(404,"Unknown dataset")
        cfg=DATASETS[dataset]
        with engine.begin() as c:
            result=c.execute(text(f'DELETE FROM "{cfg["table"]}" WHERE "{cfg["id"]}"=:id'),{"id":row_id})
        return {"version":MODULE_VERSION,"status":"DELETED","dataset":dataset,"id":row_id,
                "rows_deleted":result.rowcount}

    @app.get("/api/v3/live/pipeline")
    def pipeline(req:Request):
        if hasattr(core,"need_login"):core.need_login(req)
        out={"steps":[
          {"key":"V2.6","name":"Team Action"},
          {"key":"V2.7","name":"Existing Inventory"},
          {"key":"V2.8","name":"External Discovery"},
          {"key":"V2.9A","name":"Entity Splitter"},
          {"key":"V2.9.5","name":"Entity Verification"},
        ]}
        try:
            with engine.connect() as c:
                if _table_exists(c,"ai_v30_orchestrator_run"):
                    runs=c.execute(text("""SELECT run_id,requirement_code,run_status,current_step,next_step,
                       requires_human_review,started_at,completed_at
                       FROM ai_v30_orchestrator_run ORDER BY run_id DESC LIMIT 20""")).mappings().all()
                    out["runs"]=_serialize(runs)
                else:out["runs"]=[]
        except Exception as e:out["error"]=str(e);out["runs"]=[]
        return {"version":MODULE_VERSION,**out}

    @app.get("/v3/control-centre",response_class=HTMLResponse)
    def dashboard(req:Request):
        if hasattr(core,"need_login"):core.need_login(req)
        return HTMLResponse(HTML_FILE.read_text(encoding="utf-8"))
