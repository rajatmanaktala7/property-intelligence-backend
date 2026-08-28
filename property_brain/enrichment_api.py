from uuid import UUID
from fastapi import APIRouter,HTTPException
from sqlalchemy import text
from .stages.s8b_property_enrichment import enrich_property
router=APIRouter(prefix="/property-brain/enrichment",tags=["Property Brain Enrichment"]);_engine=None
def configure(engine):
 global _engine;_engine=engine
@router.post("/{property_id}")
def one(property_id:UUID):
 if _engine is None:raise HTTPException(503,"Enrichment engine not configured")
 return enrich_property(_engine,property_id)
@router.post("/batch/{limit}")
def batch(limit:int=1):
 if _engine is None:raise HTTPException(503,"Enrichment engine not configured")
 limit=max(1,min(limit,100))
 with _engine.connect() as c:ids=[r[0] for r in c.execute(text("SELECT property_id FROM pb_canonical_properties WHERE current_status='ACTIVE' ORDER BY updated_at ASC LIMIT :n"),{"n":limit}).all()]
 return {"requested":len(ids),"results":[enrich_property(_engine,x) for x in ids]}
