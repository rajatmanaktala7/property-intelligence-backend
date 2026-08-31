from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional

from fastapi import Query
from fastapi.responses import JSONResponse

import alliance_property_shadow_extraction_v24 as v24
import alliance_property_boundary_intelligence_v25 as v25
import alliance_property_boundary_cohesion_v251 as v251
import alliance_property_location_evidence_v253 as v253

VERSION = "2.5.4C-PROPERTY-HEADER-SCOPE-DIAGNOSTIC"
MODE = "READ_ONLY_STRUCTURAL_SCOPE_DIAGNOSTIC"

PHONE_RE = getattr(v24, "PHONE_RE", re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)"))
EMAIL_RE = getattr(v24, "EMAIL_RE", re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"))

CONFIG_RE = getattr(v25, "CONFIG_RE")
AREA_RE = getattr(v25, "AREA_RE")
MONEY_RE = getattr(v25, "MONEY_RE")
PROPERTY_RE = getattr(v25, "PROPERTY_RE")
RATE_RE = getattr(v25, "RATE_RE")

CONTACT_OR_FOOTER_RE = getattr(
    v251,
    "CONTACT_OR_FOOTER_RE",
    re.compile(r"(?i)^\s*(?:CONTACT|CALL|WHATSAPP|BROKER|CONSULTANT)\b"),
)
HARD_SECTION_RE = getattr(
    v251,
    "HARD_SECTION_RE",
    re.compile(r"(?i)^\s*(?:PREMIUM\s+)?(?:RENTAL|RENT|SALE|OUTRIGHT|COMMERCIAL)\b"),
)

# Broad geography is intentionally conservative. These are evidence recognizers,
# not an alias-expansion engine.
BROAD_LOCATION_RE = re.compile(
    r"(?i)\b(?:"
    r"KHAR\s+WEST|BANDRA\s+WEST|JUHU|JVPD|GULMOHAR\s+ROAD|"
    r"SANTACRUZ\s+WEST|ANDHERI\s+WEST|VILE\s+PARLE\s+WEST|"
    r"KALKAJI|SAKET|GREATER\s+KAILASH|GK[-\s]*[12]|"
    r"DWARKA(?:\s+SECTOR\s*\d+)?|NOIDA|GREATER\s+NOIDA|"
    r"GURGAON|GURUGRAM|SUSHANT\s*LOK(?:\s*1)?|SHUSHANT\s*LOK(?:\s*1)?|"
    r"DLF\s*PHASE\s*[1-5]"
    r")\b"
)

PROJECT_TOKEN_RE = re.compile(
    r"(?i)\b(?:"
    r"RUSTOMJEE|LODHA|OBEROI|RAHEJA|EMAAR|DLF|GODREJ|M3M|"
    r"PARAS|TATA|PRESTIGE|SOBHA|ATS|MAHINDRA|ADANI|KALPATARU|"
    r"RUNWAL|HIRANANDANI|PARK\s+GRANDEUR|ACROPOLIS|ARIA|"
    r"SHYAM\s+KUNJ|PARK\s+LAND|SHREEJI\s+KRUPA|KINARA|DLH"
    r")\b"
)

MICRO_LOCATION_RE = re.compile(
    r"(?i)\b(?:SECTOR\s*\d+|BLOCK\s*[A-Z0-9-]+|PHASE\s*[1-5]|"
    r"[A-Z]\s+BLOCK|ROAD|MARG|ENCLAVE|EXTENSION)\b"
)

SECTION_HINT_RE = re.compile(
    r"(?i)\b(?:PREMIUM|RENTAL\s+PROPERTIES|OUTRIGHT\s+PROPERTIES|"
    r"FOR\s+SALE|FOR\s+RENT|SALE|RENTALS?|COMMERCIAL|BUNGALOWS?)\b"
)


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()


def _redact(value: str) -> str:
    s = str(value or "")
    s = PHONE_RE.sub("[PHONE_REDACTED]", s)
    s = EMAIL_RE.sub("[EMAIL_REDACTED]", s)
    return _norm(s)


def _has_property_fact(text_value: str) -> bool:
    s = _norm(text_value)
    return bool(
        CONFIG_RE.search(s)
        or AREA_RE.search(s)
        or MONEY_RE.search(s)
        or PROPERTY_RE.search(s)
    )


def _fact_vector(text_value: str) -> Dict[str, bool]:
    s = _norm(text_value)
    return {
        "configuration": bool(CONFIG_RE.search(s)),
        "area": bool(AREA_RE.search(s)),
        "money": bool(MONEY_RE.search(s)),
        "property_type": bool(PROPERTY_RE.search(s)),
        "rate": bool(RATE_RE.search(s)),
    }


def _header_evidence(text_value: str) -> Dict[str, Any]:
    s = _norm(text_value).strip(" -*|:•▪🔹🔸✨")
    broad = BROAD_LOCATION_RE.search(s)
    project = PROJECT_TOKEN_RE.search(s)
    micro = MICRO_LOCATION_RE.search(s)

    if not s or len(s) > 120:
        return {"is_header": False, "header_type": None}

    if CONTACT_OR_FOOTER_RE.search(s):
        return {"is_header": False, "header_type": None}

    if v25._looks_like_section(s) or HARD_SECTION_RE.search(s):
        return {"is_header": False, "header_type": None}

    # A header must be structural, not already a full property row.
    if _has_property_fact(s):
        return {"is_header": False, "header_type": None}

    if broad and project:
        kind = "PROJECT_PLUS_LOCALITY_HEADER"
    elif project:
        kind = "PROJECT_HEADER"
    elif broad:
        kind = "LOCALITY_HEADER"
    elif micro:
        kind = "MICRO_LOCATION_HEADER"
    else:
        return {"is_header": False, "header_type": None}

    return {
        "is_header": True,
        "header_type": kind,
        "broad_location_evidence": broad.group(0) if broad else None,
        "project_evidence": project.group(0) if project else None,
        "micro_location_evidence": micro.group(0) if micro else None,
    }


def _piece_type(text_value: str) -> Dict[str, Any]:
    s = _norm(text_value)

    if not s:
        return {"type": "EMPTY"}

    if CONTACT_OR_FOOTER_RE.search(s):
        return {"type": "FOOTER"}

    if v25._looks_like_section(s) or HARD_SECTION_RE.search(s):
        return {"type": "SECTION"}

    h = _header_evidence(s)
    if h.get("is_header"):
        return {"type": "HEADER", **h}

    if _has_property_fact(s):
        return {"type": "PROPERTY_FACT", "facts": _fact_vector(s)}

    if SECTION_HINT_RE.search(s) and len(s) <= 100:
        return {"type": "SECTION_LIKE"}

    return {"type": "OTHER"}


def _scope_label(
    property_count: int,
    nested_header_count: int,
    terminated_by: Optional[str],
) -> str:
    if property_count == 0 and nested_header_count > 0:
        return "NESTED_HEADER_CHAIN"
    if property_count == 0:
        return "ORPHAN_HEADER"
    if property_count == 1:
        return "SINGLE_PROPERTY_SCOPE"
    if property_count > 1:
        return "MULTI_PROPERTY_SCOPE"
    return "AMBIGUOUS_SCOPE"


def _analyze_burst(row: Dict[str, Any]) -> Dict[str, Any]:
    burst_id = row.get("burst_group_id")
    pieces = v25._presegment(row.get("burst_text") or "")

    typed: List[Dict[str, Any]] = []
    for idx, raw in enumerate(pieces):
        redacted = _redact(raw)
        info = _piece_type(redacted)
        typed.append({
            "piece_index": idx + 1,
            "text_redacted": redacted,
            **info,
        })

    scopes: List[Dict[str, Any]] = []

    for idx, piece in enumerate(typed):
        if piece.get("type") != "HEADER":
            continue

        properties: List[Dict[str, Any]] = []
        nested_headers: List[Dict[str, Any]] = []
        intervening_other = 0
        terminated_by: Optional[str] = None
        termination_piece_index: Optional[int] = None

        for j in range(idx + 1, len(typed)):
            nxt = typed[j]
            nxt_type = nxt.get("type")

            if nxt_type in ("SECTION", "FOOTER"):
                terminated_by = nxt_type
                termination_piece_index = nxt.get("piece_index")
                break

            if nxt_type == "HEADER":
                nested_headers.append({
                    "piece_index": nxt.get("piece_index"),
                    "header_type": nxt.get("header_type"),
                    "text_redacted": nxt.get("text_redacted"),
                })
                # A new broad/project header is a hard competing scope boundary.
                terminated_by = "NEXT_HEADER"
                termination_piece_index = nxt.get("piece_index")
                break

            if nxt_type == "PROPERTY_FACT":
                properties.append({
                    "piece_index": nxt.get("piece_index"),
                    "text_redacted": nxt.get("text_redacted"),
                    "facts": nxt.get("facts") or {},
                })
                continue

            if nxt_type in ("OTHER", "SECTION_LIKE"):
                intervening_other += 1
                # Keep diagnostic window open across at most one weak text item.
                # This is observation only, not inheritance.
                if intervening_other > 1:
                    terminated_by = "WEAK_TEXT_GAP"
                    termination_piece_index = nxt.get("piece_index")
                    break

        label = _scope_label(len(properties), len(nested_headers), terminated_by)

        scopes.append({
            "burst_group_id": burst_id,
            "header_piece_index": piece.get("piece_index"),
            "header_type": piece.get("header_type"),
            "header_text_redacted": piece.get("text_redacted"),
            "broad_location_evidence": piece.get("broad_location_evidence"),
            "project_evidence": piece.get("project_evidence"),
            "micro_location_evidence": piece.get("micro_location_evidence"),
            "scope_label": label,
            "property_rows_before_boundary": len(properties),
            "property_rows": properties[:8],
            "property_rows_truncated": max(0, len(properties) - 8),
            "nested_headers_before_boundary": nested_headers[:4],
            "terminated_by": terminated_by,
            "termination_piece_index": termination_piece_index,
            "intervening_other_count": intervening_other,
            "association_applied": False,
            "database_write": False,
            "llm_used": False,
        })

    type_counts = Counter(x.get("type") for x in typed)

    return {
        "burst_group_id": burst_id,
        "piece_count": len(typed),
        "piece_type_counts": dict(type_counts),
        "scope_count": len(scopes),
        "scopes": scopes,
    }


def _diagnose(engine, limit: int) -> Dict[str, Any]:
    rows = v24._load_bursts(engine, limit)
    base = v253._benchmark(engine, limit)

    bursts: List[Dict[str, Any]] = []
    all_scopes: List[Dict[str, Any]] = []

    for row in rows:
        result = _analyze_burst(row)
        bursts.append(result)
        all_scopes.extend(result.get("scopes") or [])

    labels = Counter(x.get("scope_label") for x in all_scopes)
    header_types = Counter(x.get("header_type") for x in all_scopes)

    multi = [x for x in all_scopes if x.get("scope_label") == "MULTI_PROPERTY_SCOPE"]
    single = [x for x in all_scopes if x.get("scope_label") == "SINGLE_PROPERTY_SCOPE"]
    orphan = [x for x in all_scopes if x.get("scope_label") == "ORPHAN_HEADER"]
    nested = [x for x in all_scopes if x.get("scope_label") == "NESTED_HEADER_CHAIN"]

    base_counts = base.get("counts") or {}

    return {
        "status": "READY",
        "version": VERSION,
        "mode": MODE,
        "counts": {
            "burst_sample_size": len(rows),
            "v253_entity_count": base_counts.get("entity_count"),
            "v253_clean": base_counts.get("v253_clean"),
            "v253_location_missing": base_counts.get("location_missing_after"),
            "headers_detected": len(all_scopes),
            "single_property_scopes": len(single),
            "multi_property_scopes": len(multi),
            "orphan_headers": len(orphan),
            "nested_header_chains": len(nested),
            "properties_inside_multi_scopes": sum(
                int(x.get("property_rows_before_boundary") or 0) for x in multi
            ),
            "association_applied": 0,
            "database_writes": 0,
            "llm_used": 0,
        },
        "scope_labels": dict(labels.most_common()),
        "header_types": dict(header_types.most_common()),
        "priority_findings": {
            "multi_property_scopes": multi[:25],
            "nested_header_chains": nested[:15],
            "orphan_headers": orphan[:15],
            "single_property_examples": single[:10],
        },
        "safety_contract": {
            "read_only_diagnostic": True,
            "association_applied": False,
            "database_writes": False,
            "canonical_tables_modified": False,
            "orchestrator_modified": False,
            "live_reconstructor_replaced": False,
            "matcher_modified": False,
            "whatsapp_live_modified": False,
            "raw_data_deleted": False,
            "llm_used": False,
            "raw_burst_text_exposed": False,
            "contacts_exposed": False,
            "returned_text_redacted": True,
            "price_inherited": False,
            "area_inherited": False,
            "configuration_inherited": False,
            "floor_inherited": False,
            "location_inherited": False,
        },
        "writes_performed": 0,
        "bursts": bursts,
    }


def _regression_demo() -> Dict[str, Any]:
    multi = {
        "burst_group_id": "demo-multi",
        "burst_text": (
            "PREMIUM RENTAL PROPERTIES\n"
            "RUSTOMJEE PARAMOUNT - KHAR WEST\n"
            "3 BHK | 1365 Sq.ft. | Semi-Furnished | Rent 2.50 Lakhs\n"
            "3 BHK | 1240 Sq.ft. | Fully Furnished | Rent 2.25 Lakhs\n"
            "PARK GRANDEUR - JUHU\n"
            "3 BHK | 1250 Sq.ft. | Rent 2.40 Lakhs\n"
        ),
    }
    footer = {
        "burst_group_id": "demo-footer",
        "burst_text": (
            "RUSTOMJEE PARAMOUNT - KHAR WEST\n"
            "CONTACT 9876543210\n"
            "3 BHK | 1365 Sq.ft. | Rent 2.50 Lakhs\n"
        ),
    }

    m = _analyze_burst(multi)
    f = _analyze_burst(footer)

    m_scopes = m.get("scopes") or []
    f_scopes = f.get("scopes") or []

    passed = (
        len(m_scopes) == 2
        and m_scopes[0]["scope_label"] == "MULTI_PROPERTY_SCOPE"
        and m_scopes[0]["property_rows_before_boundary"] == 2
        and m_scopes[0]["terminated_by"] == "NEXT_HEADER"
        and m_scopes[1]["scope_label"] == "SINGLE_PROPERTY_SCOPE"
        and len(f_scopes) == 1
        and f_scopes[0]["property_rows_before_boundary"] == 0
        and f_scopes[0]["terminated_by"] == "FOOTER"
        and "[PHONE_REDACTED]" not in f_scopes[0]["header_text_redacted"]
    )

    return {
        "status": "PASS" if passed else "FAIL",
        "version": VERSION,
        "tests": {
            "multi_scope_first_header": m_scopes[0] if m_scopes else None,
            "second_header_scope": m_scopes[1] if len(m_scopes) > 1 else None,
            "footer_boundary_scope": f_scopes[0] if f_scopes else None,
            "no_association_applied": True,
            "no_database_write": True,
            "no_llm": True,
        },
        "writes_performed": 0,
    }


def register(core):
    app = core.app
    engine = core.engine
    status_route = "/api/v7/property-ai/header-scope-v254c/status"

    if any(getattr(r, "path", None) == status_route for r in app.router.routes):
        return {
            "status": "ALREADY_REGISTERED",
            "version": VERSION,
            "route": status_route,
        }

    @app.get(status_route)
    def status():
        return JSONResponse({
            "status": "READY",
            "version": VERSION,
            "mode": MODE,
            "base_v253_version": v253.VERSION,
            "read_only_diagnostic": True,
            "association_applied": False,
            "database_writes": False,
            "canonical_tables_modified": False,
            "orchestrator_modified": False,
            "live_reconstructor_replaced": False,
            "matcher_modified": False,
            "whatsapp_live_modified": False,
            "raw_data_deleted": False,
            "llm_used": False,
            "raw_burst_text_exposed": False,
            "contacts_exposed": False,
        })

    @app.get("/api/v7/property-ai/header-scope-v254c/regression-test")
    def regression_test():
        return JSONResponse(_regression_demo())

    @app.get("/api/v7/property-ai/header-scope-v254c/diagnostic")
    def diagnostic(limit: int = Query(25, ge=1, le=100)):
        return JSONResponse(_diagnose(engine, limit))

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": status_route,
        "regression": "/api/v7/property-ai/header-scope-v254c/regression-test",
        "diagnostic": "/api/v7/property-ai/header-scope-v254c/diagnostic?limit=25",
        "writes_enabled": False,
    }

