from __future__ import annotations

import asyncio
import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

VERSION = "5.0.0-SAFE-WHATSAPP-QUEUE"

_worker_lock = threading.Lock()
_worker_started = False
_worker_stop = threading.Event()

STATE = {
    "worker_started": False,
    "worker_alive": False,
    "last_poll_at": None,
    "last_processed_at": None,
    "last_error": None,
    "processed": 0,
    "duplicates": 0,
    "errors": 0,
}


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def _bridge():
    import whatsapp_live_bridge as wb
    return wb


def _ensure_queue_schema_sync():
    wb = _bridge()
    wb.init_db()
    with wb.wa_engine.begin() as c:
        c.execute(text("ALTER TABLE wa_bridge_events ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0"))
        c.execute(text("ALTER TABLE wa_bridge_events ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ"))
        c.execute(text("ALTER TABLE wa_bridge_events ADD COLUMN IF NOT EXISTS payload_json JSONB"))
        c.execute(text("CREATE INDEX IF NOT EXISTS idx_wa_bridge_events_queue ON wa_bridge_events(status,next_retry_at,id)"))
    return True


def _validate_and_queue_sync(payload: dict[str, Any], authorization: str | None, bridge_token: str | None):
    wb = _bridge()
    wb._auth(authorization, bridge_token)
    _ensure_queue_schema_sync()

    account_phone = str(payload.get("account_phone") or "").strip()
    group_name = str(payload.get("group_name") or "").strip()
    raw_text = str(payload.get("text") or "").strip()

    if not account_phone or not group_name or not raw_text:
        return 400, {"status": "ERROR", "detail": "account_phone, group_name and text are required"}

    external_message_id = str(
        payload.get("external_message_id") or payload.get("message_id") or payload.get("id") or ""
    ).strip()
    if not external_message_id:
        external_message_id = "AUTO-" + uuid.uuid5(
            uuid.NAMESPACE_URL,
            "|".join([
                account_phone,
                group_name,
                str(payload.get("sender_phone") or ""),
                str(payload.get("timestamp") or ""),
                raw_text,
            ]),
        ).hex

    with wb.wa_engine.begin() as c:
        acct = c.execute(text("""
            SELECT * FROM wa_bridge_accounts
            WHERE phone=:p AND active=TRUE
        """), {"p": account_phone}).mappings().first()
        if not acct:
            return 403, {"status": "ERROR", "detail": "This mobile number is not added/active in WhatsApp Sources"}

        group = c.execute(text("""
            SELECT * FROM wa_bridge_groups
            WHERE account_id=:a AND group_name=:n AND active=TRUE AND auto_process=TRUE
        """), {"a": acct["account_id"], "n": group_name}).mappings().first()
        if not group:
            return 403, {"status": "ERROR", "detail": "This group is not added/active for this mobile number"}

        existing = c.execute(text("""
            SELECT event_id,status,classification,entity_id
            FROM wa_bridge_events
            WHERE group_id=:g AND external_message_id=:m
            LIMIT 1
        """), {"g": group["group_id"], "m": external_message_id}).mappings().first()
        if existing:
            STATE["duplicates"] += 1
            return 200, {
                "status": "DUPLICATE",
                "event_id": existing["event_id"],
                "event_status": existing["status"],
                "classification": existing["classification"],
                "entity_id": existing["entity_id"],
            }

        event_id = "WAE-" + uuid.uuid4().hex[:16].upper()
        c.execute(text("""
            INSERT INTO wa_bridge_events(
                event_id,group_id,external_message_id,sender_name,sender_phone,
                message_timestamp,raw_text,status,error_message,payload_json,created_at
            ) VALUES(
                :eid,:gid,:mid,:sn,:sp,:ts,:raw,'QUEUED',NULL,CAST(:payload AS JSONB),NOW()
            )
        """), {
            "eid": event_id,
            "gid": group["group_id"],
            "mid": external_message_id,
            "sn": str(payload.get("sender_name") or "").strip() or None,
            "sp": str(payload.get("sender_phone") or "").strip() or None,
            "ts": str(payload.get("timestamp") or payload.get("message_timestamp") or "").strip() or None,
            "raw": raw_text,
            "payload": json.dumps(payload, ensure_ascii=False),
        })
        c.execute(text("""
            UPDATE wa_bridge_groups
            SET messages_received=COALESCE(messages_received,0)+1,
                last_message_at=NOW(),updated_at=NOW()
            WHERE group_id=:g
        """), {"g": group["group_id"]})

    return 202, {
        "status": "QUEUED",
        "event_id": event_id,
        "external_message_id": external_message_id,
        "message": "Accepted for background WhatsApp processing",
    }


async def handle_ingest(scope, receive, send):
    try:
        req = Request(scope, receive=receive)
        payload = await req.json()
        try:
            status_code, body = await asyncio.wait_for(
                asyncio.to_thread(
                    _validate_and_queue_sync,
                    payload,
                    req.headers.get("authorization"),
                    req.headers.get("x-bridge-token"),
                ),
                timeout=4.0,
            )
        except asyncio.TimeoutError:
            response = JSONResponse(
                status_code=503,
                content={"status": "BUSY", "message": "Queue receiver timeout", "retry_after_seconds": 5},
                headers={"Retry-After": "5", "Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return
        response = JSONResponse(status_code=status_code, content=body, headers={"Cache-Control": "no-store"})
        await response(scope, receive, send)
    except json.JSONDecodeError:
        response = JSONResponse(status_code=400, content={"status": "ERROR", "detail": "Invalid JSON body"})
        await response(scope, receive, send)
    except Exception as exc:
        response = JSONResponse(
            status_code=503,
            content={"status": "ERROR", "detail": f"{type(exc).__name__}: {exc}"},
            headers={"Retry-After": "5", "Cache-Control": "no-store"},
        )
        await response(scope, receive, send)


def _claim_one():
    wb = _bridge()
    _ensure_queue_schema_sync()
    with wb.wa_engine.begin() as c:
        row = c.execute(text("""
            SELECT e.id,e.event_id,e.group_id,e.external_message_id,e.sender_name,e.sender_phone,
                   e.message_timestamp,e.raw_text,e.retry_count,g.account_id,g.group_name,g.source_id
            FROM wa_bridge_events e
            JOIN wa_bridge_groups g ON g.group_id=e.group_id
            WHERE e.status IN ('QUEUED','RETRY')
              AND (e.next_retry_at IS NULL OR e.next_retry_at<=NOW())
              AND g.active=TRUE AND g.auto_process=TRUE
            ORDER BY e.id
            FOR UPDATE OF e SKIP LOCKED
            LIMIT 1
        """)).mappings().first()
        if not row:
            return None
        c.execute(text("UPDATE wa_bridge_events SET status='PROCESSING',error_message=NULL WHERE id=:id"), {"id": row["id"]})
        return dict(row)


def _process_one(row):
    wb = _bridge()
    ev = {
        "text": row.get("raw_text"),
        "sender_name": row.get("sender_name"),
        "sender_phone": row.get("sender_phone"),
        "timestamp": row.get("message_timestamp"),
        "external_message_id": row.get("external_message_id"),
    }
    try:
        with wb.wa_engine.begin() as c:
            group = c.execute(text("""
                SELECT * FROM wa_bridge_groups
                WHERE group_id=:g AND active=TRUE AND auto_process=TRUE
            """), {"g": row["group_id"]}).mappings().first()
            if not group:
                c.execute(text("UPDATE wa_bridge_events SET status='ERROR',error_message='Group inactive or missing',processed_at=NOW() WHERE id=:id"), {"id": row["id"]})
                return

            classification, entity_id = wb._process(c, group, ev)
            c.execute(text("""
                UPDATE wa_bridge_events
                SET status='PROCESSED',classification=:cl,entity_id=:entity,
                    error_message=NULL,processed_at=NOW()
                WHERE id=:id
            """), {"id": row["id"], "cl": classification, "entity": entity_id})

            if classification == "PROPERTY_INVENTORY":
                c.execute(text("UPDATE wa_bridge_groups SET properties_found=COALESCE(properties_found,0)+1,updated_at=NOW() WHERE group_id=:g"), {"g": row["group_id"]})
            elif classification == "PROPERTY_REQUIREMENT":
                c.execute(text("UPDATE wa_bridge_groups SET requirements_found=COALESCE(requirements_found,0)+1,updated_at=NOW() WHERE group_id=:g"), {"g": row["group_id"]})
            elif classification in ("REJECTED", "REVIEW"):
                c.execute(text("UPDATE wa_bridge_groups SET rejected_found=COALESCE(rejected_found,0)+1,updated_at=NOW() WHERE group_id=:g"), {"g": row["group_id"]})

        STATE["processed"] += 1
        STATE["last_processed_at"] = _utcnow()
        STATE["last_error"] = None
        try:
            import alliance_auto_updater as updater
            updater.request_refresh(force=False)
        except Exception:
            pass
    except Exception as exc:
        STATE["errors"] += 1
        STATE["last_error"] = f"{type(exc).__name__}: {exc}"
        retry_count = int(row.get("retry_count") or 0) + 1
        with wb.wa_engine.begin() as c:
            if retry_count <= 5:
                delay = min(300, 5 * (2 ** (retry_count - 1)))
                c.execute(text("""
                    UPDATE wa_bridge_events
                    SET status='RETRY',retry_count=:r,
                        next_retry_at=NOW() + (:delay || ' seconds')::interval,
                        error_message=:err
                    WHERE id=:id
                """), {"id": row["id"], "r": retry_count, "delay": str(delay), "err": STATE["last_error"][:1500]})
            else:
                c.execute(text("UPDATE wa_bridge_events SET status='ERROR',retry_count=:r,error_message=:err,processed_at=NOW() WHERE id=:id"), {"id": row["id"], "r": retry_count, "err": STATE["last_error"][:1500]})


def _worker():
    STATE["worker_alive"] = True
    try:
        _ensure_queue_schema_sync()
        while not _worker_stop.is_set():
            STATE["last_poll_at"] = _utcnow()
            try:
                row = _claim_one()
                if not row:
                    _worker_stop.wait(0.75)
                    continue
                _process_one(row)
            except Exception as exc:
                STATE["errors"] += 1
                STATE["last_error"] = f"{type(exc).__name__}: {exc}"
                _worker_stop.wait(2.0)
    finally:
        STATE["worker_alive"] = False


def start_worker():
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return dict(STATE)
        t = threading.Thread(target=_worker, name="alliance-whatsapp-safe-queue-worker", daemon=True)
        t.start()
        _worker_started = True
        STATE["worker_started"] = True
        return dict(STATE)


def queue_status():
    data = dict(STATE)
    try:
        wb = _bridge()
        _ensure_queue_schema_sync()
        with wb.wa_engine.connect() as c:
            rows = c.execute(text("SELECT status,COUNT(*) n FROM wa_bridge_events GROUP BY status")).mappings().all()
            data["queue_counts"] = {str(x["status"]): int(x["n"]) for x in rows}
            data["latest_event_at"] = c.execute(text("SELECT MAX(created_at) FROM wa_bridge_events")).scalar()
            data["latest_processed_at"] = c.execute(text("SELECT MAX(processed_at) FROM wa_bridge_events")).scalar()
    except Exception as exc:
        data["status_error"] = f"{type(exc).__name__}: {exc}"
    for k in ("latest_event_at", "latest_processed_at"):
        if hasattr(data.get(k), "isoformat"):
            data[k] = data[k].isoformat()
    data["version"] = VERSION
    return data
