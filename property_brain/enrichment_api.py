from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from .stages.s8b_property_enrichment import enrich_property

router = APIRouter(
    prefix="/property-brain/enrichment",
    tags=["Property Brain Enrichment"],
)

_engine = None

def configure(engine):
    global _engine
    _engine = engine

def _ready():
    return _engine is not None

@router.get("/status")
def status():
    return {
        "status": "READY" if _ready() else "NOT_CONFIGURED",
        "version": "1.1.0",
        "startup_ddl": False,
        "raw_source_mutation": False,
    }

@router.post("/batch/{limit}")
def enrich_batch(limit: int = 1):
    if not _ready():
        raise HTTPException(503, "Enrichment engine not configured")

    limit = max(1, min(limit, 100))

    with _engine.connect() as c:
        ids = [
            row[0]
            for row in c.execute(
                text(
                    """
                    SELECT property_id
                    FROM pb_canonical_properties
                    WHERE current_status = 'ACTIVE'
                    ORDER BY updated_at ASC
                    LIMIT :n
                    """
                ),
                {"n": limit},
            ).all()
        ]

    return {
        "requested": len(ids),
        "results": [enrich_property(_engine, property_id) for property_id in ids],
    }

@router.post("/property/{property_id}")
def enrich_one(property_id: UUID):
    if not _ready():
        raise HTTPException(503, "Enrichment engine not configured")

    return enrich_property(_engine, property_id)
