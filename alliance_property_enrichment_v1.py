VERSION="1.0.0-PROPERTY-ENRICHMENT"
def register(core):
 from property_brain.enrichment_api import router,configure
 configure(core.engine);core.include_router(router)
 return {"status":"REGISTERED","version":VERSION,"startup_ddl":False,"wrapper_replacement":False}
