from __future__ import annotations
import re
from typing import Any, Dict, List

VERSION = "6.7.0-ALLIANCE-MAGAZINE-LOSSLESS-EXTRACTION"
MODE = "LOSSLESS_FIRST_ADDRESS_LOCALITY_FLOOR_CONTACT_BINDING_QUALITY_GATE"

TRAINING_RULES = """
ALLIANCE MAGAZINE / NEWSPAPER LOSSLESS EXTRACTION STANDARD

1. Read each property row as one complete property entity.
2. Preserve the exact visible row in raw_line and original_description.
3. Keep address, locality and city as separate fields.
4. A section heading such as CONNAUGHT PLACE is locality context for following rows.
5. A row beginning A-7 / B-306 / G-3 / K-17 / P-15 / D-12 / SHOP NO-20 contains a specific property address/unit identifier.
6. Do not replace a specific address with only the parent locality.
7. FF=First Floor, SF=Second Floor, TF=Third Floor, GF=Ground Floor, UGF=Upper Ground Floor, BMT=Basement, MEZZ=Mezzanine, TERR=Terrace.
8. Text in brackets belongs to that property row unless visual evidence proves otherwise. Extract contact name and row-owned phone numbers.
9. Do not use masthead/header broker contacts as listing-owned contacts unless printed in that property row.
10. If the row visibly contains an address identifier but extracted address is blank, FAIL extraction quality and re-read.
11. If raw_line/original_description is blank, FAIL extraction quality.
12. Never invent unreadable digits or words.

CANONICAL TRAINING EXAMPLE
Section: CONNAUGHT PLACE
Row: A-7 INNER CIRCLE 7500FT FF+SF+TF (KAPIL 011 41550460)

Correct:
address = A-7, Inner Circle
locality = Connaught Place
area_sqft = 7500
area_raw = 7500FT
floor_codes = FF+SF+TF
floors = First Floor, Second Floor, Third Floor
contact_name = Kapil
phones = 01141550460
original_description = exact complete row
transaction_type = RENT when inherited from COMMERCIAL - RENT
"""

VISION_SCHEMA_PROMPT = """
Return JSON:
{"records":[{"ref":"","raw_line":"","original_description":"","address":"","locality":"","city":"","area_raw":"","area_sqft":null,"floor_codes":"","floors":[],"contact_name":"","phones":[],"transaction_type":"","confidence":0.0,"needs_review":false,"review_reason":""}]}

Mandatory:
- raw_line and original_description must be the complete visible property row.
- address is the specific property/unit/building/street identifier from the row.
- locality is inherited section/locality context, not a substitute for address.
- preserve A-7/B-306/G-3/K-17/P-15/D-12/SHOP NO style identifiers.
- if address-like evidence is visible but address is blank, needs_review=true.
- never invent uncertain digits.
"""

FLOOR_MAP = {
    "GF": "Ground Floor",
    "UGF": "Upper Ground Floor",
    "FF": "First Floor",
    "SF": "Second Floor",
    "TF": "Third Floor",
    "BMT": "Basement",
    "MEZZ": "Mezzanine",
    "TERR": "Terrace",
}

ADDRESS_TOKEN = re.compile(r"^\s*((?:SHOP\s*NO[-\s]*\d+[A-Z]?|[A-Z]-?\d+[A-Z]?(?:\s*&\s*\d+[A-Z]?)?))\b", re.I)
AREA_RE = re.compile(r"\b(\d{2,7}(?:\.\d+)?)\s*(?:SQ\.?\s*FT|SQFT|SFT|SF|FT)\b", re.I)
PHONE_RE = re.compile(r"(?<!\d)(0\d{10}|[6-9]\d{9})(?!\d)")
FLOOR_RE = re.compile(r"\b(UGF|BMT|MEZZ|TERR|GF|FF|SF|TF)\b", re.I)

def _clean_space(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()

def parse_floors(raw_line: str) -> Dict[str, Any]:
    codes = []
    for x in FLOOR_RE.findall(raw_line or ""):
        u = x.upper()
        if u not in codes:
            codes.append(u)
    return {"floor_codes": "+".join(codes), "floors": [FLOOR_MAP[x] for x in codes]}

def parse_area(raw_line: str) -> Dict[str, Any]:
    m = AREA_RE.search(raw_line or "")
    if not m:
        return {"area_raw": "", "area_sqft": None}
    val = float(m.group(1))
    if val.is_integer():
        val = int(val)
    return {"area_raw": m.group(0), "area_sqft": val}

def parse_phones(raw_line: str) -> List[str]:
    compact = re.sub(r"(?<=\d)[\s-]+(?=\d)", "", raw_line or "")
    return list(dict.fromkeys(PHONE_RE.findall(compact)))

def parse_contact_name(raw_line: str) -> str:
    for chunk in reversed(re.findall(r"\(([^()]*)\)", raw_line or "")):
        if not parse_phones(chunk):
            continue
        name = re.sub(r"0?\d[\d\s-]{7,}\d", " ", chunk)
        name = re.sub(r"[^A-Za-z .&'-]", " ", name)
        name = _clean_space(name).strip(" -/,")
        if name:
            return name.title()
    return ""

def parse_address(raw_line: str) -> str:
    s = _clean_space(raw_line)
    m = ADDRESS_TOKEN.search(s)
    if not m:
        return ""
    stops = []
    for pat in [AREA_RE, FLOOR_RE, re.compile(r"\s+\("), re.compile(r"\s+@\s*")]:
        mm = pat.search(s, m.end())
        if mm:
            stops.append(mm.start())
    end = min(stops) if stops else len(s)
    addr = s[m.start():end].strip(" ,;-")
    addr = re.sub(r"\b(?:SALE|RENT|LEASE)\b.*$", "", addr, flags=re.I).strip(" ,;-")
    mm = ADDRESS_TOKEN.search(addr)
    if mm and mm.end() < len(addr):
        first = addr[:mm.end()].strip()
        rest = addr[mm.end():].strip(" ,;-")
        if rest:
            addr = first + ", " + rest
    return addr.title()

def quality_gate(record: Dict[str, Any]) -> Dict[str, Any]:
    raw = _clean_space(record.get("original_description") or record.get("raw_line") or "")
    reasons = []
    if not raw:
        reasons.append("ORIGINAL_DESCRIPTION_MISSING")
    if ADDRESS_TOKEN.search(raw) and not _clean_space(record.get("address") or ""):
        reasons.append("ADDRESS_VISIBLE_BUT_MISSING")
    if re.search(r"\([A-Za-z][^()]*\d{8,}", raw) and not _clean_space(record.get("contact_name") or ""):
        reasons.append("CONTACT_NAME_VISIBLE_BUT_MISSING")
    out = dict(record)
    out["needs_review"] = bool(reasons)
    out["review_reason"] = ";".join(reasons)
    out["quality_status"] = "FAIL_REEXTRACT" if reasons else "PASS"
    return out

def enrich_record(record: Dict[str, Any], inherited_locality: str = "", inherited_transaction: str = "") -> Dict[str, Any]:
    out = dict(record or {})
    raw = _clean_space(out.get("original_description") or out.get("raw_line") or "")
    out["raw_line"] = raw
    out["original_description"] = raw

    if not out.get("address"):
        out["address"] = parse_address(raw)
    if not out.get("locality") and inherited_locality:
        out["locality"] = inherited_locality
    if not out.get("transaction_type") and inherited_transaction:
        out["transaction_type"] = inherited_transaction

    area = parse_area(raw)
    if not out.get("area_raw"):
        out["area_raw"] = area["area_raw"]
    if out.get("area_sqft") in (None, ""):
        out["area_sqft"] = area["area_sqft"]

    fl = parse_floors(raw)
    if not out.get("floor_codes"):
        out["floor_codes"] = fl["floor_codes"]
    if not out.get("floors"):
        out["floors"] = fl["floors"]

    if not out.get("phones"):
        out["phones"] = parse_phones(raw)
    if not out.get("contact_name"):
        out["contact_name"] = parse_contact_name(raw)

    return quality_gate(out)

def canonical_example() -> Dict[str, Any]:
    return enrich_record(
        {"ref": "A-7", "raw_line": "A-7 INNER CIRCLE 7500FT FF+SF+TF (KAPIL 011 41550460)"},
        inherited_locality="Connaught Place",
        inherited_transaction="RENT",
    )

def self_test() -> Dict[str, Any]:
    got = canonical_example()
    checks = {
        "address": got.get("address") == "A-7, Inner Circle",
        "locality": got.get("locality") == "Connaught Place",
        "area": got.get("area_sqft") == 7500,
        "floor_codes": got.get("floor_codes") == "FF+SF+TF",
        "floors": got.get("floors") == ["First Floor","Second Floor","Third Floor"],
        "contact_name": got.get("contact_name") == "Kapil",
        "phones": got.get("phones") == ["01141550460"],
        "raw_preserved": got.get("original_description") == "A-7 INNER CIRCLE 7500FT FF+SF+TF (KAPIL 011 41550460)",
        "quality_pass": got.get("quality_status") == "PASS",
    }
    return {"version": VERSION, "status": "PASS" if all(checks.values()) else "FAIL", "checks": checks, "example": got}

if __name__ == "__main__":
    import json
    print(json.dumps(self_test(), ensure_ascii=False, indent=2))
