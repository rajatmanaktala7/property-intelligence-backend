from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

VERSION = "1.0.0-REQUIREMENT-INTELLIGENCE-GATE"


def _norm(value: Any) -> str:
    s = str(value or "").upper()
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _extract_block(raw: str) -> str | None:
    m = re.search(r"(?i)\bBLOCK\s*[-:]?\s*([A-Z0-9]+)\b", raw or "")
    return f"BLOCK {m.group(1).upper()}" if m else None


def _extract_project(raw: str) -> str | None:
    text = re.sub(r"[*_]+", "", str(raw or ""))
    patterns = (
        r"(?i)\b(?:PLOT|LAND|PROPERTY|SPACE)\s+(?:IN|AT)\s+(.+?)\s+(?=SECTOR\s*[-]?\s*\d+\b)",
        r"(?i)\b(?:IN|AT)\s+(.+?)\s+(?=SECTOR\s*[-]?\s*\d+\b)",
    )
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            project = _norm(m.group(1))
            project = re.sub(r"\b(?:ONLY|REQUIRED|REQUIREMENT)$", "", project).strip()
            if project and len(project) >= 4 and project not in {"THE", "A", "AN"}:
                return project
    return None


def _strict_scope(raw: str, project: str | None, block: str | None) -> bool:
    n = _norm(raw)
    return bool((project or block) and re.search(r"\b(?:ONLY|STRICTLY|SPECIFICALLY)\b", n))


def _infer_transaction(raw: str) -> Tuple[str | None, str | None, float]:
    n = _norm(raw)
    rent = bool(re.search(r"\b(RENT|RENTAL|LEASE|LEASING)\b", n))
    sale = bool(re.search(r"\b(SALE|PURCHASE|BUY|BUYING|RESALE|SELL)\b", n))
    if sale and not rent:
        return "SALE", "EXPLICIT", 1.0
    if rent and not sale:
        return "RENT", "EXPLICIT", 1.0
    if sale and rent:
        return None, "AMBIGUOUS", 0.0

    land_or_plot = bool(re.search(r"\b(PLOT|LAND)\b", n))
    demand = bool(re.search(r"\b(REQUIREMENT|REQUIRED|NEED|NEEDED|LOOKING|WANTED|WANT|SEEKING)\b", n))
    rent_cue = bool(re.search(r"\b(RENT|RENTAL|LEASE|LEASING|MONTHLY|DEPOSIT|LOCK\s*IN|LOCKIN)\b", n))
    if land_or_plot and demand and not rent_cue:
        return "SALE", "INFERRED_LAND_DEMAND", 0.90
    return None, None, 0.0


def analyze(raw: str) -> Dict[str, Any]:
    project = _extract_project(raw)
    block = _extract_block(raw)
    transaction, transaction_source, transaction_confidence = _infer_transaction(raw)
    return {
        "version": VERSION,
        "project": project,
        "block": block,
        "scope_strict": _strict_scope(raw, project, block),
        "transaction": transaction,
        "transaction_source": transaction_source,
        "transaction_confidence": transaction_confidence,
        "requires_human_transaction_confirmation": transaction_source == "INFERRED_LAND_DEMAND",
    }


def normalize_for_matcher(raw: str) -> Tuple[str, Dict[str, Any]]:
    intelligence = analyze(raw)
    normalized = str(raw or "")
    if intelligence.get("transaction") == "SALE" and not re.search(r"(?i)\b(SALE|PURCHASE|BUY|RESALE)\b", normalized):
        normalized += " | FOR SALE"
    elif intelligence.get("transaction") == "RENT" and not re.search(r"(?i)\b(RENT|RENTAL|LEASE)\b", normalized):
        normalized += " | FOR RENT"
    return normalized, intelligence


def _candidate_blob(candidate: Dict[str, Any]) -> str:
    return _norm(" ".join(str(candidate.get(k) or "") for k in (
        "description", "location", "source_name", "property_name", "remarks", "configuration_details"
    )))


def _contains_project(blob: str, project: str) -> bool:
    words = [w for w in _norm(project).split() if len(w) >= 3]
    blob_words = set(blob.split())
    return bool(words) and all(w in blob_words for w in words)


def _contains_block(blob: str, block: str) -> bool:
    b = _norm(block)
    return not b or b in blob or b.replace(" ", "") in blob.replace(" ", "")


def enforce_strict_scope(candidates: List[Dict[str, Any]], intelligence: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if not intelligence.get("scope_strict"):
        return candidates, {"applied": False, "before": len(candidates), "after": len(candidates)}
    project = str(intelligence.get("project") or "").strip()
    block = str(intelligence.get("block") or "").strip()
    out = []
    for candidate in candidates:
        blob = _candidate_blob(candidate)
        if project and not _contains_project(blob, project):
            continue
        if block and not _contains_block(blob, block):
            continue
        out.append(candidate)
    return out, {"applied": True, "project": project or None, "block": block or None, "before": len(candidates), "after": len(out)}
