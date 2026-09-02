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


    return result
