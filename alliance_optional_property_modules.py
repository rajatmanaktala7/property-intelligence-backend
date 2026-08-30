"""Fail-safe optional property module registration."""

VERSION = "1.3.0-OPTIONAL-PROPERTY-MODULES"


def _route_exists(app, path):
    try:
        return any(
            getattr(route, "path", None) == path
            for route in app.router.routes
        )
    except Exception:
        return False


def register(wrapped):

    core = wrapped.core
    app = wrapped.app

    result = {
        "version": VERSION,
        "property_brain": {
            "status": "NOT_RUN",
            "error": None
        },
        "property_enrichment": {
            "status": "NOT_RUN",
            "error": None
        },
        "matcher_v61": {
            "status": "NOT_RUN",
            "error": None
        },
        "dashboard_cleanliness": {
            "status": "NOT_RUN",
            "error": None
        },
        "fail_safe": True
    }

    try:

        if _route_exists(
            app,
            "/property-brain/status"
        ):
            result["property_brain"]["status"] = (
                "ALREADY_REGISTERED"
            )

        else:
            import alliance_property_brain_v1 as module
            module.register(core)

            result["property_brain"]["status"] = (
                "REGISTERED"
            )

    except Exception as exc:
        result["property_brain"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}"
        }


    try:

        if _route_exists(
            app,
            "/property-brain/enrichment/batch/{limit}"
        ):
            result["property_enrichment"]["status"] = (
                "ALREADY_REGISTERED"
            )

        else:
            import alliance_property_enrichment_v1 as module
            module.register(core)

            result["property_enrichment"]["status"] = (
                "REGISTERED"
            )

    except Exception as exc:
        result["property_enrichment"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}"
        }


    try:
        import alliance_matcher_preferences_v61 as matcher

        matcher_result = matcher.register(core)

        result["matcher_v61"] = {
            "status": matcher_result.get(
                "status",
                "REGISTERED"
            ),
            "version": matcher_result.get("version"),
            "error": None
        }

    except Exception as exc:
        result["matcher_v61"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}"
        }


    try:
        import alliance_dashboard_cleanliness_v1 as clean

        result["dashboard_cleanliness"] = (
            clean.register(wrapped)
        )

    except Exception as exc:
        result["dashboard_cleanliness"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}"
        }


    return result
