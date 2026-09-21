"""Alliance V2 read-only WhatsApp sample export.

This router exposes genuine WhatsApp Live property evidence for the V2
cleaning/review experiment. It never writes to the V1 WhatsApp database.
"""
import os
from fastapi import APIRouter, HTTPException, Query, Header
from sqlalchemy import create_engine, text

router = APIRouter(prefix="/whatsapp-v2-export", tags=["WhatsApp V2 Read Only"])
WA_DATABASE_URL = os.getenv("WHATSAPP_DATABASE_URL", "").strip()\nEXPORT_TOKEN = os.getenv("WHATSAPP_V2_EXPORT_TOKEN", "").strip()

def _db_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url

engine = create_engine(_db_url(WA_DATABASE_URL), pool_pre_ping=True, pool_recycle=300) if WA_DATABASE_URL else None

@router.get("/properties")
def export_properties(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    if engine is None:
        raise HTTPException(503, "WHATSAPP_DATABASE_URL is not configured")

    sql = text("""
        SELECT
            m.message_id::text AS message_id,
            m.message_timestamp,
            m.sender_name,
            m.sender_phone,
            m.raw_text AS original_message,
            m.classification,
            m.confidence AS message_confidence,
            s.source_id::text AS source_id,
            s.group_name AS source_group,
            p.wa_property_id,
            p.source_item_no,
            p.raw_text AS extracted_property_text,
            p.property_type,
            p.transaction_type,
            p.city,
            p.location,
            p.locality,
            p.address,
            p.landmark,
            p.area_sqft,
            p.available_area_sqft,
            p.floor,
            p.frontage,
            p.rent_inr,
            p.sale_price_inr,
            p.cam_inr,
            p.possession,
            p.parking,
            p.suitable_for,
            p.nearby_brands,
            p.availability,
            p.broker_name,
            p.broker_phone,
            p.owner_name,
            p.owner_phone,
            p.sender_name AS property_sender_name,
            p.sender_phone AS property_sender_phone,
            p.duplicate_status,
            p.duplicate_of,
            p.confidence AS property_confidence,
            p.first_seen,
            p.last_seen
        FROM wa_properties p
        JOIN wa_messages m ON m.message_id = p.message_id
        LEFT JOIN wa_sources s ON s.source_id = p.source_id
        WHERE COALESCE(p.record_status, 'ACTIVE') = 'ACTIVE'
          AND COALESCE(p.duplicate_status, 'UNIQUE') <> 'DUPLICATE'
        ORDER BY COALESCE(p.last_seen, p.first_seen, m.created_at) DESC, p.id DESC
        LIMIT :limit OFFSET :offset
    """)

    with engine.connect() as conn:
        rows = conn.execute(sql, {"limit": limit, "offset": offset}).mappings().all()

    return {
        "authority": "WHATSAPP_LIVE_READ_ONLY",
        "purpose": "ALLIANCE_V2_CLEANING_REVIEW",
        "count": len(rows),
        "limit": limit,
        "offset": offset,
        "records": [dict(row) for row in rows],
    }
