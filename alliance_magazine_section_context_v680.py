from __future__ import annotations
import re
from typing import Any, Dict, List

import alliance_magazine_lossless_extraction_v670 as lossless_v670

VERSION = "6.8.0-ALLIANCE-MAGAZINE-SECTION-CONTEXT"
MODE = "LOSSLESS_PLUS_SECTION_HEADING_PROPERTY_CATEGORY_TRANSACTION_INHERITANCE"

TRAINING_RULES = lossless_v670.TRAINING_RULES + """

ALLIANCE 7.3.6 SECTION CONTEXT STANDARD

13. Read the page hierarchy, not only the individual property row.
14. A section heading can carry PROPERTY CATEGORY and TRANSACTION context for every property row below it.
15. Example: COMMERCIAL - RENT means:
    property_category = COMMERCIAL
    transaction_type = RENT
16. Example: COMMERCIAL - SALE means:
    property_category = COMMERCIAL
    transaction_type = SALE
17. Example: RESIDENTIAL - RENT means:
    property_category = RESIDENTIAL
    transaction_type = RENT
18. Example: RESIDENTIAL - SALE means:
    property_category = RESIDENTIAL
    transaction_type = SALE
19. The section context continues until another visible section heading changes it.
20. Locality headings such as CONNAUGHT PLACE are separate from property category / transaction headings.
21. Never overwrite the exact property row with the inherited heading. Preserve the row verbatim in original_description.
22. Return section_heading with each property record so source hierarchy remains auditable.
23. If a visible section heading clearly supplies category/transaction but the record omits them, mark needs_review=true.
24. Never infer COMMERCIAL or RESIDENTIAL from the address alone when no section heading or row text supports it.

CANONICAL SECTION TRAINING EXAMPLE
Section heading: COMMERCIAL - RENT
Locality heading: CONNAUGHT PLACE
Property row: A-7 INNER CIRCLE 7500FT FF+SF+TF (KAPIL 011 41550460)

Correct:
section_heading = COMMERCIAL - RENT
property_category = COMMERCIAL
transaction_type = RENT
locality = Connaught Place
address = A-7, Inner Circle
area_sqft = 7500
floors = First Floor, Second Floor, Third Floor
contact_name = Kapil
phones = 01141550460
original_description = exact property row only
"""

VISION_SCHEMA_PROMPT = """
Return JSON:
{"records":[{"ref":"","section_heading":"","raw_line":"","original_description":"","property_category":"","transaction_type":"","address":"","locality":"","city":"","area_raw":"","area_sqft":null,"floor_codes":"","floors":[],"contact_name":"","phones":[],"confidence":0.0,"needs_review":false,"review_reason":""}]}

Mandatory:
- section_heading = the visible category/transaction heading governing the row, e.g. COMMERCIAL - RENT.
- property_category must inherit COMMERCIAL / RESIDENTIAL / INDUSTRIAL / HOSPITALITY / RETAIL / OFFICE when clearly stated by the governing section heading.
- transaction_type must inherit RENT / SALE / LEASE when clearly stated by the governing section heading.
- locality is the locality heading such as CONNAUGHT PLACE, not a substitute for address.
- raw_line and original_description are the exact complete property row only, not the section heading.
- address is the specific property/unit/building/street identifier from the row.
- preserve visible digits, names and phones exactly.
- if the governing heading is clear but category or transaction is blank, needs_review=true.
- never invent uncertain words or digits.
"""

CATEGORY_ALIASES = {
    "COMMERCIAL": "COMMERCIAL",
    "COMM": "COMMERCIAL",
    "RESIDENTIAL": "RESIDENTIAL",
    "RESI": "RESIDENTIAL",
    "INDUSTRIAL": "INDUSTRIAL",
    "HOSPITALITY": "HOSPITALITY",
    "HOTEL": "HOSPITALITY",
    "RETAIL": "RETAIL",
    "OFFICE": "OFFICE",
}
TRANSACTION_ALIASES = {
    "RENT": "RENT",
    "RENTAL": "RENT",
    "LEASE": "LEASE",
    "SALE": "SALE",
    "SELL": "SALE",
}

def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()

def parse_section_heading(section_heading: str) -> Dict[str, str]:
    s = _clean(section_heading).upper()
    category = ""
    transaction = ""
    for token, canonical in CATEGORY_ALIASES.items():
        if re.search(rf"\b{re.escape(token)}\b", s):
            category = canonical
            break
    for token, canonical in TRANSACTION_ALIASES.items():
        if re.search(rf"\b{re.escape(token)}\b", s):
            transaction = canonical
            break
    return {
        "section_heading": _clean(section_heading),
        "property_category": category,
        "transaction_type": transaction,
    }

def quality_gate(record: Dict[str, Any]) -> Dict[str, Any]:
    out = lossless_v670.quality_gate(record)
    reasons = [x for x in str(out.get("review_reason") or "").split(";") if x]
    ctx = parse_section_heading(out.get("section_heading") or "")
    if ctx["property_category"] and not _clean(out.get("property_category") or ""):
        reasons.append("SECTION_CATEGORY_VISIBLE_BUT_MISSING")
    if ctx["transaction_type"] and not _clean(out.get("transaction_type") or ""):
        reasons.append("SECTION_TRANSACTION_VISIBLE_BUT_MISSING")
    out["needs_review"] = bool(reasons)
    out["review_reason"] = ";".join(dict.fromkeys(reasons))
    out["quality_status"] = "FAIL_REEXTRACT" if reasons else "PASS"
    return out

def enrich_record(
    record: Dict[str, Any],
    inherited_locality: str = "",
    inherited_transaction: str = "",
    inherited_property_category: str = "",
    inherited_section_heading: str = "",
) -> Dict[str, Any]:
    out = dict(record or {})
    section_heading = _clean(out.get("section_heading") or inherited_section_heading)
    ctx = parse_section_heading(section_heading)

    tx = _clean(out.get("transaction_type") or inherited_transaction or ctx["transaction_type"]).upper()
    cat = _clean(out.get("property_category") or inherited_property_category or ctx["property_category"]).upper()

    out["section_heading"] = section_heading
    out["transaction_type"] = tx
    out["property_category"] = cat

    out = lossless_v670.enrich_record(
        out,
        inherited_locality=inherited_locality,
        inherited_transaction=tx,
    )
    out["section_heading"] = section_heading
    out["property_category"] = cat
    out["transaction_type"] = tx
    return quality_gate(out)

def canonical_example() -> Dict[str, Any]:
    return enrich_record(
        {
            "ref": "A-7",
            "section_heading": "COMMERCIAL - RENT",
            "raw_line": "A-7 INNER CIRCLE 7500FT FF+SF+TF (KAPIL 011 41550460)",
        },
        inherited_locality="Connaught Place",
    )

def self_test() -> Dict[str, Any]:
    got = canonical_example()
    checks = {
        "section_heading": got.get("section_heading") == "COMMERCIAL - RENT",
        "property_category": got.get("property_category") == "COMMERCIAL",
        "transaction_type": got.get("transaction_type") == "RENT",
        "address": got.get("address") == "A-7, Inner Circle",
        "locality": got.get("locality") == "Connaught Place",
        "area": got.get("area_sqft") == 7500,
        "floor_codes": got.get("floor_codes") == "FF+SF+TF",
        "contact_name": got.get("contact_name") == "Kapil",
        "phones": got.get("phones") == ["01141550460"],
        "raw_preserved": got.get("original_description") == "A-7 INNER CIRCLE 7500FT FF+SF+TF (KAPIL 011 41550460)",
        "quality_pass": got.get("quality_status") == "PASS",
    }
    return {
        "version": VERSION,
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "example": got,
    }

if __name__ == "__main__":
    import json
    print(json.dumps(self_test(), ensure_ascii=False, indent=2))
