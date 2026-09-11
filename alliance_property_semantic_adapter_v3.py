from __future__ import annotations

from typing import Any, Dict

import alliance_requirement_brain_v3 as brain

VERSION = "3.0.0-PROPERTY-SEMANTIC-ADAPTER"


def _blob(row: Dict[str, Any]) -> str:
    fields = (
        "property_type", "subtype", "use_type", "property_name",
        "remarks", "description", "clean_record", "locality", "city",
    )
    return " ".join(str(row.get(k) or "") for k in fields)


def profile_property(row: Dict[str, Any]) -> Dict[str, Any]:
    blob = _blob(row)
    asset = brain.extract_asset(blob)
    use = brain.extract_use(blob)

    return {
        "version": VERSION,
        "canonical_id": str(row.get("canonical_id") or row.get("record_id") or ""),
        "asset": asset,
        "intended_use": use,
        "transaction": str(row.get("transaction_type") or row.get("transaction") or "").upper() or None,
        "location_raw": row.get("locality") or row.get("location"),
        "area_sqft": row.get("area_sqft"),
        "availability_status": row.get("availability_status"),
        "verification_status": row.get("verification_status") or row.get("verification"),
        "derived_only": True,
        "master_database_mutated": False,
    }
