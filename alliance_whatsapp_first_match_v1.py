from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

import alliance_phase5_canonical_matcher as phase5
import alliance_requirement_intelligence_os_v2 as requirement_brain
import alliance_master_property_match_source_v1 as master_source

VERSION = "1.4.0-LOCATION-PURITY-CONTACT-GUARD"


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
        ok, code, gate = phase5.eligible(req, p, "EXACT")

        if not ok:
            if len(rejected) < 1000:
                rejected.append({
                    "record_id": p.get("record_id"),
                    "source_table": p.get("source_table"),
                    "location": p.get("location"),
                    "reason": code,
                })
            continue

        ms, why = phase5.score(req, p, "EXACT", gate)

        if ms < float(min_score):
            if len(rejected) < 1000:
                rejected.append({
                    "record_id": p.get("record_id"),
                    "source_table": p.get("source_table"),
                    "location": p.get("location"),
                    "reason": "BELOW_MIN_SCORE",
                })
            continue

        item = phase5.public_item(p, ms, "EXACT", why)

        if item.get("send_eligible"):
            exact_verified.append(item)
        else:
            exact_verify.append(item)

    exact_verified.sort(key=lambda x: x.get("match_score", 0), reverse=True)
    exact_verify.sort(key=lambda x: x.get("match_score", 0), reverse=True)

    alternatives = []

    if not exact_verified and not req.get("location_only"):
        allowed = set(phase5.approved_alternatives(req))

        for p in candidates:
            if p.get("location") not in allowed:
                continue

            ok, _code, gate = phase5.eligible(req, p, "ALTERNATIVE")
            if not ok:
                continue

            ms, why = phase5.score(req, p, "ALTERNATIVE", gate)

            if ms >= max(60.0, float(min_score) - 10.0):
                alternatives.append(
                    phase5.public_item(
                        p,
                        ms,
                        "APPROVED_ALTERNATIVE",
                        why,
                    )
                )

        alternatives.sort(
            key=lambda x: (
                bool(x.get("send_eligible")),
                x.get("match_score", 0),
            ),
            reverse=True,
        )

    return {
        "exact_verified": exact_verified[:limit],
        "exact_needs_verification": exact_verify[:limit],
        "alternatives": alternatives[:limit],
        "rejected_sample": rejected[:200],
    }


def _tag(rows, stage):
    out = []
    for row in rows:
        item = dict(row)
        item["search_stage"] = stage
        out.append(item)
    return out


def _force_verification(rows, stage):
    out = []

    for row in rows:
        item = dict(row)
        item["search_stage"] = stage
        item["send_eligible"] = False
        item["availability_verification"] = "VERIFY_FIRST_WHATSAPP_MASTER"

        why = list(item.get("why") or [])
        marker = "WhatsApp master candidate; verify current availability before client sharing"

        if marker not in why:
            why.append(marker)

        item["why"] = why
        out.append(item)

    return out


def _rejection_counts(*samples):
    counter = Counter()

    for sample in samples:
        for row in sample or []:
            counter[str(row.get("reason") or "UNKNOWN")] += 1

    return dict(counter.most_common())


def _dedupe_public(rows):
    seen = set()
    out = []

    for row in rows:
        key = (
            str(row.get("source_table") or ""),
            str(row.get("record_id") or ""),
            str(row.get("location") or ""),
            str(row.get("property") or "")[:160],
        )

        if key in seen:
            continue

        seen.add(key)
        out.append(row)

    return out


def run_match(
    engine,
    requirement_text: str,
    min_score: float = 70.0,
    limit: int = 50,
):
    _normalized, req, intelligence = requirement_brain.build_canonical_requirement(
        requirement_text
    )

    if intelligence["intent"]["role"] != "REQUIREMENT":
        return {
            "version": VERSION,
            "requirement": req,
            "summary": {
                "blocked_by_intent_guard": True,
                "intent_role": intelligence["intent"]["role"],
                "intent_confidence": intelligence["intent"]["confidence"],
                "exact_verified": 0,
                "exact_needs_verification": 0,
                "approved_alternatives": 0,
                "contacts_exposed": False,
                "requirement_intelligence_version": requirement_brain.VERSION,
            },
            "exact_verified": [],
            "exact_needs_verification": [],
            "alternatives": [],
            "rejected_sample": [],
        }

    pi_raw = master_source.load_master_properties(
        engine,
        req,
        limit=12000,
    )

    master_source_used = "pi_master_properties_v711"

    if not pi_raw:
        pi_raw = phase5.load_pi_properties(engine)
        master_source_used = "pi_properties_LEGACY_FALLBACK"

    pi_candidates = phase5.dedupe_candidates(pi_raw)

    pi_candidates, pi_scope = requirement_brain.filter_candidates_by_hard_scope(
        pi_candidates,
        intelligence,
    )

    if not req.get("primary_locations"):
        try:
            wa_probe_raw = phase5.load_whatsapp_master_for_requirement(
                engine,
                {},
                limit=20000,
            )
        except Exception:
            wa_probe_raw = []

        wa_probe_candidates = phase5.dedupe_candidates(wa_probe_raw)

        wa_probe_candidates, _ = requirement_brain.filter_candidates_by_hard_scope(
            wa_probe_candidates,
            intelligence,
        )

        req = phase5.enrich_requirement_with_inventory_locations(
            req,
            requirement_text,
            pi_candidates + wa_probe_candidates,
        )

    else:
        wa_probe_raw = None
        wa_probe_candidates = None

    pi_selected = _evaluate(
        req,
        pi_candidates,
        min_score,
        limit,
    )

    try:
        if wa_probe_raw is not None and not req.get("primary_locations"):
            wa_raw = wa_probe_raw
            wa_candidates = wa_probe_candidates or []

        else:
            wa_raw = phase5.load_whatsapp_master_for_requirement(
                engine,
                req,
                limit=20000,
            )

            wa_candidates = phase5.dedupe_candidates(wa_raw)

            wa_candidates, _ = requirement_brain.filter_candidates_by_hard_scope(
                wa_candidates,
                intelligence,
            )

    except Exception:
        wa_raw = []
        wa_candidates = []

    wa_selected = _evaluate(
        req,
        wa_candidates,
        min_score,
        limit,
    )

    exact_verified = _dedupe_public(
        _tag(
            pi_selected["exact_verified"],
            "MASTER_PROPERTY_DATABASE",
        )
    )[:limit]

    exact_verify = _dedupe_public(
        _tag(
            pi_selected["exact_needs_verification"],
            "MASTER_PROPERTY_DATABASE",
        )
        + _force_verification(
            list(wa_selected["exact_verified"])
            + list(wa_selected["exact_needs_verification"]),
            "WHATSAPP_PROPERTY_MASTER",
        )
    )

    exact_verify.sort(
        key=lambda x: x.get("match_score", 0),
        reverse=True,
    )

    exact_verify = exact_verify[:limit]

    if exact_verified or req.get("location_only"):
        alternatives = []

    else:
        alternatives = _dedupe_public(
            _tag(
                pi_selected["alternatives"],
                "MASTER_PROPERTY_DATABASE",
            )
            + _force_verification(
                wa_selected["alternatives"],
                "WHATSAPP_PROPERTY_MASTER",
            )
        )

        alternatives.sort(
            key=lambda x: (
                bool(x.get("send_eligible")),
                x.get("match_score", 0),
            ),
            reverse=True,
        )

        alternatives = alternatives[:limit]

    rejection_counts = _rejection_counts(
        pi_selected.get("rejected_sample"),
        wa_selected.get("rejected_sample"),
    )

    result = {
        "version": VERSION,
        "requirement": req,
        "summary": {
            "pi_properties": len(pi_raw),
            "database_deduped_candidates": len(pi_candidates),
            "deduped_candidates": (
                len(pi_candidates)
                + len(wa_candidates)
            ),
            "pi_whatsapp_property_master": len(wa_raw),
            "whatsapp_deduped_candidates": len(wa_candidates),
            "exact_verified": len(exact_verified),
            "exact_needs_verification": len(exact_verify),
            "approved_alternatives": len(alternatives),
            "inventory_gap": not bool(
                exact_verified
                or exact_verify
                or alternatives
            ),
            "matching_path": "UNIFIED_REQUIREMENT_BRAIN_TO_MASTER_PROPERTY_DB",
            "primary_source": master_source_used,
            "master_property_rows_loaded": len(pi_raw),
            "master_property_source_adapter": master_source.VERSION,
            "contacts_exposed": False,
            "price_used_only_when_comparable": True,
            "price_excluded_from_identity": True,
            "whatsapp_matches_forced_to_verify": True,
            "location_resolution": req.get("location_resolution"),
            "transaction_source": req.get("transaction_source"),
            "transaction_confidence": req.get("transaction_confidence"),
            "requirement_intelligence_version": requirement_brain.VERSION,
            "hard_constraints": intelligence.get("hard_constraints"),
            "preferences": intelligence.get("preferences"),
            "strict_scope": pi_scope,
            "location_only": req.get("location_only"),
            "rejection_counts": rejection_counts,
        },
        "exact_verified": exact_verified,
        "exact_needs_verification": exact_verify,
        "alternatives": alternatives,
        "rejected_sample": (
            pi_selected.get("rejected_sample", [])
            + wa_selected.get("rejected_sample", [])
        )[:200],
    }

    if hasattr(phase5, "sanitize_public_payload"):
        result = phase5.sanitize_public_payload(result)

    leak_paths = (
        phase5.public_payload_contact_paths(result)
        if hasattr(phase5, "public_payload_contact_paths")
        else []
    )

    if leak_paths:
        raise RuntimeError(
            "CONTACT_LEAK_GUARD_TRIGGERED:"
            + ",".join(leak_paths[:20])
        )

    return result
