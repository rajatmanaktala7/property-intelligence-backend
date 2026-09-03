"""Fail-safe optional property module registration."""

VERSION = "2.0.0-OPTIONAL-PROPERTY-MODULES-PROPERTY-BRAIN-FOUNDATION"


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

    # PHASE 2.5.1 - boundary cohesion fix
    try:
        if _route_exists(
            app,
            "/api/v7/property-ai/boundary-cohesion-v251/status",
        ):
            result["boundary_cohesion_v251"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/boundary-cohesion-v251/status",
                "error": None,
            }
        else:
            import alliance_property_boundary_cohesion_v251 as boundary_v251
            boundary_result = boundary_v251.register(core)
            result["boundary_cohesion_v251"] = {
                **boundary_result,
                "error": None,
            }
    except Exception as exc:
        result["boundary_cohesion_v251"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/boundary-cohesion-v251/status",
            "fail_safe": True,
        }

    # PHASE 2.5.2 - deterministic locality + classification recovery
    try:
        if _route_exists(
            app,
            "/api/v7/property-ai/locality-classification-v252/status",
        ):
            result["locality_classification_v252"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/locality-classification-v252/status",
                "error": None,
            }
        else:
            import alliance_property_locality_classification_v252 as locality_v252
            locality_result = locality_v252.register(core)
            result["locality_classification_v252"] = {
                **locality_result,
                "error": None,
            }
    except Exception as exc:
        result["locality_classification_v252"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/locality-classification-v252/status",
            "fail_safe": True,
        }

    # PHASE 2.5.3 - deterministic location evidence resolver
    try:
        if _route_exists(
            app,
            "/api/v7/property-ai/location-evidence-v253/status",
        ):
            result["location_evidence_v253"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/location-evidence-v253/status",
                "error": None,
            }
        else:
            import alliance_property_location_evidence_v253 as location_v253
            location_result = location_v253.register(core)
            result["location_evidence_v253"] = {
                **location_result,
                "error": None,
            }
    except Exception as exc:
        result["location_evidence_v253"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/location-evidence-v253/status",
            "fail_safe": True,
        }

    # PHASE 2.5.3A - unresolved location diagnostic
    try:
        if _route_exists(
            app,
            "/api/v7/property-ai/location-diagnostic-v253a/status",
        ):
            result["location_diagnostic_v253a"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/location-diagnostic-v253a/status",
                "error": None,
            }
        else:
            import alliance_property_location_diagnostic_v253a as location_diag_v253a
            diag_result = location_diag_v253a.register(core)
            result["location_diagnostic_v253a"] = {
                **diag_result,
                "error": None,
            }
    except Exception as exc:
        result["location_diagnostic_v253a"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/location-diagnostic-v253a/status",
            "fail_safe": True,
        }

    # PHASE 2.5.4 - property record cohesion v2
    try:
        if _route_exists(
            app,
            "/api/v7/property-ai/record-cohesion-v254/status",
        ):
            result["record_cohesion_v254"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/record-cohesion-v254/status",
                "error": None,
            }
        else:
            import alliance_property_record_cohesion_v254 as record_v254
            record_result = record_v254.register(core)
            result["record_cohesion_v254"] = {
                **record_result,
                "error": None,
            }
    except Exception as exc:
        result["record_cohesion_v254"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/record-cohesion-v254/status",
            "fail_safe": True,
        }

    # PHASE 2.5.4C - property header scope diagnostic
    try:
        if _route_exists(
            app,
            "/api/v7/property-ai/header-scope-v254c/status",
        ):
            result["header_scope_v254c"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/header-scope-v254c/status",
                "error": None,
            }
        else:
            import alliance_property_header_scope_v254c as header_scope_v254c
            header_scope_result = header_scope_v254c.register(core)
            result["header_scope_v254c"] = {
                **header_scope_result,
                "error": None,
            }
    except Exception as exc:
        result["header_scope_v254c"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/header-scope-v254c/status",
            "fail_safe": True,
        }

    # PHASE 2.5.5 - hierarchical property scope parser preview
    try:
        if _route_exists(
            app,
            "/api/v7/property-ai/hierarchical-parser-v255/status",
        ):
            result["hierarchical_parser_v255"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/hierarchical-parser-v255/status",
                "error": None,
            }
        else:
            import alliance_property_hierarchical_parser_v255 as hierarchical_parser_v255
            v255_result = hierarchical_parser_v255.register(core)
            result["hierarchical_parser_v255"] = {
                **v255_result,
                "error": None,
            }
    except Exception as exc:
        result["hierarchical_parser_v255"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/hierarchical-parser-v255/status",
            "fail_safe": True,
        }

    # PHASE 2.5.5B - deterministic parent location evidence applier
    try:
        if _route_exists(
            app,
            "/api/v7/property-ai/parent-location-v255b/status",
        ):
            result["parent_location_v255b"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/parent-location-v255b/status",
                "error": None,
            }
        else:
            import alliance_property_parent_location_v255b as parent_location_v255b
            v255b_result = parent_location_v255b.register(core)
            result["parent_location_v255b"] = {
                **v255b_result,
                "error": None,
            }
    except Exception as exc:
        result["parent_location_v255b"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/parent-location-v255b/status",
            "fail_safe": True,
        }

    # PHASE 2.5.5C - remaining location and boundary diagnostic
    try:
        if _route_exists(
            app,
            "/api/v7/property-ai/location-boundary-diagnostic-v255c/status",
        ):
            result["location_boundary_diagnostic_v255c"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/location-boundary-diagnostic-v255c/status",
                "error": None,
            }
        else:
            import alliance_property_location_boundary_diagnostic_v255c as location_boundary_diagnostic_v255c
            v255c_result = location_boundary_diagnostic_v255c.register(core)
            result["location_boundary_diagnostic_v255c"] = {
                **v255c_result,
                "error": None,
            }
    except Exception as exc:
        result["location_boundary_diagnostic_v255c"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/location-boundary-diagnostic-v255c/status",
            "fail_safe": True,
        }

    # PHASE 2.5.5D - project header inventory diagnostic
    try:
        if _route_exists(
            app,
            "/api/v7/property-ai/project-header-inventory-v255d/status",
        ):
            result["project_header_inventory_v255d"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/project-header-inventory-v255d/status",
                "error": None,
            }
        else:
            import alliance_property_project_header_inventory_v255d as project_header_inventory_v255d
            v255d_result = project_header_inventory_v255d.register(core)
            result["project_header_inventory_v255d"] = {
                **v255d_result,
                "error": None,
            }
    except Exception as exc:
        result["project_header_inventory_v255d"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/project-header-inventory-v255d/status",
            "fail_safe": True,
        }

    # PHASE 2.5.6A - evidence-backed project location resolver
    try:
        if _route_exists(
            app,
            "/api/v7/property-ai/project-location-v256a/status",
        ):
            result["project_location_v256a"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/project-location-v256a/status",
                "error": None,
            }
        else:
            import alliance_property_project_location_v256a as project_location_v256a
            v256a_result = project_location_v256a.register(core)
            result["project_location_v256a"] = {
                **v256a_result,
                "error": None,
            }
    except Exception as exc:
        result["project_location_v256a"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/project-location-v256a/status",
            "fail_safe": True,
        }

    # PHASE 2.5.6B - remaining record diagnostic
    try:
        if _route_exists(
            app,
            "/api/v7/property-ai/remaining-diagnostic-v256b/status",
        ):
            result["remaining_diagnostic_v256b"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/remaining-diagnostic-v256b/status",
                "error": None,
            }
        else:
            import alliance_property_remaining_diagnostic_v256b as remaining_v256b
            v256b_result = remaining_v256b.register(core)
            result["remaining_diagnostic_v256b"] = {
                **v256b_result,
                "error": None,
            }
    except Exception as exc:
        result["remaining_diagnostic_v256b"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/remaining-diagnostic-v256b/status",
            "fail_safe": True,
        }

    # PHASE 2.5.7A - property record integrity + classification shadow
    try:
        if _route_exists(
            app,
            "/api/v7/property-ai/record-integrity-v257a/status",
        ):
            result["record_integrity_v257a"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/record-integrity-v257a/status",
                "error": None,
            }
        else:
            import alliance_property_record_integrity_v257a as record_v257a
            v257a_result = record_v257a.register(core)
            result["record_integrity_v257a"] = {
                **v257a_result,
                "error": None,
            }
    except Exception as exc:
        result["record_integrity_v257a"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/record-integrity-v257a/status",
            "fail_safe": True,
        }

    # PHASE 2.5.7B - evidence grammar + intent direction fix
    try:
        if _route_exists(
            app,
            "/api/v7/property-ai/evidence-grammar-v257b/status",
        ):
            result["evidence_grammar_v257b"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/evidence-grammar-v257b/status",
                "error": None,
            }
        else:
            import alliance_property_evidence_grammar_v257b as evidence_v257b
            v257b_result = evidence_v257b.register(core)
            result["evidence_grammar_v257b"] = {
                **v257b_result,
                "error": None,
            }
    except Exception as exc:
        result["evidence_grammar_v257b"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/evidence-grammar-v257b/status",
            "fail_safe": True,
        }

    # PHASE 2.5.8A - property + offers + tutor foundation
    try:
        if _route_exists(
            app,
            "/api/v7/property-ai/property-offers-tutor-v258/status",
        ):
            result["property_offers_tutor_v258"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/property-offers-tutor-v258/status",
                "error": None,
            }
        else:
            import alliance_property_offers_tutor_v258 as offers_tutor_v258
            v258_result = offers_tutor_v258.register(core)
            result["property_offers_tutor_v258"] = {
                **v258_result,
                "error": None,
            }
    except Exception as exc:
        result["property_offers_tutor_v258"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/property-offers-tutor-v258/status",
            "fail_safe": True,
        }

    # PHASE 2.5.9A - controlled property + offer write gate
    try:
        if _route_exists(
            app,
            "/api/v7/property-ai/controlled-writer-v259/status",
        ):
            result["controlled_writer_v259"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/controlled-writer-v259/status",
                "error": None,
            }
        else:
            import alliance_property_controlled_writer_v259 as controlled_writer_v259
            v259_result = controlled_writer_v259.register(core)
            result["controlled_writer_v259"] = {
                **v259_result,
                "error": None,
            }
    except Exception as exc:
        result["controlled_writer_v259"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/controlled-writer-v259/status",
            "fail_safe": True,
        }

    # V260 Alliance AI Academy + V259B write safety
    try:
        if _route_exists(app, "/api/v7/property-ai/academy-v260/status"):
            result["academy_v260"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/academy-v260/status",
                "error": None,
            }
        else:
            import alliance_ai_academy_v260 as academy_v260
            result["academy_v260"] = {**academy_v260.register(core), "error": None}
    except Exception as exc:
        result["academy_v260"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/academy-v260/status",
            "fail_safe": True,
        }

    # V261 Topper Training Lab
    try:
        if _route_exists(app, "/api/v7/property-ai/topper-v261/status"):
            result["topper_v261"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/topper-v261/status",
                "error": None,
            }
        else:
            import alliance_topper_training_v261 as topper_v261
            result["topper_v261"] = {**topper_v261.register(core), "error": None}
    except Exception as exc:
        result["topper_v261"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/topper-v261/status",
            "fail_safe": True,
        }

    # V261B Real-World Topper Coach
    try:
        if _route_exists(app, "/api/v7/property-ai/topper-v261b/status"):
            result["topper_v261b"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/topper-v261b/status",
                "error": None,
            }
        else:
            import alliance_real_topper_coach_v261b as topper_v261b
            result["topper_v261b"] = {**topper_v261b.register(core), "error": None}
    except Exception as exc:
        result["topper_v261b"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/topper-v261b/status",
            "fail_safe": True,
        }

    # V262A Real Alliance Brain Correction
    try:
        if _route_exists(app, "/api/v7/property-ai/mastery-v262a/status"):
            result["mastery_v262a"] = {"status":"ALREADY_REGISTERED","route":"/api/v7/property-ai/mastery-v262a/status","error":None}
        else:
            import alliance_real_mastery_v262a as mastery_v262a
            result["mastery_v262a"] = {**mastery_v262a.register(core), "error":None}
    except Exception as exc:
        result["mastery_v262a"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","route":"/api/v7/property-ai/mastery-v262a/status","fail_safe":True}

    # V262B Boundary + Context Mastery
    try:
        if _route_exists(app, "/api/v7/property-ai/mastery-v262b/status"):
            result["mastery_v262b"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/v7/property-ai/mastery-v262b/status",
                "error": None,
            }
        else:
            import alliance_boundary_context_mastery_v262b as mastery_v262b
            result["mastery_v262b"] = {**mastery_v262b.register(core), "error": None}
    except Exception as exc:
        result["mastery_v262b"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/v7/property-ai/mastery-v262b/status",
            "fail_safe": True,
        }

    # V262C Excellence Relationship + Offer Mastery
    try:
        if _route_exists(app, "/api/v7/property-ai/mastery-v262c/status"):
            result["mastery_v262c"] = {"status":"ALREADY_REGISTERED","route":"/api/v7/property-ai/mastery-v262c/status","error":None}
        else:
            import alliance_excellence_mastery_v262c as mastery_v262c
            result["mastery_v262c"] = {**mastery_v262c.register(core), "error":None}
    except Exception as exc:
        result["mastery_v262c"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}",
                                   "route":"/api/v7/property-ai/mastery-v262c/status","fail_safe":True}

    # V262D Real Context Recovery Academy
    try:
        if _route_exists(app, "/api/v7/property-ai/mastery-v262d/status"):
            result["mastery_v262d"] = {"status":"ALREADY_REGISTERED","route":"/api/v7/property-ai/mastery-v262d/status","error":None}
        else:
            import alliance_context_recovery_academy_v262d as mastery_v262d
            result["mastery_v262d"] = {**mastery_v262d.register(core), "error":None}
    except Exception as exc:
        result["mastery_v262d"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}",
                                   "route":"/api/v7/property-ai/mastery-v262d/status","fail_safe":True}

    # V262E Semantic Context Mastery
    try:
        if _route_exists(app, "/api/v7/property-ai/mastery-v262e/status"):
            result["mastery_v262e"] = {"status":"ALREADY_REGISTERED","route":"/api/v7/property-ai/mastery-v262e/status","error":None}
        else:
            import alliance_semantic_context_mastery_v262e as mastery_v262e
            result["mastery_v262e"] = {**mastery_v262e.register(core), "error":None}
    except Exception as exc:
        result["mastery_v262e"] = {
            "status":"ERROR",
            "error":f"{type(exc).__name__}: {exc}",
            "route":"/api/v7/property-ai/mastery-v262e/status",
            "fail_safe":True
        }

    # V263 Real Data Sampler + Semantic Precision
    try:
        if _route_exists(app, "/api/v7/property-ai/mastery-v263/status"):
            result["mastery_v263"] = {"status":"ALREADY_REGISTERED","route":"/api/v7/property-ai/mastery-v263/status","error":None}
        else:
            import alliance_real_data_sampler_v263 as mastery_v263
            result["mastery_v263"] = {**mastery_v263.register(core), "error":None}
    except Exception as exc:
        result["mastery_v263"] = {
            "status":"ERROR",
            "error":f"{type(exc).__name__}: {exc}",
            "route":"/api/v7/property-ai/mastery-v263/status",
            "fail_safe":True
        }

    # V263A Schema-Aware Real Sampler
    try:
        if _route_exists(app, "/api/v7/property-ai/mastery-v263a/status"):
            result["mastery_v263a"] = {"status":"ALREADY_REGISTERED","route":"/api/v7/property-ai/mastery-v263a/status","error":None}
        else:
            import alliance_real_data_sampler_v263a as mastery_v263a
            result["mastery_v263a"] = {**mastery_v263a.register(core), "error":None}
    except Exception as exc:
        result["mastery_v263a"] = {
            "status":"ERROR",
            "error":f"{type(exc).__name__}: {exc}",
            "route":"/api/v7/property-ai/mastery-v263a/status",
            "fail_safe":True
        }

    # Alliance Property Brain Foundation 1.0
    # Evidence Span Engine + Human Gold Lab + Evaluation Dashboard.
    # Academy-table writes only. Production property/offer/matcher/WhatsApp writes remain blocked.
    try:
        if _route_exists(app, "/api/property-brain-foundation/status"):
            result["property_brain_foundation_v1"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/api/property-brain-foundation/status",
                "error": None,
            }
        else:
            import alliance_property_brain_foundation_v1 as property_brain_foundation_v1
            foundation_result = property_brain_foundation_v1.register(core)
            result["property_brain_foundation_v1"] = {
                **foundation_result,
                "error": None,
            }
    except Exception as exc:
        result["property_brain_foundation_v1"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/api/property-brain-foundation/status",
            "fail_safe": True,
        }

    # FOUNDATION 2.0 - Gold V1 freeze + tutor training + benchmark
    try:
        if _route_exists(app, "/api/property-brain/gold-v2/status"):
            result["gold_v2_training"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/property-brain/gold-v2",
                "error": None,
            }
        else:
            import alliance_property_brain_gold_v2 as gold_v2
            result["gold_v2_training"] = {**gold_v2.register(core), "error": None}
    except Exception as exc:
        result["gold_v2_training"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/property-brain/gold-v2",
            "fail_safe": True,
        }


    # FOUNDATION 2.1 - Gold benchmark intelligence repair
    try:
        if _route_exists(app, "/api/property-brain/gold-v21/status"):
            result["gold_v21_benchmark_repair"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/property-brain/gold-v21",
                "error": None,
            }
        else:
            import alliance_property_brain_gold_v21 as gold_v21
            result["gold_v21_benchmark_repair"] = {
                **gold_v21.register(core),
                "error": None,
            }
    except Exception as exc:
        result["gold_v21_benchmark_repair"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/property-brain/gold-v21",
            "fail_safe": True,
        }



    # FOUNDATION 2.2 - World Topper Tutor + WhatsApp Live shadow infrastructure
    try:
        if _route_exists(app, "/api/property-brain/topper-v22/status"):
            result["world_topper_tutor_v22"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/property-brain/topper-v22",
                "error": None,
            }
        else:
            import alliance_world_topper_tutor_v22 as topper_v22
            result["world_topper_tutor_v22"] = {
                **topper_v22.register(core),
                "error": None,
            }
    except Exception as exc:
        result["world_topper_tutor_v22"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/property-brain/topper-v22",
            "fail_safe": True,
        }



    # FOUNDATION 2.3 - Deep Availability Intelligence Extractor
    try:
        if _route_exists(app, "/api/property-brain/deep-v23/status"):
            result["deep_availability_v23"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/property-brain/deep-v23",
                "error": None,
            }
        else:
            import alliance_deep_availability_v23 as deep_v23
            result["deep_availability_v23"] = {
                **deep_v23.register(core),
                "error": None,
            }
    except Exception as exc:
        result["deep_availability_v23"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/property-brain/deep-v23",
            "fail_safe": True,
        }



    # FOUNDATION 2.4 - Evidence First Maximum Extraction
    try:
        if _route_exists(app, "/api/property-brain/evidence-v24/status"):
            result["evidence_first_v24"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/property-brain/evidence-v24",
                "error": None,
            }
        else:
            import alliance_evidence_first_v24 as evidence_v24
            result["evidence_first_v24"] = {
                **evidence_v24.register(core),
                "error": None,
            }
    except Exception as exc:
        result["evidence_first_v24"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/property-brain/evidence-v24",
            "fail_safe": True,
        }



    # FOUNDATION 2.5 - Context Ownership + Active Learning
    try:
        if _route_exists(app, "/api/property-brain/context-v25/status"):
            result["context_ownership_v25"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/property-brain/context-v25",
                "error": None,
            }
        else:
            import alliance_context_ownership_v25 as context_v25
            result["context_ownership_v25"] = {
                **context_v25.register(core),
                "error": None,
            }
    except Exception as exc:
        result["context_ownership_v25"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/property-brain/context-v25",
            "fail_safe": True,
        }


    # FOUNDATION 2.6 - Magic Source Truth Examiner
    try:
        if _route_exists(app, "/api/property-brain/magic-v26/status"):
            result["magic_examiner_v26"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/magic-v26","error":None}
        else:
            import alliance_magic_examiner_v26 as magic_v26
            result["magic_examiner_v26"] = {**magic_v26.register(core),"error":None}
    except Exception as exc:
        result["magic_examiner_v26"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","route":"/property-brain/magic-v26","fail_safe":True}

    # FOUNDATION 2.7 - World Topper Magic Academy
    try:
        if _route_exists(app, "/api/property-brain/academy-v27/status"):
            result["world_topper_academy_v27"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/academy-v27","error":None}
        else:
            import alliance_world_topper_academy_v27 as academy_v27
            result["world_topper_academy_v27"] = {**academy_v27.register(core),"error":None}
    except Exception as exc:
        result["world_topper_academy_v27"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","route":"/property-brain/academy-v27","fail_safe":True}

    # FOUNDATION 2.8 - Intensive Tutor Weak Subject Repair
    try:
        if _route_exists(app, "/api/property-brain/tutor-v28/status"):
            result["intensive_tutor_v28"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/tutor-v28","error":None}
        else:
            import alliance_intensive_tutor_v28 as tutor_v28
            result["intensive_tutor_v28"] = {**tutor_v28.register(core),"error":None}
    except Exception as exc:
        result["intensive_tutor_v28"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","route":"/property-brain/tutor-v28","fail_safe":True}

    # FOUNDATION 2.9 - Infrastructure First Geography + Transaction/Occupancy
    try:
        if _route_exists(app, "/api/property-brain/infrastructure-v29/status"):
            result["infrastructure_v29"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/infrastructure-v29","error":None}
        else:
            import alliance_infrastructure_first_v29 as infrastructure_v29
            result["infrastructure_v29"] = {**infrastructure_v29.register(core),"error":None}
    except Exception as exc:
        result["infrastructure_v29"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}",
                                        "route":"/property-brain/infrastructure-v29","fail_safe":True}

    # FOUNDATION 2.9.1 - Revised Geography + Transaction Curriculum
    try:
        if _route_exists(app, "/api/property-brain/infrastructure-v291/status"):
            result["infrastructure_v291"] = {
                "status":"ALREADY_REGISTERED",
                "route":"/property-brain/infrastructure-v291",
                "error":None,
            }
        else:
            import alliance_infrastructure_curriculum_v291 as infrastructure_v291
            result["infrastructure_v291"] = {
                **infrastructure_v291.register(core),
                "error":None,
            }
    except Exception as exc:
        result["infrastructure_v291"] = {
            "status":"ERROR",
            "error":f"{type(exc).__name__}: {exc}",
            "route":"/property-brain/infrastructure-v291",
            "fail_safe":True,
        }

    # FOUNDATION 2.9.2 - Ownership Wiring & Structural Resolution
    try:
        if _route_exists(app, "/api/property-brain/ownership-v292/status"):
            result["ownership_v292"] = {
                "status":"ALREADY_REGISTERED",
                "route":"/property-brain/ownership-v292",
                "error":None,
            }
        else:
            import alliance_ownership_structural_v292 as ownership_v292
            result["ownership_v292"] = {
                **ownership_v292.register(core),
                "error":None,
            }
    except Exception as exc:
        result["ownership_v292"] = {
            "status":"ERROR",
            "error":f"{type(exc).__name__}: {exc}",
            "route":"/property-brain/ownership-v292",
            "fail_safe":True,
        }

    # FOUNDATION 2.9.2.1 - Structural Integrity Repair
    try:
        if _route_exists(app, "/api/property-brain/integrity-v2921/status"):
            result["integrity_v2921"] = {
                "status":"ALREADY_REGISTERED",
                "route":"/property-brain/integrity-v2921",
                "error":None,
            }
        else:
            import alliance_structural_integrity_v2921 as integrity_v2921
            result["integrity_v2921"] = {
                **integrity_v2921.register(core),
                "error":None,
            }
    except Exception as exc:
        result["integrity_v2921"] = {
            "status":"ERROR",
            "error":f"{type(exc).__name__}: {exc}",
            "route":"/property-brain/integrity-v2921",
            "fail_safe":True,
        }

    # FOUNDATION 2.9.2.2 - Final Deterministic Examiner
    try:
        if _route_exists(app, "/api/property-brain/final-exam-v2922/status"):
            result["final_exam_v2922"] = {
                "status":"ALREADY_REGISTERED",
                "route":"/property-brain/final-exam-v2922",
                "error":None,
            }
        else:
            import alliance_final_exam_v2922 as final_exam_v2922
            result["final_exam_v2922"] = {
                **final_exam_v2922.register(core),
                "error":None,
            }
    except Exception as exc:
        result["final_exam_v2922"] = {
            "status":"ERROR",
            "error":f"{type(exc).__name__}: {exc}",
            "route":"/property-brain/final-exam-v2922",
            "fail_safe":True,
        }

    # FOUNDATION 2.9.3 - Gold V2 Structural Lab
    try:
        if _route_exists(app, "/api/property-brain/gold-v2-structural/status"):
            result["gold_v2_structural_lab"] = {
                "status":"ALREADY_REGISTERED",
                "route":"/property-brain/gold-v2-structural",
                "error":None,
            }
        else:
            import alliance_gold_v2_structural_lab_v293 as gold_v2_structural
            result["gold_v2_structural_lab"] = {
                **gold_v2_structural.register(core),
                "error":None,
            }
    except Exception as exc:
        result["gold_v2_structural_lab"] = {
            "status":"ERROR",
            "error":f"{type(exc).__name__}: {exc}",
            "route":"/property-brain/gold-v2-structural",
            "fail_safe":True,
        }


    # FOUNDATION 2.9.4 - Autonomous Gold Teacher
    # Uses immutable Human Gold as seed supervision. Auto predictions stay in shadow/academy tables.
    # Only low-confidence exceptions remain for human review. No production or WhatsApp writes.
    try:
        if _route_exists(app, "/api/property-brain/auto-teacher-v294/status"):
            result["auto_teacher_v294"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/property-brain/auto-teacher-v294",
                "error": None,
            }
        else:
            import alliance_autonomous_gold_teacher_v294 as auto_teacher_v294
            result["auto_teacher_v294"] = {
                **auto_teacher_v294.register(core),
                "error": None,
            }
    except Exception as exc:
        result["auto_teacher_v294"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/property-brain/auto-teacher-v294",
            "fail_safe": True,
        }



    # FOUNDATION 3.0 - Autonomous Expertise Loop
    try:
        if _route_exists(app, "/api/property-brain/expertise-v300/status"):
            result["expertise_v300"] = {
                "status":"ALREADY_REGISTERED",
                "route":"/property-brain/expertise-v300",
                "error":None,
            }
        else:
            import alliance_property_brain_expertise_v300 as expertise_v300
            result["expertise_v300"] = {
                **expertise_v300.register(core),
                "error":None,
            }
    except Exception as exc:
        result["expertise_v300"] = {
            "status":"ERROR",
            "error":f"{type(exc).__name__}: {exc}",
            "route":"/property-brain/expertise-v300",
            "fail_safe":True,
        }



    # FOUNDATION 3.1 - Autonomous Expertise Bootcamp
    try:
        if _route_exists(app, "/api/property-brain/expertise-v310/status"):
            result["expertise_v310"] = {
                "status":"ALREADY_REGISTERED",
                "route":"/property-brain/expertise-v310",
                "error":None,
            }
        else:
            import alliance_expertise_bootcamp_v310 as expertise_v310
            result["expertise_v310"] = {
                **expertise_v310.register(core),
                "error":None,
            }
    except Exception as exc:
        result["expertise_v310"] = {
            "status":"ERROR",
            "error":f"{type(exc).__name__}: {exc}",
            "route":"/property-brain/expertise-v310",
            "fail_safe":True,
        }



    # FOUNDATION 3.2 - Mastery Repair + Blind Holdout Gate
    try:
        if _route_exists(app, "/api/property-brain/mastery-v320/status"):
            result["mastery_v320"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/mastery-v320","error":None}
        else:
            import alliance_mastery_repair_v320 as mastery_v320
            result["mastery_v320"] = {**mastery_v320.register(core),"error":None}
    except Exception as exc:
        result["mastery_v320"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}",
                                  "route":"/property-brain/mastery-v320","fail_safe":True}



    # FOUNDATION 3.3 - Ownership Mastery + Frozen Blind Set
    try:
        if _route_exists(app, "/api/property-brain/mastery-v330/status"):
            result["mastery_v330"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/mastery-v330","error":None}
        else:
            import alliance_ownership_mastery_blind_v330 as mastery_v330
            result["mastery_v330"] = {**mastery_v330.register(core),"error":None}
    except Exception as exc:
        result["mastery_v330"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}",
                                  "route":"/property-brain/mastery-v330","fail_safe":True}



    # FOUNDATION 3.4 - Mastery Finalizer + Minimal Blind Audit
    try:
        if _route_exists(app, "/api/property-brain/mastery-v340/status"):
            result["mastery_v340"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/mastery-v340","error":None}
        else:
            import alliance_mastery_finalizer_v340 as mastery_v340
            result["mastery_v340"] = {**mastery_v340.register(core),"error":None}
    except Exception as exc:
        result["mastery_v340"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}",
                                  "route":"/property-brain/mastery-v340","fail_safe":True}



    # FOUNDATION 3.5 - Training Gate Finalizer
    try:
        if _route_exists(app, "/api/property-brain/mastery-v350/status"):
            result["mastery_v350"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/mastery-v350","error":None}
        else:
            import alliance_training_gate_finalizer_v350 as mastery_v350
            result["mastery_v350"] = {**mastery_v350.register(core),"error":None}
    except Exception as exc:
        result["mastery_v350"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}",
                                  "route":"/property-brain/mastery-v350","fail_safe":True}



    # FOUNDATION 3.6 - blind failure learning + new frozen unseen exam
    try:
        if _route_exists(app, "/api/property-brain/mastery-v360/status"):
            result["mastery_v360"] = {
                "status": "ALREADY_REGISTERED",
                "route": "/property-brain/mastery-v360",
                "error": None,
            }
        else:
            import alliance_blind_failure_learning_v360 as mastery_v360
            mastery_result = mastery_v360.register(core)
            result["mastery_v360"] = {**mastery_result, "error": None}
    except Exception as exc:
        result["mastery_v360"] = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "route": "/property-brain/mastery-v360",
            "fail_safe": True,
        }


    # FOUNDATION 3.7 - Autonomous Property Brain Teacher
    # Routine message labeling is automated. Human review is exception-only.
    # No production/WhatsApp/Gold mutations.
    try:
        if _route_exists(app, "/api/property-brain/autonomous-v370/status"):
            result["autonomous_v370"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/autonomous-v370","error":None}
        else:
            import alliance_autonomous_property_brain_v370 as autonomous_v370
            result["autonomous_v370"] = {**autonomous_v370.register(core), "error": None}
    except Exception as exc:
        result["autonomous_v370"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","route":"/property-brain/autonomous-v370","fail_safe":True}


    # FOUNDATION 3.8 - Failure-Driven Mastery + Automated Routine Training
    # Learns only from already-frozen V2 human truth, suppresses duplicate source messages,
    # repairs class/transaction semantics, and freezes a NEW V3 certification set.
    # Routine labeling is automated. V3 remains independent certification only.
    # No production/WhatsApp/Gold mutations.
    try:
        if _route_exists(app, "/api/property-brain/mastery-v380/status"):
            result["mastery_v380"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/mastery-v380","error":None}
        else:
            import alliance_failure_driven_mastery_v380 as mastery_v380
            result["mastery_v380"] = {**mastery_v380.register(core), "error": None}
    except Exception as exc:
        result["mastery_v380"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","route":"/property-brain/mastery-v380","fail_safe":True}


    # FOUNDATION 4.0 - Alliance CRE Academy
    # Additive only. Foundation 3.8 and all earlier modules remain untouched.
    # V3 is retired unlabeled for 4.0 certification; a fresh V4 is required after pre-cert PASS.
    try:
        if _route_exists(app, "/api/property-brain/academy-v400/status"):
            result["academy_v400"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/academy-v400","error":None}
        else:
            import alliance_cre_academy_v400 as academy_v400
            result["academy_v400"] = {**academy_v400.register(core), "error": None}
    except Exception as exc:
        result["academy_v400"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","route":"/property-brain/academy-v400","fail_safe":True}


    # FOUNDATION 4.0.1 - Alliance CRE Academy Mastery Repair
    # Additive repair only. Foundation 4.0, 3.8 and all earlier fixes remain untouched.
    try:
        if _route_exists(app, "/api/property-brain/academy-v401/status"):
            result["academy_v401"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/academy-v401","error":None}
        else:
            import alliance_cre_academy_v401 as academy_v401
            result["academy_v401"] = {**academy_v401.register(core), "error": None}
    except Exception as exc:
        result["academy_v401"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","route":"/property-brain/academy-v401","fail_safe":True}


    # FOUNDATION 4.0.2 - Alliance CRE Academy Pre-Cert Finalizer
    # Additive only. Preserves 4.0.1, 4.0, 3.8 and every earlier registered foundation.
    try:
        if _route_exists(app, "/api/property-brain/academy-v402/status"):
            result["academy_v402"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/academy-v402","error":None}
        else:
            import alliance_cre_academy_v402 as academy_v402
            result["academy_v402"] = {**academy_v402.register(core), "error": None}
    except Exception as exc:
        result["academy_v402"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","route":"/property-brain/academy-v402","fail_safe":True}


    # FOUNDATION 4.1 - Alliance CRE Championship Blind V4
    # Certification only. No predictor tuning. Preserves 4.0.2 and all earlier foundations.
    try:
        if _route_exists(app, "/api/property-brain/championship-v410/status"):
            result["championship_v410"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/championship-v410","error":None}
        else:
            import alliance_cre_championship_v410 as championship_v410
            result["championship_v410"] = {**championship_v410.register(core), "error": None}
    except Exception as exc:
        result["championship_v410"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","route":"/property-brain/championship-v410","fail_safe":True}


    # FOUNDATION 4.2 - Alliance Automation Machine
    # Automation-first. V4 predictions remain frozen. Human work only for irreducible exceptions.
    try:
        if _route_exists(app, "/api/property-brain/automation-v420/status"):
            result["automation_v420"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/automation-v420","error":None}
        else:
            import alliance_automation_machine_v420 as automation_v420
            result["automation_v420"] = {**automation_v420.register(core), "error": None}
    except Exception as exc:
        result["automation_v420"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","route":"/property-brain/automation-v420","fail_safe":True}

    # V19.0 single stable property operations owner
    try:
        import alliance_property_operations_v190 as property_ops_v190
        result["property_operations_v190"] = {**property_ops_v190.register(wrapped), "error": None}
    except Exception as exc:
        result["property_operations_v190"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}


    # FOUNDATION 4.2.1 - Alliance Automation Truth Escalator
    # Additive. Student/V4 predictions remain frozen. Human work only after field-wise multi-judge abstention.
    try:
        if _route_exists(app, "/api/property-brain/automation-v421/status"):
            result["automation_v421"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/automation-v421","error":None}
        else:
            import alliance_automation_truth_escalator_v421 as automation_v421
            result["automation_v421"] = {**automation_v421.register(core), "error": None}
    except Exception as exc:
        result["automation_v421"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","route":"/property-brain/automation-v421","fail_safe":True}


    # FOUNDATION 4.2.2 - Alliance Automation Closure
    # Additive semantic closure. Frozen V4 student is never modified.
    try:
        if _route_exists(app, "/api/property-brain/automation-v422/status"):
            result["automation_v422"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/automation-v422","error":None}
        else:
            import alliance_automation_closure_v422 as automation_v422
            result["automation_v422"] = {**automation_v422.register(core), "error": None}
    except Exception as exc:
        result["automation_v422"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","route":"/property-brain/automation-v422","fail_safe":True}

    # V19.1 reliable newspaper/magazine upload owner
    try:
        import alliance_newspaper_upload_v191 as newspaper_v191
        result["newspaper_upload_v191"] = {**newspaper_v191.register(wrapped), "error": None}
    except Exception as exc:
        result["newspaper_upload_v191"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}


    # FOUNDATION 4.2.3 - Alliance Grammar Rescue
    # Additive only. Repairs generic CRE grammar gaps for v422 abstentions.
    try:
        if _route_exists(app, "/api/property-brain/automation-v423/status"):
            result["automation_v423"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/automation-v423","error":None}
        else:
            import alliance_automation_grammar_rescue_v423 as automation_v423
            result["automation_v423"] = {**automation_v423.register(core), "error": None}
    except Exception as exc:
        result["automation_v423"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","route":"/property-brain/automation-v423","fail_safe":True}

    # V19.2 persistent original newspaper source + direct exhaustive worker
    try:
        import alliance_newspaper_persistent_v192 as newspaper_v192
        result["newspaper_persistent_v192"]={**newspaper_v192.register(wrapped),"error":None}
    except Exception as exc:
        result["newspaper_persistent_v192"]={"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}


    # FOUNDATION 4.2.4 - Alliance Exception Forensics
    # Read-only. Surfaces raw evidence and all independent judge diagnostics for unresolved V4 cases.
    try:
        if _route_exists(app, "/api/property-brain/automation-v424/status"):
            result["automation_v424"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/automation-v424","error":None}
        else:
            import alliance_exception_forensics_v424 as automation_v424
            result["automation_v424"] = {**automation_v424.register(core), "error": None}
    except Exception as exc:
        result["automation_v424"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","route":"/property-brain/automation-v424","fail_safe":True}


    # FOUNDATION 4.2.5 - Alliance Acquisition Intent Closure
    # Additive only. Two independent generic acquisition-intent judges for final V4 abstentions.
    try:
        if _route_exists(app, "/api/property-brain/automation-v425/status"):
            result["automation_v425"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/automation-v425","error":None}
        else:
            import alliance_acquisition_intent_closure_v425 as automation_v425
            result["automation_v425"] = {**automation_v425.register(core), "error": None}
    except Exception as exc:
        result["automation_v425"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","route":"/property-brain/automation-v425","fail_safe":True}


    # FOUNDATION 4.3.1 - Alliance Autonomous Student Mastery Repair
    # Additive only. 4.3.0 remains intact; V5 freezes only after 4.3.1 regression PASS.
    try:
        if _route_exists(app, "/api/property-brain/autonomous-v431/status"):
            result["autonomous_v431"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/autonomous-v431","error":None}
        else:
            import alliance_autonomous_student_v431 as autonomous_v431
            result["autonomous_v431"] = {**autonomous_v431.register(core), "error": None}
    except Exception as exc:
        result["autonomous_v431"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","route":"/property-brain/autonomous-v431","fail_safe":True}


    # FOUNDATION 4.3.2 - Alliance Autonomous Student Final Training Repair
    # Additive only. V5 remains protected until cumulative training gates pass.
    try:
        if _route_exists(app, "/api/property-brain/autonomous-v432/status"):
            result["autonomous_v432"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/autonomous-v432","error":None}
        else:
            import alliance_autonomous_student_v432 as autonomous_v432
            result["autonomous_v432"] = {**autonomous_v432.register(core), "error": None}
    except Exception as exc:
        result["autonomous_v432"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","route":"/property-brain/autonomous-v432","fail_safe":True}


    # FOUNDATION 4.3.3 - Alliance Requirement Grammar Closure
    # Additive only. Fresh V5 remains protected until all cumulative gates PASS.
    try:
        if _route_exists(app, "/api/property-brain/autonomous-v433/status"):
            result["autonomous_v433"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/autonomous-v433","error":None}
        else:
            import alliance_autonomous_student_v433 as autonomous_v433
            result["autonomous_v433"] = {**autonomous_v433.register(core), "error": None}
    except Exception as exc:
        result["autonomous_v433"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","route":"/property-brain/autonomous-v433","fail_safe":True}


    # FOUNDATION 4.3.4 - Alliance Demand Ownership Closure
    # Additive only. Fresh V5 remains protected until all cumulative gates PASS.
    try:
        if _route_exists(app, "/api/property-brain/autonomous-v434/status"):
            result["autonomous_v434"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/autonomous-v434","error":None}
        else:
            import alliance_autonomous_student_v434 as autonomous_v434
            result["autonomous_v434"] = {**autonomous_v434.register(core), "error": None}
    except Exception as exc:
        result["autonomous_v434"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","route":"/property-brain/autonomous-v434","fail_safe":True}


    # FOUNDATION 4.3.4F - Exact V4 Failure Forensics
    # Read-only forensic route. No student changes. No V5 freeze.
    try:
        if _route_exists(app, "/api/property-brain/autonomous-v434-forensics/status"):
            result["v434_forensics"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/autonomous-v434-forensics","error":None}
        else:
            import alliance_v4_failure_forensics_v434f as v434_forensics
            result["v434_forensics"] = {**v434_forensics.register(core), "error": None}
    except Exception as exc:
        result["v434_forensics"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","route":"/property-brain/autonomous-v434-forensics","fail_safe":True}


    # FOUNDATION 4.3.5 - Truth Integrity Repair
    try:
        import alliance_truth_integrity_v426 as v426
        result["truth_integrity_v426"] = {**v426.register(core), "error": None}
    except Exception as exc:
        result["truth_integrity_v426"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
    try:
        import alliance_autonomous_student_v435 as v435
        result["autonomous_v435"] = {**v435.register(core), "error": None}
    except Exception as exc:
        result["autonomous_v435"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}


    # FOUNDATION 4.3.6 - Demand Contract Closure
    # Training only. Fresh V5 is not frozen here.
    try:
        if _route_exists(app, "/api/property-brain/autonomous-v436/status"):
            result["autonomous_v436"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/autonomous-v436","error":None}
        else:
            import alliance_autonomous_student_v436 as v436
            result["autonomous_v436"] = {**v436.register(core), "error": None}
    except Exception as exc:
        result["autonomous_v436"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}


    # FOUNDATION 4.3.7 - Leading Demand Ownership Closure
    # Training only. Restores 4.3.5 stable base and leaves V5 untouched.
    try:
        if _route_exists(app, "/api/property-brain/autonomous-v437/status"):
            result["autonomous_v437"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/autonomous-v437","error":None}
        else:
            import alliance_autonomous_student_v437 as v437
            result["autonomous_v437"] = {**v437.register(core), "error": None}
    except Exception as exc:
        result["autonomous_v437"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}


    # FOUNDATION 4.3.8 - Demand Object Closure
    # Training only. Fresh V5 remains untouched.
    try:
        if _route_exists(app, "/api/property-brain/autonomous-v438/status"):
            result["autonomous_v438"] = {"status":"ALREADY_REGISTERED","route":"/property-brain/autonomous-v438","error":None}
        else:
            import alliance_autonomous_student_v438 as v438
            result["autonomous_v438"] = {**v438.register(core), "error": None}
    except Exception as exc:
        result["autonomous_v438"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}

    return result
