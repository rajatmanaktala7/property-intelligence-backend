from __future__ import annotations

from typing import Any, Dict, List

VERSION = "3.0.0-CANONICAL-SEMANTIC-SCHEMA"

ALLOWED_ROLES = {"REQUIREMENT", "SUPPLY", "AMBIGUOUS"}
ALLOWED_TRANSACTION = {"SALE", "RENT", None}
ALLOWED_TRANSACTION_STATUS = {"EXPLICIT", "INFERRED_HIGH", "UNKNOWN", "CONFLICTING"}
ALLOWED_LOCATION_CONSTRAINTS = {"HARD", "PREFERRED", "ACCEPTABLE", "EXCLUDED", "AI_ALTERNATIVE"}
ALLOWED_FIELD_STATUS = {"EXPLICIT", "INFERRED_HIGH", "INFERRED_MEDIUM", "UNKNOWN", "CONFLICTING"}


def validate_requirement(obj: Dict[str, Any]) -> List[str]:
    errors: List[str] = []

    if not isinstance(obj, dict):
        return ["requirement must be an object"]

    if obj.get("schema_version") != "3.0":
        errors.append("schema_version must be 3.0")

    intent = obj.get("intent") or {}
    if intent.get("role") not in ALLOWED_ROLES:
        errors.append("intent.role invalid")

    tx = obj.get("transaction") or {}
    if tx.get("value") not in ALLOWED_TRANSACTION:
        errors.append("transaction.value invalid")
    if tx.get("status") not in ALLOWED_TRANSACTION_STATUS:
        errors.append("transaction.status invalid")

    asset = obj.get("asset") or {}
    if not asset.get("primary_asset"):
        errors.append("asset.primary_asset required")

    locations = obj.get("locations")
    if not isinstance(locations, list):
        errors.append("locations must be a list")
    else:
        seen = set()
        for i, loc in enumerate(locations):
            if not isinstance(loc, dict):
                errors.append(f"locations[{i}] must be an object")
                continue
            name = str(loc.get("name") or "").strip()
            if not name:
                errors.append(f"locations[{i}].name required")
            if loc.get("constraint") not in ALLOWED_LOCATION_CONSTRAINTS:
                errors.append(f"locations[{i}].constraint invalid")
            key = (name, loc.get("constraint"))
            if key in seen:
                errors.append(f"duplicate location: {key}")
            seen.add(key)

    confidence = obj.get("field_confidence") or {}
    if not isinstance(confidence, dict):
        errors.append("field_confidence must be an object")
    else:
        for key, value in confidence.items():
            try:
                f = float(value)
            except Exception:
                errors.append(f"field_confidence.{key} must be numeric")
                continue
            if f < 0 or f > 1:
                errors.append(f"field_confidence.{key} must be between 0 and 1")

    evidence = obj.get("evidence") or {}
    if not isinstance(evidence, dict):
        errors.append("evidence must be an object")

    if not isinstance(obj.get("unknowns"), list):
        errors.append("unknowns must be a list")

    if not isinstance(obj.get("hard_constraints"), list):
        errors.append("hard_constraints must be a list")

    if not isinstance(obj.get("preferences"), list):
        errors.append("preferences must be a list")

    return errors


def assert_valid_requirement(obj: Dict[str, Any]) -> Dict[str, Any]:
    errors = validate_requirement(obj)
    if errors:
        raise ValueError("INVALID_CANONICAL_REQUIREMENT: " + "; ".join(errors))
    return obj
