
import json
from uuid import UUID,uuid4
from fastapi import APIRouter,HTTPException,Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from .db import setup,table_exists
from .orchestrator import ingest_raw,process_raw_ids
from .adapters import import_whatsapp
from .stages.s9_requirement_brain import parse_requirement
from .stages.s11_match_engine import match
from .stages.s12_verification import set_verification
from .stages.s13_feedback import log_outcome
router=APIRouter(prefix="/property-brain",tags=["Property Brain"]);_engine=None
def configure(core):
    global _engine;_engine=core.engine
def ready():return _engine is not None and table_exists(_engine,"pb_raw_evidence")
@router.get("/status")
def status():return {"status":"READY" if ready() else "SETUP_REQUIRED","version":"1.0.0","database_initialized":ready(),"raw_source_mutation":False,"critical_path_llm_required":False}
@router.post("/setup")
def setup_db():
    if _engine is None:raise HTTPException(503,"Core DB unavailable")
    return setup(_engine)
@router.post("/ingest")
async def ingest(request:Request):
    if not ready():raise HTTPException(409,"Run setup first")
    p=await request.json();rid,new=ingest_raw(_engine,p.get("source_type","manual"),str(p.get("source_ref") or uuid4()),str(p.get("raw_text") or ""),p.get("sender"),p.get("sender_phone"),p.get("source_group"));return {"raw_id":str(rid),"inserted":new}
@router.post("/import/whatsapp")
def import_wa(limit:int=100,process:bool=True):
    if not ready():raise HTTPException(409,"Run setup first")
    import whatsapp_live_bridge as live
    r=import_whatsapp(_engine,live.wa_engine,min(limit,5000));ids=r.pop("inserted_raw_ids",[]);r["inserted"]=len(ids)
    if process and ids:r["processing"]=process_raw_ids(_engine,ids)
    return r
@router.post("/requirements/parse")
async def req_parse(request:Request):return parse_requirement(str((await request.json()).get("text") or "")).model_dump(mode="json")
@router.post("/match")
async def run_match(request:Request):
    p=await request.json();return match(_engine,parse_requirement(str(p.get("text") or "")),float(p.get("min_score",60)),int(p.get("limit",50)))
@router.get("/review/{queue_type}")
def review(queue_type:str,limit:int=200):
    if queue_type not in {"holding","rejected","entity-merges","location-aliases","near-misses"}:raise HTTPException(400,"Unsupported queue")
    with _engine.connect() as c:rows=[dict(r) for r in c.execute(text("SELECT * FROM pb_review_queue WHERE queue_type=:q AND status='OPEN' ORDER BY created_at DESC LIMIT :lim"),{"q":queue_type,"lim":limit}).mappings().all()]
    return {"queue":queue_type,"items":rows}
@router.post("/verify/{property_id}")
async def verify(property_id:UUID,request:Request):return set_verification(_engine,property_id,(await request.json()).get("status","VERIFIED"))
@router.post("/feedback")
async def feedback(request:Request):
    p=await request.json();return log_outcome(_engine,p.get("requirement_id"),p.get("property_id"),p["outcome"],p.get("notes"),p.get("actor"))
@router.get("",response_class=HTMLResponse)
def page():return HTMLResponse("<h2>Alliance Property Intelligence Brain</h2><p>Raw Evidence → Clean Canonical DB → Requirement → Match → Verification → Feedback</p>")
