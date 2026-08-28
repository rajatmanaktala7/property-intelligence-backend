
VERSION="1.0.0-ALLIANCE-PROPERTY-BRAIN"
def register(core):
    try:
        from property_brain.api import router,configure
        configure(core);app=core.app
        if not getattr(app.state,"alliance_property_brain_v1_registered",False):
            app.include_router(router);app.state.alliance_property_brain_v1_registered=True
        return {"status":"REGISTERED","version":VERSION,"route":"/property-brain","startup_ddl":False}
    except Exception as e:return {"status":"DEGRADED","version":VERSION,"error":f"{type(e).__name__}: {e}"}
