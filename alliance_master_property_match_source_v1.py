from __future__ import annotations

from typing import Any, Dict, List

import alliance_phase5_canonical_matcher as phase5
import alliance_master_integration_v720 as master_v720

VERSION = "1.0.0-MASTER-PROPERTY-SOURCE-ADAPTER"


def _blob(row: Dict[str, Any]) -> str:
    parts = []
    for key in (
        "property_type",
        "subtype",
        "use_type",
        "property_name",
        "remarks",
        "description",
        "clean_record",
        "locality",
        "city",
    ):
        value = row.get(key)
        if value not in (None, ""):
            parts.append(str(value))
    return " ".join(parts)


def load_master_properties(engine, req: Dict[str, Any], limit: int = 12000) -> List[Dict[str, Any]]:
    tx = str(req.get("transaction") or "").upper().strip()
    if tx not in {"SALE", "RENT"}:
        tx = ""

    rows = master_v720._search_properties(
        engine,
        q="",
        tx=tx,
        limit=max(1, min(int(limit), 20000)),
    )

    out: List[Dict[str, Any]] = []

    for d in rows:
        availability = str(d.get("availability_status") or "UNKNOWN").upper().strip()
        if availability in {"UNAVAILABLE", "INACTIVE"}:
            continue

        transaction = str(d.get("transaction_type") or "").upper().strip()
        if transaction not in {"SALE", "RENT"}:
            continue

        locality_raw = d.get("locality")
        location = phase5.canonical_location(locality_raw)
        if not location:
            location = phase5.candidate_location(locality_raw)
        if not location:
            continue

        text_blob = _blob(d)
        family, subtype = phase5.family_subtype(text_blob)

        area = d.get("area_sqft")
        try:
            area_sqft = float(area) if area not in (None, "") else None
        except Exception:
            area_sqft = None

        price_raw = d.get("price_raw")
        price = phase5.money_value(price_raw)

        verification = str(d.get("verification_status") or "UNVERIFIED")

        out.append({
            "source_bucket": "MASTER_PROPERTY_DB",
            "source_table": "pi_master_properties_v711",
            "record_id": str(d.get("canonical_id") or ""),
            "description": phase5.sanitize_text(
                d.get("property_name")
                or d.get("description")
                or d.get("remarks")
                or locality_raw
            ),
            "location": location,
            "transaction": transaction,
            "family": family,
            "subtype": subtype,
            "area_sqft": area_sqft,
            "area_unit_verified": bool(area_sqft is not None),
            "price": price,
            "price_text": phase5.sanitize_text(price_raw),
            "price_comparable": price is not None,
            "quality": "READY",
            "verification": verification,
            "captured_on": d.get("updated_at") or d.get("created_at"),
            "source_name": "Alliance Master Property Database",
            "review_reasons": None,
            "availability_status": availability,
            "canonical_id": str(d.get("canonical_id") or ""),
        })

    return out
