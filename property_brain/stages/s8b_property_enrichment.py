from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from sqlalchemy import text


# ---------------------------------------------------------------------
# Alliance Property Enrichment V2
#
# Goals:
# - Never mutate raw source evidence.
# - Never invent missing facts.
# - Enrich only from conservative cross-source matches.
# - Make missing fields transaction-aware and property-type-aware.
# - Keep existing canonical structured fields unchanged.
# - Persist only clean_description, exactly as V1 did.
# ---------------------------------------------------------------------

VERSION = "2.0.0-QUALITY-ENRICHMENT"


GENERIC_CRITICAL_FIELDS = [
    "locality",
    "property_family",
    "transaction_type",
    "area_sqft",
]

RESIDENTIAL_PRIORITY_FIELDS = [
    "project_name",
    "property_subtype",
    "configuration",
    "floor",
    "furnishing",
]

COMMERCIAL_PRIORITY_FIELDS = [
    "project_name",
    "property_subtype",
    "floor",
    "frontage",
    "use_suitability",
]

LAND_PRIORITY_FIELDS = [
    "property_subtype",
    "plot_area",
    "road_width",
    "zoning",
    "use_suitability",
]

CROSS_SOURCE_FIELDS = [
    "project_name",
    "locality",
    "property_family",
    "property_subtype",
    "transaction_type",
    "configuration",
    "area_sqft",
    "floor",
    "furnishing",
    "rent_value",
    "sale_price_value",
    "frontage",
    "use_suitability",
    "plot_area",
    "road_width",
    "zoning",
]


def _known(value: Any) -> bool:
    if value is None:
        return False
    text_value = str(value).strip()
    if not text_value:
        return False
    return text_value.upper() not in {
        "UNKNOWN",
        "N/A",
        "NA",
        "NONE",
        "NULL",
        "—",
        "-",
    }


def _text(value: Any, default: str = "Unknown") -> str:
    return str(value).strip() if _known(value) else default


def _phones(value: Any) -> List[str]:
    values = value if isinstance(value, list) else [value]
    out: List[str] = []

    for item in values:
        for raw in re.findall(r"(?:\+?91[\s-]?)?[6-9]\d{9}", str(item or "")):
            number = re.sub(r"\D", "", raw)[-10:]
            if number and number not in out:
                out.append(number)

    return out


def _number(value: Any) -> float | None:
    if not _known(value):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return None


def _format_indian_money(value: Any, suffix: str = "") -> str:
    amount = _number(value)
    if amount is None:
        return "Unknown"

    abs_amount = abs(amount)

    if abs_amount >= 10_000_000:
        formatted = f"Rs {amount / 10_000_000:.2f} Cr"
    elif abs_amount >= 100_000:
        formatted = f"Rs {amount / 100_000:.2f} Lakh"
    else:
        formatted = f"Rs {amount:,.0f}"

    return f"{formatted}{suffix}"


def _transaction(value: Any) -> str:
    t = str(value or "").upper().strip()

    if any(x in t for x in ["SALE", "SELL", "PURCHASE", "BUY"]):
        sale = True
    else:
        sale = False

    if any(x in t for x in ["RENT", "LEASE", "LEASING"]):
        rent = True
    else:
        rent = False

    if sale and rent:
        return "SALE_AND_RENT"
    if sale:
        return "SALE"
    if rent:
        return "RENT"
    return "UNKNOWN"


def _family(value: Any) -> str:
    t = str(value or "").upper()

    if any(x in t for x in ["RESIDENTIAL", "APARTMENT", "FLAT", "VILLA", "HOUSE", "FLOOR"]):
        return "RESIDENTIAL"

    if any(
        x in t
        for x in [
            "COMMERCIAL",
            "OFFICE",
            "RETAIL",
            "SHOP",
            "SHOWROOM",
            "RESTAURANT",
            "BANQUET",
            "WAREHOUSE",
            "INDUSTRIAL",
            "HOTEL",
            "HOSPITALITY",
        ]
    ):
        return "COMMERCIAL"

    if any(x in t for x in ["LAND", "PLOT", "FARM", "FARMHOUSE", "AGRICULTURAL"]):
        return "LAND"

    return "UNKNOWN"


def _critical_fields(property_data: Dict[str, Any]) -> List[str]:
    fields: List[str] = list(GENERIC_CRITICAL_FIELDS)

    transaction = _transaction(property_data.get("transaction_type"))
    family = _family(
        property_data.get("property_family")
        or property_data.get("property_subtype")
    )

    if transaction == "SALE":
        fields.append("sale_price_value")
    elif transaction == "RENT":
        fields.append("rent_value")
    elif transaction == "SALE_AND_RENT":
        fields.extend(["sale_price_value", "rent_value"])

    if family == "RESIDENTIAL":
        fields.extend(RESIDENTIAL_PRIORITY_FIELDS)
    elif family == "COMMERCIAL":
        fields.extend(COMMERCIAL_PRIORITY_FIELDS)
    elif family == "LAND":
        fields.extend(LAND_PRIORITY_FIELDS)
    else:
        fields.extend(["property_subtype"])

    # Preserve order while removing duplicates.
    seen = set()
    ordered = []
    for field in fields:
        if field not in seen:
            seen.add(field)
            ordered.append(field)

    return ordered


def _missing_fields(property_data: Dict[str, Any]) -> List[str]:
    return [
        field
        for field in _critical_fields(property_data)
        if not _known(property_data.get(field))
    ]


def _label(field: str) -> str:
    labels = {
        "project_name": "Project / Building Name",
        "property_subtype": "Property Subtype",
        "configuration": "Configuration",
        "area_sqft": "Area",
        "floor": "Floor",
        "furnishing": "Furnishing",
        "rent_value": "Rent",
        "sale_price_value": "Sale Price",
        "frontage": "Frontage",
        "use_suitability": "Use Suitability",
        "plot_area": "Plot Area",
        "road_width": "Road Width",
        "zoning": "Zoning / Land Use",
        "locality": "Locality",
        "property_family": "Property Type",
        "transaction_type": "Transaction",
    }
    return labels.get(field, field.replace("_", " ").title())


def _terms_line(p: Dict[str, Any]) -> str:
    transaction = _transaction(p.get("transaction_type"))
    parts: List[str] = []

    if transaction in {"RENT", "SALE_AND_RENT", "UNKNOWN"} and _known(p.get("rent_value")):
        parts.append(_format_indian_money(p.get("rent_value"), "/month"))

    if transaction in {"SALE", "SALE_AND_RENT", "UNKNOWN"} and _known(p.get("sale_price_value")):
        parts.append(_format_indian_money(p.get("sale_price_value")))

    if not parts:
        return "Commercial Terms: Not stated"

    if transaction == "RENT":
        return f"Rent: {parts[0]}"

    if transaction == "SALE":
        return f"Sale Price: {parts[0]}"

    if transaction == "SALE_AND_RENT":
        labelled = []
        if _known(p.get("sale_price_value")):
            labelled.append(f"Sale Price: {_format_indian_money(p.get('sale_price_value'))}")
        if _known(p.get("rent_value")):
            labelled.append(f"Rent: {_format_indian_money(p.get('rent_value'), '/month')}")
        return " | ".join(labelled)

    return " | ".join(parts)


def _detail_lines(p: Dict[str, Any]) -> List[str]:
    family = _family(p.get("property_family") or p.get("property_subtype"))
    lines: List[str] = []

    base = [
        ("Project / Building", p.get("project_name")),
        ("Configuration", p.get("configuration")),
        ("Area", f"{p.get('area_sqft')} sq ft" if _known(p.get("area_sqft")) else None),
        ("Floor", p.get("floor")),
        ("Furnishing", p.get("furnishing")),
    ]

    if family == "COMMERCIAL":
        base.extend(
            [
                ("Frontage", p.get("frontage")),
                ("Suitable Use", p.get("use_suitability")),
            ]
        )

    if family == "LAND":
        base.extend(
            [
                ("Plot Area", p.get("plot_area")),
                ("Road Width", p.get("road_width")),
                ("Zoning / Land Use", p.get("zoning")),
                ("Suitable Use", p.get("use_suitability")),
            ]
        )

    for label, value in base:
        if _known(value):
            lines.append(f"{label}: {value}")

    return lines


def build_description(p: Dict[str, Any], missing: Iterable[str]) -> str:
    subtype = _text(p.get("property_subtype"), _text(p.get("property_family"), "Property"))
    transaction = _text(p.get("transaction_type"), "Transaction")
    locality = _text(p.get("locality"))

    heading = f"{subtype} for {transaction} in {locality}"

    lines = [heading, ""]

    details = _detail_lines(p)
    if details:
        lines.extend(details)
        lines.append("")

    lines.append(_terms_line(p))
    lines.append("")

    contact_name = _text(p.get("contact_name"))
    phone_text = ", ".join(_phones(p.get("contact_numbers"))) or "Unknown"
    lines.append(f"Contact: {contact_name}")
    lines.append(f"Phone: {phone_text}")

    if _known(p.get("verification_status")):
        lines.append(f"Verification: {_text(p.get('verification_status'))}")
    else:
        lines.append("Verification: UNVERIFIED")

    if _known(p.get("overall_confidence")):
        try:
            confidence = float(p.get("overall_confidence"))
            if confidence <= 1:
                confidence *= 100
            lines.append(f"Source Confidence: {confidence:.0f}%")
        except Exception:
            lines.append(f"Source Confidence: {_text(p.get('overall_confidence'))}")

    missing_list = list(missing)
    lines.append("")
    if missing_list:
        lines.append("Still Required:")
        for field in missing_list:
            lines.append(f"- {_label(field)}")
    else:
        lines.append("Still Required: None")

    return "\n".join(lines).strip()


def _candidate_identity_matches(p: Dict[str, Any], o: Dict[str, Any]) -> bool:
    """
    Conservative identity rule:
    1. Exact project/building name match, OR
    2. Exact locality + same contact name, OR
    3. Exact locality + overlapping phone number.
    """
    p_project = str(p.get("project_name") or "").strip().upper()
    o_project = str(o.get("project_name") or "").strip().upper()

    if p_project and o_project and p_project == o_project:
        return True

    p_loc = str(p.get("locality") or "").strip().upper()
    o_loc = str(o.get("locality") or "").strip().upper()

    if not p_loc or not o_loc or p_loc != o_loc:
        return False

    p_contact = str(p.get("contact_name") or "").strip().upper()
    o_contact = str(o.get("contact_name") or "").strip().upper()

    if p_contact and o_contact and p_contact == o_contact:
        return True

    return bool(
        set(_phones(p.get("contact_numbers")))
        & set(_phones(o.get("contact_numbers")))
    )


def enrich_property(engine, property_id):
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT *
                FROM pb_canonical_properties
                WHERE property_id = :property_id
                """
            ),
            {"property_id": property_id},
        ).mappings().first()

    if not row:
        return {
            "status": "NOT_FOUND",
            "property_id": str(property_id),
            "version": VERSION,
        }

    p = dict(row)
    changed: Dict[str, Any] = {}

    provenance = {
        key: {"status": "SOURCE-STATED"}
        for key, value in p.items()
        if _known(value)
    }

    with engine.connect() as connection:
        # Keep candidate query broad but safe; final identity proof is done in Python.
        others = connection.execute(
            text(
                """
                SELECT *
                FROM pb_canonical_properties
                WHERE property_id <> :property_id
                  AND (
                        (
                            :project_name <> ''
                            AND UPPER(COALESCE(project_name, '')) = UPPER(:project_name)
                        )
                        OR
                        (
                            :locality <> ''
                            AND UPPER(COALESCE(locality, '')) = UPPER(:locality)
                        )
                      )
                ORDER BY
                    (verification_status = 'VERIFIED') DESC,
                    overall_confidence DESC NULLS LAST
                LIMIT 50
                """
            ),
            {
                "property_id": property_id,
                "project_name": str(p.get("project_name") or ""),
                "locality": str(p.get("locality") or ""),
            },
        ).mappings().all()

    for other_row in others:
        other = dict(other_row)

        if not _candidate_identity_matches(p, other):
            continue

        for field in CROSS_SOURCE_FIELDS:
            if not _known(p.get(field)) and _known(other.get(field)):
                p[field] = other[field]
                changed[field] = other[field]
                provenance[field] = {
                    "status": "CROSS-CONFIRMED",
                    "source_property_id": str(other.get("property_id")),
                }

    missing = _missing_fields(p)
    description = build_description(p, missing)

    # Preserve the V1 safety model:
    # only the generated human-readable description is persisted.
    # Cross-confirmed structured fields are returned for review but not written.
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE pb_canonical_properties
                SET clean_description = :description,
                    updated_at = NOW()
                WHERE property_id = :property_id
                """
            ),
            {
                "description": description,
                "property_id": property_id,
            },
        )

    return {
        "status": "ENRICHED",
        "version": VERSION,
        "property_id": str(property_id),
        "changed": changed,
        "changed_persisted_to_structured_fields": False,
        "missing_critical": missing,
        "provenance": provenance,
        "description": description,
        "public_enrichment": "NOT_RUN",
        "raw_source_mutation": False,
    }
