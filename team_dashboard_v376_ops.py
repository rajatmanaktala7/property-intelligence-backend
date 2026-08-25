
from __future__ import annotations
from datetime import datetime, timezone
from fastapi import Request
from sqlalchemy import text

MODULE_VERSION="3.7.6-FRESHNESS-NAVIGATION-FIX"

def _iso(v):
    return v.isoformat() if hasattr(v,"isoformat") else v

def register(app,engine,need_login):
    @app.get("/api/team-dashboard-v376/status")
    def status(req:Request):
        need_login(req)
        return {
            "version":MODULE_VERSION,
            "status":"OK",
            "freshness_monitor":True,
            "whatsapp_navigation_fixed":True,
            "dashboard":"/team-dashboard-v376",
            "same_app":True,
        }

    @app.get("/api/team-dashboard-v376/freshness")
    def freshness(req:Request):
        need_login(req)
        result={
            "version":MODULE_VERSION,
            "server_now_utc":datetime.now(timezone.utc).isoformat(),
            "whatsapp":{"status":"UNKNOWN","groups":[]},
            "newspaper":{},"magazine":{},"hospitality":{},"retail":{},"manual":{}
        }

        try:
            import whatsapp_live_bridge as wb
            if wb.wa_engine is None:
                result["whatsapp"]={"status":"OFFLINE","reason":"WHATSAPP_DATABASE_URL not configured","groups":[]}
            else:
                now=datetime.now(timezone.utc)
                with wb.wa_engine.connect() as c:
                    latest=c.execute(text("SELECT MAX(created_at) FROM wa_bridge_events")).scalar()
                    latest_processed=c.execute(text("SELECT MAX(processed_at) FROM wa_bridge_events WHERE status='PROCESSED'")).scalar()
                    groups=c.execute(text("""SELECT g.group_id,g.group_name,g.active,g.auto_process,
                        g.messages_received,g.properties_found,g.requirements_found,g.last_message_at,
                        a.label account_label,a.phone account_phone
                        FROM wa_bridge_groups g
                        JOIN wa_bridge_accounts a ON a.account_id=g.account_id
                        ORDER BY g.last_message_at DESC NULLS LAST,g.id DESC""")).mappings().all()
                age_hours=None
                if latest:
                    lv=latest if getattr(latest,"tzinfo",None) else latest.replace(tzinfo=timezone.utc)
                    age_hours=round((now-lv).total_seconds()/3600,2)
                status="LIVE" if age_hours is not None and age_hours<=6 else "STALE" if latest else "NO_EVENTS"
                result["whatsapp"]={
                    "status":status,
                    "latest_event_at":_iso(latest),
                    "latest_processed_at":_iso(latest_processed),
                    "age_hours":age_hours,
                    "groups":[{k:_iso(v) for k,v in dict(r).items()} for r in groups],
                    "ingest_endpoint":"/whatsapp-live/api/ingest",
                    "manage_sources":"/whatsapp-live/sources",
                    "live_feed":"/whatsapp-live/feed",
                }
        except Exception as exc:
            result["whatsapp"]={"status":"ERROR","reason":str(exc),"groups":[]}

        try:
            with engine.connect() as c:
                def exists(name):
                    return bool(c.execute(text("SELECT to_regclass(:n) IS NOT NULL"),{"n":"public."+name}).scalar())
                if exists("pi_newspaper_properties"):
                    result["newspaper"]={"latest":_iso(c.execute(text("SELECT MAX(COALESCE(updated_at,created_at)) FROM pi_newspaper_properties")).scalar())}
                if exists("pi_magazine_master"):
                    result["magazine"]={"latest":_iso(c.execute(text("SELECT MAX(updated_at) FROM pi_magazine_master")).scalar())}
                if exists("ai_hospitality_entity"):
                    result["hospitality"]={"latest":_iso(c.execute(text("SELECT MAX(updated_at) FROM ai_hospitality_entity")).scalar())}
                if exists("ai_retail_expansion_signal"):
                    result["retail"]={"latest":_iso(c.execute(text("SELECT MAX(COALESCE(last_seen_at,first_seen_at)) FROM ai_retail_expansion_signal")).scalar())}
                if exists("ai_manual_property_final"):
                    result["manual"]={"latest":_iso(c.execute(text("SELECT MAX(updated_at) FROM ai_manual_property_final")).scalar())}
        except Exception as exc:
            result["primary_db_error"]=str(exc)

        return result
