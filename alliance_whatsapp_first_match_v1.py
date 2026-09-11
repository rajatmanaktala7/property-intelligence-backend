from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

import alliance_phase5_canonical_matcher as phase5

VERSION = "1.3.0-SYSTEM-AUDITED-WHATSAPP-MATCHER"


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

    if not exact_verified:
        allowed = set(phase5.approved_alternatives(req))

        for p in candidates:
            if p.get("location") not in allowed:
                continue

            ok, code, gate = phase5.eligible(req, p, "ALTERNATIVE")
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


def _force_verification(rows, stage):
    out = []

    for row in rows:
        item = dict(row)
        item["search_stage"] = stage
        item["send_eligible"] = False
        item["availability_verification"] = "VERIFY_FIRST_WHATSAPP_MASTER"

        why = list(item.get("why") or [])
        marker = (
            "WhatsApp master candidate; verify current availability "
            "before client sharing"
        )
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
    req = phase5.parse_requirement(requirement_text)

    # Load canonical inventory first.
    pi_raw = phase5.load_pi_properties(engine)
    pi_candidates = phase5.dedupe_candidates(pi_raw)

    # If a location is not in the static dictionary, use the existing
    # inventory vocabulary before declaring the requirement unmatchable.
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

        req = phase5.enrich_requirement_with_inventory_locations(
            req,
            requirement_text,
            pi_candidates + wa_probe_candidates,
        )
    else:
        wa_probe_raw = None
        wa_probe_candidates = None

    # Tier A: canonical inventory. Existing hard gates stay unchanged.
    pi_selected = _evaluate(
        req,
        pi_candidates,
        min_score,
        limit,
    )

    # Tier B: requirement-filtered WhatsApp property master.
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
    except Exception:
        wa_raw = []
        wa_candidates = []

    wa_selected = _evaluate(
        req,
        wa_candidates,
        min_score,
        limit,
    )

    canonical_verified = _tag(
        pi_selected["exact_verified"],
        "CANONICAL_MASTER",
    )

    canonical_verify = _tag(
        pi_selected["exact_needs_verification"],
        "CANONICAL_MASTER",
    )

    canonical_alternatives = _tag(
        pi_selected["alternatives"],
        "CANONICAL_MASTER",
    )

    wa_exact_all = (
        list(wa_selected["exact_verified"])
        + list(wa_selected["exact_needs_verification"])
    )

    wa_verify = _force_verification(
        wa_exact_all,
        "WHATSAPP_PROPERTY_MASTER",
    )

    wa_alternatives = _force_verification(
        wa_selected["alternatives"],
        "WHATSAPP_PROPERTY_MASTER",
    )

    exact_verified = _dedupe_public(
        canonical_verified
    )[:limit]

    exact_verify = _dedupe_public(
        canonical_verify + wa_verify
    )
    exact_verify.sort(
        key=lambda x: x.get("match_score", 0),
        reverse=True,
    )
    exact_verify = exact_verify[:limit]

    if exact_verified:
        alternatives = []
    else:
        alternatives = _dedupe_public(
            canonical_alternatives + wa_alternatives
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
                len(pi_candidates) + len(wa_candidates)
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
            "matching_path": "CANONICAL_THEN_WHATSAPP_MASTER",
            "primary_source": "pi_properties",
            "evidence_source": "pi_whatsapp_property_master",
            "fallback_source": "pi_whatsapp_property_master",
            "fallback_used": bool(
                wa_verify or wa_alternatives
            ),
            "contacts_exposed": False,
            "price_used_only_when_comparable": True,
            "price_excluded_from_identity": True,
            "whatsapp_matches_forced_to_verify": True,
            "location_resolution": req.get("location_resolution"),
            "transaction_source": req.get("transaction_source"),
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

    payload = repr(result)

    if (
        phase5.PHONE_RE.search(payload)
        or phase5.EMAIL_RE.search(payload)
    ):
        raise RuntimeError(
            "CONTACT_LEAK_GUARD_TRIGGERED"
        )

    return result
