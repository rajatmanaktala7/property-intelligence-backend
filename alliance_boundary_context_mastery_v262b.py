from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional

from fastapi import Query
from fastapi.responses import JSONResponse

import alliance_property_evidence_grammar_v257b as v257b
import alliance_real_mastery_v262a as v262a

VERSION = "2.6.2B1-BOUNDARY-CONTEXT-MASTERY-FIX"
MODE = "READ_ONLY_HIERARCHICAL_BOUNDARY_CONTEXT_SHADOW"

PROPERTY_START_RE = re.compile(
    r"(?im)^(?:\s*[-*•👉✅📍🏠🏢]*\s*)?"
    r"(?:(?:BUNGALOW|VILLA|PLOT|SHOP|OFFICE|SHOWROOM|FLOOR|FLAT|APARTMENT|UNIT|PROPERTY)\s*(?:NO\.?\s*)?\d+"
    r"|(?:\d+\s*(?:BHK|BR))\b"
    r"|(?:\d[\d,]*\s*(?:SQFT|SQ\s*FT|SFT|SQYD|SQ\s*YD|SYDS|YARDS|GAJ|SQM|SQ\s*M|MTR)\b))"
)
EXPLICIT_NUMBERED_RE = re.compile(
    r"(?im)^\s*(?:[-*•]\s*)?(?:BUNGALOW|VILLA|PLOT|SHOP|OFFICE|SHOWROOM|FLOOR|FLAT|APARTMENT|UNIT|PROPERTY)\s*(?:NO\.?\s*)?\d+\b"
)
BHK_RE = re.compile(r"\b(\d+)\s*(?:BHK|BR)\b", re.I)
AREA_RE = re.compile(
    r"\b(\d[\d,]*(?:\.\d+)?)\s*(SQFT|SQ\s*FT|SFT|SQYD|SQ\s*YD|SYDS|YARDS|GAJ|SQM|SQ\s*M|MTR|CARPET)\b",
    re.I,
)
SALE_MONEY_RE = re.compile(r"(?:₹|RS\.?\s*)?(\d+(?:\.\d+)?)\s*(CR|CRORE)\b", re.I)
RENT_MONEY_RE = re.compile(r"(?:₹|RS\.?\s*)?(\d+(?:\.\d+)?)\s*(L|LAC|LAKH|LACS|LAKHS|K)\b", re.I)
RENT_SIGNAL_RE = re.compile(r"\b(?:RENT|RENTAL|FOR\s+RENT|LEASE)\b", re.I)
SALE_SIGNAL_RE = re.compile(r"\b(?:SALE|FOR\s+SALE|DEMAND|ASKING\s+PRICE|PRICE)\b", re.I)
REQUIREMENT_RE = re.compile(r"\b(?:LOOKING\s+FOR|REQUIRED|WANTED|NEED(?:ED)?|DIRECT\s+CLIENT|RENTAL\s+REQUIREMENT|PURCHASE\s+REQUIREMENT)\b", re.I)
INCIDENTAL_USE_RE = re.compile(r"\b(?:IDEAL\s+FOR|SUITABLE\s+FOR|BEST\s+FOR|PERFECT\s+FOR|CAN\s+BE\s+USED\s+FOR)\b", re.I)

LOCATION_HINT_RE = re.compile(
    r"\b(?:DLF\s+PHASE\s+\d|SUSHANT\s+LOK\s*\d*|DWARKA|KALKAJI|SAKET|NOIDA|GURGAON|GURUGRAM|"
    r"VAGATOR|ANJUNA|SIOLIM|ASSAGAO|LOKHANDWALA(?:\s+BACK\s+ROAD)?|JUHU|SECTOR\s+\d+[A-Z]?)\b",
    re.I,
)

# Only explicit project/location-style headers. Never use a generic all-caps rule.
PROJECT_SIGNAL_RE = re.compile(
    r"\b(?:DLF|EMAAR|M3M|RAHEJA|ACROPOLIS|ARIA|LEGACY|GRANDEUR|BELVEDERE|URBAN\s+OASIS|"
    r"LOKHANDWALA|SUSHANT\s+LOK|SECTOR\s+\d+[A-Z]?)\b",
    re.I,
)

DESCRIPTOR_ONLY_RE = re.compile(
    r"^(?:FULLY|SEMI)?\s*FURNISHED$|^UNFURNISHED$|^READY\s+TO\s+MOVE$|^PARK\s+FACING$|"
    r"^NORTH\s+FACING$|^SOUTH\s+FACING$|^EAST\s+FACING$|^WEST\s+FACING$|"
    r"^WITH\s+LIFT$|^WITH\s+PARKING$|^NEGOTIABLE$",
    re.I,
)

def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()

def _money_value(token: str, unit: str) -> Optional[float]:
    try:
        v = float(token.replace(",", ""))
    except Exception:
        return None
    u = unit.upper()
    if u in {"CR", "CRORE"}:
        return v * 10000000
    if u in {"L", "LAC", "LAKH", "LACS", "LAKHS"}:
        return v * 100000
    if u == "K":
        return v * 1000
    return None

def _line_location(line: str) -> Optional[str]:
    m = LOCATION_HINT_RE.search(line or "")
    return _norm(m.group(0)).title() if m else None

def _looks_header(line: str) -> bool:
    s = _norm(line)
    if not s or len(s) > 80:
        return False
    if DESCRIPTOR_ONLY_RE.match(s):
        return False
    if BHK_RE.search(s) or AREA_RE.search(s) or SALE_MONEY_RE.search(s) or RENT_MONEY_RE.search(s):
        return False
    if RENT_SIGNAL_RE.search(s) or SALE_SIGNAL_RE.search(s) or REQUIREMENT_RE.search(s):
        return False
    if EXPLICIT_NUMBERED_RE.search(s):
        return True
    if _line_location(s):
        return True
    return bool(PROJECT_SIGNAL_RE.search(s))

def _split_blocks(text_value: str) -> List[Dict[str, Any]]:
    raw = str(text_value or "").replace("\r\n", "\n").replace("\r", "\n")
    parts: List[str] = []
    for chunk in raw.split("\n"):
        parts.extend(chunk.split("|"))
    lines = [_norm(x) for x in parts if _norm(x)]

    blocks: List[Dict[str, Any]] = []
    current: List[str] = []
    inherited_location: Optional[str] = None
    inherited_header: Optional[str] = None

    def flush():
        nonlocal current
        if current:
            blocks.append({
                "lines": current[:],
                "inherited_location": inherited_location,
                "inherited_header": inherited_header,
            })
            current = []

    for line in lines:
        if _looks_header(line):
            # Numbered property/bungalow headers start a child property.
            if EXPLICIT_NUMBERED_RE.search(line):
                flush()
                inherited_header = line
                continue

            # Location/project headers define context but are not property rows.
            flush()
            loc = _line_location(line)
            if loc:
                inherited_location = loc
            inherited_header = line
            continue

        explicit_start = bool(EXPLICIT_NUMBERED_RE.search(line))
        if explicit_start and current:
            flush()
        elif PROPERTY_START_RE.search(line) and current:
            joined = " ".join(current)
            # Conservative split: only split a non-numbered row when the current
            # row already has configuration + area + commercial evidence.
            if BHK_RE.search(joined) and AREA_RE.search(joined) and (
                SALE_MONEY_RE.search(joined) or RENT_MONEY_RE.search(joined)
            ):
                flush()

        current.append(line)

    flush()
    return blocks

def _interpret_block(block: Dict[str, Any]) -> Dict[str, Any]:
    text_value = " | ".join(block.get("lines") or [])
    locations = [_norm(x.group(0)).title() for x in LOCATION_HINT_RE.finditer(text_value)]
    location = locations[0] if locations else block.get("inherited_location")

    bhks = [int(x) for x in BHK_RE.findall(text_value)]
    areas = []
    for num, unit in AREA_RE.findall(text_value):
        try:
            areas.append({"value": float(num.replace(",", "")), "unit": _norm(unit).upper()})
        except Exception:
            pass

    sale_values = []
    for num, unit in SALE_MONEY_RE.findall(text_value):
        v = _money_value(num, unit)
        if v is not None:
            sale_values.append(v)

    rent_values = []
    for num, unit in RENT_MONEY_RE.findall(text_value):
        v = _money_value(num, unit)
        if v is not None:
            rent_values.append(v)

    rent_signal = bool(RENT_SIGNAL_RE.search(text_value))
    sale_signal = bool(SALE_SIGNAL_RE.search(text_value) or sale_values)
    requirement_signal = bool(REQUIREMENT_RE.search(text_value))
    incidental_use = bool(INCIDENTAL_USE_RE.search(text_value))

    classification = "REQUIREMENT" if requirement_signal and not incidental_use else "AVAILABILITY"

    if rent_signal and sale_signal:
        tx = None
        offer_state = "MULTIPLE_OFFERS_UNRESOLVED"
    elif rent_signal:
        tx = "RENT"
        offer_state = "SINGLE_RENT_OFFER"
    elif sale_signal:
        tx = "SALE"
        offer_state = "SINGLE_SALE_OFFER"
    else:
        tx = None
        offer_state = "TRANSACTION_UNRESOLVED"

    blockers = []
    if len(sale_values) > 1 or len(rent_values) > 1:
        blockers.append("MULTIPLE_MONEY_VALUES_REVIEW")
    if tx is None and classification == "AVAILABILITY":
        blockers.append("TRANSACTION_UNRESOLVED")
    if not location:
        blockers.append("LOCATION_UNRESOLVED")

    return {
        "text": text_value,
        "classification": classification,
        "transaction": tx,
        "offer_state": offer_state,
        "location": location,
        "inherited_header": block.get("inherited_header"),
        "configurations": sorted(set(bhks)),
        "areas": areas,
        "sale_values": sale_values,
        "rent_values": rent_values,
        "blockers": sorted(set(blockers)),
        "database_write": False,
    }

def reconstruct(text_value: str) -> Dict[str, Any]:
    blocks = _split_blocks(text_value)
    entities = [_interpret_block(b) for b in blocks]

    for e in entities:
        if len(e["configurations"]) > 1 and (
            len(e["areas"]) > 1 or len(e["sale_values"]) > 1 or len(e["rent_values"]) > 1
        ):
            if "BOUNDARY_REVIEW_REQUIRED" not in e["blockers"]:
                e["blockers"].append("BOUNDARY_REVIEW_REQUIRED")

    return {
        "entity_count": len(entities),
        "entities": entities,
        "safe_auto_split": bool(
            entities and all("BOUNDARY_REVIEW_REQUIRED" not in e["blockers"] for e in entities)
        ),
        "canonical_writes": 0,
        "offer_writes": 0,
    }

REGRESSION_CASES = [
    {
        "key": "LOKHANDWALA_THREE_BUNGALOWS",
        "text": (
            "LOKHANDWALA BACK ROAD | "
            "BUNGALOW 0 | 6250 Carpet | 4 BHK + Mini Theatre | 35 Cr Negotiable | "
            "BUNGALOW 1 | 3854 Carpet | 5 BHK | "
            "BUNGALOW 2 | 2656 Carpet | 6 BHK | Both Bungalows 70 Cr Negotiable"
        ),
        "check": lambda r: r["entity_count"] == 3,
    },
    {
        "key": "HEADER_CONTEXT_INHERITANCE",
        "text": (
            "DLF PHASE 2 | "
            "PROPERTY 1 | 400 SYDS | 4 BHK | RENT 1.60 LAC | "
            "PROPERTY 2 | 500 SYDS | 5 BHK | RENT 2.20 LAC"
        ),
        "check": lambda r: r["entity_count"] == 2 and all(
            e["location"] == "Dlf Phase 2" for e in r["entities"]
        ),
    },
    {
        "key": "IDEAL_FOR_CLINICS_NOT_REQUIREMENT",
        "text": (
            "DWARKA | 3200 SQ FT Carpet Area | Excellent visibility | "
            "Ideal for Doctors Clinics Aesthetic Wellness Centres | Available"
        ),
        "check": lambda r: r["entity_count"] == 1 and r["entities"][0]["classification"] == "AVAILABILITY",
    },
    {
        "key": "REAL_REQUIREMENT_STAYS_REQUIREMENT",
        "text": "Looking for 3000 SQFT in Saket for restaurant budget 5 Lakh",
        "check": lambda r: r["entity_count"] == 1 and r["entities"][0]["classification"] == "REQUIREMENT",
    },
    {
        "key": "MIXED_RENT_SALE_STAYS_UNRESOLVED",
        "text": "NOIDA | PROPERTY 1 | 130 SQYD | Rent 2 Lac | Demand 3.80 Cr",
        "check": lambda r: r["entities"][0]["transaction"] is None and r["entities"][0]["offer_state"] == "MULTIPLE_OFFERS_UNRESOLVED",
    },
    {
        "key": "SINGLE_PROPERTY_NOT_OVER_SPLIT",
        "text": "DLF PHASE 2 | 4 BHK | 400 SYDS | Fully Furnished | Rent 1.60 Lac",
        "check": lambda r: (
            r["entity_count"] == 1
            and r["entities"][0]["transaction"] == "RENT"
            and r["entities"][0]["location"] == "Dlf Phase 2"
            and r["entities"][0]["configurations"] == [4]
            and len(r["entities"][0]["areas"]) == 1
            and len(r["entities"][0]["rent_values"]) == 1
        ),
    },
    {
        "key": "DESCRIPTOR_LINES_NEVER_HEADERS",
        "text": "SAKET | 2000 SQFT | Fully Furnished | Ready to Move | For Rent | 4 Lakh",
        "check": lambda r: r["entity_count"] == 1 and r["entities"][0]["transaction"] == "RENT",
    },
]

def regression() -> Dict[str, Any]:
    results = []
    for case in REGRESSION_CASES:
        r = reconstruct(case["text"])
        passed = bool(case["check"](r))
        results.append({"case_key": case["key"], "passed": passed, "result": r})
    total = len(results)
    passed = sum(1 for x in results if x["passed"])
    return {
        "status": "PASS" if passed == total else "FAIL",
        "version": VERSION,
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "score": round(100.0 * passed / total, 2) if total else 0.0,
        "critical_failures": total - passed,
        "results": results,
        "writes_performed": 0,
    }

def real_exam(engine, limit: int = 500) -> Dict[str, Any]:
    upstream = v257b._benchmark(engine, limit)
    stats = Counter()
    examples = []
    real_candidates = 0
    reconstructed_entities = 0

    for burst in upstream.get("bursts") or []:
        for candidate in burst.get("candidates") or []:
            real_candidates += 1
            raw = str(candidate.get("own_text_redacted") or "")
            r = reconstruct(raw)
            reconstructed_entities += r["entity_count"]

            upstream_integrity = ((candidate.get("v257b") or {}).get("record_integrity") or {}).get("class")

            if upstream_integrity == "MULTIPLE_PROPERTIES_OR_MERGED":
                stats["UPSTREAM_MERGED_RECORDS"] += 1
                if r["entity_count"] > 1:
                    stats["SHADOW_MULTI_ENTITY_RECOVERIES"] += 1
                else:
                    stats["MERGED_RECORDS_STILL_UNSPLIT"] += 1

            for e in r["entities"]:
                if e["inherited_header"] and e["location"]:
                    stats["CONTEXT_INHERITANCE_USED"] += 1
                if "BOUNDARY_REVIEW_REQUIRED" in e["blockers"]:
                    stats["BOUNDARY_REVIEW_REQUIRED"] += 1
                if e["classification"] == "REQUIREMENT":
                    stats["REQUIREMENT_ENTITIES"] += 1
                else:
                    stats["AVAILABILITY_ENTITIES"] += 1

            if (
                upstream_integrity == "MULTIPLE_PROPERTIES_OR_MERGED"
                or any("BOUNDARY_REVIEW_REQUIRED" in e["blockers"] for e in r["entities"])
            ) and len(examples) < 100:
                examples.append({
                    "upstream_classification": candidate.get("classification"),
                    "upstream_transaction": candidate.get("transaction"),
                    "upstream_location": candidate.get("location"),
                    "upstream_integrity": upstream_integrity,
                    "raw": raw,
                    "reconstruction": r,
                })

    reg = regression()
    v262a_reg = v262a.regression()
    gate = bool(reg["critical_failures"] == 0 and v262a_reg["critical_failures"] == 0)

    return {
        "status": "PASS" if gate else "TRAINING_REQUIRED",
        "version": VERSION,
        "requested_limit": limit,
        "real_candidates_examined": real_candidates,
        "shadow_entities_reconstructed": reconstructed_entities,
        "stats": dict(stats),
        "examples": examples,
        "boundary_regression_score": reg["score"],
        "boundary_critical_failures": reg["critical_failures"],
        "v262a_score": v262a_reg["score"],
        "v262a_critical_failures": v262a_reg["critical_failures"],
        "mastery_gate_passed": gate,
        "read_only": True,
        "canonical_writes": 0,
        "offer_writes": 0,
        "matcher_writes": 0,
        "whatsapp_live_writes": 0,
        "claim": "PASS means known V262A and V262B1 regressions pass. Real unsplit cases remain curriculum, not auto-approved writes.",
    }

def register(core):
    app = core.app
    engine = core.engine
    route = "/api/v7/property-ai/mastery-v262b/status"

    if any(getattr(r, "path", None) == route for r in app.router.routes):
        return {"status": "ALREADY_REGISTERED", "version": VERSION, "route": route}

    @app.get(route)
    def status():
        return JSONResponse({
            "status": "READY",
            "version": VERSION,
            "mode": MODE,
            "read_only": True,
            "canonical_writes": 0,
            "offer_writes": 0,
            "matcher_modified": False,
            "whatsapp_live_modified": False,
        })

    @app.get("/api/v7/property-ai/mastery-v262b/regression")
    def reg():
        return JSONResponse(regression())

    @app.get("/api/v7/property-ai/mastery-v262b/exam")
    def exam(limit: int = Query(500, ge=1, le=1000)):
        return JSONResponse(real_exam(engine, limit))

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": route,
        "regression": "/api/v7/property-ai/mastery-v262b/regression",
        "exam": "/api/v7/property-ai/mastery-v262b/exam?limit=500",
    }
