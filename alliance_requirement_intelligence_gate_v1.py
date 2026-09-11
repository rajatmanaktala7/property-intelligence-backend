from __future__ import annotations

from typing import Any, Dict, List, Tuple

import alliance_requirement_intelligence_os_v2 as brain

VERSION = brain.VERSION


def analyze(raw: str) -> Dict[str, Any]:
    return brain.analyze(raw)


def normalize_for_matcher(raw: str) -> Tuple[str, Dict[str, Any]]:
    normalized, _req, intel = (
        brain.build_canonical_requirement(raw)
    )
    return normalized, intel


def enforce_strict_scope(
    candidates: List[Dict[str, Any]],
    intelligence: Dict[str, Any],
):
    return brain.filter_candidates_by_hard_scope(
        candidates,
        intelligence,
    )
