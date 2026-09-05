from sqlalchemy import text
from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

def register(wrapped):
    app = wrapped.app
    core = wrapped.core
    engine = core.engine

    @app.get("/admin/db-schema-probe-117", response_class=PlainTextResponse, include_in_schema=False)
    def db_schema_probe():
        tables = [
            "pi_operational_properties",
            "pi_newspaper_properties",
            "pi_magazine_master",
            "pi_whatsapp_property_master",
            "pi_master_properties_v711",
        ]
        out = []
        with engine.connect() as conn:
            for table in tables:
                out.append("\n==============================")
                out.append("TABLE: " + table)
                out.append("==============================")
                rows = conn.execute(text("""
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema='public'
                      AND table_name=:table
                    ORDER BY ordinal_position
                """), {"table": table}).fetchall()
                for row in rows:
                    out.append(str(tuple(row)))

            out.append("\n==============================")
            out.append("MEDIA / IMAGE / VIDEO TABLES")
            out.append("==============================")

            media = conn.execute(text("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema='public'
                  AND (
                       table_name ILIKE '%media%'
                    OR table_name ILIKE '%image%'
                    OR table_name ILIKE '%video%'
                    OR table_name ILIKE '%photo%'
                    OR table_name ILIKE '%attachment%'
                  )
                ORDER BY table_name
            """)).fetchall()

            for r in media:
                table = r[0]
                out.append("\nMEDIA TABLE: " + table)
                rows = conn.execute(text("""
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema='public'
                      AND table_name=:table
                    ORDER BY ordinal_position
                """), {"table": table}).fetchall()
                for row in rows:
                    out.append("   " + str(tuple(row)))

        return "\n".join(out)

    return {"status":"REGISTERED"}
