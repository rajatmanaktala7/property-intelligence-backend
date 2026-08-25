
from __future__ import annotations
from pathlib import Path
from fastapi import Request, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text

MODULE_VERSION="3.5.0-OPERATIONAL-CONTROL-CENTRE"
HTML_FILE=Path(__file__).with_name("alliance_v35_dashboard.html")

def register(core):
    app,engine=core.app,core.engine

    @app.get("/api/v3/control/status")
    def status(req:Request):
        if hasattr(core,"need_login"): core.need_login(req)
        return {
            "version":MODULE_VERSION,
            "status":"OK",
            "same_app":True,
            "same_domain":True,
            "dashboard":"/v3/control-centre",
            "startup_db_work":False,
            "legacy_property_pipeline_untouched":True,
        }

    @app.get("/api/v3/control/requirements")
    def requirements(req:Request,limit:int=Query(100,ge=1,le=500)):
        if hasattr(core,"need_login"): core.need_login(req)
        with engine.connect() as c:
            rows=c.execute(text("""
              SELECT requirement_code,company_name,preferred_locations_raw AS locations,
                     minimum_area_sqft,maximum_area_sqft,transaction_type,
                     suitable_for,minimum_frontage_ft,created_at
              FROM ai_requirement_index
              WHERE requirement_code IS NOT NULL
              ORDER BY requirement_index_id DESC
              LIMIT :lim
            """),{"lim":limit}).mappings().all()
        return {"version":MODULE_VERSION,"count":len(rows),"requirements":[dict(x) for x in rows]}

    @app.get("/v3/control-centre",response_class=HTMLResponse)
    def dashboard(req:Request):
        if hasattr(core,"need_login"): core.need_login(req)
        return HTMLResponse(HTML_FILE.read_text(encoding="utf-8"))
