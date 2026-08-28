VERSION = "1.1.0-PROPERTY-ENRICHMENT-ROUTE-FIX"

def register(core):
    from property_brain.enrichment_api import router, configure

    app = core.app
    configure(core.engine)

    if not getattr(app.state, "alliance_property_enrichment_v1_registered", False):
        app.include_router(router)
        app.state.alliance_property_enrichment_v1_registered = True

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "startup_ddl": False,
        "wrapper_replacement": False,
        "route_prefix": "/property-brain/enrichment",
    }
