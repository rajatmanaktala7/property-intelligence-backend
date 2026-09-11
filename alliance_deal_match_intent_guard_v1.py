from __future__ import annotations

import alliance_requirement_intelligence_os_v2 as brain

VERSION = "3.1.0-V2-PRODUCTION-V3-REVIEW"


def classify(raw: str):
    result = brain.classify_intent(raw)
    return {"version": VERSION, **result}


def _capture(core, raw: str, source: str):
    try:
        import alliance_semantic_shadow_v3 as semantic_v3
        return semantic_v3.capture(core.engine, raw, source=source)
    except Exception as exc:
        return {
            "persisted": False,
            "semantic_v3_fail_safe": True,
            "error": f"{type(exc).__name__}: {exc}",
        }


def install(core):
    import alliance_deal_match_ai_v60 as v60

    semantic_registration = None
    review_registration = None

    try:
        import alliance_semantic_shadow_v3 as semantic_v3
        semantic_registration = semantic_v3.register(core)
    except Exception as exc:
        semantic_registration = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "fail_safe": True,
        }

    try:
        import alliance_semantic_review_v3 as review_v3
        review_registration = review_v3.register(core)
    except Exception as exc:
        review_registration = {
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "fail_safe": True,
        }

    if getattr(v60, "_INTENT_GUARD_INSTALLED", False):
        return {
            "status": "ALREADY_INSTALLED",
            "version": VERSION,
            "matcher_version": v60.VERSION,
            "semantic_v3": semantic_registration,
            "semantic_v3_review": review_registration,
        }

    original_render = v60.render_results

    def unified_constraint_summary(raw):
        return brain.constraint_summary(raw)

    def guarded_render(core_arg, q, mode, min_score):
        parts = v60.split_requirement_text(q)
        decisions = [classify(p["source"]) for p in parts]
        captures = []

        for part in parts:
            captures.append(
                _capture(core_arg, part["source"], "DEAL_MATCH_PAGE")
            )

        if parts and all(d["role"] == "REQUIREMENT" for d in decisions):
            response = original_render(core_arg, q, mode, min_score)

            # Do not alter matcher calculations. Add only a review-mode banner to HTML.
            try:
                if hasattr(response, "body"):
                    body = response.body.decode("utf-8")
                    review_links = [
                        c.get("review_url")
                        for c in captures
                        if isinstance(c, dict) and c.get("review_url")
                    ]
                    links = " ".join(
                        f'<a class="btn" href="{u}">Review V3 #{i+1}</a>'
                        for i, u in enumerate(review_links)
                    )
                    banner = (
                        '<div class="card"><h3>AI V3 Review Mode</h3>'
                        '<p class="amber">Visible matcher result below is still V2.3 production logic. '
                        'V3 ran independently and is ready for human review.</p>'
                        + links
                        + ' <a class="btn" href="/semantic-v3/review">Open Review Queue</a></div>'
                    )
                    body = body.replace("<main>", "<main>" + banner, 1)
                    return v60.HTMLResponse(body)
            except Exception:
                pass

            return response

        cards = [
            '<div class="card"><h2>Unified WhatsApp Intent Intelligence</h2>'
            '<p class="green">Only genuine REQUIREMENT items may enter Matcher V6.6.0.</p></div>'
        ]

        for part, decision in zip(parts, decisions):
            src = v60.phase5.sanitize_text(part["source"])
            role = decision["role"]

            if role == "SUPPLY":
                cards.append(
                    '<div class="card"><h2>Item ' + v60.esc(part["number"]) + ' · PROPERTY LISTING</h2>'
                    '<p>' + v60.esc(src) + '</p>'
                    '<p class="amber"><b>Not sent to Requirement Matcher.</b> This is supply/inventory.</p>'
                    '<p><b>Confidence:</b> ' + v60.esc(decision["confidence"]) + '% · <b>Evidence:</b> '
                    + v60.esc(", ".join(decision["supply_hits"])) + '</p>'
                    '<a class="btn" href="/property-manual">Open Property Intake</a> '
                    '<a class="btn" href="/alliance/primary/availability">Availability Verification</a></div>'
                )
            else:
                cards.append(
                    '<div class="card"><h2>Item ' + v60.esc(part["number"]) + ' · NEEDS HUMAN CLASSIFICATION</h2>'
                    '<p>' + v60.esc(src) + '</p>'
                    '<p class="red"><b>Matcher blocked for safety.</b></p>'
                    '<p>Requirement score: ' + v60.esc(decision["demand_score"]) + ' · Supply score: '
                    + v60.esc(decision["supply_score"]) + '</p></div>'
                )

        review_links = [
            c.get("review_url")
            for c in captures
            if isinstance(c, dict) and c.get("review_url")
        ]
        review_html = " ".join(
            f'<a class="btn" href="{u}">Review V3 #{i+1}</a>'
            for i, u in enumerate(review_links)
        )

        cards.append(
            '<div class="card"><p><b>Intent Guard:</b> ' + v60.esc(VERSION)
            + ' · <b>Requirement Brain:</b> ' + v60.esc(brain.VERSION)
            + ' · <b>Matcher:</b> ' + v60.esc(v60.VERSION)
            + '</p><p class="green">V3 semantic intelligence is in REVIEW mode. '
            'Production matching remains V2.3. Contacts remain hidden.</p>'
            + review_html
            + ' <a class="btn" href="/semantic-v3/review">Review Queue</a>'
            + ' <a class="btn" href="/deal-match-ai-v60">Run Another Item</a></div>'
        )

        return v60.HTMLResponse(
            v60._page("Alliance Deal Match AI · Unified Intent Guard", "".join(cards))
        )

    def guarded_multi(core_arg, q, mode="SMART", min_score=70.0, limit=100):
        parts = v60.split_requirement_text(q)
        out = []

        for part in parts:
            d = classify(part["source"])
            capture = _capture(core_arg, part["source"], "DEAL_MATCH_API")

            item = {
                "item_number": part["number"],
                "intent": d,
                "semantic_v3_review_id": capture.get("review_id") if isinstance(capture, dict) else None,
                "semantic_v3_review_url": capture.get("review_url") if isinstance(capture, dict) else None,
            }

            if d["role"] == "REQUIREMENT":
                item["matched"] = True
                item["result"] = v60.run_match(
                    core_arg, part["normalized"], mode, min_score, limit
                )
            else:
                item["matched"] = False
                item["route_to"] = (
                    "PROPERTY_INTAKE"
                    if d["role"] == "SUPPLY"
                    else "HUMAN_CLASSIFICATION"
                )
                item["source"] = v60.phase5.sanitize_text(part["source"])
            out.append(item)

        return {
            "version": v60.VERSION,
            "engine_version": v60.ENGINE_VERSION,
            "intent_guard_version": VERSION,
            "requirement_brain_version": brain.VERSION,
            "semantic_v3_mode": "REVIEW",
            "semantic_v3_global_authoritative": False,
            "production_matching_brain": brain.VERSION,
            "item_count": len(parts),
            "results": out,
            "contacts_exposed": False,
        }

    v60._constraint_summary = unified_constraint_summary
    v60.render_results = guarded_render
    v60.run_match_multi = guarded_multi
    v60._INTENT_GUARD_INSTALLED = True

    return {
        "status": "INSTALLED",
        "version": VERSION,
        "requirement_brain_version": brain.VERSION,
        "matcher_version": v60.VERSION,
        "matcher_file_modified": False,
        "blocks_supply_from_matcher": True,
        "constraint_summary_unified": True,
        "semantic_v3": semantic_registration,
        "semantic_v3_review": review_registration,
        "production_matching_brain": brain.VERSION,
        "semantic_v3_mode": "REVIEW",
        "v3_global_authoritative": False,
    }
