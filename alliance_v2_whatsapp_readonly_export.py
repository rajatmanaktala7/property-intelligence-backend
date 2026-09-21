"""Standalone authenticated V1 reader; no DDL, DML, cleanup or schema migration.

Run in the V1 runtime/network:
python -m uvicorn alliance_v2_whatsapp_readonly_export:app --host 0.0.0.0 --port 8001
Or explicitly include `router` in an existing authenticated V1 deployment.
Use a dedicated SELECT-only PostgreSQL role where available.
"""
import hmac
import os
from contextlib import asynccontextmanager
import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, FastAPI, Header, HTTPException, Query, Response

router = APIRouter(prefix='/whatsapp-v2-export')
SQL = '''SELECT m.message_id::text AS message_id, m.message_timestamp,
 m.sender_name, m.sender_phone, m.raw_text AS original_message, m.classification,
 s.source_id::text AS source_id, s.group_name AS source_group,
 p.wa_property_id, p.source_item_no, p.raw_text AS extracted_property_text
 FROM wa_properties p JOIN wa_messages m ON m.message_id=p.message_id
 LEFT JOIN wa_sources s ON s.source_id=p.source_id
 WHERE COALESCE(p.record_status,'ACTIVE')='ACTIVE' AND m.raw_text IS NOT NULL
 ORDER BY COALESCE(p.last_seen,p.first_seen,m.created_at) DESC,p.id DESC
 LIMIT %s OFFSET %s'''


def configuration():
    token = os.getenv('WHATSAPP_V2_EXPORT_TOKEN', '')
    url = os.getenv('WHATSAPP_DATABASE_URL', '')
    if len(token) < 32 or not url.startswith(('postgres://', 'postgresql://')):
        raise RuntimeError('Set WHATSAPP_DATABASE_URL and WHATSAPP_V2_EXPORT_TOKEN (32+ characters)')
    return url, token


@router.get('/properties')
def export_properties(response: Response, limit: int = Query(100, ge=1, le=100),
                      offset: int = Query(0, ge=0, le=100000), authorization: str | None = Header(None)):
    try:
        url, token = configuration()
    except RuntimeError:
        raise HTTPException(503, 'Read-only exporter is not configured')
    if not authorization or not hmac.compare_digest(authorization, 'Bearer ' + token):
        raise HTTPException(401, 'Invalid export credentials')
    response.headers['Cache-Control'] = 'no-store'
    try:
        with psycopg.connect(url, row_factory=dict_row, connect_timeout=10,
                            options='-c default_transaction_read_only=on -c statement_timeout=15000') as conn:
            conn.execute('SET TRANSACTION READ ONLY')
            rows = conn.execute(SQL, (limit, offset)).fetchall()
    except psycopg.Error:
        raise HTTPException(503, 'Read-only export unavailable; verify V1 schema and connection')
    # Keep source timestamps as source text; do not introduce a timezone assumption.
    for row in rows:
        if row['message_timestamp'] is not None:
            row['message_timestamp'] = row['message_timestamp'].isoformat() if hasattr(row['message_timestamp'], 'isoformat') else str(row['message_timestamp'])
    return {'authority': 'WHATSAPP_LIVE_READ_ONLY', 'count': len(rows), 'records': rows}


@asynccontextmanager
async def lifespan(app):
    configuration()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(router)
