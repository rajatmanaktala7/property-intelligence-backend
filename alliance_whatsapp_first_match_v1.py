from __future__ import annotations

from typing import Any, Dict, List

import alliance_phase5_canonical_matcher as phase5

VERSION = "1.0.0-WHATSAPP-FIRST-STAGED-MATCHER"


def _evaluate(
    req: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    min_score: float,
    limit: int,
) -> Dict[str, Any]:

    exact_verified = []
    exact_verify = []
    rejected = []

    for p in candidates:

        ok, code, gate = phase5.eligible(
            req,
            p,
            "EXACT"
        )

        if not ok:
            if len(rejected) < 200:
                rejected.append({
                    "record_id": p.get("record_id"),
                    "reason": code,
                })
            continue

        ms, why = phase5.score(
            req,
            p,
            "EXACT",
            gate
        )

        if ms < float(min_score):
            continue

        item = phase5.public_item(
            p,
            ms,
            "EXACT",
            why
        )

        if item.get("send_eligible"):
            exact_verified.append(item)
        else:
            exact_verify.append(item)

    exact_verified.sort(
        key=lambda x: x.get("match_score", 0),
        reverse=True
    )

    exact_verify.sort(
        key=lambda x: x.get("match_score", 0),
        reverse=True
    )

    alternatives = []

    # Same safety rule as Phase 5:
    # alternatives appear when there is no VERIFIED exact property.
    if not exact_verified:

        allowed = set(
            phase5.approved_alternatives(req)
        )

        for p in candidates:

            if p.get("location") not in allowed:
                continue

            ok, code, gate = phase5.eligible(
                req,
                p,
                "ALTERNATIVE"
            )

            if not ok:
                continue

            ms, why = phase5.score(
                req,
                p,
                "ALTERNATIVE",
                gate
            )

            if ms >= max(
                60.0,
                float(min_score) - 10.0
            ):

                alternatives.append(
                    phase5.public_item(
                        p,
                        ms,
                        "APPROVED_ALTERNATIVE",
                        why
                    )
                )

        alternatives.sort(
            key=lambda x: (
                bool(x.get("send_eligible")),
                x.get("match_score", 0)
            ),
            reverse=True
        )

    return {
        "exact_verified": exact_verified[:limit],
        "exact_needs_verification": exact_verify[:limit],
        "alternatives": alternatives[:limit],
        "rejected_sample": rejected[:100],
    }


def _has_match(result):

    return bool(
        result.get("exact_verified")
        or result.get("exact_needs_verification")
        or result.get("alternatives")
    )


def _tag(rows, stage):

    out = []

    for row in rows:

        item = dict(row)

        item["search_stage"] = stage

        out.append(item)

    return out


def run_match(
    engine,
    requirement_text: str,
    min_score: float = 70.0,
    limit: int = 50,
):

    req = phase5.parse_requirement(
        requirement_text
    )

    # =====================================================
    # STAGE 1
    # WHATSAPP AVAILABILITY ONLY
    # =====================================================

    wa_raw = phase5.load_whatsapp_master(
        engine
    )

    wa_candidates = phase5.dedupe_candidates(
        wa_raw
    )

    wa_result = _evaluate(
        req,
        wa_candidates,
        min_score,
        limit
    )

    # =====================================================
    # STAGE 2
    # DATABASE FALLBACK ONLY IF WHATSAPP FOUND NOTHING
    # =====================================================

    fallback_used = not _has_match(
        wa_result
    )

    pi_raw = []
    pi_candidates = []

    if fallback_used:

        pi_raw = phase5.load_pi_properties(
            engine
        )

        pi_candidates = phase5.dedupe_candidates(
            pi_raw
        )

        selected = _evaluate(
            req,
            pi_candidates,
            min_score,
            limit
        )

        stage = "DATABASE_FALLBACK"

    else:

        selected = wa_result

        stage = "WHATSAPP_PRIMARY"

    exact_verified = _tag(
        selected["exact_verified"],
        stage
    )

    exact_verify = _tag(
        selected["exact_needs_verification"],
        stage
    )

    alternatives = _tag(
        selected["alternatives"],
        stage
    )

    result = {

        "version": VERSION,

        "requirement": req,

        "summary": {

            "pi_whatsapp_property_master":
                len(wa_raw),

            "whatsapp_deduped_candidates":
                len(wa_candidates),

            "pi_properties":
                len(pi_raw),

            "database_deduped_candidates":
                len(pi_candidates),

            "deduped_candidates":
                len(
                    pi_candidates
                    if fallback_used
                    else wa_candidates
                ),

            "exact_verified":
                len(exact_verified),

            "exact_needs_verification":
                len(exact_verify),

            "approved_alternatives":
                len(alternatives),

            "inventory_gap":
                not bool(
                    exact_verified
                    or exact_verify
                    or alternatives
                ),

            "matching_path":
                (
                    "WHATSAPP_THEN_DATABASE_FALLBACK"
                    if fallback_used
                    else "WHATSAPP_ONLY"
                ),

            "primary_source":
                "pi_whatsapp_property_master",

            "fallback_source":
                "pi_properties",

            "fallback_used":
                fallback_used,

            "contacts_exposed":
                False,

            "price_used_only_when_comparable":
                True,

            "price_excluded_from_identity":
                True,
        },

        "exact_verified":
            exact_verified,

        "exact_needs_verification":
            exact_verify,

        "alternatives":
            alternatives,

        "rejected_sample":
            selected.get(
                "rejected_sample",
                []
            )[:100],
    }

    # =====================================================
    # CONTACT SECURITY
    # =====================================================

    if hasattr(
        phase5,
        "sanitize_public_payload"
    ):

        result = (
            phase5.sanitize_public_payload(
                result
            )
        )

    payload = repr(result)

    if (
        phase5.PHONE_RE.search(payload)
        or phase5.EMAIL_RE.search(payload)
    ):

        raise RuntimeError(
            "CONTACT_LEAK_GUARD_TRIGGERED"
        )

    return result
