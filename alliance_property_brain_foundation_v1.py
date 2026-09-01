
from __future__ import annotations

import hashlib
import html
import json
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Body, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

VERSION = "1.2.1-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"
MODE = "TRUSTED_EVIDENCE_CURRICULUM_GOLD_LAB_RUNTIME_SAFE"

# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------

PRODUCTION_WRITE_TABLES = {
    "pb_canonical_properties",
    "pb_property_offers",
    "pb_requirements",
    "pi_properties",
    "pi_whatsapp_property_master",
}

ACADEMY_TABLES = {
    "alliance_gold_source_messages",
    "alliance_gold_spans",
    "alliance_gold_span_labels",
    "alliance_gold_relationship_labels",
    "alliance_gold_evaluation_runs",
}

CONTENT_TYPES = {
    "PROPERTY_AVAILABILITY",
    "REQUIREMENT",
    "CONTACT_ONLY",
    "PROJECT_HEADER",
    "LOCALITY_HEADER",
    "FRAGMENT",
    "NOISE",
}

TRANSACTION_TYPES = {"SALE", "RENT", "BOTH", "AMBIGUOUS", "UNKNOWN"}

AREA_ROLES = {
    "TOTAL_AREA",
    "PLOT_AREA",
    "CARPET_AREA",
    "BUILTUP_AREA",
    "SUPER_AREA",
    "DISTANCE",
    "ROAD_WIDTH",
    "FRONTAGE",
    "UNKNOWN",
}

MONEY_ROLES = {
    "TOTAL_SALE_PRICE",
    "TOTAL_RENT",
    "RATE_PER_UNIT",
    "SECURITY_DEPOSIT",
    "MAINTENANCE",
    "PACKAGE_PRICE",
    "AMBIGUOUS",
}

RELATIONSHIP_TYPES = {
    "SAME_PHYSICAL_PROPERTY",
    "SAME_BUILDING_DIFFERENT_UNIT",
    "SAME_PROJECT",
    "SAME_OWNER_INVENTORY",
    "DUPLICATE_ADVERTISEMENT",
    "PACKAGE_MEMBER_OF",
    "UNRELATED",
    "UNCERTAIN",
}

NUMBERED_START_RE = re.compile(
    r"^\s*(?:\d{1,3}\s*[\.\)\-:]|(?:PROPERTY|PROP|UNIT|BUNGALOW|VILLA|SHOP|OFFICE|PLOT)\s*#?\s*\d+)\s*",
    re.I,
)

PROPERTY_FACT_RE = re.compile(
    r"\b(?:BHK|SQ\s*FT|SQFT|SFT|SQ\s*YD|SQYD|SYD|SYDS|GAJ|YARDS?|"
    r"ACRE|ACRES|CARPET|BUILT\s*UP|GROUND\s*FLOOR|GF|FIRST\s*FLOOR|"
    r"FOR\s*SALE|FOR\s*RENT|RENT|SALE|DEMAND|ASKING|LAC|LAKH|CR|CRORE)\b",
    re.I,
)

HEADER_RE = re.compile(
    r"^\s*[A-Z0-9][A-Z0-9 &./()\-]{2,55}\s*$"
)

DEMAND_RE = re.compile(
    r"\b(?:LOOKING\s+FOR|REQUIRED|WANTED|NEED(?:ED)?|DIRECT\s+CLIENT|"
    r"REQUIREMENT\s+FOR|RENTAL\s+REQUIREMENT|PURCHASE\s+REQUIREMENT|BUYER\s+REQUIREMENT)\b",
    re.I,
)

AVAILABILITY_RE = re.compile(
    r"\b(?:FOR\s+SALE|FOR\s+RENT|AVAILABLE|ASKING|DEMAND|OWNER\s+(?:WANTS|GOING)|"
    r"READY\s+TO\s+MOVE|PRE[\s\-]?RENTED)\b",
    re.I,
)

PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s\-]?)?[6-9]\d{9}(?!\d)")

AREA_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>"
    r"SQ\s*FT|SQFT|SFT|SQ\s*YD|SQYD|SYD|SYDS|GAJ|YARDS?|"
    r"SQ\s*M|SQM|SQ\s*MT|SQMT|ACRE|ACRES)\b",
    re.I,
)

MONEY_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*\.?\s*(?P<unit>CR|CRORE|CRORES|L|LAC|LACS|LAKH|LAKHS)\b",
    re.I,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _json_safe(value: Any) -> Any:
    """Convert DB/runtime values into JSON-safe Python primitives."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        # Preserve integer Decimals as ints; otherwise use float for UI/API use.
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)

def _json_response(value: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(content=_json_safe(value), status_code=status_code)

def _json(value: Any) -> str:
    return json.dumps(_json_safe(value), ensure_ascii=False)

def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default

def _safe_identifier(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
        raise ValueError("Unsafe SQL identifier")
    return '"' + name + '"'

def _fingerprint(text_value: str) -> str:
    normalized = re.sub(r"\s+", " ", (text_value or "")).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

def _route_exists(app, path: str) -> bool:
    return any(getattr(r, "path", None) == path for r in app.router.routes)

def _engine_from_core(core):
    if hasattr(core, "engine"):
        return core.engine
    if hasattr(core, "core") and hasattr(core.core, "engine"):
        return core.core.engine
    raise RuntimeError("Could not locate SQLAlchemy engine on core")

# ---------------------------------------------------------------------------
# DDL: Academy-only tables
# ---------------------------------------------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS alliance_gold_source_messages (
    source_message_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_table TEXT,
    source_row_ref TEXT,
    source_fingerprint TEXT NOT NULL UNIQUE,
    raw_text TEXT NOT NULL,
    source_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    sampling_bucket TEXT,
    message_length INTEGER NOT NULL DEFAULT 0,
    proposed_span_count INTEGER NOT NULL DEFAULT 0,
    labeling_status TEXT NOT NULL DEFAULT 'UNLABELED',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_gold_source_status
ON alliance_gold_source_messages(labeling_status);

CREATE TABLE IF NOT EXISTS alliance_gold_spans (
    span_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_message_id UUID NOT NULL
        REFERENCES alliance_gold_source_messages(source_message_id)
        ON DELETE CASCADE,
    span_order INTEGER NOT NULL,
    proposed_start_offset INTEGER NOT NULL,
    proposed_end_offset INTEGER NOT NULL,
    proposed_text TEXT NOT NULL,
    proposal_method TEXT NOT NULL DEFAULT 'DETERMINISTIC_V1',
    proposal_confidence NUMERIC,
    human_start_offset INTEGER,
    human_end_offset INTEGER,
    human_text TEXT,
    boundary_action TEXT,
    boundary_status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(source_message_id, span_order)
);

CREATE INDEX IF NOT EXISTS idx_gold_spans_source
ON alliance_gold_spans(source_message_id);

CREATE INDEX IF NOT EXISTS idx_gold_spans_status
ON alliance_gold_spans(boundary_status);

CREATE TABLE IF NOT EXISTS alliance_gold_span_labels (
    label_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    span_id UUID NOT NULL
        REFERENCES alliance_gold_spans(span_id)
        ON DELETE CASCADE,
    labeler_id TEXT NOT NULL,
    content_type TEXT NOT NULL,
    human_confidence TEXT NOT NULL DEFAULT 'HIGH',
    transaction_type TEXT,
    project_name TEXT,
    building_name TEXT,
    unit_identifier TEXT,
    city TEXT,
    locality TEXT,
    acceptable_locations JSONB NOT NULL DEFAULT '[]'::jsonb,
    areas JSONB NOT NULL DEFAULT '[]'::jsonb,
    money_mentions JSONB NOT NULL DEFAULT '[]'::jsonb,
    suitable_uses JSONB NOT NULL DEFAULT '[]'::jsonb,
    contacts JSONB NOT NULL DEFAULT '[]'::jsonb,
    property_fields JSONB NOT NULL DEFAULT '{}'::jsonb,
    requirement_fields JSONB NOT NULL DEFAULT '{}'::jsonb,
    notes TEXT,
    adjudicated BOOLEAN NOT NULL DEFAULT FALSE,
    adjudicator_id TEXT,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_gold_labels_span
ON alliance_gold_span_labels(span_id);

CREATE INDEX IF NOT EXISTS idx_gold_labels_active
ON alliance_gold_span_labels(active);

CREATE TABLE IF NOT EXISTS alliance_gold_relationship_labels (
    relationship_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    left_span_id UUID NOT NULL
        REFERENCES alliance_gold_spans(span_id)
        ON DELETE CASCADE,
    right_span_id UUID NOT NULL
        REFERENCES alliance_gold_spans(span_id)
        ON DELETE CASCADE,
    relationship_type TEXT NOT NULL,
    human_confidence TEXT NOT NULL DEFAULT 'HIGH',
    labeler_id TEXT NOT NULL,
    notes TEXT,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (left_span_id <> right_span_id)
);

CREATE TABLE IF NOT EXISTS alliance_gold_evaluation_runs (
    run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    engine_version TEXT NOT NULL,
    dataset_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    zero_tolerance_failures JSONB NOT NULL DEFAULT '[]'::jsonb,
    passed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

def install(engine) -> Dict[str, Any]:
    with engine.begin() as conn:
        for statement in [x.strip() for x in DDL.split(";") if x.strip()]:
            conn.execute(text(statement))
    return {
        "status": "INSTALLED",
        "version": VERSION,
        "academy_tables": sorted(ACADEMY_TABLES),
        "production_tables_modified": [],
        "canonical_writes": 0,
        "offer_writes": 0,
        "matcher_writes": 0,
        "whatsapp_live_writes": 0,
    }

# ---------------------------------------------------------------------------
# Source discovery
# ---------------------------------------------------------------------------

TEXT_HINT_RE = re.compile(
    r"(raw|message|text|description|content|body|remark|detail|evidence|segment|entity)",
    re.I,
)

# Columns that can look "distinct" but are not usable property evidence.
BAD_TEXT_COLUMN_RE = re.compile(
    r"(?:^|_)(?:id|uuid|key|hash|status|error|exception|trace|verification|verified_by|"
    r"property_id|record_id|generation_id|parent_message_id|identity_id|source_id)(?:$|_)",
    re.I,
)

BAD_TABLE_RE = re.compile(
    r"^(alliance_gold_|alliance_ai_|alliance_v|pb_corrections$|pb_feedback|"
    r"pi_scan_tiles$|pi_property_health$|pi_verification_log$)",
    re.I,
)

DERIVED_TEXT_COLUMN_RE = re.compile(
    r"(?:^|_)(?:frontage|floor|location|property_name|configuration|preferred_locations|"
    r"source_detail|evidence_basis|raw_value|remarks)(?:$|_)",
    re.I,
)

DERIVED_OR_AUX_TABLE_RE = re.compile(
    r"^(?:ai_property_match_index|pi_hospitality_phone_evidence|"
    r"pi_hospitality_enrichment_evidence|pi_message_drafts|"
    r"pi_operational_properties|aci_|pi_marketing_contacts|"
    r"pi_contact_directory_v2|ai_whatsapp_area_intelligence|"
    r"pi_property_contact_links)$",
    re.I,
)

TRUSTED_GOLD_SOURCES = [
    ("ai_whatsapp_purity", "raw_text", "WHATSAPP_EVIDENCE", 8),
    ("alliance_live_feed_entities", "raw_message", "WHATSAPP_EVIDENCE", 5),
    ("alliance_property_listings", "raw_text", "SEGMENTED_PROPERTY_EVIDENCE", 4),
    ("ai_clean_property_entity", "raw_text", "SEGMENTED_PROPERTY_EVIDENCE", 2),
    ("pb_raw_evidence", "raw_text", "PROPERTY_BRAIN_EVIDENCE", 3),
    ("ai_demand_signals", "source_contact_text", "REQUIREMENT_EVIDENCE", 3),
]

HARD_BOUNDARY_SOURCES = [
    ("pi_whatsapp_normalized_property", "raw_message", "HARD_BOUNDARY_EVIDENCE", 2),
    ("pi_whatsapp_newspaper_format", "raw_message", "HARD_BOUNDARY_EVIDENCE", 1),
]

PROPERTY_TYPE_RE = re.compile(
    r"\b(?:BHK|FLAT|APARTMENT|FLOOR|VILLA|KOTHI|HOUSE|BUNGALOW|PLOT|LAND|"
    r"SHOP|SHOWROOM|SCO|OFFICE|COMMERCIAL|WAREHOUSE|GODOWN|HOTEL|BANQUET|"
    r"RESTAURANT|CAFE|CLINIC|HOSPITAL|FARMHOUSE|PENTHOUSE|STUDIO|RETAIL)\b",
    re.I,
)

PROPERTY_AREA_SIGNAL_RE = re.compile(
    r"\b\d+(?:,\d{3})*(?:\.\d+)?\s*(?:SQ\s*FT|SQFT|SFT|SQ\s*YD|SQYD|"
    r"SYD|SYDS|GAJ|YARDS?|SQ\s*M|SQM|SQ\s*MT|SQMT|ACRE|ACRES|MTR|METRE|METER)\b",
    re.I,
)

PROPERTY_MONEY_SIGNAL_RE = re.compile(
    r"(?:₹|RS\.?|INR|\bRENT\b|\bDEMAND\b|\bASK(?:ING)?\b|\bPRICE\b|"
    r"\b\d+(?:\.\d+)?\s*(?:CR|CRORE|CRORES|L|LAC|LACS|LAKH|LAKHS|K)\b)",
    re.I,
)

PROPERTY_TX_SIGNAL_RE = re.compile(
    r"\b(?:FOR\s+SALE|FOR\s+RENT|AVAILABLE|AVL|SALE|RENT|LEASE|PRE[\s\-]?RENTED)\b",
    re.I,
)

PROPERTY_LOCATION_SIGNAL_RE = re.compile(
    r"\b(?:SECTOR\s*[-:]?\s*\d+[A-Z]?|SEC\s*[-:]?\s*\d+[A-Z]?|"
    r"PHASE\s*[-:]?\s*\d+|BLOCK\s*[-:]?\s*[A-Z0-9]+|"
    r"GURGAON|GURUGRAM|DELHI|NOIDA|GOA|MUMBAI|DWARKA|SAKET|KALKAJI|"
    r"JANAK\s*PURI|VASANT\s*KUNJ|GREATER\s*KAILASH|DLF|M3M|EMAAR|IREO)\b",
    re.I,
)

REQUIREMENT_SIGNAL_RE = re.compile(
    r"\b(?:LOOKING\s+FOR|REQUIRED|WANTED|NEED(?:ED)?|DIRECT\s+CLIENT|"
    r"REQUIREMENT|BUYER\s+REQUIREMENT|RENTAL\s+REQUIREMENT)\b",
    re.I,
)

ERROR_SIGNAL_RE = re.compile(
    r"\b(?:TRACEBACK|EXCEPTION|ERROR|FAILED|TIMEOUT|HTTP\s*\d{3}|STACK\s+TRACE)\b",
    re.I,
)

ID_LIKE_RE = re.compile(
    r"^[A-Za-z0-9_\-]{16,64}$"
)

def _tables(engine) -> List[str]:
    with engine.connect() as conn:
        return [
            r[0]
            for r in conn.execute(
                text(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema=current_schema()
                      AND table_type='BASE TABLE'
                    ORDER BY table_name
                    """
                )
            ).all()
        ]

def _columns(engine, table_name: str) -> List[Dict[str, str]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema=current_schema()
                  AND table_name=:table_name
                ORDER BY ordinal_position
                """
            ),
            {"table_name": table_name},
        ).mappings().all()
    return [dict(r) for r in rows]

def _row_count(engine, table_name: str) -> int:
    qt = _safe_identifier(table_name)
    with engine.connect() as conn:
        return int(conn.execute(text(f"SELECT count(*) FROM {qt}")).scalar() or 0)

def _source_class(table_name: str, column_name: str) -> str:
    t = table_name.lower()
    c = column_name.lower()
    if "requirement" in t or "demand" in t:
        return "REQUIREMENT_EVIDENCE"
    if "whatsapp" in t or "live_feed" in t:
        if c in {"raw_text", "raw_message", "message_text", "entity_text"}:
            return "WHATSAPP_EVIDENCE"
    if "magazine" in t or "newspaper" in t:
        return "PRINT_EVIDENCE"
    if "clean_property_entity" in t or "property_listings" in t:
        return "SEGMENTED_PROPERTY_EVIDENCE"
    if t.startswith("pb_"):
        return "PROPERTY_BRAIN_EVIDENCE"
    if "property" in t:
        return "PROPERTY_EVIDENCE"
    return "OTHER_EVIDENCE"

def _semantic_signal_count(value: str) -> int:
    s = value or ""
    signals = 0
    signals += 1 if PROPERTY_TYPE_RE.search(s) else 0
    signals += 1 if PROPERTY_AREA_SIGNAL_RE.search(s) else 0
    signals += 1 if PROPERTY_MONEY_SIGNAL_RE.search(s) else 0
    signals += 1 if PROPERTY_TX_SIGNAL_RE.search(s) else 0
    signals += 1 if PROPERTY_LOCATION_SIGNAL_RE.search(s) else 0
    signals += 1 if REQUIREMENT_SIGNAL_RE.search(s) else 0
    signals += 1 if PHONE_RE.search(s) else 0
    return signals

def _profile_text_column(
    engine,
    table_name: str,
    column_name: str,
    sample_size: int = 300,
) -> Dict[str, Any]:
    qt = _safe_identifier(table_name)
    qc = _safe_identifier(column_name)

    if BAD_TEXT_COLUMN_RE.search(column_name):
        return {
            "table": table_name,
            "column": column_name,
            "eligible": False,
            "rejection_reason": "IDENTIFIER_OR_METADATA_COLUMN",
            "score": -1000.0,
        }

    try:
        with engine.connect() as conn:
            values = [
                str(r[0]).strip()
                for r in conn.execute(
                    text(
                        f"""
                        SELECT {qc}::text
                        FROM {qt}
                        WHERE {qc} IS NOT NULL
                          AND length(trim({qc}::text)) >= 20
                        ORDER BY md5({qc}::text)
                        LIMIT :n
                        """
                    ),
                    {"n": sample_size},
                ).all()
                if r[0]
            ]
    except Exception as exc:
        return {
            "table": table_name,
            "column": column_name,
            "eligible": False,
            "score": -1000.0,
            "error": f"{type(exc).__name__}: {exc}",
        }

    if not values:
        return {
            "table": table_name,
            "column": column_name,
            "sampled": 0,
            "distinct": 0,
            "distinct_ratio": 0.0,
            "avg_len": 0.0,
            "property_like_ratio": 0.0,
            "avg_property_signals": 0.0,
            "error_like_ratio": 0.0,
            "id_like_ratio": 0.0,
            "eligible": False,
            "score": 0.0,
        }

    unique = len(set(v.lower() for v in values))
    ratio = unique / len(values)
    avg_len = sum(len(v) for v in values) / len(values)
    giant_ratio = sum(1 for v in values if len(v) >= 5000) / len(values)

    signal_counts = [_semantic_signal_count(v) for v in values]
    property_like = sum(1 for n in signal_counts if n >= 2) / len(values)
    strong_property_like = sum(1 for n in signal_counts if n >= 3) / len(values)
    avg_signals = sum(signal_counts) / len(values)

    error_like = sum(1 for v in values if ERROR_SIGNAL_RE.search(v)) / len(values)
    id_like = sum(
        1 for v in values
        if ID_LIKE_RE.fullmatch(v.strip()) is not None
    ) / len(values)

    source_class = _source_class(table_name, column_name)

    derived_column = bool(DERIVED_TEXT_COLUMN_RE.search(column_name))
    derived_table = bool(DERIVED_OR_AUX_TABLE_RE.search(table_name))
    sufficient_diversity = unique >= 20 and ratio >= 0.10

    eligible = (
        property_like >= 0.20
        and strong_property_like >= 0.10
        and error_like <= 0.05
        and id_like <= 0.10
        and avg_len >= 35
        and sufficient_diversity
        and not derived_column
        and not derived_table
    )

    # Property semantics dominate score. Diversity prevents repeated dumps
    # from pretending to be hundreds of independent examples.
    score = (
        property_like * 1800
        + strong_property_like * 1200
        + avg_signals * 120
        + ratio * 400
        + min(unique, 300) * 2.0
        + min(avg_len, 1200) / 20
        - giant_ratio * 250
        - error_like * 2000
        - id_like * 2000
    )

    return {
        "table": table_name,
        "column": column_name,
        "source_class": source_class,
        "sampled": len(values),
        "distinct": unique,
        "distinct_ratio": round(ratio, 4),
        "avg_len": round(avg_len, 2),
        "giant_blob_ratio": round(giant_ratio, 4),
        "property_like_ratio": round(property_like, 4),
        "strong_property_like_ratio": round(strong_property_like, 4),
        "avg_property_signals": round(avg_signals, 3),
        "error_like_ratio": round(error_like, 4),
        "id_like_ratio": round(id_like, 4),
        "derived_column": bool(derived_column),
        "derived_or_aux_table": bool(derived_table),
        "sufficient_diversity": bool(sufficient_diversity),
        "eligible": bool(eligible),
        "score": round(score, 2),
    }

def discover_sources(engine) -> Dict[str, Any]:
    profiles: List[Dict[str, Any]] = []
    available_tables = set(_tables(engine))

    for table_name in sorted(available_tables):
        if BAD_TABLE_RE.search(table_name) or DERIVED_OR_AUX_TABLE_RE.search(table_name):
            continue
        try:
            count = _row_count(engine, table_name)
        except Exception:
            continue
        if count < 20:
            continue

        cols = _columns(engine, table_name)
        textual = [
            c["column_name"] for c in cols
            if str(c["data_type"]).lower()
            in {"text", "character varying", "character", "varchar"}
        ]

        candidates = [
            c for c in textual
            if TEXT_HINT_RE.search(c)
            and not BAD_TEXT_COLUMN_RE.search(c)
            and not DERIVED_TEXT_COLUMN_RE.search(c)
        ]

        for col in candidates[:12]:
            p = _profile_text_column(engine, table_name, col)
            p["row_count"] = count
            profiles.append(p)

    ranked = sorted(
        profiles,
        key=lambda p: (
            1 if p.get("eligible") else 0,
            p.get("score", -9999),
            p.get("property_like_ratio", 0),
            p.get("distinct_ratio", 0),
        ),
        reverse=True,
    )

    recommended = [p for p in ranked if p.get("eligible")][:20]
    by_key = {(p.get("table"), p.get("column")): p for p in profiles}

    curriculum = []
    rejected_trusted = []
    for table_name, column_name, source_class, default_weight in (
        TRUSTED_GOLD_SOURCES + HARD_BOUNDARY_SOURCES
    ):
        profile = by_key.get((table_name, column_name))
        if not profile:
            rejected_trusted.append({
                "table": table_name,
                "column": column_name,
                "reason": "NOT_FOUND_OR_NOT_PROFILED",
            })
            continue
        if not profile.get("eligible"):
            rejected_trusted.append({
                "table": table_name,
                "column": column_name,
                "reason": "FAILED_SEMANTIC_OR_DIVERSITY_GATE",
                "profile": profile,
            })
            continue

        item = dict(profile)
        item["source_class"] = source_class
        item["default_weight"] = default_weight
        item["trusted_gold_source"] = True
        curriculum.append(item)

    return {
        "status": "PASS",
        "version": VERSION,
        "selector": "TRUSTED_SOURCE_PLUS_SEMANTIC_GATE_V2",
        "ranked_sources": ranked[:40],
        "recommended_sources": recommended,
        "recommended_curriculum": curriculum,
        "rejected_trusted_sources": rejected_trusted,
        "selection_rules": {
            "reject_identifier_columns": True,
            "reject_error_log_tables": True,
            "reject_derived_index_fields": True,
            "reject_auxiliary_hospitality_contact_evidence": True,
            "minimum_property_like_ratio": 0.20,
            "minimum_strong_property_like_ratio": 0.10,
            "minimum_distinct_examples": 20,
            "minimum_distinct_ratio": 0.10,
            "maximum_error_like_ratio": 0.05,
            "maximum_id_like_ratio": 0.10,
            "trusted_source_curriculum": True,
            "hard_boundary_examples_capped": True,
        },
        "read_only_discovery": True,
    }

def curriculum_plan(engine, total_messages: int = 25) -> Dict[str, Any]:
    discovery = discover_sources(engine)
    curriculum = discovery.get("recommended_curriculum") or []

    if not curriculum:
        return {
            "status": "NO_TRUSTED_ELIGIBLE_PROPERTY_SOURCES",
            "version": VERSION,
            "plan": [],
            "total_messages": 0,
            "rejected_trusted_sources": discovery.get("rejected_trusted_sources", []),
        }

    requested = max(1, min(int(total_messages), 100))
    total_weight = sum(max(1, int(x.get("default_weight") or 1)) for x in curriculum)

    plan = []
    assigned = 0
    for idx, src in enumerate(curriculum):
        weight = max(1, int(src.get("default_weight") or 1))
        if idx == len(curriculum) - 1:
            take = requested - assigned
        else:
            take = max(1, round(requested * weight / total_weight))
            take = min(take, requested - assigned)
        if take <= 0:
            continue

        plan.append({
            "table": src["table"],
            "column": src["column"],
            "source_class": src.get("source_class"),
            "messages": take,
            "property_like_ratio": src.get("property_like_ratio"),
            "strong_property_like_ratio": src.get("strong_property_like_ratio"),
            "distinct": src.get("distinct"),
            "distinct_ratio": src.get("distinct_ratio"),
            "avg_len": src.get("avg_len"),
            "trusted_gold_source": True,
        })
        assigned += take
        if assigned >= requested:
            break

    planned = sum(x["messages"] for x in plan)
    if plan and planned != requested:
        plan[0]["messages"] += requested - planned

    cap = max(1, int(round(requested * 0.40)))
    overflow = 0
    for item in plan:
        if item["messages"] > cap:
            overflow += item["messages"] - cap
            item["messages"] = cap

    while overflow > 0:
        progressed = False
        for item in plan:
            if item["messages"] < cap and overflow > 0:
                item["messages"] += 1
                overflow -= 1
                progressed = True
        if not progressed:
            break

    return {
        "status": "PASS",
        "version": VERSION,
        "requested_total_messages": requested,
        "planned_total_messages": sum(x["messages"] for x in plan),
        "plan": plan,
        "guardrails": {
            "trusted_sources_only": True,
            "derived_fields_forbidden": True,
            "single_source_max_fraction": 0.40,
            "production_writes": False,
        },
        "note": (
            "Human Gold-Lab sampling only. Derived match-index fields, IDs, "
            "hospitality contact enrichment, logs and low-diversity repeated "
            "dumps are excluded from the curriculum."
        ),
    }

def import_curriculum(engine, total_messages: int = 25) -> Dict[str, Any]:
    plan = curriculum_plan(engine, total_messages)
    if plan.get("status") != "PASS":
        return plan

    results = []
    for item in plan["plan"]:
        result = import_sources(
            engine,
            item["table"],
            item["column"],
            item["messages"],
        )
        results.append({
            "source_class": item.get("source_class"),
            **result,
        })

    return {
        "status": "IMPORTED",
        "version": VERSION,
        "plan": plan["plan"],
        "results": results,
        "inserted_sources": sum(r.get("inserted_sources", 0) for r in results),
        "inserted_proposed_spans": sum(r.get("inserted_proposed_spans", 0) for r in results),
        "academy_writes_only": True,
        "canonical_writes": 0,
        "offer_writes": 0,
        "matcher_writes": 0,
        "whatsapp_live_writes": 0,
    }

# ---------------------------------------------------------------------------
# Evidence Span proposal
# ---------------------------------------------------------------------------

def _line_offsets(raw: str) -> List[Tuple[int, int, str]]:
    out = []
    pos = 0
    for piece in raw.splitlines(keepends=True):
        text_piece = piece.rstrip("\r\n")
        start = pos
        end = pos + len(text_piece)
        out.append((start, end, text_piece))
        pos += len(piece)
    if not out and raw:
        out.append((0, len(raw), raw))
    return out

def _looks_like_header(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 60:
        return False
    if PROPERTY_FACT_RE.search(s):
        return False
    if PHONE_RE.search(s):
        return False
    return bool(HEADER_RE.fullmatch(s)) and len(s.split()) <= 8

def _strong_property_line(line: str) -> bool:
    return bool(PROPERTY_FACT_RE.search(line or ""))

def propose_spans(raw: str) -> List[Dict[str, Any]]:
    """
    Conservative proposal engine.
    It proposes review units; humans remain ground truth.
    """
    raw = raw or ""
    lines = _line_offsets(raw)
    if not lines:
        return []

    starts = [0]

    for i, (start, end, line) in enumerate(lines):
        s = line.strip()
        if not s:
            continue

        if i > 0 and NUMBERED_START_RE.match(s):
            starts.append(start)
            continue

        # A strong new property line after an already fact-rich block can
        # indicate a new span. Keep this conservative to avoid oversplitting.
        if i > 0 and _strong_property_line(s):
            prev_window = "\n".join(x[2] for x in lines[max(0, i - 5):i])
            if (
                PROPERTY_FACT_RE.search(prev_window)
                and re.search(r"\b(?:FOR SALE|FOR RENT|RENT|SALE|DEMAND|ASKING)\b", s, re.I)
                and len(prev_window) >= 30
            ):
                starts.append(start)

    starts = sorted(set(starts))
    spans = []

    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(raw)
        piece = raw[start:end].strip()

        if not piece:
            continue

        actual_start = raw.find(piece, start, end + 1)
        if actual_start < 0:
            actual_start = start
        actual_end = actual_start + len(piece)

        demand = bool(DEMAND_RE.search(piece))
        availability = bool(AVAILABILITY_RE.search(piece))
        facts = len(PROPERTY_FACT_RE.findall(piece))

        if demand and not availability:
            hint = "REQUIREMENT"
        elif availability or facts >= 2:
            hint = "PROPERTY_AVAILABILITY"
        elif _looks_like_header(piece):
            hint = "HEADER_OR_CONTEXT"
        else:
            hint = "FRAGMENT_OR_REVIEW"

        confidence = 0.90 if NUMBERED_START_RE.match(piece) else 0.65
        if hint in {"HEADER_OR_CONTEXT", "FRAGMENT_OR_REVIEW"}:
            confidence = 0.45

        spans.append(
            {
                "span_order": len(spans) + 1,
                "start_offset": actual_start,
                "end_offset": actual_end,
                "text": piece,
                "content_hint": hint,
                "proposal_confidence": confidence,
            }
        )

    # If no useful split happened, retain the whole source as one review unit.
    if not spans and raw.strip():
        piece = raw.strip()
        start = raw.find(piece)
        spans = [{
            "span_order": 1,
            "start_offset": start,
            "end_offset": start + len(piece),
            "text": piece,
            "content_hint": "FRAGMENT_OR_REVIEW",
            "proposal_confidence": 0.35,
        }]

    return spans

# ---------------------------------------------------------------------------
# Candidate extraction for reviewer assistance only
# ---------------------------------------------------------------------------

def propose_fields(span_text: str) -> Dict[str, Any]:
    s = span_text or ""

    if DEMAND_RE.search(s) and not AVAILABILITY_RE.search(s):
        content_hint = "REQUIREMENT"
    elif AVAILABILITY_RE.search(s) or PROPERTY_FACT_RE.search(s):
        content_hint = "PROPERTY_AVAILABILITY"
    else:
        content_hint = "FRAGMENT"

    if re.search(r"\bFOR\s+SALE\b|\bSALE\b|\bDEMAND\b", s, re.I) and re.search(
        r"\bFOR\s+RENT\b|\bRENT\b", s, re.I
    ):
        tx = "BOTH"
    elif re.search(r"\bFOR\s+RENT\b|\bRENT(?:AL)?\b", s, re.I):
        tx = "RENT"
    elif re.search(r"\bFOR\s+SALE\b|\bSALE\b|\bDEMAND\b", s, re.I):
        tx = "SALE"
    else:
        tx = "UNKNOWN"

    areas = []
    for m in AREA_RE.finditer(s):
        areas.append({
            "value": float(m.group("value")),
            "unit": re.sub(r"\s+", "", m.group("unit").upper()),
            "role": "UNKNOWN",
            "evidence": m.group(0),
        })

    money = []
    for m in MONEY_RE.finditer(s):
        raw_value = float(m.group("value"))
        unit = m.group("unit").upper()
        normalized = raw_value * (10000000 if unit.startswith("CR") else 100000)
        money.append({
            "value": normalized,
            "raw_value": raw_value,
            "unit": unit,
            "role": "AMBIGUOUS",
            "evidence": m.group(0),
        })

    contacts = [{"phone": m.group(0), "role": "UNKNOWN"} for m in PHONE_RE.finditer(s)]

    return {
        "content_type_hint": content_hint,
        "transaction_type_hint": tx,
        "areas": areas,
        "money_mentions": money,
        "contacts": contacts,
        "human_review_required": True,
    }

# ---------------------------------------------------------------------------
# Sampling / import into Gold Lab
# ---------------------------------------------------------------------------

def _bucket(raw: str) -> str:
    n = len(raw or "")
    if n < 300:
        return "SHORT"
    if n < 1200:
        return "MEDIUM"
    if n < 5000:
        return "LONG"
    return "GIANT_DUMP"

def _fetch_distinct_text(
    engine,
    table_name: str,
    column_name: str,
    limit: int,
) -> List[str]:
    qt = _safe_identifier(table_name)
    qc = _safe_identifier(column_name)
    fetch_limit = min(max(limit * 8, 1000), 15000)

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT {qc}::text AS raw_text
                FROM {qt}
                WHERE {qc} IS NOT NULL
                  AND length(trim({qc}::text)) >= 20
                ORDER BY md5({qc}::text)
                LIMIT :n
                """
            ),
            {"n": fetch_limit},
        ).all()

    seen = set()
    out = []
    for row in rows:
        raw = str(row[0] or "").strip()
        fp = _fingerprint(raw)
        if not raw or fp in seen:
            continue
        seen.add(fp)
        out.append(raw)
        if len(out) >= limit:
            break
    return out

def import_sources(
    engine,
    table_name: str,
    column_name: str,
    limit: int = 25,
) -> Dict[str, Any]:
    # Academy write only.
    if table_name in ACADEMY_TABLES:
        raise HTTPException(400, "Cannot sample from Gold/Academy tables")

    available_tables = set(_tables(engine))
    if table_name not in available_tables:
        raise HTTPException(400, f"Unknown source table: {table_name}")

    cols = {c["column_name"] for c in _columns(engine, table_name)}
    if column_name not in cols:
        raise HTTPException(400, f"Unknown source column: {column_name}")

    raw_messages = _fetch_distinct_text(engine, table_name, column_name, limit)

    inserted_sources = 0
    inserted_spans = 0
    bucket_counts: Dict[str, int] = {}

    with engine.begin() as conn:
        for raw in raw_messages:
            fp = _fingerprint(raw)
            bucket = _bucket(raw)
            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
            spans = propose_spans(raw)

            existing = conn.execute(
                text(
                    """
                    SELECT source_message_id
                    FROM alliance_gold_source_messages
                    WHERE source_fingerprint=:fp
                    """
                ),
                {"fp": fp},
            ).scalar()

            if existing:
                continue

            source_id = str(uuid.uuid4())
            conn.execute(
                text(
                    """
                    INSERT INTO alliance_gold_source_messages (
                        source_message_id, source_table, source_row_ref,
                        source_fingerprint, raw_text, source_metadata,
                        sampling_bucket, message_length, proposed_span_count
                    )
                    VALUES (
                        :source_message_id, :source_table, NULL,
                        :source_fingerprint, :raw_text,
                        CAST(:source_metadata AS jsonb),
                        :sampling_bucket, :message_length, :proposed_span_count
                    )
                    """
                ),
                {
                    "source_message_id": source_id,
                    "source_table": table_name,
                    "source_fingerprint": fp,
                    "raw_text": raw,
                    "source_metadata": _json({"source_column": column_name}),
                    "sampling_bucket": bucket,
                    "message_length": len(raw),
                    "proposed_span_count": len(spans),
                },
            )
            inserted_sources += 1

            for span in spans:
                conn.execute(
                    text(
                        """
                        INSERT INTO alliance_gold_spans (
                            span_id, source_message_id, span_order,
                            proposed_start_offset, proposed_end_offset,
                            proposed_text, proposal_method, proposal_confidence
                        )
                        VALUES (
                            :span_id, :source_message_id, :span_order,
                            :start_offset, :end_offset,
                            :proposed_text, 'DETERMINISTIC_V1', :proposal_confidence
                        )
                        """
                    ),
                    {
                        "span_id": str(uuid.uuid4()),
                        "source_message_id": source_id,
                        "span_order": span["span_order"],
                        "start_offset": span["start_offset"],
                        "end_offset": span["end_offset"],
                        "proposed_text": span["text"],
                        "proposal_confidence": span["proposal_confidence"],
                    },
                )
                inserted_spans += 1

    return {
        "status": "IMPORTED",
        "source_table": table_name,
        "source_column": column_name,
        "requested_messages": limit,
        "distinct_messages_found": len(raw_messages),
        "inserted_sources": inserted_sources,
        "inserted_proposed_spans": inserted_spans,
        "sampling_buckets": bucket_counts,
        "academy_writes_only": True,
        "canonical_writes": 0,
        "offer_writes": 0,
        "matcher_writes": 0,
        "whatsapp_live_writes": 0,
    }

# ---------------------------------------------------------------------------
# Human labeling
# ---------------------------------------------------------------------------

def _validate_label(payload: Dict[str, Any]) -> None:
    content_type = str(payload.get("content_type") or "").upper()
    if content_type not in CONTENT_TYPES:
        raise HTTPException(400, f"Invalid content_type: {content_type}")

    tx = payload.get("transaction_type")
    if tx is not None and str(tx).upper() not in TRANSACTION_TYPES:
        raise HTTPException(400, f"Invalid transaction_type: {tx}")

    confidence = str(payload.get("human_confidence") or "HIGH").upper()
    if confidence not in {"HIGH", "MEDIUM", "LOW"}:
        raise HTTPException(400, "human_confidence must be HIGH/MEDIUM/LOW")

def save_label(engine, span_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    _validate_label(payload)

    labeler_id = str(payload.get("labeler_id") or "").strip()
    if not labeler_id:
        raise HTTPException(400, "labeler_id is required")

    content_type = str(payload["content_type"]).upper()
    confidence = str(payload.get("human_confidence") or "HIGH").upper()
    tx = payload.get("transaction_type")
    tx = str(tx).upper() if tx else None

    with engine.begin() as conn:
        span = conn.execute(
            text(
                """
                SELECT span_id, source_message_id
                FROM alliance_gold_spans
                WHERE span_id=:span_id
                """
            ),
            {"span_id": span_id},
        ).mappings().first()

        if not span:
            raise HTTPException(404, "Span not found")

        conn.execute(
            text(
                """
                UPDATE alliance_gold_span_labels
                SET active=FALSE, updated_at=now()
                WHERE span_id=:span_id AND labeler_id=:labeler_id AND active=TRUE
                """
            ),
            {"span_id": span_id, "labeler_id": labeler_id},
        )

        conn.execute(
            text(
                """
                INSERT INTO alliance_gold_span_labels (
                    label_id, span_id, labeler_id, content_type,
                    human_confidence, transaction_type,
                    project_name, building_name, unit_identifier,
                    city, locality, acceptable_locations,
                    areas, money_mentions, suitable_uses, contacts,
                    property_fields, requirement_fields, notes,
                    adjudicated, adjudicator_id
                )
                VALUES (
                    :label_id, :span_id, :labeler_id, :content_type,
                    :human_confidence, :transaction_type,
                    :project_name, :building_name, :unit_identifier,
                    :city, :locality, CAST(:acceptable_locations AS jsonb),
                    CAST(:areas AS jsonb), CAST(:money_mentions AS jsonb),
                    CAST(:suitable_uses AS jsonb), CAST(:contacts AS jsonb),
                    CAST(:property_fields AS jsonb),
                    CAST(:requirement_fields AS jsonb), :notes,
                    :adjudicated, :adjudicator_id
                )
                """
            ),
            {
                "label_id": str(uuid.uuid4()),
                "span_id": span_id,
                "labeler_id": labeler_id,
                "content_type": content_type,
                "human_confidence": confidence,
                "transaction_type": tx,
                "project_name": payload.get("project_name"),
                "building_name": payload.get("building_name"),
                "unit_identifier": payload.get("unit_identifier"),
                "city": payload.get("city"),
                "locality": payload.get("locality"),
                "acceptable_locations": _json(payload.get("acceptable_locations") or []),
                "areas": _json(payload.get("areas") or []),
                "money_mentions": _json(payload.get("money_mentions") or []),
                "suitable_uses": _json(payload.get("suitable_uses") or []),
                "contacts": _json(payload.get("contacts") or []),
                "property_fields": _json(payload.get("property_fields") or {}),
                "requirement_fields": _json(payload.get("requirement_fields") or {}),
                "notes": payload.get("notes"),
                "adjudicated": bool(payload.get("adjudicated", False)),
                "adjudicator_id": payload.get("adjudicator_id"),
            },
        )

        conn.execute(
            text(
                """
                UPDATE alliance_gold_spans
                SET boundary_status='LABELED',
                    boundary_action=COALESCE(:boundary_action, boundary_action),
                    human_start_offset=COALESCE(:human_start_offset, human_start_offset),
                    human_end_offset=COALESCE(:human_end_offset, human_end_offset),
                    human_text=COALESCE(:human_text, human_text),
                    updated_at=now()
                WHERE span_id=:span_id
                """
            ),
            {
                "span_id": span_id,
                "boundary_action": payload.get("boundary_action") or "CORRECT",
                "human_start_offset": payload.get("human_start_offset"),
                "human_end_offset": payload.get("human_end_offset"),
                "human_text": payload.get("human_text"),
            },
        )

        conn.execute(
            text(
                """
                UPDATE alliance_gold_source_messages s
                SET labeling_status = CASE
                    WHEN NOT EXISTS (
                        SELECT 1
                        FROM alliance_gold_spans sp
                        WHERE sp.source_message_id=s.source_message_id
                          AND sp.boundary_status <> 'LABELED'
                    ) THEN 'LABELED'
                    ELSE 'IN_PROGRESS'
                END,
                updated_at=now()
                WHERE s.source_message_id=:source_message_id
                """
            ),
            {"source_message_id": str(span["source_message_id"])},
        )

    return {
        "status": "SAVED",
        "span_id": span_id,
        "labeler_id": labeler_id,
        "academy_write_only": True,
    }

def save_relationship(engine, payload: Dict[str, Any]) -> Dict[str, Any]:
    left = str(payload.get("left_span_id") or "")
    right = str(payload.get("right_span_id") or "")
    rel = str(payload.get("relationship_type") or "").upper()
    labeler = str(payload.get("labeler_id") or "").strip()
    confidence = str(payload.get("human_confidence") or "HIGH").upper()

    if not left or not right or left == right:
        raise HTTPException(400, "Two different span IDs are required")
    if rel not in RELATIONSHIP_TYPES:
        raise HTTPException(400, f"Invalid relationship_type: {rel}")
    if not labeler:
        raise HTTPException(400, "labeler_id is required")

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO alliance_gold_relationship_labels (
                    relationship_id, left_span_id, right_span_id,
                    relationship_type, human_confidence,
                    labeler_id, notes
                )
                VALUES (
                    :relationship_id, :left_span_id, :right_span_id,
                    :relationship_type, :human_confidence,
                    :labeler_id, :notes
                )
                """
            ),
            {
                "relationship_id": str(uuid.uuid4()),
                "left_span_id": left,
                "right_span_id": right,
                "relationship_type": rel,
                "human_confidence": confidence,
                "labeler_id": labeler,
                "notes": payload.get("notes"),
            },
        )

    return {"status": "SAVED", "academy_write_only": True}

# ---------------------------------------------------------------------------
# Gold dataset / evaluation
# ---------------------------------------------------------------------------

def progress(engine) -> Dict[str, Any]:
    with engine.connect() as conn:
        counts = conn.execute(
            text(
                """
                SELECT
                    (SELECT count(*) FROM alliance_gold_source_messages) AS source_messages,
                    (SELECT count(*) FROM alliance_gold_spans) AS proposed_spans,
                    (SELECT count(*) FROM alliance_gold_spans WHERE boundary_status='LABELED') AS labeled_spans,
                    (SELECT count(*) FROM alliance_gold_span_labels WHERE active=TRUE) AS active_labels,
                    (SELECT count(*) FROM alliance_gold_relationship_labels WHERE active=TRUE) AS relationship_labels,
                    (SELECT count(*) FROM alliance_gold_span_labels WHERE active=TRUE AND adjudicated=TRUE) AS adjudicated_labels
                """
            )
        ).mappings().first()

        content = conn.execute(
            text(
                """
                SELECT content_type, count(*) AS n
                FROM alliance_gold_span_labels
                WHERE active=TRUE
                GROUP BY content_type
                ORDER BY n DESC
                """
            )
        ).mappings().all()

        buckets = conn.execute(
            text(
                """
                SELECT sampling_bucket, count(*) AS n
                FROM alliance_gold_source_messages
                GROUP BY sampling_bucket
                ORDER BY sampling_bucket
                """
            )
        ).mappings().all()

    labeled = int(counts["labeled_spans"] or 0)
    milestone = (
        "PILOT_100_COMPLETE" if labeled >= 100
        else f"PILOT_100_PROGRESS_{labeled}_OF_100"
    )

    return {
        "status": "PASS",
        "version": VERSION,
        **{k: int(v or 0) for k, v in counts.items()},
        "content_type_distribution": [dict(r) for r in content],
        "sampling_bucket_distribution": [dict(r) for r in buckets],
        "first_milestone": milestone,
        "gold_business_accuracy_available": False,
        "note": (
            "Gold business accuracy becomes meaningful only after human labels "
            "exist and predictions are evaluated against a frozen Gold snapshot."
        ),
    }

def next_span(engine, labeler_id: Optional[str] = None) -> Dict[str, Any]:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT
                    sp.span_id,
                    sp.source_message_id,
                    sp.span_order,
                    sp.proposed_start_offset,
                    sp.proposed_end_offset,
                    sp.proposed_text,
                    sp.proposal_confidence,
                    s.raw_text AS source_raw_text,
                    s.source_table,
                    s.sampling_bucket,
                    s.message_length
                FROM alliance_gold_spans sp
                JOIN alliance_gold_source_messages s
                  ON s.source_message_id=sp.source_message_id
                WHERE sp.boundary_status <> 'LABELED'
                ORDER BY
                    CASE s.sampling_bucket
                        WHEN 'GIANT_DUMP' THEN 1
                        WHEN 'LONG' THEN 2
                        WHEN 'MEDIUM' THEN 3
                        ELSE 4
                    END,
                    s.created_at,
                    sp.span_order
                LIMIT 1
                """
            )
        ).mappings().first()

    if not row:
        return {"status": "EMPTY", "message": "No unlabeled spans available."}

    out = dict(row)
    out["span_id"] = str(out["span_id"])
    out["source_message_id"] = str(out["source_message_id"])
    out["proposal"] = propose_fields(out["proposed_text"])
    return _json_safe({"status": "PASS", "span": out})

def disagreements(engine) -> Dict[str, Any]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    span_id,
                    count(*) AS label_count,
                    count(DISTINCT content_type) AS content_type_versions,
                    count(DISTINCT COALESCE(transaction_type,'')) AS transaction_versions
                FROM alliance_gold_span_labels
                WHERE active=TRUE
                GROUP BY span_id
                HAVING count(*) >= 2
                   AND (
                       count(DISTINCT content_type) > 1
                       OR count(DISTINCT COALESCE(transaction_type,'')) > 1
                   )
                ORDER BY span_id
                LIMIT 200
                """
            )
        ).mappings().all()

    return {
        "status": "PASS",
        "disagreement_count": len(rows),
        "items": [
            {**dict(r), "span_id": str(r["span_id"])}
            for r in rows
        ],
    }

def export_gold(engine, limit: int = 1000) -> Dict[str, Any]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    sp.span_id,
                    sp.source_message_id,
                    COALESCE(sp.human_text, sp.proposed_text) AS span_text,
                    sp.proposed_start_offset,
                    sp.proposed_end_offset,
                    sp.human_start_offset,
                    sp.human_end_offset,
                    l.labeler_id,
                    l.content_type,
                    l.human_confidence,
                    l.transaction_type,
                    l.project_name,
                    l.building_name,
                    l.unit_identifier,
                    l.city,
                    l.locality,
                    l.acceptable_locations,
                    l.areas,
                    l.money_mentions,
                    l.suitable_uses,
                    l.contacts,
                    l.property_fields,
                    l.requirement_fields,
                    l.notes,
                    l.adjudicated,
                    s.source_table,
                    s.sampling_bucket
                FROM alliance_gold_spans sp
                JOIN alliance_gold_source_messages s
                  ON s.source_message_id=sp.source_message_id
                JOIN alliance_gold_span_labels l
                  ON l.span_id=sp.span_id
                 AND l.active=TRUE
                ORDER BY sp.created_at, sp.span_order
                LIMIT :limit
                """
            ),
            {"limit": limit},
        ).mappings().all()

    items = []
    for r in rows:
        d = dict(r)
        d["span_id"] = str(d["span_id"])
        d["source_message_id"] = str(d["source_message_id"])
        for key in [
            "acceptable_locations",
            "areas",
            "money_mentions",
            "suitable_uses",
            "contacts",
            "property_fields",
            "requirement_fields",
        ]:
            d[key] = _loads(d.get(key), [] if key not in {"property_fields", "requirement_fields"} else {})
        items.append(d)

    return {
        "status": "PASS",
        "gold_span_count": len(items),
        "items": items,
        "warning": "This is human-labeled Gold data, not production inventory.",
    }

# ---------------------------------------------------------------------------
# Evaluation dashboard: current baseline
# ---------------------------------------------------------------------------

def boundary_baseline(engine, limit: int = 500) -> Dict[str, Any]:
    """
    First measurable metric: whether humans accepted the proposed span boundary.
    More field metrics will be added only after enough Gold labels exist.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    sp.span_id,
                    sp.boundary_action,
                    sp.boundary_status,
                    l.content_type,
                    l.transaction_type,
                    l.adjudicated
                FROM alliance_gold_spans sp
                LEFT JOIN alliance_gold_span_labels l
                  ON l.span_id=sp.span_id
                 AND l.active=TRUE
                WHERE sp.boundary_status='LABELED'
                ORDER BY sp.updated_at
                LIMIT :limit
                """
            ),
            {"limit": limit},
        ).mappings().all()

    total = len(rows)
    accepted = sum(
        1 for r in rows
        if str(r.get("boundary_action") or "").upper() == "CORRECT"
    )
    changed = total - accepted

    return {
        "status": "PASS",
        "version": VERSION,
        "labeled_spans_examined": total,
        "proposed_boundary_accepted": accepted,
        "proposed_boundary_changed": changed,
        "boundary_acceptance_rate": round(accepted / total, 4) if total else None,
        "business_accuracy_claim": (
            "NOT_AVAILABLE" if total < 100 else
            "PILOT_ONLY"
        ),
        "zero_tolerance_metrics": {
            "false_property_creation_rate": "NOT_YET_MEASURED",
            "unsupported_write_rate": 0,
            "rate_vs_total_confusion_rate": "NOT_YET_MEASURED",
        },
        "production_write_permission": False,
    }

# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

LAB_UI = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Alliance Property Brain Gold Lab</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:Arial,sans-serif;margin:0;background:#f5f6f8;color:#16181d}
header{background:#111827;color:white;padding:18px 24px}
header h1{margin:0;font-size:22px}
header p{margin:6px 0 0;color:#cbd5e1}
.wrap{padding:20px;max-width:1500px;margin:auto}
.grid{display:grid;grid-template-columns:1.1fr 1fr;gap:18px}
.card{background:white;border:1px solid #e5e7eb;border-radius:12px;padding:16px}
h2{font-size:17px;margin:0 0 12px}
pre{white-space:pre-wrap;word-break:break-word;background:#f8fafc;border:1px solid #e5e7eb;padding:14px;border-radius:8px;max-height:520px;overflow:auto}
label{display:block;font-size:12px;font-weight:700;margin-top:10px}
input,select,textarea{width:100%;box-sizing:border-box;padding:9px;border:1px solid #cbd5e1;border-radius:7px;margin-top:4px}
textarea{min-height:70px}
button{border:0;border-radius:8px;padding:10px 14px;cursor:pointer;font-weight:700}
.primary{background:#111827;color:white}
.secondary{background:#e5e7eb}
.good{background:#166534;color:white}
.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.badge{display:inline-block;padding:4px 8px;border-radius:999px;background:#eef2ff;font-size:12px;margin-right:6px}
.small{font-size:12px;color:#64748b}
#msg{margin-top:10px;font-weight:700}
@media(max-width:900px){.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<header>
<h1>Alliance Property Brain — Human Gold Lab</h1>
<p>Evidence Span review. Human judgment is ground truth. Production inventory is never written from this screen.</p>
</header>
<div class="wrap">
<div class="card" style="margin-bottom:18px">
<span class="badge">Gold Dataset</span>
<span class="badge">Read-only production</span>
<span class="badge">100-span first milestone</span>
<div id="progress" class="small" style="margin-top:10px">Loading progress...</div>
</div>

<div class="grid">
<div class="card">
<h2>Original Source Message</h2>
<div id="meta" class="small"></div>
<pre id="source">Loading...</pre>
</div>

<div class="card">
<h2>Proposed Evidence Span</h2>
<div id="spanMeta" class="small"></div>
<pre id="span">Loading...</pre>

<label>Labeler ID</label>
<input id="labeler" placeholder="Team member name">

<div class="row">
<div>
<label>Boundary decision</label>
<select id="boundary">
<option>CORRECT</option>
<option>EDIT</option>
<option>SPLIT</option>
<option>MERGE</option>
</select>
</div>
<div>
<label>Human confidence</label>
<select id="confidence">
<option>HIGH</option>
<option>MEDIUM</option>
<option>LOW</option>
</select>
</div>
</div>

<label>Content type</label>
<select id="contentType">
<option>PROPERTY_AVAILABILITY</option>
<option>REQUIREMENT</option>
<option>CONTACT_ONLY</option>
<option>PROJECT_HEADER</option>
<option>LOCALITY_HEADER</option>
<option>FRAGMENT</option>
<option>NOISE</option>
</select>

<label>Transaction</label>
<select id="transaction">
<option>UNKNOWN</option>
<option>SALE</option>
<option>RENT</option>
<option>BOTH</option>
<option>AMBIGUOUS</option>
</select>

<div class="row">
<div><label>City</label><input id="city"></div>
<div><label>Locality</label><input id="locality"></div>
</div>

<div class="row">
<div><label>Project</label><input id="project"></div>
<div><label>Unit / identifier</label><input id="unit"></div>
</div>

<label>Acceptable locations for requirement (comma separated)</label>
<input id="locations">

<label>Suitable uses (comma separated)</label>
<input id="uses">

<label>Areas JSON</label>
<textarea id="areas">[]</textarea>

<label>Money mentions JSON</label>
<textarea id="money">[]</textarea>

<label>Contacts JSON</label>
<textarea id="contacts">[]</textarea>

<label>Notes / why</label>
<textarea id="notes"></textarea>

<div class="actions">
<button class="good" onclick="quickSave('PROPERTY_AVAILABILITY')">Correct Property</button>
<button class="good" onclick="quickSave('REQUIREMENT')">Correct Requirement</button>
<button class="secondary" onclick="quickSave('PROJECT_HEADER')">Project Header</button>
<button class="secondary" onclick="quickSave('LOCALITY_HEADER')">Locality Header</button>
<button class="secondary" onclick="quickSave('FRAGMENT')">Fragment</button>
<button class="secondary" onclick="quickSave('NOISE')">Noise</button>
</div>
<div class="actions">
<button class="primary" onclick="save()">Save Edited Gold Label</button>
<button class="secondary" onclick="loadNext()">Skip / Next</button>
</div>
<div id="msg"></div>
</div>
</div>
</div>

<script>
let current=null;

function csvList(id){
  return document.getElementById(id).value.split(",").map(x=>x.trim()).filter(Boolean);
}
function parseJson(id){
  try{return JSON.parse(document.getElementById(id).value||"[]")}
  catch(e){throw new Error(id+" contains invalid JSON")}
}
async function refreshProgress(){
  const r=await fetch("/api/property-brain-foundation/progress");
  const d=await r.json();
  document.getElementById("progress").innerText =
    `Sources: ${d.source_messages} | Proposed spans: ${d.proposed_spans} | Labeled spans: ${d.labeled_spans} | ${d.first_milestone}`;
}
async function loadNext(){
  document.getElementById("msg").innerText="";
  document.getElementById("source").innerText="Loading...";
  document.getElementById("span").innerText="Loading...";
  try{
    const r=await fetch("/api/property-brain-foundation/next-span");
    const raw=await r.text();
    let d={};
    try{ d=JSON.parse(raw); }
    catch(e){ throw new Error("Backend returned non-JSON response"); }
    if(!r.ok) throw new Error(d.detail||d.message||"Gold Lab backend error");
    if(d.status!=="PASS"){
      document.getElementById("source").innerText=d.message||"No spans.";
      document.getElementById("span").innerText="";
      current=null;
      return;
    }
  current=d.span;
  document.getElementById("source").innerText=current.source_raw_text;
  document.getElementById("span").innerText=current.proposed_text;
  document.getElementById("meta").innerText =
    `Source: ${current.source_table} | Bucket: ${current.sampling_bucket} | Length: ${current.message_length}`;
  document.getElementById("spanMeta").innerText =
    `Span ${current.span_order} | Proposal confidence: ${current.proposal_confidence}`;
  const p=current.proposal||{};
  document.getElementById("contentType").value =
    ["PROPERTY_AVAILABILITY","REQUIREMENT","FRAGMENT"].includes(p.content_type_hint)
      ? p.content_type_hint : "FRAGMENT";
  document.getElementById("transaction").value=p.transaction_type_hint||"UNKNOWN";
  document.getElementById("areas").value=JSON.stringify(p.areas||[],null,2);
  document.getElementById("money").value=JSON.stringify(p.money_mentions||[],null,2);
  document.getElementById("contacts").value=JSON.stringify(p.contacts||[],null,2);
  }catch(e){
    current=null;
    document.getElementById("source").innerText="Gold Lab runtime error. Do not label this record.";
    document.getElementById("span").innerText="";
    document.getElementById("msg").innerText="ERROR: "+e.message;
  }
}
async function quickSave(contentType){
  document.getElementById("contentType").value=contentType;
  document.getElementById("boundary").value="CORRECT";
  await save();
}
async function save(){
  try{
    if(!current) throw new Error("No span loaded");
    const labeler=document.getElementById("labeler").value.trim();
    if(!labeler) throw new Error("Enter Labeler ID / team member name");
    const payload={
      labeler_id:labeler,
      boundary_action:document.getElementById("boundary").value,
      content_type:document.getElementById("contentType").value,
      human_confidence:document.getElementById("confidence").value,
      transaction_type:document.getElementById("transaction").value,
      city:document.getElementById("city").value.trim()||null,
      locality:document.getElementById("locality").value.trim()||null,
      project_name:document.getElementById("project").value.trim()||null,
      unit_identifier:document.getElementById("unit").value.trim()||null,
      acceptable_locations:csvList("locations"),
      suitable_uses:csvList("uses"),
      areas:parseJson("areas"),
      money_mentions:parseJson("money"),
      contacts:parseJson("contacts"),
      notes:document.getElementById("notes").value.trim()||null
    };
    const r=await fetch(`/api/property-brain-foundation/span/${current.span_id}/label`,{
      method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)
    });
    const d=await r.json();
    if(!r.ok) throw new Error(d.detail||JSON.stringify(d));
    document.getElementById("msg").innerText="Saved to Gold Dataset.";
    document.getElementById("notes").value="";
    document.getElementById("city").value="";
    document.getElementById("locality").value="";
    document.getElementById("project").value="";
    document.getElementById("unit").value="";
    document.getElementById("locations").value="";
    document.getElementById("uses").value="";
    await refreshProgress();
    await loadNext();
  }catch(e){
    document.getElementById("msg").innerText="ERROR: "+e.message;
  }
}
refreshProgress();
loadNext();
</script>
</body>
</html>
"""

DASHBOARD_UI = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Property Brain Evaluation</title>
<style>
body{font-family:Arial,sans-serif;background:#f5f6f8;margin:0;color:#111827}
.wrap{max-width:1200px;margin:auto;padding:24px}
.card{background:white;border:1px solid #e5e7eb;border-radius:12px;padding:18px;margin-bottom:16px}
h1{margin-top:0}.metric{font-size:30px;font-weight:800}.muted{color:#64748b}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
table{width:100%;border-collapse:collapse}td,th{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left}
@media(max-width:800px){.grid{grid-template-columns:1fr 1fr}}
</style>
</head>
<body>
<div class="wrap">
<h1>Alliance Property Brain — Gold Evaluation</h1>
<p class="muted">Only human-reviewed Gold labels count as business validation.</p>
<div class="grid">
<div class="card"><div class="muted">Source messages</div><div class="metric" id="sources">-</div></div>
<div class="card"><div class="muted">Proposed spans</div><div class="metric" id="spans">-</div></div>
<div class="card"><div class="muted">Human-labeled spans</div><div class="metric" id="labeled">-</div></div>
<div class="card"><div class="muted">Boundary acceptance</div><div class="metric" id="boundary">-</div></div>
</div>
<div class="card">
<h2>Safety gates</h2>
<table>
<tr><th>Metric</th><th>Status</th></tr>
<tr><td>False property creation</td><td>NOT YET MEASURED</td></tr>
<tr><td>Unsupported production writes</td><td>0</td></tr>
<tr><td>Rate vs total-price confusion</td><td>NOT YET MEASURED</td></tr>
<tr><td>Production write permission</td><td>BLOCKED</td></tr>
</table>
</div>
<div class="card">
<h2>Current milestone</h2>
<div id="milestone">Loading...</div>
<p class="muted">First target: 100 human-reviewed evidence spans. Then expand to 300–500 stratified Gold spans.</p>
</div>
</div>
<script>
async function load(){
 const p=await (await fetch("/api/property-brain-foundation/progress")).json();
 const b=await (await fetch("/api/property-brain-foundation/evaluation/boundary")).json();
 document.getElementById("sources").innerText=p.source_messages;
 document.getElementById("spans").innerText=p.proposed_spans;
 document.getElementById("labeled").innerText=p.labeled_spans;
 document.getElementById("boundary").innerText=
   b.boundary_acceptance_rate===null?"-":Math.round(b.boundary_acceptance_rate*1000)/10+"%";
 document.getElementById("milestone").innerText=p.first_milestone;
}
load();
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Regression
# ---------------------------------------------------------------------------

def regression() -> Dict[str, Any]:
    cases = []

    def check(key: str, ok: bool, detail: Any = None):
        cases.append({"case_key": key, "passed": bool(ok), "detail": detail})

    sample = """SAKET
1. Ground Floor
5000 sqft
Rent 7.5 Lac
Restaurant suitable

2. First Floor
3200 sqft
Rent 4 Lac
"""
    spans = propose_spans(sample)
    check("NUMBERED_PROPERTIES_SPLIT", len(spans) >= 2, len(spans))

    req = "Looking for 3000-5000 sqft restaurant in Saket rent budget 5 Lakh"
    req_fields = propose_fields(req)
    check(
        "REQUIREMENT_DIRECTION",
        req_fields["content_type_hint"] == "REQUIREMENT",
        req_fields,
    )

    avail = "Available for rent 4000 sqft Saket asking 6 Lakh"
    avail_fields = propose_fields(avail)
    check(
        "AVAILABILITY_DIRECTION",
        avail_fields["content_type_hint"] == "PROPERTY_AVAILABILITY",
        avail_fields,
    )

    check(
        "AREA_PARSE",
        bool(propose_fields("Plot 500 yards")["areas"]),
        propose_fields("Plot 500 yards"),
    )

    check(
        "MONEY_PARSE",
        bool(propose_fields("Rent 1.5 Lakh")["money_mentions"]),
        propose_fields("Rent 1.5 Lakh"),
    )

    check(
        "PRODUCTION_WRITE_TABLES_EXCLUDED",
        not bool(PRODUCTION_WRITE_TABLES & ACADEMY_TABLES),
        sorted(PRODUCTION_WRITE_TABLES & ACADEMY_TABLES),
    )

    check(
        "ERROR_COLUMN_REJECTED",
        bool(BAD_TEXT_COLUMN_RE.search("error_message")),
        "error_message",
    )

    check(
        "ID_COLUMN_REJECTED",
        bool(BAD_TEXT_COLUMN_RE.search("property_id")),
        "property_id",
    )

    property_example = "DLF Phase 2 4 BHK 2700 sqft available for rent asking 2.15 Lakh"
    check(
        "PROPERTY_SEMANTIC_DENSITY",
        _semantic_signal_count(property_example) >= 4,
        _semantic_signal_count(property_example),
    )

    check(
        "WHATSAPP_SOURCE_CLASS",
        _source_class("ai_whatsapp_purity", "raw_text") == "WHATSAPP_EVIDENCE",
        _source_class("ai_whatsapp_purity", "raw_text"),
    )

    check(
        "MATCH_INDEX_DERIVED_TABLE_REJECTED",
        bool(DERIVED_OR_AUX_TABLE_RE.search("ai_property_match_index")),
        "ai_property_match_index",
    )
    check(
        "HOSPITALITY_PHONE_EVIDENCE_REJECTED",
        bool(DERIVED_OR_AUX_TABLE_RE.search("pi_hospitality_phone_evidence")),
        "pi_hospitality_phone_evidence",
    )
    check(
        "FRONTAGE_RAW_REJECTED",
        bool(DERIVED_TEXT_COLUMN_RE.search("frontage_raw")),
        "frontage_raw",
    )
    check(
        "PRIMARY_WHATSAPP_SOURCE_TRUSTED",
        any(
            t == "ai_whatsapp_purity" and c == "raw_text"
            for t, c, _, _ in TRUSTED_GOLD_SOURCES
        ),
        "ai_whatsapp_purity.raw_text",
    )

    runtime_payload = {
        "proposal_confidence": Decimal("0.65"),
        "source_message_id": uuid.UUID("12345678-1234-5678-1234-567812345678"),
        "created_at": datetime(2026, 9, 1, 12, 30, tzinfo=timezone.utc),
    }
    safe_runtime_payload = _json_safe(runtime_payload)
    check(
        "DECIMAL_JSON_SERIALIZATION",
        isinstance(safe_runtime_payload["proposal_confidence"], float)
        and safe_runtime_payload["proposal_confidence"] == 0.65,
        safe_runtime_payload,
    )
    check(
        "UUID_JSON_SERIALIZATION",
        safe_runtime_payload["source_message_id"] == "12345678-1234-5678-1234-567812345678",
        safe_runtime_payload,
    )
    check(
        "DATETIME_JSON_SERIALIZATION",
        safe_runtime_payload["created_at"].startswith("2026-09-01T12:30:00"),
        safe_runtime_payload,
    )

    failed = [c for c in cases if not c["passed"]]
    return {
        "status": "PASS" if not failed else "FAIL",
        "version": VERSION,
        "total": len(cases),
        "passed": len(cases) - len(failed),
        "failed": len(failed),
        "score": round(100 * (len(cases) - len(failed)) / len(cases), 2),
        "critical_failures": len(failed),
        "failed_cases": [c["case_key"] for c in failed],
        "results": cases,
        "canonical_writes": 0,
        "offer_writes": 0,
        "matcher_writes": 0,
        "whatsapp_live_writes": 0,
    }

# ---------------------------------------------------------------------------
# FastAPI registration
# ---------------------------------------------------------------------------

def register(core):
    app = core.app if hasattr(core, "app") else core
    engine = _engine_from_core(core)

    status_route = "/api/property-brain-foundation/status"
    if _route_exists(app, status_route):
        return {
            "status": "ALREADY_REGISTERED",
            "version": VERSION,
            "route": status_route,
        }

    # Install Academy tables only.
    install_result = install(engine)

    @app.get("/property-brain-gold-lab", response_class=HTMLResponse)
    def gold_lab():
        return HTMLResponse(LAB_UI)

    @app.get("/property-brain-evaluation", response_class=HTMLResponse)
    def evaluation_dashboard():
        return HTMLResponse(DASHBOARD_UI)

    @app.get(status_route)
    def status():
        return _json_response({
            "status": "READY",
            "version": VERSION,
            "mode": MODE,
            "gold_lab": "/property-brain-gold-lab",
            "evaluation_dashboard": "/property-brain-evaluation",
            "source_discovery": "/api/property-brain-foundation/sources/discover",
            "curriculum_plan": "/api/property-brain-foundation/sources/curriculum?total_messages=25",
            "academy_tables": sorted(ACADEMY_TABLES),
            "production_write_permission": False,
            "canonical_writes": 0,
            "offer_writes": 0,
            "matcher_writes": 0,
            "whatsapp_live_writes": 0,
        })

    @app.get("/api/property-brain-foundation/regression")
    def regression_route():
        return _json_response(regression())

    @app.get("/api/property-brain-foundation/sources/discover")
    def sources_discover():
        return _json_response(discover_sources(engine))

    @app.get("/api/property-brain-foundation/sources/curriculum")
    def sources_curriculum(total_messages: int = Query(25, ge=1, le=100)):
        return _json_response(curriculum_plan(engine, total_messages))

    @app.post("/api/property-brain-foundation/sources/import-curriculum")
    def sources_import_curriculum(payload: Dict[str, Any] = Body(default={})):
        total_messages = int((payload or {}).get("total_messages") or 25)
        total_messages = max(1, min(total_messages, 100))
        return _json_response(import_curriculum(engine, total_messages))

    @app.post("/api/property-brain-foundation/sources/import")
    def sources_import(payload: Dict[str, Any] = Body(...)):
        table_name = str(payload.get("table") or "").strip()
        column_name = str(payload.get("column") or "").strip()
        limit = int(payload.get("limit") or 25)
        if not table_name or not column_name:
            raise HTTPException(400, "table and column are required")
        limit = max(1, min(limit, 250))
        return _json_response(import_sources(engine, table_name, column_name, limit))

    @app.post("/api/property-brain-foundation/propose")
    def propose(payload: Dict[str, Any] = Body(...)):
        raw = str(payload.get("raw_text") or "")
        return _json_response({
            "status": "PASS",
            "spans": [
                {**s, "proposal": propose_fields(s["text"])}
                for s in propose_spans(raw)
            ],
            "human_review_required": True,
        })

    @app.get("/api/property-brain-foundation/next-span")
    def next_span_route(labeler_id: Optional[str] = Query(None)):
        return _json_response(next_span(engine, labeler_id))

    @app.post("/api/property-brain-foundation/span/{span_id}/label")
    def label_span(span_id: str, payload: Dict[str, Any] = Body(...)):
        return _json_response(save_label(engine, span_id, payload))

    @app.post("/api/property-brain-foundation/relationship")
    def relationship(payload: Dict[str, Any] = Body(...)):
        return _json_response(save_relationship(engine, payload))

    @app.get("/api/property-brain-foundation/progress")
    def progress_route():
        return _json_response(progress(engine))

    @app.get("/api/property-brain-foundation/disagreements")
    def disagreements_route():
        return _json_response(disagreements(engine))

    @app.get("/api/property-brain-foundation/gold/export")
    def gold_export(limit: int = Query(1000, ge=1, le=5000)):
        return _json_response(export_gold(engine, limit))

    @app.get("/api/property-brain-foundation/evaluation/boundary")
    def evaluation_boundary(limit: int = Query(500, ge=1, le=5000)):
        return _json_response(boundary_baseline(engine, limit))

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": status_route,
        "gold_lab": "/property-brain-gold-lab",
        "evaluation_dashboard": "/property-brain-evaluation",
        "install": install_result,
        "production_write_permission": False,
    }
