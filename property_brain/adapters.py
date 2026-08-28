
from sqlalchemy import text
from .orchestrator import ingest_raw
def import_whatsapp(engine,wa_engine,limit=1000):
    if wa_engine is None:return {"status":"ERROR","inserted_raw_ids":[]}
    with wa_engine.connect() as c:
        try:rows=c.execute(text("SELECT message_id,raw_text,sender_name,sender_phone,message_timestamp,source_id FROM wa_messages WHERE COALESCE(raw_text,'')<>'' ORDER BY id DESC LIMIT :lim"),{"lim":limit}).mappings().all()
        except Exception:rows=c.execute(text("SELECT wa_property_id message_id,raw_text,COALESCE(owner_name,broker_name,sender_name) sender_name,COALESCE(owner_phone,broker_phone,sender_phone) sender_phone,last_seen message_timestamp,source_id FROM wa_properties WHERE COALESCE(raw_text,'')<>'' ORDER BY id DESC LIMIT :lim"),{"lim":limit}).mappings().all()
    ids=[];existing=0
    for r in rows:
        rid,new=ingest_raw(engine,"whatsapp",str(r["message_id"]),r["raw_text"],r.get("sender_name"),r.get("sender_phone"),str(r.get("source_id") or "WhatsApp"),r.get("message_timestamp"))
        ids.append(rid) if new else None;existing+=0 if new else 1
    return {"status":"OK","inserted_raw_ids":ids,"existing":existing,"scanned":len(rows)}
