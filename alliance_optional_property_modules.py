"""Fail-safe optional property module registration."""

VERSION = "1.15.0-OPTIONAL-PROPERTY-MODULES-BOUNDARY-INTELLIGENCE-V25"


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
            "error": None,
        },

        "property_enrichment": {
            "status": "NOT_RUN",
            "error": None,
        },

        "matcher_v61": {
            "status": "NOT_RUN",
            "error": None,
        },

        "dashboard_cleanliness": {
            "status": "NOT_RUN",
            "error": None,
        },

        "commercial_intelligence": {
            "status": "NOT_RUN",
            "error": None,
        },

        "commercial_intelligence_automation": {
            "status": "NOT_RUN",
            "error": None,
        },

        "v7_foundation": {
            "status": "NOT_RUN",
            "error": None,
        },

        "property_ai": {
            "status": "NOT_RUN",
            "error": None,
        },

        "context_rescue": {
            "status": "NOT_RUN",
            "error": None,
        },

        "bundle_reconstructor": {
            "status": "NOT_RUN",
            "error": None,
        },

        "fail_safe": True,
    }

    try:

        if _route_exists(
            app,
            "/property-brain/status",
        ):

            result[
                "property_brain"
            ][
                "status"
            ] = "ALREADY_REGISTERED"

        else:

            import alliance_property_brain_v1 as module

            module.register(core)

            result[
                "property_brain"
            ][
                "status"
            ] = "REGISTERED"

    except Exception as exc:

        result[
            "property_brain"
        ] = {
            "status": "ERROR",
            "error":
                f"{type(exc).__name__}: {exc}",
        }

    try:

        if _route_exists(
            app,
            "/property-brain/enrichment/batch/{limit}",
        ):

            result[
                "property_enrichment"
            ][
                "status"
            ] = "ALREADY_REGISTERED"

        else:

            import alliance_property_enrichment_v1 as module

            module.register(core)

            result[
                "property_enrichment"
            ][
                "status"
            ] = "REGISTERED"

    except Exception as exc:

        result[
            "property_enrichment"
        ] = {
            "status": "ERROR",
            "error":
                f"{type(exc).__name__}: {exc}",
        }

    try:

        import alliance_matcher_preferences_v61 as matcher

        matcher_result = (
            matcher.register(core)
        )

        result[
            "matcher_v61"
        ] = {
            "status":
                matcher_result.get(
                    "status",
                    "REGISTERED",
                ),

            "version":
                matcher_result.get(
                    "version"
                ),

            "error":
                None,
        }

    except Exception as exc:

        result[
            "matcher_v61"
        ] = {
            "status":
                "ERROR",

            "error":
                f"{type(exc).__name__}: {exc}",
        }

    try:

        import alliance_dashboard_cleanliness_v1 as clean

        result[
            "dashboard_cleanliness"
        ] = clean.register(
            wrapped
        )

    except Exception as exc:

        result[
            "dashboard_cleanliness"
        ] = {
            "status":
                "ERROR",

            "error":
                f"{type(exc).__name__}: {exc}",
        }

    try:

        if _route_exists(
            app,
            "/commercial-intelligence",
        ):

            result[
                "commercial_intelligence"
            ] = {
                "status":
                    "ALREADY_REGISTERED",

                "route":
                    "/commercial-intelligence",

                "error":
                    None,
            }

        else:

            import alliance_commercial_intelligence_ai as commercial

            commercial_result = (
                commercial.register(core)
            )

            result[
                "commercial_intelligence"
            ] = {
                **commercial_result,
                "error": None,
            }

    except Exception as exc:

        result[
            "commercial_intelligence"
        ] = {
            "status":
                "ERROR",

            "error":
                f"{type(exc).__name__}: {exc}",

            "route":
                "/commercial-intelligence",

            "fail_safe":
                True,
        }

    try:

        if _route_exists(
            app,
            "/api/commercial-intelligence/automation-status",
        ):

            result[
                "commercial_intelligence_automation"
            ] = {
                "status":
                    "ALREADY_REGISTERED",

                "route":
                    "/api/commercial-intelligence/automation-status",

                "error":
                    None,
            }

        else:

            import alliance_commercial_intelligence_scheduler as scheduler

            scheduler_result = (
                scheduler.register(core)
            )

            result[
                "commercial_intelligence_automation"
            ] = {
                **scheduler_result,
                "error": None,
            }

    except Exception as exc:

        result[
            "commercial_intelligence_automation"
        ] = {
            "status":
                "ERROR",

            "error":
                f"{type(exc).__name__}: {exc}",

            "route":
                "/api/commercial-intelligence/automation-status",

            "fail_safe":
                True,
        }

    try:

        if _route_exists(
            app,
            "/v7/foundation",
        ):

            result[
                "v7_foundation"
            ] = {
                "status":
                    "ALREADY_REGISTERED",

                "route":
                    "/v7/foundation",

                "error":
                    None,
            }

        else:

            import alliance_v7_foundation as v7

            v7_result = (
                v7.register(core)
            )

            result[
                "v7_foundation"
            ] = {
                **v7_result,
                "error": None,
            }

    except Exception as exc:

        result[
            "v7_foundation"
        ] = {
            "status":
                "ERROR",

            "error":
                f"{type(exc).__name__}: {exc}",

            "route":
                "/v7/foundation",

            "fail_safe":
                True,
        }

    try:

        if _route_exists(
            app,
            "/api/v7/property-ai/status",
        ):

            result[
                "property_ai"
            ] = {
                "status":
                    "ALREADY_REGISTERED",

                "route":
                    "/api/v7/property-ai/status",

                "error":
                    None,
            }

        else:

            import alliance_property_ai_v1 as property_ai

            property_ai_result = (
                property_ai.register(core)
            )

            result[
                "property_ai"
            ] = {
                **property_ai_result,
                "error": None,
            }

    except Exception as exc:

        result[
            "property_ai"
        ] = {
            "status":
                "ERROR",

            "error":
                f"{type(exc).__name__}: {exc}",

            "route":
                "/api/v7/property-ai/status",

            "fail_safe":
                True,
        }

    try:

        if _route_exists(
            app,
            "/api/v7/property-ai/context-rescue/status",
        ):

            result[
                "context_rescue"
            ] = {
                "status":
                    "ALREADY_REGISTERED",

                "route":
                    "/api/v7/property-ai/context-rescue/status",

                "error":
                    None,
            }

        else:

            import alliance_property_context_rescue_v22 as context_rescue

            context_result = (
                context_rescue.register(core)
            )

            result[
                "context_rescue"
            ] = {
                **context_result,
                "error": None,
            }

    except Exception as exc:

        result[
            "context_rescue"
        ] = {
            "status":
                "ERROR",

            "error":
                f"{type(exc).__name__}: {exc}",

            "route":
                "/api/v7/property-ai/context-rescue/status",

            "fail_safe":
                True,
        }


    try:
        if _route_exists(app, "/api/v7/property-ai/bundle-reconstructor/status"):
            result["bundle_reconstructor"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/bundle-reconstructor/status",
                "error": None,
            }
        else:
            import alliance_property_bundle_reconstructor_v23 as bundle
            bundle_result = bundle.register(core)
            result["bundle_reconstructor"] = {**bundle_result, "error": None}
    except Exception as exc:
        result["bundle_reconstructor"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/bundle-reconstructor/status",
            "fail_safe": True,
        }


    try:
        if _route_exists(
            app,
            "/api/v7/property-ai/shadow-extraction/status",
        ):
            result["shadow_extraction_v24"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/shadow-extraction/status",
                "error": None,
            }
        else:
            import alliance_property_shadow_extraction_v24 as shadow_v24
            shadow_result = shadow_v24.register(core)
            result["shadow_extraction_v24"] = {
                **shadow_result,
                "error": None,
            }
    except Exception as exc:
        result["shadow_extraction_v24"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/shadow-extraction/status",
            "fail_safe": True,
        }

    # PHASE 2.4.5 - deterministic context recovery shadow module
    try:
        if _route_exists(
            app,
            "/api/v7/property-ai/context-recovery-v245/status",
        ):
            result["context_recovery_v245"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/context-recovery-v245/status",
                "error": None,
            }
        else:
            import alliance_property_context_recovery_v245 as context_v245
            context_v245_result = context_v245.register(core)
            result["context_recovery_v245"] = {
                **context_v245_result,
                "error": None,
            }
    except Exception as exc:
        result["context_recovery_v245"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/context-recovery-v245/status",
            "fail_safe": True,
        }

    # PHASE 2.4.5A - deterministic benchmark stabilizer
    try:
        if _route_exists(
            app,
            "/api/v7/property-ai/benchmark-stabilizer-v245a/status",
        ):
            result["benchmark_stabilizer_v245a"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/benchmark-stabilizer-v245a/status",
                "error": None,
            }
        else:
            import alliance_property_benchmark_stabilizer_v245a as stabilizer_v245a
            stabilizer_result = stabilizer_v245a.register(core)
            result["benchmark_stabilizer_v245a"] = {
                **stabilizer_result,
                "error": None,
            }
    except Exception as exc:
        result["benchmark_stabilizer_v245a"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/benchmark-stabilizer-v245a/status",
            "fail_safe": True,
        }

    # PHASE 2.4.6 - deterministic shared-context intelligence
    try:
        if _route_exists(
            app,
            "/api/v7/property-ai/context-intelligence-v246/status",
        ):
            result["context_intelligence_v246"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/context-intelligence-v246/status",
                "error": None,
            }
        else:
            import alliance_property_context_intelligence_v246 as context_v246
            context_result = context_v246.register(core)
            result["context_intelligence_v246"] = {
                **context_result,
                "error": None,
            }
    except Exception as exc:
        result["context_intelligence_v246"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/context-intelligence-v246/status",
            "fail_safe": True,
        }

    # PHASE 2.5 - property boundary + own-text intelligence
    try:
        if _route_exists(
            app,
            "/api/v7/property-ai/boundary-intelligence-v25/status",
        ):
            result["boundary_intelligence_v25"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/boundary-intelligence-v25/status",
                "error": None,
            }
        else:
            import alliance_property_boundary_intelligence_v25 as boundary_v25
            boundary_result = boundary_v25.register(core)
            result["boundary_intelligence_v25"] = {
                **boundary_result,
                "error": None,
            }
    except Exception as exc:
        result["boundary_intelligence_v25"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/boundary-intelligence-v25/status",
            "fail_safe": True,
        }
    return result



